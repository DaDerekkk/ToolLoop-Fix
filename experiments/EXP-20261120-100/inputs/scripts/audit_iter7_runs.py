#!/usr/bin/env python3
"""Independent CPU-only recorded-generation replay auditor for ITER-007 model runs."""
import argparse
import collections
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_calibrated_runs import (
    CORE_FILES as E2E_CORE_FILES,
    GENERATION_FIELDS,
    digest,
    read_json,
    read_rows,
    require,
    verify_accounting as verify_e2e_accounting,
    verify_inventory,
)
from scripts.audit_prefix_runs import (
    verify_accounting as verify_prefix_accounting,
    verify_metrics as verify_base_prefix_metrics,
)
from toolloop_fix.baselines import GUARD_VERSION, prompt_for
from toolloop_fix.calibrated_protocol import (
    CALIBRATED_PROTOCOL_VERSION,
    CALIBRATED_SCORE_VERSION,
    CALIBRATED_SYSTEM_PROMPT,
)
from toolloop_fix.harness import ratio, state_hash
from toolloop_fix.iter7_runners import (
    CATEGORIES,
    DOMAINS,
    EXPLICIT_TASK_PREFIX,
    ITER7_RUNNER_VERSION,
    PREFIX_RUNTIME_VERSION,
    PREFIX_SCHEMA_VERSION,
    SUBCOMMANDS,
    TASK_WORDING_VERSION,
    explicit_e2e_metrics,
    make_schedule_prefix_config,
    multiseed_prefix_metrics,
    prefix_decoding_metrics,
    prepare_iter7_prefix,
    resolve_decoding_regime,
    run_explicit_e2e_case,
    run_multiseed_prefix_trial,
    validate_explicit_e2e_config,
    validate_iter7_prefix_config,
    validate_prefix_multiseed_config,
)
from toolloop_fix.prefix_run import (
    prefix_metrics,
    prepare_prefix as prepare_iter4_prefix,
    run_prefix_case,
    validate_config as validate_iter4_prefix_config,
)

PREFIX_CORE_FILES = E2E_CORE_FILES + ("toolloop_fix/prefix_run.py",)
ITER7_E2E_CORE_FILES = E2E_CORE_FILES + ("toolloop_fix/prefix_run.py", "toolloop_fix/iter7_runners.py")
ITER7_PREFIX_CORE_FILES = PREFIX_CORE_FILES + ("toolloop_fix/iter7_runners.py",)
FORBIDDEN_PROMPT_LABEL_KEYS = (
    '"category"',
    '"hidden"',
    '"template_group"',
    '"valid_identifier"',
    '"prefix_role"',
    '"recovery_eligible"',
    '"source_provenance"',
    '"fault_subtype"',
    '"schedule_id"',
    '"trial_id"',
    '"base_prefix_id"',
)


def _is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


class Iter7RecordedBackend:
    """CPU-only recorded generation playback; never loads torch/transformers or returns saved parses."""

    def __init__(self, generations, case, provenance, turn_offset=0, prefix_mode=False, forbidden_ids=()):
        self.generations = generations
        self.case = case
        self.provenance = provenance
        self.turn_offset = turn_offset
        self.prefix_mode = prefix_mode
        self.forbidden_ids = tuple(item for item in forbidden_ids if item)
        self.position = 0

    def generate(self, messages, tools, seed):
        require(self.position < len(self.generations), "replay requested an unrecorded generation")
        row = self.generations[self.position]
        self.position += 1
        expected_turn = self.turn_offset + self.position
        require(row["turn"] == expected_turn, "generation turn mismatch")
        if self.prefix_mode:
            require(row.get("continuation_turn") == self.position, "continuation_turn mismatch")
            require(row.get("phase") == "fresh_continuation", "historical generation counted as fresh")
        require(row["messages"] == messages and row["tools"] == tools, "public dialogue/tools mismatch")
        require(row["seed"] == seed, "case/turn seed mismatch")
        require(isinstance(row.get("prompt_text"), str) and row["prompt_text"], "missing rendered prompt_text")
        require(
            row["prompt_sha256"] == hashlib.sha256(row["prompt_text"].encode()).hexdigest(),
            "prompt hash mismatch",
        )
        for field, token_key in (("input_tokens", "prompt_token_ids"), ("output_tokens", "generated_token_ids")):
            require(type(row.get(field)) is int and row[field] >= 0, "invalid token count: " + field)
            require(
                isinstance(row.get(token_key), list) and row[field] == len(row[token_key]),
                "token count vs token IDs mismatch: " + field,
            )
        require(
            isinstance(row.get("elapsed_seconds"), (int, float))
            and type(row["elapsed_seconds"]) is not bool
            and math.isfinite(row["elapsed_seconds"])
            and row["elapsed_seconds"] >= 0,
            "invalid generation elapsed_seconds",
        )
        require(
            row.get("effective_generation") == self.provenance["effective_generation"],
            "effective_generation settings mismatch",
        )
        public = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
        for label_key in FORBIDDEN_PROMPT_LABEL_KEYS:
            require(label_key not in row["prompt_text"], "evaluation label leaked into prompt_text: " + label_key)
        for hidden_key in ("value", "valid_identifier"):
            hidden_value = self.case["hidden"][hidden_key]
            require(
                not hidden_value or hidden_value not in row["prompt_text"] or hidden_value in public,
                "unseen hidden value leaked into prompt_text: " + hidden_key,
            )
        for identifier in self.forbidden_ids:
            require(identifier not in row["prompt_text"], "identifier leaked into prompt_text: " + identifier)
        return {key: copy.deepcopy(row[key]) for key in GENERATION_FIELDS}


