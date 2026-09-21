"""环境事件、承诺威胁与双重估间隔的精确边界合同。"""

from dataclasses import replace
from typing import get_args

import pytest
from pydantic import ValidationError

from tests.life.conftest import advance_next, context
from tests.life.test_day_plan import act, goal
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, StartBasicActivityRequest
from worldmock.life.rhythm import _has_commitments, engine_rhythm_inputs, next_decision_candidates
from worldmock.life.scenario import LifeScenario
from worldmock.life.schedule import (
    INTENT_KINDS, DayPlan, IntentKind, NextDecisionQuery, WakeReason, WakeSnapshot,
    evaluate_wake_reasons, next_decision_time,
)

NOW = 1000000
FROZEN = (
    "planned_end", "felt_band_change", "message_arrival", "delivery_arrival", "task_available",
    "task_due_soon", "work_review_result", "commitment_threatened", "reconsideration_interval",
    "location_hours_change", "person_arrived", "person_left", "activity_infeasible", "rejection_feedback",
    "discovery_result", "wake_from_sleep", "run_start",
)


def reasons(**values):
    return [reason.value for reason in evaluate_wake_reasons(WakeSnapshot(now=NOW, **values)).wake_reasons]


def test_frozen_wake_and_intent_names():
    assert tuple(item.value for item in WakeReason) == FROZEN
    assert tuple(get_args(IntentKind)) == INTENT_KINDS


@pytest.mark.parametrize("fields,expected", [
    ({"planned_end": NOW}, ["planned_end"]),
    ({"felt_bands_now": {"appetite": 1}}, ["felt_band_change"]),
    ({"message_arrived": True}, ["message_arrival"]),
    ({"delivery_arrived": True}, ["delivery_arrival"]),
    ({"task_newly_visible": True}, ["task_available"]),
    ({"next_task_due_at": NOW + 1800}, ["task_due_soon", "commitment_threatened"]),
    ({"work_review_returned": True}, ["work_review_result"]),
    ({"has_commitments": True, "location_closing_at": NOW + 900},
     ["commitment_threatened", "location_hours_change"]),
    ({"location_just_opened": True}, ["location_hours_change"]),
    ({"people_present_now": {"person"}}, ["person_arrived"]),
    ({"people_present_before": {"person"}}, ["person_left"]),
    ({"activity_infeasible": True}, ["commitment_threatened", "activity_infeasible"]),
    ({"consecutive_rejections": 1}, ["rejection_feedback"]),
    ({"discovery_result_ready": True}, ["discovery_result"]),
    ({"wake_from_sleep": True}, ["wake_from_sleep"]),
    ({"run_start": True}, ["run_start"]),
])
def test_concrete_wake_reasons_are_preserved(fields, expected):
    assert reasons(**fields) == expected


@pytest.mark.parametrize("fields", [
    {}, {"planned_end": NOW + 1}, {"next_task_due_at": NOW + 1801},
    {"next_task_due_at": NOW}, {"has_commitments": True, "location_closing_at": NOW + 901},
    {"has_commitments": True, "location_closing_at": NOW}, {"location_closing_at": NOW + 900},
    {"last_decision_time": NOW - 10799},
    {"has_commitments": True, "last_decision_time": NOW - 5399},
])
def test_before_warning_and_reconsideration_boundaries_is_silent(fields):
    verdict = evaluate_wake_reasons(WakeSnapshot(now=NOW, **fields))
    assert not verdict.decision_offered and verdict.wake_reasons == []


@pytest.mark.parametrize("committed,interval", [(True, 5400), (False, 10800)])
def test_candidate_and_wake_use_same_interval(committed, interval):
    query = NextDecisionQuery(now=NOW, last_decision_time=NOW, has_commitments=committed)
    assert next_decision_time(query) == NOW + interval
    assert reasons(last_decision_time=NOW - interval, has_commitments=committed) == ["reconsideration_interval"]


def test_custom_intervals_and_earlier_events_remain_effective():
    query = NextDecisionQuery(now=NOW, last_decision_time=NOW, reconsideration_interval_seconds=600,
                              idle_reconsideration_interval_seconds=1200, has_commitments=False)
    assert next_decision_time(query) == NOW + 1200
    assert next_decision_time(query.model_copy(update={"has_commitments": True})) == NOW + 600
    assert next_decision_time(query.model_copy(update={"scheduled_arrival_times": (NOW + 10,)})) == NOW + 10
    assert next_decision_time(query.model_copy(update={"felt_band_crossing_times": (NOW + 20,)})) == NOW + 20
    assert next_decision_time(query.model_copy(update={"location_hours_change_times": (NOW + 30,)})) == NOW + 30


@pytest.mark.parametrize("field,deadline,expected", [
    ("next_task_due_at", NOW + 1901, NOW + 101),
    ("location_closing_at", NOW + 1001, NOW + 101),
])
def test_warning_candidates_are_exact_not_feedback_aligned(field, deadline, expected):
    query = NextDecisionQuery(now=NOW, last_decision_time=NOW, has_commitments=True, **{field: deadline})
    assert next_decision_time(query) == expected
    snapshot = WakeSnapshot(now=expected, has_commitments=True, **{field: deadline})
    assert WakeReason.COMMITMENT_THREATENED in evaluate_wake_reasons(snapshot).wake_reasons


