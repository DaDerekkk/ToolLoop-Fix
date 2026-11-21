# 项目进度

更新时间：2026-11-20（Asia/Shanghai）。**项目主线全部完成（G0–G4 闭环收官）：ITER-008（G2 检测器冻结 + 零泄漏 N=400 训练包）与 ITER-009（Colab NVIDIA L4 3 波次 10 个 LoRA 适配器训练、开发集 `eval24` 选择、36 例零泄漏 G4 封闭测试集单次解封评测、统计检验、846-episode 纯 CPU 重放审计与全量回归）全部完成。总计 `123` 个归档实验、`0` 项完整性错误、`326/326` 单元测试通过；所有自有 `tlfix-*` 云会话已永久释放。**

- **核心交付入口**：
  - 最终中文研究报告：`FINAL_REPORT.md`
  - 正式统计对比报告：`experiments/EXP-20261120-105R1/g3_g4_comparison.{md,json}`
  - 独立审查与纯 CPU 回放审计：`agents/reviews/iteration9_final_review.md`、`experiments/EXP-20261120-REVIEW-009R2/iter9_replay_audit.json`
  - 迭代日志、交接与哈希索引：`iterations/ITER-009_colab_l4_training_and_g4_holdout.md`、`agents/handoffs/ITER009_final_handoff.md`、`data/raw/iteration9_evidence_index.json`
- **ITER-008（G2 冻结与零泄漏训练包，`EXP-20261119-060`、`EXP-20261120-061..063`）**：双盲银标 + 用户委托三盲仲裁（`user_delegated_triple_blind_silver_adjudication_v1`，明确标注 `pure_manual_human_gold_claimed: false`）冻结 `toolloop_fix/loop_detector_v2.py`（100% 精确率、97.94% 真实模型召回、0.00% 合法重试误报）与 `N=400` 训练包 `experiments/EXP-20261120-062/public_colab_training_bundle.json`。
- **ITER-009 G3 训练（Colab L4 / BF16 + `fla-core==0.6.0` Triton）**：3 波次（`COLAB-20261120-009R1`、`010`、`011R1`）训练并离线验真全部 10 个 LoRA 适配器（`EXP-070R1`、`075`、`089` 均 `verified=true`；`B3/B4/B5/P/P_no_retry/P_no_normal` 种子 `101` + `B3/P` 种子 `202/303`；对称 50 步、`2,705,664` 参数、基座哈希严格不变）。
- **ITER-009 G4 封闭测试集核心结果（`G=36`：24 域内未见模板 + 12 零样本 `jobs`；`EXP-078..104`、`EXP-105R1`）**：
  - 未训练基线：`B0` E2E 精确完成 `3/30 (10.0%)` / `loop_v2=8/36 (22.2%)`；`B1` `0/30 (0.0%)`；`B2` `2/30 (6.7%)` / `loop_v2=4/36 (11.1%)`。
  - 对比基线与消融：`B4`（单 Token FTPO）`3/30 (10.0%)` 且解析错误轮率飙升至 `72.0%`；`B5`（无错误历史专家 DPO）`13/30 (43.3%)` 但 `recoverable=0/12 (0.0%)`、`loop_v2=13/36 (36.1%)`；`P_no_retry` `retry=0/6 (0.0%)`；`P_no_normal` `0/30 (0.0%)` 且过早放弃 `22/30 (73.3%)`。
  - **`P`（ToolLoop-Fix 行动 DPO）**：`s101` 达 **`21/30 (70.0%)`** 精确完成（较 `B0` `+60.0pp`）、`loop_v2=1/36 (2.8%)`、Prefix 恢复 `11/12 (91.7%)`；3 种子均值 E2E `61.1% ± 15.4%`、Prefix `80.6% ± 12.7%`（受负偏好梯度影响 `P_s303` 出现 `43.2%` 语法解析错误轮）。
  - **`B3`（失败现场纠错 SFT — 3 种子零方差第一名）**：3 种子全部达 E2E **`29/30 (96.7% ± 0.0%)`** 精确完成、**`30/30 (100.0% ± 0.0%)`** 目标证据、零样本 `jobs` **`9/10 (90.0% ± 0.0%)`**、**`loop_v2=0/36 (0.0%)`**、Prefix 恢复 **`12/12 (100.0% ± 0.0%)`**。
  - **诚实归因结论（提案 §8.2）**：`P < B3`（3 种子 E2E 差值 `-35.6pp [-49.8pp, -21.3pp]`）。在 `0.8B` 规模下，执行验证的失败现场决策点数据是决定性因果因素，而离线负偏好梯度会按 `B4 (72.0%) → P (10.5%–43.2%) → B3 (3.4%–5.5%)` 的单调梯度破坏结构化 XML 语法稳定性。
