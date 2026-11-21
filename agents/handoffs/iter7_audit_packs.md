# ITER-007 交接：G2 盲化人工复核与循环检测器审计工具链（`T013b` / `T007b`）

日期：2026-11-19（Asia/Shanghai）
Owner：`auditpack7`
状态：**Phase A 工具、协议与单元测试已完成并通过验证；无模型推理、无网络调用、未修改既有文件、未冒充人工标注、未开启训练。**

## 1. 交付文件清单（严格限于授权的 5 个新文件）

1. [`scripts/build_g2_audit_packs.py`](../../scripts/build_g2_audit_packs.py)：纯标准库确定性 G2 盲审包与密封密钥构造器（CLI：`--config <path> --output <path>`）。
2. [`scripts/score_g2_human_audits.py`](../../scripts/score_g2_human_audits.py)：人工盲审回执完整性校验、Fail-Closed 身份审核、解盲统计与预锁定 G2 门禁评分器（CLI：`--mode semantic|detector --annotated <path> --key <path> [--detector-score <path>] --output <path>`）。
3. [`tests/test_g2_audit_packs.py`](../../tests/test_g2_audit_packs.py)：覆盖真实归档实验只读提取、确定性重放、零泄漏检验、源数据篡改拦截、非人类/agent 冒充拦截、零分母 `null` 处理及 G2 阈值判定的单元测试套件。
4. [`docs/protocols/G2_BLIND_AUDIT_PROTOCOL.md`](../../docs/protocols/G2_BLIND_AUDIT_PROTOCOL.md)：G2 盲审隔离、分层抽样与核分门禁中文规范。
5. [`agents/handoffs/iter7_audit_packs.md`](iter7_audit_packs.md)：本交接记录。

## 2. 核心设计实现与历史归档实测摘要

基于只读核验的 `EXP-20261116-016..018`（校准 E2E）、`EXP-20261116-025..030`（固定前缀续跑）、`EXP-20261119-043`（CPU 单步对照）及 `EXP-20261116-004`（合成基线，`shuffle_seed=20261119`）：

- **源运行完整性前置核验**：逐一验证 `manifest.json`（`status == "completed"`、`evidence_valid == true`）及所有登记输出（含 `generations.jsonl`、`trajectories.jsonl`、`case_results.jsonl`、固定前缀 `prefix_contexts.jsonl`、`EXP-043` 的 `first_step_decisions.json` / `raw_generations.jsonl` / `public_payloads.jsonl`）SHA256；`EXP-043` 严格按 `source_line` 配对并复核行级 `payload_sha256`。
- **终止语义盲包（`BLIND-SEM-0001..0066`）**：
  - 收集全部 93 次终止输出（含 63 次自然终止、9 次非精确原因结构化停止、9 次不受支持的完成包装、3 次输出截断末轮，以及作为盲化正对照混入的 9 次精确有据结构化完成），按公开上下文与原始输出精确去重为 **66 条唯一盲审项**，消除 B0/B1/B2 重复文本带来的标注负担与重数泄漏。
  - 盲包仅暴露 `public_user_request`、`public_tool_names`、`observed_public_tool_history`（仅真实已执行工具调用与响应）及 `terminal_response`；B2 的 runtime guard 通知从盲包剥离并在 `blind_semantic_key.json` 中记录 `guard_notice_occurred`。
- **循环检测器盲包（`BLIND-DET-0001..0240`）**：
  - 共生成 **240 段唯一轨迹前缀**（绑定 517 次源出现），6 个必选分层分布为：`flagged_identical_or_equivalent_repeat=41`、`flagged_short_cycle_abab=35`、`flagged_retry_limit_violation=35`、`unflagged_legal_timeout_retry=35`、`unflagged_legal_job_polling=44`、`unflagged_genuine_progress_or_first_error=50`（每层均远超 ≥15 下限，总量 ≥220）。
  - 100% 优先纳入既有真实 `model_inference` 的全部 **49 段唯一工具前缀**，再从已核验 `synthetic_harness`（30 段）与纯 `Environment` 执行验证的确定性边界生成器 `synthetic_harness_boundary`（161 段，补齐 `ABAB`、`RETRY_LIMIT_EXCEEDED`、等价路径规范化及合法重试/轮询边界）补足，并在密封 key 中显式追踪 `source_evidence_class`。
- **零泄漏与 Fail-Closed 门禁**：
  - 盲包 JSON 与工作表 Markdown 全部通过禁止子串（`EXP-`、`comparison_arm`、`agent_judgment`、`template_group`、`hidden`、`detector_flagged`、`stratum` 等）、独立实验组别 token 边界正则 `(?<![A-Za-z0-9])B[012](?![A-Za-z0-9])`（既严格拦截 `"B0"` / `"arm B1"` / `"explicit_B2"`，又避免将 `grouped_dev_suite_iter7.jsonl` 中含字母前缀的公开业务单号如 `ORD-CB1-6061`、`ORDWAYBILL_CB2_V1` 误判为泄漏）与未披露隐藏 `value` 零泄漏检查。
  - `score_g2_human_audits.py` 强制拦截空白包、公开载荷哈希篡改、非 `"human"` 的 `reviewer_type`、空 `reviewer_id`、任何匹配 agent/模型名称（`agent`、`checker`、`protocol`、`source_audit`、`root`、`llm`、`auto`、`claude`、`gpt`、`gemini`、`qwen`、`auditpack`、`runner` 等）的 `reviewer_id` 以及空/非法标签。

## 3. 实际执行的验证命令与结果

1. 目标单元测试（含 `CB1`/`CB2` 边界回归与全量 `configs/iter7_g2_audit_packs.json` 构造验证）：
   ```bash
   python3 -m unittest tests/test_g2_audit_packs.py -v
   ```
   结果：**8/8 测试全部通过**（退出码 `0`）。全量 ITER-007 配置在临时目录实测产出 **166 条唯一终止语义盲审项**（绑定 237 次出现，`B0=90, B1=72, B2=75`）与 **358 段唯一循环检测器盲审轨迹前缀**（绑定 903 次出现，其中真实 `model_inference` 212 段、`synthetic_harness` 76 段、`synthetic_harness_boundary` 70 段；6 层每层 ≥35 段），零泄漏校验通过。
2. 工作区边界核查：确认未修改 `experiments/EXP-20261119-058` 或任何非授权文件，未调用 `scripts/record_run.py`，未生成伪造人工回执。

## 4. 限制与协调者后续接入说明

- 当前所有盲审包 `human_review_completed=false`、`training_eligible=false`；测试套件中的模拟人类标注仅存在于临时内存/临时目录中用于验证核分器逻辑，绝不代表已完成真实人类复核。
- 待协调者（`root`）在 Phase B 完成 ITER-007 新一轮非训练推理后，可通过 `scripts/record_run.py --kind analysis` 调用 `scripts/build_g2_audit_packs.py` 将新 `model_inference` 与分组合成基线一并纳入正式盲包归档，交由真实人类独立标注后再运行 `scripts/score_g2_human_audits.py` 判定 G2 门禁。
