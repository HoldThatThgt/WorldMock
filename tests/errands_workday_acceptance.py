"""个人事务与工作安排规格的验收登记表（完整性守卫的数据源）。

@file     errands_workday_acceptance.py
@brief    把 docs/dev/SPEC-errands-workday.md「验收台账」的每一行登记到所属阶段与证据测试节点（LEDGER），登记台账之后
          「源码变异必须覆盖」一段的每一句到杀死它的源码变异（SPEC_MUTATIONS、MUTATIONS），并冻结本特性新增或改动的生产
          文件与新增测试文件（FEATURE_PRODUCTION_FILES、FEATURE_TEST_FILES），供延期标记与空壳函数扫描。判定由
          tests/test_errands_workday_acceptance.py 执行；变异经共用执行器 tests/mutation_runner.py 在隔离子进程里生效。
@note     分阶段形态：`stage` 取规格台账的阶段列；只有 `stage` 属于 STAGES_DONE 的行必须列出测试，但任何行列出的节点都
          必须解析到存在、会被收集且未被跳过的测试函数。阶段随实施增长：每个阶段的提交登记自己的行、变异与文件，阶段的
          最后一个提交把阶段号加入 STAGES_DONE；阶段 4 只依赖阶段 1，可以先于阶段 2、3 完成。
          每行必须有经真实请求、引擎与 SQLite 路径的证据（人物一侧经真实引擎、LifeEnv 与直接导入的 LifeAgent）；只断言
          辅助函数的用例、在替身环境上直调人物私有覆写点的用例至多作补充证据或变异杀手，不单独支撑一行。
          变异只登记在主进程内执行的生产代码上，`original` 在源文件恰好出现一次，杀手是已登记在某一行的行为用例。
          两个适配器目录（custom/agents、custom/envs）的文件经 AS2 注册表按文件路径以另一个模块名加载，针对它们的变异
          只有直接导入该模块（如 custom.agents.life_agent）的用例杀得死；经 Society 或 actor 的路径只算覆盖与行为证据。
          SPEC_MUTATIONS 的键按规格原文的顺序逐句照抄，守卫核对它们首尾相接恰好拼回规格那一段；一句列出几种情形时，每种
          情形各有登记变异。不对应那一段任何一句的变异（取自实施计划「突变审查的目标」，或阶段复审按规格正文条款补登的）
          登记在 EXTRA_MUTATIONS 并写明出处。
          「复跑指标（只报告）」按规格只报告、不作门禁：配对全天复跑是计费的真实运行，只在用户要求时启动。
"""

from __future__ import annotations

from pathlib import Path

from tests import mutation_runner
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, SourceMutation

#: 规格「实施计划」中的全部阶段编号（冻结之外的 0–7）。
STAGE_IDS = frozenset(str(number) for number in range(8))

#: 已完成的阶段；每个阶段的最后一个提交把自己的编号加入。阶段 1（运行修复入库）随严格反思的提交完成；阶段 2（个人事务
#: 与提示分工）随收尾提交完成：十行台账都列出节点，二十六句规格变异都有登记变异（阶段复审之后，一句列出的每种情形各有
#: 一个）。
STAGES_DONE: frozenset[str] = frozenset({"1", "2"})

_PROTOCOL = "tests/integration/test_life_agent_protocol.py::"
_FAILURE_STAGES = "tests/integration/test_host_failure_stages.py::"

#: 「故障传播」：决策失败与工具分发失败各有英文日志，异常对象、类型与失败阶段码不变。头两个节点经真实引擎、SQLite、
#: LifeEnv 与直接导入的 LifeAgent 跑醒来那一轮：世界请求在 LifeEnv.submit_action 处服务失败（两条日志、RuntimeError 带
#: TOOL_SERVICE_FAILED 与原异常、库里没有 action_requested）与模型调用失败（一条日志、同一异常对象原样上抛、失败证据记
#: MODEL_SERVICE_FAILED）。其后在替身环境上直调的用例是补充证据与变异杀手：step 的三个故障位置、工具分发失败，Society
#: 路径（中止、不结算、日志一条），模型服务失败与证据写入失败两个失败阶段码，末一个守住记忆查询失败作为失败的查询结果
#: 回给模型。四个失败阶段码都只标出失败阶段与调用位置。
_FAULT_PROPAGATION_TESTS = [
    _FAILURE_STAGES + "test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage",
    _FAILURE_STAGES + "test_model_service_failure_on_the_real_engine_is_logged_once_and_reraised_unchanged",
    _PROTOCOL + "test_decision_failure_is_logged_once_and_reraised_unchanged",
    _FAILURE_STAGES + "test_tool_service_failure_points_to_real_call_without_claiming_execution",
    _PROTOCOL + "test_memory_persistence_failure_aborts_society_before_commit",
    _FAILURE_STAGES + "test_model_service_failure_persists_stage_without_parsing_exception_text",
    _FAILURE_STAGES + "test_evidence_write_failure_stops_with_structured_stage",
    "tests/integration/test_agent_branch_coverage.py::test_tool_messages_follow_memory_queries_only",
]

_REVISION = "tests/life/test_revise_day_plan_json.py::"
_PLAN_DAY = "tests/life/test_plan_day_duplicate_feedback.py::"

#: 「计划重复意图」：零点计划与修订共用校验，首次与其后各次出现都被定位，超限与重复同批报告。头三个节点是修订入口经
#: 真实引擎、结算与 SQLite 结果事件（重复、超限加重复、至多八条与每意图一条不变）；其后两个是零点计划经真实后台任务
#: settle_day_plans（响应事件的错误文本、第二次请求的附回、最终登记的计划）；第六个是宿主一侧——直接导入的 LifeAgent 在
#: 真实 ReAct 循环里收到模型的 JSON 字符串参数，回 ARGUMENTS_INVALID 协议纠错而不崩溃。末三个只调请求模型或
#: parse_plan_day、prepare_plan_day_correction，是补充证据。
_PLAN_DUPLICATE_TESTS = [
    _REVISION + "test_engine_rejects_a_duplicate_revision_locating_first_and_later_entries",
    _REVISION + "test_engine_reports_over_capacity_and_duplicates_in_the_same_rejection",
    _REVISION + "test_engine_keeps_at_most_eight_goals_with_one_goal_per_intent",
    _PLAN_DAY + "test_plan_day_duplicate_intents_are_fed_back_with_every_position_then_registered",
    _PLAN_DAY + "test_plan_day_reports_over_capacity_together_with_duplicates",
    _REVISION + "test_host_corrects_duplicate_intents_from_real_json_arguments",
    _REVISION + "test_duplicate_intent_in_provider_json_returns_nested_validation_error",
    _REVISION + "test_nine_goals_with_a_duplicate_report_capacity_and_positions_in_one_validation",
    _PLAN_DAY + "test_repeated_work_goals_are_located_and_correction_requires_merging",
]

_READ_QUERY = "tests/integration/test_read_query_runtime.py::"
_WORLD_QUERY = "tests/life/test_world_query.py::"

#: 「只读查询转发」：按规格 1.2「证据分工」各类缺一不可。第一个节点守运行装配（worldmock.runtime._build_society 建立的是
#: LifeEnvActor 与带 read_query 的 LifeEnvProxy，交给人物的 ServiceProxy.env 也是这个代理）；第二个是真实 Ray：驱动侧与
#: worker 侧经代理各调用一次，回看任务返回结果字典而非对象引用，重新打开运行目录后世界状态逐字节不变、只多出 actor
#: 一侧那一次观察；第三个在主进程内直接构造未经 ray.remote 包装的 LifeEnvActor，两个工具的结果等于环境直调，业务拒绝
#: 是返回值，类型不符与找不到唯一观察是协议违约；
#: 第四个是 query_world 两入口（宿主本地求值的纯函数与环境的 read_query）受理时逐值相等、拒绝时同一份校验报告，它同时
#: 登记在工具交互台账并由那边的变异守着；末一个不启动 Ray，守并发度为 1 且不提供参数。
_READ_QUERY_TESTS = [
    _READ_QUERY + "test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query",
    _READ_QUERY + "test_task_recall_crosses_real_ray_without_world_mutation",
    _READ_QUERY + "test_unwrapped_actor_read_query_equals_direct_environment_call",
    _WORLD_QUERY + "test_environment_read_query_keeps_world_clock_round_and_store_unchanged",
    _READ_QUERY + "test_life_env_actor_class_fixes_single_concurrency_without_parameters",
]

_REFLECTION = "tests/integration/test_strict_reflection.py::"

#: 「严格反思」：头五个节点经真实记忆运行时（WorldMockMemoryRuntime.after_step 的就寝边界）——恰一次 submit_reflection、
#: 一至五条与固定的条目字段；每种合同违约（自由文本、其他工具、多次调用、零条或六条、未知引用、引用为空或重复、正文
#: 超长或含身份串）都附回英文错误重试一次并只写入改正后的那一条；首次之外三次重试耗尽即运行故障且不写入任何洞见；传输
#: 异常不进校验重试；没有可反思的记忆时不调用模型。第六个守文案：实际发送的 system、模块常量与接口合同登记块逐字相同。
#: 第七、八个经真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent 跑醒来那次决策（耗尽：四对请求与响应留证、请求关闭
#: 思考、没有 memory_updated、中止后动作未结算；改正：恰好一条 memory_updated）。第九个是 Society 层的验收口径（本轮中止、
#: 不提交、无 memory_updated，原因带最后一次校验错误）。其后三个是既有登记用例与真实日运行：反思条目的字段、巩固不计反思、
#: 整日运行里反思请求不是行动请求且含反思的决策其交付窗口提示仍是行动请求（一颗种子的反思首次不合合同、经一次校验
#: 重试改正：两人逐字相同的附回消息不进观察重复门禁，门禁仍为 1）。再后两个守交付窗口的提示选取与行动请求判定。
#: 末八个只调用 reflection_insights、reflection_tool、reflection_correction、只读常量或以合成事件调用行为指标，是补充证据。
_REFLECTION_TESTS = [
    _REFLECTION + "test_runtime_submits_reflection_through_the_only_strict_tool",
    _REFLECTION + "test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection",
    _REFLECTION + "test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection",
    _REFLECTION + "test_runtime_propagates_transport_failures_without_validation_retries",
    _REFLECTION + "test_runtime_makes_no_model_call_when_there_is_nothing_to_reflect_on",
    _REFLECTION + "test_reflection_prompt_is_the_contract_text",
    _REFLECTION + "test_exhausted_reflection_leaves_evidence_but_no_memory_update_in_the_run_database",
    _REFLECTION + "test_corrected_reflection_is_recorded_once_by_the_real_engine",
    _PROTOCOL + "test_reflection_contract_failure_aborts_society_before_commit",
    "tests/integration/test_verified_memory.py::test_sleep_reflection_is_subjective_and_its_id_is_reported",
    "tests/integration/test_conclude_memories.py::"
    "test_threshold_consolidation_counts_conclusion_entries_but_not_bedtime_reflection",
    "tests/integration/test_dayrun_complete_days.py::"
    "test_complete_two_person_day_uses_real_tools_memory_and_restoration",
    "tests/delivery/test_windows.py::test_reflection_requests_after_the_action_never_become_the_decision_prompt",
    "tests/life/test_engine_branch_coverage.py::test_only_action_requests_satisfy_the_predicate",
    _REFLECTION + "test_reflection_tool_schema_is_inline_strict_and_bound_to_the_as2_limit",
    _REFLECTION + "test_reflection_records_only_valid_subjective_insights",
    _REFLECTION + "test_invalid_reflection_cannot_enter_memory",
    _REFLECTION + "test_reflection_requires_exactly_one_tool_call",
    _REFLECTION + "test_reflection_tool_arguments_must_be_a_json_string",
    _REFLECTION + "test_reflection_correction_appends_one_compact_message_without_touching_the_original",
    _REFLECTION + "test_reflection_retry_bound_is_its_own_constant_with_the_plan_day_value",
    "tests/evaluation/test_behavior_metrics.py::test_reflection_requests_and_their_retries_are_not_decision_requests",
]

_SESSION_STATE = "tests/life/test_session_state.py::"
_PUBLICATION = "tests/delivery/test_publication.py::"
_ERRAND_TODO = "tests/life/test_errand_todo_layer.py::"