- **审计与回归**：`EXP-20261120-REVIEW-009R2` 无 ML 依赖纯 CPU 重放全部 32 个 ITER-009 模型实验（`846/846` episodes、`4,029/4,029` 轮生成），**`0` 不一致**、`0` 泄露；`EXP-20261120-106` 通过全仓 `326/326` 单元测试。

---

### 以下为 ITER-007 结项快照（2026-11-19）
**ITER-007：已完成 5 种子固定前缀恢复、贪心/惩罚解码拆解、180 例零泄漏分组开发集、24 例未见模板组端到端 `B0/B1/B2` 对照、G2 盲化人工复核包与纯 CPU 逐轮独立回放审计。未训练，未接触云实例。**

- **解码与多种子固定前缀（`EXP-049..053` + `028..030` = 102 episodes）**：同一 MPS/BF16 与 `explicit` 前缀下，`greedy_pp0`（`049`）与 `greedy_pp2`（`050`）恢复均为 `0/4`（`orders-timeout` 与 `files-timeout` 首步重试均 `0/1`），且纯贪心在 3 个前缀上触发 10–11 轮预算耗尽循环；5 种子官方采样（`s0..s4`）下，`B0` 在 `pfx-orders-timeout` 与 `pfx-files-timeout` 上恢复率均为 `1/5 (20.0%)`，解开了 ITER-004 单种子与 ITER-006 贪心对照的分歧。5 种子汇总恢复新证据：`B0=2/20 (10.0%)`、`B1=3/20 (15.0%)`、`B2=3/20 (15.0%)`；精确有依据结构化恢复：`B0=1/20`、`B1=2/20`、`B2=1/20`（`pfx-jobs-precondition` 与 `pfx-orders-invalid` 全臂 `0/5`）。
- **分组扩展套件与独立未见模板 E2E（`EXP-047/048`、`EXP-054..056` = 72 episodes）**：构建 180 例 / 45 `template_group` 开发集（连同原 15 例共 **195 例**，对齐 G1 规模）与跨域留出切分清单 `grouped_split_manifest_ITER007.json`（`048` 逐字节重放一致）。在 24 个互异未见模板组 `eval24` 上，三臂目标证据率均为 `2/21 (9.5%)`（`0 胜 / 21 平 / 0 负`）；`recoverable` `0/6`、`clarification` `0/6`、`jobs` 域 `poll_job` 调用 `0` 次；`B2` 机械阻断 8 次将执行循环 `5/24 -> 0/24` 但无恢复收益。同快照 `validate_alternative`（`057`）证实前缀 `52/52` 与 `eval24` `42/57`（含首步未探针例 `50/57`）存在纯公开合法推进动作。
- **G2 盲审基础设施与独立审计（`EXP-058R1`、`REVIEW-006`、`059`）**：导出零泄漏语义盲评包（166 唯一项 / 237 次出现）与循环检测盲审包（358 唯一段，含 **212 段真实模型轨迹段**，6 分层每层 `>=35`）及 Fail-Closed 人工核分器。`REVIEW-006` 无 ML 依赖逐轮重放 174 个 episode / 509 轮生成通过，B0/B2 干预前 `118/118` 轮一致；`059` 通过 290 项本地测试。总计 **70 个 run、0 项完整性错误**（保留工程失败 `046`、`058`）。入口：`iterations/ITER-007_multiseed_templates_and_g2_prep.md`、`agents/handoffs/ITER007_coordinator.md`、`agents/reviews/iteration7_independent_review.md`、`data/raw/iteration7_evidence_index.json`。下一步待真实人类完成 G2 盲审签收并获明确训练授权后方进入 G3。

