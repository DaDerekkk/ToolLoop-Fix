#!/usr/bin/env python3
"""Offline consistency, lifecycle teardown, and safetensors audit of Colab L4 G3 training.

Designed as an `analysis` CLI (`--config <path> --output <dir>`) compatible with
`scripts/record_run.py`:
  1. Audits `lifecycle.jsonl` timeline, 2-way pre-arm sentinel, worker arming nonce,
     and mandatory teardown (`owned_absent == True`, `original_set_unchanged == True`,
     zero URL/token leakage).
  2. Audits `g3_training_receipt.json` (`status == 'completed'`, `base_weights_unchanged == True`,
     identical `initial_base_weight_digest` and `final_base_weight_digest`, NVIDIA L4 BF16
     metadata, and all 9 base-model file sizes + SHA256 digests against local `model_manifest.json`).
  3. Audits every reassembled `<adapter_id>/adapter.safetensors` and `adapter_manifest.json`
     on disk (exact SHA256, byte size, tensor count, BF16 storage, finite weights, non-zero
     trained `lora_B`).
  4. When `--verify-local-load` (or `config['verify_local_load']`) is enabled, exercises
     `lora_adapter.load_lora_safetensors` on every adapter (into either a matching `nn.Linear`
     module hierarchy or local `Qwen3_5ForCausalLM`) and optionally cross-checks local
     `AutoTokenizer` prompt/decode parity on every recorded `public_probe`.
  5. Writes `colab_g3_verification.json` and `colab_g3_verification.md` (never overwriting).
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys

SNAPSHOT_ROOT = Path(__file__).resolve().parents[1]
if str(SNAPSHOT_ROOT) not in sys.path:
    sys.path.insert(0, str(SNAPSHOT_ROOT))

from scripts.colab_g3_train_worker import (
    DEFAULT_PUBLIC_PROBE,
    MODEL_ID,
    PINNED_MODEL_FILES,
    RECEIPT_SCHEMA_VERSION,
    REVISION,
    sha256_bytes,
    sha256_file,
)
from toolloop_fix import lora_adapter
from toolloop_fix.lora_adapter import LORA_ADAPTER_VERSION


class VerificationFailure(ValueError):
    """Carries a deterministic safe failure code for structured audit reporting."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def require(condition, code, detail=""):
    if not condition:
        raise VerificationFailure(code, detail)


def _live_workspace_root():
    if (
        SNAPSHOT_ROOT.name == "inputs"
        and SNAPSHOT_ROOT.parent.parent.name == "experiments"
    ):
        return SNAPSHOT_ROOT.parents[2]
    return SNAPSHOT_ROOT


def _resolve_path(raw_path):
    p = Path(raw_path)
    if p.is_absolute():
        return p
    cand_snap = SNAPSHOT_ROOT / p
    if cand_snap.exists():
        return cand_snap.resolve()
    cand_live = _live_workspace_root() / p
    return cand_live.resolve()


def _write_exclusive_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def _check_no_sensitive_leakage_in_lifecycle(raw_text):
    for pattern, code in (
        (r"https?://", "url_in_lifecycle_log"),
        (r"Bearer\s+[A-Za-z0-9._-]+", "bearer_token_in_lifecycle_log"),
        (r"ya29\.[A-Za-z0-9._-]+", "oauth_token_in_lifecycle_log"),
        (r"hf_[A-Za-z0-9]{16,}", "hf_token_in_lifecycle_log"),
    ):
        if re.search(pattern, raw_text):
            raise VerificationFailure(code)