#: 「检查点」中「版本 8 检查点被拒」一项：头一个经真实引擎与 SQLite——落盘的版本 8 世界状态（人物状态里没有 errands 键，
#: 与版本 8 写出的检查点同形）在重新打开运行时只以版本不符拒绝；其后一个守世界状态模型本身只拒绝版本、同形的版本 9
#: 通过；第三个守交付入口：版本 8 的导出在构造窗口与运行 LabelKit 之前按版本拒绝，版本 9 的导出到达窗口构造。末一个是
#: 「共同检查点恢复后待办层逐字节相同；不重复记下、完成与提醒」：经真实请求、引擎与 SQLite 记下带提醒的事务、到店累计
#: 一份、远程订单在途时创建共同检查点并恢复成分支（engine.checkpoint → store.restore → LifeEngine.open），事务逐字段相同、
#: 同一时刻的待办层逐字节相同；父运行与分支接着过下去各提醒一次、送达各一次并各办成一次，分支没有第二条记下事件。
_RESTORED_BRANCH = ("tests/integration/test_errand_checkpoint.py::"
                    "test_a_restored_branch_shows_the_same_todo_layer_and_repeats_nothing")
_CHECKPOINT_TESTS = [
    _SESSION_STATE + "test_run_with_version_8_world_state_is_rejected_on_open",
    _SESSION_STATE + "test_world_state_schema_version_8_is_rejected",
    _PUBLICATION + "test_delivery_gate_rejects_version_eight_exports",
    _RESTORED_BRANCH,
]

_OVERNIGHT = "tests/life/test_errand_overnight.py::"
_DAY_PLANNING = "tests/life/test_day_planning.py::"
_SEPARATION = "tests/integration/test_errand_prompt_separation.py::"

#: 「跨夜可见」：全部经真实请求、引擎与 SQLite——收尾时记下的事务在零点使旧计划失效、零点计划登记新计划、目标推进与
#: revise_day_plan 之后逐字段不变，待办层的事务行与零点前逐字相同、没有事务事件；记忆层超过 8000 字符被截取时与由冻结
#: 观察准备的真实 LifeAgent 发出的请求被浓缩（进度层折叠）时，待办层的事务行照旧；同一段生活带不带事务变更，记忆层逐字节
#: 相同（六种事务事件都不给记忆层添句）。末三个守零点计划：输入的只读键「个人事务」五键与同一时刻待办层同源同序、计划登记
#: 不改事务也不写事务事件；零点前办成的事务在输入里没有「办成」句、名称不出现在任何键里，进食本身仍是已核验经历（办成句
#: 只用于进度层折叠，规格 2.6）；真实请求的模板与接口合同逐字相同，输入说明末行与第七条逐字。倒数第四个是「实际行动请求的
#: 待办层含该事务且本决策没有记忆查询」：真实 LifeAgent 于 23:51 播报时记下的事务，过了零点后下一次决策在库里留下的那一次
#: 行动请求（不是续接）的待办层原样列着它，这次决策只有一个模型请求、只调用了世界请求，没有记忆查询。
_OVERNIGHT_TESTS = [
    _OVERNIGHT + "test_an_errand_stays_in_the_todo_layer_after_midnight_plan_generation_and_plan_revision",
    _OVERNIGHT + "test_an_errand_stays_when_the_memory_layer_overflows_its_window",
    _OVERNIGHT + "test_a_condensed_request_still_carries_the_todo_layer_errands",
    _OVERNIGHT + "test_the_memory_layer_writes_no_line_for_errands",
    _SEPARATION + "test_the_next_action_request_shows_the_errand_in_the_todo_layer_without_any_memory_query",
    _DAY_PLANNING + "test_plan_day_input_lists_open_errands_read_only",
    _DAY_PLANNING + "test_plan_day_input_writes_no_line_for_an_errand_completed_before_midnight",
    _DAY_PLANNING + "test_plan_template_is_the_contract_text_and_asks_only_for_differences_with_around_times",
]

_TOOL_TEXTS = "tests/integration/test_tool_description_references.py::"
_SEVEN_LAYERS = "tests/integration/test_seven_layer_request.py::"
_CONTINUATION = "tests/integration/test_same_second_continuation.py::"
_SYSTEM_VERBATIM = _SEVEN_LAYERS + "test_system_layers_are_the_contract_registration_verbatim"
_SEPARATED = _SEPARATION + "test_errand_changes_write_no_memory_and_stay_out_of_self_notes_and_progress_arguments"

#: 「提示与唯一出现」，按台账各项的顺序：
#: - 「系统层三处逐字且 18 条，第 6 条与第 16 条不相抵」：直接导入的 LifeAgent 发出的真实请求的系统层与接口合同「请求组成」
#:   的登记、人物 Prompt 规格「系统层」定稿逐字相同且为十八条；第 1、4、6 条与规格 3.1 的定稿逐字相同，第 16 条与改写之前
#:   逐字节相同，规则里没有决策次序一类的新指令。
#: - 「旧延续句不再出现」：规则、合同「请求组成」一节、人物 Prompt 规格「系统层」一节与重新汇编的设计说明书四处。
#: - 「工具用途句与字段说明与接口合同逐字且通过用途扫描」：三个控制工具的用途句与十一处参数说明与接口合同定稿表逐字相同，
#:   全部原生工具的用途句与参数说明通过只写用途的扫描。
#: - 「反思文案含 3.3 补句并与登记块逐字相同」：经真实记忆运行时在就寝边界发出的 system、模块常量与合同登记块逐字相同，
#:   是规格 1.1 的文本块空一行接 3.3 的补句（不折行的一行）。
#: - 「非续接请求里事务的完整行只在待办层，别处只有名称引用」：真实 LifeEngine、LifeEnv 与从工作区装配的 LifeAgent 跑一段
#:   单人生活（记下时被宿主纠正一次备注、更新、取消、喝完即办成、一次绕过宿主被环境拒绝），每个行动请求里事务行的内容
#:   只在待办层，事务名在别处只作为进度层记录与回执的 errand 键，只有规格 2.6 的三处回显位置例外且确实被用到。
#: - 「内部身份与新绑定键不进任何层」：经真实引擎预置未完成事务后 engine.observe，观察带两类私有事务绑定并存进 SQLite 的
#:   观察事件，五层照常渲染且不含事务身份与商品身份，任何一层带上 errand_bindings、errand_item_bindings 或 errand_id 即
#:   渲染失败，按事务名回看以 TASK_NOT_FOUND 拒绝；上一项那段运行的每个请求里也没有事务身份、商品身份与绑定键。
#: - 「同秒续接 changes 含事务增改删，同秒办成的事务在续接 events 中出现一次」：同一秒三次真实决策，changes.errands 写
#:   新增与内容有变的完整行（added）和取消的名称（removed）；在集市记下后当场买下办成，下一条 tool 消息的 events 里恰有
#:   一条只写名称的办成记录。
#: - 「同一 session 内办成的事务在进度层有一条记录，浓缩后折成一句」：经真实请求办成的事务在进度层恰一条只写名称的记录、
#:   来源计入观察，settled 句折成「HH:MM 办成「<事务名>」」（跨日带日期），取消的事务不留记录，开启新 session 的那次提交
#:   里的办成不进新 session 的进度层；其后一个是同一归属规则的提醒一半：步行到达的那次时钟推进提交开启停留 session，
#:   与到达同一边界交付的提醒归旧的路上 session，到达后第一次观察的进度层没有提醒记录、提醒事件不是进度来源。
#: - 规格 3.4 的结构项（末一个节点）：真实 LifeAgent 收尾时记下事务，AS2 记忆库只多那条收尾记忆、只有那一步是记忆写入点；
#:   暂停时更新与取消事务不写记忆；进度层工具调用参数略去 errands、回执是唯一痕迹；离开那段停留后记忆层 self_notes 里有
#:   收尾与暂停的自述和记忆，没有事务的名称、备注与取消原因。
_PROMPT_PRESENCE_TESTS = [
    _SYSTEM_VERBATIM,
    _SEVEN_LAYERS + "test_rules_1_4_6_divide_errands_summaries_and_memories",
    _SEVEN_LAYERS + "test_the_retired_continuation_sentence_is_gone_everywhere",
    _TOOL_TEXTS + "test_control_tool_purposes_and_errand_descriptions_are_the_contract_text",
    _TOOL_TEXTS + "test_every_native_tool_has_a_purpose_only_description",
    _TOOL_TEXTS + "test_parameter_descriptions_are_purpose_only",
    _REFLECTION + "test_reflection_prompt_is_the_contract_text",
    _REFLECTION + "test_reflection_prompt_ends_with_the_errand_sentences",
    "tests/integration/test_unique_presence_run.py::test_an_errand_full_row_appears_only_in_the_todo_layer",
    _ERRAND_TODO + "test_observation_carries_private_errand_bindings_outside_every_layer",
    _CONTINUATION + "test_errand_changes_reach_the_next_tool_message_as_added_and_removed",
    _CONTINUATION + "test_an_errand_completed_in_the_same_second_appears_once_in_the_continuation_events",
    "tests/life/test_progress_events.py::test_an_errand_completed_in_the_session_is_one_record_and_folds_to_one_line",
    "tests/life/test_errand_reminder.py::test_a_reminder_delivered_with_the_arrival_stays_out_of_the_new_stay_session",
    _SEPARATED,
]

_COMPLETION = "tests/life/test_errand_completion.py::"

#: 「完成依据」：全部经真实请求、引擎与 SQLite——人物以收尾请求记下事务，随后经真实的远程下单与配送、到店购买、当面给予、
#: 制作与进食产生证据：远程下单受理时不推进、送达的那次提交办成；同一秒先记下再到店买下当场办成而物品留在门店；数量 2
#: 只得 1 份仍未完成并记累计；记下前已持有的不算、记下前开始的进度区间不算（后者请求造不出，经真实时钟推进与预置的记下
#: 时刻造出）；他人买给本人与当面给予本人算、本人买给他人不算，买给或当面给不是同世界人物的联系人也不算任何人物取得；
#: 制作未完成不算、完整结束的产出算；开始喝的那次受理不算，进食按区间实际食用量累计且累计量差 1e-9 内即办成；推进了
#: 当日目标（progressed）的活动与播报、收尾里声称办成的自述都不算（规格 2.4「不算」）；缺货被拒后事务与累计量不变，
#: 闭店与买不起被拒后同样不变（规格 2.4 缺货一条的另两种情形）；同一轮先给予后取消不落到已取消的事务；结算事件的
#: 可见性、人物、载荷、原因与所在提交。
_COMPLETION_TESTS = [
    _COMPLETION + "test_a_remote_order_completes_only_in_the_commit_that_delivers_it",
    _COMPLETION + "test_noting_then_buying_at_the_store_in_the_same_second_completes_while_the_item_stays_there",
    _COMPLETION + "test_quantity_two_with_one_obtained_stays_open_and_shows_the_progress",
    _COMPLETION + "test_what_was_held_before_noting_does_not_count",
    _COMPLETION + "test_a_progress_interval_begun_before_noting_does_not_count",
    _COMPLETION + "test_bought_for_me_and_handed_to_me_count_but_bought_by_me_for_another_does_not",
    _COMPLETION + "test_an_order_and_a_gift_for_a_contact_outside_the_world_count_for_nobody",
    _COMPLETION + "test_unfinished_making_does_not_count_and_the_finished_output_does",
    _COMPLETION + "test_eating_counts_the_actual_interval_quantity",
    _COMPLETION + "test_a_progressed_goal_and_a_claimed_completion_leave_the_errand_open",
    _COMPLETION + "test_an_out_of_stock_rejection_leaves_the_errand_and_its_progress_unchanged",
    _COMPLETION + "test_a_closed_store_or_an_unaffordable_purchase_leaves_the_errand_open",
    _COMPLETION + "test_a_gift_followed_by_a_cancel_in_one_round_credits_nothing",
    _COMPLETION + "test_completion_and_progress_events_cite_their_evidence_and_carry_no_decision_identity",
]

_IDEMPOTENCE = "tests/life/test_errand_settlement_idempotence.py::"

#: 「结算幂等」：全部经真实请求、引擎与 SQLite——重放的动作结果不再累计；同一订单的配送再到期一次没有新入库行、不再累计；
#: 并行开始的合并结果只读顶层订单；决策轮提交与时钟推进提交各结算一次（数量 3 的事务两次各加 1 份）；两人同一轮里排在
#: 新增事件之前的给予不算、排在之后的算。
_IDEMPOTENCE_TESTS = [
    _IDEMPOTENCE + "test_a_replayed_action_adds_nothing",
    _IDEMPOTENCE + "test_a_repeated_due_delivery_adds_nothing",
    _IDEMPOTENCE + "test_a_parallel_start_counts_each_order_once",
    _IDEMPOTENCE + "test_each_commit_settles_once",
    _IDEMPOTENCE + "test_evidence_ordered_before_the_noting_event_in_the_same_commit_does_not_count",
]

_REMINDER = "tests/life/test_errand_reminder.py::"
_ERRAND_EXPORT = ("tests/footprints/test_errand_export.py::"
                  "test_an_exported_run_reports_errands_and_recertifies_value_for_value")

