"""ITER-009 model & LoRA evaluation runner: explicit-e2e, curated-prefix, and explicit-e2e-multiseed.

Supports both base models (`B0/B1/B2` with `lora_adapter` absent/null) and trained LoRA
adapters (`B3/B4/B5/P` and ablations, enforced to run under `comparison_arm == 'B0'` with
no `B1` prompt addition and no `B2` runtime guard per Proposal §06). Records both detector v1
and frozen G2 `loop_detector_v2` metrics on every case result and trajectory.
"""
import argparse
import copy
import hashlib
import json
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
)
from .harness import Environment, detect_loops, ratio, state_hash
from .iter7_runners import (
    CATEGORIES,
    DOMAINS,
    _validate_common_model_and_sampling,
    _validate_disjoint_seed_ranges,
    resolve_decoding_regime,
)
from .loop_detector_v2 import DETECTOR_V2_VERSION, detect_loops_v2, has_invalid_loop_v2
from .lora_adapter import LORA_ADAPTER_VERSION, base_weight_digest, load_lora_safetensors
from .model_backend import TransformersBackend, sha256_file
from .model_protocol import tool_schemas
from .model_run import ARTIFACTS
from .prefix_run import EXPLICIT_TASK_PREFIX, PREFIX_SCHEMA_VERSION, TASK_WORDING_VERSION

LORA_EVAL_RUNNER_VERSION = "iter9_lora_and_g4_eval_runner_v1"
CURATED_PREFIX_SCHEMA_VERSION = "curated_fixture_prefix_v1"
CURATED_PREFIX_RUNTIME_VERSION = "deterministic_curated_prefix_continuation_v1"
SUBCOMMANDS = ("explicit-e2e", "curated-prefix", "explicit-e2e-multiseed")
VALID_TRAINING_ARMS = (
    "B3",
    "B4",
    "B5",
    "P",
    "P_no_retry",
    "P_no_normal",
    "P_ablation_no_legal_retry",
    "P_ablation_no_normal_retention",
)
PREFIX_ROLES = ("recovery", "normal_completion_control", "unavailable_stop_control")


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def format_canonical_tool_call(name, arguments):
    """Render deterministic canonical Qwen3.5 `<tool_call>` XML for curated prefix replay."""
    lines = ["<tool_call>", "<function={}>".format(name)]
    for key in sorted(arguments.keys()):
        lines.append("<parameter={}>".format(key))
        lines.append(str(arguments[key]))
        lines.append("</parameter>")
    lines.append("</function>")
    lines.append("</tool_call><|im_end|>")
    return "\n".join(lines)


def _validate_external_inputs_decl(config, has_lora):
    refs = config.get("external_inputs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("model inference requires external_inputs references")
    names = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("each external_inputs entry must be an object")
        name = ref.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("invalid external_inputs name: " + repr(name))
        if not isinstance(ref.get("path"), str) or not Path(ref["path"]).is_absolute():
            raise ValueError("external_inputs path must be absolute for " + name)
        if not _is_sha256(ref.get("sha256")):
            raise ValueError("external_inputs sha256 must be 64-hex for " + name)
        names.append(name)
    if names.count("model_manifest") != 1 or len(names) != len(set(names)):
        raise ValueError("external_inputs must have unique names and exactly one model_manifest")
    if has_lora and names.count("adapter_manifest") != 1:
        raise ValueError("LoRA evaluation requires adapter_manifest in config['external_inputs']")
    if not has_lora and "adapter_manifest" in names:
        raise ValueError("adapter_manifest must not be declared when lora_adapter is null/absent")


def validate_lora_adapter_decl(config):
    """Validate optional `lora_adapter` declaration and Proposal §06 B0 prompt/guard invariance."""
    cases_path = config.get("cases_path")
    if (
        not isinstance(cases_path, str)
        or not cases_path
        or Path(cases_path).is_absolute()
        or ".." in Path(cases_path).parts
    ):
        raise ValueError("cases_path must be a project-relative path without '..'")
    if not isinstance(config.get("policy_name"), str) or not config["policy_name"].strip():
        raise ValueError("nonempty policy_name is required")

    lora_cfg = config.get("lora_adapter")
    if lora_cfg is None:
        _validate_external_inputs_decl(config, has_lora=False)
        return None
    if not isinstance(lora_cfg, dict):
        raise ValueError("lora_adapter must be null or an object")

    adapter_id = lora_cfg.get("adapter_id")
    st_path = lora_cfg.get("safetensors_path")
    st_sha = lora_cfg.get("safetensors_sha256")
    training_arm = lora_cfg.get("training_arm")

    if not isinstance(adapter_id, str) or not adapter_id.strip():
        raise ValueError("lora_adapter.adapter_id must be a nonempty string")
    if not isinstance(st_path, str) or not st_path.strip() or not Path(st_path).is_absolute():
        raise ValueError("lora_adapter.safetensors_path must be an absolute path")
    if not _is_sha256(st_sha):
        raise ValueError("lora_adapter.safetensors_sha256 must be a 64-hex SHA256 digest")
    if training_arm not in VALID_TRAINING_ARMS:
        raise ValueError(
            "lora_adapter.training_arm must be one of {}, got {!r}".format(VALID_TRAINING_ARMS, training_arm)
        )
    if "train_seed" in lora_cfg and (type(lora_cfg["train_seed"]) is not int or lora_cfg["train_seed"] < 0):
        raise ValueError("lora_adapter.train_seed must be a nonnegative integer")
    if "rank" in lora_cfg and (type(lora_cfg["rank"]) is not int or lora_cfg["rank"] <= 0):
        raise ValueError("lora_adapter.rank must be a positive integer")
    if "alpha" in lora_cfg and (type(lora_cfg["alpha"]) not in (int, float) or lora_cfg["alpha"] <= 0):
        raise ValueError("lora_adapter.alpha must be a positive number")

    if config.get("comparison_arm") != "B0":
        raise ValueError(
            "Proposal §06 fairness rule: trained LoRA arms must run with comparison_arm == 'B0' "
            "(base prompt, no B1 prompt addition, and no B2 runtime guard)"
        )
    _validate_external_inputs_decl(config, has_lora=True)
    return lora_cfg


def verify_adapter_manifest(config):
    """Verify `adapter_manifest` SHA256 on disk and cross-check `lora_adapter` fields."""
    lora_cfg = config.get("lora_adapter")
    if lora_cfg is None:
        return None, None
    refs = [item for item in config["external_inputs"] if item["name"] == "adapter_manifest"]
    if len(refs) != 1:
        raise ValueError("exactly one pinned adapter_manifest is required for LoRA evaluation")
    ref = refs[0]
    manifest_path = Path(ref["path"])
    if not manifest_path.is_file():
        raise FileNotFoundError("adapter_manifest file not found: {}".format(manifest_path))
    actual_sha = sha256_file(manifest_path)
    if actual_sha != ref["sha256"]:
        raise ValueError("adapter_manifest SHA256 mismatch")

    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_obj, dict):
        raise ValueError("adapter_manifest JSON must be an object")

    for rev_key in ("base_model_revision", "revision"):
        if rev_key in manifest_obj and manifest_obj[rev_key] != config["model"]["revision"]:
            raise ValueError("adapter_manifest {} mismatch with model.revision".format(rev_key))

    entry = None
    if "adapters" in manifest_obj:
        adapters = manifest_obj["adapters"]
        if isinstance(adapters, list):
            matches = [
                item
                for item in adapters
                if isinstance(item, dict) and item.get("adapter_id") == lora_cfg["adapter_id"]
            ]
            if len(matches) != 1:
                raise ValueError(
                    "adapter_manifest.adapters must contain unique entry for " + lora_cfg["adapter_id"]
                )
            entry = matches[0]
        elif isinstance(adapters, dict):
            candidate = adapters.get(lora_cfg["adapter_id"])
            if not isinstance(candidate, dict):
                raise ValueError(
                    "adapter_manifest.adapters missing object entry for " + lora_cfg["adapter_id"]
                )
            entry = candidate
        else:
            raise ValueError("adapter_manifest.adapters must be a list or object")
    else:
        entry = manifest_obj

    recorded_sha = entry.get("safetensors_sha256") or entry.get("sha256")
    if recorded_sha != lora_cfg["safetensors_sha256"]:
        raise ValueError("adapter_manifest safetensors_sha256 does not match config['lora_adapter']")
    if "adapter_id" in entry and entry["adapter_id"] != lora_cfg["adapter_id"]:
        raise ValueError("adapter_manifest adapter_id mismatch")
    if "training_arm" in entry and entry["training_arm"] != lora_cfg["training_arm"]:
        raise ValueError("adapter_manifest training_arm mismatch")
    return actual_sha, entry


