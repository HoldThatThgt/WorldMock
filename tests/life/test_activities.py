"""活动资源、暂停、中断和反馈边界测试。"""

from __future__ import annotations

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.activities import (
    ActivityOrigin,
    advance_activity,
    change_activity,
    continue_activity,
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
    ContinueActivityRequest,
    StartActivityRequest,
)
from worldmock.life.scenario import load_scenario


def agent() -> AgentState:
    """建立足够能量的测试人物。"""
    return AgentState(agent_id=1, name="worker", location_id="home", balance_account_id="agent:1",
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
        agent(), StartActivityRequest(activity_type=ActivityKind.WAIT,
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
    assert person.current_activity_id is None


def test_conflict_short_activity_and_exhaustion_are_rejected() -> None:
    """资源冲突、过短申请和能量不足具有稳定业务错误。"""
    config = load_scenario("scenarios/white-collar-12-weeks.json")[0].body
    person = agent()
    with pytest.raises(BusinessRuleError, match="ACTIVITY_TOO_SHORT"):
        start_activity(person, StartActivityRequest(activity_type="wait", planned_duration_seconds=59),
                       0, origin("short"), config)
    person, _ = start_activity(
        person, StartActivityRequest(activity_type="wait", planned_duration_seconds=60),
        0, origin("x"), config)
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        start_activity(person, StartActivityRequest(activity_type="sleep", planned_duration_seconds=60),
                       0, origin("y"), config)
    weak = agent().model_copy(update={"body": BodyState(
        energy_kcal=1, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)})
    with pytest.raises(BusinessRuleError, match="INSUFFICIENT_ENERGY"):
        start_activity(weak, StartActivityRequest(activity_type="work", planned_duration_seconds=3600,
                                                  target_id="task"), 0, origin("z"), config)


def test_completion_and_experiment_truncation_set_ended_at() -> None:
    """自然完成和实验截断具有不同边界并都记录实际结束时刻。"""
    config = load_scenario("scenarios/white-collar-12-weeks.json")[0].body
    person, activity = start_activity(
        agent(), StartActivityRequest(activity_type="sleep", planned_duration_seconds=60),
        10, origin("one"), config)
    completed = advance_activity(activity, person, 70, config)
    assert completed.activity.status == ActivityStatus.COMPLETED
    assert completed.activity.ended_at == 70
    person, activity = start_activity(
        completed.agent, StartActivityRequest(activity_type="wait", planned_duration_seconds=600),
        70, origin("two"), config)
    activity, person = truncate_activity(activity, person, 100)
    assert activity.status == ActivityStatus.TRUNCATED
    assert activity.boundary_type == "experiment_end" and activity.ended_at == 100
    assert activity.interruption_reason == "experiment_end"


def test_continue_activity_moves_feedback_strictly_forward_without_releasing_slot() -> None:
    """继续活动不改变当前时刻或槽位，并在最后一段直接等待完成。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    person, activity = start_activity(
        agent(), StartActivityRequest(activity_type="wait", planned_duration_seconds=2000),
        0, origin("continue"), scenario.body)
    activity = schedule_feedback(activity, 0, scenario.activity_feedback_seconds)
    advanced = advance_activity(activity, person, 900, scenario.body)
    request = ContinueActivityRequest(activity_id=activity.activity_id)
    person, activity = continue_activity(
        advanced.agent, advanced.activity, request, 900, scenario.activity_feedback_seconds)
    assert person.current_activity_id == activity.activity_id
    assert activity.next_interaction_at == 1800 and activity.last_settled_at == 900
    advanced = advance_activity(activity, person, 1800, scenario.body)
    _, activity = continue_activity(
        advanced.agent, advanced.activity, request, 1800, scenario.activity_feedback_seconds)
    assert activity.next_interaction_at is None and activity.planned_end_at == 2000
