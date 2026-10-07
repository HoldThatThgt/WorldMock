"""让「等待」成为例外规格的验收登记：分阶段完成集合、特性文件清单与源码变异。

@file     idle_fillers_acceptance.py
@brief    docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-验收台账 的机器登记。契约点到阶段、规格锚点、测试节点与实现符号的
          对应保存在 tests/fixtures/idle-fillers-acceptance.json；本模块保存已完成的阶段、特性文件清单与源码变异，
          判定由 tests/test_idle_fillers_acceptance.py 执行，变异经共用执行器 tests/mutation_runner.py 在隔离子进程里生效。
@note     分阶段形态：只有阶段属于 STAGES_DONE 的台账行必须列出测试与实现符号，任何行列出的节点都必须解析到存在、会被
          收集且未被跳过的测试函数。每个阶段的提交登记自己的行、变异与文件，并把阶段号加入 STAGES_DONE；阶段 6 是配对
          复跑，只报告，不登记节点。变异只登记在主进程内执行的生产代码上，`original` 在所登记的定义内恰好出现一次，
          杀手是已登记在台账某一行的行为用例。本文件不含任何延期标记字面量。
"""

from __future__ import annotations

from pathlib import Path

from tests import mutation_runner
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, SourceMutation

#: 规格「实施阶段与提交」的阶段号；阶段 0 是冻结提交，阶段 6 是只报告的配对复跑。
STAGE_IDS: frozenset[str] = frozenset({"0", "1", "2", "3", "4", "5", "6"})
#: 改代码、测试、基线与门禁的实施阶段；全部完成后由守卫判定。
IMPLEMENTATION_STAGES: frozenset[str] = frozenset({"1", "2", "3", "4", "5"})
#: 只报告的阶段：台账行不登记测试与实现符号。
REPORT_ONLY_STAGES: frozenset[str] = frozenset({"6"})
#: 已完成的阶段，随每个阶段的提交增长。
STAGES_DONE: frozenset[str] = frozenset({"1", "2", "3"})

#: 本特性新增或改动的生产文件，供延期标记与空壳函数扫描。
FEATURE_PRODUCTION_FILES: tuple[str, ...] = (
    "src/worldmock/life/duty_edges.py",
    "src/worldmock/life/schedule.py",
    "src/worldmock/life/rhythm.py",
    "src/worldmock/life/time_advance.py",
    "src/worldmock/footprints/models.py",
    "src/worldmock/evaluation/workload_metrics.py",
    "tools/run-viewer.html",
    "src/worldmock/agents/life_prompt.py",
    "src/worldmock/life/day_planning.py",
)

#: 本特性新增的测试文件与登记文件，供延期标记与空壳函数扫描。
FEATURE_TEST_FILES: tuple[str, ...] = (
    "tests/idle_fillers_acceptance.py",
    "tests/test_idle_fillers_acceptance.py",
    "tests/life/test_shift_edges.py",
    "tests/life/test_shift_edge_runs.py",
    "tests/life/test_activity_wake_facts.py",
)

