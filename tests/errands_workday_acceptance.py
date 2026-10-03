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
          SPEC_MUTATIONS 的键按规格原文的顺序逐句照抄，守卫核对它们首尾相接恰好拼回规格那一段；不对应规格任何一句、
          取自实施计划「突变审查的目标」的变异登记在 EXTRA_MUTATIONS 并写明出处。
          「复跑指标（只报告）」按规格只报告、不作门禁：配对全天复跑是计费的真实运行，只在用户要求时启动。
"""

from __future__ import annotations

from pathlib import Path

from tests import mutation_runner
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, SourceMutation

#: 规格「实施计划」中的全部阶段编号（冻结之外的 0–7）。
STAGE_IDS = frozenset(str(number) for number in range(8))

#: 已完成的阶段；每个阶段的最后一个提交把自己的编号加入。阶段 1（运行修复入库）随严格反思的提交完成。
STAGES_DONE: frozenset[str] = frozenset({"1"})

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
#: 通过；末一个守交付入口：版本 8 的导出在构造窗口与运行 LabelKit 之前按版本拒绝，版本 9 的导出到达窗口构造。共同
#: 检查点恢复一项随阶段 2 的检查点切片补登。
_CHECKPOINT_TESTS = [
    _SESSION_STATE + "test_run_with_version_8_world_state_is_rejected_on_open",
    _SESSION_STATE + "test_world_state_schema_version_8_is_rejected",
    _PUBLICATION + "test_delivery_gate_rejects_version_eight_exports",
]

_TOOL_TEXTS = "tests/integration/test_tool_description_references.py::"

#: 「提示与唯一出现」：「内部身份与新绑定键不进任何层」一项的前一半——经真实引擎预置未完成事务后 engine.observe，观察带
#: 两类私有事务绑定并存进 SQLite 的观察事件，五层照常渲染且不含事务身份与商品身份，任何一层带上 errand_bindings、
#: errand_item_bindings 或 errand_id 即渲染失败；按事务名回看以 TASK_NOT_FOUND 拒绝。「工具用途句与字段说明与接口合同逐字
#: 且通过用途扫描」一项：三个控制工具的用途句与十一处参数说明与接口合同定稿表逐字相同，全部原生工具的用途句与参数说明
#: 通过只写用途的扫描。其余各项随阶段 2 的后续切片补登。
_PROMPT_PRESENCE_TESTS = [
    _ERRAND_TODO + "test_observation_carries_private_errand_bindings_outside_every_layer",
    _TOOL_TEXTS + "test_control_tool_purposes_and_errand_descriptions_are_the_contract_text",
    _TOOL_TEXTS + "test_every_native_tool_has_a_purpose_only_description",
    _TOOL_TEXTS + "test_parameter_descriptions_are_purpose_only",
]

_ERRAND_REQUESTS = "tests/life/test_errand_requests.py::"

#: 「事务变更与原子受理」：前六个经真实请求、引擎与 SQLite——三个控制工具带请求级 errands 受理，每条被受理的变更恰好一条
#: 系统可见、不带决策与动作身份的事务事件，与动作结果同一提交，回执按请求顺序（内容没有变化的更新也写事件，点名暂停的
#: 活动播报照样恢复它）；空数组合法且回执为空数组；每种结构错误在环境 _dispatch 以完整 errands[j].<键> 路径拒绝、与宿主的
#: 参数报告逐项相同；收尾在事务复核之后被业务拒绝时事务变更一并不生效、没有事务事件；请求里无法写「完成」；重放不再记下。
#: 末一个守三个控制工具的 errands 是内联、封闭、八键全部必填且按序的变更对象数组，通过 DeepSeek strict 校验。
#: 「被业务拒绝的收尾：记忆入库而事务未记下」的人物一侧（真实 LifeAgent → LifeEnv）随宿主切片补登。
_ERRAND_CHANGE_TESTS = [
    _ERRAND_REQUESTS + "test_each_control_tool_records_errand_changes_with_one_event_per_change_and_an_ordered_receipt",
    _ERRAND_REQUESTS + "test_empty_errands_is_valid_and_the_receipt_is_an_empty_list",
    _ERRAND_REQUESTS + "test_every_structural_error_reports_the_full_errand_path",
    _ERRAND_REQUESTS + "test_a_rejected_request_registers_no_errand_and_writes_no_errand_event",
    _ERRAND_REQUESTS + "test_a_request_cannot_mark_an_errand_completed",
    _ERRAND_REQUESTS + "test_a_replayed_request_does_not_register_twice",
    "tests/integration/test_activity_schema.py::test_control_tool_schemas_inline_the_errand_change_object",
]

_CONTROL_ITEMS = "tests/life/test_control_items.py::"

#: 「交付边界」中「缺 errands 的旧控制请求被拒」一项：带 items 而缺请求级 errands 的请求经唯一读取入口 control_items、
#: 进度层与交付窗口的投影 control_request、请求级变更的读取 control_errands 与收尾记忆判定一律以完整性故障拒绝；交付构造
#: 窗口时同样拒绝、不当作没有事务变更放行；control_errands 按请求顺序读出每条变更的八键。其余各项随交付边界切片补登。
_DELIVERY_BOUNDARY_TESTS = [
    _CONTROL_ITEMS + "test_a_current_request_without_errands_is_rejected",
    "tests/delivery/test_windows.py::test_control_requests_without_errands_are_rejected_by_delivery",
    _CONTROL_ITEMS + "test_control_errands_reads_every_change_in_order",
]

#: 验收台账行（首列原文）→ 所属阶段与证据测试节点；未完成阶段的行随该阶段的提交补节点。
LEDGER: dict[str, dict[str, object]] = {
    "严格反思": {"stage": "1", "tests": _REFLECTION_TESTS},
    "只读查询转发": {"stage": "1", "tests": _READ_QUERY_TESTS},
    "计划重复意图": {"stage": "1", "tests": _PLAN_DUPLICATE_TESTS},
    "故障传播": {"stage": "1", "tests": _FAULT_PROPAGATION_TESTS},
    "事务变更与原子受理": {"stage": "2", "tests": _ERRAND_CHANGE_TESTS},
    "事务名称与错误": {"stage": "2", "tests": []},
    "跨夜可见": {"stage": "2", "tests": []},
    "完成依据": {"stage": "2", "tests": []},
    "结算幂等": {"stage": "2", "tests": []},
    "事务提醒": {"stage": "2", "tests": []},
    "检查点": {"stage": "2", "tests": _CHECKPOINT_TESTS},
    "提示与唯一出现": {"stage": "2", "tests": _PROMPT_PRESENCE_TESTS},
    "交付边界": {"stage": "2", "tests": _DELIVERY_BOUNDARY_TESTS},
    "事务与收尾记忆指标": {"stage": "2", "tests": []},
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
    "零点清空事务": {"stage": "2", "mutations": ()},
    "修订计划丢事务": {"stage": "2", "mutations": ()},
    "待办层不投影事务": {"stage": "2", "mutations": ("todo_layer_omits_errands",)},
    "记忆层超窗或上下文浓缩时丢掉待办层事务": {"stage": "2", "mutations": ()},
    "业务拒绝时事务变更照落": {"stage": "2", "mutations": ("errand_changes_applied_before_rejection",)},
    "事务名近似时取第一个候选": {"stage": "2", "mutations": ()},
    "请求可把状态置为完成": {"stage": "2", "mutations": ("request_can_mark_errand_completed",)},
    "把受理、未送达订单、活动开始、`progressed` 或自述当完成": {"stage": "2", "mutations": ()},
    "把记下之前的证据（含同一提交内排在新增事件之前的）或记下前开始的进度区间计入": {"stage": "2", "mutations": ()},
    "当面给予不计入取得": {"stage": "2", "mutations": ()},
    "按事件人物字段而非收货人判归属": {"stage": "2", "mutations": ()},
    "读取逐项结果里的重复订单": {"stage": "2", "mutations": ()},
    "一次提交结算两次": {"stage": "2", "mutations": ()},
    "检查点漏存事务": {"stage": "2", "mutations": ()},
    "取消后仍提醒": {"stage": "2", "mutations": ()},
    "提醒即置完成": {"stage": "2", "mutations": ()},
    "提醒叫醒睡眠": {"stage": "2", "mutations": ()},
    "事务计入在手承诺": {"stage": "2", "mutations": ()},
    "新唤醒原因漏登足迹词表": {"stage": "2", "mutations": ()},
    "提醒与办成事件写成系统可见": {"stage": "2", "mutations": ()},
    "结算事件沿用证据事件的决策身份": {"stage": "2", "mutations": ()},
    "事务文字写进进度层参数或 `self_notes`": {"stage": "2", "mutations": ()},
    "意图或 dreaming 投影带出请求级 `errands`": {"stage": "2", "mutations": ()},
    "投影原地修改原事件": {"stage": "2", "mutations": ()},
    "事务指标以目标数为分母": {"stage": "2", "mutations": ()},
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

#: 不对应规格任何一句的登记变异：变异名 → 出处（实施计划各阶段的「突变审查的目标」）。
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
    "src/worldmock/life/activity_control.py",
    "src/worldmock/life/control_items.py",
    "src/worldmock/life/errand_state.py",
    "src/worldmock/life/errands.py",
    "src/worldmock/life/models.py",
    "src/worldmock/life/narration.py",
    "src/worldmock/life/observe.py",
    "src/worldmock/life/prompt_schemas.py",
    "src/worldmock/life/read_query_runtime.py",
    "src/worldmock/life/result_projection.py",
    "src/worldmock/life/session_actions.py",
    "src/worldmock/life/todo_layer.py",
    "src/worldmock/runtime.py",
    "tools/run-viewer-context.js",
    "tools/run-viewer.html",
)

#: 本特性新增的测试文件，连同登记表、守卫与共用变异执行器自身；登记了节点的既有测试文件由守卫另行纳入同一扫描。
FEATURE_TEST_FILES: tuple[str, ...] = (
    "tests/errands_workday_acceptance.py",
    "tests/integration/test_read_query_runtime.py",
    "tests/integration/test_strict_reflection.py",
    "tests/life/test_errand_changes.py",
    "tests/life/test_errand_requests.py",
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
