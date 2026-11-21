# 记录基础设施独立审查（修复前）

- 日期：2026-11-16（Asia/Shanghai）；reviewer：`/root/source_audit`。
- 范围：`scripts/record_run.py`、`scripts/validate_records.py`、`tests/test_records.py`。
- 本 reviewer 未编辑任何被审查脚本、配置、协议或测试；复现实验的工作目录全部位于 `/tmp`。
- 本文是修复前观察。协调者后续修复/复跑应使用新 run，不覆盖初始失败日志。

## 审查版本

| 文件 | SHA256 |
|---|---|
| record_run.py | `4bc28adabd5492e4f81d97b89ed9bd6ab858fa81b57d70b390608e0b509babdc` |
| validate_records.py | `e249478c1e0568a90f8d7f483c1abcb8c2b1729903b188cee54a8a34a4c15d25` |
| test_records.py | `b93733ae8311c9cf21bbc2a5c997d8bb5d86a3f36284e707c9fd24eb9f954ef9` |

## 实际执行

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_records.py -v
PYTHONDONTWRITEBYTECODE=1 python3 agents/reviews/recording_regression.py
```

- 原测试：5/5 通过。
- 独立回归：5 项中 1 通过、3 失败、1 异常；退出码 1。
- 可执行复现：`agents/reviews/recording_regression.py`；原始输出：`agents/reviews/recording_regression_initial.log`。
- 这些是工程反例，不是模型实验或模型故障。

## 可行动发现

### R1 / P1：快照与实际执行代码可能不一致

`record_run.py:77–91,110–120` 保存输入副本，却在 live 项目根目录执行；只做运行前后哈希比较。另一个 agent 在期间修改文件并恢复时，不会留下最终哈希差异。

复现：归档 `task.py` 内容打印 A；子进程修改绝对 live 路径为打印 B，通过相对路径执行后还原 A。结果：归档 A、stdout B、`evidence_valid=true`、changed list 为空、校验无错误。

最小修复方向：从归档输入的冻结 cwd 执行；在 copy 后及 run 后核对冻结输入，记录 execution mode；参数中的输入路径也要确实指向快照。明确这不是防恶意进程的沙箱或签名认证。已与协调者确认回归测试针对其他 agent 修改 live workspace，不要求证明任意自修改代码的执行语义。

### R2 / P2：配置/必要产物未强制纳入完整性校验

`validate_records.py:35–49` 只校验 outputs 已列项目，另检查文件存在；未独立校验 `config_sha256`。删除 `outputs['config.json']` 后修改 config 文件，仍返回无错误。

最小修复方向：强制必要产物有哈希记录；单独比对 config 哈希与对应输入；检查输出清单遗漏。此措施用于发现损坏/漏记，不构成能对抗同步篡改清单和文件的密码学证明。

### R3 / P2：manifest schema 不完整，错误类型可能直接中断审计

`validate_records.py:25–52` 接受未知 status/kind、遗漏 command/cwd/ended timestamp，且 `[]` manifest 引发未捕获的 AttributeError，无法继续报告其它 run。

最小修复方向：在取字段前检查对象和字段类型，校验枚举、结束状态和退出码关系，要求实际命令/执行位置/起止时间/环境信息；坏记录转为 errors，不崩溃。正在运行和准备阶段应保留记录但不能通过最终审计。

## 已有优点

- 排他创建 run 目录，原测试证明相同 ID 不覆盖。
- 非零退出被保留为 failed；接受状态默认 not_reviewed，没有把进程成功等同于研究成功。
- 普通已列文件篡改会被检测；持续到运行结束的输入更改会使证据无效。
- 快照数据与原始 stdout/stderr 为后续修复提供基础。

## 尚需声明的边界（不是本轮要求的大型改造）

- SIGKILL/机器掉电无法保证 finally 执行；当前 validator 能识别未完成状态，但恢复/收尾须写新记录。SIGTERM 和进程树终止没有专门验证，不应保证所有子孙进程都已结束。
- 初始输入清单只覆盖当前模拟器的约定路径。`data/raw`、`data/processed`、外部模型/依赖不自动归档；进入真实推理/训练前需显式登记数据与模型版本、依赖、seed、设备、精度及成本。现有 kind 枚举不代表这些链路已具备。
- 日志和命令参数会原样保存；未来不得把凭据放在命令行或配置中。未检查或打印环境变量值。
- 归档文件不是 OS 层只读；无可信外部签名。完整性结论以清单可信且无恶意写入为前提。

## 验收建议

先把此失败测试归档，再修复并复跑全部原测试及独立回归；只有针对新版本的复跑才能关闭 R1–R3。当前没有证据说明早期实际 smoke 已遭 transient mutation，因此不把可复现风险等同于已发生数据污染。

---

## v2 独立复核 — 2026-11-16（追加，不替换初始结果）

**结论：R1–R3 在本轮受信任本地模拟实验范围内已关闭，可交接。** 不等于真实模型接入或训练阶段已通过。

### 核验对象与方法

- 重新阅读 v2 记录器、校验器及 `check_replay.py`；本次未修改任何代码/归档。
- 阅读 `EXP-20261116-003` 的 manifest 和完整测试日志：实际归档结果为 **51 tests / OK / exit 0**，包含原记录测试及 5 项独立回归。
- 独立比对当前记录器、校验器、两份记录测试文件与 -003 输入快照：逐个 SHA256 相等，未以较新未测试代码替代验收版本。
- 执行 `PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_records.py`：初次 **8 runs / 0 errors**；并行新增 REVIEW-002 后再次核验为 **9 runs checked，errors=[]**。
- 未另跑模型实验；51 项测试来自已归档实际运行，本 reviewer 做了日志、代码及哈希独立复核，没有把“读日志”表述为自己重跑全部测试。

| 已复核文件 | v2 SHA256 |
|---|---|
| scripts/record_run.py | `543c5d10d7604e0cfce8d24f519cdc4623ef4e87547f7ed4abaf1d369a873ec3` |
| scripts/validate_records.py | `d40278a3f5c1c6cb90d5d9dbd6eb65bdceec10fab667ce6b39cabb97824d45d1` |
| tests/test_records.py | `8482c5a8619fddcb9734ca6a8751bf5f10bef036cf02924eda5c686c1fa96244` |
| tests/test_records_regression.py | `dbf8ec878e31d43d120f72eb6e74ef256c8797926c7fe4dd1fb21bda6f05ad3b` |

### 缺陷关闭依据

- **R1**：默认 cwd 为本 run 的 `inputs/`；copy 后验证输入内容，run 后验证快照文件清单/哈希。live workspace 的改动另行记录，不再把 live 根目录执行假定为快照执行。-003 中 live 临时修改并恢复、持续 live 修改、冻结输入修改的相关测试通过。
- **R2**：validator 校验配置独立哈希，并比对实际文件清单与 inputs/outputs 清单；遗漏配置哈希条目的反例被拒绝，独立回归通过。
- **R3**：manifest 对象、关键字段类型、状态/证据类型枚举、命令、退出码一致性已检查；非对象 manifest 报错而不触发原 AttributeError，独立回归通过。
- `artifact_contract` 区分 `synthetic_evaluation` 与仅命令日志的诊断；完整评测才强制三个评测产物。v1 按已记录命令推断，不要求诊断伪造不存在的轨迹文件。通过完整性审计的 failed run 仍是失败，未被改写成实验成功。

### 重放独立核验

| 归档重放 | 来源 | reviewer 实际检查 |
|---|---|---|
| EXP-20261116-005 | EXP-20261116-004 | source manifest 哈希一致；trajectories、case_results、metrics 的源文件与 replay 逐字节 SHA256 相同，且与 comparison 中双方哈希一致 |
| EXP-20261116-006 | EXP-20261116-002 | 同上；旧输入快照可重现旧结果，没有用当前修复代码覆盖旧证据 |

两份归档记录均为 exit 0、all_equal=true。这里支持的是**确定性脚本策略模拟重放**，不支持 GPU/随机模型的可复现性声明，也不证明旧版本评测逻辑正确。

### 最终边界

- 默认相对项目路径从快照执行；调用方仍需避免绝对 live 输入、未知外部工具或未登记依赖。没有恶意进程隔离、签名或远程认证，无法证明任意自修改程序的执行语义。
- 外部大模型权重、大型数据、系统环境不在小型项目快照中；真实推理/训练前必须另外固定版本与依赖并校验。
- SIGKILL、掉电、进程树中断等未完成 run 的恢复仍待明确；不能宣称所有崩溃均能自动完成归档。
- `evidence_valid` 是记录完整性/正常退出的工程标签，不是研究假设成立或科学效果验收标签。
