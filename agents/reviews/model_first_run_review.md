# 首次真实模型运行独立审查

- Reviewer：`/root/source_audit`；日期：2026-11-16（Asia/Shanghai）。
- 范围：只读检查冻结代码、运行产物、安装源码及下载文件；本 agent 不执行模型、不安装、不改变代码/配置/原始 run。
- 状态：本轮审查完成。首个 CPU run 失败于 provenance 序列化；EXP-011 CPU 完成推理但任务失败，EXP-012 MPS 同例任务成功，EXP-013 完成15例开发推理但P1/训练门禁未通过。后续结果见追加记录。不得把首轮失败阶段标签 `model_load` 误解为已证实权重加载缺失。

## 已核验的输入

标准库流式 SHA256 独立复算 9 个下载文件及大小，与模型清单全部一致；总计 **1,769,916,415 bytes**。模型 manifest SHA256 为 `049c3d159e86cf1492a8020d5a95f8d0b92c13091fc82891ce12979c26899d2d`。

官方 revision `2fc06364715b967f1860aea9cf38778875588b17`，权重文件 SHA256 `04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696`、大小 1,746,942,600 bytes，与此前一手 pointer 核验一致。此次读取本地字节，不重新下载。

本地 tokenizer_config 再次确认 `<|im_end|>`=248046（EOS）、`<|endoftext|>`=248044（PAD），text_config EOS=248044；两种终止 token 并集的策略有输入依据，但仍需成功 run provenance 确认实际生效。

## EXP-20261116-007：工程测试证据

读取归档 manifest 和 stderr：completed、exit 0，`Ran 92 tests ... OK`。这是联合工程测试，不是模型推理质量结果；没有声称本 agent 重跑了这 92 项。

## EXP-20261116-008：保留失败，不冒充生成成功

- 冻结快照执行；开始 2026-11-16T06:24:44.702348+00:00，结束 06:24:52.418632+00:00；status=failed、exit=1。
- stderr 显示权重进度 320/320，随后在 `model_run.main -> write_json(model_provenance.json) -> json.dumps` 失败：`TypeError: Object of type set is not JSON serializable`。
- traceback 已越过 `TransformersBackend.__init__` 的 `check_loading_info`，因此没有看到 missing/mismatched 权重拒绝；但 provenance 文件没有成功写出，不能仅靠进度条认证所有加载细节。
- 没有 generations/trajectories/case_results；stdout 为空。尚无真实生成成功、EOS 行为或任务成功证据。
- `model_failure.json` 保留 TypeError 和 stage=model_load；该 stage 覆盖加载后 provenance 写入，准确根因是**元数据序列化兼容性**。

### 根因与最小修正建议（已通知协调者和实现者）

已安装 Transformers 5.23 的 `utils/loading_report.py` 中，`LoadStateDictInfo` 的 missing_keys、unexpected_keys、mismatched_keys 是 set；`to_dict()` 原样返回它们。`modeling_utils.py` 的 output_loading_info 分支将此字典返回给调用者。因此即使这些集合为空，直接作为 checkpoint_loading_info 写 JSON 仍失败。

建议加载后将集合确定性转换为列表，保留结构而非把整个对象转成字符串，并在校验错误消息格式化之前完成；否则真有 missing key 时，错误分支也会被同一 TypeError 遮蔽。加入空集合/非空集合回归测试，失败原 run 不覆盖，修正后另建 run。

## 已审查的采样路径及边界

- 冻结 backend 使用内置 Qwen3_5ForCausalLM、local_files_only、trust_remote_code=False、use_safetensors=True、eager；source 中无脚本策略接管。
- 自定义 presence penalty 对当前 completion 后缀的 unique token 只减一次 2.0，不惩罚 prompt/history；TF5.23 installed generation/utils.py 在 temperature/top-k 之前合并该 processor。此为源码语义检查，不是跨框架数值等价实验。
- MPS fallback 环境、MPS built/available、参数 device/dtype 已加入 provenance 路径；需等待成功写出的文件才能确认实际值。
- 后续判断须分别报告：下载完整、加载完整、生成协议有效、任务完成；它们不是同一个门禁。15 个手写开发夹具即便通过，也不等于正式测试、循环归因或训练效果。