#: 「事务提醒」：前九个经真实请求、引擎与 SQLite——人物以收尾请求记下带提醒钟点的事务，随后经真实的时钟推进与决策轮：清醒
#: 时到点的边界恰在提醒时刻，以 errand_reminder_due 提供一次决策并写一条人物可见、不带决策与动作身份的提醒事件，事务只多了
#: 提醒时刻（带新唤醒原因的结算与 decision_started 照常落库，进度层有只写名称的提醒记录、浓缩时不留句子，待办层钟点变
#: null）；钟点等于受理时刻取下一本地日；睡眠中不形成边界、其后仍在睡的零点边界静默且不交付不消耗，醒来的边界与
#: wake_from_sleep 一并交付；同刻到期的多条合并为一次机会、各写一条事件，与当日目标提醒同刻时按枚举顺序并列；办成或取消
#: 后不再提醒；更新钟点重新计一次；零点边界由零点节律入口在零点计划之后交付；未完成事务不改变空闲重估间隔与闭店预警。末
#: 两个守质量报告把 errand_reminder_due 计入已知原因：先是经真实请求、引擎、SQLite、共同检查点与认证导出的一段生活
#: （22:55 的提醒在共同身体结算与 decision_started 两处各计一次，两个未识别原因的分桶为空），后是以合成事件按同一份
#: 足迹词表分桶的补充证据（与同刻的当日目标提醒各计一次）。
_REMINDER_TESTS = [
    _REMINDER + "test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged",
    _REMINDER + "test_a_clock_equal_to_the_acceptance_time_means_the_next_local_day",
    _REMINDER + "test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake"
                "_boundary",
    _REMINDER + "test_errands_due_together_are_one_opportunity_with_one_event_each",
    _REMINDER + "test_errand_and_goal_reminders_due_together_are_listed_in_enum_order",
    _REMINDER + "test_completed_or_cancelled_errands_are_not_reminded",
    _REMINDER + "test_updating_the_reminder_clock_arms_it_again",
    _REMINDER + "test_a_reminder_due_at_local_midnight_is_delivered_by_the_midnight_rhythm",
    _REMINDER + "test_open_errands_change_neither_the_reconsideration_interval_nor_the_closing_warning",
    _ERRAND_EXPORT,
    "tests/footprints/test_quality.py::test_errand_reminder_due_is_a_known_wake_reason",
]

_ERRAND_REQUESTS = "tests/life/test_errand_requests.py::"

#: 「事务变更与原子受理」：前六个经真实请求、引擎与 SQLite——三个控制工具带请求级 errands 受理，每条被受理的变更恰好一条
#: 系统可见、不带决策与动作身份的事务事件，与动作结果同一提交，回执按请求顺序（内容没有变化的更新也写事件，点名暂停的
#: 活动播报照样恢复它）；空数组合法且回执为空数组；每种结构错误在环境 _dispatch 以完整 errands[j].<键> 路径拒绝、与宿主的
#: 参数报告逐项相同；收尾在事务复核之后被业务拒绝时事务变更一并不生效、没有事务事件；请求里无法写「完成」；重放不再记下。
#: 第七个守三个控制工具的 errands 是内联、封闭、八键全部必填且按序的变更对象数组，通过 DeepSeek strict 校验。
#: 末一个是「被业务拒绝的收尾：记忆入库而事务未记下」的人物一侧：真实 LifeAgent 经 LifeEnv 登记带合法事务新增的收尾，
#: 登记时照常写入收尾记忆（memory_updated 只列这一条），结算被拒后人物没有未完成事务、没有事务事件。
_ERRAND_CHANGE_TESTS = [
    _ERRAND_REQUESTS + "test_each_control_tool_records_errand_changes_with_one_event_per_change_and_an_ordered_receipt",
    _ERRAND_REQUESTS + "test_empty_errands_is_valid_and_the_receipt_is_an_empty_list",
    _ERRAND_REQUESTS + "test_every_structural_error_reports_the_full_errand_path",
    _ERRAND_REQUESTS + "test_a_rejected_request_registers_no_errand_and_writes_no_errand_event",
    _ERRAND_REQUESTS + "test_a_request_cannot_mark_an_errand_completed",
    _ERRAND_REQUESTS + "test_a_replayed_request_does_not_register_twice",
    "tests/integration/test_activity_schema.py::test_control_tool_schemas_inline_the_errand_change_object",
    "tests/integration/test_conclude_memories.py::test_rejected_conclusion_keeps_its_memories_but_registers_no_errand",
]

_ERRAND_NAMES = "tests/life/test_errand_names.py::"

#: 「事务名称与错误」：前九个经真实请求、引擎与 SQLite，每一轮由同一次冻结观察准备的 LifeAgent 先在宿主一侧本地校验，
#: 再把同一请求原样登记给引擎结算——更新与取消按待办层名称（无损归一后唯一即受理，回执写待办层名称）；找不到或有歧义的
#: 事务名两侧逐项相同、合法值列本人全部未完成事务名、不替换；商品名错误在 why 给最接近的至多三个商品名、合法值为空；
#: 取消必带原因；新增重名、同结果同商品与第九条各一例；同一请求里同一条未完成事务被第二条更新或取消点名、两条新增的结果
#: 种类与商品相同各一例（只报较晚的一条）；同一请求取消旧事务并以原名、新数量新增被受理，终结后名称可再用；
#: 绕过宿主的请求在环境被合并成一次 ACTION_REQUEST_INVALID、照常计入同刻轮数、世界不变；性质测试以固定种子枚举二百四十
#: 组变更，宿主受理的环境对同一观察也受理（每种状态另有一组经真实提交）。第十个是宿主一侧的合并纠错：直接导入的
#: LifeAgent 把活动名、事务名与结构错误并入同一次纠错，路径绑定到真实调用下标，改正之前不登记世界请求。末一个是「终结后
#: 名称可以再用」的办成一半（前面的节点以取消与办成留下的状态示范）：经真实请求、引擎与 SQLite 在集市记下并当场买下办成
#: 之后，同一名称、同一结果与商品再新增一条事务被受理，事件依次是记下、办成、记下。
_ERRAND_NAME_TESTS = [
    _ERRAND_NAMES + "test_update_and_cancel_use_the_todo_layer_name",
    _ERRAND_NAMES + "test_unknown_or_ambiguous_errand_name_lists_every_open_name_and_substitutes_nothing",
    _ERRAND_NAMES + "test_unknown_item_reports_nearest_product_names_in_why_without_candidates",
    _ERRAND_NAMES + "test_cancel_requires_a_reason",
    _ERRAND_NAMES + "test_duplicate_added_names_same_outcome_and_product_and_a_ninth_errand_are_rejected",
    _ERRAND_NAMES + "test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected",
    _ERRAND_NAMES + "test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted",
    _ERRAND_NAMES + "test_a_request_bypassing_the_host_is_refused_once_and_the_world_is_unchanged",
    _ERRAND_NAMES + "test_whatever_the_host_accepts_the_environment_accepts",
    _PROTOCOL + "test_errand_errors_join_the_single_host_correction",
    _COMPLETION + "test_a_completed_errand_frees_its_name_and_its_item_for_a_new_errand",
]

_CONTROL_ITEMS = "tests/life/test_control_items.py::"
_WINDOW_TESTS = "tests/delivery/test_windows.py::"
_ERRAND_WINDOWS = "tests/integration/test_errand_delivery_windows.py::"
_PROJECTIONS_TRIMMED = (_WINDOW_TESTS + "test_intent_and_dreaming_projections_drop_memories_views_and_errands"
                        "_and_leave_the_event_intact")
_FULL_TARGET = _WINDOW_TESTS + "test_the_action_target_keeps_the_full_control_request"
_EXPORT_WINDOWS = _ERRAND_WINDOWS + "test_an_export_with_errand_reminder_and_completion_builds_all_three_windows"
_FOREIGN_COMPLETION = (_ERRAND_WINDOWS
                       + "test_a_completion_caused_by_another_persons_action_stays_out_of_their_decision_group")
_REAL_PROJECTIONS = (_ERRAND_WINDOWS + "test_control_requests_with_errands_reach_windows_without_them_and_legacy"
                     "_requests_are_refused")

#: 「交付边界」，按台账各项的顺序：
#: - 「意图与 dreaming 投影不含 memories、views 与请求级 errands，原事件不被原地修改」与「动作目标保留完整请求」：第三个节点
#:   经真实请求、引擎、SQLite 与认证导出——记下三条事务的收尾与带一条更新、一条取消的播报，在意图窗口与陈曦每个显示它们的
#:   dreaming 窗口里投影都只留每项的活动名与自述、不带 errands，动作与意图窗口的目标就是导出里的请求事件、逐键相等、保留
#:   完整的 errands（投影没有原地改写它）。头两个以手写事件直调窗口构造（_action_windows、_dream_windows），是补充证据与
#:   变异杀手：两项都带记忆与看法的收尾请求，投影两项都只留活动名与自述（不只裁首项），构造完三类窗口后原请求事件逐键不变。
#: - 「事务事件不带决策与动作身份，他人动作触发的办成事件不落进对方决策的分组」：真实引擎、SQLite、共同检查点与认证导出
#:   上，李凡两次当面给陈曦纯牛奶，累计与办成两条事件归陈曦、以李凡的给予结果为原因，六种事务事件都没有决策、动作与活动
#:   身份，李凡那两次给予的决策分组与动作分组里没有陈曦的事件，任何窗口的目标都不含事务事件。
#: - 「缺 errands 的旧控制请求被拒」：第三个节点的后一半——经 FootprintStore 补登一条带 items 而缺请求级 errands 的旧形状
#:   播报请求并认证导出，交付构造窗口时以完整性故障拒绝，不当作没有事务变更放行。其后三个是补充证据：唯一读取入口
#:   control_items、进度层与交付窗口的投影 control_request、请求级变更的读取 control_errands 与收尾记忆判定一律拒绝，
#:   手写事件的窗口构造同样拒绝；control_errands 按请求顺序读出每条变更的八键。
#: - 「含事务提醒与办成记录的导出能构造三类窗口」：同一段真实生活的认证导出构造出动作、意图与 dreaming 三类窗口，提醒与
#:   办成两种事件本人可见，作为办成之后那次决策观察的进度来源展开为证据、公开投影是空对象；提醒带来的那次决策的窗口里
#:   待办层（含三条事务行）是存储的那一份。
_DELIVERY_BOUNDARY_TESTS = [
    _PROJECTIONS_TRIMMED,
    _FULL_TARGET,
    _REAL_PROJECTIONS,
    _FOREIGN_COMPLETION,
    _CONTROL_ITEMS + "test_a_current_request_without_errands_is_rejected",
    _WINDOW_TESTS + "test_control_requests_without_errands_are_rejected_by_delivery",
    _CONTROL_ITEMS + "test_control_errands_reads_every_change_in_order",
    _EXPORT_WINDOWS,
]

_ERRAND_METRICS = "tests/evaluation/test_errand_metrics.py::"

#: 「事务与收尾记忆指标」，按台账各项的顺序：头七个以已认证事件模型构造的导出事件逐键核对 errand_metrics 与
#: conclusion_memories 的定义（分母是记下的事务而不是当日目标；终态恰在零点不算跨夜、截止恰在零点算；同期观察数与可见
#: 次数；提醒后行动只认同一本地日、终态之前、本人被受理且指向同一商品的请求；办成须有本导出里更早且可复核的证据；收尾
#: 记忆的作息与重合计数；没有样本为 null），并经 build_quality_report 进入报告，是补充证据与变异杀手；其后两个是「没有
#: 样本为 null」经真实请求、引擎、SQLite 与认证导出的两半：两人完整一天没有记下任何事务，quality.json 的 errand_metrics
#: 为 null；含三条事务而没有任何收尾记忆的那段生活，conclusion_memories 为 null。再一个守当日目标报告的键集不变（命令
#: 导出的 quality.json 里事务分组是 day_plan_metrics 之外的顶层分组）；再一个守质量报告结构 4，分组与记录引擎设计登记的
#: 十八组逐一相同、顺序相同；末一个经真实请求、引擎、SQLite、共同检查点与认证导出：跨本地零点的一段两人生活里记下、
#: 提醒、带收尾记忆结束、送达办成、零点后取消与到终点仍未完成，quality.json 的两个分组逐值等于手算，validate_export 从
#: 同一批事件重算出相同的报告。
_METRIC_TESTS = [
    _ERRAND_METRICS + "test_counts_follow_their_definitions_and_the_identity_holds",
    _ERRAND_METRICS + "test_the_denominator_is_registered_errands_not_goals",
    _ERRAND_METRICS + "test_visible_and_expected_observations_agree",
    _ERRAND_METRICS + "test_acted_after_reminder_needs_an_accepted_request_for_the_same_product",
    _ERRAND_METRICS + "test_a_completion_without_checkable_evidence_is_counted",
    _ERRAND_METRICS + "test_conclusion_memories_count_routine_and_errand_overlap",
    _ERRAND_METRICS + "test_no_errand_or_no_conclusion_memory_gives_null",
    "tests/integration/test_dayrun_complete_days.py::"
    "test_complete_two_person_day_uses_real_tools_memory_and_restoration",
    _REAL_PROJECTIONS,
    "tests/footprints/test_goal_export.py::test_export_command_uses_authenticated_cutoff_and_manifest_code",
    "tests/footprints/test_quality.py::test_report_sections_are_the_documented_grouping_without_a_language_count",
    _ERRAND_EXPORT,
]