#: 规格「验收台账」变异表逐行登记：行首列原文 → 阶段与各分句（以「；」分隔）登记的变异名；已完成阶段的每个分句至少一个变异。
MUTATION_ROWS: dict[str, dict] = {
    '某一个成员进入睡眠允许集合（每个成员一个变异）': {"stage": '1', "clauses": ((
        "sleep_allows_shift_start", "sleep_allows_shift_end", "sleep_allows_break_start", "sleep_allows_break_end"),)},
    '足迹侧词表缺某一个名字（每个名字一个变异）': {"stage": '1', "clauses": ((
        "vocabulary_lacks_shift_start", "vocabulary_lacks_shift_end", "vocabulary_lacks_break_start",
        "vocabulary_lacks_break_end"),)},
    '下一边界候选不排除睡着的人': {"stage": '1', "clauses": (("clock_edges_include_sleepers",),)},
    '下一次决策候选不在睡眠中剔除起止': {"stage": '1', "clauses": (("decision_edges_kept_while_asleep",),)},
    '到点判定不置标志': {"stage": '1', "clauses": (("edge_flag_never_set",),)},
    '下一次决策候选漏掉起止': {"stage": '1', "clauses": (("decision_candidates_skip_edges",),)},
    '时钟边界漏掉起止': {"stage": '1', "clauses": (("clock_boundaries_skip_edges",),)},
    '生成器不扫前一日；不扫次日': {"stage": '1', "clauses": (("generator_skips_previous_day",),
                                                            ("generator_skips_next_day",))},
    '生成器「严格晚于」改成「不早于」': {"stage": '1', "clauses": (("later_edges_include_the_query_second",),)},
    '生成器「恰在」改成「不晚于」': {"stage": '1', "clauses": (("exact_edges_include_earlier_seconds",),)},
    '生成器把班次开始与结束对调': {"stage": '1', "clauses": (("generator_swaps_shift_start_and_end",),)},
    '生成器不产出休息时段的起止': {"stage": '1', "clauses": (("generator_drops_break_edges",),)},
    '到点判定输出的起止成员不按枚举次序（同秒重合时倒序）': {"stage": '1', "clauses": (
        ("coinciding_edges_in_reverse_order",),)},
    '生成器把某一种起止记成另一成员': {"stage": '1', "clauses": (("break_end_recorded_as_break_start",),)},
    '取了别人的工作安排': {"stage": '1', "clauses": (("generator_reads_the_first_persons_schedule",),)},
    '起止像闹钟一样结束睡眠': {"stage": '1', "clauses": (("edge_ends_sleep_like_an_alarm",),)},
    '起止到点时重排或结束清醒人物进行中的活动': {"stage": '1', "clauses": (("edge_reschedules_the_awake_activity",),)},
    '第 7 条「手上的活动照常进行」改成「手上的活动随之结束」': {"stage": '2', "clauses": (
        ("rule_7_activity_ends_at_the_event",),)},
    '第 14 条的插入句移到上班安排一句之后': {"stage": '2', "clauses": (
        ("rule_14_clock_sentence_after_the_duty_sentence",),)},
    '第 16 条恢复「不必因此提早上床」': {"stage": '2', "clauses": (("rule_16_bedtime_reading_restored",),)},
    '第 15 条恢复「等平常的饭点再吃正餐」': {"stage": '2', "clauses": (("rule_15_meal_wait_reading_restored",),)},
    '模板第三条去掉「几点去睡也不设提醒」': {"stage": '3', "clauses": (("plan_template_rule_three_drops_bedtime_no_reminder",),)},
    '模板第五条恢复旧文': {"stage": '3', "clauses": (("plan_template_rule_five_restores_the_old_text",),)},
    '校验器拒绝例行意图且提醒为空的目标': {"stage": '3', "clauses": ((
        "plan_validator_rejects_routine_goals_without_reminders",
        "plan_remind_window_rejects_routine_goals_without_reminders",
    ),)},
}

#: 经真实请求、引擎与 SQLite 的行为用例文件（T1–T15、T19、T20）与生成器、纯函数用例文件（T16、T17）的节点前缀。
_RUNS = "tests/life/test_shift_edge_runs.py::"
_PURE = "tests/life/test_shift_edges.py::"
_T1 = _RUNS + "test_t1_a_waiting_person_is_asked_exactly_at_the_shift_end_and_the_wait_goes_on"
_T2 = _RUNS + "test_t2_each_edge_offers_one_decision_with_its_name_and_moves_the_duty_status"
_T3 = _RUNS + "test_t3_edges_end_no_activity_write_no_new_kind_and_keep_the_shift_in_the_environment_layer"
_T4 = _RUNS + "test_t4_a_sleeper_has_no_edge_boundaries_and_slept_edges_are_not_made_up"
_T5 = _RUNS + "test_t5_of_two_people_on_one_shift_only_the_awake_one_is_asked_and_the_sleeper_sleeps_on"
_T6 = _RUNS + "test_t6_people_without_a_schedule_get_no_edges_and_never_take_anothers"
_T8 = _RUNS + "test_t8_coinciding_edges_reminders_and_a_planned_end_are_one_opportunity_in_enum_order"
_T9 = _RUNS + "test_t9_midnight_edges_are_judged_by_the_midnight_rhythm"
_T10 = _RUNS + "test_t10_an_app_boundary_after_the_edge_does_not_hide_the_edge_from_the_clock"
_T10_APP = _PURE + "test_t10_app_content_boundary_after_shift_end_does_not_hide_the_edge"
_T12 = _RUNS + "test_t12_an_app_keeps_its_boundary_and_can_pause_at_the_shift_end"
_T12_APP = _PURE + "test_t12_app_can_pause_at_shift_edge_and_content_completion_stays_quiet"
#: T16 的「睡着时逐个成员都被过滤」与 next_decision_time 只在清醒时取起止；T17 的严格晚于与恰在断言。
_T16_ASLEEP = _PURE + "test_each_duty_edge_member_is_filtered_while_asleep"
_T16_NEXT = _PURE + "test_next_decision_time_takes_duty_edges_only_while_awake"
_T17_STRICT = _PURE + "test_strictly_later_excludes_the_query_second_and_exactly_at_includes_it"

