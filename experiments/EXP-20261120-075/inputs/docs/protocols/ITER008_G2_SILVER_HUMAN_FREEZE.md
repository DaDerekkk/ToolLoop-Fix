# ITER-008 G2 双盲银标 + 人类分歧裁决、检测器 v2 冻结与 G3 数据构建协议

日期：2026-11-19（Asia/Shanghai）｜Owner：`g2_freeze_engineer`（`T017`）  
对应门禁：`EVALUATION_SPEC.md` §3–§6、`EXPERIMENT_WORKFLOW.md` §4（G2 冻结 → G3 训练数据就绪）。

---

## 1. 诚实治理原则与反冒充边界

1. **原纯人工核分器 100% 保持未改**：`scripts/score_g2_human_audits.py` 保留给全量人工逐条手填金标包（严格要求 `reviewer_type == "human"`）。
2. **双盲银标 + 人类分歧裁决独立轨道**（`scripts/adjudicate_g2_silver_and_human.py`）：
   - 两个独立盲态 subagent 标注员必须如实填写 `reviewer_type: "blind_subagent_silver"` 与互异的 `reviewer_id`；若任一条目声称 `"human"`、`reviewer_id` 相同或篡改公开载荷 SHA256，比较器立即 Fail-Closed 拒绝。
   - 解盲前提取所有 `A != B` 语义分歧（含正对照异常）以及所有 `A != B` 或 `detect_loops` (v1) `!=` `detect_loops_v2` 的检测段，生成**仍完全盲化实验臂（不含 `B0/B1/B2`、`EXP-`、`stratum` 或隐藏值）**的 `iter8_disagreements_for_human.{json,md}` 供真实人类拍板。
   - 最终合并产物显式标记治理层级 `dataset_governance_tier: "dual_blind_silver_plus_human_disagreement_adjudication_v1"` 与 `pure_manual_human_gold_claimed: false`，逐条区分 `dual_blind_subagent_silver_consensus` 与 `human_tiebreaker_adjudicated`。

---

## 2. 循环检测器 v2 规范（`toolloop_fix/loop_detector_v2.py`）

- 版本常量：`DETECTOR_V2_VERSION = "nonretryable_repeat_without_progress_v2"`。
- 输入边界：仅读取已执行公开工具历史 `history = [{"action": ..., "response": ...}, ...]`，零隐藏状态或任务类别输入。
- 动作规范化：复用 `toolloop_fix.harness.canonical_action`（仅消除首尾空白与 `read_file.identifier` 的 `posixpath.normpath` 等价形式，保留路径大小写区分）。
- 非可重试公开错误集合 `NONRETRYABLE_ERROR_CODES`：
  `INVALID_IDENTIFIER`、`MISSING_IDENTIFIER`、`RESOURCE_UNAVAILABLE`、`NOT_FOUND`、`PRECONDITION`、`INVALID_ARGUMENTS`、`UNKNOWN_TOOL`、`FORMAT_ERROR`、`RETRY_LIMIT_EXCEEDED`、`UNNECESSARY_CLARIFICATION`。
- 公开进展条件：步骤返回 `code in {"OK", "LOCATED", "USER_INFORMATION", "READY"}` 或 `retryable is True`。
- 四类检出标志（每项均记录精确 `index`、`type` 与 `matched_prior_index`）：
  1. `retry_limit_violation`：`code == "RETRY_LIMIT_EXCEEDED"`；
  2. `identical_or_equivalent_repeat`：紧邻前一步 `index - 1` 具有相同规范化动作与相同非可重试错误响应；
  3. `short_cycle_abab`：窗口 `index - 3 .. index` 构成严格四步非可重试错误交替 `A → B → A → B`；
  4. `nonadjacent_or_multistep_repeat_without_progress`（v2 新增）：当某步未命中紧邻重复或 `ABAB`，但其规范化动作重复了更早 `prev_idx < index - 1` 处 `retryable is False` 且 `code in NONRETRYABLE_ERROR_CODES` 的调用，且中间步骤 `history[prev_idx + 1 : index]` **没有任何一步取得公开进展**时触发（捕获 `ABA`、`ABCABC` 三元轮转及多步无进展重调）。
- **零误报保证**：文档允许的 1–2 次 `TIMEOUT` 重试与 1–3 次 `poll_job` 轮询均不在 `NONRETRYABLE_ERROR_CODES` 中，误报率恒为 `0.0`。

---

## 3. G2 双阶段命令与冻结准则

### 阶段一：银标双盲一致性与盲态分歧提取
```bash
.venv/bin/python scripts/adjudicate_g2_silver_and_human.py \
  --mode compare-silver \
  --blank-semantic data/processed/blind_semantic_review_pack_ITER007.json \
  --silver-sem-a data/processed/iter8_silver_semantic_A.json \
  --silver-sem-b data/processed/iter8_silver_semantic_B.json \
  --blank-detector data/processed/blind_detector_audit_pack_ITER007.json \
  --silver-det-a data/processed/iter8_silver_detector_A.json \
  --silver-det-b data/processed/iter8_silver_detector_B.json \
  --semantic-key data/raw/blind_semantic_key_ITER007.json \
  --detector-key data/raw/blind_detector_key_ITER007.json \
  --output <comparison_dir>
```
输出：`iter8_silver_agreement_report.{json,md}`（含精确一致率与未加权 Cohen's $\kappa = \frac{p_o - p_e}{1 - p_e}$）及盲态人工裁决包 `iter8_disagreements_for_human.{json,md}`。

