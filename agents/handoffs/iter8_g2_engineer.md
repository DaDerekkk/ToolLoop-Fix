# ITER-008 工程交接：`g2_freeze_engineer`（`T017`）

日期：2026-11-19（Asia/Shanghai）｜Owner：`g2_freeze_engineer`（`T017`）  
状态：**全部 6 个新文件已完成并通过 `.venv/bin/python`（真实本地 Qwen3.5-0.8B `AutoTokenizer`）与纯标准库 `python3` 双重测试验证（`10/10 OK`）；既有文件 `0` 修改。**

---

## 1. 交付文件清单（严格限定在授权的 6 个新建文件）

1. `toolloop_fix/loop_detector_v2.py`
   - 导出 `DETECTOR_V2_VERSION = "nonretryable_repeat_without_progress_v2"`、`detect_loops_v2(history)` 与 `has_invalid_loop_v2(history)`（纯标准库，仅读取公开已执行工具历史）。
   - 完整保留 v1 的 `retry_limit_violation`、紧邻 `identical_or_equivalent_repeat`、`short_cycle_abab`，并新增 `nonadjacent_or_multistep_repeat_without_progress`（检测 `prev_idx < index - 1` 非可重试错误在中间无任何 `OK/LOCATED/USER_INFORMATION/READY` 或 `retryable=True` 进展下的重复调用，精确记录 `matched_prior_index`）。
   - 在 `EXP-20261119-058R1` 的 `358` 段检测盲审包上，`detect_loops_v2` 对合法 `TIMEOUT` 重试（1–2 次）与合法 `poll_job`（1–3 次）保持 **0% 误报**，并额外检出 v1 漏检的 **18 段真实 `model_inference` 非相邻/三元轮转循环段**（含 `INV-CB2-6071` 的 `ask_user` 三元轮转）。

2. `scripts/adjudicate_g2_silver_and_human.py`
   - `--mode compare-silver`：逐条核对空白包与密封密钥 `public_payload_sha256`；对任何声称 `reviewer_type == "human"` 或 A/B 共用 `reviewer_id` 的银标文件执行 **Fail-Closed 反冒充拒绝**（强制 `reviewer_type == "blind_subagent_silver"`）；精确计算 166 条语义盲审（7×7 混淆矩阵）与 358 段检测盲审（2×2 表）的一致率及未加权 Cohen's $\kappa = \frac{p_o - p_e}{1 - p_e}$；导出完全保持实验臂盲态的 `iter8_disagreements_for_human.{json,md}`（含全部语义分歧、正对照异常、检测分歧及全部 18 段 `v1 != v2` 规则升级候选段）。
   - `--mode finalize-g2`：核验 `reviewer_type == "human_tiebreaker"`、非 agent 个人 `reviewer_id` 及所有待裁决项 100% 覆盖；合并银标共识（`dual_blind_subagent_silver_consensus`）与人类裁决（`human_tiebreaker_adjudicated`），固定标记 `dataset_governance_tier: "dual_blind_silver_plus_human_disagreement_adjudication_v1"` 与 `pure_manual_human_gold_claimed: false`；按 `B0/B1/B2` 解盲终止语义错误率，并同时汇报 `detector_v1` 与 `detector_v2` 在总体（`358` 段）、六层必选分层及真实 `model_inference`（`212` 段）上的混淆矩阵与 G2 门禁判定 `g2_frozen_verdict.{json,md}`。

