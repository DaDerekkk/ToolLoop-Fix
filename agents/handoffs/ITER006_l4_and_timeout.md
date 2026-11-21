# ITER-006 交接：Colab 原始回执与配对开发实验分开记账
2026-11-19（Asia/Shanghai）。用户再授权直接用 L4 获得可核验输出；无训练、充值、公开发布或他人实例操作。之前各轮只读，用独立编号修正，不能把后续结果补到001–003。
 
## 已完成的科学对照（不是 GPU 冒烟）
- 042 是 synthetic_harness：固定同一只读业务语义、用户模板，交叉 get_order/read_file、R-17/R-42、三种公开反馈，共12格。4个超时在环境中可以同参合法重试取得新 OK；另有4个已有 OK 和4个不可用停止反例。
- 实际模型源 `operations/CPU-20261119-001`：本地 CPU FP32、Qwen3.5-0.8B 同一锁定版本、seed 0、不启用思考、贪心，一格一个模型首步决策。12条均为自然语言终止，无截断、无工具派发，不能宣称恢复率。043 是该真实推理的离线逐 token 归档，不是第二次推理。
- `experiments/EXP-20261119-043/first_step_decisions.json` 和 `first_step_summary.json`：可重试 TIMEOUT 中两个工具名均 0/2 同参重试；OK 的 4 条文本均给对 ID 和 amber，不能按无 finish_task 判错；不可用的 4 条文本均把 RESOURCE_UNAVAILABLE 错称业务记录值。另有一个 TIMEOUT 回复称记录不存在，无相应证据。完整原文和对应工具响应见 `agents/reviews/iter6_043_semantics.md`（agent 初审，非人工金标，非训练标签）。
- 与上轮回放的两个旧例不同：统一模板后两工具首步都不重试。不可归因为工具名或 CPU；历史措辞、前缀、精度、设备和采样安排不同。后续宜先让人工盲评“错误码当业务值/声称已执行/正确自然终止”，再增加独立模板与种子做配对，不用小样本启动训练。
 
## Colab 路径与复核方式
- 038、039和041仅是运行前输入冻结及本地工程测试；040是002（冷启动探针未通过）和003（sentinel 已回、长 exec 超时）的终态归档。两次均核实停止、旧分配未动，040的完成不等于产生模型结果。
- 004 使用同一官方 revision 和公开 lookup_demo 请求；worker 在独立 nonce 激活后只执行一次，阶段和最后一帧写在 VM，Contents 回传原字节。只接受校验成功的原始 JSON，再用本机固定 tokenizer 重建 prompt、复解 generated_token_ids 并单列工具计划是否匹配。不能从 CLI 退出码、sentinel、回传字节或脚本预期单独推断成功。
- 训练计划始终是原来的 plan_only/disabled；没有核验或迁移 M6/24G。统一索引使用 `python3 scripts/validate_records.py`，后续进度见 `PROGRESS.md`。切勿为重现某个措辞重开付费实例。

**004/044 已验收：** 读取 `experiments/EXP-20261119-044/operation_sources/toolloop_probe.json` → `l4_output_verification.json` → `l4_detached_audit.json`。L4/BF16、302输入/32输出、2.96秒生成、正常EOS，解析工具名和参数均匹配；无工具执行。004已停机（191.90秒核验完成，0活动/0 CU每小时）。完整流程见 `docs/protocols/COLAB_RECEIPT_WORKFLOW.md`；模型、云运维和离线研究证据入口见 `data/raw/iteration6_evidence_index.json`。045本地回归256项通过，本轮收尾53个run完整性0错误。
