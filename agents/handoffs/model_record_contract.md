# Model record contract 交接

日期：2026-11-16（Asia/Shanghai）；owner：protocol agent。

## 已交付
- `scripts/record_run.py`：schema3，证据类别配置一致性，冻结案例清单，外部小型模型manifest快照与SHA，实际子进程解释器记录，模型产物门禁。
- `scripts/validate_records.py`：v1/v2兼容；v3配置/外部清单/模型provenance/输出类别检查；不依赖live权重位置来审计历史run。
- `tests/test_records_model.py`：仅标准库的fake契约测试，不加载任何模型。
- `agents/reviews/model_record_contract.md`：已与 root / simulator 对齐的字段、实测命令和限制。

## 验证
- 定向记录测试最终26项通过（含原有测试），历史9个run审计0错误。
- 未安装依赖、下载模型或执行真实推理；没有改现有tests、configs、harness或model runner。
- root 负责 combined tests 的正式归档与真实接入run；本 agent 当前文件可冻结。

## 联调要点
1. 模型命令直接用项目 `.venv/bin/python`；provenance.runtime.executable须由该进程取得。
2. Config：model六字段；sampling核心字段；evidence_kind/class一致；cases_path在frozen inputs；external_inputs含唯一model_manifest，可加runtime_install_manifest等命名JSON；path绝对、sha256准确。
3. Runner 输出 agreed nested provenance：model、sampling、runtime{executable,python_version,packages}、model_manifest_sha256、model_files_verified、chat_template_sha256；5类产物均标model_inference。
4. 子进程exit0不保证证据有效：缺产物或身份错误时 wrapper exit2、manifest child exit0、contract_errors非空。失败run不得补造结果。
5. 外部manifest≤2MiB；存档它，不复制GB权重或symlink权重。runner在加载前核验manifest全部文件；离线validator验证存档清单及provenance，不重复哈希live GB文件。
6. 历史模型证据检查依赖可信runner/清单，不是密码学attestation；真实运行后还需逐例内容/协议审查。

## 真实pilot独立验收补充
- 新分析run：`EXP-20261116-REVIEW-003`；review：`agents/reviews/model_pilot_acceptance.md`；完整结构化核对：该run下`model_pilot_audit.json`。
- 011/012共4轮与013全部103轮的raw→parse→event、公开messages、状态/结果、seed索引和统计均一致；真实版本/模板/清单与执行解释器一致。分析结束17个run校验0错误。
- 013严格指标：完成2/12、正常1/3、必要重试0/3、循环标记4/15、不合格finish4/15、预算耗尽5/15、严格正确停止0/3、解析错误29/103。
- accept仅限可审计真实接入/pilot。G1研究门禁需revise：29个解析错误都是无tool_call的自然语言输出；4个false_success中多例文本明确说明失败；2个不可完成案例已经stop但reason不满足严格枚举。优先共同格式/终止语义校准和正常能力检查，不能把这些严格包装指标直接归因模型固有缺陷或作为训练依据。
- root需将新增REVIEW-003纳入派生INDEX；本agent未改模型、源码、配置或已结束run。
