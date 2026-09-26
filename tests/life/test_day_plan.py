"""当日目标登记、修订、实际推进与保存恢复合同。"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.life.conftest import (
    StubPlanDayClient, advance_next, advance_until_ready, conclude_pending, context, drive, settle_day_plans,
    wake_by_mail,
)
from agentsociety2.society import DirectToolCall
from worldmock.errors import ProtocolError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    PlanDayRequest, PlanGoalInput, RecallTaskSummaryRequest, ReviseDayPlanRequest, StartActivityRequest,
)
from worldmock.life.rhythm import _goal_and_deadline_pulses, engine_rhythm_inputs
from worldmock.life.schedule import (
    INTENT_KINDS, DayPlan, PlanGoal, day_end_seconds, day_plan_expired,
    expired_plan_agent_ids, initial_day_plan, progress_day_plan, revise_day_plan,
)
from worldmock.runtime import settle_pending_day_plans


def goal(kind="工作", text="推进手头工作", registered=100, **evidence):
    return PlanGoal(intent_kind=kind, goal=text, registered_at=registered, **evidence)


def planned(kind, text):
    """零点计划客户端桩：生成只含一条不设提醒的目标的当日计划。"""
    return StubPlanDayClient(goals=[{"intent_kind": kind, "goal": text, "remind_time": None}])


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
    with pytest.raises(ValidationError, match="at most eight unique intents"):
        PlanDayRequest(goals=[{"intent_kind": kind, "goal": "想推进", "remind_time": None}
                              for kind in INTENT_KINDS[:9]])


@pytest.mark.parametrize("text", ["", " ", "\n\t", "字" * 301])
def test_goal_text_bounds_apply_to_input_and_persisted_state(text):
    with pytest.raises(ValidationError, match="pattern|exceeds 300 characters"):
        PlanGoalInput(intent_kind="工作", goal=text, remind_time=None)
    with pytest.raises(ValidationError):
        goal(text=text)


def test_goal_text_limit_and_closed_intents():
    assert PlanGoalInput(intent_kind="工作", goal="字" * 300, remind_time=None).goal == "字" * 300
    assert goal(text="字" * 300).goal == "字" * 300
    for model, fields in ((PlanGoalInput, {"remind_time": None}), (PlanGoal, {"registered_at": 100})):
        with pytest.raises(ValidationError, match="literal_error"):
            model(intent_kind="阅读", goal="看书", **fields)
    with pytest.raises(ValidationError):
        DayPlan(day_end=1000, goals=[goal(), goal()])
    with pytest.raises(ValidationError, match="at most eight unique intents"):
        PlanDayRequest(goals=[{"intent_kind": "工作", "goal": "甲", "remind_time": None},
                              {"intent_kind": "工作", "goal": "乙", "remind_time": None}])


@pytest.mark.parametrize("extra", [{"start": 100}, {"end": 300}, {"note": "备注"},
                                  {"duration_seconds": 60}, {"progressed_at": 200}])
def test_input_rejects_old_fields_and_environment_owned_fields(extra):
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PlanGoalInput.model_validate({"intent_kind": "工作", "goal": "写报告", "remind_time": None, **extra})


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
    now = engine.state.current_time
    assert settle_day_plans(engine, planned("休息", "歇一歇"))[0]["goals"] == 1
    registered = engine.store.read_agent_events("environment_event", 1)[-1]
    assert registered.data["kind"] == "day_plan_generated" and engine.state.current_time == now
    assert registered.data["day_plan"] == engine.state.agents[1].day_plan.model_dump(mode="json")
    assert engine.state.agents[1].day_plan.goals[0].registered_at == now
    assert advance_until_ready(engine)
    started = act(engine, "rest", "start_activity",
                  StartActivityRequest(targets=["自己"], actions=["等待"]))
    advance_next(engine)
    plan = engine.state.agents[1].day_plan
    assert plan.goals[0].progress_activity_id == started.activity_id
    progress = engine.store.read_agent_events("activity_progress", 1)[-1]
    assert progress.data["day_plan"] == plan.model_dump(mode="json")
    assert progress.data["activity"]["location_id"] == engine.state.agents[1].location_id
    snapshot = engine.store.assets.put_json({"agent_id": 1}, {"kind": "goal-test"})
    engine.checkpoint({"agent:1": snapshot})
    directory = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), directory)
    try:
        assert restored.state.agents[1].day_plan == plan
        assert restored.scheduling_state().ready_agent_ids == (1,)
        assert restored.state.agents[1].day_plan == plan
    finally:
        restored.close()


def test_recall_task_summary_by_goal_name_round_trips(engine_factory):
    """按目标名称回看返回目标与推进状态，结果随下一次观察进入 details.recall；未知名称是业务拒绝
    （时间与能量规格「仍须修复」第 3 条、第七章 recall_task_summary）。"""
    engine = engine_factory()
    assert settle_day_plans(engine, planned("休息", "歇一歇"))[0]["goals"] == 1
    assert advance_until_ready(engine)
    recall = RecallTaskSummaryRequest(task_name="目标：休息：歇一歇")
    before = act(engine, "recall-before", "recall_task_summary", recall)
    assert before.status == "completed"
    expected = {"kind": "goal", "goal": "歇一歇", "intent_kind": "休息", "progressed": False}
    assert before.data["recall"] == expected
    shown = engine.observe(context(engine, "recall-shown"))["facts"]["progress"]["results"]
    assert {"recall": expected} in [row.get("details") for row in shown]
    assert engine.state.agents[1].day_plan.goals[0].progressed_at is None
    assert advance_until_ready(engine)
    act(engine, "recall-rest", "start_activity", StartActivityRequest(targets=["自己"], actions=["等待"]))
    advance_next(engine)
    assert engine.state.agents[1].day_plan.goals[0].progressed_at is not None
    # 等待按计划结束即成待收尾：当秒收尾之后才做别的（第七章「活动一结束就要结束它」）。
    assert conclude_pending(engine, "recall-rest-end").status == "completed"
    after = act(engine, "recall-after", "recall_task_summary", recall)
    assert after.status == "completed"
    assert after.data["recall"] == {**expected, "progressed": True}
    assert advance_until_ready(engine)
    unknown = act(engine, "recall-unknown", "recall_task_summary",
                  RecallTaskSummaryRequest(task_name="目标：休息：睡个午觉"))
    assert unknown.status == "rejected" and unknown.error_code == "TASK_NOT_FOUND"


def test_empty_plan_is_registered_once_and_can_be_revised(engine_factory):
    """零点计划的空清单也是当日登记：人物可用 revise_day_plan 补目标；同一本地日不再生成（后台任务不调用客户端），
    系统登记入口的重复登记是协议错误，计划保持修订后的样子（第四章「输入、调用、失败与记录」）。"""
    engine = engine_factory()
    assert settle_day_plans(engine)[0]["goals"] == 0
    assert engine.state.agents[1].day_plan.goals == ()
    assert engine.state.agents[1].plan_submitted_dates == {"2026-01-05"}
    revised = act(engine, "add", "revise_day_plan", ReviseDayPlanRequest(
        goals=[{"intent_kind": "工作", "goal": "写报告", "remind_time": None}]))
    assert revised.status == "completed"
    plan = engine.state.agents[1].day_plan
    assert [item.goal for item in plan.goals] == ["写报告"]
    again = StubPlanDayClient()
    assert drive(settle_pending_day_plans(engine, client=again)) == [] and again.calls == []
    with pytest.raises(ProtocolError, match="already generated"):
        engine.register_day_plan(1, PlanDayRequest(goals=()), [], [])
    assert engine.state.agents[1].day_plan == plan


def test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time(engine_factory):
    engine = engine_factory()
    now = engine.state.current_time
    agent = engine.state.agents[1].model_copy(update={"day_plan": DayPlan(
        day_end=now + 1000, goals=[goal("休息", registered=now), goal("晨间例程", registered=now)])})
    data = {"activity": activity("wait"), "interval_start": now, "interval_end": now + 60}
    inputs = engine_rhythm_inputs(engine, engine.state, 60, progress_data={1: [data]})
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
    now = engine.state.current_time
    midnight = day_end_seconds(now, engine.scenario.timezone)
    staged = engine.state.model_copy(deep=True)
    staged.current_time = midnight - engine.scenario.activity_feedback_seconds
    staged.pending_events = []
    staged.agents[1] = staged.agents[1].model_copy(update={"next_decision_at": staged.current_time,
        "body_settled_at": staged.current_time,
        "day_plan": DayPlan(day_end=midnight, goals=[goal("休息", registered=staged.current_time)])})
    _commit_staged(engine, staged)
    started = act(engine, "last-minute", "start_activity",
                  StartActivityRequest(targets=["自己"], actions=["等待"]))
    boundary = engine.scheduling_state().next_event_at
    assert int(boundary.timestamp()) == midnight
    engine.advance(midnight - engine.state.current_time, boundary)
    assert engine.state.current_time == midnight and engine.midnight_rhythm_pending
    assert engine.state.agents[1].day_plan is None
    progress = engine.store.read_agent_events("activity_progress", 1)[-1].data["day_plan"]
    assert progress["day_end"] == midnight
    assert progress["goals"][0]["progressed_at"] == midnight
    assert progress["goals"][0]["progress_activity_id"] == started.activity_id
    settle_day_plans(engine, planned("休息", "新的一天先歇一歇"))
    fresh = engine.state.agents[1].day_plan
    assert fresh.day_end == day_end_seconds(midnight, engine.scenario.timezone)
    assert [(item.goal, item.registered_at, item.progressed_at) for item in fresh.goals] == [
        ("新的一天先歇一歇", midnight, None)]


def test_late_plan_generation_registers_now_without_retroactive_progress(engine_factory):
    """人物不需要先有当日计划就能行动；稍后才生成的计划以生成时刻登记，此前结算的同家族活动不回溯推进它
    （第四章：目标的 registered_at 为登记事件时刻）。"""
    engine = engine_factory()
    act(engine, "before-plan", "start_activity",
        StartActivityRequest(targets=["自己"], actions=["等待"]))
    assert advance_until_ready(engine)
    # 等待按计划结束即成待收尾，当秒收尾后才生成当日计划（第七章「活动一结束就要结束它」）。
    assert conclude_pending(engine, "before-plan-end").status == "completed"
    registered_at = engine.state.current_time
    settle_day_plans(engine, planned("休息", "歇一歇"))
    assert engine.state.agents[1].day_plan.goals[0].registered_at == registered_at
    assert engine.state.agents[1].day_plan.goals[0].progressed_at is None


def test_revision_merged_limit_returns_domain_error_and_preserves_plan(engine_factory):
    from tests.life.test_revision_contract import _commit_staged
    engine = engine_factory()
    now = engine.state.current_time
    goals = [goal(kind, registered=now - 60, progressed_at=now, progress_activity_id=f"a-{kind}")
             for kind in INTENT_KINDS[:8]]
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"day_plan": DayPlan(
        day_end=day_end_seconds(now, engine.scenario.timezone), goals=goals)})
    _commit_staged(engine, staged)
    original = engine.state.agents[1].day_plan
    result = act(engine, "too-many", "revise_day_plan", ReviseDayPlanRequest(
        goals=[{"intent_kind": INTENT_KINDS[8], "goal": "多一条", "remind_time": None}]))
    assert result.error_code == "PLAN_GOALS_INVALID"
    assert engine.state.agents[1].day_plan == original
