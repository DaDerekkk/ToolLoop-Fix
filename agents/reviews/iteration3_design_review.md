# ITER-003 设计独立审查

- Reviewer：`/root/source_audit`；日期：2026-11-16 Asia/Shanghai。
- 状态：设计/实现/校准门禁accept（有界开发运行范围）；真实arm结果待独立解释。无代码修改、依赖或模型运行。
- 范围：协议校准→B1/B2，明确NO TRAINING。完整论证见 `docs/research/PROTOCOL_CALIBRATION_AUDIT.md`。

## 设计结论

**conditional accept**：先冻结保守确定性的工具/终止语义，再在同一底座、显式case seeds、相同预算上比较B0/B1/B2。当前不能给实施验收或效果结论。

必须满足：
- 普通EOS文本与 malformed/truncated XML 分开；文本返回作为语义unknown，不修复为工具动作，不插入必然延长对话的格式重试。
- grounded completion、实际停止、reason精确合规、unknown语义分列；unsupported structured finish不冒充已验证的虚报成功。
- B2只用公开历史，无oracle/teacher/环境试执行；blocked proposal与真实执行事件分列，业务状态不变且预算消耗可核对。
- B1与B2各自相对同一calibrated B0；B2不暗含B1。原013保留，任何重解析都是新analysis而非原成绩替换。
- 案例重排/子集不改变seed；三arm共享显式映射及每轮规则。
- 不以循环下降单独判改善，同时看grounded completion、合法retry、unknown terminal和proposal/执行成本。

## 待实现验收清单

1. parser负例：空文本、工具标记残片、多个调用、注入角色标记、截断；正常普通文本不落入FORMAT_ERROR。
2. terminal评测：成功证据匹配；错误字段；无证据的finish；失败说明finish；reason解释文本；纯文本unknown。
3. guard：非重试错误后相同提议阻断；新信息后不沿用过期判断；TIMEOUT/RUNNING合法重试放行；不得补正确动作。
4. record：每轮raw/parse/proposal/guard/execution链可追踪；blocked不存在真实工具结果；预算和成本双口径不混淆。
5. seeds：case顺序变化和子集选择保持原seed；缺失/重复seed配置按冻结规则明确处理。
6. 源码与测试冻结后方可跑真实arm；工程通过与研究结论分别报告。

## 未批准/未实现的扩展

LLM judge、教师接管、隐藏状态驱动的修复、自动补ID、用字符串包含答案判成功、事后放松旧评分、无日志重采样、训练，均不在本设计验收范围。

## 实施中第一次源码审查：baselines.py

已读新baselines.py及tests/test_baselines.py，未执行测试。B0/B2提示相同、B1通用提醒、guard不替换行动且接口只读公开信息，这些设计方向符合约束。calibrated_protocol/calibrated_run当时尚未落盘，不能提前验收。

**R3-01（待修正/复核）：新信息只以response payload判定，丢失行动身份。**

当前known_information集合仅包含response JSON。A(id=A)失败INVALID_IDENTIFIER后，首次B(id=B)返回相同payload，会被误作没有新增观察，下一次A就被阻断；这与注释所承诺“首次新B结果算新信息，第四提议才可判ABAB”不符。现有ABAB测试让A/B返回不同代码，未覆盖该边界。

建议新增两个确定性回归：同码不同动作的首次B结果；曾出现LOCATED、随后A失败、再次成功LOCATED后再提A。新观察至少绑定canonical action+response；真实成功/信息获得响应（LOCATED/USER_INFORMATION/OK/READY）保守重置guard，即便payload先前出现过。guard反馈不计作环境新信息。该建议已发root/simulator，未改代码。

## 第二次源码审查：calibrated_protocol.py

已读新协议及其18项测试的源码；测试通过情况由owner报告，本agent没有重跑。静态检查未发现阻塞性语义问题：普通文本保留raw/legacy_parse并明确用内部生命周期stop标记结束；不伪称其含义是成功、拒绝或澄清；malformed工具标记与截断仍被拒绝；实际公开OK和snapshot证据须同时匹配才能记录tool_goal_attainment，blocked decision本身不能创造证据。

score明确分离tool_goal_attainment、exact_grounded_structured_completion、unsupported_finish_wrapper、公开不可用后的停止、精确原因码和unknown，不输出宽泛success。预算耗尽后仍可报告“曾取得证据”，但不得把这个字段当任务完成率。

**待runner核验：** 自然文本映射到stop是内部生命周期实现，事件必须标出不是模型生成的stop_task、不是actual_tool_dispatch，也不是外部工具返回STOP_RECORDED。若底层环境内部记录了STOP_RECORDED，输出必须保留其internal/lifecycle身份，不能合入真实工具次数。calibrated_run在本次阅读时尚未落盘，跨层事件/预算/seed仍待审。

## R3-01 跟进：guard v2源码关闭

回读 `GUARD_VERSION=nonretryable_no_new_observation_v2`：观察身份已改为canonical action + response；LOCATED/USER_INFORMATION/OK/READY成功信息及明确retryable=true响应均保守重置。新增测试覆盖同码不同动作的A/B和先见过又返回相同payload的成功lookup。B0/B2 prompt仍相同、guard没有动作替换或环境执行。

R3-01在源码层面关闭；owner报告新增回归修复前5项断言失败、修复后20tests通过，本agent未执行，最终测试归档仍以root冻结run为准。实现比本文最窄“仅最近一次行动”建议多覆盖了公开无新增观察的ABAB，属于已明确审查的设计扩展，不等于隐含oracle。