def _detect_run_kind(config, provenance):
    declared = provenance.get("runner_subcommand")
    if declared in SUBCOMMANDS:
        return declared
    if "seed_schedules" in config:
        return "prefix-multiseed"
    if config.get("mode") == "fixed_failure_prefix":
        if "decoding_regime" in provenance or "decoding_regime" in config or config["sampling"]["do_sample"] is False:
            return "prefix-decoding"
        return "iter4-prefix"
    if config.get("mode") == "end_to_end" and config.get("wording_profile") == "explicit":
        return "explicit-e2e"
    raise ValueError("unable to classify ITER-007 run mode")


def _verify_common_provenance_and_manifest(directory, reference):
    require(directory.is_absolute(), "source run path must be absolute")
    require(digest(directory / "manifest.json") == reference["manifest_sha256"], "source manifest pin mismatch")
    manifest = read_json(directory / "manifest.json")
    require(
        manifest.get("kind") == "model_inference"
        and manifest.get("status") == "completed"
        and manifest.get("exit_code") == 0
        and manifest.get("evidence_valid") is True
        and not manifest.get("contract_errors"),
        "source is not a completed valid model_inference record",
    )
    verify_inventory(directory, manifest)
    require(digest(directory / "config.json") == manifest["config_sha256"], "config hash mismatch")
    require(
        digest(ROOT / "toolloop_fix/harness.py") == manifest["inputs"].get("toolloop_fix/harness.py"),
        "core toolloop_fix/harness.py SHA mismatch",
    )
    config = read_json(directory / "config.json")
    provenance = read_json(directory / "model_provenance.json")
    require(
        provenance.get("evidence_class") == "model_inference"
        and provenance.get("model") == config.get("model")
        and provenance.get("sampling") == config.get("sampling"),
        "model/sampling provenance mismatch",
    )
    require(
        Path(provenance["runtime"]["executable"]).resolve() == Path(manifest["command"][0]).resolve(),
        "executed interpreter provenance mismatch",
    )
    require(
        provenance.get("model_files_verified") is True and _is_sha256(provenance.get("chat_template_sha256")),
        "model file or chat template verification not recorded",
    )
    for ref in config["external_inputs"]:
        snapshot = directory / "external_inputs" / (ref["name"] + ".json")
        require(snapshot.is_file() and digest(snapshot) == ref["sha256"], "external manifest snapshot mismatch")
        if ref["name"] == "model_manifest":
            require(
                provenance.get("model_manifest_sha256") == ref["sha256"],
                "model manifest provenance mismatch",
            )
    require(
        provenance.get("protocol_version") == CALIBRATED_PROTOCOL_VERSION
        and provenance.get("score_version") == CALIBRATED_SCORE_VERSION
        and provenance.get("guard_version") == GUARD_VERSION,
        "protocol/score/guard provenance mismatch",
    )
    extra = prompt_for(config["comparison_arm"])
    system_prompt = CALIBRATED_SYSTEM_PROMPT + ("\n\n" + extra if extra else "")
    require(
        provenance.get("system_prompt_sha256") == hashlib.sha256(system_prompt.encode()).hexdigest(),
        "system prompt provenance mismatch",
    )
    return manifest, config, provenance


def _verify_historical_prefix_sources(selected):
    sources = {}
    for row in selected:
        source = Path(row["provenance"]["source_run"])
        if str(source) not in sources:
            source_manifest = read_json(source / "manifest.json")
            verify_inventory(source, source_manifest)
            sources[str(source)] = row["provenance"]["source_manifest_sha256"]
    return sources


