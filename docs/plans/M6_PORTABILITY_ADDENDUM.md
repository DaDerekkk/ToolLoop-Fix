# M6 迁移补充：路径与旧证据不改写

ITER-004；仅方案，未迁移/连接目标机，未安装或训练。与 `M6_24G_TRAINING_HANDOFF.md`、disabled `future_training_plan.json` 一起交接。

## 需要显式处理的可移植性缺口
当前真实配置的 model_path、外部清单路径，以及固定prefix来源的source_run/source_cases_path，均含当前M5的绝对路径。旧manifest还保存当时的cwd/source_workspace。因此**不能只复制目录再直接宣称全部命令可在另一用户名/路径运行**；不得为迁移修改历史manifest来迎合当前路径。

未来目标机获准准备时：
1. 保留一份只读完整原始归档（experiments、模型/清单、原始/派生数据、源码、配置、协议/agent交接/iterations）。逐文件SHA复制核对；不默认复制依赖机器/OS的`.venv`与运行时安装环境。
2. 新建迁移run与路径映射清单：original_absolute_path → target_absolute_path、源SHA/目标SHA、复制时间、实际硬件/OS/用户批准范围；不收集凭据或设备序列号，不自动云上传。
3. 先做只读归档inventory/hash核验。现有validate_records含cwd等绝对路径一致性假设；如路径变化，先实现/测试明确的**历史来源路径映射校验**，保留历史cwd而不是回写旧manifest。未完成这步不得声称“目标机已复现”。
4. 在目标机独立新建运行配置和模型manifest：model_id/revision/文件字节SHA保持；model_path换为新位置，记录original_manifest_sha256及迁移run。新manifest是派生文件、新SHA，不覆盖原清单。运行环境另测另记，不能把旧M5安装清单当目标机已安装证明。
5. prefix来源先映射到复制后的不可变run；保持source_manifest_sha256、原始生成/轨迹/fixtureSHA和行号。用同一构造脚本生成**新的迁移版prefixdataset**（只有路径provenance变化也会改变row/datasetSHA），保存新旧对应关系并重做全部来源重放/公共信息门禁，不编辑旧data/dev文件或旧实验。
6. 在目标机先跑标准库测试/数据回放，再独立真实推理；新device/runtime/path/后端要有新B0/B1/B2配对，不将同seed当跨设备逐token确定性保证。只更换绝对路径不能自动证明训练兼容。
7. 数据/评测/训练记录契约和明确训练授权仍是前置条件。本补充不启动训练，也不提供可自动执行的迁移或训练入口。

最保守的后续顺序：路径映射与完整性 → 目标机硬件/环境核验 → 同权重推理基线 → 数据与语义门禁 → 获准后短程LoRA可行性 → 公平训练比较。每一步新run并保留失败。