#: 验收台账行（首列原文）→ 所属阶段与证据测试节点；未完成阶段的行随该阶段的提交补节点。
LEDGER: dict[str, dict[str, object]] = {
    "严格反思": {"stage": "1", "tests": _REFLECTION_TESTS},
    "只读查询转发": {"stage": "1", "tests": _READ_QUERY_TESTS},
    "计划重复意图": {"stage": "1", "tests": _PLAN_DUPLICATE_TESTS},
    "故障传播": {"stage": "1", "tests": _FAULT_PROPAGATION_TESTS},
    "事务变更与原子受理": {"stage": "2", "tests": _ERRAND_CHANGE_TESTS},
    "事务名称与错误": {"stage": "2", "tests": _ERRAND_NAME_TESTS},
    "跨夜可见": {"stage": "2", "tests": _OVERNIGHT_TESTS},
    "完成依据": {"stage": "2", "tests": _COMPLETION_TESTS},
    "结算幂等": {"stage": "2", "tests": _IDEMPOTENCE_TESTS},
    "事务提醒": {"stage": "2", "tests": _REMINDER_TESTS},
    "检查点": {"stage": "2", "tests": _CHECKPOINT_TESTS},
    "提示与唯一出现": {"stage": "2", "tests": _PROMPT_PRESENCE_TESTS},
    "交付边界": {"stage": "2", "tests": _DELIVERY_BOUNDARY_TESTS},
    "事务与收尾记忆指标": {"stage": "2", "tests": _METRIC_TESTS},
    "作息锚点": {"stage": "3", "tests": []},
    "工作安排加载": {"stage": "3", "tests": []},
    "上班安排呈现": {"stage": "3", "tests": []},
    "计划与义务": {"stage": "3", "tests": []},
    "工作合同边界": {"stage": "3", "tests": []},
    "工作负荷与作息偏离": {"stage": "3", "tests": []},
    "查询请求": {"stage": "4", "tests": []},
    "来源筛选": {"stage": "4", "tests": []},
    "可执行筛选": {"stage": "4", "tests": []},
    "分页与返回": {"stage": "4", "tests": []},
    "查询只读": {"stage": "4", "tests": []},
    "工程门禁": {"stage": "5", "tests": []},
    "复跑指标（只报告）": {"stage": "6", "tests": []},
    "文档一致性": {"stage": "7", "tests": []},
}

#: 只报告的台账行：规格写明不作门禁；行本身的真实运行不在测试中执行。
REPORT_ONLY: dict[str, str] = {
    "复跑指标（只报告）": "白领与商务的配对全天复跑按规格只报告、不作门禁：它是计费的真实运行，只在用户明确要求时启动，"
                        "指标与全部成功、失败写进验收记录；少于每臂三次时验收记录写明未达复跑要求。",
}

_AGENT_MODULE = "custom.agents.life_agent"
_AGENT_SOURCE = "custom/agents/life_agent.py"
_MODELS_MODULE = "worldmock.life.models"
_MODELS_SOURCE = "src/worldmock/life/models.py"
#: 计划重复意图三个变异的杀手，各取修订入口经引擎的一例与零点计划经后台任务的一例：前一组守逐条定位（只报笼统错误、
#: 只定位后一条），后一组守超限与重复同批报告（超限提前返回）。
_PLAN_DUPLICATE_KILLERS = (
    _REVISION + "test_engine_rejects_a_duplicate_revision_locating_first_and_later_entries",
    _PLAN_DAY + "test_plan_day_duplicate_intents_are_fed_back_with_every_position_then_registered",
)
_PLAN_CAPACITY_KILLERS = (
    _REVISION + "test_engine_reports_over_capacity_and_duplicates_in_the_same_rejection",
    _PLAN_DAY + "test_plan_day_reports_over_capacity_together_with_duplicates",
)
_RUNTIME_MODULE = "worldmock.runtime"
_RUNTIME_SOURCE = "src/worldmock/runtime.py"
_READ_QUERY_MODULE = "worldmock.life.read_query_runtime"
_READ_QUERY_SOURCE = "src/worldmock/life/read_query_runtime.py"
#: 只读查询转发六个变异的杀手，全部在主进程执行被变异的代码且不启动 Ray：装配用例（三个「改回 AS2 原类」：环境代理、
#: 交给人物的服务代理与 actor 类；代理不等待远端结果时经人物一侧的 read_query 得到协程而不是结果）、主进程内直调 actor
#: 的用例（actor 不把查询交给环境；真实 Ray 下 actor 在 worker 里，杀不到）与捕获 ray.remote 参数的用例（并发度）。
#: 真实 Ray 用例只作台账证据、不作杀手：变异子进程里 Ray 启动失败与断言失败同为退出码 1，杀死不能取决于基础设施。
_ASSEMBLY_KILLERS = (_READ_QUERY + "test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query",)
_PROXY_KILLERS = _ASSEMBLY_KILLERS
_ACTOR_KILLERS = (_READ_QUERY + "test_unwrapped_actor_read_query_equals_direct_environment_call",)
_CONCURRENCY_KILLERS = (_READ_QUERY + "test_life_env_actor_class_fixes_single_concurrency_without_parameters",)
_REFLECTION_MODULE = "custom.agents.life_reflection"
_REFLECTION_SOURCE = "custom/agents/life_reflection.py"
_MEMORY_MODULE = "custom.agents.life_memory"
_MEMORY_SOURCE = "custom/agents/life_memory.py"
_WINDOWS_MODULE = "worldmock.delivery.windows"
_WINDOWS_SOURCE = "src/worldmock/delivery/windows.py"
#: 严格反思的杀手都经真实记忆运行时：前一个逐种合同违约各重试一次（本地校验被放宽、重试上限为 0 时首个响应的去向不同），
#: 后一个四次都不合合同（不设上限时脚本在第五次调用直接失败；返回空列表时不再抛出；附回逐次累积时消息数不再恒为三）。
#: 两个模块都按本名导入（记忆运行时由人物类正常导入），经直接导入的 LifeAgent 跑真实引擎的那一例也执行变异后的代码。
_REFLECTION_CONTRACT_KILLERS = (
    _REFLECTION + "test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection",
)
_REFLECTION_RETRY_KILLERS = (
    _REFLECTION + "test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection",
)
_REFLECTION_FAULT_KILLERS = (
    *_REFLECTION_RETRY_KILLERS,
    _REFLECTION + "test_exhausted_reflection_leaves_evidence_but_no_memory_update_in_the_run_database",
)
_WINDOW_KILLERS = (
    "tests/delivery/test_windows.py::test_reflection_requests_after_the_action_never_become_the_decision_prompt",
)
_PIPELINE_MODULE = "worldmock.delivery.pipeline"
_PIPELINE_SOURCE = "src/worldmock/delivery/pipeline.py"
#: 「版本 8 检查点被放行」的两个变异：世界状态模型同时接受版本 8（杀手是只拒绝版本的模型用例与重开运行的真实引擎用例），
#: 交付入口放行版本 8 的导出（杀手是交付门禁用例：版本 8 的导出到达被替换为失败的窗口构造）。
_VERSION_8_STATE_KILLERS = (
    _SESSION_STATE + "test_world_state_schema_version_8_is_rejected",
    _SESSION_STATE + "test_run_with_version_8_world_state_is_rejected_on_open",
)
_VERSION_8_EXPORT_KILLERS = (_PUBLICATION + "test_delivery_gate_rejects_version_eight_exports",)
_TODO_LAYER_MODULE = "worldmock.life.todo_layer"
_TODO_LAYER_SOURCE = "src/worldmock/life/todo_layer.py"
_SESSION_ACTIONS_MODULE = "worldmock.life.session_actions"
_SESSION_ACTIONS_SOURCE = "src/worldmock/life/session_actions.py"
_CONTROL_ITEMS_MODULE = "worldmock.life.control_items"
_CONTROL_ITEMS_SOURCE = "src/worldmock/life/control_items.py"
#: 事务受理三个变异的杀手都经真实请求、引擎与 SQLite：待办层不投影事务时下一次观察的待办层没有刚记下的事务；收尾的复核
#: 一行把变更就地写进暂存世界时，活动检查处的业务拒绝也会把事务落库；枚举多出「完成」时写「完成」的请求不再按枚举外的
#: 值拒绝（交叉约束表没有这一种，校验直接出错）。缺 errands 的变异由 control_items 的直接用例杀死。
_TODO_ERRANDS_KILLERS = (
    _ERRAND_REQUESTS + "test_each_control_tool_records_errand_changes_with_one_event_per_change_and_an_ordered_receipt",
)
_REJECTED_ERRANDS_KILLERS = (
    _ERRAND_REQUESTS + "test_a_rejected_request_registers_no_errand_and_writes_no_errand_event",
)
_COMPLETED_CHANGE_KILLERS = (_ERRAND_REQUESTS + "test_a_request_cannot_mark_an_errand_completed",)
_ERRANDS_KEY_KILLERS = (_CONTROL_ITEMS + "test_a_current_request_without_errands_is_rejected",)
_ERRANDS_MODULE = "worldmock.life.errands"
_ERRANDS_SOURCE = "src/worldmock/life/errands.py"
#: 事务名称四个变异的杀手都经真实请求、引擎与 SQLite（宿主一侧的 LifeAgent 与环境都按本名导入 errands，同一个被变异的
#: 模块）：更新或取消的名称解析不出唯一一条时改取第一个同级匹配或最接近的名称，有歧义的取消就被受理、预置的事务被移出；
#: 新增改按扣除取消之前的全部未完成事务判重名与同结果同商品时，同一请求取消旧事务再以原名新增被拒；同一事务被第二条更新
#: 或取消点名不再判出、两条新增的同结果同商品不再判出时，两侧的明细都少了那一条。
_FIRST_CANDIDATE_KILLERS = (
    _ERRAND_NAMES + "test_unknown_or_ambiguous_errand_name_lists_every_open_name_and_substitutes_nothing",)
_CANCELS_FIRST_KILLERS = (
    _ERRAND_NAMES + "test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted",)
_DOUBLED_KILLERS = (
    _ERRAND_NAMES + "test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected",)
_ENGINE_MODULE = "worldmock.life.engine"
_ENGINE_SOURCE = "src/worldmock/life/engine.py"
_SETTLED_TEXT_MODULE = "worldmock.life.settled_text"
_SETTLED_TEXT_SOURCE = "src/worldmock/life/settled_text.py"
#: 完成依据与结算各变异的杀手都经真实请求、引擎与 SQLite（结算由引擎的两处接线按本名调用 errands.settle）：未送达订单
#: 当作取得时远程下单受理那次提交就办成；受理事件也读时到店买一份就累计两份、数量 2 的事务办成；开始进食的动作结果按
#: 所开吃的份数算作吃掉时开始喝的那一刻就办成、结算事件只剩一条；推进了当日目标的活动在其后的进度结算里算作办成、播报
#: 或收尾的自述写了事务名就算办成时，从未取得的纯牛奶事务被办成；读活动快照的制作配方时未完成的制作区间就算产出、办成
#: 事件的原因不再是完整结束的那次进度结算；记下时把已持有的份数记成累计量时，家里已有的两份让事务不再是 0 份；去掉新增
#: 位置的比较时排在新增事件之前的给予也算；去掉区间起点的比较时记下前开始的进度区间也算；不读 given 时当面给予不算；按
#: 事件人物字段归属时给予与送她的那单算到付款的李凡、她买给李凡的那单算到她自己；也读逐项结果时并行开始的同一订单算两次；
#: 同一提交结算两次时决策轮那次累计 2 份、时钟推进那次累计到 3 份办成；不看 reused 时重放的到店购买再累计一份；浓缩句
#: 为空时办成记录没有 settled 句。
_REMOTE_ORDER_KILLERS = (_COMPLETION + "test_a_remote_order_completes_only_in_the_commit_that_delivers_it",)
_RESULT_ONLY_KILLERS = (_COMPLETION + "test_quantity_two_with_one_obtained_stays_open_and_shows_the_progress",)
_START_KILLERS = (_COMPLETION + "test_eating_counts_the_actual_interval_quantity",)
_CLAIM_KILLERS = (_COMPLETION + "test_a_progressed_goal_and_a_claimed_completion_leave_the_errand_open",)
_MAKING_KILLERS = (_COMPLETION + "test_unfinished_making_does_not_count_and_the_finished_output_does",)
_HELD_KILLERS = (_COMPLETION + "test_what_was_held_before_noting_does_not_count",)
_NOTING_ORDER_KILLERS = (
    _IDEMPOTENCE + "test_evidence_ordered_before_the_noting_event_in_the_same_commit_does_not_count",)
