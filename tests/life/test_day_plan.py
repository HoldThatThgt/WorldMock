"""当日目标登记、修订、实际推进与保存恢复合同。"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.life.conftest import advance_next, advance_until_ready, context, wake_by_mail
from agentsociety2.society import DirectToolCall
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    PlanDayRequest, PlanGoalInput, RecallTaskSummaryRequest, ReviseDayPlanRequest, StartActivityRequest,
)
from worldmock.life.rhythm import _goal_and_deadline_pulses, engine_rhythm_inputs
from worldmock.life.schedule import (
    INTENT_KINDS, DayPlan, PlanGoal, day_end_seconds, day_plan_expired,
    expired_plan_agent_ids, initial_day_plan, progress_day_plan, revise_day_plan,
)


def goal(kind="工作", text="推进手头工作", registered=100, **evidence):
    return PlanGoal(intent_kind=kind, goal=text, registered_at=registered, **evidence)


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
    with pytest.raises(ValidationError):
        PlanDayRequest(goals=[{"intent_kind": kind, "goal": "想推进"} for kind in INTENT_KINDS[:9]])


@pytest.mark.parametrize("text", ["", " ", "\n\t", "字" * 301])
def test_goal_text_bounds_apply_to_input_and_persisted_state(text):
    with pytest.raises(ValidationError):
        PlanGoalInput(intent_kind="工作", goal=text)
    with pytest.raises(ValidationError):
        goal(text=text)


def test_goal_text_limit_and_closed_intents():
    assert PlanGoalInput(intent_kind="工作", goal="字" * 300).goal == "字" * 300
    assert goal(text="字" * 300).goal == "字" * 300
    for model in (PlanGoalInput, PlanGoal):
        with pytest.raises(ValidationError):
            model(intent_kind="阅读", goal="看书", registered_at=100)
    with pytest.raises(ValidationError):
        DayPlan(day_end=1000, goals=[goal(), goal()])
    with pytest.raises(ValidationError):
        PlanDayRequest(goals=[{"intent_kind": "工作", "goal": "甲"}, {"intent_kind": "工作", "goal": "乙"}])


@pytest.mark.parametrize("extra", [{"start": 100}, {"end": 300}, {"note": "备注"},
                                  {"duration_seconds": 60}, {"progressed_at": 200}])
def test_input_rejects_old_fields_and_environment_owned_fields(extra):
    with pytest.raises(ValidationError):
        PlanGoalInput.model_validate({"intent_kind": "工作", "goal": "写报告", **extra})


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
    engine = engine_factory()
    now = engine.state.current_time
    request = PlanDayRequest(goals=[{"intent_kind": "休息", "goal": "歇一歇"}])
    registered = act(engine, "goals", "plan_day", request)
    assert registered.status == "completed" and engine.state.current_time == now
    assert registered.data["day_plan"] == engine.state.agents[1].day_plan.model_dump(mode="json")
    advance_until_ready(engine)
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
    """按目标名称回看返回目标与推进状态，未知名称是业务拒绝（时间与能量规格「仍须修复」第 3 条）。"""
    engine = engine_factory()
    registered = act(engine, "recall-goals", "plan_day",
                     PlanDayRequest(goals=[{"intent_kind": "休息", "goal": "歇一歇"}]))
    assert registered.status == "completed"
    assert advance_until_ready(engine)
    recall = RecallTaskSummaryRequest(task_name="目标：休息：歇一歇")
    before = act(engine, "recall-before", "recall_task_summary", recall)
    assert before.status == "completed"
    assert before.data["goal"] == {"task_name": "歇一歇", "intent_kind": "休息", "progressed": False}
    assert engine.state.agents[1].day_plan.goals[0].progressed_at is None
    assert advance_until_ready(engine)
    act(engine, "recall-rest", "start_activity", StartActivityRequest(targets=["自己"], actions=["等待"]))
    advance_next(engine)
    assert engine.state.agents[1].day_plan.goals[0].progressed_at is not None
    assert advance_until_ready(engine)
    after = act(engine, "recall-after", "recall_task_summary", recall)
    assert after.status == "completed"
    assert after.data["goal"] == {"task_name": "歇一歇", "intent_kind": "休息", "progressed": True}
    assert advance_until_ready(engine)
    unknown = act(engine, "recall-unknown", "recall_task_summary",
                  RecallTaskSummaryRequest(task_name="目标：休息：睡个午觉"))
    assert unknown.status == "rejected" and unknown.error_code == "TASK_NOT_FOUND"


def test_empty_plan_is_registered_once_and_can_be_revised(engine_factory):
    engine = engine_factory()
    first = act(engine, "empty", "plan_day", PlanDayRequest(goals=[]))
    assert first.status == "completed" and engine.state.agents[1].day_plan.goals == ()
    advance_until_ready(engine)
    revised = act(engine, "add", "revise_day_plan", ReviseDayPlanRequest(
        goals=[{"intent_kind": "工作", "goal": "写报告"}]))
    assert revised.status == "completed"
    advance_until_ready(engine)
    duplicate = act(engine, "again", "plan_day", PlanDayRequest(goals=[]))
    assert duplicate.error_code == "PLAN_ALREADY_SUBMITTED"


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
    advance_next(engine)
    assert engine.state.current_time == midnight
    assert engine.state.agents[1].day_plan is None
    progress = engine.store.read_agent_events("activity_progress", 1)[-1].data["day_plan"]
    assert progress["day_end"] == midnight
    assert progress["goals"][0]["progressed_at"] == midnight
    assert progress["goals"][0]["progress_activity_id"] == started.activity_id


def test_late_first_submission_does_not_require_a_bedtime_schedule(engine_factory):
    engine = engine_factory()
    act(engine, "before-plan", "start_activity",
        StartActivityRequest(targets=["自己"], actions=["等待"]))
    advance_until_ready(engine)
    registered_at = engine.state.current_time
    result = act(engine, "late-goal", "plan_day", PlanDayRequest(
        goals=[{"intent_kind": "工作", "goal": "推进报告"}]))
    assert result.status == "completed"
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
        goals=[{"intent_kind": INTENT_KINDS[8], "goal": "多一条"}]))
    assert result.error_code == "PLAN_GOALS_INVALID"
    assert engine.state.agents[1].day_plan == original
