"""Offline transformers backend; importing this module needs only the stdlib."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_manifest(config):
    refs = [item for item in config["external_inputs"] if item["name"] == "model_manifest"]
    if len(refs) != 1:
        raise ValueError("exactly one pinned model_manifest is required")
    ref = refs[0]
    if sha256_file(ref["path"]) != ref["sha256"]:
        raise ValueError("model manifest SHA256 mismatch")
    manifest = json.loads(Path(ref["path"]).read_text())
    model = config["model"]
    for key in ("model_id", "revision", "model_path"):
        if manifest[key] != model[key]:
            raise ValueError("model manifest {} mismatch".format(key))
    root = Path(model["model_path"])
    if not root.is_absolute():
        raise ValueError("model_path must be absolute")
    root = root.resolve()
    files = manifest["files"]
    if not files or not any(item["path"].endswith(".safetensors") for item in files):
        raise ValueError("model manifest must include raw safetensors weights")
    seen = set()
    for item in files:
        relative = Path(item["path"])
        actual = (root / relative).resolve()
        if relative.is_absolute() or root not in actual.parents or item["path"] in seen:
            raise ValueError("unsafe or duplicate model file path")
        seen.add(item["path"])
        if actual.stat().st_size != item["size_bytes"] or sha256_file(actual) != item["sha256"]:
            raise ValueError("model file verification failed: " + item["path"])
    disk_files = {str(path.relative_to(root)) for path in root.rglob("*")
                  if path.is_file() and path.suffix in {".json", ".jinja", ".safetensors", ".model", ".txt"}}
    if not disk_files.issubset(seen):
        raise ValueError("unmanifested model/tokenizer files: " + ", ".join(sorted(disk_files - seen)))
    return ref["sha256"], files


def output_presence_penalty(scores, input_ids, prompt_length, penalty):
    """Additive presence penalty over unique CURRENT generated tokens only.

    Works with torch tensors at runtime; plain indexing stand-ins allow stdlib
    tests to assert scope. This is not repetition_penalty or prompt penalization.
    """
    for row_index in range(len(input_ids)):
        generated = set(input_ids[row_index][prompt_length:].tolist())
        if generated:
            scores[row_index, list(generated)] -= penalty
    return scores


def normalize_loading_info(info):
    """Transformers 5.23 exposes key collections as sets; keep all fields."""
    return {key: sorted(value) if isinstance(value, set) else value for key, value in info.items()}


def check_loading_info(info):
    """Reject any missing/mismatched text weights; only unused vision is allowed."""
    unexpected_text = [key for key in info.get("unexpected_keys", [])
                       if not key.startswith(("visual.", "model.visual.", "vision_tower.", "model.vision_tower."))]
    if any(info.get(key) for key in ("missing_keys", "mismatched_keys", "error_msgs")) or unexpected_text:
        raise RuntimeError("checkpoint loading was not a complete text-model load: " +
                           json.dumps(normalize_loading_info(info)))


class TransformersBackend:
    def __init__(self, config, initial_tools):
        self.config = config
        self.manifest_sha256, self.model_files = verify_model_manifest(config)
        # Deliberately lazy: stdlib tests and simulator do not require ML packages.
        import torch
        from transformers import AutoTokenizer, Qwen3_5ForCausalLM
        self.torch = torch
        model_config = config["model"]
        device, dtype_name = model_config["device"], model_config["dtype"]
        if (device, dtype_name) not in {("cpu", "float32"), ("mps", "bfloat16")}:
            raise ValueError("supported explicit configurations: CPU float32 or verified MPS bfloat16")
        if device == "mps":
            if not torch.backends.mps.is_available():
                raise RuntimeError("MPS explicitly selected but not available")
            probe = torch.ones((2, 2), device="mps", dtype=torch.bfloat16)
            if not torch.isfinite(probe @ probe).all().item():
                raise RuntimeError("MPS bfloat16 arithmetic preflight failed")
        self.device = device
        self.dtype = getattr(torch, dtype_name)
        path = model_config["model_path"]
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
        self.model, self.loading_info = Qwen3_5ForCausalLM.from_pretrained(path, local_files_only=True,
            trust_remote_code=False, use_safetensors=True, dtype=self.dtype,
            attn_implementation="eager", output_loading_info=True)
        check_loading_info(self.loading_info)
        self.loading_info = normalize_loading_info(self.loading_info)
        self.model = self.model.to(device).eval()
        self.chat_template = self.tokenizer.get_chat_template(tools=initial_tools)
        self.chat_template_sha256 = hashlib.sha256(self.chat_template.encode()).hexdigest()
        eos_candidates = [self.tokenizer.eos_token_id, self.model.generation_config.eos_token_id]
        self.eos_ids = sorted({token for value in eos_candidates for token in
                               (value if isinstance(value, list) else [value]) if token is not None})
        if not self.eos_ids:
            raise ValueError("model and tokenizer provide no EOS token")
        self.effective_generation = {key: config["sampling"][key] for key in
            ("do_sample", "temperature", "top_p", "top_k", "min_p", "repetition_penalty", "max_new_tokens")}
        self.effective_generation.update(eos_token_id=self.eos_ids,
            pad_token_id=self.tokenizer.pad_token_id,
            presence_penalty=config["sampling"]["presence_penalty"],
            presence_penalty_implementation="additive_unique_current_completion_tokens_v1",
            presence_penalty_scope="generated_tokens_since_current_prompt_end",
            enable_thinking=False, attn_implementation="eager", use_cache=True)

    def provenance(self):
        return {"schema_version": 1, "evidence_class": "model_inference",
            "model": self.config["model"], "sampling": self.config["sampling"],
            "runtime": {"executable": sys.executable, "python_version": sys.version,
                "platform": platform.platform(), "packages": {name: importlib.metadata.version(name)
                    for name in ("torch", "transformers", "tokenizers", "safetensors")},
                "pytorch_enable_mps_fallback": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
                "mps_is_built": self.torch.backends.mps.is_built(),
                "mps_is_available": self.torch.backends.mps.is_available()},
            "model_manifest_sha256": self.manifest_sha256,
            "model_files_verified": True, "model_files": self.model_files,
            "chat_template_sha256": self.chat_template_sha256,
            "effective_generation": self.effective_generation,
            "actual_model_class": type(self.model).__name__,
            "checkpoint_loading_info": self.loading_info,
            "actual_parameter_device": str(next(self.model.parameters()).device),
            "actual_parameter_dtype": str(next(self.model.parameters()).dtype),
            "model_generation_config": self.model.generation_config.to_dict(),
            "tokenizer_eos_token_id": self.tokenizer.eos_token_id,
            "tokenizer_eos_token": self.tokenizer.eos_token,
            "sampling_reproducibility": "seeded per case/turn; backend numerical reproducibility is not guaranteed"}

    def generate(self, messages, tools, seed):
        torch = self.torch
        torch.manual_seed(seed)
        if self.device == "mps":
            torch.mps.manual_seed(seed)
        prompt = self.tokenizer.apply_chat_template(messages, tools=tools, tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        encoded = self.tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(self.device)
        prompt_length = encoded["input_ids"].shape[1]
        configured_max = self.config.get("max_context_tokens", 8192)
        if prompt_length + self.config["sampling"]["max_new_tokens"] > configured_max:
            raise RuntimeError("context budget exceeded; history was not silently truncated")
        penalty = self.config["sampling"]["presence_penalty"]
        from transformers import LogitsProcessor, LogitsProcessorList
        class CurrentCompletionPresencePenalty(LogitsProcessor):
            def __call__(self, input_ids, scores):
                return output_presence_penalty(scores, input_ids, prompt_length, penalty)
        kwargs = {key: self.effective_generation[key] for key in
            ("do_sample", "temperature", "top_p", "top_k", "min_p", "repetition_penalty", "max_new_tokens",
             "eos_token_id", "pad_token_id", "use_cache")}
        start = time.monotonic()
        with torch.inference_mode():
            output = self.model.generate(**encoded, **kwargs,
                logits_processor=LogitsProcessorList([CurrentCompletionPresencePenalty()]))
        if self.device == "mps":
            torch.mps.synchronize()
        elapsed = time.monotonic() - start
        ids = output[0, prompt_length:].tolist()
        limit_reached = len(ids) >= kwargs["max_new_tokens"] and (not ids or ids[-1] not in self.eos_ids)
        return {"raw_text": self.tokenizer.decode(ids, skip_special_tokens=False),
            "prompt_text": prompt, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "prompt_token_ids": encoded["input_ids"][0].tolist(), "generated_token_ids": ids,
            "input_tokens": prompt_length, "output_tokens": len(ids), "elapsed_seconds": elapsed,
            "token_limit_reached": limit_reached, "seed": seed,
            "effective_generation": self.effective_generation}
