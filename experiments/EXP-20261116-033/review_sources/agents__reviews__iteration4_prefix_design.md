# ITER-004 固定前缀运行器独立设计审查

Reviewer：source_audit；2026-11-16（Asia/Shanghai）。范围：只读源码、实际已归档前缀、六个配置和标准库测试；只写本审查文件。**没有模型生成或训练。**

## 门禁结论

**当前：accept（仅有界固定前缀推理设计，见第 7 节归档读回）；初审为 conditional accept。** 两项先发现的防误用缺口已由 owner 修复并独立复核；本次 36 项 prefix 测试通过。实际 6 前缀 × 2 公开任务措辞 × 3 arm 的 36 个 prepared context 在快照、工具、历史、剩余预算方面一致。

这不是额外 GPU 启动授权，也不是研究效果验收。初审时要求在真实推理前完成并归档的三项条件如下（其后实际完成情况及严格范围见第 7 节）：
1. 语义复核/任务释义；agent 标签不得写成人工标签。
2. 冻结后的联合测试及 recorder 完整性检查。
3. 所有 36 个 context 的**本地官方 tokenizer/chat template** 渲染、token/EOS、上下文长度审计（计划 EXP-024；本审查初版尚未读取该结果）。

如上述任一失败则先修复/另建 run；不因本审查有条件通过而跳过。训练门禁没有变化，仍不训练。

## 1. 本次检查对象与哈希

| 对象 | 本次读取 SHA256 |
|---|---|
| `toolloop_fix/prefix_run.py` | `152e82f196a4b4023c653b7ff01583bfeeca9369f3b223d4bde0c08ad255380c` |
| `scripts/build_fixed_prefixes.py` | `4243e1e5dc884fe4930fd73533ba5e9da001fe9d7fe1f75624d3a7746b7c91c3` |
| `tests/test_prefix_run.py` | `2e4339aca1ce73f3b52180527c89910298f145e3b45bc35d7d5d1d2cdeca722e` |
| `tests/test_prefix_builder.py` | `98ebd4bd1417b63c95c5f93112ba382d12f528dce7469d2876faf8bf18fa930b` |
| `data/dev/fixed_prefix_cases_iter4.jsonl` | `503af73359679e57f7522b37334fba8b342f2a95e916bc55b5a095f4782a3441` |
| `configs/iteration4_prefix_build.json` | `56da8ccf5bd2e13cf11bdfe0f645f0881edc9b0234d3f311490d306378ead5a5` |

另读 `calibrated_protocol.py`、`calibrated_run.py`、`baselines.py`、`harness.py`，以及 `configs/prefix_{legacy,explicit}_b{0,1,2}.json`。EXP-022 为 `analysis`、completed、evidence_valid=true；其 dataset 产物与当前开发副本逐字节相同。未修改既有 run。

## 2. 实际源前缀核验（不是脚本伪造的模型输出）

| 前缀 | 源 generations.jsonl 行号（1-based） | 历史轮数 | 最后返回 | 前缀目标已达成 | 首次 fresh seed | 剩余工具/轮数 |
|---|---|---:|---|---|---:|---|
| pfx-orders-timeout | EXP-016:5 | 1 | TIMEOUT, retryable=true | false | 1001 | 11 / 15 |
| pfx-files-timeout | EXP-017:18 | 1 | TIMEOUT, retryable=true | false | 1033 | 11 / 15 |
| pfx-jobs-precondition | EXP-013:68–69 | 2 | PRECONDITION | false | 1066 | 10 / 14 |
| pfx-orders-invalid | EXP-016:3 | 1 | INVALID_IDENTIFIER | false | 1097 | 11 / 15 |
| pfx-orders-unavailable | EXP-018:10–11 | 2 | UNNECESSARY_CLARIFICATION；前一返回含 RESOURCE_UNAVAILABLE | false | 1130 | 10 / 14 |
| pfx-orders-ok | EXP-016:1 | 1 | OK：ORD-101 / shipped | true | 1161 | 11 / 15 |