### 阶段二：人类分歧裁决合并、解盲与 G2 门禁判定
```bash
.venv/bin/python scripts/adjudicate_g2_silver_and_human.py \
  --mode finalize-g2 \
  --comparison-dir <comparison_dir> \
  --human-rulings <human_rulings.json> \
  --semantic-key data/raw/blind_semantic_key_ITER007.json \
  --detector-key data/raw/blind_detector_key_ITER007.json \
  --output <final_g2_dir>
```
**G2 冻结通过条件**（全部满足时 `g2_passed: true` 且 `frozen_detector_version: "nonretryable_repeat_without_progress_v2"`）：
- `total_segments >= 200` 且 `real_model_segments >= 200`（当前含 `212` 段真实 `model_inference`）；
- 六个必选分层全覆盖；
- `detector_v2` 合法重试与轮询合并误报率 `legal_retry_and_poll_fpr == 0.0`；
- `detector_v2` 总体 `precision >= 0.95` 且 `recall >= 0.90`；
- 终止语义盲化正对照全部一致为 `grounded_correct_delivery`；
- 人类裁决回执显式确认 `approve_detector_v2_freeze == true`。

---

## 4. G3 公平匹配偏好数据集与云端零泄漏导出（`scripts/build_g3_preference_dataset.py`）

```bash
.venv/bin/python scripts/build_g3_preference_dataset.py \
  --config <g3_build_config.json> \
  --output <g3_dataset_dir>
```

1. **前置门禁**：强制校验 `g2_frozen_verdict.json`（`g2_passed == true`，支持 SHA256 锁定），未通过直接拒绝。
2. **严格数据隔离**：仅从 `dev_InDomain_candidate_pool`（`orders` + `files` 共 `14` 个 `template_group`）及其同模板确定性实体扩展采样；对 `dev_InDomain_selection`、跨域 `jobs`（`diagnostic` + `reserved`）、`pilot_cases.jsonl`、`fixed_prefix_cases_iter4.jsonl` 与 `timeout_crossed_iter6.jsonl` 执行 `template_group`、`identifier`、`target`、`value` **0% 重叠硬校验**。
3. **同快照执行验证与四组配比（`N = 400`）**：统一采用 `B0` 系统提示（`CALIBRATED_SYSTEM_PROMPT`）与 `EXPLICIT_TASK_PREFIX`（无 `B1` 提示或 `B2` guard）：
   - **Group 1（45%，180 例）**：错误反馈后本可避免的失败修复（`INVALID_IDENTIFIER`、`LOCATED`、`MISSING_IDENTIFIER`、`USER_INFORMATION`、`NOT_FOUND` / `UNNECESSARY_CLARIFICATION` 三元轮转恢复）；
   - **Group 2（20%，80 例）**：相反上下文合法 `TIMEOUT` 重试保持（含复合 `LOCATED`/`USER_INFORMATION` 后第 1–2 次超时重试）及 `RETRY_LIMIT_EXCEEDED` 合理停止对照；
   - **Group 3（15%，60 例）**：`RESOURCE_UNAVAILABLE` 如实 `stop_task` vs 把错误码当记录值/虚报未找到；
   - **Group 4（20%，80 例）**：首轮正常调用与 `OK` 后精确 `finish_task` 保持（防篡改路径/标识符及 `OK` 后多余 `ask_user`）。
4. **四训练臂与两项消融对齐**：
   - `p_toolloop_fix_dpo.jsonl`（`P`，400 对完整行动 DPO）；
   - `b3_error_sft.jsonl`（`B3`，完全相同 400 个决策点与 `chosen_text` 的纠错 SFT）；
   - `b4_single_token_ftpo_style.jsonl`（`B4`，完全相同 400 对样本，经本地 `Qwen3.5-0.8B` 官方 tokenizer 在 `enable_thinking=False` 下精确计算首个分歧 completion token `first_divergence_offset`，当正负例均为 `<tool_call>` 时 `shared_prefix_token_count >= 4`，绝不盲目压低 `<tool_call>`）；
   - `b5_general_expert_dpo.jsonl`（`B5`，等量 400 对纯清洁 `scripted_reference` 专家轨迹步骤偏好，工具历史中错误码占比 `0%`）；
   - `p_ablation_no_legal_retry.jsonl`（去掉 Group 2，320 对）与 `p_ablation_no_normal_retention.jsonl`（去掉 Group 4，320 对）。
5. **云端安全包隔离**：导出 `public_colab_training_bundle.json` 前执行 Fail-Closed 子串扫描，确保**零 `hidden` 字典、零评测切分样本、零 `case_id`/`template_group` 元数据、零本地文件系统路径**；溯源快照哈希单独写入禁止上云的 `local_only_provenance_sidecar.json`。
