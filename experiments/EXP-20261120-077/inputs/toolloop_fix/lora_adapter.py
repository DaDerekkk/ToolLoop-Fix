"""Standalone pure-PyTorch LoRA adapter and token-aligned losses for Qwen3.5 text layers.

Shared verbatim between local macOS (CPU/MPS) and Colab L4 VM (CUDA).
Imports are strictly limited to Python stdlib plus lazy `torch` and `safetensors`
so this module has zero dependencies on other `toolloop_fix.*` modules and remains
importable in pure-stdlib environments.
"""
import ctypes
import hashlib
import json
import math
from pathlib import Path

try:
    import torch as _torch_mod
    import torch.nn as _nn_mod

    _NNModuleBase = _nn_mod.Module
except ImportError:  # pragma: no cover - pure-stdlib import fallback
    _torch_mod = None
    _nn_mod = None
    _NNModuleBase = object

LORA_ADAPTER_VERSION = "pure_pytorch_qwen35_text_lora_v1"

TARGET_LEAF_MODULES = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "in_proj_qkv",
    "in_proj_z",
    "in_proj_a",
    "in_proj_b",
    "out_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)

EXCLUDED_PREFIXES = (
    "visual.",
    "model.visual.",
    "vision_tower.",
    "model.vision_tower.",
    "mtp.",
    "model.mtp.",
    "lm_head",
)


def _require_torch():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    return torch, nn, F


def _require_safetensors():
    from safetensors import safe_open
    from safetensors.torch import save_file

    return safe_open, save_file


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_excluded_name(name):
    if name.startswith(EXCLUDED_PREFIXES) or name.endswith(".lm_head"):
        return True
    for token in (".visual.", ".vision_tower.", ".mtp."):
        if token in name:
            return True
    return False


class LoRALinear(_NNModuleBase):
    """Low-rank residual wrapper over a frozen 2D `nn.Linear` module."""

    def __init__(self, base, rank=4, alpha=8.0, seed=0):
        torch, nn, _ = _require_torch()
        super().__init__()
        if isinstance(base, LoRALinear) or not isinstance(base, nn.Linear):
            raise TypeError("LoRALinear requires an unwrapped torch.nn.Linear base module")
        if base.weight.ndim != 2:
            raise ValueError("LoRALinear requires a 2D base Linear weight")
        rank = int(rank)
        alpha = float(alpha)
        if rank <= 0:
            raise ValueError("LoRA rank must be a positive integer")
        if not math.isfinite(alpha) or alpha <= 0.0:
            raise ValueError("LoRA alpha must be a positive finite float")

        self.base = base
        self.base.weight.requires_grad_(False)
        if self.base.bias is not None:
            self.base.bias.requires_grad_(False)

        self.in_features = int(base.in_features)
        self.out_features = int(base.out_features)
        self.rank = rank
        self.alpha = alpha
        self.scaling = float(alpha) / float(rank)
        self.lora_enabled = True
        self._module_prefix = "model.language_model."

        dev = base.weight.device
        self.lora_A = nn.Parameter(
            torch.empty((self.rank, self.in_features), dtype=torch.float32, device=dev)
        )
        self.lora_B = nn.Parameter(
            torch.zeros((self.out_features, self.rank), dtype=torch.float32, device=dev)
        )
        self.reset_lora(seed)

    def reset_lora(self, seed):
        """Deterministically initialize `lora_A` on CPU float32 and zero `lora_B`."""
        torch, _, _ = _require_torch()
        gen = torch.Generator(device="cpu")
        gen.manual_seed(int(seed))
        init_a = torch.empty((self.rank, self.in_features), dtype=torch.float32, device="cpu")
        init_a.normal_(0.0, 0.02, generator=gen)
        with torch.no_grad():
            self.lora_A.data.copy_(init_a.to(device=self.lora_A.device, dtype=torch.float32))
            self.lora_B.data.zero_()

    def forward(self, x):
        _, _, F = _require_torch()
        base_out = self.base(x)
        if not self.lora_enabled:
            return base_out
        delta = F.linear(F.linear(x.float(), self.lora_A), self.lora_B) * self.scaling
        return base_out + delta.to(base_out.dtype)


def _sorted_lora_modules(model):
    return sorted(
        ((name, mod) for name, mod in model.named_modules() if isinstance(mod, LoRALinear)),
        key=lambda item: item[0],
    )