来源完整 run ID 均为 `EXP-20261116-xxx`。总历史 8 工具轮；历史 input/output tokens 合计 5,918 / 288，来自原始生成记录，不是本次新推理。

当前来源保护链：选择配置 pin 原 manifest → runtime 检查 completed/valid/model_inference → 原 generations/trajectories/model_provenance 哈希与 manifest 一致 → 原 case 位于 source/inputs 且内容/哈希一致 → contiguous 从第一轮开始的 raw 生成与源事件完全一致 → raw 重解析为非终止实际工具调用 → 同环境重放 action/response/业务状态和可用的完整快照哈希 → supplied snapshot 必须等于重放结果。

runtime 另锁定源归档 `harness.py` 与当前实现相同。source EXP-013 原严格提示与其它来源不同，但仅移植合法已执行工具前缀；不复用原 source system prompt，不把旧后续输出当作新反事实结果。

**真实性边界**：这是本地已记录、已核验来源的可审计链，不是数字签名或抗恶意伪造证明。runtime 单测用临时 synthetic archive 模拟接口，不能把单测的 `model_inference` 标签当真实模型证据；实际来源的本轮只读回放与该单测分开报告。

## 3. 已检查的核心语义

### 3.1 起点与信息隔离
- 每个续跑分支创建新 `Environment`，对 prefix snapshot 深拷贝恢复；messages/tools/decisions/events 均独立复制。`prepared['env']` 不被实际续跑消耗。
- 模型只收生成用 messages/tools/seed；guard 只收实际工具公开 history。role、category、hidden snapshot、goal_at_prefix、score 等留在评测/记录层，不进入模型或 guard。
- prefix replay 使用原始模型动作，不补正确行动；既不重新采样历史动作，也不让历史 B2 规则逆向删除前缀事件。prefix_fresh_generations=0。

### 3.2 配对与公开措辞因素
- 六配置除 `policy_name`、`comparison_arm`、`wording_profile` 外完全相同，包括模型 revision/device/dtype、采样、完整 case order、case seed map、预算、协议及数据路径。
- B0 与 B2 初始系统/用户/历史消息相同；B1 只多通用 system guidance。wording 只改变 initial user content，其余历史与工具 schema 不变。
- explicit 增加公开 read-and-report 定义、要求如实报告值/标识符，并将 target 解释为 literal lookup label。**这是任务契约消歧干预，不只是同义句改写**；它没有给出隐藏答案，但可能改变原含糊“latest”等文案的理解，因此结果需分 wording 报告，不能全部归因于恢复方法。
- 所有新 arm/wording 都是对历史前缀的受控移植；不能声称新条件自然生成过该前缀。四个 recovery 及两个控制是已知开发条件选择，非随机故障样本、非自然失败发生率、非封闭集。

### 3.3 预算与 seeds
- total attempts/turns 从历史 count 起算；blocked/invalid 均占 continuation 尝试与轮次；terminal 不占工具预算但占轮次。到达任一总预算就停止，不补发免费最终回答。
- prefix 恰好耗尽预算时不调用模型、标 exhausted；超过预算拒绝。case seeds 为 1000/1032/1064/1096/1128/1160；fresh 使用 `case_seed + absolute_turns_so_far`，所以表中首 seed 有历史偏移。
- 各 condition 使用同一 seed schedule，但跨 prompt、设备或框架不保证随机输出逐字相同；本轮只有一个探索性 schedule，不能声称多种子稳定性。
- `input_tokens/output_tokens/generation_seconds` 仅 fresh continuation；historical_* 单列。预算仍按历史+续跑总量。汇总多臂 historical_* 时须注明为重复引用，不能当作本轮重新花费的生成成本。