_INTERVAL_KILLERS = (_COMPLETION + "test_a_progress_interval_begun_before_noting_does_not_count",)
_RECIPIENT_KILLERS = (
    _COMPLETION + "test_bought_for_me_and_handed_to_me_count_but_bought_by_me_for_another_does_not",)
_TOP_LEVEL_KILLERS = (_IDEMPOTENCE + "test_a_parallel_start_counts_each_order_once",)
_ONCE_PER_COMMIT_KILLERS = (_IDEMPOTENCE + "test_each_commit_settles_once",)
_REPLAY_KILLERS = (_IDEMPOTENCE + "test_a_replayed_action_adds_nothing",)
_FOLDED_LINE_KILLERS = (
    "tests/life/test_progress_events.py::test_an_errand_completed_in_the_session_is_one_record_and_folds_to_one_line",)
_SCHEDULE_MODULE = "worldmock.life.schedule"
_SCHEDULE_SOURCE = "src/worldmock/life/schedule.py"
_RHYTHM_MODULE = "worldmock.life.rhythm"
_RHYTHM_SOURCE = "src/worldmock/life/rhythm.py"
_FOOTPRINT_MODELS_MODULE = "worldmock.footprints.models"
_FOOTPRINT_MODELS_SOURCE = "src/worldmock/footprints/models.py"
#: 事务提醒各变异的杀手都经真实请求、引擎与 SQLite（节律判定、边界候选与提醒交付由引擎按本名调用 schedule、rhythm、
#: reminders 与 errands）：取消不移出事务时被取消的那条仍在状态里、到点照样提醒；提醒时把事务移出时到点后事务不在了；
#: 事务提醒进入睡眠允许集合时零点边界给睡着的人提供决策；未完成事务算在手承诺时记下事务后下一次决策按常规间隔提前到
#: 600 秒；词表漏登新唤醒原因时带它的共同身体结算落不了库、推进即失败；边界候选不按睡眠过滤时入睡后提醒时刻仍是候选、
#: 在 23:30 形成静默边界；不看唤醒判定就交付时零点边界给睡着的人记下提醒并写事件；同刻多条只写一条时两条同刻到期的
#: 事务只有一条提醒事件。
_CANCELLED_REMINDER_KILLERS = (_REMINDER + "test_completed_or_cancelled_errands_are_not_reminded",)
_AWAKE_REMINDER_KILLERS = (
    _REMINDER + "test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged",)
_SLEEPER_REMINDER_KILLERS = (
    _REMINDER + "test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake"
                "_boundary",)
_COMMITMENT_KILLERS = (
    _REMINDER + "test_open_errands_change_neither_the_reconsideration_interval_nor_the_closing_warning",)
_TOGETHER_KILLERS = (_REMINDER + "test_errands_due_together_are_one_opportunity_with_one_event_each",)
_PROMPT_CAPACITY_MODULE = "worldmock.life.prompt_capacity"
_PROMPT_CAPACITY_SOURCE = "src/worldmock/life/prompt_capacity.py"
_OBSERVE_MODULE = "worldmock.life.observe"
_OBSERVE_SOURCE = "src/worldmock/life/observe.py"
#: 跨夜与检查点各变异的杀手都经真实请求、引擎与 SQLite（引擎、观察与世界状态模型按本名导入；浓缩由直接导入的 LifeAgent
#: 按本名调用 prompt_capacity.fit_request）：零点使旧计划失效时一并清空事务、修订计划时一并清空事务，零点或修订之后事务
#: 不再原样；浓缩每折叠一步就把待办层的事务清空时，发出的请求里待办层没有事务；记忆层占到窗口一半以上（截取后的大小
#: 仍在其上）就让待办层不列事务时，记忆层超窗的那次观察的待办层没有事务；人物状态的 errands 在序列化时被略去，检查点
#: 恢复出的分支没有事务、待办层不同。
_OVERNIGHT_KILLERS = (
    _OVERNIGHT + "test_an_errand_stays_in_the_todo_layer_after_midnight_plan_generation_and_plan_revision",)
_CONDENSED_KILLERS = (_OVERNIGHT + "test_a_condensed_request_still_carries_the_todo_layer_errands",)
_OVERFLOW_KILLERS = (_OVERNIGHT + "test_an_errand_stays_when_the_memory_layer_overflows_its_window",)
_CHECKPOINT_KILLERS = (_RESTORED_BRANCH,)
_PROGRESS_LAYER_MODULE = "worldmock.life.progress_layer"
_PROGRESS_LAYER_SOURCE = "src/worldmock/life/progress_layer.py"
_MEMORY_LAYER_MODULE = "worldmock.life.memory_layer"
_MEMORY_LAYER_SOURCE = "src/worldmock/life/memory_layer.py"
_PROMPT_MODULE = "custom.agents.life_prompt"
_PROMPT_SOURCE = "custom/agents/life_prompt.py"
#: 提示分工三个变异的杀手：进度层工具调用参数带上请求级 errands 时，人物在那段停留里实际看到的收尾调用参数多出 errands、
#: 备注出现在进度层；self_notes 把请求里的事务名与备注当成自述时，离开那段停留后实际请求的记忆层出现事务的名称与备注
#: （两者都经真实 LifeAgent、LifeEnv 与 SQLite，引擎按本名导入这两个模块）；生活决策系统层第 6 条改掉一个分句时，直接
#: 导入的 LifeAgent（按本名导入 custom.agents.life_prompt）发出的系统层与接口合同登记不再逐字相同。
_SEPARATION_KILLERS = (_SEPARATED,)
_VERBATIM_KILLERS = (_SYSTEM_VERBATIM,)
_ERRAND_METRICS_MODULE = "worldmock.evaluation.errand_metrics"
_ERRAND_METRICS_SOURCE = "src/worldmock/evaluation/errand_metrics.py"
#: 交付边界与指标各变异的杀手：提醒与办成写成系统可见时，办成之后那次观察的进度来源不再是本人可见的事件，认证导出构造
#: 窗口以完整性故障失败（真实引擎、SQLite 与认证导出，errands 由引擎按本名导入）；结算事件沿用证据事件的决策与动作身份
#: 时，李凡那次给予的决策分组里出现陈曦的累计与办成事件（同一条真实链路）；投影带出请求级 errands、只裁第一项或原地修改
#: 原事件时，意图与 dreaming 输入的投影不再只留两项的活动名与自述、原请求事件被改写（动作窗口的目标随之丢掉 errands 与
#: 记忆）——带出 errands 与原地修改另由真实导出的那一例杀死（真实生活里的收尾与播报都只点名一项，只裁首项在那里不可
#: 区分）；事务指标以当日目标数为分母时，三条目标、两条事务的导出报出 3。
_VISIBLE_KILLERS = (_EXPORT_WINDOWS,)
_IDENTITY_KILLERS = (_FOREIGN_COMPLETION,)
_PROJECTION_KILLERS = (_PROJECTIONS_TRIMMED,)
_ERRANDS_PROJECTION_KILLERS = (_PROJECTIONS_TRIMMED, _REAL_PROJECTIONS)
_INTACT_KILLERS = (_PROJECTIONS_TRIMMED, _FULL_TARGET, _REAL_PROJECTIONS)
_DENOMINATOR_KILLERS = (_ERRAND_METRICS + "test_the_denominator_is_registered_errands_not_goals",)