def _validate_single_seed_map(config):
    ids = config.get("case_ids")
    seeds = config.get("case_seeds")
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


def _validate_seed_schedules(config):
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
    return entries


def validate_lora_explicit_e2e_config(config):
    _validate_common_model_and_sampling(config, allow_deterministic=True)
    if config.get("mode") != "end_to_end":
        raise ValueError("explicit-e2e requires end_to_end mode")
    if config.get("wording_profile") != "explicit":
        raise ValueError("explicit-e2e requires wording_profile == 'explicit'")
    if config.get("task_wording_version") != TASK_WORDING_VERSION:
        raise ValueError("task wording version mismatch")
    validate_lora_adapter_decl(config)
    _validate_single_seed_map(config)
    resolve_decoding_regime(config)


def validate_lora_explicit_e2e_multiseed_config(config):
    _validate_common_model_and_sampling(config, allow_deterministic=False)
    if config.get("mode") != "end_to_end":
        raise ValueError("explicit-e2e-multiseed requires end_to_end mode")
    if config.get("wording_profile") != "explicit":
        raise ValueError("explicit-e2e-multiseed requires wording_profile == 'explicit'")
    if config.get("task_wording_version") != TASK_WORDING_VERSION:
        raise ValueError("task wording version mismatch")
    validate_lora_adapter_decl(config)
    _validate_seed_schedules(config)
    resolve_decoding_regime(config)


def validate_curated_prefix_config(config):
    has_schedules = "seed_schedules" in config and config["seed_schedules"] is not None
    _validate_common_model_and_sampling(config, allow_deterministic=not has_schedules)
    if config.get("mode") != "fixed_failure_prefix":
        raise ValueError("curated-prefix requires fixed_failure_prefix mode")
    wording = config.get("wording_profile", "explicit")
    if wording != "explicit":
        raise ValueError("curated-prefix requires wording_profile == 'explicit'")
    if config.get("task_wording_version", TASK_WORDING_VERSION) != TASK_WORDING_VERSION:
        raise ValueError("task wording version mismatch")
    if "prefix_schema_version" in config and config["prefix_schema_version"] not in {
        CURATED_PREFIX_SCHEMA_VERSION,
        PREFIX_SCHEMA_VERSION,
    }:
        raise ValueError("unsupported prefix_schema_version: " + repr(config["prefix_schema_version"]))
    validate_lora_adapter_decl(config)
    if has_schedules:
        _validate_seed_schedules(config)
    else:
        _validate_single_seed_map(config)
    resolve_decoding_regime(config)


def _arm_identity_fields(config):
    lora_cfg = config.get("lora_adapter")
    if lora_cfg is None:
        return {
            "evaluated_arm": config["comparison_arm"],
            "training_arm": None,
            "adapter_id": None,
            "train_seed": None,
            "lora_enabled": False,
        }
    return {
        "evaluated_arm": lora_cfg["training_arm"],
        "training_arm": lora_cfg["training_arm"],
        "adapter_id": lora_cfg["adapter_id"],
        "train_seed": lora_cfg.get("train_seed"),
        "lora_enabled": True,
    }


def _init_backend_and_adapter(config, initial_tools, backend_factory):
    lora_cfg = config.get("lora_adapter")
    adapter_manifest_sha, adapter_entry = verify_adapter_manifest(config)
    backend = backend_factory(config, initial_tools)
    if lora_cfg is None:
        return backend, {
            "lora_enabled": False,
            "lora_adapter": None,
            "evaluated_arm": config["comparison_arm"],
            "training_arm": None,
            "adapter_id": None,
            "train_seed": None,
            "comparison_arm": config["comparison_arm"],
            "adapter_manifest_sha256": None,
        }

    model_obj = getattr(backend, "model", None)
    if model_obj is not None:
        pre_digest = base_weight_digest(model_obj)
        loaded = load_lora_safetensors(
            model_obj,
            lora_cfg["safetensors_path"],
            expected_sha256=lora_cfg["safetensors_sha256"],
        )
        post_digest = base_weight_digest(model_obj)
        if pre_digest != post_digest:
            raise RuntimeError("base model weights were mutated while loading LoRA safetensors")
    elif callable(getattr(backend, "load_lora_adapter", None)):
        actual_st_sha = sha256_file(lora_cfg["safetensors_path"])
        if actual_st_sha != lora_cfg["safetensors_sha256"]:
            raise ValueError("LoRA safetensors SHA256 mismatch on backend hook")
        loaded = backend.load_lora_adapter(copy.deepcopy(lora_cfg))
        pre_digest = loaded.get("base_weight_digest_sha256")
        post_digest = pre_digest
    else:
        raise RuntimeError("backend exposes neither .model nor .load_lora_adapter for LoRA evaluation")

    if "rank" in lora_cfg and int(loaded["rank"]) != int(lora_cfg["rank"]):
        raise ValueError("loaded LoRA rank does not match config['lora_adapter']['rank']")
    if "alpha" in lora_cfg and float(loaded["alpha"]) != float(lora_cfg["alpha"]):
        raise ValueError("loaded LoRA alpha does not match config['lora_adapter']['alpha']")

    resolved_train_seed = lora_cfg.get("train_seed")
    if resolved_train_seed is None and isinstance(adapter_entry, dict):
        for skey in ("train_seed", "seed"):
            if type(adapter_entry.get(skey)) is int:
                resolved_train_seed = adapter_entry[skey]
                break
    if resolved_train_seed is None:
        meta_seed = (loaded.get("metadata") or {}).get("seed")
        if isinstance(meta_seed, str) and meta_seed.isdigit():
            resolved_train_seed = int(meta_seed)

    provenance = {
        "lora_enabled": True,
        "adapter_version": loaded.get("adapter_version", LORA_ADAPTER_VERSION),
        "adapter_id": lora_cfg["adapter_id"],
        "training_arm": lora_cfg["training_arm"],
        "evaluated_arm": lora_cfg["training_arm"],
        "comparison_arm": config["comparison_arm"],
        "safetensors_path": lora_cfg["safetensors_path"],
        "safetensors_sha256": loaded["sha256"],
        "safetensors_size_bytes": loaded.get("size_bytes"),
        "tensor_count": loaded.get("tensor_count"),
        "loaded_module_count": loaded.get("loaded_module_count"),
        "rank": int(loaded["rank"]),
        "alpha": float(loaded["alpha"]),
        "safetensors_metadata": copy.deepcopy(loaded.get("metadata", {})),
        "adapter_manifest_sha256": adapter_manifest_sha,
        "base_weight_digest_sha256": post_digest,
        "base_weights_unchanged_on_load": (pre_digest == post_digest) if pre_digest is not None else None,
        "train_seed": resolved_train_seed,
    }
    return backend, provenance


