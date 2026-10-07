"""个人事务与工作安排规格的验收登记表（完整性守卫的数据源）。

@file     errands_workday_acceptance.py
@brief    把 docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库「验收台账」的每一行登记到所属阶段与证据测试节点（LEDGER），登记台账之后
          「源码变异必须覆盖」一段的每一句到杀死它的源码变异（SPEC_MUTATIONS、MUTATIONS），并冻结本特性新增或改动的生产
          文件与新增测试文件（FEATURE_PRODUCTION_FILES、FEATURE_TEST_FILES），供延期标记与空壳函数扫描。判定由
          tests/test_errands_workday_acceptance.py 执行；变异经共用执行器 tests/mutation_runner.py 在隔离子进程里生效。
@note     分阶段形态：`stage` 取规格台账的阶段列；只有 `stage` 属于 STAGES_DONE 的行必须列出测试，但任何行列出的节点都
          必须解析到存在、会被收集且未被跳过的测试函数。阶段随实施增长：每个阶段的提交登记自己的行、变异与文件，阶段的
          最后一个提交把阶段号加入 STAGES_DONE；阶段 4 只依赖阶段 1，可以先于阶段 2、3 完成。
          仍存在的真实请求、引擎与 SQLite 节点保留；固定食品来源与可执行筛选登记当前实际目录、冻结观察的纯节点。
          当前纯节点不证明完整宿主与 LifeEnv 链路已经运行；该链路须在冻结资源简化的后续阶段补齐并真实执行。
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
#: 一个）；阶段 3（作息与工作安排）在自己的分支上开发、随合并进入主线：它的六行都列出节点，十二句变异各有登记的杀死
#: 变异；阶段 4（查询筛选与返回精简）只依赖阶段 1，与阶段 2 并行开发、随合并进入主线：它的五行都列出经真实引擎的
#: 节点，七句变异各有登记的杀死变异。阶段 5（全量门禁与独立变异审查）随本行登记完成：「工程门禁」列出门禁自身的
#: 测试，全量测试、覆盖率门禁在最终代码上的运行结果与两轮独立源码变异审查写在本特性验收记录。阶段 7（实现后文档）
#: 随本行登记完成：「文档一致性」列出文档链接门禁、设计汇编检查与两项文档扫描的测试，实现状态文档在其后单独提交。
STAGES_DONE: frozenset[str] = frozenset({"1", "2", "3", "4", "5", "7"})

#: 改代码与测试的实施阶段，加上全量门禁阶段：必须全部完成。阶段 0 是冻结提交上的基线全量、没有台账行；阶段 6 是配对
#: 全天复跑，只报告、不作门禁，只在用户要求时启动；阶段 7 是实现后文档，实现状态文档最后提交。
IMPLEMENTATION_STAGES = frozenset({"1", "2", "3", "4", "5"})

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
_READ_QUERY_TESTS = ['tests/integration/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',
 'tests/integration/test_read_query_runtime.py::test_task_recall_crosses_real_ray_without_world_mutation',
 'tests/integration/test_read_query_runtime.py::test_unwrapped_actor_read_query_equals_direct_environment_call',
 'tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
 'tests/integration/test_read_query_runtime.py::test_life_env_actor_class_fixes_single_concurrency_without_parameters']

_REFLECTION = "tests/integration/test_strict_reflection.py::"

#: 「严格反思」：头五个节点经真实记忆运行时（WorldMockMemoryRuntime.after_step 的就寝边界）——恰一次 submit_reflection、
#: 一至五条与固定的条目字段；每种合同违约（自由文本、其他工具、多次调用、零条或六条、未知引用、引用为空或重复、正文
#: 超长或含身份串）都附回英文错误重试一次并只写入改正后的那一条；首次之外三次重试耗尽即运行故障且不写入任何洞见；传输
#: 异常不进校验重试；没有可反思的记忆时不调用模型。第六个守文案：实际发送的 system、模块常量与接口合同登记块逐字相同。
#: 第七、八个经真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent 跑醒来那次决策（耗尽：四对请求与响应留证、请求关闭
#: 思考、没有 memory_updated、中止后动作未结算；改正：恰好一条 memory_updated）。第九个是 Society 层的验收口径（本轮中止、
#: 不提交、无 memory_updated，原因带最后一次校验错误）。其后三个是既有登记用例与真实日运行：反思条目的字段、巩固不计反思、
#: 整日运行分别记录行动与反思请求；反思校验重试的附回消息不进入观察重复门禁，门禁仍为 1。
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
    "tests/life/test_engine_branch_coverage.py::test_only_action_requests_satisfy_the_predicate",
    _REFLECTION + "test_reflection_tool_schema_is_inline_strict_and_bound_to_the_host_limit",
    _REFLECTION + "test_reflection_records_only_valid_subjective_insights",
    _REFLECTION + "test_invalid_reflection_cannot_enter_memory",
    _REFLECTION + "test_reflection_requires_exactly_one_tool_call",
    _REFLECTION + "test_reflection_tool_arguments_must_be_a_json_string",
    _REFLECTION + "test_reflection_correction_appends_one_compact_message_without_touching_the_original",
    _REFLECTION + "test_reflection_retry_bound_is_its_own_constant_with_the_plan_day_value",
    "tests/evaluation/test_behavior_metrics.py::test_reflection_requests_and_their_retries_are_not_decision_requests",
]

_SESSION_STATE = "tests/life/test_session_state.py::"
_ERRAND_TODO = "tests/life/test_errand_todo_layer.py::"

#: 「检查点」中「版本 8 检查点被拒」一项：头一个经真实引擎与 SQLite——落盘的版本 8 世界状态（人物状态里没有 errands 键，
#: 与版本 8 写出的检查点同形）在重新打开运行时只以版本不符拒绝；其后一个守世界状态模型本身只拒绝版本、同形的版本 9
#: 通过；末一个是
#: 「共同检查点恢复后待办层逐字节相同；不重复记下、完成与提醒」：经真实请求、引擎与 SQLite 记下带提醒的事务、到店累计
#: 一份、远程订单在途时创建共同检查点并恢复成分支（engine.checkpoint → store.restore → LifeEngine.open），事务逐字段相同、
#: 同一时刻的待办层逐字节相同；父运行与分支接着过下去各提醒一次、送达各一次并各办成一次，分支没有第二条记下事件。
_RESTORED_BRANCH = ("tests/integration/test_errand_checkpoint.py::"
                    "test_a_restored_branch_shows_the_same_todo_layer_and_repeats_nothing")
_CHECKPOINT_TESTS = [
    _SESSION_STATE + "test_run_with_version_8_world_state_is_rejected_on_open",
    _SESSION_STATE + "test_world_state_schema_version_8_is_rejected",
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
#: - 「系统层两份手写副本与实际请求逐字且 18 条，第 6 条与第 16 条不相抵」：直接导入的 LifeAgent 发出的真实请求的系统层与
#:   两份手写副本（模块常量 LIFE_SYSTEM_LAYER、接口合同「请求组成」的登记）逐字相同且为十八条；第 1、4、6 条与规格 3.1 的
#:   定稿逐字相同，第 16 条（随让「等待」成为例外规格改写）仍说睡着时只有提醒或睡够了才会醒、不提事务，与第 6 条不相抵，
#:   规则里没有决策次序一类的新指令。
#: - 「旧延续句不再出现」：规则、合同「请求组成」一节与重新汇编的设计说明书三处。
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

_FOOD_ERRAND = "tests/life/test_food_errand_evidence.py::"

#: 「完成依据」读取真实收货、本人归属、登记顺序与异常人物来源；付款、自述与旧给予进食不构成证据。
_COMPLETION_TESTS = ['tests/life/test_food_errand_evidence.py::test_actual_pickup_credits_only_receipt_recipient_and_repeat_settlement_emits_no_evidence',
 'tests/life/test_food_errand_evidence.py::test_retired_giving_consumption_and_self_reports_cannot_progress_errands',
 'tests/life/test_food_errand_evidence.py::test_actual_pending_delivery_does_not_credit_and_restored_receipt_credits_once',
 'tests/life/test_food_errand_evidence.py::test_two_actual_receipts_accumulate_quantity_and_remove_only_the_completed_recipient_errand',
 'tests/life/test_errand_completion.py::test_a_remote_order_completes_only_in_the_commit_that_receives_it',
 'tests/life/test_errand_completion.py::test_noting_then_buying_at_the_store_completes_with_no_inventory',
 'tests/life/test_errand_completion.py::test_quantity_two_with_one_received_stays_open_and_a_second_receipt_completes',
 'tests/life/test_errand_completion.py::test_goals_and_claimed_completion_leave_the_errand_open',
 'tests/life/test_errand_completion.py::test_an_unaffordable_purchase_leaves_the_errand_and_world_unchanged',
 'tests/life/test_errand_completion.py::test_receipt_completion_tolerance_has_both_sides',
 'tests/life/test_food_errand_evidence.py::test_actual_payment_orders_are_not_errand_evidence_even_when_delivered',
 'tests/life/test_food_errand_evidence.py::test_receipt_before_registration_in_same_commit_does_not_count_but_after_it_does',
 'tests/life/test_food_errand_evidence.py::test_goods_receipt_rejects_an_invalid_simulated_recipient',
 'tests/life/test_food_errand_evidence.py::test_unaffordable_actual_purchase_preserves_open_errand_quantity_and_all_world_bytes',
 'tests/life/test_acs_receipt_readers.py::test_actual_contact_delivery_never_completes_buyers_errand_or_meal_goal']

_IDEMPOTENCE = "tests/life/test_errand_settlement_idempotence.py::"

#: 「结算幂等」：全部经真实请求、引擎与 SQLite——重放的动作结果不再累计；同一订单的配送再到期一次没有新入库行、不再累计；
#: 并行开始的合并结果只读顶层订单；决策轮提交与时钟推进提交各结算一次（数量 3 的事务两次各加 1 份）；两人同一轮里排在
#: 新增事件之前的给予不算、排在之后的算。
_IDEMPOTENCE_TESTS = ['tests/life/test_errand_settlement_idempotence.py::test_a_replayed_action_adds_neither_payment_receipt_nor_errand_progress',
 'tests/life/test_errand_settlement_idempotence.py::test_repeated_due_delivery_has_no_second_receipt_or_errand_evidence',
 'tests/life/test_errand_settlement_idempotence.py::test_parallel_order_result_is_not_an_extra_copy_of_receipt_evidence',
 'tests/life/test_errand_settlement_idempotence.py::test_purchase_commit_and_delivery_commit_each_credit_their_own_receipt_once',
 'tests/life/test_errand_settlement_idempotence.py::test_same_commit_registration_uses_event_order_instead_of_equal_clock',
 'tests/life/test_food_errand_evidence.py::test_actual_pending_delivery_does_not_credit_and_restored_receipt_credits_once',
 'tests/life/test_food_errand_evidence.py::test_receipt_before_registration_in_same_commit_does_not_count_but_after_it_does']

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
    _REMINDER + "test_open_errands_do_not_change_the_reconsideration_interval",
    _REMINDER + "test_partial_progress_after_the_reminder_does_not_remind_again",
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
_ERRAND_NAME_TESTS = ['tests/life/test_errand_names.py::test_update_and_cancel_use_the_todo_layer_name',
 'tests/life/test_errand_names.py::test_unknown_or_ambiguous_errand_name_lists_every_open_name_and_substitutes_nothing',
 'tests/life/test_errand_names.py::test_unknown_item_reports_nearest_product_names_in_why_without_candidates',
 'tests/life/test_errand_names.py::test_cancel_requires_a_reason',
 'tests/life/test_errand_names.py::test_duplicate_added_names_same_outcome_and_product_and_a_ninth_errand_are_rejected',
 'tests/life/test_errand_names.py::test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected',
 'tests/life/test_errand_names.py::test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted',
 'tests/life/test_errand_names.py::test_a_request_bypassing_the_host_is_refused_once_and_the_world_is_unchanged',
 'tests/life/test_errand_names.py::test_whatever_the_host_accepts_the_environment_accepts',
 'tests/integration/test_life_agent_protocol.py::test_errand_errors_join_the_single_host_correction',
 'tests/life/test_errand_completion.py::test_a_completed_errand_frees_its_name_and_product_for_a_new_errand']

_CONTROL_ITEMS = "tests/life/test_control_items.py::"

#: 控制请求在记录和读取时保留个人事务变更的顺序，并拒绝缺少请求级 errands 的旧形状。
_CONTROL_RECORD_TESTS = [
    _CONTROL_ITEMS + "test_current_shape_items_carry_views_as_subject_memories",
    _CONTROL_ITEMS + "test_a_current_request_without_errands_is_rejected",
    _CONTROL_ITEMS + "test_control_errands_reads_every_change_in_order",
    "tests/life/test_export_integration.py::test_completed_activity_exports_terminal_outcome_and_recorded_prompt",
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
    "tests/footprints/test_goal_export.py::test_export_command_uses_authenticated_cutoff_and_manifest_code",
    "tests/footprints/test_quality.py::test_report_sections_are_the_documented_grouping_without_a_language_count",
    _ERRAND_EXPORT,
]

_QUERY_FILTERS = "tests/life/test_world_query_filters.py::"
_TOOL_PROTOCOL = "tests/life/test_tool_protocol.py::"
_QUERY_COST = "tests/life/test_query_cost_report.py::"

#: 六键必填、严格布尔、独立字段错误与用途句保留；食品查询请求使用当前真实模型的两个必填键反例。
_QUERY_REQUEST_TESTS = ['tests/life/test_world_query.py::test_every_query_field_is_required',
 'tests/life/test_world_query.py::test_empty_filter_reports_each_position_and_keeps_other_type_errors',
 'tests/life/test_world_query.py::test_filter_errors_are_aggregated_and_never_hidden_by_offset_or_type_errors',
 'tests/integration/test_tool_description_references.py::test_query_world_purpose_is_the_contract_text_and_moves_only_the_tool_fingerprint',
 'tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',
 'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change']

#: 来源取实际 app 成员门店和同一个购买名称解析器；未知来源拒绝，候选排除没有食品菜单的地点。
_SOURCE_FILTER_TESTS = ['tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',
 'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change',
 'tests/life/test_name_resolution.py::test_registered_action_name_is_exact_and_app_is_not_an_activity_target',
 'tests/life/test_name_resolution.py::test_location_home_and_current_ways_are_normalized_without_app_guess',
 'tests/life/test_named_purchase.py::test_named_purchase_rejects_unknown_goods_and_unbound_apps_atomically',
 'tests/life/test_world_query.py::test_unknown_app_and_store_names_have_the_exact_new_error_contract',
 'tests/life/test_world_query_filters.py::test_action_app_and_category_filters_use_real_action_names_and_labels']

#: 保留仍存在的宿主、环境与只读报告节点；固定食品当前经真实目录与冻结查询断言状态不变。
#: 当前新增纯节点没有运行完整 LifeAgent、LifeEnv、SQLite 链路。
_QUERY_READONLY_TESTS = [
    'tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
    'tests/life/test_tool_protocol.py::test_world_query_mixed_batches_are_atomic',
    'tests/life/test_tool_protocol.py::test_final_turn_and_truncation_retry_disable_every_query',
    'tests/life/test_tool_protocol.py::test_query_dispatch_has_no_world_side_effect',
    'tests/life/test_tool_interaction_runtime.py::test_read_queries_keep_actual_model_exchange_without_progress_events',
    'tests/life/test_query_cost_report.py::test_query_cost_on_a_real_engine_exchange',
    'tests/life/test_query_cost_report.py::test_query_cost_counts_failures_empty_filtered_pages_and_reflection_retries',
    'tests/life/test_query_cost_report.py::test_query_cost_of_a_run_without_queries_and_the_printed_keys',
    'tests/integration/test_output_truncation.py::test_a_truncated_turn_is_dropped_and_retried_once_without_thinking',
    'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change',
]

#: 购买方式展开为到店和远程下单；只看所写方式的动作，未写方式时同时看实际活动控制工具。
_EXECUTABLE_FILTER_TESTS = ['tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',
 'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change',
 'tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
 'tests/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way']

#: 筛选先于分页与总数计算，实际食品页面没有缺项；现存紧凑返回和权限字段断言保留。
_PAGING_TESTS = ['tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
 'tests/life/test_food_presentation.py::test_real_food_candidates_page_without_gaps_using_the_actual_order_key',
 'tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',
 'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change',
 'tests/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way',
 'tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero_current_action_and_control_tools',
 'tests/life/test_world_query_filters.py::test_full_query_contains_goods_beyond_the_current_presentation_window']

_CARD_EXTRACTION = "tests/profile/test_extractor.py::"
_CARD_MODELS = "tests/profile/test_models.py::"
_CARD_INPUT_WARNINGS = "tests/integration/test_input_warnings.py::"
_CARD_PERSONA_LAYER = "tests/life/test_persona_layer.py::"
_CARD_TEXT = "tests/profile/test_textgen.py::"
_CARD_INJECTION = "tests/profile/test_injection.py::"
_CARD_SCENARIO = "tests/life/test_scenario.py::"

#: 「作息锚点」（卡片一侧）：头七个节点经真实 extract_persona 与脚本化会话——故事写明时产出窗口、未提及为 null 且不取映射
#: 表缺省、下班摇摆不改变其余五锚点、单次采样乱序只为放行而降级且不消耗重试、共识乱序先降下班再降到家且卡片照常编译、
#: 三次采样各自乱序而共识合法时不降级、格式错误照常重试；第八个是 null 新锚点下凌晨就寝仍回绕归一；第九个是首版不支持
#: 跨午夜的下班与到家：就寝回绕到次日时，起点不晚于就寝而越过当日 24 点的到家在共识后被置 null 并留一条说明；第十个是真实
#: compile_persona 把降级说明接在一致性警告之后。其后三个是卡片模型与降级函数的补充证据（必填可空、四条顺序约束与当日
#: 午夜上界、固定的降级顺序与说明）。再后两个经真实抽取（脚本化会话）、真实新建（画像编译、场景作息覆盖、人物装配、LifeEnv 与 SQLite
#: 运行清单）与直接导入的 LifeAgent：降级说明作为画像编译警告冻结进清单（PERSONA_AGREEMENT_LOW 只带原文、消息不归因于
#: 一致性），写明的下班与到家按九键键序到达实际请求的人设层、卡片文本追加两个分句。随后是画像文本各自非空时追加分句并
#: 计入预算、都为 null 时逐字节不变（render_card 与新建运行冻结的卡片文本是同一函数）；人设层（直接导入
#: worldmock.agents.life_persona）九键键序与 null、缺两键的旧结构化卡片在恢复路径被拒（真实 _prepare_resume 与父运行
#: SQLite）；再一个守宿主不渲染人物档案里的工作安排与场景窗口（人设层键集与值不变）。场景作息覆盖七字段的三个节点经
#: 真实新建（场景副本、画像编译、场景作息覆盖、LifeEnv 与 SQLite 运行清单）、直接导入的 LifeAgent 与真实 plan_inputs：
#: 场景声明的下班、到家窗口落进卡片空槽并记覆盖、到达人设层与零点计划人物输入；只来自卡片的两个锚点不进零点计划输入；
#: 违反顺序约束的场景下班窗口让空槽保持 null 并冻结一条冲突。最后五个是补充证据：覆盖函数在 null 槽上的覆盖、冲突
#: （三种顺序冲突，含就寝早于卡片到家时冲突落在就寝上）、不动点与写不成窗口的冲突，以及场景两个窗口的校验。
_WORK_ANCHOR_TESTS = [
    _CARD_EXTRACTION + "test_stated_work_anchors_are_extracted_into_the_card",
    _CARD_EXTRACTION + "test_unmentioned_work_anchors_stay_null_without_toml_defaults",
    _CARD_EXTRACTION + "test_wavering_leave_work_does_not_change_the_five_anchors",
    _CARD_EXTRACTION + "test_out_of_order_sample_is_admitted_without_a_parse_retry",
    _CARD_EXTRACTION + "test_out_of_order_consensus_demotes_leave_work_then_arrive_home_and_still_compiles",
    _CARD_EXTRACTION + "test_samples_out_of_order_on_their_own_but_legal_in_consensus_are_not_demoted",
    _CARD_EXTRACTION + "test_malformed_or_missing_work_anchors_take_the_parse_retry",
    _CARD_EXTRACTION + "test_null_work_anchors_keep_the_after_midnight_bedtime_normalised",
    _CARD_EXTRACTION + "test_a_work_anchor_past_midnight_is_demoted_even_before_a_later_bedtime",
    "tests/profile/test_compile.py::test_demotion_notes_follow_the_agreement_warning_and_stand_alone_without_it",
    _CARD_MODELS + "test_work_anchors_are_required_nullable_and_outside_the_five_anchor_order",
    _CARD_MODELS + "test_work_anchor_order_constraints_are_enforced",
    _CARD_MODELS + "test_settle_work_anchors_demotes_in_the_fixed_order_with_one_text_each",
    _CARD_INPUT_WARNINGS + "test_a_demotion_note_is_frozen_as_a_compiler_warning_that_does_not_blame_agreement",
    _CARD_INPUT_WARNINGS + "test_extracted_work_anchors_reach_the_real_request_persona_layer",
    _CARD_TEXT + "test_non_null_work_anchors_each_append_a_clause_and_count_towards_the_budget",
    _CARD_TEXT + "test_render_card_is_byte_identical_to_the_pre_persona_layer_renderer",
    _CARD_PERSONA_LAYER + "test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent",
    _CARD_PERSONA_LAYER + "test_structured_card_without_work_anchor_keys_is_rejected_on_restore",
    _CARD_PERSONA_LAYER + "test_a_declared_work_schedule_is_not_rendered_in_the_persona_layer",
    _CARD_INPUT_WARNINGS + "test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input",
    _CARD_INPUT_WARNINGS + "test_card_only_work_anchors_never_enter_the_plan_input",
    (_CARD_INPUT_WARNINGS
     + "test_a_conflicting_scenario_work_window_keeps_the_null_card_slot_and_is_frozen_as_a_conflict"),
    _CARD_INJECTION + "test_a_null_card_slot_takes_the_scenario_work_window_and_is_reported_as_overridden",
    _CARD_INJECTION + "test_a_scenario_window_violating_the_work_anchor_order_keeps_the_card_value_as_a_conflict",
    _CARD_INJECTION + "test_work_windows_declared_together_land_together_and_the_result_is_a_fixed_point",
    _CARD_INJECTION + "test_an_unrepresentable_work_window_is_a_conflict_on_a_null_slot",
    _CARD_SCENARIO + "test_work_window_bounds_and_order_are_rejected",
]

_WORK_SCHEDULE_LOADS = "tests/life/test_load_checks.py::"
_SCENARIO_STRUCTURE = "tests/life/test_scenario.py::test_structure_eight_rejects_old_scenario_version[4]"
#: 「工作安排加载」：每个人物显式声明 work_schedule 或 null、缺键与场景结构 4 经真实入口被拒；工作地点不是所选地点、时段
#: 与休息时段非法、时间窗内与场景日历不一致都经真实的 load_runtime_scenario 被拒（顶层明细写明人物、日期与字段，形状
#: 错误的原因在异常链里），时间窗外的日期、加班机会日与 null 的人物不核对。场景模型校验器逐条拒绝的用例是补充证据。
_WORK_SCHEDULE_LOAD_TESTS = [
    _SCENARIO_STRUCTURE,
    _WORK_SCHEDULE_LOADS + "test_work_schedule_workplace_must_be_a_selected_location",
    _WORK_SCHEDULE_LOADS + "test_real_loader_rejects_malformed_work_shift_and_break",
    "tests/life/test_scenario.py::test_work_schedule_shape_errors_are_rejected",
    _WORK_SCHEDULE_LOADS + "test_work_schedule_must_agree_with_the_calendar_inside_the_window",
    _WORK_SCHEDULE_LOADS + "test_overtime_dates_and_dates_outside_the_window_are_not_checked",
    _WORK_SCHEDULE_LOADS + "test_a_null_work_schedule_is_not_cross_checked_and_others_still_are",
]

_WORK_SCHEDULE = "tests/life/test_work_schedule.py::"
#: 「上班安排呈现」：有工作安排的人物经真实观察、请求、结算与 SQLite（engine_factory 加 declare 改写场景副本）：duty 随
#: 时钟经过班前、当班、休息时段与班后，地点写显示名、不含内部身份、只在环境层出现；工作提交与付款后仍是当班中；
#: 修订计划被受理后逐值不变；休息日来自每周 null 与日期例外，例外的班次压过每周的 null；没有工作安排的人物为 null 而
#: 同场景的他人照旧；共同检查点恢复的分支环境层逐字节相同；当班中离开办公室照常列出、照常受理，事件计数与没有工作安排
#: 的同一场景相同；真实 LifeAgent 的同秒续接链内 duty 不变、不进 changes。末一个是 Schema 枚举与闭集一致的补充证据。
_DUTY_TESTS = [
    _WORK_SCHEDULE + "test_duty_follows_the_clock_through_a_working_day",
    _WORK_SCHEDULE + "test_duty_stays_on_duty_after_topic_completion",
    _WORK_SCHEDULE + "test_revising_the_day_plan_does_not_change_duty",
    _WORK_SCHEDULE + "test_rest_days_come_from_weekly_null_and_exception_null_and_an_exception_shift_overrides_weekly",
    _WORK_SCHEDULE + "test_people_without_a_schedule_have_no_duty_while_others_keep_theirs",
    _WORK_SCHEDULE + "test_environment_layer_is_byte_identical_after_checkpoint_restore",
    _WORK_SCHEDULE + "test_leaving_the_workplace_on_duty_is_listed_and_accepted_without_new_event_kinds",
    "tests/integration/test_same_second_continuation.py::"
    "test_duty_is_constant_within_a_same_second_chain_and_never_enters_changes",
    "tests/life/test_prompt_layers.py::test_duty_enumerations_are_the_work_schedule_constants",
]
#: 「工作合同边界」：班前 08:30 在办公室真实开工被受理，班后 18:30 工作仍在进行、暂停后恢复被受理；有无工作安排的同一
#: 场景里任务的开放与截止、工具层任务目标的合同与开工动作逐值相同。
_WORK_CONTRACT_BOUNDARY_TESTS = [
    _WORK_SCHEDULE + "test_work_can_start_before_the_shift_and_continue_after_it",
]
#: 「计划与义务」：直接导入的 LifeAgent 发出的真实请求里系统层仍是十八条，第 1 条只把环境层一句改成含今天的上班安排、
#: 第 14 条在四个分句之后依次是让「等待」成为例外规格插入的钟点一句与规格 4.6 的上班安排一句（后者逐字不动），与接口
#: 合同「请求组成」的登记、模块常量逐字相同；
#: 真实零点计划请求的模板输入说明首行与第八条与规格 4.6、接口合同逐字相同；零点计划输入的「上班安排」按计划日期当天
#: 求值、不含 status（工作日、休息日、没有工作安排各一例，载荷与客户端收到的请求相同），零点边界上前一日休息、当日
#: 当班时输入是新开始那一日的工作日安排。
_PLAN_DUTY_TESTS = [
    _SEVEN_LAYERS + "test_rules_1_and_14_state_the_duty_arrangement_verbatim",
    _DAY_PLANNING + "test_plan_template_first_line_and_rule_eight_are_the_contract_text",
    _DAY_PLANNING + "test_plan_input_duty_is_for_the_new_day_at_the_midnight_boundary",
    _DAY_PLANNING + "test_plan_input_carries_the_plan_dates_duty_arrangement_without_status",
]

_WORKLOAD = "tests/evaluation/test_workload_metrics.py::"
_WORKLOAD_REPORT = "tests/life/test_workload_report.py::"
#: 「工作负荷与作息偏离」：真实运行（engine_factory 加 declare，只发真实请求、不预置地点）班内 15:00 离开办公室回家，从
#: SQLite 读回的事件与改写后的场景文档经 behavior_metrics_report 重算出三小时的早退与缺勤、恒等关系成立，离开工作地点
#: 对班次结束早 180 分钟、证据是回家那一程的首个进度事件；另一组真实运行把首个任务改成集市的预约任务，班内约定开始前
#: 出发赴约、在那里工作（午休也在）、直接回办公室，重算为任务外出（扣掉午休）而缺勤与早退为 0，同一行程在场景按日期
#: 例外写成不当班的运行里十个量都是 0、作息偏离对班次的两项为 null。手写事件逐值核对各量的定义与恒等关系、两种声明
#: 例外（休息时段、已开放未结清任务的要求地点的外出，预约任务从约定当天起算）不计缺勤而不当班日不进统计、赴约早于
#: 约定开始的路上不是缺勤也不是早退、作息偏离三项对窗口与班次的正负与零、证据身份、容差
#: 写进报告不作用于偏离值、班次两端在睡的标注、没有工作安排的人物离开一项为 null、缺输入为 null 并写说明、不出现已删除
#: 的偏离指标键名、同一输入逐字节相同；打包输入取自经真实解析的场景文档与卡片。只读子命令 workday 对同一真实运行目录
#: 经子进程打印恰好四键、值等于进程内重算、库文件字节不变；场景文件摘要不等于运行清单的 scenario_sha256 时以英文错误
#: 非零退出、不打印；作息窗口取自运行工作区的结构化卡片，没有卡片时按 null 计并写说明。
_WORKLOAD_TESTS = [
    _WORKLOAD + "test_a_real_run_leaving_on_duty_is_recomputed_as_early_leave",
    _WORKLOAD + "test_workload_fields_follow_their_definitions_on_a_hand_built_day",
    _WORKLOAD + "test_declared_breaks_are_not_absence_and_rest_days_are_left_out",
    _WORKLOAD + "test_routine_deviation_reports_three_items_with_evidence_tolerance_and_sleep_flags",
    _WORKLOAD + "test_people_without_a_schedule_have_null_leave_items_and_still_report_arrival_home",
    _WORKLOAD + "test_missing_inputs_are_null_with_a_note",
    _WORKLOAD + "test_routine_deviation_uses_none_of_the_removed_plan_deviation_names",
    _WORKLOAD + "test_workday_inputs_take_shifts_home_windows_and_interval_from_the_scenario_and_cards",
    _WORKLOAD + "test_reports_are_byte_reproducible",
    _WORKLOAD_REPORT + "test_workday_command_prints_the_four_keys_for_a_real_run",
    _WORKLOAD_REPORT + "test_workday_command_rejects_a_scenario_that_is_not_the_runs_scenario",
    _WORKLOAD_REPORT + "test_workday_command_reports_null_windows_without_a_workspace_card",
]

#: 验收台账行（首列原文）→ 所属阶段与证据测试节点；未完成阶段的行随该阶段的提交补节点。
#: 阶段 5 的「工程门禁」行：风格门禁零新增违规、覆盖率门禁的阈值与未覆盖公共接口判定、文档链接门禁在真实仓库上通过。
#: 全量测试与覆盖率门禁在最终代码上的运行结果（重新生成并提交的 docs/dev/coverage-report.json）写在本特性验收记录。
_DOC_GATE = "tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve"
_ENGINEERING_GATE_TESTS = [
    "tests/test_style_gate.py::test_style_gate_admits_no_new_violations",
    "tests/test_coverage_gate.py::test_branch_threshold_boundary",
    "tests/test_coverage_gate.py::test_uncovered_public_interface_detection",
    _DOC_GATE,
]

#: 阶段 7 的「文档一致性」行：文档链接门禁通过、设计汇编与源一致、带锚点的链接指向现有标题、文档里不留临时待验收标记。
_DOCUMENT_CONSISTENCY_TESTS = [
    _DOC_GATE,
    "tests/test_design_doc_build.py::"
    "test_repository_chapters_assemble_deterministically_and_the_committed_html_is_current",
    "tests/test_dayrun_fidelity_acceptance.py::test_anchored_links_in_docs_point_to_existing_headings",
    "tests/test_dayrun_fidelity_acceptance.py::test_docs_carry_no_pending_markers_of_this_feature",
]

LEDGER: dict[str, dict[str, object]] = {'严格反思': {'stage': '1',
          'tests': ['tests/integration/test_strict_reflection.py::test_runtime_submits_reflection_through_the_only_strict_tool',
                    'tests/integration/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',
                    'tests/integration/test_strict_reflection.py::test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection',
                    'tests/integration/test_strict_reflection.py::test_runtime_propagates_transport_failures_without_validation_retries',
                    'tests/integration/test_strict_reflection.py::test_runtime_makes_no_model_call_when_there_is_nothing_to_reflect_on',
                    'tests/integration/test_strict_reflection.py::test_reflection_prompt_is_the_contract_text',
                    'tests/integration/test_strict_reflection.py::test_exhausted_reflection_leaves_evidence_but_no_memory_update_in_the_run_database',
                    'tests/integration/test_strict_reflection.py::test_corrected_reflection_is_recorded_once_by_the_real_engine',
                    'tests/integration/test_life_agent_protocol.py::test_reflection_contract_failure_aborts_society_before_commit',
                    'tests/integration/test_verified_memory.py::test_sleep_reflection_is_subjective_and_its_id_is_reported',
                    'tests/integration/test_conclude_memories.py::test_threshold_consolidation_counts_conclusion_entries_but_not_bedtime_reflection',
                    'tests/integration/test_dayrun_complete_days.py::test_complete_two_person_day_uses_real_tools_memory_and_restoration',
                    'tests/life/test_engine_branch_coverage.py::test_only_action_requests_satisfy_the_predicate',
                    'tests/integration/test_strict_reflection.py::test_reflection_tool_schema_is_inline_strict_and_bound_to_the_host_limit',
                    'tests/integration/test_strict_reflection.py::test_reflection_records_only_valid_subjective_insights',
                    'tests/integration/test_strict_reflection.py::test_invalid_reflection_cannot_enter_memory',
                    'tests/integration/test_strict_reflection.py::test_reflection_requires_exactly_one_tool_call',
                    'tests/integration/test_strict_reflection.py::test_reflection_tool_arguments_must_be_a_json_string',
                    'tests/integration/test_strict_reflection.py::test_reflection_correction_appends_one_compact_message_without_touching_the_original',
                    'tests/integration/test_strict_reflection.py::test_reflection_retry_bound_is_its_own_constant_with_the_plan_day_value',
                    'tests/evaluation/test_behavior_metrics.py::test_reflection_requests_and_their_retries_are_not_decision_requests']},
 '只读查询转发': {'stage': '1',
            'tests': ['tests/integration/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',
                      'tests/integration/test_read_query_runtime.py::test_task_recall_crosses_real_ray_without_world_mutation',
                      'tests/integration/test_read_query_runtime.py::test_unwrapped_actor_read_query_equals_direct_environment_call',
                      'tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
                      'tests/integration/test_read_query_runtime.py::test_life_env_actor_class_fixes_single_concurrency_without_parameters']},
 '计划重复意图': {'stage': '1',
            'tests': ['tests/life/test_revise_day_plan_json.py::test_engine_rejects_a_duplicate_revision_locating_first_and_later_entries',
                      'tests/life/test_revise_day_plan_json.py::test_engine_reports_over_capacity_and_duplicates_in_the_same_rejection',
                      'tests/life/test_revise_day_plan_json.py::test_engine_keeps_at_most_eight_goals_with_one_goal_per_intent',
                      'tests/life/test_plan_day_duplicate_feedback.py::test_plan_day_duplicate_intents_are_fed_back_with_every_position_then_registered',
                      'tests/life/test_plan_day_duplicate_feedback.py::test_plan_day_reports_over_capacity_together_with_duplicates',
                      'tests/life/test_revise_day_plan_json.py::test_host_corrects_duplicate_intents_from_real_json_arguments',
                      'tests/life/test_revise_day_plan_json.py::test_duplicate_intent_in_provider_json_returns_nested_validation_error',
                      'tests/life/test_revise_day_plan_json.py::test_nine_goals_with_a_duplicate_report_capacity_and_positions_in_one_validation',
                      'tests/life/test_plan_day_duplicate_feedback.py::test_repeated_work_goals_are_located_and_correction_requires_merging']},
 '故障传播': {'stage': '1',
          'tests': ['tests/integration/test_host_failure_stages.py::test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage',
                    'tests/integration/test_host_failure_stages.py::test_model_service_failure_on_the_real_engine_is_logged_once_and_reraised_unchanged',
                    'tests/integration/test_life_agent_protocol.py::test_decision_failure_is_logged_once_and_reraised_unchanged',
                    'tests/integration/test_host_failure_stages.py::test_tool_service_failure_points_to_real_call_without_claiming_execution',
                    'tests/integration/test_life_agent_protocol.py::test_memory_persistence_failure_aborts_society_before_commit',
                    'tests/integration/test_host_failure_stages.py::test_model_service_failure_persists_stage_without_parsing_exception_text',
                    'tests/integration/test_host_failure_stages.py::test_evidence_write_failure_stops_with_structured_stage',
                    'tests/integration/test_agent_branch_coverage.py::test_tool_messages_follow_memory_queries_only']},
 '事务变更与原子受理': {'stage': '2',
               'tests': ['tests/life/test_errand_requests.py::test_each_control_tool_records_errand_changes_with_one_event_per_change_and_an_ordered_receipt',
                         'tests/life/test_errand_requests.py::test_empty_errands_is_valid_and_the_receipt_is_an_empty_list',
                         'tests/life/test_errand_requests.py::test_every_structural_error_reports_the_full_errand_path',
                         'tests/life/test_errand_requests.py::test_a_rejected_request_registers_no_errand_and_writes_no_errand_event',
                         'tests/life/test_errand_requests.py::test_a_request_cannot_mark_an_errand_completed',
                         'tests/life/test_errand_requests.py::test_a_replayed_request_does_not_register_twice',
                         'tests/integration/test_activity_schema.py::test_control_tool_schemas_inline_the_errand_change_object',
                         'tests/integration/test_conclude_memories.py::test_rejected_conclusion_keeps_its_memories_but_registers_no_errand']},
 '事务名称与错误': {'stage': '2',
             'tests': ['tests/life/test_errand_names.py::test_update_and_cancel_use_the_todo_layer_name',
                       'tests/life/test_errand_names.py::test_unknown_or_ambiguous_errand_name_lists_every_open_name_and_substitutes_nothing',
                       'tests/life/test_errand_names.py::test_unknown_item_reports_nearest_product_names_in_why_without_candidates',
                       'tests/life/test_errand_names.py::test_cancel_requires_a_reason',
                       'tests/life/test_errand_names.py::test_duplicate_added_names_same_outcome_and_product_and_a_ninth_errand_are_rejected',
                       'tests/life/test_errand_names.py::test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected',
                       'tests/life/test_errand_names.py::test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted',
                       'tests/life/test_errand_names.py::test_a_request_bypassing_the_host_is_refused_once_and_the_world_is_unchanged',
                       'tests/life/test_errand_names.py::test_whatever_the_host_accepts_the_environment_accepts',
                       'tests/integration/test_life_agent_protocol.py::test_errand_errors_join_the_single_host_correction',
                       'tests/life/test_errand_completion.py::test_a_completed_errand_frees_its_name_and_product_for_a_new_errand']},
 '跨夜可见': {'stage': '2',
          'tests': ['tests/life/test_errand_overnight.py::test_an_errand_stays_in_the_todo_layer_after_midnight_plan_generation_and_plan_revision',
                    'tests/life/test_errand_overnight.py::test_an_errand_stays_when_the_memory_layer_overflows_its_window',
                    'tests/life/test_errand_overnight.py::test_a_condensed_request_still_carries_the_todo_layer_errands',
                    'tests/life/test_errand_overnight.py::test_the_memory_layer_writes_no_line_for_errands',
                    'tests/integration/test_errand_prompt_separation.py::test_the_next_action_request_shows_the_errand_in_the_todo_layer_without_any_memory_query',
                    'tests/life/test_day_planning.py::test_plan_day_input_lists_open_errands_read_only',
                    'tests/life/test_day_planning.py::test_plan_day_input_writes_no_line_for_an_errand_completed_before_midnight',
                    'tests/life/test_day_planning.py::test_plan_template_is_the_contract_text_and_asks_only_for_differences_with_around_times']},
 '完成依据': {'stage': '2',
          'tests': ['tests/life/test_food_errand_evidence.py::test_actual_pickup_credits_only_receipt_recipient_and_repeat_settlement_emits_no_evidence',
                    'tests/life/test_food_errand_evidence.py::test_retired_giving_consumption_and_self_reports_cannot_progress_errands',
                    'tests/life/test_food_errand_evidence.py::test_actual_pending_delivery_does_not_credit_and_restored_receipt_credits_once',
                    'tests/life/test_food_errand_evidence.py::test_two_actual_receipts_accumulate_quantity_and_remove_only_the_completed_recipient_errand',
                    'tests/life/test_errand_completion.py::test_a_remote_order_completes_only_in_the_commit_that_receives_it',
                    'tests/life/test_errand_completion.py::test_noting_then_buying_at_the_store_completes_with_no_inventory',
                    'tests/life/test_errand_completion.py::test_quantity_two_with_one_received_stays_open_and_a_second_receipt_completes',
                    'tests/life/test_errand_completion.py::test_goals_and_claimed_completion_leave_the_errand_open',
                    'tests/life/test_errand_completion.py::test_an_unaffordable_purchase_leaves_the_errand_and_world_unchanged',
                    'tests/life/test_errand_completion.py::test_receipt_completion_tolerance_has_both_sides',
                    'tests/life/test_food_errand_evidence.py::test_actual_payment_orders_are_not_errand_evidence_even_when_delivered',
                    'tests/life/test_food_errand_evidence.py::test_receipt_before_registration_in_same_commit_does_not_count_but_after_it_does',
                    'tests/life/test_food_errand_evidence.py::test_goods_receipt_rejects_an_invalid_simulated_recipient',
                    'tests/life/test_food_errand_evidence.py::test_unaffordable_actual_purchase_preserves_open_errand_quantity_and_all_world_bytes',
                    'tests/life/test_acs_receipt_readers.py::test_actual_contact_delivery_never_completes_buyers_errand_or_meal_goal']},
 '结算幂等': {'stage': '2',
          'tests': ['tests/life/test_errand_settlement_idempotence.py::test_a_replayed_action_adds_neither_payment_receipt_nor_errand_progress',
                    'tests/life/test_errand_settlement_idempotence.py::test_repeated_due_delivery_has_no_second_receipt_or_errand_evidence',
                    'tests/life/test_errand_settlement_idempotence.py::test_parallel_order_result_is_not_an_extra_copy_of_receipt_evidence',
                    'tests/life/test_errand_settlement_idempotence.py::test_purchase_commit_and_delivery_commit_each_credit_their_own_receipt_once',
                    'tests/life/test_errand_settlement_idempotence.py::test_same_commit_registration_uses_event_order_instead_of_equal_clock',
                    'tests/life/test_food_errand_evidence.py::test_actual_pending_delivery_does_not_credit_and_restored_receipt_credits_once',
                    'tests/life/test_food_errand_evidence.py::test_receipt_before_registration_in_same_commit_does_not_count_but_after_it_does']},
 '事务提醒': {'stage': '2',
          'tests': ['tests/life/test_errand_reminder.py::test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged',
                    'tests/life/test_errand_reminder.py::test_a_clock_equal_to_the_acceptance_time_means_the_next_local_day',
                    'tests/life/test_errand_reminder.py::test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary',
                    'tests/life/test_errand_reminder.py::test_errands_due_together_are_one_opportunity_with_one_event_each',
                    'tests/life/test_errand_reminder.py::test_errand_and_goal_reminders_due_together_are_listed_in_enum_order',
                    'tests/life/test_errand_reminder.py::test_completed_or_cancelled_errands_are_not_reminded',
                    'tests/life/test_errand_reminder.py::test_updating_the_reminder_clock_arms_it_again',
                    'tests/life/test_errand_reminder.py::test_a_reminder_due_at_local_midnight_is_delivered_by_the_midnight_rhythm',
                    'tests/life/test_errand_reminder.py::test_open_errands_do_not_change_the_reconsideration_interval',
                    'tests/life/test_errand_reminder.py::test_partial_progress_after_the_reminder_does_not_remind_again',
                    'tests/footprints/test_errand_export.py::test_an_exported_run_reports_errands_and_recertifies_value_for_value',
                    'tests/footprints/test_quality.py::test_errand_reminder_due_is_a_known_wake_reason']},
 '检查点': {'stage': '2',
         'tests': ['tests/life/test_session_state.py::test_run_with_version_8_world_state_is_rejected_on_open',
                   'tests/life/test_session_state.py::test_world_state_schema_version_8_is_rejected',
                   'tests/integration/test_errand_checkpoint.py::test_a_restored_branch_shows_the_same_todo_layer_and_repeats_nothing']},
 '提示与唯一出现': {'stage': '2',
             'tests': ['tests/integration/test_seven_layer_request.py::test_system_layers_are_the_contract_registration_verbatim',
                       'tests/integration/test_seven_layer_request.py::test_rules_1_4_6_divide_errands_summaries_and_memories',
                       'tests/integration/test_seven_layer_request.py::test_the_retired_continuation_sentence_is_gone_everywhere',
                       'tests/integration/test_tool_description_references.py::test_control_tool_purposes_and_errand_descriptions_are_the_contract_text',
                       'tests/integration/test_tool_description_references.py::test_every_native_tool_has_a_purpose_only_description',
                       'tests/integration/test_tool_description_references.py::test_parameter_descriptions_are_purpose_only',
                       'tests/integration/test_strict_reflection.py::test_reflection_prompt_is_the_contract_text',
                       'tests/integration/test_strict_reflection.py::test_reflection_prompt_ends_with_the_errand_sentences',
                       'tests/integration/test_unique_presence_run.py::test_an_errand_full_row_appears_only_in_the_todo_layer',
                       'tests/life/test_errand_todo_layer.py::test_observation_carries_private_errand_bindings_outside_every_layer',
                       'tests/integration/test_same_second_continuation.py::test_errand_changes_reach_the_next_tool_message_as_added_and_removed',
                       'tests/integration/test_same_second_continuation.py::test_an_errand_completed_in_the_same_second_appears_once_in_the_continuation_events',
                       'tests/life/test_progress_events.py::test_an_errand_completed_in_the_session_is_one_record_and_folds_to_one_line',
                       'tests/life/test_errand_reminder.py::test_a_reminder_delivered_with_the_arrival_stays_out_of_the_new_stay_session',
                       'tests/integration/test_errand_prompt_separation.py::test_errand_changes_write_no_memory_and_stay_out_of_self_notes_and_progress_arguments']},
 '控制请求记录': {'stage': '2',
            'tests': ['tests/life/test_control_items.py::test_current_shape_items_carry_views_as_subject_memories',
                      'tests/life/test_control_items.py::test_a_current_request_without_errands_is_rejected',
                      'tests/life/test_control_items.py::test_control_errands_reads_every_change_in_order',
                      'tests/life/test_export_integration.py::test_completed_activity_exports_terminal_outcome_and_recorded_prompt']},
 '事务与收尾记忆指标': {'stage': '2',
               'tests': ['tests/evaluation/test_errand_metrics.py::test_counts_follow_their_definitions_and_the_identity_holds',
                         'tests/evaluation/test_errand_metrics.py::test_the_denominator_is_registered_errands_not_goals',
                         'tests/evaluation/test_errand_metrics.py::test_visible_and_expected_observations_agree',
                         'tests/evaluation/test_errand_metrics.py::test_acted_after_reminder_needs_an_accepted_request_for_the_same_product',
                         'tests/evaluation/test_errand_metrics.py::test_a_completion_without_checkable_evidence_is_counted',
                         'tests/evaluation/test_errand_metrics.py::test_conclusion_memories_count_routine_and_errand_overlap',
                         'tests/evaluation/test_errand_metrics.py::test_no_errand_or_no_conclusion_memory_gives_null',
                         'tests/integration/test_dayrun_complete_days.py::test_complete_two_person_day_uses_real_tools_memory_and_restoration',
                         'tests/footprints/test_goal_export.py::test_export_command_uses_authenticated_cutoff_and_manifest_code',
                         'tests/footprints/test_quality.py::test_report_sections_are_the_documented_grouping_without_a_language_count',
                         'tests/footprints/test_errand_export.py::test_an_exported_run_reports_errands_and_recertifies_value_for_value']},
 '作息锚点': {'stage': '3',
          'tests': ['tests/profile/test_extractor.py::test_stated_work_anchors_are_extracted_into_the_card',
                    'tests/profile/test_extractor.py::test_unmentioned_work_anchors_stay_null_without_toml_defaults',
                    'tests/profile/test_extractor.py::test_wavering_leave_work_does_not_change_the_five_anchors',
                    'tests/profile/test_extractor.py::test_out_of_order_sample_is_admitted_without_a_parse_retry',
                    'tests/profile/test_extractor.py::test_out_of_order_consensus_demotes_leave_work_then_arrive_home_and_still_compiles',
                    'tests/profile/test_extractor.py::test_samples_out_of_order_on_their_own_but_legal_in_consensus_are_not_demoted',
                    'tests/profile/test_extractor.py::test_malformed_or_missing_work_anchors_take_the_parse_retry',
                    'tests/profile/test_extractor.py::test_null_work_anchors_keep_the_after_midnight_bedtime_normalised',
                    'tests/profile/test_extractor.py::test_a_work_anchor_past_midnight_is_demoted_even_before_a_later_bedtime',
                    'tests/profile/test_compile.py::test_demotion_notes_follow_the_agreement_warning_and_stand_alone_without_it',
                    'tests/profile/test_models.py::test_work_anchors_are_required_nullable_and_outside_the_five_anchor_order',
                    'tests/profile/test_models.py::test_work_anchor_order_constraints_are_enforced',
                    'tests/profile/test_models.py::test_settle_work_anchors_demotes_in_the_fixed_order_with_one_text_each',
                    'tests/integration/test_input_warnings.py::test_a_demotion_note_is_frozen_as_a_compiler_warning_that_does_not_blame_agreement',
                    'tests/integration/test_input_warnings.py::test_extracted_work_anchors_reach_the_real_request_persona_layer',
                    'tests/profile/test_textgen.py::test_non_null_work_anchors_each_append_a_clause_and_count_towards_the_budget',
                    'tests/profile/test_textgen.py::test_render_card_is_byte_identical_to_the_pre_persona_layer_renderer',
                    'tests/life/test_persona_layer.py::test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent',
                    'tests/life/test_persona_layer.py::test_structured_card_without_work_anchor_keys_is_rejected_on_restore',
                    'tests/life/test_persona_layer.py::test_a_declared_work_schedule_is_not_rendered_in_the_persona_layer',
                    'tests/integration/test_input_warnings.py::test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input',
                    'tests/integration/test_input_warnings.py::test_card_only_work_anchors_never_enter_the_plan_input',
                    'tests/integration/test_input_warnings.py::test_a_conflicting_scenario_work_window_keeps_the_null_card_slot_and_is_frozen_as_a_conflict',
                    'tests/profile/test_injection.py::test_a_null_card_slot_takes_the_scenario_work_window_and_is_reported_as_overridden',
                    'tests/profile/test_injection.py::test_a_scenario_window_violating_the_work_anchor_order_keeps_the_card_value_as_a_conflict',
                    'tests/profile/test_injection.py::test_work_windows_declared_together_land_together_and_the_result_is_a_fixed_point',
                    'tests/profile/test_injection.py::test_an_unrepresentable_work_window_is_a_conflict_on_a_null_slot',
                    'tests/life/test_scenario.py::test_work_window_bounds_and_order_are_rejected']},
 '工作安排加载': {'stage': '3',
            'tests': ['tests/life/test_scenario.py::test_structure_eight_rejects_old_scenario_version[4]',
                      'tests/life/test_load_checks.py::test_work_schedule_workplace_must_be_a_selected_location',
                      'tests/life/test_load_checks.py::test_real_loader_rejects_malformed_work_shift_and_break',
                      'tests/life/test_scenario.py::test_work_schedule_shape_errors_are_rejected',
                      'tests/life/test_load_checks.py::test_work_schedule_must_agree_with_the_calendar_inside_the_window',
                      'tests/life/test_load_checks.py::test_overtime_dates_and_dates_outside_the_window_are_not_checked',
                      'tests/life/test_load_checks.py::test_a_null_work_schedule_is_not_cross_checked_and_others_still_are',
                      'tests/life/test_scenario.py::test_structure_eight_rejects_old_scenario_version']},
 '上班安排呈现': {'stage': '3',
            'tests': ['tests/life/test_work_schedule.py::test_duty_follows_the_clock_through_a_working_day',
                      'tests/life/test_work_schedule.py::test_duty_stays_on_duty_after_topic_completion',
                      'tests/life/test_work_schedule.py::test_revising_the_day_plan_does_not_change_duty',
                      'tests/life/test_work_schedule.py::test_rest_days_come_from_weekly_null_and_exception_null_and_an_exception_shift_overrides_weekly',
                      'tests/life/test_work_schedule.py::test_people_without_a_schedule_have_no_duty_while_others_keep_theirs',
                      'tests/life/test_work_schedule.py::test_environment_layer_is_byte_identical_after_checkpoint_restore',
                      'tests/life/test_work_schedule.py::test_leaving_the_workplace_on_duty_is_listed_and_accepted_without_new_event_kinds',
                      'tests/integration/test_same_second_continuation.py::test_duty_is_constant_within_a_same_second_chain_and_never_enters_changes',
                      'tests/life/test_prompt_layers.py::test_duty_enumerations_are_the_work_schedule_constants']},
 '计划与义务': {'stage': '3',
           'tests': ['tests/integration/test_seven_layer_request.py::test_rules_1_and_14_state_the_duty_arrangement_verbatim',
                     'tests/life/test_day_planning.py::test_plan_template_first_line_and_rule_eight_are_the_contract_text',
                     'tests/life/test_day_planning.py::test_plan_input_duty_is_for_the_new_day_at_the_midnight_boundary',
                     'tests/life/test_day_planning.py::test_plan_input_carries_the_plan_dates_duty_arrangement_without_status']},
 '工作合同边界': {'stage': '3',
            'tests': ['tests/life/test_work_schedule.py::test_work_can_start_before_the_shift_and_continue_after_it']},
 '工作负荷与作息偏离': {'stage': '3',
               'tests': ['tests/evaluation/test_workload_metrics.py::test_a_real_run_leaving_on_duty_is_recomputed_as_early_leave',
                         'tests/evaluation/test_workload_metrics.py::test_workload_fields_follow_their_definitions_on_a_hand_built_day',
                         'tests/evaluation/test_workload_metrics.py::test_declared_breaks_are_not_absence_and_rest_days_are_left_out',
                         'tests/evaluation/test_workload_metrics.py::test_routine_deviation_reports_three_items_with_evidence_tolerance_and_sleep_flags',
                         'tests/evaluation/test_workload_metrics.py::test_people_without_a_schedule_have_null_leave_items_and_still_report_arrival_home',
                         'tests/evaluation/test_workload_metrics.py::test_missing_inputs_are_null_with_a_note',
                         'tests/evaluation/test_workload_metrics.py::test_routine_deviation_uses_none_of_the_removed_plan_deviation_names',
                         'tests/evaluation/test_workload_metrics.py::test_workday_inputs_take_shifts_home_windows_and_interval_from_the_scenario_and_cards',
                         'tests/evaluation/test_workload_metrics.py::test_reports_are_byte_reproducible',
                         'tests/life/test_workload_report.py::test_workday_command_prints_the_four_keys_for_a_real_run',
                         'tests/life/test_workload_report.py::test_workday_command_rejects_a_scenario_that_is_not_the_runs_scenario',
                         'tests/life/test_workload_report.py::test_workday_command_reports_null_windows_without_a_workspace_card']},
 '查询请求': {'stage': '4',
          'tests': ['tests/life/test_world_query.py::test_every_query_field_is_required',
                    'tests/life/test_world_query.py::test_empty_filter_reports_each_position_and_keeps_other_type_errors',
                    'tests/life/test_world_query.py::test_filter_errors_are_aggregated_and_never_hidden_by_offset_or_type_errors',
                    'tests/integration/test_tool_description_references.py::test_query_world_purpose_is_the_contract_text_and_moves_only_the_tool_fingerprint',
                    'tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',
                    'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change']},
 '应用筛选': {'stage': '4',
          'tests': ('tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',
                    'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change',
                    'tests/life/test_name_resolution.py::test_registered_action_name_is_exact_and_app_is_not_an_activity_target',
                    'tests/life/test_name_resolution.py::test_location_home_and_current_ways_are_normalized_without_app_guess',
                    'tests/life/test_named_purchase.py::test_named_purchase_rejects_unknown_goods_and_unbound_apps_atomically',
                    'tests/life/test_world_query.py::test_unknown_app_and_store_names_have_the_exact_new_error_contract',
                    'tests/life/test_world_query_filters.py::test_action_app_and_category_filters_use_real_action_names_and_labels',
                    'tests/life/test_world_query.py::test_every_query_field_is_required',
                    'tests/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way')},
 '可执行筛选': {'stage': '4',
           'tests': ['tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',
                     'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change',
                     'tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
                     'tests/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way']},
 '分页与返回': {'stage': '4',
           'tests': ['tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
                     'tests/life/test_food_presentation.py::test_real_food_candidates_page_without_gaps_using_the_actual_order_key',
                     'tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',
                     'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change',
                     'tests/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way',
                     'tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero_current_action_and_control_tools',
                     'tests/life/test_world_query_filters.py::test_full_query_contains_goods_beyond_the_current_presentation_window']},
 '查询只读': {'stage': '4',
          'tests': ['tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
                    'tests/life/test_tool_protocol.py::test_world_query_mixed_batches_are_atomic',
                    'tests/life/test_tool_protocol.py::test_final_turn_and_truncation_retry_disable_every_query',
                    'tests/life/test_tool_protocol.py::test_query_dispatch_has_no_world_side_effect',
                    'tests/life/test_tool_interaction_runtime.py::test_read_queries_keep_actual_model_exchange_without_progress_events',
                    'tests/life/test_query_cost_report.py::test_query_cost_on_a_real_engine_exchange',
                    'tests/life/test_query_cost_report.py::test_query_cost_counts_failures_empty_filtered_pages_and_reflection_retries',
                    'tests/life/test_query_cost_report.py::test_query_cost_of_a_run_without_queries_and_the_printed_keys',
                    'tests/integration/test_output_truncation.py::test_a_truncated_turn_is_dropped_and_retried_once_without_thinking',
                    'tests/life/test_world_query_filters.py::test_query_remains_frozen_after_the_source_world_and_catalog_order_change']},
 '工程门禁': {'stage': '5',
          'tests': ['tests/test_style_gate.py::test_style_gate_admits_no_new_violations',
                    'tests/test_coverage_gate.py::test_branch_threshold_boundary',
                    'tests/test_coverage_gate.py::test_uncovered_public_interface_detection',
                    'tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve']},
 '复跑指标（只报告）': {'stage': '6', 'tests': []},
 '文档一致性': {'stage': '7',
           'tests': ['tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve',
                     'tests/test_design_doc_build.py::test_repository_chapters_assemble_deterministically_and_the_committed_html_is_current',
                     'tests/test_dayrun_fidelity_acceptance.py::test_anchored_links_in_docs_point_to_existing_headings',
                     'tests/test_dayrun_fidelity_acceptance.py::test_docs_carry_no_pending_markers_of_this_feature']}}

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
_REFLECTION_MODULE = "worldmock.agents.life_reflection"
_REFLECTION_SOURCE = "src/worldmock/agents/life_reflection.py"
_MEMORY_MODULE = "worldmock.agents.life_memory"
_MEMORY_SOURCE = "src/worldmock/agents/life_memory.py"
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
#: 世界状态模型接受旧版本的变异由模型拒绝和真实运行重开两个用例共同验证。
_VERSION_8_STATE_KILLERS = (
    _SESSION_STATE + "test_world_state_schema_version_8_is_rejected",
    _SESSION_STATE + "test_run_with_version_8_world_state_is_rejected_on_open",
)
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
#: 或收尾的自述写了事务名就算办成时，从未取得的纯牛奶事务被办成；记下时把已持有的份数记成累计量时，家里已有的两份让事务不再是 0 份；去掉新增
#: 位置的比较时排在新增事件之前的给予也算；去掉区间起点的比较时记下前开始的进度区间也算；不读 given 时当面给予不算；按
#: 事件人物字段归属时给予与送她的那单算到付款的李凡、她买给李凡的那单算到她自己；也读逐项结果时并行开始的同一订单算两次；
#: 同一提交结算两次时决策轮那次累计 2 份、时钟推进那次累计到 3 份办成；不看 reused 时重放的到店购买再累计一份；浓缩句
#: 为空时办成记录没有 settled 句。
_REMOTE_ORDER_KILLERS = ('tests/life/test_errand_completion.py::test_a_remote_order_completes_only_in_the_commit_that_receives_it',)
_RESULT_ONLY_KILLERS = ('tests/life/test_errand_completion.py::test_quantity_two_with_one_received_stays_open_and_a_second_receipt_completes',)
_START_KILLERS = ('tests/life/test_errand_completion.py::test_quantity_two_with_one_received_stays_open_and_a_second_receipt_completes',)
_CLAIM_KILLERS = ('tests/life/test_errand_completion.py::test_goals_and_claimed_completion_leave_the_errand_open',)
_HELD_KILLERS = ('tests/life/test_errand_completion.py::test_a_receipt_before_noting_does_not_count_for_a_new_errand',)
_NOTING_ORDER_KILLERS = ('tests/life/test_errand_settlement_idempotence.py::test_same_commit_registration_uses_event_order_instead_of_equal_clock',)
_INTERVAL_KILLERS = ('tests/life/test_food_errand_evidence.py::test_receipt_before_registration_in_same_commit_does_not_count_but_after_it_does',)
_RECIPIENT_KILLERS = ('tests/life/test_errand_completion.py::test_another_payer_receipt_credits_only_the_actual_recipient',)
_TOP_LEVEL_KILLERS = ('tests/life/test_errand_settlement_idempotence.py::test_parallel_order_result_is_not_an_extra_copy_of_receipt_evidence',)
_ONCE_PER_COMMIT_KILLERS = ('tests/life/test_errand_settlement_idempotence.py::test_purchase_commit_and_delivery_commit_each_credit_their_own_receipt_once',)
_REPLAY_KILLERS = ('tests/life/test_errand_settlement_idempotence.py::test_a_replayed_action_adds_neither_payment_receipt_nor_errand_progress',)
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
    _REMINDER + "test_open_errands_do_not_change_the_reconsideration_interval",)
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
_PROMPT_MODULE = "worldmock.agents.life_prompt"
_PROMPT_SOURCE = "src/worldmock/agents/life_prompt.py"
#: 提示分工三个变异的杀手：进度层工具调用参数带上请求级 errands 时，人物在那段停留里实际看到的收尾调用参数多出 errands、
#: 备注出现在进度层；self_notes 把请求里的事务名与备注当成自述时，离开那段停留后实际请求的记忆层出现事务的名称与备注
#: （两者都经真实 LifeAgent、LifeEnv 与 SQLite，引擎按本名导入这两个模块）；生活决策系统层第 6 条改掉一个分句时，直接
#: 导入的 LifeAgent（按本名导入 worldmock.agents.life_prompt）发出的系统层与接口合同登记不再逐字相同。
_SEPARATION_KILLERS = (_SEPARATED,)
_VERBATIM_KILLERS = (_SYSTEM_VERBATIM,)
_ERRAND_METRICS_MODULE = "worldmock.evaluation.errand_metrics"
_ERRAND_METRICS_SOURCE = "src/worldmock/evaluation/errand_metrics.py"
#: 核心行为杀手核对提醒可见性、事务事件归属、进度层参数及指标分母；数据集投影的变异由下游负责。
_VISIBLE_KILLERS = (
    "tests/life/test_errand_reminder.py::"
    "test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged",
)
_IDENTITY_KILLERS = ('tests/life/test_errand_completion.py::test_receipt_completion_tolerance_has_both_sides',)
_PROJECTION_KILLERS = (_CONTROL_ITEMS + "test_current_shape_items_carry_views_as_subject_memories",)
_ERRANDS_PROJECTION_KILLERS = (_CONTROL_ITEMS + "test_current_shape_items_carry_views_as_subject_memories",)
_DENOMINATOR_KILLERS = (_ERRAND_METRICS + "test_the_denominator_is_registered_errands_not_goals",)
_EXTRACTOR_MODULE = "worldmock.profile.extractor"
_EXTRACTOR_SOURCE = "src/worldmock/profile/extractor.py"
_CARD_MODELS_MODULE = "worldmock.profile.models"
_CARD_MODELS_SOURCE = "src/worldmock/profile/models.py"
#: 卡片锚点五个变异的杀手都经真实 extract_persona（脚本化会话）：抽取把写明的下班或到家丢成 null 时卡片没有那个窗口；下班与到家
#: 的 null 换成固定的傍晚窗口时卡片不再是 null；新锚点并入回绕归一的名字元组时 null 让归一整体放弃、凌晨就寝按同日乱序被拒
#: 直至重试耗尽；乱序不降级而原样落进锚点时每次采样都被卡片校验拒绝、重试耗尽。
_STATED_ANCHOR_KILLERS = (_CARD_EXTRACTION + "test_stated_work_anchors_are_extracted_into_the_card",)
_NO_DEFAULT_KILLERS = (_CARD_EXTRACTION + "test_unmentioned_work_anchors_stay_null_without_toml_defaults",)
_WRAPAROUND_KILLERS = (_CARD_EXTRACTION + "test_null_work_anchors_keep_the_after_midnight_bedtime_normalised",)
_DEMOTION_KILLERS = (
    _CARD_EXTRACTION + "test_out_of_order_consensus_demotes_leave_work_then_arrive_home_and_still_compiles",)
_PERSONA_LAYER_MODULE = "worldmock.agents.life_persona"
_PERSONA_LAYER_SOURCE = "src/worldmock/agents/life_persona.py"
#: 人设层三个变异的杀手都直接导入 worldmock.agents.life_persona（适配器目录的文件只有直接导入才杀得死）：人设层把卡片写明的
#: 下班或到家投影成 null 时，写明它们的卡片在人设层的下班不再是「18点到18点半」、到家不再是「19点到19点半」；人设层多渲染
#: profile 里的工作安排时，带工作安排的档案的人设层多出一个键、不再与不带工作安排的同一档案相同。
_PERSONA_ANCHOR_KILLERS = (
    _CARD_PERSONA_LAYER + "test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent",)
_PERSONA_SCHEDULE_KILLERS = (
    _CARD_PERSONA_LAYER + "test_a_declared_work_schedule_is_not_rendered_in_the_persona_layer",)
_DAY_PLAN_INPUTS_MODULE = "worldmock.life.day_plan_inputs"
_DAY_PLAN_INPUTS_SOURCE = "src/worldmock/life/day_plan_inputs.py"
_INJECTION_MODULE = "worldmock.profile.injection"
_INJECTION_SOURCE = "src/worldmock/profile/injection.py"
#: 场景作息覆盖两个变异的杀手是同一个真实新建用例（_create_run 在进程内建立 LifeEnv，runtime 按本名导入
#: worldmock.profile.injection，用例直接调用 plan_inputs）：零点计划人物输入丢掉场景声明的下班、到家时段时两个时段
#: 不再出现；覆盖函数对 null 槽跳过场景值时冻结卡片没有下班、到家窗口，清单也没有这两条覆盖。
_SCENARIO_WORK_WINDOW_KILLERS = (
    _CARD_INPUT_WARNINGS + "test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input",)
_LOAD_CHECKS_MODULE = "worldmock.life.load_checks"
_LOAD_CHECKS_SOURCE = "src/worldmock/life/load_checks.py"
_SCENARIO_MODULE = "worldmock.life.scenario"
_SCENARIO_SOURCE = "src/worldmock/life/scenario.py"
#: 工作安排加载两个变异的杀手（都在主进程内经真实加载入口）：加载时不做日历交叉核对，时间窗内与日历不一致的工作安排
#: 照常加载；场景结构 4 被放行，版本写回 4 的场景经 parse_scenario_document 与 load_scenario 照常加载。
_CALENDAR_CHECK_KILLERS = (_WORK_SCHEDULE_LOADS + "test_work_schedule_must_agree_with_the_calendar_inside_the_window",)
_STRUCTURE_FOUR_KILLERS = (_SCENARIO_STRUCTURE,)
_WORK_SCHEDULE_MODULE = "worldmock.life.work_schedule"
_WORK_SCHEDULE_SOURCE = "src/worldmock/life/work_schedule.py"
_ENVIRONMENT_LAYER_MODULE = "worldmock.life.environment_layer"
_ENVIRONMENT_LAYER_SOURCE = "src/worldmock/life/environment_layer.py"
_WORK_MODULE = "worldmock.life.work"
_WORK_SOURCE = "src/worldmock/life/work.py"
#: 上班安排七个变异的杀手都在主进程内驱动真实 LifeEngine（环境层与工作合同按本名导入 work_schedule、environment_layer
#: 与 work）：按星期推断休息日时周一写 null 的人物仍有班次，忽略例外日期时例外写 null 的周一当班、例外写班次的周一休息
#: （双人场景的时间窗没有日历特殊日，加载照常）；休息时段显示为当班中时 12:00 与 12:30 不是「休息时段」；所有人物套某人的班次时
#: 没有工作安排的人物 2 也有 duty；计划有目标后 duty 消失时修订被受理后的观察里 duty 为 null；有任务付款结清后 duty 消失时
#: 真实提交并付款后的观察里 duty 为 null；班次并入任务开放判定（写死夹具的 09:00 与场景的 +08:00）时 08:30 的真实开工被
#: 拒绝。
_REST_DAY_KILLERS = (
    _WORK_SCHEDULE + "test_rest_days_come_from_weekly_null_and_exception_null_and_an_exception_shift_overrides_weekly",)
_BREAK_KILLERS = (_WORK_SCHEDULE + "test_duty_follows_the_clock_through_a_working_day",)
_UNSCHEDULED_KILLERS = (_WORK_SCHEDULE + "test_people_without_a_schedule_have_no_duty_while_others_keep_theirs",)
_PLAN_REVISION_DUTY_KILLERS = (_WORK_SCHEDULE + "test_revising_the_day_plan_does_not_change_duty",)
_TOPIC_COMPLETION_DUTY_KILLERS = (_WORK_SCHEDULE + "test_duty_stays_on_duty_after_topic_completion",)
#: 零点计划输入带上 status 的杀手（主进程内经真实引擎与零点计划后台任务，零点计划输入按本名调用
#: work_schedule.day_arrangement）：当班日的「上班安排」多出 status 键，载荷与请求不再是四键。环境层 duty 本来就带
#: status，这一变异不改变它。
_PLAN_INPUT_STATUS_KILLERS = (
    _DAY_PLANNING + "test_plan_input_carries_the_plan_dates_duty_arrangement_without_status",)
_TOOL_GROUPS_MODULE = "worldmock.life.tool_groups"
_TOOL_GROUPS_SOURCE = "src/worldmock/life/tool_groups.py"
#: 紧凑投影按真假值丢值的杀手：冻结页夹具上读出 false 与零价的一例，真实引擎上读出门店商品到店 false 的一例。原文与
#: 工具交互台账的 compact_drops_false 相同，两份台账各登记一次，回执目录分开。
_COMPACT_KILLERS = ('tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',)
_WORLD_QUERY_MODULE = "worldmock.life.world_query"
_WORLD_QUERY_SOURCE = "src/worldmock/life/world_query.py"
#: 环境分支写世界的杀手保留真实 LifeEnv 节点，完整引擎路径当前没有在食品纯验证中执行。
_ENV_QUERY_KILLERS = ('tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',)
#: 食品查询的纯杀手逐个写在对应变异条目，分别验证实际 app 来源、必填键、方式、可执行性和分页。

#: 源码变异清单；每个阶段的提交在这里追加自己的条目（未实现阶段的原文还不存在，无法预先登记）。
MUTATIONS = (SourceMutation(name='decision_failure_swallowed_after_log',
                symbol='custom.agents.life_agent::LifeAgent.step',
                original='            logging.exception("LifeAgent decision failed for agent %s", self.id)\n'
                         '            raise\n',
                replacement='            logging.exception("LifeAgent decision failed for agent %s", self.id)\n'
                            '            return ""\n',
                tests=('tests/integration/test_life_agent_protocol.py::test_decision_failure_is_logged_once_and_reraised_unchanged',
                       'tests/integration/test_host_failure_stages.py::test_model_service_failure_on_the_real_engine_is_logged_once_and_reraised_unchanged')),
 SourceMutation(name='tool_dispatch_failure_loses_stage_code',
                symbol='custom.agents.life_agent::LifeAgent._execute_react_tool',
                original='            feedback = _stage_feedback("TOOL_SERVICE_FAILED", ("tool_calls", index))\n'
                         '            raise RuntimeError(feedback) from error\n',
                replacement='            feedback = _stage_feedback("TOOL_SERVICE_FAILED", ("tool_calls", '
                            'index))\n'
                            '            raise\n',
                tests=('tests/integration/test_host_failure_stages.py::test_tool_service_failure_points_to_real_call_without_claiming_execution',
                       'tests/integration/test_host_failure_stages.py::test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage')),
 SourceMutation(name='plan_duplicates_report_only_a_generic_error',
                symbol='worldmock.life.models::_validate_plan_goals',
                original='        raise ValidationError.from_exception_data(model_name, errors)\n',
                replacement='        raise ValueError("day goals must contain at most eight unique intents")\n',
                tests=('tests/life/test_revise_day_plan_json.py::test_engine_rejects_a_duplicate_revision_locating_first_and_later_entries',
                       'tests/life/test_plan_day_duplicate_feedback.py::test_plan_day_duplicate_intents_are_fed_back_with_every_position_then_registered')),
 SourceMutation(name='plan_duplicates_locate_only_later_entries',
                symbol='worldmock.life.models::_validate_plan_goals.repeated',
                original='if len(indexes) > 1 for index in indexes)\n',
                replacement='if len(indexes) > 1 for index in indexes[1:])\n',
                tests=('tests/life/test_revise_day_plan_json.py::test_engine_rejects_a_duplicate_revision_locating_first_and_later_entries',
                       'tests/life/test_plan_day_duplicate_feedback.py::test_plan_day_duplicate_intents_are_fed_back_with_every_position_then_registered')),
 SourceMutation(name='plan_over_capacity_returns_before_duplicates',
                symbol='worldmock.life.models::_validate_plan_goals',
                original='        errors.append({"type": "value_error", "loc": (), "input": '
                         '[goal.model_dump(mode="json") for goal in goals],\n'
                         '                       "ctx": {"error": ValueError("day goals require at most eight '
                         'entries")}})\n',
                replacement='        raise ValueError("day goals require at most eight entries")\n',
                tests=('tests/life/test_revise_day_plan_json.py::test_engine_reports_over_capacity_and_duplicates_in_the_same_rejection',
                       'tests/life/test_plan_day_duplicate_feedback.py::test_plan_day_reports_over_capacity_together_with_duplicates')),
 SourceMutation(name='assembly_proxy_reverts_to_the_as2_class',
                symbol='worldmock.runtime::_build_society.env',
                original='        env = LifeEnvProxy(actor, run_dir=prepared.run_dir, '
                         'env_module_types=["LifeEnv"])\n',
                replacement='        from agentsociety2.env.env_router_proxy import EnvRouterProxy\n'
                            '\n'
                            '        env = EnvRouterProxy(actor, run_dir=prepared.run_dir, '
                            'env_module_types=["LifeEnv"])\n',
                tests=('tests/integration/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',)),
 SourceMutation(name='assembly_service_proxy_reverts_to_the_as2_class',
                symbol='worldmock.runtime::_build_society.service',
                original='        service = build_service_proxy(env, run_dir=prepared.run_dir, trace=False, '
                         'replay=False)\n',
                replacement='        from agentsociety2.env.env_router_proxy import EnvRouterProxy\n'
                            '\n'
                            '        service = build_service_proxy(\n'
                            '            EnvRouterProxy(actor, run_dir=prepared.run_dir, '
                            'env_module_types=["LifeEnv"]),\n'
                            '            run_dir=prepared.run_dir, trace=False, replay=False)\n',
                tests=('tests/integration/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',)),
 SourceMutation(name='assembly_actor_reverts_to_the_as2_class',
                symbol='worldmock.runtime::_build_society.actor',
                original='    actor = life_env_actor_class().remote(spec)\n',
                replacement='    from agentsociety2.env.direct_env_actor import get_direct_env_actor_class\n'
                            '\n'
                            '    actor = get_direct_env_actor_class().remote(spec)\n',
                tests=('tests/integration/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',)),
 SourceMutation(name='proxy_returns_without_awaiting_the_remote_result',
                symbol='worldmock.life.read_query_runtime::LifeEnvProxy.read_query',
                original='        return await self._actor.read_query.remote(context, tool, request)\n',
                replacement='        return self._actor.read_query.remote(context, tool, request)\n',
                tests=('tests/integration/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',)),
 SourceMutation(name='actor_answers_without_the_environment',
                symbol='worldmock.life.read_query_runtime::LifeEnvActor.read_query',
                original='        return await self._router._environment().read_query(context, tool, request)\n',
                replacement='        return {}\n',
                tests=('tests/integration/test_read_query_runtime.py::test_unwrapped_actor_read_query_equals_direct_environment_call',)),
 SourceMutation(name='actor_class_allows_concurrent_calls',
                symbol='worldmock.life.read_query_runtime::life_env_actor_class',
                original='    return ray.remote(max_concurrency=1)(LifeEnvActor)\n',
                replacement='    return ray.remote(max_concurrency=2)(LifeEnvActor)\n',
                tests=('tests/integration/test_read_query_runtime.py::test_life_env_actor_class_fixes_single_concurrency_without_parameters',)),
 SourceMutation(name='reflection_accepts_free_text',
                symbol='worldmock.agents.life_reflection::reflection_insights.arguments',
                original='    arguments = _tool_arguments(response)\n',
                replacement='    try:\n'
                            '        arguments = _tool_arguments(response)\n'
                            '    except ReflectionOutputError:\n'
                            '        arguments = json.dumps({"insights": '
                            'json.loads(response.choices[0].message.content)})\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_accepts_another_tool',
                symbol='worldmock.model_feedback::tool_arguments',
                original='        if _field(function, "name") != tool:\n',
                replacement='        if False:\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_skips_unknown_reference_check',
                symbol='worldmock.agents.life_reflection::reflection_insights',
                original='    if problems:\n',
                replacement='    if False:\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_count_bound_widened',
                symbol='worldmock.agents.life_reflection::ReflectionRequest.require_insights',
                original='        if not 1 <= len(insights) <= MEMORY_REFLECTION_MAX_INSIGHTS:\n',
                replacement='        if not 1 <= len(insights) <= MEMORY_REFLECTION_MAX_INSIGHTS + 1:\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_text_validation_skipped',
                symbol='worldmock.agents.life_reflection::ReflectionInsight.validate_insight',
                original='        return validate_memory_text(insight)\n',
                replacement='        return insight\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_retry_bound_zero',
                symbol='worldmock.agents.life_reflection::REFLECTION_VALIDATION_RETRIES',
                original='REFLECTION_VALIDATION_RETRIES = 3\n',
                replacement='REFLECTION_VALIDATION_RETRIES = 0\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_retries_without_a_bound',
                symbol='worldmock.agents.life_memory::WorldMockMemoryRuntime._validated_reflection',
                original='                if attempt > REFLECTION_VALIDATION_RETRIES:\n',
                replacement='                if False:\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection',)),
 SourceMutation(name='reflection_failure_returns_an_empty_list',
                symbol='worldmock.agents.life_memory::WorldMockMemoryRuntime._validated_reflection',
                original='                    raise ProtocolError(\n'
                         '                        f"memory reflection output remained invalid after three '
                         'retries: {error}") from error\n',
                replacement='                    return []\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection',
                       'tests/integration/test_strict_reflection.py::test_exhausted_reflection_leaves_evidence_but_no_memory_update_in_the_run_database')),
 SourceMutation(name='reflection_correction_accumulates_messages',
                symbol='worldmock.agents.life_memory::WorldMockMemoryRuntime._validated_reflection',
                original='                messages, attempt = reflection_correction(original, response, error), '
                         'attempt + 1\n',
                replacement='                messages, attempt = reflection_correction(messages, response, '
                            'error), attempt + 1\n',
                tests=('tests/integration/test_strict_reflection.py::test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection',)),
 SourceMutation(name='version_8_world_state_accepted',
                symbol='worldmock.life.models::WorldState.schema_version',
                original='    schema_version: Literal[WORLD_SCHEMA_VERSION]\n',
                replacement='    schema_version: Literal[WORLD_SCHEMA_VERSION, 8]\n',
                tests=('tests/life/test_session_state.py::test_world_state_schema_version_8_is_rejected',
                       'tests/life/test_session_state.py::test_run_with_version_8_world_state_is_rejected_on_open')),
 SourceMutation(name='todo_layer_omits_errands',
                symbol='worldmock.life.todo_layer::todo_layer',
                original='            "errands": errands.todo_rows(agent, names, context.scenario.timezone),\n',
                replacement='            "errands": [],\n',
                tests=('tests/life/test_errand_requests.py::test_each_control_tool_records_errand_changes_with_one_event_per_change_and_an_ordered_receipt',)),
 SourceMutation(name='errand_changes_applied_before_rejection',
                symbol='worldmock.life.session_actions::ActionHandlers._conclude_activity.changes',
                original='        changes = self._errand_changes(pending, "conclude_activity", request)\n',
                replacement='        changes = self._errand_changes(pending, "conclude_activity", request)\n'
                            '        state.agents[pending.agent_id] = self._apply_errands(state, pending, '
                            'changes)[0].agents[\n'
                            '            pending.agent_id]\n',
                tests=('tests/life/test_errand_requests.py::test_a_rejected_request_registers_no_errand_and_writes_no_errand_event',)),
 SourceMutation(name='request_can_mark_errand_completed',
                symbol='worldmock.life.models::ErrandChange.change',
                original='    change: Literal["新增", "更新", "取消"] = Field(description="这条变更做什么，从列出的三种里选一种。")\n',
                replacement='    change: Literal["新增", "更新", "取消", "完成"] = '
                            'Field(description="这条变更做什么，从列出的三种里选一种。")\n',
                tests=('tests/life/test_errand_requests.py::test_a_request_cannot_mark_an_errand_completed',)),
 SourceMutation(name='items_without_errands_accepted',
                symbol='worldmock.life.control_items::control_items',
                original='        _list(_field(body, "errands", tool), tool)\n',
                replacement='',
                tests=('tests/life/test_control_items.py::test_a_current_request_without_errands_is_rejected',)),
 SourceMutation(name='errand_name_takes_first_candidate',
                symbol='worldmock.life.errands::_open_name',
                original='    return _named_rows(written, rows)\n',
                replacement='    found, matches = _named_rows(written, rows)\n'
                            '    fallback = [*matches, *near_names(written, [row["name"] for row in rows])]\n'
                            '    if len(found) != 1 and fallback:\n'
                            '        found = [row for row in rows if row["name"] == fallback[0]][:1]\n'
                            '    return found, matches\n',
                tests=('tests/life/test_errand_names.py::test_unknown_or_ambiguous_errand_name_lists_every_open_name_and_substitutes_nothing',)),
 SourceMutation(name='duplicates_judged_before_cancels',
                symbol='worldmock.life.errands::_plan.remaining',
                original='    remaining = [row for row in rows if row["errand_id"] not in cancelled]\n',
                replacement='    remaining = list(rows)\n',
                tests=('tests/life/test_errand_names.py::test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted',)),
 SourceMutation(name='errand_named_twice_accepted',
                symbol='worldmock.life.errands::_plan_targets',
                original='        elif found[0]["errand_id"] in named:\n',
                replacement='        elif False:\n',
                tests=('tests/life/test_errand_names.py::test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected',)),
 SourceMutation(name='added_twins_with_one_product_accepted',
                symbol='worldmock.life.errands::_added_conflicts.doubled',
                original='    doubled = [other for other in earlier if other in plan.products\n',
                replacement='    doubled = [other for other in () if other in plan.products\n',
                tests=('tests/life/test_errand_names.py::test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected',)),
 SourceMutation(name='pending_order_counts_as_obtained',
                symbol='worldmock.life.errands::evidence_rows',
                original='    if event_type != "environment_event" or data.get("kind") != "goods_received":\n'
                         '        return []',
                replacement='    if event_type == "action_result":\n'
                            '        orders = (data.get("data") or {}).get("orders", [])\n'
                            '        return [Evidence(int(order["recipient_id"].split(":", 1)[1]), '
                            'order["goods_id"], float(order["quantity"]))\n'
                            '                for order in orders if order["recipient_id"].startswith("agent:") '
                            'and order["status"] == "pending"]\n'
                            '    if event_type != "environment_event" or data.get("kind") != "goods_received":\n'
                            '        return []',
                tests=('tests/life/test_food_errand_evidence.py::test_actual_payment_orders_are_not_errand_evidence_even_when_delivered',)),
 SourceMutation(name='accepted_event_counts_as_evidence',
                symbol='worldmock.life.errands::evidence_rows',
                original='    if event_type != "environment_event" or data.get("kind") != "goods_received":\n'
                         '        return []',
                replacement='    if event_type in {"action_result", "action_accepted"}:\n'
                            '        orders = (data.get("data") or {}).get("orders", [])\n'
                            '        return [Evidence(int(order["recipient_id"].split(":", 1)[1]), '
                            'order["goods_id"], float(order["quantity"]))\n'
                            '                for order in orders if order["recipient_id"].startswith("agent:")]\n'
                            '    if event_type != "environment_event" or data.get("kind") != "goods_received":\n'
                            '        return []',
                tests=('tests/life/test_food_errand_evidence.py::test_actual_payment_orders_are_not_errand_evidence_even_when_delivered',)),
 SourceMutation(name='progressed_goal_counts_as_completion',
                symbol='worldmock.life.errands::evidence_rows',
                original='data.get("kind") != "goods_received"',
                replacement='data.get("kind") not in {"goods_received", "goal_progressed"}',
                tests=('tests/life/test_food_errand_evidence.py::test_retired_giving_consumption_and_self_reports_cannot_progress_errands',)),
 SourceMutation(name='self_report_counts_as_completion',
                symbol='worldmock.life.errands::evidence_rows',
                original='data.get("kind") != "goods_received"',
                replacement='data.get("kind") not in {"goods_received", "errand_claimed"}',
                tests=('tests/life/test_food_errand_evidence.py::test_retired_giving_consumption_and_self_reports_cannot_progress_errands',)),
 SourceMutation(name='evidence_before_noting_event_counts',
                symbol='worldmock.life.errands::settle',
                original='            if errand is None or noted.get(errand.errand_id, -1) > index:\n',
                replacement='            if errand is None:\n',
                tests=('tests/life/test_food_errand_evidence.py::test_receipt_before_registration_in_same_commit_does_not_count_but_after_it_does',)),
 SourceMutation(name='ownership_follows_event_agent',
                symbol='worldmock.life.errands::evidence_rows',
                original='return [Evidence(agent_id, data["goods_id"], float(data["quantity"]))]',
                replacement='return [Evidence(data["payer_id"], data["goods_id"], float(data["quantity"]))]',
                tests=('tests/life/test_food_errand_evidence.py::test_actual_pickup_credits_only_receipt_recipient_and_repeat_settlement_emits_no_evidence',)),
 SourceMutation(name='round_commit_settles_twice',
                symbol='worldmock.life.decision_coordinator::DecisionCoordinator.commit_decision_round',
                original='        (staged, settled) = errands.settle(staged, drafts)\n',
                replacement='        (staged, settled) = errands.settle(staged, drafts)\n'
                            '        staged, again = errands.settle(staged, drafts)\n'
                            '        settled = settled + again\n',
                tests=('tests/life/test_errand_completion.py::'
                       'test_quantity_two_with_one_received_stays_open_and_a_second_receipt_completes',)),
 SourceMutation(name='advance_commit_settles_twice',
                symbol='worldmock.life.time_advance::TimeAdvanceCoordinator.advance',
                original='        (staged, credited) = errands.settle(staged, drafts)\n',
                replacement='        (staged, credited) = errands.settle(staged, drafts)\n'
                            '        staged, again = errands.settle(staged, drafts)\n'
                            '        credited = credited + again\n',
                tests=('tests/life/test_errand_reminder.py::'
                       'test_partial_progress_after_the_reminder_does_not_remind_again',)),
 SourceMutation(name='condensation_drops_completion_line',
                symbol='worldmock.life.settled_text::record_lines',
                original='        return [f"{_clock(context, record[\'occurred_at\'])} '
                         '办成「{record[\'errand\']}」"]\n',
                replacement='        return []\n',
                tests=('tests/life/test_progress_events.py::test_an_errand_completed_in_the_session_is_one_record_and_folds_to_one_line',)),
 SourceMutation(name='cancelled_errand_stays_armed',
                symbol='worldmock.life.errands::_apply_one',
                original='        errands.pop(plan.targets[index]["errand_id"])\n',
                replacement='        errands.get(plan.targets[index]["errand_id"])\n',
                tests=('tests/life/test_errand_reminder.py::test_completed_or_cancelled_errands_are_not_reminded',)),
 SourceMutation(name='reminder_completes_errand',
                symbol='worldmock.life.errands::deliver_reminders',
                original='        errands[errand.errand_id] = reminded\n',
                replacement='        errands.pop(errand.errand_id)\n',
                tests=('tests/life/test_errand_reminder.py::test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged',)),
 SourceMutation(name='errand_reminder_wakes_sleep',
                symbol='worldmock.life.schedule::SLEEP_ALLOWED_REASONS',
                original='WakeReason.PLAN_REMINDER_DUE, WakeReason.ALARM_RING}',
                replacement='WakeReason.PLAN_REMINDER_DUE, WakeReason.ALARM_RING, WakeReason.ERRAND_REMINDER_DUE}',
                tests=('tests/life/test_errand_reminder.py::test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary',)),
 SourceMutation(name='open_errands_are_commitments',
                symbol='worldmock.life.rhythm::_has_commitments',
                original='    if (\n        plan is not None\n',
                replacement='    if agent.errands or (\n        plan is not None\n',
                tests=('tests/life/test_errand_reminder.py::test_open_errands_do_not_change_the_reconsideration_interval',)),
 SourceMutation(name='errand_wake_reason_missing_from_vocabulary',
                symbol='worldmock.footprints.models::WAKE_REASON_NAMES',
                original='    "progress_report_due", "no_active_activity", "plan_reminder_due", '
                         '"errand_reminder_due",\n',
                replacement='    "progress_report_due", "no_active_activity", "plan_reminder_due",\n',
                tests=('tests/life/test_errand_reminder.py::test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged',)),
 SourceMutation(name='sleeper_reminder_time_is_a_boundary',
                symbol='worldmock.life.errands::boundary_times',
                original='    return [moment for agent_id, agent in state.agents.items() if not _asleep(state, '
                         'agent_id)\n',
                replacement='    return [moment for agent_id, agent in state.agents.items()\n',
                tests=('tests/life/test_errand_reminder.py::test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary',)),
 SourceMutation(name='reminder_recorded_without_delivery',
                symbol='worldmock.life.rhythm::_deliver_errand_reminders',
                original='        if rhythm_schedule.WakeReason.ERRAND_REMINDER_DUE.value in '
                         'verdict.wake_reasons:\n',
                replacement='        if True:\n',
                tests=('tests/life/test_errand_reminder.py::test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary',)),
 SourceMutation(name='simultaneous_reminders_write_one_event',
                symbol='worldmock.life.errands::deliver_reminders',
                original='    return state.model_copy(update={"agents": agents}), drafts\n',
                replacement='    return state.model_copy(update={"agents": agents}), drafts[:1]\n',
                tests=('tests/life/test_errand_reminder.py::test_errands_due_together_are_one_opportunity_with_one_event_each',)),
 SourceMutation(name='midnight_clears_errands',
                symbol='worldmock.life.time_advance::TimeAdvanceCoordinator.advance',
                original='            staged = _put_agent(staged, '
                         'staged.agents[agent_id].model_copy(update={"day_plan": None}, deep=True))\n',
                replacement='            staged = _put_agent(staged, staged.agents[agent_id].model_copy(\n'
                            '                update={"day_plan": None, "errands": {}}, deep=True))\n',
                tests=('tests/life/test_errand_overnight.py::test_an_errand_stays_in_the_todo_layer_after_midnight_plan_generation_and_plan_revision',)),
 SourceMutation(name='plan_revision_drops_errands',
                symbol='worldmock.life.session_actions::ActionHandlers._revise_day_plan.changed_agent',
                original='        changed_agent = agent.model_copy(update={"day_plan": plan}, deep=True)\n',
                replacement='        changed_agent = agent.model_copy(update={"day_plan": plan, "errands": {}}, '
                            'deep=True)\n',
                tests=('tests/life/test_errand_overnight.py::test_an_errand_stays_in_the_todo_layer_after_midnight_plan_generation_and_plan_revision',)),
 SourceMutation(name='condensation_drops_todo_errands',
                symbol='worldmock.life.prompt_capacity::fit_request',
                original='            layers[index] = {**layers[index], "events": records}\n',
                replacement='            layers[index] = {**layers[index], "events": records}\n'
                            '            layers = [{**layer, "errands": []} if layer.get("layer") == "待办层" else '
                            'layer\n'
                            '                      for layer in layers]\n',
                tests=('tests/life/test_errand_overnight.py::test_a_condensed_request_still_carries_the_todo_layer_errands',)),
 SourceMutation(name='memory_overflow_drops_todo_errands',
                symbol='worldmock.life.observe::ObservationBuilder._layer_data',
                original='            "todo_layer": todo_layer(context, self.context.store),\n',
                replacement='            "todo_layer": {**todo_layer(context, self.context.store), **(\n'
                            '                {"errands": []} if len(str(memory.layer)) > '
                            'memory_layer.MEMORY_WINDOW_CHARS // 2\n'
                            '                else {})},\n',
                tests=('tests/life/test_errand_overnight.py::test_an_errand_stays_when_the_memory_layer_overflows_its_window',)),
 SourceMutation(name='checkpoint_omits_errands',
                symbol='worldmock.life.models::AgentState.errands',
                original='    errands: dict[str, ErrandState] = Field(default_factory=dict)\n',
                replacement='    errands: dict[str, ErrandState] = Field(default_factory=dict, exclude=True)\n',
                tests=('tests/integration/test_errand_checkpoint.py::test_a_restored_branch_shows_the_same_todo_layer_and_repeats_nothing',)),
 SourceMutation(name='progress_arguments_carry_errands',
                symbol='worldmock.life.progress_layer::_arguments',
                original='    return control_request(tool, request)\n',
                replacement='    return {**control_request(tool, request), "errands": request["errands"]}\n',
                tests=('tests/integration/test_errand_prompt_separation.py::test_errand_changes_write_no_memory_and_stay_out_of_self_notes_and_progress_arguments',)),
 SourceMutation(name='self_notes_carry_errand_text',
                symbol='worldmock.life.memory_layer::_self_notes',
                original='            notes.extend((item.activity_name, text) for text in texts if '
                         'text.strip())\n',
                replacement='            notes.extend((item.activity_name, text) for text in texts if '
                            'text.strip())\n'
                            '            from worldmock.life.control_items import control_errands\n'
                            '            notes.extend((item.activity_name, change[key]) for change in '
                            'control_errands(\n'
                            '                request.data["tool_name"], request.data["request"]) for key in '
                            '("name", "memo") if change[key])\n',
                tests=('tests/integration/test_errand_prompt_separation.py::test_errand_changes_write_no_memory_and_stay_out_of_self_notes_and_progress_arguments',)),
 SourceMutation(name='system_layer_copy_diverges',
                symbol='worldmock.agents.life_prompt::LIFE_SYSTEM_LAYER',
                original='到提醒时刻只提醒一次，不会叫醒睡着的你',
                replacement='到提醒时刻只提醒一次，会叫醒睡着的你',
                tests=('tests/integration/test_seven_layer_request.py::test_system_layers_are_the_contract_registration_verbatim',)),
 SourceMutation(name='errand_agent_events_written_as_system',
                symbol='worldmock.life.errands::_draft',
                original='                      visibility="agent" if kind in AGENT_VISIBLE_KINDS else '
                         '"system",\n',
                replacement='                      visibility="system",\n',
                tests=('tests/life/test_errand_reminder.py::test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged',)),
 SourceMutation(name='settlement_event_inherits_evidence_identity',
                symbol='worldmock.life.errands::settle',
                original='            settled.append(_credit(open_sets[row.owner_id], errand, row, head))\n',
                replacement='            settled.append(_credit(open_sets[row.owner_id], errand, row, '
                            'head).model_copy(\n'
                            '                update={"decision_id": draft.decision_id, "action_id": '
                            'draft.action_id}))\n',
                tests=('tests/life/test_errand_completion.py::test_receipt_completion_tolerance_has_both_sides',)),
 SourceMutation(name='control_projection_carries_errands',
                symbol='worldmock.life.control_items::control_request',
                original='    return {"items": rows}\n',
                replacement='    return {"items": rows, "errands": list(control_errands(tool, request))}\n',
                tests=('tests/life/test_control_items.py::test_current_shape_items_carry_views_as_subject_memories',)),
 SourceMutation(name='control_projection_trims_first_item_only',
                symbol='worldmock.life.control_items::control_request',
                original='    for item in control_items(tool, request):\n'
                         '        row: dict[str, Any] = {"activity_name": item.activity_name, "summary": '
                         'item.summary}\n',
                replacement='    for index, item in enumerate(control_items(tool, request)):\n'
                            '        row: dict[str, Any] = {"activity_name": item.activity_name, "summary": '
                            'item.summary}\n'
                            '        if index:\n'
                            '            row["views"] = list(item.views)\n',
                tests=('tests/life/test_control_items.py::test_current_shape_items_carry_views_as_subject_memories',)),
 SourceMutation(name='errand_metrics_use_goal_denominator',
                symbol='worldmock.evaluation.errand_metrics::errand_metrics.registered',
                original='    registered = len(lives)\n',
                replacement='    registered = sum(len(event["data"]["day_plan"]["goals"]) for event in events\n'
                            '                     if event["data"].get("kind") == "day_plan_generated")\n',
                tests=('tests/evaluation/test_errand_metrics.py::test_the_denominator_is_registered_errands_not_goals',)),
 SourceMutation(name='extraction_drops_stated_work_anchor',
                symbol='worldmock.profile.extractor::_work_anchors',
                original='        anchors.leave_home, anchors.bedtime, parsed["leave_work"], '
                         'parsed["arrive_home"])\n',
                replacement='        anchors.leave_home, anchors.bedtime, None, parsed["arrive_home"])\n',
                tests=('tests/profile/test_extractor.py::test_stated_work_anchors_are_extracted_into_the_card',)),
 SourceMutation(name='extraction_drops_stated_arrive_home',
                symbol='worldmock.profile.extractor::_work_anchors',
                original='        anchors.leave_home, anchors.bedtime, parsed["leave_work"], '
                         'parsed["arrive_home"])\n',
                replacement='        anchors.leave_home, anchors.bedtime, parsed["leave_work"], None)\n',
                tests=('tests/profile/test_extractor.py::test_stated_work_anchors_are_extracted_into_the_card',)),
 SourceMutation(name='leave_work_takes_a_default',
                symbol='worldmock.profile.extractor::_work_anchors.parsed',
                original='None if raw[name] is None else TimeWindow.from_clock_range(raw[name])',
                replacement='TimeWindow.from_clock_range(raw[name] or "18:00-18:30")',
                tests=('tests/profile/test_extractor.py::test_unmentioned_work_anchors_stay_null_without_toml_defaults',)),
 SourceMutation(name='work_anchors_join_wraparound',
                symbol='worldmock.profile.models::TimeAnchors.normalize_wraparound.names',
                original='        names = ("wake", "leave_home", "lunch", "dinner", "bedtime")\n',
                replacement='        names = ("wake", "leave_home", "lunch", "dinner", "bedtime", "leave_work", '
                            '"arrive_home")\n',
                tests=('tests/profile/test_extractor.py::test_null_work_anchors_keep_the_after_midnight_bedtime_normalised',)),
 SourceMutation(name='out_of_order_work_anchor_fails_the_card',
                symbol='worldmock.profile.extractor::_work_anchors',
                original='    leave_work, arrive_home, problems = settle_work_anchors(\n'
                         '        anchors.leave_home, anchors.bedtime, parsed["leave_work"], '
                         'parsed["arrive_home"])\n',
                replacement='    leave_work, arrive_home, problems = parsed["leave_work"], parsed["arrive_home"], '
                            '()\n',
                tests=('tests/profile/test_extractor.py::test_out_of_order_consensus_demotes_leave_work_then_arrive_home_and_still_compiles',)),
 SourceMutation(name='persona_layer_drops_work_anchor',
                symbol='worldmock.agents.life_persona::_card_fields',
                original='"leave_work": _optional_window(anchors.leave_work)',
                replacement='"leave_work": None',
                tests=('tests/life/test_persona_layer.py::test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent',)),
 SourceMutation(name='persona_layer_drops_arrive_home',
                symbol='worldmock.agents.life_persona::_card_fields',
                original='"arrive_home": _optional_window(anchors.arrive_home)',
                replacement='"arrive_home": None',
                tests=('tests/life/test_persona_layer.py::test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent',)),
 SourceMutation(name='host_renders_work_schedule',
                symbol='worldmock.agents.life_persona::persona_layer.layer',
                original='        "preferences": [dict(item) for item in views["preferences"]],\n',
                replacement='        "preferences": [dict(item) for item in views["preferences"]],\n'
                            '        "work_schedule": profile.get("work_schedule"),\n',
                tests=('tests/life/test_persona_layer.py::test_a_declared_work_schedule_is_not_rendered_in_the_persona_layer',)),
 SourceMutation(name='scenario_work_windows_missing_from_plan_input',
                symbol='worldmock.life.day_plan_inputs::_persona.fields',
                original='"下班时段": _hours(config.leave_work_window),\n'
                         '        "到家时段": _hours(config.arrive_home_window),',
                replacement='"下班时段": None,\n        "到家时段": None,',
                tests=('tests/integration/test_input_warnings.py::test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input',)),
 SourceMutation(name='override_skips_null_card_slot',
                symbol='worldmock.profile.injection::_window_overrides',
                original='        if field not in declared:\n            continue\n',
                replacement='        if field not in declared or getattr(anchors, slot) is None:\n'
                            '            continue\n',
                tests=('tests/integration/test_input_warnings.py::test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input',)),
 SourceMutation(name='calendar_cross_check_skipped',
                symbol='worldmock.life.load_checks::check_work_schedules.problems',
                original='problems = [*_workplace_problems(scenario), *_calendar_problems(scenario)]',
                replacement='problems = [*_workplace_problems(scenario)]',
                tests=('tests/life/test_load_checks.py::test_work_schedule_must_agree_with_the_calendar_inside_the_window',)),
 SourceMutation(name='scenario_structure_four_admitted',
                symbol='worldmock.life.scenario::LifeScenario',
                original='    schema_version: Literal[8]\n',
                replacement='    schema_version: Literal[4, 8]\n',
                tests=('tests/life/test_scenario.py::test_structure_eight_rejects_old_scenario_version',)),
 SourceMutation(name='rest_day_inferred_from_weekday',
                symbol='worldmock.life.work_schedule::shift_on',
                original='return schedule.weekly[str(day.weekday())]',
                replacement='return None if day.weekday() >= 5 else next(item for item in '
                            'schedule.weekly.values() if item is not None)',
                tests=('tests/life/test_work_schedule.py::test_rest_days_come_from_weekly_null_and_exception_null_and_an_exception_shift_overrides_weekly',)),
 SourceMutation(name='exception_dates_ignored',
                symbol='worldmock.life.work_schedule::shift_on',
                original='if key in schedule.exceptions:',
                replacement='if False:',
                tests=('tests/life/test_work_schedule.py::test_rest_days_come_from_weekly_null_and_exception_null_and_an_exception_shift_overrides_weekly',)),
 SourceMutation(name='break_shown_as_on_duty',
                symbol='worldmock.life.work_schedule::duty_status',
                original='if any(begin <= now < finish for begin, finish in break_spans(shift, day, zone)):',
                replacement='if False:',
                tests=('tests/life/test_work_schedule.py::test_duty_follows_the_clock_through_a_working_day',)),
 SourceMutation(name='everyone_gets_a_shift',
                symbol='worldmock.life.environment_layer::_duty.config',
                original='if item.agent_id == context.agent_id)',
                replacement='if item.work_schedule is not None)',
                tests=('tests/life/test_work_schedule.py::test_people_without_a_schedule_have_no_duty_while_others_keep_theirs',)),
 SourceMutation(name='duty_vanishes_after_plan_revision',
                symbol='worldmock.life.environment_layer::environment_layer',
                original='"duty": _duty(context)',
                replacement='"duty": None if (context.state.agents[context.agent_id].day_plan\n'
                            '                              and '
                            'context.state.agents[context.agent_id].day_plan.goals) else _duty(context)',
                tests=('tests/life/test_work_schedule.py::test_revising_the_day_plan_does_not_change_duty',)),
 SourceMutation(name='duty_vanishes_after_topic_completion',
                symbol='worldmock.life.environment_layer::environment_layer',
                original='"duty": _duty(context)',
                replacement='"duty": None if any(item.kind.value == "action" and item.status.value == "completed"\n'
                            '                              and item.data.get("category") == "work_study"\n'
                            '                              for item in context.state.activities.values()) '
                            'else _duty(context)',
                tests=('tests/life/test_work_schedule.py::test_duty_stays_on_duty_after_topic_completion',)),
 SourceMutation(name='plan_input_carries_duty_status',
                symbol='worldmock.life.work_schedule::day_arrangement',
                original='"breaks": [_span_text(begin, finish) for begin, finish in shift.breaks]}',
                replacement='"breaks": [_span_text(begin, finish) for begin, finish in shift.breaks], "status": '
                            'STATUS_ON_DUTY}',
                tests=('tests/life/test_day_planning.py::test_plan_input_carries_the_plan_dates_duty_arrangement_without_status',)),
 SourceMutation(name='query_compaction_drops_false_and_zero',
                symbol='worldmock.life.tool_groups::compact',
                original='if item is not None and item != [] and item != {}',
                replacement='if item and item != [] and item != {}',
                tests=('tests/life/test_tool_interaction_runtime.py::'
                       'test_compact_layer_preserves_false_zero_current_action_and_control_tools',)),
 SourceMutation(name='query_app_filter_ignored',
                symbol='worldmock.life.world_query::_matching_entries',
                original='and (request.app is None or any(action["app"] == request.app for action in '
                         'entry["match"]["actions"]))',
                replacement='and True',
                tests=('tests/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',)),
 SourceMutation(name='query_environment_branch_writes_world',
                symbol='custom.envs.life_env::LifeEnv.read_query',
                original='            return error.payload() if error else query_world(observed, request)\n',
                replacement='            account = engine.context.state.agents[context.agent_id].balance_account_id\n'
                            '            engine.context.state.accounts[account] += 1\n'
                            '            return error.payload() if error else query_world(observed, request)\n',
                tests=('tests/life/test_tool_interaction_runtime.py::'
                       'test_read_queries_keep_actual_model_exchange_without_progress_events',)),
 SourceMutation(name='query_new_keys_get_defaults',
                symbol='worldmock.life.world_query::QueryWorldRequest',
                original='    app: str | None\n    available_only: bool\n',
                replacement='    app: str | None = None\n    available_only: bool = False\n',
                tests=('tests/life/test_world_query.py::test_every_query_field_is_required',)),
 SourceMutation(name='query_unknown_app_returns_empty_page',
                symbol='worldmock.life.world_query::_filter_issues',
                original='if app is not None and app not in _app_names(observation):',
                replacement='if False:',
                tests=('tests/life/test_world_query.py::test_unknown_app_and_store_names_have_the_exact_new_error_contract',)),
 SourceMutation(name='available_filter_applied_after_paging',
                symbol='worldmock.life.world_query::query_world',
                original='    entries = _matching_entries(observation, request)\n'
                         '    end = min(request.offset + observation["world_query_page_size"], len(entries))\n'
                         '    return {"matches": [compact(entry["match"]) for entry in '
                         'entries[request.offset:end]],',
                replacement='    entries = _matching_entries(observation, '
                            'request.model_copy(update={"available_only": False}))\n'
                            '    end = min(request.offset + observation["world_query_page_size"], len(entries))\n'
                            '    page = [entry for entry in entries[request.offset:end]\n'
                            '            if not request.available_only or _executable(entry["match"], '
                            '_query_ways(request.way))]\n'
                            '    return {"matches": [compact(entry["match"]) for entry in page],',
                tests=('tests/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way',)),
 SourceMutation(name='available_judged_on_unmatched_way_actions',
                symbol='worldmock.life.world_query::_executable',
                original='if (ways is None or action["way"] in ways) and (app is None or action["app"] == app)',
                replacement='if True',
                tests=('tests/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way',)),
 SourceMutation(name='available_without_way_ignores_tools',
                symbol='worldmock.life.world_query::_executable',
                original='    return any(row["available"] for row in (*match["actions"], *match["tools"]))\n',
                replacement='    return any(row["available"] for row in match["actions"])\n',
                tests=('tests/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way',)),
 SourceMutation(name='query_total_count_before_filtering',
                symbol='worldmock.life.world_query::query_world',
                original='"total_count": len(entries),',
                replacement='"total_count": len(observation["world_query_bindings"]),',
                tests=('tests/life/test_world_query.py::'
                       'test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',)),
 SourceMutation(name='query_app_candidates_include_places',
                symbol='worldmock.life.world_query::_app_issue',
                original='chosen = close if close else names[:MAX_CANDIDATES]',
                replacement='chosen = [entry["match"]["name"] for entry in observation["world_query_bindings"]\n'
                            '              if entry["match"]["kind"] == "location"][:MAX_CANDIDATES]',
                tests=('tests/life/test_world_query.py::test_unknown_app_and_store_names_have_the_exact_new_error_contract',)))

#: 规格「源码变异必须覆盖」一段逐句（按原文顺序照抄）→ 所属阶段与杀死它的登记变异名；已完成阶段的每一句至少一个变异。
SPEC_MUTATIONS: dict[str, dict[str, object]] = {'反思接受自由文本': {'stage': '1', 'mutations': ('reflection_accepts_free_text', 'reflection_accepts_another_tool')},
 '跳过未知引用校验': {'stage': '1', 'mutations': ('reflection_skips_unknown_reference_check',)},
 '失败返回空列表': {'stage': '1', 'mutations': ('reflection_failure_returns_an_empty_list',)},
 '重试不设上限': {'stage': '1', 'mutations': ('reflection_retries_without_a_bound',)},
 '运行装配改回 AS2 原类': {'stage': '1',
                   'mutations': ('assembly_proxy_reverts_to_the_as2_class',
                                 'assembly_service_proxy_reverts_to_the_as2_class',
                                 'assembly_actor_reverts_to_the_as2_class')},
 '代理不等待远端结果': {'stage': '1', 'mutations': ('proxy_returns_without_awaiting_the_remote_result',)},
 '重复意图只报笼统错误': {'stage': '1', 'mutations': ('plan_duplicates_report_only_a_generic_error',)},
 '日志后吞掉异常': {'stage': '1', 'mutations': ('decision_failure_swallowed_after_log',)},
 '零点清空事务': {'stage': '2', 'mutations': ('midnight_clears_errands',)},
 '修订计划丢事务': {'stage': '2', 'mutations': ('plan_revision_drops_errands',)},
 '待办层不投影事务': {'stage': '2', 'mutations': ('todo_layer_omits_errands',)},
 '记忆层超窗或上下文浓缩时丢掉待办层事务': {'stage': '2',
                         'mutations': ('condensation_drops_todo_errands', 'memory_overflow_drops_todo_errands')},
 '业务拒绝时事务变更照落': {'stage': '2', 'mutations': ('errand_changes_applied_before_rejection',)},
 '事务名近似时取第一个候选': {'stage': '2', 'mutations': ('errand_name_takes_first_candidate',)},
 '请求可把状态置为完成': {'stage': '2', 'mutations': ('request_can_mark_errand_completed',)},
 '把受理、未送达订单、活动开始、`progressed` 或自述当完成': {'stage': '2',
                                        'mutations': ('accepted_event_counts_as_evidence',
                                                      'pending_order_counts_as_obtained',
                                                      'progressed_goal_counts_as_completion',
                                                      'self_report_counts_as_completion')},
 '把同一提交内排在新增事件之前的收货证据计入': {'stage': '2', 'mutations': ('evidence_before_noting_event_counts',)},
 '按付款人字段而非收货人判归属': {'stage': '2', 'mutations': ('ownership_follows_event_agent',)},
 '一次提交结算两次': {'stage': '2', 'mutations': ('round_commit_settles_twice', 'advance_commit_settles_twice')},
 '检查点漏存事务': {'stage': '2', 'mutations': ('checkpoint_omits_errands',)},
 '取消后仍提醒': {'stage': '2', 'mutations': ('cancelled_errand_stays_armed',)},
 '提醒即置完成': {'stage': '2', 'mutations': ('reminder_completes_errand',)},
 '提醒叫醒睡眠': {'stage': '2', 'mutations': ('errand_reminder_wakes_sleep',)},
 '事务计入在手承诺': {'stage': '2', 'mutations': ('open_errands_are_commitments',)},
 '新唤醒原因漏登足迹词表': {'stage': '2', 'mutations': ('errand_wake_reason_missing_from_vocabulary',)},
 '提醒与办成事件写成系统可见': {'stage': '2', 'mutations': ('errand_agent_events_written_as_system',)},
 '结算事件沿用证据事件的决策身份': {'stage': '2', 'mutations': ('settlement_event_inherits_evidence_identity',)},
 '事务文字写进进度层参数或 `self_notes`': {'stage': '2',
                               'mutations': ('progress_arguments_carry_errands', 'self_notes_carry_errand_text')},
 '进度层请求参数带出个人事务': {'stage': '2', 'mutations': ('control_projection_carries_errands',)},
 '事务指标以目标数为分母': {'stage': '2', 'mutations': ('errand_metrics_use_goal_denominator',)},
 '版本 8 检查点被放行': {'stage': '2', 'mutations': ('version_8_world_state_accepted',)},
 '抽取或人设层投影丢掉故事写明的下班、到家锚点': {'stage': '3',
                            'mutations': ('extraction_drops_stated_work_anchor',
                                          'extraction_drops_stated_arrive_home',
                                          'persona_layer_drops_work_anchor',
                                          'persona_layer_drops_arrive_home')},
 '抽取对下班锚点取缺省': {'stage': '3', 'mutations': ('leave_work_takes_a_default',)},
 '新锚点并入回绕归一': {'stage': '3', 'mutations': ('work_anchors_join_wraparound',)},
 '乱序时整卡失败': {'stage': '3', 'mutations': ('out_of_order_work_anchor_fails_the_card',)},
 '场景声明的下班、到家窗口不进零点计划输入': {'stage': '3', 'mutations': ('scenario_work_windows_missing_from_plan_input',)},
 '按星期推断休息日': {'stage': '3', 'mutations': ('rest_day_inferred_from_weekday',)},
 '所有人物套班次': {'stage': '3', 'mutations': ('everyone_gets_a_shift',)},
 '计划修订后 `duty` 消失': {'stage': '3', 'mutations': ('duty_vanishes_after_plan_revision',)},
 '休息时段显示为当班中': {'stage': '3', 'mutations': ('break_shown_as_on_duty',)},
 '忽略例外日期': {'stage': '3', 'mutations': ('exception_dates_ignored',)},
 '宿主在人设层重复渲染工作安排': {'stage': '3', 'mutations': ('host_renders_work_schedule',)},
 '忽略应用筛选': {'stage': '4', 'mutations': ('query_app_filter_ignored',)},
 '可执行筛选放到切页之后或在未命中方式的动作上判定': {'stage': '4',
                              'mutations': ('available_filter_applied_after_paging',
                                            'available_judged_on_unmatched_way_actions')},
 '精简时按真假值丢掉 false 或零': {'stage': '4', 'mutations': ('query_compaction_drops_false_and_zero',)},
 '查询写世界': {'stage': '4', 'mutations': ('query_environment_branch_writes_world',)},
 '四键旧请求被受理': {'stage': '4', 'mutations': ('query_new_keys_get_defaults',)}}

#: 不对应规格「源码变异必须覆盖」一段任何一句的登记变异：变异名 → 出处（实施计划各阶段的「突变审查的目标」，或阶段复审
#: 按规格正文条款补登的变异）。
EXTRA_MUTATIONS: dict[str, str] = {'tool_dispatch_failure_loses_stage_code': '实施计划阶段 1「突变审查的目标」故障传播：工具分发失败不再按失败阶段码 TOOL_SERVICE_FAILED '
                                           '抛出（改为把原异常直接上抛）。',
 'plan_duplicates_locate_only_later_entries': '实施计划阶段 1「突变审查的目标」计划重复意图：只定位后一条（重复意图的首次出现不再报告）。',
 'plan_over_capacity_returns_before_duplicates': '实施计划阶段 1「突变审查的目标」计划重复意图：超限提前返回而不与重复意图同批报告。',
 'actor_answers_without_the_environment': '实施计划阶段 1「突变审查的目标」只读转发：actor 不把查询交给环境（只能由主进程内直接调用 actor 的用例杀死）。',
 'actor_class_allows_concurrent_calls': '实施计划阶段 1「突变审查的目标」只读转发：max_concurrency=1 改为 2（杀手为捕获 ray.remote 参数的用例）。',
 'reflection_count_bound_widened': '实施计划阶段 1「突变审查的目标」严格反思：条数上限放宽（六条洞见被接受）。',
 'reflection_text_validation_skipped': '实施计划阶段 1「突变审查的目标」严格反思：正文文字校验不执行（超过一千字符或含条目身份的洞见被接受）。',
 'reflection_retry_bound_zero': '实施计划阶段 1「突变审查的目标」严格反思：重试上限改为 0（第一次不合合同即运行故障）。',
 'reflection_correction_accumulates_messages': '实施计划阶段 1「突变审查的目标」严格反思：附回消息逐次累积而不基于原始消息。',
 'items_without_errands_accepted': '实施计划阶段 2「突变审查的目标」：带 items 缺 `errands` 的请求被放行（control_items 去掉请求级 errands '
                                   '的必有检查）。',
 'duplicates_judged_before_cancels': '实施计划阶段 2「突变审查的目标」：同一请求不先扣除取消就判重名与上限（新增与扣除本请求取消之前的全部未完成事务比较）。',
 'condensation_drops_completion_line': '实施计划阶段 2「突变审查的目标」：浓缩后丢掉 `errand_completed` 的句子（record_lines 对办成记录返回空）。',
 'sleeper_reminder_time_is_a_boundary': '实施计划阶段 2「突变审查的目标」：睡眠人物的提醒时刻进入边界候选（boundary_times 去掉睡眠过滤）。',
 'reminder_recorded_without_delivery': '实施计划阶段 2「突变审查的目标」：提醒在睡眠中记账（不看唤醒判定，给每个人物交付到点的事务提醒）。',
 'simultaneous_reminders_write_one_event': '实施计划阶段 2「突变审查的目标」：同刻多条只写一条提醒事件（deliver_reminders 只留第一条草稿）。',
 'system_layer_copy_diverges': '实施计划阶段 2「突变审查的目标」：系统层两份手写副本不一致（life_prompt.py 第 6 条改掉一个分句，接口合同「请求组成」的登记不跟随）。',
 'control_projection_trims_first_item_only': '实施计划阶段 2「突变审查的目标」：进度层只过滤首项（control_request 让第二项起带出 views）。',
 'errand_named_twice_accepted': '规格 2.2「同一条未完成事务在一次请求里至多被一条更新或取消点名」与 2.3 表第 1 行（阶段 2 复审补登）：同一事务被第二条更新或取消点名时不再判出。',
 'added_twins_with_one_product_accepted': '规格 2.3 表第 4 行「结果种类与商品都与…同请求另一条新增相同」（阶段 2 '
                                          '复审补登）：同一请求两条新增的结果种类与商品相同时不再判出。',
 'override_skips_null_card_slot': '实施计划阶段 3「突变审查的目标」：覆盖函数对 null 槽跳过场景值（卡片下班、到家槽为 null 时场景声明的窗口不再落下）。',
 'calendar_cross_check_skipped': '实施计划阶段 3「突变审查的目标」：加载时不做日历交叉核对（check_work_schedules 只查工作地点）。',
 'scenario_structure_four_admitted': '实施计划阶段 3「突变审查的目标」：场景结构 4 被放行（瘦身场景文档的 schema_version 接受 4）。',
 'plan_input_carries_duty_status': '实施计划阶段 3「突变审查的目标」：零点计划输入带上 `status`（day_arrangement 的当班日形状多出 status 键）。',
 'duty_vanishes_after_topic_completion': '实施计划阶段 3「突变审查的目标」：主题完成之后 `duty` 变 null（环境层在有任务付款结清后把 duty 投影成 '
                                         'null；同句的计划修订一半对应规格「计划修订后 `duty` 消失」）。',
 'query_unknown_app_returns_empty_page': '当前查询的未知应用必须报告 app 参数错误，不能返回合法空页。',
 'available_without_way_ignores_tools': '实施计划阶段 4「突变审查的目标」「`way` 为 null 时不看工具的 '
                                        '`available`」：未写方式时只看动作，以该目标为参数的工具不再计入可执行。',
 'query_total_count_before_filtering': '实施计划阶段 4「突变审查的目标」「`total_count` 取筛选前总数」：total_count '
                                       '改取全部冻结条目数，而不是全部筛选交集后的完整匹配数。',
 'query_app_candidates_include_places': '当前查询的 app 纠错候选只能来自登记应用，不能出现地点。'}

#: 本特性新增或改动的生产文件（延期标记与空壳函数的扫描范围），随各提交增长；阶段 1 的清单取自实施计划「台账形态」行。
FEATURE_PRODUCTION_FILES: tuple[str, ...] = (
    "custom/agents/life_agent.py",
    "src/worldmock/agents/life_continuation.py",
    "src/worldmock/agents/life_memory.py",
    "src/worldmock/agents/life_persona.py",
    "src/worldmock/agents/life_prompt.py",
    "src/worldmock/agents/life_reflection.py",
    "scripts/inspect_run.py",
    "src/worldmock/evaluation/behavior_metrics.py",
    "src/worldmock/evaluation/errand_metrics.py",
    "src/worldmock/evaluation/workload_metrics.py",
    "src/worldmock/footprints/models.py",
    "src/worldmock/footprints/quality.py",
    "src/worldmock/life/activity_control.py",
    "src/worldmock/life/callable_index.py",
    "src/worldmock/life/control_items.py",
    "src/worldmock/life/day_plan_inputs.py",
    "src/worldmock/life/day_planning.py",
    "src/worldmock/life/engine.py",
    "src/worldmock/life/environment_layer.py",
    "src/worldmock/life/errand_state.py",
    "src/worldmock/life/errands.py",
    "src/worldmock/life/load_checks.py",
    "src/worldmock/life/models.py",
    "src/worldmock/life/name_resolution.py",
    "src/worldmock/life/narration.py",
    "src/worldmock/life/observe.py",
    "src/worldmock/life/progress_layer.py",
    "src/worldmock/life/prompt_schemas.py",
    "src/worldmock/life/purchase_offers.py",
    "src/worldmock/life/read_query_runtime.py",
    "src/worldmock/life/reminders.py",
    "src/worldmock/life/result_projection.py",
    "src/worldmock/life/rhythm.py",
    "src/worldmock/life/scenario.py",
    "src/worldmock/life/schedule.py",
    "src/worldmock/life/semantics.py",
    "src/worldmock/life/session_actions.py",
    "src/worldmock/life/settled_text.py",
    "src/worldmock/life/todo_layer.py",
    "src/worldmock/life/tool_groups.py",
    "src/worldmock/life/work_schedule.py",
    "src/worldmock/life/world_query.py",
    "src/worldmock/profile/compile.py",
    "src/worldmock/profile/extractor.py",
    "src/worldmock/profile/injection.py",
    "src/worldmock/profile/models.py",
    "src/worldmock/profile/textgen.py",
    "src/worldmock/runtime.py",
    "tools/run-viewer-context.js",
    "tools/run-viewer.html",
)

#: 本特性新增的测试文件，连同登记表、守卫与共用变异执行器自身；登记了节点的既有测试文件由守卫另行纳入同一扫描。
FEATURE_TEST_FILES: tuple[str, ...] = (
    "tests/errands_workday_acceptance.py",
    "tests/evaluation/test_errand_metrics.py",
    "tests/evaluation/test_workload_metrics.py",
    "tests/footprints/test_errand_export.py",
    "tests/integration/test_errand_checkpoint.py",
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
    "tests/life/test_food_errand_evidence.py",
    "tests/life/test_errand_todo_layer.py",
    "tests/life/test_plan_day_duplicate_feedback.py",
    "tests/life/test_query_cost_report.py",
    "tests/life/test_revise_day_plan_json.py",
    "tests/life/test_work_schedule.py",
    "tests/life/test_workload_report.py",
    "tests/life/test_world_query_filters.py",
    "tests/life/work_schedule_fixtures.py",
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
