#!/usr/bin/env python3
"""CPU-only recorded-generation consistency audit of six fixed-prefix cells."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_calibrated_runs import (CORE_FILES as E2E_CORE_FILES,
    RecordedBackend as E2ERecordedBackend, digest, read_json, read_rows, require,
    verify_accounting as verify_fresh_accounting, verify_inventory)
from toolloop_fix.baselines import GUARD_VERSION, prompt_for
from toolloop_fix.calibrated_protocol import (CALIBRATED_PROTOCOL_VERSION,
    CALIBRATED_SCORE_VERSION, CALIBRATED_SYSTEM_PROMPT)
from toolloop_fix.harness import state_hash
from toolloop_fix.prefix_run import (PREFIX_RUNTIME_VERSION, PREFIX_SCHEMA_VERSION,
    TASK_WORDING_VERSION, prepare_prefix, prefix_metrics, run_prefix_case, validate_config)

CORE_FILES = E2E_CORE_FILES + ("toolloop_fix/prefix_run.py",)
CELLS = tuple((wording, arm) for wording in ("legacy", "explicit") for arm in ("B0", "B1", "B2"))


class RecordedBackend(E2ERecordedBackend):
    """Reuse backend-only playback; never return a saved action, guard, or score."""
    def __init__(self, generations, row, provenance):
        count = len(row["prefix_generations"])
        normalized = []
        for index, generation in enumerate(generations, 1):
            require(generation["turn"] == count + index and generation["continuation_turn"] == index,
                    "prefix/continuation generation turn mismatch")
            require(generation["phase"] == "fresh_continuation", "historical generation counted as fresh")
            # The inherited backend checks consecutive fresh turns; output fields are unchanged.
            normalized.append({**generation, "turn": index})
        super().__init__(normalized, row["original_case"], provenance)
        self.prefix_id = row["case_id"]

    def generate(self, messages, tools, seed):
        require(self.position < len(self.generations), "replay requested an unrecorded generation")
        row = self.generations[self.position]
        for field in ("input_tokens", "output_tokens"):
            require(type(row[field]) is int and row[field] >= 0, "invalid fresh token count")
        require(isinstance(row["elapsed_seconds"], (int, float)) and
                math.isfinite(row["elapsed_seconds"]) and row["elapsed_seconds"] >= 0,
                "invalid fresh generation time")
        for key in ('"prefix_role"', '"recovery_eligible"', '"source_provenance"'):
            require(key not in row["prompt_text"], "prefix evaluation label leaked into prompt")
        require(self.prefix_id not in row["prompt_text"], "prefix ID leaked into prompt")
        return super().generate(messages, tools, seed)


def verify_accounting(result, trace, generations, context, row, config):
    count = len(row["prefix_generations"])
    prefix, fresh = trace["events"][:count], trace["events"][count:]
    require(len(prefix) == count and all(e["phase"] == "historical_prefix" for e in prefix),
            "historical prefix event count/phase mismatch")
    require(all(e["phase"] == "fresh_continuation" for e in fresh), "fresh event phase mismatch")
    require([e["turn"] for e in trace["events"]] == list(range(1, count + len(fresh) + 1)),
            "nonconsecutive total event turns")
    require([e["continuation_turn"] for e in fresh] == list(range(1, len(fresh) + 1)),
            "nonconsecutive fresh event turns")
    require(all(e["actual_tool_dispatch"] and e["executed"] and not e["guard_blocked"] for e in prefix),
            "prefix contains an unexecuted or terminal event")
    actual = [{"action": e["action"], "response": e["response"]}
              for e in trace["events"] if e["actual_tool_dispatch"]]
    require(trace["actual_tool_history"] == actual and
            trace["loop_index_scope"] == "actual_tool_history_including_prefix", "actual tool history mismatch")
    # Reuse separate raw/action, guard-without-result, native-terminal and fresh sum checks.
    fresh_result = {**result, "turns": result["continuation_turns"],
        "proposal_attempts": result["continuation_proposal_attempts"],
        "actual_tool_attempts": result["continuation_actual_tool_attempts"],
        "tool_attempts": result["continuation_proposal_attempts"]}
    fresh_trace = {**trace, "events": fresh, "loop_index_scope": "actual_tool_history_only",
        "actual_tool_history": actual[count:]}
    verify_fresh_accounting(fresh_result, fresh_trace, generations)
    expected = {
        "prefix_turns": count, "prefix_tool_attempts": count, "prefix_actual_tool_attempts": count,
        "turns": count + len(generations),
        "proposal_attempts": count + sum(e["action"].get("type") not in ("final", "stop") for e in fresh),
        "actual_tool_attempts": len(actual), "prefix_fresh_generations": 0,
        "remaining_tool_budget_at_start": config["tool_budget"] - count,
        "remaining_turn_budget_at_start": config["turn_budget"] - count,
        "historical_input_tokens": sum(g["input_tokens"] for g in row["prefix_generations"]),
        "historical_output_tokens": sum(g["output_tokens"] for g in row["prefix_generations"]),
        "historical_generation_seconds": sum(g["elapsed_seconds"] for g in row["prefix_generations"]),
        "continuation_input_tokens": sum(g["input_tokens"] for g in generations),
        "continuation_output_tokens": sum(g["output_tokens"] for g in generations),
        "prefix_proposed_repeat_count": sum(e["guard"]["blocked"] for e in prefix),
    }
    require(all(result[key] == value for key, value in expected.items()), "independent prefix/total accounting mismatch")
    require(result["tool_attempts"] == result["proposal_attempts"] <= config["tool_budget"] and
            result["turns"] <= config["turn_budget"], "prefix budget exceeded or uncharged proposals")
    require(context["remaining_tool_budget"] == config["tool_budget"] - count and
            context["remaining_turn_budget"] == config["turn_budget"] - count, "remaining prefix budget mismatch")
    require(result["prefix_snapshot_sha256"] == state_hash(trace["prefix_snapshot"]) ==
            state_hash(row["prefix_snapshot"]), "prefix snapshot identity mismatch")
    require(result["prefix_public_history_sha256"] == state_hash(row["prefix_snapshot"]["history"]),
            "prefix public history identity mismatch")
    inherited = [flag for flag in trace["loop_flags"] if flag["index"] < count]
    continuation = [flag for flag in trace["loop_flags"] if flag["index"] >= count]
    require(trace["prefix_loop_flags"] == inherited and trace["continuation_loop_flags"] == continuation and
            result["has_inherited_loop"] == bool(inherited) and result["has_invalid_loop"] == bool(continuation),
            "inherited/new loop attribution mismatch")
    require(result["new_goal_evidence"] == (result["tool_goal_attainment"] and not result["goal_at_prefix"]),
            "inherited goal counted as new evidence")
    require(result["recovery_eligible"] == (row["role"] == "recovery" and
            result["completable"] and not result["goal_at_prefix"]), "recovery eligibility mismatch")
    if row["role"] == "normal_completion_control":
        require(result["goal_at_prefix"] and not result["new_goal_evidence"] and not result["recovery_eligible"],
                "normal completion control counted as recovery")


def verify_metrics(metrics, results):
    group = metrics["metrics"]
    def ratio(name, numerator, denominator, table=group):
        require(table[name] == {"numerator": numerator, "denominator": denominator,
                "value": numerator / denominator if denominator else None}, "metric denominator/value mismatch: " + name)
    recovery = [r for r in results if r["recovery_eligible"]]
    ratio("new_goal_evidence_recovery", sum(r["new_goal_evidence"] for r in recovery), len(recovery))
    ratio("exact_structured_completion_recovery", sum(r["new_goal_evidence"] and
          r["exact_grounded_structured_completion"] for r in recovery), len(recovery))
    ratio("prefix_goal_already_attained", sum(r["goal_at_prefix"] for r in results), len(results))
    for role in ("normal_completion_control", "unavailable_stop_control"):
        subset = [r for r in results if r["prefix_role"] == role]
        require(group[role]["case_count"] == len(subset), "control denominator mismatch")
        for key, field in (("exact_structured_completion", "exact_grounded_structured_completion"),
                           ("stopped_after_public_unavailable", "stopped_after_public_unavailable"),
                           ("natural_termination_semantics_unknown", "natural_termination_semantics_unknown")):
            ratio(key, sum(r[field] for r in subset), len(subset), group[role])
    for key in ("tool_goal_attainment", "exact_grounded_structured_completion"):
        subset = [r for r in results if r["completable"]]
        ratio(key, sum(r[key] for r in subset), len(subset))
    for key in ("stopped_after_public_unavailable", "exact_stop_reason_code", "stop_reason_semantics_unknown"):
        subset = [r for r in results if not r["completable"]]
        ratio(key, sum(r[key] for r in subset), len(subset))
    for key in ("turns", "proposal_attempts", "actual_tool_attempts", "guard_blocked_proposals",
                "proposed_repeat_count", "parser_error_turns", "input_tokens", "output_tokens", "generation_seconds",
                "prefix_turns", "prefix_tool_attempts", "continuation_turns", "continuation_proposal_attempts",
                "continuation_actual_tool_attempts", "historical_input_tokens", "historical_output_tokens",
                "historical_generation_seconds", "prefix_proposed_repeat_count"):
        require(group[key] == sum(r[key] for r in results), "aggregate counter mismatch: " + key)
    ratio("parser_error_turn_rate", group["parser_error_turns"], group["continuation_turns"])
    ratio("proposed_repeat_rate", group["proposed_repeat_count"], group["continuation_proposal_attempts"])


def audit_run(reference):
    directory = Path(reference["path"])
    require(directory.is_absolute(), "source run path must be absolute")
    require(digest(directory / "manifest.json") == reference["manifest_sha256"], "source manifest pin mismatch")
    manifest = read_json(directory / "manifest.json")
    require(manifest["kind"] == "model_inference" and manifest["status"] == "completed" and
            manifest["exit_code"] == 0 and manifest["evidence_valid"] is True and
            not manifest.get("contract_errors"), "source is not a completed valid model record")
    verify_inventory(directory, manifest)
    require(digest(directory / "config.json") == manifest["config_sha256"], "config hash mismatch")
    for name in CORE_FILES:
        require(digest(ROOT / name) == manifest["inputs"][name], "auditor replay code differs from source: " + name)
    config, provenance = read_json(directory / "config.json"), read_json(directory / "model_provenance.json")
    validate_config(config)
    require(provenance["evidence_class"] == "model_inference" and provenance["model"] == config["model"] and
            provenance["sampling"] == config["sampling"], "model/sampling provenance mismatch")
    require(Path(provenance["runtime"]["executable"]).resolve() == Path(manifest["command"][0]).resolve(),
            "executed interpreter provenance mismatch")
    require(provenance["model_files_verified"] is True, "model file verification not recorded")
    for ref in config["external_inputs"]:
        require(digest(directory / "external_inputs" / (ref["name"] + ".json")) == ref["sha256"],
                "external manifest snapshot mismatch")
        if ref["name"] == "model_manifest":
            require(provenance["model_manifest_sha256"] == ref["sha256"], "model manifest provenance mismatch")
    versions = {"protocol_version": CALIBRATED_PROTOCOL_VERSION, "score_version": CALIBRATED_SCORE_VERSION,
        "guard_version": GUARD_VERSION, "prefix_runtime_version": PREFIX_RUNTIME_VERSION,
        "prefix_schema_version": PREFIX_SCHEMA_VERSION, "task_wording_version": TASK_WORDING_VERSION,
        "mode": "fixed_failure_prefix", "case_seeds": config["case_seeds"],
        "comparison_arm": config["comparison_arm"], "wording_profile": config["wording_profile"]}
    require(all(provenance[k] == v for k, v in versions.items()), "prefix/version/seed provenance mismatch")
    system = CALIBRATED_SYSTEM_PROMPT + ("\n\n" + prompt_for(config["comparison_arm"]) if prompt_for(config["comparison_arm"]) else "")
    require(provenance["system_prompt_sha256"] == hashlib.sha256(system.encode()).hexdigest(), "system prompt mismatch")
    data_path = directory / "inputs" / config["cases_path"]
    data_path.resolve().relative_to((directory / "inputs").resolve())
    require(digest(data_path) == provenance["prefix_dataset_sha256"], "prefix dataset provenance mismatch")
    rows = read_rows(data_path)
    by_id = {row["case_id"]: row for row in rows}
    require(len(rows) == len(by_id), "duplicate prefix IDs")
    selected = [by_id[key] for key in config["case_ids"]]
    require(provenance["prefix_source_rows"] == {row["case_id"]: state_hash(row) for row in selected},
            "prefix row provenance mismatch")
    sources = {}
    for row in selected:
        source = Path(row["provenance"]["source_run"])
        if str(source) not in sources:
            source_manifest = read_json(source / "manifest.json")
            verify_inventory(source, source_manifest)
            sources[str(source)] = row["provenance"]["source_manifest_sha256"]
    results, traces = read_rows(directory / "case_results.jsonl"), read_rows(directory / "trajectories.jsonl")
    contexts = read_rows(directory / "prefix_contexts.jsonl")
    generations, events = read_rows(directory / "generations.jsonl"), read_rows(directory / "execution_events.jsonl")
    for records in (results, traces, contexts):
        require([r["case_id"] for r in records] == config["case_ids"], "prefix selection/order mismatch")
    by_case = collections.defaultdict(list)
    for generation in generations:
        by_case[generation["case_id"]].append(generation)
    require(set(by_case) <= set(config["case_ids"]), "unconfigured continuation case")
    emitted_generations, emitted_events, emitted_contexts, replayed_results, dialogues = [], [], [], [], []
    for row, result, trace, context in zip(selected, results, traces, contexts):
        prepared = prepare_prefix(row, config)
        require(prepared["context"] == context, "saved prefix context differs from authentic replay")
        dialogues.append({"messages": prepared["messages"], "tools": prepared["tools"]})
        raw = by_case[row["case_id"]]
        backend = RecordedBackend(raw, row, provenance)
        replayed, trajectory = run_prefix_case(row, backend, config, prepared=prepared,
            emit_generation=emitted_generations.append, emit_event=emitted_events.append,
            emit_prefix_context=emitted_contexts.append)
        require(backend.position == len(raw), "unused generation after terminal/budget")
        require(replayed == result and trajectory == trace, "raw replay result/state mismatch: " + row["case_id"])
        verify_accounting(result, trace, raw, context, row, config)
        replayed_results.append(replayed)
    require(emitted_generations == generations and emitted_events == events and emitted_contexts == contexts,
            "saved parse/guard/event/context mismatch")
    metrics = read_json(directory / "metrics.json")
    require(prefix_metrics(replayed_results) == metrics, "saved metrics differ from replay")
    verify_metrics(metrics, results)
    summary = {"run_id": manifest["run_id"], "path": str(directory), "manifest_sha256": reference["manifest_sha256"],
        "wording_profile": config["wording_profile"], "arm": config["comparison_arm"],
        "case_count": len(results), "generation_count": len(generations), "metrics": metrics["metrics"],
        "historical_source_manifests": sources, "all_cases_replayed": True}
    return summary, config, provenance, manifest, {"contexts": contexts, "dialogues": dialogues}


def audit_runs(config):
    refs = config["run_dirs"]
    require(isinstance(refs, list) and len(refs) == 6, "audit requires six wording/arm cells")
    audited = [audit_run(ref) for ref in refs]
    cells = {(r[0]["wording_profile"], r[0]["arm"]): r for r in audited}
    require(len(cells) == 6 and set(cells) == set(CELLS), "exactly one of each wording/arm cell required")
    base = cells[("legacy", "B0")]
    config_exceptions = {"comparison_arm", "policy_name", "wording_profile"}
    provenance_exceptions = {"comparison_arm", "system_prompt_sha256", "wording_profile"}
    context_exceptions = {"comparison_arm", "wording_profile", "rebuilt_dialogue_sha256", "initial_user_content"}
    def without(value, keys):
        return {k: v for k, v in value.items() if k not in keys}
    for cell, current in cells.items():
        require(without(current[1], config_exceptions) == without(base[1], config_exceptions),
                "uncontrolled six-cell configuration difference")
        require(without(current[2], provenance_exceptions) == without(base[2], provenance_exceptions),
                "uncontrolled six-cell model/runtime provenance difference")
        for name in CORE_FILES + (current[1]["cases_path"],):
            require(current[3]["inputs"][name] == base[3]["inputs"][name], "uncontrolled code/data difference: " + name)
        for i, context in enumerate(current[4]["contexts"]):
            require(without(context, context_exceptions) == without(base[4]["contexts"][i], context_exceptions),
                    "prefix source/snapshot/remaining budgets differ across cells")
            dialogue, baseline = current[4]["dialogues"][i], base[4]["dialogues"][i]
            require(dialogue["tools"] == baseline["tools"] and dialogue["messages"][2:] == baseline["messages"][2:],
                    "historical dialogue/tools altered across cells")
            same_wording = cells[(cell[0], "B0")][4]["dialogues"][i]
            require(dialogue["messages"][1:] == same_wording["messages"][1:], "arm changed public task/history")
            if cell[1] in ("B0", "B2"):
                require(dialogue["messages"][0] == baseline["messages"][0], "B0/B2 system prompts differ")
    preflight = verify_preflight(config["preflight_context_run"], audited) if config.get("preflight_context_run") else None
    return {"schema_version": 1, "evidence_class": "analysis", "accepted": True,
        "audit_scope": "recorded_generation_cpu_prefix_replay_no_model_execution",
        "runs": [cells[cell][0] for cell in CELLS], "paired_controls_verified": True,
        "case_runs_replayed": sum(r[0]["case_count"] for r in audited),
        "fresh_generations_replayed": sum(r[0]["generation_count"] for r in audited),
        "preflight_context_verification": preflight,
        "limitations": ["Same versioned parser/guard/environment/scorer replay establishes consistency, not independent scientific semantic truth or cryptographic attestation.",
                        "No model loading, new generation, tokenizer re-encoding, training, or full weight rehash was performed.",
                        "Natural terminal meanings remain unknown; inherited goal evidence is not new recovery, and structured completion is not a universal answer correctness measure.",
                        "Selected development prefixes and paired continuations are not independent samples or natural failure rates."]}


def verify_preflight(reference, audited):
    """Bind fresh first prompts to a separately archived tokenizer context gate."""
    path = Path(reference["path"])
    require(path.is_absolute() and digest(path / "manifest.json") == reference["manifest_sha256"],
            "preflight manifest pin mismatch")
    manifest = read_json(path / "manifest.json")
    require(manifest["kind"] == "analysis" and manifest["status"] == "completed" and
            manifest["exit_code"] == 0 and manifest["evidence_valid"] is True and
            not manifest.get("contract_errors"), "preflight is not a completed valid analysis record")
    verify_inventory(path, manifest)
    for name in CORE_FILES:
        require(manifest["inputs"][name] == audited[0][3]["inputs"][name], "preflight/runtime code mismatch")
    rows = read_rows(path / "preflight_contexts.jsonl")
    by_key = {(r["wording_profile"], r["comparison_arm"], r["case_id"]): r for r in rows}
    expected = {(r[0]["wording_profile"], r[0]["arm"], case_id) for r in audited for case_id in r[1]["case_ids"]}
    require(len(rows) == len(by_key) and set(by_key) == expected, "preflight cell/prefix coverage mismatch")
    checked = no_fresh = 0
    for summary, cfg, provenance, _, controls in audited:
        first = {}
        for generation in read_rows(Path(summary["path"]) / "generations.jsonl"):
            first.setdefault(generation["case_id"], generation)
        for context, dialogue in zip(controls["contexts"], controls["dialogues"]):
            gate = by_key[(cfg["wording_profile"], cfg["comparison_arm"], context["case_id"])]
            require(gate["evidence_class"] == "analysis" and gate["new_model_generation_performed"] is False,
                    "preflight context is mislabeled as generation")
            require(gate["prefix_context"] == context and
                    gate["messages_sha256"] == state_hash(dialogue["messages"]) and
                    gate["tools_sha256"] == state_hash(dialogue["tools"]) and
                    gate["system_prompt_sha256"] == provenance["system_prompt_sha256"], "preflight public context mismatch")
            require(gate["first_continuation_seed"] == cfg["case_seeds"][context["case_id"]] + context["prefix_turns"],
                    "preflight first seed mismatch")
            if context["case_id"] not in first:
                no_fresh += 1
                continue
            generation = first[context["case_id"]]
            require(generation["prompt_sha256"] == gate["rendered_prompt_sha256"] and
                    generation["input_tokens"] == gate["prompt_tokens"] and
                    generation["seed"] == gate["first_continuation_seed"], "first generation differs from tokenizer preflight")
            checked += 1
    return {"path": str(path), "manifest_sha256": reference["manifest_sha256"],
            "contexts_verified": len(rows), "first_generation_prompt_seed_matches": checked,
            "contexts_without_fresh_generation": no_fresh, "tokenizer_reencoded_by_auditor": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "prefix_audit.json"
    if target.exists():
        raise FileExistsError("refusing to overwrite audit evidence")
    try:
        result = audit_runs(read_json(Path(args.config)))
    except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
        result = {"evidence_class": "analysis", "accepted": False,
                  "error_type": type(exc).__name__, "error": str(exc)}
    with target.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"accepted": result["accepted"], "output": str(target)}, ensure_ascii=False))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