def _open_output_writers(output, include_prefix_contexts=False):
    output.mkdir(parents=True, exist_ok=True)
    reserved = (*ARTIFACTS, "prefix_contexts.jsonl") if include_prefix_contexts else ARTIFACTS
    if any((output / name).exists() for name in reserved):
        raise FileExistsError("refusing to overwrite model artifacts")
    handles = {}

    def write_json(name, value):
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def emit(name, value):
        if name not in handles:
            handles[name] = (output / name).open("x", encoding="utf-8")
        handles[name].write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handles[name].flush()

    return handles, write_json, emit


def run_lora_explicit_e2e_case(
    case,
    backend,
    config,
    case_seed=None,
    emit_generation=None,
    emit_event=None,
    emit_partial=None,
):
    """Run calibrated explicit E2E episode with both v1 and G2-frozen v2 loop detection."""
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

    resolved_seed = int(config["case_seeds"][case["case_id"]] if case_seed is None else case_seed)
    identity = {
        "case_id": case["case_id"],
        "domain": case["domain"],
        "category": case["category"],
        "template_group": case["template_group"],
        "policy": config["policy_name"],
        "comparison_arm": config["comparison_arm"],
        **_arm_identity_fields(config),
        "wording_profile": "explicit",
        "task_wording_version": TASK_WORDING_VERSION,
        "mode": "end_to_end",
        "evidence_class": "model_inference",
        "protocol_version": CALIBRATED_PROTOCOL_VERSION,
        "case_seed": resolved_seed,
    }
    if "fault_subtype" in case:
        identity["fault_subtype"] = case["fault_subtype"]
    if "split" in case:
        identity["split"] = case["split"]

    while env.snapshot()["terminal"] is None:
        if attempts >= config["tool_budget"] or turns >= config["turn_budget"]:
            exhausted = True
            break
        seed = resolved_seed + turns
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
    history = public_tool_view(env)["history"]
    flags_v1 = detect_loops(history)
    flags_v2 = detect_loops_v2(history)
    inv_v2 = has_invalid_loop_v2(history, start_index=0)
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
        "has_invalid_loop": bool(flags_v1),
        "loop_types": sorted({flag["type"] for flag in flags_v1}),
        "has_invalid_loop_v2": bool(inv_v2),
        "loop_types_v2": sorted({flag["type"] for flag in flags_v2}),
        "loop_flags_v2": flags_v2,
        "detector_v2_version": DETECTOR_V2_VERSION,
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
        "loop_flags": flags_v1,
        "has_invalid_loop": bool(flags_v1),
        "loop_flags_v2": flags_v2,
        "has_invalid_loop_v2": bool(inv_v2),
        "detector_v2_version": DETECTOR_V2_VERSION,
        "loop_index_scope": "actual_tool_history_only",
        "actual_tool_history": history,
        "final_snapshot": env.snapshot(),
    }
    return result, trajectory