def _candidate_linear_modules(model, prefix):
    _, nn, _ = _require_torch()
    target_set = frozenset(TARGET_LEAF_MODULES)
    matched = []
    for name, mod in model.named_modules():
        if not name or isinstance(mod, LoRALinear) or not isinstance(mod, nn.Linear):
            continue
        if mod.weight.ndim != 2:
            continue
        if _is_excluded_name(name):
            continue
        leaf = name.rsplit(".", 1)[-1]
        if leaf not in target_set:
            continue
        if prefix and not name.startswith(prefix):
            continue
        matched.append((name, mod))
    matched.sort(key=lambda item: item[0])
    return matched


def inject_lora(model, rank=4, alpha=8.0, seed=101, module_prefix="model.language_model."):
    """Freeze all base parameters and inject `LoRALinear` into matching text projections."""
    _require_torch()
    if _sorted_lora_modules(model):
        raise ValueError("model already contains injected LoRALinear modules")
    if module_prefix is None:
        module_prefix = ""
    if not isinstance(module_prefix, str):
        raise TypeError("module_prefix must be a string")

    rank = int(rank)
    alpha = float(alpha)
    seed = int(seed)
    if rank <= 0:
        raise ValueError("rank must be a positive integer")
    if not math.isfinite(alpha) or alpha <= 0.0:
        raise ValueError("alpha must be a positive finite float")

    for param in model.parameters():
        param.requires_grad_(False)

    effective_prefix = module_prefix
    matched = _candidate_linear_modules(model, effective_prefix)
    if not matched and module_prefix == "model.language_model.":
        # `Qwen3_5ForCausalLM` strips `.language_model` when loading checkpoint weights,
        # placing text layers at `model.layers.*` in the exact same sorted order.
        matched = _candidate_linear_modules(model, "model.layers.")
        if matched:
            effective_prefix = "model.layers."
        else:
            all_module_names = [name for name, _ in model.named_modules() if name]
            if not any(name == "model" or name.startswith("model.") for name in all_module_names):
                matched = _candidate_linear_modules(model, "")
                effective_prefix = ""

    if not matched:
        raise ValueError(f"inject_lora matched 0 target Linear modules for module_prefix={module_prefix!r}")

    names = []
    for idx, (name, linear_mod) in enumerate(matched):
        wrapped = LoRALinear(
            linear_mod,
            rank=rank,
            alpha=alpha,
            seed=seed + idx * 1009,
        )
        wrapped._module_prefix = module_prefix
        wrapped._effective_module_prefix = effective_prefix
        if "." in name:
            parent_name, attr_name = name.rsplit(".", 1)
            parent_mod = model.get_submodule(parent_name)
        else:
            parent_mod = model
            attr_name = name
        setattr(parent_mod, attr_name, wrapped)
        names.append(name)

    lora_params = trainable_lora_parameters(model)
    trainable_scalars = sum(int(p.numel()) for p in lora_params)
    all_trainable = sum(int(p.numel()) for p in model.parameters() if p.requires_grad)
    if trainable_scalars != all_trainable:
        raise RuntimeError("non-LoRA trainable parameter leak detected after inject_lora")

    return {
        "adapter_version": LORA_ADAPTER_VERSION,
        "rank": rank,
        "alpha": alpha,
        "matched_module_count": len(names),
        "trainable_param_count": int(trainable_scalars),
        "module_names": names,
    }


def set_lora_enabled(model, enabled):
    """Enable or disable the LoRA delta branch across all `LoRALinear` modules."""
    flag = bool(enabled)
    modules = _sorted_lora_modules(model)
    for _, mod in modules:
        mod.lora_enabled = flag
    return len(modules)


def reset_lora_parameters(model, seed):
    """Re-initialize all `LoRALinear` modules in sorted name order to exact step-0 identity."""
    base_seed = int(seed)
    modules = _sorted_lora_modules(model)
    for idx, (_, mod) in enumerate(modules):
        mod.reset_lora(base_seed + idx * 1009)
    return len(modules)


def trainable_lora_parameters(model):
    """Return `[lora_A, lora_B, ...]` across all `LoRALinear` modules in sorted name order."""
    modules = _sorted_lora_modules(model)
    return [param for _, mod in modules for param in (mod.lora_A, mod.lora_B)]


