"""当日目标登记、修订、实际推进与保存恢复合同。"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.life.conftest import (
    StubPlanDayClient, advance_next, advance_until_ready, conclude_pending, context, drive, settle_day_plans,
    start_activity, wake_by_mail,
)
from agentsociety2.society import DirectToolCall
from worldmock.errors import BusinessRuleError, ProtocolError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    PlanDayRequest, PlanGoalInput, RecallTaskSummaryRequest, ReviseDayPlanRequest,
)
from worldmock.life.rhythm import _goal_and_deadline_pulses, engine_rhythm_inputs
from worldmock.life.schedule import (
    GOAL_TIME_TOLERANCE_SECONDS, INTENT_KINDS, ROUTINE_INTENT_KINDS, DayPlan, PlanGoal, RemindTimeInvalid,
    day_end_seconds, day_plan_expired, expired_plan_agent_ids, initial_day_plan, progress_day_plan,
    ran_in_goal_window, registered_goals, revise_day_plan,
)
from worldmock.runtime import settle_pending_day_plans

#: 纯函数用例的当日时刻：以 0 为当日零点，HOUR 为一小时。
HOUR = 3600
DAY_END = 24 * HOUR


def at(hour: int, minute: int = 0) -> int:
    """纯函数用例中当日 hour:minute 的时刻（当日零点为 0）。"""
    return hour * HOUR + minute * 60


def goal(kind="工作", text="推进手头工作", registered=100, **evidence):
    return PlanGoal(intent_kind=kind, goal=text, registered_at=registered, **evidence)


def planned(kind, text):
    """零点计划客户端桩：生成只含一条不设提醒的目标的当日计划。"""
    return StubPlanDayClient(goals=[{"intent_kind": kind, "goal": text, "remind_time": None, "around_time": None}])


def activity(kind="work", **changes):
    return {"activity_id": "activity-a", "agent_id": 1, "kind": kind, "status": "active", **changes}


def act(engine, name, tool, request):
    ctx = context(engine, name)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, f"action-{name}", tool, request))
    return engine.commit_decision_round(name)[0]


def test_empty_and_eight_goals_are_valid_and_ninth_is_rejected():
    assert initial_day_plan([], 1000).goals == ()
    goals = tuple(goal(kind) for kind in INTENT_KINDS[:8])
    assert len(initial_day_plan(goals, 1000).goals) == 8
    with pytest.raises(ValidationError):
        initial_day_plan(goals + (goal(INTENT_KINDS[8]),), 1000)
    with pytest.raises(ValidationError, match="at most eight entries"):
        PlanDayRequest(goals=[{"intent_kind": kind, "goal": "想推进", "remind_time": None, "around_time": None}
                              for kind in INTENT_KINDS[:9]])


@pytest.mark.parametrize("text", ["", " ", "\n\t", "字" * 301])
def test_goal_text_bounds_apply_to_input_and_persisted_state(text):
    with pytest.raises(ValidationError, match="pattern|exceeds 300 characters"):
        PlanGoalInput(intent_kind="工作", goal=text, remind_time=None, around_time=None)
    with pytest.raises(ValidationError):
        goal(text=text)


def test_goal_text_limit_and_closed_intents():
    assert PlanGoalInput(intent_kind="工作", goal="字" * 300, remind_time=None, around_time=None).goal == "字" * 300
    assert goal(text="字" * 300).goal == "字" * 300
    for model, fields in ((PlanGoalInput, {"remind_time": None, "around_time": None}),
                          (PlanGoal, {"registered_at": 100})):
        with pytest.raises(ValidationError, match="literal_error"):
            model(intent_kind="阅读", goal="看书", **fields)
    with pytest.raises(ValidationError):
        DayPlan(day_end=1000, goals=[goal(), goal()])
    with pytest.raises(ValidationError, match="keep one goal for this intent"):
        PlanDayRequest(goals=[{"intent_kind": "工作", "goal": "甲", "remind_time": None, "around_time": None},
                              {"intent_kind": "工作", "goal": "乙", "remind_time": None, "around_time": None}])


@pytest.mark.parametrize("extra", [{"start": 100}, {"end": 300}, {"note": "备注"},
                                  {"duration_seconds": 60}, {"progressed_at": 200}])
def test_input_rejects_old_fields_and_environment_owned_fields(extra):
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PlanGoalInput.model_validate({"intent_kind": "工作", "goal": "写报告", "remind_time": None, "around_time": None,
                                      **extra})


def test_old_checkpoint_and_request_shapes_are_rejected():
    with pytest.raises(ValidationError):
        DayPlan.model_validate({"day_end": 1000, "bedtime": 900, "segments": []})
    with pytest.raises(ValidationError):
        PlanDayRequest.model_validate({"segments": []})


@pytest.mark.parametrize("evidence", [
    {"progressed_at": 200}, {"progress_activity_id": "a"},
    {"progressed_at": 100, "progress_activity_id": "a"},
    {"progressed_at": 99, "progress_activity_id": "a"},
])
def test_progress_evidence_pair_must_follow_registration(evidence):
    with pytest.raises(ValidationError):
        goal(**evidence)


def test_state_rejects_duplicate_activity_evidence_and_out_of_day_goals():
    progressed = goal(progressed_at=200, progress_activity_id="a")
    with pytest.raises(ValidationError):
        DayPlan(day_end=1000, goals=[progressed, goal("休息", progressed_at=200, progress_activity_id="a")])
    with pytest.raises(ValidationError):
        DayPlan(day_end=100, goals=[goal()])
    with pytest.raises(ValidationError):
        DayPlan(day_end=150, goals=[progressed])


def test_revision_keeps_progressed_prefix_and_original_registration_for_unchanged_goals():
    pending = goal("工作", "写报告")
    progressed = goal("休息", "放松", progressed_at=200, progress_activity_id="a")
    plan = DayPlan(day_end=1000, goals=[pending, progressed, goal("运动", "散步")])
    changed = revise_day_plan(plan, 300, [goal("运动", "散步", 300), goal("工作", "新报告", 300)])
    assert changed.goals == (progressed, plan.goals[2], goal("工作", "新报告", 300))
    assert revise_day_plan(plan, 300, []).goals == (progressed,)
    assert plan.goals[0] == pending
    with pytest.raises(ValueError, match="already progressed"):
        revise_day_plan(plan, 300, [goal("休息", "其他休息", 300)])


def test_revision_merged_capacity_is_checked():
    progressed = tuple(goal(kind, progressed_at=200, progress_activity_id=f"a-{kind}")
                       for kind in INTENT_KINDS[:8])
    with pytest.raises(ValidationError):
        revise_day_plan(DayPlan(day_end=1000, goals=progressed), 300, [goal(INTENT_KINDS[8], registered=300)])


@pytest.mark.parametrize("payload,start,end", [
    (activity(), 100, 100), (activity(), 0, 99), (activity(), 0, 100),
    (activity(status="cancelled"), 100, 200), (activity(status="failed"), 100, 200),
    (activity("wait"), 100, 200), (activity(), 1000, 1100),
])
def test_progress_requires_positive_registered_day_matching_activity(payload, start, end):
    plan = DayPlan(day_end=1000, goals=[goal()])
    assert progress_day_plan(plan, payload, start, end) == plan


def test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization():
    plan = DayPlan(day_end=1000, goals=[goal("通勤"), goal("外出")])
    first = progress_day_plan(plan, activity("travel"), 100, 200)
    assert first.goals[0].progressed_at == 200
    assert first.goals[1].progressed_at is None
    loaded = DayPlan.model_validate_json(first.model_dump_json())
    revised = revise_day_plan(loaded, 250, [goal("外出", registered=250)])
    assert progress_day_plan(revised, activity("travel"), 250, 300) == revised
    other = progress_day_plan(revised, activity("travel", activity_id="activity-b"), 300, 400)
    assert other.goals[1].progress_activity_id == "activity-b"


def test_lifestyle_family_uses_actual_category_and_midnight_interval_counts():
    plan = DayPlan(day_end=1000, goals=[goal("运动", registered=950)])
    assert progress_day_plan(plan, activity("lifestyle"), 950, 1000) == plan
    assert progress_day_plan(plan, activity("lifestyle", category="hobby"), 950, 1000) == plan
    result = progress_day_plan(plan, activity("lifestyle", category="sport"), 950, 1050)
    assert result.goals[0].progressed_at == 1000
    assert result.goals[0].progress_activity_id == "activity-a"


def _around_plan(kind: str, around: int | None, registered: int = 0) -> DayPlan:
    """一条带大致时刻（或不带）的当日目标，当日零点登记。"""
    return DayPlan(day_end=DAY_END, goals=[goal(kind, registered=registered, around_at=around)])


def _ran(kind: str, started: int, **changes) -> dict:
    """开始时刻为 started 的活动快照。"""
    return activity(kind, started_at=started, **changes)


@pytest.mark.parametrize(("kind", "intent", "around", "started", "interval", "progressed"), [
    # 大致时刻 12:00 的午餐：10:20–10:45 的进食与 [10:30, 13:30] 有交集，推进；09:30–09:55 的不推进。
    ("consume", "用餐", at(12), at(10, 20), (at(10, 20), at(10, 45)), at(10, 45)),
    ("consume", "用餐", at(12), at(9, 30), (at(9, 30), at(9, 55)), None),
    # 08:40 的浏览不推进大致时刻为 23:00 的娱乐目标；同一浏览推进不带大致时刻的目标（沿用现行判定）。
    ("browse", "娱乐", at(23), at(8, 40), (at(8, 40), at(8, 55)), None),
    ("browse", "娱乐", None, at(8, 40), (at(8, 40), at(8, 55)), at(8, 55)),
    # 零点前开始、零点后结算的睡眠不推进大致时刻为 23:30 的睡眠目标；不带大致时刻的照旧推进。
    ("sleep", "睡眠", at(23, 30), -10 * 60, (0, at(0, 30)), None),
    ("sleep", "睡眠", None, -10 * 60, (0, at(0, 30)), at(0, 30)),
    # 零点前开始的睡眠在大致时刻 00:30 的窗口内进行，推进。
    ("sleep", "睡眠", at(0, 30), -10 * 60, (0, at(0, 15)), at(0, 15)),
    # 活动开始得早、这次结算落在窗口内：按活动的进行区间判定，推进。
    ("watch", "娱乐", at(23), at(20), (at(21, 15), at(21, 45)), at(21, 45)),
    # 窗口两端：进行区间与窗口只相接不算交集，多一秒才算。
    ("watch", "娱乐", at(12), at(10), (at(10), at(10, 30)), None),
    ("watch", "娱乐", at(12), at(10), (at(10), at(10, 30) + 1), at(10, 30) + 1),
    ("watch", "娱乐", at(12), at(13, 30), (at(13, 30), at(13, 45)), None),
    ("watch", "娱乐", at(12), at(13, 30) - 1, (at(13, 30) - 1, at(13, 45)), at(13, 45)),
    # 窗口越过次日零点的部分不算：结算止点按次日零点裁剪，次日零点之后才开始的不推进。
    ("watch", "娱乐", at(23, 30), at(23, 50), (at(23, 50), DAY_END + HOUR), DAY_END),
    ("watch", "娱乐", at(23, 30), DAY_END + 600, (DAY_END + 600, DAY_END + 1200), None),
])
def test_progress_with_an_around_time_requires_the_activity_to_run_near_it(kind, intent, around, started, interval,
                                                                            progressed):
    """推进判定（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）：同家族、登记后的正时长结算，且活动在当日内的进行区间（活动
    实际开始时刻至结算止点，不是结算区间起点）与 [around − 90 分钟, around + 90 分钟] ∩ 当日有正时长交集；大致时刻为空
    的目标沿用现行判定。"""
    assert GOAL_TIME_TOLERANCE_SECONDS == 5400
    plan = _around_plan(intent, around)
    result = progress_day_plan(plan, _ran(kind, started), *interval)
    assert result.goals[0].progressed_at == progressed
    assert ran_in_goal_window(around, started, min(interval[1], DAY_END)) is (progressed is not None)


def test_around_time_skips_a_goal_outside_its_window_and_needs_the_actual_start():
    """同家族的前一个目标不在窗口内时照清单顺序匹配下一个；有大致时刻的目标需要活动的实际开始时刻，快照残缺时不推进。"""
    plan = DayPlan(day_end=DAY_END, goals=[goal("娱乐", "睡前刷会儿", 0, around_at=at(23)),
                                           goal("休息", "歇一歇", 0), goal("外出", "散步", 0, around_at=at(9))])
    browsed = progress_day_plan(plan, _ran("browse", at(8, 40)), at(8, 40), at(8, 55))
    assert [item.progressed_at for item in browsed.goals] == [None, None, None]
    waited = progress_day_plan(plan, _ran("wait", at(8, 40)), at(8, 40), at(8, 55))
    assert [item.progressed_at for item in waited.goals] == [None, at(8, 55), None]
    assert progress_day_plan(plan, activity("travel"), at(8, 40), at(8, 55)) == plan
    walked = progress_day_plan(plan, _ran("travel", at(8, 40)), at(8, 40), at(8, 55))
    assert walked.goals[2].progress_activity_id == "activity-a"
    assert not ran_in_goal_window(at(9), None, at(8, 55)) and ran_in_goal_window(None, None, at(8, 55))
    # 13:40 修订登记的午饭目标（大致时刻 12:00）：13:00 开始的进食在窗口内进行，登记后 13:30–13:45 的结算推进它；
    # 若按结算区间起点 13:30 判定就落在窗口之外。
    revised = DayPlan(day_end=DAY_END, goals=[goal("用餐", "午饭", at(13, 40), around_at=at(12))])
    eaten = progress_day_plan(revised, _ran("consume", at(13)), at(13, 30), at(13, 45))
    assert eaten.goals[0].progressed_at == at(13, 45)
    assert not ran_in_goal_window(at(12), at(13, 30), at(13, 45))


def test_around_time_is_required_nullable_hh_mm_and_needs_no_later_than_now():
    """around_time 必填可空、格式同 remind_time；换算为登记日的绝对时刻，不像提醒那样要求晚于登记时刻，状态里不越过
    次日零点（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）。"""
    for value in (None, "00:00", "08:15", "23:59"):
        assert PlanGoalInput(intent_kind="用餐", goal="午饭", remind_time=None, around_time=value).around_time == value
    for value in ("8:15", "24:00", "12:60", "08:15:00", "约 08:15", ""):
        with pytest.raises(ValidationError, match="around_time"):
            PlanGoalInput(intent_kind="用餐", goal="午饭", remind_time=None, around_time=value)
    with pytest.raises(ValidationError, match="around_time"):
        PlanGoalInput.model_validate({"intent_kind": "用餐", "goal": "午饭", "remind_time": None})
    now = 1_767_589_200  # 2026-01-05 13:00（Asia/Shanghai）
    request = PlanGoalInput(intent_kind="用餐", goal="午饭推迟到一点前后", remind_time=None, around_time="12:00")
    [registered] = registered_goals([request], now, "Asia/Shanghai")
    assert (registered.registered_at, registered.around_at, registered.remind_at) == (now, now - HOUR, None)
    assert initial_day_plan([registered], day_end_seconds(now, "Asia/Shanghai")).goals == (registered,)
    late = request.model_copy(update={"remind_time": "12:00"})
    with pytest.raises(RemindTimeInvalid):
        initial_day_plan(registered_goals([late], now, "Asia/Shanghai"), day_end_seconds(now, "Asia/Shanghai"))
    with pytest.raises(ValidationError, match="exceeds day_end"):
        DayPlan(day_end=DAY_END, goals=[goal(around_at=DAY_END)])


def test_revision_compares_four_fields_and_changing_only_around_keeps_registration():
    """修订的等价比较含 around_time（四字段）：四项都相同的原样保留；只改大致时刻的保留登记时刻与提醒状态（已触发的提醒
    不重校、不再触发），只换大致时刻；改了提醒的保留登记时刻、换新提醒与新大致时刻；改文字的是新登记。"""
    fired = goal("用餐", "午饭", 0, remind_at=at(11), reminded_at=at(11), around_at=at(12))
    plan = DayPlan(day_end=DAY_END, goals=[fired, goal("娱乐", "看剧", 0, around_at=at(21))])
    now = at(13)
    same = revise_day_plan(plan, now, registered_goals_at(now, ("用餐", "午饭", at(11), at(12)),
                                                          ("娱乐", "看剧", None, at(21))))
    assert same.goals == plan.goals
    moved = revise_day_plan(plan, now, registered_goals_at(now, ("用餐", "午饭", at(11), at(13, 30)),
                                                           ("娱乐", "看剧", None, None)))
    assert moved.goals[0] == fired.model_copy(update={"around_at": at(13, 30)})
    assert (moved.goals[1].registered_at, moved.goals[1].around_at) == (0, None)
    reminded = revise_day_plan(plan, now, registered_goals_at(now, ("用餐", "午饭", at(14), at(14))))
    assert (reminded.goals[0].registered_at, reminded.goals[0].remind_at, reminded.goals[0].reminded_at,
            reminded.goals[0].around_at) == (0, at(14), None, at(14))
    rewritten = revise_day_plan(plan, now, registered_goals_at(now, ("用餐", "晚点吃午饭", None, at(12))))
    assert (rewritten.goals[0].registered_at, rewritten.goals[0].around_at) == (now, at(12))


def registered_goals_at(now: int, *rows: tuple) -> list[PlanGoal]:
    """以修订时刻 now 登记的目标（意图、文字、提醒时刻、大致时刻；时刻已是绝对时刻）。"""
    return [PlanGoal(intent_kind=kind, goal=text, registered_at=now, remind_at=remind, around_at=around)
            for kind, text, remind, around in rows]


def test_routine_intents_are_the_four_routine_kinds_of_the_closed_set():
    """例行意图是十一种意图闭集中的睡眠、晨间例程、通勤、用餐（docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-零点计划只写今天与平常不同的事 ）。"""
    assert ROUTINE_INTENT_KINDS == {"睡眠", "晨间例程", "通勤", "用餐"} and ROUTINE_INTENT_KINDS < set(INTENT_KINDS)


def test_day_boundary_expiry_and_agent_isolation():
    plan = DayPlan(day_end=1000, goals=[])
    assert not day_plan_expired(plan, 999)
    assert day_plan_expired(plan, 1000)
    state = SimpleNamespace(current_time=1000, agents={1: SimpleNamespace(day_plan=plan),
                            2: SimpleNamespace(day_plan=DayPlan(day_end=2000, goals=[])),
                            3: SimpleNamespace(day_plan=None)})
    assert expired_plan_agent_ids(state) == (1,)
    assert day_end_seconds(0, "Asia/Shanghai") == 57600


def test_minimal_engine_registration_progress_checkpoint_path(engine_factory):
    """零点计划经系统登记入口登记（不耗时，登记事件带完整计划），实际活动推进目标，检查点恢复后保持（第四章）。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    assert settle_day_plans(engine, planned("休息", "歇一歇"))[0]["goals"] == 1
    registered = engine.context.store.read_agent_events("environment_event", 1)[-1]
    assert registered.data["kind"] == "day_plan_generated" and engine.context.state.current_time == now
    assert registered.data["day_plan"] == engine.context.state.agents[1].day_plan.model_dump(mode="json")
    assert engine.context.state.agents[1].day_plan.goals[0].registered_at == now
    assert advance_until_ready(engine)
    started = act(engine, "rest", "start_activity",
                  start_activity("等待", "自己"))
    advance_next(engine)
    plan = engine.context.state.agents[1].day_plan
    assert plan.goals[0].progress_activity_id == started.activity_id
    progress = engine.context.store.read_agent_events("activity_progress", 1)[-1]
    assert progress.data["day_plan"] == plan.model_dump(mode="json")
    assert progress.data["activity"]["location_id"] == engine.context.state.agents[1].location_id
    snapshot = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "goal-test"})
    engine.checkpoint({"agent:1": snapshot})
    directory = engine.context.store.run_dir
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), directory)
    try:
        assert restored.context.state.agents[1].day_plan == plan
        assert restored.scheduling_state().ready_agent_ids == (1,)
        assert restored.context.state.agents[1].day_plan == plan
    finally:
        restored.close()


