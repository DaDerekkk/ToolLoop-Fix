#!/usr/bin/env python3
"""CPU-only, recorded-generation audit of a pinned B0/B1/B2 run triplet."""
import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from toolloop_fix.baselines import GUARD_VERSION, prompt_for
from toolloop_fix.calibrated_protocol import CALIBRATED_PROTOCOL_VERSION, CALIBRATED_SCORE_VERSION, CALIBRATED_SYSTEM_PROMPT
from toolloop_fix.calibrated_run import run_calibrated_case, calibrated_metrics, validate_config

CORE_FILES = tuple("toolloop_fix/" + name for name in (
    "harness.py", "model_protocol.py", "model_backend.py", "model_run.py",
    "calibrated_protocol.py", "baselines.py", "calibrated_run.py"))
GENERATION_FIELDS = ("raw_text", "prompt_text", "prompt_sha256", "prompt_token_ids",
                     "generated_token_ids", "input_tokens", "output_tokens", "elapsed_seconds",
                     "token_limit_reached", "seed", "effective_generation")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text())


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify_inventory(directory, manifest):
    for section, base in (("inputs", directory / "inputs"), ("outputs", directory)):
        entries = manifest[section]
        for name, expected in entries.items():
            candidate = base / name
            require(not Path(name).is_absolute(), "absolute archived artifact path")
            candidate.resolve().relative_to(base.resolve())
            require(candidate.is_file() and digest(candidate) == expected, "artifact hash mismatch: " + name)
        actual = {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file() and
                  (section == "inputs" or (p.relative_to(base).parts[0] != "inputs" and p.name != "manifest.json"))}
        require(set(entries) == actual, section + " inventory mismatch")


class RecordedBackend:
    """Returns only original backend fields, never saved parsed actions or scores."""
    def __init__(self, generations, case, provenance):
        self.generations = generations
        self.case = case
        self.provenance = provenance
        self.position = 0

    def generate(self, messages, tools, seed):
        require(self.position < len(self.generations), "replay requested an unrecorded generation")
        row = self.generations[self.position]
        self.position += 1
        require(row["turn"] == self.position, "nonconsecutive generation turns")
        require(row["messages"] == messages and row["tools"] == tools, "public dialogue/tools mismatch")
        require(row["seed"] == seed, "case/turn seed mismatch")
        require(row["prompt_sha256"] == hashlib.sha256(row["prompt_text"].encode()).hexdigest(), "prompt hash mismatch")
        require(row["input_tokens"] == len(row["prompt_token_ids"]) and
                row["output_tokens"] == len(row["generated_token_ids"]), "token count mismatch")
        require(row["effective_generation"] == self.provenance["effective_generation"], "generation settings mismatch")
        public = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
        for key in ('"category"', '"hidden"', '"template_group"', '"valid_identifier"'):
            require(key not in row["prompt_text"], "evaluation label leaked into rendered prompt: " + key)
        for key in ("value", "valid_identifier"):
            value = self.case["hidden"][key]
            require(value not in row["prompt_text"] or value in public, "unseen target value in rendered prompt")
        require(self.case["case_id"] not in row["prompt_text"], "case ID leaked into rendered prompt")
        return {key: copy.deepcopy(row[key]) for key in GENERATION_FIELDS}


