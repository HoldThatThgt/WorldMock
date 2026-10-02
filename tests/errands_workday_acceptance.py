"""个人事务与工作安排规格的验收登记表（完整性守卫的数据源）。

@file     errands_workday_acceptance.py
@brief    把 docs/dev/SPEC-errands-workday.md「验收台账」的每一行登记到所属阶段与证据测试节点（LEDGER），登记台账之后
          「源码变异必须覆盖」一段的每一句到杀死它的源码变异（SPEC_MUTATIONS、MUTATIONS），并冻结本特性新增或改动的生产
          文件与新增测试文件（FEATURE_PRODUCTION_FILES、FEATURE_TEST_FILES），供延期标记与空壳函数扫描。判定由
          tests/test_errands_workday_acceptance.py 执行；变异经共用执行器 tests/mutation_runner.py 在隔离子进程里生效。
@note     分阶段形态：`stage` 取规格台账的阶段列；只有 `stage` 属于 STAGES_DONE 的行必须列出测试，但任何行列出的节点都
          必须解析到存在、会被收集且未被跳过的测试函数。阶段随实施增长：每个阶段的提交登记自己的行、变异与文件，阶段的
          最后一个提交把阶段号加入 STAGES_DONE；阶段 4 只依赖阶段 1，可以先于阶段 2、3 完成。
          登记的节点是经真实请求、引擎与 SQLite 路径的行为测试；人物一侧的行经真实记忆运行时或直接导入的 LifeAgent。
          只断言辅助函数的用例至多作补充证据，不单独支撑一行。
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

#: 已完成的阶段；每个阶段的最后一个提交把自己的编号加入。
STAGES_DONE: frozenset[str] = frozenset()

_PROTOCOL = "tests/integration/test_life_agent_protocol.py::"
_FAILURE_STAGES = "tests/integration/test_host_failure_stages.py::"

#: 「故障传播」：决策失败与工具分发失败各有英文日志，异常对象、类型与失败阶段码不变。头两个节点用直接导入的 LifeAgent
#: （step 的三个故障位置、工具分发失败），第三个是 Society 路径上的行为证据（中止、不结算、日志一条）；其后两个守住模型
#: 服务失败（记进该次模型证据）与证据写入失败（运行故障）两个失败阶段码，末一个守住记忆查询失败作为失败的查询结果回给
#: 模型。四个失败阶段码都只标出失败阶段与调用位置。
_FAULT_PROPAGATION_TESTS = [
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
#: LifeEnvActor 与带 read_query 的 LifeEnvProxy）；第二个是真实 Ray：驱动侧与 worker 侧经代理各调用一次，回看任务返回结果
#: 字典而非对象引用，重新打开运行目录后世界状态逐字节不变、只多出 actor 一侧那一次观察；第三个在主进程内直接构造未经
#: ray.remote 包装的 LifeEnvActor，两个工具的结果等于环境直调，业务拒绝是返回值，类型不符与找不到唯一观察是协议违约；
#: 第四个是 query_world 两入口（宿主本地求值的纯函数与环境的 read_query）受理时逐值相等、拒绝时同一份校验报告，它同时
#: 登记在工具交互台账并由那边的变异守着；末一个不启动 Ray，守并发度为 1 且不提供参数。
_READ_QUERY_TESTS = [
    _READ_QUERY + "test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query",
    _READ_QUERY + "test_task_recall_crosses_real_ray_without_world_mutation",
    _READ_QUERY + "test_unwrapped_actor_read_query_equals_direct_environment_call",
    _WORLD_QUERY + "test_environment_read_query_keeps_world_clock_round_and_store_unchanged",
    _READ_QUERY + "test_life_env_actor_class_fixes_single_concurrency_without_parameters",
]

#: 验收台账行（首列原文）→ 所属阶段与证据测试节点；未完成阶段的行随该阶段的提交补节点。
LEDGER: dict[str, dict[str, object]] = {
    "严格反思": {"stage": "1", "tests": []},
    "只读查询转发": {"stage": "1", "tests": _READ_QUERY_TESTS},
    "计划重复意图": {"stage": "1", "tests": _PLAN_DUPLICATE_TESTS},
    "故障传播": {"stage": "1", "tests": _FAULT_PROPAGATION_TESTS},
    "事务变更与原子受理": {"stage": "2", "tests": []},
    "事务名称与错误": {"stage": "2", "tests": []},
    "跨夜可见": {"stage": "2", "tests": []},
    "完成依据": {"stage": "2", "tests": []},
    "结算幂等": {"stage": "2", "tests": []},
    "事务提醒": {"stage": "2", "tests": []},
    "检查点": {"stage": "2", "tests": []},
    "提示与唯一出现": {"stage": "2", "tests": []},
    "交付边界": {"stage": "2", "tests": []},
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
#: 只读查询转发五个变异的杀手，全部在主进程执行被变异的代码：装配用例（两个「改回 AS2 原类」）、真实 Ray 用例的驱动侧
#: 调用（代理不等待；代理在驱动进程里执行，worker 里的 actor 从磁盘导入原文、不受变异影响）、主进程内直调 actor 的用例
#: （actor 不把查询交给环境；真实 Ray 下 actor 在 worker 里，杀不到）与捕获 ray.remote 参数的用例（并发度）。
_ASSEMBLY_KILLERS = (_READ_QUERY + "test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query",)
_PROXY_KILLERS = (_READ_QUERY + "test_task_recall_crosses_real_ray_without_world_mutation",)
_ACTOR_KILLERS = (_READ_QUERY + "test_unwrapped_actor_read_query_equals_direct_environment_call",)
_CONCURRENCY_KILLERS = (_READ_QUERY + "test_life_env_actor_class_fixes_single_concurrency_without_parameters",)

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
        (_PROTOCOL + "test_decision_failure_is_logged_once_and_reraised_unchanged",),
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
        (_FAILURE_STAGES + "test_tool_service_failure_points_to_real_call_without_claiming_execution",),
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
)

#: 规格「源码变异必须覆盖」一段逐句（按原文顺序照抄）→ 所属阶段与杀死它的登记变异名；已完成阶段的每一句至少一个变异。
SPEC_MUTATIONS: dict[str, dict[str, object]] = {
    "反思接受自由文本": {"stage": "1", "mutations": ()},
    "跳过未知引用校验": {"stage": "1", "mutations": ()},
    "失败返回空列表": {"stage": "1", "mutations": ()},
    "重试不设上限": {"stage": "1", "mutations": ()},
    "运行装配改回 AS2 原类": {"stage": "1", "mutations": ("assembly_proxy_reverts_to_the_as2_class",
                                                    "assembly_actor_reverts_to_the_as2_class")},
    "代理不等待远端结果": {"stage": "1", "mutations": ("proxy_returns_without_awaiting_the_remote_result",)},
    "重复意图只报笼统错误": {"stage": "1", "mutations": ("plan_duplicates_report_only_a_generic_error",)},
    "日志后吞掉异常": {"stage": "1", "mutations": ("decision_failure_swallowed_after_log",)},
    "零点清空事务": {"stage": "2", "mutations": ()},
    "修订计划丢事务": {"stage": "2", "mutations": ()},
    "待办层不投影事务": {"stage": "2", "mutations": ()},
    "记忆层超窗或上下文浓缩时丢掉待办层事务": {"stage": "2", "mutations": ()},
    "业务拒绝时事务变更照落": {"stage": "2", "mutations": ()},
    "事务名近似时取第一个候选": {"stage": "2", "mutations": ()},
    "请求可把状态置为完成": {"stage": "2", "mutations": ()},
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
    "版本 8 检查点被放行": {"stage": "2", "mutations": ()},
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
}

#: 本特性新增或改动的生产文件（延期标记与空壳函数的扫描范围），随各提交增长；实施计划「台账形态」行给出阶段 1 的清单，
#: 其中新增的 custom/agents/life_reflection.py 由严格反思的提交加入。
FEATURE_PRODUCTION_FILES: tuple[str, ...] = (
    "custom/agents/life_agent.py",
    "custom/agents/life_memory.py",
    "src/worldmock/delivery/windows.py",
    "src/worldmock/life/models.py",
    "src/worldmock/life/read_query_runtime.py",
    "src/worldmock/runtime.py",
    "tools/run-viewer-context.js",
)

#: 本特性新增的测试文件，连同登记表、守卫与共用变异执行器自身；登记了节点的既有测试文件由守卫另行纳入同一扫描。
FEATURE_TEST_FILES: tuple[str, ...] = (
    "tests/errands_workday_acceptance.py",
    "tests/integration/test_read_query_runtime.py",
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
