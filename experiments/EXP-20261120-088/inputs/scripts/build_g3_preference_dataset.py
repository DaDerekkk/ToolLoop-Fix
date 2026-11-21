#!/usr/bin/env python3
"""Deterministic G3 preference/SFT/FTPO/expert dataset & cloud-safe bundle builder.

Enforces:
  1. Passing G2 gate prerequisite (`g2_frozen_verdict.json` with `g2_passed == True`,
     SHA256-verified if pinned in config) before marking `g3_dataset_ready: True`.
  2. Strict split & cross-domain isolation: sources ONLY from `dev_InDomain_candidate_pool`
     (`orders` + `files`, 14 `template_group`s) plus deterministic within-group entity
     variants inside those exact 14 template schemas, with 0% overlap in `template_group`,
     `identifier`, `target`, and `value` against `dev_InDomain_selection`,
     `dev_CrossDomain_diagnostic`, `dev_CrossDomain_reserved`, `pilot_cases.jsonl`,
     `fixed_prefix_cases_iter4.jsonl`, and `timeout_crossed_iter6.jsonl`.
  3. 100% same-snapshot `Environment` execution verification on every decision point.
  4. Exact Qwen3.5-0.8B `enable_thinking=False` chat-template tokenization and completion
     token divergence index `first_divergence_offset` (with stdlib XML tokenizer fallback
     explicitly gated for unit tests only).
  5. Hard fail-closed zero-leakage verification on `public_colab_training_bundle.json` and
     all 6 exported JSONL datasets (`P`, `B3`, `B4`, `B5`, and 2 Proposal §06 ablations).
"""
import argparse
import copy
import hashlib
import json
import posixpath
from pathlib import Path
import random
import re
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.build_grouped_dev_suite import CATEGORY_COHORTS, DOMAINS, CATEGORIES
from toolloop_fix.calibrated_protocol import (
    CALIBRATED_PROTOCOL_VERSION,
    CALIBRATED_SYSTEM_PROMPT,
    messages_for_calibrated_view,
    parse_calibrated_output,
)
from toolloop_fix.calibrated_run import append_feedback
from toolloop_fix.harness import (
    DOMAIN_TOOLS,
    Environment,
    scripted_policy,
    state_hash,
    tool_call,
    validate_alternative,
)
from toolloop_fix.loop_detector_v2 import DETECTOR_V2_VERSION, NONRETRYABLE_ERROR_CODES
from toolloop_fix.model_protocol import tool_schemas
from toolloop_fix.prefix_run import EXPLICIT_TASK_PREFIX, TASK_WORDING_VERSION

G3_DATASET_SCHEMA_VERSION = "iter8_g3_action_preference_v1"
PINNED_MODEL_ID = "Qwen/Qwen3.5-0.8B"
PINNED_MODEL_REVISION = "2fc06364715b967f1860aea9cf38778875588b17"
DEFAULT_MODEL_RELPATH = f"models/Qwen3.5-0.8B/{PINNED_MODEL_REVISION}"

GROUP_1 = "group1_post_feedback_recovery"
GROUP_2 = "group2_legal_retry_retention"
GROUP_3 = "group3_unrecoverable_truthful_stop"
GROUP_4 = "group4_normal_and_grounded_finish"
GROUP_B5 = "b5_clean_expert_step_preference"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_exclusive_bytes(path, payload):
    with Path(path).open("xb") as handle:
        handle.write(payload)
    return sha256_bytes(payload)