## 本审查实际操作

使用 sed/rg 读取指定冻结和安装文件；Python 标准库解析 manifest/tokenizer_config，分块哈希 9 个文件；读取归档 stdout/stderr 与 failure JSON。未调用模型、未额外实验、未修改运行证据。

## 追加：EXP-010 回归与 EXP-011 CPU 重试加载门禁

- EXP-010 归档 stderr：94 tests、OK；只读取，不声称重跑。
- EXP-011 冻结 backend 将 set 确定性排序为列表；错误消息分支也使用该转换。provenance 已实际写出，序列化故障已被新 run 越过；EXP-008 原始失败保留。
- 成功写出的实际类为 Qwen3_5ForCausalLM，参数 CPU / torch.float32；checkpoint_loading_info 的 missing/unexpected/mismatched/error_msgs 四项全部空。
- model_manifest 与 chat_template SHA256 均匹配前述本地独立核验；模型 revision 不变。
- effective_generation 确认 sampling=true、temperature=1、top_p=1、top_k=20、min_p=0、presence=2、repetition=1、nonthinking、eager、use_cache；EOS=[248044,248046]、PAD=248044、每次生成上限256。
- runtime 确认 Python3.12.13、torch2.15.0、transformers5.23.0、tokenizers0.23.3、safetensors0.8.0；MPS built=true/available=false（该沙箱进程），MPS fallback 环境="0"。
- stderr 的 causal_conv1d / Gated Delta reference PyTorch 警告是未安装优化内核的回退路径，未观察到加载失败；这不等于运行速度已达标。
- 本次冻结 parser 已拒绝工具调用后缀，先前“后缀移到调用前”的 concern 在源码层面关闭。其余 native assistant/tool history 仍由模板规范化，raw文本及token单独保存。
- 此追加检查时还没有 generations.jsonl，run 为 running；支持“加载门禁已通过”，暂不支持“生成或任务成功”。后续结果另追加，不覆盖本检查时点。

## 追加：EXP-011 CPU 完整结果——模型 final 字段错误

最终 manifest：completed、exit=0、evidence_valid=true、contract_errors=[]；进程/记录成功与任务成功分开。

1. 首轮实际 prompt 只有通用系统约束、公开工具 schema 与公开请求（orders / ORD-101 / latest order）；没有 category、template_group、隐藏快照或预填答案。原始 XML 调用 get_order(ORD-101)，30 output tokens，末 token=248046，未超生成预算。环境真实返回公开响应 OK / ORD-101 / shipped。
2. 次轮 messages 包含上述 assistant 调用及原样公开 tool 响应，没有看到工具结果丢失或 identifier 被适配器替换。模型原始 XML 自己生成 finish_task(answer=shipped, identifier=completed)；parser 忠实复制了该字段。
3. 因 identifier 错误，success=false、false_success=true；2 turns / 1 tool attempt、0 parser errors、0 detected loops、未耗尽预算。这是**一个随机采样结果中的模型最终字段错误**，不是已证实的适配器解析错，也不是循环证据。不能确定具体数值/后端原因，更不能据此推断总体模型能力。
4. 共 1,426 input tokens、70 output tokens；两次 generate 用时 98.680932333s 和 121.017801417s，合计 219.69873375s。包含预填充及采样处理，不是纯 decode throughput。

两个 prompt 的 SHA256 均复算一致；generated_token_ids 长度与 output_tokens 一致，均以 248046 结束。原失败不作成功重标。

## 追加：EXP-012 MPS 同例探针——有限通过

