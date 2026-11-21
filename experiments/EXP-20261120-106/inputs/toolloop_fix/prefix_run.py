"""Authentic fixed-prefix continuation, isolated from the existing E2E runner."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

from .baselines import GUARD_VERSION, guard_action, prompt_for
from .calibrated_protocol import (CALIBRATED_PROTOCOL_VERSION, CALIBRATED_SCORE_VERSION,
    messages_for_calibrated_view, parse_calibrated_output, score_calibrated_episode)
from .calibrated_run import (append_feedback, calibrated_metrics, public_tool_view,
                            validate_config as validate_calibrated_config)
from .harness import Environment, detect_loops, ratio, state_hash
from .model_backend import TransformersBackend, sha256_file
from .model_protocol import tool_schemas
from .model_run import ARTIFACTS

PREFIX_SCHEMA_VERSION = "authentic_tool_prefix_v1"
PREFIX_RUNTIME_VERSION = "verified_prefix_continuation_v1"
TASK_WORDING_VERSION = "read_report_literal_target_v1"
EXPLICIT_TASK_PREFIX = (
    "Retrieve and report the result value and identifier for the resource described by this request. "
    "This is a read-and-report task: do not create, modify, or execute the resource. "
    "The target field is a literal lookup label, not an additional instruction about recency, completeness, "
    "or further processing. Completion means reporting the value and identifier from a successful tool "
    "response, not merely repeating the request or reporting an error. Request: "
)
ROLES = {"recovery", "normal_completion_control", "unavailable_stop_control"}


def validate_config(config):
    if config["mode"] != "fixed_failure_prefix":
        raise ValueError("prefix runner requires fixed_failure_prefix mode")
    # Reuse the existing model/arm/seed validation without altering its E2E API.
    common = {**config, "mode": "end_to_end"}
    validate_calibrated_config(common)
    if config["wording_profile"] not in {"legacy", "explicit"}:
        raise ValueError("wording_profile must be legacy or explicit")
    if config["task_wording_version"] != TASK_WORDING_VERSION:
        raise ValueError("task wording version mismatch")
    if config["prefix_schema_version"] != PREFIX_SCHEMA_VERSION:
        raise ValueError("prefix schema version mismatch")


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _verify_source(row):
    provenance = row["provenance"]
    source = Path(provenance["source_run"]).resolve()
    manifest_path = source / "manifest.json"
    if sha256_file(manifest_path) != provenance["source_manifest_sha256"]:
        raise ValueError("source manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "completed" or manifest.get("evidence_valid") is not True:
        raise ValueError("source run is not completed valid evidence")
    if manifest.get("kind") != "model_inference":
        raise ValueError("source prefix must originate in model_inference")
    harness_key = "toolloop_fix/harness.py"
    historical_harness = manifest["inputs"][harness_key]
    if (sha256_file(source / "inputs" / harness_key) != historical_harness or
            sha256_file(Path(__file__).with_name("harness.py")) != historical_harness):
        raise ValueError("source and current environment code must match")
    for name, pinned_key in (("generations.jsonl", "source_generations_sha256"),
                             ("trajectories.jsonl", "source_trajectories_sha256")):
        actual = sha256_file(source / name)
        if actual != provenance[pinned_key] or actual != manifest["outputs"][name]:
            raise ValueError("source artifact hash mismatch: " + name)
    model_provenance = source / "model_provenance.json"
    if sha256_file(model_provenance) != manifest["outputs"]["model_provenance.json"]:
        raise ValueError("source model provenance hash mismatch")
    if json.loads(model_provenance.read_text()).get("evidence_class") != "model_inference":
        raise ValueError("source model provenance has the wrong evidence class")
    cases_path = Path(provenance["source_cases_path"])
    cases_path = cases_path.resolve() if cases_path.is_absolute() else (source / cases_path).resolve()
    relative_cases = str(cases_path.relative_to(source / "inputs"))
    actual_cases_hash = sha256_file(cases_path)
    if (actual_cases_hash != provenance["source_cases_sha256"] or
            actual_cases_hash != manifest["inputs"][relative_cases]):
        raise ValueError("source fixture file hash mismatch")
    source_case_id = provenance["source_case_id"]
    matching_cases = [case for case in _jsonl(cases_path) if case["case_id"] == source_case_id]
    if len(matching_cases) != 1 or matching_cases[0] != row["original_case"]:
        raise ValueError("original case differs from pinned source fixture")
    if state_hash(row["original_case"]) != provenance["source_case_sha256"]:
        raise ValueError("source case object hash mismatch")
    generations = _jsonl(source / "generations.jsonl")
    trajectories = _jsonl(source / "trajectories.jsonl")
    trajectory_index = provenance["source_trajectory_line_1based"]
    if type(trajectory_index) is not int or not 1 <= trajectory_index <= len(trajectories):
        raise ValueError("invalid source trajectory line")
    trajectory = trajectories[trajectory_index - 1]
    if trajectory["case_id"] != source_case_id or trajectory.get("evidence_class") != "model_inference":
        raise ValueError("source trajectory belongs to another case")
    count = provenance["source_prefix_turns"]
    if type(count) is not int or count <= 0:
        raise ValueError("a nonempty source prefix is required")
    indices = provenance["source_event_indices_1based"]
    lines = provenance["source_generation_lines_1based"]
    if indices != list(range(1, count + 1)) or len(lines) != count or len(set(lines)) != count:
        raise ValueError("source prefix must cover the contiguous initial executed events")
    if count > len(trajectory["events"]):
        raise ValueError("source prefix exceeds trajectory")
    if any(type(line) is not int or not 1 <= line <= len(generations) for line in lines):
        raise ValueError("invalid source generation line")
    selected = [generations[line - 1] for line in lines]
    if selected != row["prefix_generations"] or trajectory["events"][:count] != row["prefix_events"]:
        raise ValueError("embedded prefix differs from pinned source records")
    for turn, generation in enumerate(selected, 1):
        if (generation["case_id"] != source_case_id or generation["turn"] != turn or
                generation.get("evidence_class") != "model_inference"):
            raise ValueError("source generation case/turn is not the initial prefix")
    return count


def prepare_prefix(row, config):
    """Verify source files and replay raw calls; never trust a supplied snapshot."""
    validate_config(config)
    if row["prefix_schema_version"] != PREFIX_SCHEMA_VERSION or row["role"] not in ROLES:
        raise ValueError("invalid prefix schema or role")
    count = _verify_source(row)
    if len(row["prefix_events"]) != count or len(row["prefix_generations"]) != count:
        raise ValueError("prefix record count mismatch")
    if count > config["tool_budget"] or count > config["turn_budget"]:
        raise ValueError("prefix exceeds total tool/turn budget")
    case = row["original_case"]
    env = Environment(case)
    initial = env.snapshot()
    messages = messages_for_calibrated_view(env.public_view(), [], prompt_for(config["comparison_arm"]))
    if config["wording_profile"] == "explicit":
        messages[1]["content"] = EXPLICIT_TASK_PREFIX + json.dumps(env.public_view()["request"], ensure_ascii=False)
    elif config["wording_profile"] != "legacy":
        raise ValueError("unknown wording profile")
    decisions, replayed = [], []
    historical_input = historical_output = 0
    historical_seconds = 0.0
    inherited_proposals = 0
    for turn, (generation, saved_event) in enumerate(zip(row["prefix_generations"], row["prefix_events"]), 1):
        decision = parse_calibrated_output(generation["raw_text"], generation["token_limit_reached"])
        if (not decision["valid"] or decision["is_terminal"] or decision["action"].get("type") != "tool" or
                saved_event.get("guard_blocked", False) or saved_event.get("executed") is False or
                saved_event.get("actual_tool_dispatch") is False):
            raise ValueError("prefix must contain only valid actually executed tool calls")
        if decision["action"] != saved_event["action"] or generation["parse"]["action"] != decision["action"]:
            raise ValueError("raw source generation/action mismatch")
        before = env.snapshot()
        guard = guard_action(public_tool_view(env), decision["action"])
        inherited_proposals += int(guard["blocked"])
        actual = env.execute(decision["action"])
        for key in ("action", "response", "state_before", "state_after"):
            if actual[key] != saved_event[key]:
                raise ValueError("source tool replay mismatch: " + key)
        for key, value in (("snapshot_before_sha256", state_hash(before)),
                           ("snapshot_after_sha256", state_hash(env.snapshot()))):
            if key in saved_event and saved_event[key] != value:
                raise ValueError("source snapshot replay mismatch: " + key)
        event = {**actual, "phase": "historical_prefix", "turn": turn,
                 "guard_blocked": False, "executed": True, "actual_tool_dispatch": True,
                 "event_kind": "replayed_historical_tool_response", "guard": guard,
                 "snapshot_before_sha256": state_hash(before), "snapshot_after_sha256": state_hash(env.snapshot())}
        append_feedback(messages, decision, event, turn)
        decisions.append(decision)
        replayed.append(event)
        for field in ("input_tokens", "output_tokens"):
            if type(generation[field]) is not int or generation[field] < 0:
                raise ValueError("invalid historical token count")
        if "generated_token_ids" in generation and generation["output_tokens"] != len(generation["generated_token_ids"]):
            raise ValueError("historical output-token accounting mismatch")
        if "prompt_token_ids" in generation and generation["input_tokens"] != len(generation["prompt_token_ids"]):
            raise ValueError("historical input-token accounting mismatch")
        historical_input += generation["input_tokens"]
        historical_output += generation["output_tokens"]
        historical_seconds += generation["elapsed_seconds"]
    snapshot = env.snapshot()
    if snapshot != row["prefix_snapshot"]:
        raise ValueError("supplied prefix snapshot differs from actual replay")
    prefix_score = score_calibrated_episode(case, snapshot, decisions, False)
    goal = prefix_score["tool_goal_attainment"]
    if row["role"] == "recovery" and (goal or not prefix_score["completable"]):
        raise ValueError("recovery prefix must be completable and not already at goal")
    if row["role"] == "normal_completion_control" and not goal:
        raise ValueError("normal completion control must already have goal evidence")
    if row["role"] == "unavailable_stop_control" and not any(
            event["response"].get("code") == "RESOURCE_UNAVAILABLE" and
            event["response"].get("retryable") is False for event in snapshot["history"]):
        raise ValueError("unavailable control lacks public unavailable evidence")
    context = {"evidence_class": "model_inference", "case_id": row["case_id"],
        "original_case_id": case["case_id"], "role": row["role"],
        "prefix_schema_version": PREFIX_SCHEMA_VERSION, "prefix_runtime_version": PREFIX_RUNTIME_VERSION,
        "source_provenance": copy.deepcopy(row["provenance"]), "prefix_row_sha256": state_hash(row),
        "prefix_turns": count, "prefix_tool_attempts": count,
        "prefix_snapshot_sha256": state_hash(snapshot),
        "prefix_public_history_sha256": state_hash(env.public_view()["history"]),
        "rebuilt_dialogue_sha256": state_hash(messages),
        "wording_profile": config["wording_profile"], "task_wording_version": TASK_WORDING_VERSION,
        "comparison_arm": config["comparison_arm"], "source_system_prompt_reused": False,
        "goal_at_prefix": goal, "historical_input_tokens": historical_input,
        "historical_output_tokens": historical_output, "historical_generation_seconds": historical_seconds,
        "prefix_fresh_generations": 0, "prefix_proposed_repeat_count": inherited_proposals,
        "remaining_tool_budget": config["tool_budget"] - count,
        "remaining_turn_budget": config["turn_budget"] - count,
        "total_tool_budget": config["tool_budget"], "total_turn_budget": config["turn_budget"],
        "actual_prefix_snapshot": snapshot, "replayed_prefix_events": replayed,
        "initial_user_content": messages[1]["content"]}
    return {"row": row, "env": env, "initial_snapshot": initial, "messages": messages,
            "tools": tool_schemas(env.public_view()), "decisions": decisions, "context": context}


def run_prefix_case(row, backend, config, emit_generation=None, emit_event=None, emit_partial=None,
                    emit_prefix_context=None, prepared=None):
    prepared = prepared if prepared is not None else prepare_prefix(row, config)
    context = prepared["context"]
    if (state_hash(row) != context["prefix_row_sha256"] or
            config["comparison_arm"] != context["comparison_arm"] or
            config["wording_profile"] != context["wording_profile"] or
            config["tool_budget"] != context["total_tool_budget"] or
            config["turn_budget"] != context["total_turn_budget"]):
        raise ValueError("prepared prefix belongs to a different row or condition")
    env = Environment(row["original_case"])
    env.restore(copy.deepcopy(context["actual_prefix_snapshot"]))
    messages, tools = copy.deepcopy(prepared["messages"]), copy.deepcopy(prepared["tools"])
    decisions = copy.deepcopy(prepared["decisions"])
    count = context["prefix_turns"]
    traces = copy.deepcopy(context["replayed_prefix_events"])
    attempts = turns = actual_tools = count
    blocks = repeats = parser_errors = input_tokens = output_tokens = continuation_turns = 0
    seconds = 0.0
    exhausted = output_limit = False
    case = row["original_case"]
    identity = {"case_id": row["case_id"], "original_case_id": case["case_id"],
        "domain": case["domain"], "category": case["category"], "template_group": case["template_group"],
        "prefix_role": row["role"], "policy": config["policy_name"], "comparison_arm": config["comparison_arm"],
        "wording_profile": config["wording_profile"], "task_wording_version": TASK_WORDING_VERSION,
        "mode": "fixed_failure_prefix", "evidence_class": "model_inference",
        "protocol_version": CALIBRATED_PROTOCOL_VERSION, "prefix_runtime_version": PREFIX_RUNTIME_VERSION,
        "case_seed": config["case_seeds"][row["case_id"]]}
    if emit_prefix_context:
        emit_prefix_context(copy.deepcopy(context))
    while env.snapshot()["terminal"] is None:
        if attempts >= config["tool_budget"] or turns >= config["turn_budget"]:
            exhausted = True
            break
        seed = identity["case_seed"] + turns
        try:
            generation = backend.generate(copy.deepcopy(messages), copy.deepcopy(tools), seed)
        except Exception as exc:
            if emit_generation:
                emit_generation({**identity, "turn": turns + 1, "continuation_turn": continuation_turns + 1,
                    "messages": copy.deepcopy(messages), "tools": tools, "seed": seed,
                    "raw_text": None, "input_tokens": None, "output_tokens": None,
                    "generation_error": {"type": type(exc).__name__, "message": str(exc)}})
            if emit_partial:
                emit_partial({**identity, "state": env.snapshot(), "events": traces,
                              "tool_attempts": attempts, "turns": turns, "status": "generation_failed"})
            raise
        decision = parse_calibrated_output(generation["raw_text"], generation["token_limit_reached"])
        action = decision["action"] if decision["valid"] else {
            "type": "invalid_model_output", "raw_text": generation["raw_text"], "parser_error": decision["error"]}
        guard = guard_action(public_tool_view(env), action)
        blocked = config["comparison_arm"] == "B2" and guard["blocked"]
        if emit_generation:
            emit_generation({**identity, "turn": turns + 1, "continuation_turn": continuation_turns + 1,
                "phase": "fresh_continuation", "messages": copy.deepcopy(messages), "tools": tools,
                **generation, "parse": copy.deepcopy(decision), "guard_observation": guard, "guard_enforced": blocked})
        turns += 1
        continuation_turns += 1
        attempts += int(action.get("type") not in {"final", "stop"})
        dispatch = not blocked and action.get("type") == "tool"
        actual_tools += int(dispatch)
        blocks += int(blocked)
        repeats += int(guard["blocked"])
        parser_errors += int(not decision["valid"])
        input_tokens += generation["input_tokens"]
        output_tokens += generation["output_tokens"]
        seconds += generation["elapsed_seconds"]
        before = env.snapshot()
        if blocked:
            event = {"action": copy.deepcopy(action), "response": None,
                "state_before": state_hash(before["state"]), "state_after": state_hash(before["state"])}
            kind = "guard_blocked_proposal"
        else:
            event = env.execute(action)
            kind = ("actual_tool_response" if dispatch else
                    "internal_terminal_lifecycle" if decision["is_terminal"] else "internal_protocol_rejection")
        event.update(phase="fresh_continuation", turn=turns, continuation_turn=continuation_turns,
            event_kind=kind, executed=not blocked, actual_tool_dispatch=dispatch,
            model_generated_terminal_tool=bool(decision["is_terminal"] and decision["call"] is not None),
            guard_blocked=blocked, guard=guard, parser_error=decision["error"],
            snapshot_before_sha256=state_hash(before), snapshot_after_sha256=state_hash(env.snapshot()))
        traces.append(event)
        decisions.append(decision)
        if emit_event:
            emit_event({**identity, **event})
        if emit_partial:
            emit_partial({**identity, "state": env.snapshot(), "events": traces,
                          "tool_attempts": attempts, "turns": turns, "status": "running"})
        append_feedback(messages, decision, event, turns)
        if generation["token_limit_reached"]:
            exhausted = output_limit = True
            break
    outcome = score_calibrated_episode(case, env.snapshot(), decisions, exhausted)
    all_flags = detect_loops(public_tool_view(env)["history"])
    inherited = [flag for flag in all_flags if flag["index"] < count]
    new_flags = [flag for flag in all_flags if flag["index"] >= count]
    new_goal = outcome["tool_goal_attainment"] and not context["goal_at_prefix"]
    result = {**identity, **outcome, "prefix_turns": count, "prefix_tool_attempts": count,
        "prefix_actual_tool_attempts": count, "turns": turns, "continuation_turns": continuation_turns,
        "tool_attempts": attempts, "proposal_attempts": attempts, "continuation_proposal_attempts": attempts - count,
        "actual_tool_attempts": actual_tools, "continuation_actual_tool_attempts": actual_tools - count,
        "prefix_snapshot_sha256": context["prefix_snapshot_sha256"],
        "prefix_public_history_sha256": context["prefix_public_history_sha256"],
        "prefix_row_sha256": context["prefix_row_sha256"],
        "remaining_tool_budget_at_start": context["remaining_tool_budget"],
        "remaining_turn_budget_at_start": context["remaining_turn_budget"],
        "goal_at_prefix": context["goal_at_prefix"], "new_goal_evidence": bool(new_goal),
        "recovery_eligible": row["role"] == "recovery" and not context["goal_at_prefix"] and outcome["completable"],
        "guard_blocked_proposals": blocks, "proposed_repeat_count": repeats,
        "prefix_proposed_repeat_count": context["prefix_proposed_repeat_count"],
        "has_inherited_loop": bool(inherited), "has_invalid_loop": bool(new_flags),
        "loop_types": sorted({flag["type"] for flag in new_flags}),
        "parser_error_turns": parser_errors, "output_token_limit_reached": output_limit,
        "historical_input_tokens": context["historical_input_tokens"],
        "historical_output_tokens": context["historical_output_tokens"],
        "historical_generation_seconds": context["historical_generation_seconds"],
        "input_tokens": input_tokens, "output_tokens": output_tokens, "generation_seconds": seconds,
        "continuation_input_tokens": input_tokens, "continuation_output_tokens": output_tokens,
        "prefix_fresh_generations": 0,
        "cost_scope": "input/output_tokens and generation_seconds are fresh continuation only; historical prefix separately reported",
        "budget_scope": "total prefix plus all continuation proposals, including blocked and invalid; terminal excluded from tool budget"}
    trajectory = {**identity, "public_initial_request": case["request"],
        "initial_user_content": context["initial_user_content"], "source_provenance": row["provenance"],
        "initial_snapshot": prepared["initial_snapshot"], "prefix_snapshot": context["actual_prefix_snapshot"],
        "prefix_snapshot_sha256": context["prefix_snapshot_sha256"],
        "prefix_public_history_sha256": context["prefix_public_history_sha256"],
        "events": traces, "loop_flags": all_flags, "prefix_loop_flags": inherited,
        "continuation_loop_flags": new_flags, "loop_index_scope": "actual_tool_history_including_prefix",
        "actual_tool_history": public_tool_view(env)["history"], "final_snapshot": env.snapshot()}
    return result, trajectory


def prefix_metrics(results):
    metrics = calibrated_metrics(results)
    group = metrics["metrics"]
    recovery = [row for row in results if row["recovery_eligible"]]
    group["new_goal_evidence_recovery"] = ratio(sum(row["new_goal_evidence"] for row in recovery), len(recovery))
    group["exact_structured_completion_recovery"] = ratio(
        sum(row["exact_grounded_structured_completion"] and row["new_goal_evidence"] for row in recovery), len(recovery))
    group["prefix_goal_already_attained"] = ratio(sum(row["goal_at_prefix"] for row in results), len(results))
    for role in ("normal_completion_control", "unavailable_stop_control"):
        rows = [row for row in results if row["prefix_role"] == role]
        group[role] = {"case_count": len(rows),
            "exact_structured_completion": ratio(sum(row["exact_grounded_structured_completion"] for row in rows), len(rows)),
            "stopped_after_public_unavailable": ratio(sum(row["stopped_after_public_unavailable"] for row in rows), len(rows)),
            "natural_termination_semantics_unknown": ratio(sum(row["natural_termination_semantics_unknown"] for row in rows), len(rows))}
    for field in ("prefix_turns", "prefix_tool_attempts", "continuation_turns", "continuation_proposal_attempts",
                  "continuation_actual_tool_attempts", "historical_input_tokens", "historical_output_tokens",
                  "historical_generation_seconds", "prefix_proposed_repeat_count"):
        group[field] = sum(row[field] for row in results)
    group["parser_error_turn_rate"] = ratio(group["parser_error_turns"], group["continuation_turns"])
    group["proposed_repeat_rate"] = ratio(group["proposed_repeat_count"], group["continuation_proposal_attempts"])
    metrics.update(mode="fixed_failure_prefix", prefix_runtime_version=PREFIX_RUNTIME_VERSION,
        prefix_schema_version=PREFIX_SCHEMA_VERSION, wording_profile=results[0]["wording_profile"],
        task_wording_version=TASK_WORDING_VERSION,
        warning="Authentic selected historical tool prefixes transplanted into a shared calibrated dialogue; exploratory known development cases, not independent trials or natural failure rates. No historical model generation is rerun. Goal already at prefix is not recovery. Natural terminal semantics remain unknown; B2 can mechanically suppress executed loops. No training.")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    validate_config(config)
    root = Path(__file__).resolve().parents[1]
    data_path = root / config["cases_path"]
    rows = _jsonl(data_path)
    if len({row["case_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate prefix identifiers")
    by_id = {row["case_id"]: row for row in rows}
    selected = [by_id[case_id] for case_id in config["case_ids"]]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in (*ARTIFACTS, "prefix_contexts.jsonl")):
        raise FileExistsError("refusing to overwrite fixed-prefix artifacts")
    def write_json(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    handles = {}
    def emit(name, value):
        if name not in handles:
            handles[name] = (output / name).open("x")
        handles[name].write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handles[name].flush()
    stage = "prefix_verification"
    try:
        prepared = [prepare_prefix(row, config) for row in selected]
        stage = "model_load"
        backend = TransformersBackend(config, prepared[0]["tools"])
        provenance = backend.provenance()
        provenance.update(protocol_version=CALIBRATED_PROTOCOL_VERSION, score_version=CALIBRATED_SCORE_VERSION,
            comparison_arm=config["comparison_arm"], guard_version=GUARD_VERSION,
            system_prompt_sha256=hashlib.sha256(prepared[0]["messages"][0]["content"].encode()).hexdigest(),
            case_seeds=config["case_seeds"], mode="fixed_failure_prefix",
            prefix_runtime_version=PREFIX_RUNTIME_VERSION, prefix_schema_version=PREFIX_SCHEMA_VERSION,
            task_wording_version=TASK_WORDING_VERSION, wording_profile=config["wording_profile"],
            prefix_dataset_sha256=sha256_file(data_path),
            prefix_source_rows={row["case_id"]: state_hash(row) for row in selected})
        write_json("model_provenance.json", provenance)
        results = []
        for row, prefix in zip(selected, prepared):
            stage = "case:" + row["case_id"]
            result, trajectory = run_prefix_case(row, backend, config, prepared=prefix,
                emit_generation=lambda value: emit("generations.jsonl", value),
                emit_event=lambda value: emit("execution_events.jsonl", value),
                emit_partial=lambda value: write_json("partial_case.json", value),
                emit_prefix_context=lambda value: emit("prefix_contexts.jsonl", value))
            emit("case_results.jsonl", result)
            emit("trajectories.jsonl", trajectory)
            results.append(result)
            print(json.dumps({key: result[key] for key in ("case_id", "prefix_role", "goal_at_prefix",
                "new_goal_evidence", "exact_grounded_structured_completion", "continuation_turns", "guard_blocked_proposals")}), flush=True)
        write_json("metrics.json", prefix_metrics(results))
        write_json("partial_case.json", {"evidence_class": "model_inference", "status": "completed",
                                         "completed_case_count": len(results)})
    except Exception as exc:
        write_json("model_failure.json", {"evidence_class": "model_inference", "stage": stage,
            "error_type": type(exc).__name__, "message": str(exc), "executable": sys.executable,
            "note": "Failure retained; source records untouched; no scripted fallback or fabricated continuation."})
        raise
    finally:
        for handle in handles.values():
            handle.close()


if __name__ == "__main__":
    main()