def _verify_explicit_e2e_slices(metrics, results):
    group = metrics["metrics"]
    completable = [row for row in results if row["completable"]]
    unavailable = [row for row in results if not row["completable"]]
    for key in ("tool_goal_attainment", "exact_grounded_structured_completion"):
        require(
            group[key] == ratio(sum(row[key] for row in completable), len(completable)),
            "overall completable metric mismatch: " + key,
        )
    for key in ("stopped_after_public_unavailable", "exact_stop_reason_code", "stop_reason_semantics_unknown"):
        require(
            group[key] == ratio(sum(row[key] for row in unavailable), len(unavailable)),
            "overall unavailable metric mismatch: " + key,
        )
    for key in (
        "turns",
        "proposal_attempts",
        "actual_tool_attempts",
        "guard_blocked_proposals",
        "proposed_repeat_count",
        "parser_error_turns",
        "input_tokens",
        "output_tokens",
        "generation_seconds",
    ):
        require(group[key] == sum(row[key] for row in results), "aggregate counter mismatch: " + key)
    for table_key, field, expected_keys in (
        ("by_category", "category", CATEGORIES),
        ("by_domain", "domain", DOMAINS),
    ):
        table = metrics.get(table_key)
        require(isinstance(table, dict) and set(table) == set(expected_keys), "missing slice table: " + table_key)
        for slice_name in expected_keys:
            subset = [row for row in results if row[field] == slice_name]
            sub_comp = [row for row in subset if row["completable"]]
            sub_unavail = [row for row in subset if not row["completable"]]
            cell = table[slice_name]
            require(
                cell["case_count"] == len(subset)
                and cell["completable_count"] == len(sub_comp)
                and cell["unrecoverable_count"] == len(sub_unavail),
                "slice count mismatch: {}.{}".format(table_key, slice_name),
            )
            require(
                cell["tool_goal_attainment"] == ratio(sum(r["tool_goal_attainment"] for r in sub_comp), len(sub_comp))
                and cell["exact_grounded_structured_completion"]
                == ratio(sum(r["exact_grounded_structured_completion"] for r in sub_comp), len(sub_comp))
                and cell["has_invalid_loop"] == ratio(sum(r["has_invalid_loop"] for r in subset), len(subset))
                and cell["premature_stop_without_goal_evidence"]
                == ratio(sum(r["premature_stop_without_goal_evidence"] for r in subset), len(subset))
                and cell["stopped_after_public_unavailable"]
                == ratio(sum(r["stopped_after_public_unavailable"] for r in sub_unavail), len(sub_unavail))
                and cell["exact_stop_reason_code"]
                == ratio(sum(r["exact_stop_reason_code"] for r in sub_unavail), len(sub_unavail))
                and cell["natural_termination_semantics_unknown"]
                == ratio(sum(r["natural_termination_semantics_unknown"] for r in subset), len(subset)),
                "slice metric mismatch: {}.{}".format(table_key, slice_name),
            )


def _verify_multiseed_breakdowns(metrics, results, schedules, case_ids):
    verify_base_prefix_metrics(metrics, results)
    schedule_ids = [item["schedule_id"] for item in schedules]
    require(
        metrics.get("schedule_ids") == schedule_ids
        and metrics.get("schedule_count") == len(schedule_ids)
        and metrics.get("unique_prefix_count") == len(case_ids),
        "multiseed metadata mismatch",
    )
    by_schedule = metrics.get("by_schedule")
    require(isinstance(by_schedule, dict) and set(by_schedule) == set(schedule_ids), "by_schedule keys mismatch")
    for sid in schedule_ids:
        subset = [row for row in results if row["schedule_id"] == sid]
        verify_base_prefix_metrics({"metrics": by_schedule[sid]}, subset)
    by_prefix = metrics.get("by_prefix")
    require(isinstance(by_prefix, dict) and set(by_prefix) == set(case_ids), "by_prefix keys mismatch")
    for cid in case_ids:
        subset = [row for row in results if row["base_prefix_id"] == cid]
        entry = by_prefix[cid]
        require(
            entry["schedule_count"] == len(schedules)
            and entry["trial_ids"] == ["{}@{}".format(cid, sid) for sid in schedule_ids]
            and entry["new_goal_evidence"] == ratio(sum(r["new_goal_evidence"] for r in subset), len(subset))
            and entry["exact_structured_completion_with_new_goal"]
            == ratio(
                sum(r["exact_grounded_structured_completion"] and r["new_goal_evidence"] for r in subset),
                len(subset),
            )
            and entry["guard_blocked_proposals"] == sum(r["guard_blocked_proposals"] for r in subset)
            and entry["continuation_turns"] == sum(r["continuation_turns"] for r in subset),
            "by_prefix entry mismatch: " + cid,
        )


