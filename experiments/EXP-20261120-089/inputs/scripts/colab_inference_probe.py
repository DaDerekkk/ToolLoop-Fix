"""Standalone one-turn CUDA smoke test. Only this file is needed on the VM."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tempfile
import time

MODEL_ID = "Qwen/Qwen3.5-0.8B"
REVISION = "2fc06364715b967f1860aea9cf38778875588b17"
OUTPUT = Path("/content/toolloop_probe.json")
MAX_NEW_TOKENS = 96
SEED = 0

# Public files only; no paths or identifiers from any workspace or account.
FILES = {
    "LICENSE": "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a",
    "chat_template.jinja": "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80",
    "config.json": "b90b86f35c8e6925ef74ee04d0e758f0a845c83a42089ad82bbaa948de9b4204",
    "merges.txt": "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d",
    "model.safetensors-00001-of-00001.safetensors": "04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696",
    "model.safetensors.index.json": "d8a08838a613b025eb7952ed9db11696213e57e76a375661ef5c12f9dd5dcf4e",
    "tokenizer.json": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
    "tokenizer_config.json": "49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c",
    "vocab.json": "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003"
}

MESSAGES = [
    {"role": "system", "content": "Use the provided tool when the user asks for a lookup."},
    {"role": "user", "content": "Call lookup_demo for the example identifier DEMO-001."},
]
TOOLS = [{"type": "function", "function": {
    "name": "lookup_demo", "description": "Look up a fictional demonstration record.",
    "parameters": {"type": "object", "properties": {"identifier": {"type": "string"}},
                   "required": ["identifier"], "additionalProperties": False},
}}]


def verify_files(directory, expected):
    for name, digest in expected.items():
        hasher = hashlib.sha256()
        with (directory / name).open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            raise ValueError("public snapshot file hash mismatch")


def verify_loading(info):
    if any(info.get(key) for key in ("missing_keys", "mismatched_keys", "error_msgs")):
        raise ValueError("incomplete checkpoint load")
    if any(not key.startswith(("visual.", "model.visual.", "vision_tower.", "model.vision_tower."))
           for key in info.get("unexpected_keys", [])):
        raise ValueError("unexpected nonvision checkpoint keys")


def run_probe(report):
    # These must take effect before importing the Hub client.
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    report["stage"] = "runtime_check"
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer, GenerationConfig, Qwen3_5ForCausalLM

    report["runtime"] = {"python": sys.version.split()[0], "cuda_version": torch.version.cuda,
                         "packages": {k: importlib.metadata.version(k) for k in
                                      ("torch", "transformers", "huggingface-hub", "tokenizers", "safetensors")}}
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required")
    device = torch.cuda.get_device_properties(0)
    report["gpu"] = {"name": device.name, "memory_bytes": device.total_memory,
                     "compute_capability": [device.major, device.minor]}
    if "L4" not in device.name.split() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("this probe requires an L4 with BF16")
    with tempfile.TemporaryDirectory() as tmp:
        report["stage"] = "anonymous_public_download"
        snapshot_download(repo_id=MODEL_ID, revision=REVISION, token=False, local_dir=tmp,
                          allow_patterns=list(FILES), endpoint="https://huggingface.co", max_workers=3)
        report["stage"] = "verify_public_snapshot"
        verify_files(Path(tmp), FILES)
        report["verified_files"] = FILES
        report["stage"] = "local_model_load"
        tokenizer = AutoTokenizer.from_pretrained(tmp, trust_remote_code=False, local_files_only=True)
        model, info = Qwen3_5ForCausalLM.from_pretrained(
            tmp, dtype=torch.bfloat16, attn_implementation="eager", use_safetensors=True,
            local_files_only=True, trust_remote_code=False, output_loading_info=True)
        verify_loading(info)
        model.to("cuda:0").eval()
        if any(p.device != torch.device("cuda:0") for p in model.parameters()):
            raise RuntimeError("not all model parameters on intended device")
        report["model_class"] = type(model).__name__
        report["parameter_device"] = str(next(model.parameters()).device)
        report["parameter_dtype"] = str(next(model.parameters()).dtype)
        report["stage"] = "render_public_prompt"
        prompt = tokenizer.apply_chat_template(MESSAGES, tools=TOOLS, tokenize=False,
                                              add_generation_prompt=True, enable_thinking=False)
        inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to("cuda:0")
        ids = inputs["input_ids"][0].tolist()
        if not ids or len(ids) > 2048:
            raise ValueError("unexpected toy prompt length")
        eos_ids = set()
        for v in (tokenizer.eos_token_id, model.generation_config.eos_token_id):
            eos_ids.update(v if isinstance(v, list) else [v] if v is not None else [])
        if sorted(eos_ids) != [248044, 248046]:
            raise ValueError("unexpected EOS union for pinned model")
        generation = GenerationConfig(do_sample=False, num_beams=1, max_new_tokens=MAX_NEW_TOKENS,
                                      use_cache=True, eos_token_id=sorted(eos_ids),
                                      pad_token_id=tokenizer.pad_token_id)
        report.update(messages=MESSAGES, tools=TOOLS, prompt_text=prompt, prompt_token_ids=ids,
                      prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(), seed=SEED,
                      input_tokens=len(ids), generation_config=generation.to_dict())
        report["stage"] = "generate"
        torch.manual_seed(SEED)
        torch.cuda.synchronize()
        start = time.monotonic()
        with torch.inference_mode():
            out = model.generate(**inputs, generation_config=generation)
        torch.cuda.synchronize()
        elapsed = time.monotonic() - start
        output_ids = out[0, len(ids):].tolist()
        if not 1 <= len(output_ids) <= MAX_NEW_TOKENS:
            raise ValueError("unexpected generated token count")
        report.update(raw_text=tokenizer.decode(output_ids, skip_special_tokens=False),
                      generated_token_ids=output_ids, output_tokens=len(output_ids),
                      generation_seconds=elapsed,
                      token_limit_reached=len(output_ids) == MAX_NEW_TOKENS and output_ids[-1] not in eos_ids)


def main(output=OUTPUT, probe=run_probe):
    report = {"schema_version": 1, "scope": "single_public_tool_call_proposal_only",
              "model_id": MODEL_ID, "revision": REVISION, "training_performed": False,
              "tool_executed": False, "stage": "start"}
    # Exclusive open before any download: never overwrite or duplicate a completed attempt.
    with Path(output).open("x", encoding="utf-8") as f:
        try:
            probe(report)
        except Exception as exc:
            # Exceptions from network/SDKs can contain signed URLs. Never export their text.
            report.update(status="failed", error_type=type(exc).__name__)
        else:
            report.update(status="completed", stage="completed")
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"status": report["status"], "stage": report["stage"]}))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