def test_recall_task_summary_by_goal_name_round_trips(engine_factory):
    """按待办层目标文字即时回看推进状态，不登记动作或进度事件；未知名称是业务拒绝。"""
    engine = engine_factory()
    assert settle_day_plans(engine, planned("休息", "歇一歇"))[0]["goals"] == 1
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "recall-plan"))
    shown = observed["todo_layer"]["day_plan"]["goals"][0]["goal"]
    assert shown == "歇一歇"
    recall = RecallTaskSummaryRequest(task_name=shown)
    state, sequence = engine.context.state.model_dump(), engine.context.store.last_event_seq
    before = engine.read_task_summary(1, recall, observed)
    expected = {"kind": "goal", "goal": "歇一歇", "intent_kind": "休息", "progressed": False}
    assert before["recall"] == expected
    assert (engine.context.state.model_dump(), engine.context.store.last_event_seq) == (state, sequence)
    shown = engine.observe(context(engine, "recall-shown"))["progress_layer"]["events"]
    assert not any(row.get("tool") == "recall_task_summary" for row in shown)
    assert engine.context.state.agents[1].day_plan.goals[0].progressed_at is None
    assert advance_until_ready(engine)
    act(engine, "recall-rest", "start_activity", start_activity("等待", "自己"))
    advance_next(engine)
    assert engine.context.state.agents[1].day_plan.goals[0].progressed_at is not None
    # 等待按计划结束即成待收尾：当秒收尾之后才做别的（第七章「活动一结束就要结束它」）。
    assert conclude_pending(engine, "recall-rest-end").status == "completed"
    observed = engine.observe(context(engine, "recall-after"))
    after = engine.read_task_summary(1, recall, observed)
    assert after["recall"] == {**expected, "progressed": True}
    with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND"):
        engine.read_task_summary(1, RecallTaskSummaryRequest(task_name="睡个午觉"), observed)


