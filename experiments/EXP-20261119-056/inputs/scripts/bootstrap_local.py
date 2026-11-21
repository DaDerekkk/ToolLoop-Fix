#!/usr/bin/env python3
"""Explicitly authorized, isolated runtime bootstrap with retained operation logs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config_bytes = args.config.read_bytes()
    (output / "config.json").write_bytes(config_bytes)
    (output / "bootstrap_local.py").write_bytes(Path(__file__).read_bytes())
    record = {"operation": "isolated_runtime_install", "status": "running", "started_at_utc": now(),
              "authorization": config["authorization"], "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
              "source_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "stages": []}
    def save():
        (output / "manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    save()
    env = dict(os.environ, UV_CACHE_DIR=str(ROOT / "runtime/uv-cache"),
               UV_PYTHON_INSTALL_DIR=str(ROOT / "runtime/python"), UV_NO_PROGRESS="1", PYTHONDONTWRITEBYTECODE="1")
    def run(name, command):
        stage = {"name": name, "command": command, "started_at_utc": now()}
        record["stages"].append(stage)
        save()
        with (output / (name + ".stdout.log")).open("w") as out, (output / (name + ".stderr.log")).open("w") as err:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=out, stderr=err)
        stage.update(exit_code=completed.returncode, ended_at_utc=now())
        save()
        print(name, completed.returncode, flush=True)
        if completed.returncode:
            raise RuntimeError("stage failed: " + name)
    try:
        uv = config["uv"]
        run("python_install", [uv, "python", "install", config["python_version"], "--no-bin"])
        run("venv", [uv, "venv", "--python", config["python_version"], str(ROOT / ".venv")])
        python = str(ROOT / ".venv/bin/python")
        run("packages", [uv, "pip", "install", "--python", python] + config["packages"])
        run("freeze", [uv, "pip", "freeze", "--python", python])
        run("runtime_probe", [python, "-c", "import json,platform,sys,torch,transformers; print(json.dumps({'python':sys.version,'platform':platform.platform(),'torch':torch.__version__,'transformers':transformers.__version__,'mps_built':torch.backends.mps.is_built(),'mps_available':torch.backends.mps.is_available()},indent=2))"])
        record["status"] = "completed"
        code = 0
    except Exception as exc:
        record.update(status="failed", error="{}: {}".format(type(exc).__name__, exc))
        code = 1
    record.update(ended_at_utc=now(), exit_code=code)
    record["artifacts"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()
                           if p.is_file() and p.name != "manifest.json"}
    save()
    return code


if __name__ == "__main__":
    sys.exit(main())