#: C 的行为用例（规格「验收台账」行为用例表首列）→ 测试节点；引用某 T 的台账行所在阶段完成时该 T 必须有节点。
BEHAVIOUR_CASES: dict[str, tuple[str, ...]] = {
    'T1': (_T1,),
    'T2': (_T2,),
    'T3': (_T3,),
    'T4': (_T4,),
    'T5': (_T5,),
    'T6': (_T6,),
    'T7': (_RUNS + "test_t7_rest_days_have_no_edges_and_a_date_exception_sets_the_clock",),
    'T8': (_T8,),
    'T9': (_T9,),
    'T10': (_T10, _T10_APP),
    'T11': (_RUNS + "test_t11_on_the_road_the_walk_goes_on_and_pausing_is_still_refused",
            _RUNS + "test_t11_in_a_call_the_call_goes_on_and_pausing_is_still_refused",
            _RUNS + "test_t11_in_a_conversation_both_chats_go_on_and_pausing_is_still_refused"),
    'T12': (_T12, _T12_APP),
    'T13': (_RUNS + "test_t13_an_around_time_is_still_not_a_boundary",),
    'T14': (_RUNS + "test_t14_after_restore_and_a_counterfactual_branch_the_edge_fires_once",),
    'T15': (_RUNS + "test_t15_the_four_names_are_known_wake_reasons_in_the_quality_report",
            _RUNS + "test_t15_an_offline_run_with_a_shift_exports_validates_and_reports"),
    # T16 含 tests/life/test_wake_reasons.py 既有的逐项命中参数化用例（不改而照常通过）。
    'T16': (_PURE + "test_duty_edge_members_follow_errand_reminder_due_and_stay_out_of_sleep",
            _T16_ASLEEP,
            _PURE + "test_awake_snapshot_with_every_condition_lists_the_declaration_order",
            _T16_NEXT,
            "tests/life/test_wake_reasons.py::test_concrete_wake_reasons_are_preserved"),
    'T17': (_PURE + "test_midnight_edges_come_from_the_previous_and_next_local_day",
            _PURE + "test_rest_days_and_missing_schedules_have_no_edges",
            _PURE + "test_date_exceptions_override_the_weekly_shift",
            _T17_STRICT,
            _PURE + "test_dst_day_edges_match_shift_and_break_spans"),
    # T18：九处同步点的最终唤醒词表、睡眠过滤、真实主题完成和查看器 Node 行为互锁。
    'T18': ("tests/footprints/test_event_fields.py::test_wake_reason_vocabulary_is_the_frozen_spec_set",
            "tests/footprints/test_event_fields.py::test_wake_reason_vocabulary_matches_the_engine_side_enum",
            "tests/life/test_wake_reasons.py::test_frozen_wake_and_intent_names",
            "tests/life/test_wake_reasons.py::test_sleep_allowed_reasons_add_only_plan_reminder_due",
            "tests/life/test_work_topics.py::test_actual_engine_eight_hours_emit_own_completion_progress_and_remove_start_target",
            "tests/tools/test_run_viewer.py::test_run_viewer_recorded_names",
            "tests/life/test_app_push.py::test_sleep_stores_push_without_wake_and_existing_activity_is_unchanged"),
    'T19': (_RUNS + "test_t19_an_alarm_at_the_shift_start_keeps_the_edge_after_the_reminder",
            _RUNS + "test_t19_a_run_starting_on_a_shift_start_records_only_run_start"),
    'T20': (_RUNS + "test_t20_an_edge_ends_the_technical_pause_after_the_same_second_budget",),
}

_SCHEDULE = "worldmock.life.schedule"
_EDGES = "worldmock.life.duty_edges"
#: 睡眠允许集合的末行与足迹侧词表里四个新名字的那一行。
_SLEEP_LAST_LINE = "     WakeReason.PLAN_REMINDER_DUE}\n"
_VOCABULARY_LINE = '    "shift_start", "shift_end", "break_start", "break_end",\n'