def prepare_curated_prefix(case, config):
    """Deterministically replay `case['fixed_prefix']` in `Environment` under `EXPLICIT_TASK_PREFIX`."""
    fixed_prefix = case.get("fixed_prefix")
    if not isinstance(fixed_prefix, list) or not fixed_prefix:
        raise ValueError(
            "curated-prefix requires nonempty fixed_prefix list on case {!r}".format(case.get("case_id"))
        )
    count = len(fixed_prefix)
    if count >= config["tool_budget"] or count >= config["turn_budget"]:
        raise ValueError(
            "curated fixed_prefix length {} leaves zero continuation budget (tool_budget={}, turn_budget={})".format(
                count, config["tool_budget"], config["turn_budget"]
            )
        )

    env = Environment(case)
    initial_snapshot = env.snapshot()
    view = env.public_view()
    messages = messages_for_calibrated_view(view, [], prompt_for(config["comparison_arm"]))
    messages[1]["content"] = EXPLICIT_TASK_PREFIX + json.dumps(view["request"], ensure_ascii=False)
    initial_user_content = messages[1]["content"]

    decisions, replayed = [], []
    inherited_proposals = 0
    for turn, raw_action in enumerate(fixed_prefix, 1):
        if (
            not isinstance(raw_action, dict)
            or raw_action.get("type") != "tool"
            or not isinstance(raw_action.get("tool"), str)
            or not isinstance(raw_action.get("arguments"), dict)
        ):
            raise ValueError("curated fixed_prefix step {} must be a valid tool action".format(turn))
        raw_xml = format_canonical_tool_call(raw_action["tool"], raw_action["arguments"])
        decision = parse_calibrated_output(raw_xml, False)
        if not decision["valid"] or decision["is_terminal"] or decision["action"].get("type") != "tool":
            raise ValueError("synthesized canonical XML failed calibrated tool parse at turn {}".format(turn))

        before = env.snapshot()
        guard = guard_action(public_tool_view(env), decision["action"])
        inherited_proposals += int(guard["blocked"])
        actual = env.execute(decision["action"])
        event = {
            **actual,
            "phase": "curated_prefix",
            "turn": turn,
            "guard_blocked": False,
            "executed": True,
            "actual_tool_dispatch": True,
            "event_kind": "replayed_curated_tool_response",
            "guard": guard,
            "snapshot_before_sha256": state_hash(before),
            "snapshot_after_sha256": state_hash(env.snapshot()),
        }
        append_feedback(messages, decision, event, turn)
        decisions.append(decision)
        replayed.append(event)

    snapshot = env.snapshot()
    prefix_score = score_calibrated_episode(case, snapshot, decisions, False)
    goal_at_prefix = bool(prefix_score["tool_goal_attainment"])

    declared_role = case.get("prefix_role") or case.get("role")
    if declared_role is not None:
        if declared_role not in PREFIX_ROLES:
            raise ValueError("unsupported curated prefix role: " + repr(declared_role))
        role = declared_role
    elif case["category"] == "unrecoverable":
        role = "unavailable_stop_control"
    elif goal_at_prefix:
        role = "normal_completion_control"
    else:
        role = "recovery"

    context = {
        "evidence_class": "model_inference",
        "case_id": case["case_id"],
        "original_case_id": case["case_id"],
        "role": role,
        "prefix_schema_version": CURATED_PREFIX_SCHEMA_VERSION,
        "prefix_runtime_version": CURATED_PREFIX_RUNTIME_VERSION,
        "source_provenance": {
            "prefix_origin": "curated_fixture_fixed_prefix",
            "case_id": case["case_id"],
            "fixed_prefix_sha256": state_hash(fixed_prefix),
        },
        "prefix_row_sha256": state_hash(case),
        "prefix_turns": count,
        "prefix_tool_attempts": count,
        "prefix_snapshot_sha256": state_hash(snapshot),
        "prefix_public_history_sha256": state_hash(env.public_view()["history"]),
        "rebuilt_dialogue_sha256": state_hash(messages),
        "shared_non_system_dialogue_sha256": state_hash(messages[1:]),
        "wording_profile": "explicit",
        "task_wording_version": TASK_WORDING_VERSION,
        "comparison_arm": config["comparison_arm"],
        **_arm_identity_fields(config),
        "source_system_prompt_reused": False,
        "goal_at_prefix": goal_at_prefix,
        "historical_input_tokens": 0,
        "historical_output_tokens": 0,
        "historical_generation_seconds": 0.0,
        "prefix_fresh_generations": 0,
        "prefix_proposed_repeat_count": inherited_proposals,
        "remaining_tool_budget": config["tool_budget"] - count,
        "remaining_turn_budget": config["turn_budget"] - count,
        "total_tool_budget": config["tool_budget"],
        "total_turn_budget": config["turn_budget"],
        "actual_prefix_snapshot": snapshot,
        "replayed_prefix_events": replayed,
        "initial_user_content": initial_user_content,
    }
    return {
        "case": copy.deepcopy(case),
        "initial_snapshot": initial_snapshot,
        "messages": messages,
        "tools": tool_schemas(env.public_view()),
        "decisions": decisions,
        "context": context,
    }


