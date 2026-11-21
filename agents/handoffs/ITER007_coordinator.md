# ITER-007 协调者交接：多种子/解码拆解、180例分组套件与 G2 人工盲审基础设施

日期：2026-11-19（Asia/Shanghai）｜Owner：`root`（协同 `runner7`、`dataset7`、`auditpack7`、`auditor7`）。
状态：**全部 70 个实验 run 完整性校验 `0 errors`；本轮新增 156 个真实 MPS/BF16 模型 episode 并经纯 CPU 逐轮独立回放核验通过；未训练、未触碰云实例、旧 53 个 run 只读未改。**

## 1. 本轮完成了什么
1. **解开 ITER-004 与 ITER-006 的重试差异（`EXP-049`、`EXP-050` vs `028/051`）**：
   - 在同一真实历史前缀（`explicit/B0`）与 MPS/BF16 上，纯贪心 `greedy_pp0`（`049`）和贪心加惩罚 `greedy_pp2`（`050`）对 `pfx-orders-timeout` 与 `pfx-files-timeout` 首步重试均为 `0/1`（恢复 `0/4`），且 `greedy_pp0` 在 `jobs-precondition`、`orders-invalid`、`orders-unavailable` 上连续调用 10–11 次 `ask_user` 耗尽预算。
   - 在 5 采样种子（`s0..s4`）下，`B0` 对 `pfx-orders-timeout` 与 `pfx-files-timeout` 的重试恢复率完全一致，均为 **`1/5 (20.0%)`**，证实单种子 `s0` 的表观领域差异属于采样方差。
2. **5 种子固定前缀 `B0/B1/B2` 配对矩阵（`EXP-028..030` + `EXP-051..053` = 90 episodes）**：
   - 恢复新证据率：`B0 = 2/20 (10.0%)`、`B1 = 3/20 (15.0%)`、`B2 = 3/20 (15.0%)`；精确有依据结构化恢复：`B0 = 1/20`、`B1 = 2/20`、`B2 = 1/20`（`B2` vs `B0` 为 `0/20/0`）。
   - `pfx-jobs-precondition` 与 `pfx-orders-invalid` 三臂跨 5 种子均为 `0/5`。`B2` 在 `pfx-files-timeout@s2`（`EXP-053` 第 15–17 行）阻断重复 `ask_user` 后虽多拿到 1 次 `OK`，但末轮自然文本将真实路径篡改为 `file:///reports/file.txt`（agent 初审观察，非人工金标）。
3. **180 例零泄漏分组开发集与 24 例未见模板组 E2E 对照（`EXP-047/048`、`EXP-054..056` = 72 episodes）**：
   - `data/dev/grouped_dev_suite_iter7.jsonl`（180 例 / 45 `template_group`，连同原 15 例共 **195 例**，对齐 G1 ~200 例规模）及 `data/processed/grouped_split_manifest_ITER007.json`（域内候选池 14 组/选择集 16 组 + 跨域 `jobs` 诊断集 8 组/保留集 7 组，零组/实体/目标交叉）。
   - 24 个互异未见模板组 `eval24` 上，三臂可完成任务目标证据率均为 **`2/21 (9.5%)`**（`0/21/0`）；`recoverable` `0/6`、`clarification` `0/6`、`jobs` 全域 `poll_job` 调用 `0` 次；`B2` 阻断 8 次将执行循环 `5/24 -> 0/24`，但无恢复增益。
   - 同快照 `validate_alternative`（`EXP-057`）证实：5 种子前缀未恢复轨迹 `52/52 (100%)` 与 `eval24` 未完成轨迹 `42/57 (73.7%)`（含首步未探针例为 `50/57 = 87.7%`）均存在仅依赖公开信息的有效下一步动作。
4. **G2 盲化人工复核与检测器审计包（`EXP-058R1`）及独立回放审计（`EXP-20261119-REVIEW-006`）**：
   - 盲评语义包 `data/processed/blind_semantic_review_pack_ITER007.json`（及可读工作表 `blind_semantic_worksheet_ITER007.md`）：**166 条唯一盲审项**（绑定 237 次出现）。
   - 盲评循环检测包 `data/processed/blind_detector_audit_pack_ITER007.json`：**358 段唯一前缀**（含 **212 段真实 `model_inference` 轨迹段**，6 分层每层 `>=35`）；密封密钥存于 `data/raw/blind_*_key_ITER007.json`。
   - `EXP-20261119-REVIEW-006`：纯 CPU 逐轮重放 174 个 episode / 509 轮生成全部通过，`B0` vs `B2` 干预前 `118/118` 轮生成一致；`EXP-059` 全量 290 项单元测试通过。保留工程失败原件 `EXP-046` 与 `EXP-058`。

## 2. 接手与后续动作入口
1. 先运行 `python3 scripts/validate_records.py`（应为 `70` runs、`0` errors），阅读 `iterations/ITER-007_multiseed_templates_and_g2_prep.md`、`experiments/EXP-20261119-057/iter7_comparison.md` 与独立审查 `agents/reviews/iteration7_independent_review.md`。
2. **G2 人工盲审完成路径**（见 `docs/protocols/G2_BLIND_AUDIT_PROTOCOL.md`）：
   - 由真实人类标注员填写 `data/processed/blind_detector_audit_pack_ITER007.json` 与 `data/processed/blind_semantic_review_pack_ITER007.json`（填写 `"reviewer_type": "human"` 与真实 `"reviewer_id"`；核分器拒绝任何 agent/模型名称）。
   - 执行 `scripts/score_g2_human_audits.py --mode detector ...` 与 `--mode semantic ...`，并根据 `INV-CB2-6071` 暴露的三元轮转（`ABCABC`）决定是否冻结 `detect_loops` v2 规则。
3. **G3 训练与 G4 封闭测试门禁**：当前全部样本 `training_eligible=false`、正式封闭集未创建/未解封。必须先通过 G2 人工核分门禁并取得用户**明确训练授权**后，方能按 `docs/plans/M6_24G_TRAINING_HANDOFF.md` 推进训练可行性探针与 `B3/B4/B5/P` 对照。
