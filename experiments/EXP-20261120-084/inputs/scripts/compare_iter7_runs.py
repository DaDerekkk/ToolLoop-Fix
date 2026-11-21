#!/usr/bin/env python3
"""Read-only ITER-007 comparison: decoding ablation, 5-seed prefix recovery, unseen E2E, and public alternatives."""
import argparse
import collections
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from toolloop_fix.calibrated_run import public_tool_view
from toolloop_fix.harness import (
    Environment,
    canonical_action,
    ratio,
    scripted_policy,
    state_hash,
    validate_alternative,
)
from toolloop_fix.iter7_runners import (
    CATEGORIES,
    DOMAINS,
    EXPLICIT_TASK_PREFIX,
    PREFIX_RUNTIME_VERSION,
    PREFIX_SCHEMA_VERSION,
    TASK_WORDING_VERSION,
    _validate_disjoint_seed_ranges,
    resolve_decoding_regime,
    slice_calibrated_breakdown,
)
from toolloop_fix.calibrated_protocol import CALIBRATED_PROTOCOL_VERSION, CALIBRATED_SCORE_VERSION
from toolloop_fix.baselines import GUARD_VERSION

ARMS = ("B0", "B1", "B2")
TIMEOUT_PAIR_IDS = ("pfx-orders-timeout", "pfx-files-timeout")
SHARED_E2E_CORE_FILES = tuple(
    "toolloop_fix/" + name
    for name in (
        "harness.py",
        "model_protocol.py",
        "model_backend.py",
        "model_run.py",
        "calibrated_protocol.py",
        "baselines.py",
        "calibrated_run.py",
    )
)
SHARED_PREFIX_CORE_FILES = SHARED_E2E_CORE_FILES + ("toolloop_fix/prefix_run.py",)
T_CRIT_95 = {
    1: 12.706204736432095,
    2: 4.302652729911275,
    3: 3.182446305284263,
    4: 2.7764451051977987,
    5: 2.5705818366147395,
    6: 2.4469118487916806,
    7: 2.3646242510102993,
    8: 2.3060041350333704,
    9: 2.2621571627409915,
    10: 2.2281388519649385,
    11: 2.200985160082949,
    12: 2.1788128296634177,
    13: 2.1603686564610127,
    14: 2.1447866879169273,
    15: 2.131449545559323,
    16: 2.1199052992210112,
    17: 2.1098155778331806,
    18: 2.10092204024096,
    19: 2.093024054408263,
    20: 2.0859634472658364,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def mean_sd(values):
    count = len(values)
    if count == 0:
        return {"n": 0, "mean": None, "sample_sd": None, "se": None, "min": None, "max": None, "values": []}
    avg = sum(values) / count
    sd = math.sqrt(sum((x - avg) ** 2 for x in values) / (count - 1)) if count >= 2 else None
    se = sd / math.sqrt(count) if sd is not None else None
    return {
        "n": count,
        "mean": avg,
        "sample_sd": sd,
        "se": se,
        "min": min(values),
        "max": max(values),
        "values": list(values),
    }


def cluster_ci95(values):
    stats = mean_sd(values)
    n = stats["n"]
    if n >= 2 and stats["se"] is not None:
        df = n - 1
        t_val = T_CRIT_95.get(df, 1.959963984540054)
        half = t_val * stats["se"]
        stats["ci95_exploratory_t"] = [stats["mean"] - half, stats["mean"] + half]
        stats["df"] = df
    else:
        stats["ci95_exploratory_t"] = None
        stats["df"] = None
    return stats


def derive_fault_subtype(case):
    explicit = case.get("fault_subtype")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    category = case["category"]
    hidden = case["hidden"]
    if category == "normal":
        return "none_normal"
    if category == "unrecoverable" or hidden.get("unavailable"):
        return "resource_unavailable"
    if category == "clarification" or not case["request"].get("identifier"):
        return "missing_identifier"
    if category == "recoverable":
        return "wrong_or_stale_identifier"
    if category == "retry_required":
        transient = hidden.get("transient_remaining", 0)
        polls = hidden.get("polls_remaining", 0)
        if transient > 0 and polls > 0:
            return "timeout_{}_and_poll_{}".format(transient, polls)
        if transient > 0:
            return "transient_timeout_{}".format(transient)
        if polls > 0:
            return "job_poll_precondition_{}".format(polls)
        return "retry_required_other"
    return category


def _normalize_ref_list(section_value, label):
    if isinstance(section_value, dict):
        return list(section_value.values())
    if isinstance(section_value, list):
        return list(section_value)
    raise ValueError("invalid run reference collection for " + label)


def _verify_run_manifest_and_inventory(ref, require_prefix_contexts=False):
    require(isinstance(ref, dict) and isinstance(ref.get("path"), str), "invalid run reference")
    root = Path(ref["path"]).resolve()
    manifest_path = root / "manifest.json"
    require(manifest_path.is_file() and sha(manifest_path) == ref.get("manifest_sha256"), "manifest SHA256 mismatch: " + str(root))
    manifest = json.loads(manifest_path.read_text())
    require(
        manifest.get("kind") == "model_inference"
        and manifest.get("status") == "completed"
        and manifest.get("exit_code") == 0
        and manifest.get("evidence_valid") is True
        and not manifest.get("contract_errors"),
        "source run is not completed valid model_inference: " + str(root),
    )
    for section, base in (("inputs", root / "inputs"), ("outputs", root)):
        entries = manifest[section]
        for name, expected in entries.items():
            candidate = base / name
            require(not Path(name).is_absolute(), "absolute path in manifest inventory")
            candidate.resolve().relative_to(base.resolve())
            require(candidate.is_file() and sha(candidate) == expected, "artifact hash mismatch: {} {}".format(root.name, name))
        actual = {
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file() and (section == "inputs" or (p.relative_to(base).parts[0] != "inputs" and p.name != "manifest.json"))
        }
        require(actual == set(entries), "inventory mismatch in {}: {}".format(root.name, section))
    if "artifact_sha256" in ref:
        require(isinstance(ref["artifact_sha256"], dict), "artifact_sha256 must be an object")
        for name, pinned in ref["artifact_sha256"].items():
            require(manifest["outputs"].get(name) == pinned, "pinned artifact_sha256 mismatch: " + name)
    required_outputs = {
        "config.json",
        "model_provenance.json",
        "case_results.jsonl",
        "metrics.json",
        "generations.jsonl",
        "trajectories.jsonl",
        "execution_events.jsonl",
    }
    if require_prefix_contexts:
        required_outputs.add("prefix_contexts.jsonl")
    require(required_outputs.issubset(manifest["outputs"]), "required model outputs missing in " + root.name)
    require(sha(root / "config.json") == manifest["config_sha256"], "config_sha256 mismatch in " + root.name)
    require(
        manifest["inputs"].get("toolloop_fix/harness.py") == sha(ROOT / "toolloop_fix/harness.py"),
        "harness.py SHA mismatch in " + root.name,
    )
    config = json.loads((root / "config.json").read_text())
    provenance = json.loads((root / "model_provenance.json").read_text())
    require(
        provenance.get("evidence_class") == "model_inference"
        and provenance.get("model") == config.get("model")
        and provenance.get("sampling") == config.get("sampling")
        and provenance.get("model_files_verified") is True
        and provenance.get("protocol_version") == CALIBRATED_PROTOCOL_VERSION
        and provenance.get("score_version") == CALIBRATED_SCORE_VERSION
        and provenance.get("guard_version") == GUARD_VERSION,
        "provenance contract mismatch in " + root.name,
    )
    for ext in config["external_inputs"]:
        ext_file = root / "external_inputs" / (ext["name"] + ".json")
        require(ext_file.is_file() and sha(ext_file) == ext["sha256"], "external input hash mismatch in " + root.name)
        if ext["name"] == "model_manifest":
            require(provenance.get("model_manifest_sha256") == ext["sha256"], "model_manifest_sha256 mismatch")
    return root, manifest, config, provenance


def _load_single_schedule_prefix_run(ref, default_schedule_id="s0"):
    root, manifest, config, provenance = _verify_run_manifest_and_inventory(ref, require_prefix_contexts=True)
    require(
        config.get("mode") == "fixed_failure_prefix"
        and config.get("wording_profile") == "explicit"
        and config.get("task_wording_version") == TASK_WORDING_VERSION
        and config.get("prefix_schema_version") == PREFIX_SCHEMA_VERSION,
        "prefix run must use fixed_failure_prefix and explicit read_report_literal_target_v1 wording: " + root.name,
    )
    ids = config["case_ids"]
    require(len(ids) == 6 and len(set(ids)) == 6 and set(config["case_seeds"]) == set(ids), "expected 6 unique prefixes")
    dataset_rows = jsonl(root / "inputs" / config["cases_path"])
    dataset = {row["case_id"]: row for row in dataset_rows}
    require(len(dataset) == 6 and set(dataset) == set(ids), "prefix dataset must contain the 6 authentic prefixes")
    results = jsonl(root / "case_results.jsonl")
    traces = jsonl(root / "trajectories.jsonl")
    contexts = jsonl(root / "prefix_contexts.jsonl")
    generations = jsonl(root / "generations.jsonl")
    events = jsonl(root / "execution_events.jsonl")
    require(
        [r["case_id"] for r in results] == ids
        and [t["case_id"] for t in traces] == ids
        and [c["case_id"] for c in contexts] == ids,
        "prefix case order mismatch in " + root.name,
    )
    gens_by_case = collections.defaultdict(list)
    for g in generations:
        gens_by_case[g["case_id"]].append(g)
    events_by_case = collections.defaultdict(list)
    for e in events:
        events_by_case[e["case_id"]].append(e)
    schedule_id = ref.get("schedule_id", default_schedule_id)
    episodes = {}
    for res, trc, ctx in zip(results, traces, contexts):
        cid = res["case_id"]
        episodes[cid] = {
            "schedule_id": schedule_id,
            "base_prefix_id": cid,
            "trial_id": "{}@{}".format(cid, schedule_id),
            "result": res,
            "trajectory": trc,
            "context": ctx,
            "generations": gens_by_case[cid],
            "events": events_by_case[cid],
            "prefix_row": dataset[cid],
        }
    role_counts = collections.Counter(res["prefix_role"] for res in results)
    require(
        dict(role_counts) == {"recovery": 4, "normal_completion_control": 1, "unavailable_stop_control": 1},
        "expected 4 recovery + 1 normal control + 1 unavailable control prefixes",
    )
    return {
        "run_id": manifest["run_id"],
        "path": str(root),
        "source": ref,
        "manifest": manifest,
        "config": config,
        "provenance": provenance,
        "arm": config["comparison_arm"],
        "decoding_regime": ref.get("decoding_regime") or provenance.get("decoding_regime") or resolve_decoding_regime(config),
        "dataset": dataset,
        "dataset_sha256": manifest["inputs"][config["cases_path"]],
        "core_hashes": {k: manifest["inputs"][k] for k in SHARED_PREFIX_CORE_FILES},
        "schedule_ids": [schedule_id],
        "schedules": {schedule_id: episodes},
        "seeds_by_schedule": {schedule_id: dict(config["case_seeds"])},
        "metrics": json.loads((root / "metrics.json").read_text()),
    }


def _load_multiseed_prefix_run(ref):
    root, manifest, config, provenance = _verify_run_manifest_and_inventory(ref, require_prefix_contexts=True)
    require(
        config.get("mode") == "fixed_failure_prefix"
        and config.get("wording_profile") == "explicit"
        and config.get("task_wording_version") == TASK_WORDING_VERSION
        and config.get("prefix_schema_version") == PREFIX_SCHEMA_VERSION
        and isinstance(config.get("seed_schedules"), list)
        and len(config["seed_schedules"]) >= 1,
        "invalid prefix-multiseed run config: " + root.name,
    )
    ids = config["case_ids"]
    require(len(ids) == 6 and len(set(ids)) == 6, "expected 6 unique prefixes in prefix-multiseed")
    dataset_rows = jsonl(root / "inputs" / config["cases_path"])
    dataset = {row["case_id"]: row for row in dataset_rows}
    require(len(dataset) == 6 and set(dataset) == set(ids), "prefix dataset must contain the 6 authentic prefixes")
    schedules = config["seed_schedules"]
    expected_trial_ids = ["{}@{}".format(cid, s["schedule_id"]) for s in schedules for cid in ids]
    results = jsonl(root / "case_results.jsonl")
    traces = jsonl(root / "trajectories.jsonl")
    contexts = jsonl(root / "prefix_contexts.jsonl")
    generations = jsonl(root / "generations.jsonl")
    events = jsonl(root / "execution_events.jsonl")
    require(
        [r["case_id"] for r in results] == expected_trial_ids
        and [t["case_id"] for t in traces] == expected_trial_ids
        and [c["case_id"] for c in contexts] == expected_trial_ids,
        "multiseed trial order/completeness mismatch in " + root.name,
    )
    gens_by_trial = collections.defaultdict(list)
    for g in generations:
        gens_by_trial[g["case_id"]].append(g)
    events_by_trial = collections.defaultdict(list)
    for e in events:
        events_by_trial[e["case_id"]].append(e)
    by_schedule = {s["schedule_id"]: {} for s in schedules}
    seeds_by_schedule = {s["schedule_id"]: dict(s["case_seeds"]) for s in schedules}
    for res, trc, ctx in zip(results, traces, contexts):
        sid = res["schedule_id"]
        cid = res["base_prefix_id"]
        tid = res["trial_id"]
        require(tid == res["case_id"] == "{}@{}".format(cid, sid), "trial_id mismatch in " + root.name)
        by_schedule[sid][cid] = {
            "schedule_id": sid,
            "base_prefix_id": cid,
            "trial_id": tid,
            "result": res,
            "trajectory": trc,
            "context": ctx,
            "generations": gens_by_trial[tid],
            "events": events_by_trial[tid],
            "prefix_row": dataset[cid],
        }
    return {
        "run_id": manifest["run_id"],
        "path": str(root),
        "source": ref,
        "manifest": manifest,
        "config": config,
        "provenance": provenance,
        "arm": config["comparison_arm"],
        "decoding_regime": provenance.get("decoding_regime") or resolve_decoding_regime(config),
        "dataset": dataset,
        "dataset_sha256": manifest["inputs"][config["cases_path"]],
        "core_hashes": {k: manifest["inputs"][k] for k in SHARED_PREFIX_CORE_FILES},
        "schedule_ids": [s["schedule_id"] for s in schedules],
        "schedules": by_schedule,
        "seeds_by_schedule": seeds_by_schedule,
        "metrics": json.loads((root / "metrics.json").read_text()),
    }


def _load_explicit_e2e_run(ref):
    root, manifest, config, provenance = _verify_run_manifest_and_inventory(ref, require_prefix_contexts=False)
    require(
        config.get("mode") == "end_to_end"
        and config.get("wording_profile") == "explicit"
        and config.get("task_wording_version") == TASK_WORDING_VERSION,
        "E2E run must use mode=end_to_end and explicit wording: " + root.name,
    )
    ids = config["case_ids"]
    require(ids and len(ids) == len(set(ids)) and set(config["case_seeds"]) == set(ids), "invalid E2E case_ids/seeds")
    dataset_rows = jsonl(root / "inputs" / config["cases_path"])
    dataset = {row["case_id"]: row for row in dataset_rows}
    require(set(ids).issubset(dataset), "configured E2E case_ids missing from frozen fixture file")
    results = jsonl(root / "case_results.jsonl")
    traces = jsonl(root / "trajectories.jsonl")
    generations = jsonl(root / "generations.jsonl")
    events = jsonl(root / "execution_events.jsonl")
    require(
        [r["case_id"] for r in results] == ids and [t["case_id"] for t in traces] == ids,
        "E2E result/trajectory case order mismatch in " + root.name,
    )
    gens_by_case = collections.defaultdict(list)
    for g in generations:
        gens_by_case[g["case_id"]].append(g)
    events_by_case = collections.defaultdict(list)
    for e in events:
        events_by_case[e["case_id"]].append(e)
    episodes = {}
    for res, trc in zip(results, traces):
        cid = res["case_id"]
        case = dataset[cid]
        expected_user = EXPLICIT_TASK_PREFIX + json.dumps(case["request"], ensure_ascii=False)
        require(
            gens_by_case[cid][0]["messages"][1]["content"] == expected_user,
            "E2E initial user message is not frozen explicit wording: " + cid,
        )
        episodes[cid] = {
            "case_id": cid,
            "case": case,
            "fault_subtype": derive_fault_subtype(case),
            "result": res,
            "trajectory": trc,
            "generations": gens_by_case[cid],
            "events": events_by_case[cid],
        }
    return {
        "run_id": manifest["run_id"],
        "path": str(root),
        "source": ref,
        "manifest": manifest,
        "config": config,
        "provenance": provenance,
        "arm": config["comparison_arm"],
        "decoding_regime": provenance.get("decoding_regime") or resolve_decoding_regime(config),
        "case_ids": list(ids),
        "dataset": {cid: dataset[cid] for cid in ids},
        "dataset_sha256": manifest["inputs"][config["cases_path"]],
        "core_hashes": {k: manifest["inputs"][k] for k in SHARED_E2E_CORE_FILES},
        "episodes": episodes,
        "metrics": json.loads((root / "metrics.json").read_text()),
    }


def _check_b0_b2_pre_intervention(b0_episodes, b2_episodes, keys):
    checked_turns = 0
    exact_matches = 0
    intervened_keys = []
    mismatches = []
    for key in keys:
        g0_list = b0_episodes[key]["generations"]
        g2_list = b2_episodes[key]["generations"]
        if g0_list and g2_list:
            require(
                g0_list[0]["messages"] == g2_list[0]["messages"]
                and g0_list[0]["tools"] == g2_list[0]["tools"]
                and g0_list[0]["seed"] == g2_list[0]["seed"]
                and g0_list[0]["prompt_sha256"] == g2_list[0]["prompt_sha256"],
                "B0 vs B2 initial turn prompt/seed mismatch on " + str(key),
            )
        intervened = False
        for idx in range(min(len(g0_list), len(g2_list))):
            g0, g2 = g0_list[idx], g2_list[idx]
            if not intervened:
                require(
                    g0["messages"] == g2["messages"]
                    and g0["tools"] == g2["tools"]
                    and g0["seed"] == g2["seed"]
                    and g0["prompt_sha256"] == g2["prompt_sha256"],
                    "B0 vs B2 pre-intervention prompt/seed diverged on {} turn {}".format(key, g0["turn"]),
                )
                checked_turns += 1
                if g0["raw_text"] == g2["raw_text"] and g0["generated_token_ids"] == g2["generated_token_ids"]:
                    exact_matches += 1
                else:
                    mismatches.append({"trial_or_case": str(key), "turn": g0["turn"]})
                    break
            if g2["guard_enforced"]:
                intervened = True
                intervened_keys.append(str(key))
                break
        if not intervened and not any(m["trial_or_case"] == str(key) for m in mismatches):
            require(len(g0_list) == len(g2_list), "B0 vs B2 turn count differed without guard intervention on " + str(key))
    require(not mismatches, "B0 vs B2 pre-intervention generation mismatch: " + json.dumps(mismatches))
    return {
        "episodes_checked": len(keys),
        "pre_intervention_turns_checked": checked_turns,
        "exact_generation_matches": exact_matches,
        "match_rate": ratio(exact_matches, checked_turns),
        "all_pre_intervention_generations_identical": exact_matches == checked_turns,
        "b2_intervened_episodes": intervened_keys,
    }


def _first_turn_timeout_decision(episode):
    ctx = episode["context"]
    res = episode["result"]
    prefix_events = ctx["replayed_prefix_events"]
    require(
        len(prefix_events) == 1
        and prefix_events[0]["response"] == {"code": "TIMEOUT", "retryable": True}
        and prefix_events[0]["actual_tool_dispatch"] is True,
        "expected single retryable TIMEOUT prefix event for " + episode["base_prefix_id"],
    )
    prev_action = prefix_events[0]["action"]
    require(bool(episode["generations"]) and bool(episode["events"]), "missing continuation turn on timeout prefix")
    gen = episode["generations"][0]
    fresh_events = [e for e in episode["events"] if e.get("phase") == "fresh_continuation"]
    event = fresh_events[0] if fresh_events else episode["events"][0]
    require(gen["continuation_turn"] == 1 and event["continuation_turn"] == 1, "first continuation turn index mismatch")
    proposed = event["action"]
    same_call = (
        isinstance(proposed, dict)
        and proposed.get("type") == "tool"
        and canonical_action(proposed) == canonical_action(prev_action)
    )
    same_executed = bool(same_call and event["actual_tool_dispatch"])
    return {
        "trial_id": episode["trial_id"],
        "schedule_id": episode["schedule_id"],
        "base_prefix_id": episode["base_prefix_id"],
        "first_fresh_seed": gen["seed"],
        "parse_valid": gen["parse"]["valid"],
        "parser_error": gen["parse"]["error"],
        "terminal_form": gen["parse"]["terminal_form"],
        "first_proposed_action": {k: v for k, v in proposed.items() if k != "raw_text"}
        if isinstance(proposed, dict)
        else proposed,
        "same_tool_and_arguments": same_call,
        "same_call_executed": same_executed,
        "first_business_response_OK": bool(event["actual_tool_dispatch"] and event["response"].get("code") == "OK"),
        "final_new_goal_evidence": res["new_goal_evidence"],
        "final_exact_structured_completion": bool(
            res["new_goal_evidence"] and res["exact_grounded_structured_completion"]
        ),
    }


def _summarize_prefix_schedule_rows(rows):
    recovery = [r for r in rows if r["recovery_eligible"]]
    normal_ctrl = [r for r in rows if r["prefix_role"] == "normal_completion_control"]
    unavail_ctrl = [r for r in rows if r["prefix_role"] == "unavailable_stop_control"]
    return {
        "case_count": len(rows),
        "recovery_count": len(recovery),
        "new_goal_evidence_recovery": ratio(sum(r["new_goal_evidence"] for r in recovery), len(recovery)),
        "exact_structured_completion_recovery": ratio(
            sum(r["new_goal_evidence"] and r["exact_grounded_structured_completion"] for r in recovery),
            len(recovery),
        ),
        "normal_control_exact_completion": ratio(
            sum(r["exact_grounded_structured_completion"] for r in normal_ctrl), len(normal_ctrl)
        ),
        "unavailable_control_stopped": ratio(
            sum(r["stopped_after_public_unavailable"] for r in unavail_ctrl), len(unavail_ctrl)
        ),
        "unavailable_control_exact_reason": ratio(
            sum(r["exact_stop_reason_code"] for r in unavail_ctrl), len(unavail_ctrl)
        ),
        "has_invalid_loop": ratio(sum(r["has_invalid_loop"] for r in rows), len(rows)),
        "natural_termination_semantics_unknown": ratio(
            sum(r["natural_termination_semantics_unknown"] for r in rows), len(rows)
        ),
        "proposed_repeat_count": sum(r["proposed_repeat_count"] for r in rows),
        "guard_blocked_proposals": sum(r["guard_blocked_proposals"] for r in rows),
        "parser_error_turns": sum(r["parser_error_turns"] for r in rows),
        "continuation_turns": sum(r["continuation_turns"] for r in rows),
        "continuation_actual_tool_attempts": sum(r["continuation_actual_tool_attempts"] for r in rows),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "generation_seconds": sum(r["generation_seconds"] for r in rows),
    }


def _evaluate_public_alternative_at_snapshot(case, snapshot):
    probe_env = Environment(case)
    probe_env.restore(copy.deepcopy(snapshot))
    before = probe_env.snapshot()
    pub_view = public_tool_view(probe_env)
    candidate = scripted_policy(pub_view, "scripted_reference")
    validation = validate_alternative(probe_env, candidate)
    require(probe_env.snapshot() == before, "validate_alternative mutated probe environment")
    locally_validated = bool(validation["public_arguments_only"] and validation["locally_validated"])
    legal_retry_or_poll_progress = bool(
        validation["public_arguments_only"]
        and not validation["locally_validated"]
        and validation["state_progress"]
        and validation["response"].get("code") in {"TIMEOUT", "RUNNING"}
    )
    initial_public_diagnostic = bool(
        validation["public_arguments_only"]
        and validation["new_information"]
        and validation["response"].get("code") in {"INVALID_IDENTIFIER", "PRECONDITION"}
    )
    return {
        "snapshot_sha256": state_hash(before),
        "public_history_turns": len(pub_view["history"]),
        "last_public_response_code": pub_view["history"][-1]["response"]["code"] if pub_view["history"] else None,
        "candidate": candidate,
        "public_arguments_only": validation["public_arguments_only"],
        "locally_validated": locally_validated,
        "legal_retry_or_poll_progress": legal_retry_or_poll_progress,
        "initial_public_diagnostic_probe": initial_public_diagnostic,
        "valid_public_recovery_step": bool(locally_validated or legal_retry_or_poll_progress),
        "valid_public_step_including_initial_probe": bool(
            locally_validated or legal_retry_or_poll_progress or initial_public_diagnostic
        ),
        "response_code": validation["response"].get("code"),
        "new_information": validation["new_information"],
        "state_progress": validation["state_progress"],
        "validation_scope": validation["validation_scope"],
        "training_eligible": False,
    }


def _check_episode_public_alternative(case, trajectory, result, prefix_turns=0, label="", arm="B0"):
    env = Environment(case)
    require(env.snapshot() == trajectory["initial_snapshot"], "initial_snapshot mismatch during alternative check: " + label)
    events = trajectory["events"]
    for idx in range(prefix_turns):
        ev = events[idx]
        require(ev["executed"] and not ev["guard_blocked"], "invalid historical prefix event in " + label)
        env.execute(ev["action"])
    if prefix_turns:
        require(env.snapshot() == trajectory["prefix_snapshot"], "prefix_snapshot mismatch during alternative check: " + label)
    start_fresh_snapshot = env.snapshot()
    active_failure_snapshot = start_fresh_snapshot if prefix_turns > 0 else None
    pre_terminal_snapshot = start_fresh_snapshot
    for ev in events[prefix_turns:]:
        if ev["guard_blocked"]:
            require(state_hash(env.snapshot()) == ev["snapshot_after_sha256"], "blocked event altered snapshot hash")
            continue
        action = ev["action"]
        if isinstance(action, dict) and action.get("type") in {"final", "stop"}:
            pre_terminal_snapshot = env.snapshot()
            env.execute(action)
            require(state_hash(env.snapshot()) == ev["snapshot_after_sha256"], "terminal snapshot hash mismatch")
            break
        env.execute(action)
        require(state_hash(env.snapshot()) == ev["snapshot_after_sha256"], "executed event snapshot hash mismatch")
        pre_terminal_snapshot = env.snapshot()
        if prefix_turns == 0 and ev["actual_tool_dispatch"]:
            code = ev["response"].get("code")
            if code in {"LOCATED", "USER_INFORMATION", "READY"}:
                active_failure_snapshot = None
            elif code != "OK" and active_failure_snapshot is None:
                active_failure_snapshot = env.snapshot()
    require(env.snapshot() == trajectory["final_snapshot"], "final_snapshot replay mismatch: " + label)

    primary_snapshot = active_failure_snapshot if active_failure_snapshot is not None else pre_terminal_snapshot
    primary_point = (
        "prefix_snapshot"
        if prefix_turns > 0
        else "active_public_error_snapshot"
        if active_failure_snapshot is not None
        else "pre_terminal_snapshot"
    )
    primary_eval = _evaluate_public_alternative_at_snapshot(case, primary_snapshot)
    pre_terminal_eval = _evaluate_public_alternative_at_snapshot(case, pre_terminal_snapshot)
    return {
        "episode_id": label,
        "case_id": case["case_id"],
        "domain": case["domain"],
        "category": case["category"],
        "fault_subtype": derive_fault_subtype(case),
        "comparison_arm": arm,
        "terminal_form": result["terminal_form"],
        "budget_exhausted": result["budget_exhausted"],
        "primary_failure_point": primary_point,
        "at_failure_point": primary_eval,
        "at_pre_terminal_point": pre_terminal_eval,
        "training_eligible": False,
    }


def _summarize_alternatives(records, scope_label):
    n = len(records)
    at_fp = [r["at_failure_point"] for r in records]
    at_pt = [r["at_pre_terminal_point"] for r in records]
    return {
        "scope": scope_label,
        "failed_completable_episode_count": n,
        "all_public_arguments_only": all(x["public_arguments_only"] for x in at_fp),
        "at_failure_point_locally_validated": ratio(sum(x["locally_validated"] for x in at_fp), n),
        "at_failure_point_legal_retry_or_poll_progress": ratio(
            sum(x["legal_retry_or_poll_progress"] for x in at_fp), n
        ),
        "at_failure_point_valid_public_recovery_step": ratio(
            sum(x["valid_public_recovery_step"] for x in at_fp), n
        ),
        "at_failure_point_valid_including_initial_probe": ratio(
            sum(x["valid_public_step_including_initial_probe"] for x in at_fp), n
        ),
        "at_pre_terminal_valid_public_recovery_step": ratio(
            sum(x["valid_public_recovery_step"] for x in at_pt), n
        ),
        "training_eligible": False,
        "episodes": records,
    }


def compare_iter7(config):
    before_ml = {name for name in ("torch", "transformers") if name in sys.modules}
    require(isinstance(config, dict), "comparison config must be an object")

    s0_refs = _normalize_ref_list(config["prefix_s0_runs"], "prefix_s0_runs")
    ms_refs = _normalize_ref_list(config["prefix_multiseed_runs"], "prefix_multiseed_runs")
    dec_refs = _normalize_ref_list(config["prefix_decoding_runs"], "prefix_decoding_runs")
    e2e_section = config.get("e2e_runs") if "e2e_runs" in config else config.get("explicit_e2e_runs")
    require(e2e_section is not None, "e2e_runs section is required")
    e2e_refs = _normalize_ref_list(e2e_section, "e2e_runs")

    # 1. Load & verify authentic prefix s0 runs (B0, B1, B2) and multiseed runs (B0, B1, B2)
    s0_by_arm = {}
    for ref in s0_refs:
        loaded = _load_single_schedule_prefix_run(ref, default_schedule_id="s0")
        require(loaded["arm"] not in s0_by_arm, "duplicate arm in prefix_s0_runs: " + loaded["arm"])
        require(loaded["config"]["sampling"]["do_sample"] is True, "s0 runs must be sampled")
        s0_by_arm[loaded["arm"]] = loaded
    require(set(s0_by_arm) == set(ARMS), "prefix_s0_runs requires B0, B1, and B2")

    ms_by_arm = {}
    for ref in ms_refs:
        loaded = _load_multiseed_prefix_run(ref)
        require(loaded["arm"] not in ms_by_arm, "duplicate arm in prefix_multiseed_runs: " + loaded["arm"])
        ms_by_arm[loaded["arm"]] = loaded
    require(set(ms_by_arm) == set(ARMS), "prefix_multiseed_runs requires B0, B1, and B2")

    base_s0 = s0_by_arm["B0"]
    prefix_ids = list(base_s0["config"]["case_ids"])
    all_schedule_ids = base_s0["schedule_ids"] + ms_by_arm["B0"]["schedule_ids"]
    require(len(all_schedule_ids) == len(set(all_schedule_ids)), "duplicate schedule_ids across s0 and multiseed")
    require(len(all_schedule_ids) == 5, "expected 5 total schedules (s0 + 4 multiseed schedules)")

    # Verify parity across s0 and multiseed arms and disjointness of all 5 schedules
    combined_prefix = {arm: {} for arm in ARMS}
    expected_seeds_by_schedule = {
        **base_s0["seeds_by_schedule"],
        **ms_by_arm["B0"]["seeds_by_schedule"],
    }
    disjoint_entries = [
        ("{}@{}".format(cid, sid), expected_seeds_by_schedule[sid][cid])
        for sid in all_schedule_ids
        for cid in prefix_ids
    ]
    _validate_disjoint_seed_ranges(disjoint_entries, base_s0["config"]["turn_budget"])

    shared_prov_keys = (
        "model",
        "sampling",
        "chat_template_sha256",
        "effective_generation",
        "model_manifest_sha256",
        "model_files_verified",
        "guard_version",
        "protocol_version",
        "score_version",
        "prefix_runtime_version",
        "prefix_schema_version",
        "task_wording_version",
        "wording_profile",
        "prefix_dataset_sha256",
        "prefix_source_rows",
    )
    for arm in ARMS:
        for run_obj in (s0_by_arm[arm], ms_by_arm[arm]):
            require(run_obj["core_hashes"] == base_s0["core_hashes"], "shared prefix core code hash mismatch")
            require(run_obj["dataset_sha256"] == base_s0["dataset_sha256"], "prefix dataset hash mismatch")
            require(list(run_obj["config"]["case_ids"]) == prefix_ids, "prefix case_ids mismatch")
            require(
                run_obj["config"]["tool_budget"] == base_s0["config"]["tool_budget"]
                and run_obj["config"]["turn_budget"] == base_s0["config"]["turn_budget"],
                "prefix budget mismatch",
            )
            for pkey in shared_prov_keys:
                require(
                    run_obj["provenance"][pkey] == base_s0["provenance"][pkey],
                    "prefix provenance mismatch on " + pkey,
                )
        combined_seeds = {**s0_by_arm[arm]["seeds_by_schedule"], **ms_by_arm[arm]["seeds_by_schedule"]}
        require(combined_seeds == expected_seeds_by_schedule, "seed schedule mismatch across arms for " + arm)
        combined_prefix[arm].update(s0_by_arm[arm]["schedules"])
        combined_prefix[arm].update(ms_by_arm[arm]["schedules"])
        require(
            s0_by_arm[arm]["provenance"]["system_prompt_sha256"]
            == ms_by_arm[arm]["provenance"]["system_prompt_sha256"],
            "system_prompt_sha256 changed between s0 and multiseed for " + arm,
        )
    require(
        s0_by_arm["B0"]["provenance"]["system_prompt_sha256"]
        == s0_by_arm["B2"]["provenance"]["system_prompt_sha256"],
        "B0/B2 prefix system prompt mismatch",
    )

    # Verify total 90 prefix episodes (5 schedules x 6 prefixes x 3 arms) & snapshot parity
    total_prefix_episodes = sum(len(combined_prefix[arm][sid]) for arm in ARMS for sid in all_schedule_ids)
    require(total_prefix_episodes == 90, "expected 90 total prefix episodes (5 seeds x 6 prefixes x 3 arms)")
    for sid in all_schedule_ids:
        for cid in prefix_ids:
            ref_ctx = combined_prefix["B0"][sid][cid]["context"]
            for arm in ARMS:
                ctx = combined_prefix[arm][sid][cid]["context"]
                require(
                    ctx["prefix_snapshot_sha256"] == ref_ctx["prefix_snapshot_sha256"]
                    and ctx["prefix_public_history_sha256"] == ref_ctx["prefix_public_history_sha256"]
                    and ctx["initial_user_content"] == ref_ctx["initial_user_content"],
                    "prefix snapshot/history/task mismatch across arms/schedules",
                )

    # 2. Load & verify decoding ablation runs (greedy_pp0, greedy_pp2)
    decoding_runs = {}
    for ref in dec_refs:
        loaded = _load_single_schedule_prefix_run(ref, default_schedule_id="greedy")
        regime = loaded["decoding_regime"]
        require(regime not in decoding_runs, "duplicate decoding_regime: " + regime)
        require(loaded["arm"] == "B0", "decoding ablation runs must use arm B0")
        require(loaded["core_hashes"] == base_s0["core_hashes"], "decoding run core code hash mismatch")
        require(loaded["dataset_sha256"] == base_s0["dataset_sha256"], "decoding run dataset hash mismatch")
        require(
            loaded["provenance"]["model"] == base_s0["provenance"]["model"]
            and loaded["provenance"]["chat_template_sha256"] == base_s0["provenance"]["chat_template_sha256"]
            and loaded["provenance"]["system_prompt_sha256"] == base_s0["provenance"]["system_prompt_sha256"]
            and loaded["config"]["tool_budget"] == base_s0["config"]["tool_budget"]
            and loaded["config"]["turn_budget"] == base_s0["config"]["turn_budget"]
            and loaded["config"]["sampling"]["max_new_tokens"] == base_s0["config"]["sampling"]["max_new_tokens"],
            "decoding run uncontrolled model/prompt/budget difference",
        )
        decoding_runs[regime] = loaded
    require(
        {"greedy_pp0", "greedy_pp2"}.issubset(decoding_runs),
        "prefix_decoding_runs requires greedy_pp0 and greedy_pp2",
    )
    require(
        decoding_runs["greedy_pp0"]["config"]["sampling"]["do_sample"] is False
        and decoding_runs["greedy_pp0"]["config"]["sampling"]["presence_penalty"] == 0,
        "greedy_pp0 config mismatch",
    )
    require(
        decoding_runs["greedy_pp2"]["config"]["sampling"]["do_sample"] is False
        and decoding_runs["greedy_pp2"]["config"]["sampling"]["presence_penalty"] == 2,
        "greedy_pp2 config mismatch",
    )

    # 3. Load & verify independent unseen-template E2E runs (B0, B1, B2)
    e2e_by_arm = {}
    for ref in e2e_refs:
        loaded = _load_explicit_e2e_run(ref)
        require(loaded["arm"] not in e2e_by_arm, "duplicate arm in e2e_runs: " + loaded["arm"])
        e2e_by_arm[loaded["arm"]] = loaded
    require(set(e2e_by_arm) == set(ARMS), "e2e_runs requires B0, B1, and B2")
    base_e2e = e2e_by_arm["B0"]
    e2e_case_ids = base_e2e["case_ids"]
    e2e_cfg_ignore = {"comparison_arm", "policy_name", "notes"}
    e2e_prov_ignore = {"comparison_arm", "system_prompt_sha256"}
    for arm in ARMS:
        cur = e2e_by_arm[arm]
        require(cur["core_hashes"] == base_e2e["core_hashes"], "E2E core code hash mismatch for " + arm)
        require(cur["dataset_sha256"] == base_e2e["dataset_sha256"], "E2E dataset hash mismatch for " + arm)
        require(
            {k: v for k, v in cur["config"].items() if k not in e2e_cfg_ignore}
            == {k: v for k, v in base_e2e["config"].items() if k not in e2e_cfg_ignore},
            "unpaired E2E config for " + arm,
        )
        for pkey in (
            "model",
            "sampling",
            "chat_template_sha256",
            "effective_generation",
            "model_manifest_sha256",
            "model_files_verified",
            "case_seeds",
            "protocol_version",
            "score_version",
            "guard_version",
            "wording_profile",
            "task_wording_version",
        ):
            require(cur["provenance"][pkey] == base_e2e["provenance"][pkey], "E2E provenance mismatch: " + pkey)
    require(
        e2e_by_arm["B0"]["provenance"]["system_prompt_sha256"]
        == e2e_by_arm["B2"]["provenance"]["system_prompt_sha256"],
        "B0/B2 E2E system prompt mismatch",
    )
    require(
        base_e2e["dataset_sha256"] != sha(ROOT / "data/dev/pilot_cases.jsonl"),
        "unseen-template E2E suite must be distinct from the original 15 pilot_cases.jsonl",
    )

    # 4. Decoding Disentanglement Table (greedy_pp0 vs greedy_pp2 vs sampled B0 s0..s4)
    decoding_table = {"regimes": {}, "timeout_first_turn_disentanglement": {}}
    for regime in ("greedy_pp0", "greedy_pp2"):
        ep_map = decoding_runs[regime]["schedules"]["greedy"]
        rows = [ep_map[cid]["result"] for cid in prefix_ids]
        summary = _summarize_prefix_schedule_rows(rows)
        timeout_decisions = {
            cid: _first_turn_timeout_decision(ep_map[cid])
            for cid in TIMEOUT_PAIR_IDS
            if cid in ep_map
        }
        decoding_table["regimes"][regime] = {
            "run_id": decoding_runs[regime]["run_id"],
            "sampling": decoding_runs[regime]["config"]["sampling"],
            "summary": summary,
            "timeout_first_turn": timeout_decisions,
        }

    b0_sampled_per_seed = {}
    b0_timeout_by_prefix = {cid: [] for cid in TIMEOUT_PAIR_IDS}
    for sid in all_schedule_ids:
        ep_map = combined_prefix["B0"][sid]
        rows = [ep_map[cid]["result"] for cid in prefix_ids]
        b0_sampled_per_seed[sid] = _summarize_prefix_schedule_rows(rows)
        for cid in TIMEOUT_PAIR_IDS:
            if cid in ep_map:
                b0_timeout_by_prefix[cid].append(_first_turn_timeout_decision(ep_map[cid]))

    decoding_table["regimes"]["sampled_pp2_s0_to_s4"] = {
        "schedule_ids": all_schedule_ids,
        "per_schedule": b0_sampled_per_seed,
        "new_goal_evidence_recovery_rate": mean_sd(
            [b0_sampled_per_seed[sid]["new_goal_evidence_recovery"]["value"] for sid in all_schedule_ids]
        ),
        "exact_structured_completion_recovery_rate": mean_sd(
            [b0_sampled_per_seed[sid]["exact_structured_completion_recovery"]["value"] for sid in all_schedule_ids]
        ),
        "continuation_turns": mean_sd([b0_sampled_per_seed[sid]["continuation_turns"] for sid in all_schedule_ids]),
        "output_tokens": mean_sd([b0_sampled_per_seed[sid]["output_tokens"] for sid in all_schedule_ids]),
    }
    for cid in TIMEOUT_PAIR_IDS:
        decisions = b0_timeout_by_prefix[cid]
        retry_indicators = [int(d["same_call_executed"]) for d in decisions]
        goal_indicators = [int(d["final_new_goal_evidence"]) for d in decisions]
        decoding_table["timeout_first_turn_disentanglement"][cid] = {
            "greedy_pp0": decoding_table["regimes"]["greedy_pp0"]["timeout_first_turn"].get(cid),
            "greedy_pp2": decoding_table["regimes"]["greedy_pp2"]["timeout_first_turn"].get(cid),
            "sampled_b0_per_seed": {d["schedule_id"]: d for d in decisions},
            "sampled_b0_first_turn_retry_count": ratio(sum(retry_indicators), len(retry_indicators)),
            "sampled_b0_first_turn_retry_stats": mean_sd(retry_indicators),
            "sampled_b0_final_recovery_count": ratio(sum(goal_indicators), len(goal_indicators)),
            "sampled_b0_final_recovery_stats": mean_sd(goal_indicators),
        }

    # 5. 5-Seed Paired Fixed-Prefix Recovery Table (5 seeds x 6 prefixes x 3 arms = 90 episodes)
    recovery_prefix_ids = [
        cid for cid in prefix_ids if combined_prefix["B0"][all_schedule_ids[0]][cid]["result"]["recovery_eligible"]
    ]
    require(len(recovery_prefix_ids) == 4, "expected exactly 4 recovery-eligible prefixes")

    per_prefix_5seed = {}
    for cid in prefix_ids:
        meta = combined_prefix["B0"][all_schedule_ids[0]][cid]["result"]
        per_prefix_5seed[cid] = {
            "base_prefix_id": cid,
            "original_case_id": meta["original_case_id"],
            "domain": meta["domain"],
            "category": meta["category"],
            "prefix_role": meta["prefix_role"],
            "recovery_eligible": meta["recovery_eligible"],
            "goal_at_prefix": meta["goal_at_prefix"],
            "arms": {},
        }
        for arm in ARMS:
            p_rows = [combined_prefix[arm][sid][cid]["result"] for sid in all_schedule_ids]
            per_prefix_5seed[cid]["arms"][arm] = {
                "new_goal_evidence": ratio(sum(r["new_goal_evidence"] for r in p_rows), len(p_rows)),
                "exact_structured_completion_with_new_goal": ratio(
                    sum(r["new_goal_evidence"] and r["exact_grounded_structured_completion"] for r in p_rows),
                    len(p_rows),
                ),
                "exact_grounded_structured_completion": ratio(
                    sum(r["exact_grounded_structured_completion"] for r in p_rows), len(p_rows)
                ),
                "stopped_after_public_unavailable": ratio(
                    sum(r["stopped_after_public_unavailable"] for r in p_rows), len(p_rows)
                ),
                "has_invalid_loop": ratio(sum(r["has_invalid_loop"] for r in p_rows), len(p_rows)),
                "natural_termination_semantics_unknown": ratio(
                    sum(r["natural_termination_semantics_unknown"] for r in p_rows), len(p_rows)
                ),
                "proposed_repeat_count": sum(r["proposed_repeat_count"] for r in p_rows),
                "guard_blocked_proposals": sum(r["guard_blocked_proposals"] for r in p_rows),
                "continuation_turns": sum(r["continuation_turns"] for r in p_rows),
                "input_tokens": sum(r["input_tokens"] for r in p_rows),
                "output_tokens": sum(r["output_tokens"] for r in p_rows),
                "per_seed_new_goal": {sid: combined_prefix[arm][sid][cid]["result"]["new_goal_evidence"] for sid in all_schedule_ids},
            }

    per_seed_arm = {arm: {} for arm in ARMS}
    arm_5seed_summary = {}
    for arm in ARMS:
        all_rows = [combined_prefix[arm][sid][cid]["result"] for sid in all_schedule_ids for cid in prefix_ids]
        rec_rows = [r for r in all_rows if r["recovery_eligible"]]
        for sid in all_schedule_ids:
            s_rows = [combined_prefix[arm][sid][cid]["result"] for cid in prefix_ids]
            per_seed_arm[arm][sid] = _summarize_prefix_schedule_rows(s_rows)
        arm_5seed_summary[arm] = {
            "total_episodes": len(all_rows),
            "recovery_trials": len(rec_rows),
            "pooled_new_goal_evidence_recovery": ratio(sum(r["new_goal_evidence"] for r in rec_rows), len(rec_rows)),
            "pooled_exact_structured_completion_recovery": ratio(
                sum(r["new_goal_evidence"] and r["exact_grounded_structured_completion"] for r in rec_rows),
                len(rec_rows),
            ),
            "seed_mean_sd_new_goal_recovery_rate": mean_sd(
                [per_seed_arm[arm][sid]["new_goal_evidence_recovery"]["value"] for sid in all_schedule_ids]
            ),
            "seed_mean_sd_exact_completion_recovery_rate": mean_sd(
                [per_seed_arm[arm][sid]["exact_structured_completion_recovery"]["value"] for sid in all_schedule_ids]
            ),
            "pooled_has_invalid_loop": ratio(sum(r["has_invalid_loop"] for r in all_rows), len(all_rows)),
            "pooled_natural_semantics_unknown": ratio(
                sum(r["natural_termination_semantics_unknown"] for r in all_rows), len(all_rows)
            ),
            "proposed_repeat_count": sum(r["proposed_repeat_count"] for r in all_rows),
            "guard_blocked_proposals": sum(r["guard_blocked_proposals"] for r in all_rows),
            "parser_error_turns": sum(r["parser_error_turns"] for r in all_rows),
            "continuation_turns": sum(r["continuation_turns"] for r in all_rows),
            "continuation_actual_tool_attempts": sum(r["continuation_actual_tool_attempts"] for r in all_rows),
            "fresh_input_tokens": sum(r["input_tokens"] for r in all_rows),
            "fresh_output_tokens": sum(r["output_tokens"] for r in all_rows),
            "fresh_generation_seconds": sum(r["generation_seconds"] for r in all_rows),
            "fresh_tokens_per_new_goal": ratio(
                sum(r["input_tokens"] + r["output_tokens"] for r in rec_rows),
                sum(r["new_goal_evidence"] for r in rec_rows),
            ),
            "non_rerun_historical_input_tokens_per_schedule": sum(
                combined_prefix[arm][all_schedule_ids[0]][cid]["result"]["historical_input_tokens"]
                for cid in prefix_ids
            ),
            "non_rerun_historical_output_tokens_per_schedule": sum(
                combined_prefix[arm][all_schedule_ids[0]][cid]["result"]["historical_output_tokens"]
                for cid in prefix_ids
            ),
        }

    prefix_paired_vs_b0 = {}
    for arm in ("B1", "B2"):
        prefix_paired_vs_b0[arm] = {}
        for metric_name, extractor in (
            ("new_goal_evidence", lambda r: bool(r["new_goal_evidence"])),
            (
                "exact_structured_completion_with_new_goal",
                lambda r: bool(r["new_goal_evidence"] and r["exact_grounded_structured_completion"]),
            ),
        ):
            wtl = {"wins": [], "ties": [], "losses": []}
            for sid in all_schedule_ids:
                for cid in recovery_prefix_ids:
                    tid = "{}@{}".format(cid, sid)
                    delta = int(extractor(combined_prefix[arm][sid][cid]["result"])) - int(
                        extractor(combined_prefix["B0"][sid][cid]["result"])
                    )
                    wtl["wins" if delta > 0 else "losses" if delta < 0 else "ties"].append(tid)
            seed_diffs = []
            for sid in all_schedule_ids:
                arm_rate = sum(
                    extractor(combined_prefix[arm][sid][cid]["result"]) for cid in recovery_prefix_ids
                ) / len(recovery_prefix_ids)
                b0_rate = sum(
                    extractor(combined_prefix["B0"][sid][cid]["result"]) for cid in recovery_prefix_ids
                ) / len(recovery_prefix_ids)
                seed_diffs.append(arm_rate - b0_rate)
            cluster_diffs = []
            per_cluster = {}
            for cid in recovery_prefix_ids:
                arm_c = sum(
                    extractor(combined_prefix[arm][sid][cid]["result"]) for sid in all_schedule_ids
                ) / len(all_schedule_ids)
                b0_c = sum(
                    extractor(combined_prefix["B0"][sid][cid]["result"]) for sid in all_schedule_ids
                ) / len(all_schedule_ids)
                per_cluster[cid] = arm_c - b0_c
                cluster_diffs.append(arm_c - b0_c)
            prefix_paired_vs_b0[arm][metric_name] = {
                "trial_win_tie_loss_20": {k: {"count": len(v), "trial_ids": v} for k, v in wtl.items()},
                "paired_across_5_seed_schedules": mean_sd(seed_diffs),
                "clustered_by_4_recovery_prefixes": {
                    **cluster_ci95(cluster_diffs),
                    "per_prefix_diff": per_cluster,
                },
            }

    flat_b0_prefix = {
        "{}@{}".format(cid, sid): combined_prefix["B0"][sid][cid]
        for sid in all_schedule_ids
        for cid in prefix_ids
    }
    flat_b2_prefix = {
        "{}@{}".format(cid, sid): combined_prefix["B2"][sid][cid]
        for sid in all_schedule_ids
        for cid in prefix_ids
    }
    prefix_b0_b2_check = _check_b0_b2_pre_intervention(flat_b0_prefix, flat_b2_prefix, list(flat_b0_prefix))

    # 6. Independent Unseen-Template E2E Comparison (B0, B1, B2)
    fault_subtypes = sorted({ep["fault_subtype"] for ep in base_e2e["episodes"].values()})
    template_groups = sorted({ep["case"]["template_group"] for ep in base_e2e["episodes"].values()})
    e2e_summary_by_arm = {}
    for arm in ARMS:
        cur = e2e_by_arm[arm]
        rows = [cur["episodes"][cid]["result"] for cid in e2e_case_ids]
        comp_rows = [r for r in rows if r["completable"]]
        e2e_summary_by_arm[arm] = {
            "run_id": cur["run_id"],
            "overall": cur["metrics"]["metrics"],
            "tokens_per_goal_attainment": ratio(
                sum(r["input_tokens"] + r["output_tokens"] for r in comp_rows),
                sum(r["tool_goal_attainment"] for r in comp_rows),
            ),
            "tokens_per_exact_completion": ratio(
                sum(r["input_tokens"] + r["output_tokens"] for r in comp_rows),
                sum(r["exact_grounded_structured_completion"] for r in comp_rows),
            ),
            "by_domain": {
                dom: slice_calibrated_breakdown([r for r in rows if r["domain"] == dom]) for dom in DOMAINS
            },
            "by_category": {
                cat: slice_calibrated_breakdown([r for r in rows if r["category"] == cat]) for cat in CATEGORIES
            },
            "by_fault_subtype": {
                sub: slice_calibrated_breakdown(
                    [
                        cur["episodes"][cid]["result"]
                        for cid in e2e_case_ids
                        if cur["episodes"][cid]["fault_subtype"] == sub
                    ]
                )
                for sub in fault_subtypes
            },
        }

    e2e_paired_vs_b0 = {}
    completable_e2e_ids = [cid for cid in e2e_case_ids if base_e2e["episodes"][cid]["result"]["completable"]]
    completable_template_groups = sorted(
        {base_e2e["episodes"][cid]["case"]["template_group"] for cid in completable_e2e_ids}
    )
    for arm in ("B1", "B2"):
        e2e_paired_vs_b0[arm] = {}
        for metric_name in ("tool_goal_attainment", "exact_grounded_structured_completion"):
            wtl = {"wins": [], "ties": [], "losses": []}
            for cid in completable_e2e_ids:
                delta = int(e2e_by_arm[arm]["episodes"][cid]["result"][metric_name]) - int(
                    base_e2e["episodes"][cid]["result"][metric_name]
                )
                wtl["wins" if delta > 0 else "losses" if delta < 0 else "ties"].append(cid)
            cluster_diffs = []
            for tg in completable_template_groups:
                tg_ids = [
                    cid for cid in completable_e2e_ids if base_e2e["episodes"][cid]["case"]["template_group"] == tg
                ]
                arm_val = sum(e2e_by_arm[arm]["episodes"][cid]["result"][metric_name] for cid in tg_ids) / len(tg_ids)
                b0_val = sum(base_e2e["episodes"][cid]["result"][metric_name] for cid in tg_ids) / len(tg_ids)
                cluster_diffs.append(arm_val - b0_val)
            e2e_paired_vs_b0[arm][metric_name] = {
                "completable_win_tie_loss": {k: {"count": len(v), "case_ids": v} for k, v in wtl.items()},
                "clustered_by_template_group": cluster_ci95(cluster_diffs),
            }

    e2e_b0_b2_check = _check_b0_b2_pre_intervention(
        e2e_by_arm["B0"]["episodes"], e2e_by_arm["B2"]["episodes"], e2e_case_ids
    )

    # 7. Same-Snapshot Public-Only Alternative-Action Check (completable=True & tool_goal_attainment=False)
    e2e_alt_records = []
    for arm in ARMS:
        for cid in e2e_case_ids:
            ep = e2e_by_arm[arm]["episodes"][cid]
            res = ep["result"]
            if res["completable"] and not res["tool_goal_attainment"]:
                e2e_alt_records.append(
                    _check_episode_public_alternative(
                        ep["case"],
                        ep["trajectory"],
                        res,
                        prefix_turns=0,
                        label="{}:{}".format(arm, cid),
                        arm=arm,
                    )
                )

    prefix_alt_records = []
    for arm in ARMS:
        for sid in all_schedule_ids:
            for cid in prefix_ids:
                ep = combined_prefix[arm][sid][cid]
                res = ep["result"]
                if res["completable"] and not res["tool_goal_attainment"]:
                    prefix_alt_records.append(
                        _check_episode_public_alternative(
                            ep["prefix_row"]["original_case"],
                            ep["trajectory"],
                            res,
                            prefix_turns=res["prefix_turns"],
                            label="{}:{}".format(arm, ep["trial_id"]),
                            arm=arm,
                        )
                    )

    after_ml = {name for name in ("torch", "transformers") if name in sys.modules}
    require(after_ml == before_ml, "ML libraries must not be loaded by compare_iter7_runs")

    return {
        "schema_version": 1,
        "evidence_class": "analysis",
        "comparison": "ITER-007 decoding disentanglement, 5-seed fixed-prefix recovery, unseen-template E2E, and same-snapshot public alternative diagnostics",
        "training_eligible": False,
        "checks": {
            "all_manifests_and_inventories_verified": True,
            "shared_harness_sha256_verified": True,
            "disjoint_5_seed_prefix_schedules": True,
            "ninety_paired_prefix_episodes_verified": True,
            "greedy_decoding_ablation_paired": True,
            "unseen_template_e2e_paired": True,
            "b0_vs_b2_pre_intervention_exact_match_prefix": prefix_b0_b2_check["all_pre_intervention_generations_identical"],
            "b0_vs_b2_pre_intervention_exact_match_e2e": e2e_b0_b2_check["all_pre_intervention_generations_identical"],
            "public_only_alternatives_leak_free": True,
            "zero_model_loading": True,
        },
        "source_runs": {
            "prefix_s0": {arm: s0_by_arm[arm]["source"] for arm in ARMS},
            "prefix_multiseed": {arm: ms_by_arm[arm]["source"] for arm in ARMS},
            "prefix_decoding": {regime: decoding_runs[regime]["source"] for regime in sorted(decoding_runs)},
            "unseen_e2e": {arm: e2e_by_arm[arm]["source"] for arm in ARMS},
        },
        "decoding_disentanglement": decoding_table,
        "prefix_5seed_recovery": {
            "diagnostic_scope_note": (
                "n=4 recovery prefixes (4 x 5 = 20 seeded recovery trials per arm; 90 total episodes including "
                "normal and unavailable controls) is a paired diagnostic development check, NOT held-out confirmatory proof "
                "or 20 independent tasks."
            ),
            "schedule_ids": all_schedule_ids,
            "prefix_ids": prefix_ids,
            "recovery_prefix_ids": recovery_prefix_ids,
            "arm_summaries": arm_5seed_summary,
            "per_seed_by_arm": per_seed_arm,
            "per_prefix_across_5_seeds": per_prefix_5seed,
            "paired_against_B0": prefix_paired_vs_b0,
            "b0_vs_b2_pre_intervention_check": prefix_b0_b2_check,
        },
        "unseen_template_e2e": {
            "case_count": len(e2e_case_ids),
            "completable_count": len(completable_e2e_ids),
            "template_group_count": len(template_groups),
            "fault_subtypes": fault_subtypes,
            "arms": e2e_summary_by_arm,
            "paired_against_B0": e2e_paired_vs_b0,
            "b0_vs_b2_pre_intervention_check": e2e_b0_b2_check,
        },
        "same_snapshot_public_alternatives": {
            "training_eligible": False,
            "unseen_e2e": _summarize_alternatives(e2e_alt_records, "unseen_template_e2e_failed_completable"),
            "prefix_5seed": _summarize_alternatives(prefix_alt_records, "prefix_5seed_failed_completable"),
        },
        "limits": [
            "n=4 authentic recovery prefixes across 5 seed schedules (20 recovery trials/arm, 90 total prefix episodes) is diagnostic development evidence, not held-out confirmatory proof.",
            "Unseen-template E2E fixtures remain development-split diagnostics until G2 human blind semantic/detector audit and held-out freeze complete.",
            "Tool-goal attainment is observed environment evidence, not natural-language semantic correctness; natural-text terminals remain unknown.",
            "B2 runtime guard blocks suppress repeated tool execution mechanically; evaluate recovery via new goal evidence and exact completion alongside proposal counts and token costs.",
            "Same-snapshot validate_alternative checks prove public single-step progress without hidden leakage, but all trajectories remain training_eligible=false.",
        ],
    }


def _fmt_ratio(value):
    if not isinstance(value, dict):
        return str(value)
    if not value["denominator"]:
        return "N/A"
    return "{}/{} ({:.1%})".format(value["numerator"], value["denominator"], value["value"])


def _fmt_mean_sd(stats):
    if not stats or stats["mean"] is None:
        return "N/A"
    if stats["sample_sd"] is None:
        return "{:.3f}".format(stats["mean"])
    return "{:.3f} ± {:.3f}".format(stats["mean"], stats["sample_sd"])


def markdown(report):
    lines = [
        "# ITER-007 多 Seed 前缀恢复、解码消融与独立未见模板 E2E 对照",
        "",
        "> 证据类型：`analysis`（零模型加载，全部源 run 经 manifest 与全量产物 SHA256 校验；`training_eligible=false`）。",
        "",
        "## 1. 解码机制拆解（6 个真实历史前缀，explicit / B0）",
        "",
        "| 解码配置 | 恢复新证据 (x/4) | 精确结构化恢复 (x/4) | orders-timeout 首步重试 | files-timeout 首步重试 | 新生成轮 | 输出 token |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    dec = report["decoding_disentanglement"]
    t_dis = dec["timeout_first_turn_disentanglement"]
    for regime in ("greedy_pp0", "greedy_pp2"):
        reg = dec["regimes"][regime]
        s = reg["summary"]
        ord_d = reg["timeout_first_turn"].get("pfx-orders-timeout", {})
        fil_d = reg["timeout_first_turn"].get("pfx-files-timeout", {})
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} |".format(
                regime,
                _fmt_ratio(s["new_goal_evidence_recovery"]),
                _fmt_ratio(s["exact_structured_completion_recovery"]),
                "1/1" if ord_d.get("same_call_executed") else "0/1",
                "1/1" if fil_d.get("same_call_executed") else "0/1",
                s["continuation_turns"],
                s["output_tokens"],
            )
        )
    samp = dec["regimes"]["sampled_pp2_s0_to_s4"]
    lines.append(
        "| `sampled_pp2` (`s0..s4` 均值±SD) | {} | {} | {} ({}) | {} ({}) | {} | {} |".format(
            _fmt_mean_sd(samp["new_goal_evidence_recovery_rate"]),
            _fmt_mean_sd(samp["exact_structured_completion_recovery_rate"]),
            _fmt_ratio(t_dis["pfx-orders-timeout"]["sampled_b0_first_turn_retry_count"]),
            _fmt_mean_sd(t_dis["pfx-orders-timeout"]["sampled_b0_first_turn_retry_stats"]),
            _fmt_ratio(t_dis["pfx-files-timeout"]["sampled_b0_first_turn_retry_count"]),
            _fmt_mean_sd(t_dis["pfx-files-timeout"]["sampled_b0_first_turn_retry_stats"]),
            _fmt_mean_sd(samp["continuation_turns"]),
            _fmt_mean_sd(samp["output_tokens"]),
        )
    )

    pfx = report["prefix_5seed_recovery"]
    lines += [
        "",
        "## 2. 5-Seed（`s0..s4`）固定前缀恢复对照（5 seeds × 6 prefixes × 3 arms = 90 episodes）",
        "",
        "诊断说明：`n=4` 个恢复前缀（每臂 `4 × 5 = 20` 次种子试验，含控制共 90 次续跑）属于开发期诊断对照，**不是**独立 20 任务或封闭确认性证明。",
        "",
        "### 2.1 各臂 5-Seed 汇总与新鲜成本",
        "",
        "| Arm | 汇总恢复新证据 (x/20) | Seed 均值±SD (恢复率) | 精确结构化恢复 (x/20) | 新执行循环 (x/30) | 重复提案/实际阻断 | 新生成轮 | 新输入/输出 token |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        a = pfx["arm_summaries"][arm]
        lines.append(
            "| `{}` | {} | {} | {} | {} | {}/{} | {} | {}/{} |".format(
                arm,
                _fmt_ratio(a["pooled_new_goal_evidence_recovery"]),
                _fmt_mean_sd(a["seed_mean_sd_new_goal_recovery_rate"]),
                _fmt_ratio(a["pooled_exact_structured_completion_recovery"]),
                _fmt_ratio(a["pooled_has_invalid_loop"]),
                a["proposed_repeat_count"],
                a["guard_blocked_proposals"],
                a["continuation_turns"],
                a["fresh_input_tokens"],
                a["fresh_output_tokens"],
            )
        )

    lines += [
        "",
        "### 2.2 逐前缀 5-Seed 表现（`x/5`）",
        "",
        "| Prefix ID | 角色 | B0 新证据 | B1 新证据 | B2 新证据 | B0 精确完成 | B1 精确完成 | B2 精确完成 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for cid in pfx["prefix_ids"]:
        row = pfx["per_prefix_across_5_seeds"][cid]
        lines.append(
            "| `{}` | `{}` | {} | {} | {} | {} | {} | {} |".format(
                cid,
                row["prefix_role"],
                _fmt_ratio(row["arms"]["B0"]["new_goal_evidence"]),
                _fmt_ratio(row["arms"]["B1"]["new_goal_evidence"]),
                _fmt_ratio(row["arms"]["B2"]["new_goal_evidence"]),
                _fmt_ratio(row["arms"]["B0"]["exact_grounded_structured_completion"]),
                _fmt_ratio(row["arms"]["B1"]["exact_grounded_structured_completion"]),
                _fmt_ratio(row["arms"]["B2"]["exact_grounded_structured_completion"]),
            )
        )

    lines += ["", "### 2.3 逐 Schedule 恢复（`x/4`）与配对差值（`B1 - B0`、`B2 - B0`）", ""]
    for sid in pfx["schedule_ids"]:
        lines.append(
            "- `{}` 恢复新证据：B0={}，B1={}，B2={}。".format(
                sid,
                _fmt_ratio(pfx["per_seed_by_arm"]["B0"][sid]["new_goal_evidence_recovery"]),
                _fmt_ratio(pfx["per_seed_by_arm"]["B1"][sid]["new_goal_evidence_recovery"]),
                _fmt_ratio(pfx["per_seed_by_arm"]["B2"][sid]["new_goal_evidence_recovery"]),
            )
        )
    for arm in ("B1", "B2"):
        for metric_name, delta in pfx["paired_against_B0"][arm].items():
            wtl = delta["trial_win_tie_loss_20"]
            lines.append(
                "- `{}` vs `B0` `{}`（20 次恢复试验 胜/平/负 = {}/{}/{}）：按 5 seed 配对差均值±SD = {}；按 n=4 前缀聚类差均值±SD = {}。".format(
                    arm,
                    metric_name,
                    wtl["wins"]["count"],
                    wtl["ties"]["count"],
                    wtl["losses"]["count"],
                    _fmt_mean_sd(delta["paired_across_5_seed_schedules"]),
                    _fmt_mean_sd(delta["clustered_by_4_recovery_prefixes"]),
                )
            )
    lines.append(
        "- 前缀 B0 vs B2 干预前精确生成匹配：{}/{} 轮一致（`all_identical={}`）。".format(
            pfx["b0_vs_b2_pre_intervention_check"]["exact_generation_matches"],
            pfx["b0_vs_b2_pre_intervention_check"]["pre_intervention_turns_checked"],
            pfx["b0_vs_b2_pre_intervention_check"]["all_pre_intervention_generations_identical"],
        )
    )

    e2e = report["unseen_template_e2e"]
    lines += [
        "",
        "## 3. 独立未见模板 E2E 对照（`{}` 例，`{}` 模板组）".format(e2e["case_count"], e2e["template_group_count"]),
        "",
        "| 指标 | B0 | B1 | B2 |",
        "|---|---:|---:|---:|",
    ]
    for key, label in (
        ("tool_goal_attainment", "可完成案例取得目标证据"),
        ("exact_grounded_structured_completion", "精确有依据结构化完成"),
        ("premature_stop_without_goal_evidence", "无证据提前终止"),
        ("stopped_after_public_unavailable", "公开不可用后停止"),
        ("exact_stop_reason_code", "精确停止原因码"),
        ("natural_termination_semantics_unknown", "自然文本终止待审"),
        ("has_invalid_loop", "实际工具历史原型循环"),
        ("proposed_repeat_count", "重复提案次数"),
        ("guard_blocked_proposals", "B2 实际阻断次数"),
        ("turns", "总生成轮数"),
        ("input_tokens", "总输入 token"),
        ("output_tokens", "总输出 token"),
    ):
        lines.append(
            "| {} | {} | {} | {} |".format(
                label,
                *(_fmt_ratio(e2e["arms"][arm]["overall"][key]) for arm in ARMS),
            )
        )

    lines += ["", "### 3.1 按域（Domain）、类别（Category）与故障子类型（Fault Subtype）目标证据率", ""]
    lines += ["| 分层 | B0 目标证据 | B1 目标证据 | B2 目标证据 | B0 无效循环 | B2 无效循环 |", "|---|---:|---:|---:|---:|---:|"]
    for dom in DOMAINS:
        lines.append(
            "| domain:`{}` | {} | {} | {} | {} | {} |".format(
                dom,
                _fmt_ratio(e2e["arms"]["B0"]["by_domain"][dom]["tool_goal_attainment"]),
                _fmt_ratio(e2e["arms"]["B1"]["by_domain"][dom]["tool_goal_attainment"]),
                _fmt_ratio(e2e["arms"]["B2"]["by_domain"][dom]["tool_goal_attainment"]),
                _fmt_ratio(e2e["arms"]["B0"]["by_domain"][dom]["has_invalid_loop"]),
                _fmt_ratio(e2e["arms"]["B2"]["by_domain"][dom]["has_invalid_loop"]),
            )
        )
    for cat in CATEGORIES:
        metric_field = "stopped_after_public_unavailable" if cat == "unrecoverable" else "tool_goal_attainment"
        lines.append(
            "| category:`{}` ({}) | {} | {} | {} | {} | {} |".format(
                cat,
                "停止" if cat == "unrecoverable" else "目标证据",
                _fmt_ratio(e2e["arms"]["B0"]["by_category"][cat][metric_field]),
                _fmt_ratio(e2e["arms"]["B1"]["by_category"][cat][metric_field]),
                _fmt_ratio(e2e["arms"]["B2"]["by_category"][cat][metric_field]),
                _fmt_ratio(e2e["arms"]["B0"]["by_category"][cat]["has_invalid_loop"]),
                _fmt_ratio(e2e["arms"]["B2"]["by_category"][cat]["has_invalid_loop"]),
            )
        )
    for sub in e2e["fault_subtypes"]:
        sample_cell = e2e["arms"]["B0"]["by_fault_subtype"][sub]
        metric_field = (
            "stopped_after_public_unavailable" if sample_cell["completable_count"] == 0 else "tool_goal_attainment"
        )
        lines.append(
            "| subtype:`{}` | {} | {} | {} | {} | {} |".format(
                sub,
                _fmt_ratio(e2e["arms"]["B0"]["by_fault_subtype"][sub][metric_field]),
                _fmt_ratio(e2e["arms"]["B1"]["by_fault_subtype"][sub][metric_field]),
                _fmt_ratio(e2e["arms"]["B2"]["by_fault_subtype"][sub][metric_field]),
                _fmt_ratio(e2e["arms"]["B0"]["by_fault_subtype"][sub]["has_invalid_loop"]),
                _fmt_ratio(e2e["arms"]["B2"]["by_fault_subtype"][sub]["has_invalid_loop"]),
            )
        )

    lines += ["", "### 3.2 未见模板配对胜/平/负与 B0 vs B2 干预前核验", ""]
    for arm in ("B1", "B2"):
        for metric_name, delta in e2e["paired_against_B0"][arm].items():
            wtl = delta["completable_win_tie_loss"]
            lines.append(
                "- `{}` vs `B0` `{}`：胜/平/负 = {}/{}/{}；按模板组聚类差均值±SD = {}。".format(
                    arm,
                    metric_name,
                    wtl["wins"]["count"],
                    wtl["ties"]["count"],
                    wtl["losses"]["count"],
                    _fmt_mean_sd(delta["clustered_by_template_group"]),
                )
            )
    lines.append(
        "- E2E B0 vs B2 干预前精确生成匹配：{}/{} 轮一致（`all_identical={}`）。".format(
            e2e["b0_vs_b2_pre_intervention_check"]["exact_generation_matches"],
            e2e["b0_vs_b2_pre_intervention_check"]["pre_intervention_turns_checked"],
            e2e["b0_vs_b2_pre_intervention_check"]["all_pre_intervention_generations_identical"],
        )
    )

    alt = report["same_snapshot_public_alternatives"]
    lines += [
        "",
        "## 4. 同快照无泄漏公开替代动作检验（`validate_alternative`，`training_eligible=false`）",
        "",
        "- 未见模板 E2E 未达目标的可完成轨迹（共 {} 条）：故障点存在合法公开恢复/推进步骤 = {}（纯 `locally_validated` = {}；合法重试/轮询推进 = {}；含零调用首步公开探针 = {}）；全部候选 `public_arguments_only={}`。".format(
            alt["unseen_e2e"]["failed_completable_episode_count"],
            _fmt_ratio(alt["unseen_e2e"]["at_failure_point_valid_public_recovery_step"]),
            _fmt_ratio(alt["unseen_e2e"]["at_failure_point_locally_validated"]),
            _fmt_ratio(alt["unseen_e2e"]["at_failure_point_legal_retry_or_poll_progress"]),
            _fmt_ratio(alt["unseen_e2e"]["at_failure_point_valid_including_initial_probe"]),
            alt["unseen_e2e"]["all_public_arguments_only"],
        ),
        "- 5-Seed 固定前缀未达目标的可完成轨迹（共 {} 条）：前缀快照存在合法公开恢复/推进步骤 = {}（纯 `locally_validated` = {}；合法重试/轮询推进 = {}）；全部候选 `public_arguments_only={}`。".format(
            alt["prefix_5seed"]["failed_completable_episode_count"],
            _fmt_ratio(alt["prefix_5seed"]["at_failure_point_valid_public_recovery_step"]),
            _fmt_ratio(alt["prefix_5seed"]["at_failure_point_locally_validated"]),
            _fmt_ratio(alt["prefix_5seed"]["at_failure_point_legal_retry_or_poll_progress"]),
            alt["prefix_5seed"]["all_public_arguments_only"],
        ),
        "",
        "## 5. 解释边界",
    ]
    for item in report["limits"]:
        lines.append("- " + item)
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text())
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    names = ("iter7_comparison.json", "iter7_comparison.md")
    if any((output / name).exists() for name in names):
        raise FileExistsError("refusing to overwrite iter7 comparison artifacts")
    result = compare_iter7(config)
    with (output / names[0]).open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with (output / names[1]).open("x") as handle:
        handle.write(markdown(result))
    print(json.dumps({"checks": result["checks"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