def run_curated_prefix_case(
    case,
    backend,
    config,
    case_seed,
    prepared=None,
    emit_generation=None,
    emit_event=None,
    emit_partial=None,
    emit_prefix_context=None,
):
    """Run real model continuation from a deterministically replayed `case['fixed_prefix']` snapshot."""
    prepared = prepared if prepared is not None else prepare_curated_prefix(case, config)
    context = prepared["context"]
    if (
        state_hash(case) != context["prefix_row_sha256"]
        or config["comparison_arm"] != context["comparison_arm"]
        or config["tool_budget"] != context["total_tool_budget"]
        or config["turn_budget"] != context["total_turn_budget"]
    ):
        raise ValueError("prepared curated prefix belongs to a different case or condition")

    env = Environment(case)
    env.restore(copy.deepcopy(context["actual_prefix_snapshot"]))
    messages = copy.deepcopy(prepared["messages"])
    tools = copy.deepcopy(prepared["tools"])
    decisions = copy.deepcopy(prepared["decisions"])
    count = context["prefix_turns"]
    traces = copy.deepcopy(context["replayed_prefix_events"])
    attempts = turns = actual_tools = count
    blocks = repeats = parser_errors = input_tokens = output_tokens = continuation_turns = 0
    seconds = 0.0
    exhausted = output_limit = False
    resolved_seed = int(case_seed)
    role = context["role"]

    identity = {
        "case_id": case["case_id"],
        "original_case_id": case["case_id"],
        "domain": case["domain"],
        "category": case["category"],
        "template_group": case["template_group"],
        "prefix_role": role,
        "policy": config["policy_name"],
        "comparison_arm": config["comparison_arm"],
        **_arm_identity_fields(config),
        "wording_profile": "explicit",
        "task_wording_version": TASK_WORDING_VERSION,
        "mode": "fixed_failure_prefix",
        "evidence_class": "model_inference",
        "protocol_version": CALIBRATED_PROTOCOL_VERSION,
        "prefix_runtime_version": CURATED_PREFIX_RUNTIME_VERSION,
        "case_seed": resolved_seed,
    }
    if "fault_subtype" in case:
        identity["fault_subtype"] = case["fault_subtype"]
    if "split" in case:
        identity["split"] = case["split"]

    if emit_prefix_context:
        emit_prefix_context(copy.deepcopy(context))

    while env.snapshot()["terminal"] is None:
        if attempts >= config["tool_budget"] or turns >= config["turn_budget"]:
            exhausted = True
            break
        seed = resolved_seed + turns
        try:
            generation = backend.generate(copy.deepcopy(messages), copy.deepcopy(tools), seed)
        except Exception as exc:
            if emit_generation:
                emit_generation(
                    {
                        **identity,
                        "turn": turns + 1,
                        "continuation_turn": continuation_turns + 1,
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
                    "continuation_turn": continuation_turns + 1,
                    "phase": "fresh_continuation",
                    "messages": copy.deepcopy(messages),
                    "tools": tools,
                    **generation,
                    "parse": copy.deepcopy(decision),
                    "guard_observation": guard,
                    "guard_enforced": blocked,
                }
            )
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
            event = {
                "action": copy.deepcopy(action),
                "response": None,
                "state_before": state_hash(before["state"]),
                "state_after": state_hash(before["state"]),
            }
            kind = "guard_blocked_proposal"
        else:
            event = env.execute(action)
            kind = (
                "actual_tool_response"
                if dispatch
                else "internal_terminal_lifecycle"
                if decision["is_terminal"]
                else "internal_protocol_rejection"
            )
        event.update(
            phase="fresh_continuation",
            turn=turns,
            continuation_turn=continuation_turns,
            event_kind=kind,
            executed=not blocked,
            actual_tool_dispatch=dispatch,
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
    history = public_tool_view(env)["history"]
    all_flags_v1 = detect_loops(history)
    inherited_v1 = [flag for flag in all_flags_v1 if flag["index"] < count]
    new_flags_v1 = [flag for flag in all_flags_v1 if flag["index"] >= count]

    all_flags_v2 = detect_loops_v2(history)
    inherited_v2 = [flag for flag in all_flags_v2 if flag["index"] < count]
    new_flags_v2 = [flag for flag in all_flags_v2 if flag["index"] >= count]
    inv_v2 = has_invalid_loop_v2(history, start_index=count)

    new_goal = bool(outcome["tool_goal_attainment"] and not context["goal_at_prefix"])
    result = {
        **identity,
        **outcome,
        "prefix_turns": count,
        "prefix_tool_attempts": count,
        "prefix_actual_tool_attempts": count,
        "turns": turns,
        "continuation_turns": continuation_turns,
        "tool_attempts": attempts,
        "proposal_attempts": attempts,
        "continuation_proposal_attempts": attempts - count,
        "actual_tool_attempts": actual_tools,
        "continuation_actual_tool_attempts": actual_tools - count,
        "prefix_snapshot_sha256": context["prefix_snapshot_sha256"],
        "prefix_public_history_sha256": context["prefix_public_history_sha256"],
        "prefix_row_sha256": context["prefix_row_sha256"],
        "remaining_tool_budget_at_start": context["remaining_tool_budget"],
        "remaining_turn_budget_at_start": context["remaining_turn_budget"],
        "goal_at_prefix": context["goal_at_prefix"],
        "new_goal_evidence": new_goal,
        "recovery_eligible": bool(role == "recovery" and not context["goal_at_prefix"] and outcome["completable"]),
        "guard_blocked_proposals": blocks,
        "proposed_repeat_count": repeats,
        "prefix_proposed_repeat_count": context["prefix_proposed_repeat_count"],
        "has_inherited_loop": bool(inherited_v1),
        "has_invalid_loop": bool(new_flags_v1),
        "loop_types": sorted({flag["type"] for flag in new_flags_v1}),
        "has_inherited_loop_v2": bool(inherited_v2),
        "has_invalid_loop_v2": bool(inv_v2),
        "loop_types_v2": sorted({flag["type"] for flag in new_flags_v2}),
        "loop_flags_v2": new_flags_v2,
        "detector_v2_version": DETECTOR_V2_VERSION,
        "parser_error_turns": parser_errors,
        "output_token_limit_reached": output_limit,
        "historical_input_tokens": 0,
        "historical_output_tokens": 0,
        "historical_generation_seconds": 0.0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "generation_seconds": seconds,
        "continuation_input_tokens": input_tokens,
        "continuation_output_tokens": output_tokens,
        "prefix_fresh_generations": 0,
        "cost_scope": "input/output_tokens and generation_seconds are fresh continuation only; curated prefix replayed deterministically",
        "budget_scope": "total prefix plus all continuation proposals, including blocked and invalid; terminal excluded from tool budget",
    }
    trajectory = {
        **identity,
        "public_initial_request": case["request"],
        "initial_user_content": context["initial_user_content"],
        "source_provenance": context["source_provenance"],
        "initial_snapshot": prepared["initial_snapshot"],
        "prefix_snapshot": context["actual_prefix_snapshot"],
        "prefix_snapshot_sha256": context["prefix_snapshot_sha256"],
        "prefix_public_history_sha256": context["prefix_public_history_sha256"],
        "events": traces,
        "loop_flags": all_flags_v1,
        "prefix_loop_flags": inherited_v1,
        "continuation_loop_flags": new_flags_v1,
        "has_invalid_loop": bool(new_flags_v1),
        "loop_flags_v2": all_flags_v2,
        "prefix_loop_flags_v2": inherited_v2,
        "continuation_loop_flags_v2": new_flags_v2,
        "has_invalid_loop_v2": bool(inv_v2),
        "detector_v2_version": DETECTOR_V2_VERSION,
        "loop_index_scope": "actual_tool_history_including_prefix",
        "actual_tool_history": history,
        "final_snapshot": env.snapshot(),
    }
    return result, trajectory


def _slice_e2e_v2_breakdown(rows):
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
        "has_invalid_loop_v2": ratio(sum(row["has_invalid_loop_v2"] for row in rows), len(rows)),
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


def _slice_prefix_v2_breakdown(rows):
    recovery = [row for row in rows if row["recovery_eligible"]]
    return {
        "case_count": len(rows),
        "recovery_eligible_count": len(recovery),
        "new_goal_evidence_recovery": ratio(sum(row["new_goal_evidence"] for row in recovery), len(recovery)),
        "exact_structured_completion_recovery": ratio(
            sum(row["exact_grounded_structured_completion"] and row["new_goal_evidence"] for row in recovery),
            len(recovery),
        ),
        "has_invalid_loop": ratio(sum(row["has_invalid_loop"] for row in rows), len(rows)),
        "has_invalid_loop_v2": ratio(sum(row["has_invalid_loop_v2"] for row in rows), len(rows)),
        "premature_stop_without_goal_evidence": ratio(
            sum(row["premature_stop_without_goal_evidence"] for row in rows), len(rows)
        ),
    }


def build_lora_explicit_e2e_metrics(results, decoding_regime, config, subcommand="explicit-e2e"):
    unknown_categories = {row["category"] for row in results} - set(CATEGORIES)
    unknown_domains = {row["domain"] for row in results} - set(DOMAINS)
    if unknown_categories or unknown_domains:
        raise ValueError("unexpected category or domain in explicit-e2e results")
    metrics = calibrated_metrics(results)
    metrics["metrics"]["has_invalid_loop_v2"] = ratio(
        sum(row["has_invalid_loop_v2"] for row in results), len(results)
    )
    metrics.update(
        mode="end_to_end",
        wording_profile="explicit",
        task_wording_version=TASK_WORDING_VERSION,
        decoding_regime=decoding_regime,
        runner_subcommand=subcommand,
        lora_eval_runner_version=LORA_EVAL_RUNNER_VERSION,
        detector_v2_version=DETECTOR_V2_VERSION,
        **_arm_identity_fields(config),
        by_category={
            category: _slice_e2e_v2_breakdown([row for row in results if row["category"] == category])
            for category in CATEGORIES
        },
        by_domain={
            domain: _slice_e2e_v2_breakdown([row for row in results if row["domain"] == domain])
            for domain in DOMAINS
        },
        warning=(
            "Explicit read-and-report end_to_end evaluation ({} episodes); reports both v1 and G2-frozen v2 "
            "loop detectors. Natural-language semantics remain ungraded.".format(len(results))
        ),
    )
    return metrics


def build_lora_explicit_e2e_multiseed_metrics(results, seed_schedules, case_ids, decoding_regime, config):
    metrics = build_lora_explicit_e2e_metrics(
        results, decoding_regime, config, subcommand="explicit-e2e-multiseed"
    )
    schedule_ids = [item["schedule_id"] for item in seed_schedules]
    by_schedule = {}
    for sid in schedule_ids:
        subset = [row for row in results if row["schedule_id"] == sid]
        if not subset:
            raise ValueError("missing results for schedule " + sid)
        sched_summary = calibrated_metrics(subset)["metrics"]
        sched_summary["has_invalid_loop_v2"] = ratio(
            sum(row["has_invalid_loop_v2"] for row in subset), len(subset)
        )
        by_schedule[sid] = sched_summary

    by_case = {}
    for cid in case_ids:
        subset = [row for row in results if row["base_case_id"] == cid]
        if not subset:
            raise ValueError("missing results for base_case_id " + cid)
        first = subset[0]
        by_case[cid] = {
            "base_case_id": cid,
            "domain": first["domain"],
            "category": first["category"],
            "template_group": first["template_group"],
            "completable": first["completable"],
            "schedule_count": len(subset),
            "trial_ids": [row["trial_id"] for row in subset],
            "tool_goal_attainment": ratio(sum(row["tool_goal_attainment"] for row in subset), len(subset)),
            "exact_grounded_structured_completion": ratio(
                sum(row["exact_grounded_structured_completion"] for row in subset), len(subset)
            ),
            "has_invalid_loop": ratio(sum(row["has_invalid_loop"] for row in subset), len(subset)),
            "has_invalid_loop_v2": ratio(sum(row["has_invalid_loop_v2"] for row in subset), len(subset)),
            "premature_stop_without_goal_evidence": ratio(
                sum(row["premature_stop_without_goal_evidence"] for row in subset), len(subset)
            ),
            "stopped_after_public_unavailable": ratio(
                sum(row["stopped_after_public_unavailable"] for row in subset), len(subset)
            ),
            "exact_stop_reason_code": ratio(sum(row["exact_stop_reason_code"] for row in subset), len(subset)),
            "turns": sum(row["turns"] for row in subset),
            "proposal_attempts": sum(row["proposal_attempts"] for row in subset),
            "actual_tool_attempts": sum(row["actual_tool_attempts"] for row in subset),
            "guard_blocked_proposals": sum(row["guard_blocked_proposals"] for row in subset),
            "parser_error_turns": sum(row["parser_error_turns"] for row in subset),
            "input_tokens": sum(row["input_tokens"] for row in subset),
            "output_tokens": sum(row["output_tokens"] for row in subset),
            "generation_seconds": sum(row["generation_seconds"] for row in subset),
        }

    metrics.update(
        schedule_ids=schedule_ids,
        schedule_count=len(schedule_ids),
        unique_case_count=len(case_ids),
        by_schedule=by_schedule,
        by_case=by_case,
    )
    return metrics


def _core_curated_prefix_group(results):
    metrics = calibrated_metrics(results)
    group = metrics["metrics"]
    group["has_invalid_loop_v2"] = ratio(
        sum(row["has_invalid_loop_v2"] for row in results), len(results)
    )
    recovery = [row for row in results if row["recovery_eligible"]]
    group["new_goal_evidence_recovery"] = ratio(
        sum(row["new_goal_evidence"] for row in recovery), len(recovery)
    )
    group["exact_structured_completion_recovery"] = ratio(
        sum(row["exact_grounded_structured_completion"] and row["new_goal_evidence"] for row in recovery),
        len(recovery),
    )
    group["prefix_goal_already_attained"] = ratio(
        sum(row["goal_at_prefix"] for row in results), len(results)
    )
    for cat in ("recoverable", "retry_required"):
        cat_rows = [row for row in recovery if row["category"] == cat]
        group[cat + "_new_goal_evidence_recovery"] = ratio(
            sum(row["new_goal_evidence"] for row in cat_rows), len(cat_rows)
        )
        group[cat + "_exact_structured_completion_recovery"] = ratio(
            sum(row["exact_grounded_structured_completion"] and row["new_goal_evidence"] for row in cat_rows),
            len(cat_rows),
        )
    for role in ("normal_completion_control", "unavailable_stop_control"):
        role_rows = [row for row in results if row["prefix_role"] == role]
        group[role] = {
            "case_count": len(role_rows),
            "exact_structured_completion": ratio(
                sum(row["exact_grounded_structured_completion"] for row in role_rows), len(role_rows)
            ),
            "stopped_after_public_unavailable": ratio(
                sum(row["stopped_after_public_unavailable"] for row in role_rows), len(role_rows)
            ),
            "natural_termination_semantics_unknown": ratio(
                sum(row["natural_termination_semantics_unknown"] for row in role_rows), len(role_rows)
            ),
        }
    for field in (
        "prefix_turns",
        "prefix_tool_attempts",
        "continuation_turns",
        "continuation_proposal_attempts",
        "continuation_actual_tool_attempts",
        "historical_input_tokens",
        "historical_output_tokens",
        "historical_generation_seconds",
        "prefix_proposed_repeat_count",
    ):
        group[field] = sum(row[field] for row in results)
    group["parser_error_turn_rate"] = ratio(group["parser_error_turns"], group["continuation_turns"])
    group["proposed_repeat_rate"] = ratio(
        group["proposed_repeat_count"], group["continuation_proposal_attempts"]
    )
    return metrics


def build_curated_prefix_metrics(results, decoding_regime, config, seed_schedules=None, case_ids=None):
    metrics = _core_curated_prefix_group(results)
    metrics.update(
        mode="fixed_failure_prefix",
        prefix_runtime_version=CURATED_PREFIX_RUNTIME_VERSION,
        prefix_schema_version=CURATED_PREFIX_SCHEMA_VERSION,
        wording_profile="explicit",
        task_wording_version=TASK_WORDING_VERSION,
        decoding_regime=decoding_regime,
        runner_subcommand="curated-prefix",
        lora_eval_runner_version=LORA_EVAL_RUNNER_VERSION,
        detector_v2_version=DETECTOR_V2_VERSION,
        **_arm_identity_fields(config),
        by_category={
            category: _slice_prefix_v2_breakdown([row for row in results if row["category"] == category])
            for category in CATEGORIES
        },
        by_domain={
            domain: _slice_prefix_v2_breakdown([row for row in results if row["domain"] == domain])
            for domain in DOMAINS
        },
        warning=(
            "Curated fixed_failure_prefix continuation from identical Environment post-prefix snapshots "
            "and canonical <tool_call> history ({} episodes); recoverable and retry_required reported separately."
        ).format(len(results)),
    )
    if seed_schedules is not None and case_ids is not None:
        schedule_ids = [item["schedule_id"] for item in seed_schedules]
        by_schedule = {}
        for sid in schedule_ids:
            subset = [row for row in results if row["schedule_id"] == sid]
            if not subset:
                raise ValueError("missing curated-prefix results for schedule " + sid)
            by_schedule[sid] = _core_curated_prefix_group(subset)["metrics"]
        by_case = {}
        for cid in case_ids:
            subset = [row for row in results if row["base_case_id"] == cid]
            if not subset:
                raise ValueError("missing curated-prefix results for case " + cid)
            first = subset[0]
            by_case[cid] = {
                "base_case_id": cid,
                "base_prefix_id": cid,
                "domain": first["domain"],
                "category": first["category"],
                "template_group": first["template_group"],
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
                    sum(
                        row["exact_grounded_structured_completion"] and row["new_goal_evidence"]
                        for row in subset
                    ),
                    len(subset),
                ),
                "has_invalid_loop": ratio(sum(row["has_invalid_loop"] for row in subset), len(subset)),
                "has_invalid_loop_v2": ratio(
                    sum(row["has_invalid_loop_v2"] for row in subset), len(subset)
                ),
                "premature_stop_without_goal_evidence": ratio(
                    sum(row["premature_stop_without_goal_evidence"] for row in subset), len(subset)
                ),
                "continuation_turns": sum(row["continuation_turns"] for row in subset),
                "continuation_proposal_attempts": sum(
                    row["continuation_proposal_attempts"] for row in subset
                ),
                "continuation_actual_tool_attempts": sum(
                    row["continuation_actual_tool_attempts"] for row in subset
                ),
                "guard_blocked_proposals": sum(row["guard_blocked_proposals"] for row in subset),
                "parser_error_turns": sum(row["parser_error_turns"] for row in subset),
                "input_tokens": sum(row["input_tokens"] for row in subset),
                "output_tokens": sum(row["output_tokens"] for row in subset),
                "generation_seconds": sum(row["generation_seconds"] for row in subset),
            }
        metrics.update(
            schedule_ids=schedule_ids,
            schedule_count=len(schedule_ids),
            unique_case_count=len(case_ids),
            unique_prefix_count=len(case_ids),
            by_schedule=by_schedule,
            by_case=by_case,
            by_prefix=by_case,
        )
    return metrics


