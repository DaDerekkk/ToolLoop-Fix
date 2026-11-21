# ITER-008 G2 冻结裁决报告（双盲银标 + 用户显式委托银标仲裁（无人工金标冒充））

- **G2 门禁通过 (`g2_passed`)**: `True`
- **冻结检测器版本 (`frozen_detector_version`)**: `nonretryable_repeat_without_progress_v2`
- **数据治理层级 (`dataset_governance_tier`)**: `user_delegated_triple_blind_silver_adjudication_v1`
- **声称 100% 纯手工金标 (`pure_manual_human_gold_claimed`)**: `False`
- **人类手工裁决 (`human_manual_tiebreak_performed`)**: `False` | **仲裁者 ID (`arbiter_id`)**: `blind_silver_arbiter_C` (`approve_detector_v2_freeze=True`)
- **银标双盲 κ**: Semantic κ=`0.9462` (`0.9578 (159/166)`) | Detector κ=`1.0000` (`1.0000 (358/358)`)
- **来源构成**: Semantic 共识 `159` + 仲裁 `7` | Detector 共识 `340` + 仲裁 `18`

## 1. 终止语义按实验臂解盲统计 (`B0` / `B1` / `B2`)

| Label | B0 | B1 | B2 |
|---|---:|---:|---:|
| `grounded_correct_delivery` | 0.1889 (17/90) | 0.1528 (11/72) | 0.1467 (11/75) |
| `truthful_inability_or_timeout_stop` | 0.1778 (16/90) | 0.1389 (10/72) | 0.1467 (11/75) |
| `error_code_as_record_value` | 0.1333 (12/90) | 0.0833 (6/72) | 0.0800 (6/75) |
| `unobserved_execution_claim` | 0.0333 (3/90) | 0.0278 (2/72) | 0.0400 (3/75) |
| `unsupported_success_claim` | 0.1222 (11/90) | 0.0417 (3/72) | 0.1467 (11/75) |
| `unsupported_absence_or_cause_claim` | 0.3000 (27/90) | 0.4167 (30/72) | 0.3467 (26/75) |
| `incomplete_or_ambiguous` | 0.0444 (4/90) | 0.1389 (10/72) | 0.0933 (7/75) |
| **Any Unsupported Factual Claim** | 0.5889 (53/90) | 0.5694 (41/72) | 0.6133 (46/75) |

## 2. 循环检测器 `v1` vs `v2` 解盲对比

| Scope / Detector | Total | TP | FP | FN | TN | Precision | Recall | Legal Retry/Poll FPR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Overall (`358`) — `v1` | 358 | 144 | 0 | 20 | 194 | 1.0000 (144/144) | 0.8780 (144/164) | 0.0000 (0/85) |
| Overall (`358`) — `v2` | 358 | 162 | 0 | 2 | 194 | 1.0000 (162/162) | 0.9878 (162/164) | 0.0000 (0/85) |
| Real `model_inference` (`212`) — `v1` | 212 | 77 | 0 | 20 | 115 | 1.0000 (77/77) | 0.7938 (77/97) | — |
| Real `model_inference` (`212`) — `v2` | 212 | 95 | 0 | 2 | 115 | 1.0000 (95/95) | 0.9794 (95/97) | — |

## 3. `detector_v2` 六层必选分层明细

| Stratum | Total | TP | FP | FN | TN | Precision | Recall | FPR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `flagged_identical_or_equivalent_repeat` | 74 | 74 | 0 | 0 | 0 | 1.0000 (74/74) | 1.0000 (74/74) | N/A (0/0) |
| `flagged_short_cycle_abab` | 35 | 35 | 0 | 0 | 0 | 1.0000 (35/35) | 1.0000 (35/35) | N/A (0/0) |
| `flagged_retry_limit_violation` | 35 | 35 | 0 | 0 | 0 | 1.0000 (35/35) | 1.0000 (35/35) | N/A (0/0) |
| `unflagged_legal_timeout_retry` | 50 | 0 | 0 | 0 | 50 | N/A (0/0) | N/A (0/0) | 0.0000 (0/50) |
| `unflagged_legal_job_polling` | 35 | 0 | 0 | 0 | 35 | N/A (0/0) | N/A (0/0) | 0.0000 (0/35) |
| `unflagged_genuine_progress_or_first_error` | 129 | 18 | 0 | 2 | 109 | 1.0000 (18/18) | 0.9000 (18/20) | 0.0000 (0/109) |

## 4. G2 冻结门禁逐项核验

- `total_segments_gte_200`: `True`
- `real_model_segments_gte_200`: `True`
- `all_six_mandatory_strata_covered`: `True`
- `detector_v2_legal_retry_and_poll_fpr_eq_0`: `True`
- `detector_v2_precision_gte_0_95`: `True`
- `detector_v2_recall_gte_0_90`: `True`
- `semantic_positive_controls_consistent`: `True`
- `approve_detector_v2_freeze`: `True`
