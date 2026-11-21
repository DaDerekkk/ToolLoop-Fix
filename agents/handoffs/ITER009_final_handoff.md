# ITER-009 最终项目交接（G1–G4 全流程闭环）

- **日期**：2026-11-20（Asia/Shanghai）
- **Owner**：`root`（协调汇总 `T019`–`T022` 与 `auditor9`）
- **最终状态**：**项目主线全部完成（G0–G4 全部通过并审计收官）**，云端已无任何自有 `tlfix-*` 会话，本地台账 `123` 个实验 `0` 错误，全仓 `326/326` 单元测试通过。

## 1. 核心交付入口
1. **最终中文研究报告**：`FINAL_REPORT.md`
2. **正式统计对比报告（JSON + Markdown）**：`experiments/EXP-20261120-105R1/g3_g4_comparison.{md,json}`
3. **独立纯 CPU 重放审计**：`experiments/EXP-20261120-REVIEW-009R2/iter9_replay_audit.json`、`agents/reviews/iteration9_final_review.md`
4. **最终回归测试归档**：`experiments/EXP-20261120-106/manifest.json`（`326/326` tests pass）
5. **迭代记录与证据索引**：`iterations/ITER-009_colab_l4_training_and_g4_holdout.md`、`data/raw/iteration9_evidence_index.json`

## 2. 全部 10 个已验真 LoRA 适配器资产位置
- Wave 1（`models/adapters/iter9_g3_wave1/`，审计 `EXP-20261120-070R1`）：`B3_s101`、`B4_s101`、`B5_s101`、`P_s101`
- Wave 2（`models/adapters/iter9_g3_wave2/`，审计 `EXP-20261120-075`）：`P_no_retry_s101`、`P_no_normal_s101`、`B3_s202`、`B3_s303`
- Wave 3（`models/adapters/iter9_g3_wave3/`，审计 `EXP-20261120-089`）：`P_s202`、`P_s303`

## 3. 实际核验命令与结果
- `.venv/bin/python scripts/validate_records.py --write-index` → `{"runs_checked": 123, "errors": []}`
- `EXP-20261120-106`（冻结快照全量测试）→ `Ran 326 tests — OK`
- `EXP-20261120-REVIEW-009R2` → `audit_passed: true`, `846/846` episodes, `4,029/4,029` generations, `0` mismatches, `10/10` Colab adapters verified, zero ML imports.

## 4. 若未来继续扩展的方向（不修改任何既有封闭集结果）
1. 在更大基座模型（如 `Qwen3.5-4B/9B`）或在线/保守 KL 正则偏好优化（如 SFT 冷启动 + 低学习率 DPO / GRPO）上复验 `B3` 与 `P` 的跨域权衡。
2. 如需更高统计精度，可基于 `g4_holdout_manifest_ITER009.json` 预登记的 `g4_e2e_s2/s3` 与 `g4_pfx_s2/s3` 追加多种子采样验证，或引入真实人类盲审签收升级 G2 治理层级。
