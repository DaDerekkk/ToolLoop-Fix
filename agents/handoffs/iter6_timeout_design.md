# ITER-006｜独立工具名 × 标识符 × 反馈开发夹具
Owner: research6。输入：ITER-005/037R1 指出两个旧 TIMEOUT 前缀的首步不同，但其任务/标识等也不同；仅作为设计动机。输出：042（synthetic_harness）及三个带 timeout_crossed 的新文件。依赖：已有本地 Environment、校准协议；不用权重、GPU、网络或训练。

## 运行前登记
- 一套通用“读取并报告该记录的 value 和 identifier”的用户模板、一种只读工具文档、两个终止工具；同一格只有一个业务工具。2 个工具名 get_order/read_file × 2 个同格式、中性标识 R-17/R-42 × 3 个公开工具响应 TIMEOUT(retryable=true)/OK/RESOURCE_UNAVAILABLE(retryable=false)，共 12 格；这些是手写公开开发历史，非真实模型产生的前缀。
- 两个 alias 都使用既有 Environment，参数始终 identifier，相同底层隐藏 value、等价状态及只读语义。只使用中性规范化不变字符串，避免文件路径规范化差异。限制业务选择面为一个仅读工具；不涉及 ID 缺失、查找、更名、轮询，不能直接当老 B0 指标或跨域/训练证据。
- 验收：验证完整析因网格，各状态内按名称/ID归一化后发布载荷逐字节相同；模型载荷只含 messages/tools，不含标签、隐藏状态、案例号、预期动作；原始响应重放通过。脚本策略应在4个超时格实际同参重试取得新 OK 再精确交付；4个正常格用前缀证据交付（不是恢复）；4个不可用格按可见错误停止。另测自然文本仍待审、错误认领/错误四舍五入式泛化不算目标。条件不满足则失败保留，不改原 run。
- 后续模型主指标限**首个新决策**，还需分开报告同参提案、是否实际执行、取得新目标证据、最终精确结构化交付；正常/不可用对照分开计，文本终止不被自动认作正确或失败。冻结每个格的公开 payload SHA，另行登记后才可跑模型；本轮此任务不运行模型。

## 已执行 / 接手
- `EXP-20261119-042` completed / exit 0 / evidence_valid=true。记录器从 inputs/ 冻结副本执行 `.venv/bin/python -m scripts.check_timeout_crossed --config '{config}' --output '{run_dir}'`（绝对命令见 manifest）。人工编写的 12 个前缀、同一模板组；可见反馈三组，组内任意组合只差工具名/标识符。TIMEOUT 4/4 脚本合法重试取得新证据后交付；OK 4/4 用已有证据交付，无新恢复；不可用 4/4 精确停止。三者均为脚本轨迹，不是 Qwen 分数。独立读取 12 行 public_payloads.jsonl，内容逐行 SHA 与 synthetic_trajectories.jsonl 对齐。
- 数据：`data/dev/timeout_crossed_iter6.jsonl` 是夹具索引，含 condition/case_id，不给模型。父 run 037R1 仅作来源定位。新 run 的 `public_payloads.jsonl` 每行只含 messages/tools，最后一轮为真实模拟器产生的合成工具结果；`synthetic_trajectories.jsonl` 是隐藏评估结果，不作为提示或训练。已验证 SHA 对应**去除行尾换行**的规范 JSON 字节，不代表 tokenizer 提示哈希或云端验证。
- 云端候选：若另行登记小批次，仅以每行公开载荷作为独立新会话、每格共同种子和固定解码，保留 raw/token/实际输入 prompt/hash，按上述三种状态分别计数；首轮输出在本地校准 parser 审查，无效/被截断保留，论文式成功率不在本批次范围。只有仿真执行后读到真正新 OK 才能计“恢复”；单次提案本身只够判“首步同参重试”。正常文本的值/解释由人工复核。12 格复用同模板和中性标识，不能推断领域效应；尚未对照随机种子、域内措辞/文档差异，未在此设计上运行任何模型或训练。

## 后续同轮 CPU 模型执行（与 042 脚本预检分开）
后续获得协调者分配，在本地 CPU 而非云执行了 12 格的真实单次首步；043 analysis 只做 token 级别复验。请分别看 `iterations/ITER-006_cpu_first_step_protocol.md`、`operations/CPU-20261119-001/` 和 `experiments/EXP-20261119-043/first_step_summary.json`。两种工具在 TIMEOUT 上均 0/2 同参重试；OK 的四条自然回答均文字报告正确 id/value，不可用四条均有把错误码当数据值的表述。自动语义保持未知、agent 观察不等于人工金标。无业务工具派发、无恢复率、未训练；不和 L4 单请求烟测合并。
