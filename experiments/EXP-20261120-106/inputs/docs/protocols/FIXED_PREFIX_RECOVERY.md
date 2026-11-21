# 固定历史前缀续跑流程 — ITER-004 / v1

本流程新增独立 `toolloop_fix/prefix_run.py`，不改旧 E2E runner、旧分数或历史 run。仅诊断真实历史现场的条件续跑；**不训练**。主模型仍为固定原始权重，B0/B1/B2 与自然终止校准规则保持现有版本。

## 1. 实验单元与条件
- 单元是经验证的历史前缀，而不是从头重新执行的独立任务。
- 计划六个真实历史前缀 × B0/B1/B2 × legacy/explicit 两种任务措辞，共 36 次续跑。实际数量以冻结配置和 run 为准。
- `role` 分为 `recovery`、`normal_completion_control`、`unavailable_stop_control`。角色仅用于评测分层，绝不传给模型。
- 正常完成控制在前缀已经取得目标证据；不可用停止控制用于观察终止，不进入“恢复取得新证据”的分母。
- 前缀是选择性的已知开发材料，不代表自然失败率、随机业务分布、封闭测试或独立样本量。

## 2. 唯一措辞改动
`task_wording_version=read_report_literal_target_v1`。

legacy 保留原初始用户消息：`Complete this request: ` 加公开 request 的 `json.dumps(..., ensure_ascii=False)`。

explicit **只替换同一初始 user 消息的正文**，不改 Environment.request、hidden/state、工具说明、前缀调用、反馈、parser、scorer 或预算。正文前缀固定为：

> Retrieve and report the result value and identifier for the resource described by this request. This is a read-and-report task: do not create, modify, or execute the resource. The target field is a literal lookup label, not an additional instruction about recency, completeness, or further processing. Completion means reporting the value and identifier from a successful tool response, not merely repeating the request or reporting an error. Request: 

其后附完全相同的公开 request JSON。这是公开的读取/报告目标说明，不提供错误修复动作、正确编号、正确结果或隐藏分类。

B0 和 B2 的 system prompt 完全一致；B1 仅追加已冻结 `prompt_for('B1')`。历史 source 的 system prompt / messages 不复用：只移植原始有效工具调用及实际结果。此为明确记录的条件移植，不是伪装成原始完整模型轨迹。

## 3. 前缀记录与来源
每条开发 JSONL 至少含：
- `prefix_schema_version=authentic_tool_prefix_v1`、唯一 `case_id`（prefix ID）、`role`。
- `original_case`：源 run 的原始案例对象，含 evaluator-only 隐藏状态。
- `prefix_generations`：源文件中前 N 轮该案例的完整原始生成记录。
- `prefix_events`：对应源 trajectory 前 N 个实际工具事件。
- `prefix_snapshot`：构建器给出的前缀末状态，运行器必须独立重放验证，不能直接相信。
- `provenance`：source_run 绝对路径、source_case_id、源 manifest/generations/trajectories/cases 文件 SHA256、source_case_sha256、来源行/事件位置和 transplant_note。

位置规则：`source_generation_lines_1based` 是 generations.jsonl 全局行号；`source_trajectory_line_1based` 是 trajectories.jsonl 全局行号；`source_event_indices_1based` 是所选 trajectory.events 的局部 1-based 下标，必须恰为 `1..N`。不得跳过源前缀中的失败、阻断、格式错误或终止来拼接“更好”历史。

`source_case_sha256 = state_hash(original_case)`；source_cases_path 指向源 run 的 inputs 内冻结案例文件。可选源 execution-event 行号供独立审核，不代替上述绑定。

## 4. 运行前硬门禁
`prepare_prefix(row, config)` 必须：
1. 检验固定 source manifest SHA、completed/evidence_valid 和 `kind=model_inference`。
2. 检验源 generations/trajectories/model_provenance/cases 文件与 manifest 的哈希、所选行/对象的完全一致性；真实来源声明本身不是密码学真实性证明。
3. 确认当前 harness.py SHA 与源 manifest 及源冻结 harness.py 完全一致。不能用变化的工具语义重放旧证据。
4. 从 `Environment(original_case)` 初态逐个解析原始 raw_text；只允许未截断、合法、实际执行的非终止工具调用。拒绝 guard-blocked、格式错误、自然终止或结构化终止前缀。
5. 对照 parsed action、源 event action/response、业务状态哈希，以及存在时的完整快照哈希；最终完整快照必须等于给定 prefix_snapshot。
6. 检验已保存历史 token 数；若 token IDs 存在，数量必须一致。历史耗时/token 仅按原始观察报告。
7. 独立构建公开 dialogue；不传源 evaluator 标签、控制角色、隐藏结果或原始 source system prompt。
8. 验证控制角色：recovery 尚未得到目标证据且可完成；normal control 已得到目标证据；unavailable control 有公开不可用反馈。