def base_weight_digest(model):
    """Compute SHA256 hex digest over all non-LoRA parameters `(name, shape, dtype, raw_bytes_cpu)`.

    Canonicalizes `<module>.base.weight` / `<module>.base.bias` inside `LoRALinear` back to
    `<module>.weight` / `<module>.bias` so the digest is bit-identical before `inject_lora`,
    immediately after `inject_lora`, and after any number of LoRA optimizer steps.
    """
    torch, _, _ = _require_torch()
    lora_module_names = {name for name, _ in _sorted_lora_modules(model)}
    entries = []
    for raw_name, param in model.named_parameters():
        canonical_name = raw_name
        skip = False
        if "." in raw_name:
            prefix, leaf = raw_name.rsplit(".", 1)
            if prefix in lora_module_names and leaf in ("lora_A", "lora_B"):
                skip = True
            elif prefix.endswith(".base"):
                maybe_lora = prefix[: -len(".base")]
                if maybe_lora in lora_module_names and leaf in ("weight", "bias"):
                    canonical_name = f"{maybe_lora}.{leaf}"
        if skip:
            continue
        entries.append((canonical_name, param))

    entries.sort(key=lambda item: item[0])
    digest = hashlib.sha256()
    for name, param in entries:
        header = f"{name}|{tuple(int(d) for d in param.shape)}|{param.dtype}|".encode("utf-8")
        digest.update(header)
        u8 = param.detach().contiguous().cpu().view(torch.uint8)
        nbytes = int(u8.numel())
        if nbytes > 0:
            buf = (ctypes.c_ubyte * nbytes).from_address(u8.data_ptr())
            digest.update(memoryview(buf))
        digest.update(b"\n")
    return digest.hexdigest()


def save_lora_safetensors(model, path, extra_metadata=None):
    """Save LoRA `lora_A`/`lora_B` weights in contiguous CPU `bfloat16` `.safetensors` format."""
    torch, _, _ = _require_torch()
    _, save_file = _require_safetensors()
    modules = _sorted_lora_modules(model)
    if not modules:
        raise ValueError("cannot save LoRA safetensors: model has no LoRALinear modules")

    first_mod = modules[0][1]
    rank = int(first_mod.rank)
    alpha = float(first_mod.alpha)
    declared_prefix = str(getattr(first_mod, "_module_prefix", "model.language_model."))

    tensors = {}
    for name, mod in modules:
        if int(mod.rank) != rank or float(mod.alpha) != alpha:
            raise ValueError("inconsistent rank/alpha across LoRALinear modules")
        tensors[f"{name}.lora_A"] = mod.lora_A.detach().to(torch.bfloat16).contiguous().cpu()
        tensors[f"{name}.lora_B"] = mod.lora_B.detach().to(torch.bfloat16).contiguous().cpu()

    str_meta = {}
    if extra_metadata is not None:
        if not isinstance(extra_metadata, dict):
            raise TypeError("extra_metadata must be a mapping when provided")
        for key, value in extra_metadata.items():
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                str_meta[str(key)] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            else:
                str_meta[str(key)] = str(value)

    str_meta["adapter_version"] = LORA_ADAPTER_VERSION
    str_meta["rank"] = str(rank)
    str_meta["alpha"] = str(alpha)
    str_meta.setdefault("module_prefix", declared_prefix)

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_file(tensors, str(out_path), metadata=str_meta)
    sha256_hex = _sha256_file(out_path)
    size_bytes = int(out_path.stat().st_size)
    return {
        "sha256": sha256_hex,
        "size_bytes": size_bytes,
        "tensor_count": len(tensors),
        "metadata": str_meta,
    }


def _align_qwen35_backbone_keys(saved_tensors, expected_keys):
    """Translate `model.language_model.layers.*` <-> `model.layers.*` when needed."""
    saved_keys = set(saved_tensors.keys())
    if saved_keys == expected_keys:
        return saved_tensors
    cond_prefix = "model.language_model.layers."
    causal_prefix = "model.layers."
    if all(k.startswith(cond_prefix) for k in saved_keys) and all(
        k.startswith(causal_prefix) for k in expected_keys
    ):
        return {
            causal_prefix + k[len(cond_prefix) :]: tensor
            for k, tensor in saved_tensors.items()
        }
    if all(k.startswith(causal_prefix) for k in saved_keys) and all(
        k.startswith(cond_prefix) for k in expected_keys
    ):
        return {
            cond_prefix + k[len(causal_prefix) :]: tensor
            for k, tensor in saved_tensors.items()
        }
    return saved_tensors


