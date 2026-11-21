#!/usr/bin/env python3
"""Client-side Colab L4 lifecycle controller for ITER-009 G3 multi-arm LoRA training.

Safety invariants (strictly aligned with `operations/COLAB-20261119-004/lifecycle.py`
and `docs/protocols/COLAB_RECEIPT_WORKFLOW.md`):
  1. Never executes on import; CLI requires `--config`, `--operation-dir`, `--adapters-out-dir`.
  2. Records baseline `colab sessions` (endpoint SHA256 digests only) and `colab usage`
     before creating a uniquely named `tlfix-<12hex>` L4 session.
  3. Verifies isolation: exactly 1 new L4 assignment added, original session set untouched,
     and incremental CU/hr rate `<= 15.0`.
  4. Pings kernel (`TLP_READY`), bootstraps `lora_adapter.py`, `colab_g3_train_worker.py`,
     `train_job_config.json`, and `nonce.txt`, and verifies a 2-way pre-arm sentinel via
     BOTH `exec` stdout and `colab download` BEFORE uploading `bundle.zlib` and `armed.txt`.
  5. Polls `stage.txt` every 4s, downloads `receipt_frame.txt`, verifies nonce + SHA256,
     downloads `<480 KB` `TLP_CHUNK_V1` chunks (retrying transient single-chunk failures up
     to 3 times without re-running training), reassembles & SHA256-verifies each
     `<adapter_id>/adapter.safetensors`, and writes `<adapter_id>/adapter_manifest.json`.
  6. Mandatory `finally:` teardown stops ONLY our owned `session` even on `KeyboardInterrupt`
     or `SIGTERM`, verifying `owned_absent == True` and `original_set_unchanged == True`,
     while logging sanitized `lifecycle.jsonl` with zero URLs, tokens, or raw CLI dumps.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.colab_g3_train_worker import (
    DEFAULT_PUBLIC_PROBE,
    DEFAULT_TRAINING_JOBS,
    MAX_CHUNK_FRAME_BYTES,
    MAX_CHUNK_RAW_BYTES,
    MODEL_ID,
    PINNED_MODEL_FILES,
    RECEIPT_FRAME_PREFIX,
    REVISION,
    SENTINEL_FRAME_PREFIX,
    check_adapter_id,
    check_nonce,
    decode_and_reassemble_chunks,
    emit_framed_json,
    is_recognized_stage,
    receive_framed_json,
    sha256_bytes,
    sha256_file,
)

LORA_ADAPTER_VERSION = "pure_pytorch_qwen35_text_lora_v1"
DEFAULT_COLAB_CLI = str(Path.home() / ".local/bin/colab")
SAFE_CLI_REASONS = (
    "Allocation refused",
    "too many active sessions",
    "Backend rejected accelerator",
    "Session READY",
    "Session terminated",
    "Installation Complete",
    "not found",
    "Download failed",
    "Upload complete",
    "Uploaded",
    "timed out",
)


def _resolve_project_path(raw_path):
    p = Path(raw_path)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p


def _write_exclusive_bytes(path, raw_bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw_bytes)
    return sha256_bytes(raw_bytes)


def _write_exclusive_json(path, obj):
    payload = (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return _write_exclusive_bytes(path, payload)


def _build_bootstrap_cell(remote_workdir, text_files, sentinel_bytes, sentinel_ticket):
    """Build compressed kernel cell that writes code/config, verifies sentinel, and spawns worker."""
    inner = f"""from pathlib import Path
import os, subprocess, sys

W = Path({remote_workdir!r})
W.mkdir(parents=True, exist_ok=True)
files = {text_files!r}
for rel_name, content in files.items():
    target = W / rel_name
    if target.exists():
        assert target.read_text(encoding='utf-8') == content, 'bootstrap_content_mismatch'
    else:
        tmp = W / ('.' + rel_name + '.tmp')
        tmp.write_text(content, encoding='utf-8')
        os.replace(tmp, target)

if str(W) not in sys.path:
    sys.path.insert(0, str(W))
import colab_g3_train_worker as worker_mod

sent_dir = W / 'sentinel'
sent_dir.mkdir(exist_ok=True)
sent_path = sent_dir / 'sentinel.json'
sent_bytes = {sentinel_bytes!r}
sent_path.write_bytes(sent_bytes)
frame = worker_mod.emit_framed_json(sent_bytes, {sentinel_ticket!r}, prefix=worker_mod.SENTINEL_FRAME_PREFIX)
worker_mod.publish_text(W, 'sentinel_frame.txt', frame.rstrip('\\n'))

if not (W / 'started.lock').exists():
    subprocess.Popen(
        [sys.executable, str(W / 'colab_g3_train_worker.py'), '--workdir', str(W)],
        cwd=str(W),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )

