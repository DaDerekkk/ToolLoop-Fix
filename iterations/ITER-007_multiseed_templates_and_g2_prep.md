# ITER-007：多种子前缀恢复、贪心/惩罚解耦、分组扩展开发集与 G2 盲审基础设施

报告日期：2026-11-19（Asia/Shanghai）。状态：**已完成本轮非训练实验、盲包归档与独立回放审查（`bounded accept`）；未训练、未接触云实例、既有 53 个历史 run 原封保留**。

## 1. 研究动机与预登记假设
承接 `agents/handoffs/ITER006_l4_and_timeout.md` 与 `PROGRESS.md` 的阶段结论：
- **ITER-004（`EXP-025..030`）** 在 MPS/BF16 官方非思考采样（`do_sample=true, temp=1.0, top_k=20, presence_penalty=2.0`）单一种子安排（`s0`）下，6 个真实历史前缀中仅 `pfx-orders-timeout` 恢复（`1/4`），`B0/B1/B2` 与 `legacy/explicit` 全部 `0 胜 / 4 平 / 0 负`。
- **ITER-006（`EXP-042` → `CPU-001` → `EXP-043`）** 在中性单工具模板、CPU FP32、贪心（`do_sample=false, presence_penalty=0.0`）单步提案下，`get_order/TIMEOUT` 与 `read_file/TIMEOUT` 均 `0/2` 重试，且 `RESOURCE_UNAVAILABLE` `4/4` 将错误码表述为业务记录值。
- 本轮围绕 **G1 问题确认完善** 与 **G2 盲评/切分基础设施就绪** 预登记四项假设：
  - **H1（解码解耦）**：在同一真实历史前缀 `explicit/B0`（MPS/BF16）下比较纯贪心（`greedy_pp0`）、贪心加惩罚（`greedy_pp2`）与 5 种子官方采样（`sampled_pp2`），分离解码机制与领域/模板混杂。
  - **H2（5 种子配对固定前缀）**：完成 `s1..s4 × B0/B1/B2`（72 次新续跑），连同 `EXP-028..030`（`s0`）构成 `5 seeds × 6 prefixes × 3 arms = 90` 次配对续跑，测定单种子波动与 `B1`/`B2` 配对差异。
  - **H3（未见独立模板泛化诊断）**：构建零泄漏 180 例分组开发集（45 模板组，连同既有 15 例共 195 例），并在平衡抽出的 24 个互异未见模板组（`eval24`）上配对评测 `explicit` 契约 `B0/B1/B2`（72 次端到端运行）。
  - **H4（分组零泄漏与 G2 盲评门禁）**：验证同快照无泄漏公开替代动作（`validate_alternative`），并构建去 `arm`/去真值的人工语义盲评包与覆盖全部 6 分层的循环检测人工复核包及 Fail-Closed 人工核分器。

## 2. 完整运行序列与工程记录（新增 17 个 run，总计 70 个 run，`0 errors`）
- **保留的工程失败记录（按协议只读保留，不覆盖）**：
  - `EXP-20261119-046`（`analysis`, failed）：首次联合测试在 `inputs/` 冻结快照内因测试脚本直接按相对路径查找未拷贝目录（`experiments/`、`data/processed/`）而退出 `1`；无模型调用。修复快照归档根解析后建立 `EXP-20261119-046R1`（289 项测试全过）。
  - `EXP-20261119-058`（`analysis`, failed）：首次汇总生成 G2 盲审包时，新开发集公开业务编号（如 `ORD-CB1-6061`、`INV-CB2-6071`）含中性批次码 `CB1`/`CB2`，触发 `build_g2_audit_packs.py` 两字符子串 `"B1"`/`"B2"` 零泄漏门禁误报；无模型重采样。收窄为词边界匹配（保留独立/下划线分隔 `B0/B1/B2` 拦截）并补回归测试后，`EXP-20261119-058R1` 通过。
- **合成开发集与重放验证**：
  - `EXP-20261119-047`（`synthetic_harness`）：`data/dev/grouped_dev_suite_iter7.jsonl`（180 例 / 45 `template_group`，504 行轨迹/结果）`scripted_reference` 完成率 `144/144`、恢复率 `36/36`、必要重试率 `36/36`、正确终止率 `36/36`、无效循环 `0%`；`scripted_naive` 在超时内成功、非可重试故障产生预期循环。
  - `EXP-20261119-048`（`analysis`）：从 `047` 归档输入逐字节确定性重放，三份产物 SHA256 100% 相等。
