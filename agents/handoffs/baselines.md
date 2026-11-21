# B1 / B2 component handoff — 2026-11-16

## Scope and status
Only new files were added: `toolloop_fix/baselines.py`, `tests/test_baselines.py`, and this handoff. Existing harness, model adapter, recorders and 17 prior runs were not edited. No GPU call, installation, training or model experiment was performed by this agent.

Root approved the API/design before implementation. The component scope is now frozen for integration.

## Interfaces
- `prompt_for(arm: str) -> str`: returns an **addition** to the common calibrated protocol. `B0` and `B2` return `""`; `B1` returns generic recovery guidance. Unknown/composed arm names raise `ValueError`. B2 is not B1+B2.
- `guard_action(view, action) -> dict`: pure, stateless **would-block** decision. Fields: `blocked`, `reason`, `feedback`, `matched_history_index`, `prior_response_code`, `rule_version`.
- `BASELINE_VERSION = public_recovery_baselines_v1`; `GUARD_VERSION = nonretryable_no_new_information_v1`.

Root should call the same guard observationally for all arms, but prevent actual execution **only in B2**. `blocked` is a rule-trigger signal, not proof of task success, hidden-state impossibility, or a validated semantic-loop classifier.

## B1
Guidance preserves exact supplied identifiers/values, consults documented prerequisites/information gathering after nonretryable errors, preserves permitted finite retries/polling, avoids unnecessary requests for information, and finishes/stops only with appropriate evidence. It contains no case IDs, golden values, named domain tools, hidden categories or task-specific correct actions.

## B2 decision rule
1. Consider only parsed `type=tool` actions whose tool is in the current public docs and whose arguments are a mapping. Natural-language terminals, structured finish/stop, unknown tools and malformed protocol actions remain for the existing adapter/environment to handle.
2. Match the most recent equivalent executed action using the environment's existing `canonical_action` (including its documented path/whitespace equivalence and ignored top-level metadata).
3. Require a known public response code and an explicit Boolean `retryable=False`. Missing, unknown or non-Boolean flags are not assumed to mean permanent failure.
4. Block only if no intervening response provides new public information. A permitted retry/poll response (`retryable=True`) conservatively permits proceeding even if its response text was previously seen.
5. An adjacent unchanged permanent failure can be blocked after the first failure. An A→B→A→B no-information cycle can be blocked on the fourth proposal; the first new B response is not discarded as no progress.

TIMEOUT/RUNNING with `retryable=True` are never blocked by this rule. With explicit False they indicate the environment's public limit has been reached and may qualify. A newer response to the same action supersedes older failures. Repeated successful reads and unknown error codes are outside this conservative guard.

## Critical runner responsibilities
- Pass **only actual executed environment history** (`env.public_view()`) to the guard. Keep blocked proposals and feedback in the model-visible dialogue and separate logs, not in the guard's executed history.
- On block, do **not** call `env.execute` with a fabricated response. Do not substitute a correct action, terminal action or success result. Deliver the returned generic replan feedback.
- Each blocked proposal still consumes the shared proposal/interaction budget. Record original proposal/raw generation, guard reason/index, executed=false, actual execution counts and remaining budget.
- Preserve matched explicit per-case seeds and common protocol/backend/config across B0/B1/B2.
- Report guard-proposed-repeat triggers separately from executed-loop detections, tool-goal attainment, structured completion, premature stopping, budget exhaustion and cost. Fewer executed loops after blocking alone is not evidence that the model learned recovery.

## Actual verification
Command: `python3 -m unittest discover -s tests -p test_baselines.py -v`.
Result: **18 tests passed**. This was a narrow diagnostic; root owns the archived integration test and model runs.

Coverage: B0/B2 prompt identity, generic B1, unknown arms, adjacent/equivalent repeats, changed args, legal transient retry/poll, explicit exhausted limits, missing/nonboolean flags, unknown errors, new information, ABAB, latest-response precedence, snapshot/view/action nonmutation, repeated would-block determinism, terminal/malformed passthrough, and no reading of hidden labels/request.

## Remaining boundaries
This is an engineering baseline, not a trained policy or proven optimal guard. Public-response equality is conservative: incidental new fields count as information, while unseen semantic cycles are not covered. It does not infer hidden state changes, generate repair actions or stop the task itself. Its fixed code list and normalizer apply to the current simulated API; new interfaces require separately reviewed rules. The existing detector and this prospective guard have different coverage, so their rates must not be given interchangeable names.

## Pre-integration audit refinement
The source auditor identified that response-only equality lost the fact that a different action had just been tried. Added two regression tests before the fix: a new B action with the same INVALID_IDENTIFIER payload must count as a new observation, and repeated successful public results must conservatively reset the guard. The narrow pre-fix run executed 20 tests and produced 5 failing assertions (one action/response case plus four progress-code subtests).

Minimal fix: observation identity is now `(canonical executed action, public response)`, not response alone. LOCATED / USER_INFORMATION / OK / READY and permitted retry/poll responses always conservatively reset the block decision, even if the exact observation was seen earlier. ABAB remains detectable at the fourth proposal after its observations repeat.

`GUARD_VERSION` is now **`nonretryable_no_new_observation_v2`**; prompt version/behavior are unchanged. Post-fix actual narrow command `python3 -m unittest discover -s tests -p test_baselines.py -v`: **20 tests passed**. Root owns the archived integrated verification and real experiments. Scope is frozen again; no prior run was edited.

## Comparison-script independent test addition (inference files remain frozen)
Root assigned only `tests/test_calibrated_comparison.py` plus this handoff. Added 17 tests using synthetic temporary run artifacts; no GPU/model call and no edits to inference modules, old tests, source runs or the comparison script.

The diagnostic command `python3 -m unittest discover -s tests -p test_calibrated_comparison.py -v` currently executes **17 tests with 20 failing assertions/subtests, 0 errors** against the initial comparison script. An earlier first pass also had two test-fixture construction errors, corrected in this new test file before the reported diagnostic result.

Already passing: three-arm uniqueness, correct paired wins/ties/losses and unreachable exclusion, read-only behavior and pinned source references, common-config / declared-runtime-hash / case-order / seed mismatches, manifest/output tamper detection, CLI overwrite refusal and Markdown table structure.

Regressions reported to root (script owner): actual runtime/model/sampling/template/effective-generation/device/dtype/weight-manifest/guard/protocol/score/seed provenance agreement; B0/B2 prompt identity with B1 allowed to differ; shared data hashes; verification of frozen input bytes; mandatory output-hash coverage; stable completable/category/template metadata; and N/A instead of 0/0 for zero denominators. Root will repair the script; these tests deliberately remain failing until that change, without changing active inference inputs.

### Comparison repair readback
Root repaired only `scripts/compare_calibrated_runs.py`; this agent independently read the resulting script and reran `python3 -m unittest discover -s tests -p test_calibrated_comparison.py -v`: **17 tests passed**. The script now verifies actual complete input/output inventories, paired case data and metadata, the required actual provenance fields and B0/B2 prompt identity; zero-denominator display is N/A. Wins/ties/losses remain restricted to completable cases and original source files/references remain unchanged.

`tests/test_calibrated_comparison.py` and this handoff are now frozen. These are synthetic comparison/record tests, not additional model observations; inference modules/configurations and all historical runs remain untouched by this agent.