def _tag_e2e_multiseed_record(record, base_case_id, schedule_id):
    trial_id = "{}@{}".format(base_case_id, schedule_id)
    return {
        **record,
        "case_id": trial_id,
        "trial_id": trial_id,
        "base_case_id": base_case_id,
        "schedule_id": schedule_id,
    }


def _tag_curated_prefix_multiseed_record(record, base_case_id, schedule_id):
    trial_id = "{}@{}".format(base_case_id, schedule_id)
    return {
        **record,
        "case_id": trial_id,
        "trial_id": trial_id,
        "base_case_id": base_case_id,
        "base_prefix_id": base_case_id,
        "schedule_id": schedule_id,
    }


def _execute_explicit_e2e(config, root, output, backend_factory):
    validate_lora_explicit_e2e_config(config)
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
        backend, adapter_provenance = _init_backend_and_adapter(
            config, tool_schemas(first_view), backend_factory
        )
        prompt = messages_for_calibrated_view(
            first_view, [], prompt_for(config["comparison_arm"])
        )[0]["content"]
        provenance = backend.provenance()
        provenance.update(
            protocol_version=CALIBRATED_PROTOCOL_VERSION,
            score_version=CALIBRATED_SCORE_VERSION,
            comparison_arm=config["comparison_arm"],
            **{**_arm_identity_fields(config), "train_seed": adapter_provenance.get("train_seed")},
            adapter_provenance=adapter_provenance,
            guard_version=GUARD_VERSION,
            detector_v2_version=DETECTOR_V2_VERSION,
            system_prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            case_seeds=copy.deepcopy(config["case_seeds"]),
            mode="end_to_end",
            wording_profile="explicit",
            task_wording_version=TASK_WORDING_VERSION,
            cases_dataset_sha256=sha256_file(data_path),
            decoding_regime=decoding_regime,
            runner_subcommand="explicit-e2e",
            lora_eval_runner_version=LORA_EVAL_RUNNER_VERSION,
        )
        write_json("model_provenance.json", provenance)
        results = []
        for case in cases:
            stage = "case:" + case["case_id"]
            result, trajectory = run_lora_explicit_e2e_case(
                case,
                backend,
                config,
                case_seed=config["case_seeds"][case["case_id"]],
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
                            "evaluated_arm",
                            "tool_goal_attainment",
                            "exact_grounded_structured_completion",
                            "has_invalid_loop_v2",
                            "terminal_form",
                            "turns",
                        )
                    }
                ),
                flush=True,
            )
        write_json("metrics.json", build_lora_explicit_e2e_metrics(results, decoding_regime, config))
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