print(frame, end='', flush=True)
"""
    packed = base64.b64encode(zlib.compress(inner.encode("utf-8"), 9)).decode("ascii")
    return (
        "import base64, zlib; "
        f"exec(compile(zlib.decompress(base64.b64decode({packed!r})), '<public-g3-bootstrap>', 'exec'))\n"
    )


def prepare_local_operation_artifacts(config, operation_dir):
    """Validate pinned local sources & bundle, writing frozen operation copies before any cloud call."""
    operation_dir = Path(operation_dir).resolve()
    operation_dir.mkdir(parents=True, exist_ok=True)
    events_path = operation_dir / "lifecycle.jsonl"
    if events_path.exists():
        raise FileExistsError(f"refusing to reuse existing operation directory: {events_path}")

    lora_path = _resolve_project_path(config.get("lora_adapter_path", "toolloop_fix/lora_adapter.py"))
    worker_path = _resolve_project_path(
        config.get("worker_script_path", "scripts/colab_g3_train_worker.py")
    )
    bundle_path = _resolve_project_path(
        config.get(
            "training_bundle_path",
            "experiments/EXP-20261120-062/public_colab_training_bundle.json",
        )
    )

    for label, p in (
        ("lora_adapter_path", lora_path),
        ("worker_script_path", worker_path),
        ("training_bundle_path", bundle_path),
    ):
        if not p.is_file():
            raise FileNotFoundError(f"{label} not found: {p}")

    lora_text = lora_path.read_text(encoding="utf-8")
    worker_text = worker_path.read_text(encoding="utf-8")
    lora_sha = sha256_bytes(lora_text.encode("utf-8"))
    worker_sha = sha256_bytes(worker_text.encode("utf-8"))

    pins = config.get("source_sha256") or {}
    if "lora_adapter.py" in pins and pins["lora_adapter.py"] != lora_sha:
        raise ValueError("lora_adapter.py SHA256 pin mismatch")
    if "colab_g3_train_worker.py" in pins and pins["colab_g3_train_worker.py"] != worker_sha:
        raise ValueError("colab_g3_train_worker.py SHA256 pin mismatch")
    if "controller_sha256" in config and config["controller_sha256"]:
        ctrl_sha = sha256_file(Path(__file__).resolve())
        if ctrl_sha != config["controller_sha256"]:
            raise ValueError("run_colab_g3_lifecycle.py controller_sha256 pin mismatch")

    raw_bundle = bundle_path.read_bytes()
    bundle_sha = sha256_bytes(raw_bundle)
    if "expected_bundle_sha256" in config and config["expected_bundle_sha256"]:
        if bundle_sha != config["expected_bundle_sha256"]:
            raise ValueError(
                f"training bundle SHA256 mismatch: expected {config['expected_bundle_sha256']}, got {bundle_sha}"
            )
    bundle_obj = json.loads(raw_bundle.decode("utf-8"))
    if (
        not isinstance(bundle_obj, dict)
        or bundle_obj.get("g3_dataset_ready") is not True
        or bundle_obj.get("g2_passed_verified") is not True
    ):
        raise ValueError("training bundle failed g3_dataset_ready / g2_passed_verified precheck")

    compressed_bundle = zlib.compress(raw_bundle, 9)
    bundle_zlib_sha = sha256_bytes(compressed_bundle)

    training_jobs = config.get("training_jobs") or list(DEFAULT_TRAINING_JOBS)
    for job in training_jobs:
        check_adapter_id(job["adapter_id"])

    train_job_config = {
        "schema_version": "iter9_colab_g3_train_job_config_v1",
        "model_id": MODEL_ID,
        "revision": REVISION,
        "expected_model_files": PINNED_MODEL_FILES,
        "expected_bundle_sha256": bundle_sha,
        "expected_bundle_zlib_sha256": bundle_zlib_sha,
        "lora_rank": int(config.get("lora_rank", 4)),
        "lora_alpha": float(config.get("lora_alpha", 8.0)),
        "lora_module_prefix": str(config.get("lora_module_prefix", "model.language_model.")),
        "arm_deadline_seconds": float(config.get("arm_deadline_seconds", 450.0)),
        "max_chunk_raw_bytes": int(config.get("max_chunk_raw_bytes", MAX_CHUNK_RAW_BYTES)),
        "micro_batch_sequences": int(config.get("micro_batch_sequences", 4)),
        "eval_batch_sequences": int(config.get("eval_batch_sequences", 8)),
        "training_jobs": training_jobs,
        "public_probe": config.get("public_probe") or DEFAULT_PUBLIC_PROBE,
    }
    train_job_cfg_text = json.dumps(train_job_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    train_job_cfg_sha = sha256_bytes(train_job_cfg_text.encode("utf-8"))

    sources_dir = operation_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    _write_exclusive_bytes(sources_dir / "lora_adapter.py", lora_text.encode("utf-8"))
    _write_exclusive_bytes(sources_dir / "colab_g3_train_worker.py", worker_text.encode("utf-8"))
    _write_exclusive_bytes(sources_dir / "train_job_config.json", train_job_cfg_text.encode("utf-8"))
    bundle_zlib_path = operation_dir / "bundle.zlib"
    _write_exclusive_bytes(bundle_zlib_path, compressed_bundle)

    return {
        "events_path": events_path,
        "lora_text": lora_text,
        "worker_text": worker_text,
        "train_job_cfg_text": train_job_cfg_text,
        "train_job_config": train_job_config,
        "bundle_zlib_path": bundle_zlib_path,
        "bundle_sha256": bundle_sha,
        "bundle_size_bytes": len(raw_bundle),
        "bundle_zlib_sha256": bundle_zlib_sha,
        "bundle_zlib_size_bytes": len(compressed_bundle),
        "source_sha256": {
            "lora_adapter.py": lora_sha,
            "colab_g3_train_worker.py": worker_sha,
            "train_job_config.json": train_job_cfg_sha,
            "controller": sha256_file(Path(__file__).resolve()),
        },
    }


def _write_adapter_manifests(
    adapters_out_dir,
    operation_dir,
    receipt,
    receipt_sha256,
    reassembled_records,
):
    """Write per-adapter `<adapter_id>/adapter_manifest.json` and top-level `g3_adapters_manifest.json`."""
    adapters_out_dir = Path(adapters_out_dir).resolve()
    operation_dir = Path(operation_dir).resolve()
    lora_inj = receipt["lora_injection"]
    bundle_info = receipt["training_bundle"]

    summary_entries = []
    for rec in reassembled_records:
        job_report = rec["job_report"]
        adapter_id = job_report["adapter_id"]
        st_path = rec["safetensors_path"]
        manifest_path = adapters_out_dir / adapter_id / "adapter_manifest.json"
        manifest_obj = {
            "schema_version": 1,
            "adapter_id": adapter_id,
            "training_arm": job_report["arm"],
            "arm": job_report["arm"],
            "objective": job_report["objective"],
            "train_seed": int(job_report["seed"]),
            "seed": int(job_report["seed"]),
            "adapter_version": lora_inj.get("adapter_version", LORA_ADAPTER_VERSION),
            "model_id": receipt["model_id"],
            "revision": receipt["revision"],
            "base_model_revision": receipt["revision"],
            "rank": int(lora_inj["rank"]),
            "alpha": float(lora_inj["alpha"]),
            "lora_rank": int(lora_inj["rank"]),
            "lora_alpha": float(lora_inj["alpha"]),
            "module_prefix": lora_inj.get("module_prefix", "model.language_model."),
            "matched_module_count": int(lora_inj["matched_module_count"]),
            "trainable_param_count": int(lora_inj["trainable_param_count"]),
            "safetensors_path": str(st_path.resolve()),
            "adapter_path": str(st_path.resolve()),
            "safetensors_relpath": "adapter.safetensors",
            "safetensors_sha256": job_report["safetensors_sha256"],
            "sha256": job_report["safetensors_sha256"],
            "size_bytes": int(job_report["safetensors_size_bytes"]),
            "tensor_count": int(job_report["tensor_count"]),
            "base_weights_unchanged": bool(receipt.get("base_weights_unchanged") is True),
            "initial_base_weight_digest": receipt.get("initial_base_weight_digest"),
            "final_base_weight_digest": receipt.get("final_base_weight_digest"),
            "source_operation_dir": str(operation_dir),
            "training_receipt_sha256": receipt_sha256,
            "bundle_sha256": bundle_info["bundle_sha256"],
            "dataset": job_report["dataset"],
            "dataset_sha256": job_report.get("dataset_sha256", ""),
            "example_count": int(job_report["example_count"]),
            "hyperparameters": job_report["hyperparameters"],
            "training_metrics": {
                "optimizer_steps": job_report["optimizer_steps"],
                "initial_loss": job_report["initial_loss"],
                "epoch_mean_losses": job_report["epoch_mean_losses"],
                "final_loss": job_report["final_loss"],
                "final_dpo_loss": job_report["final_dpo_loss"],
                "final_nll_loss": job_report["final_nll_loss"],
                "loss_decreased": job_report["loss_decreased"],
                "post_train_reward_accuracy": job_report["post_train_reward_accuracy"],
                "post_train_mean_reward_margin": job_report["post_train_mean_reward_margin"],
                "total_train_tokens": job_report["total_train_tokens"],
                "total_completion_tokens": job_report["total_completion_tokens"],
                "max_memory_allocated_bytes": job_report["max_memory_allocated_bytes"],
                "train_wall_seconds": job_report["train_wall_seconds"],
            },
            "public_probe": job_report["public_probe"],
            "files": [
                {
                    "path": "adapter.safetensors",
                    "sha256": job_report["safetensors_sha256"],
                    "size_bytes": int(job_report["safetensors_size_bytes"]),
                }
            ],
        }
        manifest_sha = _write_exclusive_json(manifest_path, manifest_obj)
        summary_entries.append(
            {
                "adapter_id": adapter_id,
                "training_arm": job_report["arm"],
                "objective": job_report["objective"],
                "train_seed": int(job_report["seed"]),
                "safetensors_path": str(st_path.resolve()),
                "safetensors_sha256": job_report["safetensors_sha256"],
                "size_bytes": int(job_report["safetensors_size_bytes"]),
                "adapter_manifest_path": str(manifest_path.resolve()),
                "adapter_manifest_sha256": manifest_sha,
            }
        )

    index_obj = {
        "schema_version": 1,
        "model_id": receipt["model_id"],
        "revision": receipt["revision"],
        "base_model_revision": receipt["revision"],
        "adapter_version": lora_inj.get("adapter_version", LORA_ADAPTER_VERSION),
        "source_operation_dir": str(operation_dir),
        "training_receipt_sha256": receipt_sha256,
        "adapter_count": len(summary_entries),
        "adapters": summary_entries,
    }
    index_sha = _write_exclusive_json(adapters_out_dir / "g3_adapters_manifest.json", index_obj)
    return summary_entries, index_sha


def run_lifecycle(
    config_path,
    operation_dir,
    adapters_out_dir,
    colab_cli=None,
    subprocess_runner=subprocess.run,
    sleep_fn=time.sleep,
):
    """Execute the full Colab L4 G3 training lifecycle with mandatory teardown."""
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    operation_dir = Path(operation_dir).resolve()
    adapters_out_dir = Path(adapters_out_dir).resolve()
    adapters_out_dir.mkdir(parents=True, exist_ok=True)

    prepared = prepare_local_operation_artifacts(config, operation_dir)
    events_path = prepared["events_path"]
    events_path.touch(exist_ok=False)

    cli_bin = str(colab_cli or config.get("colab_cli_path") or DEFAULT_COLAB_CLI)
    wall_limit = float(config.get("wall_limit_seconds", 1200.0))
    poll_interval = float(config.get("poll_interval_seconds", 4.0))
    max_cu_delta = float(config.get("max_cu_rate_delta", 15.0))
    chunk_retries = int(config.get("chunk_download_retries", 3))

    nonce = config.get("fixed_test_nonce") or uuid.uuid4().hex
    check_nonce(nonce)
    sentinel_ticket = uuid.uuid4().hex
    session_name = "tlfix-" + nonce[:12]
    remote_workdir = f"/content/{session_name}"
    start_mono = time.monotonic()

    _write_exclusive_bytes(operation_dir / "sources" / "nonce.txt", (nonce + "\n").encode("utf-8"))

    def note(step, **fields):
        row = {
            "step": step,
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "elapsed_s": round(time.monotonic() - start_mono, 2),
            **fields,
        }
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def run_cli_text(step, *args, limit=35.0, stdin_text=None, cleanup=False, log_event=True):
        allowance = float(limit) if cleanup else min(float(limit), wall_limit - (time.monotonic() - start_mono))
        if allowance < 1.0:
            raise RuntimeError("wall_deadline")
        try:
            proc = subprocess_runner(
                [cli_bin, *args],
                input=(stdin_text.encode("utf-8") if stdin_text is not None else b""),
                capture_output=True,
                timeout=allowance,
            )
        except subprocess.TimeoutExpired:
            if log_event:
                note(step, exit=None, error="client_wall_timeout")
            raise RuntimeError("client_wall_timeout") from None
        stdout_b = proc.stdout if isinstance(proc.stdout, (bytes, bytearray)) else (proc.stdout or "").encode("utf-8")
        stderr_b = proc.stderr if isinstance(proc.stderr, (bytes, bytearray)) else (proc.stderr or "").encode("utf-8")
        combined = stdout_b.decode("utf-8", errors="replace") + "\n" + stderr_b.decode("utf-8", errors="replace")
        reason = next((s for s in SAFE_CLI_REASONS if s.lower() in combined.lower()), None)
        if log_event:
            note(step, exit=proc.returncode, reason=reason)
        return proc.returncode, stdout_b, combined

    def exec_cell(label, code_text, timeout_secs):
        allowance = min(float(timeout_secs) + 8.0, wall_limit - (time.monotonic() - start_mono))
        if allowance < 1.0:
            raise RuntimeError("wall_deadline")
        code_bytes = code_text.encode("utf-8")
        try:
            proc = subprocess_runner(
                [cli_bin, "exec", "-s", session_name, "--timeout", str(int(timeout_secs))],
                input=code_bytes,
                capture_output=True,
                timeout=allowance,
            )
            stdout_b = (
                proc.stdout
                if isinstance(proc.stdout, (bytes, bytearray))
                else (proc.stdout or "").encode("utf-8")
            )
            note(
                label,
                exit=proc.returncode,
                stdout_bytes=len(stdout_b),
                cell_sha256=sha256_bytes(code_bytes),
            )
            return proc.returncode, bytes(stdout_b)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or b""
            if isinstance(partial, str):
                partial = partial.encode("utf-8")
            note(
                label,
                exit=None,
                stdout_bytes=len(partial),
                cell_sha256=sha256_bytes(code_bytes),
            )
            return None, bytes(partial)

    def download_remote_file(remote_rel_path, max_bytes=MAX_CHUNK_FRAME_BYTES + 65536, timeout_secs=20.0):
        if ".." in Path(remote_rel_path).parts or str(remote_rel_path).startswith("/"):
            raise ValueError("unsafe remote relative path")
        allowance = min(float(timeout_secs), wall_limit - (time.monotonic() - start_mono))
        if allowance < 1.0:
            raise RuntimeError("wall_deadline")
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "dl.bin"
            try:
                proc = subprocess_runner(
                    [cli_bin, "download", "-s", session_name, f"{remote_workdir}/{remote_rel_path}", str(dst)],
                    input=b"",
                    capture_output=True,
                    timeout=allowance,
                )
            except subprocess.TimeoutExpired:
                return None
            if proc.returncode == 0 and dst.is_file() and dst.stat().st_size <= max_bytes:
                return dst.read_bytes()
            return None

    def list_assignments(step, cleanup=False):
        rc, _, combined = run_cli_text(step, "sessions", limit=30.0, cleanup=cleanup)
        if rc != 0:
            raise RuntimeError("session_listing_failed")
        records = re.findall(r"^\[([^\]]+)\]\s+(\S+)\s+\| Hardware: ([^|]+?)\s+\|", combined, re.M)
        if not records and "No active sessions found on server." not in combined:
            raise RuntimeError("unrecognized_session_list")
        return {
            hashlib.sha256(endpoint.encode("utf-8")).hexdigest(): (name, hw.strip())
            for name, endpoint, hw in records
        }

    def query_usage(step, cleanup=False):
        rc, _, combined = run_cli_text(step, "usage", limit=30.0, cleanup=cleanup)
        if rc != 0:
            raise RuntimeError("usage_failed")
        parsed = {}
        for key, pattern in (
            ("balance_cu", r"Current balance:\s*([\d.]+)"),
            ("rate_cu_hr", r"Usage rate:\s*([\d.]+)"),
            ("assignments", r"Active assignments:\s*([\d]+)"),
        ):
            match = re.search(pattern, combined)
            if not match:
                raise RuntimeError("unrecognized_usage_output")
            parsed[key] = float(match.group(1))
        note(step + "_observation", **parsed)
        return parsed

    note(
        "reservation",
        session=session_name,
        receipt_nonce=nonce,
        training=True,
        source_sha256=prepared["source_sha256"],
        bundle_sha256=prepared["bundle_sha256"],
        bundle_zlib_sha256=prepared["bundle_zlib_sha256"],
        bundle_zlib_bytes=prepared["bundle_zlib_size_bytes"],
        job_count=len(prepared["train_job_config"]["training_jobs"]),
        wall_limit_seconds=wall_limit,
    )

    before = list_assignments("baseline_sessions")
    u0 = query_usage("baseline_usage")
    if len(before) != int(u0["assignments"]) or any(n == session_name for n, _ in before.values()):
        raise RuntimeError("ambiguous_baseline")

    own = None
    allocation_attempted = False
    allocation_refused = False
    released = False
    receipt_completed = False
    adapters_reassembled = False

    sentinel_payload = (
        json.dumps(
            {
                "schema_version": 1,
                "scope": "pre_arm_two_way_transport_sentinel",
                "status": "sentinel_ready",
                "training_performed": False,
                "tool_executed": False,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    bootstrap_code = _build_bootstrap_cell(
        remote_workdir=remote_workdir,
        text_files={
            "lora_adapter.py": prepared["lora_text"],
            "colab_g3_train_worker.py": prepared["worker_text"],
            "train_job_config.json": prepared["train_job_cfg_text"],
            "nonce.txt": nonce + "\n",
        },
        sentinel_bytes=sentinel_payload,
        sentinel_ticket=sentinel_ticket,
    )

    try:
        allocation_attempted = True
        rc_new, _, out_new = run_cli_text("new", "new", "-s", session_name, "--gpu", "L4", limit=65.0)
        allocation_refused = rc_new != 0 and (
            "Allocation refused" in out_new or "Backend rejected accelerator" in out_new
        )
        cur = list_assignments("allocated_sessions")
        matches = [
            ep
            for ep, (n, hw) in cur.items()
            if n == session_name and ep not in before and hw.upper() == "L4"
        ]
        if rc_new != 0 or len(matches) != 1 or set(cur) - {matches[0]} != set(before):
            if matches:
                own = matches[0]
            raise RuntimeError("new_l4_not_isolated")
        own = matches[0]
        note("owned_l4", endpoint_sha256=own)

        u_alloc = query_usage("allocated_usage")
        if int(u_alloc["assignments"]) != len(cur) or (u_alloc["rate_cu_hr"] - u0["rate_cu_hr"]) > max_cu_delta:
            raise RuntimeError("unexpected_assignment_or_cost")

        rc_status, _, out_status = run_cli_text("status", "status", "-s", session_name, limit=25.0)
        if rc_status != 0 or "Hardware: L4" not in out_status:
            raise RuntimeError("status_mismatch")

        ping_code = "print('TLP_READY', flush=True)\n"
        _, warm1 = exec_cell("warm", ping_code, 75)
        if b"TLP_READY" not in warm1.splitlines():
            _, warm2 = exec_cell("warm_again", ping_code, 45)
            if b"TLP_READY" not in warm2.splitlines():
                raise RuntimeError("kernel_unreachable")

        _, boot_stdout = exec_cell("bootstrap_no_model_until_armed", bootstrap_code, 45)
        stdout_sentinel_ok = False
        try:
            decoded_stdout, _ = receive_framed_json(
                boot_stdout, sentinel_ticket, prefix=SENTINEL_FRAME_PREFIX
            )
            stdout_sentinel_ok = decoded_stdout == sentinel_payload
        except ValueError:
            stdout_sentinel_ok = False

        if not stdout_sentinel_ok:
            # Safe before arming: started.lock prevents duplicate worker; retry once to obtain stdout frame.
            _, boot_stdout = exec_cell("idempotent_bootstrap_no_model", bootstrap_code, 45)
            decoded_stdout, _ = receive_framed_json(
                boot_stdout, sentinel_ticket, prefix=SENTINEL_FRAME_PREFIX
            )
            if decoded_stdout != sentinel_payload:
                raise RuntimeError("stdout_sentinel_mismatch")

        dl_sent_frame = download_remote_file("sentinel_frame.txt", max_bytes=65536, timeout_secs=15.0)
        if not dl_sent_frame:
            raise RuntimeError("download_sentinel_missing")
        decoded_dl, _ = receive_framed_json(dl_sent_frame, sentinel_ticket, prefix=SENTINEL_FRAME_PREFIX)
        if decoded_dl != sentinel_payload:
            raise RuntimeError("contents_sentinel_mismatch")

        _write_exclusive_bytes(operation_dir / "sentinel.json", sentinel_payload)
        note("two_way_sentinel", identical=True, bytes=len(sentinel_payload))

        ready_stage = ""
        for _ in range(6):
            raw_stage = download_remote_file("stage.txt", max_bytes=4096, timeout_secs=12.0)
            if raw_stage is not None:
                ready_stage = raw_stage.decode("utf-8", errors="replace").strip()
                if ready_stage == "waiting_for_arm":
                    break
            sleep_fn(1.0)
        if ready_stage != "waiting_for_arm":
            raise RuntimeError("detached_worker_not_waiting_for_arm")

        bundle_uploaded = False
        for attempt_idx in range(1, 3):
            rc_up, _, _ = run_cli_text(
                f"upload_bundle_attempt_{attempt_idx}",
                "upload",
                "-s",
                session_name,
                str(prepared["bundle_zlib_path"]),
                f"{remote_workdir}/bundle.zlib",
                limit=45.0,
            )
            if rc_up == 0:
                bundle_uploaded = True
                break
            sleep_fn(1.0)
        if not bundle_uploaded:
            raise RuntimeError("bundle_upload_failed")

        arm_path = operation_dir / "armed.txt"
        _write_exclusive_bytes(arm_path, (nonce + "\n").encode("utf-8"))
        rc_arm, _, _ = run_cli_text(
            "arm_single_worker",
            "upload",
            "-s",
            session_name,
            str(arm_path),
            f"{remote_workdir}/armed.txt",
            limit=25.0,
        )
        if rc_arm != 0:
            raise RuntimeError("activation_upload_failed")

        last_stage = None
        final_stage_seen = False
        last_keepalive_mono = time.monotonic()
        consecutive_missing_stage = 0
        keepalive_interval = float(config.get("keepalive_interval_seconds", 120.0))
        while (time.monotonic() - start_mono) < (wall_limit - 30.0):
            raw_stage = download_remote_file("stage.txt", max_bytes=4096, timeout_secs=15.0)
            if raw_stage is not None:
                consecutive_missing_stage = 0
                stage_str = raw_stage.decode("utf-8", errors="replace").strip()
                if stage_str != last_stage:
                    note(
                        "worker_stage",
                        stage=stage_str if is_recognized_stage(stage_str) else "not_observed_or_unrecognized",
                    )
                    last_stage = stage_str
                if stage_str.startswith("receipt_published"):
                    final_stage_seen = True
                    break
            else:
                consecutive_missing_stage += 1
                if consecutive_missing_stage >= 6:
                    cur_alive = list_assignments("poll_session_alive_check")
                    if own not in cur_alive:
                        raise RuntimeError("session_lost_before_receipt")
                    consecutive_missing_stage = 0
            if (time.monotonic() - last_keepalive_mono) >= keepalive_interval:
                exec_cell("kernel_keepalive", "print('TLP_ALIVE', flush=True)\n", 20)
                last_keepalive_mono = time.monotonic()
            sleep_fn(poll_interval)

        if not final_stage_seen:
            raw_stage = download_remote_file("stage.txt", max_bytes=4096, timeout_secs=15.0)
            if raw_stage is not None:
                stage_str = raw_stage.decode("utf-8", errors="replace").strip()
                if stage_str.startswith("receipt_published"):
                    final_stage_seen = True

        receipt_frame_bytes = None
        for _ in range(3):
            receipt_frame_bytes = download_remote_file(
                "receipt_frame.txt", max_bytes=MAX_CHUNK_FRAME_BYTES * 4, timeout_secs=25.0
            )
            if receipt_frame_bytes:
                break
            sleep_fn(1.5)
        if not receipt_frame_bytes:
            raise RuntimeError("verified_receipt_frame_missing")

        receipt_raw_bytes, receipt_obj = receive_framed_json(
            receipt_frame_bytes, nonce, prefix=RECEIPT_FRAME_PREFIX
        )
        receipt_path = operation_dir / "g3_training_receipt.json"
        receipt_sha = _write_exclusive_bytes(receipt_path, receipt_raw_bytes)

        receipt_completed = (
            receipt_obj.get("status") == "completed"
            and receipt_obj.get("base_weights_unchanged") is True
            and isinstance(receipt_obj.get("adapters"), list)
            and len(receipt_obj["adapters"]) > 0
        )
        if not receipt_completed:
            raw_diag = download_remote_file("diagnostic.txt", max_bytes=1024, timeout_secs=12.0)
            if raw_diag:
                diag_clean = raw_diag.strip()
                if len(diag_clean) < 400 and re.fullmatch(rb"[a-zA-Z0-9_.,:= -]+", diag_clean):
                    note("sanitized_diagnostic", code=diag_clean.decode("ascii"))
            note(
                "local_receipt",
                status=receipt_obj.get("status"),
                stage=receipt_obj.get("stage"),
                error_type=receipt_obj.get("error_type"),
                adapter_count=0,
                sha256=receipt_sha,
            )
            raise RuntimeError("remote_worker_reported_failure")

        note(
            "local_receipt",
            status=receipt_obj.get("status"),
            stage=receipt_obj.get("stage"),
            error_type=None,
            adapter_count=len(receipt_obj["adapters"]),
            base_weights_unchanged=receipt_obj.get("base_weights_unchanged"),
            sha256=receipt_sha,
        )

        local_chunks_dir = operation_dir / "chunks"
        local_chunks_dir.mkdir(parents=True, exist_ok=True)
        reassembled_records = []

        for job_report in receipt_obj["adapters"]:
            adapter_id = check_adapter_id(job_report["adapter_id"])
            chunk_files = job_report["chunk_files"]
            expected_frame_shas = job_report["chunk_frame_sha256s"]
            if len(chunk_files) != int(job_report["chunk_count"]) or len(expected_frame_shas) != len(chunk_files):
                raise RuntimeError("receipt_chunk_inventory_invalid")

            chunk_frames = []
            for rel_chunk, expected_fsha in zip(chunk_files, expected_frame_shas):
                chunk_bytes = None
                for _ in range(chunk_retries):
                    candidate = download_remote_file(
                        rel_chunk, max_bytes=MAX_CHUNK_FRAME_BYTES, timeout_secs=25.0
                    )
                    if candidate is not None and sha256_bytes(candidate) == expected_fsha:
                        chunk_bytes = candidate
                        break
                    sleep_fn(1.0)
                if chunk_bytes is None:
                    raise RuntimeError(f"chunk_download_failed_{adapter_id}")
                _write_exclusive_bytes(local_chunks_dir / Path(rel_chunk).name, chunk_bytes)
                chunk_frames.append(chunk_bytes)

            st_bytes = decode_and_reassemble_chunks(
                chunk_frame_payloads=chunk_frames,
                expected_nonce=nonce,
                expected_adapter_id=adapter_id,
                expected_safetensors_sha256=job_report["safetensors_sha256"],
            )
            st_out_path = adapters_out_dir / adapter_id / "adapter.safetensors"
            written_sha = _write_exclusive_bytes(st_out_path, st_bytes)
            if written_sha != job_report["safetensors_sha256"]:
                raise RuntimeError("written_safetensors_sha256_mismatch")

            note(
                "adapter_reassembled",
                adapter_id=adapter_id,
                chunk_count=len(chunk_frames),
                size_bytes=len(st_bytes),
                safetensors_sha256=written_sha,
            )
            reassembled_records.append(
                {
                    "adapter_id": adapter_id,
                    "safetensors_path": st_out_path,
                    "job_report": job_report,
                }
            )

        manifest_summaries, index_sha = _write_adapter_manifests(
            adapters_out_dir=adapters_out_dir,
            operation_dir=operation_dir,
            receipt=receipt_obj,
            receipt_sha256=receipt_sha,
            reassembled_records=reassembled_records,
        )
        note(
            "adapter_manifests_written",
            adapter_count=len(manifest_summaries),
            g3_adapters_manifest_sha256=index_sha,
        )
        adapters_reassembled = True

    except KeyboardInterrupt:
        note("work_interrupted", code="KeyboardInterrupt")
    except Exception as exc:
        safe_err = str(exc) if type(exc) is RuntimeError and re.fullmatch(r"[a-zA-Z0-9_.-]+", str(exc)) else type(exc).__name__
        note("work_failed", code=safe_err)
    finally:
        for attempt in range(3):
            try:
                cur_rel = list_assignments("release_sessions_check", cleanup=True)
                if own is None and allocation_attempted and not allocation_refused:
                    own = next(
                        (ep for ep, (n, _) in cur_rel.items() if n == session_name and ep not in before),
                        None,
                    )
                if own is not None and own in cur_rel and cur_rel[own][0] == session_name:
                    rc_stop, _, msg_stop = run_cli_text(
                        "stop_owned_only", "stop", "-s", session_name, limit=35.0, cleanup=True
                    )
                    note("stop_ack", confirmed=(rc_stop == 0 and "Session terminated" in msg_stop))
                fin = list_assignments("final_sessions", cleanup=True)
                u_fin = query_usage("final_usage", cleanup=True)
                released = (
                    (own not in fin)
                    and all(n != session_name for n, _ in fin.values())
                    and len(fin) == int(u_fin["assignments"])
                )
                note(
                    "cleanup_observed",
                    owned_absent=released,
                    original_set_unchanged=(set(before) == set(fin)),
                    active_count=len(fin),
                )
                if released:
                    break
            except Exception as exc:
                note("cleanup_check_error", error=type(exc).__name__)
            if attempt < 2:
                sleep_fn(1.5)

    note(
        "finished",
        received_completed_training=receipt_completed,
        adapters_reassembled=adapters_reassembled,
        owned_released=released,
        allocation_refused=allocation_refused,
    )
    return 0 if (receipt_completed and adapters_reassembled and released) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="JSON lifecycle configuration")
    parser.add_argument("--operation-dir", type=Path, required=True, help="new operation directory")
    parser.add_argument(
        "--adapters-out-dir",
        type=Path,
        required=True,
        help="directory where <adapter_id>/adapter.safetensors & adapter_manifest.json are written",
    )
    parser.add_argument("--colab-cli", type=Path, default=None, help="optional colab CLI path override")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    return run_lifecycle(
        config_path=args.config,
        operation_dir=args.operation_dir,
        adapters_out_dir=args.adapters_out_dir,
        colab_cli=args.colab_cli,
    )


if __name__ == "__main__":
    sys.exit(main())
