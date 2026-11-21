"""ITER-007 isolated model runners: prefix-decoding, prefix-multiseed, and explicit-e2e."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from .baselines import GUARD_VERSION, guard_action, prompt_for
from .calibrated_protocol import (
    CALIBRATED_PROTOCOL_VERSION,
    CALIBRATED_SCORE_VERSION,
    messages_for_calibrated_view,
    parse_calibrated_output,
    score_calibrated_episode,
)
from .calibrated_run import (
    append_feedback,
    calibrated_metrics,
    public_tool_view,
    validate_config as validate_calibrated_config,
)
from .harness import Environment, detect_loops, ratio, state_hash
from .model_backend import TransformersBackend, sha256_file
from .model_protocol import tool_schemas
from .model_run import ARTIFACTS
from .prefix_run import (
    EXPLICIT_TASK_PREFIX,
    PREFIX_RUNTIME_VERSION,
    PREFIX_SCHEMA_VERSION,
    TASK_WORDING_VERSION,
    prefix_metrics,
    prepare_prefix,
    run_prefix_case,
)

ITER7_RUNNER_VERSION = "iter7_multiseed_and_templates_v1"
SUBCOMMANDS = ("prefix-decoding", "prefix-multiseed", "explicit-e2e")
CATEGORIES = ("normal", "recoverable", "retry_required", "clarification", "unrecoverable")
DOMAINS = ("orders", "files", "jobs")


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _validate_disjoint_seed_ranges(entries, turn_budget):
    if not entries:
        raise ValueError("at least one seed assignment is required")
    intervals = []
    for label, seed in entries:
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer for " + str(label))
        intervals.append((seed, seed + turn_budget, label))
    intervals.sort(key=lambda item: (item[0], item[1], item[2]))
    for index in range(len(intervals) - 1):
        left, right = intervals[index], intervals[index + 1]
        if left[1] > right[0]:
            raise ValueError(
                "seed ranges must be pairwise disjoint: {} [{}, {}) overlaps {} [{}, {})".format(
                    left[2], left[0], left[1], right[2], right[0], right[1]
                )
            )


def _validate_common_model_and_sampling(config, allow_deterministic=False):
    if not isinstance(config, dict):
        raise ValueError("configuration must be an object")
    if config.get("evidence_class") != "model_inference" or config.get("evidence_kind") != "model_inference":
        raise ValueError("iter7 runner requires model_inference evidence labels")
    model = config.get("model")
    if not isinstance(model, dict):
        raise ValueError("model configuration requires model object")
    if model.get("backend") != "transformers" or not re.fullmatch(r"[0-9a-f]{40}", str(model.get("revision", ""))):
        raise ValueError("requires transformers and a pinned 40-hex model revision")
    if not isinstance(model.get("model_path"), str) or not Path(model["model_path"]).is_absolute():
        raise ValueError("model.model_path must be an absolute path")
    sampling = config.get("sampling")
    if not isinstance(sampling, dict):
        raise ValueError("model configuration requires sampling object")
    if sampling.get("enable_thinking") is not False:
        raise ValueError("iter7 runners require enable_thinking=false")
    if type(sampling.get("do_sample")) is not bool:
        raise ValueError("sampling.do_sample must be boolean")
    if not allow_deterministic and sampling["do_sample"] is not True:
        raise ValueError("this runner requires sampling.do_sample=true")
    for key, minimum in (("top_k", 0), ("max_new_tokens", 1), ("seed", 0)):
        if type(sampling.get(key)) is not int or sampling[key] < minimum:
            raise ValueError("invalid sampling." + key)
    for key in ("temperature", "top_p", "min_p", "presence_penalty", "repetition_penalty"):
        if type(sampling.get(key)) not in (int, float) or not math.isfinite(sampling[key]):
            raise ValueError("sampling.{} must be finite numeric".format(key))
    if not 0 < sampling["top_p"] <= 1:
        raise ValueError("sampling.top_p must be in (0, 1]")
    if sampling["temperature"] < 0 or (sampling["do_sample"] and sampling["temperature"] == 0):
        raise ValueError("invalid sampling.temperature")
    if not 0 <= sampling["min_p"] <= 1:
        raise ValueError("sampling.min_p must be in [0, 1]")
    if sampling["repetition_penalty"] <= 0:
        raise ValueError("sampling.repetition_penalty must be positive")
    for field in ("tool_budget", "turn_budget", "max_context_tokens"):
        if type(config.get(field)) is not int or config[field] <= 0:
            raise ValueError("positive integer required: " + field)
    prompt_for(config.get("comparison_arm"))
    if config.get("protocol_version") != CALIBRATED_PROTOCOL_VERSION:
        raise ValueError("calibrated protocol version mismatch")


def resolve_decoding_regime(config):
    sampling = config["sampling"]
    do_sample = sampling["do_sample"]
    penalty = sampling["presence_penalty"]
    declared = config.get("decoding_regime")
    if declared is not None:
        if not isinstance(declared, str) or not declared.strip():
            raise ValueError("decoding_regime must be a nonempty string")
        if declared == "greedy_pp0" and (do_sample is not False or penalty != 0):
            raise ValueError("greedy_pp0 requires do_sample=false and presence_penalty=0")
        if declared == "greedy_pp2" and (do_sample is not False or penalty != 2):
            raise ValueError("greedy_pp2 requires do_sample=false and presence_penalty=2")
        if declared.startswith("greedy") and do_sample is not False:
            raise ValueError("greedy decoding_regime requires do_sample=false")
        if declared.startswith("sampled") and do_sample is not True:
            raise ValueError("sampled decoding_regime requires do_sample=true")
        return declared
    prefix = "sampled" if do_sample else "greedy"
    if penalty == 0:
        return prefix + "_pp0"
    if penalty == 2:
        return prefix + "_pp2"
    return "{}_pp{:g}".format(prefix, penalty)


def validate_iter7_prefix_config(config):
    """Validate fixed-prefix config allowing both do_sample=False and do_sample=True."""
    _validate_common_model_and_sampling(config, allow_deterministic=True)
    if config.get("mode") != "fixed_failure_prefix":
        raise ValueError("prefix-decoding requires fixed_failure_prefix mode")
    if config.get("wording_profile") not in {"legacy", "explicit"}:
        raise ValueError("wording_profile must be legacy or explicit")
    if config.get("task_wording_version") != TASK_WORDING_VERSION:
        raise ValueError("task wording version mismatch")
    if config.get("prefix_schema_version") != PREFIX_SCHEMA_VERSION:
        raise ValueError("prefix schema version mismatch")
    ids, seeds = config.get("case_ids"), config.get("case_seeds")
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(cid, str) and cid for cid in ids)
        or len(ids) != len(set(ids))
        or not isinstance(seeds, dict)
        or set(ids) != set(seeds)
    ):
        raise ValueError("unique case_ids and an exact explicit case_seeds map required")
    _validate_disjoint_seed_ranges([(cid, seeds[cid]) for cid in ids], config["turn_budget"])
    resolve_decoding_regime(config)


def validate_prefix_multiseed_config(config):
    """Validate multi-schedule fixed-prefix config with pairwise disjoint seed ranges."""
    _validate_common_model_and_sampling(config, allow_deterministic=False)
    if config.get("mode") != "fixed_failure_prefix":
        raise ValueError("prefix-multiseed requires fixed_failure_prefix mode")
    if config.get("wording_profile") not in {"legacy", "explicit"}:
        raise ValueError("wording_profile must be legacy or explicit")
    if config.get("task_wording_version") != TASK_WORDING_VERSION:
        raise ValueError("task wording version mismatch")
    if config.get("prefix_schema_version") != PREFIX_SCHEMA_VERSION:
        raise ValueError("prefix schema version mismatch")
    ids = config.get("case_ids")
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(cid, str) and cid and "@" not in cid for cid in ids)
        or len(ids) != len(set(ids))
    ):
        raise ValueError("unique case_ids without '@' delimiter required")
    schedules = config.get("seed_schedules")
    if not isinstance(schedules, list) or not schedules:
        raise ValueError("nonempty seed_schedules list required")
    schedule_ids = []
    entries = []
    for schedule in schedules:
        if not isinstance(schedule, dict):
            raise ValueError("each seed schedule must be an object")
        sid = schedule.get("schedule_id")
        if not isinstance(sid, str) or not sid.strip() or "@" in sid:
            raise ValueError("schedule_id must be a nonempty string without '@'")
        if sid in schedule_ids:
            raise ValueError("duplicate schedule_id: " + sid)
        schedule_ids.append(sid)
        seeds = schedule.get("case_seeds")
        if not isinstance(seeds, dict) or set(seeds) != set(ids):
            raise ValueError("schedule {} must map exact case_ids".format(sid))
        for cid in ids:
            entries.append(("{}@{}".format(cid, sid), seeds[cid]))
    _validate_disjoint_seed_ranges(entries, config["turn_budget"])
    if "case_seeds" in config and config["case_seeds"] != dict(entries):
        raise ValueError("optional top-level case_seeds does not match seed_schedules")
    resolve_decoding_regime(config)


def validate_explicit_e2e_config(config):
    """Validate calibrated E2E config using frozen explicit read-and-report wording."""
    validate_calibrated_config(config)
    _validate_common_model_and_sampling(config, allow_deterministic=False)
    if config.get("wording_profile") != "explicit":
        raise ValueError("explicit-e2e requires wording_profile == 'explicit'")
    if config.get("task_wording_version") != TASK_WORDING_VERSION:
        raise ValueError("task wording version mismatch")
    resolve_decoding_regime(config)


def prepare_iter7_prefix(row, config):
    """Verify source files and replay raw calls via prefix_run.prepare_prefix."""
    validate_iter7_prefix_config(config)
    shim = copy.deepcopy(config)
    shim["sampling"] = {
        **shim["sampling"],
        "do_sample": True,
        "temperature": shim["sampling"]["temperature"] if shim["sampling"]["temperature"] > 0 else 1.0,
    }
    return prepare_prefix(row, shim)


def tag_multiseed_record(record, base_prefix_id, schedule_id):
    trial_id = "{}@{}".format(base_prefix_id, schedule_id)
    return {
        **record,
        "case_id": trial_id,
        "trial_id": trial_id,
        "base_prefix_id": base_prefix_id,
        "schedule_id": schedule_id,
    }


def make_schedule_prefix_config(config, schedule):
    base = {key: copy.deepcopy(value) for key, value in config.items() if key != "seed_schedules"}
    base["case_ids"] = list(config["case_ids"])
    base["case_seeds"] = copy.deepcopy(schedule["case_seeds"])
    return base


def run_multiseed_prefix_trial(
    row,
    schedule,
    backend,
    config,
    emit_generation=None,
    emit_event=None,
    emit_partial=None,
    emit_prefix_context=None,
    prepared=None,
):
    schedule_id = schedule["schedule_id"]
    base_prefix_id = row["case_id"]
    sched_config = make_schedule_prefix_config(config, schedule)
    fresh_row = copy.deepcopy(row)
    prepared_trial = prepared if prepared is not None else prepare_iter7_prefix(fresh_row, sched_config)
    result, trajectory = run_prefix_case(
        fresh_row,
        backend,
        sched_config,
        prepared=prepared_trial,
        emit_generation=(
            (lambda value: emit_generation(tag_multiseed_record(value, base_prefix_id, schedule_id)))
            if emit_generation
            else None
        ),
        emit_event=(
            (lambda value: emit_event(tag_multiseed_record(value, base_prefix_id, schedule_id)))
            if emit_event
            else None
        ),
        emit_partial=(
            (lambda value: emit_partial(tag_multiseed_record(value, base_prefix_id, schedule_id)))
            if emit_partial
            else None
        ),
        emit_prefix_context=(
            (lambda value: emit_prefix_context(tag_multiseed_record(value, base_prefix_id, schedule_id)))
            if emit_prefix_context
            else None
        ),
    )
    return (
        tag_multiseed_record(result, base_prefix_id, schedule_id),
        tag_multiseed_record(trajectory, base_prefix_id, schedule_id),
    )


def prefix_decoding_metrics(results, decoding_regime):
    metrics = prefix_metrics(results)
    metrics.update(
        decoding_regime=decoding_regime,
        runner_subcommand="prefix-decoding",
        iter7_runner_version=ITER7_RUNNER_VERSION,
    )
    return metrics


def multiseed_prefix_metrics(results, seed_schedules, case_ids, decoding_regime):
    metrics = prefix_metrics(results)
    schedule_ids = [item["schedule_id"] for item in seed_schedules]
    by_schedule = {}
    for sid in schedule_ids:
        subset = [row for row in results if row["schedule_id"] == sid]
        if not subset:
            raise ValueError("missing results for schedule " + sid)
        by_schedule[sid] = prefix_metrics(subset)["metrics"]
    by_prefix = {}
    for cid in case_ids:
        subset = [row for row in results if row["base_prefix_id"] == cid]
        if not subset:
            raise ValueError("missing results for prefix " + cid)
        first = subset[0]
        by_prefix[cid] = {
            "base_prefix_id": cid,
            "original_case_id": first["original_case_id"],
            "domain": first["domain"],
            "category": first["category"],
            "prefix_role": first["prefix_role"],
            "completable": first["completable"],
            "recovery_eligible": first["recovery_eligible"],
            "goal_at_prefix": first["goal_at_prefix"],
            "schedule_count": len(subset),
            "trial_ids": [row["trial_id"] for row in subset],
            "tool_goal_attainment": ratio(sum(row["tool_goal_attainment"] for row in subset), len(subset)),
            "new_goal_evidence": ratio(sum(row["new_goal_evidence"] for row in subset), len(subset)),
            "exact_grounded_structured_completion": ratio(
                sum(row["exact_grounded_structured_completion"] for row in subset), len(subset)
            ),
            "exact_structured_completion_with_new_goal": ratio(
                sum(row["exact_grounded_structured_completion"] and row["new_goal_evidence"] for row in subset),
                len(subset),
            ),
            "has_invalid_loop": ratio(sum(row["has_invalid_loop"] for row in subset), len(subset)),
            "premature_stop_without_goal_evidence": ratio(
                sum(row["premature_stop_without_goal_evidence"] for row in subset), len(subset)
            ),
            "stopped_after_public_unavailable": ratio(
                sum(row["stopped_after_public_unavailable"] for row in subset), len(subset)
            ),
            "exact_stop_reason_code": ratio(sum(row["exact_stop_reason_code"] for row in subset), len(subset)),
            "natural_termination_semantics_unknown": ratio(
                sum(row["natural_termination_semantics_unknown"] for row in subset), len(subset)
            ),
            "continuation_turns": sum(row["continuation_turns"] for row in subset),
            "continuation_proposal_attempts": sum(row["continuation_proposal_attempts"] for row in subset),
            "continuation_actual_tool_attempts": sum(row["continuation_actual_tool_attempts"] for row in subset),
            "proposed_repeat_count": sum(row["proposed_repeat_count"] for row in subset),
            "guard_blocked_proposals": sum(row["guard_blocked_proposals"] for row in subset),
            "parser_error_turns": sum(row["parser_error_turns"] for row in subset),
            "input_tokens": sum(row["input_tokens"] for row in subset),
            "output_tokens": sum(row["output_tokens"] for row in subset),
            "generation_seconds": sum(row["generation_seconds"] for row in subset),
        }
    metrics.update(
        decoding_regime=decoding_regime,
        runner_subcommand="prefix-multiseed",
        iter7_runner_version=ITER7_RUNNER_VERSION,
        schedule_ids=schedule_ids,
        schedule_count=len(schedule_ids),
        unique_prefix_count=len(case_ids),
        by_schedule=by_schedule,
        by_prefix=by_prefix,
        historical_cost_scope=(
            "historical_* counters sum across schedule exposures of fixed source prefixes; "
            "no historical prefix generation was rerun"
        ),
        warning=(
            "Multi-seed continuation over authentic selected historical prefixes in a single model load; "
            "seeded trials share 4 recovery + 2 control prefixes and are diagnostic development evidence, "
            "not independent tasks, held-out evaluation, or training."
        ),
    )
    return metrics


def run_explicit_e2e_case(case, backend, config, emit_generation=None, emit_event=None, emit_partial=None):
    """Run calibrated E2E with the frozen explicit read-and-report initial user message."""
    env = Environment(case)
    initial = env.snapshot()
    view = env.public_view()
    messages = messages_for_calibrated_view(view, [], prompt_for(config["comparison_arm"]))
    messages[1]["content"] = EXPLICIT_TASK_PREFIX + json.dumps(view["request"], ensure_ascii=False)
    initial_user_content = messages[1]["content"]
    tools = tool_schemas(view)
    decisions, traces = [], []
    attempts = turns = actual_tools = blocks = repeats = parser_errors = input_tokens = output_tokens = 0
    seconds = 0.0
    exhausted = output_limit = False
    identity = {
        "case_id": case["case_id"],
        "domain": case["domain"],
        "category": case["category"],
        "template_group": case["template_group"],
        "policy": config["policy_name"],
        "comparison_arm": config["comparison_arm"],
        "wording_profile": "explicit",
        "task_wording_version": TASK_WORDING_VERSION,
        "mode": "end_to_end",
        "evidence_class": "model_inference",
        "protocol_version": CALIBRATED_PROTOCOL_VERSION,
        "case_seed": config["case_seeds"][case["case_id"]],
    }
    if "fault_subtype" in case:
        identity["fault_subtype"] = case["fault_subtype"]
    while env.snapshot()["terminal"] is None:
        if attempts >= config["tool_budget"] or turns >= config["turn_budget"]:
            exhausted = True
            break
        seed = identity["case_seed"] + turns
        try:
            generation = backend.generate(copy.deepcopy(messages), copy.deepcopy(tools), seed)
        except Exception as exc:
            if emit_generation:
                emit_generation(
                    {
                        **identity,
                        "turn": turns + 1,
                        "messages": copy.deepcopy(messages),
                        "tools": tools,
                        "seed": seed,
                        "raw_text": None,
                        "input_tokens": None,
                        "output_tokens": None,
                        "generation_error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                )
            if emit_partial:
                emit_partial(
                    {
                        **identity,
                        "state": env.snapshot(),
                        "events": traces,
                        "tool_attempts": attempts,
                        "turns": turns,
                        "status": "generation_failed",
                    }
                )
            raise
        decision = parse_calibrated_output(generation["raw_text"], generation["token_limit_reached"])
        action = (
            decision["action"]
            if decision["valid"]
            else {
                "type": "invalid_model_output",
                "raw_text": generation["raw_text"],
                "parser_error": decision["error"],
            }
        )
        guard = guard_action(public_tool_view(env), action)
        blocked = config["comparison_arm"] == "B2" and guard["blocked"]
        if emit_generation:
            emit_generation(
                {
                    **identity,
                    "turn": turns + 1,
                    "messages": copy.deepcopy(messages),
                    "tools": tools,
                    **generation,
                    "parse": copy.deepcopy(decision),
                    "guard_observation": guard,
                    "guard_enforced": blocked,
                }
            )
        turns += 1
        attempts += int(action.get("type") not in {"final", "stop"})
        actual_dispatch = not blocked and action.get("type") == "tool"
        actual_tools += int(actual_dispatch)
        blocks += int(blocked)
        repeats += int(guard["blocked"])
        parser_errors += int(not decision["valid"])
        input_tokens += generation["input_tokens"]
        output_tokens += generation["output_tokens"]
        seconds += generation["elapsed_seconds"]
        before = env.snapshot()
        if blocked:
            event = {
                "action": copy.deepcopy(action),
                "response": None,
                "state_before": state_hash(before["state"]),
                "state_after": state_hash(before["state"]),
            }
            event_kind = "guard_blocked_proposal"
        else:
            event = env.execute(action)
            event_kind = (
                "actual_tool_response"
                if actual_dispatch
                else "internal_terminal_lifecycle"
                if decision["is_terminal"]
                else "internal_protocol_rejection"
            )
        event.update(
            turn=turns,
            event_kind=event_kind,
            executed=not blocked,
            actual_tool_dispatch=actual_dispatch,
            model_generated_terminal_tool=bool(decision["is_terminal"] and decision["call"] is not None),
            guard_blocked=blocked,
            guard=guard,
            parser_error=decision["error"],
            snapshot_before_sha256=state_hash(before),
            snapshot_after_sha256=state_hash(env.snapshot()),
        )
        traces.append(event)
        decisions.append(decision)
        if emit_event:
            emit_event({**identity, **event})
        if emit_partial:
            emit_partial(
                {
                    **identity,
                    "state": env.snapshot(),
                    "events": traces,
                    "tool_attempts": attempts,
                    "turns": turns,
                    "status": "running",
                }
            )
        append_feedback(messages, decision, event, turns)
        if generation["token_limit_reached"]:
            exhausted = output_limit = True
            break
    outcome = score_calibrated_episode(case, env.snapshot(), decisions, exhausted)
    flags = detect_loops(public_tool_view(env)["history"])
    result = {
        **identity,
        **outcome,
        "tool_attempts": attempts,
        "proposal_attempts": attempts,
        "actual_tool_attempts": actual_tools,
        "guard_blocked_proposals": blocks,
        "proposed_repeat_count": repeats,
        "turns": turns,
        "parser_error_turns": parser_errors,
        "has_invalid_loop": bool(flags),
        "loop_types": sorted({flag["type"] for flag in flags}),
        "output_token_limit_reached": output_limit,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "generation_seconds": seconds,
        "budget_scope": "all_proposals_including_blocked_and_invalid; terminal_excluded_from_tool_budget",
    }
    trajectory = {
        **identity,
        "public_initial_request": case["request"],
        "initial_user_content": initial_user_content,
        "initial_snapshot": initial,
        "events": traces,
        "loop_flags": flags,
        "loop_index_scope": "actual_tool_history_only",
        "actual_tool_history": public_tool_view(env)["history"],
        "final_snapshot": env.snapshot(),
    }
    return result, trajectory


def slice_calibrated_breakdown(rows):
    completable = [row for row in rows if row["completable"]]
    unavailable = [row for row in rows if not row["completable"]]
    return {
        "case_count": len(rows),
        "completable_count": len(completable),
        "unrecoverable_count": len(unavailable),
        "tool_goal_attainment": ratio(sum(row["tool_goal_attainment"] for row in completable), len(completable)),
        "exact_grounded_structured_completion": ratio(
            sum(row["exact_grounded_structured_completion"] for row in completable), len(completable)
        ),
        "has_invalid_loop": ratio(sum(row["has_invalid_loop"] for row in rows), len(rows)),
        "premature_stop_without_goal_evidence": ratio(
            sum(row["premature_stop_without_goal_evidence"] for row in rows), len(rows)
        ),
        "stopped_after_public_unavailable": ratio(
            sum(row["stopped_after_public_unavailable"] for row in unavailable), len(unavailable)
        ),
        "exact_stop_reason_code": ratio(
            sum(row["exact_stop_reason_code"] for row in unavailable), len(unavailable)
        ),
        "natural_termination_semantics_unknown": ratio(
            sum(row["natural_termination_semantics_unknown"] for row in rows), len(rows)
        ),
    }


def explicit_e2e_metrics(results, decoding_regime):
    unknown_categories = {row["category"] for row in results} - set(CATEGORIES)
    unknown_domains = {row["domain"] for row in results} - set(DOMAINS)
    if unknown_categories or unknown_domains:
        raise ValueError("unexpected category or domain in explicit-e2e results")
    metrics = calibrated_metrics(results)
    metrics.update(
        mode="end_to_end",
        wording_profile="explicit",
        task_wording_version=TASK_WORDING_VERSION,
        decoding_regime=decoding_regime,
        runner_subcommand="explicit-e2e",
        iter7_runner_version=ITER7_RUNNER_VERSION,
        by_category={
            category: slice_calibrated_breakdown([row for row in results if row["category"] == category])
            for category in CATEGORIES
        },
        by_domain={
            domain: slice_calibrated_breakdown([row for row in results if row["domain"] == domain])
            for domain in DOMAINS
        },
        warning=(
            "{} development fixtures under frozen explicit read-and-report wording, one seed schedule; "
            "not held-out evaluation or training. Tool-goal evidence is not answer correctness; "
            "natural-language semantics remain unknown; B2 may mechanically suppress execution.".format(len(results))
        ),
    )
    return metrics


def _open_output_writers(output, include_prefix_contexts=False):
    output.mkdir(parents=True, exist_ok=True)
    reserved = (*ARTIFACTS, "prefix_contexts.jsonl") if include_prefix_contexts else ARTIFACTS
    if any((output / name).exists() for name in reserved):
        raise FileExistsError("refusing to overwrite model artifacts")
    handles = {}

    def write_json(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    def emit(name, value):
        if name not in handles:
            handles[name] = (output / name).open("x")
        handles[name].write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handles[name].flush()

    return handles, write_json, emit


def _execute_prefix_decoding(config, root, output, backend_factory):
    validate_iter7_prefix_config(config)
    decoding_regime = resolve_decoding_regime(config)
    data_path = root / config["cases_path"]
    rows = _jsonl(data_path)
    if len({row["case_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate prefix identifiers")
    by_id = {row["case_id"]: row for row in rows}
    selected = [by_id[case_id] for case_id in config["case_ids"]]
    handles, write_json, emit = _open_output_writers(output, include_prefix_contexts=True)
    stage = "prefix_verification"
    try:
        prepared = [prepare_iter7_prefix(row, config) for row in selected]
        stage = "model_load"
        backend = backend_factory(config, prepared[0]["tools"])
        provenance = backend.provenance()
        provenance.update(
            protocol_version=CALIBRATED_PROTOCOL_VERSION,
            score_version=CALIBRATED_SCORE_VERSION,
            comparison_arm=config["comparison_arm"],
            guard_version=GUARD_VERSION,
            system_prompt_sha256=hashlib.sha256(prepared[0]["messages"][0]["content"].encode()).hexdigest(),
            case_seeds=config["case_seeds"],
            mode="fixed_failure_prefix",
            prefix_runtime_version=PREFIX_RUNTIME_VERSION,
            prefix_schema_version=PREFIX_SCHEMA_VERSION,
            task_wording_version=TASK_WORDING_VERSION,
            wording_profile=config["wording_profile"],
            prefix_dataset_sha256=sha256_file(data_path),
            prefix_source_rows={row["case_id"]: state_hash(row) for row in selected},
            decoding_regime=decoding_regime,
            runner_subcommand="prefix-decoding",
            iter7_runner_version=ITER7_RUNNER_VERSION,
        )
        write_json("model_provenance.json", provenance)
        results = []
        for row, prefix in zip(selected, prepared):
            stage = "case:" + row["case_id"]
            result, trajectory = run_prefix_case(
                row,
                backend,
                config,
                prepared=prefix,
                emit_generation=lambda value: emit("generations.jsonl", value),
                emit_event=lambda value: emit("execution_events.jsonl", value),
                emit_partial=lambda value: write_json("partial_case.json", value),
                emit_prefix_context=lambda value: emit("prefix_contexts.jsonl", value),
            )
            emit("case_results.jsonl", result)
            emit("trajectories.jsonl", trajectory)
            results.append(result)
            print(
                json.dumps(
                    {
                        key: result[key]
                        for key in (
                            "case_id",
                            "prefix_role",
                            "goal_at_prefix",
                            "new_goal_evidence",
                            "exact_grounded_structured_completion",
                            "continuation_turns",
                            "guard_blocked_proposals",
                        )
                    }
                ),
                flush=True,
            )
        write_json("metrics.json", prefix_decoding_metrics(results, decoding_regime))
        write_json(
            "partial_case.json",
            {"evidence_class": "model_inference", "status": "completed", "completed_case_count": len(results)},
        )
    except Exception as exc:
        write_json(
            "model_failure.json",
            {
                "evidence_class": "model_inference",
                "stage": stage,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "executable": sys.executable,
                "note": "Failure retained; source records untouched; no scripted fallback or fabricated continuation.",
            },
        )
        raise
    finally:
        for handle in handles.values():
            handle.close()


def _execute_prefix_multiseed(config, root, output, backend_factory):
    validate_prefix_multiseed_config(config)
    decoding_regime = resolve_decoding_regime(config)
    data_path = root / config["cases_path"]
    rows = _jsonl(data_path)
    if len({row["case_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate prefix identifiers")
    by_id = {row["case_id"]: row for row in rows}
    selected = [by_id[case_id] for case_id in config["case_ids"]]
    schedules = config["seed_schedules"]
    handles, write_json, emit = _open_output_writers(output, include_prefix_contexts=True)
    stage = "prefix_verification"
    try:
        prepared_trials = []
        for schedule in schedules:
            sched_config = make_schedule_prefix_config(config, schedule)
            for row in selected:
                prepared_trials.append((schedule, row, prepare_iter7_prefix(copy.deepcopy(row), sched_config)))
        stage = "model_load"
        backend = backend_factory(config, prepared_trials[0][2]["tools"])
        trial_seeds = {
            "{}@{}".format(cid, schedule["schedule_id"]): schedule["case_seeds"][cid]
            for schedule in schedules
            for cid in config["case_ids"]
        }
        provenance = backend.provenance()
        provenance.update(
            protocol_version=CALIBRATED_PROTOCOL_VERSION,
            score_version=CALIBRATED_SCORE_VERSION,
            comparison_arm=config["comparison_arm"],
            guard_version=GUARD_VERSION,
            system_prompt_sha256=hashlib.sha256(
                prepared_trials[0][2]["messages"][0]["content"].encode()
            ).hexdigest(),
            case_seeds=trial_seeds,
            seed_schedules=copy.deepcopy(schedules),
            mode="fixed_failure_prefix",
            prefix_runtime_version=PREFIX_RUNTIME_VERSION,
            prefix_schema_version=PREFIX_SCHEMA_VERSION,
            task_wording_version=TASK_WORDING_VERSION,
            wording_profile=config["wording_profile"],
            prefix_dataset_sha256=sha256_file(data_path),
            prefix_source_rows={row["case_id"]: state_hash(row) for row in selected},
            decoding_regime=decoding_regime,
            runner_subcommand="prefix-multiseed",
            iter7_runner_version=ITER7_RUNNER_VERSION,
        )
        write_json("model_provenance.json", provenance)
        results = []
        for schedule, row, prepared in prepared_trials:
            trial_id = "{}@{}".format(row["case_id"], schedule["schedule_id"])
            stage = "case:" + trial_id
            result, trajectory = run_multiseed_prefix_trial(
                row,
                schedule,
                backend,
                config,
                prepared=prepared,
                emit_generation=lambda value: emit("generations.jsonl", value),
                emit_event=lambda value: emit("execution_events.jsonl", value),
                emit_partial=lambda value: write_json("partial_case.json", value),
                emit_prefix_context=lambda value: emit("prefix_contexts.jsonl", value),
            )
            emit("case_results.jsonl", result)
            emit("trajectories.jsonl", trajectory)
            results.append(result)
            print(
                json.dumps(
                    {
                        key: result[key]
                        for key in (
                            "case_id",
                            "schedule_id",
                            "base_prefix_id",
                            "prefix_role",
                            "new_goal_evidence",
                            "exact_grounded_structured_completion",
                            "continuation_turns",
                            "guard_blocked_proposals",
                        )
                    }
                ),
                flush=True,
            )
        write_json("metrics.json", multiseed_prefix_metrics(results, schedules, config["case_ids"], decoding_regime))
        write_json(
            "partial_case.json",
            {"evidence_class": "model_inference", "status": "completed", "completed_case_count": len(results)},
        )
    except Exception as exc:
        write_json(
            "model_failure.json",
            {
                "evidence_class": "model_inference",
                "stage": stage,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "executable": sys.executable,
                "note": "Failure retained; source records untouched; no scripted fallback or fabricated continuation.",
            },
        )
        raise
    finally:
        for handle in handles.values():
            handle.close()


def _execute_explicit_e2e(config, root, output, backend_factory):
    validate_explicit_e2e_config(config)
    decoding_regime = resolve_decoding_regime(config)
    data_path = root / config["cases_path"]
    rows = _jsonl(data_path)
    if len({case["case_id"] for case in rows}) != len(rows):
        raise ValueError("duplicate case identifiers in fixture file")
    by_id = {case["case_id"]: case for case in rows}
    cases = [by_id[case_id] for case_id in config["case_ids"]]
    handles, write_json, emit = _open_output_writers(output, include_prefix_contexts=False)
    stage = "model_load"
    try:
        first_view = Environment(cases[0]).public_view()
        backend = backend_factory(config, tool_schemas(first_view))
        prompt = messages_for_calibrated_view(first_view, [], prompt_for(config["comparison_arm"]))[0]["content"]
        provenance = backend.provenance()
        provenance.update(
            protocol_version=CALIBRATED_PROTOCOL_VERSION,
            score_version=CALIBRATED_SCORE_VERSION,
            comparison_arm=config["comparison_arm"],
            guard_version=GUARD_VERSION,
            system_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            case_seeds=config["case_seeds"],
            mode="end_to_end",
            wording_profile="explicit",
            task_wording_version=TASK_WORDING_VERSION,
            cases_dataset_sha256=sha256_file(data_path),
            decoding_regime=decoding_regime,
            runner_subcommand="explicit-e2e",
            iter7_runner_version=ITER7_RUNNER_VERSION,
        )
        write_json("model_provenance.json", provenance)
        results = []
        for case in cases:
            stage = "case:" + case["case_id"]
            result, trajectory = run_explicit_e2e_case(
                case,
                backend,
                config,
                emit_generation=lambda row: emit("generations.jsonl", row),
                emit_event=lambda row: emit("execution_events.jsonl", row),
                emit_partial=lambda row: write_json("partial_case.json", row),
            )
            emit("case_results.jsonl", result)
            emit("trajectories.jsonl", trajectory)
            results.append(result)
            print(
                json.dumps(
                    {
                        key: result[key]
                        for key in (
                            "case_id",
                            "tool_goal_attainment",
                            "exact_grounded_structured_completion",
                            "terminal_form",
                            "turns",
                            "guard_blocked_proposals",
                        )
                    }
                ),
                flush=True,
            )
        write_json("metrics.json", explicit_e2e_metrics(results, decoding_regime))
        write_json(
            "partial_case.json",
            {"evidence_class": "model_inference", "status": "completed", "completed_case_count": len(results)},
        )
    except Exception as exc:
        write_json(
            "model_failure.json",
            {
                "evidence_class": "model_inference",
                "stage": stage,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "executable": sys.executable,
                "note": "Failure retained; no scripted fallback or unrecorded retries.",
            },
        )
        raise
    finally:
        for handle in handles.values():
            handle.close()


def main(argv=None, backend_factory=TransformersBackend, root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    for name in SUBCOMMANDS:
        sub = subparsers.add_parser(name)
        sub.add_argument("--config", required=True)
        sub.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text())
    resolved_root = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    output = Path(args.output)
    if args.subcommand == "prefix-decoding":
        _execute_prefix_decoding(config, resolved_root, output, backend_factory)
    elif args.subcommand == "prefix-multiseed":
        _execute_prefix_multiseed(config, resolved_root, output, backend_factory)
    elif args.subcommand == "explicit-e2e":
        _execute_explicit_e2e(config, resolved_root, output, backend_factory)
    else:
        raise ValueError("unknown subcommand: " + str(args.subcommand))
    return 0


if __name__ == "__main__":
    sys.exit(main())