def _build_skeleton_model_from_tensors(saved_tensors):
    """Construct an `nn.Module` hierarchy matching `.lora_A` / `.lora_B` paths for `load_lora_safetensors`."""
    import torch.nn as nn

    class SkeletonNode(nn.Module):
        pass

    root = SkeletonNode()
    lora_pairs = {}
    for key, tensor in saved_tensors.items():
        if key.endswith(".lora_A"):
            mod_path = key[: -len(".lora_A")]
            lora_pairs.setdefault(mod_path, {})["A"] = tuple(int(d) for d in tensor.shape)
        elif key.endswith(".lora_B"):
            mod_path = key[: -len(".lora_B")]
            lora_pairs.setdefault(mod_path, {})["B"] = tuple(int(d) for d in tensor.shape)
        else:
            raise VerificationFailure("unexpected_safetensors_tensor_name", key)

    for mod_path, shapes in sorted(lora_pairs.items()):
        require("A" in shapes and "B" in shapes, "incomplete_lora_ab_pair", mod_path)
        rank_a, in_features = shapes["A"]
        out_features, rank_b = shapes["B"]
        require(rank_a == rank_b and rank_a > 0, "lora_rank_shape_mismatch", mod_path)

        parts = mod_path.split(".")
        cur = root
        for part in parts[:-1]:
            if not hasattr(cur, part):
                setattr(cur, part, SkeletonNode())
            cur = getattr(cur, part)
        setattr(cur, parts[-1], nn.Linear(in_features, out_features, bias=False))
    return root


def _load_manifest_files_map(manifest_path):
    require(manifest_path.is_file(), "missing_local_model_manifest", str(manifest_path))
    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(
        manifest_obj.get("model_id") == MODEL_ID and manifest_obj.get("revision") == REVISION,
        "local_model_manifest_target_mismatch",
    )
    files_map = {}
    for entry in manifest_obj.get("files", []):
        files_map[entry["path"]] = {
            "sha256": entry["sha256"],
            "size_bytes": int(entry["size_bytes"]),
        }
    require(files_map == PINNED_MODEL_FILES, "local_model_manifest_files_mismatch")
    return files_map, sha256_file(manifest_path)


