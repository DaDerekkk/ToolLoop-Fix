#!/usr/bin/env python3
"""Deterministic G2 blind semantic-review and loop-detector audit pack builder.

Stdlib only; zero model execution, zero network access, zero mutation of source runs.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from toolloop_fix.calibrated_protocol import parse_calibrated_output
from toolloop_fix.harness import (
    DOMAIN_TOOLS,
    Environment,
    canonical_action,
    detect_loops,
    tool_call,
)

SCHEMA_VERSION = 1
SEMANTIC_PACK_TYPE = "g2_blind_semantic_review_pack"
SEMANTIC_KEY_TYPE = "g2_blind_semantic_key"
DETECTOR_PACK_TYPE = "g2_blind_detector_audit_pack"
DETECTOR_KEY_TYPE = "g2_blind_detector_key"

ALLOWED_SEMANTIC_LABELS = (
    "grounded_correct_delivery",
    "truthful_inability_or_timeout_stop",
    "error_code_as_record_value",
    "unobserved_execution_claim",
    "unsupported_success_claim",
    "unsupported_absence_or_cause_claim",
    "incomplete_or_ambiguous",
)

MANDATORY_DETECTOR_STRATA = (
    "flagged_identical_or_equivalent_repeat",
    "flagged_short_cycle_abab",
    "flagged_retry_limit_violation",
    "unflagged_legal_timeout_retry",
    "unflagged_legal_job_polling",
    "unflagged_genuine_progress_or_first_error",
)

FORBIDDEN_BLIND_SUBSTRINGS = (
    "B0",
    "B1",
    "B2",
    "EXP-",
    "comparison_arm",
    "agent_judgment",
    "template_group",
    "hidden",
    "detector_flagged",
    "stratum",
    "Runtime guard notice",
    "guard_blocked",
    "synthetic_harness",
    "model_inference",
    "scripted_naive",
    "scripted_reference",
    "CPU-202",
    "wording_profile",
    "prefix_role",
)

DETECTOR_EXTRA_FORBIDDEN = (
    "identical_or_equivalent_repeat",
    "short_cycle_abab",
    "retry_limit_violation",
    "loop_flags",
) + MANDATORY_DETECTOR_STRATA


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def object_sha256(value):
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def semantic_public_payload(item):
    return {
        "blind_id": item["blind_id"],
        "public_user_request": item["public_user_request"],
        "public_tool_names": item["public_tool_names"],
        "observed_public_tool_history": item["observed_public_tool_history"],
        "terminal_response": item["terminal_response"],
    }


def detector_public_payload(item):
    return {
        "blind_id": item["blind_id"],
        "public_request": item["public_request"],
        "public_tool_docs": item["public_tool_docs"],
        "history_prefix": item["history_prefix"],
        "focal_step_k": item["focal_step_k"],
    }


def _resolve_run_ref(ref, base_dir):
    if isinstance(ref, str):
        raw_path, pinned_sha = ref, None
    elif isinstance(ref, dict) and isinstance(ref.get("path"), str):
        raw_path = ref["path"]
        pinned_sha = ref.get("manifest_sha256")
    else:
        raise ValueError("run reference must be a path string or {'path': ...} object")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate, pinned_sha


def _verify_run_manifest(run_dir, expected_kind, required_outputs, pinned_manifest_sha=None):
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"missing manifest.json in {run_dir}")
    actual_manifest_sha = sha256_file(manifest_path)
    if pinned_manifest_sha is not None and actual_manifest_sha != pinned_manifest_sha:
        raise ValueError(f"manifest SHA256 mismatch for {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed":
        raise ValueError(f"run is not completed: {run_dir}")
    if manifest.get("evidence_valid") is not True:
        raise ValueError(f"run evidence_valid is not True: {run_dir}")
    if expected_kind is not None and manifest.get("kind") != expected_kind:
        raise ValueError(
            f"run kind mismatch in {run_dir}: expected {expected_kind}, got {manifest.get('kind')}"
        )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(f"manifest outputs missing in {run_dir}")
    missing = set(required_outputs) - set(outputs.keys())
    if missing:
        raise ValueError(f"missing required outputs {sorted(missing)} in {run_dir}")
    for rel_name, expected_sha in outputs.items():
        target = (run_dir / rel_name).resolve()
        target.relative_to(run_dir.resolve())
        if not target.is_file():
            raise ValueError(f"missing output file {rel_name} in {run_dir}")
        if sha256_file(target) != expected_sha:
            raise ValueError(f"output SHA256 mismatch for {rel_name} in {run_dir}")
    return manifest, actual_manifest_sha


def _public_tool_docs_for_domain(domain, request):
    dummy_case = {
        "domain": domain,
        "request": copy.deepcopy(request),
        "hidden": {
            "valid_identifier": "",
            "value": "",
            "unavailable": False,
            "transient_remaining": 0,
            "polls_remaining": 0,
        },
    }
    return Environment(dummy_case).public_view()["tools"]


def _extract_request_object(user_message, fallback_request=None):
    if fallback_request is not None:
        return copy.deepcopy(fallback_request)
    for marker in ("Request: ", "Complete this request: "):
        if marker in user_message:
            tail = user_message.split(marker, 1)[1].strip()
            return json.loads(tail)
    return {"raw_user_request": user_message}


def _clean_parsed_terminal_action(parse_obj, terminal_form):
    if terminal_form == "structured_finish" and isinstance(parse_obj.get("action"), dict):
        action = parse_obj["action"]
        return {
            "type": "final",
            "identifier": action.get("identifier"),
            "answer": action.get("answer"),
        }
    if terminal_form == "structured_stop" and isinstance(parse_obj.get("action"), dict):
        return {
            "type": "stop",
            "reason": parse_obj["action"].get("reason"),
        }
    return None


def _semantic_inclusion_category(case_result, last_gen):
    form = case_result.get("terminal_form")
    parse_obj = last_gen.get("parse")
    if not isinstance(parse_obj, dict) or "terminal_form" not in parse_obj:
        parse_obj = parse_calibrated_output(
            last_gen.get("raw_text") or "",
            bool(last_gen.get("token_limit_reached", False)),
        )
    if form is None:
        form = parse_obj.get("terminal_form") or "none"

    grounded_finish = bool(
        case_result.get("exact_grounded_structured_completion")
        or (form == "structured_finish" and case_result.get("success") is True)
    )
    exact_stop = bool(
        case_result.get("exact_stop_reason_code")
        or (form == "structured_stop" and case_result.get("correct_termination") is True)
    )
    unsupported_finish = bool(
        case_result.get("unsupported_finish_wrapper")
        or (form == "structured_finish" and not grounded_finish)
    )

    if grounded_finish:
        return "grounded_structured_positive_control", form, parse_obj
    if unsupported_finish:
        return "unsupported_finish_wrapper", form, parse_obj
    if form == "natural_text":
        return "natural_text_terminal", form, parse_obj
    if form == "structured_stop":
        if exact_stop:
            return None, form, parse_obj
        return "structured_stop_non_exact", form, parse_obj
    token_limit = bool(
        case_result.get("output_token_limit_reached")
        or last_gen.get("token_limit_reached")
    )
    if form == "none" and (token_limit or not parse_obj.get("valid", False)):
        return "truncated_or_unparsed_terminal", form, parse_obj
    return None, form, parse_obj


def classify_detector_step(history, k):
    """Classify prefix history[:k] + focal history[k] into one of 6 G2 strata."""
    if not (0 <= k < len(history)):
        raise ValueError(f"step index {k} out of bounds for history length {len(history)}")
    prefix = history[:k]
    focal = history[k]
    flags_at_k = [flag for flag in detect_loops(history[: k + 1]) if flag["index"] == k]
    if flags_at_k:
        flag_types = {flag["type"] for flag in flags_at_k}
        if "retry_limit_violation" in flag_types:
            return "flagged_retry_limit_violation", flags_at_k
        if "short_cycle_abab" in flag_types:
            return "flagged_short_cycle_abab", flags_at_k
        return "flagged_identical_or_equivalent_repeat", flags_at_k

    action = focal.get("action")
    response = focal.get("response", {})
    if isinstance(action, dict) and action.get("type") == "tool":
        if action.get("tool") == "poll_job" and response.get("code") in {"RUNNING", "READY"}:
            ident = (action.get("arguments") or {}).get("identifier")
            already_ready = any(
                isinstance(ev.get("action"), dict)
                and ev["action"].get("tool") == "poll_job"
                and (ev["action"].get("arguments") or {}).get("identifier") == ident
                and ev.get("response", {}).get("code") == "READY"
                for ev in prefix
            )
            if not already_ready:
                return "unflagged_legal_job_polling", []

        key = canonical_action(action)
        prior_same = [ev for ev in prefix if canonical_action(ev["action"]) == key]
        if (
            prior_same
            and prior_same[-1].get("response", {}).get("code") == "TIMEOUT"
            and prior_same[-1].get("response", {}).get("retryable") is True
        ):
            return "unflagged_legal_timeout_retry", []

    return "unflagged_genuine_progress_or_first_error", []


def _extract_tool_history(trajectory):
    if isinstance(trajectory.get("actual_tool_history"), list):
        return [
            {"action": copy.deepcopy(e["action"]), "response": copy.deepcopy(e["response"])}
            for e in trajectory["actual_tool_history"]
        ]
    raw_hist = trajectory.get("final_snapshot", {}).get("history", [])
    return [
        {"action": copy.deepcopy(e["action"]), "response": copy.deepcopy(e["response"])}
        for e in raw_hist
        if isinstance(e.get("action"), dict) and e["action"].get("type") not in {"final", "stop"}
    ]


def _cohort_label_for_model_run(config, manifest):
    mode = config.get("mode", "end_to_end")
    wording = config.get("wording_profile")
    if mode == "fixed_failure_prefix":
        return f"fixed_failure_prefix_{wording}" if wording else "fixed_failure_prefix"
    return f"{mode}"


def _load_model_run(run_dir, pinned_sha=None):
    required = ("config.json", "generations.jsonl", "trajectories.jsonl", "case_results.jsonl")
    manifest, manifest_sha = _verify_run_manifest(
        run_dir, "model_inference", required, pinned_manifest_sha=pinned_sha
    )
    has_prefix_contexts = "prefix_contexts.jsonl" in manifest["outputs"]
    if (run_dir / "prefix_contexts.jsonl").exists() and not has_prefix_contexts:
        raise ValueError(f"untracked prefix_contexts.jsonl in {run_dir}")
    prefix_contexts = (
        read_jsonl(run_dir / "prefix_contexts.jsonl") if has_prefix_contexts else None
    )

    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    case_results = read_jsonl(run_dir / "case_results.jsonl")
    trajectories = read_jsonl(run_dir / "trajectories.jsonl")
    generations = read_jsonl(run_dir / "generations.jsonl")
    if len(case_results) != len(trajectories):
        raise ValueError(f"case_results vs trajectories length mismatch in {run_dir}")
    if prefix_contexts is not None and len(prefix_contexts) != len(case_results):
        raise ValueError(f"prefix_contexts length mismatch in {run_dir}")

    run_id = manifest["run_id"]
    cohort = _cohort_label_for_model_run(config, manifest)
    semantic_occurrences = []
    excluded_exact_stops = 0
    detector_occurrences = []

    gen_cursor = 0
    for ep_idx, (cr, tr) in enumerate(zip(case_results, trajectories), 1):
        if cr["case_id"] != tr["case_id"]:
            raise ValueError(f"case_id mismatch at episode {ep_idx} in {run_dir}")
        if "case_seed" in cr and "case_seed" in tr and cr["case_seed"] != tr["case_seed"]:
            raise ValueError(f"case_seed mismatch at episode {ep_idx} in {run_dir}")

        if cr.get("mode") == "fixed_failure_prefix" and "continuation_turns" in cr:
            fresh_turns = cr["continuation_turns"]
        else:
            fresh_turns = cr["turns"]
        ep_gens = generations[gen_cursor : gen_cursor + fresh_turns]
        if len(ep_gens) != fresh_turns:
            raise ValueError(f"insufficient generation rows for {cr['case_id']} in {run_dir}")
        for g in ep_gens:
            if g["case_id"] != cr["case_id"]:
                raise ValueError(f"generation case_id mismatch in {run_dir}")
            if "case_seed" in cr and "case_seed" in g and g["case_seed"] != cr["case_seed"]:
                raise ValueError(f"generation case_seed mismatch in {run_dir}")
        last_gen_line_1based = gen_cursor + fresh_turns if fresh_turns > 0 else None
        gen_cursor += fresh_turns

        tool_history = _extract_tool_history(tr)
        public_request = copy.deepcopy(tr["public_initial_request"])
        domain = tr["domain"]
        public_docs = _public_tool_docs_for_domain(domain, public_request)
        oracle_state = tr.get("final_snapshot", {}).get("state", {})
        oracle_value = oracle_state.get("value")

        for k in range(len(tool_history)):
            stratum, flags_at_k = classify_detector_step(tool_history, k)
            detector_occurrences.append(
                {
                    "public_request": copy.deepcopy(public_request),
                    "public_tool_docs": copy.deepcopy(public_docs),
                    "history_prefix": copy.deepcopy(tool_history[:k]),
                    "focal_step_k": copy.deepcopy(tool_history[k]),
                    "stratum": stratum,
                    "detector_flags_at_k": flags_at_k,
                    "detector_flagged_bool": bool(flags_at_k),
                    "source_evidence_class": "model_inference",
                    "oracle_value": oracle_value,
                    "provenance": {
                        "run_id": run_id,
                        "manifest_sha256": manifest_sha,
                        "trajectories_sha256": manifest["outputs"]["trajectories.jsonl"],
                        "trajectory_line_1based": ep_idx,
                        "case_id": cr["case_id"],
                        "case_seed": cr.get("case_seed"),
                        "comparison_arm": cr.get("comparison_arm") or config.get("comparison_arm", "B0"),
                        "mode": cr.get("mode", config.get("mode", "end_to_end")),
                        "step_index_0based": k,
                    },
                }
            )

        if not ep_gens:
            continue
        last_gen = ep_gens[-1]
        inc_cat, form, parse_obj = _semantic_inclusion_category(cr, last_gen)
        if inc_cat is None:
            if cr.get("exact_stop_reason_code") or cr.get("correct_termination"):
                excluded_exact_stops += 1
            continue

        user_msg = last_gen["messages"][1]["content"]
        tool_names = [t["function"]["name"] for t in last_gen["tools"]]
        guard_events = [
            ev for ev in tr.get("events", []) if ev.get("guard_blocked") is True
        ]
        guard_notice_in_messages = any(
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith("Runtime guard notice")
            for m in last_gen.get("messages", [])
        )
        raw_text = last_gen["raw_text"]
        token_limit = bool(
            cr.get("output_token_limit_reached") or last_gen.get("token_limit_reached")
        )
        semantic_occurrences.append(
            {
                "public_user_request": {
                    "user_message": user_msg,
                    "request_object": _extract_request_object(user_msg, public_request),
                },
                "public_tool_names": tool_names,
                "observed_public_tool_history": copy.deepcopy(tool_history),
                "terminal_response": {
                    "raw_text": raw_text,
                    "terminal_form": form,
                    "parsed_terminal_action": _clean_parsed_terminal_action(parse_obj, form),
                    "token_limit_reached": token_limit,
                },
                "inclusion_category": inc_cat,
                "oracle_value": oracle_value,
                "occurrence": {
                    "run_id": run_id,
                    "manifest_sha256": manifest_sha,
                    "generations_sha256": manifest["outputs"]["generations.jsonl"],
                    "trajectories_sha256": manifest["outputs"]["trajectories.jsonl"],
                    "case_results_sha256": manifest["outputs"]["case_results.jsonl"],
                    "source_cohort": cohort,
                    "source_evidence_class": "model_inference",
                    "case_id": cr["case_id"],
                    "case_seed": cr.get("case_seed"),
                    "comparison_arm": cr.get("comparison_arm") or config.get("comparison_arm", "B0"),
                    "wording_profile": cr.get("wording_profile") or config.get("wording_profile", "legacy"),
                    "turn": last_gen.get("turn", cr.get("turns")),
                    "generation_line_1based": last_gen_line_1based,
                    "trajectory_line_1based": ep_idx,
                    "raw_sha256": sha256_bytes(raw_text.encode("utf-8")),
                    "guard_notice_occurred": bool(guard_events or guard_notice_in_messages),
                    "guard_blocked_count": len(guard_events),
                    "automatic_scores": {
                        "terminal_form": form,
                        "tool_goal_attainment": cr.get("tool_goal_attainment"),
                        "exact_grounded_structured_completion": cr.get(
                            "exact_grounded_structured_completion"
                        ),
                        "unsupported_finish_wrapper": cr.get("unsupported_finish_wrapper"),
                        "natural_termination_semantics_unknown": cr.get(
                            "natural_termination_semantics_unknown"
                        ),
                        "stop_reason_semantics_unknown": cr.get("stop_reason_semantics_unknown"),
                        "budget_exhausted": cr.get("budget_exhausted"),
                        "output_token_limit_reached": token_limit,
                    },
                },
            }
        )

    if gen_cursor != len(generations):
        raise ValueError(
            f"unconsumed generation rows in {run_dir}: {gen_cursor} != {len(generations)}"
        )

    return {
        "run_id": run_id,
        "path": str(run_dir),
        "manifest_sha256": manifest_sha,
        "prefix_contexts_verified": has_prefix_contexts,
        "semantic_occurrences": semantic_occurrences,
        "excluded_exact_stops": excluded_exact_stops,
        "detector_occurrences": detector_occurrences,
    }


def _load_cpu_first_step_run(run_dir, pinned_sha=None):
    required = (
        "first_step_decisions.json",
        "operation_sources/raw_generations.jsonl",
        "operation_sources/public_payloads.jsonl",
    )
    manifest, manifest_sha = _verify_run_manifest(
        run_dir, None, required, pinned_manifest_sha=pinned_sha
    )
    if "operation_sources/status.json" in manifest["outputs"]:
        status = json.loads(
            (run_dir / "operation_sources/status.json").read_text(encoding="utf-8")
        )
        if status.get("evidence_kind") != "model_inference" or status.get("exit_code") != 0:
            raise ValueError(f"invalid operation status in {run_dir}")

    decisions = json.loads((run_dir / "first_step_decisions.json").read_text(encoding="utf-8"))
    raw_lines = (
        (run_dir / "operation_sources/raw_generations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    raw_rows = [(idx + 1, json.loads(line)) for idx, line in enumerate(raw_lines) if line.strip()]
    payload_lines = [
        line
        for line in (run_dir / "operation_sources/public_payloads.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    raw_by_source_line = {}
    for phys_line, row in raw_rows:
        s_line = row["source_line"]
        if s_line in raw_by_source_line:
            raise ValueError(f"duplicate source_line {s_line} in {run_dir}")
        raw_by_source_line[s_line] = (phys_line, row)

    if len(decisions) != len(payload_lines) or len(raw_by_source_line) != len(payload_lines):
        raise ValueError(f"row count mismatch across first-step sources in {run_dir}")

    run_id = manifest["run_id"]
    semantic_occurrences = []
    for dec in sorted(decisions, key=lambda d: d["source_line"]):
        s_line = dec["source_line"]
        if not (1 <= s_line <= len(payload_lines)) or s_line not in raw_by_source_line:
            raise ValueError(f"unmatched source_line {s_line} in {run_dir}")
        raw_phys_line, raw_row = raw_by_source_line[s_line]
        payload_raw_str = payload_lines[s_line - 1]
        if raw_row["payload_sha256"] != sha256_bytes(payload_raw_str.encode("utf-8")):
            raise ValueError(f"payload_sha256 mismatch at source_line {s_line} in {run_dir}")
        payload = json.loads(payload_raw_str)
        messages = payload["messages"]
        tools = payload["tools"]

        tool_history = []
        idx = 2
        while idx + 1 < len(messages):
            asst_msg = messages[idx]
            tool_msg = messages[idx + 1]
            if asst_msg.get("role") == "assistant" and asst_msg.get("tool_calls") and tool_msg.get("role") == "tool":
                call = asst_msg["tool_calls"][0]["function"]
                tool_history.append(
                    {
                        "action": {
                            "type": "tool",
                            "tool": call["name"],
                            "arguments": copy.deepcopy(call["arguments"]),
                        },
                        "response": json.loads(tool_msg["content"]),
                    }
                )
                idx += 2
            else:
                idx += 1

        raw_text = raw_row["raw_text"]
        token_limit = bool(raw_row.get("token_limit_reached", False))
        parse_obj = parse_calibrated_output(raw_text, token_limit)
        form = dec.get("observed_form") or parse_obj.get("terminal_form") or "none"
        if parse_obj.get("valid") and not parse_obj.get("is_terminal"):
            # Single-step non-terminal tool proposal has no terminal claim to grade.
            continue

        if form == "natural_text":
            inc_cat = "natural_text_terminal"
        elif form == "structured_finish":
            ok_obs = [
                ev["response"]
                for ev in tool_history
                if ev["response"].get("code") == "OK"
            ]
            act = parse_obj.get("action") or {}
            if ok_obs and any(
                r.get("identifier") == act.get("identifier") and r.get("value") == act.get("answer")
                for r in ok_obs
            ):
                inc_cat = "grounded_structured_positive_control"
            else:
                inc_cat = "unsupported_finish_wrapper"
        elif form == "structured_stop":
            reason = (parse_obj.get("action") or {}).get("reason")
            unavail = any(
                ev["response"].get("code") == "RESOURCE_UNAVAILABLE" for ev in tool_history
            )
            if unavail and reason == "RESOURCE_UNAVAILABLE":
                continue
            inc_cat = "structured_stop_non_exact"
        else:
            inc_cat = "truncated_or_unparsed_terminal"

        user_msg = messages[1]["content"]
        tool_names = [t["function"]["name"] for t in tools]
        semantic_occurrences.append(
            {
                "public_user_request": {
                    "user_message": user_msg,
                    "request_object": _extract_request_object(user_msg),
                },
                "public_tool_names": tool_names,
                "observed_public_tool_history": tool_history,
                "terminal_response": {
                    "raw_text": raw_text,
                    "terminal_form": form,
                    "parsed_terminal_action": _clean_parsed_terminal_action(parse_obj, form),
                    "token_limit_reached": token_limit,
                },
                "inclusion_category": inc_cat,
                "oracle_value": None,
                "occurrence": {
                    "run_id": run_id,
                    "manifest_sha256": manifest_sha,
                    "raw_generations_sha256": manifest["outputs"][
                        "operation_sources/raw_generations.jsonl"
                    ],
                    "public_payloads_sha256": manifest["outputs"][
                        "operation_sources/public_payloads.jsonl"
                    ],
                    "first_step_decisions_sha256": manifest["outputs"][
                        "first_step_decisions.json"
                    ],
                    "source_cohort": "cpu_first_step",
                    "source_evidence_class": "model_inference",
                    "case_id": dec["case_id"],
                    "case_seed": raw_row.get("seed", 0),
                    "comparison_arm": "B0",
                    "wording_profile": "crossed_neutral_v1",
                    "turn": len(tool_history) + 1,
                    "source_line_1based": s_line,
                    "generation_line_1based": raw_phys_line,
                    "raw_sha256": sha256_bytes(raw_text.encode("utf-8")),
                    "guard_notice_occurred": False,
                    "guard_blocked_count": 0,
                    "automatic_scores": {
                        "terminal_form": form,
                        "observed_last_code": dec.get("code"),
                        "result_tag": dec.get("result"),
                        "output_token_limit_reached": token_limit,
                    },
                },
            }
        )

    return {
        "run_id": run_id,
        "path": str(run_dir),
        "manifest_sha256": manifest_sha,
        "semantic_occurrences": semantic_occurrences,
    }


def _load_synthetic_run(run_dir, pinned_sha=None):
    manifest, manifest_sha = _verify_run_manifest(
        run_dir, "synthetic_harness", ("trajectories.jsonl",), pinned_manifest_sha=pinned_sha
    )
    trajectories = read_jsonl(run_dir / "trajectories.jsonl")
    run_id = manifest["run_id"]
    detector_occurrences = []

    for traj_idx, tr in enumerate(trajectories, 1):
        tool_history = _extract_tool_history(tr)
        public_request = copy.deepcopy(tr["public_initial_request"])
        domain = tr["domain"]
        public_docs = _public_tool_docs_for_domain(domain, public_request)
        oracle_value = tr.get("final_snapshot", {}).get("state", {}).get("value")

        for k in range(len(tool_history)):
            stratum, flags_at_k = classify_detector_step(tool_history, k)
            detector_occurrences.append(
                {
                    "public_request": copy.deepcopy(public_request),
                    "public_tool_docs": copy.deepcopy(public_docs),
                    "history_prefix": copy.deepcopy(tool_history[:k]),
                    "focal_step_k": copy.deepcopy(tool_history[k]),
                    "stratum": stratum,
                    "detector_flags_at_k": flags_at_k,
                    "detector_flagged_bool": bool(flags_at_k),
                    "source_evidence_class": "synthetic_harness",
                    "oracle_value": oracle_value,
                    "provenance": {
                        "run_id": run_id,
                        "manifest_sha256": manifest_sha,
                        "trajectories_sha256": manifest["outputs"]["trajectories.jsonl"],
                        "trajectory_line_1based": traj_idx,
                        "case_id": tr["case_id"],
                        "policy": tr.get("policy"),
                        "mode": tr.get("mode"),
                        "step_index_0based": k,
                    },
                }
            )

    return {
        "run_id": run_id,
        "path": str(run_dir),
        "manifest_sha256": manifest_sha,
        "detector_occurrences": detector_occurrences,
    }


def _run_boundary_scenario(scenario_id, case, actions, target_indices=None):
    """Execute actions strictly through Environment and emit public prefix segments."""
    env = Environment(case)
    for act in actions:
        env.execute(act)
    view = env.public_view()
    tool_history = [
        {"action": copy.deepcopy(e["action"]), "response": copy.deepcopy(e["response"])}
        for e in view["history"]
        if isinstance(e.get("action"), dict) and e["action"].get("type") not in {"final", "stop"}
    ]
    indices = range(len(tool_history)) if target_indices is None else target_indices
    oracle_value = case["hidden"]["value"]
    segments = []
    for k in indices:
        stratum, flags_at_k = classify_detector_step(tool_history, k)
        segments.append(
            {
                "public_request": copy.deepcopy(view["request"]),
                "public_tool_docs": copy.deepcopy(view["tools"]),
                "history_prefix": copy.deepcopy(tool_history[:k]),
                "focal_step_k": copy.deepcopy(tool_history[k]),
                "stratum": stratum,
                "detector_flags_at_k": flags_at_k,
                "detector_flagged_bool": bool(flags_at_k),
                "source_evidence_class": "synthetic_harness_boundary",
                "oracle_value": oracle_value,
                "provenance": {
                    "generator": "environment_boundary_v1",
                    "scenario_id": scenario_id,
                    "step_index_0based": k,
                },
            }
        )
    return segments


def _generate_boundary_detector_segments():
    """Deterministically construct Environment-verified boundary segments across all 6 strata."""
    segments = []
    domain_specs = (
        ("orders", "ORD", "order batch", "dispatched"),
        ("files", "/docs/rec", "audit note", "verified summary line"),
        ("jobs", "JOB", "nightly sync", "completed 40 rows"),
    )

    # 1. Short-cycle ABAB scenarios across all 3 domains (>= 42 ABAB segments + ABA unflagged boundaries)
    for idx in range(14):
        domain, prefix_id, base_target, val = domain_specs[idx % 3]
        primary, locate = DOMAIN_TOOLS[domain]
        valid_id = f"{prefix_id}-{200 + idx}" if domain != "files" else f"{prefix_id}_{200 + idx}.txt"
        stale_id = f"{prefix_id}-OLD-{idx}" if domain != "files" else f"/stale/item_{idx}.txt"
        target = f"{base_target} {idx + 1}"
        wrong_target = f"missing {base_target} {idx + 1}"

        # Variant A: primary(stale) -> INVALID_IDENTIFIER, locate(wrong) -> NOT_FOUND, repeat A, B
        case_a = {
            "case_id": f"bnd-abab-a-{idx}",
            "domain": domain,
            "category": "recoverable",
            "request": {"domain": domain, "identifier": stale_id, "target": target},
            "hidden": {
                "valid_identifier": valid_id,
                "value": f"{val} #{idx}",
                "unavailable": False,
                "transient_remaining": 0,
                "polls_remaining": 0,
            },
        }
        acts_a = [
            tool_call(primary, identifier=stale_id),
            tool_call(locate, target=wrong_target),
            tool_call(primary, identifier=stale_id),
            tool_call(locate, target=wrong_target),
        ]
        segments.extend(_run_boundary_scenario(f"bnd-abab-inv-nf-{idx}", case_a, acts_a, [2, 3]))

        # Variant B: unavailable primary -> RESOURCE_UNAVAILABLE, ask_user -> UNNECESSARY_CLARIFICATION, repeat A, B
        case_b = copy.deepcopy(case_a)
        case_b["hidden"]["unavailable"] = True
        case_b["request"]["identifier"] = valid_id
        acts_b = [
            tool_call(primary, identifier=valid_id),
            tool_call("ask_user", field="identifier"),
            tool_call(primary, identifier=valid_id),
            tool_call("ask_user", field="identifier"),
        ]
        segments.extend(_run_boundary_scenario(f"bnd-abab-unavail-ask-{idx}", case_b, acts_b, [3]))

        # Variant C: missing identifier primary("") -> MISSING_IDENTIFIER, locate(target) -> MISSING_IDENTIFIER, repeat
        case_c = copy.deepcopy(case_a)
        case_c["request"] = {"domain": domain, "target": target}
        acts_c = [
            tool_call(primary, identifier=""),
            tool_call(locate, target=target),
            tool_call(primary, identifier=""),
            tool_call(locate, target=target),
        ]
        segments.extend(_run_boundary_scenario(f"bnd-abab-missing-{idx}", case_c, acts_c, [3]))

    # 2. Retry-limit violations + legal timeout retries across all 3 domains
    for idx in range(18):
        domain, prefix_id, base_target, val = domain_specs[idx % 3]
        primary, locate = DOMAIN_TOOLS[domain]
        valid_id = f"{prefix_id}-{300 + idx}" if domain != "files" else f"{prefix_id}_{300 + idx}.txt"
        target = f"timeout {base_target} {idx + 1}"

        # Exceed timeout retry cap (transient_remaining=4 -> attempts 0,1,2 TIMEOUT, attempt 3 RETRY_LIMIT_EXCEEDED)
        case_cap = {
            "case_id": f"bnd-tcap-{idx}",
            "domain": domain,
            "category": "unrecoverable",
            "request": {"domain": domain, "identifier": valid_id, "target": target},
            "hidden": {
                "valid_identifier": valid_id,
                "value": f"{val} cap-{idx}",
                "unavailable": False,
                "transient_remaining": 4,
                "polls_remaining": 0,
            },
        }
        acts_cap = [tool_call(primary, identifier=valid_id) for _ in range(4)]
        # k=1 and k=2 are legal timeout retries (within 2-retry limit); k=3 is retry_limit_violation!
        segments.extend(_run_boundary_scenario(f"bnd-timeout-cap-{idx}", case_cap, acts_cap, [1, 2, 3]))

        # Legal 1-retry and 2-retry succeeding with OK
        transient_n = 1 if idx % 2 == 0 else 2
        case_ok = {
            "case_id": f"bnd-tok-{idx}",
            "domain": domain,
            "category": "retry_required",
            "request": {"domain": domain, "identifier": valid_id, "target": f"legal {target}"},
            "hidden": {
                "valid_identifier": valid_id,
                "value": f"{val} ok-{idx}",
                "unavailable": False,
                "transient_remaining": transient_n,
                "polls_remaining": 0,
            },
        }
        acts_ok = [tool_call(primary, identifier=valid_id) for _ in range(transient_n + 1)]
        segments.extend(
            _run_boundary_scenario(f"bnd-timeout-ok-{idx}", case_ok, acts_ok, [transient_n])
        )

    # 3. Job polling: legal 1..3 polls + 4th poll RETRY_LIMIT_EXCEEDED
    for idx in range(20):
        job_id = f"JOB-{400 + idx}"
        target = f"pipeline report {idx + 1}"
        # Poll limit violation (polls_remaining=4 -> 3 RUNNING + 4th RETRY_LIMIT_EXCEEDED)
        case_pcap = {
            "case_id": f"bnd-pcap-{idx}",
            "domain": "jobs",
            "category": "unrecoverable",
            "request": {"domain": "jobs", "identifier": job_id, "target": target},
            "hidden": {
                "valid_identifier": job_id,
                "value": f"job output {idx}",
                "unavailable": False,
                "transient_remaining": 0,
                "polls_remaining": 4,
            },
        }
        acts_pcap = [tool_call("poll_job", identifier=job_id) for _ in range(4)]
        # k=0,1,2 are unflagged_legal_job_polling; k=3 is flagged_retry_limit_violation
        segments.extend(_run_boundary_scenario(f"bnd-poll-cap-{idx}", case_pcap, acts_pcap, [1, 2, 3]))

        # Precondition -> poll -> READY -> get_result -> OK
        polls_req = (idx % 3) + 1
        case_pok = {
            "case_id": f"bnd-pok-{idx}",
            "domain": "jobs",
            "category": "retry_required",
            "request": {"domain": "jobs", "identifier": job_id, "target": f"ready {target}"},
            "hidden": {
                "valid_identifier": job_id,
                "value": f"ready output {idx}",
                "unavailable": False,
                "transient_remaining": 0,
                "polls_remaining": polls_req,
            },
        }
        acts_pok = (
            [tool_call("get_result", identifier=job_id)]
            + [tool_call("poll_job", identifier=job_id) for _ in range(polls_req)]
            + [tool_call("get_result", identifier=job_id)]
        )
        segments.extend(
            _run_boundary_scenario(
                f"bnd-poll-ok-{idx}",
                case_pok,
                acts_pok,
                [0, polls_req, polls_req + 1],
            )
        )

    # 4. Equivalent-argument repeats (whitespace & posixpath normpath) + case-sensitive non-repeats
    for idx in range(15):
        file_id = f"/records/sub_{idx}/report.txt"
        bad_id = f"/old/sub_{idx}/report.txt"
        equiv_bad_id = f"  /old/./sub_{idx}/report.txt  "
        case_eq = {
            "case_id": f"bnd-eq-{idx}",
            "domain": "files",
            "category": "recoverable",
            "request": {"domain": "files", "identifier": bad_id, "target": f"file_{idx}.txt"},
            "hidden": {
                "valid_identifier": file_id,
                "value": f"document payload {idx}",
                "unavailable": False,
                "transient_remaining": 0,
                "polls_remaining": 0,
            },
        }
        acts_eq = [
            tool_call("read_file", identifier=bad_id),
            tool_call("read_file", identifier=equiv_bad_id),
            tool_call("search_file", target=f"file_{idx}.txt"),
            tool_call("read_file", identifier=file_id),
        ]
        # k=1 is flagged_identical_or_equivalent_repeat; k=2,3 are unflagged_genuine_progress_or_first_error
        segments.extend(_run_boundary_scenario(f"bnd-equiv-path-{idx}", case_eq, acts_eq, [1, 2, 3]))

        # Case-sensitive distinct path: /Records/... fails, /records/... succeeds -> unflagged progress
        upper_id = f"/Records/sub_{idx}/report.txt"
        case_cs = copy.deepcopy(case_eq)
        case_cs["request"]["identifier"] = upper_id
        acts_cs = [
            tool_call("read_file", identifier=upper_id),
            tool_call("read_file", identifier=file_id),
        ]
        segments.extend(_run_boundary_scenario(f"bnd-casesens-path-{idx}", case_cs, acts_cs, [1]))

    # 5. Clarification + non-retryable repeat across orders/jobs
    for idx in range(12):
        domain, prefix_id, base_target, val = domain_specs[idx % 3]
        primary, _ = DOMAIN_TOOLS[domain]
        valid_id = f"{prefix_id}-{500 + idx}" if domain != "files" else f"{prefix_id}_{500 + idx}.txt"
        case_cl = {
            "case_id": f"bnd-clar-{idx}",
            "domain": domain,
            "category": "clarification",
            "request": {"domain": domain, "target": f"clarify {base_target} {idx}"},
            "hidden": {
                "valid_identifier": valid_id,
                "value": f"{val} clar-{idx}",
                "unavailable": False,
                "transient_remaining": 0,
                "polls_remaining": 0,
            },
        }
        acts_cl = [
            tool_call("ask_user", field="identifier"),
            tool_call("ask_user", field="identifier"),
            tool_call("ask_user", field="identifier"),
            tool_call(primary, identifier=valid_id),
        ]
        # k=0 ask_user -> USER_INFORMATION; k=1 first UNNECESSARY_CLARIFICATION; k=2 repeat UNNECESSARY_CLARIFICATION; k=3 OK
        segments.extend(_run_boundary_scenario(f"bnd-clar-repeat-{idx}", case_cl, acts_cl, [0, 1, 2, 3]))

    return segments


def _build_semantic_pack_and_key(all_occurrences, shuffle_seed):
    grouped = {}
    for entry in all_occurrences:
        sig_obj = {
            "public_user_request": entry["public_user_request"],
            "public_tool_names": entry["public_tool_names"],
            "observed_public_tool_history": entry["observed_public_tool_history"],
            "terminal_response": entry["terminal_response"],
        }
        sig = object_sha256(sig_obj)
        if sig not in grouped:
            grouped[sig] = {
                "signature_sha256": sig,
                "public_fields": copy.deepcopy(sig_obj),
                "inclusion_category": entry["inclusion_category"],
                "oracle_values": set(),
                "occurrences": [],
            }
        if entry["oracle_value"] is not None:
            grouped[sig]["oracle_values"].add(entry["oracle_value"])
        grouped[sig]["occurrences"].append(entry["occurrence"])

    ordered_groups = [grouped[k] for k in sorted(grouped.keys())]
    rng = random.Random(shuffle_seed)
    rng.shuffle(ordered_groups)

    blind_items = []
    key_items = {}
    for idx, grp in enumerate(ordered_groups, 1):
        blind_id = f"BLIND-SEM-{idx:04d}"
        pub = grp["public_fields"]
        item = {
            "blind_id": blind_id,
            "public_user_request": pub["public_user_request"],
            "public_tool_names": pub["public_tool_names"],
            "observed_public_tool_history": pub["observed_public_tool_history"],
            "terminal_response": pub["terminal_response"],
            "reviewer_type": None,
            "reviewer_id": None,
            "factual_grounding_label": None,
            "notes": "",
        }
        blind_items.append(item)
        key_items[blind_id] = {
            "public_payload_sha256": object_sha256(semantic_public_payload(item)),
            "dedup_signature_sha256": grp["signature_sha256"],
            "inclusion_category": grp["inclusion_category"],
            "occurrence_count": len(grp["occurrences"]),
            "oracle_values_for_leakage_audit": sorted(grp["oracle_values"]),
            "occurrences": grp["occurrences"],
        }

    pack_id = f"G2-SEM-{object_sha256([it['blind_id'] + key_items[it['blind_id']]['public_payload_sha256'] for it in blind_items])[:16]}"
    pack = {
        "schema_version": SCHEMA_VERSION,
        "pack_type": SEMANTIC_PACK_TYPE,
        "pack_id": pack_id,
        "allowed_factual_grounding_labels": list(ALLOWED_SEMANTIC_LABELS),
        "annotation_instructions": (
            "Human annotators only (set reviewer_type='human' and non-empty personal reviewer_id). "
            "Judge ONLY whether terminal_response is supported by public_user_request and "
            "observed_public_tool_history. Do not modify public fields."
        ),
        "item_count": len(blind_items),
        "items": blind_items,
    }
    key = {
        "schema_version": SCHEMA_VERSION,
        "key_type": SEMANTIC_KEY_TYPE,
        "pack_id": pack_id,
        "shuffle_seed": shuffle_seed,
        "blank_pack_sha256": None,
        "unique_item_count": len(blind_items),
        "total_occurrence_count": len(all_occurrences),
        "items": key_items,
    }
    return pack, key


def _render_semantic_worksheet(semantic_pack):
    lines = [
        "# G2 盲化终止语义人工复核工作表",
        "",
        f"- Pack ID: `{semantic_pack['pack_id']}`",
        f"- 盲审条目数: `{semantic_pack['item_count']}`",
        "- 要求: **仅限真实人类独立标注**（严禁使用任何 AI/agent 代填）。请在 `blind_semantic_review_pack.json` 中填写 `reviewer_type` (`\"human\"`)、`reviewer_id`、`factual_grounding_label` 与可选 `notes`。",
        "- 允许的 `factual_grounding_label` 枚举值:",
    ]
    for label in ALLOWED_SEMANTIC_LABELS:
        lines.append(f"  - `{label}`")
    lines.append("")

    for item in semantic_pack["items"]:
        lines.extend(
            [
                f"## {item['blind_id']}",
                "",
                "- **公开用户请求**:",
                f"  `{item['public_user_request']['user_message']}`",
                f"- **公开可用工具**: `{', '.join(item['public_tool_names'])}`",
                "- **实际已执行公开工具历史 (`observed_public_tool_history`)**:",
                "```json",
                json.dumps(item["observed_public_tool_history"], ensure_ascii=False, indent=2),
                "```",
                f"- **终止形式**: `{item['terminal_response']['terminal_form']}` | **输出截断**: `{item['terminal_response']['token_limit_reached']}`",
                "- **模型最终原始输出 (`raw_text`)**:",
                "```text",
                item["terminal_response"]["raw_text"],
                "```",
                "- **人工标注栏**:",
                "  - `reviewer_type`: `[ ] human`",
                "  - `reviewer_id`: `________________`",
                "  - `factual_grounding_label`: `________________`",
                "  - `notes`: `________________`",
                "",
            ]
        )
    return "\n".join(lines)


def _build_detector_pack_and_key(
    model_det_occs,
    synthetic_det_occs,
    shuffle_seed,
    min_total_segments=240,
    min_per_stratum=35,
):
    min_total_segments = max(220, int(min_total_segments))
    min_per_stratum = max(15, int(min_per_stratum))

    def make_sig(occ):
        return object_sha256(
            {
                "public_request": occ["public_request"],
                "public_tool_docs": occ["public_tool_docs"],
                "history_prefix": occ["history_prefix"],
                "focal_step_k": occ["focal_step_k"],
            }
        )

    selected = {}
    stratum_counts = {s: 0 for s in MANDATORY_DETECTOR_STRATA}

    def register_occ(occ, allow_new=True):
        sig = make_sig(occ)
        if sig in selected:
            selected[sig]["provenance"].append(occ["provenance"])
            if occ["oracle_value"] is not None:
                selected[sig]["oracle_values"].add(occ["oracle_value"])
            return False
        if not allow_new:
            return False
        selected[sig] = {
            "signature_sha256": sig,
            "public_request": occ["public_request"],
            "public_tool_docs": occ["public_tool_docs"],
            "history_prefix": occ["history_prefix"],
            "focal_step_k": occ["focal_step_k"],
            "stratum": occ["stratum"],
            "source_evidence_class": occ["source_evidence_class"],
            "detector_flags_at_k": occ["detector_flags_at_k"],
            "detector_flagged_bool": occ["detector_flagged_bool"],
            "oracle_values": {occ["oracle_value"]} if occ["oracle_value"] is not None else set(),
            "provenance": [occ["provenance"]],
        }
        stratum_counts[occ["stratum"]] += 1
        return True

    # 1. Include ALL unique real model_inference segments first!
    for occ in model_det_occs:
        register_occ(occ, allow_new=True)

    # 2. Deterministically draw from verified synthetic_harness runs, capping repetitive naive loops per case
    # so one case repeated 11 times does not crowd out diverse strata.
    syn_sorted = sorted(
        synthetic_det_occs,
        key=lambda o: (
            o["provenance"].get("step_index_0based", 0),
            make_sig(o),
        ),
    )
    per_case_stratum_cap = {}
    reserve_synthetic = []
    for occ in syn_sorted:
        sig = make_sig(occ)
        if sig in selected:
            register_occ(occ, allow_new=False)
            continue
        case_key = (occ["provenance"].get("case_id"), occ["stratum"])
        used = per_case_stratum_cap.get(case_key, 0)
        if used < 2 and stratum_counts[occ["stratum"]] < min_per_stratum + 15:
            if register_occ(occ, allow_new=True):
                per_case_stratum_cap[case_key] = used + 1
        else:
            reserve_synthetic.append(occ)

    # 3. Top up each mandatory stratum to >= min_per_stratum and total >= min_total_segments
    # using deterministic Environment-verified boundary segments.
    boundary_occs = _generate_boundary_detector_segments()
    boundary_sorted = sorted(boundary_occs, key=make_sig)

    for stratum in MANDATORY_DETECTOR_STRATA:
        for occ in boundary_sorted:
            if stratum_counts[stratum] >= min_per_stratum:
                break
            if occ["stratum"] == stratum:
                register_occ(occ, allow_new=True)

    # Top up total to >= min_total_segments across boundary then reserve_synthetic
    for occ in boundary_sorted:
        if len(selected) >= min_total_segments:
            break
        if stratum_counts[occ["stratum"]] < min_per_stratum + 15:
            register_occ(occ, allow_new=True)

    for occ in boundary_sorted + reserve_synthetic:
        if len(selected) >= min_total_segments:
            break
        register_occ(occ, allow_new=True)

    for stratum, cnt in stratum_counts.items():
        if cnt < 15:
            raise ValueError(f"stratum {stratum} has only {cnt} segments (< 15 minimum)")
    if len(selected) < 220:
        raise ValueError(f"total detector segments {len(selected)} < 220 minimum")

    ordered = [selected[k] for k in sorted(selected.keys())]
    rng = random.Random(shuffle_seed + 1)
    rng.shuffle(ordered)

    blind_items = []
    key_items = {}
    total_occ_bound = 0
    for idx, seg in enumerate(ordered, 1):
        blind_id = f"BLIND-DET-{idx:04d}"
        item = {
            "blind_id": blind_id,
            "public_request": seg["public_request"],
            "public_tool_docs": seg["public_tool_docs"],
            "history_prefix": seg["history_prefix"],
            "focal_step_k": seg["focal_step_k"],
            "reviewer_type": None,
            "reviewer_id": None,
            "human_is_avoidable_invalid_loop": None,
            "notes": "",
        }
        blind_items.append(item)
        total_occ_bound += len(seg["provenance"])
        key_items[blind_id] = {
            "public_payload_sha256": object_sha256(detector_public_payload(item)),
            "dedup_signature_sha256": seg["signature_sha256"],
            "stratum": seg["stratum"],
            "source_evidence_class": seg["source_evidence_class"],
            "detector_flags_at_k": seg["detector_flags_at_k"],
            "detector_flagged_bool": seg["detector_flagged_bool"],
            "oracle_values_for_leakage_audit": sorted(seg["oracle_values"]),
            "provenance": seg["provenance"],
        }

    pack_id = f"G2-DET-{object_sha256([it['blind_id'] + key_items[it['blind_id']]['public_payload_sha256'] for it in blind_items])[:16]}"
    pack = {
        "schema_version": SCHEMA_VERSION,
        "pack_type": DETECTOR_PACK_TYPE,
        "pack_id": pack_id,
        "annotation_instructions": (
            "Human annotators only (set reviewer_type='human' and non-empty personal reviewer_id). "
            "Given public_request, public_tool_docs, and history_prefix (steps 0..k-1), set "
            "human_is_avoidable_invalid_loop to true ONLY if focal_step_k is an avoidable invalid loop "
            "(prior visible feedback already showed repeating/cycling is futile without new information, "
            "or focal_step_k violates documented retry/poll limits); otherwise set false."
        ),
        "item_count": len(blind_items),
        "items": blind_items,
    }
    key = {
        "schema_version": SCHEMA_VERSION,
        "key_type": DETECTOR_KEY_TYPE,
        "pack_id": pack_id,
        "shuffle_seed": shuffle_seed,
        "blank_pack_sha256": None,
        "unique_segment_count": len(blind_items),
        "total_bound_occurrences": total_occ_bound,
        "items": key_items,
    }
    return pack, key


def verify_zero_leakage(semantic_pack, semantic_key, worksheet_md, detector_pack, detector_key):
    """Hard fail-closed zero-leakage verification across all blind artifacts."""
    violations = []
    undisclosed_oracle_leaks = 0

    sem_json = json.dumps(semantic_pack, ensure_ascii=False)
    for token in FORBIDDEN_BLIND_SUBSTRINGS:
        if token in sem_json:
            violations.append(f"semantic_pack contains forbidden substring: {token}")
        if token in worksheet_md:
            violations.append(f"semantic_worksheet contains forbidden substring: {token}")

    for item in semantic_pack["items"]:
        bid = item["blind_id"]
        item_str = json.dumps(item, ensure_ascii=False)
        for token in FORBIDDEN_BLIND_SUBSTRINGS:
            if token in item_str:
                violations.append(f"{bid} contains forbidden substring: {token}")
        if (
            item["reviewer_type"] is not None
            or item["reviewer_id"] is not None
            or item["factual_grounding_label"] is not None
            or item["notes"] != ""
        ):
            violations.append(f"{bid} pre-populated annotation fields are not blank")

        key_entry = semantic_key["items"][bid]
        disclosed_values = {
            ev.get("response", {}).get("value")
            for ev in item["observed_public_tool_history"]
            if isinstance(ev.get("response"), dict) and "value" in ev["response"]
        }
        non_raw_obj = copy.deepcopy(item)
        raw_text_str = non_raw_obj["terminal_response"].pop("raw_text", "")
        non_raw_str = json.dumps(non_raw_obj, ensure_ascii=False)
        req_str = json.dumps(item["public_user_request"], ensure_ascii=False)
        for oval in key_entry.get("oracle_values_for_leakage_audit", []):
            if oval and oval not in disclosed_values and oval not in req_str:
                if oval not in raw_text_str and oval in item_str:
                    undisclosed_oracle_leaks += 1
                    violations.append(f"{bid} leaked undisclosed oracle value in blind item")
                elif oval in json.dumps(
                    {
                        "public_user_request": item["public_user_request"],
                        "public_tool_names": item["public_tool_names"],
                        "observed_public_tool_history": item["observed_public_tool_history"],
                    },
                    ensure_ascii=False,
                ):
                    undisclosed_oracle_leaks += 1
                    violations.append(f"{bid} leaked undisclosed oracle value in public context")

    det_json = json.dumps(detector_pack, ensure_ascii=False)
    for token in FORBIDDEN_BLIND_SUBSTRINGS + DETECTOR_EXTRA_FORBIDDEN:
        if token in det_json:
            violations.append(f"detector_pack contains forbidden substring: {token}")

    for item in detector_pack["items"]:
        bid = item["blind_id"]
        item_str = json.dumps(item, ensure_ascii=False)
        for token in FORBIDDEN_BLIND_SUBSTRINGS + DETECTOR_EXTRA_FORBIDDEN:
            if token in item_str:
                violations.append(f"{bid} contains forbidden substring: {token}")
        if set(item.keys()) != {
            "blind_id",
            "public_request",
            "public_tool_docs",
            "history_prefix",
            "focal_step_k",
            "reviewer_type",
            "reviewer_id",
            "human_is_avoidable_invalid_loop",
            "notes",
        }:
            violations.append(f"{bid} has unexpected keys: {sorted(item.keys())}")
        if (
            item["reviewer_type"] is not None
            or item["reviewer_id"] is not None
            or item["human_is_avoidable_invalid_loop"] is not None
            or item["notes"] != ""
        ):
            violations.append(f"{bid} pre-populated annotation fields are not blank")

        key_entry = detector_key["items"][bid]
        visible_events = list(item["history_prefix"]) + [item["focal_step_k"]]
        disclosed_values = {
            ev.get("response", {}).get("value")
            for ev in visible_events
            if isinstance(ev.get("response"), dict) and "value" in ev["response"]
        }
        req_str = json.dumps(item["public_request"], ensure_ascii=False)
        for oval in key_entry.get("oracle_values_for_leakage_audit", []):
            if oval and oval not in disclosed_values and oval not in req_str:
                if oval in item_str:
                    undisclosed_oracle_leaks += 1
                    violations.append(f"{bid} leaked undisclosed oracle value")

    if violations:
        raise ValueError("Zero-leakage check failed: " + "; ".join(violations[:5]))

    return {
        "passed": True,
        "checked_forbidden_substrings": list(FORBIDDEN_BLIND_SUBSTRINGS),
        "checked_detector_extra_substrings": list(DETECTOR_EXTRA_FORBIDDEN),
        "semantic_items_checked": len(semantic_pack["items"]),
        "semantic_worksheet_checked": True,
        "detector_items_checked": len(detector_pack["items"]),
        "undisclosed_oracle_value_leaks": undisclosed_oracle_leaks,
        "violations": [],
    }


def _write_exclusive_json(path, obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    data = text.encode("utf-8")
    with Path(path).open("xb") as handle:
        handle.write(data)
    return sha256_bytes(data)


def _write_exclusive_text(path, text):
    if not text.endswith("\n"):
        text += "\n"
    data = text.encode("utf-8")
    with Path(path).open("xb") as handle:
        handle.write(data)
    return sha256_bytes(data)


def build_audit_packs(config, output_dir=None, base_dir=None):
    """Build deterministic G2 blind semantic & detector packs and sealed keys."""
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    shuffle_seed = config.get("shuffle_seed")
    if type(shuffle_seed) is not int:
        raise ValueError("shuffle_seed must be an integer")
    model_run_refs = config.get("model_runs")
    if not isinstance(model_run_refs, list) or not model_run_refs:
        raise ValueError("model_runs must be a non-empty list of completed model_inference runs")
    cpu_run_refs = config.get("cpu_first_step_runs", [])
    syn_run_refs = config.get("synthetic_runs", [])
    if not isinstance(cpu_run_refs, list) or not isinstance(syn_run_refs, list):
        raise ValueError("cpu_first_step_runs and synthetic_runs must be lists")

    root_dir = Path(base_dir).resolve() if base_dir is not None else WORKSPACE_ROOT

    verified_model = []
    all_sem_occs = []
    model_det_occs = []
    excluded_exact_stops = 0
    for ref in model_run_refs:
        rdir, psha = _resolve_run_ref(ref, root_dir)
        loaded = _load_model_run(rdir, pinned_sha=psha)
        verified_model.append(
            {
                "run_id": loaded["run_id"],
                "path": loaded["path"],
                "manifest_sha256": loaded["manifest_sha256"],
                "prefix_contexts_verified": loaded["prefix_contexts_verified"],
            }
        )
        all_sem_occs.extend(loaded["semantic_occurrences"])
        excluded_exact_stops += loaded["excluded_exact_stops"]
        model_det_occs.extend(loaded["detector_occurrences"])

    verified_cpu = []
    for ref in cpu_run_refs:
        rdir, psha = _resolve_run_ref(ref, root_dir)
        loaded = _load_cpu_first_step_run(rdir, pinned_sha=psha)
        verified_cpu.append(
            {
                "run_id": loaded["run_id"],
                "path": loaded["path"],
                "manifest_sha256": loaded["manifest_sha256"],
            }
        )
        all_sem_occs.extend(loaded["semantic_occurrences"])

    verified_syn = []
    syn_det_occs = []
    for ref in syn_run_refs:
        rdir, psha = _resolve_run_ref(ref, root_dir)
        loaded = _load_synthetic_run(rdir, pinned_sha=psha)
        verified_syn.append(
            {
                "run_id": loaded["run_id"],
                "path": loaded["path"],
                "manifest_sha256": loaded["manifest_sha256"],
            }
        )
        syn_det_occs.extend(loaded["detector_occurrences"])

    if not all_sem_occs:
        raise ValueError("no terminal responses found for semantic review pack")

    semantic_pack, semantic_key = _build_semantic_pack_and_key(all_sem_occs, shuffle_seed)
    worksheet_md = _render_semantic_worksheet(semantic_pack)
    detector_pack, detector_key = _build_detector_pack_and_key(
        model_det_occs,
        syn_det_occs,
        shuffle_seed,
        min_total_segments=config.get("min_detector_segments", 240),
        min_per_stratum=config.get("min_per_stratum", 35),
    )

    leakage_report = verify_zero_leakage(
        semantic_pack, semantic_key, worksheet_md, detector_pack, detector_key
    )

    # Pre-compute serialized pack hashes for sealed keys
    sem_pack_bytes = (json.dumps(semantic_pack, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    det_pack_bytes = (json.dumps(detector_pack, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    sem_blank_sha = sha256_bytes(sem_pack_bytes)
    det_blank_sha = sha256_bytes(det_pack_bytes)
    semantic_key["blank_pack_sha256"] = sem_blank_sha
    semantic_key["blind_semantic_review_pack_sha256"] = sem_blank_sha
    detector_key["blank_pack_sha256"] = det_blank_sha
    detector_key["blind_detector_audit_pack_sha256"] = det_blank_sha

    # Build summary statistics
    sem_unique_by_cat = {}
    sem_occ_by_cat = {}
    sem_occ_by_arm = {}
    sem_occ_by_cohort = {}
    for entry in semantic_key["items"].values():
        cat = entry["inclusion_category"]
        sem_unique_by_cat[cat] = sem_unique_by_cat.get(cat, 0) + 1
        sem_occ_by_cat[cat] = sem_occ_by_cat.get(cat, 0) + entry["occurrence_count"]
        for occ in entry["occurrences"]:
            arm = occ["comparison_arm"]
            cohort = occ["source_cohort"]
            sem_occ_by_arm[arm] = sem_occ_by_arm.get(arm, 0) + 1
            sem_occ_by_cohort[cohort] = sem_occ_by_cohort.get(cohort, 0) + 1

    det_by_stratum = {s: 0 for s in MANDATORY_DETECTOR_STRATA}
    det_by_class = {}
    det_by_stratum_and_class = {s: {} for s in MANDATORY_DETECTOR_STRATA}
    for entry in detector_key["items"].values():
        st = entry["stratum"]
        ev_cls = entry["source_evidence_class"]
        det_by_stratum[st] += 1
        det_by_class[ev_cls] = det_by_class.get(ev_cls, 0) + 1
        det_by_stratum_and_class[st][ev_cls] = (
            det_by_stratum_and_class[st].get(ev_cls, 0) + 1
        )

    summary = {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": "analysis",
        "shuffle_seed": shuffle_seed,
        "verified_sources": {
            "model_runs": verified_model,
            "cpu_first_step_runs": verified_cpu,
            "synthetic_runs": verified_syn,
        },
        "semantic_pack": {
            "pack_id": semantic_pack["pack_id"],
            "unique_blind_items": semantic_pack["item_count"],
            "total_source_occurrences": semantic_key["total_occurrence_count"],
            "excluded_exact_stop_episodes": excluded_exact_stops,
            "unique_counts_by_inclusion_category": sem_unique_by_cat,
            "occurrence_counts_by_inclusion_category": sem_occ_by_cat,
            "occurrence_counts_by_comparison_arm": sem_occ_by_arm,
            "occurrence_counts_by_source_cohort": sem_occ_by_cohort,
        },
        "detector_pack": {
            "pack_id": detector_pack["pack_id"],
            "unique_blind_segments": detector_pack["item_count"],
            "total_bound_occurrences": detector_key["total_bound_occurrences"],
            "counts_by_stratum": det_by_stratum,
            "counts_by_source_evidence_class": det_by_class,
            "counts_by_stratum_and_evidence_class": det_by_stratum_and_class,
            "sampling_warning": (
                "Stratified non-uniform audit sample across 6 mandatory strata; "
                "sample proportions MUST NOT be interpreted as natural model loop rates."
            ),
        },
        "zero_leakage_check": leakage_report,
        "human_review_completed": False,
        "training_eligible": False,
    }

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        sem_pack_sha = _write_exclusive_json(out / "blind_semantic_review_pack.json", semantic_pack)
        ws_sha = _write_exclusive_text(out / "blind_semantic_worksheet.md", worksheet_md)
        sem_key_sha = _write_exclusive_json(out / "blind_semantic_key.json", semantic_key)
        det_pack_sha = _write_exclusive_json(out / "blind_detector_audit_pack.json", detector_pack)
        det_key_sha = _write_exclusive_json(out / "blind_detector_key.json", detector_key)
        summary["artifacts"] = {
            "blind_semantic_review_pack.json": sem_pack_sha,
            "blind_semantic_worksheet.md": ws_sha,
            "blind_semantic_key.json": sem_key_sha,
            "blind_detector_audit_pack.json": det_pack_sha,
            "blind_detector_key.json": det_key_sha,
        }
        _write_exclusive_json(out / "g2_pack_summary.json", summary)

    return {
        "semantic_pack": semantic_pack,
        "semantic_worksheet": worksheet_md,
        "semantic_key": semantic_key,
        "detector_pack": detector_pack,
        "detector_key": detector_key,
        "summary": summary,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to JSON config file")
    parser.add_argument("--output", required=True, help="Output directory for generated packs")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    built = build_audit_packs(config, output_dir=Path(args.output))
    print(
        json.dumps(
            {
                "semantic_unique_items": built["summary"]["semantic_pack"]["unique_blind_items"],
                "semantic_total_occurrences": built["summary"]["semantic_pack"][
                    "total_source_occurrences"
                ],
                "detector_unique_segments": built["summary"]["detector_pack"][
                    "unique_blind_segments"
                ],
                "detector_counts_by_stratum": built["summary"]["detector_pack"][
                    "counts_by_stratum"
                ],
                "zero_leakage_passed": built["summary"]["zero_leakage_check"]["passed"],
                "human_review_completed": built["summary"]["human_review_completed"],
                "training_eligible": built["summary"]["training_eligible"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