def test_empty_plan_is_registered_once_and_can_be_revised(engine_factory):
    """零点计划的空清单也是当日登记：人物可用 revise_day_plan 补目标；同一本地日不再生成（后台任务不调用客户端），
    系统登记入口的重复登记是协议错误，计划保持修订后的样子（第四章「输入、调用、失败与记录」）。"""
    engine = engine_factory()
    assert settle_day_plans(engine)[0]["goals"] == 0
    assert engine.context.state.agents[1].day_plan.goals == ()
    assert engine.context.state.agents[1].plan_submitted_dates == {"2026-01-05"}
    revised = act(engine, "add", "revise_day_plan", ReviseDayPlanRequest(
        goals=[{"intent_kind": "工作", "goal": "写报告", "remind_time": None, "around_time": None}]))
    assert revised.status == "completed"
    plan = engine.context.state.agents[1].day_plan
    assert [item.goal for item in plan.goals] == ["写报告"]
    again = StubPlanDayClient()
    assert drive(settle_pending_day_plans(engine, client=again)) == [] and again.calls == []
    with pytest.raises(ProtocolError, match="already generated"):
        engine.register_day_plan(1, PlanDayRequest(goals=[]), [], [])
    assert engine.context.state.agents[1].day_plan == plan


def test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time(engine_factory):
    engine = engine_factory()
    now = engine.context.state.current_time
    agent = engine.context.state.agents[1].model_copy(update={"day_plan": DayPlan(
        day_end=now + 1000, goals=[goal("休息", registered=now), goal("晨间例程", registered=now)])})
    data = {"activity": activity("wait"), "interval_start": now, "interval_end": now + 60}
    inputs = engine.context_rhythm_inputs(engine, engine.context.state, 60, progress_data={1: [data]})
    result = _goal_and_deadline_pulses(agent, inputs)
    from worldmock.life.body import apply_mood_pulse
    assert result.body == apply_mood_pulse(agent.body, "goal_progressed")
    assert _goal_and_deadline_pulses(result, inputs).body == result.body
    assert result.day_plan.goals[1].progressed_at is None
    for changed in ({"agent_id": 2}, {"status": "cancelled"}, {"status": "failed"}):
        data["activity"] = activity("wait", **changed)
        assert _goal_and_deadline_pulses(agent, inputs) == agent
    data["activity"] = activity("wait")
    data["interval_end"] = now
    assert _goal_and_deadline_pulses(agent, inputs) == agent
    assert _goal_and_deadline_pulses(agent, replace(inputs, progress_data={})) == agent