#: 源码变异清单；每个阶段的提交在这里追加自己的条目（未实现阶段的原文还不存在，无法预先登记）。
MUTATIONS: tuple[SourceMutation, ...] = (
    SourceMutation(
        "decision_failure_swallowed_after_log",
        _AGENT_MODULE,
        _AGENT_SOURCE,
        (
            '            logging.exception("LifeAgent decision failed for agent %s", self.id)\n'
            '            raise\n'
        ),
        (
            '            logging.exception("LifeAgent decision failed for agent %s", self.id)\n'
            '            return ""\n'
        ),
        (_PROTOCOL + "test_decision_failure_is_logged_once_and_reraised_unchanged",
         _FAILURE_STAGES + "test_model_service_failure_on_the_real_engine_is_logged_once_and_reraised_unchanged"),
    ),
    SourceMutation(
        "tool_dispatch_failure_loses_stage_code",
        _AGENT_MODULE,
        _AGENT_SOURCE,
        (
            '            feedback = _stage_feedback("TOOL_SERVICE_FAILED", ("tool_calls", index))\n'
            '            raise RuntimeError(feedback) from error\n'
        ),
        (
            '            feedback = _stage_feedback("TOOL_SERVICE_FAILED", ("tool_calls", index))\n'
            '            raise\n'
        ),
        (_FAILURE_STAGES + "test_tool_service_failure_points_to_real_call_without_claiming_execution",
         _FAILURE_STAGES + "test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage"),
    ),
    SourceMutation(
        "plan_duplicates_report_only_a_generic_error",
        _MODELS_MODULE,
        _MODELS_SOURCE,
        "        raise ValidationError.from_exception_data(model_name, errors)\n",
        '        raise ValueError("day goals must contain at most eight unique intents")\n',
        _PLAN_DUPLICATE_KILLERS,
    ),
    SourceMutation(
        "plan_duplicates_locate_only_later_entries",
        _MODELS_MODULE,
        _MODELS_SOURCE,
        "if len(indexes) > 1 for index in indexes)\n",
        "if len(indexes) > 1 for index in indexes[1:])\n",
        _PLAN_DUPLICATE_KILLERS,
    ),
    SourceMutation(
        "plan_over_capacity_returns_before_duplicates",
        _MODELS_MODULE,
        _MODELS_SOURCE,
        (
            '        errors.append({"type": "value_error", "loc": (), "input": '
            '[goal.model_dump(mode="json") for goal in goals],\n'
            '                       "ctx": {"error": ValueError("day goals require at most eight entries")}})\n'
        ),
        '        raise ValueError("day goals require at most eight entries")\n',
        _PLAN_CAPACITY_KILLERS,
    ),
    SourceMutation(
        "assembly_proxy_reverts_to_the_as2_class",
        _RUNTIME_MODULE,
        _RUNTIME_SOURCE,
        '        env = LifeEnvProxy(actor, run_dir=prepared.run_dir, env_module_types=["LifeEnv"])\n',
        (
            '        from agentsociety2.env.env_router_proxy import EnvRouterProxy\n\n'
            '        env = EnvRouterProxy(actor, run_dir=prepared.run_dir, env_module_types=["LifeEnv"])\n'
        ),
        _ASSEMBLY_KILLERS,
    ),
    SourceMutation(
        "assembly_service_proxy_reverts_to_the_as2_class",
        _RUNTIME_MODULE,
        _RUNTIME_SOURCE,
        "        service = build_service_proxy(env, run_dir=prepared.run_dir, trace=False, replay=False)\n",
        (
            "        from agentsociety2.env.env_router_proxy import EnvRouterProxy\n\n"
            "        service = build_service_proxy(\n"
            '            EnvRouterProxy(actor, run_dir=prepared.run_dir, env_module_types=["LifeEnv"]),\n'
            "            run_dir=prepared.run_dir, trace=False, replay=False)\n"
        ),
        _ASSEMBLY_KILLERS,
    ),
    SourceMutation(
        "assembly_actor_reverts_to_the_as2_class",
        _RUNTIME_MODULE,
        _RUNTIME_SOURCE,
        "    actor = life_env_actor_class().remote(spec)\n",
        (
            "    from agentsociety2.env.direct_env_actor import get_direct_env_actor_class\n\n"
            "    actor = get_direct_env_actor_class().remote(spec)\n"
        ),
        _ASSEMBLY_KILLERS,
    ),
    SourceMutation(
        "proxy_returns_without_awaiting_the_remote_result",
        _READ_QUERY_MODULE,
        _READ_QUERY_SOURCE,
        "        return await self._actor.read_query.remote(context, tool, request)\n",
        "        return self._actor.read_query.remote(context, tool, request)\n",
        _PROXY_KILLERS,
    ),
    SourceMutation(
        "actor_answers_without_the_environment",
        _READ_QUERY_MODULE,
        _READ_QUERY_SOURCE,
        "        return await self._router._environment().read_query(context, tool, request)\n",
        "        return {}\n",
        _ACTOR_KILLERS,
    ),
    SourceMutation(
        "actor_class_allows_concurrent_calls",
        _READ_QUERY_MODULE,
        _READ_QUERY_SOURCE,
        "    return ray.remote(max_concurrency=1)(LifeEnvActor)\n",
        "    return ray.remote(max_concurrency=2)(LifeEnvActor)\n",
        _CONCURRENCY_KILLERS,
    ),
    SourceMutation(
        "reflection_accepts_free_text",
        _REFLECTION_MODULE,
        _REFLECTION_SOURCE,
        "    arguments = _tool_arguments(response)\n",
        (
            "    try:\n"
            "        arguments = _tool_arguments(response)\n"
            "    except ReflectionOutputError:\n"
            '        arguments = json.dumps({"insights": json.loads(response.choices[0].message.content)})\n'
        ),
        _REFLECTION_CONTRACT_KILLERS,
    ),
    SourceMutation(
        "reflection_accepts_another_tool",
        _REFLECTION_MODULE,
        _REFLECTION_SOURCE,
        '    if getattr(function, "name", None) != REFLECTION_TOOL:\n',
        "    if False:\n",
        _REFLECTION_CONTRACT_KILLERS,
    ),
    SourceMutation(
        "reflection_skips_unknown_reference_check",
        _REFLECTION_MODULE,
        _REFLECTION_SOURCE,
        "    if problems:\n",
        "    if False:\n",
        _REFLECTION_CONTRACT_KILLERS,
    ),
    SourceMutation(
        "reflection_count_bound_widened",
        _REFLECTION_MODULE,
        _REFLECTION_SOURCE,
        "        if not 1 <= len(insights) <= MEMORY_REFLECTION_MAX_INSIGHTS:\n",
        "        if not 1 <= len(insights) <= MEMORY_REFLECTION_MAX_INSIGHTS + 1:\n",
        _REFLECTION_CONTRACT_KILLERS,
    ),
    SourceMutation(
        "reflection_text_validation_skipped",
        _REFLECTION_MODULE,
        _REFLECTION_SOURCE,
        "        return validate_memory_text(insight)\n",
        "        return insight\n",
        _REFLECTION_CONTRACT_KILLERS,
    ),
    SourceMutation(
        "reflection_retry_bound_zero",
        _REFLECTION_MODULE,
        _REFLECTION_SOURCE,
        "REFLECTION_VALIDATION_RETRIES = 3\n",
        "REFLECTION_VALIDATION_RETRIES = 0\n",
        _REFLECTION_CONTRACT_KILLERS,
    ),
    SourceMutation(
        "reflection_retries_without_a_bound",
        _MEMORY_MODULE,
        _MEMORY_SOURCE,
        "                if attempt > REFLECTION_VALIDATION_RETRIES:\n",
        "                if False:\n",
        _REFLECTION_RETRY_KILLERS,
    ),
    SourceMutation(
        "reflection_failure_returns_an_empty_list",
        _MEMORY_MODULE,
        _MEMORY_SOURCE,
        (
            "                    raise ProtocolError(\n"
            '                        f"memory reflection output remained invalid after three retries: {error}") '
            "from error\n"
        ),
        "                    return []\n",
        _REFLECTION_FAULT_KILLERS,
    ),
    SourceMutation(
        "reflection_correction_accumulates_messages",
        _MEMORY_MODULE,
        _MEMORY_SOURCE,
        "                messages, attempt = reflection_correction(original, response, error), attempt + 1\n",
        "                messages, attempt = reflection_correction(messages, response, error), attempt + 1\n",
        _REFLECTION_RETRY_KILLERS,
    ),
    SourceMutation(
        "decision_prompt_ignores_action_order",
        _WINDOWS_MODULE,
        _WINDOWS_SOURCE,
        '                  and row["event_seq"] < action["event_seq"] and row["agent_id"] == action["agent_id"]\n',
        '                  and row["agent_id"] == action["agent_id"]\n',
        _WINDOW_KILLERS,
    ),
    SourceMutation(
        "version_8_world_state_accepted",
        _MODELS_MODULE,
        _MODELS_SOURCE,
        "    schema_version: Literal[WORLD_SCHEMA_VERSION]\n",
        "    schema_version: Literal[WORLD_SCHEMA_VERSION, 8]\n",
        _VERSION_8_STATE_KILLERS,
    ),
    SourceMutation(
        "version_8_export_passes_delivery_gate",
        _PIPELINE_MODULE,
        _PIPELINE_SOURCE,
        "    if schema_version != WORLD_SCHEMA_VERSION:\n",
        "    if schema_version not in {WORLD_SCHEMA_VERSION, 8}:\n",
        _VERSION_8_EXPORT_KILLERS,
    ),
    SourceMutation(
        "todo_layer_omits_errands",
        _TODO_LAYER_MODULE,
        _TODO_LAYER_SOURCE,
        '            "errands": errands.todo_rows(agent, names, context.scenario.timezone),\n',
        '            "errands": [],\n',
        _TODO_ERRANDS_KILLERS,
    ),
    SourceMutation(
        "errand_changes_applied_before_rejection",
        _SESSION_ACTIONS_MODULE,
        _SESSION_ACTIONS_SOURCE,
        '        changes = self._errand_changes(pending, "conclude_activity", request)\n',
        (
            '        changes = self._errand_changes(pending, "conclude_activity", request)\n'
            '        state.agents[pending.agent_id] = self._apply_errands(state, pending, changes)[0].agents[\n'
            '            pending.agent_id]\n'
        ),
        _REJECTED_ERRANDS_KILLERS,
    ),
    SourceMutation(
        "request_can_mark_errand_completed",
        _MODELS_MODULE,
        _MODELS_SOURCE,
        '    change: Literal["新增", "更新", "取消"] = Field(description="这条变更做什么，从列出的三种里选一种。")\n',
        '    change: Literal["新增", "更新", "取消", "完成"] = Field(description="这条变更做什么，从列出的三种里选一种。")\n',
        _COMPLETED_CHANGE_KILLERS,
    ),
    SourceMutation(
        "items_without_errands_accepted",
        _CONTROL_ITEMS_MODULE,
        _CONTROL_ITEMS_SOURCE,
        '        _list(_field(body, "errands", tool), tool)\n',
        "",
        _ERRANDS_KEY_KILLERS,
    ),
    SourceMutation(
        "errand_name_takes_first_candidate",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        "    return _named_rows(written, rows)\n",
        (
            "    found, matches = _named_rows(written, rows)\n"
            '    fallback = [*matches, *near_names(written, [row["name"] for row in rows])]\n'
            "    if len(found) != 1 and fallback:\n"
            '        found = [row for row in rows if row["name"] == fallback[0]][:1]\n'
            "    return found, matches\n"
        ),
        _FIRST_CANDIDATE_KILLERS,
    ),
    SourceMutation(
        "duplicates_judged_before_cancels",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '    remaining = [row for row in rows if row["errand_id"] not in cancelled]\n',
        "    remaining = list(rows)\n",
        _CANCELS_FIRST_KILLERS,
    ),
    SourceMutation(
        "errand_named_twice_accepted",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '        elif found[0]["errand_id"] in named:\n',
        "        elif False:\n",
        _DOUBLED_KILLERS,
    ),
    SourceMutation(
        "added_twins_with_one_product_accepted",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        "    doubled = [other for other in earlier if other in plan.products\n",
        "    doubled = [other for other in () if other in plan.products\n",
        _DOUBLED_KILLERS,
    ),
    SourceMutation(
        "pending_order_counts_as_obtained",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '    handed = [order for order in orders if order["status"] == "delivered"]\n',
        '    handed = [order for order in orders if order["status"] in {"delivered", "pending"}]\n',
        _REMOTE_ORDER_KILLERS,
    ),
    SourceMutation(
        "accepted_event_counts_as_evidence",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '    if event_type == "action_result":\n',
        '    if event_type in {"action_result", "action_accepted"}:\n',
        _RESULT_ONLY_KILLERS,
    ),
    SourceMutation(
        "activity_start_counts_as_eaten",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '        return [] if data.get("reused") else _handed(data.get("data") or {})\n',
        (
            '        payload = data.get("data") or {}\n'
            '        started = [payload["activity"]] if "activity" in payload else payload.get("activities", [])\n'
            '        return [] if data.get("reused") else [*_handed(payload), *(\n'
            '            Evidence(agent_id, _EAT, item["product_id"], float(item["quantity"]), None)\n'
            "            for activity in started\n"
            '            if activity.get("data", {}).get("request", {}).get("use_mode") == "eat"\n'
            '            for item in activity["data"]["request"]["items"])]\n'
        ),
        _START_KILLERS,
    ),
    SourceMutation(
        "progressed_goal_counts_as_completion",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        (
            "    for index, draft in enumerate(drafts):\n"
            "        for row in evidence_rows(draft.event_type, draft.agent_id, draft.data):\n"
        ),
        (
            "    for index, draft in enumerate(drafts):\n"
            "        plan = state.agents[draft.agent_id].day_plan if draft.agent_id in state.agents else None\n"
            "        claimed = [Evidence(draft.agent_id, errand.outcome, errand.product_id, float(errand.quantity),\n"
            "                            None)\n"
            "                   for errand in open_sets.get(draft.agent_id, {}).values()\n"
            '                   if draft.event_type == "activity_progress" and plan is not None\n'
            "                   and any(goal.progress_activity_id == draft.activity_id for goal in plan.goals)]\n"
            "        for row in [*evidence_rows(draft.event_type, draft.agent_id, draft.data), *claimed]:\n"
        ),
        _CLAIM_KILLERS,
    ),
    SourceMutation(
        "self_report_counts_as_completion",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        "    if not settled:\n        return state, []\n",
        (
            "    for draft in drafts:\n"
            '        named = (draft.data.get("data") or {}).get("activity_ids", []) '
            'if draft.event_type == "action_result" else []\n'
            "        notes = [state.activities[key].progress_note for key in named if key in state.activities]\n"
            "        for errand in [item for item in open_sets.get(draft.agent_id, {}).values()\n"
            "                       if any(item.name in note for note in notes)]:\n"
            "            head = _Head(draft.agent_id, state.current_time, (draft.event_id,))\n"
            "            settled.append(_credit(open_sets[draft.agent_id], errand, Evidence(\n"
            "                draft.agent_id, errand.outcome, errand.product_id, float(errand.quantity), None), head))\n"
            "    if not settled:\n        return state, []\n"
        ),
        _CLAIM_KILLERS,
    ),
    SourceMutation(
        "unfinished_making_counts_as_obtained",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '    transform = consumption.get("inventory_transform")\n',
        '    transform = data["activity"]["data"].get("inventory_recipe")\n',
        _MAKING_KILLERS,
    ),
    SourceMutation(
        "holdings_at_noting_count_as_obtained",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '    return agent.model_copy(update={"errands": errands}), receipt\n',
        (
            "    held = {product: sum(place.get(product, 0.0) for place in agent.inventory.values())\n"
            "            for product in plan.products.values()}\n"
            '    errands = {key: errand.model_copy(update={"progress": held[errand.product_id]})\n'
            "               if key not in agent.errands and held.get(errand.product_id) else errand\n"
            "               for key, errand in errands.items()}\n"
            '    return agent.model_copy(update={"errands": errands}), receipt\n'
        ),
        _HELD_KILLERS,
    ),
    SourceMutation(
        "evidence_before_noting_event_counts",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        "            if errand is None or noted.get(errand.errand_id, -1) > index:\n",
        "            if errand is None:\n",
        _NOTING_ORDER_KILLERS,
    ),
    SourceMutation(
        "interval_begun_before_noting_counts",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        "            if row.interval_start is not None and row.interval_start < errand.registered_at:\n",
        "            if False:\n",
        _INTERVAL_KILLERS,
    ),
    SourceMutation(
        "gift_is_not_obtained",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '    handed += payload.get("given", [])\n',
        "",
        _RECIPIENT_KILLERS,
    ),
    SourceMutation(
        "ownership_follows_event_agent",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        (
            '    if event_type == "action_result":\n'
            '        return [] if data.get("reused") else _handed(data.get("data") or {})\n'
            '    if event_type == "environment_event" and data.get("kind") == "delivery_arrival":\n'
            "        return _arrived(data)\n"
        ),
        (
            '    if event_type == "action_result":\n'
            '        return [] if data.get("reused") else [\n'
            '            row._replace(owner_id=agent_id) for row in _handed(data.get("data") or {})]\n'
            '    if event_type == "environment_event" and data.get("kind") == "delivery_arrival":\n'
            "        return [row._replace(owner_id=agent_id) for row in _arrived(data)]\n"
        ),
        _RECIPIENT_KILLERS,
    ),
    SourceMutation(
        "nested_results_orders_counted",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '    orders = payload.get("orders", [])\n',
        (
            '    orders = [*payload.get("orders", []),\n'
            '              *(order for row in payload.get("activity_results", []) for order in row.get("orders", []))]\n'
        ),
        _TOP_LEVEL_KILLERS,
    ),
    SourceMutation(
        "round_commit_settles_twice",
        _ENGINE_MODULE,
        _ENGINE_SOURCE,
        "        staged, settled = errands.settle(staged, drafts)\n",
        (
            "        staged, settled = errands.settle(staged, drafts)\n"
            "        staged, again = errands.settle(staged, drafts)\n"
            "        settled = settled + again\n"
        ),
        _ONCE_PER_COMMIT_KILLERS,
    ),
    SourceMutation(
        "advance_commit_settles_twice",
        _ENGINE_MODULE,
        _ENGINE_SOURCE,
        "        staged, credited = errands.settle(staged, drafts)\n",
        (
            "        staged, credited = errands.settle(staged, drafts)\n"
            "        staged, again = errands.settle(staged, drafts)\n"
            "        credited = credited + again\n"
        ),
        _ONCE_PER_COMMIT_KILLERS,
    ),
    SourceMutation(
        "replayed_result_counts",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '        return [] if data.get("reused") else _handed(data.get("data") or {})\n',
        '        return _handed(data.get("data") or {})\n',
        _REPLAY_KILLERS,
    ),
    SourceMutation(
        "condensation_drops_completion_line",
        _SETTLED_TEXT_MODULE,
        _SETTLED_TEXT_SOURCE,
        "        return [f\"{_clock(context, record['occurred_at'])} 办成「{record['errand']}」\"]\n",
        "        return []\n",
        _FOLDED_LINE_KILLERS,
    ),
    SourceMutation(
        "cancelled_errand_stays_armed",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '        errands.pop(plan.targets[index]["errand_id"])\n',
        '        errands.get(plan.targets[index]["errand_id"])\n',
        _CANCELLED_REMINDER_KILLERS,
    ),
    SourceMutation(
        "reminder_completes_errand",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        "        errands[errand.errand_id] = reminded\n",
        "        errands.pop(errand.errand_id)\n",
        _AWAKE_REMINDER_KILLERS,
    ),
    SourceMutation(
        "errand_reminder_wakes_sleep",
        _SCHEDULE_MODULE,
        _SCHEDULE_SOURCE,
        "     WakeReason.PLAN_REMINDER_DUE}\n",
        "     WakeReason.PLAN_REMINDER_DUE, WakeReason.ERRAND_REMINDER_DUE}\n",
        _SLEEPER_REMINDER_KILLERS,
    ),
    SourceMutation(
        "open_errands_are_commitments",
        _RHYTHM_MODULE,
        _RHYTHM_SOURCE,
        "    if (plan is not None and not rhythm_schedule.day_plan_expired(plan, inputs.state.current_time)\n",
        "    if agent.errands or (plan is not None and not rhythm_schedule.day_plan_expired(plan, inputs.state.current_time)\n",
        _COMMITMENT_KILLERS,
    ),
    SourceMutation(
        "errand_wake_reason_missing_from_vocabulary",
        _FOOTPRINT_MODELS_MODULE,
        _FOOTPRINT_MODELS_SOURCE,
        '    "progress_report_due", "no_active_activity", "plan_reminder_due", "errand_reminder_due",\n',
        '    "progress_report_due", "no_active_activity", "plan_reminder_due",\n',
        _AWAKE_REMINDER_KILLERS,
    ),
    SourceMutation(
        "sleeper_reminder_time_is_a_boundary",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        "    return [moment for agent_id, agent in state.agents.items() if not _asleep(state, agent_id)\n",
        "    return [moment for agent_id, agent in state.agents.items()\n",
        _SLEEPER_REMINDER_KILLERS,
    ),
    SourceMutation(
        "reminder_recorded_without_delivery",
        _RHYTHM_MODULE,
        _RHYTHM_SOURCE,
        "        if rhythm_schedule.WakeReason.ERRAND_REMINDER_DUE.value in verdict.wake_reasons:\n",
        "        if True:\n",
        _SLEEPER_REMINDER_KILLERS,
    ),
    SourceMutation(
        "simultaneous_reminders_write_one_event",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '    return state.model_copy(update={"agents": agents}), drafts\n',
        '    return state.model_copy(update={"agents": agents}), drafts[:1]\n',
        _TOGETHER_KILLERS,
    ),
    SourceMutation(
        "midnight_clears_errands",
        _ENGINE_MODULE,
        _ENGINE_SOURCE,
        (
            "            staged = _put_agent(staged, staged.agents[agent_id].model_copy("
            'update={"day_plan": None}, deep=True))\n'
        ),
        (
            "            staged = _put_agent(staged, staged.agents[agent_id].model_copy(\n"
            '                update={"day_plan": None, "errands": {}}, deep=True))\n'
        ),
        _OVERNIGHT_KILLERS,
    ),
    SourceMutation(
        "plan_revision_drops_errands",
        _ENGINE_MODULE,
        _ENGINE_SOURCE,
        '        changed_agent = agent.model_copy(update={"day_plan": plan}, deep=True)\n',
        '        changed_agent = agent.model_copy(update={"day_plan": plan, "errands": {}}, deep=True)\n',
        _OVERNIGHT_KILLERS,
    ),
    SourceMutation(
        "condensation_drops_todo_errands",
        _PROMPT_CAPACITY_MODULE,
        _PROMPT_CAPACITY_SOURCE,
        '            layers[index] = {**layers[index], "events": records}\n',
        (
            '            layers[index] = {**layers[index], "events": records}\n'
            '            layers = [{**layer, "errands": []} if layer.get("layer") == "待办层" else layer\n'
            "                      for layer in layers]\n"
        ),
        _CONDENSED_KILLERS,
    ),
    SourceMutation(
        "memory_overflow_drops_todo_errands",
        _OBSERVE_MODULE,
        _OBSERVE_SOURCE,
        '                "tool_layer": tool_layer(context, index), "todo_layer": todo_layer(context, self.store),\n',
        (
            '                "tool_layer": tool_layer(context, index),\n'
            '                "todo_layer": {**todo_layer(context, self.store), **(\n'
            '                    {"errands": []} if len(str(memory.layer)) > memory_layer.MEMORY_WINDOW_CHARS // 2\n'
            "                    else {})},\n"
        ),
        _OVERFLOW_KILLERS,
    ),
    SourceMutation(
        "checkpoint_omits_errands",
        _MODELS_MODULE,
        _MODELS_SOURCE,
        "    errands: dict[str, ErrandState] = Field(default_factory=dict)\n",
        "    errands: dict[str, ErrandState] = Field(default_factory=dict, exclude=True)\n",
        _CHECKPOINT_KILLERS,
    ),
    SourceMutation(
        "progress_arguments_carry_errands",
        _PROGRESS_LAYER_MODULE,
        _PROGRESS_LAYER_SOURCE,
        "    return control_request(tool, request, memories=True)\n",
        '    return {**control_request(tool, request, memories=True), "errands": request["errands"]}\n',
        _SEPARATION_KILLERS,
    ),
    SourceMutation(
        "self_notes_carry_errand_text",
        _MEMORY_LAYER_MODULE,
        _MEMORY_LAYER_SOURCE,
        "            notes.extend((item.activity_name, text) for text in texts if text.strip())\n",
        (
            "            notes.extend((item.activity_name, text) for text in texts if text.strip())\n"
            "            from worldmock.life.control_items import control_errands\n"
            "            notes.extend((item.activity_name, change[key]) for change in control_errands(\n"
            '                request.data["tool_name"], request.data["request"]) for key in ("name", "memo") if change[key])\n'
        ),
        _SEPARATION_KILLERS,
    ),
    SourceMutation(
        "system_layer_copy_diverges",
        _PROMPT_MODULE,
        _PROMPT_SOURCE,
        '        "到提醒时刻只提醒一次，不会叫醒睡着的你；其他需要延续的打算写进 pause_activity 或 conclude_activity 的 "\n',
        '        "到提醒时刻只提醒一次，会叫醒睡着的你；其他需要延续的打算写进 pause_activity 或 conclude_activity 的 "\n',
        _VERBATIM_KILLERS,
    ),
    SourceMutation(
        "errand_agent_events_written_as_system",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        '                      visibility="agent" if kind in AGENT_VISIBLE_KINDS else "system",\n',
        '                      visibility="system",\n',
        _VISIBLE_KILLERS,
    ),
    SourceMutation(
        "settlement_event_inherits_evidence_identity",
        _ERRANDS_MODULE,
        _ERRANDS_SOURCE,
        "            settled.append(_credit(open_sets[row.owner_id], errand, row, head))\n",
        (
            "            settled.append(_credit(open_sets[row.owner_id], errand, row, head).model_copy(\n"
            '                update={"decision_id": draft.decision_id, "action_id": draft.action_id}))\n'
        ),
        _IDENTITY_KILLERS,
    ),
    SourceMutation(
        "control_projection_carries_errands",
        _CONTROL_ITEMS_MODULE,
        _CONTROL_ITEMS_SOURCE,
        '    return {"items": rows}\n',
        '    return {"items": rows, "errands": list(control_errands(tool, request))}\n',
        _ERRANDS_PROJECTION_KILLERS,
    ),
    SourceMutation(
        "control_projection_trims_first_item_only",
        _CONTROL_ITEMS_MODULE,
        _CONTROL_ITEMS_SOURCE,
        (
            "    for item in control_items(tool, request):\n"
            '        row: dict[str, Any] = {"activity_name": item.activity_name, "summary": item.summary}\n'
        ),
        (
            "    for index, item in enumerate(control_items(tool, request)):\n"
            '        row: dict[str, Any] = {"activity_name": item.activity_name, "summary": item.summary}\n'
            "        if index:\n"
            "            row.update(memories=list(item.plain_memories), views=list(item.views))\n"
        ),
        _PROJECTION_KILLERS,
    ),
    SourceMutation(
        "projection_mutates_source_event",
        _WINDOWS_MODULE,
        _WINDOWS_SOURCE,
        '        public = {key: data[key] for key in ("tool_name", "request", "self_note") if key in data}\n',
        "        public = data\n",
        _INTACT_KILLERS,
    ),
    SourceMutation(
        "errand_metrics_use_goal_denominator",
        _ERRAND_METRICS_MODULE,
        _ERRAND_METRICS_SOURCE,
        "    registered = len(lives)\n",
        (
            '    registered = sum(len(event["data"]["day_plan"]["goals"]) for event in events\n'
            '                     if event["data"].get("kind") == "day_plan_generated")\n'
        ),
        _DENOMINATOR_KILLERS,
    ),
)

