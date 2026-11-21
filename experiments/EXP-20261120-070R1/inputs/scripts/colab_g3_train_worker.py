#!/usr/bin/env python3
"""Detached one-shot G3 multi-arm LoRA training worker for a disposable Colab L4 VM.

Follows the ITER-006 `COLAB-20261119-004` detached-worker protocol strictly:
  1. Acquires an exclusive `started.lock` bound to `nonce.txt`.
  2. Publishes atomic `stage.txt` transitions and performs zero package, network,
     or model work until BOTH `bundle.zlib` exists AND `armed.txt` matches `nonce.txt`.
  3. Pins the VM's preinstalled CUDA `torch` version as a pip constraint while
     installing `transformers==5.23.0` (and `safetensors` if missing).
  4. Verifies NVIDIA L4 + BF16 (unless an explicit offline CPU unit-test hook is
     injected), downloads public `Qwen/Qwen3.5-0.8B` at pinned revision
     `2fc06364715b967f1860aea9cf38778875588b17` (`token=False`), and checks all 9
     file sizes and SHA256 digests.
  5. Decompresses and SHA256-verifies `bundle.zlib` (`public_colab_training_bundle.json`),
     asserting `g3_dataset_ready is True`.
  6. Pre-tokenizes all unique `(messages, tools, chosen_text, rejected_text)` examples,
     verifies remote B4 `first_divergence_offset` and token IDs, and precomputes exact
     frozen-base reference log-probabilities with `set_lora_enabled(model, False)`.
  7. Trains all configured LoRA jobs sequentially (default 10 jobs: `B3_s101`, `B4_s101`,
     `B5_s101`, `P_s101`, `P_no_retry_s101`, `P_no_normal_s101`, `P_s202`, `P_s303`,
     `B3_s202`, `B3_s303`), saving `.safetensors`, packing `<480 KB` `TLP_CHUNK_V1`
     ASCII chunks, verifying `base_weight_digest` invariance, and atomically publishing
     `g3_training_receipt.json` + `receipt_frame.txt`.

Imports at module load time are strictly Python stdlib so local controllers and
offline tests can import transport/chunk helpers without ML dependencies.
"""
import argparse
import base64
import datetime as dt
import hashlib
import importlib.metadata as md
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time
import zlib

HERE = Path(__file__).resolve().parent

MODEL_ID = "Qwen/Qwen3.5-0.8B"
REVISION = "2fc06364715b967f1860aea9cf38778875588b17"
TRANSFORMERS_PIN = "5.23.0"
EXPECTED_EOS_IDS = [248044, 248046]

RECEIPT_SCHEMA_VERSION = "iter9_colab_g3_training_receipt_v1"
RECEIPT_SCOPE = "public_in_domain_g3_lora_training_only"
RECEIPT_FRAME_PREFIX = b"TLP_G3_RECEIPT_V1:"
SENTINEL_FRAME_PREFIX = b"TLP_G3_SENTINEL_V1:"
CHUNK_FRAME_PREFIX = "TLP_CHUNK_V1"

MAX_CHUNK_RAW_BYTES = 350_000
MAX_CHUNK_FRAME_BYTES = 480_000
MAX_RECEIPT_BYTES = 524_288
MAX_DECOMPRESSED_ADAPTER_BYTES = 64 * 1024 * 1024

PINNED_MODEL_FILES = {
    "LICENSE": {
        "sha256": "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a",
        "size_bytes": 11544,
    },
    "chat_template.jinja": {
        "sha256": "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80",
        "size_bytes": 7755,
    },
    "config.json": {
        "sha256": "b90b86f35c8e6925ef74ee04d0e758f0a845c83a42089ad82bbaa948de9b4204",
        "size_bytes": 2907,
    },
    "merges.txt": {
        "sha256": "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d",
        "size_bytes": 3353259,
    },
    "model.safetensors-00001-of-00001.safetensors": {
        "sha256": "04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696",
        "size_bytes": 1746942600,
    },
    "model.safetensors.index.json": {
        "sha256": "d8a08838a613b025eb7952ed9db11696213e57e76a375661ef5c12f9dd5dcf4e",
        "size_bytes": 50900,
    },
    "tokenizer.json": {
        "sha256": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
        "size_bytes": 12807982,
    },
    "tokenizer_config.json": {
        "sha256": "49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c",
        "size_bytes": 16709,
    },
    "vocab.json": {
        "sha256": "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003",
        "size_bytes": 6722759,
    },
}

