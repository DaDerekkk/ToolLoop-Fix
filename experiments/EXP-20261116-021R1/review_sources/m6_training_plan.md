# ITER-004 — M6 / 24G 延期训练计划交接

Owner：source_audit；2026-11-16（Asia/Shanghai）。

## 交付与范围
- `docs/plans/M6_24G_TRAINING_HANDOFF.md`：硬件核验→离线复现→数据门禁→另获授权的 LoRA 探针→B3/B4/B5/P→封闭评估；内存算术、MLX 等价门禁、checkpoint/回滚。
- `configs/future_training_plan.json`：`plan_only=true`、`enabled=false`、无执行入口。目标机实际信息/训练测量/新库版本/训练数据保持 null 或 pending；不是可执行训练配置。
- 只改以上两文件及本交接；未改总进度、源码、旧配置、原实验/权重。

## 核心结论
1. M6 / 24G 未连接核验，不声称专属官方支持。现有 M5 / 16GiB 推理证据不证明训练可行。统一内存必须给系统留空间。
2. 优先保留官方未量化权重和现有 Transformers/PyTorch CPU/MPS 基线，LoRA 候选优先；全量 AdamW 文本状态估算约 8.41–11.21 GiB **尚不含激活/大词表 logits/reference/系统**，不能承诺全量 DPO 稳定。
3. 只读 safetensors 头：文本存储标量 752,393,024，实际存储 1,504,791,232 bytes；是文件头统计，不是 live 参数或峰值内存实测。完整数字和估算假设已入 JSON。
4. PEFT/TRL 官方有相关接口，但当前目标模型+MPS backward 未验证；MLX Qwen3.5 源码存在不等于版本、训练或 DPO 可用。若切后端必须做转换、模板/token/mask/采样、数值/基线重跑门禁。
5. 当前 42 项待审队列、旧 13 项队列和 15 个开发夹具都不训练就绪；禁止 unknown 自动负标、整队列对象喂模型、隐藏标签/测试信息泄漏。G1/G2 与新训练授权未过，不开始任何 backward/优化器步。

## 本次实际核验
- 读取 AGENTS、用户工作协议、README/PROGRESS/TASK_BOARD、SOP/评测规范、ITER-003/协调者交接、提案、现有模型/运行清单与后端关键代码。
- 标准库只读命令：读取权重前 8 字节与 59,840-byte JSON header；按 shape/data_offsets 统计文本/视觉/MTP 计数，并计算 P×2/4/12/16 和 batch1 FP32 logits 内存；退出 0，未加载模型。
- agent-reach 的 Jina Reader curl DNS 失败；只读 web fallback 核验 PyTorch 2.15 MPS/环境变量、PEFT LoRA（重定向 v0.21.0）、TRL DPOTrainer、MLX-LM LoRA 与 Qwen3.5 源码。MLX 猜测 DPO 路径 404，记未核实，不推断不存在。
- 没有安装、下载、远程连接、模型生成、反向传播或训练；没有新增生产依赖。

## 验收与下一步
- 交付后执行 JSON 语法与安全声明/引用路径检查；结果见本文件后续验收段。
- 协调者可将计划与只读 header 数字归档到新的 analysis run；原 run 不补写。
- 下一 agent 首先确认目标机/许可/G1/G2；先实现训练专属记录与 fail-closed 检查，再谈训练执行。当前 `enabled=false` 并非现有运行器已实现的安全检查。

### 已执行文档验收（2026-11-16）
标准库 `python3` 读取 JSON 并断言：plan_only=true、enabled=false、无执行入口/训练授权；硬件未连接、测量 null、训练集 null、PEFT 版本未选、主 P 无 guard；核对文档和 EXP-020 comparison/ITER-003 review/runtime freeze 文件存在；检查 header+数据字节=权重文件长度。**全部通过，退出 0**。这不是训练测试或模型实测，没有运行全套测试（只改文档与禁用计划 JSON）。

可复验最小命令（只读）：
```bash
python3 -m json.tool configs/future_training_plan.json >/dev/null
python3 -c 'import json; d=json.load(open("configs/future_training_plan.json")); assert d["plan_only"] and not d["enabled"] and not d["authorization"]["training_authorized"]; assert d["execution_entrypoint"] is None and not d["data"]["training_ready"]; print("disabled plan verified")'
```
