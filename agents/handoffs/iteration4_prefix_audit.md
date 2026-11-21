# ITER-004 fixed-prefix audit handoff

Owner：protocol；2026-11-16。状态：**审计脚本/14项测试完成并冻结；025只读诊断通过；六格正式审计待完整归档输入**。

## 修改范围

仅新增：
- `scripts/audit_prefix_runs.py`
- `tests/test_prefix_audit.py`
- 本交接

未修改 runtime、历史 run、模型、配置、依赖或旧测试；无 GPU/训练/新模型生成。

## 用法与输入

通过记录器执行新的 `analysis` run：

```text
python scripts/audit_prefix_runs.py --config {config} --output {run_dir}
```

配置中 `run_dirs` 为六项 `{ "path": "绝对run目录", "manifest_sha256": "..." }`，恰好覆盖 legacy/explicit × B0/B1/B2。正式 ITER-004 审计还应提供：

```json
{"preflight_context_run": {"path": "024的绝对run目录", "manifest_sha256": "024的manifest SHA256"}}
```

输出仅为新目录 `prefix_audit.json`，禁止覆盖已存在输出；通过 exit0/accepted=true，失败保留 accepted=false/error 并 exit1。未提供可选 preflight 引用时该字段为 null，不能声称核对过 tokenizer gate；本轮正式验收必须提供它。

## 核验范围

1. 六个 run 的 manifest pin、完成/证据有效状态、输入/输出全 inventory SHA、config SHA；八个推理核心源码与本审计回放版本一致。
2. 模型/revision/采样、实际 runtime/packages/interpreter、设备/dtype、模型外部清单、版本与显式 case seeds；六格 provenance 除 arm/wording/system prompt 字段外完全相同。只核对已留档权重清单，不重新哈希全部权重。
3. prefix 数据集/逐行 hash、原始来源完整 inventory；`prepare_prefix` 重新验证来源 manifest/generation/trajectory/fixture/harness 并从 raw 调用重演，不能相信输入 snapshot。
4. RecordedBackend 只返回原 backend 字段，不返回已保存 action/guard/score；只用 CPU 重跑 parser/guard/environment/scorer，精确对比 generations、events、prefix contexts、trajectories、results、metrics。
5. 逐生成 messages/tools、seed、prompt SHA、token count、有效采样设置；隐藏字段/未公开目标值/case ID 不出现在模型 prompt。原 prefix 与 fresh turn 连续且 seed 含 prefix offset。
6. 独立相加 prefix/continuation 调用、回合、blocked/invalid、历史与 fresh token/time；blocked 无工具结果、snapshot 不变且反馈为 user runtime notice。自然终止是内部生命周期，不补工具调用或继续重试。
7. inherited/new loop 分开、总预算包含 prefix、已取得 OK 的正常控制不是新恢复；恢复/完成/不可用控制各自分母、比率和成本相加再核对。
8. 六格同 prefix source/snapshot/history/剩余预算；历史消息和 tools 不变，wording 仅初始 user，B1 仅共同 system 的原通用提示，B0/B2 system 相同。
9. 提供024引用时，逐格/逐prefix将首轮 fresh prompt SHA/token count/seed、messages/tools/system SHA 和 prefix context 与已归档官方 tokenizer gate 比较；本审计不重新加载或编码 tokenizer。

## 实际验证

已执行：

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -p 'test_prefix_audit.py' -v
```

**14 tests passed（4.231s）**。假档案明确为 unit-test doubles，测试不加载真实模型。覆盖六格成功回放、缺格/重复格、manifest pin、伪造blocked结果、prefix预算、seed/turn偏移、prompt SHA、未公开值泄漏、有效采样设置、配置混杂、继承目标/成本归因、指标分母，以及 tokenizer preflight 首轮匹配/篡改拒绝。

另以标准库只读调用 `audit_run` 检查 `EXP-20261116-025`：**legacy B0，6案例/9次fresh generation，精确回放通过**。这只是执行前的诊断，没有往025补写文件；其输出未代替稍后的正式 analysis run。

## 证据边界与下一步

- 回放复用相同版本的 parser/guard/环境/评分器，是**可审计一致性**，不是独立语义真值、远程签名证明或模型效果复现。
- 自然语言保持 unknown；取得工具证据不等于最终答对；本审计不会调用语言裁判、修复答案或产生训练标签。
- 假档案测试不能证明真实模型效果。六格全部完成后，由协调者提供 pinned refs +024引用，再创建正式 REVIEW-005；在此之前不宣称36次实际续跑全部验收。
- 本审计不更改旧记录，未新训练、联网或增加依赖。后续修改必须在新测试/analysis run 留档，不回写已结束实验。