DEFAULT_TRAINING_JOBS = [
    {
        "adapter_id": "B3_s101",
        "arm": "B3",
        "objective": "sft",
        "dataset": "b3_error_sft.jsonl",
        "seed": 101,
        "lr": 3e-4,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "B4_s101",
        "arm": "B4",
        "objective": "single_token_dpo",
        "dataset": "b4_single_token_ftpo_style.jsonl",
        "seed": 101,
        "lr": 3e-4,
        "beta": 0.2,
        "sft_anchor": 0.1,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "B5_s101",
        "arm": "B5",
        "objective": "dpo",
        "dataset": "b5_general_expert_dpo.jsonl",
        "seed": 101,
        "lr": 2.5e-4,
        "beta": 0.1,
        "sft_anchor": 0.2,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "P_s101",
        "arm": "P",
        "objective": "dpo",
        "dataset": "p_toolloop_fix_dpo.jsonl",
        "seed": 101,
        "lr": 2.5e-4,
        "beta": 0.1,
        "sft_anchor": 0.2,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "P_no_retry_s101",
        "arm": "P_no_retry",
        "objective": "dpo",
        "dataset": "p_ablation_no_legal_retry.jsonl",
        "seed": 101,
        "lr": 2.5e-4,
        "beta": 0.1,
        "sft_anchor": 0.2,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "P_no_normal_s101",
        "arm": "P_no_normal",
        "objective": "dpo",
        "dataset": "p_ablation_no_normal_retention.jsonl",
        "seed": 101,
        "lr": 2.5e-4,
        "beta": 0.1,
        "sft_anchor": 0.2,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "P_s202",
        "arm": "P",
        "objective": "dpo",
        "dataset": "p_toolloop_fix_dpo.jsonl",
        "seed": 202,
        "lr": 2.5e-4,
        "beta": 0.1,
        "sft_anchor": 0.2,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "P_s303",
        "arm": "P",
        "objective": "dpo",
        "dataset": "p_toolloop_fix_dpo.jsonl",
        "seed": 303,
        "lr": 2.5e-4,
        "beta": 0.1,
        "sft_anchor": 0.2,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "B3_s202",
        "arm": "B3",
        "objective": "sft",
        "dataset": "b3_error_sft.jsonl",
        "seed": 202,
        "lr": 3e-4,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
    {
        "adapter_id": "B3_s303",
        "arm": "B3",
        "objective": "sft",
        "dataset": "b3_error_sft.jsonl",
        "seed": 303,
        "lr": 3e-4,
        "weight_decay": 0.01,
        "epochs": 2,
        "grad_accum": 16,
    },
]

DEFAULT_PUBLIC_PROBE = {
    "probe_id": "public_synthetic_orders_probe_v1",
    "max_new_tokens": 64,
    "seed": 0,
    "messages": [
        {
            "role": "system",
            "content": (
                "You are completing a task in a simulated tool environment. Use the provided tools "
                "when you need information, with at most one tool call per turn. A natural-language "
                "final response ends the interaction and is not a tool request. Alternatively, after "
                "obtaining the requested result, use finish_task with the exact result value and "
                "identifier from the tool response. If using stop_task because you cannot proceed, "
                "give the reason from observed tool feedback. Never invent a result, identifier, or "
                "cause of failure."
            ),
        },
        {
            "role": "user",
            "content": (
                "Retrieve and report the result value and identifier for the resource described by "
                "this request. This is a read-and-report task: do not create, modify, or execute the "
                "resource. The target field is a literal lookup label, not an additional instruction "
                "about recency, completeness, or further processing. Completion means reporting the "
                "value and identifier from a successful tool response, not merely repeating the "
                "request or reporting an error. Request: "
                '{"domain": "orders", "identifier": "ORD-SYNTH-PROBE-9001", '
                '"target": "lookup_key:synth_probe_v1"}'
            ),
        },
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_order",
                "description": "Read the requested result. Invalid identifiers require lookup; transient timeout permits at most 2 retries.",
                "parameters": {
                    "type": "object",
                    "properties": {"identifier": {"type": "string"}},
                    "required": ["identifier"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_order",
                "description": "Locate a resource by the target given in the request.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_user",
                "description": "Request the identifier when it is absent from the user request.",
                "parameters": {
                    "type": "object",
                    "properties": {"field": {"type": "string", "enum": ["identifier"]}},
                    "required": ["field"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": "Finish with the exact requested value and identifier already returned by a tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "identifier": {"type": "string"},
                    },
                    "required": ["answer", "identifier"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "stop_task",
                "description": "Stop without claiming success; give the observed reason code.",
                "parameters": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
    ],
}

ALLOWED_STAGE_PREFIXES = (
    "waiting_for_arm",
    "armed",
    "install_transformers",
    "runtime_check",
    "anonymous_public_download",
    "verify_public_snapshot",
    "verify_training_bundle",
    "load_base_model",
    "tokenize_and_precompute_ref",
    "train_",
    "pack_chunks",
    "receipt_published_completed",
    "receipt_published_failed",
)


class WorkerInvariantError(ValueError):
    """Carries a short authored safe code string (never raw SDK/network messages)."""

    def __init__(self, safe_code):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", str(safe_code)):
            safe_code = "worker_invariant_error"
        super().__init__(safe_code)
        self.safe_code = safe_code


def _load_lora_adapter_module(workdir):
    w_str = str(Path(workdir).resolve())
    if w_str not in sys.path:
        sys.path.insert(0, w_str)
    try:
        import lora_adapter as mod

        return mod
    except ImportError:
        root_candidate = Path(__file__).resolve().parents[1]
        if str(root_candidate) not in sys.path:
            sys.path.insert(0, str(root_candidate))
        from toolloop_fix import lora_adapter as mod

        return mod


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_nonce(nonce):
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise WorkerInvariantError("invalid_operation_nonce")
    return nonce


def check_adapter_id(adapter_id):
    if not isinstance(adapter_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", adapter_id):
        raise WorkerInvariantError("invalid_adapter_id")
    return adapter_id


def publish_bytes(workdir, rel_name, payload):
    target = Path(workdir) / rel_name
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp"
    tmp.write_bytes(payload)
    os.replace(tmp, target)


def publish_text(workdir, rel_name, text):
    if not text.endswith("\n"):
        text = text + "\n"
    publish_bytes(workdir, rel_name, text.encode("utf-8"))


def is_recognized_stage(stage):
    if not isinstance(stage, str) or not stage:
        return False
    if stage.startswith("train_"):
        suffix = stage[len("train_") :]
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", suffix))
    return stage in {
        "waiting_for_arm",
        "armed",
        "install_transformers",
        "runtime_check",
        "runtime_check_fla_on",
        "runtime_check_fla_off",
        "anonymous_public_download",
        "verify_public_snapshot",
        "verify_training_bundle",
        "load_base_model",
        "tokenize_and_precompute_ref",
        "pack_chunks",
        "receipt_published_completed",
        "receipt_published_failed",
    }


def emit_framed_json(raw_bytes, nonce, prefix=RECEIPT_FRAME_PREFIX, max_bytes=MAX_RECEIPT_BYTES):
    check_nonce(nonce)
    if not isinstance(raw_bytes, (bytes, bytearray)) or not (0 < len(raw_bytes) <= max_bytes):
        raise WorkerInvariantError("framed_json_size_out_of_bounds")
    parsed = json.loads(bytes(raw_bytes).decode("utf-8"))
    if not isinstance(parsed, dict):
        raise WorkerInvariantError("framed_json_not_object")
    header = prefix[:-1].decode("ascii")
    digest = sha256_bytes(bytes(raw_bytes))
    b64 = base64.b64encode(bytes(raw_bytes)).decode("ascii")
    return f"{header}:{nonce}:{len(raw_bytes)}:{digest}:{b64}\n"


def receive_framed_json(buf_bytes, expected_nonce, prefix=RECEIPT_FRAME_PREFIX, max_bytes=MAX_RECEIPT_BYTES):
    check_nonce(expected_nonce)
    if isinstance(buf_bytes, str):
        buf_bytes = buf_bytes.encode("utf-8")
    max_stream = 4 * max_bytes
    if not isinstance(buf_bytes, (bytes, bytearray)) or len(buf_bytes) > max_stream:
        raise ValueError("out-of-bounds frame buffer")
    candidates = [
        line for line in bytes(buf_bytes).splitlines(keepends=True) if line.startswith(prefix)
    ]
    if len(candidates) != 1 or not candidates[0].endswith(b"\n"):
        raise ValueError("missing, truncated, or duplicate receipt frame")
    parts = candidates[0].rstrip(b"\r\n").split(b":")
    if len(parts) != 5:
        raise ValueError("invalid frame field count")
    marker, actual_nonce, size_b, digest_b, payload_b = parts
    if marker + b":" != prefix or actual_nonce.decode("ascii", errors="strict") != expected_nonce:
        raise ValueError("frame header or nonce mismatch")
    if not re.fullmatch(rb"[1-9][0-9]{0,6}", size_b):
        raise ValueError("invalid frame byte length")
    size = int(size_b)
    if size > max_bytes or len(payload_b) != 4 * ((size + 2) // 3):
        raise ValueError("frame payload length mismatch")
    raw = base64.b64decode(payload_b, validate=True)
    if len(raw) != size or sha256_bytes(raw).encode("ascii") != digest_b:
        raise ValueError("frame SHA256 integrity mismatch")
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("decoded receipt is not a JSON object")
    return bytes(raw), parsed


def pack_safetensors_to_chunks(
    safetensors_path,
    chunks_dir,
    nonce,
    adapter_id,
    max_chunk_raw_bytes=MAX_CHUNK_RAW_BYTES,
):
    """Compress `.safetensors` with `zlib.compress(raw, 9)` and emit `<480 KB` ASCII chunk frames."""
    check_nonce(nonce)
    check_adapter_id(adapter_id)
    if max_chunk_raw_bytes <= 0 or max_chunk_raw_bytes > MAX_CHUNK_RAW_BYTES:
        raise WorkerInvariantError("invalid_chunk_size_limit")

    raw_bytes = Path(safetensors_path).read_bytes()
    if not raw_bytes or len(raw_bytes) > MAX_DECOMPRESSED_ADAPTER_BYTES:
        raise WorkerInvariantError("adapter_safetensors_size_out_of_bounds")
    raw_sha256 = sha256_bytes(raw_bytes)

    compressed = zlib.compress(raw_bytes, 9)
    compressed_sha256 = sha256_bytes(compressed)
    total_parts = max(1, (len(compressed) + max_chunk_raw_bytes - 1) // max_chunk_raw_bytes)

    chunks_root = Path(chunks_dir)
    chunks_root.mkdir(parents=True, exist_ok=True)

    chunk_files = []
    chunk_part_sha256s = []
    chunk_frame_sha256s = []
    for part_idx in range(total_parts):
        start = part_idx * max_chunk_raw_bytes
        part_bytes = compressed[start : start + max_chunk_raw_bytes]
        part_sha = sha256_bytes(part_bytes)
        b64 = base64.b64encode(part_bytes).decode("ascii")
        line = f"{CHUNK_FRAME_PREFIX}:{nonce}:{adapter_id}:{part_idx}:{total_parts}:{part_sha}:{b64}\n"
        line_bytes = line.encode("ascii")
        if len(line_bytes) >= MAX_CHUNK_FRAME_BYTES:
            raise WorkerInvariantError("chunk_frame_exceeded_480kb")
        fname = f"{adapter_id}.part{part_idx:02d}.txt"
        publish_bytes(chunks_root, fname, line_bytes)
        chunk_files.append(f"chunks/{fname}")
        chunk_part_sha256s.append(part_sha)
        chunk_frame_sha256s.append(sha256_bytes(line_bytes))

    return {
        "safetensors_sha256": raw_sha256,
        "safetensors_size_bytes": len(raw_bytes),
        "compressed_sha256": compressed_sha256,
        "compressed_size_bytes": len(compressed),
        "chunk_count": total_parts,
        "chunk_files": chunk_files,
        "chunk_part_sha256s": chunk_part_sha256s,
        "chunk_frame_sha256s": chunk_frame_sha256s,
    }


def decode_and_reassemble_chunks(
    chunk_frame_payloads,
    expected_nonce,
    expected_adapter_id,
    expected_safetensors_sha256,
    max_decompressed_bytes=MAX_DECOMPRESSED_ADAPTER_BYTES,
):
    """Validate and reassemble `TLP_CHUNK_V1` ASCII frames back into exact `.safetensors` bytes."""
    check_nonce(expected_nonce)
    check_adapter_id(expected_adapter_id)
    if not isinstance(expected_safetensors_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_safetensors_sha256
    ):
        raise ValueError("invalid expected safetensors SHA256")
    if not chunk_frame_payloads:
        raise ValueError("empty chunk frame list")

    parts_by_index = {}
    declared_total = None

    for raw_frame in chunk_frame_payloads:
        if isinstance(raw_frame, str):
            raw_frame = raw_frame.encode("ascii")
        if not isinstance(raw_frame, (bytes, bytearray)) or len(raw_frame) > MAX_CHUNK_FRAME_BYTES:
            raise ValueError("chunk frame missing or exceeds 480 KB")
        lines = bytes(raw_frame).splitlines(keepends=True)
        if len(lines) != 1 or not lines[0].endswith(b"\n"):
            raise ValueError("chunk frame must be a single newline-terminated line")
        text = lines[0].rstrip(b"\r\n").decode("ascii")
        fields = text.split(":")
        if len(fields) != 7:
            raise ValueError("invalid TLP_CHUNK_V1 field count")
        prefix, nonce, adapter_id, part_idx_s, total_parts_s, part_sha, b64 = fields
        if prefix != CHUNK_FRAME_PREFIX:
            raise ValueError("unexpected chunk prefix")
        if nonce != expected_nonce:
            raise ValueError("chunk nonce mismatch")
        if adapter_id != expected_adapter_id:
            raise ValueError("chunk adapter_id mismatch")
        if not re.fullmatch(r"[0-9]{1,4}", part_idx_s) or not re.fullmatch(r"[1-9][0-9]{0,3}", total_parts_s):
            raise ValueError("invalid chunk index or total_parts")
        if not re.fullmatch(r"[0-9a-f]{64}", part_sha):
            raise ValueError("invalid chunk part SHA256 header")

        part_idx = int(part_idx_s)
        total_parts = int(total_parts_s)
        if not (0 <= part_idx < total_parts):
            raise ValueError("chunk part_idx out of bounds")
        if declared_total is None:
            declared_total = total_parts
        elif declared_total != total_parts:
            raise ValueError("inconsistent total_parts across chunks")
        if part_idx in parts_by_index:
            raise ValueError(f"duplicate chunk index {part_idx}")

        part_bytes = base64.b64decode(b64.encode("ascii"), validate=True)
        if not (0 < len(part_bytes) <= MAX_CHUNK_RAW_BYTES):
            raise ValueError("decoded chunk raw byte length out of bounds")
        if sha256_bytes(part_bytes) != part_sha:
            raise ValueError(f"chunk {part_idx} SHA256 mismatch")
        parts_by_index[part_idx] = part_bytes

    if declared_total is None or len(parts_by_index) != declared_total:
        raise ValueError(
            f"missing chunk parts: expected {declared_total}, got {sorted(parts_by_index.keys())}"
        )

    compressed = b"".join(parts_by_index[idx] for idx in range(declared_total))
    decompressor = zlib.decompressobj()
    decompressed = decompressor.decompress(compressed, max_decompressed_bytes + 1)
    decompressed += decompressor.flush()
    if decompressor.unconsumed_tail or len(decompressed) > max_decompressed_bytes:
        raise ValueError("decompressed adapter exceeds maximum allowed size")

    actual_sha = sha256_bytes(decompressed)
    if actual_sha != expected_safetensors_sha256:
        raise ValueError(
            f"reassembled safetensors SHA256 mismatch: expected {expected_safetensors_sha256}, got {actual_sha}"
        )
    return decompressed


def sanitize_diagnostic(exc):
    """Emit only bounded allowlisted exception class names and authored safe codes."""
    codes = []
    cur = exc
    for _ in range(4):
        if cur is None:
            break
        if isinstance(cur, WorkerInvariantError):
            codes.append(cur.safe_code)
        else:
            known = {
                "ImportError",
                "ModuleNotFoundError",
                "PackageNotFoundError",
                "ValueError",
                "TypeError",
                "KeyError",
                "RuntimeError",
                "PermissionError",
                "OSError",
                "TimeoutError",
                "TimeoutExpired",
                "CalledProcessError",
                "HTTPError",
                "HfHubHTTPError",
                "LocalEntryNotFoundError",
                "ReadTimeout",
                "ConnectTimeout",
                "ConnectionError",
                "OutOfMemoryError",
                "Error",
            }
            cls_name = type(cur).__name__
            codes.append(cls_name if cls_name in known else "other_exception")
            missing = getattr(cur, "name", None)
            if missing in (
                "torch",
                "transformers",
                "huggingface_hub",
                "tokenizers",
                "safetensors",
                "lora_adapter",
            ):
                codes.append("module." + missing)
            status = getattr(getattr(cur, "response", None), "status_code", None)
            if type(status) is int and 100 <= status <= 599:
                codes.append("http." + str(status))
            lowered = str(cur).lower()
            for token, label in (
                ("no space left", "disk_full"),
                ("out of memory", "oom"),
                ("cuda required", "no_cuda"),
                ("l4", "gpu_l4_check"),
                ("hash mismatch", "digest_mismatch"),
                ("sha256 mismatch", "digest_mismatch"),
                ("arm deadline", "arm_timeout"),
                ("worker deadline", "worker_timeout"),
            ):
                if token in lowered:
                    codes.append(label)
        cur = cur.__cause__ or cur.__context__
    deduped = []
    for item in codes:
        if item not in deduped:
            deduped.append(item)
    return ",".join(deduped)[:380]


def _install_packages_if_needed(workdir, skip_pip_install=False):
    if skip_pip_install:
        return
    needed = []
    try:
        tf_ver = md.version("transformers")
    except md.PackageNotFoundError:
        tf_ver = None
    if tf_ver != TRANSFORMERS_PIN:
        needed.append(f"transformers=={TRANSFORMERS_PIN}")

    try:
        md.version("safetensors")
    except md.PackageNotFoundError:
        needed.append("safetensors")

    if needed:
        torch_version = md.version("torch")
        constraint = Path(workdir) / "keep_torch.txt"
        publish_text(workdir, "keep_torch.txt", f"torch=={torch_version}")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "--no-input",
                "--progress-bar",
                "off",
                "--constraint",
                str(constraint),
                *needed,
            ],
            check=True,
            timeout=180,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if md.version("torch") != torch_version or md.version("transformers") != TRANSFORMERS_PIN:
            raise WorkerInvariantError("package_versions_differ")
        md.version("safetensors")

    # Best-effort pure-Python Triton wheel install for Qwen3.5 GatedDeltaNet speedup.
    # Never upgrades torch/triton and never compiles C++; verified via CUDA smoke check before import.
    if not _pkg_present("fla-core") or not _pkg_present("flash-linear-attention"):
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--progress-bar",
                    "off",
                    "--only-binary=:all:",
                    "--no-deps",
                    "fla-core==0.6.0",
                    "flash-linear-attention==0.6.0",
                    "einops",
                ],
                check=False,
                timeout=60,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def _probe_fla_triton_kernel(device):
    """Smoke-test `fla.ops.gated_delta_rule.chunk_gated_delta_rule` forward+backward on CUDA.

    If incompatible with the VM's Triton/PyTorch build, block `fla` via `sys.modules['fla'] = None`
    so `transformers` cleanly falls back to its exact reference PyTorch implementation.
    """
    import torch

    if not torch.cuda.is_available():
        return False
    try:
        from fla.ops.gated_delta_rule import chunk_gated_delta_rule

        q = torch.randn(1, 64, 4, 128, device=device, dtype=torch.bfloat16, requires_grad=True)
        k = torch.randn(1, 64, 4, 128, device=device, dtype=torch.bfloat16, requires_grad=True)
        v = torch.randn(1, 64, 4, 128, device=device, dtype=torch.bfloat16, requires_grad=True)
        g = -torch.rand(1, 64, 4, device=device, dtype=torch.float32, requires_grad=True)
        beta = torch.sigmoid(torch.randn(1, 64, 4, device=device, dtype=torch.bfloat16)).requires_grad_(True)
        out, _ = chunk_gated_delta_rule(q, k, v, g, beta, use_qk_l2norm_in_kernel=True)
        loss = out.float().mean()
        loss.backward()
        torch.cuda.synchronize()
        if not torch.isfinite(loss).item() or q.grad is None or not torch.isfinite(q.grad).all().item():
            raise RuntimeError("non_finite_fla_smoke")
        del q, k, v, g, beta, out, loss
        torch.cuda.empty_cache()
        return True
    except Exception:
        for mod_name in list(sys.modules.keys()):
            if mod_name == "fla" or mod_name.startswith("fla."):
                sys.modules.pop(mod_name, None)
        sys.modules["fla"] = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return False


def _normalize_expected_model_files(raw_expected):
    if raw_expected is None:
        return dict(PINNED_MODEL_FILES)
    if not isinstance(raw_expected, dict) or set(raw_expected.keys()) != set(PINNED_MODEL_FILES.keys()):
        raise WorkerInvariantError("invalid_expected_model_files_inventory")
    normalized = {}
    for fname, pinned in PINNED_MODEL_FILES.items():
        entry = raw_expected[fname]
        if isinstance(entry, str):
            sha_val = entry
            size_val = pinned["size_bytes"]
        elif isinstance(entry, dict):
            sha_val = entry.get("sha256")
            size_val = entry.get("size_bytes")
        else:
            raise WorkerInvariantError("invalid_expected_model_file_entry")
        if sha_val != pinned["sha256"] or int(size_val) != pinned["size_bytes"]:
            raise WorkerInvariantError("expected_model_files_pin_mismatch")
        normalized[fname] = {"sha256": sha_val, "size_bytes": int(size_val)}
    return normalized


def verify_model_snapshot_files(model_dir, expected_files):
    verified = {}
    base = Path(model_dir)
    for fname, spec in sorted(expected_files.items()):
        fpath = base / fname
        if not fpath.is_file() or fpath.is_symlink():
            raise WorkerInvariantError("missing_model_snapshot_file")
        actual_size = int(fpath.stat().st_size)
        if actual_size != int(spec["size_bytes"]):
            raise WorkerInvariantError("model_snapshot_size_mismatch")
        actual_sha = sha256_file(fpath)
        if actual_sha != spec["sha256"]:
            raise WorkerInvariantError("model_snapshot_sha256_mismatch")
        verified[fname] = {"sha256": actual_sha, "size_bytes": actual_size}
    return verified


def _verify_checkpoint_loading(info):
    if any(info.get(k) for k in ("missing_keys", "mismatched_keys", "error_msgs")):
        raise WorkerInvariantError("incomplete_checkpoint_load")
    for key in info.get("unexpected_keys", []):
        if not key.startswith(
            ("visual.", "model.visual.", "vision_tower.", "model.vision_tower.", "mtp.", "model.mtp.")
        ):
            raise WorkerInvariantError("unexpected_nonvision_checkpoint_keys")


def _resolve_eos_ids(tokenizer, model):
    eos_ids = set()
    tok_eos = getattr(tokenizer, "eos_token_id", None)
    if isinstance(tok_eos, list):
        eos_ids.update(int(x) for x in tok_eos if x is not None)
    elif tok_eos is not None:
        eos_ids.add(int(tok_eos))
    gen_cfg = getattr(model, "generation_config", None)
    mod_eos = getattr(gen_cfg, "eos_token_id", None)
    if isinstance(mod_eos, list):
        eos_ids.update(int(x) for x in mod_eos if x is not None)
    elif mod_eos is not None:
        eos_ids.add(int(mod_eos))
    return sorted(eos_ids)


def _run_greedy_public_probe(model, tokenizer, probe_cfg, device, eos_ids):
    import torch

    messages = probe_cfg["messages"]
    tools = probe_cfg["tools"]
    max_new_tokens = int(probe_cfg.get("max_new_tokens", 64))
    seed = int(probe_cfg.get("seed", 0))

    prompt_text = tokenizer.apply_chat_template(
        messages,
        tools=tools,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    encoded = tokenizer(prompt_text, add_special_tokens=False)
    prompt_ids = [int(t) for t in encoded["input_ids"]]
    if not prompt_ids or len(prompt_ids) > 4096:
        raise WorkerInvariantError("unexpected_probe_prompt_length")

    model.eval()
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
        torch.cuda.synchronize()
    t0 = time.monotonic()

    input_tensor = torch.tensor([prompt_ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        if hasattr(model, "generate") and getattr(model, "generation_config", None) is not None:
            from transformers import GenerationConfig

            pad_id = getattr(tokenizer, "pad_token_id", None)
            if pad_id is None and eos_ids:
                pad_id = eos_ids[0]
            gen_cfg = GenerationConfig(
                do_sample=False,
                num_beams=1,
                max_new_tokens=max_new_tokens,
                use_cache=True,
                eos_token_id=eos_ids,
                pad_token_id=pad_id,
            )
            out = model.generate(input_ids=input_tensor, generation_config=gen_cfg)
            generated_ids = [int(t) for t in out[0, len(prompt_ids) :].tolist()]
        else:
            cur_ids = list(prompt_ids)
            generated_ids = []
            eos_set = set(eos_ids)
            for _ in range(max_new_tokens):
                cur_t = torch.tensor([cur_ids], dtype=torch.long, device=device)
                logits = model(input_ids=cur_t, use_cache=False).logits
                next_id = int(logits[0, -1].float().argmax(dim=-1).item())
                generated_ids.append(next_id)
                cur_ids.append(next_id)
                if next_id in eos_set:
                    break

    if torch.cuda.is_available():
        torch.cuda.synchronize()
    elapsed = time.monotonic() - t0
    if not (1 <= len(generated_ids) <= max_new_tokens):
        raise WorkerInvariantError("unexpected_probe_output_tokens")

    eos_set = set(eos_ids)
    limit_reached = len(generated_ids) == max_new_tokens and generated_ids[-1] not in eos_set
    raw_text = tokenizer.decode(generated_ids, skip_special_tokens=False)
    return {
        "probe_id": probe_cfg.get("probe_id", "public_synthetic_orders_probe_v1"),
        "seed": seed,
        "max_new_tokens": max_new_tokens,
        "prompt_sha256": sha256_bytes(prompt_text.encode("utf-8")),
        "input_tokens": len(prompt_ids),
        "generated_token_ids": generated_ids,
        "output_tokens": len(generated_ids),
        "raw_text": raw_text,
        "token_limit_reached": bool(limit_reached),
        "generation_seconds": round(elapsed, 4),
    }


def _set_gradient_checkpointing(model, enabled):
    """Enable/disable non-reentrant activation checkpointing on HF CausalLM models."""
    if enabled:
        if hasattr(model, "activation_checkpointing_enable"):
            model.activation_checkpointing_enable(
                activation_checkpointing_kwargs={"use_reentrant": False}
            )
            return True
        if hasattr(model, "gradient_checkpointing_enable"):
            model.gradient_checkpointing_enable(
                gradient_checkpointing_kwargs={"use_reentrant": False}
            )
            return True
        return False
    if hasattr(model, "activation_checkpointing_disable"):
        model.activation_checkpointing_disable()
        return False
    if hasattr(model, "gradient_checkpointing_disable"):
        model.gradient_checkpointing_disable()
        return False
    return False


def _supports_backbone_hidden_slice(model):
    import torch

    backbone = getattr(model, "model", None)
    return (
        backbone is not None
        and type(backbone) is not torch.nn.Module
        and callable(backbone)
        and hasattr(model, "lm_head")
        and callable(model.lm_head)
    )


def _forward_right_padded_sequences(model, seq_list, device, pad_id=0):
    """Run causal forward pass on 1..B variable-length token ID lists with exact right-padding."""
    import torch

    if len(seq_list) == 1:
        ids_t = torch.tensor([seq_list[0]], dtype=torch.long, device=device)
        logits = model(input_ids=ids_t, use_cache=False).logits
        return [(ids_t, logits)]

    bsz = len(seq_list)
    max_len = max(len(s) for s in seq_list)
    pad_ids = torch.full((bsz, max_len), int(pad_id), dtype=torch.long, device=device)
    attn_mask = torch.zeros((bsz, max_len), dtype=torch.long, device=device)
    per_seq_ids = []
    for idx, seq in enumerate(seq_list):
        slen = len(seq)
        seq_t = torch.tensor(seq, dtype=torch.long, device=device)
        pad_ids[idx, :slen] = seq_t
        attn_mask[idx, :slen] = 1
        per_seq_ids.append(seq_t.unsqueeze(0))

    batch_logits = model(input_ids=pad_ids, attention_mask=attn_mask, use_cache=False).logits
    sliced = [(per_seq_ids[idx], batch_logits[idx : idx + 1, : len(seq_list[idx])]) for idx in range(bsz)]
    return sliced


def _forward_completion_windows(model, seq_list, prompt_lens, device, pad_id=0):
    """Run causal forward pass and project ONLY `[prompt_len - 1 : slen]` through `lm_head`.

    Because `lm_head` is position-wise linear and all prompt tokens `< prompt_len - 1`
    carry zero loss/gradient, projecting `hidden[:, plen - 1 : slen]` with `eff_prompt_len = 1`
    is bit-for-bit identical to full-sequence `logits` while avoiding `(B, L, 248320)` allocations.
    """
    import torch

    bsz = len(seq_list)
    use_hidden_slice = _supports_backbone_hidden_slice(model)
    if bsz == 1:
        seq = seq_list[0]
        plen = int(prompt_lens[0])
        slen = len(seq)
        ids_t = torch.tensor([seq], dtype=torch.long, device=device)
        if use_hidden_slice:
            hidden = model.model(input_ids=ids_t, use_cache=False).last_hidden_state
            win_logits = model.lm_head(hidden[:, plen - 1 : slen])
            return [(ids_t[:, plen - 1 : slen], win_logits, 1)]
        logits = model(input_ids=ids_t, use_cache=False).logits
        return [(ids_t, logits, plen)]

    max_len = max(len(s) for s in seq_list)
    pad_ids = torch.full((bsz, max_len), int(pad_id), dtype=torch.long, device=device)
    attn_mask = torch.zeros((bsz, max_len), dtype=torch.long, device=device)
    per_seq_ids = []
    for idx, seq in enumerate(seq_list):
        slen = len(seq)
        seq_t = torch.tensor(seq, dtype=torch.long, device=device)
        pad_ids[idx, :slen] = seq_t
        attn_mask[idx, :slen] = 1
        per_seq_ids.append(seq_t.unsqueeze(0))

    if use_hidden_slice:
        hidden = model.model(input_ids=pad_ids, attention_mask=attn_mask, use_cache=False).last_hidden_state
        results = []
        for idx, seq in enumerate(seq_list):
            slen = len(seq)
            plen = int(prompt_lens[idx])
            win_logits = model.lm_head(hidden[idx : idx + 1, plen - 1 : slen])
            results.append((per_seq_ids[idx][:, plen - 1 : slen], win_logits, 1))
        return results

    batch_logits = model(input_ids=pad_ids, attention_mask=attn_mask, use_cache=False).logits
    return [
        (per_seq_ids[idx], batch_logits[idx : idx + 1, : len(seq_list[idx])], int(prompt_lens[idx]))
        for idx in range(bsz)
    ]


def _pretokenize_and_precompute_references(
    model,
    tokenizer,
    lora_adapter,
    datasets,
    training_jobs,
    device,
    eval_batch_sequences=8,
):
    """Deduplicate tokenization & frozen-base reference log-probs across all jobs."""
    import torch

    needed_dataset_names = []
    for job in training_jobs:
        ds_name = job["dataset"]
        if ds_name not in datasets:
            raise WorkerInvariantError("missing_job_dataset_in_bundle")
        if ds_name not in needed_dataset_names:
            needed_dataset_names.append(ds_name)
    if "b4_single_token_ftpo_style.jsonl" in datasets and (
        "b4_single_token_ftpo_style.jsonl" not in needed_dataset_names
    ):
        needed_dataset_names.append("b4_single_token_ftpo_style.jsonl")

    prompt_cache = {}
    seq_cache = {}
    prepared_datasets = {}
    b4_divergence_requests = {}
    b4_verified_count = 0

    def get_prompt_entry(messages, tools):
        key = json.dumps({"messages": messages, "tools": tools}, sort_keys=True, ensure_ascii=False)
        entry = prompt_cache.get(key)
        if entry is None:
            prompt_text = tokenizer.apply_chat_template(
                messages,
                tools=tools,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            prompt_ids = [int(x) for x in tokenizer(prompt_text, add_special_tokens=False)["input_ids"]]
            if not prompt_ids:
                raise WorkerInvariantError("empty_prompt_tokens")
            entry = {
                "prompt_key": key,
                "prompt_text": prompt_text,
                "prompt_ids": prompt_ids,
                "prompt_len": len(prompt_ids),
            }
            prompt_cache[key] = entry
        return entry

    def get_seq_entry(p_entry, completion_text):
        s_key = (p_entry["prompt_key"], completion_text)
        s_entry = seq_cache.get(s_key)
        if s_entry is None:
            full_ids = [
                int(x)
                for x in tokenizer(p_entry["prompt_text"] + completion_text, add_special_tokens=False)[
                    "input_ids"
                ]
            ]
            p_len = p_entry["prompt_len"]
            if full_ids[:p_len] != p_entry["prompt_ids"]:
                raise WorkerInvariantError("bpe_prompt_prefix_merge_mismatch")
            comp_ids = full_ids[p_len:]
            if not comp_ids:
                raise WorkerInvariantError("empty_completion_tokens")
            s_entry = {
                "seq_key": s_key,
                "prompt_len": p_len,
                "full_ids": full_ids,
                "completion_ids": comp_ids,
                "completion_len": len(comp_ids),
                "ref_sum_logprob": None,
                "ref_mean_logprob": None,
                "ref_mean_nll": None,
            }
            seq_cache[s_key] = s_entry
        return s_entry

    for ds_name in needed_dataset_names:
        rows = datasets[ds_name]
        if not isinstance(rows, list) or not rows:
            raise WorkerInvariantError("empty_training_dataset")
        prepared_rows = []
        is_b4 = ds_name == "b4_single_token_ftpo_style.jsonl"
        for row in rows:
            p_entry = get_prompt_entry(row["messages"], row["tools"])
            c_entry = get_seq_entry(p_entry, row["chosen_text"])
            r_entry = None
            if "rejected_text" in row and row["rejected_text"] is not None:
                r_entry = get_seq_entry(p_entry, row["rejected_text"])

            item = {
                "example_id": row.get("example_id", ""),
                "prompt_len": p_entry["prompt_len"],
                "chosen_seq_key": c_entry["seq_key"],
                "rejected_seq_key": r_entry["seq_key"] if r_entry is not None else None,
            }

            if is_b4 or row.get("arm") == "B4" or "divergence_chosen_token_id" in row:
                if r_entry is None:
                    raise WorkerInvariantError("b4_missing_rejected_text")
                c_ids = c_entry["completion_ids"]
                r_ids = r_entry["completion_ids"]
                limit = min(len(c_ids), len(r_ids))
                actual_offset = None
                for idx in range(limit):
                    if c_ids[idx] != r_ids[idx]:
                        actual_offset = idx
                        break
                if actual_offset is None:
                    raise WorkerInvariantError("b4_no_interior_divergence")
                if actual_offset != int(row["first_divergence_offset"]):
                    raise WorkerInvariantError("b4_divergence_offset_mismatch")
                if "divergence_chosen_token_id" in row and c_ids[actual_offset] != int(
                    row["divergence_chosen_token_id"]
                ):
                    raise WorkerInvariantError("b4_divergence_chosen_id_mismatch")
                if "divergence_rejected_token_id" in row and r_ids[actual_offset] != int(
                    row["divergence_rejected_token_id"]
                ):
                    raise WorkerInvariantError("b4_divergence_rejected_id_mismatch")
                if (
                    row["chosen_text"].startswith("<tool_call>")
                    and row["rejected_text"].startswith("<tool_call>")
                    and actual_offset == 0
                ):
                    raise WorkerInvariantError("b4_tool_call_prefix_suppressed")

                div_spec = (actual_offset, int(c_ids[actual_offset]), int(r_ids[actual_offset]))
                item["div_offset"] = div_spec[0]
                item["div_chosen_id"] = div_spec[1]
                item["div_rejected_id"] = div_spec[2]
                b4_divergence_requests.setdefault(c_entry["seq_key"], set()).add(div_spec)
                if is_b4:
                    b4_verified_count += 1

            prepared_rows.append(item)
        prepared_datasets[ds_name] = prepared_rows

    lora_adapter.set_lora_enabled(model, False)
    _set_gradient_checkpointing(model, False)
    model.eval()
    div_ref_cache = {}
    pad_id = int(getattr(tokenizer, "pad_token_id", None) or 0)
    ordered_seq_items = sorted(seq_cache.items(), key=lambda kv: len(kv[1]["full_ids"]))
    batch_sz = max(1, int(eval_batch_sequences))

    t0 = time.monotonic()
    with torch.inference_mode():
        idx_cursor = 0
        while idx_cursor < len(ordered_seq_items):
            chunk_items = ordered_seq_items[idx_cursor : idx_cursor + batch_sz]
            try:
                outputs = _forward_completion_windows(
                    model,
                    [it[1]["full_ids"] for it in chunk_items],
                    [it[1]["prompt_len"] for it in chunk_items],
                    device,
                    pad_id=pad_id,
                )
            except torch.OutOfMemoryError:
                if batch_sz <= 1:
                    raise
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                batch_sz = max(1, batch_sz // 2)
                continue
            for (s_key, s_entry), (ids_t, logits, eff_plen) in zip(chunk_items, outputs):
                stats = lora_adapter.sequence_completion_stats(logits, ids_t, eff_plen)
                s_entry["ref_sum_logprob"] = float(stats["sum_logprob"].detach().cpu().item())
                s_entry["ref_mean_logprob"] = float(stats["mean_logprob"].detach().cpu().item())
                s_entry["ref_mean_nll"] = float(stats["mean_nll"].detach().cpu().item())
                for div_spec in b4_divergence_requests.get(s_key, ()):
                    div_offset, div_c_id, div_r_id = div_spec
                    c_lp, r_lp, c_nll = lora_adapter.single_token_divergence_logprobs(
                        logits, eff_plen, div_offset, div_c_id, div_r_id
                    )
                    div_ref_cache[(s_key, div_spec)] = (
                        float(c_lp.detach().cpu().item()),
                        float(r_lp.detach().cpu().item()),
                        float(c_nll.detach().cpu().item()),
                    )
            del outputs
            idx_cursor += len(chunk_items)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    ref_elapsed = time.monotonic() - t0

    return {
        "seq_cache": seq_cache,
        "div_ref_cache": div_ref_cache,
        "prepared_datasets": prepared_datasets,
        "unique_prompt_count": len(prompt_cache),
        "unique_sequence_count": len(seq_cache),
        "b4_divergence_verified_count": b4_verified_count,
        "reference_precompute_seconds": round(ref_elapsed, 4),
    }


def _compute_step0_exact_loss(job, examples, seq_cache, div_ref_cache):
    objective = job["objective"]
    if objective == "sft":
        vals = [seq_cache[ex["chosen_seq_key"]]["ref_mean_nll"] for ex in examples]
        return float(sum(vals) / len(vals))
    if objective == "single_token_dpo":
        sft_anchor = float(job.get("sft_anchor", 0.1))
        dpo_0 = math.log(2.0)
        vals = []
        for ex in examples:
            div_spec = (ex["div_offset"], ex["div_chosen_id"], ex["div_rejected_id"])
            _, _, ref_c_nll = div_ref_cache[(ex["chosen_seq_key"], div_spec)]
            vals.append(dpo_0 + sft_anchor * ref_c_nll)
        return float(sum(vals) / len(vals))
    if objective == "dpo":
        sft_anchor = float(job.get("sft_anchor", 0.2))
        dpo_0 = math.log(2.0)
        vals = [dpo_0 + sft_anchor * seq_cache[ex["chosen_seq_key"]]["ref_mean_nll"] for ex in examples]
        return float(sum(vals) / len(vals))
    raise WorkerInvariantError("unknown_training_objective")


def _evaluate_trained_adapter_on_dataset(
    model,
    tokenizer,
    lora_adapter,
    job,
    examples,
    seq_cache,
    div_ref_cache,
    device,
    eval_batch_sequences=8,
):
    import torch

    objective = job["objective"]
    _set_gradient_checkpointing(model, False)
    lora_adapter.set_lora_enabled(model, True)
    model.eval()
    pad_id = int(getattr(tokenizer, "pad_token_id", None) or 0)
    batch_sz = max(1, int(eval_batch_sequences))

    losses = []
    dpo_losses = []
    nll_losses = []
    margins = []
    prefer_count = 0

    needed_keys = {}
    div_reqs = {}
    for ex in examples:
        ck = ex["chosen_seq_key"]
        needed_keys[ck] = seq_cache[ck]
        if objective == "dpo":
            rk = ex["rejected_seq_key"]
            needed_keys[rk] = seq_cache[rk]
        elif objective == "single_token_dpo":
            div_spec = (ex["div_offset"], ex["div_chosen_id"], ex["div_rejected_id"])
            div_reqs.setdefault(ck, set()).add(div_spec)

    ordered_eval_items = sorted(needed_keys.items(), key=lambda kv: len(kv[1]["full_ids"]))
    pol_seq_stats = {}
    pol_div_stats = {}

    with torch.inference_mode():
        idx_cursor = 0
        while idx_cursor < len(ordered_eval_items):
            sub_items = ordered_eval_items[idx_cursor : idx_cursor + batch_sz]
            try:
                outputs = _forward_completion_windows(
                    model,
                    [it[1]["full_ids"] for it in sub_items],
                    [it[1]["prompt_len"] for it in sub_items],
                    device,
                    pad_id=pad_id,
                )
            except torch.OutOfMemoryError:
                if batch_sz <= 1:
                    raise
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                batch_sz = max(1, batch_sz // 2)
                continue
            for (s_key, _), (ids_t, logits, eff_plen) in zip(sub_items, outputs):
                st = lora_adapter.sequence_completion_stats(logits, ids_t, eff_plen)
                pol_seq_stats[s_key] = (
                    float(st["sum_logprob"].detach().cpu().item()),
                    float(st["mean_nll"].detach().cpu().item()),
                )
                for div_spec in div_reqs.get(s_key, ()):
                    div_offset, div_c_id, div_r_id = div_spec
                    c_lp, r_lp, c_nll = lora_adapter.single_token_divergence_logprobs(
                        logits, eff_plen, div_offset, div_c_id, div_r_id
                    )
                    pol_div_stats[(s_key, div_spec)] = (
                        float(c_lp.detach().cpu().item()),
                        float(r_lp.detach().cpu().item()),
                        float(c_nll.detach().cpu().item()),
                    )
            del outputs
            idx_cursor += len(sub_items)

        if objective == "sft":
            for ex in examples:
                _, nll_val = pol_seq_stats[ex["chosen_seq_key"]]
                losses.append(nll_val)
                nll_losses.append(nll_val)
        elif objective == "single_token_dpo":
            beta = float(job.get("beta", 0.2))
            sft_anchor = float(job.get("sft_anchor", 0.1))
            for ex in examples:
                div_spec = (ex["div_offset"], ex["div_chosen_id"], ex["div_rejected_id"])
                ref_c_lp, ref_r_lp, _ = div_ref_cache[(ex["chosen_seq_key"], div_spec)]
                pol_c_lp, pol_r_lp, pol_c_nll = pol_div_stats[(ex["chosen_seq_key"], div_spec)]
                out = lora_adapter.dpo_step_loss(
                    torch.tensor(pol_c_lp, dtype=torch.float32),
                    torch.tensor(pol_r_lp, dtype=torch.float32),
                    ref_c_lp,
                    ref_r_lp,
                    torch.tensor(pol_c_nll, dtype=torch.float32),
                    beta=beta,
                    sft_anchor=sft_anchor,
                )
                losses.append(float(out["loss"].item()))
                dpo_losses.append(float(out["dpo_loss"].item()))
                nll_losses.append(float(out["nll_loss"].item()))
                margins.append(float(out["reward_margin"].item()))
                if out["prefer_chosen"]:
                    prefer_count += 1
        elif objective == "dpo":
            beta = float(job.get("beta", 0.1))
            sft_anchor = float(job.get("sft_anchor", 0.2))
            for ex in examples:
                c_entry = seq_cache[ex["chosen_seq_key"]]
                r_entry = seq_cache[ex["rejected_seq_key"]]
                pol_c_sum, pol_c_nll = pol_seq_stats[ex["chosen_seq_key"]]
                pol_r_sum, _ = pol_seq_stats[ex["rejected_seq_key"]]
                out = lora_adapter.dpo_step_loss(
                    torch.tensor(pol_c_sum, dtype=torch.float32),
                    torch.tensor(pol_r_sum, dtype=torch.float32),
                    c_entry["ref_sum_logprob"],
                    r_entry["ref_sum_logprob"],
                    torch.tensor(pol_c_nll, dtype=torch.float32),
                    beta=beta,
                    sft_anchor=sft_anchor,
                )
                losses.append(float(out["loss"].item()))
                dpo_losses.append(float(out["dpo_loss"].item()))
                nll_losses.append(float(out["nll_loss"].item()))
                margins.append(float(out["reward_margin"].item()))
                if out["prefer_chosen"]:
                    prefer_count += 1
        else:
            raise WorkerInvariantError("unknown_training_objective")

    n = len(examples)
    summary = {
        "final_loss": round(sum(losses) / n, 6),
        "final_nll_loss": round(sum(nll_losses) / n, 6),
        "final_dpo_loss": round(sum(dpo_losses) / n, 6) if dpo_losses else None,
        "post_train_reward_accuracy": round(prefer_count / n, 6) if objective != "sft" else None,
        "post_train_prefer_chosen_count": int(prefer_count) if objective != "sft" else None,
        "post_train_mean_reward_margin": round(sum(margins) / n, 6) if margins else None,
    }
    return summary


def _train_single_job(
    model,
    tokenizer,
    lora_adapter,
    job,
    precomp,
    probe_cfg,
    eos_ids,
    adapters_dir,
    device,
    bundle_sha256,
    dataset_sha256,
    micro_batch_sequences=4,
    eval_batch_sequences=8,
):
    import torch

    adapter_id = check_adapter_id(job["adapter_id"])
    objective = job["objective"]
    if objective not in ("sft", "dpo", "single_token_dpo"):
        raise WorkerInvariantError("unknown_training_objective")

    job_seed = int(job["seed"])
    lr = float(job["lr"])
    weight_decay = float(job.get("weight_decay", 0.01))
    epochs = int(job.get("epochs", 2))
    grad_accum = int(job.get("grad_accum", 16))
    max_grad_norm = float(job.get("max_grad_norm", 1.0))
    pad_id = int(getattr(tokenizer, "pad_token_id", None) or 0)
    micro_seqs = max(1, int(micro_batch_sequences))

    seq_cache = precomp["seq_cache"]
    div_ref_cache = precomp["div_ref_cache"]
    examples = precomp["prepared_datasets"][job["dataset"]]

    torch.manual_seed(job_seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(job_seed)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    lora_adapter.reset_lora_parameters(model, job_seed)
    lora_adapter.set_lora_enabled(model, True)
    params = lora_adapter.trainable_lora_parameters(model)
    if not params:
        raise WorkerInvariantError("empty_trainable_lora_parameters")

    optimizer = torch.optim.AdamW(params, lr=lr, weight_decay=weight_decay)

    initial_loss = _compute_step0_exact_loss(job, examples, seq_cache, div_ref_cache)
    epoch_mean_losses = []
    total_train_tokens = 0
    total_completion_tokens = 0
    optimizer_steps = 0
    max_grad_norm_observed = 0.0

    if torch.cuda.is_available():
        torch.cuda.synchronize()
    t0 = time.monotonic()

    grad_ckpt_enabled = _set_gradient_checkpointing(model, True)
    model.train()
    for epoch in range(epochs):
        order = list(range(len(examples)))
        random.Random(job_seed + epoch).shuffle(order)
        optimizer.zero_grad(set_to_none=True)
        epoch_losses = []

        for start_idx in range(0, len(order), grad_accum):
            batch_indices = order[start_idx : start_idx + grad_accum]
            accum_denom = float(len(batch_indices))

            if objective in ("sft", "single_token_dpo"):
                m_cursor = 0
                while m_cursor < len(batch_indices):
                    ex_step = max(1, micro_seqs)
                    sub_idxs = batch_indices[m_cursor : m_cursor + ex_step]
                    sub_exs = [examples[i] for i in sub_idxs]
                    seqs = [seq_cache[ex["chosen_seq_key"]]["full_ids"] for ex in sub_exs]
                    plens = [ex["prompt_len"] for ex in sub_exs]
                    try:
                        outputs = _forward_completion_windows(
                            model, seqs, plens, device, pad_id=pad_id
                        )
                        micro_losses = []
                        sub_vals = []
                        sub_tok = 0
                        sub_comp = 0
                        for ex, (c_ids_t, c_logits, eff_plen) in zip(sub_exs, outputs):
                            c_entry = seq_cache[ex["chosen_seq_key"]]
                            sub_tok += len(c_entry["full_ids"])
                            sub_comp += c_entry["completion_len"]
                            if objective == "sft":
                                c_stats = lora_adapter.sequence_completion_stats(
                                    c_logits, c_ids_t, eff_plen
                                )
                                step_loss = c_stats["mean_nll"]
                            else:
                                beta = float(job.get("beta", 0.2))
                                sft_anchor = float(job.get("sft_anchor", 0.1))
                                div_spec = (ex["div_offset"], ex["div_chosen_id"], ex["div_rejected_id"])
                                ref_c_lp, ref_r_lp, _ = div_ref_cache[(ex["chosen_seq_key"], div_spec)]
                                pol_c_lp, pol_r_lp, pol_c_nll = lora_adapter.single_token_divergence_logprobs(
                                    c_logits,
                                    eff_plen,
                                    ex["div_offset"],
                                    ex["div_chosen_id"],
                                    ex["div_rejected_id"],
                                )
                                step_loss = lora_adapter.dpo_step_loss(
                                    pol_c_lp,
                                    pol_r_lp,
                                    ref_c_lp,
                                    ref_r_lp,
                                    pol_c_nll,
                                    beta=beta,
                                    sft_anchor=sft_anchor,
                                )["loss"]
                            loss_val = float(step_loss.detach().cpu().item())
                            if not math.isfinite(loss_val):
                                raise WorkerInvariantError("non_finite_training_loss")
                            sub_vals.append(loss_val)
                            micro_losses.append(step_loss)
                        del outputs
                        (sum(micro_losses) / accum_denom).backward()
                        del micro_losses
                    except torch.OutOfMemoryError:
                        if ex_step <= 1:
                            raise
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                        micro_seqs = max(1, ex_step // 2)
                        continue
                    epoch_losses.extend(sub_vals)
                    total_train_tokens += sub_tok
                    total_completion_tokens += sub_comp
                    m_cursor += len(sub_idxs)
            else:
                beta = float(job.get("beta", 0.1))
                sft_anchor = float(job.get("sft_anchor", 0.2))
                m_cursor = 0
                while m_cursor < len(batch_indices):
                    pair_step = max(1, micro_seqs // 2)
                    sub_idxs = batch_indices[m_cursor : m_cursor + pair_step]
                    sub_exs = [examples[i] for i in sub_idxs]
                    seqs = []
                    plens = []
                    sub_tok = 0
                    sub_comp = 0
                    for ex in sub_exs:
                        c_entry = seq_cache[ex["chosen_seq_key"]]
                        r_entry = seq_cache[ex["rejected_seq_key"]]
                        seqs.append(c_entry["full_ids"])
                        plens.append(ex["prompt_len"])
                        seqs.append(r_entry["full_ids"])
                        plens.append(ex["prompt_len"])
                        sub_tok += len(c_entry["full_ids"]) + len(r_entry["full_ids"])
                        sub_comp += c_entry["completion_len"] + r_entry["completion_len"]
                    try:
                        outputs = _forward_completion_windows(
                            model, seqs, plens, device, pad_id=pad_id
                        )
                        micro_losses = []
                        sub_vals = []
                        for idx_in_sub, ex in enumerate(sub_exs):
                            c_entry = seq_cache[ex["chosen_seq_key"]]
                            r_entry = seq_cache[ex["rejected_seq_key"]]
                            c_ids_t, c_logits, c_eff_plen = outputs[2 * idx_in_sub]
                            r_ids_t, r_logits, r_eff_plen = outputs[2 * idx_in_sub + 1]
                            c_stats = lora_adapter.sequence_completion_stats(
                                c_logits, c_ids_t, c_eff_plen
                            )
                            r_stats = lora_adapter.sequence_completion_stats(
                                r_logits, r_ids_t, r_eff_plen
                            )
                            step_loss = lora_adapter.dpo_step_loss(
                                c_stats["sum_logprob"],
                                r_stats["sum_logprob"],
                                c_entry["ref_sum_logprob"],
                                r_entry["ref_sum_logprob"],
                                c_stats["mean_nll"],
                                beta=beta,
                                sft_anchor=sft_anchor,
                            )["loss"]
                            loss_val = float(step_loss.detach().cpu().item())
                            if not math.isfinite(loss_val):
                                raise WorkerInvariantError("non_finite_training_loss")
                            sub_vals.append(loss_val)
                            micro_losses.append(step_loss)
                        del outputs
                        (sum(micro_losses) / accum_denom).backward()
                        del micro_losses
                    except torch.OutOfMemoryError:
                        if pair_step <= 1:
                            raise
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                        micro_seqs = max(2, pair_step)
                        if pair_step == 2:
                            micro_seqs = 2
                        else:
                            micro_seqs = max(2, (pair_step // 2) * 2)
                        continue
                    epoch_losses.extend(sub_vals)
                    total_train_tokens += sub_tok
                    total_completion_tokens += sub_comp
                    m_cursor += len(sub_idxs)

            grad_norm = float(torch.nn.utils.clip_grad_norm_(params, max_grad_norm).detach().cpu().item())
            if not math.isfinite(grad_norm):
                raise WorkerInvariantError("non_finite_grad_norm")
            if grad_norm > max_grad_norm_observed:
                max_grad_norm_observed = grad_norm
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            optimizer_steps += 1

        epoch_mean_losses.append(round(sum(epoch_losses) / len(epoch_losses), 6))

    _set_gradient_checkpointing(model, False)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    train_seconds = time.monotonic() - t0

    eval_t0 = time.monotonic()
    post_eval = _evaluate_trained_adapter_on_dataset(
        model,
        tokenizer,
        lora_adapter,
        job,
        examples,
        seq_cache,
        div_ref_cache,
        device,
        eval_batch_sequences=eval_batch_sequences,
    )
    probe_result = _run_greedy_public_probe(model, tokenizer, probe_cfg, device, eos_ids)
    eval_seconds = time.monotonic() - eval_t0

    max_vram_bytes = int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else 0

    safetensors_path = Path(adapters_dir) / f"{adapter_id}.safetensors"
    save_info = lora_adapter.save_lora_safetensors(
        model,
        safetensors_path,
        extra_metadata={
            "adapter_id": adapter_id,
            "arm": job["arm"],
            "objective": objective,
            "dataset": job["dataset"],
            "dataset_sha256": dataset_sha256,
            "bundle_sha256": bundle_sha256,
            "model_id": MODEL_ID,
            "revision": REVISION,
            "seed": str(job_seed),
            "epochs": str(epochs),
            "grad_accum": str(grad_accum),
            "lr": str(lr),
            "beta": str(job["beta"]) if "beta" in job else None,
            "sft_anchor": str(job["sft_anchor"]) if "sft_anchor" in job else None,
        },
    )

    return {
        "adapter_id": adapter_id,
        "arm": job["arm"],
        "objective": objective,
        "dataset": job["dataset"],
        "dataset_sha256": dataset_sha256,
        "example_count": len(examples),
        "seed": job_seed,
        "hyperparameters": {
            "lr": lr,
            "weight_decay": weight_decay,
            "epochs": epochs,
            "grad_accum": grad_accum,
            "max_grad_norm": max_grad_norm,
            "beta": float(job["beta"]) if "beta" in job else None,
            "sft_anchor": float(job["sft_anchor"]) if "sft_anchor" in job else None,
        },
        "optimizer_steps": optimizer_steps,
        "initial_loss": round(initial_loss, 6),
        "epoch_mean_losses": epoch_mean_losses,
        "final_loss": post_eval["final_loss"],
        "final_dpo_loss": post_eval["final_dpo_loss"],
        "final_nll_loss": post_eval["final_nll_loss"],
        "loss_decreased": bool(
            post_eval["final_loss"] < initial_loss or epoch_mean_losses[-1] < initial_loss
        ),
        "post_train_reward_accuracy": post_eval["post_train_reward_accuracy"],
        "post_train_prefer_chosen_count": post_eval["post_train_prefer_chosen_count"],
        "post_train_mean_reward_margin": post_eval["post_train_mean_reward_margin"],
        "total_train_tokens": int(total_train_tokens),
        "total_completion_tokens": int(total_completion_tokens),
        "max_grad_norm_observed": round(max_grad_norm_observed, 6),
        "max_memory_allocated_bytes": max_vram_bytes,
        "train_wall_seconds": round(train_seconds, 4),
        "eval_and_probe_seconds": round(eval_seconds, 4),
        "public_probe": probe_result,
        "safetensors_sha256": save_info["sha256"],
        "safetensors_size_bytes": save_info["size_bytes"],
        "tensor_count": save_info["tensor_count"],
    }


def run_worker(workdir=HERE, local_test_hooks=None):
    """Execute the one-shot detached G3 training worker inside `workdir`.

    `local_test_hooks` is strictly `None` on Colab. Offline unit tests in
    `tests/test_colab_g3_pipeline.py` may pass a dict providing a tiny synthetic
    CPU `(model, tokenizer)` loader to exercise the exact worker state machine,
    training loops, chunking, and receipt publication without GPU or network.
    """
    workdir = Path(workdir).resolve()
    nonce = check_nonce((workdir / "nonce.txt").read_text(encoding="utf-8").strip())
    try:
        with (workdir / "started.lock").open("x", encoding="utf-8") as lock:
            lock.write(nonce + "\n")
    except FileExistsError:
        return 3

    os.environ.update(
        HF_HUB_DISABLE_TELEMETRY="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        HF_HUB_DISABLE_PROGRESS_BARS="1",
        TQDM_DISABLE="1",
        HF_HUB_DISABLE_XET="1",
        TOKENIZERS_PARALLELISM="false",
        PIP_NO_INPUT="1",
    )

    stage_history = []
    current_stage = "waiting_for_arm"

    def set_stage(stage_name):
        nonlocal current_stage
        current_stage = stage_name
        stage_history.append(stage_name)
        publish_text(workdir, "stage.txt", stage_name)

    set_stage("waiting_for_arm")
    worker_t0 = time.monotonic()
    started_utc = dt.datetime.now(dt.timezone.utc).isoformat()

    cfg_path = workdir / "train_job_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    arm_deadline = time.monotonic() + float(cfg.get("arm_deadline_seconds", 450.0))

    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "scope": RECEIPT_SCOPE,
        "nonce": nonce,
        "model_id": MODEL_ID,
        "revision": REVISION,
        "training_performed": False,
        "tool_executed": False,
        "status": "failed",
        "stage": "waiting_for_arm",
        "started_at_utc": started_utc,
    }

    try:
        while True:
            arm_file = workdir / "armed.txt"
            bundle_file = workdir / "bundle.zlib"
            if (
                bundle_file.is_file()
                and bundle_file.stat().st_size > 0
                and arm_file.is_file()
                and arm_file.read_text(encoding="utf-8").strip() == nonce
            ):
                break
            if time.monotonic() >= arm_deadline:
                raise WorkerInvariantError("arm_deadline_exceeded")
            time.sleep(0.25 if local_test_hooks is not None else 0.5)

        set_stage("armed")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        lora_rank = int(cfg.get("lora_rank", 4))
        lora_alpha = float(cfg.get("lora_alpha", 8.0))
        module_prefix = str(cfg.get("lora_module_prefix", "model.language_model."))
        training_jobs = cfg.get("training_jobs") or list(DEFAULT_TRAINING_JOBS)
        probe_cfg = cfg.get("public_probe") or DEFAULT_PUBLIC_PROBE
        expected_model_files = _normalize_expected_model_files(cfg.get("expected_model_files"))
        max_chunk_raw_bytes = int(cfg.get("max_chunk_raw_bytes", MAX_CHUNK_RAW_BYTES))
        micro_batch_sequences = int(cfg.get("micro_batch_sequences", 4))
        eval_batch_sequences = int(cfg.get("eval_batch_sequences", 8))

        set_stage("install_transformers")
        _install_packages_if_needed(
            workdir,
            skip_pip_install=bool(local_test_hooks and local_test_hooks.get("skip_pip_install")),
        )

        set_stage("runtime_check")
        import torch

        lora_adapter = _load_lora_adapter_module(workdir)

        if local_test_hooks is not None:
            train_device = str(local_test_hooks.get("device", "cpu"))
            tf32_enabled = False
            receipt["runtime"] = {
                "python": sys.version.split()[0],
                "cuda_version": getattr(torch.version, "cuda", None),
                "execution_mode": "local_cpu_unit_test",
                "tf32_enabled": tf32_enabled,
                "packages": {
                    k: md.version(k)
                    for k in ("torch", "safetensors")
                    if _pkg_present(k)
                },
            }
            receipt["gpu"] = local_test_hooks.get(
                "gpu_metadata",
                {"name": "SYNTHETIC_CPU_TEST", "memory_bytes": 0, "compute_capability": [0, 0]},
            )
        else:
            if not torch.cuda.is_available():
                raise WorkerInvariantError("cuda_required")
            dev_props = torch.cuda.get_device_properties(0)
            gpu_info = {
                "name": dev_props.name,
                "memory_bytes": int(dev_props.total_memory),
                "compute_capability": [int(dev_props.major), int(dev_props.minor)],
            }
            receipt["gpu"] = gpu_info
            if "L4" not in dev_props.name.split() or not torch.cuda.is_bf16_supported():
                raise WorkerInvariantError("l4_bf16_required")
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            tf32_enabled = True
            train_device = "cuda:0"
            fla_triton_enabled = _probe_fla_triton_kernel(train_device)
            set_stage("runtime_check_fla_on" if fla_triton_enabled else "runtime_check_fla_off")
            pkg_versions = {
                k: md.version(k)
                for k in ("torch", "transformers", "huggingface-hub", "tokenizers", "safetensors")
            }
            if fla_triton_enabled and _pkg_present("fla-core"):
                pkg_versions["fla-core"] = md.version("fla-core")
            if fla_triton_enabled and _pkg_present("flash-linear-attention"):
                pkg_versions["flash-linear-attention"] = md.version("flash-linear-attention")
            receipt["runtime"] = {
                "python": sys.version.split()[0],
                "cuda_version": torch.version.cuda,
                "execution_mode": "colab_l4_cuda_bf16",
                "tf32_enabled": tf32_enabled,
                "fla_triton_enabled": bool(fla_triton_enabled),
                "packages": pkg_versions,
            }

        with tempfile.TemporaryDirectory() as model_tmp:
            set_stage("anonymous_public_download")
            if local_test_hooks is not None and "prepare_snapshot_dir" in local_test_hooks:
                snapshot_dir = Path(local_test_hooks["prepare_snapshot_dir"](Path(model_tmp)))
            else:
                from huggingface_hub import snapshot_download

                snapshot_download(
                    repo_id=MODEL_ID,
                    revision=REVISION,
                    token=False,
                    local_dir=model_tmp,
                    allow_patterns=list(expected_model_files.keys()),
                    endpoint="https://huggingface.co",
                    max_workers=3,
                )
                snapshot_dir = Path(model_tmp)

            set_stage("verify_public_snapshot")
            if local_test_hooks is not None and "expected_test_model_files" in local_test_hooks:
                verified_files = verify_model_snapshot_files(
                    snapshot_dir, local_test_hooks["expected_test_model_files"]
                )
            else:
                verified_files = verify_model_snapshot_files(snapshot_dir, expected_model_files)
            receipt["verified_model_files"] = verified_files

            set_stage("verify_training_bundle")
            compressed_bundle = (workdir / "bundle.zlib").read_bytes()
            bundle_zlib_sha256 = sha256_bytes(compressed_bundle)
            if "expected_bundle_zlib_sha256" in cfg and cfg["expected_bundle_zlib_sha256"]:
                if bundle_zlib_sha256 != cfg["expected_bundle_zlib_sha256"]:
                    raise WorkerInvariantError("bundle_zlib_sha256_mismatch")
            raw_bundle = zlib.decompress(compressed_bundle)
            bundle_sha256 = sha256_bytes(raw_bundle)
            expected_bundle_sha = cfg.get("expected_bundle_sha256")
            if not expected_bundle_sha or bundle_sha256 != expected_bundle_sha:
                raise WorkerInvariantError("bundle_sha256_mismatch")
            bundle = json.loads(raw_bundle.decode("utf-8"))
            if (
                not isinstance(bundle, dict)
                or bundle.get("g3_dataset_ready") is not True
                or bundle.get("g2_passed_verified") is not True
                or bundle.get("model_target", {}).get("model_id") != MODEL_ID
                or bundle.get("model_target", {}).get("revision") != REVISION
            ):
                raise WorkerInvariantError("bundle_gate_or_target_invalid")
            datasets = bundle.get("datasets")
            dataset_files_meta = bundle.get("dataset_files", {})
            if not isinstance(datasets, dict) or not datasets:
                raise WorkerInvariantError("bundle_datasets_missing")
            receipt["training_bundle"] = {
                "bundle_sha256": bundle_sha256,
                "bundle_size_bytes": len(raw_bundle),
                "bundle_zlib_sha256": bundle_zlib_sha256,
                "bundle_zlib_size_bytes": len(compressed_bundle),
                "schema_version": bundle.get("schema_version"),
                "g3_dataset_ready": True,
                "g2_frozen_verdict_sha256": bundle.get("g2_frozen_verdict_sha256"),
                "dataset_governance_tier": bundle.get("dataset_governance_tier"),
            }

            set_stage("load_base_model")
            if local_test_hooks is not None and "build_model_and_tokenizer" in local_test_hooks:
                model, tokenizer = local_test_hooks["build_model_and_tokenizer"](snapshot_dir)
                model = model.to(train_device)
            else:
                from transformers import AutoTokenizer, Qwen3_5ForCausalLM

                tokenizer = AutoTokenizer.from_pretrained(
                    str(snapshot_dir), trust_remote_code=False, local_files_only=True
                )
                model, load_info = Qwen3_5ForCausalLM.from_pretrained(
                    str(snapshot_dir),
                    dtype=torch.bfloat16,
                    attn_implementation="sdpa",
                    use_cache=False,
                    use_safetensors=True,
                    local_files_only=True,
                    trust_remote_code=False,
                    output_loading_info=True,
                )
                _verify_checkpoint_loading(load_info)
                if hasattr(model, "config"):
                    model.config.use_cache = False
                model = model.to("cuda:0").eval()
                if any(p.device != torch.device("cuda:0") for p in model.parameters()):
                    raise WorkerInvariantError("model_parameters_not_on_cuda0")

            eos_ids = _resolve_eos_ids(tokenizer, model)
            if local_test_hooks is None and eos_ids != EXPECTED_EOS_IDS:
                raise WorkerInvariantError("unexpected_eos_ids")
            if not eos_ids:
                raise WorkerInvariantError("missing_eos_ids")

            initial_base_digest = lora_adapter.base_weight_digest(model)
            lora_info = lora_adapter.inject_lora(
                model,
                rank=lora_rank,
                alpha=lora_alpha,
                seed=101,
                module_prefix=module_prefix,
            )
            post_inject_base_digest = lora_adapter.base_weight_digest(model)
            if post_inject_base_digest != initial_base_digest:
                raise WorkerInvariantError("base_digest_changed_on_inject")

            receipt["model_class"] = type(model).__name__
            receipt["parameter_device"] = str(next(model.parameters()).device)
            receipt["base_parameter_dtype"] = str(
                next(
                    p.dtype
                    for n, p in model.named_parameters()
                    if not n.endswith(("lora_A", "lora_B"))
                )
            )
            receipt["initial_base_weight_digest"] = initial_base_digest
            receipt["lora_injection"] = {
                "adapter_version": lora_info["adapter_version"],
                "rank": lora_info["rank"],
                "alpha": lora_info["alpha"],
                "matched_module_count": lora_info["matched_module_count"],
                "trainable_param_count": lora_info["trainable_param_count"],
                "module_prefix": module_prefix,
            }

            set_stage("tokenize_and_precompute_ref")
            precomp = _pretokenize_and_precompute_references(
                model=model,
                tokenizer=tokenizer,
                lora_adapter=lora_adapter,
                datasets=datasets,
                training_jobs=training_jobs,
                device=train_device,
                eval_batch_sequences=eval_batch_sequences,
            )
            lora_adapter.set_lora_enabled(model, False)
            base_probe = _run_greedy_public_probe(model, tokenizer, probe_cfg, train_device, eos_ids)
            receipt["reference_precompute"] = {
                "unique_prompt_count": precomp["unique_prompt_count"],
                "unique_sequence_count": precomp["unique_sequence_count"],
                "b4_divergence_verified_count": precomp["b4_divergence_verified_count"],
                "elapsed_seconds": precomp["reference_precompute_seconds"],
                "frozen_base_public_probe": base_probe,
            }

            adapters_dir = workdir / "adapters"
            adapters_dir.mkdir(parents=True, exist_ok=True)
            chunks_dir = workdir / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)

            trained_jobs = []
            for job in training_jobs:
                adapter_id = check_adapter_id(job["adapter_id"])
                set_stage(f"train_{adapter_id}")
                ds_meta = dataset_files_meta.get(job["dataset"], {})
                ds_sha = ds_meta.get("sha256", "")
                job_report = _train_single_job(
                    model=model,
                    tokenizer=tokenizer,
                    lora_adapter=lora_adapter,
                    job=job,
                    precomp=precomp,
                    probe_cfg=probe_cfg,
                    eos_ids=eos_ids,
                    adapters_dir=adapters_dir,
                    device=train_device,
                    bundle_sha256=bundle_sha256,
                    dataset_sha256=ds_sha,
                    micro_batch_sequences=micro_batch_sequences,
                    eval_batch_sequences=eval_batch_sequences,
                )
                trained_jobs.append(job_report)
                receipt["training_performed"] = True

            set_stage("pack_chunks")
            for job_report in trained_jobs:
                adapter_id = job_report["adapter_id"]
                st_path = adapters_dir / f"{adapter_id}.safetensors"
                chunk_info = pack_safetensors_to_chunks(
                    safetensors_path=st_path,
                    chunks_dir=chunks_dir,
                    nonce=nonce,
                    adapter_id=adapter_id,
                    max_chunk_raw_bytes=max_chunk_raw_bytes,
                )
                if chunk_info["safetensors_sha256"] != job_report["safetensors_sha256"]:
                    raise WorkerInvariantError("adapter_safetensors_digest_changed")
                job_report.update(
                    compressed_sha256=chunk_info["compressed_sha256"],
                    compressed_size_bytes=chunk_info["compressed_size_bytes"],
                    chunk_count=chunk_info["chunk_count"],
                    chunk_files=chunk_info["chunk_files"],
                    chunk_part_sha256s=chunk_info["chunk_part_sha256s"],
                    chunk_frame_sha256s=chunk_info["chunk_frame_sha256s"],
                )

            final_base_digest = lora_adapter.base_weight_digest(model)
            base_unchanged = final_base_digest == initial_base_digest
            if not base_unchanged:
                raise WorkerInvariantError("base_weights_mutated_during_training")

            receipt.update(
                status="completed",
                stage="completed",
                final_base_weight_digest=final_base_digest,
                base_weights_unchanged=True,
                adapter_count=len(trained_jobs),
                adapters=trained_jobs,
            )

    except Exception as exc:
        diag = sanitize_diagnostic(exc)
        publish_text(workdir, "diagnostic.txt", diag)
        receipt.update(
            status="failed",
            stage=current_stage,
            error_type=type(exc).__name__,
            error_code=exc.safe_code if isinstance(exc, WorkerInvariantError) else diag,
        )

    receipt["stage_history"] = stage_history
    receipt["ended_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    receipt["worker_wall_seconds"] = round(time.monotonic() - worker_t0, 4)

    receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    publish_bytes(workdir, "g3_training_receipt.json", receipt_bytes)
    frame = emit_framed_json(receipt_bytes, nonce, prefix=RECEIPT_FRAME_PREFIX)
    publish_text(workdir, "receipt_frame.txt", frame)

    terminal_stage = (
        "receipt_published_completed"
        if receipt["status"] == "completed"
        else "receipt_published_failed"
    )
    set_stage(terminal_stage)
    return 0 if receipt["status"] == "completed" else 1


def _pkg_present(name):
    try:
        md.version(name)
        return True
    except md.PackageNotFoundError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        type=Path,
        default=HERE,
        help="exclusive worker directory containing nonce.txt and train_job_config.json",
    )
    args = parser.parse_args(argv)
    return run_worker(workdir=args.workdir)


if __name__ == "__main__":
    sys.exit(main())