#: 规格「源码变异必须覆盖」一段逐句（按原文顺序照抄）→ 所属阶段与杀死它的登记变异名；已完成阶段的每一句至少一个变异。
SPEC_MUTATIONS: dict[str, dict[str, object]] = {
    "反思接受自由文本": {"stage": "1", "mutations": ("reflection_accepts_free_text",
                                                "reflection_accepts_another_tool")},
    "跳过未知引用校验": {"stage": "1", "mutations": ("reflection_skips_unknown_reference_check",)},
    "失败返回空列表": {"stage": "1", "mutations": ("reflection_failure_returns_an_empty_list",)},
    "重试不设上限": {"stage": "1", "mutations": ("reflection_retries_without_a_bound",)},
    "运行装配改回 AS2 原类": {"stage": "1", "mutations": ("assembly_proxy_reverts_to_the_as2_class",
                                                    "assembly_service_proxy_reverts_to_the_as2_class",
                                                    "assembly_actor_reverts_to_the_as2_class")},
    "代理不等待远端结果": {"stage": "1", "mutations": ("proxy_returns_without_awaiting_the_remote_result",)},
    "重复意图只报笼统错误": {"stage": "1", "mutations": ("plan_duplicates_report_only_a_generic_error",)},
    "日志后吞掉异常": {"stage": "1", "mutations": ("decision_failure_swallowed_after_log",)},
    "零点清空事务": {"stage": "2", "mutations": ("midnight_clears_errands",)},
    "修订计划丢事务": {"stage": "2", "mutations": ("plan_revision_drops_errands",)},
    "待办层不投影事务": {"stage": "2", "mutations": ("todo_layer_omits_errands",)},
    "记忆层超窗或上下文浓缩时丢掉待办层事务": {"stage": "2", "mutations": ("condensation_drops_todo_errands",
                                                                "memory_overflow_drops_todo_errands")},
    "业务拒绝时事务变更照落": {"stage": "2", "mutations": ("errand_changes_applied_before_rejection",)},
    "事务名近似时取第一个候选": {"stage": "2", "mutations": ("errand_name_takes_first_candidate",)},
    "请求可把状态置为完成": {"stage": "2", "mutations": ("request_can_mark_errand_completed",)},
    "把受理、未送达订单、活动开始、`progressed` 或自述当完成": {"stage": "2", "mutations": (
        "accepted_event_counts_as_evidence", "pending_order_counts_as_obtained", "activity_start_counts_as_eaten",
        "progressed_goal_counts_as_completion", "self_report_counts_as_completion")},
    "把记下之前的证据（含同一提交内排在新增事件之前的）或记下前开始的进度区间计入": {"stage": "2", "mutations": (
        "holdings_at_noting_count_as_obtained", "evidence_before_noting_event_counts",
        "interval_begun_before_noting_counts")},
    "当面给予不计入取得": {"stage": "2", "mutations": ("gift_is_not_obtained",)},
    "按事件人物字段而非收货人判归属": {"stage": "2", "mutations": ("ownership_follows_event_agent",)},
    "读取逐项结果里的重复订单": {"stage": "2", "mutations": ("nested_results_orders_counted",)},
    "一次提交结算两次": {"stage": "2", "mutations": ("round_commit_settles_twice", "advance_commit_settles_twice")},
    "检查点漏存事务": {"stage": "2", "mutations": ("checkpoint_omits_errands",)},
    "取消后仍提醒": {"stage": "2", "mutations": ("cancelled_errand_stays_armed",)},
    "提醒即置完成": {"stage": "2", "mutations": ("reminder_completes_errand",)},
    "提醒叫醒睡眠": {"stage": "2", "mutations": ("errand_reminder_wakes_sleep",)},
    "事务计入在手承诺": {"stage": "2", "mutations": ("open_errands_are_commitments",)},
    "新唤醒原因漏登足迹词表": {"stage": "2", "mutations": ("errand_wake_reason_missing_from_vocabulary",)},
    "提醒与办成事件写成系统可见": {"stage": "2", "mutations": ("errand_agent_events_written_as_system",)},
    "结算事件沿用证据事件的决策身份": {"stage": "2", "mutations": ("settlement_event_inherits_evidence_identity",)},
    "事务文字写进进度层参数或 `self_notes`": {"stage": "2", "mutations": ("progress_arguments_carry_errands",
                                                                     "self_notes_carry_errand_text")},
    "意图或 dreaming 投影带出请求级 `errands`": {"stage": "2", "mutations": ("control_projection_carries_errands",)},
    "投影原地修改原事件": {"stage": "2", "mutations": ("projection_mutates_source_event",)},
    "事务指标以目标数为分母": {"stage": "2", "mutations": ("errand_metrics_use_goal_denominator",)},
    "版本 8 检查点被放行": {"stage": "2", "mutations": ("version_8_world_state_accepted",
                                                  "version_8_export_passes_delivery_gate")},
    "抽取或人设层投影丢掉故事写明的下班、到家锚点": {"stage": "3", "mutations": ()},
    "抽取对下班锚点取缺省": {"stage": "3", "mutations": ()},
    "新锚点并入回绕归一": {"stage": "3", "mutations": ()},
    "乱序时整卡失败": {"stage": "3", "mutations": ()},
    "场景声明的下班、到家窗口不进零点计划输入": {"stage": "3", "mutations": ()},
    "按星期推断休息日": {"stage": "3", "mutations": ()},
    "所有人物套班次": {"stage": "3", "mutations": ()},
    "计划修订后 `duty` 消失": {"stage": "3", "mutations": ()},
    "休息时段显示为当班中": {"stage": "3", "mutations": ()},
    "班次并入任务开放判定": {"stage": "3", "mutations": ()},
    "忽略例外日期": {"stage": "3", "mutations": ()},
    "宿主在人设层重复渲染工作安排": {"stage": "3", "mutations": ()},
    "忽略来源": {"stage": "4", "mutations": ()},
    "来源解析另写一套": {"stage": "4", "mutations": ()},
    "「购买」不展开": {"stage": "4", "mutations": ()},
    "可执行筛选放到切页之后或在未命中方式的动作上判定": {"stage": "4", "mutations": ()},
    "精简时按真假值丢掉 false 或零": {"stage": "4", "mutations": ()},
    "查询写世界": {"stage": "4", "mutations": ()},
    "四键旧请求被受理": {"stage": "4", "mutations": ()},
}

