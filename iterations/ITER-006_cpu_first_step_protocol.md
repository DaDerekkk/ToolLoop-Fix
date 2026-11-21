# ITER-006 / CPU 首步提案研究（登记于推理前）
登记 owner research6，2026-11-19 Asia/Shanghai；独立于 Colab L4 冒烟验收，限定本机 CPU-only，不联网／训练。父实验 042 的 12 格单模板开发集固定，不改变既结束的记录。批次占用 CPU 测试最多 4 个计算线程；若 8 分钟仍不能完成，则保留已生成材料并按未完成报告，不逐行筛选重试。

**问题：** 固定“读并报告 value 和 identifier”任务、一个只读工具、共同系统消息及同一类公开反馈，只换工具名 get_order/read_file 与两个中性 ID R-17/R-42，观察第一次决策。2×2×(TIMEOUT/OK/RESOURCE_UNAVAILABLE)；每种条件出现一次，共同 seed=0，Qwen3.5-0.8B 固定 revision 2fc06364715b967f1860aea9cf38778875588b17，CPU float32，贪心、beam=1、禁思考、最多96新 token。三个固定批，各包含同一反馈下四种工具名/ID配对，左填充；不跨会话共享 KV。一次完整输入一次输出，不重采样、不执行模型给出的工具提案、不续跑或计恢复。

**预设指标：** TIMEOUT 精确同工具同参数重试提案；OK 用现有响应中确切的值和 id 的结构化 finish；RESOURCE_UNAVAILABLE 精确错误码的结构化 stop。按每个工具×状态分别列分母 2；原样保留其他提案、无效/截断和自然语言终止，后者语义 unknown，不能仅因未使用结构化输出认定为失败。第一步一个正确提案不等于拿到新证据或端到端成功；不是历史 B0/B1/B2 排名。

**记录形式：** operations/CPU-20261119-001 独占新目录，归类 model_inference/single_proposal_no_dispatch；独立冻结代码配置及042公开输入。EXP-20261119-043 为后验 analysis 核验原始 token、raw、prompt 来源和分组结果并拷贝原件（不虚构旧 schema 所需的 E2E 轨迹）。全量来源在 SHA、命令、stderr/stdout、平台与实际设备中区分。配对不代表独立抽样；此两种语义别名、两个中性 ID、一个模板组不能推出跨域结论。

## 实际结果与解释边界（2026-11-19，上海）
- CPU-20261119-001 至 16:22:58 完成，exit=0，201.16 秒。12/12，7,428 输入 token、234 新 token，0 截断；所用参数均在 CPU float32，4 计算线程；无 MPS、GPU、云、训练或工具执行。043 本身仅核验/归档，不重复计算模型输出；重新编码 12 个公开 prompt，与每例原输出 token 解码校验一致。
- 预设每格 n=2（2个ID）。六格结构化正确提案均0/2，**所有 12 条都是自然文本**，无格式错误。TIMEOUT 两种工具都没有同参重试（各0/2）；固定中性请求后，本开发模板不再出现旧两例中订单重试/文件不重试的分化。CPU dtype、解码、批处理和任务上下文均与历史研究不同，不能归因到某一个变量，更不代表任何领域规律。这里没有续跑或取得新证据。
- 人工复核候选（agent 阅读原文，不改自动分数）：正常 OK 四条均写出可见的 R-17/R-42 和 amber，不能说“没用 finish 就都失败”；四条不可用响应只有 code=RESOURCE_UNAVAILABLE 和 retryable=false，没有 value，四份文本却将错误码写成记录的 value。另外 TIMEOUT 组的 get_order / R-17 声称记录“was not found”，错误反馈未提供这一事实。自动分类仍是 natural_semantics_unknown；建议交给独立人工复核事实主张。
- 查阅/接手：043/first_step_summary.json 和 first_step_decisions.json；operations/CPU-20261119-001/raw_generations.jsonl 含原始解码文本及完整 token，来源 payload 第 1–12 行按 SHA 绑定。配置/权重清单/实际命令/依赖与日志保存在操作目录；043/operation_sources 为哈希校验副本。未将输出选作训练数据。