def _sleep_allows(member: str) -> SourceMutation:
    """@brief 某一个成员进入睡眠允许集合：T16 的逐个成员过滤与 T5 的睡着的人静默杀死它。"""
    return SourceMutation(f"sleep_allows_{member}", f"{_SCHEDULE}::SLEEP_ALLOWED_REASONS", _SLEEP_LAST_LINE,
                          f"     WakeReason.PLAN_REMINDER_DUE, WakeReason.{member.upper()}}}\n", (_T16_ASLEEP, _T5))


def _vocabulary_lacks(member: str) -> SourceMutation:
    """@brief 足迹侧词表缺某一个名字：T2 里带这个名字的那一支在写入共同身体结算时被闭集校验拒绝。"""
    remaining = ", ".join(f'"{name}"' for name in ("shift_start", "shift_end", "break_start", "break_end")
                          if name != member)
    return SourceMutation(f"vocabulary_lacks_{member}", "worldmock.footprints.models::WAKE_REASON_NAMES",
                          _VOCABULARY_LINE, f"    {remaining},\n", (_T2,))


_LIFE_PROMPT = "worldmock.agents.life_prompt::LIFE_SYSTEM_LAYER"
_SEVEN_LAYERS = "tests/integration/test_seven_layer_request.py::"
#: 系统层三方逐字（接口合同登记、模块常量与实际请求）与第 7、14、16 条新说法的正向断言。
_SYSTEM_VERBATIM = _SEVEN_LAYERS + "test_system_layers_are_the_contract_registration_verbatim"
_IDLE_PHRASES = _SEVEN_LAYERS + "test_rules_7_14_16_say_when_decisions_come_and_that_clock_times_are_approximate"
#: 第 14 条的结尾断言：以插入的钟点一句再接上班安排一句结尾。
_RULE_14_ENDING = _SEVEN_LAYERS + "test_rule_14_makes_the_routine_the_default_and_the_day_plan_override_it"
#: 第 15、16 条旧读法的反向断言。
_RETIRED_READINGS = _SEVEN_LAYERS + "test_retired_meal_delivery_and_bedtime_readings_are_gone_from_the_system_layer"
#: 第 14 条在模块常量里独占一个源码行的插入句（规格「系统层」：整句插入句落在同一源码行内）。
_RULE_14_CLOCK = ("作息里的钟点，和今日计划目标文字里的钟点、around_time，都只是大概的时候，到时不一定会请你决策："
                  "差不多到了就做，早一点晚一点都算照常，不为凑钟点干等；和人约好的事宁早勿晚，非准点不可的要靠目标的提醒时刻。")
#: 第 14 条末尾逐字不动的上班安排一句（模块常量里紧跟插入句的两个源码行）。
_RULE_14_DUTY = ("环境层写了上班安排的日子，那是定下的安排，不是习惯：今日计划改不了它，"
                 "手上的工作交了也还在班上，写明的休息时段和工作要求去别处的外出除外。")
#: 只改插入句那一行：换成「上班安排一句再接插入句」并以条件表达式收住，其后两行原有的上班安排一句落进永不取值的
#: else 分支；第 14 条因此以插入句结尾，上班安排一句仍只出现一次，其余各条与十八条的条数不变。
_RULE_14_MOVED = f'"{_RULE_14_DUTY}{_RULE_14_CLOCK}" if True else'

_DAY_PLANNING = "tests/life/test_day_planning.py::"
#: 零点计划模板逐字用例：真实请求、接口合同与模块常量三者相等，第三、五、六条的新增断言（阶段 3）。
_PLAN_TEMPLATE_KILLERS = (
    _DAY_PLANNING + "test_plan_template_is_the_contract_text_and_asks_only_for_differences_with_around_times",
)
#: 例行意图且提醒为空的目标照常受理：新增的零点计划与修订用例，以及既有的零点计划用例（阶段 3）。
_ROUTINE_GOAL_KILLERS = (
    _DAY_PLANNING + "test_routine_goals_without_reminders_are_accepted_by_zero_hour_plan_and_revision",
    _DAY_PLANNING + "test_zero_hour_plan_items_carry_around_time_and_a_malformed_one_is_retried",
)
#: 改写前的模板第五条（变异把定稿换回它）。
_PLAN_RULE_FIVE_BEFORE = ("五、人物档案的作息里已经有的事（睡觉、起床、上下班、吃饭）不写，除非今天与平常不同或需要提醒；"
                          "写的时候写清楚哪里不同。")
