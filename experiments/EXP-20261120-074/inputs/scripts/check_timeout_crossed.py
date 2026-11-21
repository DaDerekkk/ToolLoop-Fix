"""Exercise twelve handcrafted, matched visible prefixes; never invoke a model."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

from toolloop_fix.calibrated_protocol import messages_for_calibrated_view, parse_calibrated_output, score_calibrated_episode
from toolloop_fix.harness import Environment, tool_call
from toolloop_fix.model_protocol import tool_schemas

ALIASES = {"get_order": "orders", "read_file": "files"}
IDS = ("R-17", "R-42")
CODES = ("TIMEOUT", "OK", "RESOURCE_UNAVAILABLE")
VALUE = "amber"


def check(ok, msg):
    if not ok:
        raise ValueError(msg)


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def call(name, **args):
    body = "".join(f"<parameter={k}>{v}</parameter>" for k, v in args.items())
    return parse_calibrated_output(f"<tool_call><function={name}>{body}</function></tool_call>")


def fixture(row):
    name, ident, code = (row[k] for k in ("tool_name", "identifier", "condition"))
    case = {"domain": ALIASES[name], "request": {"instruction": "Read the record and report its value and identifier.",
            "identifier": ident}, "category": "unrecoverable" if code == CODES[2] else "normal",
            "hidden": {"valid_identifier": ident, "value": VALUE, "transient_remaining": int(code == "TIMEOUT"),
                       "unavailable": code == CODES[2], "polls_remaining": 0}}
    env = Environment(case)
    prefix = call(name, identifier=ident)
    check(prefix["action"] == tool_call(name, identifier=ident), "reference prefix invalid")
    resp = env.execute(prefix["action"])["response"]
    expected = {"code": code, "identifier": ident, "value": VALUE} if code == "OK" else {
        "code": code, "retryable": code == "TIMEOUT"}
    check(resp == expected, "unexpected prefix response")
    snap = env.snapshot()
    # Explicit allowlist: never project the case record, private state, or other (unoffered) tools.
    view = {"request": case["request"], "history": env.public_view()["history"],
            "tools": {name: {"arguments": {"identifier": "string"},
                             "description": "Read a record by identifier. A transient timeout permits at most two retries."}}}
    payload = {"messages": messages_for_calibrated_view(view, [prefix]), "tools": tool_schemas(view)}
    check([t["function"]["name"] for t in payload["tools"]] == [name, "finish_task", "stop_task"], "tool leak")
    check([x["role"] for x in payload["messages"]] == ["system", "user", "assistant", "tool"], "history roles")
    text = canonical(payload)
    check(not any(x in text for x in (row["case_id"], "condition", "transient_remaining", "valid_identifier",
              "template_group", "unrecoverable", "case_id")), "unexpected metadata in public request")
    check(code == "OK" or VALUE not in text, "undisclosed value leaked")

    def apply(decisions):
        env.restore(snap)
        responses = [env.execute(x["action"])["response"] for x in decisions]
        return responses, score_calibrated_episode(case, env.snapshot(), decisions, budget_exhausted=False)

    finish = call("finish_task", answer=VALUE, identifier=ident)
    terminal = call("stop_task", reason=code) if code == CODES[2] else finish
    continuation = ([call(name, identifier=ident)] if code == "TIMEOUT" else []) + [terminal]
    responses, scores = apply(continuation)
    if code == "TIMEOUT":
        check(responses[0] == {"code": "OK", "identifier": ident, "value": VALUE},
              "legal retry did not yield new evidence")
    check(scores["exact_grounded_structured_completion"] == (code != CODES[2]) and
          scores["exact_stop_reason_code"] == (code == CODES[2]), "scripted control outcome")
    check(apply([parse_calibrated_output("The request is complete.")])[1]
          ["natural_termination_semantics_unknown"], "natural prose must remain ungraded")
    if code != "OK":
        check(apply([finish])[1]["unsupported_finish_wrapper"], "unsupported finish not detected")
    else:
        check(apply([call("finish_task", answer="invented", identifier=ident)])[1]
              ["unsupported_finish_wrapper"], "wrong value incorrectly accepted")
    result = {"case_id": row["case_id"], "tool": name, "identifier": ident, "prefix_response": resp,
              "payload_sha256": hashlib.sha256(text.encode()).hexdigest(),
              "scripted_actions": [d["action"] for d in continuation], "scripted_responses": responses,
              "new_goal_evidence": code == "TIMEOUT" and responses[0]["code"] == "OK",
              "exact_structured_completion": scores["exact_grounded_structured_completion"],
              "exact_stop_reason": scores["exact_stop_reason_code"]}
    return text, result, text.replace(name, "<alias>").replace(ident, "<id>")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    path = Path(json.loads(a.config.read_text())["dataset"])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    grid = list(itertools.product(ALIASES, IDS, CODES))
    keys = [(r["tool_name"], r["identifier"], r["condition"]) for r in rows]
    check(sorted(keys) == sorted(grid) and len({r["case_id"] for r in rows}) == len(grid), "incomplete grid")
    check(all(r["split"] == "dev" and r["template_group"] == "neutral-read-v1" for r in rows), "split/group")
    payloads, traces, groups = [], [], {}
    for row in rows:
        text, result, norm = fixture(row)
        check(norm == groups.setdefault(row["condition"], norm), "unexpected within-condition public difference")
        payloads.append(text)
        traces.append(result)
    for name, lines in (("public_payloads.jsonl", payloads), ("synthetic_trajectories.jsonl", map(canonical, traces))):
        with (a.output / name).open("x") as f:
            for line in lines:
                f.write(line + "\n")
    summary = {"kind": "synthetic_harness", "cells": len(rows), "template_groups": 1,
               "normalized_payload_clusters": len(set(groups.values())), "scripted_new_goal_evidence": sum(
                   x["new_goal_evidence"] for x in traces), "cloud_access": False, "model_generations": 0,
               "training": False, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    check(summary["normalized_payload_clusters"] == 3, "outcome must remain visible")
    with (a.output / "fixture_results.json").open("x") as f:
        f.write(json.dumps(summary, indent=2) + "\n")
    print(canonical(summary))


if __name__ == "__main__":
    main()
