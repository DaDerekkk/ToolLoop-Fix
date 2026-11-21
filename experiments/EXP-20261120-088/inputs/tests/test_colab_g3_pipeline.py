"""Offline unit tests for ITER-009 Colab L4 G3 training worker, lifecycle, and verifier.

Covers:
  1. Detached worker `started.lock` exclusivity and `bundle.zlib` + `armed.txt` gate.
  2. End-to-end CPU execution of `colab_g3_train_worker` across `B3` (SFT),
     `B4` (single-token FTPO-style DPO), `B5` (clean expert DPO), and `P` (ToolLoop-Fix DPO)
     with a tiny causal Qwen-like `nn.Module` and deterministic XML tokenizer, verifying
     loss decrease, `base_weights_unchanged == True`, valid `g3_training_receipt.json`,
     and multi-part `<480 KB` `TLP_CHUNK_V1` frames.
  3. Bit-identical safetensors chunk reassembly and rejection of wrong nonces, missing
     parts, duplicate indices, and corrupted payloads.
  4. Fail-closed B4 remote divergence offset verification.
  5. Stateful mocked `colab` CLI lifecycle (`run_colab_g3_lifecycle.py`) including
     pre-existing session preservation, 2-way pre-arm sentinel, transient chunk retry,
     `lora_eval_runner.verify_adapter_manifest` compatibility, and mandatory `finally:`
     teardown on `KeyboardInterrupt`.
  6. Offline `verify_colab_g3_training.py` acceptance on valid operations and rejection
     of unreleased sessions, altered baseline sessions, non-L4 GPUs, mutated base digests,
     and tampered `.safetensors` files.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zlib

import torch
import torch.nn as nn
import torch.nn.functional as F

ARCHIVE_ROOT = Path(__file__).resolve().parents[1]
if str(ARCHIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(ARCHIVE_ROOT))

from scripts import colab_g3_train_worker as worker
from scripts import run_colab_g3_lifecycle as lifecycle
from scripts import verify_colab_g3_training as verifier
from toolloop_fix import lora_adapter
from toolloop_fix.lora_eval_runner import verify_adapter_manifest


class TinyXmlTokenizer:
    """Deterministic small-vocab XML tokenizer with exact prefix invariance."""

    def __init__(self, vocab_size=128):
        self.vocab_size = vocab_size
        self.pad_token_id = 0
        self.eos_token_id = 2
        self.eos_token = "<|im_end|>"
        fixed_pieces = [
            "<|pad|>",
            "<|im_start|>",
            "<|im_end|>",
            "<tool_call>",
            "</tool_call>",
            "<think>",
            "</think>",
            "\n",
            "\n\n",
            "<function=get_order>",
            "<function=find_order>",
            "<function=read_file>",
            "<function=search_file>",
            "<function=ask_user>",
            "<function=finish_task>",
            "<function=stop_task>",
            "</function>",
            "<parameter=identifier>",
            "<parameter=target>",
            "<parameter=answer>",
            "<parameter=reason>",
            "<parameter=field>",
            "<parameter=wrong_param>",
            "</parameter>",
            "ORD-101",
            "ORD-202",
            "ORD-999",
            "ORD-SYNTH-PROBE-9001",
            "lookup_key:t1",
            "lookup_key:t2",
            "lookup_key:synth_probe_v1",
            "VAL_OK_1",
            "RESOURCE_UNAVAILABLE",
            "INVALID_IDENTIFIER",
            "TIMEOUT",
            "identifier",
            "Cannot",
            "proceed",
        ]
        self._piece_to_id = {p: idx for idx, p in enumerate(fixed_pieces)}
        self._id_to_piece = {idx: p for p, idx in self._piece_to_id.items()}
        self._next_id = len(fixed_pieces)
        escaped = sorted((re.escape(p) for p in fixed_pieces if len(p) > 1), key=len, reverse=True)
        self._regex = re.compile("|".join(escaped + [r"[A-Za-z0-9_:/.-]+", r"."]))

    def _tok(self, piece):
        if piece in self._piece_to_id:
            return self._piece_to_id[piece]
        if self._next_id < self.vocab_size:
            tid = self._next_id
            self._next_id += 1
            self._piece_to_id[piece] = tid
            self._id_to_piece[tid] = piece
            return tid
        digest_bucket = 40 + (int(hashlib.sha256(piece.encode("utf-8")).hexdigest()[:8], 16) % (self.vocab_size - 40))
        return digest_bucket

    def apply_chat_template(
        self,
        messages,
        tools=None,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    ):
        if enable_thinking is not False:
            raise ValueError("enable_thinking must be False")
        chunks = ["<|im_start|>"]
        for msg in messages:
            chunks.append(msg.get("role", "user"))
            chunks.append("\n")
            chunks.append(msg.get("content", ""))
            chunks.append("<|im_end|>\n")
        if add_generation_prompt:
            chunks.append("<|im_start|>assistant\n<think>\n\n</think>\n\n")
        rendered = "".join(chunks)
        return self.encode(rendered) if tokenize else rendered

    def encode(self, text, add_special_tokens=False):
        return [self._tok(m.group(0)) for m in self._regex.finditer(text)]

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": self.encode(text, add_special_tokens=add_special_tokens)}

    def decode(self, token_ids, skip_special_tokens=False):
        return "".join(self._id_to_piece.get(int(t), f"<u{int(t)}>") for t in token_ids)


class TinyCausalBlock(nn.Module):
    def __init__(self, hidden_size=32, intermediate_size=48):
        super().__init__()
        self.q_proj = nn.Linear(hidden_size, hidden_size, bias=False)
        self.k_proj = nn.Linear(hidden_size, hidden_size, bias=False)
        self.v_proj = nn.Linear(hidden_size, hidden_size, bias=False)
        self.o_proj = nn.Linear(hidden_size, hidden_size, bias=False)
        self.gate_proj = nn.Linear(hidden_size, intermediate_size, bias=False)
        self.up_proj = nn.Linear(hidden_size, intermediate_size, bias=False)
        self.down_proj = nn.Linear(intermediate_size, hidden_size, bias=False)

    def forward(self, x):
        _, seq_len, hidden = x.shape
        q = self.q_proj(x)
        k = self.k_proj(x)
        v = self.v_proj(x)
        scores = (q @ k.transpose(-1, -2)) * (hidden ** -0.5)
        causal_mask = torch.ones((seq_len, seq_len), dtype=torch.bool, device=x.device).triu(1)
        scores = scores.masked_fill(causal_mask, float("-inf"))
        attn = torch.softmax(scores, dim=-1) @ v
        x = x + self.o_proj(attn)
        mlp = self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))
        return x + mlp


class TinyQwenLikeCausalLM(nn.Module):
    """Tiny causal LM placing decoder blocks at `model.layers.*` just like `Qwen3_5ForCausalLM`."""

    def __init__(self, vocab_size=128, hidden_size=32, num_layers=2, seed=7):
        super().__init__()
        gen = torch.Generator(device="cpu").manual_seed(seed)
        self.model = nn.Module()
        self.model.embed_tokens = nn.Embedding(vocab_size, hidden_size)
        self.model.layers = nn.ModuleList([TinyCausalBlock(hidden_size) for _ in range(num_layers)])
        self.lm_head = nn.Linear(hidden_size, vocab_size, bias=False)
        with torch.no_grad():
            for p in self.parameters():
                p.normal_(0.0, 0.04, generator=gen)

    def forward(self, input_ids, attention_mask=None, use_cache=False):
        h = self.model.embed_tokens(input_ids)
        for block in self.model.layers:
            h = block(h)
        logits = self.lm_head(h)
        return SimpleNamespace(logits=logits)


def _build_synthetic_g3_bundle(tokenizer):
    """Build a valid mini G3 bundle with exact B4 divergence metadata under `tokenizer`."""
    raw_pairs = [
        (
            "EX-01",
            [{"role": "system", "content": "Tool env."}, {"role": "user", "content": "Lookup ORD-999 target lookup_key:t1 after INVALID_IDENTIFIER"}],
            "<tool_call>\n<function=find_order>\n<parameter=target>\nlookup_key:t1\n</parameter>\n</function>\n</tool_call><|im_end|>",
            "<tool_call>\n<function=get_order>\n<parameter=identifier>\nORD-999\n</parameter>\n</function>\n</tool_call><|im_end|>",
        ),
        (
            "EX-02",
            [{"role": "system", "content": "Tool env."}, {"role": "user", "content": "Retry ORD-101 after transient TIMEOUT"}],
            "<tool_call>\n<function=get_order>\n<parameter=identifier>\nORD-101\n</parameter>\n</function>\n</tool_call><|im_end|>",
            "<tool_call>\n<function=finish_task>\n<parameter=answer>\n0\n</parameter>\n<parameter=identifier>\nORD-101\n</parameter>\n</function>\n</tool_call><|im_end|>",
        ),
        (
            "EX-03",
            [{"role": "system", "content": "Tool env."}, {"role": "user", "content": "Stop truthfully after RESOURCE_UNAVAILABLE on ORD-202"}],
            "<tool_call>\n<function=stop_task>\n<parameter=reason>\nRESOURCE_UNAVAILABLE\n</parameter>\n</function>\n</tool_call><|im_end|>",
            "Cannot proceed ORD-202<|im_end|>",
        ),
        (
            "EX-04",
            [{"role": "system", "content": "Tool env."}, {"role": "user", "content": "Finish grounded VAL_OK_1 for ORD-101"}],
            "<tool_call>\n<function=finish_task>\n<parameter=answer>\nVAL_OK_1\n</parameter>\n<parameter=identifier>\nORD-101\n</parameter>\n</function>\n</tool_call><|im_end|>",
            "<tool_call>\n<function=get_order>\n<parameter=identifier>\nORD-101\n</parameter>\n</function>\n</tool_call><|im_end|>",
        ),
    ]
    tools = worker.DEFAULT_PUBLIC_PROBE["tools"]

    p_rows, b3_rows, b4_rows, b5_rows = [], [], [], []
    for ex_id, messages, chosen_text, rejected_text in raw_pairs:
        prompt = tokenizer.apply_chat_template(
            messages, tools=tools, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        p_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        c_full = tokenizer(prompt + chosen_text, add_special_tokens=False)["input_ids"]
        r_full = tokenizer(prompt + rejected_text, add_special_tokens=False)["input_ids"]
        c_ids = c_full[len(p_ids) :]
        r_ids = r_full[len(p_ids) :]
        div_offset = next(i for i in range(min(len(c_ids), len(r_ids))) if c_ids[i] != r_ids[i])

        base_row = {
            "example_id": ex_id,
            "messages": messages,
            "tools": tools,
            "chosen_text": chosen_text,
            "prompt_token_count": len(p_ids),
            "chosen_token_count": len(c_ids),
        }
        b3_rows.append({**base_row, "arm": "B3"})
        p_rows.append(
            {
                **base_row,
                "arm": "P",
                "rejected_text": rejected_text,
                "rejected_token_count": len(r_ids),
                "first_divergence_offset": div_offset,
            }
        )
        b4_rows.append(
            {
                **base_row,
                "arm": "B4",
                "rejected_text": rejected_text,
                "rejected_token_count": len(r_ids),
                "first_divergence_offset": div_offset,
                "shared_prefix_token_count": div_offset,
                "full_sequence_divergence_index": len(p_ids) + div_offset,
                "divergence_chosen_token_id": c_ids[div_offset],
                "divergence_rejected_token_id": r_ids[div_offset],
            }
        )
        b5_rows.append(
            {
                **base_row,
                "example_id": "B5-" + ex_id,
                "arm": "B5",
                "rejected_text": rejected_text,
                "rejected_token_count": len(r_ids),
                "first_divergence_offset": div_offset,
            }
        )

    datasets = {
        "p_toolloop_fix_dpo.jsonl": p_rows,
        "b3_error_sft.jsonl": b3_rows,
        "b4_single_token_ftpo_style.jsonl": b4_rows,
        "b5_general_expert_dpo.jsonl": b5_rows,
    }
    dataset_files = {}
    for name, rows in datasets.items():
        raw_jsonl = "".join(json.dumps(r) + "\n" for r in rows).encode("utf-8")
        dataset_files[name] = {
            "example_count": len(rows),
            "sha256": worker.sha256_bytes(raw_jsonl),
        }

    return {
        "schema_version": "iter8_g3_action_preference_v1",
        "g3_dataset_ready": True,
        "g2_passed_verified": True,
        "g2_frozen_verdict_sha256": "a" * 64,
        "dataset_governance_tier": "user_delegated_triple_blind_silver_adjudication_v1",
        "model_target": {
            "model_id": worker.MODEL_ID,
            "revision": worker.REVISION,
        },
        "dataset_files": dataset_files,
        "datasets": datasets,
    }


def _make_local_test_hooks(tokenizer):
    dummy_files = {"config.json": b'{"tiny": true}\n', "tokenizer.json": b'{"vocab": 128}\n'}
    expected_spec = {
        name: {"sha256": worker.sha256_bytes(payload), "size_bytes": len(payload)}
        for name, payload in dummy_files.items()
    }

    def prepare_snapshot_dir(tmp_dir):
        for name, payload in dummy_files.items():
            (tmp_dir / name).write_bytes(payload)
        return tmp_dir

    def build_model_and_tokenizer(_snapshot_dir):
        return TinyQwenLikeCausalLM(vocab_size=tokenizer.vocab_size, hidden_size=32, num_layers=2, seed=7), tokenizer

    return {
        "skip_pip_install": True,
        "device": "cpu",
        "prepare_snapshot_dir": prepare_snapshot_dir,
        "expected_test_model_files": expected_spec,
        "build_model_and_tokenizer": build_model_and_tokenizer,
        "gpu_metadata": {
            "name": "NVIDIA L4",
            "memory_bytes": 24159191040,
            "compute_capability": [8, 9],
        },
    }


class TestColabG3Pipeline(unittest.TestCase):
    def setUp(self):
        self.tokenizer = TinyXmlTokenizer(vocab_size=128)
        self.bundle = _build_synthetic_g3_bundle(self.tokenizer)
        self.raw_bundle = (json.dumps(self.bundle) + "\n").encode("utf-8")
        self.bundle_sha = worker.sha256_bytes(self.raw_bundle)
        self.compressed_bundle = zlib.compress(self.raw_bundle, 9)
        self.bundle_zlib_sha = worker.sha256_bytes(self.compressed_bundle)
        self.four_jobs = [
            {
                "adapter_id": "B3_s101",
                "arm": "B3",
                "objective": "sft",
                "dataset": "b3_error_sft.jsonl",
                "seed": 101,
                "lr": 1.5e-2,
                "epochs": 4,
                "grad_accum": 2,
            },
            {
                "adapter_id": "B4_s101",
                "arm": "B4",
                "objective": "single_token_dpo",
                "dataset": "b4_single_token_ftpo_style.jsonl",
                "seed": 101,
                "lr": 1.5e-2,
                "beta": 0.3,
                "sft_anchor": 0.1,
                "epochs": 4,
                "grad_accum": 2,
            },
            {
                "adapter_id": "B5_s101",
                "arm": "B5",
                "objective": "dpo",
                "dataset": "b5_general_expert_dpo.jsonl",
                "seed": 101,
                "lr": 1.5e-2,
                "beta": 0.2,
                "sft_anchor": 0.2,
                "epochs": 4,
                "grad_accum": 2,
            },
            {
                "adapter_id": "P_s101",
                "arm": "P",
                "objective": "dpo",
                "dataset": "p_toolloop_fix_dpo.jsonl",
                "seed": 101,
                "lr": 1.5e-2,
                "beta": 0.2,
                "sft_anchor": 0.2,
                "epochs": 4,
                "grad_accum": 2,
            },
        ]

    def test_worker_started_lock_and_arm_gate(self):
        with tempfile.TemporaryDirectory() as td:
            wdir = Path(td)
            nonce = "0123456789abcdef0123456789abcdef"
            (wdir / "nonce.txt").write_text(nonce + "\n", encoding="utf-8")
            (wdir / "train_job_config.json").write_text(
                json.dumps({"arm_deadline_seconds": 0.25, "expected_bundle_sha256": self.bundle_sha}),
                encoding="utf-8",
            )
            # Only armed.txt present, bundle.zlib MISSING -> must NOT proceed past waiting_for_arm!
            (wdir / "armed.txt").write_text(nonce + "\n", encoding="utf-8")

            rc = worker.run_worker(workdir=wdir, local_test_hooks=_make_local_test_hooks(self.tokenizer))
            self.assertEqual(rc, 1)
            self.assertEqual((wdir / "stage.txt").read_text().strip(), "receipt_published_failed")

            raw_frame = (wdir / "receipt_frame.txt").read_bytes()
            _, receipt = worker.receive_framed_json(raw_frame, nonce)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["stage"], "waiting_for_arm")
            self.assertEqual(receipt["error_code"], "arm_deadline_exceeded")
            self.assertFalse(receipt["training_performed"])

            # Second invocation in same workdir must return 3 immediately without touching files
            rc_dup = worker.run_worker(workdir=wdir, local_test_hooks=_make_local_test_hooks(self.tokenizer))
            self.assertEqual(rc_dup, 3)

    def test_worker_trains_b3_b4_b5_p_and_packs_chunks(self):
        with tempfile.TemporaryDirectory() as td:
            wdir = Path(td)
            nonce = "fedcba9876543210fedcba9876543210"
            (wdir / "nonce.txt").write_text(nonce + "\n", encoding="utf-8")
            (wdir / "bundle.zlib").write_bytes(self.compressed_bundle)
            (wdir / "armed.txt").write_text(nonce + "\n", encoding="utf-8")

            probe_cfg = copy.deepcopy(worker.DEFAULT_PUBLIC_PROBE)
            probe_cfg["max_new_tokens"] = 12
            cfg = {
                "expected_bundle_sha256": self.bundle_sha,
                "expected_bundle_zlib_sha256": self.bundle_zlib_sha,
                "lora_rank": 4,
                "lora_alpha": 8.0,
                "max_chunk_raw_bytes": 512,  # force multi-part chunking on tiny adapter!
                "training_jobs": self.four_jobs,
                "public_probe": probe_cfg,
            }
            (wdir / "train_job_config.json").write_text(json.dumps(cfg), encoding="utf-8")

            rc = worker.run_worker(workdir=wdir, local_test_hooks=_make_local_test_hooks(self.tokenizer))
            self.assertEqual(rc, 0)
            self.assertEqual((wdir / "stage.txt").read_text().strip(), "receipt_published_completed")

            frame_bytes = (wdir / "receipt_frame.txt").read_bytes()
            raw_receipt, receipt = worker.receive_framed_json(frame_bytes, nonce)
            self.assertEqual(raw_receipt, (wdir / "g3_training_receipt.json").read_bytes())
            self.assertEqual(receipt["status"], "completed")
            self.assertTrue(receipt["training_performed"])
            self.assertFalse(receipt["tool_executed"])
            self.assertTrue(receipt["base_weights_unchanged"])
            self.assertEqual(
                receipt["initial_base_weight_digest"],
                receipt["final_base_weight_digest"],
            )
            self.assertEqual(
                receipt["stage_history"],
                [
                    "waiting_for_arm",
                    "armed",
                    "install_transformers",
                    "runtime_check",
                    "anonymous_public_download",
                    "verify_public_snapshot",
                    "verify_training_bundle",
                    "load_base_model",
                    "tokenize_and_precompute_ref",
                    "train_B3_s101",
                    "train_B4_s101",
                    "train_B5_s101",
                    "train_P_s101",
                    "pack_chunks",
                ],
            )
            self.assertEqual(receipt["reference_precompute"]["b4_divergence_verified_count"], 4)
            self.assertEqual(len(receipt["adapters"]), 4)

            for item in receipt["adapters"]:
                aid = item["adapter_id"]
                self.assertTrue(item["loss_decreased"], f"{aid} did not decrease loss: {item}")
                self.assertLess(item["final_loss"], item["initial_loss"], f"{aid} final >= initial")
                if item["objective"] == "sft":
                    self.assertIsNone(item["post_train_reward_accuracy"])
                else:
                    self.assertGreaterEqual(item["post_train_reward_accuracy"], 0.75)

                self.assertGreaterEqual(item["chunk_count"], 2)
                frames = [(wdir / rel).read_bytes() for rel in item["chunk_files"]]
                for fbytes in frames:
                    self.assertLess(len(fbytes), worker.MAX_CHUNK_FRAME_BYTES)

                rebuilt = worker.decode_and_reassemble_chunks(
                    frames,
                    expected_nonce=nonce,
                    expected_adapter_id=aid,
                    expected_safetensors_sha256=item["safetensors_sha256"],
                )
                orig = (wdir / "adapters" / f"{aid}.safetensors").read_bytes()
                self.assertEqual(rebuilt, orig)

                # Load reassembled adapter into fresh TinyQwenLikeCausalLM and verify base invariance
                fresh_model = TinyQwenLikeCausalLM(
                    vocab_size=self.tokenizer.vocab_size, hidden_size=32, num_layers=2, seed=7
                )
                pre_digest = lora_adapter.base_weight_digest(fresh_model)
                loaded = lora_adapter.load_lora_safetensors(
                    fresh_model,
                    wdir / "adapters" / f"{aid}.safetensors",
                    expected_sha256=item["safetensors_sha256"],
                )
                self.assertEqual(pre_digest, lora_adapter.base_weight_digest(fresh_model))
                self.assertEqual(loaded["tensor_count"], item["tensor_count"])

    def test_chunk_reassembly_rejects_tamper_missing_and_wrong_nonce(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nonce = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            model = TinyQwenLikeCausalLM(vocab_size=64, hidden_size=16, num_layers=1, seed=3)
            lora_adapter.inject_lora(model, rank=4, alpha=8.0, seed=101)
            with torch.no_grad():
                for p in lora_adapter.trainable_lora_parameters(model):
                    p.add_(0.05)
            st_path = root / "P_s101.safetensors"
            save_info = lora_adapter.save_lora_safetensors(model, st_path)
            chunk_info = worker.pack_safetensors_to_chunks(
                st_path, root / "chunks", nonce, "P_s101", max_chunk_raw_bytes=300
            )
            frames = [(root / rel).read_bytes() for rel in chunk_info["chunk_files"]]
            self.assertGreaterEqual(len(frames), 2)

            # Valid reassembly succeeds
            rebuilt = worker.decode_and_reassemble_chunks(
                frames, nonce, "P_s101", save_info["sha256"]
            )
            self.assertEqual(rebuilt, st_path.read_bytes())

            # Wrong nonce rejected
            with self.assertRaisesRegex(ValueError, "nonce mismatch"):
                worker.decode_and_reassemble_chunks(
                    frames, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "P_s101", save_info["sha256"]
                )

            # Wrong adapter_id rejected
            with self.assertRaisesRegex(ValueError, "adapter_id mismatch"):
                worker.decode_and_reassemble_chunks(
                    frames, nonce, "B3_s101", save_info["sha256"]
                )

            # Missing chunk rejected
            with self.assertRaisesRegex(ValueError, "missing chunk parts"):
                worker.decode_and_reassemble_chunks(
                    frames[:-1], nonce, "P_s101", save_info["sha256"]
                )

            # Duplicate chunk index rejected
            with self.assertRaisesRegex(ValueError, "duplicate chunk index"):
                worker.decode_and_reassemble_chunks(
                    [frames[0], frames[0]], nonce, "P_s101", save_info["sha256"]
                )

            # Tampered base64 payload rejected by part SHA256
            parts = frames[0].decode("ascii").rstrip("\n").split(":")
            raw_p0 = zlib.compress(b"tampered", 9)
            parts[-1] = worker.base64.b64encode(raw_p0).decode("ascii")
            tampered_frame = (":".join(parts) + "\n").encode("ascii")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                worker.decode_and_reassemble_chunks(
                    [tampered_frame] + frames[1:], nonce, "P_s101", save_info["sha256"]
                )

            # Wrong expected safetensors SHA256 rejected
            with self.assertRaisesRegex(ValueError, "reassembled safetensors SHA256 mismatch"):
                worker.decode_and_reassemble_chunks(
                    frames, nonce, "P_s101", "f" * 64
                )

    def test_worker_rejects_b4_divergence_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            wdir = Path(td)
            nonce = "11112222333344445555666677778888"
            bad_bundle = copy.deepcopy(self.bundle)
            bad_bundle["datasets"]["b4_single_token_ftpo_style.jsonl"][0]["first_divergence_offset"] += 1
            raw_bad = (json.dumps(bad_bundle) + "\n").encode("utf-8")

            (wdir / "nonce.txt").write_text(nonce + "\n", encoding="utf-8")
            (wdir / "bundle.zlib").write_bytes(zlib.compress(raw_bad, 9))
            (wdir / "armed.txt").write_text(nonce + "\n", encoding="utf-8")
            (wdir / "train_job_config.json").write_text(
                json.dumps(
                    {
                        "expected_bundle_sha256": worker.sha256_bytes(raw_bad),
                        "training_jobs": [self.four_jobs[1]],
                    }
                ),
                encoding="utf-8",
            )

            rc = worker.run_worker(workdir=wdir, local_test_hooks=_make_local_test_hooks(self.tokenizer))
            self.assertEqual(rc, 1)
            _, receipt = worker.receive_framed_json((wdir / "receipt_frame.txt").read_bytes(), nonce)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["stage"], "tokenize_and_precompute_ref")
            self.assertEqual(receipt["error_code"], "b4_divergence_offset_mismatch")

    def test_lifecycle_end_to_end_and_verifier_acceptance_and_rejections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle_path = root / "public_colab_training_bundle.json"
            bundle_path.write_bytes(self.raw_bundle)

            # Local model manifest matching PINNED_MODEL_FILES
            model_manifest_path = root / "model_manifest.json"
            model_manifest_obj = {
                "schema_version": 1,
                "model_id": worker.MODEL_ID,
                "revision": worker.REVISION,
                "model_path": str(root / "dummy_model"),
                "files": [
                    {"path": k, "sha256": v["sha256"], "size_bytes": v["size_bytes"]}
                    for k, v in sorted(worker.PINNED_MODEL_FILES.items())
                ],
            }
            model_manifest_path.write_text(json.dumps(model_manifest_obj), encoding="utf-8")

            remote_fs_root = root / "mock_remote"
            remote_fs_root.mkdir()

            probe_cfg = copy.deepcopy(worker.DEFAULT_PUBLIC_PROBE)
            probe_cfg["max_new_tokens"] = 10

            lc_config_path = root / "lifecycle_config.json"
            lc_config = {
                "lora_adapter_path": str(ARCHIVE_ROOT / "toolloop_fix" / "lora_adapter.py"),
                "worker_script_path": str(ARCHIVE_ROOT / "scripts" / "colab_g3_train_worker.py"),
                "training_bundle_path": str(bundle_path),
                "expected_bundle_sha256": self.bundle_sha,
                "lora_rank": 4,
                "lora_alpha": 8.0,
                "max_chunk_raw_bytes": 512,
                "wall_limit_seconds": 300.0,
                "poll_interval_seconds": 0.01,
                "training_jobs": [self.four_jobs[0], self.four_jobs[3]],
                "public_probe": probe_cfg,
            }
            lc_config_path.write_text(json.dumps(lc_config), encoding="utf-8")

            # Stateful simulated Colab backend with a pre-existing baseline session
            active_sessions = {"other-user-sess": ("ep-baseline-001", "T4")}
            stopped_sessions = []
            upload_order = []
            injected_chunk_glitch_done = {"done": False}
            hooks = _make_local_test_hooks(self.tokenizer)
            # In this test, return full 9-file PINNED_MODEL_FILES in receipt so strict L4 manifest check passes!
            hooks_full_manifest = dict(hooks)
            hooks_full_manifest.pop("expected_test_model_files")

            def prepare_full_dummy_snapshot(tmp_dir):
                # Write small stub files only for verify_model_snapshot_files bypass? No: let's patch
                # verify_model_snapshot_files inside mock arm trigger so receipt has exact 9 PINNED_MODEL_FILES!
                return tmp_dir

            def fake_subprocess_run(cmd, input=b"", capture_output=True, timeout=None, **kwargs):
                sub = cmd[1]
                if sub == "sessions":
                    if not active_sessions:
                        out = "No active sessions found on server.\n"
                    else:
                        lines = [
                            f"[{sname}] {ep} | Hardware: {hw} | Status: READY"
                            for sname, (ep, hw) in sorted(active_sessions.items())
                        ]
                        out = "\n".join(lines) + "\n"
                    return subprocess.CompletedProcess(cmd, 0, out.encode("utf-8"), b"")

                if sub == "usage":
                    n_act = len(active_sessions)
                    rate = 0.8 + (1.94 if n_act > 1 else 0.0)
                    bal = 2700.0 - (0.05 if stopped_sessions else 0.0)
                    out = (
                        f"Current balance: {bal:.2f}\n"
                        f"Usage rate: {rate:.2f}\n"
                        f"Active assignments: {n_act}\n"
                    )
                    return subprocess.CompletedProcess(cmd, 0, out.encode("utf-8"), b"")

                if sub == "new":
                    sname = cmd[cmd.index("-s") + 1]
                    hw = cmd[cmd.index("--gpu") + 1]
                    active_sessions[sname] = (f"ep-{sname}", hw)
                    return subprocess.CompletedProcess(cmd, 0, b"Session READY\n", b"")

                if sub == "status":
                    sname = cmd[cmd.index("-s") + 1]
                    _, hw = active_sessions[sname]
                    out = f"Session: {sname} | Hardware: {hw}\n"
                    return subprocess.CompletedProcess(cmd, 0, out.encode("utf-8"), b"")

                if sub == "exec":
                    code_str = input.decode("utf-8")
                    if "TLP_READY" in code_str and "public-g3-bootstrap" not in code_str:
                        return subprocess.CompletedProcess(cmd, 0, b"TLP_READY\n", b"")
                    # Execute bootstrap cell remapping `/content/` -> `remote_fs_root/`
                    sname = cmd[cmd.index("-s") + 1]
                    remapped_dir = remote_fs_root / sname
                    remapped_dir.mkdir(parents=True, exist_ok=True)
                    # Decode inner bootstrap code to run in-process without spawning infinite waiting child
                    b64_match = re.search(r"b64decode\('([^']+)'\)", code_str)
                    inner = zlib.decompress(worker.base64.b64decode(b64_match.group(1))).decode("utf-8")
                    inner = inner.replace(f"'/content/{sname}'", repr(str(remapped_dir)))
                    # Replace subprocess.Popen with initial waiting_for_arm stage publication
                    inner_lines = [
                        line for line in inner.splitlines() if "subprocess.Popen" not in line
                    ]
                    # Rebuild clean snippet without dangling Popen args
                    cut_idx = inner.index("if not (W / 'started.lock').exists():")
                    tail_idx = inner.index("print(frame, end='', flush=True)")
                    patched_inner = (
                        inner[:cut_idx]
                        + "worker_mod.publish_text(W, 'stage.txt', 'waiting_for_arm')\n"
                        + inner[tail_idx:]
                    )
                    ns = {}
                    import io
                    from contextlib import redirect_stdout

                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        exec(compile(patched_inner, "<test-bootstrap>", "exec"), ns)
                    return subprocess.CompletedProcess(cmd, 0, buf.getvalue().encode("utf-8"), b"")

                if sub == "upload":
                    sname = cmd[cmd.index("-s") + 1]
                    src_local = Path(cmd[-2])
                    dst_remote_str = cmd[-1]
                    rel_name = dst_remote_str.split(f"/content/{sname}/", 1)[1]
                    upload_order.append(rel_name)
                    remapped_dst = remote_fs_root / sname / rel_name
                    remapped_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_local, remapped_dst)

                    if rel_name == "armed.txt":
                        # Verify 2-way sentinel and bundle upload happened BEFORE arming!
                        self.assertIn("bundle.zlib", upload_order)
                        w_remote = remote_fs_root / sname
                        orig_verify = worker.verify_model_snapshot_files
                        try:
                            worker.verify_model_snapshot_files = lambda _d, exp: dict(exp)
                            test_hooks = {
                                "skip_pip_install": True,
                                "device": "cpu",
                                "prepare_snapshot_dir": prepare_full_dummy_snapshot,
                                "build_model_and_tokenizer": hooks["build_model_and_tokenizer"],
                                "gpu_metadata": hooks["gpu_metadata"],
                            }
                            worker.run_worker(workdir=w_remote, local_test_hooks=test_hooks)
                            # Patch parameter_device & base_parameter_dtype to emulate L4 receipt for strict verifier test
                            rec_p = w_remote / "g3_training_receipt.json"
                            rec_obj = json.loads(rec_p.read_text(encoding="utf-8"))
                            rec_obj["parameter_device"] = "cuda:0"
                            rec_obj["base_parameter_dtype"] = "torch.bfloat16"
                            rec_bytes = (json.dumps(rec_obj, indent=2) + "\n").encode("utf-8")
                            rec_p.write_bytes(rec_bytes)
                            nonce_val = (w_remote / "nonce.txt").read_text().strip()
                            (w_remote / "receipt_frame.txt").write_text(
                                worker.emit_framed_json(rec_bytes, nonce_val), encoding="utf-8"
                            )
                        finally:
                            worker.verify_model_snapshot_files = orig_verify
                    return subprocess.CompletedProcess(cmd, 0, b"Upload complete\n", b"")

                if sub == "download":
                    sname = cmd[cmd.index("-s") + 1]
                    src_remote_str = cmd[-2]
                    dst_local = Path(cmd[-1])
                    rel_name = src_remote_str.split(f"/content/{sname}/", 1)[1]
                    if rel_name.endswith(".part00.txt") and not injected_chunk_glitch_done["done"]:
                        injected_chunk_glitch_done["done"] = True
                        return subprocess.CompletedProcess(cmd, 1, b"", b"Download failed\n")
                    remapped_src = remote_fs_root / sname / rel_name
                    if not remapped_src.is_file():
                        return subprocess.CompletedProcess(cmd, 1, b"", b"not found\n")
                    shutil.copy2(remapped_src, dst_local)
                    return subprocess.CompletedProcess(cmd, 0, b"", b"")

                if sub == "stop":
                    sname = cmd[cmd.index("-s") + 1]
                    stopped_sessions.append(sname)
                    active_sessions.pop(sname, None)
                    return subprocess.CompletedProcess(cmd, 0, b"Session terminated.\n", b"")

                raise AssertionError(f"unexpected colab command: {cmd}")

            op_dir = root / "op_005"
            adapters_dir = root / "adapters_out"
            rc = lifecycle.run_lifecycle(
                config_path=lc_config_path,
                operation_dir=op_dir,
                adapters_out_dir=adapters_dir,
                colab_cli="/mock/bin/colab",
                subprocess_runner=fake_subprocess_run,
                sleep_fn=lambda _s: None,
            )
            self.assertEqual(rc, 0)
            self.assertTrue(injected_chunk_glitch_done["done"])
            self.assertEqual(list(active_sessions.keys()), ["other-user-sess"])
            self.assertEqual(len(stopped_sessions), 1)
            self.assertTrue(stopped_sessions[0].startswith("tlfix-"))
            self.assertEqual(upload_order, ["bundle.zlib", "armed.txt"])

            # Verify lora_eval_runner.verify_adapter_manifest accepts generated adapter_manifest.json!
            p_manifest_path = adapters_dir / "P_s101" / "adapter_manifest.json"
            p_st_path = adapters_dir / "P_s101" / "adapter.safetensors"
            eval_cfg = {
                "model": {"revision": worker.REVISION},
                "lora_adapter": {
                    "adapter_id": "P_s101",
                    "training_arm": "P",
                    "safetensors_path": str(p_st_path.resolve()),
                    "safetensors_sha256": worker.sha256_file(p_st_path),
                },
                "external_inputs": [
                    {
                        "name": "adapter_manifest",
                        "path": str(p_manifest_path.resolve()),
                        "sha256": worker.sha256_file(p_manifest_path),
                    }
                ],
            }
            m_sha, m_entry = verify_adapter_manifest(eval_cfg)
            self.assertEqual(m_sha, worker.sha256_file(p_manifest_path))
            self.assertEqual(m_entry["adapter_id"], "P_s101")

            # Run offline verifier on valid operation -> MUST PASS (exit 0)
            ver_cfg = {
                "operation_dir": str(op_dir),
                "adapters_dir": str(adapters_dir),
                "model_manifest_path": str(model_manifest_path),
                "expected_bundle_sha256": self.bundle_sha,
                "expected_adapter_ids": ["B3_s101", "P_s101"],
                "verify_local_load": True,
            }
            ver_cfg_path = root / "verify_cfg.json"
            ver_cfg_path.write_text(json.dumps(ver_cfg), encoding="utf-8")
            ver_out = root / "verify_out_ok"
            v_rc = verifier.main(["--config", str(ver_cfg_path), "--output", str(ver_out), "--verify-local-load"])
            self.assertEqual(v_rc, 0)
            v_report = json.loads((ver_out / "colab_g3_verification.json").read_text(encoding="utf-8"))
            self.assertTrue(v_report["verified"])
            self.assertTrue((ver_out / "colab_g3_verification.md").is_file())

            # Negative verifier test A: Unreleased session (`owned_absent == False`)
            bad_op_unrel = root / "op_unreleased"
            shutil.copytree(op_dir, bad_op_unrel)
            lc_lines = [
                json.loads(l) for l in (bad_op_unrel / "lifecycle.jsonl").read_text().splitlines()
            ]
            for row in lc_lines:
                if row["step"] == "cleanup_observed":
                    row["owned_absent"] = False
            (bad_op_unrel / "lifecycle.jsonl").write_text(
                "\n".join(json.dumps(r) for r in lc_lines) + "\n", encoding="utf-8"
            )
            rep_unrel = verifier.verify_colab_g3_operation(
                {**ver_cfg, "operation_dir": str(bad_op_unrel)},
                root / "verify_out_unrel",
            )
            self.assertFalse(rep_unrel["verified"])
            self.assertEqual(rep_unrel["failure_code"], "owned_session_not_absent")

            # Negative verifier test B: Mutated base model digest
            bad_op_mut = root / "op_mutated_base"
            shutil.copytree(op_dir, bad_op_mut)
            rec_mut_p = bad_op_mut / "g3_training_receipt.json"
            rec_mut = json.loads(rec_mut_p.read_text())
            rec_mut["final_base_weight_digest"] = "0" * 64
            rec_mut_bytes = (json.dumps(rec_mut) + "\n").encode("utf-8")
            rec_mut_p.write_bytes(rec_mut_bytes)
            lc_mut = [json.loads(l) for l in (bad_op_mut / "lifecycle.jsonl").read_text().splitlines()]
            for row in lc_mut:
                if row["step"] == "local_receipt":
                    row["sha256"] = worker.sha256_bytes(rec_mut_bytes)
            (bad_op_mut / "lifecycle.jsonl").write_text(
                "\n".join(json.dumps(r) for r in lc_mut) + "\n", encoding="utf-8"
            )
            rep_mut = verifier.verify_colab_g3_operation(
                {**ver_cfg, "operation_dir": str(bad_op_mut)},
                root / "verify_out_mut",
            )
            self.assertFalse(rep_mut["verified"])
            self.assertEqual(rep_mut["failure_code"], "base_weights_mutated_or_unverified")

            # Negative verifier test C: Tampered safetensors file on disk
            bad_adapters_dir = root / "adapters_tampered"
            shutil.copytree(adapters_dir, bad_adapters_dir)
            tamper_target = bad_adapters_dir / "P_s101" / "adapter.safetensors"
            orig_bytes = bytearray(tamper_target.read_bytes())
            orig_bytes[-1] ^= 0xFF
            tamper_target.write_bytes(bytes(orig_bytes))
            rep_tamp = verifier.verify_colab_g3_operation(
                {**ver_cfg, "adapters_dir": str(bad_adapters_dir)},
                root / "verify_out_tamp",
            )
            self.assertFalse(rep_tamp["verified"])
            self.assertEqual(rep_tamp["failure_code"], "safetensors_sha256_mismatch")

    def test_lifecycle_mandatory_teardown_on_keyboard_interrupt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle_path = root / "public_colab_training_bundle.json"
            bundle_path.write_bytes(self.raw_bundle)
            lc_config_path = root / "lc_interrupt.json"
            lc_config_path.write_text(
                json.dumps(
                    {
                        "lora_adapter_path": str(ARCHIVE_ROOT / "toolloop_fix" / "lora_adapter.py"),
                        "worker_script_path": str(ARCHIVE_ROOT / "scripts" / "colab_g3_train_worker.py"),
                        "training_bundle_path": str(bundle_path),
                        "expected_bundle_sha256": self.bundle_sha,
                        "training_jobs": [self.four_jobs[0]],
                    }
                ),
                encoding="utf-8",
            )

            active_sessions = {"keep-me-alive": ("ep-keep-1", "A100")}
            stopped = []

            def interrupt_runner(cmd, input=b"", capture_output=True, timeout=None, **kwargs):
                sub = cmd[1]
                if sub == "sessions":
                    lines = [
                        f"[{sname}] {ep} | Hardware: {hw} | Status: READY"
                        for sname, (ep, hw) in sorted(active_sessions.items())
                    ]
                    return subprocess.CompletedProcess(cmd, 0, ("\n".join(lines) + "\n").encode(), b"")
                if sub == "usage":
                    out = f"Current balance: 100.0\nUsage rate: 5.0\nActive assignments: {len(active_sessions)}\n"
                    return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")
                if sub == "new":
                    sname = cmd[cmd.index("-s") + 1]
                    active_sessions[sname] = (f"ep-{sname}", "L4")
                    return subprocess.CompletedProcess(cmd, 0, b"Session READY\n", b"")
                if sub == "status":
                    raise KeyboardInterrupt("simulated operator SIGINT after allocation")
                if sub == "stop":
                    sname = cmd[cmd.index("-s") + 1]
                    stopped.append(sname)
                    active_sessions.pop(sname, None)
                    return subprocess.CompletedProcess(cmd, 0, b"Session terminated.\n", b"")
                raise AssertionError(f"unexpected command: {cmd}")

            op_dir = root / "op_interrupted"
            rc = lifecycle.run_lifecycle(
                config_path=lc_config_path,
                operation_dir=op_dir,
                adapters_out_dir=root / "adapters_unused",
                colab_cli="/mock/bin/colab",
                subprocess_runner=interrupt_runner,
                sleep_fn=lambda _s: None,
            )
            self.assertEqual(rc, 1)
            self.assertEqual(list(active_sessions.keys()), ["keep-me-alive"])
            self.assertEqual(len(stopped), 1)
            events = [json.loads(l) for l in (op_dir / "lifecycle.jsonl").read_text().splitlines()]
            cleanup_ev = next(e for e in events if e["step"] == "cleanup_observed")
            self.assertTrue(cleanup_ev["owned_absent"])
            self.assertTrue(cleanup_ev["original_set_unchanged"])
            fin_ev = events[-1]
            self.assertEqual(fin_ev["step"], "finished")
            self.assertTrue(fin_ev["owned_released"])
            self.assertFalse(fin_ev["received_completed_training"])

    def test_real_qwen35_micro_model_batched_vs_unbatched_equivalence(self):
        """Verify worker training on a real 2-layer Qwen3_5ForCausalLM matches micro_batch=1 vs 4."""
        from transformers import Qwen3_5ForCausalLM
        from transformers.models.qwen3_5.configuration_qwen3_5 import Qwen3_5TextConfig

        def build_real_micro_qwen(_snap):
            torch.manual_seed(17)
            qcfg = Qwen3_5TextConfig(
                vocab_size=self.tokenizer.vocab_size,
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
                layer_types=["linear_attention", "full_attention"],
                attn_implementation="eager",
                use_cache=False,
                eos_token_id=self.tokenizer.eos_token_id,
                pad_token_id=self.tokenizer.pad_token_id,
            )
            return Qwen3_5ForCausalLM(qcfg).to(dtype=torch.float32), self.tokenizer

        results = {}
        for mb_seqs in (1, 4):
            with tempfile.TemporaryDirectory() as td:
                wdir = Path(td)
                nonce = f"{mb_seqs:032x}"
                (wdir / "nonce.txt").write_text(nonce + "\n", encoding="utf-8")
                (wdir / "bundle.zlib").write_bytes(self.compressed_bundle)
                (wdir / "armed.txt").write_text(nonce + "\n", encoding="utf-8")
                probe_cfg = copy.deepcopy(worker.DEFAULT_PUBLIC_PROBE)
                probe_cfg["max_new_tokens"] = 8
                (wdir / "train_job_config.json").write_text(
                    json.dumps(
                        {
                            "expected_bundle_sha256": self.bundle_sha,
                            "lora_rank": 4,
                            "lora_alpha": 8.0,
                            "micro_batch_sequences": mb_seqs,
                            "eval_batch_sequences": mb_seqs,
                            "training_jobs": [
                                {
                                    "adapter_id": "P_s101",
                                    "arm": "P",
                                    "objective": "dpo",
                                    "dataset": "p_toolloop_fix_dpo.jsonl",
                                    "seed": 101,
                                    "lr": 1e-2,
                                    "beta": 0.2,
                                    "sft_anchor": 0.2,
                                    "epochs": 2,
                                    "grad_accum": 4,
                                }
                            ],
                            "public_probe": probe_cfg,
                        }
                    ),
                    encoding="utf-8",
                )
                hooks = _make_local_test_hooks(self.tokenizer)
                hooks["build_model_and_tokenizer"] = build_real_micro_qwen
                rc = worker.run_worker(workdir=wdir, local_test_hooks=hooks)
                self.assertEqual(rc, 0)
                rec = json.loads((wdir / "g3_training_receipt.json").read_text(encoding="utf-8"))
                self.assertTrue(rec["base_weights_unchanged"])
                results[mb_seqs] = rec["adapters"][0]

        self.assertAlmostEqual(results[1]["initial_loss"], results[4]["initial_loss"], places=5)
        self.assertAlmostEqual(results[1]["final_loss"], results[4]["final_loss"], places=4)
        self.assertLess(results[4]["final_loss"], results[4]["initial_loss"])


if __name__ == "__main__":
    unittest.main()