3. `scripts/build_g3_preference_dataset.py`
   - 强制校验已通过的 `g2_frozen_verdict.json`（`g2_passed == True` 且 `frozen_detector_version == DETECTOR_V2_VERSION`）。
   - **严格数据隔离**：仅从 `dev_InDomain_candidate_pool`（`orders` + `files` 共 `14` 个 `template_group`）及其同模板确定性实体扩展构造样本，对 `dev_InDomain_selection`、跨域 `jobs`（`diagnostic` + `reserved`）、`pilot_cases.jsonl`、`fixed_prefix_cases_iter4.jsonl`、`timeout_crossed_iter6.jsonl` 执行 `template_group`、`identifier`、`target`、`value` **0% 交叉硬校验**。
   - **100% 同快照 `Environment` 执行验证**：采用 `B0` 提示（`CALIBRATED_SYSTEM_PROMPT` + `EXPLICIT_TASK_PREFIX`，无 `B1`/`B2` guard），生成 `N = 400` 个平衡决策点：
     - Group 1 错误反馈后恢复：`180` 对（`45.0%`）
     - Group 2 相反上下文合法 `TIMEOUT` 重试保持与超限停止对照：`80` 对（`20.0%`）
     - Group 3 `RESOURCE_UNAVAILABLE` 如实停止 vs 错误码当记录值：`60` 对（`15.0%`）
     - Group 4 正常首轮调用与 `OK` 后精确 `finish_task` 保持：`80` 对（`20.0%`）
   - 导出公平匹配的四训练臂与两项消融：`p_toolloop_fix_dpo.jsonl`（400）、`b3_error_sft.jsonl`（400）、`b4_single_token_ftpo_style.jsonl`（400，经本地 `Qwen3.5-0.8B` 官方 tokenizer 在 `enable_thinking=False` 下校验首个分歧 completion token `first_divergence_offset`，双 `<tool_call>` 样本 `shared_prefix_token_count >= 4`，绝不压低 `<tool_call>`）、`b5_general_expert_dpo.jsonl`（400，纯清洁专家轨迹偏好，历史错误码占比 `0%`）、`p_ablation_no_legal_retry.jsonl`（320）、`p_ablation_no_normal_retention.jsonl`（320）。
   - 导出经过硬性零泄漏校验的云端安全包 `public_colab_training_bundle.json`（零 `hidden`、零评测切分泄露、零 `case_id`/`template_group`、零本地路径），并将溯源哈希单独存入禁止上云的 `local_only_provenance_sidecar.json`。

4. `tests/test_iter8_g2_and_dataset.py`（10 项端到端与反例单元测试）
5. `docs/protocols/ITER008_G2_SILVER_HUMAN_FREEZE.md`
6. `agents/handoffs/iter8_g2_engineer.md`

---

## 2. 实际执行的验证命令与结果

1. `.venv/bin/python -m unittest tests/test_iter8_g2_and_dataset.py -v`  
   → `Ran 10 tests in 8.252s — OK`（加载本地 `models/Qwen3.5-0.8B/2fc06364715b967f1860aea9cf38778875588b17` 真实 `AutoTokenizer` 验证全部 400 + 400 条样本的 chat template 与精确分歧 token 位置）。
2. `python3 -m unittest tests/test_iter8_g2_and_dataset.py -v`  
   → `Ran 10 tests in 9.259s — OK`（纯标准库 fallback 路径同样全部通过）。
3. 既有核心模块（`toolloop_fix/harness.py`、`scripts/score_g2_human_audits.py` 等）保持 100% 未改动；未调用 `record_run.py`、未访问网络或云资源。

---

## 3. 限制与下一步（交 `root` / `T018`）

1. 待 `T016a`（`blind_silver_A`）与 `T016b`（`blind_silver_B`）交付银标 JSON 后，由协调者通过 `record_run.py` 运行 `adjudicate_g2_silver_and_human.py --mode compare-silver` 生成双盲 $\kappa$ 报告与 `iter8_disagreements_for_human.md` 呈交用户对话内盲态裁决。
2. 用户提交真实人类 `human-rulings` 并确认 `approve_detector_v2_freeze: true` 后，运行 `--mode finalize-g2` 产出 `g2_frozen_verdict.json`，再运行 `build_g3_preference_dataset.py` 构建 `public_colab_training_bundle.json` 进入获批的 G3 Colab L4 训练阶段。