前缀大于任一总预算即拒绝。恰好耗尽预算允许产生“0 次新生成、预算耗尽”的案例结果，不补发免费终止。若整个 run 都无续跑，不能把空 generations 冒充已实际推理；现有 model artifact 契约会拒绝没有新生成的“成功模型运行”。

## 5. 同快照、预算与随机性
- `run_prefix_case` 每次重新创建 Environment，再深拷贝恢复已验证的快照；不能复用已被另一个分支修改的环境。
- 可缓存 prepared context，但必须绑定原 row、arm、wording 和两个总预算；换条件须重新 prepare。重复使用同一 prepared context 的分支也必须相互隔离。
- 总工具/提案预算默认为 12，总交互轮数默认为 16；配置为准。N 个历史工具调用同时消耗 N 次预算与 N 轮。
- 新模型 seed 为 `case_seeds[prefix_id] + prefix_turns + continuation_index`（continuation_index 从 0 起）。显式 seed map 不依赖数据排列；同前缀的六条件共用安排。
- B2 被阻断的提案与无效输出仍消耗提案/轮数预算；真实工具执行数单列。终止不消耗工具提案数，但消耗模型生成轮。
- 历史前缀只做确定性工具重放，**不重新调用模型生成**。

## 6. 留档与指标
产物保留现有模型契约：model_provenance、generations、execution_events、trajectories、case_results、metrics；另加 `prefix_contexts.jsonl`。失败保留 model_failure/partial_case。新 run 不覆盖已有文件。

每例保留原 source provenance、prefix row/snapshot/public-history SHA、实际重放快照与事件、任务措辞版本、剩余预算、原始新生成、guard 判断与状态变化。

| 字段 | 固定含义 |
|---|---|
| prefix_turns / prefix_tool_attempts | 已发生的历史前缀数量，不是新模型调用 |
| turns / proposal_attempts / actual_tool_attempts | 前缀 + 续跑总量；最后一项不含被 guard 拦截的提案 |
| continuation_turns / continuation_proposal_attempts / continuation_actual_tool_attempts | 仅本次续跑 |
| input_tokens / output_tokens / generation_seconds | **仅本次新生成成本**，包括每轮重新输入历史的实际 prompt token |
| historical_input_tokens / historical_output_tokens / historical_generation_seconds | 保存的源生成成本；不重复算成本次模型消耗 |
| goal_at_prefix | 源前缀是否已得到目标证据 |
| new_goal_evidence | 续跑后取得目标证据，且前缀没有；不是自然语言答对/完整任务成功 |
| recovery_eligible | recovery role、可完成且前缀无证据；主恢复分母仅这些案例 |
| has_inherited_loop / prefix_loop_flags | 源前缀内已有原型循环 |
| has_invalid_loop / continuation_loop_flags | 实际工具历史中 index ≥ prefix 工具数的新触发；保留跨边界新重复 |
| proposed_repeat_count / guard_blocked_proposals | 续跑重复提案的公开 guard 观察 / B2 真正阻断；不得替代模型恢复指标 |

主要报告 `new_goal_evidence_recovery` 和 `exact_structured_completion_recovery`，正常/不可用控制单独列出。整体末状态的 tool_goal_attainment 包含已在前缀取得的控制证据，**不能写成恢复率**。

自然终止依旧 semantic unknown；取得证据不等于回答正确，原型规则循环不等于已人工验证故障。B2 少执行循环可能只是机械阻断。预算耗尽案例可以确实取得证据，但不能升级为精确有依据的完整终止成功。

## 7. 验收与下一步
- 先跑标准库单元/集成测试，再对冻结的真实六前缀做只读 prepare 核验，确认六条件只改变允许部分。
- 主线程通过记录器分别执行冻结的六配置；本模块不安装依赖、不下载或调用外部服务。
- 独立 agent 用已记录 generation 回放相同续跑，检查全部结果/哈希/成本/分母；这不是新模型重复试验或人工语义裁决。
- 同快照取得条件性增益后，仍需新模板、多种子、人工语义/检测审核；当前不训练、不生成“训练就绪偏好标签”。