## 第三次源码审查：calibrated_run.py与三arm配置

已读runner和calibrated_b0/b1/b2配置路径；B0明确15例case→seed_base=0,16,...224，每轮base+turns，不再依赖case_index；加载/采样底座沿用已核验后端。blocked事件response=None、不env.execute、before/after完整snapshot hash相等，并消耗turn/proposal预算；自然文本标记internal_terminal_lifecycle、actual_tool_dispatch=false、model_generated_terminal_tool=false。以上符合主要控制要求。

新增两项已通知root、待回读关闭：

- **R3-02：内部格式拒绝混入guard/执行循环输入。** runner把invalid_model_output通过env.execute写入环境history，再将完整public_view交给guard。A真实不可重试失败→一个内部FORMAT_ERROR→再提A，可能被错误视为有了新工具信息。应仅以真实action.type=tool分发历史给guard，并对“实际执行工具循环”同样过滤；内部交互循环若保留应单列。阻断记录本身当前已正确排除。
- **R3-03：被阻断raw的EOS重复渲染。** blocked/invalid反馈分支直接把含末尾im_end的raw_text作为assistant content；官方模板又加im_end。原始文件保持完全不变，但重建对话应仅去掉已知尾部生成EOS，再由模板添加一次终止标记；增加render-level回归。该显示转换不是参数修复，也不允许改模型原始动作。

最后两项关闭前尚不给runner最终验收。此审查没有运行任何测试或模型。

## 最终pre-run源码复核：conditional accept

R3-02已通过public_tool_view过滤真实action.type=tool；guard和执行循环只读该过滤历史，原始内部拒绝/终止事件仍保留。trajectory新增loop_index_scope=actual_tool_history_only及actual_tool_history，避免过滤后的index被误当全事件index。

R3-03已通过dialogue_text仅去除尾部已知生成EOS修正；raw_text保持原样。tests覆盖A→内部格式错误→A仍阻断、重复内部错误不算执行工具循环、对话末EOS去除且中间文本不更改。offline校准脚本增加官方本地tokenizer渲染检查：被阻断assistant段只含一次im_end、不伪造tool_response、不载入模型。

直接diff三组配置只见policy_name/comparison_arm不同；15个case IDs、显式seed映射、设备/精度、采样、预算、协议及外部输入hash相同。B2不含B1提示。

本次冻结源码校验值：

| 文件 | SHA256 |
|---|---|
| toolloop_fix/calibrated_run.py | 686b534bb26b6d50b007327a98597784a60a9d062beed8bbba0f8631f60f3c2a |
| toolloop_fix/calibrated_protocol.py | ba4b1ef50fbfe69d652e890c1bd231a08c623a1cdfa7db7fab611bb7960368de |
| toolloop_fix/baselines.py | 88c48607e4d80a883b9044505690c00905e02834a082d02b378cacd35b7b3b87 |

root报告149项联合诊断测试通过；尚须由正式冻结run保留测试输出及103行离线校准/官方template-render结果。**满足这些归档检查后，可以执行本轮15dev三arm探索**；并非通用语义评测、方法效果或研究门禁验收。未接受任何训练路径。

## 归档门禁实证核验：EXP-014 / EXP-015

- 读取EXP-014 manifest：completed、exit=0、evidence_valid=true；归档stderr为149tests、OK。不是本agent重跑测试。
- 读取EXP-015 manifest：completed、exit=0、evidence_valid=true；calibration.json确认103行原parse重放一致，新分类为64业务工具、6结构化finish、4结构化stop、29由旧EXPECTED_EXACTLY_ONE_TOOL_CALL转为natural_text。
- 按保存的逐行标记独立计数，49行位于各案例首个校准terminal之后；这些旧后续生成不可当新协议的反事实结果。该诊断没有修改旧评分。
- 本agent独立重新计算1个源generations文件及17个旧manifest的SHA256，全部与EXP-015冻结config一致，hash_errors=[]。
- 官方本地tokenizer渲染检查实际归档：blocked assistant仅1个im_end，原raw不变，不加载模型；模板hash为此前核验的273d8e0e...。
- EXP-016冻结副本的三个新模块hash与上表完全一致，当前live文件也一致；开始时B0 run仍running，不提前给成绩结论。

**门禁判断：accept limited calibration/implementation。** 可以进行已登记、同底座的三arm15dev探索；这不代表自然语言语义已被判定，也不代表B1/B2有效、研究门禁或训练路线通过。

## 首个真实arm：EXP-016 B0解释边界

读取已完成B0归档：15例、41轮；11自然语言terminal、0parser错误、1/15实际工具循环、3/12取得目标工具证据、1/12精确结构化完成、0/3必要重试取得证据；7/15为可完成任务中没有目标证据就返回文本/stop的观察（本聚合字段分母为全部15例）。这些是新协议成绩，不与旧013直接作方法收益差值。

原始输出抽查揭示指标必须保持分层：jobs-normal在早先已得到正确OK，因此tool_goal_attainment=true，但随后发明额外ID，最终自然语言说无法访问并建议其他target；**取得工具证据并不代表正确回答**。orders-normal则在得到证据后返回有关shipped的文本；本自动协议仍不自行判其完整语义正确。两者同属natural_text，但含义不能由“文字终止”一刀切决定。

jobs-recoverable的finish_task.answer明确写未找到JOB-OLD，因此unsupported_finish_wrapper不应翻译为确定的“虚报成功”。循环下降与格式错误为零很大程度受自然文本早停规则影响；这是校准后控制流改变，不是已经修复目标循环。需等同协议B1/B2完成后作配对解释。
