"""Small deterministic tool environment with an explicit public/private boundary."""
import copy
import hashlib
import json
import posixpath

EVIDENCE_CLASS = "synthetic_harness"
DOMAIN_TOOLS = {
    "orders": ("get_order", "find_order"),
    "files": ("read_file", "search_file"),
    "jobs": ("get_result", "find_job"),
}


def canonical_action(action):
    """Only normalize equivalences also honored by the simulated tool API."""
    value = copy.deepcopy(action)
    if isinstance(value, dict) and isinstance(value.get("arguments"), dict):
        for key, item in value["arguments"].items():
            if isinstance(item, str):
                item = item.strip()
                if key == "identifier" and value.get("tool") == "read_file" and item:
                    item = posixpath.normpath(item)
                value["arguments"][key] = item
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def state_hash(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def tool_call(name, **arguments):
    return {"type": "tool", "tool": name, "arguments": arguments}


class Environment:
    """Private case data never enters public_view; all mutations are in memory."""

    def __init__(self, case):
        self._case = copy.deepcopy(case)
        self._state = copy.deepcopy(case["hidden"])
        self._history = []
        self._terminal = None
        self._evidence = None

    def snapshot(self):
        return copy.deepcopy({"state": self._state, "history": self._history,
                              "terminal": self._terminal, "evidence": self._evidence})

    def restore(self, snapshot):
        self._state = copy.deepcopy(snapshot["state"])
        self._history = copy.deepcopy(snapshot["history"])
        self._terminal = copy.deepcopy(snapshot["terminal"])
        self._evidence = copy.deepcopy(snapshot["evidence"])

    def public_view(self):
        domain = self._case["domain"]
        primary, locate = DOMAIN_TOOLS[domain]
        docs = {
            primary: {"arguments": {"identifier": "string"},
                      "description": "Read the requested result. Invalid identifiers require lookup; transient timeout permits at most 2 retries."},
            locate: {"arguments": {"target": "string"},
                     "description": "Locate a resource by the target given in the request."},
            "ask_user": {"arguments": {"field": "identifier"},
                         "description": "Request the identifier when it is absent from the user request."},
        }
        if domain == "jobs":
            docs["poll_job"] = {"arguments": {"identifier": "string"},
                                "description": "Poll a running job, at most 3 times, then get_result after READY."}
        return copy.deepcopy({"request": self._case["request"], "tools": docs,
                              "history": self._history})

    def _response(self, code, **fields):
        return {"code": code, **fields}

    def execute(self, action):
        """Execute one interaction. The runner, not this method, owns budgets."""
        if self._terminal is not None:
            raise ValueError("cannot execute after terminal action")
        before = state_hash(self._state)
        response = self._execute(action)
        event = {"action": copy.deepcopy(action), "response": response}
        self._history.append(event)
        return {**copy.deepcopy(event), "state_before": before,
                "state_after": state_hash(self._state)}

    def _execute(self, action):
        if not isinstance(action, dict):
            return self._response("FORMAT_ERROR", retryable=False)
        kind = action.get("type")
        if kind == "final":
            self._terminal = copy.deepcopy(action)
            return self._response("FINAL_RECORDED")
        if kind == "stop":
            self._terminal = copy.deepcopy(action)
            return self._response("STOP_RECORDED")
        if kind != "tool" or not isinstance(action.get("arguments"), dict):
            return self._response("FORMAT_ERROR", retryable=False)
        action = json.loads(canonical_action(action))
        tool, args = action.get("tool"), action["arguments"]
        docs = self.public_view()["tools"]
        if tool not in docs:
            return self._response("UNKNOWN_TOOL", retryable=False)
        required = set(docs[tool]["arguments"])
        if set(args) != required or any(not isinstance(v, str) for v in args.values()):
            return self._response("INVALID_ARGUMENTS", retryable=False)
        primary, locate = DOMAIN_TOOLS[self._case["domain"]]
        state = self._state
        if tool == "ask_user":
            if args["field"] != "identifier":
                return self._response("INVALID_ARGUMENTS", retryable=False)
            if self._case["request"].get("identifier"):
                return self._response("UNNECESSARY_CLARIFICATION", retryable=False)
            return self._response("USER_INFORMATION", identifier=state["valid_identifier"])
        if tool == locate:
            if args["target"] != self._case["request"]["target"]:
                return self._response("NOT_FOUND", retryable=False)
            if state["unavailable"]:
                return self._response("RESOURCE_UNAVAILABLE", retryable=False)
            # The user must provide the missing identifier; lookup cannot bypass this gate.
            if not self._case["request"].get("identifier"):
                return self._response("MISSING_IDENTIFIER", retryable=False)
            return self._response("LOCATED", identifier=state["valid_identifier"])
        identifier = args["identifier"]
        if not identifier:
            return self._response("MISSING_IDENTIFIER", retryable=False)
        if state["unavailable"]:
            return self._response("RESOURCE_UNAVAILABLE", retryable=False)
        if identifier != state["valid_identifier"]:
            return self._response("INVALID_IDENTIFIER", retryable=False)
        if tool == "poll_job":
            if state["polls_remaining"] > 0:
                state["polls_remaining"] -= 1
            return self._response("READY" if state["polls_remaining"] == 0 else "RUNNING",
                                  retryable=state["polls_remaining"] > 0)
        if tool != primary:
            return self._response("UNKNOWN_TOOL", retryable=False)
        if state["transient_remaining"] > 0:
            state["transient_remaining"] -= 1
            return self._response("TIMEOUT", retryable=True)
        if state["polls_remaining"] > 0:
            return self._response("PRECONDITION", retryable=False, detail="poll_job before get_result")
        self._evidence = {"identifier": identifier, "value": state["value"]}
        return self._response("OK", **self._evidence)

    def evaluate(self, budget_exhausted):
        terminal = self._terminal or {}
        final_claim = terminal.get("type") == "final"
        grounded = (self._evidence is not None and
                    terminal.get("answer") == self._case["hidden"]["value"] and
                    terminal.get("identifier") == self._case["hidden"]["valid_identifier"])
        success = final_claim and grounded and not budget_exhausted
        unreachable = self._case["category"] == "unrecoverable"
        seen_codes = {e["response"]["code"] for e in self._history[:-1]}
        correct_stop = (unreachable and terminal.get("type") == "stop" and
                        terminal.get("reason") == "RESOURCE_UNAVAILABLE" and
                        "RESOURCE_UNAVAILABLE" in seen_codes and not budget_exhausted)
        return {"success": success, "correct_termination": correct_stop,
                "false_success": final_claim and not grounded,
                "premature_abandonment": not unreachable and terminal.get("type") == "stop",
                "budget_exhausted": budget_exhausted, "completable": not unreachable}


def detect_loops(history):
    """Observer only: public responses and actions, no oracle state/category.

    An invalid repeat repeats a prior non-retryable error without any intervening
    new response information. ABAB requires four public non-retryable errors.
    This intentionally conservative prototype is NOT a validated detector.
    """
    errors = {"INVALID_IDENTIFIER", "MISSING_IDENTIFIER", "RESOURCE_UNAVAILABLE",
              "NOT_FOUND", "PRECONDITION", "INVALID_ARGUMENTS", "UNKNOWN_TOOL", "FORMAT_ERROR"}
    flags = []
    for index, current in enumerate(history):
        response = current["response"]
        if response.get("retryable") or response.get("code") not in errors:
            continue
        key = canonical_action(current["action"])
        if index and canonical_action(history[index - 1]["action"]) == key:
            previous = history[index - 1]["response"]
            if previous == response and previous.get("code") in errors:
                flags.append({"index": index, "type": "identical_or_equivalent_repeat"})
        if index >= 3:
            window = history[index - 3:index + 1]
            actions = [canonical_action(item["action"]) for item in window]
            if (actions[0] == actions[2] and actions[1] == actions[3] and actions[0] != actions[1]
                    and all(item["response"].get("code") in errors
                            and not item["response"].get("retryable") for item in window)
                    and window[0]["response"] == window[2]["response"]
                    and window[1]["response"] == window[3]["response"]):
                flags.append({"index": index, "type": "short_cycle_abab"})
    return flags


def scripted_policy(view, name):
    """Policies receive ONLY public_view, not the case or private snapshot."""
    if name not in {"scripted_naive", "scripted_reference"}:
        raise ValueError("unknown scripted policy")
    request, history = view["request"], view["history"]
    primary, locate = DOMAIN_TOOLS[request["domain"]]
    identifier = request.get("identifier", "")
    if history:
        last = history[-1]
        response = last["response"]
        code = response["code"]
        if code == "OK":
            return {"type": "final", "answer": response["value"],
                    "identifier": response["identifier"]}
        if name == "scripted_naive":
            return copy.deepcopy(last["action"])
        if code == "RESOURCE_UNAVAILABLE":
            return {"type": "stop", "reason": code}
        if code in {"LOCATED", "USER_INFORMATION"}:
            return tool_call(primary, identifier=response["identifier"])
        if code == "INVALID_IDENTIFIER":
            return tool_call(locate, target=request["target"])
        if code == "MISSING_IDENTIFIER":
            return tool_call("ask_user", field="identifier")
        if code in {"PRECONDITION", "RUNNING"}:
            return tool_call("poll_job", identifier=last["action"]["arguments"]["identifier"])
        if code == "READY":
            return tool_call(primary, identifier=last["action"]["arguments"]["identifier"])
        if code == "TIMEOUT":
            return copy.deepcopy(last["action"])
    if not identifier and name == "scripted_reference":
        return tool_call("ask_user", field="identifier")
    return tool_call(primary, identifier=identifier)


def validate_alternative(env, candidate):
    """Execute in an isolated snapshot and conservatively verify a local step.

    Candidate strings must be present in public request/history (tool names and
    the documented field name are allowed). No claim of full-path recovery.
    """
    snapshot = env.snapshot()
    view = env.public_view()
    public_values = set()
    def collect(value):
        if isinstance(value, str):
            public_values.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(view["request"])
    collect(view["history"])
    public_values.add("identifier")
    args = candidate.get("arguments", {}) if isinstance(candidate, dict) else {}
    visible = (isinstance(candidate, dict) and candidate.get("type") == "tool" and
               candidate.get("tool") in view["tools"] and isinstance(args, dict) and
               all(isinstance(value, str) and value in public_values for value in args.values()))
    try:
        trace = env.execute(candidate)
        new_information = trace["response"] not in [event["response"] for event in view["history"]]
        state_progress = trace["state_before"] != trace["state_after"]
        valid = (trace["response"]["code"] in {"OK", "LOCATED", "USER_INFORMATION", "READY"}
                 and (new_information or state_progress))
        result = {"candidate": candidate, "public_arguments_only": visible,
                  "locally_validated": visible and valid, "response": trace["response"],
                  "new_information": new_information, "state_progress": state_progress,
                  "validation_scope": "one_step_synthetic_not_training_ready"}
    finally:
        env.restore(snapshot)
    return result


def run_case(case, policy, mode, tool_budget=12, turn_budget=16):
    env = Environment(case)
    traces, prefix_count = [], 0
    if mode == "fixed_failure_prefix":
        prefix = case.get("fixed_prefix", [])
        if not prefix:
            raise ValueError("fixed mode requires a curated prefix")
        for action in prefix:
            traces.append({**env.execute(action), "phase": "curated_prefix"})
        prefix_count = len(traces)
    elif mode != "end_to_end":
        raise ValueError("unknown evaluation mode")
    prefix_snapshot = env.snapshot()
    prefix_hash = state_hash(prefix_snapshot)
    calls = 0
    turns = 0
    exhausted = False
    while env._terminal is None:
        if calls >= tool_budget or turns >= turn_budget:
            exhausted = True
            break
        action = scripted_policy(env.public_view(), policy)
        # Malformed attempts also consume the tool-attempt budget.
        is_attempt = not isinstance(action, dict) or action.get("type") not in {"final", "stop"}
        calls += int(is_attempt)
        turns += 1
        traces.append({**env.execute(action), "phase": "continuation"})
    outcome = env.evaluate(exhausted)
    flags = detect_loops(env.public_view()["history"])
    identity = {"case_id": case["case_id"], "domain": case["domain"],
                "category": case["category"], "template_group": case["template_group"],
                "policy": policy, "mode": mode, "evidence_class": EVIDENCE_CLASS}
    result = {**identity, **outcome, "tool_attempts": calls, "turns": turns,
              "prefix_tool_attempts": prefix_count, "has_invalid_loop": bool(flags),
              "loop_types": sorted({flag["type"] for flag in flags}),
              "prefix_snapshot_sha256": prefix_hash}
    trajectory = {**identity, "public_initial_request": case["request"],
                  "prefix_provenance": "hand_authored_fixture_not_model_failure" if prefix_count else None,
                  "prefix_snapshot": prefix_snapshot, "events": traces, "loop_flags": flags,
                  "final_snapshot": env.snapshot()}
    return result, trajectory


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def summarize(results):
    groups = []
    for policy, mode in sorted({(r["policy"], r["mode"]) for r in results}):
        rows = [r for r in results if r["policy"] == policy and r["mode"] == mode]
        complete = [r for r in rows if r["completable"]]
        normal = [r for r in rows if r["category"] == "normal"]
        recoverable = [r for r in rows if r["category"] == "recoverable"]
        retry = [r for r in rows if r["category"] == "retry_required"]
        unreachable = [r for r in rows if not r["completable"]]
        successes = sum(r["success"] for r in complete)
        groups.append({"policy": policy, "mode": mode, "case_count": len(rows),
            "task_completion": ratio(successes, len(complete)),
            "normal_completion": ratio(sum(r["success"] for r in normal), len(normal)),
            "error_recovery": ratio(sum(r["success"] for r in recoverable), len(recoverable))
                if mode == "fixed_failure_prefix" else ratio(0, 0),
            "invalid_loop_rate": ratio(sum(r["has_invalid_loop"] for r in rows), len(rows)),
            "necessary_retry_success": ratio(sum(r["success"] for r in retry), len(retry)),
            "premature_abandonment": ratio(sum(r["premature_abandonment"] for r in complete), len(complete)),
            "false_success": ratio(sum(r["false_success"] for r in rows), len(rows)),
            "correct_termination": ratio(sum(r["correct_termination"] for r in unreachable), len(unreachable)),
            "budget_exhaustion": ratio(sum(r["budget_exhausted"] for r in rows), len(rows)),
            "tool_attempts_per_success": ratio(sum(r["tool_attempts"] for r in complete), successes),
            "cost_scope": "continuation_only_in_fixed_prefix; tool_attempts_not_tokens_or_money"})
    return {"schema_version": 1, "evidence_class": EVIDENCE_CLASS,
            "warning": "Scripted policies on 15 development fixtures; not model or training evidence.",
            "detector_status": "prototype_not_human_validated", "groups": groups}
