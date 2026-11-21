# ITER-002 — 隔离安装、官方权重与真实模型预检

日期：2026-11-16（Asia/Shanghai）。运行前登记；结束后追加实际结果，不把计划当结果。

## 授权与目标
用户明确要求“继续用多 agent 推进，安装、下载权重、开始运行”。本轮安装本地隔离环境，下载固定官方 revision，并在模拟工具环境运行真实 Qwen。无付费 API、云 GPU、真实业务写入、上传/发布或训练。

## 分工
- root：硬件/安装/下载、运行执行、证据整合。
- source_audit：官方框架/模型兼容核验及接入审查。
- simulator：模型后端、工具协议、完整轨迹与回归测试。
- protocol：严格模型记录契约、外部模型 manifest 和独立验收。

## 假设与验收（运行前）
1. 本机可在隔离环境正确加载官方 text-only 模型；权重必须匹配固定 revision 与 SHA256，不能存在未加载的语言层随机权重。
2. 小样本先验证 tokenizer/chat template/工具解析/完整历史；正常任务的失败先排查接入，不直接归因模型循环。
3. CPU FP32 先做一个 normal；若可运行，再试主机 MPS BF16。GPU 数值可行性与 CPU 接入证据分开；不能仅因 MPS is_available 就称模型兼容。
4. 预检通过后，在同一批准环境有界运行现有 15 开发夹具的单种子 E2E。它是开发 preflight/B0 pilot，不是正式 B0 大样本、封闭测试或训练收益。
5. 每次保留原始 messages、模板 prompt、生成 token/原文、解析、工具结果、最终状态、实际版本/设备/dtype/采样与成本；禁止脚本策略接管。
6. 输出截断、解析错误、运行异常、预算耗尽都必须保留；达不到接入标准就停在框架诊断，不扩训练。

## 技术选择
- 主机：实际只读核验 Apple M5、10 核、16 GiB；MPS 在批准主机进程中可用，沙箱内不可见。这是权限差异，不是 GPU 不存在。
- Python 3.12.13、torch 2.15.0、transformers 5.23.0；仅项目 runtime/.venv，完整依赖见安装记录。
- 模型：`Qwen/Qwen3.5-0.8B@2fc06364715b967f1860aea9cf38778875588b17`，原始 safetensors，无量化；原生 `Qwen3_5ForCausalLM`，不执行仓库代码，不安装 CUDA/vLLM/flash-attn。
- 仅文本工具任务；本项目 finish_task/stop_task 单调用 XML 适配器属于自建协议，不称原生 benchmark。
- 非思考采样按模型卡核验参数，presence penalty 明确作用于当前 completion 的已生成 token；EOS 取实际模型/tokenizer 所提供的停止标记。

## 操作与实验
- 安装记录：`operations/SETUP-20261116-001`，五阶段退出 0；实际运行环境 probe 已保存。
- 下载记录：`operations/DOWNLOAD-20261116-001`；完成状态、全部文件/大小/官方校验由 manifest 判定。
- 后续实验 ID 和结果待实际执行后追加。旧 9 run 保持不可变。

## 实际运行与修复
| Run / 操作 | 实际结果 | 判断 |
|---|---|---|
| SETUP-001 | Python/venv/依赖/freeze/运行环境探针五阶段均退出 0 | 安装完成，非模型成绩 |
| DOWNLOAD-001 | 9 文件 / 1,769,916,415 bytes；官方 LFS 或 Git blob 及本地 SHA 核验通过 | 官方固定权重已就绪 |
| EXP-007 | 92 项联合测试通过 | 初版接入工程测试 |
| EXP-008 | 加载 320 项权重后，加载报告中的 set 不能 JSON 序列化；无生成 | 框架记录故障，不能计模型任务失败或成功 |
| EXP-009 | 28 项模型回归测试中 2 errors，退出 1 | 保留预期失败；一个检查新规范化接口缺失，另一个复现缺失权重错误被序列化错误掩盖 |
| EXP-010 | 最小修复为已知 set 字段转排序列表；94 项联合测试通过 | 不删除加载诊断、不绕过 missing/mismatch 检查 |
| EXP-011 | CPU FP32 真实 2 轮 / 70 输出 token；正常案例失败 | finish_task 输出 identifier=completed 而非 ORD-101；无 parser error，保留 false_success |
| EXP-012 | MPS BF16 真实 2 轮 / 74 输出 token；正常案例成功 | 同 prompt/token 输入，采样设备/dtype不同；不能推导设备质量优劣 |
| EXP-013 | MPS 15 开发案例 / 103 轮 / 5,801 输出 token；进程退出 0、记录契约通过 | 开发 pilot 完成，不等于任务全部成功 |

