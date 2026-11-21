# ITER-009 独立审计报告（纯 CPU 逐轮重放、防泄漏与云端溯源核验）

- **审计日期**：2026-11-20（Asia/Shanghai）
- **审计执行者**：`auditor9`（只读核验）
- **正式归档 Run**：`EXP-20261120-REVIEW-009R2`（`status=completed`, `exit_code=0`, `evidence_valid=true`；早期脚本接口报错 `REVIEW-009`、`REVIEW-009R1` 完整只读保留）
- **审计脚本与配置**：`scripts/audit_iter9_runs.py`、`configs/iter9_audit_replay.json`

---

## 1. 审计范围与零模型加载约束

本次审计覆盖 ITER-009 全部正式训练与模型评测产物：
1. **3 波 Colab L4 离线核验报告**：`EXP-20261120-070R1`（Wave 1，4 适配器）、`EXP-20261120-075`（Wave 2，4 适配器）、`EXP-20261120-089`（Wave 3，2 适配器），合计 **10 个 LoRA 适配器**。
2. **32 个完成态 `model_inference` Run**：
   - `eval24` 训后开发集（6 run）：`EXP-071..074`, `076..077`（`144` episodes）
   - G4 封闭集端到端（13 run）：`EXP-078..088`, `090..091`（`468` episodes）
   - G4 封闭集固定失败前缀（13 run）：`EXP-092..096`, `097R1`, `098..104`（`234` episodes）
   - 合计：**`846` 个真实模型推理 episode / `4,029` 轮真实生成**。
3. **零 ML 导入断言**：脚本运行前后检查 `sys.modules`，证实未导入 `torch`、`transformers`、`peft`、`trl`、`safetensors` 或 `accelerate`（`zero_ml_imports_verified: true`）。

---

## 2. 核验项与审计结论

| 核验维度 | 核验结果 | 实测指标 / 证据 |
|---|---|---|
| 核心环境不变性 | **PASS** | `toolloop_fix/harness.py` SHA256 恒等于 `02aa7a4a4b08e7a1ceac68b25183d957006ef5e3a8db4fb0609647e84b10f444` |
| 云端训练基座零漂移与释放 | **PASS** | 3 波 L4 均满足 `verified=true`、`owned_absent=true`、`base_weights_unchanged=true`、`fla_triton_enabled=true`，`10/10` 适配器 SHA256 与张量审计通过 |
| 逐轮确定性 CPU 重放 | **PASS** | `32/32` run、`846/846` episode、`4,029/4,029` 次生成全部通过 `run_lora_explicit_e2e_case` / `run_curated_prefix_case` 字段级一致重放，**`total_exact_replay_mismatches = 0`** |
| 隐藏状态与标签零泄漏 | **PASS** | 全部 `4,029` 条 `prompt_text` 零包含未公开 `hidden.value`、`hidden.valid_identifier` 或评测标签键；G4 清单 6 项零重叠断言全部为 `true` |
| Proposal §06 公平门禁 | **PASS** | 全部 26 个 LoRA 评测 run 均运行在 `comparison_arm == "B0"`、`base_weights_unchanged_on_load == true`；全部 30 个非 `B2` 评测 run 均满足 `guard_blocked_proposals == 0` |
| 统计比较器与全仓账本 | **PASS** | `EXP-20261120-105R1` 零模型加载完成 9 臂配对检验与诚实裁决；`EXP-20261120-106` `326/326` 测试通过；`validate_records.py --write-index` `123` run **`0` errors** |

---

## 3. 审计保留意见与边界记录（诚实披露）

1. **云端并发外部会话**：Wave 1/2/3 执行期间用户账号存在外部无关会话（如 `ego-gen8r2-super`），故 `original_set_unchanged` 在部分波次为 `false`；经 `lifecycle.jsonl` 逐行审计，控制器仅终止并核验释放了自有 `tlfix-*` 会话（`stop_ack.confirmed=true`、`owned_absent=true`），未触碰任何外部会话。
2. **历史失败与用户中断保留**：`EXP-063`、`070`、`097`（`status=interrupted`）、`105`、`REVIEW-009`、`REVIEW-009R1` 及 `COLAB-005..009, 011` 均按协议完整只读留档，不构成隐藏或篡改。
3. **样本与治理边界**：G4 封闭集为单采样种子配对（`g4_e2e_s1`, `g4_pfx_s1`）+ 3 训练种子稳定性检验，G2 治理层级为公开声明的三盲代理银标仲裁；相关边界已在 `FINAL_REPORT.md` 明确披露。