def _write_exclusive_json(path, obj):
    payload = (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return _write_exclusive_bytes(path, payload)


def _write_exclusive_jsonl(path, rows):
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    return _write_exclusive_bytes(path, payload)


def format_qwen_tool_call(name, **arguments):
    """Render canonical Qwen3.5 XML tool call matching the official Jinja chat template."""
    lines = ["<tool_call>", f"<function={name}>"]
    for key in sorted(arguments.keys()):
        lines.append(f"<parameter={key}>")
        lines.append(str(arguments[key]))
        lines.append("</parameter>")
    lines.append("</function>")
    lines.append("</tool_call><|im_end|>")
    return "\n".join(lines)


class StdlibXmlTestTokenizer:
    """Deterministic XML-aware subword tokenizer strictly for stdlib unit-test fallback."""

    is_production_tokenizer = False

    def __init__(self):
        self._vocab = {
            "<|im_start|>": 248045,
            "<|im_end|>": 248046,
            "<tool_call>": 248058,
            "</tool_call>": 248059,
            "<think>": 248068,
            "</think>": 248069,
            "\n": 198,
            "\n\n": 271,
        }
        self._inv = {v: k for k, v in self._vocab.items()}
        self._next_id = 300000
        self._pattern = re.compile(
            r"<\|im_start\|>|<\|im_end\|>|</?tool_call>|</?think>|\n\n|\n|"
            r"<function=[^>]+>|</function>|<parameter=[^>]+>|</parameter>|"
            r"[A-Za-z0-9_]+|."
        )

    def _tok_id(self, piece):
        if piece not in self._vocab:
            tid = self._next_id
            self._next_id += 1
            self._vocab[piece] = tid
            self._inv[tid] = piece
        return self._vocab[piece]

    def apply_chat_template(self, messages, tools=None, tokenize=False, add_generation_prompt=True, enable_thinking=False):
        if enable_thinking is not False:
            raise ValueError("enable_thinking must be False")
        parts = [f"<|im_start|>tools\n{json.dumps(tools or [], sort_keys=True)}<|im_end|>"]
        for msg in messages:
            role = msg["role"]
            if role == "assistant" and msg.get("tool_calls"):
                fn = msg["tool_calls"][0]["function"]
                body = format_qwen_tool_call(fn["name"], **fn["arguments"])
                parts.append(f"<|im_start|>assistant\n<think>\n\n</think>\n\n{body}")
            else:
                parts.append(f"<|im_start|>{role}\n{msg.get('content', '')}<|im_end|>")
        if add_generation_prompt:
            parts.append("<|im_start|>assistant\n<think>\n\n</think>\n\n")
        rendered = "\n".join(parts)
        return self.encode(rendered) if tokenize else rendered

    def encode(self, text, add_special_tokens=False):
        return [self._tok_id(m.group(0)) for m in self._pattern.finditer(text)]

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": self.encode(text, add_special_tokens=add_special_tokens)}

    def decode(self, token_ids, skip_special_tokens=False):
        return "".join(self._inv.get(tid, "?") for tid in token_ids)

    def get_chat_template(self, tools=None):
        return "stdlib_xml_test_template_v1"


def load_qwen_tokenizer(config, root_dir):
    mode = config.get("tokenizer_mode", "transformers")
    if mode == "stdlib_xml_mock_for_tests":
        tok = StdlibXmlTestTokenizer()
        return tok, {
            "tokenizer_mode": mode,
            "is_production_tokenizer": False,
            "model_id": PINNED_MODEL_ID,
            "revision": PINNED_MODEL_REVISION,
            "chat_template_sha256": sha256_bytes(tok.get_chat_template().encode("utf-8")),
        }
    if mode != "transformers":
        raise ValueError(f"unsupported tokenizer_mode: {mode!r}")

    raw_model_path = config.get("model_path", DEFAULT_MODEL_RELPATH)
    model_path = Path(raw_model_path)
    if not model_path.is_absolute():
        model_path = (root_dir / model_path).resolve()
    if not model_path.is_dir():
        raise FileNotFoundError(f"local Qwen3.5-0.8B directory not found: {model_path}")

    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(
        str(model_path), local_files_only=True, trust_remote_code=False
    )
    sample_tools = tool_schemas(
        Environment(
            {
                "domain": "orders",
                "request": {"domain": "orders", "identifier": "X", "target": "Y"},
                "hidden": {
                    "valid_identifier": "X",
                    "value": "Z",
                    "unavailable": False,
                    "transient_remaining": 0,
                    "polls_remaining": 0,
                },
            }
        ).public_view()
    )
    chat_tmpl = tok.get_chat_template(tools=sample_tools)
    return tok, {
        "tokenizer_mode": "transformers",
        "is_production_tokenizer": True,
        "model_id": PINNED_MODEL_ID,
        "revision": PINNED_MODEL_REVISION,
        "chat_template_sha256": sha256_bytes(chat_tmpl.encode("utf-8")),
        "eos_token": tok.eos_token,
        "eos_token_id": tok.eos_token_id,
    }


def compute_token_divergence(tokenizer, messages, tools, chosen_text, rejected_text):
    """Tokenize `(messages, tools, chosen_text, rejected_text)` with `enable_thinking=False`."""
    prompt_text = tokenizer.apply_chat_template(
        messages,
        tools=tools,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    full_chosen_ids = tokenizer(prompt_text + chosen_text, add_special_tokens=False)["input_ids"]
    full_rejected_ids = tokenizer(prompt_text + rejected_text, add_special_tokens=False)["input_ids"]

    if full_chosen_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError("BPE boundary merge altered prompt token prefix on chosen_text")
    if full_rejected_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError("BPE boundary merge altered prompt token prefix on rejected_text")

    chosen_ids = full_chosen_ids[len(prompt_ids) :]
    rejected_ids = full_rejected_ids[len(prompt_ids) :]
    if not chosen_ids or not rejected_ids:
        raise ValueError("empty completion token sequence")

    limit = min(len(chosen_ids), len(rejected_ids))
    offset = None
    for idx in range(limit):
        if chosen_ids[idx] != rejected_ids[idx]:
            offset = idx
            break
    if offset is None or not (0 <= offset < limit):
        raise ValueError(
            "chosen and rejected completions failed strict interior divergence check "
            f"(len_chosen={len(chosen_ids)}, len_rejected={len(rejected_ids)})"
        )
    if chosen_ids[offset] == rejected_ids[offset]:
        raise ValueError("internal divergence invariant violated")

    both_tool_calls = chosen_text.startswith("<tool_call>") and rejected_text.startswith("<tool_call>")
    if both_tool_calls and offset == 0:
        raise ValueError(
            "B4 safety violation: first_divergence_offset == 0 would suppress <tool_call> "
            "when both chosen and rejected are tool calls"
        )

    return {
        "prompt_token_count": len(prompt_ids),
        "chosen_token_count": len(chosen_ids),
        "rejected_token_count": len(rejected_ids),
        "first_divergence_offset": offset,
        "shared_prefix_token_count": offset,
        "full_sequence_divergence_index": len(prompt_ids) + offset,
        "divergence_chosen_token_id": int(chosen_ids[offset]),
        "divergence_rejected_token_id": int(rejected_ids[offset]),
        "divergence_chosen_token_str": tokenizer.decode([chosen_ids[offset]], skip_special_tokens=False),
        "divergence_rejected_token_str": tokenizer.decode([rejected_ids[offset]], skip_special_tokens=False),
        "both_begin_with_tool_call": both_tool_calls,
    }


def _candidate_pool_within_group_variant(base_case, variant_num):
    """Deterministically expand an entity variant strictly inside the same candidate-pool template_group."""
    if variant_num < 5:
        raise ValueError("within-group expansion variant_num must be >= 5")
    domain = base_case["domain"]
    category = base_case["category"]
    tgroup = base_case["template_group"]
    family = int(tgroup.rsplit("-f", 1)[1])
    if domain not in ("orders", "files") or family not in (2, 3):
        raise ValueError(f"illegal template_group outside candidate_pool schema: {tgroup}")

    d_idx = DOMAINS.index(domain)
    c_idx = CATEGORIES.index(category)
    cohort = CATEGORY_COHORTS[category]
    cohort_up = cohort.upper()
    offset = d_idx * 500 + c_idx * 80 + family * 20 + variant_num
    num = 14000 + offset
    snum = 24000 + offset
    vnum = 34000 + offset

    if domain == "orders":
        if family == 2:
            valid_id = f"INV-{cohort_up}2-{num}"
            alt_id = f"INV-{cohort_up}2-{snum}"
            target = f"lookup_key:ord_waybill_{cohort}2_v{variant_num}"
            value = f"ord_status_settled_{cohort}2_{vnum}"
        else:
            valid_id = f"PO-{cohort_up}3-{num}"
            alt_id = f"PO-{cohort_up}3-{snum}"
            target = f"lookup_key:ord_manifest_{cohort}3_v{variant_num}"
            value = f"ord_status_verified_{cohort}3_{vnum}"
    else:
        if family == 2:
            valid_id = f"/ledger_{cohort}2/entry_{num}.json"
            alt_id = f"/ledger_{cohort}2/entry_{snum}.json"
            target = f"lookup_key:fil_catalog_{cohort}2_v{variant_num}"
            value = f"fil_payload_crc_{cohort}2_{vnum}"
        else:
            valid_id = f"/coldstore_{cohort}3/snap_{num}.log"
            alt_id = f"/coldstore_{cohort}3/snap_{snum}.log"
            target = f"lookup_key:fil_archive_{cohort}3_v{variant_num}"
            value = f"fil_extract_tag_{cohort}3_{vnum}"
        for pval in (valid_id, alt_id):
            if posixpath.normpath(pval) != pval or not pval.startswith("/"):
                raise ValueError(f"unnormalized expanded path: {pval}")

    new_case = copy.deepcopy(base_case)
    new_case["case_id"] = f"gdev7-{domain}-{category}-f{family}-{variant_num:02d}"
    new_case["hidden"]["valid_identifier"] = valid_id
    new_case["hidden"]["value"] = value
    new_case["request"] = {"domain": domain, "target": target}
    if "identifier" in base_case["request"]:
        if base_case["request"]["identifier"] == base_case["hidden"]["valid_identifier"]:
            new_case["request"]["identifier"] = valid_id
        else:
            new_case["request"]["identifier"] = alt_id
    if new_case.get("fixed_prefix"):
        primary, _ = DOMAIN_TOOLS[domain]
        new_case["fixed_prefix"] = [tool_call(primary, identifier=new_case["request"]["identifier"])]
    return new_case


def _load_and_verify_isolated_pool(root_dir, manifest_relpath="data/processed/grouped_split_manifest_ITER007.json"):
    manifest_path = (root_dir / manifest_relpath).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    suite_path = (root_dir / manifest["dataset_path"]).resolve()
    if sha256_file(suite_path) != manifest["dataset_sha256"]:
        raise ValueError("grouped_dev_suite_iter7.jsonl SHA256 mismatch against manifest")

    all_suite_cases = _read_jsonl(suite_path)
    by_id = {c["case_id"]: c for c in all_suite_cases}

    cand_part = manifest["partitions"]["dev_InDomain_candidate_pool"]
    allowed_groups = set(cand_part["template_groups"])
    if len(allowed_groups) != 14 or set(cand_part["domains"]) != {"orders", "files"}:
        raise ValueError("unexpected dev_InDomain_candidate_pool structure")

    base_pool_cases = [by_id[cid] for cid in cand_part["case_ids"]]
    if len(base_pool_cases) != 56:
        raise ValueError(f"expected 56 candidate_pool cases, got {len(base_pool_cases)}")

    # Collect all forbidden evaluation/diagnostic/reserved/legacy cases and strings
    forbidden_groups = set()
    forbidden_identifiers = set()
    forbidden_targets = set()
    forbidden_values = set()
    forbidden_case_ids = {c["case_id"] for c in all_suite_cases}

    for part_name in (
        "dev_InDomain_selection",
        "dev_CrossDomain_diagnostic",
        "dev_CrossDomain_reserved",
    ):
        for cid in manifest["partitions"][part_name]["case_ids"]:
            c = by_id[cid]
            forbidden_groups.add(c["template_group"])
            forbidden_identifiers.add(c["hidden"]["valid_identifier"])
            if "identifier" in c["request"]:
                forbidden_identifiers.add(c["request"]["identifier"])
            forbidden_targets.add(c["request"]["target"])
            forbidden_values.add(c["hidden"]["value"])

    pilot_cases = _read_jsonl(root_dir / "data/dev/pilot_cases.jsonl")
    for c in pilot_cases:
        forbidden_case_ids.add(c["case_id"])
        forbidden_groups.add(c["template_group"])
        forbidden_identifiers.add(c["hidden"]["valid_identifier"])
        if "identifier" in c["request"]:
            forbidden_identifiers.add(c["request"]["identifier"])
        forbidden_targets.add(c["request"]["target"])
        forbidden_values.add(c["hidden"]["value"])

    iter4_rows = _read_jsonl(root_dir / "data/dev/fixed_prefix_cases_iter4.jsonl")
    for row in iter4_rows:
        forbidden_case_ids.add(row["case_id"])
        oc = row["original_case"]
        forbidden_case_ids.add(oc["case_id"])
        forbidden_groups.add(oc["template_group"])
        forbidden_identifiers.add(oc["hidden"]["valid_identifier"])
        if "identifier" in oc["request"]:
            forbidden_identifiers.add(oc["request"]["identifier"])
        forbidden_targets.add(oc["request"]["target"])
        forbidden_values.add(oc["hidden"]["value"])

    iter6_rows = _read_jsonl(root_dir / "data/dev/timeout_crossed_iter6.jsonl")
    for row in iter6_rows:
        forbidden_case_ids.add(row["case_id"])
        forbidden_groups.add(row["template_group"])
        forbidden_identifiers.add(row["identifier"])

    # Group base cases by template_group and create deterministic within-group variants 5..16
    by_group = {tg: [] for tg in cand_part["template_groups"]}
    for c in base_pool_cases:
        by_group[c["template_group"]].append(copy.deepcopy(c))
    for tg in cand_part["template_groups"]:
        proto = by_group[tg][0]
        for v_num in range(5, 17):
            expanded = _candidate_pool_within_group_variant(proto, v_num)
            by_group[tg].append(expanded)
            forbidden_case_ids.add(expanded["case_id"])

    # Verify 0% overlap between candidate pool (base + expanded) and ALL forbidden splits
    pool_groups = set()
    pool_identifiers = set()
    pool_targets = set()
    pool_values = set()
    for tg, group_cases in by_group.items():
        for c in group_cases:
            if c["domain"] not in {"orders", "files"}:
                raise ValueError(f"forbidden domain in candidate pool: {c['domain']}")
            pool_groups.add(c["template_group"])
            pool_identifiers.add(c["hidden"]["valid_identifier"])
            if "identifier" in c["request"]:
                pool_identifiers.add(c["request"]["identifier"])
            pool_targets.add(c["request"]["target"])
            pool_values.add(c["hidden"]["value"])

    if pool_groups != allowed_groups or (pool_groups & forbidden_groups):
        raise ValueError("template_group overlap with held-out/selection/legacy splits")
    if pool_identifiers & forbidden_identifiers:
        raise ValueError(f"identifier overlap: {sorted(pool_identifiers & forbidden_identifiers)[:3]}")
    if pool_targets & forbidden_targets:
        raise ValueError(f"target overlap: {sorted(pool_targets & forbidden_targets)[:3]}")
    if pool_values & forbidden_values:
        raise ValueError(f"value overlap: {sorted(pool_values & forbidden_values)[:3]}")

    return {
        "manifest_sha256": sha256_file(manifest_path),
        "suite_sha256": manifest["dataset_sha256"],
        "by_group": by_group,
        "all_template_groups": set(by_group.keys()) | forbidden_groups,
        "all_case_ids": forbidden_case_ids,
        "forbidden_groups": forbidden_groups,
        "forbidden_identifiers": forbidden_identifiers,
        "forbidden_targets": forbidden_targets,
        "forbidden_values": forbidden_values,
    }


def _prepare_snapshot_and_dialogue(case, prefix_tool_actions):
    """Execute `prefix_tool_actions` in `Environment(case)` and build B0 explicit dialogue."""
    env = Environment(case)
    view0 = env.public_view()
    messages = messages_for_calibrated_view(view0, [], "")
    if messages[0]["content"] != CALIBRATED_SYSTEM_PROMPT:
        raise ValueError("must use exact B0 CALIBRATED_SYSTEM_PROMPT without B1/B2 additions")
    messages[1]["content"] = EXPLICIT_TASK_PREFIX + json.dumps(view0["request"], ensure_ascii=False)
    tools = tool_schemas(view0)

    for turn_idx, act in enumerate(prefix_tool_actions, 1):
        if act.get("type") != "tool":
            raise ValueError("prefix actions must be non-terminal tool calls")
        raw_step = format_qwen_tool_call(act["tool"], **act["arguments"])
        dec = parse_calibrated_output(raw_step, False)
        if not dec["valid"] or dec["action"] != act:
            raise ValueError("prefix tool formatting round-trip mismatch")
        ev = env.execute(act)
        ev["guard_blocked"] = False
        append_feedback(messages, dec, ev, turn_idx)

    return env, env.snapshot(), messages, tools


def verify_decision_point_in_snapshot(
    case,
    snapshot,
    chosen_text,
    rejected_text,
    allow_retry_limit_stop=False,
):
    """Verify both `chosen_text` and `rejected_text` in isolated copies of the SAME snapshot."""
    snap_sha = state_hash(snapshot)
    env_probe = Environment(case)
    env_probe.restore(snapshot)
    env_chosen = Environment(case)
    env_chosen.restore(snapshot)
    env_rejected = Environment(case)
    env_rejected.restore(snapshot)

    if state_hash(env_chosen.snapshot()) != snap_sha or state_hash(env_rejected.snapshot()) != snap_sha:
        raise ValueError("snapshot restoration hash mismatch")

    chosen_dec = parse_calibrated_output(chosen_text, False)
    if not chosen_dec["valid"] or chosen_dec["terminal_form"] == "natural_text":
        raise ValueError(f"chosen_text must be a valid structured tool/terminal call: {chosen_text!r}")
    chosen_act = chosen_dec["action"]

    # Public-only argument check on chosen
    view = env_probe.public_view()
    chosen_val_alt = None
    if chosen_act["type"] == "tool":
        chosen_val_alt = validate_alternative(env_probe, chosen_act)
        if state_hash(env_probe.snapshot()) != snap_sha:
            raise ValueError("validate_alternative mutated probe snapshot")
        if not chosen_val_alt["public_arguments_only"]:
            raise ValueError(f"chosen tool leaked non-public argument: {chosen_act}")
        trace_c = env_chosen.execute(chosen_act)
        advanced_timeout = (
            trace_c["response"].get("code") == "TIMEOUT"
            and trace_c["state_before"] != trace_c["state_after"]
        )
        if not (chosen_val_alt["locally_validated"] or advanced_timeout):
            raise ValueError(
                f"chosen tool neither locally_validated nor advanced transient timeout: {trace_c['response']}"
            )
        # Also prove full-trajectory completability from post-chosen state via scripted_reference
        steps = 0
        while env_chosen.snapshot()["terminal"] is None and steps < 10:
            nxt = scripted_policy(env_chosen.public_view(), "scripted_reference")
            env_chosen.execute(nxt)
            steps += 1
        eval_c = env_chosen.evaluate(False)
        if not eval_c["success"] or eval_c["false_success"]:
            raise ValueError(f"chosen tool did not lead to full-trajectory success: {eval_c}")
    elif chosen_act["type"] == "final":
        ok_pairs = {
            (ev["response"].get("identifier"), ev["response"].get("value"))
            for ev in view["history"]
            if ev["response"].get("code") == "OK"
        }
        if (chosen_act["identifier"], chosen_act["answer"]) not in ok_pairs:
            raise ValueError("chosen finish_task uses ungrounded answer/identifier")
        env_chosen.execute(chosen_act)
        eval_c = env_chosen.evaluate(False)
        if not eval_c["success"] or eval_c["false_success"]:
            raise ValueError(f"chosen finish_task failed Environment.evaluate: {eval_c}")
    elif chosen_act["type"] == "stop":
        seen_codes = {ev["response"].get("code") for ev in view["history"]}
        if chosen_act["reason"] not in seen_codes:
            raise ValueError(f"chosen stop_task reason {chosen_act['reason']} not observed in public history")
        env_chosen.execute(chosen_act)
        eval_c = env_chosen.evaluate(False)
        if allow_retry_limit_stop and chosen_act["reason"] == "RETRY_LIMIT_EXCEEDED":
            if eval_c["premature_abandonment"] or eval_c["false_success"]:
                raise ValueError(f"chosen RETRY_LIMIT_EXCEEDED stop invalid: {eval_c}")
        elif not eval_c["correct_termination"] or eval_c["false_success"]:
            raise ValueError(f"chosen stop_task failed correct_termination: {eval_c}")
    else:
        raise ValueError(f"unexpected chosen action type: {chosen_act}")

    # Verify rejected fails / makes zero valid progress in the exact same snapshot
    rejected_dec = parse_calibrated_output(rejected_text, False)
    if rejected_dec["valid"]:
        rej_act = rejected_dec["action"]
        if rej_act["type"] == "tool":
            rej_val_alt = validate_alternative(env_probe, rej_act)
            if rej_val_alt["locally_validated"]:
                raise ValueError(f"rejected tool unexpectedly passed locally_validated: {rej_act}")
            trace_r = env_rejected.execute(rej_act)
            if trace_r["state_before"] != trace_r["state_after"]:
                raise ValueError(f"rejected tool unexpectedly mutated state: {rej_act}")
            if trace_r["response"].get("code") in {"OK", "LOCATED", "USER_INFORMATION", "READY"}:
                if rej_val_alt["new_information"]:
                    raise ValueError(f"rejected tool produced new valid progress: {trace_r['response']}")
        else:
            env_rejected.execute(rej_act)
            eval_r = env_rejected.evaluate(False)
            if eval_r["success"] or eval_r["correct_termination"]:
                raise ValueError(f"rejected terminal action unexpectedly succeeded: {eval_r}")
        rej_summary = {
            "valid_parse": True,
            "action_type": rej_act["type"],
            "terminal_form": rejected_dec["terminal_form"],
        }
    else:
        trace_r = env_rejected.execute({"type": "invalid_model_output", "error": rejected_dec["error"]})
        if trace_r["response"].get("code") != "FORMAT_ERROR" or trace_r["state_before"] != trace_r["state_after"]:
            raise ValueError("malformed rejected action did not fail cleanly with FORMAT_ERROR")
        rej_summary = {
            "valid_parse": False,
            "parser_error": rejected_dec["error"],
            "terminal_form": None,
        }

    return {
        "snapshot_sha256": snap_sha,
        "chosen_locally_validated": bool(chosen_val_alt["locally_validated"]) if chosen_val_alt else True,
        "chosen_action_type": chosen_act["type"],
        "rejected_summary": rej_summary,
    }


def _make_record(
    pool_case,
    prefix_actions,
    group,
    subtype,
    chosen_text,
    rejected_text,
    tokenizer,
    allow_retry_limit_stop=False,
):
    _, snapshot, messages, tools = _prepare_snapshot_and_dialogue(pool_case, prefix_actions)
    snap_ver = verify_decision_point_in_snapshot(
        pool_case,
        snapshot,
        chosen_text,
        rejected_text,
        allow_retry_limit_stop=allow_retry_limit_stop,
    )
    tok_div = compute_token_divergence(tokenizer, messages, tools, chosen_text, rejected_text)
    return {
        "group": group,
        "subtype": subtype,
        "domain": pool_case["domain"],
        "messages": messages,
        "tools": tools,
        "chosen_text": chosen_text,
        "rejected_text": rejected_text,
        "token_divergence": tok_div,
        "provenance": {
            "source_case_id": pool_case["case_id"],
            "template_group": pool_case["template_group"],
            "partition": "dev_InDomain_candidate_pool",
            "prefix_length": len(prefix_actions),
            **snap_ver,
        },
    }


def _generate_domain_p_records(domain, by_group, tokenizer):
    """Construct 200 balanced, snapshot-verified decision points for one in-domain (`orders` or `files`)."""
    primary, locate = DOMAIN_TOOLS[domain]
    records = []

    rec_cases = by_group[f"iter7-{domain}-recoverable-f3"]
    retry_cases = by_group[f"iter7-{domain}-retry_required-f3"]
    clar_cases = by_group[f"iter7-{domain}-clarification-f2"]
    unrec_f2 = by_group[f"iter7-{domain}-unrecoverable-f2"]
    unrec_f3 = by_group[f"iter7-{domain}-unrecoverable-f3"]
    norm_f2 = by_group[f"iter7-{domain}-normal-f2"]
    norm_f3 = by_group[f"iter7-{domain}-normal-f3"]

    # =========================================================================
    # GROUP 1: Avoidable Post-Feedback Recovery Pairs (90 per domain -> 180 total = 45%)
    # =========================================================================
    # 1a. After INVALID_IDENTIFIER (24 per domain: 6 variants x 4 rejected failure modes)
    for idx in range(6):
        c = rec_cases[idx]
        stale_id = c["request"]["identifier"]
        target = c["request"]["target"]
        pfx = [tool_call(primary, identifier=stale_id)]
        chosen = format_qwen_tool_call(locate, target=target)

        rej_repeat = format_qwen_tool_call(primary, identifier=stale_id)
        rej_wrong_param = format_qwen_tool_call(locate, target=stale_id)
        rej_ask = format_qwen_tool_call("ask_user", field="identifier")
        rej_premature = (
            f"The requested resource '{stale_id}' does not exist in the system.<|im_end|>"
            if idx % 2 == 0
            else format_qwen_tool_call("stop_task", reason="INVALID_IDENTIFIER")
        )
        for mode_tag, rej in (
            ("repeat_stale_primary", rej_repeat),
            ("locate_with_stale_id_as_target", rej_wrong_param),
            ("unnecessary_ask_user", rej_ask),
            ("premature_absence_or_stop", rej_premature),
        ):
            rec = _make_record(
                c, pfx, GROUP_1, f"after_invalid_identifier_{mode_tag}", chosen, rej, tokenizer
            )
            if not rec["provenance"]["chosen_locally_validated"]:
                raise ValueError("1a chosen must satisfy validate_alternative locally_validated=True")
            records.append(rec)

    # 1b. After LOCATED(valid_id) (18 per domain: 6 variants x 3 rejected failure modes)
    for idx in range(6):
        c = rec_cases[idx]
        stale_id = c["request"]["identifier"]
        valid_id = c["hidden"]["valid_identifier"]
        target = c["request"]["target"]
        pfx = [tool_call(primary, identifier=stale_id), tool_call(locate, target=target)]
        chosen = format_qwen_tool_call(primary, identifier=valid_id)

        for mode_tag, rej in (
            ("repeat_locate", format_qwen_tool_call(locate, target=target)),
            ("repeat_stale_primary", format_qwen_tool_call(primary, identifier=stale_id)),
            ("finish_with_id_as_answer", format_qwen_tool_call("finish_task", answer=valid_id, identifier=valid_id)),
        ):
            records.append(
                _make_record(c, pfx, GROUP_1, f"after_located_{mode_tag}", chosen, rej, tokenizer)
            )

    # 1c. Missing-ID initial turn & after MISSING_IDENTIFIER (18 per domain: 6 x 3 contexts)
    for idx in range(6):
        c = clar_cases[idx]
        target = c["request"]["target"]
        chosen = format_qwen_tool_call("ask_user", field="identifier")

        # Turn 0 missing ID
        rej_t0 = (
            format_qwen_tool_call(primary, identifier="")
            if idx % 3 == 0
            else format_qwen_tool_call(locate, target=target)
            if idx % 3 == 1
            else f"Cannot retrieve '{target}' because no identifier was supplied in the request.<|im_end|>"
        )
        records.append(
            _make_record(c, [], GROUP_1, "turn0_missing_identifier_clarify", chosen, rej_t0, tokenizer)
        )

        # Turn 1 after primary("") -> MISSING_IDENTIFIER
        pfx_empty = [tool_call(primary, identifier="")]
        rej_after_empty = (
            format_qwen_tool_call(locate, target=target)
            if idx % 2 == 0
            else format_qwen_tool_call(primary, identifier="")
        )
        records.append(
            _make_record(
                c, pfx_empty, GROUP_1, "after_missing_identifier_from_primary", chosen, rej_after_empty, tokenizer
            )
        )

        # Turn 1 after locate(target) -> MISSING_IDENTIFIER
        pfx_loc = [tool_call(locate, target=target)]
        rej_after_loc = (
            f"Unable to locate '{target}' without an explicit identifier.<|im_end|>"
            if idx % 2 == 0
            else format_qwen_tool_call(locate, target=target)
        )
        records.append(
            _make_record(
                c, pfx_loc, GROUP_1, "after_missing_identifier_from_locate", chosen, rej_after_loc, tokenizer
            )
        )

    # 1d. After USER_INFORMATION(valid_id) (12 per domain: 6 variants x 2 rejected modes)
    for idx in range(6):
        c = clar_cases[idx]
        valid_id = c["hidden"]["valid_identifier"]
        pfx = [tool_call("ask_user", field="identifier")]
        chosen = format_qwen_tool_call(primary, identifier=valid_id)

        records.append(
            _make_record(
                c,
                pfx,
                GROUP_1,
                "after_user_information_repeat_ask",
                chosen,
                format_qwen_tool_call("ask_user", field="identifier"),
                tokenizer,
            )
        )
        records.append(
            _make_record(
                c,
                pfx,
                GROUP_1,
                "after_user_information_premature_finish",
                chosen,
                format_qwen_tool_call("finish_task", answer=valid_id, identifier=valid_id),
                tokenizer,
            )
        )

    # 1e. After NOT_FOUND (from wrong locate(stale_id)) or UNNECESSARY_CLARIFICATION (18 per domain)
    for idx in range(6):
        c = rec_cases[6 + idx]
        stale_id = c["request"]["identifier"]
        target = c["request"]["target"]
        chosen_loc = format_qwen_tool_call(locate, target=target)

        # After [primary(stale_id) -> INVALID_IDENTIFIER, locate(stale_id) -> NOT_FOUND]
        pfx_nf = [tool_call(primary, identifier=stale_id), tool_call(locate, target=stale_id)]
        records.append(
            _make_record(
                c,
                pfx_nf,
                GROUP_1,
                "after_wrong_locate_not_found",
                chosen_loc,
                format_qwen_tool_call("ask_user", field="identifier"),
                tokenizer,
            )
        )

        # After 3-step failure [primary(stale_id), locate(stale_id), ask_user("identifier")] -> INV-CB2-6071 cycle!
        pfx_3step = pfx_nf + [tool_call("ask_user", field="identifier")]
        bad_field = "domain" if idx % 2 == 0 else "target"
        records.append(
            _make_record(
                c,
                pfx_3step,
                GROUP_1,
                "after_unnecessary_clarification_3cycle_recovery",
                chosen_loc,
                format_qwen_tool_call("ask_user", field=bad_field),
                tokenizer,
            )
        )

    norm_mix = norm_f2[:3] + norm_f3[:3]
    for idx, c in enumerate(norm_mix):
        valid_id = c["request"]["identifier"]
        pfx_unnec = [tool_call("ask_user", field="identifier")]
        chosen_prim = format_qwen_tool_call(primary, identifier=valid_id)
        rej_cycle = format_qwen_tool_call("ask_user", field="domain" if idx % 2 == 0 else "identifier")
        records.append(
            _make_record(
                c,
                pfx_unnec,
                GROUP_1,
                "after_unnecessary_clarification_direct_primary",
                chosen_prim,
                rej_cycle,
                tokenizer,
            )
        )

    # =========================================================================
    # GROUP 2: Opposite-Context Legal Retry Retention (40 per domain -> 80 total = 20%)
    # =========================================================================
    # 2a. Direct 1st TIMEOUT (retryable=true) (16 per domain: 8 variants x 2 rejected modes covering all 4 types)
    for idx in range(8):
        c = retry_cases[idx]
        valid_id = c["request"]["identifier"]
        pfx = [tool_call(primary, identifier=valid_id)]
        chosen = format_qwen_tool_call(primary, identifier=valid_id)

        rej_pool = [
            format_qwen_tool_call("stop_task", reason="TIMEOUT"),
            format_qwen_tool_call("ask_user", field="identifier"),
            f"The request for '{valid_id}' timed out; stopping without retrying.<|im_end|>",
            (
                f"Identifier: {valid_id}\nResult Value: 0<|im_end|>"
                if idx % 2 == 0
                else format_qwen_tool_call("finish_task", answer="0", identifier=valid_id)
            ),
        ]
        for slot in (idx % 4, (idx + 2) % 4):
            records.append(
                _make_record(
                    c, pfx, GROUP_2, f"legal_1st_timeout_retry_vs_slot{slot}", chosen, rej_pool[slot], tokenizer
                )
            )

    # 2b. Compound post-LOCATED 1st & 2nd TIMEOUT + post-USER_INFORMATION TIMEOUT (16 per domain)
    for idx in range(8):
        c = rec_cases[idx]
        stale_id = c["request"]["identifier"]
        valid_id = c["hidden"]["valid_identifier"]
        target = c["request"]["target"]
        pfx_t1 = [
            tool_call(primary, identifier=stale_id),
            tool_call(locate, target=target),
            tool_call(primary, identifier=valid_id),
        ]
        chosen = format_qwen_tool_call(primary, identifier=valid_id)
        rej = (
            format_qwen_tool_call("stop_task", reason="TIMEOUT")
            if idx % 4 == 0
            else format_qwen_tool_call("ask_user", field="identifier")
            if idx % 4 == 1
            else f"Lookup succeeded with '{valid_id}', but reading timed out; unable to continue.<|im_end|>"
            if idx % 4 == 2
            else f"Identifier: {valid_id}\nResult Value: 0<|im_end|>"
        )
        records.append(
            _make_record(c, pfx_t1, GROUP_2, "compound_post_located_1st_timeout_retry", chosen, rej, tokenizer)
        )

    for idx in range(4):
        c = rec_cases[idx]
        stale_id = c["request"]["identifier"]
        valid_id = c["hidden"]["valid_identifier"]
        target = c["request"]["target"]
        pfx_t2 = [
            tool_call(primary, identifier=stale_id),
            tool_call(locate, target=target),
            tool_call(primary, identifier=valid_id),
            tool_call(primary, identifier=valid_id),
        ]
        chosen = format_qwen_tool_call(primary, identifier=valid_id)
        rej = (
            format_qwen_tool_call("stop_task", reason="TIMEOUT")
            if idx % 2 == 0
            else format_qwen_tool_call("finish_task", answer="0", identifier=valid_id)
        )
        records.append(
            _make_record(c, pfx_t2, GROUP_2, "compound_post_located_2nd_timeout_retry", chosen, rej, tokenizer)
        )

    for idx in range(4):
        c = clar_cases[idx]
        valid_id = c["hidden"]["valid_identifier"]
        pfx_ct = [tool_call("ask_user", field="identifier"), tool_call(primary, identifier=valid_id)]
        chosen = format_qwen_tool_call(primary, identifier=valid_id)
        rej = (
            format_qwen_tool_call("stop_task", reason="TIMEOUT")
            if idx % 2 == 0
            else format_qwen_tool_call("ask_user", field="identifier")
        )
        records.append(
            _make_record(
                c, pfx_ct, GROUP_2, "compound_post_clarification_timeout_retry", chosen, rej, tokenizer
            )
        )

    # 2c. Contrast: after RETRY_LIMIT_EXCEEDED (retryable=false) -> stop_task vs 4th retry (8 per domain)
    for idx in range(8):
        base_u = copy.deepcopy(unrec_f3[8 + idx])
        base_u["hidden"]["unavailable"] = False
        base_u["hidden"]["transient_remaining"] = 4
        valid_id = base_u["request"]["identifier"]
        pfx_cap = [tool_call(primary, identifier=valid_id) for _ in range(4)]
        chosen_stop = format_qwen_tool_call("stop_task", reason="RETRY_LIMIT_EXCEEDED")
        rej_fifth = format_qwen_tool_call(primary, identifier=valid_id)
        records.append(
            _make_record(
                base_u,
                pfx_cap,
                GROUP_2,
                "after_retry_limit_exceeded_stop_contrast",
                chosen_stop,
                rej_fifth,
                tokenizer,
                allow_retry_limit_stop=True,
            )
        )

    # =========================================================================
    # GROUP 3: Truthful Unrecoverable Stop vs Error-as-Value Hallucination (30 per domain -> 60 total = 15%)
    # =========================================================================
    unrec_direct = unrec_f2[:12] + unrec_f3[:8] + unrec_f2[12:16]  # 24 cases
    for idx, c in enumerate(unrec_direct):
        req_id = c["request"]["identifier"]
        pfx = [tool_call(primary, identifier=req_id)]
        chosen = format_qwen_tool_call("stop_task", reason="RESOURCE_UNAVAILABLE")
        mode = idx % 4
        if mode == 0:
            rej = format_qwen_tool_call(primary, identifier=req_id)
            tag = "repeat_unavailable_primary"
        elif mode == 1:
            rej = f"Identifier: {req_id}\nValue: RESOURCE_UNAVAILABLE<|im_end|>"
            tag = "prose_error_code_as_value"
        elif mode == 2:
            rej = format_qwen_tool_call("finish_task", answer="RESOURCE_UNAVAILABLE", identifier=req_id)
            tag = "finish_wrapper_error_code_as_value"
        else:
            rej = f"The requested record '{req_id}' was not found in the archive.<|im_end|>"
            tag = "unsupported_was_not_found_claim"
        records.append(_make_record(c, pfx, GROUP_3, f"after_unavailable_{tag}", chosen, rej, tokenizer))

    for idx in range(6):
        c = unrec_f3[idx]
        req_id = c["request"]["identifier"]
        target = c["request"]["target"]
        pfx = [tool_call(primary, identifier=req_id), tool_call(locate, target=target)]
        chosen = format_qwen_tool_call("stop_task", reason="RESOURCE_UNAVAILABLE")
        rej = (
            f"Identifier: {req_id}\nValue: RESOURCE_UNAVAILABLE<|im_end|>"
            if idx % 3 == 0
            else format_qwen_tool_call("finish_task", answer="RESOURCE_UNAVAILABLE", identifier=req_id)
            if idx % 3 == 1
            else format_qwen_tool_call(locate, target=target)
        )
        records.append(
            _make_record(c, pfx, GROUP_3, "after_double_unavailable_stop", chosen, rej, tokenizer)
        )

    # =========================================================================
    # GROUP 4: Normal Initial Call & Exact Grounded Finish Retention (40 per domain -> 80 total = 20%)
    # =========================================================================
    # 4a. Turn 0 with identifier present (16 per domain)
    norm_16 = norm_f2[:8] + norm_f3[:8]
    for idx, c in enumerate(norm_16):
        req_id = c["request"]["identifier"]
        target = c["request"]["target"]
        chosen = format_qwen_tool_call(primary, identifier=req_id)
        rej = (
            format_qwen_tool_call("ask_user", field="identifier")
            if idx % 2 == 0
            else format_qwen_tool_call("finish_task", answer=target, identifier=req_id)
        )
        records.append(_make_record(c, [], GROUP_4, "turn0_normal_initial_call", chosen, rej, tokenizer))

    # 4b. After OK(identifier, value) -> finish_task(answer=value, identifier=identifier) (24 per domain)
    ok_trajectories = []
    for c in norm_f2[8:14] + norm_f3[8:14]:  # 12 normal post-OK
        ok_trajectories.append((c, [tool_call(primary, identifier=c["request"]["identifier"])]))
    for c in retry_cases[8:12]:  # 4 post-retry OK
        vid = c["request"]["identifier"]
        ok_trajectories.append((c, [tool_call(primary, identifier=vid), tool_call(primary, identifier=vid)]))
    for c in rec_cases[8:12]:  # 4 post-recovery OK
        sid = c["request"]["identifier"]
        vid = c["hidden"]["valid_identifier"]
        ok_trajectories.append(
            (
                c,
                [
                    tool_call(primary, identifier=sid),
                    tool_call(locate, target=c["request"]["target"]),
                    tool_call(primary, identifier=vid),
                    tool_call(primary, identifier=vid),
                    tool_call(primary, identifier=vid),
                ],
            )
        )
    for c in clar_cases[8:12]:  # 4 post-clarification OK
        vid = c["hidden"]["valid_identifier"]
        ok_trajectories.append(
            (
                c,
                [
                    tool_call("ask_user", field="identifier"),
                    tool_call(primary, identifier=vid),
                    tool_call(primary, identifier=vid),
                ],
            )
        )

    corrupted_id = (
        "file:///archive/document.dat" if domain == "files" else "order://ledger/record_ref"
    )
    for idx, (c, pfx) in enumerate(ok_trajectories):
        valid_id = c["hidden"]["valid_identifier"]
        val = c["hidden"]["value"]
        chosen = format_qwen_tool_call("finish_task", answer=val, identifier=valid_id)
        mode = idx % 3
        if mode == 0:
            rej = format_qwen_tool_call("finish_task", answer=val, identifier=corrupted_id)
            tag = "corrupted_identifier_finish"
        elif mode == 1:
            rej = format_qwen_tool_call("ask_user", field="identifier")
            tag = "unnecessary_ask_after_ok"
        else:
            rej = format_qwen_tool_call(primary, identifier=valid_id)
            tag = "redundant_primary_after_ok"
        records.append(_make_record(c, pfx, GROUP_4, f"after_ok_{tag}", chosen, rej, tokenizer))

    if len(records) != 200:
        raise AssertionError(f"expected 200 records for {domain}, got {len(records)}")
    return records


def _generate_domain_b5_records(domain, by_group, tokenizer):
    """Construct 200 clean expert-trajectory action DPO pairs WITHOUT post-error failure-site mining."""
    primary, locate = DOMAIN_TOOLS[domain]
    norm_all = by_group[f"iter7-{domain}-normal-f2"] + by_group[f"iter7-{domain}-normal-f3"]  # 32 cases
    clar_clean = []
    for c in by_group[f"iter7-{domain}-clarification-f2"]:
        cc = copy.deepcopy(c)
        cc["hidden"]["transient_remaining"] = 0
        clar_clean.append(cc)

    records = []

    # 1. Clean Turn-0 expert calls on normal cases (64 pairs: 32 cases x 2 generic distractors)
    for idx, c in enumerate(norm_all):
        req_id = c["request"]["identifier"]
        target = c["request"]["target"]
        chosen = format_qwen_tool_call(primary, identifier=req_id)
        generic_distractors = (
            format_qwen_tool_call(locate, target=req_id),
            f"<tool_call>\n<function={primary}>\n<parameter=wrong_param>\n{req_id}\n</parameter>\n</function>\n</tool_call><|im_end|>",
        )
        for d_idx, rej in enumerate(generic_distractors):
            records.append(
                _make_record(c, [], GROUP_B5, f"clean_turn0_primary_{d_idx}", chosen, rej, tokenizer)
            )

    # 2. Clean post-OK expert finish on normal cases (64 pairs: 32 cases x 2 generic distractors)
    for idx, c in enumerate(norm_all):
        req_id = c["request"]["identifier"]
        target = c["request"]["target"]
        val = c["hidden"]["value"]
        pfx = [tool_call(primary, identifier=req_id)]
        chosen = format_qwen_tool_call("finish_task", answer=val, identifier=req_id)
        generic_distractors = (
            format_qwen_tool_call("finish_task", answer=target, identifier=req_id),
            f"<tool_call>\n<function=unknown_finish>\n<parameter=answer>\n{val}\n</parameter>\n</function>\n</tool_call><|im_end|>",
        )
        for d_idx, rej in enumerate(generic_distractors):
            records.append(
                _make_record(c, pfx, GROUP_B5, f"clean_post_ok_finish_{d_idx}", chosen, rej, tokenizer)
            )

    # 3. Clean clarification expert trajectory (Turn 0 ask_user, Turn 1 primary, Turn 2 finish_task: 48 pairs)
    for idx, c in enumerate(clar_clean[:16]):
        valid_id = c["hidden"]["valid_identifier"]
        target = c["request"]["target"]
        val = c["hidden"]["value"]

        # Turn 0 clean ask_user (16 pairs)
        records.append(
            _make_record(
                c,
                [],
                GROUP_B5,
                "clean_clar_turn0",
                format_qwen_tool_call("ask_user", field="identifier"),
                format_qwen_tool_call("ask_user", field="target"),
                tokenizer,
            )
        )
        # Turn 1 clean post-USER_INFORMATION primary (16 pairs)
        records.append(
            _make_record(
                c,
                [tool_call("ask_user", field="identifier")],
                GROUP_B5,
                "clean_clar_turn1",
                format_qwen_tool_call(primary, identifier=valid_id),
                format_qwen_tool_call(locate, target=valid_id),
                tokenizer,
            )
        )
        # Turn 2 clean post-OK finish (16 pairs)
        records.append(
            _make_record(
                c,
                [tool_call("ask_user", field="identifier"), tool_call(primary, identifier=valid_id)],
                GROUP_B5,
                "clean_clar_turn2",
                format_qwen_tool_call("finish_task", answer=val, identifier=valid_id),
                format_qwen_tool_call("stop_task", reason="UNKNOWN"),
                tokenizer,
            )
        )

    # 4. Clean pre-locate expert transitions on normal cases (24 pairs: locate(target)->LOCATED->primary)
    for idx, c in enumerate(norm_all[:24]):
        req_id = c["request"]["identifier"]
        target = c["request"]["target"]
        pfx = [tool_call(locate, target=target)]
        chosen = format_qwen_tool_call(primary, identifier=req_id)
        rej = f"<tool_call>\n<function={primary}>\n</function>\n</tool_call><|im_end|>"
        records.append(
            _make_record(c, pfx, GROUP_B5, "clean_post_locate_primary", chosen, rej, tokenizer)
        )

    if len(records) != 200:
        raise AssertionError(f"expected 200 B5 records for {domain}, got {len(records)}")

    # Verify ZERO post-error states appear in any B5 message history
    forbidden_b5_codes = NONRETRYABLE_ERROR_CODES | {"TIMEOUT"}
    for rec in records:
        for msg in rec["messages"]:
            if msg.get("role") == "tool":
                resp = json.loads(msg["content"])
                if resp.get("code") in forbidden_b5_codes:
                    raise ValueError(f"B5 record unexpectedly contains error code {resp.get('code')}")
    return records


def verify_cloud_safe_artifacts(serialized_blobs, pool_info, root_dir):
    """Hard fail-closed zero-leakage gate on all cloud-exportable datasets and bundle JSON."""
    static_forbidden = (
        '"hidden"',
        '"valid_identifier"',
        '"transient_remaining"',
        '"polls_remaining"',
        '"unavailable"',
        '"oracle_value"',
        '"case_id"',
        '"template_group"',
        '"fault_subtype"',
        '"partition"',
        '"source_case_id"',
        "poll_job",
        "get_result",
        "find_job",
        '"jobs"',
        str(root_dir),
        "/Users/",
        "/home/",
        "/private/var/",
        "/var/folders/",
        "experiments/EXP-",
        "data/dev/",
        "data/processed/",
        "models/Qwen",
    )

    dynamic_forbidden = (
        set(pool_info["all_template_groups"])
        | set(pool_info["all_case_ids"])
        | set(pool_info["forbidden_identifiers"])
        | set(pool_info["forbidden_targets"])
        | set(pool_info["forbidden_values"])
    )
    dynamic_forbidden.discard("")

    for artifact_name, text in serialized_blobs.items():
        for tok in static_forbidden:
            if tok and tok in text:
                raise ValueError(
                    f"Cloud zero-leakage gate FAILED on {artifact_name}: contains forbidden token {tok!r}"
                )
        for leak_str in dynamic_forbidden:
            if leak_str in text:
                raise ValueError(
                    f"Cloud zero-leakage gate FAILED on {artifact_name}: leaked split/entity string {leak_str!r}"
                )

    return {
        "passed": True,
        "artifacts_checked": sorted(serialized_blobs.keys()),
        "dynamic_forbidden_string_count": len(dynamic_forbidden),
        "zero_hidden_dicts": True,
        "zero_eval_split_leakage": True,
        "zero_case_or_template_metadata": True,
        "zero_local_filesystem_paths": True,
    }


def _resolve_workspace_root(root_dir=None):
    base = Path(root_dir).resolve() if root_dir is not None else WORKSPACE_ROOT
    if (base / "data/processed").is_dir():
        return base
    parent_manifest = base.parent / "manifest.json"
    if parent_manifest.is_file():
        src = json.loads(parent_manifest.read_text(encoding="utf-8")).get("source_workspace")
        if src and (Path(src) / "data/processed").is_dir():
            return Path(src).resolve()
    return base


def build_g3_datasets(config, output_dir=None, root_dir=None):
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    root = _resolve_workspace_root(root_dir)

    # 1. Verify passing G2 frozen verdict
    verdict_raw_path = config.get("g2_verdict_path")
    if not isinstance(verdict_raw_path, str) or not verdict_raw_path.strip():
        raise ValueError("g2_verdict_path is required in config")
    verdict_path = Path(verdict_raw_path)
    if not verdict_path.is_absolute():
        verdict_path = (root / verdict_path).resolve()
    if not verdict_path.is_file():
        raise FileNotFoundError(f"g2_frozen_verdict.json not found: {verdict_path}")

    actual_verdict_sha = sha256_file(verdict_path)
    pinned_verdict_sha = config.get("g2_verdict_sha256")
    if pinned_verdict_sha is not None and actual_verdict_sha != pinned_verdict_sha:
        raise ValueError(
            f"g2_frozen_verdict.json SHA256 mismatch: expected {pinned_verdict_sha}, got {actual_verdict_sha}"
        )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    if verdict.get("g2_passed") is not True:
        raise ValueError("Fail-closed rejection: g2_frozen_verdict has g2_passed != True")
    if verdict.get("frozen_detector_version") != DETECTOR_V2_VERSION:
        raise ValueError(
            f"frozen_detector_version mismatch: expected {DETECTOR_V2_VERSION!r}, got {verdict.get('frozen_detector_version')!r}"
        )

    # 2. Load isolated candidate pool and tokenizer
    pool_info = _load_and_verify_isolated_pool(
        root, config.get("split_manifest_relpath", "data/processed/grouped_split_manifest_ITER007.json")
    )
    tokenizer, tok_meta = load_qwen_tokenizer(config, root)
    shuffle_seed = int(config.get("shuffle_seed", 20261119))

    # 3. Generate and snapshot-verify all 400 P/B3/B4 records and 400 B5 records
    raw_p = _generate_domain_p_records("orders", pool_info["by_group"], tokenizer) + _generate_domain_p_records(
        "files", pool_info["by_group"], tokenizer
    )
    raw_b5 = _generate_domain_b5_records("orders", pool_info["by_group"], tokenizer) + _generate_domain_b5_records(
        "files", pool_info["by_group"], tokenizer
    )

    rng_p = random.Random(shuffle_seed)
    rng_p.shuffle(raw_p)
    rng_b5 = random.Random(shuffle_seed + 1)
    rng_b5.shuffle(raw_b5)

    if len(raw_p) < 320 or len(raw_p) != len(raw_b5):
        raise ValueError(f"dataset count invariant failed: P={len(raw_p)}, B5={len(raw_b5)}")

    p_rows = []
    b3_rows = []
    b4_rows = []
    p_provenance = {}
    for idx, item in enumerate(raw_p, 1):
        ex_id = f"G3-DP-{idx:04d}"
        td = item["token_divergence"]
        p_rows.append(
            {
                "example_id": ex_id,
                "arm": "P",
                "group": item["group"],
                "subtype": item["subtype"],
                "domain": item["domain"],
                "messages": item["messages"],
                "tools": item["tools"],
                "chosen_text": item["chosen_text"],
                "rejected_text": item["rejected_text"],
                "prompt_token_count": td["prompt_token_count"],
                "chosen_token_count": td["chosen_token_count"],
                "rejected_token_count": td["rejected_token_count"],
                "first_divergence_offset": td["first_divergence_offset"],
            }
        )
        b3_rows.append(
            {
                "example_id": ex_id,
                "arm": "B3",
                "group": item["group"],
                "subtype": item["subtype"],
                "domain": item["domain"],
                "messages": item["messages"],
                "tools": item["tools"],
                "chosen_text": item["chosen_text"],
                "prompt_token_count": td["prompt_token_count"],
                "chosen_token_count": td["chosen_token_count"],
            }
        )
        b4_rows.append(
            {
                "example_id": ex_id,
                "arm": "B4",
                "group": item["group"],
                "subtype": item["subtype"],
                "domain": item["domain"],
                "messages": item["messages"],
                "tools": item["tools"],
                "chosen_text": item["chosen_text"],
                "rejected_text": item["rejected_text"],
                "prompt_token_count": td["prompt_token_count"],
                "chosen_token_count": td["chosen_token_count"],
                "rejected_token_count": td["rejected_token_count"],
                "first_divergence_offset": td["first_divergence_offset"],
                "shared_prefix_token_count": td["shared_prefix_token_count"],
                "full_sequence_divergence_index": td["full_sequence_divergence_index"],
                "divergence_chosen_token_id": td["divergence_chosen_token_id"],
                "divergence_rejected_token_id": td["divergence_rejected_token_id"],
                "divergence_chosen_token_str": td["divergence_chosen_token_str"],
                "divergence_rejected_token_str": td["divergence_rejected_token_str"],
            }
        )
        p_provenance[ex_id] = {**item["provenance"], "token_divergence": td}

    b5_rows = []
    b5_provenance = {}
    for idx, item in enumerate(raw_b5, 1):
        ex_id = f"G3-B5-{idx:04d}"
        td = item["token_divergence"]
        b5_rows.append(
            {
                "example_id": ex_id,
                "arm": "B5",
                "group": item["group"],
                "subtype": item["subtype"],
                "domain": item["domain"],
                "messages": item["messages"],
                "tools": item["tools"],
                "chosen_text": item["chosen_text"],
                "rejected_text": item["rejected_text"],
                "prompt_token_count": td["prompt_token_count"],
                "chosen_token_count": td["chosen_token_count"],
                "rejected_token_count": td["rejected_token_count"],
                "first_divergence_offset": td["first_divergence_offset"],
            }
        )
        b5_provenance[ex_id] = {**item["provenance"], "token_divergence": td}

    p_no_legal_retry = [r for r in p_rows if r["group"] != GROUP_2]
    p_no_normal_ret = [r for r in p_rows if r["group"] != GROUP_4]

    jsonl_datasets = {
        "p_toolloop_fix_dpo.jsonl": p_rows,
        "b3_error_sft.jsonl": b3_rows,
        "b4_single_token_ftpo_style.jsonl": b4_rows,
        "b5_general_expert_dpo.jsonl": b5_rows,
        "p_ablation_no_legal_retry.jsonl": p_no_legal_retry,
        "p_ablation_no_normal_retention.jsonl": p_no_normal_ret,
    }
    serialized_jsonl = {
        fname: "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        for fname, rows in jsonl_datasets.items()
    }
    dataset_digests = {
        fname: {
            "example_count": len(jsonl_datasets[fname]),
            "sha256": sha256_bytes(text.encode("utf-8")),
            "total_chosen_tokens": sum(r["chosen_token_count"] for r in jsonl_datasets[fname]),
            "total_prompt_tokens": sum(r["prompt_token_count"] for r in jsonl_datasets[fname]),
        }
        for fname, text in serialized_jsonl.items()
    }

    group_counts = {
        grp: sum(1 for r in p_rows if r["group"] == grp)
        for grp in (GROUP_1, GROUP_2, GROUP_3, GROUP_4)
    }

    cloud_bundle = {
        "schema_version": G3_DATASET_SCHEMA_VERSION,
        "g3_dataset_ready": bool(tok_meta["is_production_tokenizer"]),
        "g2_passed_verified": True,
        "g2_frozen_verdict_sha256": actual_verdict_sha,
        "frozen_detector_version": DETECTOR_V2_VERSION,
        "dataset_governance_tier": verdict.get("dataset_governance_tier"),
        "protocol_version": CALIBRATED_PROTOCOL_VERSION,
        "task_wording_version": TASK_WORDING_VERSION,
        "base_prompt_arm": "B0",
        "external_guard_enabled_for_P": False,
        "model_target": tok_meta,
        "group_counts": group_counts,
        "dataset_files": dataset_digests,
        "datasets": jsonl_datasets,
    }
    bundle_text = json.dumps(cloud_bundle, ensure_ascii=False, indent=2) + "\n"

    # 4. Run hard fail-closed Cloud Zero-Leakage Gate across bundle + all 6 JSONLs
    leakage_audit = verify_cloud_safe_artifacts(
        {"public_colab_training_bundle.json": bundle_text, **serialized_jsonl},
        pool_info,
        root,
    )

    local_sidecar = {
        "schema_version": G3_DATASET_SCHEMA_VERSION,
        "evidence_class": "analysis",
        "cloud_upload_prohibited": True,
        "g3_dataset_ready": cloud_bundle["g3_dataset_ready"],
        "g2_frozen_verdict_path": str(verdict_path),
        "g2_frozen_verdict_sha256": actual_verdict_sha,
        "grouped_split_manifest_sha256": pool_info["manifest_sha256"],
        "grouped_dev_suite_sha256": pool_info["suite_sha256"],
        "cloud_zero_leakage_audit": leakage_audit,
        "disjointness_verified": {
            "source_partition_only": "dev_InDomain_candidate_pool",
            "allowed_domains": ["orders", "files"],
            "template_group_count": 14,
            "zero_overlap_with_selection_diagnostic_reserved_pilot_iter4_iter6": True,
        },
        "summary": {
            "total_decision_points_N": len(p_rows),
            "b5_expert_pairs_N": len(b5_rows),
            "group_counts": group_counts,
            "dataset_files": dataset_digests,
        },
        "p_b3_b4_provenance": p_provenance,
        "b5_provenance": b5_provenance,
    }

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for fname, text in serialized_jsonl.items():
            _write_exclusive_bytes(out / fname, text.encode("utf-8"))
        _write_exclusive_bytes(out / "public_colab_training_bundle.json", bundle_text.encode("utf-8"))
        _write_exclusive_json(out / "local_only_provenance_sidecar.json", local_sidecar)

    return {
        "cloud_bundle": cloud_bundle,
        "jsonl_datasets": jsonl_datasets,
        "local_sidecar": local_sidecar,
        "leakage_audit": leakage_audit,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to JSON config file")
    parser.add_argument("--output", required=True, help="Output directory for G3 datasets and bundle")
    args = parser.parse_args(argv)

    cfg = json.loads(Path(args.config).resolve().read_text(encoding="utf-8"))
    built = build_g3_datasets(cfg, output_dir=args.output)
    print(
        json.dumps(
            {
                "output_dir": str(Path(args.output)),
                "g3_dataset_ready": built["cloud_bundle"]["g3_dataset_ready"],
                "N": len(built["jsonl_datasets"]["p_toolloop_fix_dpo.jsonl"]),
                "group_counts": built["cloud_bundle"]["group_counts"],
                "cloud_zero_leakage_passed": built["leakage_audit"]["passed"],
                "public_colab_training_bundle_sha256": sha256_file(
                    Path(args.output) / "public_colab_training_bundle.json"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
