# 项目进度

更新时间：2026-11-16（Asia/Shanghai）。本文件由协调者维护；实验事实以独立 run 目录为准。

## ITER-002 已完成
- 用户已追加授权安装依赖、下载权重并运行；原先“先本地验证”的限制已扩展，不含训练跳门禁/付费服务。
- 硬件已通过获准只读检查确认：Apple M5、10 CPU 核、16 GiB 内存。
- `operations/SETUP-20261116-001`：隔离 Python 3.12.13、torch 2.15.0、transformers 5.23.0 已安装并通过 import/runtime 探针；主机 MPS 可用。
- 固定 revision 官方 Qwen3.5-0.8B 已下载：9 文件、1,769,916,415 bytes，SHA/大小校验通过；不量化、不执行仓库代码。
- 模型适配器和 schema v3 记录契约已完成，94 项联合测试通过（EXP-010）。
- EXP-011 CPU：完整真实推理，但最终 identifier 错误，任务失败；EXP-012 MPS：正常案例成功。
- EXP-013 MPS：15 开发案例、103 轮、5,801 输出 token；进程及记录契约通过，严格任务完成 2/12。
- REVIEW-003 独立重算全部 107 轮真实生成/动作/状态与指标通过；17 run 完整性检查 0 errors。
- 数据索引和 13 项失败复核队列已写入 data/raw、data/processed；不属于训练就绪样本。

## 当前
- 阶段：P1 真实模型开发预检已开始；门禁未通过，训练保持 hold。
- 已完成：4 agent 分工、三轮迭代、来源/运行审查、可审计模拟及真实模型执行。
- 模拟支持 E2E/固定前缀；当前真实模型仅 E2E。真实模型推理已执行；模型训练/正式测试未执行。
- 无付费服务、无云 GPU、无上传/发布。已有本地运行已获授权，不再把旧“待环境选择”当阻塞。

## 历史 G0 证据（最新模型证据见上）
- `EXP-20261116-003`：51 项联合测试通过。
- `EXP-20261116-004`：42 条模拟轨迹及结果（15 端到端 + 6 固定前缀，分别 2 策略）。
- `EXP-20261116-005` / `006`：修正版/首版分别从归档输入重放，三类产物 SHA256 全等。
- `EXP-20261116-REVIEW-002`：独立重跑 51 项测试及边界断言通过；独立复算重放哈希通过。
- 共 9 个 run，含 1 个预期失败回归；最终完整性检查 0 errors。失败日志未覆盖。
- 迭代：`iterations/ITER-000_bootstrap.md`、`iterations/ITER-001_regression_and_replay.md`。
- 独立审查：`agents/reviews/iteration1_review.md`、`agents/reviews/recording_review.md`。

## 阶段门禁
| 阶段 | 状态 | 通过依据 |
|---|---|---|
| P0 工程基础与来源 | 有界通过 | 51 测试、两次冻结重放、独立审查；仅模拟工程范围 |
| P1 真实问题确认 | 部分完成，未通过 | 已真实 pilot，但正常 1/3、格式错 29/103；待协议/B1/B2、约 200 开发案例 |
| P2 数据与评测冻结 | 未开始 | 检测器人工复核、分组切分、候选验证、封闭集冻结 |
| P3 训练比较 | 未开始 | 先过 P1/P2；SFT、FTPO、普通行动 DPO 与专项方法公平对照 |
| P4 封闭测试 | 未开始 | 冻结配置、多种子、聚类区间、外部回归及成本 |

## 资源现状
- 系统 Python 3.9.6 未修改；模型实际使用项目 .venv 的 Python 3.12.13 / torch 2.15.0 / transformers 5.23.0。
- 主机 M5/16 GiB、MPS 可用；沙箱内 Metal 不可见时走正常工具审批，不静默换后端。
- 纯 PyTorch reference kernels，未安装 CUDA/vLLM/FLA；MPS fallback 环境值 0。
- 完整安装/下载 manifest 和依赖 freeze 在 operations/；原始权重在忽略的 models/。

## 下一步
1. 先审查严格终止字段与自然语言语义：false_success=finish_task 未满足目标，不是所有案例都声称成功；正确终止 0/3 不等于 3 个都未停止。
2. 冻结 jobs-retry_required 的 PRECONDITION 后重复 get_result 现场，比较公开信息下的通用 B1/B2；保留当前 pilot 不重打分。
3. 改善正常任务/协议后扩约 200 开发案例、多种子；显式冻结 per-case 种子，防止重排案例造成不配对。
4. 检测器人工复核、分组/封闭划分、完整候选恢复验证未完成；不直接训练或生成声称已验证的 DPO 数据。

其他限制：只做 policy/mode 自动聚合；复杂语义循环、ask_user→定位分支、外部 BFCL、训练成本尚未覆盖。模型 token/generate 耗时已记录；快照/hash 不等于安全沙箱或签名。

接手入口：`agents/handoffs/coordinator.md` 和 `agents/NEXT_AGENT_PROMPT.md`。
