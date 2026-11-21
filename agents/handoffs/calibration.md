# Calibration agent 交接

日期：2026-11-16（Asia/Shanghai）。Owner：protocol agent。

## 文件与范围
- 新增 `toolloop_fix/calibrated_protocol.py`、`tests/test_calibrated_protocol.py`。
- 新增 `docs/protocols/CALIBRATED_PROTOCOL.md` 与本handoff。
- 未改harness/model_protocol/model_run/recorder/历史结果，未加载模型或安装依赖。

## API（已与root确认）
- `CALIBRATED_PROTOCOL_VERSION = qwen_xml_or_native_terminal_v1`
- `CALIBRATED_SCORE_VERSION = evidence_and_terminal_form_v1`
- `CALIBRATED_SYSTEM_PROMPT`、复用的`tool_schemas`
- `parse_calibrated_output(raw_text, token_limit_reached=False)`
- `messages_for_calibrated_view(view, decisions, extra_prompt='')`
- `score_calibrated_episode(case, final_snapshot, decisions, budget_exhausted)`

自然终止的action是透明内部stop marker，保留raw_text；parse的call=None、terminal_form=natural_text、is_terminal=true。可直接交给现有env.execute结束生命周期，但不能解释为模型真的调用stop_task。任何terminal之后消息helper拒绝续跑；root的B2时间线需要自建公开chronological transcript，不伪造tool返回。

## 评分输出keys
score_version、completable、tool_goal_attainment、exact_grounded_structured_completion、terminal_form、terminal_form_valid、unsupported_finish_wrapper、natural_termination_semantics_unknown、stopped_after_public_unavailable、exact_stop_reason_code、stop_reason_semantics_unknown、premature_stop_without_goal_evidence、budget_exhausted、legacy_strict_parse。

没有success/task_success/general false_success；自然语义unknown不能充当成功或失败语义标签。tool_goal_attainment是已取得目标证据，不等于最终答对任务。legacy仅旧parser诊断，不计算旧策略反事实得分。

## 验证
`PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -p test_calibrated_protocol.py -v`：18项通过，标准库、无模型调用。root负责集成后combined tests与离线校准的正式归档，之后再新跑共同协议下B0/B1/B2。

## 集成注意
- scorer可接受中间被blocked而没有对应env事件的decisions；它只看最后terminal决定及实际envhistory，不能从guard文字制造目标/不可恢复证据。
- 末尾terminal decision与snapshot terminal不同会报错；有terminal就不得继续生成。
- B0和B2公共systemprompt应逐字相同；B1仅extra_prompt追加；模型、工具、预算与显式case-seed计划相同。
- 原013自然终止之后的生成不能作为新协议运行结果；保留旧数字，离线只做输出分类诊断。
- 依据当前读取夹具设计，未承诺真实写任务或语义裁判能力，未通过训练门禁。