- manifest：completed、exit=0、evidence_valid=true。加载信息四项空；实际参数 mps:0 / torch.bfloat16；MPS built/available=true，PYTORCH_ENABLE_MPS_FALLBACK="0"，模型文件/模板与CPU相同。
- 原始调用依次 get_order(ORD-101)、finish_task(answer=shipped, identifier=ORD-101)，parser 与 raw 一致；环境与 evaluator 得到 success=true、false_success=false、0 parser errors、0 detected loops、未耗尽预算。
- 两轮分别 30/44 output tokens，末 token 都为248046；共74 output tokens、10.620828875s generate 时间（6.405324208s + 4.215504667s），1,426 input tokens。
- 独立比较两 run：三个冻结 model_*.py 文件相同；两轮 prompt_text 和 prompt_token_ids 均完全相同。公开信息链没有因 CPU/MPS 切换改变。
- 支持“该机器该配置可完成一次加载→工具→终止的真实 MPS 端到端探针”；不支持总体成功率、模型循环问题、训练收益、数值等价或普适速度提升。CPU FP32/MPS BF16 精度与采样后端同时变化，同种子不意味着同一随机序列，不能把一次输出差异归因到单一因素。
- reference PyTorch kernel 警告仍存在；它与 unsupported-MPS-op 的 CPU fallback 不是同一概念。fallback=0 且运行完成没有观察到后者启用，但未做逐算子性能分析。

后续可固定 MPS/BF16 进行有界开发夹具检查；不可把15例开发集当封闭测试，继续保留解析、字段和模型行为失败。

## EXP-013 开发夹具：定性审查（完成案例的原始证据）

下表按 `generations.jsonl` 的 case_id/turn 和对应 `execution_events.jsonl` 定位，区分不同失败，不把所有未成功轨迹称为“循环”。汇总数值另由 protocol agent 独立复算；本节不重复运行模型。

| 案例与轮次 | 原始行为与可见反馈 | 解释边界 |
|---|---|---|
| orders-recoverable，t1–5 | 旧 ID 得 INVALID_IDENTIFIER；t2自然语言被强制工具协议拒绝；随后猜 ORD-123，并把它当 find_order 的 target，而不是公开 target=latest order，最后停止 | 有真实参数/恢复路径错误，也有协议反馈介入；没有被检出循环，不应只写“重复工具循环” |
| files-normal，t1–3 | 公开请求为 /records/report.txt，模型调用 /report.txt；随后自然语言声称资源不存在，最后停止 | 首次路径改写是 raw 模型动作错误；之后格式拒绝会影响轨迹。正常任务能力不足是训练前必须排除的混杂 |
| files-recoverable，t2–12 | ask_user(identifier) 连续11次，均返回相同、不可重试的 UNNECESSARY_CLARIFICATION；终止于工具预算 | 在本环境协议下是清楚的无进展动作重复，不是合法 retry/轮询；但“已给错误ID就不允许再问”是本环境约定，不能普遍推出真实业务中澄清不合理 |
| jobs-retry_required，t2–4 | get_result(JOB-101) 连续重复；反馈明确为 PRECONDITION、retryable=false、detail=poll_job before get_result | 较干净的研究候选：poll_job 工具/schema实际提供，t3 prompt 含t2准确反馈；重复发生在后续自然语言/FORMAT_ERROR 链之前，不是工具缺失或历史丢失 |
| jobs-normal，t1–12 | t1已取得 OK 和正确结果，但未 finish；t2发明 JOB-101.daily.report；后续再次获得 OK，又重复 ask_user，最终预算耗尽 | 包含成功后不会终止、字段臆造和无进展澄清；不是仅“错误工具反馈导致重复”，应单列成功后过度行动 |
| files-clarification，t1–10 | 先猜路径，多次输出自然语言而被拒绝；t7终于取得 USER_INFORMATION 的正确ID，但t8–9仍问同一字段，最后填入虚构 final 字段 | 前段是协议交互混杂，t8–9是得到新信息后仍重复的独立动作错误；不应把整段作为单一同质负样本 |
| files-unrecoverable，t1–2 | RESOURCE_UNAVAILABLE 后选择 stop_task，但 reason 为说明性自然语言，不是精确枚举代码 | 行为上确已停止；correct_termination=false 主要体现终止schema不满足，不能写成“无法停止” |
| orders-unrecoverable，t1–6 | 不可用后仍查找/两次多余澄清，最后 stop_task；reason包含RESOURCE_UNAVAILABLE及额外说明/虚构代码 | 额外调用成本是真实行为问题，但最终未获精确终止分数还有严格 reason 相等规则的影响，两者分开 |

### 终止指标与适配器混杂必须单列

