#!/usr/bin/env python3
"""Verify archived run integrity; optionally regenerate a derived index."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(root):
    errors, rows = [], []
    experiments = root / "experiments"
    for directory in sorted(p for p in experiments.iterdir() if p.is_dir()):
        path = directory / "manifest.json"
        if not path.exists():
            errors.append("{}: missing manifest".format(directory.name))
            continue
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                errors.append("{}: manifest must be an object".format(directory.name))
                continue
            required = {"run_id": str, "kind": str, "status": str, "owner": str,
                        "hypothesis": str, "acceptance_criteria": str, "inputs": dict,
                        "outputs": dict, "command": list, "cwd": str, "environment": dict,
                        "started_at_utc": str, "ended_at_utc": str, "config_sha256": str,
                        "exit_code": int, "evidence_valid": bool, "inputs_changed_during_run": list}
            schema_errors = [key for key, expected in required.items()
                             if type(value.get(key)) is not expected]
            if schema_errors:
                errors.append("{}: missing/invalid fields {}".format(directory.name, schema_errors))
            if value.get("schema_version") not in (1, 2):
                errors.append("{}: unsupported schema version".format(directory.name))
            if value.get("kind") not in ("synthetic_harness", "model_inference", "model_training", "analysis"):
                errors.append("{}: unknown evidence kind".format(directory.name))
            if value.get("status") not in ("completed", "failed", "interrupted"):
                errors.append("{}: unknown/unfinished status".format(directory.name))
            if not isinstance(value.get("command"), list) or not value.get("command") or not all(
                    isinstance(item, str) for item in value.get("command", [])):
                errors.append("{}: command must be a nonempty string list".format(directory.name))
            if value.get("run_id") != directory.name:
                errors.append("{}: ID mismatch".format(directory.name))
            if value.get("status") == "completed" and value.get("exit_code") != 0:
                errors.append("{}: completed with nonzero exit".format(directory.name))
            if value.get("status") in ("failed", "interrupted") and value.get("exit_code") == 0:
                errors.append("{}: failure status with zero exit".format(directory.name))
            if value.get("evidence_valid") and (value.get("status") != "completed"
                                                or value.get("inputs_changed_during_run")):
                errors.append("{}: inconsistent evidence_valid".format(directory.name))
            if value.get("schema_version") == 2:
                if value.get("execution_mode") != "frozen_snapshot" or Path(value.get("cwd", "")).resolve() != (directory / "inputs").resolve():
                    errors.append("{}: frozen execution metadata mismatch".format(directory.name))
                expected_contract = "synthetic_evaluation" if "toolloop_fix.run" in value.get("command", []) else "command_logs"
                if value.get("artifact_contract") != expected_contract:
                    errors.append("{}: artifact contract mismatch".format(directory.name))
            for section, prefix in (("inputs", directory / "inputs"), ("outputs", directory)):
                entries = value.get(section)
                if not isinstance(entries, dict):
                    continue
                for name, expected in entries.items():
                    if not isinstance(name, str) or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                        errors.append("{}: invalid {} hash entry".format(directory.name, section))
                        continue
                    candidate = prefix / name
                    if Path(name).is_absolute():
                        raise ValueError("Artifact paths must be relative")
                    candidate.resolve().relative_to(prefix.resolve())
                    if not candidate.is_file() or digest(candidate) != expected:
                        errors.append("{}: {} integrity mismatch {}".format(directory.name, section, name))
                actual = {str(p.relative_to(prefix)) for p in prefix.rglob("*") if p.is_file()
                          and (section == "inputs" or
                               (p.relative_to(prefix).parts[0] != "inputs" and p.name != "manifest.json"))}
                if set(entries) != actual:
                    errors.append("{}: {} inventory mismatch".format(directory.name, section))
            if not value.get("inputs"):
                errors.append("{}: no input snapshot".format(directory.name))
            if (directory / "config.json").is_file() and digest(directory / "config.json") != value.get("config_sha256"):
                errors.append("{}: configuration hash mismatch".format(directory.name))
            for name in ("config.json", "stdout.log", "stderr.log"):
                if not (directory / name).is_file():
                    errors.append("{}: missing {}".format(directory.name, name))
            # v1 had no explicit contract. A diagnostic probe is not a full evaluation.
            full_evaluation = isinstance(value.get("command"), list) and "toolloop_fix.run" in value["command"]
            if full_evaluation and value.get("status") == "completed":
                for name in ("trajectories.jsonl", "case_results.jsonl", "metrics.json"):
                    if not (directory / name).is_file():
                        errors.append("{}: missing harness artifact {}".format(directory.name, name))
            if isinstance(value.get("run_id"), str):
                rows.append(value)
        except (ValueError, OSError, TypeError) as exc:
            errors.append("{}: {}".format(directory.name, exc))
    return errors, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-index", action="store_true")
    args = parser.parse_args()
    errors, rows = validate(ROOT)
    if args.write_index:
        lines = ["# 实验索引（由 manifest 自动生成）", "", "进程 completed 不等于科学假设成立；验收结论见 iterations/。", "",
                 "| Run | 证据类型 | 状态 | 退出码 | 输入稳定 | Owner |", "|---|---|---|---|---|---|"]
        for row in rows:
            name = row["run_id"]
            lines.append("| [{}]({}/manifest.json) | {} | {} | {} | {} | {} |".format(
                name, name, row.get("kind"), row.get("status"), row.get("exit_code"),
                not row.get("inputs_changed_during_run", []), row.get("owner")))
        (ROOT / "experiments" / "INDEX.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"runs_checked": len(rows), "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