---

### 以下为 ITER-006 结项快照
**ITER-006：L4 真实推理已取回并通过 token 复核；全部新实例均已释放。未训练。**

- 云端：前两次传输失败分开保留；004 在 NVIDIA L4/BF16 得到 Qwen3.5-0.8B 的 32 个输出 token，044 离线复核原始回执，精确提案 `lookup_demo(identifier="DEMO-001")`，只提出未执行。成功这次3分12秒内释放并核验；最终0实例/0 CU每小时。
- 研究：042（合成开发夹具）→ CPU-001（12次真实首步）→ 043（只读复核）。同一中性模板中两工具各0/2重试，4/4正常回答给对ID和值，4/4不可用回答把错误码当记录值。独立 agent 初审，不是人工金标或端到端成绩。
- 045：256 项本地测试通过。总计53 个run、0项完整性错误。记录与下一步：`iterations/ITER-006_l4_verified.md`、`data/raw/iteration6_evidence_index.json`、`agents/handoffs/ITER006_l4_and_timeout.md`。训练/数据划分门禁保持原样。

### 以下为 ITER-005 结项快照
ITER-005 的独立 L4 当时已释放但未取回模型输出。下列结果只对应 ITER-005，不是 ITER-006 的回执。

- 本轮交接：`agents/handoffs/ITER005_colab.md`；分类结果：`iterations/ITER-005_colab_l4_smoke.md`。034 为本地预检，035 为失败回执/资源释放的离线核验，均不是已验证的模型生成。
- 服务端分配数 1→2→1；费率 8.88→10.82→8.88 CU/小时。停止后端点集合恢复原样，从开始至核验 100.07 秒；单实例实际扣费未知。一次推理命令返回 0，但下载 30 秒超时，故 CUDA 生成、token、正确工具提案均未知。
- 旧结果/训练门禁不变。不能用外层命令成功、脚本模拟或传输测试替代 GPU 原始输出。
- 036：离线回传修复，251 项本地测试通过，未上云复测。037 初次分析失败保留；037R1 对同两个旧 TIMEOUT 前缀做六格配对：订单首步重试获 OK 为 6/6，文件首步重试为 0/6，文件最终无新目标证据；不是独立样本或领域因果效应。
- 最后核验 45 个 run，0 项完整性错误；ITER-004 交接清单 7/7 旧文件哈希一致。分类索引：`data/raw/iteration5_colab_index.json`。下次先做配对反例/人工复核；没有持续 GPU 或训练授权。

---

### 以下为 ITER-004 结项快照（2026-11-16）
语义复核、固定前缀真实续跑、比较/回放和延期训练交接已完成。更早进度在 `agents/handoffs/progress_ITER003.md`。

## 本轮实际工作
- 4 agent 分工推进语义、prefix运行器、独立审查/回放、资源规划；所有旧结果保留。
- 旧42项待审输出逐项agent初审，另3个结构化正对照，45条原文来源核对通过。明确B2无依据成功、未执行动作却声称执行、超时误解释、任务措辞歧义分别记录；**不是人工金标，不改旧自动分数**。
- EXP-021归档因两份文件同名失败，保留；修复命名及前置校验后021R1成功。仅归档故障，没有模型重采样。
- EXP-022提取6个真实历史工具prefix（8个历史工具轮）；023的210项联合测试通过；024核验36个同快照/历史/预算的官方tokenizer起点。
- EXP-025–030：原措辞/明确读取契约 × B0/B1/B2，各6个prefix，共36次真实续跑、**62轮新生成**。
- REVIEW-005：36次案例/62轮CPU已有数据回放一致，36个首轮prompt/token/seed与024一致；无新模型执行。回放不是独立语义裁判。
- EXP-031：六格配对比较归档；032：最终240项联合测试通过；033：最终独立设计/结果审查、工程交接与迁移补充归档。当前40个run完整性0errors，最初25份manifest及旧成果保持不变。

## 固定前缀结果
每格4个恢复prefix、1个已取得OK的完成控制、1个不可用停止控制。四恢复中，只有同一个订单TIMEOUT场景取得新证据并精确结构化完成。