def test_midnight_final_interval_progresses_old_plan_before_expiry(engine_factory):
    """零点边界先按旧计划结算末段、旧计划随即失效，零点计划之后才为新的一天登记（第四章「运行时机」）。"""
    from tests.life.test_revision_contract import _commit_staged
    engine = engine_factory()
    now = engine.context.state.current_time
    midnight = day_end_seconds(now, engine.context.scenario.timezone)
    staged = engine.context.state.model_copy(deep=True)
    staged.current_time = midnight - engine.context.scenario.activity_feedback_seconds
    staged.pending_events = []
    staged.agents[1] = staged.agents[1].model_copy(update={"next_decision_at": staged.current_time,
        "body_settled_at": staged.current_time,
        "day_plan": DayPlan(day_end=midnight, goals=[goal("休息", registered=staged.current_time)])})
    _commit_staged(engine, staged)
    started = act(engine, "last-minute", "start_activity",
                  start_activity("等待", "自己"))
    boundary = engine.scheduling_state().next_event_at
    assert int(boundary.timestamp()) == midnight
    engine.advance(midnight - engine.context.state.current_time, boundary)
    assert engine.context.state.current_time == midnight and engine.midnight_rhythm_pending
    assert engine.context.state.agents[1].day_plan is None
    progress = engine.context.store.read_agent_events("activity_progress", 1)[-1].data["day_plan"]
    assert progress["day_end"] == midnight
    assert progress["goals"][0]["progressed_at"] == midnight
    assert progress["goals"][0]["progress_activity_id"] == started.activity_id
    settle_day_plans(engine, planned("休息", "新的一天先歇一歇"))
    fresh = engine.context.state.agents[1].day_plan
    assert fresh.day_end == day_end_seconds(midnight, engine.context.scenario.timezone)
    assert [(item.goal, item.registered_at, item.progressed_at) for item in fresh.goals] == [
        ("新的一天先歇一歇", midnight, None)]