所有 EXP 编号均带前缀 `EXP-20261116-`；操作编号带 `-20261116-001`。精确命令、版本、输入快照、采样、token 及耗时在原始目录。CPU/MPS 各自有完整权重加载诊断，missing/mismatch/unexpected/error 均为空；未自动 CPU fallback、无脚本接管。

## EXP-013 指标（当前严格自建协议）
- 可完成任务：2/12；正常任务：1/3；必要重试：0/3。
- 原型规则循环：4/15；字段 false_success（finish_task 未满足目标）：4/15；预算耗尽：5/15。部分 answer 原文明确描述 NOT_FOUND，不应统称为自然语言“声称成功”。
- 提前放弃：5/12；严格原因码正确终止：0/3；parser-error turns：29/103。
- 输入 111,370 tokens，输出 5,801 tokens；generate 合计 491.107 秒，run 总计 495.764 秒。generate 含 prompt/prefill，不能当纯 decode 吞吐。
- 每成功可完成任务工具尝试 75/2=37.5；成本含失败可完成任务，不只看成功轨迹。
- 没有固定错误前缀模型续跑，因此 error_recovery 分母 0、值 null，不能把它说成恢复率 0%。

## 归因边界与下一步决策
`accept`：隔离安装、官方权重、实际 CPU/MPS 调用、模型审计记录及 15 案例 pilot 已完成。
`revise / hold training`：正常能力与协议尚弱，不能进入专项训练、正式测试或声称 AntiDoom 有效。

已观察到一个较清楚的失败：jobs-retry_required 在收到非重试型 PRECONDITION（明确要求 poll_job）且工具已公开时，仍重复 get_result；发生在后续格式错误链之前。files-recoverable 也有重复 ask_user 的无进展片段，但“是否必要澄清”取决于本模拟工具契约。

另一方面，部分不可完成任务已停止，但 reason 使用解释文本而非精确错误码，严格指标记未通过；不能概括成“模型都不会停止”。自由文本回答被单调用 XML 适配器拒绝与参数臆造/动作重复要分开分析。CPU/MPS 同种子单次不同结果不能证明某设备更准确。

下一轮先做失败现场审查、格式/终止语义对照和开发集 B1/B2；保留当前不变 B0-pilot。约 200 开发样本、检测器至少 200 段人工复核、多种子和正式划分仍未完成。无训练、无 LoRA 权重、无 DPO 数据集。

## 数据与交接
- 真实数据索引：`data/raw/model_runs_20261116.json`。
- 13 项失败复核队列：`data/processed/failure_review_queue_20261116.json`，全部 training_eligible=false。
- 运行说明：`docs/protocols/LOCAL_MODEL_RUN.md`。
- 归因独立审查：`agents/reviews/model_first_run_review.md`；数值/轨迹独立验收：`agents/reviews/model_pilot_acceptance.md`，以其最终状态为准。
- `EXP-20261116-REVIEW-003` 已完成：独立核对 CPU/MPS 预检共 4 轮及 pilot 103 轮的 raw→parse→event、公开 messages、状态、种子和全部指标。接受可审计 pilot，研究/训练门禁仍未通过；最终 17 runs 完整性 0 errors。
