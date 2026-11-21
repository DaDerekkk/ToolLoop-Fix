# 来源核验交接 — 2026-11-16

- owner：`/root/source_audit`；类别：`analysis`；状态：完成限定范围核验。
- 输出：`docs/research/SOURCE_AUDIT.md`、`sources/references.json`、本文件。
- 编辑范围外没有写入；没有安装、下载权重、调用模型、训练或更改环境。

## 已做验证

1. 阅读 `AGENTS.md`、完整提案文本、agent-reach 及 GitHub 路由说明。
2. `gh repo view Liquid4All/antidoom ...`：失败，CLI 不存在；转可用 web 只读工具。
3. 读取官方 AntiDoom 博客/README/依赖文件/commit 页，Qwen 模型卡及公开模型 API，Agentic-DPO HTML，Fission-GRPO 摘要，Feedback That Backfires 摘要，BFCL V3 评测说明及 DPO/LoRA/τ-bench 基础来源。
4. Python 标准库检查 JSON 可解析、12 个来源 ID 唯一、用户提案副本 SHA256 与记录一致：通过。
5. `agent-reach check-update`：更新查询 DNS 失败，本地显示 v1.5.0，未修改环境。

## 协调者需要采用的结论

- 相关核心引用真实可访问，但没有任何本项目模型效果证据。
- Qwen 的模型 revision 已读得；建议按文档中的文本非思考参数作合理起点，再记录后端实际生效值。
- AntiDoom 固定 commit 已观察，但实时 HEAD 未独立核实；不把缓存 commit 称最新。
- 官方模型卡的协议分为服务端 `qwen3_coder` 解析与 Qwen-Agent raw API；选择一条进行端到端验证，不混叠。
- 当前 FTPO upstream 的 GPU/Python 依赖与项目标准库模拟器不同。不要为了“推进”直接安装或执行整个训练脚本。
- 强 B1 需要处理失败动作的逐字呈现问题；不能只加一句“不要重复”，也不能盲目清空历史。
- ToolSandbox 未完整核验、BFCL 数据版本未冻结、真实模型接入/检测器人工复核/正式数据分割均待完成。

## 下一执行者

按 SOURCE_AUDIT 的接入预检计划先跑 B0 能力与协议检查；样本数/预算为计划，需经 record_run 生成实际记录。源网页内容和提案建议不是付款或安装授权。来源核验不是代码测试或算法复现。

## 追加：记录基础设施复核完成

- 独立审查已记录 `agents/reviews/recording_review.md`，保留修复前 SHA256、失败反例和日志。
- v2 复核关闭本轮 R1–R3：冻结 cwd 执行、配置/清单校验、manifest 类型与状态校验。查看 -003 归档 **51 tests / OK**，并确认当前相关代码与其快照哈希一致。
- reviewer 实际运行总台账校验：最终 **9 runs / 0 errors**（包含并行新增 REVIEW-002）；独立重算 -005、-006 三类重放产物哈希与 source manifest 哈希，均相等。
- 可交接本地模拟记录骨架；不表示模型实验、训练、外部大数据归档或恶意程序隔离已完成。SIGKILL/掉电恢复仍为限制。
