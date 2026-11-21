# ITER-007 多 Seed 前缀续跑、解码拆解与未见模板 E2E 流程规范

更新日期：2026-11-19（Asia/Shanghai）。本规范对应独立新模块 `toolloop_fix/iter7_runners.py`、`scripts/compare_iter7_runs.py` 与 `scripts/audit_iter7_runs.py`，**不修改任何既有核心模块、历史 run 或旧分数；不训练**。

## 1. 模块边界与运行入口
`python -m toolloop_fix.iter7_runners <subcommand> --config <path> --output <path>`

所有子命令在不实例化 `TransformersBackend` 时仅依赖 Python 标准库，严格满足 `scripts/validate_records.py` 的 `model_artifact_errors` 契约（输出 `model_provenance.json`、`generations.jsonl`、`trajectories.jsonl`、`case_results.jsonl`、`metrics.json`、`execution_events.jsonl`、`partial_case.json`；异常时写入 `model_failure.json` 并原样抛出；前缀模式另写 `prefix_contexts.jsonl`；拒绝覆盖任何已存在文件）。

### 1.1 `prefix-decoding`（解码机制拆解）
- **用途**：在 6 个真实历史前缀（`data/dev/fixed_prefix_cases_iter4.jsonl`）上同时支持确定性贪心（`sampling.do_sample: false`，如 `greedy_pp0` 对应 `presence_penalty: 0.0, top_k: 0`；`greedy_pp2` 对应 `presence_penalty: 2.0, top_k: 0`）与采样解码（`sampling.do_sample: true`）。
- **门禁**：保留 `enable_thinking: false`、40 位十六进制固定模型 `revision`、非重叠 `case_seeds`、`mode == "fixed_failure_prefix"`、`wording_profile in {"legacy", "explicit"}`、`task_wording_version == "read_report_literal_target_v1"`、`prefix_schema_version == "authentic_tool_prefix_v1"`。
- **源校验复用**：通过 `prepare_iter7_prefix` 直接调用 `prefix_run.prepare_prefix`（不复制或放宽 `_verify_source`、原始解析重放、`harness.py` SHA256 或控制角色门禁）。
- **记录字段**：`case_results.jsonl`、`trajectories.jsonl`、`prefix_contexts.jsonl`、`generations.jsonl` 与 `prefix_run.py` 完全同构；`metrics.json` 与 `model_provenance.json` 额外记录 `decoding_regime`、`runner_subcommand="prefix-decoding"` 与 `iter7_runner_version="iter7_multiseed_and_templates_v1"`。

### 1.2 `prefix-multiseed`（单次加载多 Schedule 前缀续跑）
- **用途**：单次加载模型权重后，跨多个独立种子表（如 `s1..s4` × 6 前缀 = 24 次试验）执行真实历史前缀续跑。
- **配置字段**：使用 `"seed_schedules": [{"schedule_id": "s1", "case_seeds": {...}}, ...]`（不含 `@` 分隔符），要求 `sampling.do_sample: true`。
- **全局不重叠门禁**：所有 `(schedule_id, prefix_id)` 对应的种子区间 `[base_seed, base_seed + turn_budget)` 在**跨全部 schedule 与全部前缀**范围内必须两两不相交。
- **环境隔离与唯一标识**：每个 `(schedule_id, row)` 独立深拷贝源前缀与 `Environment` 快照；每条导出的 `generation`、`event`、`prefix_context`、`case_result`、`trajectory` 均标记：
  - `"case_id": "<prefix_id>@<schedule_id>"`
  - `"trial_id": "<prefix_id>@<schedule_id>"`
  - `"base_prefix_id": "<prefix_id>"`
  - `"schedule_id": "<schedule_id>"`
  同时完整保留 `"original_case_id"` 与源 `prefix_row_sha256`（对未改写的原始前缀行计算）。
- **分层指标**：`metrics.json` 输出总体 `prefix_metrics` 以及 `by_schedule`（逐 schedule 恢复与控制指标）和 `by_prefix`（逐前缀跨 schedule 汇总）。历史前缀 token 未重新生成，`historical_*` 明确标注为多 schedule 暴露计数而非新算力消耗。