def verify_accounting(result, trace, generations):
    events = trace["events"]
    require(len(events) == len(generations) == result["turns"], "turn/event count mismatch")
    actual_history = []
    for index, (event, generation) in enumerate(zip(events, generations)):
        parsed = generation["parse"]
        action = parsed["action"] if parsed["valid"] else {
            "type": "invalid_model_output", "raw_text": generation["raw_text"], "parser_error": parsed["error"]}
        require(event["action"] == action, "raw parse was not the proposed action")
        require(event["guard_blocked"] == generation["guard_enforced"], "guard enforcement mismatch")
        if event["guard_blocked"]:
            require(event["response"] is None and not event["executed"] and not event["actual_tool_dispatch"], "blocked proposal has a fabricated tool result")
            require(event["state_before"] == event["state_after"] and
                    event["snapshot_before_sha256"] == event["snapshot_after_sha256"], "blocked proposal changed environment")
            if index + 1 < len(generations):
                before, after = generation["messages"], generations[index + 1]["messages"]
                require(after[:len(before)] == before and len(after) == len(before) + 2, "guard dialogue is not append-only")
                require([item["role"] for item in after[-2:]] == ["assistant", "user"] and
                        after[-1]["content"].startswith("Runtime guard notice (not a tool result): "), "guard feedback impersonates a tool")
        if event["actual_tool_dispatch"]:
            actual_history.append({"action": event["action"], "response": event["response"]})
        if parsed["terminal_form"] == "natural_text":
            require(index == len(events) - 1 and not event["actual_tool_dispatch"] and
                    not event["model_generated_terminal_tool"] and
                    event["event_kind"] == "internal_terminal_lifecycle", "native terminal was repaired or retried")
    require(trace["loop_index_scope"] == "actual_tool_history_only" and
            trace["actual_tool_history"] == actual_history, "executed-loop history includes internal/blocked events")
    counts = {
        "proposal_attempts": sum(e["action"].get("type") not in ("final", "stop") for e in events),
        "actual_tool_attempts": sum(e["actual_tool_dispatch"] for e in events),
        "guard_blocked_proposals": sum(e["guard_blocked"] for e in events),
        "proposed_repeat_count": sum(e["guard"]["blocked"] for e in events),
        "parser_error_turns": sum(not g["parse"]["valid"] for g in generations),
        "input_tokens": sum(g["input_tokens"] for g in generations),
        "output_tokens": sum(g["output_tokens"] for g in generations),
        "generation_seconds": sum(g["elapsed_seconds"] for g in generations),
    }
    require(all(result[key] == value for key, value in counts.items()), "independent counter sum mismatch")
    require(result["tool_attempts"] == counts["proposal_attempts"], "budget omits blocked/invalid proposals")


def audit_run(reference):
    directory = Path(reference["path"])
    require(directory.is_absolute(), "source run path must be absolute")
    require(digest(directory / "manifest.json") == reference["manifest_sha256"], "source manifest pin mismatch")
    manifest = read_json(directory / "manifest.json")
    require(manifest["kind"] == "model_inference" and manifest["status"] == "completed" and
            manifest["exit_code"] == 0 and manifest["evidence_valid"] and not manifest.get("contract_errors"), "source is not a completed valid model record")
    verify_inventory(directory, manifest)
    for name in CORE_FILES:
        require(digest(ROOT / name) == manifest["inputs"][name], "auditor replay code differs from source: " + name)
    config = read_json(directory / "config.json")
    validate_config(config)
    provenance = read_json(directory / "model_provenance.json")
    require(provenance["model"] == config["model"] and provenance["sampling"] == config["sampling"], "model/sampling provenance mismatch")
    require(Path(provenance["runtime"]["executable"]).resolve() == Path(manifest["command"][0]).resolve(), "executed interpreter provenance mismatch")
    require(provenance["model_files_verified"] is True, "model file verification was not recorded")
    for ref in config["external_inputs"]:
        require(digest(directory / "external_inputs" / (ref["name"] + ".json")) == ref["sha256"], "external manifest snapshot mismatch")
        if ref["name"] == "model_manifest":
            require(provenance["model_manifest_sha256"] == ref["sha256"], "model manifest provenance mismatch")
    require(provenance["protocol_version"] == CALIBRATED_PROTOCOL_VERSION and
            provenance["score_version"] == CALIBRATED_SCORE_VERSION and provenance["guard_version"] == GUARD_VERSION,
            "protocol/score/guard provenance mismatch")
    require(provenance["case_seeds"] == config["case_seeds"] and provenance["comparison_arm"] == config["comparison_arm"], "arm/seed provenance mismatch")
    system_prompt = CALIBRATED_SYSTEM_PROMPT + ("\n\n" + prompt_for(config["comparison_arm"]) if prompt_for(config["comparison_arm"]) else "")
    require(provenance["system_prompt_sha256"] == hashlib.sha256(system_prompt.encode()).hexdigest(), "system prompt provenance mismatch")
    cases = {row["case_id"]: row for row in read_rows(directory / "inputs" / config["cases_path"])}
    results, traces = read_rows(directory / "case_results.jsonl"), read_rows(directory / "trajectories.jsonl")
    generations, recorded_events = read_rows(directory / "generations.jsonl"), read_rows(directory / "execution_events.jsonl")
    require([row["case_id"] for row in results] == config["case_ids"] == [row["case_id"] for row in traces], "case selection/order mismatch")
    by_case = collections.defaultdict(list)
    for row in generations:
        by_case[row["case_id"]].append(row)
    require(set(by_case) == set(config["case_ids"]), "generation cases differ from configured cases")
    replayed_generations, replayed_events, replayed_results = [], [], []
    for result, trace in zip(results, traces):
        case = cases[result["case_id"]]
        backend = RecordedBackend(by_case[case["case_id"]], case, provenance)
        replayed, trajectory = run_calibrated_case(case, backend, config,
            emit_generation=replayed_generations.append, emit_event=replayed_events.append)
        require(backend.position == len(backend.generations), "unused generations after terminal/budget")
        require(replayed == result and trajectory == trace, "raw-generation replay result/state mismatch: " + case["case_id"])
        verify_accounting(result, trace, backend.generations)
        replayed_results.append(replayed)
    require(replayed_generations == generations and replayed_events == recorded_events, "saved raw parse/guard/event identity mismatch")
    metrics = read_json(directory / "metrics.json")
    require(calibrated_metrics(replayed_results) == metrics, "saved metrics do not match replayed cases")
    group = metrics["metrics"]
    completable = [row for row in results if row["completable"]]
    for key in ("tool_goal_attainment", "exact_grounded_structured_completion"):
        require(group[key]["denominator"] == len(completable) and
                group[key]["numerator"] == sum(row[key] for row in completable), "completion evidence denominator mismatch")
    unavailable = [row for row in results if not row["completable"]]
    for key in ("stopped_after_public_unavailable", "exact_stop_reason_code", "stop_reason_semantics_unknown"):
        require(group[key]["denominator"] == len(unavailable) and
                group[key]["numerator"] == sum(row[key] for row in unavailable), "unavailable-case denominator mismatch")
    for key in ("turns", "proposal_attempts", "actual_tool_attempts", "guard_blocked_proposals",
                "proposed_repeat_count", "parser_error_turns", "input_tokens", "output_tokens"):
        require(group[key] == sum(row[key] for row in results), "aggregate counter mismatch: " + key)
    return {"run_id": manifest["run_id"], "path": str(directory), "manifest_sha256": reference["manifest_sha256"],
            "arm": config["comparison_arm"], "case_count": len(results), "generation_count": len(generations),
            "metrics": group, "all_cases_replayed": True}, config, provenance, manifest


