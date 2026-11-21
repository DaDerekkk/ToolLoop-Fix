#!/usr/bin/env python3
"""Replay a synthetic run from its archived inputs, not the current code."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.source_run.resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest["kind"] != "synthetic_harness" or manifest["status"] != "completed":
        raise ValueError("Replay requires a completed synthetic harness run")
    for name, expected in manifest["inputs"].items():
        if digest(source / "inputs" / name) != expected:
            raise ValueError("Source snapshot hash mismatch: " + name)
    if digest(source / "config.json") != manifest["config_sha256"]:
        raise ValueError("Source configuration hash mismatch")
    args.output.mkdir(parents=True, exist_ok=True)
    replay = args.output.resolve() / "replay"
    replay.mkdir(exist_ok=False)
    command = [sys.executable, "-m", "toolloop_fix.run", "--config",
               str(source / "config.json"), "--output", str(replay)]
    result = subprocess.run(command, cwd=str(source / "inputs"), capture_output=True, text=True,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    (args.output / "replay.stdout.log").write_text(result.stdout)
    (args.output / "replay.stderr.log").write_text(result.stderr)
    comparisons = {}
    for name in ("trajectories.jsonl", "case_results.jsonl", "metrics.json"):
        original = digest(source / name)
        actual = digest(replay / name) if (replay / name).is_file() else None
        comparisons[name] = {"source_sha256": original, "replay_sha256": actual,
                             "matches": original == actual}
    passed = result.returncode == 0 and all(item["matches"] for item in comparisons.values())
    report = {"source_run": manifest["run_id"], "source_manifest_sha256": digest(source / "manifest.json"),
              "execution_cwd": str(source / "inputs"), "command": command,
              "exit_code": result.returncode, "comparisons": comparisons,
              "all_equal": passed, "scope": "deterministic synthetic replay, not model reproducibility"}
    (args.output / "replay_comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