def _audit_lifecycle_jsonl(lifecycle_path, operation_dir):
    require(lifecycle_path.is_file(), "missing_lifecycle_jsonl", str(lifecycle_path))
    raw_text = lifecycle_path.read_text(encoding="utf-8")
    _check_no_sensitive_leakage_in_lifecycle(raw_text)

    lines = [line for line in raw_text.splitlines() if line.strip()]
    require(len(lines) >= 6, "incomplete_lifecycle_timeline")
    records = [json.loads(line) for line in lines]

    require(records[0].get("step") == "reservation", "missing_reservation_step")
    require(records[-1].get("step") == "finished", "missing_finished_step")
    elapsed_vals = [float(r["elapsed_s"]) for r in records]
    require(elapsed_vals == sorted(elapsed_vals), "non_monotonic_lifecycle_elapsed")

    by_step = {}
    cleanup_records = []
    stage_events = []
    reassembled_events = []
    for row in records:
        step = row.get("step")
        by_step[step] = row
        if step == "cleanup_observed":
            cleanup_records.append(row)
        elif step == "worker_stage":
            stage_events.append(row.get("stage"))
        elif step == "adapter_reassembled":
            reassembled_events.append(row)

    res = by_step["reservation"]
    nonce = res.get("receipt_nonce")
    require(
        isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{32}", nonce),
        "invalid_reservation_nonce",
    )
    require(res.get("training") is True, "reservation_training_flag_not_true")
    require("new" in by_step and by_step["new"].get("exit") == 0, "session_allocation_failed")
    require(
        "owned_l4" in by_step
        and isinstance(by_step["owned_l4"].get("endpoint_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", by_step["owned_l4"]["endpoint_sha256"]),
        "missing_owned_l4_endpoint_digest",
    )

    for usage_key in (
        "baseline_usage_observation",
        "allocated_usage_observation",
        "final_usage_observation",
    ):
        require(usage_key in by_step, "missing_usage_observation", usage_key)

    require(
        "two_way_sentinel" in by_step and by_step["two_way_sentinel"].get("identical") is True,
        "two_way_sentinel_not_verified",
    )
    sentinel_path = operation_dir / "sentinel.json"
    if sentinel_path.is_file():
        require(
            len(sentinel_path.read_bytes()) == int(by_step["two_way_sentinel"]["bytes"]),
            "sentinel_bytes_length_mismatch",
        )

    require(
        "arm_single_worker" in by_step and by_step["arm_single_worker"].get("exit") == 0,
        "worker_not_armed",
    )
    armed_path = operation_dir / "armed.txt"
    if armed_path.is_file():
        require(armed_path.read_text(encoding="utf-8").strip() == nonce, "armed_nonce_mismatch")

    require(bool(cleanup_records), "missing_cleanup_observed_event")
    last_cleanup = cleanup_records[-1]
    require(last_cleanup.get("owned_absent") is True, "owned_session_not_absent")
    require(last_cleanup.get("original_set_unchanged") is True, "original_sessions_set_changed")

    fin = by_step["finished"]
    require(fin.get("owned_released") is True, "finished_owned_released_false")
    require(fin.get("received_completed_training") is True, "finished_training_not_completed")
    require(fin.get("adapters_reassembled") is True, "finished_adapters_not_reassembled")
    require("local_receipt" in by_step, "missing_local_receipt_event")

    return {
        "session_name": res.get("session"),
        "receipt_nonce": nonce,
        "recorded_receipt_sha256": by_step["local_receipt"].get("sha256"),
        "two_way_sentinel_identical": True,
        "owned_absent": True,
        "original_set_unchanged": True,
        "owned_released": True,
        "gross_elapsed_seconds": float(records[-1]["elapsed_s"]),
        "account_assignments": [
            by_step["baseline_usage_observation"]["assignments"],
            by_step["allocated_usage_observation"]["assignments"],
            by_step["final_usage_observation"]["assignments"],
        ],
        "account_rates_cu_hr": [
            by_step["baseline_usage_observation"]["rate_cu_hr"],
            by_step["allocated_usage_observation"]["rate_cu_hr"],
            by_step["final_usage_observation"]["rate_cu_hr"],
        ],
        "account_balances_cu": [
            by_step["baseline_usage_observation"]["balance_cu"],
            by_step["allocated_usage_observation"]["balance_cu"],
            by_step["final_usage_observation"]["balance_cu"],
        ],
        "observed_worker_stages": stage_events,
        "reassembled_event_count": len(reassembled_events),
    }


def _audit_receipt_and_adapters(
    receipt_path,
    adapters_dir,
    expected_nonce,
    expected_receipt_sha256,
    manifest_files_map,
    config,
    verify_local_load=False,
):
    import torch
    from safetensors import safe_open

    require(receipt_path.is_file(), "missing_g3_training_receipt", str(receipt_path))
    raw_receipt = receipt_path.read_bytes()
    actual_receipt_sha = sha256_bytes(raw_receipt)
    if expected_receipt_sha256:
        require(
            actual_receipt_sha == expected_receipt_sha256,
            "receipt_sha256_mismatch",
            f"expected {expected_receipt_sha256}, got {actual_receipt_sha}",
        )

    receipt = json.loads(raw_receipt.decode("utf-8"))
    require(receipt.get("schema_version") == RECEIPT_SCHEMA_VERSION, "receipt_schema_mismatch")
    require(receipt.get("nonce") == expected_nonce, "receipt_nonce_mismatch")
    require(
        receipt.get("status") == "completed" and receipt.get("stage") == "completed",
        "receipt_not_completed",
    )
    require(
        receipt.get("training_performed") is True and receipt.get("tool_executed") is False,
        "receipt_scope_flags_invalid",
    )
    require(
        receipt.get("model_id") == MODEL_ID and receipt.get("revision") == REVISION,
        "receipt_model_pin_mismatch",
    )

    init_digest = receipt.get("initial_base_weight_digest")
    final_digest = receipt.get("final_base_weight_digest")
    require(
        receipt.get("base_weights_unchanged") is True
        and isinstance(init_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", init_digest)
        and init_digest == final_digest,
        "base_weights_mutated_or_unverified",
    )

    allow_cpu_test = bool(config.get("allow_synthetic_cpu_receipt_for_tests", False))
    gpu = receipt.get("gpu") or {}
    if not allow_cpu_test:
        require(
            isinstance(gpu.get("name"), str)
            and "L4" in gpu["name"].split()
            and gpu.get("compute_capability") == [8, 9]
            and int(gpu.get("memory_bytes", 0)) > 0,
            "gpu_not_verified_l4",
        )
        require(
            receipt.get("parameter_device") == "cuda:0"
            and receipt.get("base_parameter_dtype") == "torch.bfloat16",
            "model_device_or_dtype_not_l4_bf16",
        )
        require(receipt.get("verified_model_files") == manifest_files_map, "base_model_files_hash_mismatch")

    bundle_meta = receipt.get("training_bundle") or {}
    require(bundle_meta.get("g3_dataset_ready") is True, "bundle_not_g3_ready")
    if config.get("expected_bundle_sha256"):
        require(
            bundle_meta.get("bundle_sha256") == config["expected_bundle_sha256"],
            "bundle_sha256_mismatch",
        )

    lora_inj = receipt.get("lora_injection") or {}
    require(
        lora_inj.get("adapter_version") == LORA_ADAPTER_VERSION
        and int(lora_inj.get("rank", 0)) > 0
        and int(lora_inj.get("matched_module_count", 0)) > 0
        and int(lora_inj.get("trainable_param_count", 0)) > 0,
        "invalid_lora_injection_metadata",
    )
    expected_rank = int(lora_inj["rank"])
    expected_alpha = float(lora_inj["alpha"])
    expected_tensor_count = 2 * int(lora_inj["matched_module_count"])

    adapters = receipt.get("adapters")
    require(isinstance(adapters, list) and len(adapters) > 0, "empty_adapters_list")
    if "expected_adapter_ids" in config and config["expected_adapter_ids"] is not None:
        actual_ids = [item.get("adapter_id") for item in adapters]
        require(actual_ids == list(config["expected_adapter_ids"]), "adapter_id_inventory_mismatch")

    tokenizer = None
    if config.get("verify_tokenizer_probes", False):
        local_model_dir = _resolve_path(
            config.get("local_model_dir", f"models/Qwen3.5-0.8B/{REVISION}")
        )
        require(local_model_dir.is_dir(), "missing_local_model_dir_for_tokenizer", str(local_model_dir))
        os.environ["HF_HUB_OFFLINE"] = "1"
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            str(local_model_dir), local_files_only=True, trust_remote_code=False
        )

    probe_cfg = config.get("public_probe") or DEFAULT_PUBLIC_PROBE
    expected_probe_prompt_sha = None
    expected_probe_input_tokens = None
    if tokenizer is not None:
        rendered_probe = tokenizer.apply_chat_template(
            probe_cfg["messages"],
            tools=probe_cfg["tools"],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        expected_probe_prompt_sha = sha256_bytes(rendered_probe.encode("utf-8"))
        expected_probe_input_tokens = len(tokenizer(rendered_probe, add_special_tokens=False)["input_ids"])

        base_probe = (receipt.get("reference_precompute") or {}).get("frozen_base_public_probe")
        if base_probe:
            require(
                base_probe["prompt_sha256"] == expected_probe_prompt_sha
                and base_probe["input_tokens"] == expected_probe_input_tokens
                and tokenizer.decode(base_probe["generated_token_ids"], skip_special_tokens=False)
                == base_probe["raw_text"],
                "base_probe_tokenizer_replay_mismatch",
            )

    verified_adapters = []
    for item in adapters:
        adapter_id = item["adapter_id"]
        st_path = adapters_dir / adapter_id / "adapter.safetensors"
        manifest_path = adapters_dir / adapter_id / "adapter_manifest.json"
        require(st_path.is_file(), "missing_adapter_safetensors", str(st_path))
        require(manifest_path.is_file(), "missing_adapter_manifest", str(manifest_path))

        actual_size = int(st_path.stat().st_size)
        actual_sha = sha256_file(st_path)
        require(actual_size == int(item["safetensors_size_bytes"]), "safetensors_size_mismatch", adapter_id)
        require(actual_sha == item["safetensors_sha256"], "safetensors_sha256_mismatch", adapter_id)

        manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(
            manifest_obj.get("adapter_id") == adapter_id
            and manifest_obj.get("training_arm") == item["arm"]
            and manifest_obj.get("safetensors_sha256") == actual_sha
            and manifest_obj.get("revision") == REVISION
            and manifest_obj.get("base_weights_unchanged") is True,
            "adapter_manifest_content_mismatch",
            adapter_id,
        )

        with safe_open(str(st_path), framework="pt", device="cpu") as handle:
            meta = dict(handle.metadata() or {})
            saved_tensors = {k: handle.get_tensor(k) for k in handle.keys()}

        require(meta.get("adapter_version") == LORA_ADAPTER_VERSION, "safetensors_version_mismatch", adapter_id)
        require(
            int(meta.get("rank", -1)) == expected_rank
            and math.isclose(float(meta.get("alpha", -1.0)), expected_alpha),
            "safetensors_rank_alpha_mismatch",
            adapter_id,
        )
        require(
            len(saved_tensors) == expected_tensor_count == int(item["tensor_count"]),
            "safetensors_tensor_count_mismatch",
            adapter_id,
        )

        nonzero_b_count = 0
        for t_name, tensor in saved_tensors.items():
            require(tensor.dtype == torch.bfloat16, "safetensors_dtype_not_bf16", f"{adapter_id}:{t_name}")
            require(bool(torch.isfinite(tensor.float()).all().item()), "non_finite_lora_tensor", f"{adapter_id}:{t_name}")
            if t_name.endswith(".lora_A"):
                require(int(tensor.shape[0]) == expected_rank, "lora_a_rank_mismatch", f"{adapter_id}:{t_name}")
            elif t_name.endswith(".lora_B"):
                require(int(tensor.shape[1]) == expected_rank, "lora_b_rank_mismatch", f"{adapter_id}:{t_name}")
                if bool((tensor != 0).any().item()):
                    nonzero_b_count += 1
            else:
                raise VerificationFailure("unexpected_tensor_key", f"{adapter_id}:{t_name}")

        require(nonzero_b_count > 0, "all_lora_b_weights_remained_zero", adapter_id)

        local_load_checked = False
        if verify_local_load:
            skeleton = _build_skeleton_model_from_tensors(saved_tensors)
            pre_skel_digest = lora_adapter.base_weight_digest(skeleton)
            loaded_info = lora_adapter.load_lora_safetensors(
                skeleton, st_path, expected_sha256=actual_sha
            )
            post_skel_digest = lora_adapter.base_weight_digest(skeleton)
            require(
                pre_skel_digest == post_skel_digest
                and int(loaded_info["tensor_count"]) == expected_tensor_count,
                "local_lora_load_verification_failed",
                adapter_id,
            )
            local_load_checked = True

        probe = item.get("public_probe") or {}
        require(
            isinstance(probe.get("generated_token_ids"), list)
            and len(probe["generated_token_ids"]) == int(probe.get("output_tokens", -1))
            and len(probe["generated_token_ids"]) >= 1
            and isinstance(probe.get("raw_text"), str),
            "invalid_adapter_public_probe",
            adapter_id,
        )
        probe_tokenizer_verified = False
        if tokenizer is not None:
            require(
                probe["prompt_sha256"] == expected_probe_prompt_sha
                and int(probe["input_tokens"]) == expected_probe_input_tokens
                and tokenizer.decode(probe["generated_token_ids"], skip_special_tokens=False)
                == probe["raw_text"],
                "adapter_probe_tokenizer_replay_mismatch",
                adapter_id,
            )
            probe_tokenizer_verified = True

        verified_adapters.append(
            {
                "adapter_id": adapter_id,
                "arm": item["arm"],
                "objective": item["objective"],
                "seed": int(item["seed"]),
                "dataset": item["dataset"],
                "example_count": int(item["example_count"]),
                "optimizer_steps": int(item["optimizer_steps"]),
                "initial_loss": float(item["initial_loss"]),
                "epoch_mean_losses": [float(x) for x in item["epoch_mean_losses"]],
                "final_loss": float(item["final_loss"]),
                "loss_decreased": bool(item["loss_decreased"]),
                "post_train_reward_accuracy": item["post_train_reward_accuracy"],
                "post_train_mean_reward_margin": item["post_train_mean_reward_margin"],
                "total_train_tokens": int(item["total_train_tokens"]),
                "total_completion_tokens": int(item["total_completion_tokens"]),
                "max_memory_allocated_bytes": int(item["max_memory_allocated_bytes"]),
                "max_memory_allocated_mib": round(int(item["max_memory_allocated_bytes"]) / (1024 * 1024), 2),
                "train_wall_seconds": float(item["train_wall_seconds"]),
                "safetensors_sha256": actual_sha,
                "safetensors_size_bytes": actual_size,
                "adapter_manifest_sha256": sha256_file(manifest_path),
                "tensor_count": len(saved_tensors),
                "nonzero_lora_b_modules": nonzero_b_count,
                "local_load_verified": local_load_checked,
                "probe_tokenizer_verified": probe_tokenizer_verified,
                "probe_output_tokens": int(probe["output_tokens"]),
                "probe_first_line": probe["raw_text"].splitlines()[0][:80] if probe["raw_text"] else "",
            }
        )

    return {
        "receipt_sha256": actual_receipt_sha,
        "gpu": gpu,
        "runtime": receipt.get("runtime"),
        "lora_injection": lora_inj,
        "training_bundle": bundle_meta,
        "initial_base_weight_digest": init_digest,
        "final_base_weight_digest": final_digest,
        "base_weights_unchanged": True,
        "worker_wall_seconds": receipt.get("worker_wall_seconds"),
        "adapter_count": len(verified_adapters),
        "adapters": verified_adapters,
    }


def _render_markdown_summary(report):
    lines = [
        "# ITER-009 Colab L4 G3 Multi-Arm LoRA Training Verification",
        "",
        f"- **Verification Verdict**: `{'PASS' if report['verified'] else 'FAIL'}`",
    ]
    if not report["verified"]:
        lines.append(f"- **Failure Code**: `{report.get('failure_code')}` ({report.get('failure_detail', '')})")
        return "\n".join(lines) + "\n"

    lc = report["lifecycle_audit"]
    rc = report["receipt_and_adapter_audit"]
    gpu = rc["gpu"]
    inj = rc["lora_injection"]
    cu_bals = lc["account_balances_cu"]
    cu_delta = round(cu_bals[0] - cu_bals[-1], 4) if len(cu_bals) >= 2 else None

    lines.extend(
        [
            f"- **Evidence Class**: `analysis` (auditing remote `model_training` operation `{lc['session_name']}`)",
            f"- **GPU Verified**: `{gpu.get('name')}` (compute `{gpu.get('compute_capability')}`, `{round(gpu.get('memory_bytes', 0) / (1024**3), 2)} GiB`)",
            f"- **Lifecycle Teardown**: `owned_absent={lc['owned_absent']}`, `original_set_unchanged={lc['original_set_unchanged']}`, `two_way_sentinel={lc['two_way_sentinel_identical']}`",
            f"- **Account CU Observations**: balances `{cu_bals}` (observed delta `{cu_delta} CU`), rates `{lc['account_rates_cu_hr']} CU/hr`, client elapsed `{lc['gross_elapsed_seconds']}s`",
            f"- **Base Model Invariance**: `base_weights_unchanged={rc['base_weights_unchanged']}` (`{rc['initial_base_weight_digest'][:16]}...`)",
            f"- **LoRA Architecture**: `{inj['adapter_version']}`, `rank={inj['rank']}`, `alpha={inj['alpha']}`, `{inj['matched_module_count']}` modules (`{inj['trainable_param_count']:,}` trainable params)",
            f"- **Training Bundle SHA256**: `{rc['training_bundle'].get('bundle_sha256')}`",
            f"- **Receipt SHA256**: `{rc['receipt_sha256']}`",
            "",
            "## Trained G3 Adapters Summary",
            "",
            "| Adapter ID | Arm | Objective | Seed | N | Init Loss | Final Loss | Epoch Losses | Train Reward Acc | Train Tokens | Peak VRAM (MiB) | Train (s) | Safetensors SHA256 |",
            "|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|",
        ]
    )
    for ad in rc["adapters"]:
        rew = (
            f"{ad['post_train_reward_accuracy'] * 100:.1f}%"
            if ad["post_train_reward_accuracy"] is not None
            else "N/A (SFT)"
        )
        ep_str = " -> ".join(f"{x:.4f}" for x in ad["epoch_mean_losses"])
        lines.append(
            f"| `{ad['adapter_id']}` | `{ad['arm']}` | `{ad['objective']}` | {ad['seed']} | "
            f"{ad['example_count']} | {ad['initial_loss']:.4f} | {ad['final_loss']:.4f} | "
            f"`{ep_str}` | {rew} | {ad['total_train_tokens']:,} | "
            f"{ad['max_memory_allocated_mib']:.1f} | {ad['train_wall_seconds']:.2f} | "
            f"`{ad['safetensors_sha256'][:16]}...` |"
        )
    lines.append("")
    return "\n".join(lines)


def _archive_operation_sources(operation_dir, output_dir):
    op_out = output_dir / "operation_sources"
    op_out.mkdir(parents=True, exist_ok=False)
    copied = {}
    for rel in (
        "lifecycle.jsonl",
        "g3_training_receipt.json",
        "sentinel.json",
        "armed.txt",
        "sources/train_job_config.json",
    ):
        src = operation_dir / rel
        if src.is_file():
            dst_name = rel.replace("/", "__")
            dst = op_out / dst_name
            shutil.copy2(src, dst)
            copied[dst_name] = sha256_file(dst)
    return copied


def verify_colab_g3_operation(config, output_dir, force_verify_local_load=False):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_out = output_dir / "colab_g3_verification.json"
    md_out = output_dir / "colab_g3_verification.md"
    if json_out.exists() or md_out.exists():
        raise FileExistsError("refusing to overwrite existing colab_g3_verification artifacts")

    operation_dir = _resolve_path(config["operation_dir"])
    adapters_dir = _resolve_path(config["adapters_dir"])
    manifest_path = _resolve_path(
        config.get(
            "model_manifest_path",
            f"models/manifests/Qwen3.5-0.8B-{REVISION}.json",
        )
    )
    verify_local_load = bool(force_verify_local_load or config.get("verify_local_load", True))

    report = {
        "schema_version": "iter9_colab_g3_verification_v1",
        "evidence_class": "analysis",
        "remote_evidence_source": "remote_colab_l4_model_training",
        "verified": False,
        "operation_dir": str(operation_dir),
        "adapters_dir": str(adapters_dir),
    }

    try:
        allow_cpu_test = bool(config.get("allow_synthetic_cpu_receipt_for_tests", False))
        if allow_cpu_test and not manifest_path.is_file():
            manifest_files_map = dict(PINNED_MODEL_FILES)
            manifest_sha = None
        else:
            manifest_files_map, manifest_sha = _load_manifest_files_map(manifest_path)

        lifecycle_audit = _audit_lifecycle_jsonl(operation_dir / "lifecycle.jsonl", operation_dir)
        expected_r_sha = config.get("expected_receipt_sha256") or lifecycle_audit["recorded_receipt_sha256"]
        receipt_audit = _audit_receipt_and_adapters(
            receipt_path=operation_dir / "g3_training_receipt.json",
            adapters_dir=adapters_dir,
            expected_nonce=lifecycle_audit["receipt_nonce"],
            expected_receipt_sha256=expected_r_sha,
            manifest_files_map=manifest_files_map,
            config=config,
            verify_local_load=verify_local_load,
        )
        archived = {}
        if config.get("archive_operation_sources", True):
            archived = _archive_operation_sources(operation_dir, output_dir)

        report.update(
            verified=True,
            local_model_manifest_sha256=manifest_sha,
            verify_local_load_performed=verify_local_load,
            archived_operation_sources=archived,
            lifecycle_audit=lifecycle_audit,
            receipt_and_adapter_audit=receipt_audit,
        )
    except VerificationFailure as vf:
        report.update(verified=False, failure_code=vf.code, failure_detail=vf.detail)
    except Exception as exc:
        report.update(verified=False, failure_code=type(exc).__name__, failure_detail="")

    _write_exclusive_text(
        json_out, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    _write_exclusive_text(md_out, _render_markdown_summary(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="JSON verification config")
    parser.add_argument("--output", type=Path, required=True, help="output directory")
    parser.add_argument(
        "--verify-local-load",
        action="store_true",
        help="load every adapter safetensors into LoRALinear on CPU to verify structural integrity",
    )
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    report = verify_colab_g3_operation(
        config=cfg,
        output_dir=args.output,
        force_verify_local_load=args.verify_local_load,
    )
    summary = {
        "verified": report["verified"],
        "adapter_count": report.get("receipt_and_adapter_audit", {}).get("adapter_count", 0),
        "failure_code": report.get("failure_code"),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