def audit_runs(config):
    refs = config["run_dirs"]
    require(isinstance(refs, list) and len(refs) == 3, "audit requires a B0/B1/B2 triplet")
    audited = [audit_run(ref) for ref in refs]
    arms = {row[0]["arm"]: row for row in audited}
    require(set(arms) == {"B0", "B1", "B2"}, "exactly one B0/B1/B2 required")
    common = {key: value for key, value in arms["B0"][1].items() if key not in ("comparison_arm", "policy_name")}
    provenance_keys = ("model", "sampling", "runtime", "model_manifest_sha256", "chat_template_sha256",
                       "effective_generation", "actual_model_class", "actual_parameter_device", "actual_parameter_dtype",
                       "checkpoint_loading_info", "case_seeds", "protocol_version", "score_version", "guard_version")
    for arm, (_, arm_config, provenance, manifest) in arms.items():
        require({k: v for k, v in arm_config.items() if k not in ("comparison_arm", "policy_name")} == common,
                "uncontrolled configuration difference for " + arm)
        for key in provenance_keys:
            require(provenance[key] == arms["B0"][2][key], "uncontrolled provenance difference: " + key)
        for name in CORE_FILES + (arm_config["cases_path"],):
            require(manifest["inputs"][name] == arms["B0"][3]["inputs"][name], "uncontrolled code/data difference: " + name)
    require(arms["B0"][2]["system_prompt_sha256"] == arms["B2"][2]["system_prompt_sha256"], "B0/B2 prompt differs")
    return {"schema_version": 1, "evidence_class": "analysis", "accepted": True,
            "audit_scope": "recorded_generation_cpu_replay_no_model_execution",
            "runs": [arms[arm][0] for arm in ("B0", "B1", "B2")],
            "paired_controls_verified": True,
            "limitations": ["No new model generation or tokenizer re-encoding was performed.",
                            "Replay checks implementation/data consistency using the same versioned scorer, not independent scientific validity or cryptographic attestation.",
                            "Natural-language semantics remain ungraded; tool evidence is not general task success."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "calibrated_audit.json"
    if target.exists():
        raise FileExistsError("refusing to overwrite audit evidence")
    try:
        result = audit_runs(read_json(Path(args.config)))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result = {"evidence_class": "analysis", "accepted": False,
                  "error_type": type(exc).__name__, "error": str(exc)}
    with target.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"accepted": result["accepted"], "output": str(target)}, ensure_ascii=False))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