- **真实模型推理（MPS/BF16，固定 `Qwen/Qwen3.5-0.8B@2fc06364715b967f1860aea9cf38778875588b17`，共 156 个新 episode）**：
  - `EXP-20261119-049`（`prefix-decoding`, `explicit/B0`, `greedy_pp0`）：6 个真实前缀纯贪心续跑（34 轮新生成）。
  - `EXP-20261119-050`（`prefix-decoding`, `explicit/B0`, `greedy_pp2`）：6 个真实前缀贪心 + `presence_penalty=2.0` 续跑（24 轮新生成）。
  - `EXP-20261119-051` / `052` / `053`（`prefix-multiseed`, `explicit` `B0` / `B1` / `B2`）：各跑 `s1..s4 × 6` 前缀 = 24 次续跑（共 72 次新续跑、153 轮新生成）。
  - `EXP-20261119-054` / `055` / `056`（`explicit-e2e`, `eval24` `B0` / `B1` / `B2`）：24 个独立未见模板组端到端运行（共 72 个 episode、265 轮新生成）。
- **统计分析、G2 盲包与独立回放审查**：
  - `EXP-20261119-057`（`analysis`）：配对比较与同快照 `validate_alternative` 诊断（`iter7_comparison.json` / `iter7_comparison.md`）。
  - `EXP-20261119-058R1`（`analysis`）：生成零泄漏 G2 终止语义盲评包（166 条唯一项，绑定 237 次出现）与分层循环检测盲审包（358 段唯一前缀，其中真实 `model_inference` 达 **212 段**，6 分层每层 `>=35`）。
  - `EXP-20261119-REVIEW-006`（`analysis`, owner `auditor7`）：纯 CPU 无 ML 依赖逐轮重放全部 11 个模型 run（`049..056` + `028..030`，共 **174 个 episode / 509 轮新生成**），`accepted=true`，`B0` vs `B2` 干预前 **118/118 轮生成完全一致**。独立定性/定量审查见 `agents/reviews/iteration7_independent_review.md`。
  - `EXP-20261119-059`（`analysis`）：最终全量本地回归测试 **290 项全部通过**；`validate_records.py --write-index` 校验 **70 个 run，0 errors**。

## 3. 核心实验发现

### 3.1 解码消融：彻底解开 ITER-004 与 ITER-006 的表观冲突（H1）
在同一真实历史前缀与同一 MPS/BF16 设备上：
- `greedy_pp0`（`049`）与 `greedy_pp2`（`050`）在 4 个恢复前缀上新目标证据均为 **`0/4`**，`pfx-orders-timeout` 与 `pfx-files-timeout` 首步重试均为 **`0/1`**（订单例首步直接错调 `finish_task`，文件例首步自然终止）。
- 同时，`greedy_pp0` 在 `pfx-jobs-precondition`（10 轮）、`pfx-orders-invalid`（11 轮）、`pfx-orders-unavailable`（10 轮）上均连续重复 `ask_user(field="identifier")` 直至耗尽 12 次工具预算；`greedy_pp2` 仅在 `pfx-orders-invalid` 上因 `presence_penalty=2.0` 压低重复标记而改调 `stop_task`（11 轮→1 轮），其余两例仍循环 10 轮。
- 而在 5 种子官方采样 `sampled_pp2`（`B0` `s0..s4`）下，**`pfx-orders-timeout` 与 `pfx-files-timeout` 首步重试及获得新证据率完全相同，均为 `1/5 (20.0%, 0.200 ± 0.447)`**。说明 ITER-004 单种子下“订单 6/6 重试、文件 0/6 重试”的差异纯属随机采样在 `s0` 上的单样本波动，而非领域因果或 CPU/MPS 硬件差异。

### 3.2 5 种子固定前缀配对结果（90 个 episode，H2）
| Arm | 恢复新证据 (x/20) | Seed 均值±SD | 精确结构化恢复 (x/20) | 新执行循环 (x/30) | 重复提案/阻断 | 新生成轮 | 新输入/输出 token |
|---|---:|---:|---:|---:|---:|---:|---:|
| `B0` | 2/20 (10.0%) | 0.100 ± 0.137 | 1/20 (5.0%) | 4/30 (13.3%) | 20/0 | 67 | 69,571 / 3,470 |
| `B1` | 3/20 (15.0%) | 0.150 ± 0.137 | 2/20 (10.0%) | 2/30 (6.7%) | 10/0 | 65 | 71,914 / 3,888 |
| `B2` | 3/20 (15.0%) | 0.150 ± 0.137 | 1/20 (5.0%) | 0/30 (0.0%) | 5/5 | 54 | 51,914 / 3,635 |

- `pfx-jobs-precondition` 与 `pfx-orders-invalid` 在三臂 5 种子（各 15 次试验）上恢复率均为 **`0/5`**。
- `B1` 相比 `B0` 在 20 次恢复试验中仅 `1 胜 / 19 平 / 0 负`（聚类差 `0.050 ± 0.100`）。
- `B2` 机械阻断 5 次重复提案使执行循环降为 `0/30`；在 `pfx-files-timeout@s2`（`EXP-053` 第 15–17 行）阻断重复 `ask_user` 后虽促使模型调用 `read_file` 多拿到 1 次 `OK`（使 `new_goal_evidence` 为 `3/20`），但独立审查发现其末轮自然回答在写对值的同时**将真实路径 `/records/report.txt` 篡改为虚构的 `file:///reports/file.txt`**（`agent_initial_observation_not_human_gold`），精确结构化恢复仍与 `B0` 持平（`0 胜 / 20 平 / 0 负`）。

