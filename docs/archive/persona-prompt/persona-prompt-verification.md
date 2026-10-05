> 代码基线：e80404099946187a9def070c9e9ae05c45c14621
> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/persona-prompt-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 人物 Prompt 七层特性验收记录

> 状态：**验收记录**。阶段 1–6 全部合入；合入后经两路独立只读评审，发现的缺陷由四个修复分支按裁决修复并补测；此后两路测试加固与一次规格符合性修复合入。最终代码 `e804040` 通过全量测试、风格、覆盖率、文档与完整性门禁；四组独立语义突变共 46 个，除一个等价突变外全部被测试杀死。阶段 7（真实复验，只报告）未执行：它需要用户的模型账号。实现状态文档（台账 IS1）由最后一个提交单独更新，不在本记录的提交内。
> 日期：2026-09-28
> 依据：[人物 Prompt 七层特性规格](SPEC-persona-prompt.md)（v1.0，FROZEN）的[验收台账](SPEC-persona-prompt.md#验收台账)；阶段、文档同步作业与文件清单见[实施台账](research/persona-prompt-implementation-plan.md)。行为以规格为准，本记录只登记证据，不另定行为。

本记录是实施台账 E4-22、VF10、IX8 所指的特性验收记录：登记源码身份与提交、评审与修复、验收台账逐行的证据测试、独立语义突变审查、
全量门禁与覆盖率、代码与规格的出入、文档一致性与锚点核对，以及未执行的真实复验。验收台账各行的证据节点取自
`tests/persona_prompt_acceptance.py`，由 `tests/test_persona_prompt_acceptance.py` 逐行判定；本记录不另建一份清单。

## 源码身份

| 身份 | 固定值 |
| --- | --- |
| 最终代码提交 | `e80404099946187a9def070c9e9ae05c45c14621`（`e804040`）：阶段 1–6、全部评审修复、两路测试加固与规格符合性修复合入后的代码，全量测试与覆盖率门禁的被测源码 |
| 覆盖率报告提交 | `95a4447e98b062dc36fa2a45ec226e39f4457bb2`（`95a4447`），只含 `docs/dev/coverage-report.json`；此后到本记录的提交只改文档 |
| 实施基线 | `c3b24da3dfeda289c0b9fd7636dd908cd8dae2d2`（`c3b24da`，本特性实施前的主线提交）；规格与台账中的代码行号取 `3ea5b06`（代码与 `bda42ee` 相同） |
| 规格冻结 | `0f62568`（文档分支，经 `eeaabd7` 合入特性分支 `persona-prompt-seven-layers`），规格 v1.0 |
| AS2 | 锁定提交 `8768e14`，未改动；本机 `vendor/agentsociety2` 子模块未检出，源码取自锁定提交的解包副本 |
| 覆盖率快照 | `snapshot_id` 为 `ce9092b7-c81e-45fe-9046-395d20212185`，`manifest_hash` 为 `350dcc52460e04fccdcb5d41a39bfe6163646cd8a3bf6498864642309a379a0a`，`source_identity` 为 `e804040` 的完整哈希，生成于 `2026-09-28T15:39:41+00:00` |
| 数据集仓库 `WorldMock-Dataset` | 分支 `persona-prompt-seven-layers` 的 `c9c7c2c`（未推送）：白领数据集场景中要收货的同事联系人（王磊、周凯、刘洋）补 `receive_location_id`（办公室），与仓库场景同批（规格第七章「版本」）；变更的场景经本仓库场景加载器加载通过 |

`git log --first-parent c3b24da..95a4447` 的提交，按合入顺序：

| 提交 | 阶段 | 内容 |
| --- | --- | --- |
| `316909b`、`2bc6631`、`6d33ca6`、`1a2df70`、`eeaabd7`、`9315f1e` | 冻结与文档同步 | 六个文档分支合入：规格冻结、实施计划改为实施台账、全部【冻】作业，以及各阶段【同】作业的文档部分（接口合同 C1–C30、C32） |
| `9bec1e5`、`395811f`、`2dd626e` | 1 七层请求与单一行动阶段 | 环境侧层数据、宿主侧七层请求与决策事件、下游读者三个工作包；世界结构版本 8 |
| `d93a9bd` | 5（前半） | 恢复不越过合同截止；购买事实导出 `purchases.jsonl` |
| `e756ea7`、`8fd6503`、`d8cc34d`、`180cb39`、`76db6f9`、`5fa4100`、`431623f`、`6f4f982` | 1 集成修复 | 重放不记已读、调用记录带受理时的结果、距截止一分钟内的恢复标不可用、参数描述只写用途、旧运行的自述请求标「其他调用·未解析」、代码注释改指新标题、测试对齐合并后的形状、调用记录取请求的最新结果 |
| `1e3c948` | 5（后半） | 逾期任务保留、删 `wait_only`、跨决策预算耗尽 |
| `c1fd2af` | 2 同秒续接 | 续接链重建、tool 消息 `{result, changes, events}`、预算第 0 步、开关与 `scripts/inspect_run.py continuation` |
| `9bf7d92` | 4B | 指名购买、进食开吃份量与小数份、收尾结果 |
| `34f3d98` | 3 记忆层与人设层 | 记忆层派生、人设层、`subject`、只读问答快照、零条记忆守卫 |
| `49d577c` | 4A | `from`、`to`、给予、联系人收货与往来、代点关联 |
| `c03843c`、`4c95fec`、`64c3876` | 6 门禁 | 删去七层后不可达的代码；补测覆盖率门禁标出的防护与回退路径；重新生成覆盖率报告 |
| `ab974a4`、`138ec72`、`55fcf12` | 6 突变审查 | M1、M3、M4 的补测合入（M2 没有存活突变，无提交） |
| `bf5594f`、`d06aab7`、`2c878ab`、`b8c5294` | 评审修复 | F2、F3（含验收登记表与无延期守卫）、F4、F1 合入 |
| `4ab59eb` | 评审修复 | 撤回 F3 的路程菜单改动：冻结的时间与能量规格规定途中沿用出发地菜单，人物 Prompt 规格没有修订这一条 |
| `11fb147` | 评审修复 | `render_card` 保留自己的时刻写法，输出与 `c3b24da` 逐字节相同（规格「`render_card` 不变」），有黄金用例 |
| `8e4d90e` | 清理 | 删去 `progress_layer.result_object` 与过时的登记项 |
| `f798ea7` | 6 | 合入后刷新验收登记表（279 个节点） |
| `da0d393` | 6 | 在 `f798ea7` 上重新生成覆盖率报告 |
| `234af02` | 测试加固 T2 | 走真实路径的续接、进度层、记忆层与引擎测试：带记忆查询与协议纠正轮的决策逐字节续接，三决策链中关系、偏好、暂停活动与身体的变更叠加，缺回复的请求断链，带干预的重放继承开关；同秒混合记录的顺序；记忆层 8000 字窗口的裁剪与跨零点的停留；暂停的进食结束、指名购买遇关门与余额不足、按跨日消息 `answering`、真实运行的 `purchases.jsonl`；新节点登记进验收登记表 |
| `d27f4b0` | 测试加固 T1 | 可用性改为逐个提交工具层列出的每个候选、经宿主校验与 LifeEnv 受理的性质测试；真实两人运行逐个请求检查唯一出现与可读性；全天运行逐个行动请求检查系统层字节、工具列表与各用户层；修复 `src/worldmock/life/work.py` 合同剩余把 60 秒的要求写成「0.0166667 小时」，要求工时至多两位小数（规格「通用格式」），回归用例 `tests/life/test_work_contracts.py::test_a_one_minute_requirement_is_written_as_a_readable_phrase` |
| `7b05cd6` | 8 实现后文档 | 现行文档按最终代码核对（接口合同、冻结规格、设计文档、验收标准、实验与手册）；阶段 8 除 IS1 外的【后】作业；本记录首版 |
| `e804040` | 规格符合性修复 | 「代码与规格的出入」所列已修复的七项：目标回看名、按记忆层活动名回看、受限轮收尾阶段的可用性、`next_step` 为 `null`、购买结果不复述方式、门店来源标记、两项同时结束的浏览结果，每项有回归用例 |
| `95a4447` | 6 | 在 `e804040` 上重新生成覆盖率报告 |

## 评审与修复

阶段 1–5 全部合入后，两路独立只读评审分别对照规格第一至五、七章（评审 B）与验收台账逐行（评审 A）核查实际代码。
发现的缺陷按编排方裁决分给四个修复分支，每项修复都补了改回旧代码即失败的行为测试：

| 修复 | 内容 |
| --- | --- |
| F1 记忆层与会话归属 | session 区间按边界提交识别（一次提交是事件序号连续、`state_version_before` 与 `state_version_after` 相同的一组事件），进度层下界与记忆层区间共用一个判定，同一事件只在两层之一；活动句按活动结束的提交归属，到达的出行归路程、实验截止截断的活动也写句子；交谈邀请不写 `settled` 句；零点计划输入带暂停区间；截止预警与唤醒共用常量；`answering` 只绑定进度层消息与记忆层未回复的消息；同秒暂停按事件顺序；进食进度只写「已吃 X 份，剩 Y 份」 |
| F2 人设层、收货人与指名购买 | 同世界全部其他人物的私有绑定 `person_bindings`，用于 relationship 的对象校验与 `to` 解析（给予不同处以 `recipient_present` 拒绝，配送送到对方所在地点）；仓库场景中与同世界人物重复的联系人 `receive_location_id` 为 `null`；零点之后的作息窗口写对；只凭看法出现的人物 `role` 为 `null`；完整标签的 `obtain` 带 `from` 以 `ACTION_REQUEST_INVALID` 拒绝；重名门店全部列出；`RECIPIENT_NOT_FOUND` 走业务拒绝；给予的可用性与给予拒绝同一判定 |
| F3 公开结果、回看与工具层 | 读完邮件的结果带 `details.mail`；回看的 summary 仍在记忆层时只写活动名与「已在记忆层」；`*_at` 为带时区 ISO；回看不逐字段标「未经核验」；技能不够的任务不列；恢复结果带 `details.planned`；会话名不可回看 |
| F4 下游与数据 | dreaming 窗口附实际请求的层 JSON（`input.decision_layers`、`provenance.decision_layer_sources`）；五个清单入口共用一个校验函数；`scripts/inspect_run.py` 对特性之前的运行报告开关为 `null`；查看器旧模板解析、续接前缀回复正文标「未经核验」、容量第 0 步；数据集场景补 `receive_location_id` |

规格没有写到的实现细节按接口合同草案（编排方的 contract）第 5 节的决定落地，并已写进现行文档：派生的截止与提醒记录排在同秒事件记录之前、
结果 `details` 的键闭集与形状、受理与可用性共用拒绝原文、预算逐条去 `self_note` 并逐段折叠、续接开关冻结进人物配置、已读保留受限轮与待收尾轮次的既有排除。

## 全量门禁与覆盖率

| 门禁 | 结果 |
| --- | --- |
| 全量测试 | 在 `e804040` 的代码上（`95a4447` 只多覆盖率报告），一次调用 `python -m pytest tests`：4,295 passed、8 skipped、0 failed、25 warnings（警告数与实施前基线相同），用时 1:04:15；覆盖率下的八个分片合计同为 4,295 passed、8 skipped、0 failed。跳过都是既有的平台或产物条件跳过：TextWorld 在锁定文件中没有 Windows wheel 1 项、Windows 账户没有符号链接权限 3 项、资金锚定运行产物不在本机 4 项；本特性新增用例无跳过。实施前基线（`3ea5b06`，代码同 `c3b24da`）为 3,983 passed、8 skipped、1 failed，失败的是查看器回归用例（工作副本 CRLF 行尾），现已通过 |
| 风格门禁 | `tests/test_style_gate.py::test_style_gate_admits_no_new_violations` 通过，没有新增违规 |
| 覆盖率门禁 | 分支覆盖率 94.88%（covered=7467，missing=403，total=7870；阈值 85.00），未覆盖公共接口 0，`passed` 为 true；报告随 `95a4447` 提交，口径与基线见[覆盖率门禁口径与基线](coverage-baseline.md) |
| 文档门禁 | `e804040`：扫描 128 份文档，0 处断链；本记录所在提交：见「文档一致性」 |
| 完整性守卫 | `tests/test_persona_prompt_acceptance.py` 25 项通过：验收台账 22 行、第六章 12 行与裁决 #21–#38 都有登记节点，318 个不重复节点都解析到会被收集、未被跳过的测试函数；本特性新增或改动的 71 个生产文件与 26 个新增测试文件（含登记表与守卫自身）没有延期标记，Python 文件没有空壳函数 |

本机 `uv run` 不可用（AS2 子模块未检出，`uv` 找不到可编辑依赖），测试一律用虚拟环境解释器执行，查看器回归经 VS Code 自带的 Electron 以 Node 24 运行。
全量测试在主工作区以一次 `python -m pytest tests` 调用执行；覆盖率另在主工作区分八个分片（至多三个并行）执行 `python -m coverage run -p -m pytest`，
分片合计与一次调用相同；随后 `coverage combine`，再以分片的测试退出码与虚拟环境解释器的 `coverage json` 运行
`scripts/coverage_gate.py` 的判定。报告中的 `command` 字段仍是脚本常量 `uv run coverage run -m pytest -q`。查看器的复验另见
[运行查看器上下文解析验证](run-viewer-context-verification.md)「人物 Prompt 七层实现后复验」。

## 验收台账逐行登记

下表逐行列出 `tests/persona_prompt_acceptance.py` 为规格「验收台账」登记的证据节点，全部在上节的全量测试中通过。
「真实复验（只报告）」一行登记的是它所用的只读子命令的测试，真实运行本身未执行（见「真实复验（阶段 7，只报告）」）；
「文档一致性」一行除登记的文档门禁测试外，一次性核对的结果见本记录「文档一致性」；「工程门禁」一行的门禁结果见上节。
「程序错误」一行登记第六章程序错误表的全部回归用例，逐行列在其后的第六章表中。

| 契约点 | 节点数 | 证据测试（按文件分组） |
| --- | --- | --- |
| 七层请求 | 25 | `tests/integration/test_seven_layer_request.py`：`test_user_content_is_one_json_array_in_the_fixed_layer_order`、`test_system_layers_persona_memory_and_tools_validate_against_the_layer_schemas`；`tests/integration/test_life_agent_protocol.py`：`test_react_messages_are_system_layer_and_six_layer_user`、`test_how_to_live_a_day_is_one_resident_section_of_both_decision_stages`、`test_blacklisted_observation_fields_never_enter_messages`、`test_session_names_are_not_recall_names`；`tests/integration/test_grounded_evidence.py`：`test_model_request_persists_exact_grounded_tools`；`tests/integration/test_conclude_memories.py`：`test_nothing_sent_to_the_person_model_carries_an_internal_identity`；`tests/life/test_prompt_layers.py`：`test_schema_enums_are_literal_copies_of_the_source_closed_sets`、`test_memory_layer_and_persona_views_are_empty_at_run_start`、`test_env_layers_validate_through_purchase_rejection_pause_travel_and_arrival`；`tests/life/test_revision_contract.py`：`test_public_observation_excludes_private_fields_and_supply_expires`；`tests/life/test_grounded_observation.py`：`test_observation_keeps_current_time_without_plan_conversion_anchors`；`tests/life/test_readable.py`：`test_iso_time_and_local_date_use_the_scene_timezone_with_offset`、`test_duration_phrase_rounds_minutes_then_half_hours`、`test_money_and_portion_phrases_keep_at_most_two_decimals`；`tests/life/test_progress_layer_projection.py`：`test_recall_reads_task_and_activity_without_identities`；`tests/life/test_location_sessions.py`：`test_recall_by_task_and_activity_names_reaches_the_next_observation`；`tests/life/test_todo_layer.py`：`test_same_second_pauses_from_two_decisions_follow_the_pause_event_order`；`tests/integration/test_unique_presence_run.py`：`test_every_request_of_a_multi_state_run_presents_each_item_once`、`test_the_presence_and_readability_checks_have_teeth`；`tests/integration/test_dayrun_complete_days.py`：`test_complete_two_person_day_uses_real_tools_memory_and_restoration`；`tests/life/test_work_contracts.py`：`test_a_one_minute_requirement_is_written_as_a_readable_phrase`；`tests/integration/test_observation_agent_seam.py`：`test_goal_recall_names_are_the_goal_texts_written_in_the_todo_layer`；`tests/life/test_tool_layer.py`：`test_each_store_product_target_carries_its_source_marker_once` |
| 唯一出现 | 18 | `tests/life/test_prompt_layers.py`：`test_direct_message_and_invite_text_appear_only_in_the_progress_layer`、`test_paused_activity_progress_appears_only_in_the_todo_layer`、`test_work_contract_appears_only_in_the_task_target`、`test_paused_work_does_not_repeat_the_contract_in_the_todo_layer`、`test_browse_supplies_live_in_the_tool_layer_and_the_result_lists_names_only`、`test_just_ended_browse_target_states_the_end_reason_without_the_browse_result`、`test_a_browse_ending_with_the_other_activity_of_its_request_is_listed_and_pinned_once`、`test_a_trip_ending_with_its_companion_leaves_the_arrival_to_the_memory_layer`；`tests/life/test_progress_events.py`：`test_conclude_arguments_omit_relationship_and_preference_memories`、`test_successful_memory_query_is_recorded_with_its_count_and_failures_are_not`；`tests/integration/test_conclude_memories.py`：`test_memory_queries_and_the_reply_body_reach_the_next_progress_layer`；`tests/life/test_memory_layer.py`：`test_departure_and_arrival_attribute_same_second_calls_to_the_right_session`、`test_recalled_summary_still_in_the_memory_layer_is_not_repeated`；`tests/life/test_location_sessions.py`：`test_self_report_stays_out_of_the_tool_layer_and_is_only_a_call_argument`；`tests/integration/test_mail_read_result.py`：`test_the_mail_body_appears_once_in_the_request_as_the_read_result`；`tests/integration/test_unique_presence_run.py`：`test_every_request_of_a_multi_state_run_presents_each_item_once`、`test_the_presence_and_readability_checks_have_teeth`；`tests/integration/test_dayrun_complete_days.py`：`test_complete_two_person_day_uses_real_tools_memory_and_restoration` |
| 系统层与工具 | 11 | `tests/integration/test_seven_layer_request.py`：`test_system_layer_is_byte_identical_across_turns_and_decisions`、`test_system_layers_are_the_contract_registration_verbatim`、`test_native_tools_are_stable_purpose_only_and_drop_memory_only_on_the_last_turn`；`tests/integration/test_same_second_continuation.py`：`test_the_continued_request_starts_with_the_previous_request_byte_for_byte`；`tests/integration/test_interaction_menu.py`：`test_actual_tool_schema_is_stable_whatever_the_menu`；`tests/integration/test_agent_branch_coverage.py`：`test_restricted_rounds_do_not_narrow_the_host_tool_surface`；`tests/integration/test_tool_description_references.py`：`test_every_native_tool_has_a_purpose_only_description`、`test_interaction_rules_live_only_in_the_system_layer`、`test_description_scan_has_teeth`、`test_parameter_descriptions_are_purpose_only`；`tests/integration/test_dayrun_complete_days.py`：`test_complete_two_person_day_uses_real_tools_memory_and_restoration` |
| 单一行动阶段 | 10 | `tests/integration/test_seven_layer_request.py`：`test_a_decision_without_memory_queries_sends_exactly_one_request_and_no_prelude`、`test_decision_started_is_committed_with_and_before_the_first_action_request`、`test_non_action_requests_leave_the_start_to_the_engine_driven_fallback`、`test_abort_flushes_the_pending_start_without_model_evidence`；`tests/footprints/test_event_fields.py`：`test_decision_started_data_is_the_closed_four_field_set`、`test_retired_decision_statement_field_is_rejected_on_event_drafts`；`tests/life/test_engine_branch_coverage.py`：`test_first_action_request_pops_the_pending_start_once`；`tests/integration/test_agent_branch_coverage.py`：`test_tool_messages_follow_memory_queries_only`；`tests/integration/test_life_agent_protocol.py`：`test_native_react_submits_one_host_bound_life_action`；`tests/integration/test_decision_context_protocol.py`：`test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user` |
| 回复正文 | 8 | `tests/integration/test_seven_layer_request.py`：`test_self_note_is_the_latest_reply_body_of_the_decision`、`test_self_note_ignores_other_decisions_and_failed_calls`；`tests/life/test_engine_branch_coverage.py`：`test_response_self_note_is_the_stripped_reply_body_or_none`；`tests/life/test_progress_events.py`：`test_self_note_is_copied_from_the_request_and_omitted_when_empty`；`tests/integration/test_conclude_memories.py`：`test_memory_queries_and_the_reply_body_reach_the_next_progress_layer`；`tests/integration/test_decision_context_roundtrip.py`：`test_real_context_roundtrip_preserves_evidence_and_delays_results`；`tests/life/test_memory_layer.py`：`test_reply_body_stays_in_its_session_progress_and_never_enters_the_memory_layer`、`test_reply_bodies_stay_in_their_session_and_never_reach_the_memory_layer` |
| 同秒续接 | 22 | `tests/integration/test_same_second_continuation.py`：`test_the_continued_request_starts_with_the_previous_request_byte_for_byte`、`test_a_three_decision_chain_keeps_every_prefix_and_reports_each_change_once`、`test_another_persons_same_second_message_appears_once_in_the_whole_request`、`test_a_message_already_returned_in_the_chain_is_not_repeated_by_a_later_tool_message`、`test_after_time_advances_the_request_has_no_native_turns_or_earlier_thinking`、`test_a_same_second_departure_breaks_the_chain`、`test_a_counterfactual_branch_breaks_the_chain`、`test_missing_thinking_breaks_the_chain`、`test_missing_model_evidence_breaks_the_chain`、`test_recorded_evidence_decides_whether_the_chain_holds`、`test_a_plain_resume_from_a_mid_second_checkpoint_sends_the_same_request`、`test_switch_off_every_decisions_first_request_has_exactly_two_messages`、`test_the_switch_is_frozen_into_the_run_manifest_and_the_agent_profile`、`test_resume_inherits_the_parent_switch_and_rejects_a_diverged_profile`、`test_the_pre_send_check_fails_when_the_current_decision_lacks_thinking`、`test_the_pre_send_check_breaks_a_chain_whose_assistant_lacks_thinking`、`test_a_decision_with_memory_and_correction_turns_is_continued_byte_for_byte`、`test_changes_overlay_relationships_and_preferences_so_later_tool_messages_do_not_repeat_them`、`test_real_pause_and_body_changes_reach_the_next_tool_message_once`、`test_a_recorded_request_without_its_response_breaks_the_chain`、`test_replay_with_an_intervention_inherits_the_parent_switch`、`test_a_real_continuation_run_passes_delivery_windows_and_the_observation_gate` |
| 可用性 | 25 | `tests/life/test_tool_layer.py`：`test_every_listed_item_matches_admission_at_run_start`、`test_pending_conclusion_marks_everything_but_conclusion_unavailable`、`test_restricted_round_marks_closed_items_with_the_same_moment_reason`、`test_road_activity_cannot_be_paused_and_matches_admission`、`test_held_elsewhere_and_paused_elsewhere_are_listed_with_reasons`、`test_driving_marks_messages_and_submission_unavailable_with_the_admission_reason`、`test_give_availability_follows_whether_someone_can_be_handed_to`、`test_submit_while_working_says_finish_the_work_first`、`test_resume_within_a_minute_of_the_deadline_is_unavailable_with_the_admission_reason`、`test_resume_or_conclude_stage_opens_only_resuming_and_ending`、`test_conclude_pending_stage_opens_only_the_conclusion`、`test_conclude_pending_stage_with_two_pending_opens_each_conclusion`、`test_closed_sold_out_and_unaffordable_supplies_are_listed_and_refused_alike`、`test_a_co_present_person_asleep_can_be_messaged_but_not_talked_to`、`test_overdue_task_stays_listed_and_its_submission_is_refused_alike`、`test_permanently_impossible_actions_are_not_listed_and_the_host_corrects_them`；`tests/life/test_same_moment_decisions.py`：`test_restricted_observation_marks_closed_actions_unavailable`、`test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three`；`tests/integration/test_agent_branch_coverage.py`：`test_restricted_rounds_do_not_narrow_the_host_tool_surface`、`test_visible_objects_freeze_verb_way_and_bound_internal_action`；`tests/integration/test_life_agent_protocol.py`：`test_incomplete_work_stays_submittable_for_business_rejection`；`tests/life/test_work_contracts.py`：`test_a_task_beyond_the_persons_skill_is_not_listed`；`tests/life/test_parallel_admission.py`：`test_en_route_person_is_nowhere_with_nothing_at_hand`；`tests/life/test_giving.py`：`test_give_is_listed_available_only_with_someone_to_hand_to`；`tests/life/test_interaction_menu_runtime.py`：`test_availability_judges_the_menu_first_with_the_admission_reason` |
| 进度层 | 21 | `tests/life/test_progress_events.py`：`test_run_start_session_is_time_bound_and_keeps_every_result`、`test_started_activity_record_shows_the_latest_result_of_the_request`、`test_same_second_departure_opens_a_session_bounded_by_its_result`、`test_arrival_session_is_time_bound_and_excludes_the_departure`、`test_en_route_conclusion_opens_a_session_bounded_by_the_conclusion_result`、`test_direct_message_is_presented_in_full_and_marked_presented`、`test_delivery_appears_once_with_order_label_and_message`、`test_task_deadline_records_due_soon_and_expired_once_each`、`test_plan_reminder_appears_once_and_precedes_same_second_events`、`test_records_after_gives_the_progress_records_whose_sources_follow_the_bound`、`test_same_second_deadline_reminder_arrivals_and_call_keep_a_stable_order`、`test_the_advancing_clock_derives_due_soon_reminder_and_expiry_once_each`、`test_a_rejection_without_a_next_step_writes_null`；`tests/integration/test_same_second_continuation.py`：`test_another_persons_same_second_message_appears_once_in_the_whole_request`；`tests/life/test_same_moment_decisions.py`：`test_same_second_results_stay_in_the_progress_layer`、`test_purchase_result_names_the_order_in_the_progress_layer`；`tests/footprints/test_store.py`：`test_action_request_cutoff_counts_only_thinking_requests_with_the_action_tools`、`test_commit_last_seq_groups_one_versioned_commit_without_later_same_version_appends`；`tests/life/test_session_attribution.py`：`test_departure_commit_belongs_wholly_to_the_old_session`、`test_arrival_commit_belongs_wholly_to_the_transit_session`、`test_due_soon_record_uses_the_wake_lead_time` |
| 记忆层 | 26 | `tests/life/test_memory_layer.py`：`test_departure_and_arrival_attribute_same_second_calls_to_the_right_session`、`test_departure_request_belongs_wholly_to_the_old_session`、`test_en_route_conclusion_keeps_the_conclusion_in_the_transit_session`、`test_denied_arrival_ends_the_trip_and_the_failed_trip_is_concluded_in_the_new_stay`、`test_experiment_end_closes_the_last_session_into_the_memory_layer`、`test_failures_replays_recalls_queries_and_empty_sessions_leave_no_entry`、`test_sentences_write_portions_money_and_cross_day_clocks`、`test_paused_interval_appears_in_the_concluded_activity_sentence`、`test_unresolved_order_crosses_midnight_into_earlier_days_and_disappears_when_delivered`、`test_unanswered_message_crosses_midnight_counts_as_presented_and_a_reply_resolves_it`、`test_new_work_is_a_world_event_sentence`、`test_reflection_date_is_the_local_date_six_hours_before_sleep_start`、`test_bedtime_reflection_lands_on_the_date_of_the_sleep_it_follows`、`test_nap_reflection_stays_out_until_the_next_day`、`test_window_deletes_oldest_reflections_then_shrinks_oldest_today_entries_and_keeps_exemptions`、`test_memory_layer_is_byte_identical_after_restore_and_the_cache_only_accelerates`、`test_a_real_memory_layer_over_the_window_drops_reflections_then_shrinks_the_oldest_entry`、`test_a_stay_spanning_midnight_is_one_entry_of_the_day_it_ended_with_dated_clocks`、`test_meal_and_decimal_price_order_sentences_come_from_real_settlements`、`test_work_hours_and_submission_verdicts_are_settled_sentences`、`test_a_chat_ended_by_the_partner_is_settled_as_partner_ended`、`test_messages_sent_and_deliveries_received_on_the_road_land_in_the_transit_entry`；`tests/life/test_session_attribution.py`：`test_departure_commit_belongs_wholly_to_the_old_session`、`test_arrival_commit_belongs_wholly_to_the_transit_session`、`test_plan_inputs_keep_resumed_pause_intervals_in_ended_activity_sentences`；`tests/integration/test_observation_agent_seam.py`：`test_recall_accepts_the_activity_name_as_written_in_self_notes` |
| 人设层 | 13 | `tests/life/test_persona_layer.py`：`test_card_path_renders_card_fields_and_merges_relationships_by_exact_name`、`test_quarter_hour_times_are_written_as_quarters`、`test_no_card_person_keeps_the_name_only_path_and_view_only_people_get_an_item`、`test_six_hundred_hanzi_cap_checks_only_card_derived_fields`、`test_persona_card_struct_is_saved_on_new_runs_and_validated_on_restore`、`test_later_view_replaces_earlier_while_events_keep_both`、`test_first_purchase_writes_no_preference_and_leaves_no_trace_after_delivery_and_midnight`、`test_consolidation_is_kept_but_its_summary_never_reaches_the_prompt`；`tests/integration/test_persona_profile.py`：`test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer`；`tests/life/test_persona_upgrade.py`：`test_persona_layer_from_the_scenario_profile_reaches_the_native_llm_prompt`；`tests/integration/test_observation_agent_seam.py`：`test_relationship_memory_may_name_a_same_world_person_who_is_elsewhere`；`tests/profile/test_textgen.py`：`test_window_ends_at_or_after_midnight_render_as_clock_hours`、`test_render_card_is_byte_identical_to_the_pre_persona_layer_renderer` |
| 预算 | 14 | `tests/life/test_decision_context_projection.py`：`test_below_threshold_returns_the_request_untouched`、`test_self_notes_are_removed_oldest_first_one_at_a_time_and_protected_notes_stay`、`test_oldest_unprotected_run_folds_into_one_condensed_record_then_the_next`、`test_an_adjacent_earlier_condensed_record_is_merged_into_the_next_fold`、`test_exactly_85_percent_triggers_condensation`、`test_capacity_failure_keeps_protected_records_and_logs_every_part`、`test_host_request_condenses_progress_from_observation_metadata`；`tests/life/test_progress_events.py`：`test_meta_marks_records_after_the_last_action_request_as_protected_and_writes_lines`；`tests/integration/test_same_second_continuation.py`：`test_budget_step_zero_ends_the_continuation_before_any_condensation`、`test_budget_step_zero_starts_the_decision_from_a_fresh_snapshot_for_every_turn`；`tests/life/test_memory_layer.py`：`test_window_deletes_oldest_reflections_then_shrinks_oldest_today_entries_and_keeps_exemptions`、`test_a_real_memory_layer_over_the_window_drops_reflections_then_shrinks_the_oldest_entry`；`tests/life/test_session_attribution.py`：`test_chat_invitation_writes_no_settled_line_for_condensation`、`test_open_order_record_stays_uncondensed_while_older_records_fold` |
| 来源与去向 | 15 | `tests/test_start_activity_contract.py`：`test_legal_from_to_answering_combinations_route_each_item`、`test_illegal_combinations_are_protocol_errors_and_invalid_requests`、`test_from_and_to_need_one_item_per_target`、`test_unknown_recipient_is_recipient_not_found_before_admission`；`tests/integration/test_storefront_tools.py`：`test_host_checks_from_to_and_answering_against_private_bindings`；`tests/life/test_giving.py`：`test_direct_delivery_to_a_person_goes_into_their_holdings_and_nobody_eats`、`test_self_receipt_then_giving_transfers_exactly_once`、`test_same_product_for_two_recipients_splits_orders_by_destination`、`test_two_gives_in_one_request_hand_over_real_holdings_only`、`test_giving_to_a_colocated_person_moves_one_portion_and_nobody_eats`、`test_giving_to_a_person_who_left_is_refused_as_not_together`、`test_giving_to_a_same_world_person_elsewhere_is_refused_as_not_together`、`test_direct_delivery_to_a_same_world_person_elsewhere_goes_to_where_they_are`、`test_scenario_contacts_that_are_same_world_persons_do_not_receive`；`tests/life/test_named_purchase.py`：`test_full_label_with_a_store_in_from_is_an_invalid_request` |
| 进食 | 14 | `tests/life/test_meal_outcome.py`：`test_ending_a_meal_at_300_of_1500_seconds_eats_0_2_discards_0_8_and_keeps_3`、`test_pause_keeps_the_opened_remainder_and_resume_eats_it_after_another_meal_took_the_stock`、`test_less_than_one_portion_is_eaten_for_a_scaled_duration_and_energy`、`test_experiment_cutoff_truncation_discards_nothing`、`test_portion_duration_and_meal_portion_share_one_basis`、`test_meal_duration_is_validated_against_the_scaled_portion`、`test_open_meal_moves_the_portion_out_of_stock_only_for_eating`、`test_conclusion_outcome_names_failure_and_skips_other_activities`、`test_ending_a_meal_early_adds_no_energy_for_the_discarded_portion`、`test_ending_a_paused_meal_discards_the_opened_remainder_without_intake`、`test_concluding_a_failed_meal_names_the_failure_and_discards_the_remainder`；`tests/life/test_dayrun_inventory_regressions.py`：`test_meal_partitions_reach_zero_without_losing_quantity_or_energy`、`test_eating_progress_ignores_stock_taken_while_the_meal_was_paused`；`tests/life/test_todo_layer.py`：`test_paused_meal_progress_is_only_the_eaten_and_left_portions` |
| 指名购买 | 12 | `tests/life/test_named_purchase.py`：`test_rotation_hidden_supply_is_bought_by_name_at_the_current_quote`、`test_unknown_name_is_rejected_with_near_candidates_and_nothing_is_substituted`、`test_same_name_in_several_stores_is_ambiguous_until_a_store_is_named`、`test_ambiguous_name_lists_every_store_not_just_three`、`test_named_resolution_follows_the_chosen_way_and_never_matches_internal_ids`、`test_near_candidates_are_deterministic_term_similarity`、`test_unlisted_obtain_selection_is_a_named_placeholder_other_verbs_are_not`、`test_host_lets_unlisted_obtain_names_through_and_corrects_other_verbs`、`test_named_purchase_at_a_closed_store_is_a_business_rejection_and_nothing_is_substituted`、`test_named_purchase_beyond_the_balance_is_a_business_rejection_and_nothing_is_substituted`；`tests/life/test_engine_branch_coverage.py`：`test_purchase_by_unlisted_name_resolves_only_existing_supplies`；`tests/integration/test_storefront_tools.py`：`test_unoffered_supply_name_is_resolved_by_the_environment_not_the_host` |
| 代点关联 | 6 | `tests/life/test_request_linking.py`：`test_answering_links_only_items_sent_to_the_sender_and_shows_the_link`、`test_resubmitting_the_same_request_is_rejected_and_points_to_the_order`、`test_a_new_message_is_a_new_request`、`test_message_bindings_name_visible_direct_messages_by_sender_and_arrival`、`test_answering_a_cross_day_unreplied_memory_message_links_the_order`；`tests/life/test_session_attribution.py`：`test_answering_binds_progress_messages_and_only_unreplied_memory_messages` |
| 最小完整路径 | 1 | `tests/integration/test_minimum_complete_path.py`：`test_requested_order_goes_to_the_colleague_and_is_not_placed_twice` |
| 程序错误 | 26 | 第六章程序错误表七行的全部回归用例，逐行见下表「第六章：程序错误」 |
| 强制等待与预算 | 8 | `tests/life/test_decision_budget.py`：`test_restricted_rounds_used_up_without_an_activity_pause_technically`、`test_a_successful_last_restricted_round_carries_the_latest_rejection_code`、`test_a_replayed_rejection_in_the_last_restricted_round_exhausts_the_budget`、`test_budget_exhausted_with_a_pending_conclusion_beside_a_running_activity`、`test_budget_exhaustion_units`；`tests/life/test_same_moment_decisions.py`：`test_rejected_start_stays_in_start_or_resume_and_marks_the_option`、`test_restricted_stage_check_and_ladder_units`；`tests/footprints/test_quality.py`：`test_decision_rhythm_counts_silent_boundaries_and_wake_reasons` |
| 下游 | 28 | `tests/footprints/test_event_fields.py`：`test_retired_decision_statement_field_is_rejected_on_event_drafts`、`test_persisted_decision_started_is_validated_when_reading_events`；`tests/footprints/test_quality.py`：`test_decision_rhythm_counts_silent_boundaries_and_wake_reasons`；`tests/evaluation/test_behavior_metrics.py`：`test_observation_gate_allows_same_second_continuation_chain_reuse`、`test_observation_gate_fails_on_cross_second_or_cross_agent_duplicate`；`tests/delivery/test_windows.py`：`test_dreaming_window_reads_self_note_and_the_actual_request_layer_json`、`test_dreaming_rejects_an_action_without_the_recorded_layer_json`、`test_action_and_intent_windows_show_layers_and_self_note_but_never_decision_started`、`test_continued_same_second_request_is_kept_verbatim_in_action_and_intent_windows`；`tests/delivery/test_publication.py`：`test_delivery_gate_is_world_schema_eight_and_rejects_version_seven_exports`；`tests/footprints/test_purchases.py`：`test_export_writes_purchase_facts_under_manifest_version_two`、`test_validation_recomputes_purchase_facts_and_rejects_tampering`、`test_real_run_export_counts_eat_and_remote_order_once_despite_a_replay`；`tests/integration/test_same_second_continuation.py`：`test_a_real_continuation_run_passes_delivery_windows_and_the_observation_gate`；`tests/tools/test_run_viewer.py`：`test_run_viewer_recorded_names`；`tests/life/test_continuation_metrics.py`：`test_the_subcommand_opens_the_run_read_only`、`test_the_baseline_comparison_refuses_runs_that_are_not_comparable`、`test_a_pre_feature_run_has_no_switch_and_cannot_be_an_arm`；`tests/integration/test_run_manifest.py`：`test_retired_stage_is_rejected_even_beside_the_current_stage`、`test_engine_create_rejects_retired_stage_before_writing_run_artifacts`、`test_runtime_restore_rejects_retired_stage_even_when_code_identity_matches`；`tests/life/test_restored_model_identity.py`：`test_environment_lifecycles_reject_invalid_saved_stage_identity`、`test_direct_engine_open_rejects_retired_stage`、`test_new_run_rejects_a_retired_stage_identity`、`test_resume_and_replay_identity_rejects_a_retired_stage_before_comparing`、`test_environment_validation_rejects_a_retired_stage`、`test_create_open_resume_and_replay_call_the_one_manifest_validator`；`tests/footprints/test_render_day.py`：`test_resolves_names_from_seven_layer_observation_evidence_keys` |
| 真实复验（只报告） | 5 | `tests/life/test_continuation_metrics.py`：`test_a_real_run_reports_chain_positions_reasoning_cache_cost_and_topics`、`test_the_subcommand_opens_the_run_read_only`、`test_errors_are_classified_with_400_separate_and_partial_tokens_fall_back_to_chars`、`test_the_baseline_comparison_refuses_runs_that_are_not_comparable`、`test_a_pre_feature_run_has_no_switch_and_cannot_be_an_arm`（只报告：白领全天与双人半日的真实模型运行按规格只报告、不作门禁，需要用户的模型账号；登记的测试验证续接对比所用的只读子命令 scripts/inspect_run.py continuation。） |
| 文档一致性 | 1 | `tests/test_documentation_gate.py`：`test_real_repository_documentation_links_resolve` |
| 工程门禁 | 4 | `tests/test_style_gate.py`：`test_style_gate_admits_no_new_violations`；`tests/test_coverage_gate.py`：`test_branch_pass_and_fail_states`、`test_uncovered_public_interface_detection`；`tests/test_documentation_gate.py`：`test_real_repository_documentation_links_resolve` |

### 第六章：程序错误

每行的回归用例在把修复改回旧代码时失败（规格第六章）。

| 程序错误 | 节点数 | 证据测试（按文件分组） |
| --- | --- | --- |
| 直接消息截断，没有全文入口 | 6 | `tests/life/test_progress_events.py`：`test_direct_message_is_presented_in_full_and_marked_presented`、`test_round_commit_reads_only_messages_presented_in_full`；`tests/life/test_same_moment_decisions.py`：`test_read_observed_messages_marks_only_presented_messages_of_open_rounds`；`tests/life/test_engine_branch_coverage.py`：`test_presented_messages_come_from_the_decision_observation`；`tests/life/test_prompt_layers.py`：`test_direct_message_and_invite_text_appear_only_in_the_progress_layer`；`tests/life/test_memory_layer.py`：`test_unanswered_message_crosses_midnight_counts_as_presented_and_a_reply_resolves_it` |
| 恢复工作越过合同截止 | 5 | `tests/life/test_work_contracts.py`：`test_resume_plans_the_end_no_later_than_the_contract_deadline`、`test_resume_plans_the_end_within_the_contract_hours_left_after_other_work_on_the_task`、`test_work_ending_at_the_deadline_is_concluded_then_submitted_and_paid_once`、`test_resume_with_less_than_a_minute_to_the_deadline_is_a_business_rejection`；`tests/life/test_tool_layer.py`：`test_resume_within_a_minute_of_the_deadline_is_unavailable_with_the_admission_reason` |
| 工时达标与提交状态不一致 | 3 | `tests/life/test_work_contracts.py`：`test_target_met_while_still_working_states_the_three_facts_separately`；`tests/life/test_tool_layer.py`：`test_submit_while_working_says_finish_the_work_first`；`tests/life/test_work_review.py`：`test_paused_work_keeps_progress_and_has_an_independent_todo` |
| 逾期工作从观察和工具中消失 | 2 | `tests/life/test_work_contracts.py`：`test_overdue_task_stays_listed_until_the_end_of_its_day_and_submission_is_a_business_rejection`；`tests/life/test_engine_branch_coverage.py`：`test_visible_task_rows_keep_overdue_tasks_until_the_end_of_their_day` |
| 不足一份的食物无法消费 | 4 | `tests/life/test_meal_outcome.py`：`test_less_than_one_portion_is_eaten_for_a_scaled_duration_and_energy`、`test_meal_duration_is_validated_against_the_scaled_portion`；`tests/life/test_dayrun_inventory_regressions.py`：`test_irregular_meal_progress_matches_one_settlement`、`test_meal_rounding_tolerance_eats_only_the_opened_quantity` |
| 混合动作的购买记录遗漏 | 5 | `tests/footprints/test_purchases.py`：`test_mixed_eat_and_buy_in_progress_result_counts_its_order_once`、`test_replayed_receipt_is_not_counted_again`、`test_export_writes_purchase_facts_under_manifest_version_two`、`test_validation_recomputes_purchase_facts_and_rejects_tampering`、`test_real_run_export_counts_eat_and_remote_order_once_despite_a_replay` |
| 零条最近记忆失效 | 1 | `tests/integration/test_verified_memory.py`：`test_zero_recent_memory_limit_returns_no_recent_entries` |

### 第六章：缺口

| 缺口 | 节点数 | 证据测试（按文件分组） |
| --- | --- | --- |
| 进食提前结束只显示「已收尾」 | 4 | `tests/life/test_meal_outcome.py`：`test_ending_a_meal_at_300_of_1500_seconds_eats_0_2_discards_0_8_and_keeps_3`、`test_conclusion_outcome_names_failure_and_skips_other_activities`、`test_ending_a_paused_meal_discards_the_opened_remainder_without_intake`、`test_concluding_a_failed_meal_names_the_failure_and_discards_the_remainder` |
| 提示菜单与受限工具不一致 | 6 | `tests/life/test_tool_layer.py`：`test_every_listed_item_matches_admission_at_run_start`、`test_restricted_round_marks_closed_items_with_the_same_moment_reason`、`test_pending_conclusion_marks_everything_but_conclusion_unavailable`、`test_conclude_pending_stage_with_two_pending_opens_each_conclusion`；`tests/integration/test_agent_branch_coverage.py`：`test_restricted_rounds_do_not_narrow_the_host_tool_surface`；`tests/life/test_same_moment_decisions.py`：`test_start_or_resume_schema_matches_engine_dispatch` |
| 未完成浏览被写成采购完成 | 2 | `tests/life/test_meal_outcome.py`：`test_unfinished_browse_concludes_without_a_browse_result`；`tests/life/test_memory_layer.py`：`test_unfinished_browse_writes_no_purchase_sentence` |
| 联系人回复缺少双方历史 | 5 | `tests/life/test_contact_exchange.py`：`test_messages_to_contacts_go_to_the_outbox_and_the_reply_request_carries_both_sides`、`test_reply_history_keeps_the_latest_twenty_messages_in_time_order`、`test_received_lists_delivered_orders_and_gifts_to_the_contact`；`tests/integration/test_host_branch_coverage.py`：`test_prepare_contact_reply_builds_disabled_thinking_request`；`tests/life/test_social.py`：`test_model_reply_request_and_finalize_validate_200_chars` |
| 把没有订单当作没有使用应用 | 4 | `tests/footprints/test_purchases.py`：`test_browse_only_counts_as_app_browse`、`test_rejected_order_counts_as_order_attempt`、`test_mixed_eat_and_buy_in_progress_result_counts_its_order_once`、`test_real_run_export_counts_eat_and_remote_order_once_despite_a_replay` |

### 裁决 #21–#38

「裁决记录」第二张表各行按推荐定为已定，每行登记验证推荐做法的测试。

| 裁决 | 节点数 | 证据测试（按文件分组） |
| --- | --- | --- |
| #21 | 4 | `tests/integration/test_decision_context_protocol.py`：`test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user`；`tests/life/test_narration.py`：`TestFieldBlacklist::test_internal_identity_cannot_hide_inside_a_progress_result`；`tests/life/test_progress_layer_projection.py`：`test_fresh_result_preserves_feedback_without_action_id`；`tests/integration/test_agent_branch_coverage.py`：`test_tool_messages_follow_memory_queries_only` |
| #22 | 5 | `tests/test_start_activity_contract.py`：`test_start_activity_schema_rejects_model_supplied_calculation`、`test_ways_vocabulary_maps_each_way_to_exactly_one_life_verb`、`test_legal_from_to_answering_combinations_route_each_item`、`test_from_and_to_need_one_item_per_target`；`tests/life/test_giving.py`：`test_direct_delivery_to_a_contact_goes_to_their_receive_location` |
| #23 | 4 | `tests/life/test_request_linking.py`：`test_answering_links_only_items_sent_to_the_sender_and_shows_the_link`、`test_resubmitting_the_same_request_is_rejected_and_points_to_the_order`、`test_a_new_message_is_a_new_request`、`test_answering_a_cross_day_unreplied_memory_message_links_the_order` |
| #24 | 6 | `tests/life/test_giving.py`：`test_giving_to_a_contact_needs_being_at_their_receive_location`、`test_giving_to_a_contact_at_their_receive_location_goes_into_contact_holdings`、`test_direct_delivery_to_a_contact_goes_to_their_receive_location`、`test_direct_delivery_to_a_contact_who_cannot_receive_is_refused`；`tests/life/test_contact_exchange.py`：`test_messages_to_contacts_go_to_the_outbox_and_the_reply_request_carries_both_sides`、`test_received_lists_delivered_orders_and_gifts_to_the_contact` |
| #25 | 3 | `tests/life/test_decision_budget.py`：`test_restricted_rounds_used_up_without_an_activity_pause_technically`、`test_budget_exhaustion_units`；`tests/life/test_same_moment_decisions.py`：`test_rejected_start_stays_in_start_or_resume_and_marks_the_option` |
| #26 | 5 | `tests/integration/test_interaction_menu.py`：`test_actual_tool_schema_is_stable_whatever_the_menu`；`tests/integration/test_seven_layer_request.py`：`test_native_tools_are_stable_purpose_only_and_drop_memory_only_on_the_last_turn`；`tests/integration/test_agent_branch_coverage.py`：`test_restricted_rounds_do_not_narrow_the_host_tool_surface`；`tests/integration/test_life_agent_protocol.py`：`test_life_agent_schema_is_closed_and_host_fields_are_hidden`；`tests/integration/test_grounded_evidence.py`：`test_grounded_tool_uses_unique_readable_names_without_internal_ids` |
| #27 | 8 | `tests/integration/test_same_second_continuation.py`：`test_the_continued_request_starts_with_the_previous_request_byte_for_byte`、`test_a_three_decision_chain_keeps_every_prefix_and_reports_each_change_once`、`test_layer_changes_overlay_earlier_changes_and_keep_the_latest`、`test_continuation_messages_read_earlier_changes_and_skip_memory_and_feedback_tool_messages`、`test_a_rejected_request_continues_with_the_rejection_reason`、`test_a_decision_with_memory_and_correction_turns_is_continued_byte_for_byte`、`test_changes_overlay_relationships_and_preferences_so_later_tool_messages_do_not_repeat_them`、`test_real_pause_and_body_changes_reach_the_next_tool_message_once` |
| #28 | 6 | `tests/integration/test_seven_layer_request.py`：`test_observe_stores_the_body_without_the_host_only_progress_keys`；`tests/life/test_narration.py`：`TestFieldBlacklist::test_private_observation_keys_are_registered_as_forbidden`；`tests/integration/test_same_second_continuation.py`：`test_recorded_evidence_decides_whether_the_chain_holds`、`test_missing_model_evidence_breaks_the_chain`、`test_a_missing_result_of_the_previous_request_breaks_the_chain`、`test_a_recorded_request_without_its_response_breaks_the_chain` |
| #29 | 2 | `tests/integration/test_same_second_continuation.py`：`test_a_plain_resume_from_a_mid_second_checkpoint_sends_the_same_request`、`test_a_counterfactual_branch_breaks_the_chain` |
| #30 | 2 | `tests/integration/test_same_second_continuation.py`：`test_budget_step_zero_ends_the_continuation_before_any_condensation`、`test_budget_step_zero_starts_the_decision_from_a_fresh_snapshot_for_every_turn` |
| #31 | 7 | `tests/integration/test_same_second_continuation.py`：`test_the_stage_identity_requires_a_boolean_switch`、`test_the_switch_is_frozen_into_the_run_manifest_and_the_agent_profile`、`test_life_env_freezes_the_switch_into_the_manifest`、`test_resume_and_replay_cannot_override_the_switch`、`test_resume_inherits_the_parent_switch_and_rejects_a_diverged_profile`、`test_switch_off_every_decisions_first_request_has_exactly_two_messages`、`test_replay_with_an_intervention_inherits_the_parent_switch` |
| #32 | 2 | `tests/life/test_memory_layer.py`：`test_memory_layer_is_byte_identical_after_restore_and_the_cache_only_accelerates`；`tests/life/test_prompt_layers.py`：`test_memory_layer_and_persona_views_are_empty_at_run_start` |
| #33 | 3 | `tests/life/test_persona_layer.py`：`test_subject_is_required_for_relationship_and_preference_and_stripped_before_memory`、`test_later_view_replaces_earlier_while_events_keep_both`；`tests/life/test_progress_events.py`：`test_conclude_arguments_omit_relationship_and_preference_memories` |
| #34 | 4 | `tests/life/test_memory_layer.py`：`test_reflection_date_is_the_local_date_six_hours_before_sleep_start`、`test_bedtime_reflection_lands_on_the_date_of_the_sleep_it_follows`、`test_nap_reflection_stays_out_until_the_next_day`；`tests/life/test_memory_evidence.py`：`test_reflection_snapshots_carry_their_source_and_both_checks_accept_it` |
| #35 | 3 | `tests/life/test_persona_layer.py`：`test_card_path_renders_card_fields_and_merges_relationships_by_exact_name`、`test_no_card_person_keeps_the_name_only_path_and_view_only_people_get_an_item`；`tests/integration/test_seven_layer_request.py`：`test_no_card_persona_layer_keeps_the_name_and_scene_contacts` |
| #36 | 2 | `tests/life/test_persona_layer.py`：`test_consolidation_is_kept_but_its_summary_never_reaches_the_prompt`；`tests/integration/test_conclude_memories.py`：`test_threshold_consolidation_counts_conclusion_entries_but_not_bedtime_reflection` |
| #37 | 2 | `tests/integration/test_life_agent_protocol.py`：`test_life_agent_schema_is_closed_and_host_fields_are_hidden`；`tests/integration/test_seven_layer_request.py`：`test_native_tools_are_stable_purpose_only_and_drop_memory_only_on_the_last_turn` |
| #38 | 2 | `tests/life/test_persona_layer.py`：`test_readonly_questions_use_the_latest_decision_snapshot`；`tests/integration/test_decision_context_protocol.py`：`test_readonly_loop_uses_only_persona_memory_and_question` |

## 独立语义突变审查（阶段 6）

按验收标准「人物 Prompt 七层特性门禁」，在干净提交的隔离 worktree 上做 Uncle Bob 语义突变：每个突变是对生产源码的一处精确替换，
恢复规格「根因」列或台账「突变审查的目标」所述的旧错误，不改测试；先跑最可能检出的测试，再扩到整个目录；存活的突变补一个走真实引擎、
宿主或导出路径的行为测试，确认它在突变下失败、还原后通过，再以原补丁复验。突变脚本与补丁保存在编排会话的临时目录，不进仓库。

| 组 | 基线 | 突变（共 46 个） | 结果 | 初次存活与补测 |
| --- | --- | --- | --- | --- |
| M1 进食、指名购买、恢复上限 | `9bf7d92` | 11 个：结束进食把丢弃份量计入摄入（记录与身体能量、只计身体能量两种）；开吃份量改回固定一份（受理处、共享口径两种）；指名购买以最相近的候选或包含关系的商品替换；恢复不设上限（宿主不传、活动转换内忽略两种）；恢复上限只按合同或只按截止；指名路径恢复可见凭据门 | 全部杀死 | 「只把丢弃份量计入身体能量」与「恢复上限只按截止」初次存活：新增 `tests/life/test_meal_outcome.py::test_ending_a_meal_early_adds_no_energy_for_the_discarded_portion`、`tests/life/test_work_contracts.py::test_resume_plans_the_end_within_the_contract_hours_left_after_other_work_on_the_task`（`db1d8a0`） |
| M2 强制等待、预算耗尽、购买事实、消息全文 | `9bf7d92` | 7 个：恢复 `wait_only` 整条阶梯、只恢复被拒后的升级；预算耗尽时替人物造等待活动（不写事件、事件与活动并存两种）；购买事实只取整体 `completed` 的结果；进度层直接消息截到 40 字；轮前收件箱全部记已读 | 全部被既有测试杀死 | 无。消息全文只由 `test_direct_message_is_presented_in_full_and_marked_presented` 一个测试守卫 |
| M3 唯一出现、思考与续接、可用性 | `9bf7d92` | 13 个：时间推进后仍回传思考；跨 session 不断链（调用处、判定函数内两种）；驾车时发消息的原因与受理不一致、驾车时提交标为可用；工作活动 `state` 重复合同剩余；待办层暂停工作重复截止（字段、进度短句两种）；暂停活动也作工具层活动目标；在场人物的 `state` 重复其最新消息全文；续接 tool 消息的 `events` 重复整段 session；system 带剩余回合预算；system 带观察时刻 | 除 1 个等价突变外全部杀死 | 驾车两项、暂停工作两项与续接 `events` 一项初次存活：新增 `tests/life/test_tool_layer.py::test_driving_marks_messages_and_submission_unavailable_with_the_admission_reason`、`tests/life/test_prompt_layers.py::test_paused_work_does_not_repeat_the_contract_in_the_todo_layer`、`tests/integration/test_same_second_continuation.py::test_a_message_already_returned_in_the_chain_is_not_repeated_by_a_later_tool_message`（`70f0e94`） |
| M4 收货人、给予、订单去重、记忆层 | `49d577c` | 15 个：收货人改回付款人（远程送达、到店结算、两者三种）；给予增加给予者或收件人的摄入（两种）；同一（消息，收货人，商品）再提交仍下第二单；被拒请求写 `settled` 句；被拒的暂停与收尾写进 `self_notes`；回看写 `settled` 句；relationship、preference 记忆泄入记忆层 `self_notes` 或进度层参数（两种）；提前结束的浏览写购买句；回复正文写进记忆层 `self_notes` 或 `settled`（两种）；就寝反思归到醒来日期 | 全部杀死 | 被拒请求写 `settled`、被拒请求写 `self_notes`、浏览购买句与回复正文两种初次存活：加强 `tests/life/test_memory_layer.py::test_failures_replays_recalls_queries_and_empty_sessions_leave_no_entry` 与 `test_unfinished_browse_writes_no_purchase_sentence`，新增 `test_reply_bodies_stay_in_their_session_and_never_reach_the_memory_layer`（`d68e61d`） |

验收标准列出的十个检验点都有突变覆盖：唯一出现（M3 的工作合同、暂停项、在场人物与续接 `events`，M4 的看法偏好与回复正文）、
时间推进时思考消失（M3）、续接断链（M3）、给予不计摄入（M4）、进食丢弃不计摄入（M1）、指名购买不替换（M1）、恢复不越截止（M1）、
消息全文（M2）、订单去重（M4）、预算耗尽不造活动（M2）。12 个初次存活的突变补测后都以原补丁复验杀死。

**等价突变。** M3 去掉 `src/worldmock/life/continuation.py` 中 `same_second_chain` 调用处的 session 切换检查后，没有测试失败，也构造不出能让它失败的输入：
续接链还要在当前 session 的进度层记录里找到上一决策世界请求的那条记录（`records_after` 按 `session_bound` 取记录），session 一旦切换，
这条记录落在旧 session，找不到就断链。调用处的检查与按 session 取记录重复，因此判为等价；同一回归放进判定函数内的另一变体被测试杀死。

## 代码与规格的出入

以下各项是按规格逐条核对最终代码时发现的出入。除「回看与修订计划的可用性」（规格内部的缺口，代码未改）外，都已按规格修复
（接口合同与各规格版本表的 `2026-09-28-persona-prompt-spec-fixes` 行）；「处理」列写修复与回归用例，每个用例在把修复改回旧代码时失败。
受限轮收尾一项按编排方裁决：受限轮的收尾阶段收尾任意几项待收尾活动都受理，阶段持续到没有待收尾活动，每项的 `conclude_activity` 都标可用。

| 项 | 规格 | 原先的代码 | 处理 |
| --- | --- | --- | --- |
| 当日目标的回看名称 | 系统层规则 12「名称照工具层、待办层中的写法填写」；回看的名称只取七层中出现的名称 | 目标的私有绑定名是「目标：<意图>：<目标文字>」（`observe.py` 的 `_name_bindings`），待办层只给 `intent_kind` 与 `goal` 两个字段，七层中不出现这个名称；模型要自己拼出它才能回看目标 | 已修复：目标绑定名就是待办层写出的目标文字（`todo_layer.visible_goals` 是待办层与绑定的同一来源，计划过期时两处都没有目标）；两个目标文字相同时回看以 `TASK_NOT_FOUND` 拒绝。`tests/integration/test_observation_agent_seam.py::test_goal_recall_names_are_the_goal_texts_written_in_the_todo_layer`、`tests/life/test_day_plan.py::test_recall_task_summary_by_goal_name_round_trips` |
| 按记忆层的活动名回看 | 第三章「窗口」：更早的 summary 可按活动名用 `recall_task_summary` 回看 | 记忆层 `self_notes` 的活动名在条目内不重名时去掉开始时刻后缀，回看只接受完整的活动绑定名（宿主本地校验「task_name is not visible」），按记忆层所见的名称回看不成立 | 已修复：记忆层派生时记下各短名所指的完整活动名，活动绑定另带 `note_labels`，宿主与环境都按它解析；两个条目的同一短名指不同活动时回看以 `TASK_NOT_FOUND` 拒绝。`tests/integration/test_observation_agent_seam.py::test_recall_accepts_the_activity_name_as_written_in_self_notes` |
| 受限轮收尾阶段的可用性 | 第一章「工具层」：受限轮与待收尾期间「其余动作」标不可用，列出即可提交 | 达到同刻上限后的 `conclude_pending` 受限轮要求一次点名全部待收尾活动；有两项待收尾时，工具层逐项判定，把每项活动的 `conclude_activity` 都标为不可用（同刻限制原文），而一次点名两项的请求会被受理 | 已修复（按裁决）：`same_moment.request_kind` 把只点名待收尾活动（全部或其中几项）的收尾都记为 `conclude_pending`，工具层与受理同一判定。`tests/life/test_tool_layer.py::test_conclude_pending_stage_with_two_pending_opens_each_conclusion`、`tests/life/test_same_moment_decisions.py::test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three`、`tests/life/test_same_moment_decisions.py::test_restricted_conclusions_endings_and_waits_pass_the_stage_check` |
| 回看与修订计划的可用性 | 第一章「请求组成」：八个人物工具始终都在，此刻不能用的由工具层 `available`、`reason` 说明 | 工具层 Schema 没有放 `recall_task_summary`、`revise_day_plan` 可用性的位置（`tools[]` 只列以目标名称为参数的五个工具）；受限轮、待收尾与没有当日计划时它们被业务拒绝，事前没有标注 | 未改：这是规格内部的缺口，本批不在修复范围 |
| 拒绝的下一步为空 | 通用格式：允许为空的写 `null`，不填编造的值 | 业务拒绝没有可读下一步时 `details.next_step` 写空串 `""`（`guidance.rejection_guidance`） | 已修复：写 `null`。`tests/life/test_progress_events.py::test_a_rejection_without_a_next_step_writes_null` |
| 购买结果复述方式 | 进度层：`details` 不复述参数 | `details.purchases[].way` 与请求的 `ways[i]` 相同 | 已修复：删去 `way`，收货人与所回应的消息照写。`tests/life/test_same_moment_decisions.py::test_purchase_result_names_the_order_in_the_progress_layer` |
| 门店来源标记 | 地图与门店规格「地点发现与候选呈现」：模型中的来源使用类型、来源引用与事实截止日；人物 Prompt 规格修订 24 的 MS1：来源标记落在工具层商品目标的 `note` | 七层工具层的商品目标不带来源标记，动作 `note` 只写送达估计 | 已修复：门店商品目标第一个动作的 `note` 写「来源：<事实或场景假设>（<来源引用>），事实截止 <日期>」，每个目标一次，不含内部身份。`tests/life/test_tool_layer.py::test_each_store_product_target_carries_its_source_marker_once` |
| 两项同时结束的结果 | 唯一出现：浏览调用的结果只列名称，浏览得到的供给在工具层 | 两项请求在全部子活动结束后才写一条聚合终态，结果只带各活动快照；`details.browse_items` 与浏览结果的置顶只读单项结果顶层 | 浏览一侧可达且有误，已修复：`result_projection.browse_results` 也从聚合终态的浏览活动快照取浏览结果，`browse_items` 与置顶共用它，各一次。`tests/life/test_prompt_layers.py::test_a_browse_ending_with_the_other_activity_of_its_request_is_listed_and_pinned_once`。`details.arrival` 一侧不可达：出行的终态结果（单项或聚合）落在到达那次提交，归路程 session，出发请求整体归原停留 session，任何进度层都没有这条调用，到达只以记忆层路程条目的 settled 句出现；`tests/life/test_prompt_layers.py::test_a_trip_ending_with_its_companion_leaves_the_arrival_to_the_memory_layer` 记下这一点，代码未改 |

规格没有写明、代码按裁决或既有冻结条款实现、文档已按代码写明的细节（不算出入）：

- **预算耗尽也覆盖待收尾活动。** 规格写「受限轮用完仍没有进行中活动时」写 `decision_budget_exhausted`；代码在受限轮用完时仍有待收尾活动（例如待收尾活动旁另有进行中活动）也写该事件并技术停顿。有待收尾就要在同一秒再决策，不这样处理这一秒没有上界。阶段闭集保留 `conclude_only`，但开始或恢复被拒后留在原阶段，现行阶梯不再进入它。
- **`to` 可写不在工具层的同世界人物。** 规格写 `to` 的候选是本层的人物目标与「自己」，又要求同世界人物在其所在地点收货；按裁决，同世界全部其他人物都有私有绑定，写「人物：<名>」时给予以不同处拒绝、配送送到其所在地点。
- **没有结构化卡片的人物。** 规格写「只填姓名，其余为 `null` 或 `[]`」；按裁决 #35 的测试，卡片派生的字段为空，场景联系人与看法、偏好照常呈现。
- **返工的提交句。** 规格的句式含「提交「<任务>」，通过／未通过」，又规定只收成功结果；代码对评审要求返工（`WORK_REQUIRES_REWORK`）写「未通过」，这是唯一写成 `settled` 句的业务拒绝。
- **续接 `events` 的范围。** 规格写「链首快照之后同秒发生」的记录；代码每条 tool 消息只带上一决策观察之后的新记录，使同一条消息在整个请求中只出现一次（唯一出现原则与台账「他人同秒发来的消息在整个请求中只出现一次」）。
- **反事实断链。** 反事实分支在分支起点一律断链；分支内此后同一秒的相邻决策之间没有干预，照常成链（成链条件）。
- **路程中的菜单。** 途中仍按出发地解析菜单（冻结的时间与能量规格第五章，人物 Prompt 规格未修订），环境层 `fixed_rules` 取同一菜单。
- **字幕。** 规格没有给字幕正文分配层；代码中字幕正文不进七层，内容目标的 `state` 只写观看进度。
- **重名的回看名称。** 规格要求名称唯一可读，但没有写两个当日目标文字相同、或两个记忆条目的同一 `self_notes` 活动名指不同活动时怎么办；
  目标文字是人物自己写的，不能像活动名那样追加序号，代码让这类名称照常通过宿主校验，由环境按找不到一样以 `TASK_NOT_FOUND` 拒绝，不猜是哪一个。
- **浏览先于同一请求的另一项结束。** 聚合终态在同一请求的全部子活动结束后才写（时间与能量规格），浏览若先结束，`browse_items` 与置顶要等另一项结束后
  随那条结果出现；两项同时结束时当场出现。
- **只读问答的生成参数。** 与行动相同（thinking 开启、`tool_choice=auto`、`max_tokens=8000`），经 LifeAgent 同一个请求入口发送。
- **协议门禁 `memory_md_identity_residue`。** 仍检查调用方传入的 `MEMORY.md` 文本；冻结时文档把它改写为「检查记忆层 JSON」，与代码不符，本次改回（规格没有修订这一门禁）。

## 文档一致性

- **冻结条款修订与文档同步作业。** 【冻】作业随规格冻结落地（`eeaabd7` 等六个文档分支），【同】作业随各阶段代码合入（接口合同与设计文档的阶段同批行、修复分支的同步）；
  【后】作业随 `7b05cd6` 合入的文档核对完成，CB1 与本记录在本记录所在的提交按最终数字刷新，见下节。实现后按最终代码逐文档核对，改正了冻结时按规格预写而与实现不符的细节，逐项写在各文档的版本表
  （`2026-09-28-persona-prompt-implemented` 行，设计文档的新版本行）。
- **现行条款中的过时表述。** 一次性扫描现行文档（除 `docs/dev/research/` 下的历史材料、历史快照 `docs/dev/ANALYSIS-spec-coverage.md`、
  最后更新的 `docs/dev/implementation-status.md`、提案与本规格自身，并跳过各版本表）中的「决策自述」「身份前言」「怎样过一天」「`scene`」「`facts`」「吃饭途径句」：
  17 份文档共 51 行命中，逐行核对都是删除、取代或「不再」的说明，或位于时间与能量规格第六章（章首注明已被取代）之内；没有一处仍把它们写成现行行为。
- **测试名。** 现行文档中引用的测试节点与测试函数名逐个核对到 `tests/`：冻结时按预定名称写的 `test_dreaming_window_reads_self_note_and_layer_json_without_decision_started`
  改为现行的 `test_dreaming_window_reads_self_note_and_the_actual_request_layer_json`；生活运行时规格「完整请求容量」行原引的跨轮容量场景不存在，改指实际守卫的测试；
  人物规格验收矩阵补上实际守卫的测试文件。其余名称都存在，AS2 分支测试目录的名称因子模块未检出无法核对。`docs/dev/research/` 下的历史验证记录保留当时的名称。
- **锚点核对。** 一次性脚本（按 GitHub 标题锚点规则解析，只读，不进仓库）核对自 `d27f4b0`（文档核对之前）以来改动的 35 份文档（含本记录）中的全部 809 个链接（其中 244 个带锚点），以及其他文档指向这些文档的 24 个带锚点链接：目标文件都存在，锚点都指向现有标题，0 处失效。
- **文档门禁。** `scripts/documentation_gate.py` 扫描 128 份文档（含本记录），0 处断链，判定 PASSED；在本记录所在的提交上，`tests/test_documentation_gate.py`、钉住部分文档原文的 `tests/test_time_and_energy_completeness.py` 与读取规格验收台账的 `tests/test_persona_prompt_acceptance.py` 共 39 项通过。

## 实现后文档（阶段 8）

| 作业 | 落地 |
| --- | --- |
| E4-20、E4-22 | 模拟引擎设计「当前实现边界」删去「人物 Prompt 七层规格尚未实现」一句；「验收重点」补本记录链接 |
| E5-13、E5-14 | 记录引擎设计删去本规格的八项实现差距；「记录生活全过程的验收」的经历句子行改为记忆层 `settled`，增续接证据、记忆层、订单收货人三行 |
| E6-8 | 数据交付设计「4.2 编码示例」改用现存的观察字段 |
| VF10 | 验收标准的人物 Prompt 门禁链接本记录；测试矩阵十二行改回离线覆盖 |
| IX8 | 文档入口登记本记录 |
| AN1 | 规格覆盖追溯新增「人物 Prompt 七层增量映射」，列出被取代的 17 行与抽样自验第 17 条 |
| CB1 | 覆盖率基线记录改为 `e804040` 的门禁结果 |
| RV3 | 查看器上下文验证记录补「人物 Prompt 七层实现后复验」 |
| 文件头 | 各被修订文档的「按人物 Prompt 规格修订，修订部分待实现」改为已实现，冻结文档各追加版本行，设计文档升小版本 |
| IS1 | `docs/dev/implementation-status.md` 由编排方的最后一个提交更新，不在本记录的提交内 |

## 真实复验（阶段 7，只报告）

未执行：规格第七章的续接对比与白领全天、双人半日复验要调用真实模型，需要用户的模型账号。
这一行按规格只报告、不作门禁；`tests/persona_prompt_acceptance.py` 为它登记的是只读子命令 `scripts/inspect_run.py continuation` 的测试
（链位置、思考量、错误分类、缓存与成本、话题、`--baseline` 拒绝不可比的对比、特性之前的运行不能作任何一臂）。

执行时的协议（规格第七章「续接对比」）：同一提交、同一场景文件、同一模型身份，两臂只有 `worldmock run --same-second-continuation on|off` 不同；
每臂至少三次白领全天运行，另加一次双人半日，交替启动；逐运行执行 `scripts/inspect_run.py <run_dir> continuation --baseline <另一臂>`，
报告重复下单次数、同秒跟进的思考 token、协议错误与 400、话题重复推敲、计入缓存命中后的成本。`2026-09-28-c9a4fc1-day-r3` 带决策自述阶段，不能作关闭臂。

## 范围外

- **数据集仓库。** 规格「范围外登记」：`simulate_phone.py` 改读 `purchases.jsonl`、LabelKit 输入脚本随交付窗口同步，在数据集仓库单独提交，本次未做。
  数据集场景的 `receive_location_id` 已在数据集仓库分支同步（见「源码身份」）；该仓库的场景构建脚本重建时会丢掉这个字段，也属数据集仓库，未改。
- **其余范围外项**：工作材料正文进入哪一层、结果 `message` 的语言、`start_activity` 的可选 summary，都按规格「范围外登记」不在本次范围。
