"""Unit tests for pure-PyTorch Qwen3.5 LoRA adapter, token-aligned losses, and ITER-009 eval runner."""
import ast
import contextlib
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from safetensors import safe_open
    from safetensors.torch import save_file

    HAS_TORCH_AND_SAFETENSORS = True
except ImportError:  # pragma: no cover - system stdlib Python fallback
    torch = None
    nn = None
    F = None
    safe_open = None
    save_file = None
    HAS_TORCH_AND_SAFETENSORS = False

from scripts import validate_records
from toolloop_fix.baselines import prompt_for
from toolloop_fix.harness import DOMAIN_TOOLS
from toolloop_fix.loop_detector_v2 import DETECTOR_V2_VERSION
from toolloop_fix.lora_adapter import (
    EXCLUDED_PREFIXES,
    LORA_ADAPTER_VERSION,
    TARGET_LEAF_MODULES,
    LoRALinear,
    base_weight_digest,
    dpo_step_loss,
    inject_lora,
    load_lora_safetensors,
    reset_lora_parameters,
    save_lora_safetensors,
    sequence_completion_stats,
    set_lora_enabled,
    single_token_divergence_logprobs,
    trainable_lora_parameters,
)
from toolloop_fix.lora_eval_runner import (
    CURATED_PREFIX_SCHEMA_VERSION,
    LORA_EVAL_RUNNER_VERSION,
    main as lora_eval_main,
    prepare_curated_prefix,
    validate_curated_prefix_config,
    validate_lora_explicit_e2e_config,
    validate_lora_explicit_e2e_multiseed_config,
)
from toolloop_fix.model_backend import sha256_file
from toolloop_fix.prefix_run import EXPLICIT_TASK_PREFIX, TASK_WORDING_VERSION

ROOT = Path(__file__).resolve().parents[1]
PILOT_CASES = [
    json.loads(line)
    for line in (ROOT / "data/dev/pilot_cases.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]


def xml_call(name, **arguments):
    lines = ["<tool_call>", "<function={}>".format(name)]
    for key in sorted(arguments.keys()):
        lines.append("<parameter={}>".format(key))
        lines.append(str(arguments[key]))
        lines.append("</parameter>")
    lines.append("</function>")
    lines.append("</tool_call><|im_end|>")
    return "\n".join(lines)


if HAS_TORCH_AND_SAFETENSORS:

    class _TinyLinearAttn(nn.Module):
        def __init__(self, hidden=16):
            super().__init__()
            self.in_proj_qkv = nn.Linear(hidden, hidden * 3, bias=False)
            self.in_proj_z = nn.Linear(hidden, hidden, bias=False)
            self.in_proj_a = nn.Linear(hidden, 4, bias=False)
            self.in_proj_b = nn.Linear(hidden, 4, bias=False)
            self.out_proj = nn.Linear(hidden, hidden, bias=False)

        def forward(self, x):
            qkv = self.in_proj_qkv(x)
            q, k, v = qkv.chunk(3, dim=-1)
            gate = torch.sigmoid(self.in_proj_z(x))
            ab = (self.in_proj_a(x) * self.in_proj_b(x)).mean(dim=-1, keepdim=True)
            return self.out_proj((q + k + v) * gate + ab)

    class _TinySelfAttn(nn.Module):
        def __init__(self, hidden=16):
            super().__init__()
            self.q_proj = nn.Linear(hidden, hidden, bias=False)
            self.k_proj = nn.Linear(hidden, hidden, bias=False)
            self.v_proj = nn.Linear(hidden, hidden, bias=False)
            self.o_proj = nn.Linear(hidden, hidden, bias=False)

        def forward(self, x):
            q = self.q_proj(x)
            k = self.k_proj(x)
            v = self.v_proj(x)
            seq_len = x.shape[1]
            scores = torch.matmul(q, k.transpose(-1, -2)) / math.sqrt(float(x.shape[-1]))
            causal_mask = torch.triu(
                torch.ones((seq_len, seq_len), device=x.device, dtype=torch.bool), diagonal=1
            )
            scores = scores.masked_fill(causal_mask, float("-inf"))
            weights = torch.softmax(scores.float(), dim=-1).to(v.dtype)
            return self.o_proj(torch.matmul(weights, v))

    class _TinyMLP(nn.Module):
        def __init__(self, hidden=16, inter=24):
            super().__init__()
            self.gate_proj = nn.Linear(hidden, inter, bias=False)
            self.up_proj = nn.Linear(hidden, inter, bias=False)
            self.down_proj = nn.Linear(inter, hidden, bias=False)

        def forward(self, x):
            return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))

    class _TinyDecoderLayer(nn.Module):
        def __init__(self, kind, hidden=16):
            super().__init__()
            if kind == "linear":
                self.linear_attn = _TinyLinearAttn(hidden)
                self.self_attn = None
            else:
                self.linear_attn = None
                self.self_attn = _TinySelfAttn(hidden)
            self.mlp = _TinyMLP(hidden)

        def forward(self, x):
            attn_out = self.linear_attn(x) if self.linear_attn is not None else self.self_attn(x)
            x = x + attn_out
            return x + self.mlp(x)

    class TinyQwen35ConditionalModel(nn.Module):
        """Synthetic Qwen3.5 `ConditionalGeneration`-shaped model (`model.language_model.layers.*`)."""

        def __init__(self, vocab_size=48, hidden=16, seed=7):
            super().__init__()
            torch.manual_seed(seed)
            self.model = nn.Module()
            self.model.visual = nn.Module()
            self.model.visual.q_proj = nn.Linear(hidden, hidden, bias=False)
            self.model.language_model = nn.Module()
            self.model.language_model.embed_tokens = nn.Embedding(vocab_size, hidden)
            self.model.language_model.layers = nn.ModuleList(
                [_TinyDecoderLayer("linear", hidden), _TinyDecoderLayer("full", hidden)]
            )
            self.mtp = nn.Module()
            self.mtp.q_proj = nn.Linear(hidden, hidden, bias=False)
            self.lm_head = nn.Linear(hidden, vocab_size, bias=False)

        def forward(self, input_ids):
            x = self.model.language_model.embed_tokens(input_ids)
            for layer in self.model.language_model.layers:
                x = layer(x)
            return self.lm_head(x)

    class TinyQwen35CausalModel(nn.Module):
        """Synthetic Qwen3.5 `CausalLM`-shaped model (`model.layers.*`, matching `TransformersBackend`)."""

        def __init__(self, vocab_size=48, hidden=16, seed=7):
            super().__init__()
            torch.manual_seed(seed)
            self.model = nn.Module()
            self.model.embed_tokens = nn.Embedding(vocab_size, hidden)
            self.model.layers = nn.ModuleList(
                [_TinyDecoderLayer("linear", hidden), _TinyDecoderLayer("full", hidden)]
            )
            self.lm_head = nn.Linear(hidden, vocab_size, bias=False)

        def forward(self, input_ids):
            x = self.model.embed_tokens(input_ids)
            for layer in self.model.layers:
                x = layer(x)
            return self.lm_head(x)


