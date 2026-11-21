#!/usr/bin/env python3
"""Record one command run without installing dependencies or hiding failures."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("synthetic_harness", "model_inference", "model_training", "analysis")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def input_files(root, config):
    # Small, project-owned inputs only. Model weights and external data are NOT copied.
    files = {config}
    for folder, pattern in (("toolloop_fix", "*.py"), ("scripts", "*.py"),
                            ("tests", "*.py"), ("configs", "*.json"),
                            ("data/dev", "*.jsonl"), ("docs/protocols", "*.md")):
        files.update((root / folder).rglob(pattern))
    return sorted(p for p in files if p.is_file())


def execute_run(root, run_id, kind, config, hypothesis, acceptance, owner, command, parent=None):
    root = root.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,95}", run_id):
        raise ValueError("ID must be 3–96 letters/digits/underscore/hyphen")
    if kind not in KINDS or not command:
        raise ValueError("Known evidence kind and a command are required")
    config = (root / config).resolve()
    config.relative_to(root)  # Never silently snapshot unrelated user files.
    config_value = json.loads(config.read_text())
    if not isinstance(config_value, dict):
        raise ValueError("Configuration must be a JSON object")
    if parent and not (root / "experiments" / parent / "manifest.json").is_file():
        raise ValueError("Parent experiment does not exist")
    run_dir = root / "experiments" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)  # Atomic reservation; never overwrite.
    manifest = {
        "schema_version": 1, "run_id": run_id, "kind": kind, "owner": owner,
        "parent_run": parent, "status": "preparing", "acceptance_status": "not_reviewed",
        "hypothesis": hypothesis, "acceptance_criteria": acceptance,
        "started_at_utc": now(), "report_timezone": "Asia/Shanghai",
        "environment": {"python": sys.version, "executable": sys.executable,
                        "platform": platform.platform(), "machine": platform.machine()},
        "inputs": {}, "outputs": {}, "exit_code": None,
        "evidence_valid": False,
    }
    write_json(run_dir / "manifest.json", manifest)
    start = time.monotonic()
    process = None
    code = 127
    try:
        shutil.copy2(config, run_dir / "config.json")
        files = input_files(root, config)
        for path in files:
            relative = str(path.relative_to(root))
            manifest["inputs"][relative] = digest(path)
            target = run_dir / "inputs" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        resolved = [x.replace("{run_dir}", str(run_dir)).replace("{config}", str(run_dir / "config.json"))
                    for x in command]
        manifest.update({"command": resolved, "cwd": str(root), "status": "running",
                         "config_sha256": digest(run_dir / "config.json")})
        write_json(run_dir / "manifest.json", manifest)
        with (run_dir / "stdout.log").open("w") as stdout, (run_dir / "stderr.log").open("w") as stderr:
            process = subprocess.Popen(resolved, cwd=str(root), stdout=stdout, stderr=stderr)
            code = process.wait()
        manifest["status"] = "completed" if code == 0 else "failed"
    except KeyboardInterrupt:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        code = 130
        manifest["status"] = "interrupted"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = "{}: {}".format(type(exc).__name__, exc)
        with (run_dir / "stderr.log").open("a") as stream:
            stream.write(manifest["error"] + "\n")
        (run_dir / "stdout.log").touch(exist_ok=True)
    finally:
        changed = [name for name, value in manifest["inputs"].items()
                   if not (root / name).is_file() or digest(root / name) != value]
        for path in sorted(run_dir.rglob("*")):
            relative = path.relative_to(run_dir)
            if path.is_file() and relative.parts[0] != "inputs" and path.name != "manifest.json":
                manifest["outputs"][str(relative)] = digest(path)
        manifest.update({"exit_code": code, "ended_at_utc": now(),
                         "elapsed_seconds": round(time.monotonic() - start, 6),
                         "inputs_changed_during_run": changed,
                         "evidence_valid": manifest["status"] == "completed" and not changed})
        write_json(run_dir / "manifest.json", manifest)
    print(json.dumps({"run_id": run_id, "status": manifest["status"], "exit_code": code,
                      "evidence_valid": manifest["evidence_valid"], "directory": str(run_dir)}, ensure_ascii=False))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--kind", required=True, choices=KINDS)
    parser.add_argument("--config", required=True)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--acceptance", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--parent")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return execute_run(ROOT, args.id, args.kind, args.config, args.hypothesis,
                       args.acceptance, args.owner, command, args.parent)


if __name__ == "__main__":
    sys.exit(main())