### 3.3 24 个独立未见模板组端到端对照（`eval24`，72 个 episode，H3）
- **整体表现**：可完成案例目标证据率三臂完全一致，均为 **`2/21 (9.5%)`**（`B1`/`B2` vs `B0` 均为 `0 胜 / 21 平 / 0 负`）；精确有依据结构化完成为 `B0=0/21`、`B1=1/21`、`B2=0/21`。
- **具体模型层缺陷可复现确认（G1 核心证据）**：
  - `recoverable`（`stale_id_locate` 及复合故障）：三臂均 **`0/6`**（例如 `INV-CB2-6071` 将无效 `identifier` 误填入 `find_order(target=...)`，收到 `NOT_FOUND` 后陷入 `ask_user(identifier/domain/target)` 三元轮转并凭空编造标识符耗尽预算）。
  - `clarification`（缺失标识符需先 `ask_user`）：三臂均 **`0/6`**。
  - `jobs` 跨域轮询：全部 24 个 `jobs` episode 实际调用 `poll_job` 次数为 **`0` 次**。
  - `transient_2_retries_boundary`（需连续两次合法超时重试）：三臂恢复 `0/2`，且 `B0`/`B1` 均 `2/2 (100%)` 触发无效循环。
  - `B2` 在 `eval24` 中触发 `8` 次阻断，将执行循环从 `5/24 (20.8%)` 压至 `0/24 (0.0%)`，但**未产生任何额外任务完成或恢复收益**。
- **检测器边界新发现**：`EXP-054` 的 `ask_user(identifier -> domain -> target)` 三元轮转未被仅覆盖相邻重复（`AA`）与二元短周期（`ABAB`）的保守原型 `detect_loops` 标出，而被历史集合判定的 `B2 guard_action` 捕获——该现象已作为真实 FN 候选纳入 G2 检测器人工复核。

### 3.4 同快照无泄漏替代动作与 G2 盲审准备（H4）
- **同快照可恢复性验证（`validate_alternative`）**：5 种子固定前缀未恢复的 `52/52 (100%)` 条轨迹，以及 `eval24` 未达目标可完成轨迹中的 `42/57 (73.7%)`（含零调用首步公开探针则 `50/57 = 87.7%`），在故障发生快照均存在严格仅依赖公开参数（`public_arguments_only=true`）的合法恢复或推进动作，证明瓶颈确系模型决策失误而非环境不可解。所有诊断记录保持 `training_eligible=false`。
- **G2 盲审包就绪**：`data/processed/blind_semantic_review_pack_ITER007.json`（166 条唯一项 / `blind_semantic_worksheet_ITER007.md`）与 `data/processed/blind_detector_audit_pack_ITER007.json`（358 段唯一段，含 **212 段真实模型轨迹段**）已通过零泄漏校验，配套核分器 `scripts/score_g2_human_audits.py` 强制拒绝非 `"human"` 标签。

## 4. 结论判定与下一步门禁
- **本轮判定：`bounded accept`**。
  - **可支持**：G1 问题确认（约 195 例分组开发规模、多采样种子、贪心/惩罚拆解、未见模板与跨域诊断、B0/B1/B2 严格配对）已有扎实可复现证据，确认 Qwen3.5-0.8B 存在真实的非协议性恢复缺失与循环倾向，且 B1 提示与 B2 机械守卫均不能解决参数定位、前置轮询与澄清恢复；G2 分组隔离与盲化人工审计工具链已齐备。
  - **不可支持**：尚未获得人类金标语义正确率或人类核验后的检测器误报/漏报率；不能据此直接启动训练或宣称训练方法有效；4 个历史前缀与 24 个开发诊断案例不构成正式封闭测试（G4）泛化结论。
- **下一步（进入 G3 训练前的明确路径）**：
  1. **完成 G2 人工盲审签收**：由人类评审员填写 `data/processed/blind_semantic_review_pack_ITER007.json` 与 `data/processed/blind_detector_audit_pack_ITER007.json`（重点裁决三元轮转 `ABCABC` 是否纳入冻结检测器版本），运行 `scripts/score_g2_human_audits.py` 锁定检测器版本与阈值。
  2. **训练环境与明确训练授权（G3）**：待 G2 人工门禁通过且用户明确授权开启训练（在当前机器或核验后的目标机 M6/24G 上按 `docs/plans/M6_24G_TRAINING_HANDOFF.md` 先过 T0–T4 短程 LoRA 梯度/内存探针），再按公平预算推进 `B3`（纠错 SFT）、`B4`（单 token FTPO 风格对照）、`B5`（一般动作 DPO）与 `P`（失败现场完整行动偏好训练 + 正常/合法重试保持），最后才建立并单次解封 G4 正式封闭测试集。
