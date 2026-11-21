"""Observer-only loop detector v2 using exclusively executed public tool history."""
from .harness import canonical_action

DETECTOR_V2_VERSION = "nonretryable_repeat_without_progress_v2"

NONRETRYABLE_ERROR_CODES = frozenset(
    {
        "INVALID_IDENTIFIER",
        "MISSING_IDENTIFIER",
        "RESOURCE_UNAVAILABLE",
        "NOT_FOUND",
        "PRECONDITION",
        "INVALID_ARGUMENTS",
        "UNKNOWN_TOOL",
        "FORMAT_ERROR",
        "RETRY_LIMIT_EXCEEDED",
        "UNNECESSARY_CLARIFICATION",
    }
)

PROGRESS_CODES = frozenset({"OK", "LOCATED", "USER_INFORMATION", "READY"})


def _step_achieved_progress(event):
    if not isinstance(event, dict):
        return False
    observed = event.get("response")
    if not isinstance(observed, dict):
        return False
    return bool(observed.get("retryable") is True or observed.get("code") in PROGRESS_CODES)


def detect_loops_v2(history):
    """Detect avoidable invalid tool loops from public executed tool history only.

    Preserves all v1 detections (`retry_limit_violation`, adjacent
    `identical_or_equivalent_repeat`, `short_cycle_abab`) and adds
    `nonadjacent_or_multistep_repeat_without_progress` whenever step `index`
    repeats a prior canonical action at `prev_idx < index - 1` whose response
    had `retryable is False` and `code in NONRETRYABLE_ERROR_CODES` with no
    intervening step in `history[prev_idx + 1 : index]` achieving progress
    (`code in PROGRESS_CODES` or `retryable is True`).

    Guarantees 0% false positives on documented legal `TIMEOUT` retries
    (1st and 2nd retries) and documented legal `poll_job` calls (1..3 polls).
    """
    flags = []
    for index, current in enumerate(history):
        if not isinstance(current, dict):
            continue
        response = current.get("response")
        if not isinstance(response, dict):
            continue
        if response.get("retryable") is not False or response.get("code") not in NONRETRYABLE_ERROR_CODES:
            continue

        key = canonical_action(current.get("action"))
        most_recent_prior_idx = None
        for prev_idx in range(index - 1, -1, -1):
            prev_item = history[prev_idx]
            if isinstance(prev_item, dict) and canonical_action(prev_item.get("action")) == key:
                most_recent_prior_idx = prev_idx
                break

        if response.get("code") == "RETRY_LIMIT_EXCEEDED":
            flags.append(
                {
                    "index": index,
                    "type": "retry_limit_violation",
                    "matched_prior_index": most_recent_prior_idx,
                }
            )

        matched_adjacent = False
        if index >= 1 and most_recent_prior_idx == index - 1:
            previous = history[index - 1].get("response")
            if (
                isinstance(previous, dict)
                and previous == response
                and previous.get("retryable") is False
                and previous.get("code") in NONRETRYABLE_ERROR_CODES
            ):
                matched_adjacent = True
                flags.append(
                    {
                        "index": index,
                        "type": "identical_or_equivalent_repeat",
                        "matched_prior_index": index - 1,
                    }
                )

        matched_abab = False
        if index >= 3:
            window = history[index - 3 : index + 1]
            if all(isinstance(item, dict) and isinstance(item.get("response"), dict) for item in window):
                actions = [canonical_action(item.get("action")) for item in window]
                if (
                    actions[0] == actions[2]
                    and actions[1] == actions[3]
                    and actions[0] != actions[1]
                    and all(
                        item["response"].get("code") in NONRETRYABLE_ERROR_CODES
                        and item["response"].get("retryable") is False
                        for item in window
                    )
                    and window[0]["response"] == window[2]["response"]
                    and window[1]["response"] == window[3]["response"]
                ):
                    matched_abab = True
                    flags.append(
                        {
                            "index": index,
                            "type": "short_cycle_abab",
                            "matched_prior_index": index - 2,
                        }
                    )

        if not matched_adjacent and not matched_abab:
            for prev_idx in range(index - 2, -1, -1):
                prev_item = history[prev_idx]
                if not isinstance(prev_item, dict) or canonical_action(prev_item.get("action")) != key:
                    continue
                prev_resp = prev_item.get("response")
                if (
                    isinstance(prev_resp, dict)
                    and prev_resp.get("retryable") is False
                    and prev_resp.get("code") in NONRETRYABLE_ERROR_CODES
                ):
                    intervening = history[prev_idx + 1 : index]
                    if not any(_step_achieved_progress(step) for step in intervening):
                        flags.append(
                            {
                                "index": index,
                                "type": "nonadjacent_or_multistep_repeat_without_progress",
                                "matched_prior_index": prev_idx,
                            }
                        )
                break

    return flags


def has_invalid_loop_v2(history, start_index=0):
    """Return True iff `detect_loops_v2` emits any flag at `index >= start_index`."""
    return any(flag["index"] >= start_index for flag in detect_loops_v2(history))
