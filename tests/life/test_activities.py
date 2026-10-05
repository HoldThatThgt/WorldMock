"""活动资源、暂停、中断和反馈边界测试。"""

from __future__ import annotations

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.activities import (
    ActivityOrigin,
    advance_activity,
    at_decision_boundary,
    change_activity,
    schedule_feedback,
    start_activity,
    truncate_activity,
)
from worldmock.life.models import (
    ActivityChange,
    ActivityKind,
    ActivityStatus,
    AgentState,
    BodyState,
    ChangeActivityRequest,
    ActivityStartParams,
)
from worldmock.life.world_setup import load_scenario


def agent() -> AgentState:
    """建立足够能量的测试人物。"""
    return AgentState(agent_id=1, name="worker", location_id="home", balance_account_id="agent:1",
                      body_settled_at=0,
                      body=BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0),
                      next_decision_at=0)


def origin(name: str) -> ActivityOrigin:
    """形成可核对的测试动作来源。"""
    return ActivityOrigin(name, f"decision-{name}", f"request-{name}")


def test_activity_pause_resume_cancel_preserves_actual_progress() -> None:
    """暂停不算结束，恢复重算边界，退出保留已结算区间。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    feedback = scenario.activity_feedback_seconds
    person, activity = start_activity(
        agent(), ActivityStartParams(activity_type=ActivityKind.WAIT,
                                      planned_duration_seconds=2700), 100, origin("a"), scenario.body)
    activity = schedule_feedback(activity, 100, feedback)
    advanced = advance_activity(activity, person, 1000, scenario.body)
    person, activity = change_activity(
        advanced.agent, advanced.activity,
        ChangeActivityRequest(activity_id="activity-a", operation=ActivityChange.PAUSE), 1000, feedback)
    assert activity.status == ActivityStatus.PAUSED and activity.ended_at is None
    person, activity = change_activity(
        person, activity, ChangeActivityRequest(activity_id="activity-a", operation=ActivityChange.RESUME),
        1300, feedback)
    assert activity.planned_end_at == 3100 and activity.next_interaction_at == 2200
    advanced = advance_activity(activity, person, 2200, scenario.body)
    person, activity = change_activity(
        advanced.agent, advanced.activity,
        ChangeActivityRequest(activity_id="activity-a", operation=ActivityChange.CANCEL), 2200, feedback)
    assert activity.elapsed_seconds == 1800 and activity.ended_at == 2200
    assert activity.status == ActivityStatus.CANCELLED


def test_pausing_a_paused_activity_is_refused_and_keeps_it_paused() -> None:
    """决策边界只对进行中的活动成立：已暂停的活动即使已结算到此刻也不在边界，再次暂停以 ACTIVITY_FEEDBACK_NOT_DUE
    拒绝，暂停状态与已结算进度不变。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    feedback = scenario.activity_feedback_seconds
    person, activity = start_activity(
        agent(), ActivityStartParams(activity_type=ActivityKind.WAIT,
                                      planned_duration_seconds=2700), 100, origin("a"), scenario.body)
    advanced = advance_activity(schedule_feedback(activity, 100, feedback), person, 1000, scenario.body)
    pause = ChangeActivityRequest(activity_id="activity-a", operation=ActivityChange.PAUSE)
    assert at_decision_boundary(advanced.activity, 1000)
    person, paused = change_activity(advanced.agent, advanced.activity, pause, 1000, feedback)
    assert not at_decision_boundary(paused, 1300)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_FEEDBACK_NOT_DUE"):
        change_activity(person, paused, pause, 1300, feedback)
    assert paused.status == ActivityStatus.PAUSED and paused.elapsed_seconds == 900


def test_direct_start_rejects_short_request_and_defers_shared_gates_to_engine() -> None:
    """领域构造检查最小时长；资源和共享能量由引擎在完整集合上检查。"""
    config = load_scenario("scenarios/white-collar-12-weeks.json")[0].body
    person = agent()
    with pytest.raises(BusinessRuleError, match="ACTIVITY_TOO_SHORT"):
        start_activity(person, ActivityStartParams(activity_type="wait", planned_duration_seconds=59),
                       0, origin("short"), config)
    person, _ = start_activity(
        person, ActivityStartParams(activity_type="wait", planned_duration_seconds=60),
        0, origin("x"), config)
    _, sleep = start_activity(person, ActivityStartParams(activity_type="sleep", planned_duration_seconds=60),
                              0, origin("y"), config)
    assert sleep.kind == ActivityKind.SLEEP
    weak = agent().model_copy(update={"body": BodyState(
        energy_kcal=1, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)})
    _, work = start_activity(weak, ActivityStartParams(activity_type="work", planned_duration_seconds=3600,
                                                        target_id="task"), 0, origin("z"), config)
    assert work.kind == ActivityKind.WORK


def test_completion_and_experiment_truncation_set_ended_at() -> None:
    """自然完成和实验截断具有不同边界并都记录实际结束时刻。"""
    config = load_scenario("scenarios/white-collar-12-weeks.json")[0].body
    person, activity = start_activity(
        agent(), ActivityStartParams(activity_type="sleep", planned_duration_seconds=60),
        10, origin("one"), config)
    completed = advance_activity(activity, person, 70, config)
    assert completed.activity.status == ActivityStatus.COMPLETED
    assert completed.activity.ended_at == 70
    person, activity = start_activity(
        completed.agent, ActivityStartParams(activity_type="wait", planned_duration_seconds=600),
        70, origin("two"), config)
    activity, person = truncate_activity(activity, person, 100)
    assert activity.status == ActivityStatus.TRUNCATED
    assert activity.boundary_type == "experiment_end" and activity.ended_at == 100
    assert activity.interruption_reason == "experiment_end"