def load_lora_safetensors(model, path, expected_sha256=None):
    """Verify SHA256, inject LoRA if absent, strictly load all tensors in float32, and set eval mode."""
    torch, _, _ = _require_torch()
    safe_open, _ = _require_safetensors()
    in_path = Path(path)
    if not in_path.is_file():
        raise FileNotFoundError(f"LoRA safetensors file not found: {in_path}")

    actual_sha256 = _sha256_file(in_path)
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or actual_sha256 != expected_sha256.lower():
            raise ValueError(
                f"LoRA safetensors SHA256 mismatch: expected {expected_sha256!r}, got {actual_sha256!r}"
            )

    with safe_open(str(in_path), framework="pt", device="cpu") as handle:
        meta = dict(handle.metadata() or {})
        saved_tensors = {key: handle.get_tensor(key) for key in handle.keys()}

    if meta.get("adapter_version") != LORA_ADAPTER_VERSION:
        raise ValueError(f"unsupported or missing LoRA adapter_version: {meta.get('adapter_version')!r}")
    if "rank" not in meta or "alpha" not in meta:
        raise ValueError("LoRA safetensors metadata must declare 'rank' and 'alpha'")

    rank = int(meta["rank"])
    alpha = float(meta["alpha"])
    module_prefix = meta.get("module_prefix", "model.language_model.")

    modules = _sorted_lora_modules(model)
    if not modules:
        inject_lora(model, rank=rank, alpha=alpha, seed=0, module_prefix=module_prefix)
        modules = _sorted_lora_modules(model)

    expected_map = {}
    for name, mod in modules:
        if int(mod.rank) != rank:
            raise ValueError(f"LoRALinear rank mismatch on {name}: model has {mod.rank}, file has {rank}")
        expected_map[f"{name}.lora_A"] = (mod, "lora_A")
        expected_map[f"{name}.lora_B"] = (mod, "lora_B")

    aligned_tensors = _align_qwen35_backbone_keys(saved_tensors, set(expected_map.keys()))
    missing = sorted(set(expected_map.keys()) - set(aligned_tensors.keys()))
    unexpected = sorted(set(aligned_tensors.keys()) - set(expected_map.keys()))
    if missing or unexpected:
        raise ValueError(
            f"LoRA safetensors key mismatch: missing={missing[:5]}, unexpected={unexpected[:5]}"
        )

    for key, (mod, attr) in expected_map.items():
        src = aligned_tensors[key]
        dst = getattr(mod, attr)
        if tuple(src.shape) != tuple(dst.shape):
            raise ValueError(
                f"LoRA tensor shape mismatch for {key}: expected {tuple(dst.shape)}, got {tuple(src.shape)}"
            )

    with torch.no_grad():
        for _, mod in modules:
            mod.alpha = alpha
            mod.scaling = float(alpha) / float(rank)
        for key, (mod, attr) in expected_map.items():
            dst = getattr(mod, attr)
            dst.data.copy_(aligned_tensors[key].to(device=dst.device, dtype=torch.float32))

    enabled_count = set_lora_enabled(model, True)
    model.eval()
    return {
        **meta,
        "metadata": meta,
        "sha256": actual_sha256,
        "size_bytes": int(in_path.stat().st_size),
        "tensor_count": len(aligned_tensors),
        "loaded_module_count": enabled_count,
    }


