#!/usr/bin/env python3
"""Verify archived run integrity; optionally regenerate a derived index."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MODEL_FIELDS = ("model_id", "revision", "backend", "model_path", "device", "dtype")
MODEL_ARTIFACTS = ("model_provenance.json", "generations.jsonl", "trajectories.jsonl",
                   "case_results.jsonl", "metrics.json")


def is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def config_errors(config, kind):
    """Validate declarations, not whether a command actually ran a model."""
    if not isinstance(config, dict):
        return ["configuration must be an object"]
    errors = []
    for key in ("evidence_kind", "evidence_class"):
        if key in config and config[key] != kind:
            errors.append("configuration {} does not match kind".format(key))
    if kind != "model_inference":
        return errors
    if not any(key in config for key in ("evidence_kind", "evidence_class")):
        errors.append("model configuration requires an evidence kind/class")
    model = config.get("model")
    if not isinstance(model, dict):
        errors.append("model configuration requires model object")
    else:
        for key in MODEL_FIELDS:
            if not isinstance(model.get(key), str) or not model[key].strip():
                errors.append("model.{} must be a nonempty string".format(key))
        if not re.fullmatch(r"[a-f0-9]{40}", str(model.get("revision", ""))):
            errors.append("model.revision must be an immutable 40-character commit")
        if not Path(str(model.get("model_path", ""))).is_absolute():
            errors.append("model.model_path must be absolute")
    sampling = config.get("sampling")
    if not isinstance(sampling, dict):
        errors.append("model configuration requires sampling object")
    else:
        for key in ("do_sample", "enable_thinking"):
            if type(sampling.get(key)) is not bool:
                errors.append("sampling.{} must be boolean".format(key))
        for key, minimum in (("top_k", 0), ("max_new_tokens", 1), ("seed", 0)):
            if type(sampling.get(key)) is not int or sampling[key] < minimum:
                errors.append("invalid sampling.{}".format(key))
        for key in ("temperature", "top_p"):
            if type(sampling.get(key)) not in (int, float) or not math.isfinite(sampling[key]):
                errors.append("sampling.{} must be finite numeric".format(key))
        if type(sampling.get("top_p")) in (int, float) and not 0 < sampling["top_p"] <= 1:
            errors.append("sampling.top_p must be in (0, 1]")
        if type(sampling.get("temperature")) in (int, float) and (
                sampling["temperature"] < 0 or sampling.get("do_sample") and sampling["temperature"] == 0):
            errors.append("invalid sampling.temperature")
    refs = config.get("external_inputs")
    if not isinstance(refs, list) or not refs:
        errors.append("model inference requires external input references")
    else:
        names = []
        for ref in refs:
            if not isinstance(ref, dict):
                errors.append("invalid external input reference")
                continue
            name = ref.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
                errors.append("invalid external input name")
            else:
                names.append(name)
            if not isinstance(ref.get("path"), str) or not Path(ref["path"]).is_absolute() or not is_sha256(ref.get("sha256")):
                errors.append("invalid external input path/hash")
        if names.count("model_manifest") != 1 or len(names) != len(set(names)):
            errors.append("external inputs need unique names and exactly one model_manifest")
    cases_path = config.get("cases_path")
    if not isinstance(cases_path, str) or not cases_path or Path(cases_path).is_absolute() or ".." in Path(cases_path).parts:
        errors.append("model cases_path must be a project-relative frozen input")
    return errors


def model_manifest_errors(value, config):
    if not isinstance(value, dict):
        return ["external model manifest must be an object"]
    errors = []
    for key in ("model_id", "revision", "model_path"):
        if value.get(key) != config["model"].get(key):
            errors.append("external model manifest {} mismatch".format(key))
    files = value.get("files")
    if not isinstance(files, list) or not files:
        return errors + ["external model manifest needs nonempty files"]
    names = []
    for item in files:
        if not isinstance(item, dict):
            errors.append("invalid model file entry")
            continue
        name = item.get("path")
        if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
            errors.append("model file path must stay inside model directory")
        else:
            names.append(name)
        if not is_sha256(item.get("sha256")) or type(item.get("size_bytes")) is not int or item["size_bytes"] < 0:
            errors.append("invalid model file hash/size")
    if len(names) != len(set(names)):
        errors.append("duplicate model file paths")
    return errors


def model_artifact_errors(directory, config, manifest):
    """Check recorded model provenance/outputs; this is not remote attestation."""
    errors = []
    for name in MODEL_ARTIFACTS:
        if not (directory / name).is_file():
            errors.append("missing model artifact " + name)
    if errors:
        return errors
    try:
        provenance = json.loads((directory / "model_provenance.json").read_text())
        metrics = json.loads((directory / "metrics.json").read_text())
        for name, value in (("model_provenance", provenance), ("metrics", metrics)):
            if not isinstance(value, dict) or value.get("evidence_class") != "model_inference":
                errors.append(name + " evidence_class mismatch")
        if not isinstance(provenance, dict):
            return errors
        if provenance.get("model") != config["model"]:
            errors.append("executed model provenance does not match configuration")
        if provenance.get("sampling") != config["sampling"]:
            errors.append("executed sampling provenance does not match configuration")
        model_ref = next(ref for ref in config["external_inputs"] if ref["name"] == "model_manifest")
        if provenance.get("model_manifest_sha256") != model_ref["sha256"]:
            errors.append("executed model manifest hash mismatch")
        if provenance.get("model_files_verified") is not True or not is_sha256(provenance.get("chat_template_sha256")):
            errors.append("model files/template verification not recorded")
        runtime = provenance.get("runtime", {})
        if not isinstance(runtime, dict):
            errors.append("model runtime must be an object")
        else:
            for key in ("executable", "python_version"):
                if not isinstance(runtime.get(key), str) or not runtime[key]:
                    errors.append("missing executed runtime " + key)
            executable = runtime.get("executable")
            expected = manifest.get("child_executable")
            if not isinstance(executable, str) or not Path(executable).is_absolute() or not expected or Path(executable).resolve() != Path(expected).resolve():
                errors.append("executed interpreter provenance mismatch")
            packages = runtime.get("packages")
            if not isinstance(packages, dict) or not packages or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in packages.items()):
                errors.append("actual runtime package versions not recorded")
            elif config["model"]["backend"] == "transformers" and not {"torch", "transformers"}.issubset(packages):
                errors.append("transformers backend needs actual torch/transformers versions")
        for name in ("generations.jsonl", "trajectories.jsonl", "case_results.jsonl"):
            rows = [json.loads(line) for line in (directory / name).read_text().splitlines() if line.strip()]
            if not rows or any(not isinstance(row, dict) or row.get("evidence_class") != "model_inference" for row in rows):
                errors.append(name + " must contain nonempty model_inference rows")
    except (ValueError, OSError, TypeError, KeyError) as exc:
        errors.append("invalid model artifacts: " + str(exc))
    return errors


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
            if value.get("schema_version") not in (1, 2, 3):
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
            if value.get("schema_version") in (2, 3):
                if value.get("execution_mode") != "frozen_snapshot" or Path(value.get("cwd", "")).resolve() != (directory / "inputs").resolve():
                    errors.append("{}: frozen execution metadata mismatch".format(directory.name))
                expected_contract = ("model_inference" if value.get("schema_version") == 3 and value.get("kind") == "model_inference" else
                                     "synthetic_evaluation" if "toolloop_fix.run" in value.get("command", []) else "command_logs")
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
            if value.get("schema_version") == 3:
                config = json.loads((directory / "config.json").read_text())
                declared_errors = config_errors(config, value.get("kind"))
                errors.extend("{}: {}".format(directory.name, item) for item in declared_errors)
                config_input = value.get("config_input_path")
                input_hashes = value.get("inputs") if isinstance(value.get("inputs"), dict) else {}
                if not isinstance(config_input, str) or input_hashes.get(config_input) != value.get("config_sha256"):
                    errors.append("{}: frozen configuration input mismatch".format(directory.name))
                if not isinstance(value.get("contract_errors"), list) or not isinstance(value.get("external_inputs_changed_during_run"), list):
                    errors.append("{}: missing model-contract bookkeeping".format(directory.name))
                if value.get("evidence_valid") and (value.get("contract_errors") or value.get("external_inputs_changed_during_run")):
                    errors.append("{}: inconsistent model evidence_valid".format(directory.name))
                if value.get("kind") == "model_inference" and not declared_errors:
                    expected_refs = [{**ref, "snapshot_path": "external_inputs/{}.json".format(ref["name"])}
                                     for ref in config["external_inputs"]]
                    if value.get("external_inputs") != expected_refs:
                        errors.append("{}: external input references mismatch".format(directory.name))
                    for ref in expected_refs:
                        external = directory / ref["snapshot_path"]
                        if not external.is_file() or digest(external) != ref["sha256"]:
                            errors.append("{}: external snapshot mismatch {}".format(directory.name, ref["name"]))
                        else:
                            external_value = json.loads(external.read_text())
                            if not isinstance(external_value, dict):
                                errors.append("{}: external JSON must be an object".format(directory.name))
                            elif ref["name"] == "model_manifest":
                                external_errors = model_manifest_errors(external_value, config)
                                errors.extend("{}: {}".format(directory.name, item) for item in external_errors)
                    if config["cases_path"] not in input_hashes:
                        errors.append("{}: model cases not in frozen inputs".format(directory.name))
                    if value.get("external_inputs_changed_during_run"):
                        errors.append("{}: external model manifest changed during run".format(directory.name))
                    if value.get("status") == "completed":
                        observed_errors = model_artifact_errors(directory, config, value)
                        errors.extend("{}: {}".format(directory.name, item) for item in observed_errors)
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