def audit_run(reference):
    before_ml = {name for name in ("torch", "transformers") if name in sys.modules}
    directory = Path(reference["path"])
    manifest, config, provenance = _verify_common_provenance_and_manifest(directory, reference)
    run_kind = _detect_run_kind(config, provenance)
    expected_core = (
        PREFIX_CORE_FILES
        if run_kind == "iter4-prefix"
        else ITER7_E2E_CORE_FILES
        if run_kind == "explicit-e2e"
        else ITER7_PREFIX_CORE_FILES
    )
    for name in expected_core:
        require(digest(ROOT / name) == manifest["inputs"].get(name), "auditor replay code differs from source: " + name)

    data_path = directory / "inputs" / config["cases_path"]
    data_path.resolve().relative_to((directory / "inputs").resolve())
    data_sha = digest(data_path)

    if run_kind in ("prefix-decoding", "iter4-prefix"):
        if run_kind == "prefix-decoding":
            validate_iter7_prefix_config(config)
            regime = resolve_decoding_regime(config)
            require(
                provenance.get("decoding_regime") == regime
                and provenance.get("runner_subcommand") == "prefix-decoding"
                and provenance.get("iter7_runner_version") == ITER7_RUNNER_VERSION,
                "prefix-decoding provenance metadata mismatch",
            )
            prepare_fn = prepare_iter7_prefix
        else:
            validate_iter4_prefix_config(config)
            regime = resolve_decoding_regime(config)
            prepare_fn = prepare_iter4_prefix
        require(
            provenance.get("mode") == "fixed_failure_prefix"
            and provenance.get("prefix_runtime_version") == PREFIX_RUNTIME_VERSION
            and provenance.get("prefix_schema_version") == PREFIX_SCHEMA_VERSION
            and provenance.get("task_wording_version") == TASK_WORDING_VERSION
            and provenance.get("wording_profile") == config["wording_profile"]
            and provenance.get("comparison_arm") == config["comparison_arm"]
            and provenance.get("case_seeds") == config["case_seeds"]
            and provenance.get("prefix_dataset_sha256") == data_sha,
            "prefix provenance mismatch",
        )
        rows = read_rows(data_path)
        by_id = {row["case_id"]: row for row in rows}
        require(len(rows) == len(by_id), "duplicate prefix IDs")
        selected = [by_id[key] for key in config["case_ids"]]
        require(
            provenance.get("prefix_source_rows") == {row["case_id"]: state_hash(row) for row in selected},
            "prefix row provenance mismatch",
        )
        sources = _verify_historical_prefix_sources(selected)
        results = read_rows(directory / "case_results.jsonl")
        traces = read_rows(directory / "trajectories.jsonl")
        contexts = read_rows(directory / "prefix_contexts.jsonl")
        generations = read_rows(directory / "generations.jsonl")
        events = read_rows(directory / "execution_events.jsonl")
        for records in (results, traces, contexts):
            require([r["case_id"] for r in records] == config["case_ids"], "prefix selection/order mismatch")
        by_case = collections.defaultdict(list)
        for generation in generations:
            by_case[generation["case_id"]].append(generation)
        require(set(by_case) <= set(config["case_ids"]), "unconfigured continuation case")
        emitted_generations, emitted_events, emitted_contexts, replayed_results, dialogues = [], [], [], [], {}
        for row, result, trace, context in zip(selected, results, traces, contexts):
            prepared = prepare_fn(row, config)
            require(prepared["context"] == context, "saved prefix context differs from authentic replay")
            dialogues[row["case_id"]] = {"messages": prepared["messages"], "tools": prepared["tools"]}
            raw = by_case[row["case_id"]]
            backend = Iter7RecordedBackend(
                raw,
                row["original_case"],
                provenance,
                turn_offset=len(row["prefix_generations"]),
                prefix_mode=True,
                forbidden_ids=(row["case_id"], row["original_case"]["case_id"]),
            )
            replayed, trajectory = run_prefix_case(
                row,
                backend,
                config,
                prepared=prepared,
                emit_generation=emitted_generations.append,
                emit_event=emitted_events.append,
                emit_prefix_context=emitted_contexts.append,
            )
            require(backend.position == len(raw), "unused generation after terminal/budget")
            require(replayed == result and trajectory == trace, "raw replay result/state mismatch: " + row["case_id"])
            verify_prefix_accounting(result, trace, raw, context, row, config)
            replayed_results.append(replayed)
        require(
            emitted_generations == generations and emitted_events == events and emitted_contexts == contexts,
            "saved parse/guard/event/context mismatch",
        )
        metrics = read_json(directory / "metrics.json")
        expected_metrics = (
            prefix_decoding_metrics(replayed_results, regime)
            if run_kind == "prefix-decoding"
            else prefix_metrics(replayed_results)
        )
        require(expected_metrics == metrics, "saved metrics differ from replay")
        verify_base_prefix_metrics(metrics, results)
        summary = {
            "run_id": manifest["run_id"],
            "path": str(directory),
            "manifest_sha256": reference["manifest_sha256"],
            "runner_subcommand": run_kind,
            "wording_profile": config["wording_profile"],
            "arm": config["comparison_arm"],
            "decoding_regime": regime,
            "case_count": len(results),
            "generation_count": len(generations),
            "metrics": metrics["metrics"],
            "historical_source_manifests": sources,
            "all_cases_replayed": True,
        }
        return {
            "summary": summary,
            "run_kind": run_kind,
            "config": config,
            "provenance": provenance,
            "manifest": manifest,
            "dataset_sha256": data_sha,
            "trial_seeds": dict(config["case_seeds"]),
            "generations_by_case": dict(by_case),
            "contexts_by_case": {c["case_id"]: c for c in contexts},
            "dialogues_by_case": dialogues,
        }

    if run_kind == "prefix-multiseed":
        validate_prefix_multiseed_config(config)
        regime = resolve_decoding_regime(config)
        schedules = config["seed_schedules"]
        expected_trial_seeds = {
            "{}@{}".format(cid, sched["schedule_id"]): sched["case_seeds"][cid]
            for sched in schedules
            for cid in config["case_ids"]
        }
        expected_trial_ids = list(expected_trial_seeds)
        require(
            provenance.get("mode") == "fixed_failure_prefix"
            and provenance.get("prefix_runtime_version") == PREFIX_RUNTIME_VERSION
            and provenance.get("prefix_schema_version") == PREFIX_SCHEMA_VERSION
            and provenance.get("task_wording_version") == TASK_WORDING_VERSION
            and provenance.get("wording_profile") == config["wording_profile"]
            and provenance.get("comparison_arm") == config["comparison_arm"]
            and provenance.get("seed_schedules") == schedules
            and provenance.get("case_seeds") == expected_trial_seeds
            and provenance.get("prefix_dataset_sha256") == data_sha
            and provenance.get("decoding_regime") == regime
            and provenance.get("runner_subcommand") == "prefix-multiseed"
            and provenance.get("iter7_runner_version") == ITER7_RUNNER_VERSION,
            "prefix-multiseed provenance mismatch",
        )
        rows = read_rows(data_path)
        by_id = {row["case_id"]: row for row in rows}
        require(len(rows) == len(by_id), "duplicate prefix IDs")
        selected = [by_id[key] for key in config["case_ids"]]
        require(
            provenance.get("prefix_source_rows") == {row["case_id"]: state_hash(row) for row in selected},
            "prefix row provenance mismatch",
        )
        sources = _verify_historical_prefix_sources(selected)
        results = read_rows(directory / "case_results.jsonl")
        traces = read_rows(directory / "trajectories.jsonl")
        contexts = read_rows(directory / "prefix_contexts.jsonl")
        generations = read_rows(directory / "generations.jsonl")
        events = read_rows(directory / "execution_events.jsonl")
        for records in (results, traces, contexts):
            require(
                [r["case_id"] for r in records] == expected_trial_ids
                and [r["trial_id"] for r in records] == expected_trial_ids,
                "multiseed trial selection/order mismatch",
            )
        by_trial = collections.defaultdict(list)
        for generation in generations:
            require(
                generation.get("case_id") == generation.get("trial_id"),
                "generation case_id/trial_id mismatch",
            )
            by_trial[generation["case_id"]].append(generation)
        require(set(by_trial) <= set(expected_trial_ids), "unconfigured multiseed generation trial")
        emitted_generations, emitted_events, emitted_contexts, replayed_results, dialogues = [], [], [], [], {}
        cursor = 0
        for schedule in schedules:
            sched_config = make_schedule_prefix_config(config, schedule)
            for row in selected:
                trial_id = "{}@{}".format(row["case_id"], schedule["schedule_id"])
                result, trace, context = results[cursor], traces[cursor], contexts[cursor]
                cursor += 1
                prepared = prepare_iter7_prefix(copy.deepcopy(row), sched_config)
                expected_context = {
                    **prepared["context"],
                    "case_id": trial_id,
                    "trial_id": trial_id,
                    "base_prefix_id": row["case_id"],
                    "schedule_id": schedule["schedule_id"],
                }
                require(expected_context == context, "saved multiseed prefix context differs from replay: " + trial_id)
                dialogues[trial_id] = {"messages": prepared["messages"], "tools": prepared["tools"]}
                raw = by_trial[trial_id]
                backend = Iter7RecordedBackend(
                    raw,
                    row["original_case"],
                    provenance,
                    turn_offset=len(row["prefix_generations"]),
                    prefix_mode=True,
                    forbidden_ids=(trial_id, row["case_id"], row["original_case"]["case_id"]),
                )
                replayed, trajectory = run_multiseed_prefix_trial(
                    row,
                    schedule,
                    backend,
                    config,
                    prepared=prepared,
                    emit_generation=emitted_generations.append,
                    emit_event=emitted_events.append,
                    emit_prefix_context=emitted_contexts.append,
                )
                require(backend.position == len(raw), "unused generation after terminal/budget: " + trial_id)
                require(replayed == result and trajectory == trace, "raw multiseed replay mismatch: " + trial_id)
                verify_prefix_accounting(result, trace, raw, context, row, sched_config)
                replayed_results.append(replayed)
        require(
            emitted_generations == generations and emitted_events == events and emitted_contexts == contexts,
            "saved multiseed parse/guard/event/context mismatch",
        )
        metrics = read_json(directory / "metrics.json")
        require(
            multiseed_prefix_metrics(replayed_results, schedules, config["case_ids"], regime) == metrics,
            "saved multiseed metrics differ from replay",
        )
        _verify_multiseed_breakdowns(metrics, results, schedules, config["case_ids"])
        summary = {
            "run_id": manifest["run_id"],
            "path": str(directory),
            "manifest_sha256": reference["manifest_sha256"],
            "runner_subcommand": "prefix-multiseed",
            "wording_profile": config["wording_profile"],
            "arm": config["comparison_arm"],
            "decoding_regime": regime,
            "schedule_ids": [s["schedule_id"] for s in schedules],
            "case_count": len(results),
            "generation_count": len(generations),
            "metrics": metrics["metrics"],
            "historical_source_manifests": sources,
            "all_cases_replayed": True,
        }
        return {
            "summary": summary,
            "run_kind": "prefix-multiseed",
            "config": config,
            "provenance": provenance,
            "manifest": manifest,
            "dataset_sha256": data_sha,
            "trial_seeds": expected_trial_seeds,
            "generations_by_case": dict(by_trial),
            "contexts_by_case": {c["case_id"]: c for c in contexts},
            "dialogues_by_case": dialogues,
        }

    if run_kind == "explicit-e2e":
        validate_explicit_e2e_config(config)
        regime = resolve_decoding_regime(config)
        require(
            provenance.get("mode") == "end_to_end"
            and provenance.get("wording_profile") == "explicit"
            and provenance.get("task_wording_version") == TASK_WORDING_VERSION
            and provenance.get("comparison_arm") == config["comparison_arm"]
            and provenance.get("case_seeds") == config["case_seeds"]
            and provenance.get("cases_dataset_sha256") == data_sha
            and provenance.get("decoding_regime") == regime
            and provenance.get("runner_subcommand") == "explicit-e2e"
            and provenance.get("iter7_runner_version") == ITER7_RUNNER_VERSION,
            "explicit-e2e provenance mismatch",
        )
        rows = read_rows(data_path)
        cases = {row["case_id"]: row for row in rows}
        require(len(cases) == len(rows), "duplicate E2E fixture IDs")
        results = read_rows(directory / "case_results.jsonl")
        traces = read_rows(directory / "trajectories.jsonl")
        generations = read_rows(directory / "generations.jsonl")
        events = read_rows(directory / "execution_events.jsonl")
        require(
            [row["case_id"] for row in results] == config["case_ids"] == [row["case_id"] for row in traces],
            "explicit-e2e case selection/order mismatch",
        )
        by_case = collections.defaultdict(list)
        for row in generations:
            by_case[row["case_id"]].append(row)
        require(set(by_case) == set(config["case_ids"]), "generation cases differ from configured cases")
        replayed_generations, replayed_events, replayed_results, dialogues = [], [], [], {}
        for result, trace in zip(results, traces):
            case = cases[result["case_id"]]
            expected_initial = EXPLICIT_TASK_PREFIX + json.dumps(case["request"], ensure_ascii=False)
            require(
                trace.get("initial_user_content") == expected_initial,
                "explicit-e2e initial_user_content mismatch: " + case["case_id"],
            )
            raw = by_case[case["case_id"]]
            require(
                raw[0]["messages"][1]["content"] == expected_initial,
                "explicit-e2e first generation user message mismatch: " + case["case_id"],
            )
            dialogues[case["case_id"]] = {
                "messages": raw[0]["messages"][:2],
                "tools": raw[0]["tools"],
            }
            backend = Iter7RecordedBackend(
                raw,
                case,
                provenance,
                turn_offset=0,
                prefix_mode=False,
                forbidden_ids=(case["case_id"],),
            )
            replayed, trajectory = run_explicit_e2e_case(
                case,
                backend,
                config,
                emit_generation=replayed_generations.append,
                emit_event=replayed_events.append,
            )
            require(backend.position == len(raw), "unused generations after terminal/budget: " + case["case_id"])
            require(
                replayed == result and trajectory == trace,
                "raw-generation replay result/state mismatch: " + case["case_id"],
            )
            verify_e2e_accounting(result, trace, raw)
            replayed_results.append(replayed)
        require(
            replayed_generations == generations and replayed_events == events,
            "saved explicit-e2e parse/guard/event identity mismatch",
        )
        metrics = read_json(directory / "metrics.json")
        require(
            explicit_e2e_metrics(replayed_results, regime) == metrics,
            "saved explicit-e2e metrics do not match replayed cases",
        )
        _verify_explicit_e2e_slices(metrics, results)
        summary = {
            "run_id": manifest["run_id"],
            "path": str(directory),
            "manifest_sha256": reference["manifest_sha256"],
            "runner_subcommand": "explicit-e2e",
            "wording_profile": "explicit",
            "arm": config["comparison_arm"],
            "decoding_regime": regime,
            "case_count": len(results),
            "generation_count": len(generations),
            "metrics": metrics["metrics"],
            "all_cases_replayed": True,
        }
        return {
            "summary": summary,
            "run_kind": "explicit-e2e",
            "config": config,
            "provenance": provenance,
            "manifest": manifest,
            "dataset_sha256": data_sha,
            "trial_seeds": dict(config["case_seeds"]),
            "generations_by_case": dict(by_case),
            "contexts_by_case": {},
            "dialogues_by_case": dialogues,
        }

    raise ValueError("unsupported run kind: " + run_kind)


