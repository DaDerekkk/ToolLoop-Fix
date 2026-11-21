# ITER-003 结果定性审查与配对前缀核验

- 日期：2026-11-16（Asia/Shanghai）；reviewer：`/root/source_audit`。
- 输入：EXP-016/B0、EXP-017/B1、EXP-018/B2的归档manifest、provenance、generations、execution_events、case_results与metrics；EXP-020 comparison。
- 本agent仅只读核验和写审查，无新模型生成、测试、依赖、代码/配置修改。全量轨迹/指标独立重放由protocol agent负责，另见REVIEW-004。
- **结论：校准/执行链有界接受；本次没有观察到B1/B2提高两个冻结目标指标。B2阻断了重复执行，但不能称任务可靠性恢复。NO TRAINING。**

## 1. 控制条件及有限结果

三arm均15个已知手写开发案例、一套显式case seed；12个可完成任务和3个不可完成任务。三个manifest均completed/exit0/evidence_valid=true；provenance均MPS:0/BF16、四项loading diagnostics为空。B0/B2系统提示hash相同，B1只有通用提示增量；共同源码、采样、数据及配置对照由EXP-020检查通过。

| 冻结指标 | B0 / EXP-016 | B1 / EXP-017 | B2 / EXP-018 |
|---|---:|---:|---:|
| 实际工具取得目标证据 | 3/12 | 3/12 | 3/12 |
| 精确、有证据的结构化完成 | 1/12 | 1/12 | 1/12 |
| 正常任务取得目标证据 | 2/3 | 2/3 | 2/3 |
| 必要重试任务取得目标证据 | 0/3 | 0/3 | 0/3 |
| 实际工具执行被原型检测器标记循环 | 1/15 | 1/15 | 0/15 |
| 模型提出已知无新观察的重复提议数 | 2 | 2 | 2 |
| guard实际阻断提议数 | 0 | 0 | 2 |
| 自然语言终止，语义未自动评分 | 11/15 | 10/15 | 12/15 |
| 生成轮数 / 实际工具分发数 | 41 / 26 | 47 / 31 | 46 / 27 |
| 解析错误轮数 | 0/41 | 3/47 | 2/46 |

B1及B2对B0，在“工具目标证据”和“精确结构化完成”上均为0胜/12平/0负；不只是聚合数相同，逐case的这两个布尔结果也相同。该探索性单seed结果**不证明方法等效或永远无效**，但不支持本轮收益主张。

工具目标证据不是完整回答成功：B0的jobs-normal先得到正确OK，之后又编造ID并以无法访问的文本结束，仍保有tool_goal_attainment=true。自然文本unknown也不是错误率或正确率；不能将3/12改称总体任务成功率，亦不能把其余文本统一判为失败语义。

## 2. 最高优先级定性问题：B2阻断后出现无证据的成功措辞

定位：
- `experiments/EXP-20261116-018/generations.jsonl` **第12行**：orders-unrecoverable-001，turn3，重复ask_user(identifier)被guard阻断。
- 同文件**第13行**：同例turn4自然语言终止。
- `experiments/EXP-20261116-018/execution_events.jsonl` **第12–13行**：阻断与内部terminal生命周期。
- 同run `case_results.jsonl` **第5行**：tool_goal_attainment=false、natural_termination_semantics_unknown=true。

真实执行历史只有get_order→RESOURCE_UNAVAILABLE及ask_user→UNNECESSARY_CLARIFICATION。turn3 response=null、executed=false、actual_tool_dispatch=false，完整snapshot前后hash相同，没有新工具结果，也没有OK证据。turn4原文明确写：

> The task has been completed successfully with a response value and identifier.

这是**审查者识别出的无证据成功措辞样本**，不是已完成的人类语义标注或总体虚报率估计。原自动unknown评分保持不变；它应进入待人工语义复核队列，而不是偷偷增加/修改旧false_success分子。

该例说明“实际重复不再执行”与“最终回答可信”是两件事。guard提示本身明确说未执行，没有给答案或成功反馈；不能把后续措辞归咎于工具实际返回成功，也不能据一次样本证明所有guard都会导致此问题。

## 3. B0/B2干预前原始token一致性