def sequence_completion_stats(logits, full_ids, prompt_len):
    """Compute exact causal completion log-probs (`logits[0, pos - 1]` predicts `full_ids[pos]`)."""
    _, _, F = _require_torch()
    if logits.ndim != 3 or int(logits.shape[0]) != 1:
        raise ValueError(f"logits must have shape [1, L, V], got {tuple(logits.shape)}")
    if full_ids.ndim == 1:
        flat_ids = full_ids.reshape(-1)
    elif full_ids.ndim == 2 and int(full_ids.shape[0]) == 1:
        flat_ids = full_ids.reshape(-1)
    else:
        raise ValueError(f"full_ids must be 1D or [1, L], got {tuple(full_ids.shape)}")

    total_len = int(flat_ids.shape[0])
    if int(logits.shape[1]) != total_len:
        raise ValueError(
            f"logits sequence length {int(logits.shape[1])} != full_ids length {total_len}"
        )
    prompt_len = int(prompt_len)
    if not (0 < prompt_len < total_len):
        raise ValueError(f"prompt_len must satisfy 0 < prompt_len < L (got prompt_len={prompt_len}, L={total_len})")

    pred_logits = logits[0, prompt_len - 1 : total_len - 1].float()
    target_ids = flat_ids[prompt_len:total_len].long().to(device=pred_logits.device)
    log_probs = F.log_softmax(pred_logits, dim=-1)
    token_lp = log_probs.gather(-1, target_ids.unsqueeze(-1)).squeeze(-1)
    mean_lp = token_lp.mean()
    return {
        "sum_logprob": token_lp.sum(),
        "mean_logprob": mean_lp,
        "mean_nll": -mean_lp,
        "token_count": int(total_len - prompt_len),
    }


def single_token_divergence_logprobs(logits, prompt_len, div_offset, div_chosen_id, div_rejected_id):
    """Evaluate FTPO-style single-token preference log-probs at `pos = prompt_len + div_offset - 1`."""
    _, _, F = _require_torch()
    if logits.ndim != 3 or int(logits.shape[0]) != 1:
        raise ValueError(f"logits must have shape [1, L, V], got {tuple(logits.shape)}")
    prompt_len = int(prompt_len)
    div_offset = int(div_offset)
    if prompt_len <= 0 or div_offset < 0:
        raise ValueError(f"invalid prompt_len={prompt_len} or div_offset={div_offset}")
    pos = prompt_len + div_offset - 1
    if not (0 <= pos < int(logits.shape[1])):
        raise ValueError(f"causal position {pos} out of bounds for sequence length {int(logits.shape[1])}")

    chosen_id = int(div_chosen_id)
    rejected_id = int(div_rejected_id)
    if chosen_id == rejected_id:
        raise ValueError("div_chosen_id and div_rejected_id must be distinct token IDs")
    vocab_size = int(logits.shape[2])
    if not (0 <= chosen_id < vocab_size and 0 <= rejected_id < vocab_size):
        raise ValueError("divergence token IDs out of vocabulary bounds")

    lp = F.log_softmax(logits[0, pos].float(), dim=-1)
    chosen_lp = lp[chosen_id]
    rejected_lp = lp[rejected_id]
    return chosen_lp, rejected_lp, -chosen_lp


def dpo_step_loss(
    pol_chosen_lp,
    pol_rejected_lp,
    ref_chosen_lp,
    ref_rejected_lp,
    chosen_nll,
    beta=0.1,
    sft_anchor=0.15,
):
    """Compute reference-anchored DPO step loss with optional positive SFT NLL anchor."""
    torch, _, F = _require_torch()
    beta = float(beta)
    sft_anchor = float(sft_anchor)
    if not math.isfinite(beta) or beta <= 0.0:
        raise ValueError("beta must be a positive finite float")
    if not math.isfinite(sft_anchor) or sft_anchor < 0.0:
        raise ValueError("sft_anchor must be a nonnegative finite float")

    ref_c = ref_chosen_lp.detach() if isinstance(ref_chosen_lp, torch.Tensor) else float(ref_chosen_lp)
    ref_r = ref_rejected_lp.detach() if isinstance(ref_rejected_lp, torch.Tensor) else float(ref_rejected_lp)
    logits_diff = beta * ((pol_chosen_lp - ref_c) - (pol_rejected_lp - ref_r))
    dpo_loss = -F.logsigmoid(logits_diff)
    nll_tensor = (
        chosen_nll
        if isinstance(chosen_nll, torch.Tensor)
        else torch.as_tensor(chosen_nll, dtype=dpo_loss.dtype, device=dpo_loss.device)
    )
    total = dpo_loss + sft_anchor * nll_tensor
    margin_detached = logits_diff.detach()
    return {
        "loss": total,
        "dpo_loss": dpo_loss,
        "nll_loss": nll_tensor,
        "reward_margin": margin_detached,
        "prefer_chosen": bool((margin_detached > 0).item()),
    }