| 条件 | 新证据/恢复 | 精确结构化完成/恢复 | 新生成轮 | 实际阻断 |
|---|---:|---:|---:|---:|
| legacy B0 | 1/4 | 1/4 | 9 | 0 |
| legacy B1 | 1/4 | 1/4 | 11 | 0 |
| legacy B2 | 1/4 | 1/4 | 9 | 0 |
| explicit B0 | 1/4 | 1/4 | 9 | 0 |
| explicit B1 | 1/4 | 1/4 | 14 | 0 |
| explicit B2 | 1/4 | 1/4 | 10 | 2 |

**本轮未观察到B1/B2或明确任务契约带来的恢复指标收益**，四恢复逐例0胜/4平/0负。legacy B2没有触发guard；explicit B2阻断2次但语义风险仍在。明确契约不是纯同义改写，不能把其效应算成恢复算法收益。

新生成输入/输出tokens=58,643/3,433，实际续跑工具调用23次。8个独特历史工具轮在6格重复引用为48次prefix exposure，**不是48次新生成**。正常控制的OK证据已在prefix存在，不能计恢复；自然文本正确性仍自动unknown，定性agent审查另列。

重要例子：明确契约下文件TIMEOUT后仍虚构value=0；jobs终止解释声称做过实际未执行的poll/成功读取；停止控制仍有以错误码包装成功的措辞。减少finish_task错误包装或执行循环，不等于可信性提高。仅6个选择过的已知开发prefix、单一种子安排，不是封闭/总体效果证据。

## 分类资料
- 语义审查：`data/processed/semantic_review_ITER004.json`、`docs/research/ITER004_SEMANTIC_REVIEW.md`。
- 固定prefix及来源：`data/dev/fixed_prefix_cases_iter4.jsonl`、EXP-022；来源为013/016/017/018原始run。
- 新原始索引：`data/raw/fixed_prefix_model_runs_ITER004.json`。
- 新36项全量待审队列：`data/processed/fixed_prefix_review_queue_ITER004.json`，不是36个失败或训练标签。
- 独立审查：`agents/reviews/iteration4_prefix_design.md`、`iteration4_results_review.md`、REVIEW-005。
- 对照结果：`experiments/EXP-20261116-031/prefix_comparison.json`、`prefix_comparison.md`；交接文件hash索引见 `data/raw/iteration4_handoff_manifest.json`。
- 固定流程：`docs/protocols/FIXED_PREFIX_RECOVERY.md`；迭代：`iterations/ITER-004_semantics_and_fixed_prefix.md`。
- 当前总交接：`agents/handoffs/coordinator.md`、`agents/NEXT_AGENT_PROMPT.md`；旧版本另存。

## 训练留下来：M6 / 24G 后续计划
- 用户报告的目标机尚未连接/核验；本轮主机实际只读确认M5/16GiB、macOS26.6.2。没有在M6运行或训练。
- `docs/plans/M6_24G_TRAINING_HANDOFF.md`与`configs/future_training_plan.json`已归档021R1；plan_only=true、enabled=false、无执行入口。
- 包含硬件/环境/权重验证、数据门禁、获准后LoRA短程可行性、B3/B4/B5/P公平比较、loss mask/reference、checkpoint恢复与回滚。当前队列不具备训练资格。
- `docs/plans/M6_PORTABILITY_ADDENDUM.md`补充绝对路径/历史cwd/外部manifest迁移缺口；须新路径映射和派生配置，不改旧manifest。**不能直接复制.venv或改旧记录冒充目标机复现。**
- 24G资源数字只是规划算术，不是M6训练峰值/速度实测；训练专属记录契约仍需实现测试，模型backward/PEFT/TRL/MLX兼容仍待验证。

## 阶段门禁与下一步
P0工程/记录有界通过；P1问题确认仍部分完成；P2人工语义/检测器复核、分组划分未完成；**P3训练未开始且本轮禁止**；P4封闭测试未开始。

下一步先：人工复核无依据成功/执行/原因表述，明确真实任务目标；增加独立模板与多seed的非训练开发诊断，分清参数错误、合法重试、前置步骤、自然终止与可恢复能力。不能只凭单token/循环指标启动训练。目标机可用后先做迁移完整性与推理复现；训练须新的明确授权且数据/评测门禁通过。