def test_late_plan_generation_registers_now_without_retroactive_progress(engine_factory):
    """人物不需要先有当日计划就能行动；稍后才生成的计划以生成时刻登记，此前结算的同家族活动不回溯推进它
    （第四章：目标的 registered_at 为登记事件时刻）。"""
    engine = engine_factory()
    act(engine, "before-plan", "start_activity",
        start_activity("等待", "自己"))
    assert advance_until_ready(engine)
    # 等待按计划结束即成待收尾，当秒收尾后才生成当日计划（第七章「活动一结束就要结束它」）。
    assert conclude_pending(engine, "before-plan-end").status == "completed"
    registered_at = engine.context.state.current_time
    settle_day_plans(engine, planned("休息", "歇一歇"))
    assert engine.context.state.agents[1].day_plan.goals[0].registered_at == registered_at
    assert engine.context.state.agents[1].day_plan.goals[0].progressed_at is None


def test_revision_merged_limit_returns_domain_error_and_preserves_plan(engine_factory):
    from tests.life.test_revision_contract import _commit_staged
    engine = engine_factory()
    now = engine.context.state.current_time
    goals = [goal(kind, registered=now - 60, progressed_at=now, progress_activity_id=f"a-{kind}")
             for kind in INTENT_KINDS[:8]]
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"day_plan": DayPlan(
        day_end=day_end_seconds(now, engine.context.scenario.timezone), goals=goals)})
    _commit_staged(engine, staged)
    original = engine.context.state.agents[1].day_plan
    result = act(engine, "too-many", "revise_day_plan", ReviseDayPlanRequest(
        goals=[{"intent_kind": INTENT_KINDS[8], "goal": "多一条", "remind_time": None, "around_time": None}]))
    assert result.error_code == "PLAN_GOALS_INVALID"
    assert engine.context.state.agents[1].day_plan == original