#: 不对应规格「源码变异必须覆盖」一段任何一句的登记变异：变异名 → 出处（实施计划各阶段的「突变审查的目标」，或阶段复审
#: 按规格正文条款补登的变异）。
EXTRA_MUTATIONS: dict[str, str] = {
    "tool_dispatch_failure_loses_stage_code": "实施计划阶段 1「突变审查的目标」故障传播：工具分发失败不再按失败阶段码 "
                                              "TOOL_SERVICE_FAILED 抛出（改为把原异常直接上抛）。",
    "plan_duplicates_locate_only_later_entries": "实施计划阶段 1「突变审查的目标」计划重复意图：只定位后一条（重复意图的首次"
                                                 "出现不再报告）。",
    "plan_over_capacity_returns_before_duplicates": "实施计划阶段 1「突变审查的目标」计划重复意图：超限提前返回而不与重复意图"
                                                    "同批报告。",
    "actor_answers_without_the_environment": "实施计划阶段 1「突变审查的目标」只读转发：actor 不把查询交给环境（只能由主进程内"
                                             "直接调用 actor 的用例杀死）。",
    "actor_class_allows_concurrent_calls": "实施计划阶段 1「突变审查的目标」只读转发：max_concurrency=1 改为 2（杀手为捕获 "
                                           "ray.remote 参数的用例）。",
    "reflection_count_bound_widened": "实施计划阶段 1「突变审查的目标」严格反思：条数上限放宽（六条洞见被接受）。",
    "reflection_text_validation_skipped": "实施计划阶段 1「突变审查的目标」严格反思：正文文字校验不执行（超过一千字符或含"
                                          "条目身份的洞见被接受）。",
    "reflection_retry_bound_zero": "实施计划阶段 1「突变审查的目标」严格反思：重试上限改为 0（第一次不合合同即运行故障）。",
    "reflection_correction_accumulates_messages": "实施计划阶段 1「突变审查的目标」严格反思：附回消息逐次累积而不基于原始"
                                                  "消息。",
    "decision_prompt_ignores_action_order": "实施计划阶段 1「突变审查的目标」交付窗口：选取决策提示时去掉「早于动作」条件"
                                            "（杀手为反思请求排在动作之后的一例）。",
    "items_without_errands_accepted": "实施计划阶段 2「突变审查的目标」：带 items 缺 `errands` 的请求被放行（control_items "
                                      "去掉请求级 errands 的必有检查）。",
    "duplicates_judged_before_cancels": "实施计划阶段 2「突变审查的目标」：同一请求不先扣除取消就判重名与上限（新增与"
                                        "扣除本请求取消之前的全部未完成事务比较）。",
    "replayed_result_counts": "实施计划阶段 2「突变审查的目标」：把重放结果计数（读动作结果的证据时不看 reused）。",
    "condensation_drops_completion_line": "实施计划阶段 2「突变审查的目标」：浓缩后丢掉 `errand_completed` 的句子"
                                          "（record_lines 对办成记录返回空）。",
    "sleeper_reminder_time_is_a_boundary": "实施计划阶段 2「突变审查的目标」：睡眠人物的提醒时刻进入边界候选"
                                           "（boundary_times 去掉睡眠过滤）。",
    "reminder_recorded_without_delivery": "实施计划阶段 2「突变审查的目标」：提醒在睡眠中记账（不看唤醒判定，给每个人物"
                                          "交付到点的事务提醒）。",
    "simultaneous_reminders_write_one_event": "实施计划阶段 2「突变审查的目标」：同刻多条只写一条提醒事件"
                                              "（deliver_reminders 只留第一条草稿）。",
    "system_layer_copy_diverges": "实施计划阶段 2「突变审查的目标」：系统层三处文案不一致（life_prompt.py 第 6 条改掉一个"
                                  "分句，接口合同与人物 Prompt 规格不跟随）。",
    "control_projection_trims_first_item_only": "实施计划阶段 2「突变审查的目标」：交付投影只裁首项（control_request 让"
                                                "第二项起保留 memories 与 views）。",
    "unfinished_making_counts_as_obtained": "规格 2.4「不算」的制作未完成（「源码变异必须覆盖」一段没有单列这一种，阶段 2 "
                                            "复审补登）：读活动快照的制作配方而不是完整结束时的库存转换，未完成的制作区间"
                                            "也算产出。",
    "errand_named_twice_accepted": "规格 2.2「同一条未完成事务在一次请求里至多被一条更新或取消点名」与 2.3 表第 1 行（阶段 2 "
                                   "复审补登）：同一事务被第二条更新或取消点名时不再判出。",
    "added_twins_with_one_product_accepted": "规格 2.3 表第 4 行「结果种类与商品都与…同请求另一条新增相同」（阶段 2 复审"
                                             "补登）：同一请求两条新增的结果种类与商品相同时不再判出。",
}

#: 本特性新增或改动的生产文件（延期标记与空壳函数的扫描范围），随各提交增长；阶段 1 的清单取自实施计划「台账形态」行。
FEATURE_PRODUCTION_FILES: tuple[str, ...] = (
    "custom/agents/life_agent.py",
    "custom/agents/life_continuation.py",
    "custom/agents/life_memory.py",
    "custom/agents/life_prompt.py",
    "custom/agents/life_reflection.py",
    "src/worldmock/delivery/pipeline.py",
    "src/worldmock/delivery/windows.py",
    "src/worldmock/evaluation/behavior_metrics.py",
    "src/worldmock/evaluation/errand_metrics.py",
    "src/worldmock/footprints/models.py",
    "src/worldmock/footprints/quality.py",
    "src/worldmock/life/activity_control.py",
    "src/worldmock/life/control_items.py",
    "src/worldmock/life/day_plan_inputs.py",
    "src/worldmock/life/day_planning.py",
    "src/worldmock/life/engine.py",
    "src/worldmock/life/errand_state.py",
    "src/worldmock/life/errands.py",
    "src/worldmock/life/models.py",
    "src/worldmock/life/narration.py",
    "src/worldmock/life/observe.py",
    "src/worldmock/life/progress_layer.py",
    "src/worldmock/life/prompt_schemas.py",
    "src/worldmock/life/read_query_runtime.py",
    "src/worldmock/life/reminders.py",
    "src/worldmock/life/result_projection.py",
    "src/worldmock/life/rhythm.py",
    "src/worldmock/life/schedule.py",
    "src/worldmock/life/session_actions.py",
    "src/worldmock/life/settled_text.py",
    "src/worldmock/life/todo_layer.py",
    "src/worldmock/runtime.py",
    "tools/run-viewer-context.js",
    "tools/run-viewer.html",
)

#: 本特性新增的测试文件，连同登记表、守卫与共用变异执行器自身；登记了节点的既有测试文件由守卫另行纳入同一扫描。
FEATURE_TEST_FILES: tuple[str, ...] = (
    "tests/errands_workday_acceptance.py",
    "tests/evaluation/test_errand_metrics.py",
    "tests/footprints/test_errand_export.py",
    "tests/integration/test_errand_checkpoint.py",
    "tests/integration/test_errand_delivery_windows.py",
    "tests/integration/test_errand_prompt_separation.py",
    "tests/integration/test_read_query_runtime.py",
    "tests/integration/test_strict_reflection.py",
    "tests/life/test_errand_changes.py",
    "tests/life/test_errand_completion.py",
    "tests/life/test_errand_names.py",
    "tests/life/test_errand_overnight.py",
    "tests/life/test_errand_reminder.py",
    "tests/life/test_errand_requests.py",
    "tests/life/test_errand_settlement_idempotence.py",
    "tests/life/test_errand_state.py",
    "tests/life/test_errand_todo_layer.py",
    "tests/life/test_plan_day_duplicate_feedback.py",
    "tests/life/test_revise_day_plan_json.py",
    "tests/mutation_runner.py",
    "tests/test_errands_workday_acceptance.py",
)

#: 本台账的回执目录与超时：基线按节点数设定（每个节点留足一次冷启动），单个变异按最慢的杀手（真实 Ray 用例）设定。
RECEIPTS = REPO_ROOT / ".work/errands-workday/mutation-receipts"
BASELINE_TIMEOUT_FLOOR_SECONDS = 600
BASELINE_TIMEOUT_SECONDS_PER_NODE = 120
MUTATION_TIMEOUT_SECONDS = 600


def run_behavior(nodes: tuple[str, ...], directory: Path, mutation: SourceMutation | None = None) -> dict:
    """@brief 以本台账的回执目录与超时调用共用变异执行器：不带变异是基线，带变异是一次杀死尝试。"""
    timeout = MUTATION_TIMEOUT_SECONDS
    if mutation is None:
        timeout = max(BASELINE_TIMEOUT_FLOOR_SECONDS, BASELINE_TIMEOUT_SECONDS_PER_NODE * len(nodes))
    return mutation_runner.run_behavior(nodes, directory, mutation, RunSettings(CHILD_ENTRY, RECEIPTS, timeout))
