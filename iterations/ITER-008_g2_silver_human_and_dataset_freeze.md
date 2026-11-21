# ITER-008：G2 双盲银标与委托仲裁、检测器 v2 冻结及 G3 无泄漏数据集构建

报告日期：2026-11-20（Asia/Shanghai）。状态：**已完成本轮冻结（`g2_passed=true`、`g3_dataset_ready=true`）；严格区分银标委托仲裁与人工手工金标，零伪造数据**。

## 1. 治理记录与真实性边界
1. **双盲独立银标（`EXP-20261119-060`）**：`blind_silver_A`（洗牌种子 `8001`）与 `blind_silver_B`（洗牌种子 `8002`）在完全隔离密封密钥与源码的条件下完成盲标，如实写入 `"reviewer_type": "blind_subagent_silver"`：
   - 终止语义盲包（166 项）：精确一致率 **`95.78% (159/166)`**，Unweighted Cohen's $\kappa = \mathbf{0.9462}$，仅 7 项分歧。
   - 循环检测盲包（358 段，含 212 段真实模型轨迹）：精确一致率 **`100.00% (358/358)`**，Cohen's $\kappa = \mathbf{1.0000}$（双盲零分歧）；另有 18 段跨步非相邻不可重试重复，双盲一致判为 `true`（`v1=false, v2=true`）。
2. **用户委托仲裁与反冒充披露（`EXP-20261120-061`）**：用户在审阅分歧摘要后明确指示“你自己决定”。根据 `AGENTS.md` 不虚报原则，系统使用显式 `--governance-mode user-delegated-silver`（`"reviewer_type": "user_delegated_silver_arbiter"`），在 `g2_frozen_verdict.json` 中如实登记 `dataset_governance_tier = "user_delegated_triple_blind_silver_adjudication_v1"`、`pure_manual_human_gold_claimed = false`、`human_manual_tiebreak_performed = false`、`user_explicitly_delegated_tiebreak = true`（既有纯人工核分器 `scripts/score_g2_human_audits.py` 保持原封不动）。
3. **检测器 v2 正式冻结**：`toolloop_fix/loop_detector_v2.py`（版本 `nonretryable_repeat_without_progress_v2`）在全部 358 段上达到精确率 **`100.00% (162/162)`**、总体召回率 **`98.78% (162/164)`**（在 212 段真实 `model_inference` 上召回率从 `v1` 的 `79.38%` 提升至 **`97.94%`**）、合法超时重试与任务轮询误报率 **`0.00% (0/85)`**，满足全部 G2 门槛。
4. **G3 四臂与消融数据集构建（`EXP-20261120-062`）**：严格限定域内候选池 `dev_InDomain_candidate_pool`（`orders` + `files` 14 个模板组，与 `selection`、跨域 `jobs`、`pilot_cases`、`iter4`/`iter6` 零交叉），经同快照 `Environment` 执行验证与本地 Qwen3.5-0.8B `AutoTokenizer` 分歧位置验证，产出：
   - `p_toolloop_fix_dpo.jsonl`（`N=400`：恢复 180 + 相反上下文合法超时重试保持 80 + 不可用如实停止 60 + 正常调用与精确完成保持 80）
   - `b3_error_sft.jsonl`（同 `N=400` 正例决策点）
   - `b4_single_token_ftpo_style.jsonl`（同 `N=400` 决策点，双工具调用时 `shared_prefix_token_count >= 4`，绝不压低起始 `<tool_call>`）
   - `b5_general_expert_dpo.jsonl`（`N=400` 纯净专家轨迹偏好对，历史 0% 错误码）
   - `p_ablation_no_legal_retry.jsonl`（`N=320`）与 `p_ablation_no_normal_retention.jsonl`（`N=320`）
   - 云端上传包 `public_colab_training_bundle.json`（SHA256 `08d833addafcf94dcd07ff7ad229c82b3616e92cc34b5636d88d6864f6eda637`）通过零隐藏状态、零评估集、零本地路径硬门禁。
5. **工程失败保留说明**：`EXP-20261120-063` 因 `test_iter8_g2_and_dataset.py` 第 547 行一处 `WORKSPACE_ROOT` 未改为 `ARCHIVE_ROOT` 退出 `1`，原样只读保留，修复纳入 `ITER-009` 预检回归一并核验。
