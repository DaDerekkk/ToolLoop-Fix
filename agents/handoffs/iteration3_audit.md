# ITER-003 source_audit handoff

- Date：2026-11-16 Asia/Shanghai。
- Owner：`/root/source_audit`。
- Owned outputs only：`docs/research/PROTOCOL_CALIBRATION_AUDIT.md`、`agents/reviews/iteration3_design_review.md`、本文件。
- Task：本地协议校准与B1/B2实施前独立建议，随后只读复核实现；NO TRAINING。

## 已完成

读取README/PROGRESS/TASK_BOARD、SOP/EVALUATION_SPEC、ITER-002及协调者handoff，以及当前model_protocol/model_run/harness；结合已核验EXP-013 raw失败定位。向root、simulator、protocol及时发送控制语义、guard边界、预算、指标与显式seed建议。

核心建议：非空无控制标记自然语言EOS终止为unknown，不发明工具调用或格式重试；严格grounded completion保留，但结构化finish不自动解释为语义成功主张；stop行为与精确理由码分列。B1通用提示与B2公开重复guard各自对照同一校准B0；阻断提议不是执行结果，且计预算。旧013不覆盖，离线重解析不当反事实结果。

## 验证与限制

本次仅本地定向文件阅读与文档写入；未跑测试、未调用模型、未安装或访问网络。设计conditional accept，代码/运行验收尚待后续审查。实际采用与建议不同的细节须在review追加，而非悄悄宣称完全一致。

## 下一步

待实现者冻结新协议/guard/seed代码后，按iteration3_design_review的六项清单只读审查；运行结果由root执行、protocol独立验证，本agent聚焦语义/信息边界/因果归因。任何blocked提议或未知语义保留为独立观察，不生成训练标签。

## 实施审查追加

已只读审查新baselines、calibrated_protocol、calibrated_run和相关测试/三组配置。提出并回读关闭：R3-01新信息不能只看response payload（现绑定action+response且真实进展重置）；R3-02内部FORMAT_ERROR不能成为guard新工具信息或执行循环（现过滤actual tool history）；R3-03被阻断raw尾EOS不可在chat template重复渲染（现仅在dialogue去尾EOS，raw保留）。自然文本终止明确internal lifecycle，blocked与真实执行分开，seed重排稳定。

pre-run conditional accept已送root：归档联合测试、103行离线校准及官方tokenizer render检查通过后可执行三arm15dev探索。详细hash/边界见review。没有运行测试/模型，也未修改任何代码、配置或旧run。

## 归档门禁已通过

只读核验EXP-014的149tests OK和EXP-015的103行诊断/官方tokenizer1EOS检查；独立复算源generations+17旧manifest哈希全部一致。49条首个新terminal之后的旧输出不可解释为反事实新成绩。EXP-016冻结新模块hash匹配审查版本。设计/实现/校准门禁更新为有界accept，真实B0/B1/B2结果仍须后续核验；无训练授权或训练结论。

## 三arm结果审查完成

根线程追加授权审查文件 `agents/reviews/iteration3_results_review.md` 已写。016/017/018均有效完成；三arm工具目标证据3/12、精确结构化完成1/12，逐case相对B0均0胜12平0负。B2机械阻断2个提议、执行循环0/15，但重复提议仍2次；不能声称修复有效。

独立比对B0/B2共39轮seed、prompt文本/token、生成token及raw完全相同，覆盖首次block提议及13个无block案例全部31轮，未观察到干预前漂移。最高优先级待人工样本是018 generations第13行：在无OK证据且turn3被阻断后，turn4原文却声称任务成功；自动语义仍unknown，不改原分数或估计总体虚报率。完整边界、B1截断/协议失败、任务措辞歧义及后续非训练建议见结果审查。
