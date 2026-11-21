"""Bounded B1 prompting and a B2 guard using only executed public tool history."""
import json

from .harness import canonical_action

BASELINE_VERSION = "public_recovery_baselines_v1"
GUARD_VERSION = "nonretryable_no_new_observation_v2"
B1_GUIDANCE = (
    "Read the latest tool response and the documented requirements before acting. "
    "Preserve identifiers and result values exactly as supplied; do not replace them with inferred labels. "
    "After a non-retryable error, reconsider the parameters or use a documented information-gathering "
    "or prerequisite step instead of repeating the unchanged failed call. "
    "Retry transient failures or poll running work only when the documentation permits it and within its limits. "
    "Ask for missing information only when necessary. Once the requested evidence is obtained, finish without "
    "further work. Stop only when the available evidence justifies stopping, and distinguish inability from success."
)
# These are public response codes implemented by the current environment, not
# oracle categories. TIMEOUT/RUNNING qualify only when explicitly nonretryable.
NONRETRYABLE_CODES = frozenset({
    "INVALID_IDENTIFIER", "MISSING_IDENTIFIER", "RESOURCE_UNAVAILABLE", "NOT_FOUND",
    "PRECONDITION", "INVALID_ARGUMENTS", "UNKNOWN_TOOL", "FORMAT_ERROR",
    "RETRY_LIMIT_EXCEEDED", "UNNECESSARY_CLARIFICATION", "TIMEOUT", "RUNNING",
})
PROGRESS_CODES = frozenset({"LOCATED", "USER_INFORMATION", "OK", "READY"})


def prompt_for(arm):
    """Return an ADDITION to the common calibrated protocol, never a replacement.

    B2 deliberately receives exactly B0's prompt; it is not B1 plus a guard.
    """
    if arm not in {"B0", "B1", "B2"}:
        raise ValueError("baseline arm must be B0, B1 or B2")
    return B1_GUIDANCE if arm == "B1" else ""


def _observation_key(event):
    return (canonical_action(event["action"]),
            json.dumps(event["response"], sort_keys=True, ensure_ascii=False))


def guard_action(view, action):
    """Return a pure would-block decision; caller applies it only in B2.

    Input history must contain actual public environment events only. Blocked
    proposals/guard messages belong in the dialogue, not this history. The guard
    never executes, substitutes or terminates an action, and does not own budgets.

    A prior equivalent action must have an explicit false retryable flag and a
    known error code. Any new action/response observation, public success or
    permitted retry/poll response conservatively prevents a block. Thus ABAB can be caught
    at the fourth proposal without treating the first new B result as no progress.
    """
    verdict = {"blocked": False, "reason": None, "feedback": None,
               "matched_history_index": None, "prior_response_code": None,
               "rule_version": GUARD_VERSION}
    if (not isinstance(action, dict) or action.get("type") != "tool" or
            not isinstance(action.get("arguments"), dict) or action.get("tool") not in view["tools"]):
        return verdict
    key = canonical_action(action)
    history = view["history"]
    for index in range(len(history) - 1, -1, -1):
        event = history[index]
        if canonical_action(event["action"]) != key:
            continue
        response = event["response"]
        # The most recent result supersedes older failures, even if unknown.
        if response.get("retryable") is not False or response.get("code") not in NONRETRYABLE_CODES:
            return verdict
        known_information = {_observation_key(item) for item in history[:index + 1]}
        for intervening in history[index + 1:]:
            observed = intervening["response"]
            if (observed.get("retryable") is True or observed.get("code") in PROGRESS_CODES or
                    _observation_key(intervening) not in known_information):
                return verdict
        code = response["code"]
        verdict.update(blocked=True, reason="REPEATED_NONRETRYABLE_WITHOUT_NEW_INFORMATION",
            matched_history_index=index, prior_response_code=code,
            feedback=("This proposed tool call was not executed: an equivalent call already returned " + code +
                      ", and no new tool information has arrived. Reconsider the plan using the visible history "
                      "and tool documentation. This proposal still consumes the interaction budget."))
        return verdict
    return verdict
