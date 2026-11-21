# Simulator handoff — 2026-11-16

## Delivered scope
- `toolloop_fix/harness.py`: resettable, in-memory order/file/job environment; detached public views; scripted naive/reference policies; conservative visible-history detector; evaluator and metrics; one-step candidate verification with snapshot restoration.
- `toolloop_fix/run.py`: CLI writes new `trajectories.jsonl`, `case_results.jsonl`, `metrics.json`; refuses existing artifacts.
- `data/dev/pilot_cases.jsonl`: 15 manually authored fixtures (three domains × five categories), all **dev**, not a formal dataset.
- `configs/smoke.json`: deterministic synthetic-only configuration, 12 tool-attempt / 16 interaction budgets.
- `tests/test_harness.py`: 26 unit/integration tests.

## Actual verification
Command executed: `python3 -m unittest discover -s tests -p test_harness.py -v`.
Final local result: **26 tests passed** (Python 3.9-compatible standard library).
An earlier implementation pass ran 25 tests successfully; a final test now also rejects a repeated information-only candidate as non-progress. Both are exploratory checks; coordinator will rerun through the experiment recorder for archival evidence.

CLI integration test executes in an isolated temporary directory and checks 42 rows:
- 15 end-to-end fixtures × 2 scripted policies = 30.
- 6 fixed-prefix fixtures (recoverable/retry-required) × 2 scripted policies = 12.

Tests cover malformed-budget accounting, independent turn cap, no success on budget exhaustion, grounded answer checks, unreachable denominators, failure-inclusive call cost, legal retry/polling negatives, equivalent actions, ABAB errors, same-prefix hashes, snapshot restoration, public/private isolation and overwrite refusal.

## Evidence boundary and limitations
- **No Qwen / other LLM inference, no training, no paid call, no package installation.** Scripted policies are not B0/B1/B2.
- Fixed prefixes are hand-authored fixture inputs, not mined model failures. There are no confidence intervals or sampling-seed replications; `seed: 0` is recorded but randomness is not used.
- Three/four tool interfaces per domain (including simulated ask_user), fewer than the proposal's planned 4–8. No general-purpose language parser, real writes or external executor.
- Detector is a conservative prototype for adjacent equivalent permanent errors and ABAB permanent-error cycles. It does not validate semantic loops, repeated successful reads, all longer cycles, or detect arbitrary infinite transient retries. No 200-segment human audit has occurred.
- Candidate validation supports tool steps only, checks public argument provenance and new public information / simulator state progress; it is **not** a full-trajectory success proof, training dataset builder, final/stop validator or trusted preference oracle.
- `ask_user` receives a scripted user response. Tool-policy execution can only see request/tools/history; complete trajectory artifacts intentionally include evaluator snapshots and must never be passed wholesale to a model.
- Fixed-prefix tool cost excludes its common preamble and records `prefix_tool_attempts` separately. Resource costs are tool attempts, not tokens, money, GPU time or latency.
- Input output-length limits, model adapter, grouped data split, human annotations, confidence intervals, training comparisons and external benchmark regression remain next-stage work.

## Integration
Archive the smoke run using coordinator-owned `scripts/record_run.py` with command:
`python3 -m toolloop_fix.run --config configs/smoke.json --output <run_dir>`.
The output directory may already contain recorder config/log/manifest files; only the three harness output names are protected from overwrite.

## ITER-001 review repair — 2026-11-16

Initial results above are retained as history; the following supersedes the initial budget/cap behavior.

### Preserved failing evidence
The coordinator archived newly added regression tests **before** implementation changes as `EXP-20261116-REGRESSION-001` (full suite: 48 tests, 12 failures + 3 errors, exit 1, per coordinator readback). The original harness was not overwritten inside that run. Four boundary issues from `initial_design_review.md` were repaired:

1. Prefix tool attempts and turns now consume the total budget. `tool_attempts` / `turns` are total counts; `prefix_*`, `continuation_*`, and `remaining_*_at_start` are explicit. A prefix greater than either budget raises; exactly exhausted prefixes cannot continue.
2. The environment, not only a policy, enforces initial attempt + at most two timeout retries and at most three polls. `RETRY_LIMIT_EXCEEDED` is public, nonretryable and detected. Equivalent arguments and irrelevant action metadata cannot reset counters. Counters are stored in private `runtime` snapshots separately from business state to avoid treating bookkeeping as task progress.
3. New loop metrics only count triggers at or after the continuation boundary. Inherited flags remain in `prefix_loop_flags`; all flags and `continuation_loop_flags` are retained, including newly invalid repeats crossing the boundary.
4. `unnecessary_clarification` and event count are reported separately and contribute to premature abandonment on completable tasks, even if the task later succeeds. Asking again after a user already supplied the missing identifier is unnecessary. Legitimate first clarification remains excluded.

Primary cost now includes the prefix; `continuation_tool_attempts_per_success` remains available separately. Metrics schema is **2** because count/cost semantics changed. Initial v1 records must not be silently compared without their scope labels.

### Actual diagnostics and freeze
- Post-fix diagnostic `python3 -m unittest discover -s tests -p test_harness.py -v`: **39 tests passed** (38 after the four fixes, then 39 including metadata retry-cap regression).
- Coordinator will archive the final combined suite and smoke output after all agents freeze.
- No external model, dependency, paid call or training was added. The initial 15 fixture values are unchanged; only harness behavior and tests were repaired.
- Runtime-enforced finite retries now supersede the earlier handoff's infinite-retry limitation. Broader semantic-loop detection and formal human audit remain unimplemented.

### Final ITER-001 action-equivalence consistency check
Coordinator identified that ignored top-level tool metadata could otherwise evade repeat detection. Added a regression with changing `trace_label` on identical invalid calls: the pre-fix diagnostic ran 40 harness tests and failed that one new test. `canonical_action` now retains only executor-relevant `type/tool/arguments` for tool actions, while raw trace actions remain unchanged. Final narrow diagnostic rerun: **40 harness tests passed**. These diagnostic executions are not separately archived; the coordinator's final combined run provides the archived verification.

The simulated locator still requires the identifier to have been present in the original request; a request initially missing it uses `ask_user → primary read` after the scripted response. General `ask_user → locator` workflows are not implemented/tested. This nonblocking scenario was left unchanged to keep the review fix surgical; add a dedicated fixture/test before supporting that workflow.
