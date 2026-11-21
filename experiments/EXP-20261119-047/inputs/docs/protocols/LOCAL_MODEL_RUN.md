# 本地真实模型运行（2026-11-16）

## 已批准与已安装
用户已明确授权安装、下载权重并开始运行。环境仅在项目 `runtime/python` 与 `.venv`；不修改系统 Python。固定 Python 3.12.13、torch 2.15.0、transformers 5.23.0，完整依赖与实际安装命令见 `operations/SETUP-20261116-001`。

官方模型 `Qwen/Qwen3.5-0.8B` revision `2fc06364715b967f1860aea9cf38778875588b17` 的 9 个文件已下载并核验，共 1,769,916,415 bytes。模型为原始 safetensors、无量化；文件清单副本在 `operations/DOWNLOAD-20261116-001/model_manifest.json`。不自动从 main 取新权重。

目前优先用通过实测的本机 MPS/BF16（M5/16 GiB）；沙箱进程可能看不到 MPS，需正常申请主机 GPU 权限，不能偷偷改为 CPU。CPU/FP32 接入能运行，但本轮单案例 generate 合计约 220 秒；这个数包括 prompt 处理，不是纯 decode benchmark。未安装 CUDA/vLLM/FLA 扩展。

## 入口
1. 阅读 AGENTS、PROGRESS、最新 iteration/review；先确认环境、权重哈希与磁盘是否仍有效。
2. 复制并明确修改一份配置，不覆盖原运行配置。参考 `model_cpu_preflight.json`、`model_mps_preflight.json`、`model_mps_dev15.json`。`model_preflight.json` 是故意保留的未配置模板，不可直接运行。
3. 使用唯一 run ID；配置内两个 evidence 字段均须为 model_inference，提供固定 model/revision/backend/device/dtype/sampling、模型与安装清单的绝对路径+SHA。

在项目根目录执行：
```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
TOKENIZERS_PARALLELISM=false PYTORCH_ENABLE_MPS_FALLBACK=0 \
.venv/bin/python scripts/record_run.py \
  --id EXP-YYYYMMDD-agent-unique --kind model_inference \
  --config configs/model_mps_dev15.json \
  --hypothesis '填写本次真实问题，不默认训练有效' \
  --acceptance '填写预先验收与停损条件' --owner agent_name -- \
  "$PWD/.venv/bin/python" -m toolloop_fix.model_run --config '{config}' --output '{run_dir}'
```
GPU 权限由工具审批通道处理，不通过换路绕过。结束后运行 `python3 scripts/validate_records.py --write-index`。

## 实际协议与证据
- 原生官方聊天模板，非思考模式；Qwen XML 单调用解析。finish_task/stop_task 是自建评测包装，不是 Qwen 原生 benchmark。
- 严格解析，不凭文字猜参数；多调用/格式错误消耗尝试预算。调用后缀会拒绝，不把后缀重排到前缀。
- 从 public_view 构建完整消息，只向模型提供公开请求、工具说明和真实反馈；隐藏标签仅留给环境/评测器。
- 禁止脚本策略、教师或 guard 接管；此轮无训练。
- 模型重新核验全部权重/模板/配置 hash；加载语言权重存在 missing/mismatched 等异常则停止，不能用随机初始化参数继续。
- EOS 使用实际 tokenizer/model 给出的 248046/248044；采样参数与加性 presence penalty 实现在 provenance 明示。

## schema v3 记录契约
record_run 从冻结 inputs 执行；轻量外部清单逐份归档，GB 权重不复制。模型 run 必须有实际 runtime/provenance、原始 generations、逐例 trajectories/results 和 metrics。完成但契约缺失会使 evidence_valid=false，不能只凭进程退出 0 验收。

`evidence_valid=true` 表示这次记录满足工程契约，**不代表任务成功**。例如 CPU `EXP-20261116-011` 有完整可用证据但任务失败；不得改写为成功。失败时保留日志与已有的 partial_case/execution_events/model_failure。

## 明确限制与后续
- 当前仅 E2E、单种子、15 手写开发夹具；固定错误前缀模型续跑、约 200 开发样本、多种子、B1/B2、人工 detector 审查仍未完成。
- 种子按 case_index/turn 派生；改变案例顺序会改变种子。正式配对前应冻结显式 per-case 种子清单，不把重排后的运行当严格配对。
- CPU/MPS/dtype 的单样本差异不能证明某设备更准；本轮未证明跨设备数值一致性或稳定吞吐。
- 模型目录绝对路径变化需重新生成清单/配置；不改旧 run。安装 manifest 引用保留全包 freeze，但不提供环境克隆/恶意进程认证。
- 阶段门禁不变：先排除协议问题并确认真实故障，再冻结评测/样本，最后才考虑训练。