### 3.4 循环、目标与 guard
- loops 在过滤后的实际 tool history 上检测，index<count 归 inherited，index≥count 归 continuation；跨边界新重复计入 continuation。继承循环分离由专用合成回归覆盖；实际 jobs 前缀是 find_job→LOCATED、get_result→PRECONDITION，不是重复 PRECONDITION，六条实际前缀均无 inherited loop。
- `new_goal_evidence = final_goal and not goal_at_prefix`；仅四个 recovery 入恢复分母。orders-ok 是最终报告控制，不可把已在 prefix 取得的 OK 再计成恢复收益；unavailable 另列停止控制。
- B2 只阻断当前真实 raw proposal：response=null、snapshot 不变、无 tool-role 假结果；向模型加入明确 runtime user notice，保留原 raw。不会自动选择恢复动作或替模型终止。单测覆盖边界阻断、合法 retry/poll 放行、invalid 与 blocked 混合预算。
- 自然终止语义仍 unknown；结构化 exact completion 与目标证据分列。stopped_after_public_unavailable 只表明“见过公开不可用后停止”，不证明停止解释诚实。减少 executed loop 或拿到 OK 不能单独证明任务完成。

## 4. 审查问题与闭环

| ID | 发现 | 处理与独立验证 | 状态 |
|---|---|---|---|
| R4-P01 | 首版 prepare_source 未与源归档锁定当前环境代码；builder 虽检查，未来消费旧数据仍可能漂移 | runtime 现在校验 source frozen harness + current harness SHA；`test_source_and_current_environment_code_must_match` 通过 | closed |
| R4-P02 | 首版 prepared 绑定 row/arm/wording，但没绑定总预算；改预算复用可能产生 stale remaining fields | total_tool_budget/total_turn_budget 写入 context 并比对；独立 actual-row 探针改 tool_budget=1 后在生成前抛 `ValueError: prepared prefix belongs to a different row or condition`；对应测试通过 | closed |
| R4-P03 | 协调者先发现 prepared env 跨分支共享消耗风险 | 当前每次新建 Environment+deepcopy restore；两次复用得到同结果，prepared env/context 不变的测试通过；非本 reviewer 首报 | closed |

没有要求改动当前评分口径、原始历史或模型输出。builder 自身不是完整 run validator；本次实际来源亦通过 runtime 的更严格验证。不要将 builder 的生成文件成功单独视作模型真实性/正式研究门禁通过。

## 5. 实际命令与结果

1. `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_prefix_builder.py' -v`：6 tests，OK。
2. `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_prefix*.py' -q`：36 tests，OK，退出 0（30 runtime + 6 builder）；无模型依赖加载或真实推理。
3. 标准库 inline diagnostic：`build_entry` ×6，再 `prepare_prefix` ×36；比对快照、public history、tools、messages 差异位置及 prefix goal/token 计数；PASS。
4. 对正式六配置/EXP-022 数据再次检查：仅三个允许字段不同；dataset bytes 与 archived output SHA 一致；每 case 六条件的 snapshot/public history/tools/remaining budgets 相同；PASS，退出 0。
5. actual-row prepared budget 变更探针使用 `NeverGenerate` 后端（调用即 AssertionError），结果在生成前 ValueError 拒绝；未发生生成。

一次组合 shell 命令在测试成功后读取尚未写出的 SOP 文件，末尾 sed 返回 file-not-found；不是单测失败，也没有用重试隐藏该情况。之后隔离执行的上述 36 测试命令退出 0。源码最终以本文件哈希和协调者归档版本对齐；如果之后变化，需重核。