class FakeLoRABackend:
    """Fast deterministic backend: uses real `TinyQwen35CausalModel` + safetensors when torch is installed,
    and pure-stdlib `.load_lora_adapter` hook when executed under stdlib-only Python.
    """

    def __init__(self, config, initial_tools, responder=None, counter=None):
        if counter is not None:
            counter["loads"] += 1
        self.config = copy.deepcopy(config)
        self.initial_tools = copy.deepcopy(initial_tools)
        self.responder = responder
        self.lora_active = False
        self.model = TinyQwen35CausalModel(vocab_size=32, hidden=8, seed=19) if HAS_TORCH_AND_SAFETENSORS else None
        model_ref = next(ref for ref in config["external_inputs"] if ref["name"] == "model_manifest")
        self.manifest_sha256 = model_ref["sha256"]
        self.chat_template_sha256 = hashlib.sha256(
            json.dumps(initial_tools, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.effective_generation = {
            key: config["sampling"][key]
            for key in (
                "do_sample",
                "temperature",
                "top_p",
                "top_k",
                "min_p",
                "repetition_penalty",
                "max_new_tokens",
            )
        }
        self.effective_generation.update(
            eos_token_id=[248044, 248046],
            pad_token_id=248044,
            presence_penalty=config["sampling"]["presence_penalty"],
            presence_penalty_implementation="additive_unique_current_completion_tokens_v1",
            presence_penalty_scope="generated_tokens_since_current_prompt_end",
            enable_thinking=False,
            attn_implementation="eager",
            use_cache=True,
        )

    def load_lora_adapter(self, lora_cfg):
        self.lora_active = True
        return {
            "adapter_version": LORA_ADAPTER_VERSION,
            "sha256": lora_cfg["safetensors_sha256"],
            "size_bytes": Path(lora_cfg["safetensors_path"]).stat().st_size,
            "tensor_count": 30,
            "loaded_module_count": 15,
            "rank": lora_cfg.get("rank", 4),
            "alpha": lora_cfg.get("alpha", 8.0),
            "metadata": {"adapter_version": LORA_ADAPTER_VERSION},
            "base_weight_digest_sha256": "a" * 64,
        }

    def provenance(self):
        return {
            "schema_version": 1,
            "evidence_class": "model_inference",
            "model": copy.deepcopy(self.config["model"]),
            "sampling": copy.deepcopy(self.config["sampling"]),
            "runtime": {
                "executable": sys.executable,
                "python_version": sys.version,
                "platform": "unit-test",
                "packages": {
                    "torch": torch.__version__ if HAS_TORCH_AND_SAFETENSORS else "0.0-testdouble",
                    "transformers": "5.0-testdouble",
                    "tokenizers": "0.20-testdouble",
                    "safetensors": "0.4-testdouble",
                },
            },
            "model_manifest_sha256": self.manifest_sha256,
            "model_files_verified": True,
            "chat_template_sha256": self.chat_template_sha256,
            "effective_generation": copy.deepcopy(self.effective_generation),
            "actual_model_class": type(self.model).__name__ if self.model is not None else "FakeLoRABackend",
            "actual_parameter_device": "cpu",
            "actual_parameter_dtype": "torch.float32",
            "checkpoint_loading_info": {
                "missing_keys": [],
                "unexpected_keys": [],
                "mismatched_keys": [],
                "error_msgs": [],
            },
        }

    def generate(self, messages, tools, seed):
        if self.model is not None:
            has_lora = any(isinstance(m, LoRALinear) and m.lora_enabled for _, m in self.model.named_modules())
        else:
            has_lora = self.lora_active
        raw_text = self.responder(messages, tools, seed, self.config, has_lora)
        prompt_text = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, sort_keys=True)
        prompt_ids = [11, 12, (seed % 19) + 1]
        gen_ids = [21, 22, 23]
        return {
            "raw_text": raw_text,
            "prompt_text": prompt_text,
            "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            "prompt_token_ids": prompt_ids,
            "generated_token_ids": gen_ids,
            "input_tokens": len(prompt_ids),
            "output_tokens": len(gen_ids),
            "elapsed_seconds": 0.01,
            "token_limit_reached": False,
            "seed": seed,
            "effective_generation": copy.deepcopy(self.effective_generation),
        }


class StandaloneIsolationTests(unittest.TestCase):
    def test_standalone_ast_isolation_and_constants(self):
        src_path = ROOT / "toolloop_fix/lora_adapter.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    self.fail("Relative import forbidden in standalone lora_adapter.py")
                if node.module:
                    imported_modules.append(node.module)
        for mod_name in imported_modules:
            self.assertFalse(
                mod_name.startswith("toolloop_fix"),
                f"lora_adapter.py must not import {mod_name} so it runs standalone on Colab VM",
            )
        self.assertEqual(LORA_ADAPTER_VERSION, "pure_pytorch_qwen35_text_lora_v1")
        self.assertEqual(
            TARGET_LEAF_MODULES,
            (
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
            ),
        )
        self.assertIn("lm_head", EXCLUDED_PREFIXES)
        self.assertIn("model.visual.", EXCLUDED_PREFIXES)
        self.assertIn("mtp.", EXCLUDED_PREFIXES)


@unittest.skipUnless(HAS_TORCH_AND_SAFETENSORS, "requires torch and safetensors in .venv")
class LoRAAdapterAndLossesTests(unittest.TestCase):
    def test_step0_exact_identity_toggle_reset_and_base_immutability(self):
        for dtype in (torch.float32, torch.bfloat16):
            model = TinyQwen35ConditionalModel(vocab_size=48, hidden=16, seed=11).to(dtype=dtype)
            model.eval()
            digest_pre_inject = base_weight_digest(model)

            x = torch.tensor([[2, 7, 13, 19, 31]], dtype=torch.long)
            with torch.no_grad():
                ref_logits = model(x).clone()

            summary = inject_lora(model, rank=4, alpha=8.0, seed=101, module_prefix="model.language_model.")
            self.assertEqual(summary["matched_module_count"], 15)
            self.assertEqual(summary["adapter_version"], LORA_ADAPTER_VERSION)
            self.assertNotIsInstance(model.model.visual.q_proj, LoRALinear)
            self.assertNotIsInstance(model.mtp.q_proj, LoRALinear)
            self.assertNotIsInstance(model.lm_head, LoRALinear)
            self.assertFalse(model.lm_head.weight.requires_grad)

            digest_post_inject = base_weight_digest(model)
            self.assertEqual(digest_pre_inject, digest_post_inject)

            with torch.no_grad():
                step0_logits = model(x)
            max_abs_diff = (step0_logits.float() - ref_logits.float()).abs().max().item()
            self.assertEqual(max_abs_diff, 0.0)

            params = trainable_lora_parameters(model)
            self.assertEqual(len(params), 30)
            self.assertTrue(all(p.dtype == torch.float32 and p.requires_grad for p in params))
            opt = torch.optim.AdamW(params, lr=5e-2)
            for _ in range(4):
                opt.zero_grad()
                logits = model(x)
                stats = sequence_completion_stats(logits, x, prompt_len=2)
                stats["mean_nll"].backward()
                opt.step()

            self.assertEqual(base_weight_digest(model), digest_pre_inject)

            with torch.no_grad():
                adapted_logits = model(x).clone()
            self.assertGreater((adapted_logits.float() - ref_logits.float()).abs().max().item(), 1e-4)

            toggled = set_lora_enabled(model, False)
            self.assertEqual(toggled, 15)
            with torch.no_grad():
                disabled_logits = model(x)
            self.assertEqual((disabled_logits.float() - ref_logits.float()).abs().max().item(), 0.0)

            set_lora_enabled(model, True)
            with torch.no_grad():
                reenabled_logits = model(x)
            self.assertEqual((reenabled_logits.float() - adapted_logits.float()).abs().max().item(), 0.0)

            reset_count = reset_lora_parameters(model, seed=101)
            self.assertEqual(reset_count, 15)
            with torch.no_grad():
                after_reset_logits = model(x)
            self.assertEqual((after_reset_logits.float() - ref_logits.float()).abs().max().item(), 0.0)

    def test_safetensors_roundtrip_cross_backbone_and_strict_validation(self):
        src_model = TinyQwen35ConditionalModel(vocab_size=48, hidden=16, seed=23).to(dtype=torch.bfloat16)
        inject_lora(src_model, rank=4, alpha=8.0, seed=303, module_prefix="model.language_model.")
        opt = torch.optim.AdamW(trainable_lora_parameters(src_model), lr=8e-2)
        seq = torch.tensor([[3, 9, 14, 22, 27]], dtype=torch.long)
        for _ in range(3):
            opt.zero_grad()
            sequence_completion_stats(src_model(seq), seq, prompt_len=2)["mean_nll"].backward()
            opt.step()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            st_path = tmp_dir / "adapter.safetensors"
            saved = save_lora_safetensors(
                src_model,
                st_path,
                extra_metadata={"training_arm": "P", "train_seed": 303},
            )
            self.assertEqual(saved["tensor_count"], 30)
            self.assertEqual(saved["metadata"]["training_arm"], "P")
            self.assertEqual(saved["metadata"]["rank"], "4")

            fresh_cond = TinyQwen35ConditionalModel(vocab_size=48, hidden=16, seed=23).to(dtype=torch.bfloat16)
            loaded = load_lora_safetensors(fresh_cond, st_path, expected_sha256=saved["sha256"])
            self.assertEqual(loaded["sha256"], saved["sha256"])
            self.assertEqual(loaded["loaded_module_count"], 15)
            self.assertFalse(fresh_cond.training)

            st_path_2 = tmp_dir / "adapter_roundtrip.safetensors"
            save_lora_safetensors(
                fresh_cond,
                st_path_2,
                extra_metadata={"training_arm": "P", "train_seed": 303},
            )
            with safe_open(str(st_path), framework="pt", device="cpu") as f1, safe_open(
                str(st_path_2), framework="pt", device="cpu"
            ) as f2:
                self.assertEqual(set(f1.keys()), set(f2.keys()))
                for key in f1.keys():
                    self.assertTrue(torch.equal(f1.get_tensor(key), f2.get_tensor(key)))

            causal_dst = TinyQwen35CausalModel(vocab_size=48, hidden=16, seed=99).to(dtype=torch.bfloat16)
            causal_dst.model.embed_tokens.load_state_dict(
                fresh_cond.model.language_model.embed_tokens.state_dict()
            )
            causal_dst.model.layers.load_state_dict(
                {
                    k: v
                    for k, v in TinyQwen35ConditionalModel(vocab_size=48, hidden=16, seed=23)
                    .to(dtype=torch.bfloat16)
                    .model.language_model.layers.state_dict()
                    .items()
                }
            )
            causal_dst.lm_head.load_state_dict(fresh_cond.lm_head.state_dict())
            load_lora_safetensors(causal_dst, st_path, expected_sha256=saved["sha256"])
            with torch.no_grad():
                out_cond = fresh_cond(seq)
                out_causal = causal_dst(seq)
            self.assertEqual((out_cond.float() - out_causal.float()).abs().max().item(), 0.0)

            with self.assertRaises(ValueError):
                load_lora_safetensors(
                    TinyQwen35ConditionalModel(vocab_size=48, hidden=16, seed=23),
                    st_path,
                    expected_sha256="0" * 64,
                )

            with safe_open(str(st_path), framework="pt", device="cpu") as f_orig:
                orig_meta = dict(f_orig.metadata())
                orig_tensors = {k: f_orig.get_tensor(k) for k in f_orig.keys()}

            missing_path = tmp_dir / "missing.safetensors"
            dropped_key = sorted(orig_tensors.keys())[0]
            save_file(
                {k: v for k, v in orig_tensors.items() if k != dropped_key},
                str(missing_path),
                metadata=orig_meta,
            )
            with self.assertRaises(ValueError):
                load_lora_safetensors(TinyQwen35ConditionalModel(vocab_size=48, hidden=16, seed=23), missing_path)

            extra_path = tmp_dir / "extra.safetensors"
            save_file(
                {**orig_tensors, "bogus.layer.lora_A": torch.zeros((4, 16), dtype=torch.bfloat16)},
                str(extra_path),
                metadata=orig_meta,
            )
            with self.assertRaises(ValueError):
                load_lora_safetensors(TinyQwen35ConditionalModel(vocab_size=48, hidden=16, seed=23), extra_path)

            bad_shape_path = tmp_dir / "bad_shape.safetensors"
            bad_tensors = dict(orig_tensors)
            bad_tensors[dropped_key] = torch.zeros((4, 7), dtype=torch.bfloat16)
            save_file(bad_tensors, str(bad_shape_path), metadata=orig_meta)
            with self.assertRaises(ValueError):
                load_lora_safetensors(
                    TinyQwen35ConditionalModel(vocab_size=48, hidden=16, seed=23), bad_shape_path
                )

    def test_exact_causal_shift_and_prompt_masking(self):
        torch.manual_seed(41)
        vocab = 12
        full_ids = torch.tensor([[2, 5, 8, 3, 10]], dtype=torch.long)
        prompt_len = 3
        raw_logits = torch.randn((1, 5, vocab), dtype=torch.float32, requires_grad=True)

        stats = sequence_completion_stats(raw_logits, full_ids, prompt_len=prompt_len)
        self.assertEqual(stats["token_count"], 2)

        lp_pos2 = F.log_softmax(raw_logits[0, 2], dim=-1)[3]
        lp_pos3 = F.log_softmax(raw_logits[0, 3], dim=-1)[10]
        expected_sum = lp_pos2 + lp_pos3
        self.assertAlmostEqual(stats["sum_logprob"].item(), expected_sum.item(), places=6)
        self.assertAlmostEqual(stats["mean_nll"].item(), (-expected_sum / 2.0).item(), places=6)

        stats["mean_nll"].backward()
        self.assertEqual(raw_logits.grad[0, : prompt_len - 1].abs().max().item(), 0.0)
        self.assertEqual(raw_logits.grad[0, 4].abs().max().item(), 0.0)
        self.assertGreater(raw_logits.grad[0, 2:4].abs().max().item(), 0.0)

        perturbed = raw_logits.detach().clone()
        perturbed[0, : prompt_len - 1] += 1e6
        perturbed[0, 4] -= 1e6
        stats_pert = sequence_completion_stats(perturbed, full_ids.view(-1), prompt_len=prompt_len)
        self.assertEqual(stats_pert["sum_logprob"].item(), stats["sum_logprob"].item())

        with self.assertRaises(ValueError):
            sequence_completion_stats(raw_logits.detach(), full_ids, prompt_len=0)
        with self.assertRaises(ValueError):
            sequence_completion_stats(raw_logits.detach(), full_ids, prompt_len=5)

    def test_b3_sft_p_dpo_and_b4_single_token_ftpo_objectives(self):
        b3_model = TinyQwen35CausalModel(vocab_size=32, hidden=16, seed=51)
        inject_lora(b3_model, rank=4, alpha=8.0, seed=101)
        chosen_seq = torch.tensor([[1, 4, 7, 12, 15, 18]], dtype=torch.long)
        rejected_seq = torch.tensor([[1, 4, 7, 12, 15, 29]], dtype=torch.long)
        prompt_len = 2

        with torch.no_grad():
            init_b3_nll = sequence_completion_stats(b3_model(chosen_seq), chosen_seq, prompt_len)["mean_nll"].item()
        opt_b3 = torch.optim.AdamW(trainable_lora_parameters(b3_model), lr=4e-2)
        for _ in range(8):
            opt_b3.zero_grad()
            loss_b3 = sequence_completion_stats(b3_model(chosen_seq), chosen_seq, prompt_len)["mean_nll"]
            loss_b3.backward()
            opt_b3.step()
        with torch.no_grad():
            final_b3_nll = sequence_completion_stats(b3_model(chosen_seq), chosen_seq, prompt_len)["mean_nll"].item()
        self.assertLess(final_b3_nll, init_b3_nll - 0.2)

        p_model = TinyQwen35CausalModel(vocab_size=32, hidden=16, seed=52)
        inject_lora(p_model, rank=4, alpha=8.0, seed=202)
        set_lora_enabled(p_model, False)
        with torch.no_grad():
            ref_c = sequence_completion_stats(p_model(chosen_seq), chosen_seq, prompt_len)["sum_logprob"].item()
            ref_r = sequence_completion_stats(p_model(rejected_seq), rejected_seq, prompt_len)["sum_logprob"].item()
        set_lora_enabled(p_model, True)

        c0 = sequence_completion_stats(p_model(chosen_seq), chosen_seq, prompt_len)
        r0 = sequence_completion_stats(p_model(rejected_seq), rejected_seq, prompt_len)
        step0 = dpo_step_loss(c0["sum_logprob"], r0["sum_logprob"], ref_c, ref_r, c0["mean_nll"], beta=0.1, sft_anchor=0.15)
        self.assertAlmostEqual(step0["reward_margin"].item(), 0.0, places=6)
        self.assertAlmostEqual(step0["dpo_loss"].item(), math.log(2.0), places=6)
        self.assertFalse(step0["prefer_chosen"])

        opt_p = torch.optim.AdamW(trainable_lora_parameters(p_model), lr=4e-2)
        for _ in range(8):
            opt_p.zero_grad()
            cs = sequence_completion_stats(p_model(chosen_seq), chosen_seq, prompt_len)
            rs = sequence_completion_stats(p_model(rejected_seq), rejected_seq, prompt_len)
            out_p = dpo_step_loss(
                cs["sum_logprob"], rs["sum_logprob"], ref_c, ref_r, cs["mean_nll"], beta=0.1, sft_anchor=0.15
            )
            out_p["loss"].backward()
            opt_p.step()

        with torch.no_grad():
            cs_f = sequence_completion_stats(p_model(chosen_seq), chosen_seq, prompt_len)
            rs_f = sequence_completion_stats(p_model(rejected_seq), rejected_seq, prompt_len)
            final_p = dpo_step_loss(
                cs_f["sum_logprob"], rs_f["sum_logprob"], ref_c, ref_r, cs_f["mean_nll"], beta=0.1, sft_anchor=0.15
            )
        self.assertTrue(final_p["prefer_chosen"])
        self.assertGreater(final_p["reward_margin"].item(), 0.05)
        self.assertLess(final_p["dpo_loss"].item(), math.log(2.0))
        self.assertLess(cs_f["mean_nll"].item(), c0["mean_nll"].item())

        div_offset = 3
        probe_logits = torch.randn((1, 6, 32), dtype=torch.float32, requires_grad=True)
        c_lp, r_lp, c_nll = single_token_divergence_logprobs(
            probe_logits, prompt_len=prompt_len, div_offset=div_offset, div_chosen_id=18, div_rejected_id=29
        )
        b4_probe = dpo_step_loss(c_lp, r_lp, -3.0, -3.0, c_nll, beta=0.1, sft_anchor=0.15)
        b4_probe["loss"].backward()
        pos = prompt_len + div_offset - 1
        self.assertEqual(pos, 4)
        self.assertEqual(probe_logits.grad[0, :pos].abs().max().item(), 0.0)
        self.assertEqual(probe_logits.grad[0, pos + 1 :].abs().max().item(), 0.0)
        self.assertGreater(probe_logits.grad[0, pos].abs().max().item(), 0.0)

        b4_model = TinyQwen35CausalModel(vocab_size=32, hidden=16, seed=53)
        inject_lora(b4_model, rank=4, alpha=8.0, seed=303)
        set_lora_enabled(b4_model, False)
        with torch.no_grad():
            ref_logits_b4 = b4_model(chosen_seq)
            b4_ref_c, b4_ref_r, _ = single_token_divergence_logprobs(
                ref_logits_b4, prompt_len, div_offset, 18, 29
            )
            init_gap = (b4_ref_c - b4_ref_r).item()
        set_lora_enabled(b4_model, True)
        opt_b4 = torch.optim.AdamW(trainable_lora_parameters(b4_model), lr=4e-2)
        for _ in range(8):
            opt_b4.zero_grad()
            pol_logits = b4_model(chosen_seq)
            pc, pr, pnll = single_token_divergence_logprobs(pol_logits, prompt_len, div_offset, 18, 29)
            dpo_step_loss(pc, pr, b4_ref_c.item(), b4_ref_r.item(), pnll, beta=0.1, sft_anchor=0.15)["loss"].backward()
            opt_b4.step()
        with torch.no_grad():
            end_logits = b4_model(chosen_seq)
            ec, er, _ = single_token_divergence_logprobs(end_logits, prompt_len, div_offset, 18, 29)
            final_gap = (ec - er).item()
        self.assertGreater(final_gap, init_gap + 0.5)

        # Orthogonal prefix-token readout check: when `lora_A` reads only coordinate >= 4,
        # updating B4 at `pos = 4` leaves logits before `div_offset` (`0..3`) bit-exact (`0.0`)
        # and post-divergence token embeddings (`index 5`) receive strictly 0.0 gradient.
        class _OrthogonalTokenModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.embed = nn.Embedding(8, 8)
                with torch.no_grad():
                    self.embed.weight.copy_(torch.eye(8, dtype=torch.float32))
                self.q_proj = nn.Linear(8, 12, bias=False)

            def forward(self, ids):
                return self.q_proj(self.embed(ids))

        orth_model = _OrthogonalTokenModel()
        inject_lora(orth_model, rank=2, alpha=4.0, seed=77, module_prefix="")
        with torch.no_grad():
            orth_model.q_proj.lora_A.data[:, :4].zero_()
        orth_seq = torch.tensor([[0, 1, 2, 3, 4, 5]], dtype=torch.long)  # pos = 2 + 3 - 1 = 4 -> token 4
        with torch.no_grad():
            before_orth = orth_model(orth_seq).clone()
        opt_orth = torch.optim.SGD(trainable_lora_parameters(orth_model), lr=0.5)
        for _ in range(5):
            opt_orth.zero_grad()
            ol = orth_model(orth_seq)
            oc, orj, onll = single_token_divergence_logprobs(
                ol, prompt_len=2, div_offset=3, div_chosen_id=6, div_rejected_id=7
            )
            dpo_step_loss(oc, orj, -2.5, -2.5, onll, beta=0.2, sft_anchor=0.15)["loss"].backward()
            opt_orth.step()
        with torch.no_grad():
            after_orth = orth_model(orth_seq)
        self.assertEqual((after_orth[0, :4] - before_orth[0, :4]).abs().max().item(), 0.0)
        self.assertGreater(
            (F.log_softmax(after_orth[0, 4], dim=-1)[6] - F.log_softmax(after_orth[0, 4], dim=-1)[7]).item(),
            (F.log_softmax(before_orth[0, 4], dim=-1)[6] - F.log_softmax(before_orth[0, 4], dim=-1)[7]).item(),
        )

    def test_standalone_colab_copy_and_tiny_hf_qwen35_integration(self):
        import shutil
        import subprocess
        from transformers.models.qwen3_5.configuration_qwen3_5 import Qwen3_5Config, Qwen3_5TextConfig
        from transformers import Qwen3_5ForCausalLM

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            shutil.copy2(ROOT / "toolloop_fix/lora_adapter.py", tmp_dir / "lora_adapter.py")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    (
                        f"import site; site.main(); import sys; sys.path.insert(0, {str(tmp_dir)!r}); "
                        "import lora_adapter; assert lora_adapter.LORA_ADAPTER_VERSION == 'pure_pytorch_qwen35_text_lora_v1'"
                    ),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            tc = Qwen3_5TextConfig(
                hidden_size=32,
                intermediate_size=64,
                num_hidden_layers=2,
                num_attention_heads=2,
                num_key_value_heads=1,
                head_dim=16,
                linear_num_key_heads=2,
                linear_num_value_heads=2,
                linear_key_head_dim=16,
                linear_value_head_dim=16,
                vocab_size=64,
                layer_types=["linear_attention", "full_attention"],
            )
            _ = Qwen3_5Config(text_config=tc.to_dict())
            hf_model = Qwen3_5ForCausalLM(tc).to(dtype=torch.bfloat16).eval()
            d0 = base_weight_digest(hf_model)
            ids = torch.tensor([[1, 5, 9, 12]], dtype=torch.long)
            with torch.no_grad():
                base_out = hf_model(input_ids=ids).logits.clone()
            info = inject_lora(hf_model, rank=4, alpha=8.0, seed=101)
            self.assertEqual(info["matched_module_count"], 15)
            self.assertEqual(base_weight_digest(hf_model), d0)
            with torch.no_grad():
                step0_out = hf_model(input_ids=ids).logits
            self.assertEqual((step0_out.float() - base_out.float()).abs().max().item(), 0.0)

            st_file = tmp_dir / "hf_tiny.safetensors"
            saved = save_lora_safetensors(hf_model, st_file)
            hf_model2 = Qwen3_5ForCausalLM(tc).to(dtype=torch.bfloat16)
            hf_model2.load_state_dict(
                {
                    k.replace(".base.", "."): v
                    for k, v in hf_model.state_dict().items()
                    if "lora_" not in k
                }
            )
            load_lora_safetensors(hf_model2, st_file, expected_sha256=saved["sha256"])
            with torch.no_grad():
                loaded_out = hf_model2(input_ids=ids).logits
            self.assertEqual((loaded_out.float() - step0_out.float()).abs().max().item(), 0.0)


class LoRAEvalRunnerAndValidationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "data/dev").mkdir(parents=True, exist_ok=True)
        self.cases_rel = "data/dev/test_cases.jsonl"
        (self.root / self.cases_rel).write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in PILOT_CASES),
            encoding="utf-8",
        )

        self.model_dir = self.root / "models/fake_qwen"
        self.model_dir.mkdir(parents=True, exist_ok=True)
        weight_file = self.model_dir / "model.safetensors"
        weight_file.write_bytes(b"fake-safetensors-header")
        model_manifest_obj = {
            "model_id": "Qwen/Qwen3.5-0.8B",
            "revision": "2fc06364715b967f1860aea9cf38778875588b17",
            "model_path": str(self.model_dir),
            "files": [
                {
                    "path": "model.safetensors",
                    "sha256": sha256_file(weight_file),
                    "size_bytes": weight_file.stat().st_size,
                }
            ],
        }
        self.model_manifest_path = self.root / "operations/model_manifest.json"
        self.model_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_manifest_path.write_text(json.dumps(model_manifest_obj, indent=2) + "\n", encoding="utf-8")
        self.model_manifest_sha = sha256_file(self.model_manifest_path)

        self.adapter_st_path = self.root / "models/adapters/P_s101/adapter.safetensors"
        self.adapter_st_path.parent.mkdir(parents=True, exist_ok=True)
        if HAS_TORCH_AND_SAFETENSORS:
            donor = TinyQwen35CausalModel(vocab_size=32, hidden=8, seed=19)
            inject_lora(donor, rank=4, alpha=8.0, seed=101)
            with torch.no_grad():
                for p in trainable_lora_parameters(donor):
                    p.add_(0.05)
            st_info = save_lora_safetensors(
                donor,
                self.adapter_st_path,
                extra_metadata={"adapter_id": "P_s101", "training_arm": "P"},
            )
            self.adapter_st_sha = st_info["sha256"]
        else:
            self.adapter_st_path.write_bytes(b"stdlib-test-adapter-bytes")
            self.adapter_st_sha = sha256_file(self.adapter_st_path)

        adapter_manifest_obj = {
            "schema_version": 1,
            "base_model_revision": "2fc06364715b967f1860aea9cf38778875588b17",
            "adapters": [
                {
                    "adapter_id": "P_s101",
                    "training_arm": "P",
                    "train_seed": 101,
                    "rank": 4,
                    "alpha": 8.0,
                    "safetensors_sha256": self.adapter_st_sha,
                }
            ],
        }
        self.adapter_manifest_path = self.root / "models/adapters/adapter_manifest.json"
        self.adapter_manifest_path.write_text(
            json.dumps(adapter_manifest_obj, indent=2) + "\n", encoding="utf-8"
        )
        self.adapter_manifest_sha = sha256_file(self.adapter_manifest_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _base_config(self, mode="end_to_end", arm="B0", with_lora=False):
        external_inputs = [
            {
                "name": "model_manifest",
                "path": str(self.model_manifest_path),
                "sha256": self.model_manifest_sha,
            }
        ]
        lora_block = None
        if with_lora:
            external_inputs.append(
                {
                    "name": "adapter_manifest",
                    "path": str(self.adapter_manifest_path),
                    "sha256": self.adapter_manifest_sha,
                }
            )
            lora_block = {
                "adapter_id": "P_s101",
                "training_arm": "P",
                "train_seed": 101,
                "rank": 4,
                "alpha": 8.0,
                "safetensors_path": str(self.adapter_st_path),
                "safetensors_sha256": self.adapter_st_sha,
            }
        return {
            "evidence_class": "model_inference",
            "evidence_kind": "model_inference",
            "mode": mode,
            "protocol_version": "qwen_xml_or_native_terminal_v1",
            "wording_profile": "explicit",
            "task_wording_version": TASK_WORDING_VERSION,
            "policy_name": "qwen35_lora_P" if with_lora else "qwen35_" + arm,
            "comparison_arm": arm,
            "tool_budget": 8,
            "turn_budget": 10,
            "max_context_tokens": 8192,
            "cases_path": self.cases_rel,
            "model": {
                "model_id": "Qwen/Qwen3.5-0.8B",
                "revision": "2fc06364715b967f1860aea9cf38778875588b17",
                "backend": "transformers",
                "model_path": str(self.model_dir),
                "device": "cpu",
                "dtype": "float32",
            },
            "sampling": {
                "do_sample": True,
                "temperature": 1.0,
                "top_p": 1.0,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 2.0,
                "repetition_penalty": 1.0,
                "max_new_tokens": 128,
                "enable_thinking": False,
                "seed": 1000,
            },
            "external_inputs": external_inputs,
            "lora_adapter": lora_block,
        }

    @staticmethod
    def _smart_or_loop_responder(messages, tools, seed, config, has_active_lora):
        request = json.loads(messages[1]["content"].split(EXPLICIT_TASK_PREFIX, 1)[1])
        domain = request["domain"]
        primary, locate = DOMAIN_TOOLS[domain]
        tool_msgs = [m for m in messages if m["role"] == "tool"]

        if not has_active_lora and config["comparison_arm"] == "B0" and request.get("identifier") == "ORD-OLD":
            step = len(tool_msgs)
            if step == 0:
                return xml_call(primary, identifier="ORD-OLD")
            if step == 1:
                return xml_call(locate, target="WRONG_TARGET")
            if step == 2:
                return xml_call(primary, identifier="ORD-OLD")
            return xml_call("stop_task", reason="INVALID_IDENTIFIER")

        if not tool_msgs:
            if not request.get("identifier"):
                return xml_call("ask_user", field="identifier")
            return xml_call(primary, identifier=request["identifier"])

        last = json.loads(tool_msgs[-1]["content"])
        code = last["code"]
        if code == "OK":
            return xml_call("finish_task", answer=last["value"], identifier=last["identifier"])
        if code == "RESOURCE_UNAVAILABLE":
            return xml_call("stop_task", reason="RESOURCE_UNAVAILABLE")
        if code in ("LOCATED", "USER_INFORMATION"):
            return xml_call(primary, identifier=last["identifier"])
        if code == "INVALID_IDENTIFIER":
            return xml_call(locate, target=request["target"])
        if code == "MISSING_IDENTIFIER":
            return xml_call("ask_user", field="identifier")
        if code in ("PRECONDITION", "RUNNING"):
            return xml_call("poll_job", identifier=request["identifier"])
        if code == "READY":
            return xml_call(primary, identifier=request["identifier"])
        if code == "TIMEOUT":
            return xml_call(primary, identifier=request["identifier"])
        return xml_call("stop_task", reason=code)

    def _assert_zero_validator_errors(self, out_dir, config):
        self.assertEqual(validate_records.config_errors(config, "model_inference"), [])
        self.assertEqual(
            validate_records.model_artifact_errors(
                out_dir, config, {"child_executable": sys.executable}
            ),
            [],
        )

    def test_proposal_section06_guardrails(self):
        for forbidden_arm in ("B1", "B2"):
            bad = self._base_config(mode="end_to_end", arm=forbidden_arm, with_lora=True)
            bad["case_ids"] = ["orders-normal-001"]
            bad["case_seeds"] = {"orders-normal-001": 100}
            with self.assertRaises(ValueError):
                validate_lora_explicit_e2e_config(bad)

        no_manifest = self._base_config(mode="end_to_end", arm="B0", with_lora=True)
        no_manifest["external_inputs"] = [no_manifest["external_inputs"][0]]
        no_manifest["case_ids"] = ["orders-normal-001"]
        no_manifest["case_seeds"] = {"orders-normal-001": 100}
        with self.assertRaises(ValueError):
            validate_lora_explicit_e2e_config(no_manifest)

    def test_explicit_e2e_base_and_lora_plus_v2_detector(self):
        selected_ids = [
            "orders-normal-001",
            "orders-recoverable-001",
            "orders-retry_required-001",
            "orders-clarification-001",
            "orders-unrecoverable-001",
        ]
        seeds = {cid: 1000 + idx * 20 for idx, cid in enumerate(selected_ids)}

        for label, with_lora in (("b0", False), ("lora_p", True)):
            cfg = self._base_config(mode="end_to_end", arm="B0", with_lora=with_lora)
            cfg["case_ids"] = selected_ids
            cfg["case_seeds"] = seeds
            cfg_path = self.root / f"cfg_e2e_{label}.json"
            cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            out_dir = self.root / f"out_e2e_{label}"

            with contextlib.redirect_stdout(io.StringIO()):
                rc = lora_eval_main(
                    ["explicit-e2e", "--config", str(cfg_path), "--output", str(out_dir)],
                    backend_factory=lambda c, t: FakeLoRABackend(c, t, responder=self._smart_or_loop_responder),
                    root=self.root,
                )
            self.assertEqual(rc, 0)
            self._assert_zero_validator_errors(out_dir, cfg)

            prov = json.loads((out_dir / "model_provenance.json").read_text(encoding="utf-8"))
            metrics = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
            results = [
                json.loads(line)
                for line in (out_dir / "case_results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            trajs = [
                json.loads(line)
                for line in (out_dir / "trajectories.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(prov["lora_eval_runner_version"], LORA_EVAL_RUNNER_VERSION)
            self.assertEqual(prov["detector_v2_version"], DETECTOR_V2_VERSION)
            self.assertEqual(prov["adapter_provenance"]["lora_enabled"], with_lora)

            rec_res = next(r for r in results if r["case_id"] == "orders-recoverable-001")
            rec_traj = next(t for t in trajs if t["case_id"] == "orders-recoverable-001")
            if not with_lora:
                self.assertFalse(rec_res["has_invalid_loop"])
                self.assertTrue(rec_res["has_invalid_loop_v2"])
                self.assertEqual(
                    rec_res["loop_flags_v2"][0]["type"],
                    "nonadjacent_or_multistep_repeat_without_progress",
                )
                self.assertTrue(rec_traj["has_invalid_loop_v2"])
                self.assertEqual(metrics["metrics"]["has_invalid_loop_v2"]["numerator"], 1)
            else:
                self.assertEqual(prov["adapter_provenance"]["safetensors_sha256"], self.adapter_st_sha)
                self.assertTrue(prov["adapter_provenance"]["base_weights_unchanged_on_load"])
                self.assertEqual(rec_res["evaluated_arm"], "P")
                self.assertTrue(rec_res["exact_grounded_structured_completion"])
                self.assertFalse(rec_res["has_invalid_loop_v2"])
                self.assertEqual(metrics["metrics"]["exact_grounded_structured_completion"]["numerator"], 4)

    def test_curated_prefix_shared_snapshot_single_and_multiseed(self):
        prefix_ids = [c["case_id"] for c in PILOT_CASES if c.get("fixed_prefix")]
        self.assertEqual(len(prefix_ids), 6)

        snapshots = set()
        non_sys_dialogues = set()
        rem_budgets = set()
        for arm, with_lora in (("B0", False), ("B1", False), ("B2", False), ("B0", True)):
            cfg = self._base_config(mode="fixed_failure_prefix", arm=arm, with_lora=with_lora)
            cfg["case_ids"] = prefix_ids
            cfg["case_seeds"] = {cid: 2000 + i * 20 for i, cid in enumerate(prefix_ids)}
            validate_curated_prefix_config(cfg)
            prep = prepare_curated_prefix(
                next(c for c in PILOT_CASES if c["case_id"] == "orders-recoverable-001"), cfg
            )
            snapshots.add(prep["context"]["prefix_snapshot_sha256"])
            non_sys_dialogues.add(prep["context"]["shared_non_system_dialogue_sha256"])
            rem_budgets.add(
                (prep["context"]["remaining_tool_budget"], prep["context"]["remaining_turn_budget"])
            )
            if arm != "B1":
                self.assertNotIn(prompt_for("B1"), prep["messages"][0]["content"])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(non_sys_dialogues), 1)
        self.assertEqual(rem_budgets, {(7, 9)})

        cfg_single = self._base_config(mode="fixed_failure_prefix", arm="B0", with_lora=True)
        cfg_single["prefix_schema_version"] = CURATED_PREFIX_SCHEMA_VERSION
        cfg_single["case_ids"] = prefix_ids
        cfg_single["case_seeds"] = {cid: 3000 + i * 20 for i, cid in enumerate(prefix_ids)}
        cfg_single_path = self.root / "cfg_curated_single.json"
        cfg_single_path.write_text(json.dumps(cfg_single, indent=2) + "\n", encoding="utf-8")
        out_single = self.root / "out_curated_single"

        with contextlib.redirect_stdout(io.StringIO()):
            lora_eval_main(
                ["curated-prefix", "--config", str(cfg_single_path), "--output", str(out_single)],
                backend_factory=lambda c, t: FakeLoRABackend(c, t, responder=self._smart_or_loop_responder),
                root=self.root,
            )
        self._assert_zero_validator_errors(out_single, cfg_single)
        single_metrics = json.loads((out_single / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(single_metrics["metrics"]["exact_structured_completion_recovery"]["numerator"], 6)
        self.assertEqual(
            single_metrics["metrics"]["recoverable_exact_structured_completion_recovery"]["numerator"], 3
        )
        self.assertEqual(
            single_metrics["metrics"]["retry_required_exact_structured_completion_recovery"]["numerator"], 3
        )

        load_counter = {"loads": 0}
        cfg_multi = self._base_config(mode="fixed_failure_prefix", arm="B0", with_lora=True)
        cfg_multi["case_ids"] = prefix_ids
        cfg_multi["seed_schedules"] = [
            {"schedule_id": "s1", "case_seeds": {cid: 4000 + i * 20 for i, cid in enumerate(prefix_ids)}},
            {"schedule_id": "s2", "case_seeds": {cid: 5000 + i * 20 for i, cid in enumerate(prefix_ids)}},
        ]
        cfg_multi_path = self.root / "cfg_curated_multi.json"
        cfg_multi_path.write_text(json.dumps(cfg_multi, indent=2) + "\n", encoding="utf-8")
        out_multi = self.root / "out_curated_multi"

        with contextlib.redirect_stdout(io.StringIO()):
            lora_eval_main(
                ["curated-prefix", "--config", str(cfg_multi_path), "--output", str(out_multi)],
                backend_factory=lambda c, t: FakeLoRABackend(
                    c, t, responder=self._smart_or_loop_responder, counter=load_counter
                ),
                root=self.root,
            )
        self.assertEqual(load_counter["loads"], 1)
        self._assert_zero_validator_errors(out_multi, cfg_multi)
        multi_metrics = json.loads((out_multi / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(multi_metrics["schedule_ids"], ["s1", "s2"])
        self.assertEqual(multi_metrics["metrics"]["case_count"], 12)
        self.assertEqual(len(multi_metrics["by_case"]), 6)

    def test_explicit_e2e_multiseed_single_load_and_tagging(self):
        selected_ids = ["orders-normal-001", "files-recoverable-001", "jobs-retry_required-001"]
        load_counter = {"loads": 0}
        cfg = self._base_config(mode="end_to_end", arm="B0", with_lora=True)
        cfg["case_ids"] = selected_ids
        cfg["seed_schedules"] = [
            {"schedule_id": sid, "case_seeds": {cid: 6000 + s_idx * 200 + i * 20 for i, cid in enumerate(selected_ids)}}
            for s_idx, sid in enumerate(("s1", "s2", "s3"))
        ]
        validate_lora_explicit_e2e_multiseed_config(cfg)
        cfg_path = self.root / "cfg_e2e_multiseed.json"
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        out_dir = self.root / "out_e2e_multiseed"

        with contextlib.redirect_stdout(io.StringIO()):
            lora_eval_main(
                ["explicit-e2e-multiseed", "--config", str(cfg_path), "--output", str(out_dir)],
                backend_factory=lambda c, t: FakeLoRABackend(
                    c, t, responder=self._smart_or_loop_responder, counter=load_counter
                ),
                root=self.root,
            )
        self.assertEqual(load_counter["loads"], 1)
        self._assert_zero_validator_errors(out_dir, cfg)

        rows = [
            json.loads(line)
            for line in (out_dir / "case_results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows[0]["case_id"], "orders-normal-001@s1")
        self.assertEqual(rows[0]["base_case_id"], "orders-normal-001")
        self.assertEqual(rows[0]["schedule_id"], "s1")
        self.assertIn("has_invalid_loop", rows[0])
        self.assertIn("has_invalid_loop_v2", rows[0])
        self.assertIn("loop_flags_v2", rows[0])

        metrics = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["schedule_ids"], ["s1", "s2", "s3"])
        self.assertEqual(metrics["unique_case_count"], 3)
        self.assertEqual(len(metrics["by_schedule"]), 3)
        self.assertEqual(len(metrics["by_case"]), 3)


if __name__ == "__main__":
    unittest.main()