def _execute_explicit_e2e_multiseed(config, root, output, backend_factory):
    validate_lora_explicit_e2e_multiseed_config(config)
    decoding_regime = resolve_decoding_regime(config)
    data_path = root / config["cases_path"]
    rows = _jsonl(data_path)
    if len({case["case_id"] for case in rows}) != len(rows):
        raise ValueError("duplicate case identifiers in fixture file")
    by_id = {case["case_id"]: case for case in rows}
    cases = [by_id[case_id] for case_id in config["case_ids"]]
    schedules = config["seed_schedules"]
    handles, write_json, emit = _open_output_writers(output, include_prefix_contexts=False)
    stage = "model_load"
    try:
        first_view = Environment(cases[0]).public_view()
        backend, adapter_provenance = _init_backend_and_adapter(
            config, tool_schemas(first_view), backend_factory
        )
        prompt = messages_for_calibrated_view(
            first_view, [], prompt_for(config["comparison_arm"])
        )[0]["content"]
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
            **{**_arm_identity_fields(config), "train_seed": adapter_provenance.get("train_seed")},
            adapter_provenance=adapter_provenance,
            guard_version=GUARD_VERSION,
            detector_v2_version=DETECTOR_V2_VERSION,
            system_prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            case_seeds=trial_seeds,
            seed_schedules=copy.deepcopy(schedules),
            mode="end_to_end",
            wording_profile="explicit",
            task_wording_version=TASK_WORDING_VERSION,
            cases_dataset_sha256=sha256_file(data_path),
            decoding_regime=decoding_regime,
            runner_subcommand="explicit-e2e-multiseed",
            lora_eval_runner_version=LORA_EVAL_RUNNER_VERSION,
        )
        write_json("model_provenance.json", provenance)
        results = []
        for schedule in schedules:
            sid = schedule["schedule_id"]
            for case in cases:
                cid = case["case_id"]
                trial_id = "{}@{}".format(cid, sid)
                stage = "case:" + trial_id
                raw_res, raw_traj = run_lora_explicit_e2e_case(
                    copy.deepcopy(case),
                    backend,
                    config,
                    case_seed=schedule["case_seeds"][cid],
                    emit_generation=lambda row, b=cid, s=sid: emit(
                        "generations.jsonl", _tag_e2e_multiseed_record(row, b, s)
                    ),
                    emit_event=lambda row, b=cid, s=sid: emit(
                        "execution_events.jsonl", _tag_e2e_multiseed_record(row, b, s)
                    ),
                    emit_partial=lambda row, b=cid, s=sid: write_json(
                        "partial_case.json", _tag_e2e_multiseed_record(row, b, s)
                    ),
                )
                result = _tag_e2e_multiseed_record(raw_res, cid, sid)
                trajectory = _tag_e2e_multiseed_record(raw_traj, cid, sid)
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
                                "base_case_id",
                                "evaluated_arm",
                                "tool_goal_attainment",
                                "exact_grounded_structured_completion",
                                "has_invalid_loop_v2",
                            )
                        }
                    ),
                    flush=True,
                )
        write_json(
            "metrics.json",
            build_lora_explicit_e2e_multiseed_metrics(
                results, schedules, config["case_ids"], decoding_regime, config
            ),
        )
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