### 可复验的实际数据/配置只读检查
```python
import hashlib, json
from pathlib import Path
from toolloop_fix.prefix_run import prepare_prefix, validate_config
from toolloop_fix.harness import state_hash
configs = [json.loads(p.read_text()) for p in sorted(Path('configs').glob('prefix_*_b[012].json'))]
assert len(configs) == 6
ignored = {'policy_name', 'comparison_arm', 'wording_profile'}
common = {k:v for k,v in configs[0].items() if k not in ignored}
for c in configs:
    validate_config(c)
    assert {k:v for k,v in c.items() if k not in ignored} == common
data = Path(common['cases_path']).read_bytes()
archive = Path('experiments/EXP-20261116-022/fixed_prefix_cases_iter4.jsonl')
manifest = json.loads((archive.parent/'manifest.json').read_text())
assert data == archive.read_bytes()
assert hashlib.sha256(data).hexdigest() == manifest['outputs'][archive.name]
for row in map(json.loads, data.splitlines()):
    ps = [prepare_prefix(row, c) for c in configs]
    for field in ('prefix_snapshot_sha256', 'prefix_public_history_sha256',
                  'remaining_tool_budget', 'remaining_turn_budget'):
        assert len({p['context'][field] for p in ps}) == 1
    assert len({state_hash(p['tools']) for p in ps}) == 1
print('PASS: 6 configs / 36 contexts / pinned archived dataset; no generation')
```

## 6. 对后续结果解释的约束

- 按 wording × arm 分报 4 recovery、1 report control、1 stop control；逐例配对比只给总百分比更重要。因 denominator 极小，不做泛化、统计显著或训练必要性结论。
- 分开报告 inherited loop、新 executed loop、重复 proposal、B2 block、new goal、exact finish、unknown 与预算/输出截断。对 early stop/无依据成功措辞的语义结论依旧要审原文，不能由 wrapper 自动代替。
- 精确 raw、实际环境 event、未执行 proposal 和 first intervention 前生成前缀可用于之后诊断；不把 guard 机械阻断数写成模型能力提升。
- 本审查不证明当前 42 项语义队列已变成训练数据，也不解除 G1/G2/人工复核/训练授权门禁。

## 7. 归档门禁结果追加（2026-11-16）

已实际读取并逐个重算以下 run 的全部 outputs SHA256，均与 manifest 一致，未新增模型调用：

| Run | 观察结果 | 严格解释 |
|---|---|---|
| EXP-20261116-021 | failed、exit=1、evidence_valid=false | review/handoff basename 冲突失败保留，未改成成功 |
| EXP-20261116-021R1 | completed、exit=0、evidence_valid=true；7 个 review artifacts，哈希检查通过 | 语义初审与延期训练方案归档；42 项仍为 agent_review_pending_human / training_eligible=false，不是人工 gold label |
| EXP-20261116-023 | completed、exit=0、evidence_valid=true；stderr 明确 `Ran 210 tests ... OK` | 工程测试，不是模型/训练收益 |
| EXP-20261116-024 | completed、exit=0、evidence_valid=true；36 context，最大 prompt=1092 tokens；model_loaded=false | 官方本地 tokenizer 渲染与输入控制检查，不是新模型生成或语义成功验证 |

EXP-023/024 的冻结 prefix runtime/builder/两个测试文件 SHA 与本审查第 1 节完全相同。已读 EXP-024 冻结 `scripts/check_prefix_contexts.py`：核验模型清单；`AutoTokenizer` 本地加载、trust_remote_code=false；用原 tools/messages、enable_thinking=false 渲染；检查工具/历史/起点/预算/公开措辞因素、上下文长度和指定私有元数据泄漏哨兵。1092+256 低于 8192 上限。

精确范围：EXP-024 没有单独输出“所有 EOS 数量正确”的指标，也没有穷尽信息流证明；其 actual checks 是官方模板正常渲染、长度和显式元数据/可见值检查。已有 append_feedback 尾 EOS 处理沿用已验的校准模块，本审查不扩大 024 能力主张。

语义文件明确 explicit 的 execute the resource 禁止新启动业务任务，不取消工具文档允许的读取/轮询；unknown 与结构化评分维持旧版本；source prefix 仍为条件移植。以上归档条件完成后，**本设计审查更新为 accept（仅有界固定前缀推理设计）**。后续生成结果仍须独立回放/定性审查，不支持训练、旧 run 重评分或泛化结论。

文字勘误：在结果抽查时复读 jobs 两条原事件，修正第 3.4 节初版将其举为“已有重复”的不准确例子。实际为 LOCATED→PRECONDITION；没有改数据、检测器或任何 run 分数。