#: 零点计划与修订共用的清单校验把例行意图且提醒为空的目标逐条报错；models.py 不导入 ROUTINE_INTENT_KINDS，变异内联同一集合。
_ROUTINE_GOALS_REJECTED = (
    "    errors: list[Any] = [{\"type\": \"value_error\", \"loc\": (index, \"remind_time\"), \"input\": None,\n"
    "                          \"ctx\": {\"error\": ValueError(\"routine goals require remind_time\")}}\n"
    "                         for index, goal in enumerate(goals) if goal.remind_time is None\n"
    "                         and goal.intent_kind in {\"睡眠\", \"晨间例程\", \"通勤\", \"用餐\"}]\n"
)

#: 规格「验收台账」变异表逐行落成的源码变异；变异名登记在 MUTATION_ROWS 的对应分句。
MUTATIONS: tuple[SourceMutation, ...] = (
    *(_sleep_allows(member) for member in ("shift_start", "shift_end", "break_start", "break_end")),
    *(_vocabulary_lacks(member) for member in ("shift_start", "shift_end", "break_start", "break_end")),
    SourceMutation(
        "clock_edges_include_sleepers",
        "worldmock.life.rhythm::awake_duty_edge_times",
        "if not is_asleep(state, agent_id)",
        "",
        (_T4,),
    ),
    SourceMutation(
        "decision_edges_kept_while_asleep",
        f"{_SCHEDULE}::next_decision_time",
        "    if not query.is_asleep:\n        candidates.extend(query.duty_edge_times)\n",
        "    candidates.extend(query.duty_edge_times)\n",
        (_T4, _T16_NEXT),
    ),
    SourceMutation(
        "edge_flag_never_set",
        "worldmock.life.rhythm::_wake_snapshot",
        "        duty_edges_now=duty_edges.edges_at(inputs.scenario, agent.agent_id, now),\n",
        "",
        (_T1,),
    ),
    SourceMutation(
        "decision_candidates_skip_edges",
        "worldmock.life.rhythm::next_decision_candidates",
        "            duty_edge_times=duty_edges.edge_times_after("
        "inputs.scenario, agent.agent_id, state.current_time),\n",
        "",
        (_T1, _T9),
    ),
    SourceMutation(
        "clock_boundaries_skip_edges",
        "worldmock.life.time_advance::TimeAdvanceCoordinator._next_epoch",
        "        candidates.extend(awake_duty_edge_times(self.context.state, self.context.scenario))\n",
        "",
        (_T10,),
    ),
    SourceMutation(
        "generator_skips_previous_day",
        f"{_EDGES}::duty_edges",
        "today - timedelta(days=1), ",
        "",
        (_T9,),
    ),
    SourceMutation(
        "generator_skips_next_day",
        f"{_EDGES}::duty_edges",
        ", today + timedelta(days=1)",
        "",
        (_T9,),
    ),
    SourceMutation(
        "later_edges_include_the_query_second",
        f"{_EDGES}::edge_times_after",
        "if moment > now",
        "if moment >= now",
        (_T17_STRICT, _T1),
    ),
    SourceMutation(
        "exact_edges_include_earlier_seconds",
        f"{_EDGES}::edges_at",
        "if moment == now",
        "if moment <= now",
        (_T1, _T17_STRICT),
    ),
    SourceMutation(
        "generator_swaps_shift_start_and_end",
        f"{_EDGES}::duty_edges",
        "start, end = shift_span(shift, day, zone)",
        "end, start = shift_span(shift, day, zone)",
        (_T2,),
    ),
    SourceMutation(
        "generator_drops_break_edges",
        f"{_EDGES}::duty_edges",
        "for begin, finish in break_spans(shift, day, zone):",
        "for begin, finish in ():",
        (_T2, _T8),
    ),
    SourceMutation(
        "coinciding_edges_in_reverse_order",
        f"{_SCHEDULE}::_clock_reasons",
        "for member in DUTY_EDGE_REASONS if",
        "for member in reversed(DUTY_EDGE_REASONS) if",
        (_T8,),
    ),
    SourceMutation(
        "break_end_recorded_as_break_start",
        f"{_EDGES}::duty_edges",
        "yield finish, WakeReason.BREAK_END",
        "yield finish, WakeReason.BREAK_START",
        (_T2,),
    ),
    SourceMutation(
        "generator_reads_the_first_persons_schedule",
        f"{_EDGES}::duty_edges",
        "agent_config(scenario, agent_id)",
        "agent_config(scenario, scenario.agents[0].agent_id)",
        (_T6,),
    ),
    SourceMutation(
        "edge_ends_sleep_like_an_alarm",
        "worldmock.life.time_advance::TimeAdvanceCoordinator._advance_one_activity",
        "        if reminders.reminder_due(agent, target):\n",
        (
            "        if reminders.reminder_due(agent, target) or __import__(\n"
            '                "worldmock.life.duty_edges", fromlist=["edges_at"]).edges_at(\n'
            "                    self.context.scenario, activity.agent_id, target):\n"
        ),
        (_T5,),
    ),
    SourceMutation(
        "edge_reschedules_the_awake_activity",
        "worldmock.life.rhythm::decide_engine_rhythm",
        "            if activity.next_interaction_at is not None and activity.next_interaction_at "
        "<= boundary.target:\n",
        (
            "            if set(rhythms[agent_id].wake_reasons) & {\n"
            '                    "shift_start", "shift_end", "break_start", "break_end"} or (\n'
            "                    activity.next_interaction_at is not None\n"
            "                    and activity.next_interaction_at <= boundary.target):\n"
        ),
        (_T3,),
    ),
    SourceMutation(
        "rule_7_activity_ends_at_the_event",
        _LIFE_PROMPT,
        "手上的活动照常进行",
        "手上的活动随之结束",
        (_SYSTEM_VERBATIM, _IDLE_PHRASES),
    ),
    SourceMutation(
        "rule_14_clock_sentence_after_the_duty_sentence",
        _LIFE_PROMPT,
        f'"{_RULE_14_CLOCK}"',
        _RULE_14_MOVED,
        (_RULE_14_ENDING,),
    ),
    SourceMutation(
        "rule_16_bedtime_reading_restored",
        _LIFE_PROMPT,
        "还没到就寝时间就照常过晚上",
        "不必因此提早上床",
        (_RETIRED_READINGS,),
    ),
    SourceMutation(
        "rule_15_meal_wait_reading_restored",
        _LIFE_PROMPT,
        "正餐留到平常的饭点前后吃",
        "等平常的饭点再吃正餐",
        (_RETIRED_READINGS,),
    ),
    SourceMutation(
        "plan_template_rule_three_drops_bedtime_no_reminder",
        'worldmock.life.day_planning::PLAN_DAY_PROMPT',
        "，几点去睡也不设提醒。",
        "。",
        _PLAN_TEMPLATE_KILLERS,
    ),
    SourceMutation(
        "plan_template_rule_five_restores_the_old_text",
        'worldmock.life.day_planning::PLAN_DAY_PROMPT',
        ("五、人物档案与背景里天天如此的事（睡觉、起床、上下班、吃饭、休息、每天的消遣）不写成目标；"
         "只写今天与平常不同的安排（写清楚哪里不同）、要办的事，以及照第三条要提醒的事。清单可以很短。"),
        _PLAN_RULE_FIVE_BEFORE,
        _PLAN_TEMPLATE_KILLERS,
    ),
    SourceMutation(
        "plan_validator_rejects_routine_goals_without_reminders",
        'worldmock.life.models::_validate_plan_goals',
        "    errors: list[Any] = []\n",
        _ROUTINE_GOALS_REJECTED,
        _ROUTINE_GOAL_KILLERS,
    ),
    SourceMutation(
        "plan_remind_window_rejects_routine_goals_without_reminders",
        'worldmock.life.schedule::require_remind_window',
        "    if goal.remind_at is not None and not now < goal.remind_at < day_end:\n",
        ("    if (goal.remind_at is None and goal.intent_kind in ROUTINE_INTENT_KINDS) or (\n"
         "            goal.remind_at is not None and not now < goal.remind_at < day_end):\n"),
        _ROUTINE_GOAL_KILLERS,
    ),
)

RECEIPTS = REPO_ROOT / ".work/idle-fillers/mutation-receipts"
BASELINE_TIMEOUT_FLOOR_SECONDS = 600
BASELINE_TIMEOUT_SECONDS_PER_NODE = 120
MUTATION_TIMEOUT_SECONDS = 600


def run_behavior(nodes: tuple[str, ...], directory: Path, mutation: SourceMutation | None = None) -> dict:
    """@brief 以本台账的回执目录与超时调用共用变异执行器：不带变异是基线，带变异是一次杀死尝试。"""
    timeout = MUTATION_TIMEOUT_SECONDS
    if mutation is None:
        timeout = max(BASELINE_TIMEOUT_FLOOR_SECONDS, BASELINE_TIMEOUT_SECONDS_PER_NODE * len(nodes))
    return mutation_runner.run_behavior(nodes, directory, mutation, RunSettings(CHILD_ENTRY, RECEIPTS, timeout))