### 1.3 `explicit-e2e`（冻结明确读取契约的独立模板 E2E）
- **用途**：在任意开发夹具 JSONL（如 `data/dev/grouped_dev_suite_iter7.jsonl`）上执行 `end_to_end` 的 `B0`/`B1`/`B2` 对照。
- **唯一提示差异**：初始用户消息 `messages[1]["content"]` 固定为 `EXPLICIT_TASK_PREFIX + json.dumps(view["request"], ensure_ascii=False)`（强制 `wording_profile == "explicit"`、`task_wording_version == "read_report_literal_target_v1"`）；`CALIBRATED_SYSTEM_PROMPT`（B1 追加 `prompt_for("B1")`，B0/B2 完全一致）、B2 `guard_action`、`parse_calibrated_output` 与 `score_calibrated_episode` 与 `calibrated_run.py` 完全一致。
- **分层指标**：`metrics.json` 保留完整 `calibrated_metrics(results)`，并新增 `by_category`（`normal`、`recoverable`、`retry_required`、`clarification`、`unrecoverable`）与 `by_domain`（`orders`、`files`、`jobs`）七项指标分解：`tool_goal_attainment`、`exact_grounded_structured_completion`、`has_invalid_loop`、`premature_stop_without_goal_evidence`、`stopped_after_public_unavailable`、`exact_stop_reason_code`、`natural_termination_semantics_unknown`。

## 2. 离线比较脚本（`scripts/compare_iter7_runs.py`）
命令：`python3 scripts/compare_iter7_runs.py --config <path> --output <path>`（证据类别 `analysis`，零模型加载）。

输入配置包含四组固定 `{"path": "...", "manifest_sha256": "..."}` 引用：
- `prefix_s0_runs`：ITER-004 已归档 `explicit` 固定前缀 `s0` 三臂（`EXP-20261116-028/029/030`）。
- `prefix_multiseed_runs`：新增 `s1..s4` 多 seed 三臂（合并 `s0` 共 `5 seeds × 6 prefixes × 3 arms = 90` 个续跑 episode，强制核验跨 `s0..s4` 全部 30 个种子区间不重叠）。
- `prefix_decoding_runs`：`greedy_pp0` 与 `greedy_pp2` 解码消融 run。
- `e2e_runs`：独立未见模板 E2E `B0/B1/B2` 三臂（强制核验数据集 SHA256 与原 `pilot_cases.jsonl` 不同）。

输出 `iter7_comparison.json` 与 `iter7_comparison.md`，涵盖：
1. **解码拆解表**：`greedy_pp0`、`greedy_pp2` 与 `sampled s0..s4`（均值±样本 SD）在 6 个真实前缀上的表现，以及 `pfx-orders-timeout` 与 `pfx-files-timeout` 首步同参重试率对比。
2. **5-Seed 固定前缀配对表**：逐前缀恢复率（`x/5`）、逐 seed 恢复率（`x/4`）、5 seed 均值与样本 SD、`B1 - B0` 与 `B2 - B0` 按 5 seed 配对及按 `n=4` 恢复前缀聚类差值、20 次恢复试验胜/平/负、阻断与新鲜 token/轮数成本。明确标注 `n=4` 恢复前缀（20 次种子试验）仅为开发期诊断，非封闭确认性证明。
3. **独立未见模板 E2E 对照**：总体、按域、按类别、按 `fault_subtype`（优先读取案例字段，缺失时确定性派生）分层表、可完成案例胜/平/负、按 `template_group` 聚类差值，以及 B0 vs B2 干预前逐轮精确生成一致性核验。
4. **同快照无泄漏公开替代动作核验（`validate_alternative`）**：对未见模板 E2E 与 5-seed 前缀中所有 `completable=True` 且 `tool_goal_attainment=False` 的真实模型轨迹，在 `Environment` 中重放至故障点快照，仅用 `public_tool_view` 驱动 `scripted_reference` 并通过 `validate_alternative` 验证单步合法公开恢复（`locally_validated=True` 或多步重试/轮询 `state_progress=True`）。所有记录强制 `training_eligible: false`。

## 3. 独立 CPU 回放审计（`scripts/audit_iter7_runs.py`）
命令：`python3 scripts/audit_iter7_runs.py --config <path> --output <path>`（零 PyTorch/Transformers 导入，输出 `iter7_audit.json`）。
- 支持 `{"run_dirs": [...]}` 或与比较脚本相同的分组键。
- 逐 run 核验 `manifest.json` 状态、`inputs`/`outputs` 全量 SHA256 清单、核心 `toolloop_fix/harness.py` 及运行器源码哈希、外部模型清单哈希与解释器来源。
- 用 `Iter7RecordedBackend` 逐轮核对 `messages`、`tools`、`seed`、`prompt_sha256`、token 计数，并拒绝任何评测标签（`category`/`hidden`/`template_group`/`fault_subtype`/`prefix_role`/`schedule_id` 等）、未公开隐藏答案/标识符、或 `case_id`/`trial_id` 混入 `prompt_text`。
- 逐字比对重放所得 `case_results.jsonl`、`trajectories.jsonl`、`execution_events.jsonl`、`prefix_contexts.jsonl` 与 `metrics.json`，并验证同组 `B0` 与 `B2` 在相同 `(prompt_sha256, seed)` 下的干预前生成完全一致。