def verify_b0_b2_pre_intervention_identity(b0_record, b2_record):
    require(
        b0_record["provenance"]["system_prompt_sha256"] == b2_record["provenance"]["system_prompt_sha256"],
        "B0/B2 system_prompt_sha256 differs",
    )
    require(
        b0_record["trial_seeds"] == b2_record["trial_seeds"],
        "B0/B2 trial seeds differ",
    )
    checked_turns = 0
    post_intervention_diverged_cases = []
    for cid in b0_record["trial_seeds"]:
        gens_b0 = b0_record["generations_by_case"].get(cid, [])
        gens_b2 = b2_record["generations_by_case"].get(cid, [])
        if gens_b0 and gens_b2:
            require(
                gens_b0[0]["messages"] == gens_b2[0]["messages"]
                and gens_b0[0]["tools"] == gens_b2[0]["tools"]
                and gens_b0[0]["seed"] == gens_b2[0]["seed"]
                and gens_b0[0]["prompt_sha256"] == gens_b2[0]["prompt_sha256"],
                "B0/B2 initial fresh turn prompt/seed mismatch: " + cid,
            )
        intervened = False
        for index in range(min(len(gens_b0), len(gens_b2))):
            g0, g2 = gens_b0[index], gens_b2[index]
            if not intervened:
                require(
                    g0["messages"] == g2["messages"]
                    and g0["tools"] == g2["tools"]
                    and g0["seed"] == g2["seed"]
                    and g0["prompt_sha256"] == g2["prompt_sha256"],
                    "B0/B2 pre-intervention prompt/seed diverged before guard block: {} turn {}".format(
                        cid, g0["turn"]
                    ),
                )
            if g0["seed"] == g2["seed"] and g0["prompt_sha256"] == g2["prompt_sha256"]:
                require(
                    g0["raw_text"] == g2["raw_text"]
                    and g0["generated_token_ids"] == g2["generated_token_ids"],
                    "B0/B2 pre-intervention generation mismatch where prompt and seed match: {} turn {}".format(
                        cid, g0["turn"]
                    ),
                )
                checked_turns += 1
            if g2["guard_enforced"]:
                intervened = True
                post_intervention_diverged_cases.append(cid)
                break
        if not intervened:
            require(
                len(gens_b0) == len(gens_b2),
                "B0/B2 episode length differed without any B2 guard intervention: " + cid,
            )
    return {
        "b0_run_id": b0_record["summary"]["run_id"],
        "b2_run_id": b2_record["summary"]["run_id"],
        "run_kind": b0_record["run_kind"],
        "trials_checked": len(b0_record["trial_seeds"]),
        "pre_intervention_turns_verified": checked_turns,
        "b2_intervened_trials": post_intervention_diverged_cases,
    }


