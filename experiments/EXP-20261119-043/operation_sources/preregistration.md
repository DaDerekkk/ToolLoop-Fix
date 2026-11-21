# ITER-006 / CPU 首步提案研究（登记于推理前）
登记 owner research6，2026-11-19 Asia/Shanghai；独立于 Colab L4 冒烟验收，限定本机 CPU-only，不联网／训练。父实验 042 的 12 格单模板开发集固定，不改变既结束的记录。批次占用 CPU 测试最多 4 个计算线程；若 8 分钟仍不能完成，则保留已生成材料并按未完成报告，不逐行筛选重试。

**问题：** 固定“读并报告 value 和 identifier”任务、一个只读工具、共同系统消息及同一类公开反馈，只换工具名 get_order/read_file 与两个中性 ID R-17/R-42，观察第一次决策。2×2×(TIMEOUT/OK/RESOURCE_UNAVAILABLE)；每种条件出现一次，共同 seed=0，Qwen3.5-0.8B 固定 revision 2fc06364715b967f1860aea9cf38778875588b17，CPU float32，贪心、beam=1、禁思考、最多96新 token。三个固定批，各包含同一反馈下四种工具名/ID配对，左填充；不跨会话共享 KV。一次完整输入一次输出，不重采样、不执行模型给出的工具提案、不续跑或计恢复。

**预设指标：** TIMEOUT 精确同工具同参数重试提案；OK 用现有响应中确切的值和 id 的结构化 finish；RESOURCE_UNAVAILABLE 精确错误码的结构化 stop。按每个工具×状态分别列分母 2；原样保留其他提案、无效/截断和自然语言终止，后者语义 unknown，不能仅因未使用结构化输出认定为失败。第一步一个正确提案不等于拿到新证据或端到端成功；不是历史 B0/B1/B2 排名。

**记录形式：** operations/CPU-20261119-001 独占新目录，归类 model_inference/single_proposal_no_dispatch；独立冻结代码配置及042公开输入。EXP-20261119-043 为后验 analysis 核验原始 token、raw、prompt 来源和分组结果并拷贝原件（不虚构旧 schema 所需的 E2E 轨迹）。全量来源在 SHA、命令、stderr/stdout、平台与实际设备中区分。配对不代表独立抽样；此两种语义别名、两个中性 ID、一个模板组不能推出跨域结论。