为排查同seed MPS潜在数值漂移，本agent读取两个run的逐例生成，并比较每个共同轮次的：seed、prompt_text、prompt_token_ids、generated_token_ids、raw_text。对有阻断案例比较至首次阻断提议**包含该提议**（提议生成时尚未收到guard反馈）；对没有阻断的案例比较完整轨迹。

- 合计**39个配对轮次全部五字段相等，未观察到干预前差异**。
- 13个未阻断案例：完整31轮相同。
- orders-unrecoverable-001：turn1–3，两个run的generations第10–12行相同；首次实际干预在turn3生成完成之后。
- jobs-recoverable-001：turn1–5，两个run的generations第28–32行相同；首次实际干预在turn5生成完成之后。

这是本批归档输入/输出的比对，不是新的推理实验，也不是后端普遍确定性的证明。它支持把这两条已观察分支的后续差别与guard干预后的不同历史联系起来；**不支持因果“修复有效”、泛化收益或对其他seed的保证**。即使未来出现干预前差异，也应诚实保留，而不是挑seed或重跑到一致。

## 4. B1：目标指标没有配对收益，失败形式改变

- orders-unrecoverable的循环标记从B0的1变0；jobs-normal则从0变1，聚合仍1/15。jobs-normal在取得正确结果后仍生成JOB-101.daily.report并重复调用；提醒未消除该例成功后的多余行动。
- orders-retry_required明确看到TIMEOUT/retryable=true，却转向ask_user并最终停止；files-retry_required也把TIMEOUT解释成可能不存在。故0/3必要重试证据不能说已保持或改善合理重试。
- B1的两个预算失败来自256输出token截断（jobs-retry_required t6、jobs-clarification t5），不是12次工具调用上限。第三个解析错误来自files-retry_required t2为stop_task增加未定义的compliance_check参数。不能把这三轮统一称普通自然语言被拒绝。
- orders-normal先取得shipped后，却认为“latest order”需要额外排序/列表工具，选择停止。这也暴露手写请求中目标名称与预设读取目标之间可能存在歧义；不能只据此声称模型没有读取能力。后续若澄清任务措辞，必须新版本、所有arm一致，而不是逐例加入正确行动提示。

## 5. B2：干预执行正确，不等于恢复改善

两次阻断均为重复ask_user，分别在orders-unrecoverable t3与jobs-recoverable t5。原始提议保留、未调用工具、未伪造响应、消耗原预算；这验证了已登记guard路径，而非模型学会不重复：三个arm的重复提议数仍均为2。

jobs-recoverable在阻断后又出现两个不完整XML、未知end_task调用，最终自然语言放弃；B2此例11轮，相对B0的6轮更长，未取得目标证据。全组B2实际工具分发27次，B0为26次；不能只用“少执行两个被阻断提议”声称总工具成本下降。

读取generate合计耗时B0约234.184s、B1约352.480s、B2约179.678s，但三组输出长度/路径不同，顺序执行、机器负载及热状态未控制。不能把这些时长当纯decode吞吐或因果速度提升。token与实际调用成本按归档保留即可。

## 6. 研究结论与后续边界

1. 接受：旧103行诊断不改分、新协议unknown语义分层、真实三arm执行、显式seed/共同配置、blocked与executed记录分离。
2. 不接受：总体任务成功率3/12、B2“修好了循环”、B1泛化有效/无效、自然语义已全判、AntiDoom/训练路线已验证。旧013因协议不同不是当前B0控制。
3. 下一步优先人工复核无证据成功措辞、正确取得证据却否认完成、合理澄清意图与封装要求；明确公共任务语义，保持原raw和自动unknown标签。agent审查不能冒充人工标注。
4. 当前三组全部E2E，未做固定失败前缀续跑。原013的jobs-retry_required/PRECONDITION后重复get_result仍可作为另行登记、公开历史一致的条件恢复诊断；当前E2E未必到达同一失败点，不能代替该实验。
5. 无模型训练、teacher接管、LLM judge或新模型发布。不要因为简单基线本轮没有指标提升就推论“因此必须训练”。

## 实际只读核验方法

使用标准库JSON逐行读取归档；按case_id/turn对齐B0/B2，首次guard_enforced前（含该行）逐字段相等比较；未阻断则检查全部轮次和长度。读取manifest/provenance、对照EXP-020配对摘要，并按case/turn定位原始生成与事件。没有重跑模型、修改归档或对unknown自动补标签。