def _collect_references(config):
    if "run_dirs" in config:
        refs = config["run_dirs"]
        require(isinstance(refs, list) and refs, "run_dirs must be a nonempty list")
        return refs
    collected = []
    for section in ("prefix_s0_runs", "prefix_multiseed_runs", "prefix_decoding_runs", "e2e_runs", "explicit_e2e_runs"):
        if section not in config:
            continue
        value = config[section]
        if isinstance(value, dict):
            collected.extend(value.values())
        elif isinstance(value, list):
            collected.extend(value)
        else:
            raise ValueError("invalid reference section: " + section)
    require(bool(collected), "at least one run reference is required")
    return collected


def _verify_group_controls(audited):
    arm_groups = collections.defaultdict(dict)
    decoding_groups = collections.defaultdict(list)
    for item in audited:
        key = (
            item["run_kind"],
            item["dataset_sha256"],
            item["summary"]["wording_profile"],
            item["summary"]["decoding_regime"],
            json.dumps(item["trial_seeds"], sort_keys=True),
        )
        arm = item["summary"]["arm"]
        require(arm not in arm_groups[key], "duplicate arm in paired audit group: " + arm)
        arm_groups[key][arm] = item
        if item["run_kind"] in ("prefix-decoding", "iter4-prefix"):
            dec_key = (item["dataset_sha256"], item["summary"]["wording_profile"], arm)
            decoding_groups[dec_key].append(item)

    b0_b2_checks = []
    for key, by_arm in arm_groups.items():
        anchor = next(iter(by_arm.values()))
        cfg_ignore = {"comparison_arm", "policy_name", "notes"}
        prov_ignore = {"comparison_arm", "system_prompt_sha256"}
        for arm, record in by_arm.items():
            require(
                {k: v for k, v in record["config"].items() if k not in cfg_ignore}
                == {k: v for k, v in anchor["config"].items() if k not in cfg_ignore},
                "uncontrolled config difference across paired arms for " + arm,
            )
            require(
                {k: v for k, v in record["provenance"].items() if k not in prov_ignore}
                == {k: v for k, v in anchor["provenance"].items() if k not in prov_ignore},
                "uncontrolled provenance difference across paired arms for " + arm,
            )
            for cid, dialogue in record["dialogues_by_case"].items():
                base_dialogue = anchor["dialogues_by_case"][cid]
                require(
                    dialogue["tools"] == base_dialogue["tools"]
                    and dialogue["messages"][1:] == base_dialogue["messages"][1:],
                    "arm changed public task/history or tools on " + cid,
                )
        if "B0" in by_arm and "B2" in by_arm:
            b0_b2_checks.append(verify_b0_b2_pre_intervention_identity(by_arm["B0"], by_arm["B2"]))

    for dec_key, records in decoding_groups.items():
        base = records[0]
        ctx_ignore = {"decoding_regime"}
        for record in records[1:]:
            require(
                set(record["contexts_by_case"]) == set(base["contexts_by_case"]),
                "prefix-decoding case set mismatch across regimes",
            )
            for cid, ctx in record["contexts_by_case"].items():
                require(
                    {k: v for k, v in ctx.items() if k not in ctx_ignore}
                    == {k: v for k, v in base["contexts_by_case"][cid].items() if k not in ctx_ignore},
                    "prefix snapshot/context differed across decoding regimes on " + cid,
                )
                require(
                    record["dialogues_by_case"][cid] == base["dialogues_by_case"][cid],
                    "prefix initial dialogue/tools differed across decoding regimes on " + cid,
                )
    return b0_b2_checks


