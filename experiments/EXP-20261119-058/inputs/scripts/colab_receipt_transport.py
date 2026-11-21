"""Stdlib transport for the pinned, public-only probe JSON. No model or cloud client.

Encode only a receipt from the reviewed probe, never CLI logs or arbitrary JSON.
In a fresh operation, send the marker through Python print/Colab stream stdout.
Pipe *only stdout* to --receive; do not persist mixed CLI diagnostics. This
checks byte integrity and the expected nonce, not provenance or model success.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys

PREFIX = b"TLP_RECEIPT_V1:"
MAX_BYTES = 65536
MAX_STREAM = 4 * MAX_BYTES
KEYS = set("""schema_version scope model_id revision training_performed tool_executed
stage status error_type runtime gpu verified_files model_class parameter_device
parameter_dtype messages tools prompt_text prompt_token_ids prompt_sha256 seed
input_tokens generation_config raw_text generated_token_ids output_tokens
generation_seconds token_limit_reached""".split())


def check_nonce(nonce):
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("invalid operation nonce")
    return nonce.encode("ascii")


def check_receipt(raw):
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("out-of-bounds receipt")
    data = json.loads(raw.decode("utf-8"))
    if (not isinstance(data, dict) or set(data) - KEYS or
            data.get("schema_version") != 1 or
            data.get("scope") != "single_public_tool_call_proposal_only" or
            data.get("training_performed") is not False or data.get("tool_executed") is not False or
            data.get("status") not in ("completed", "failed")):
        raise ValueError("not a public probe receipt")


def emit(path, nonce):
    """Return one ASCII line (without newline) for an existing probe file."""
    key = check_nonce(nonce)
    path = Path(path)
    if path.name != "toolloop_probe.json" or path.is_symlink() or not path.is_file():
        raise ValueError("unexpected receipt file")
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    check_receipt(raw)
    return b":".join((PREFIX[:-1], key, str(len(raw)).encode(),
                      hashlib.sha256(raw).hexdigest().encode(), base64.b64encode(raw))).decode("ascii")


def receive(stream, nonce, output):
    key = check_nonce(nonce)
    buf = stream.read(MAX_STREAM + 1)
    if len(buf) > MAX_STREAM:
        raise ValueError("out-of-bounds stdout")
    # Keep line endings so missing the last newline cannot look like a complete frame.
    candidates = [line for line in buf.splitlines(keepends=True) if line.startswith(PREFIX)]
    if len(candidates) != 1 or not candidates[0].endswith(b"\n"):
        raise ValueError("missing, truncated, or duplicate frame")
    marker, actual, size, digest, payload = candidates[0].rstrip(b"\r\n").split(b":")
    if marker + b":" != PREFIX or actual != key or not re.fullmatch(rb"[1-9][0-9]{0,4}", size):
        raise ValueError("invalid frame header")
    n = int(size)
    if n > MAX_BYTES or len(payload) != 4 * ((n + 2) // 3):
        raise ValueError("invalid frame length")
    raw = base64.b64decode(payload, validate=True)
    if len(raw) != n or hashlib.sha256(raw).hexdigest().encode() != digest:
        raise ValueError("integrity mismatch")
    check_receipt(raw)
    with Path(output).open("xb") as target:
        target.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit", type=Path, metavar="toolloop_probe.json")
    mode.add_argument("--receive", type=Path, metavar="NEW_OUTPUT")
    parser.add_argument("--nonce", required=True, help="unique public 32-character lowercase hex operation ID")
    args = parser.parse_args()
    try:
        if args.emit:
            print(emit(args.emit, args.nonce), flush=True)
        else:
            receive(sys.stdin.buffer, args.nonce, args.receive)
    except (OSError, ValueError, RecursionError):
        # Never print the file, CLI diagnostics, or malformed payload/JSON.
        parser.exit(1, "receipt transport rejected input or output\n")


if __name__ == "__main__":
    main()