def test_sleep_accepts_only_existing_sleep_reasons():
    snapshot = WakeSnapshot(now=NOW, is_asleep=True, has_commitments=True,
                            last_decision_time=NOW - 20000, next_task_due_at=NOW + 100,
                            location_closing_at=NOW + 100, message_arrived=True, activity_infeasible=True)
    assert not evaluate_wake_reasons(snapshot).decision_offered
    assert reasons(is_asleep=True, planned_end=NOW, wake_from_sleep=True, run_start=True) == [
        "planned_end", "wake_from_sleep", "run_start"]
    query = NextDecisionQuery(now=NOW, is_asleep=True, planned_end=NOW + 20000,
                              last_decision_time=NOW, has_commitments=True)
    assert next_decision_time(query) == NOW + 20000
    assert next_decision_time(query.model_copy(update={"next_task_due_at": NOW + 1900,
        "location_closing_at": NOW + 1000})) == NOW + 20000
    assert next_decision_time(NextDecisionQuery(now=NOW)) is None


def test_scenario_rejects_idle_interval_shorter_than_regular(engine_factory):
    document = engine_factory().semantics.document.model_dump(mode="json")
    assert LifeScenario.model_validate(document).idle_reconsideration_interval_seconds == 10800
    document["idle_reconsideration_interval_seconds"] = document["reconsideration_interval_seconds"] - 1
    with pytest.raises(ValidationError, match="must not be shorter"):
        LifeScenario.model_validate(document)


def test_commitments_exclude_messages_history_and_completed_activities(engine_factory):
    engine = engine_factory()
    state = engine.state.model_copy(deep=True)
    now = state.current_time
    agent = state.agents[1].model_copy(update={"visible_task_ids": set(), "day_plan": None})
    inputs = engine_rhythm_inputs(engine, state)
    assert not _has_commitments(agent, inputs)
    pending = DayPlan(day_end=now + 1000, goals=[goal("工作", registered=now)])
    assert _has_commitments(agent.model_copy(update={"day_plan": pending}), inputs)
    history = pending.model_copy(update={"goals": (goal("工作", registered=now - 60,
                                         progressed_at=now, progress_activity_id="old"),)})
    assert not _has_commitments(agent.model_copy(update={"day_plan": history}), inputs)
    assert not _has_commitments(agent.model_copy(update={"day_plan": pending.model_copy(
        update={"day_end": now})}), inputs)
    visible = agent.model_copy(update={"visible_task_ids": {"task"}})
    assert _has_commitments(visible, inputs)
    state.paid_task_ids.add("task")
    assert not _has_commitments(visible, inputs)
    item = ActivityState(activity_id="a", agent_id=1, kind=ActivityKind.WAIT,
        status=ActivityStatus.PAUSED, started_at=now, planned_end_at=now + 600,
        last_settled_at=now, elapsed_seconds=0, planned_duration_seconds=600,
        origin_action_id="act", origin_decision_id="dec", origin_request_event_id="evt")
    state.activities["a"] = item
    assert _has_commitments(agent, inputs)
    state.activities["a"] = item.model_copy(update={"status": ActivityStatus.COMPLETED})
    assert not _has_commitments(agent, inputs)


def test_engine_schedules_exact_task_warning_and_appends_threat_reason(engine_factory):
    engine = engine_factory()
    state = engine.state.model_copy(deep=True)
    task = engine.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    state.current_time = due - 1901
    state.pending_events = []
    state.agents[1] = state.agents[1].model_copy(update={"visible_task_ids": {task.task_id},
        "next_decision_at": state.current_time, "day_plan": DayPlan(day_end=due + 10000, goals=())})
    from tests.life.test_revision_contract import _commit_staged
    _commit_staged(engine, state)
    started = act(engine, "warning-wait", "start_basic_activity",
                  StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
    assert started.status == "in_progress"
    assert engine.scheduling_state().next_event_at.timestamp() == due - 1800
    advance_next(engine)
    assert engine.state.current_time == due - 1800
    assert {"task_due_soon", "commitment_threatened"}.issubset(engine.state.agents[1].pending_wake_reasons)


def test_engine_schedules_exact_closing_warning_with_commitment(engine_factory):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tests.life.test_revision_contract import _commit_staged
    engine = engine_factory()
    state = engine.state.model_copy(deep=True)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(engine.scenario.timezone))
    closing_minute = local.hour * 60 + local.minute + 17
    closing = int(local.replace(second=0).timestamp()) + 17 * 60
    state.pending_events = []
    state.agents[1] = state.agents[1].model_copy(update={"visible_task_ids": set(),
        "day_plan": DayPlan(day_end=closing + 10000, goals=())})
    location_id = state.agents[1].location_id
    state.opening_exceptions[location_id][local.date().isoformat()] = [(0, closing_minute)]
    _commit_staged(engine, state)
    result = act(engine, "closing-wait", "start_basic_activity",
                 StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
    assert result.status == "in_progress"
    assert engine.scheduling_state().next_event_at.timestamp() == closing - 900
    advance_next(engine)
    assert engine.state.current_time == closing - 900
    assert {"location_hours_change", "commitment_threatened"}.issubset(
        engine.state.agents[1].pending_wake_reasons)