def _execute_curated_prefix(config, root, output, backend_factory):
    validate_curated_prefix_config(config)
    decoding_regime = resolve_decoding_regime(config)
    data_path = root / config["cases_path"]
    rows = _jsonl(data_path)
    if len({case["case_id"] for case in rows}) != len(rows):
        raise ValueError("duplicate case identifiers in fixture file")
    by_id = {case["case_id"]: case for case in rows}
    selected = [by_id[case_id] for case_id in config["case_ids"]]
    has_schedules = "seed_schedules" in config and config["seed_schedules"] is not None
    schedules = config["seed_schedules"] if has_schedules else None

    handles, write_json, emit = _open_output_writers(output, include_prefix_contexts=True)
    stage = "prefix_verification"
    try:
        prepared_by_id = {case["case_id"]: prepare_curated_prefix(case, config) for case in selected}
        first_prepared = prepared_by_id[selected[0]["case_id"]]
        stage = "model_load"
        backend, adapter_provenance = _init_backend_and_adapter(
            config, first_prepared["tools"], backend_factory
        )
        if has_schedules:
            recorded_seeds = {
                "{}@{}".format(cid, schedule["schedule_id"]): schedule["case_seeds"][cid]
                for schedule in schedules
                for cid in config["case_ids"]
            }
        else:
            recorded_seeds = copy.deepcopy(config["case_seeds"])

        provenance = backend.provenance()
        provenance.update(
            protocol_version=CALIBRATED_PROTOCOL_VERSION,
            score_version=CALIBRATED_SCORE_VERSION,
            comparison_arm=config["comparison_arm"],
            **{**_arm_identity_fields(config), "train_seed": adapter_provenance.get("train_seed")},
            adapter_provenance=adapter_provenance,
            guard_version=GUARD_VERSION,
            detector_v2_version=DETECTOR_V2_VERSION,
            system_prompt_sha256=hashlib.sha256(
                first_prepared["messages"][0]["content"].encode("utf-8")
            ).hexdigest(),
            case_seeds=recorded_seeds,
            mode="fixed_failure_prefix",
            prefix_runtime_version=CURATED_PREFIX_RUNTIME_VERSION,
            prefix_schema_version=CURATED_PREFIX_SCHEMA_VERSION,
            task_wording_version=TASK_WORDING_VERSION,
            wording_profile="explicit",
            prefix_dataset_sha256=sha256_file(data_path),
            cases_dataset_sha256=sha256_file(data_path),
            prefix_source_rows={case["case_id"]: state_hash(case) for case in selected},
            decoding_regime=decoding_regime,
            runner_subcommand="curated-prefix",
            lora_eval_runner_version=LORA_EVAL_RUNNER_VERSION,
        )
        if has_schedules:
            provenance["seed_schedules"] = copy.deepcopy(schedules)
        write_json("model_provenance.json", provenance)

        results = []
        if has_schedules:
            for schedule in schedules:
                sid = schedule["schedule_id"]
                for case in selected:
                    cid = case["case_id"]
                    trial_id = "{}@{}".format(cid, sid)
                    stage = "case:" + trial_id
                    raw_res, raw_traj = run_curated_prefix_case(
                        copy.deepcopy(case),
                        backend,
                        config,
                        case_seed=schedule["case_seeds"][cid],
                        prepared=prepared_by_id[cid],
                        emit_generation=lambda value, b=cid, s=sid: emit(
                            "generations.jsonl", _tag_curated_prefix_multiseed_record(value, b, s)
                        ),
                        emit_event=lambda value, b=cid, s=sid: emit(
                            "execution_events.jsonl", _tag_curated_prefix_multiseed_record(value, b, s)
                        ),
                        emit_partial=lambda value, b=cid, s=sid: write_json(
                            "partial_case.json", _tag_curated_prefix_multiseed_record(value, b, s)
                        ),
                        emit_prefix_context=lambda value, b=cid, s=sid: emit(
                            "prefix_contexts.jsonl", _tag_curated_prefix_multiseed_record(value, b, s)
                        ),
                    )
                    result = _tag_curated_prefix_multiseed_record(raw_res, cid, sid)
                    trajectory = _tag_curated_prefix_multiseed_record(raw_traj, cid, sid)
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
                                    "base_case_id",
                                    "evaluated_arm",
                                    "new_goal_evidence",
                                    "exact_grounded_structured_completion",
                                    "has_invalid_loop_v2",
                                )
                            }
                        ),
                        flush=True,
                    )
            write_json(
                "metrics.json",
                build_curated_prefix_metrics(
                    results, decoding_regime, config, seed_schedules=schedules, case_ids=config["case_ids"]
                ),
            )
        else:
            for case in selected:
                cid = case["case_id"]
                stage = "case:" + cid
                result, trajectory = run_curated_prefix_case(
                    copy.deepcopy(case),
                    backend,
                    config,
                    case_seed=config["case_seeds"][cid],
                    prepared=prepared_by_id[cid],
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
                                "evaluated_arm",
                                "prefix_role",
                                "new_goal_evidence",
                                "exact_grounded_structured_completion",
                                "has_invalid_loop_v2",
                                "continuation_turns",
                            )
                        }
                    ),
                    flush=True,
                )
            write_json("metrics.json", build_curated_prefix_metrics(results, decoding_regime, config))

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
                "note": "Failure retained; source fixtures untouched; no scripted fallback or fabricated continuation.",
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
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    resolved_root = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    output = Path(args.output)
    if args.subcommand == "explicit-e2e":
        _execute_explicit_e2e(config, resolved_root, output, backend_factory)
    elif args.subcommand == "curated-prefix":
        _execute_curated_prefix(config, resolved_root, output, backend_factory)
    elif args.subcommand == "explicit-e2e-multiseed":
        _execute_explicit_e2e_multiseed(config, resolved_root, output, backend_factory)
    else:
        raise ValueError("unknown subcommand: " + str(args.subcommand))
    return 0


if __name__ == "__main__":
    sys.exit(main())
