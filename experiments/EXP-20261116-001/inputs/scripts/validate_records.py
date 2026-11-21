#!/usr/bin/env python3
"""Verify archived run integrity; optionally regenerate a derived index."""
import argparse
import hashlib
import json
from pathlib import Path
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
            for key in ("run_id", "kind", "status", "owner", "hypothesis", "acceptance_criteria", "inputs", "outputs"):
                if key not in value:
                    errors.append("{}: missing {}".format(directory.name, key))
            if value.get("run_id") != directory.name:
                errors.append("{}: ID mismatch".format(directory.name))
            if value.get("status") in ("preparing", "running"):
                errors.append("{}: unfinished run; recover explicitly".format(directory.name))
            if value.get("status") == "completed" and value.get("exit_code") != 0:
                errors.append("{}: completed with nonzero exit".format(directory.name))
            for section, prefix in (("inputs", directory / "inputs"), ("outputs", directory)):
                for name, expected in value.get(section, {}).items():
                    candidate = prefix / name
                    candidate.resolve().relative_to(prefix.resolve())
                    if not candidate.is_file() or digest(candidate) != expected:
                        errors.append("{}: {} integrity mismatch {}".format(directory.name, section, name))
            if not value.get("inputs"):
                errors.append("{}: no input snapshot".format(directory.name))
            for name in ("config.json", "stdout.log", "stderr.log"):
                if not (directory / name).is_file():
                    errors.append("{}: missing {}".format(directory.name, name))
            if value.get("kind") == "synthetic_harness" and value.get("status") == "completed":
                for name in ("trajectories.jsonl", "case_results.jsonl", "metrics.json"):
                    if not (directory / name).is_file():
                        errors.append("{}: missing harness artifact {}".format(directory.name, name))
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