现有 evaluate 要求 final 的 answer/identifier 与工具证据精确相等；unrecoverable 的 stop reason 必须精确等于 RESOURCE_UNAVAILABLE。这个契约可以保留为严格主指标，但不能将其直接解释为一般语义正确性。自然语言结果被强制 `finish_task/stop_task` 包装拒绝会插入新的 user-role 协议错误消息，后续轨迹属于该适配器交互条件，而非未经干预的模型原生使用。

建议在**新分析/新迭代**中另报人工复核的终止意图、理由语义和字段严格合规三项；保留原分数，不在已完成 run 上放松评测以“修好结果”。尤其应区分：正确地停止但理由格式不对、过早停止、无工具证据就声称完成、拿到证据后仍不停。

### 文献整合与下一门禁建议（尚未执行）

1. 已观察到本配置的真实无进展重复候选，但正常任务、工具schema和终止协议表现不足，**P1尚未完整通过，不能直接训练**。优先预注册接入/表达方式小对照，观察正常任务与合法重试是否改善，而非立即把所有失败都作为DPO负例。
2. 先选 jobs-retry_required 的t3前公开历史冻结为诊断前缀：输入已明确要求 poll_job，候选重复动作为 get_result。由独立可见信息策略提出候选，再在同一环境快照验证完整行动，不把隐藏状态传给模型。files-recoverable可作第二类，但需明确澄清规则的环境特异性。
3. [Feedback That Backfires](https://arxiv.org/abs/2608.23651v1) 使历史呈现成为必须控制的因素：B1可改变失败动作逐字呈现，但保留错误语义/必要执行历史；不可删掉反馈后将改善归因于训练。配对多seed，报告格式错误率、成功后终止和合法retry保持。
4. [AntiDoom](https://www.liquid.ai/blog/antidoom) 的文本重复起始token方法不能直接把通用 `<tool_call>` 设为负token。这里 get_result 与 poll_job 可能在function名称处才分歧，应在真实tokenizer记录差异，并验证完整替代行动；“FTPO风格简化”不冒充完整复现。
5. [Agentic-DPO](https://arxiv.org/html/2607.10601v1) 已有动作偏好训练；本项目可能的增量在真实失败现场、执行验证与合法重试反例，而非“首次动作级DPO”。[Fission-GRPO](https://aclanthology.org/2026.acl-long.1880/) 也限制笼统“首次恢复训练”主张。当前这些只是研究设计推论，没有新颖性/效果证明。
6. 再扩至分组开发样本与多seed，人工复核检测器正反例，冻结B0/B1/B2及数据划分；通过后才考虑训练候选。15例手写开发运行不支持外部基准、泛化循环率、训练必要性或修复收益。

## EXP-013 完成后结案核验

- 最终 manifest：completed、exit=0、evidence_valid=true、contract_errors=[]；UTC 2026-11-16T06:33:40.096230 至06:41:55.867119。
- 实际文件含15条case_results、103条generations；provenance仍为mps:0/BF16，四项loading diagnostics为空，模型及模板hash匹配。运行链成立，不等于所有任务成功。
- 读取归档 metrics：严格任务完成2/12、正常完成1/3、检测器标记循环4/15、false_success4/15、合法重试完成0/3、精确reason正确停止0/3、解析错误29/103轮、预算耗尽5/15；111,370 input tokens、5,801 output tokens、generate合计491.106646419s。这些是**本次手写开发夹具及自定义协议下的计数**，不是Qwen普遍性能率；独立指标重算由protocol agent负责。
- 最后两例补查：jobs-clarification有多次自然语言询问ID，但没有转换成规定ask_user调用，最终预算耗尽；jobs-unrecoverable多次换猜测target，t11取得RESOURCE_UNAVAILABLE后t12仍换ID尝试，最终耗尽。两者未触发该保守重复检测器，**未标记循环不等于有效进展或行为正确**。
- reason精确匹配失败与语义停止意图的区别继续保留；不因指标难看而事后改变已归档评分，也不把所有低分归因于模型能力。

**结论：** 原始官方模型本机加载/生成/执行/留档链已取得真实证据；同时观察到可定位的无进展动作重复候选。接入协议、正常任务表现、终止表达及原型检测仍构成实质混杂，训练路线没有得到验证。下一轮应新登记协议诊断、通用B1/B2与冻结前缀配对实验，再扩展约200开发案例及人工复核；本轮不推进训练或封闭测试。
