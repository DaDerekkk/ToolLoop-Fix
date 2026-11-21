# ITER-007 Phase C 独立审计交接（`auditor7`）

- **日期**：2026-11-19（Asia/Shanghai）
- **Owner**：`auditor7`
- **结论（Verdict）**：`bounded accept`（工程、记录与 G1 多维诊断闭环通过；G2 人工盲审金标尚未完成，`training_eligible=false`，G3 训练与 G4 封闭测试保持 `hold`）

## 1. 新建文件与严格边界确认
本次仅创建授权范围内的 3 项交付物：
1. `experiments/EXP-20261119-REVIEW-006/`（仅通过 `.venv/bin/python scripts/record_run.py` 创建）：
   - `manifest.json` SHA256：`ef76f34dde308eef06dd3c9ab8f00977acb5ce24dc542754ff82264b9927acd4`
   - `iter7_audit.json` SHA256：`55b88f53b5a6b9c9c04e21340e341201570b8c48d5fb143d235c701a6b528d42`
2. `agents/reviews/iteration7_independent_review.md`：ITER-007 独立中文审查报告（含完整 SHA256、分母核对、1-based `generations.jsonl` 行号轨迹核查及剩余 G2/G3 门禁）。
3. `agents/handoffs/iter7_audit.md`：本交接文件。

**边界核实**：未编辑任何源码、配置、数据集、`PROGRESS.md`、`agents/TASK_BOARD.md`、`iterations/` 或任何既有/失败实验目录（`EXP-20261119-046` 与 `EXP-20261119-058` 保持只读原貌）；未加载 PyTorch/Transformers 权重；未触碰网络或云端资源；所有自然语言终止阅读均显式标注 `agent_initial_observation_not_human_gold`。

## 2. 实际执行命令与结果摘要

1. **官方纯 CPU 已记录生成重放审计（`EXP-20261119-REVIEW-006`）**：
   ```bash
   .venv/bin/python scripts/record_run.py \
     --id EXP-20261119-REVIEW-006 \
     --kind analysis \
     --config configs/iter7_audit_replay.json \
     --hypothesis 'ITER-007 全部8组新模型运行（049..056，156个episode）与ITER-004 s0锚点（028..030）可在无PyTorch/Transformers下精确CPU重放且零标签泄漏' \
     --acceptance 'iter7_audit.json accepted=true；全部174个episode逐轮消息/工具/种子/计分重放一致；B0与B2干预前完全一致' \
     --owner auditor7 \
     --parent EXP-20261119-057 \
     -- "$PWD/.venv/bin/python" scripts/audit_iter7_runs.py --config '{config}' --output '{run_dir}'
   ```
   - 退出码：`0`（`status=completed, evidence_valid=true`）。
   - `iter7_audit.json` 输出：`accepted=true`、`ml_libraries_loaded=false`、`run_count=11`、`episodes_replayed=174`（`049..056` 共 `156` 个新 episode + `028..030` 共 `18` 个 `s0` 锚点 episode）、`fresh_generations_replayed=509`、`b0_b2_pre_intervention_checks` 共 `118` 轮干预前生成 100% 一致（`028/030`: `8` 轮；`051/053`: `40` 轮；`054/056`: `70` 轮）。

2. **全局实验台账只读校验**：
   ```bash
   python3 scripts/validate_records.py
   ```
   - 退出码：`0`，输出 `{"runs_checked": 69, "errors": []}`（含保留的失败记录 `EXP-20261119-046` 与 `EXP-20261119-058`）。

3. **标准库只读抽查（`python3 -c`）**：
   - 核实 `EXP-20261119-057/iter7_comparison.json`（`88abbbe43aa8a06b34f6ea87f4bcd5eef91737c69c3d37496e4f0d3c865ac246`）中 `n=4` 恢复前缀（`20` 次种子试验/臂）与 `pfx-orders-ok`（`15/15` 均 `goal_at_prefix=true, new_goal_evidence=false`）、`pfx-orders-unavailable`（停止控制）严格分离；`eval24` 严格分离 `21` 可完成与 `3` 不可恢复案例，`by_domain`、`by_category`、`by_fault_subtype` 分母及零分母 `null` 完全合规。
   - 逐行核查 `EXP-049..056` 的 `generations.jsonl`（详见 `agents/reviews/iteration7_independent_review.md` §3）：确认贪心首轮 `finish_task` 错包导致不重试、`EXP-053` `pfx-files-timeout@s2` 第 `17` 行虽在第 `16` 行获 `OK` 但自然文本将标识符虚构为 `file:///reports/file.txt`（`agent_initial_observation_not_human_gold`）、`eval24` 全部 24 个 `jobs` episode 实际调用 `poll_job` 为 `0` 次、`INV-CB2-6071` 上三参数 `ask_user` 轮转与 `ORDWAYBILL_CB2_V1` 改写、以及 `EXP-056` 的 `8` 次 B2 阻断使循环 `5/24 -> 0/24` 而目标证据率维持 `2/21`。
   - 核实 `EXP-20261119-058R1` 全部 6 份产物与 `data/processed/*_ITER007.*`、`data/raw/*_key_ITER007.json` SHA256 逐字节一致，盲包零实验臂/标签/未披露真值泄漏，检测器盲包共 `358` 段（其中真实 `model_inference` 为 `212` 段），且 `human_review_completed=false`、`training_eligible=false`。

## 3. 限制与下一步门禁
- 同版本解析器/护栏/环境重放证明记录一致性与零提示词泄漏，不替代独立人类语义真值裁决。
- G2 人类盲审（`166` 项终止语义 + `358` 段循环检测）完成并通过 `scripts/score_g2_human_audits.py` 门禁、冻结最终检测器规则（含评估多参数 `ask_user` 三步轮转边界）与封闭集、以及取得明确训练授权前，严禁启动模型训练。