def audit_runs(config):
    before_ml = {name for name in ("torch", "transformers") if name in sys.modules}
    require(isinstance(config, dict), "audit config must be an object")
    refs = _collect_references(config)
    seen_paths = set()
    for ref in refs:
        require(isinstance(ref, dict) and isinstance(ref.get("path"), str), "invalid run reference")
        resolved = str(Path(ref["path"]).resolve())
        require(resolved not in seen_paths, "duplicate run path in audit config: " + resolved)
        seen_paths.add(resolved)
    audited = [audit_run(ref) for ref in refs]
    b0_b2_checks = _verify_group_controls(audited)
    after_ml = {name for name in ("torch", "transformers") if name in sys.modules}
    require(after_ml == before_ml, "ML libraries must not be imported during CPU replay audit")
    return {
        "schema_version": 1,
        "evidence_class": "analysis",
        "accepted": True,
        "audit_scope": "iter7_recorded_generation_cpu_replay_no_model_execution",
        "runs": [item["summary"] for item in audited],
        "run_count": len(audited),
        "episodes_replayed": sum(item["summary"]["case_count"] for item in audited),
        "fresh_generations_replayed": sum(item["summary"]["generation_count"] for item in audited),
        "b0_b2_pre_intervention_checks": b0_b2_checks,
        "ml_libraries_loaded": False,
        "limitations": [
            "Same versioned parser/guard/environment/scorer replay establishes consistency and zero prompt leakage, not independent semantic truth or cryptographic attestation.",
            "No PyTorch/Transformers loading, new generation, tokenizer re-encoding, or training was performed.",
            "Natural-language terminal meanings remain ungraded (unknown) until blind human G2 audit.",
            "Multi-seed continuations over 4 recovery prefixes are paired diagnostic observations, not held-out confirmatory proof.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "iter7_audit.json"
    if target.exists():
        raise FileExistsError("refusing to overwrite iter7 audit evidence")
    try:
        result = audit_runs(read_json(Path(args.config)))
    except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
        result = {
            "evidence_class": "analysis",
            "accepted": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    with target.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"accepted": result["accepted"], "output": str(target)}, ensure_ascii=False))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
