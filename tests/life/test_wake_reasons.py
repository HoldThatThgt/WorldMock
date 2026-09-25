"""环境事件、承诺威胁与双重估间隔的精确边界合同。"""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from tests.life.conftest import advance_next, context, start_activity, wake_by_mail
from tests.life.test_day_plan import act, goal
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, ConcludeActivityRequest
from worldmock.life.rhythm import _has_commitments, engine_rhythm_inputs, next_decision_candidates
from worldmock.life.scenario import LifeScenario
from worldmock.life.schedule import (
    INTENT_KINDS, SLEEP_ALLOWED_REASONS, DayPlan, IntentKind, NextDecisionQuery, WakeReason,
    WakeSnapshot, evaluate_wake_reasons, next_decision_time,
)

NOW = 1000000
FROZEN = (
    "planned_end", "felt_band_change", "message_arrival", "delivery_arrival", "task_available",
    "task_due_soon", "work_review_result", "commitment_threatened", "reconsideration_interval",
    "location_hours_change", "person_arrived", "person_left", "activity_infeasible", "rejection_feedback",
    "discovery_result", "wake_from_sleep", "run_start", "progress_report_due", "no_active_activity",
    "plan_reminder_due",
)


def reasons(**values):
    return [reason.value for reason in evaluate_wake_reasons(WakeSnapshot(now=NOW, **values)).wake_reasons]


def test_frozen_wake_and_intent_names():
    assert tuple(item.value for item in WakeReason) == FROZEN
    assert tuple(get_args(IntentKind)) == INTENT_KINDS


def test_sleep_allowed_reasons_add_only_plan_reminder_due():
    """时间与能量规格第一章规则 6：睡眠允许集合只新增 plan_reminder_due，no_active_activity 不进入。"""
    assert SLEEP_ALLOWED_REASONS == {
        WakeReason.PLANNED_END, WakeReason.WAKE_FROM_SLEEP, WakeReason.RUN_START,
        WakeReason.PLAN_REMINDER_DUE,
    }
    assert WakeReason.NO_ACTIVE_ACTIVITY not in SLEEP_ALLOWED_REASONS


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
    ({"discovery_result_ready": True}, ["discovery_result"]),
    ({"wake_from_sleep": True}, ["wake_from_sleep"]),
    ({"run_start": True}, ["run_start"]),
    ({"no_active_activity": True}, ["no_active_activity"]),
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
    assert reasons(is_asleep=True, no_active_activity=True) == []
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
    item = ActivityState(activity_id="a", agent_id=1, location_id=agent.location_id,
        kind=ActivityKind.WAIT,
        status=ActivityStatus.PAUSED, started_at=now, planned_end_at=now + 600,
        last_settled_at=now, elapsed_seconds=0, planned_duration_seconds=600,
        origin_action_id="act", origin_decision_id="dec", origin_request_event_id="evt")
    state.activities["a"] = item
    assert _has_commitments(agent, inputs)
    state.activities["a"] = item.model_copy(update={"status": ActivityStatus.COMPLETED})
    assert not _has_commitments(agent, inputs)
    # 时间与能量规格第一章：进行中的等待不算在手承诺；等待加暂停活动、或等待之外的进行中活动仍算。
    state.activities["a"] = item.model_copy(update={"status": ActivityStatus.ACTIVE})
    assert not _has_commitments(agent, inputs)
    state.activities["b"] = item.model_copy(update={"activity_id": "b"})
    assert _has_commitments(agent, inputs)
    state.activities["b"] = item.model_copy(update={"activity_id": "b", "status": ActivityStatus.ACTIVE,
                                                    "kind": ActivityKind.WATCH})
    assert _has_commitments(agent, inputs)


def test_unread_inbox_message_yields_message_arrival_then_is_marked_read(engine_factory):
    """收件箱在唤醒判定之后才记为已读：未读消息在边界成立 message_arrival，判定后记为已读（时间与能量规格第一章「人物之间的消息」）。"""
    from worldmock.life.rhythm import apply_decision_rhythm
    from worldmock.life.social import Message

    engine = engine_factory()
    state = engine.state.model_copy(deep=True)
    state.agents[1].last_decision_time = state.current_time
    message = Message(message_id="message-unread", kind="direct", sender_id="agent:2", recipient_id="agent:1",
                      text="到家了吗", created_at=state.current_time)
    state.agents[1].inbox = {message.message_id: message}
    changed, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine, state))
    assert "message_arrival" in rhythms[1].wake_reasons
    assert "message-unread" in changed.agents[1].read_inbox_ids
    _, again = apply_decision_rhythm(engine_rhythm_inputs(engine, changed))
    assert "message_arrival" not in again[1].wake_reasons


def test_game_turn_due_offers_decision_on_an_otherwise_silent_boundary(engine_factory):
    """游戏保持逐回合反馈：交互边界到期即提供决策，不经唤醒条件枚举（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。"""
    from worldmock.life.rhythm import apply_decision_rhythm

    engine = engine_factory()
    state = engine._snapshot_decision_references(engine.state.model_copy(deep=True), [1])
    now = state.current_time
    game = ActivityState(activity_id="game", agent_id=1, location_id=state.agents[1].location_id,
                         kind=ActivityKind.PLAY_GAME, status=ActivityStatus.ACTIVE, started_at=now,
                         planned_end_at=now + 3600, last_settled_at=now, elapsed_seconds=0,
                         planned_duration_seconds=3600, next_interaction_at=now + 600,
                         origin_action_id="act", origin_decision_id="dec", origin_request_event_id="evt")
    state.activities["game"] = game
    _, quiet = apply_decision_rhythm(engine_rhythm_inputs(engine, state))
    assert not quiet[1].offered and quiet[1].wake_reasons == []
    state.activities["game"] = game.model_copy(update={"next_interaction_at": now})
    changed, due = apply_decision_rhythm(engine_rhythm_inputs(engine, state))
    assert due[1].offered and due[1].wake_reasons == [] and changed.agents[1].next_decision_at == now


def test_engine_schedules_exact_task_warning_and_appends_threat_reason(engine_factory):
    engine = engine_factory()
    state = engine.state.model_copy(deep=True)
    task = engine.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    state.current_time = due - 1901
    state.pending_events = []
    state.agents[1] = state.agents[1].model_copy(update={"visible_task_ids": {task.task_id},
        "body_settled_at": state.current_time,
        "next_decision_at": state.current_time, "day_plan": DayPlan(day_end=due + 10000, goals=())})
    from tests.life.test_revision_contract import _commit_staged
    _commit_staged(engine, state)
    started = act(engine, "warning-wait", "start_activity",
                  start_activity("等待", "自己"))
    assert started.status == "in_progress"
    assert engine.scheduling_state().next_event_at.timestamp() == due - 1800
    advance_next(engine)
    assert engine.state.current_time == due - 1800
    assert {"task_due_soon", "commitment_threatened"}.issubset(engine.state.agents[1].pending_wake_reasons)


@pytest.mark.parametrize("bound_to_location", [False, True])
def test_engine_schedules_closing_warning_only_for_local_task(engine_factory, bound_to_location):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tests.life.test_revision_contract import _commit_staged
    engine = engine_factory()
    state = engine.state.model_copy(deep=True)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(engine.scenario.timezone))
    closing_minute = local.hour * 60 + local.minute + 17
    closing = int(local.replace(second=0).timestamp()) + 17 * 60
    state.pending_events = []
    task = engine.scenario.tasks[0]
    location_id = task.required_location_id if bound_to_location else state.agents[1].location_id
    state.agents[1] = state.agents[1].model_copy(update={
        "location_id": location_id, "visible_task_ids": {task.task_id},
        "known_task_ids": {task.task_id},
        "day_plan": DayPlan(day_end=closing + 10000,
                            goals=[goal("工作", "去别处完成任务", registered=state.current_time)]),
    })
    state.opening_exceptions[location_id][local.date().isoformat()] = [(0, closing_minute)]
    _commit_staged(engine, state)
    result = act(engine, "closing-wait", "start_activity",
                 start_activity("等待", "自己"))
    assert result.status == "in_progress"
    if bound_to_location:
        assert engine.scheduling_state().next_event_at.timestamp() == closing - 900
        advance_next(engine)
        assert engine.state.current_time == closing - 900
        assert {"location_hours_change", "commitment_threatened"}.issubset(
            engine.state.agents[1].pending_wake_reasons)
    else:
        assert engine.scheduling_state().next_event_at.timestamp() != closing - 900
        while engine.state.current_time < closing:
            if engine.scheduling_state().ready_agent_ids:
                act(engine, "closing-continue", "start_activity", start_activity("等待", "自己"))
            else:
                advance_next(engine)
        assert "commitment_threatened" not in engine.state.agents[1].pending_wake_reasons


def _stage_task_warning(engine, task_ids: set[str], due: int, lead_seconds: int = 1901) -> None:
    """把真实引擎放到预警前并同步身体结算时钟。"""
    from worldmock.footprints.models import EventDraft

    state = engine.state.model_copy(deep=True)
    now = due - lead_seconds
    state.current_time = now
    state.pending_events = []
    agent = state.agents[1]
    state.agents[1] = agent.model_copy(update={
        "body_settled_at": now, "next_decision_at": now,
        "visible_task_ids": task_ids, "known_task_ids": task_ids,
        "day_plan": DayPlan(day_end=due + 10000, goals=()),
    }, deep=True)
    state.version += 1
    engine.store.commit(state.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system",
        source_kind="scenario", data={"kind": "test_warning_clock"})])
    engine.state = state


def _task_warning_ids(agent) -> set[str]:
    """读取已经交付的具体任务截止签名。"""
    return {row[1] for entry in agent.delivered_warning_signatures
            if (row := json.loads(entry))[0] == "task"}


def test_second_task_new_warning_is_not_hidden_by_first(engine_factory) -> None:
    """第二个任务进入预警窗口时，即使第一项仍未完成也再次提醒。"""
    engine = engine_factory()
    first, second = engine.scenario.tasks[:2]
    due = int(first.due_at.timestamp())
    engine.rules.tasks[second.task_id] = second.model_copy(update={
        "available_at": datetime.fromtimestamp(due - 3600, first.due_at.tzinfo),
        "due_at": datetime.fromtimestamp(due + 100, first.due_at.tzinfo)})
    _stage_task_warning(engine, {first.task_id, second.task_id}, due)
    started = act(engine, "two-task-wait", "start_activity",
                  start_activity("等待", "自己"))
    assert started.status == "in_progress"
    advance_next(engine)
    assert engine.state.current_time == due - 1800
    assert _task_warning_ids(engine.state.agents[1]) == {first.task_id}
    session = next(item for item in engine.state.sessions.values()
                   if started.activity_id in item.activity_ids)
    act(engine, "first-warning-ack", "conclude_activity",
        ConcludeActivityRequest(task=[session.session_name], summary=["结束等待。"]))
    act(engine, "first-warning-rest", "start_activity", start_activity("等待", "自己"))
    while engine.state.current_time < due - 1700:
        advance_next(engine)
    assert engine.state.current_time == due - 1700
    assert "task_due_soon" in engine.state.agents[1].pending_wake_reasons
    assert _task_warning_ids(engine.state.agents[1]) == {first.task_id, second.task_id}


def test_sleep_filtered_warning_is_delivered_after_waking(engine_factory) -> None:
    """睡眠区间经过预警时不标记交付，醒来且威胁仍在才标记。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    sleep_seconds = round(engine.state.agents[1].body.sleep_need_hours * 3600)
    _stage_task_warning(engine, {task.task_id}, due, sleep_seconds + 1750)
    started = act(engine, "sleep-warning", "start_activity",
                  start_activity("睡觉", "自己"))
    assert started.status == "in_progress"
    wake_by_mail(engine, offset=101)
    advance_next(engine)
    assert engine.state.activities[started.activity_id].status == ActivityStatus.ACTIVE
    assert _task_warning_ids(engine.state.agents[1]) == set()
    while engine.state.activities[started.activity_id].status == ActivityStatus.ACTIVE:
        advance_next(engine)
    assert engine.state.current_time == due - 1750
    assert engine.state.activities[started.activity_id].status == ActivityStatus.COMPLETED
    assert "task_due_soon" in engine.state.agents[1].pending_wake_reasons
    assert _task_warning_ids(engine.state.agents[1]) == {task.task_id}


def test_checkpoint_restore_does_not_redeliver_same_task_warning(engine_factory) -> None:
    """共同检查点恢复后，持续成立的同一任务威胁不会再次唤醒。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    _stage_task_warning(engine, {task.task_id}, due)
    started = act(engine, "checkpoint-wait", "start_activity",
                  start_activity("等待", "自己"))
    advance_next(engine)
    assert _task_warning_ids(engine.state.agents[1]) == {task.task_id}
    session = next(item for item in engine.state.sessions.values()
                   if started.activity_id in item.activity_ids)
    act(engine, "checkpoint-ack", "conclude_activity",
        ConcludeActivityRequest(task=[session.session_name], summary=["结束等待。"]))
    act(engine, "checkpoint-rest", "start_activity", start_activity("等待", "自己"))
    reference = engine.store.assets.put_json({"agent_id": 1}, {"kind": "warning_restore_test"})
    checkpoint = engine.checkpoint({"agent:1": reference})
    assert checkpoint.checkpoint_id
    run_dir = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    try:
        assert _task_warning_ids(restored.state.agents[1]) == {task.task_id}
        wake_by_mail(restored, offset=60)
        while not restored.scheduling_state().ready_agent_ids:
            advance_next(restored)
        reasons = restored.state.agents[1].pending_wake_reasons
        assert "message_arrival" in reasons
        assert "task_due_soon" not in reasons
        assert "commitment_threatened" not in reasons
        assert _task_warning_ids(restored.state.agents[1]) == {task.task_id}
    finally:
        restored.close()


def _warning_step(engine, state, **changes):
    """用引擎实际装配输入测试同一边界的威胁状态转换，不重复结算身体。"""
    from worldmock.life.rhythm import apply_decision_rhythm
    inputs = replace(engine_rhythm_inputs(engine, state), interval_seconds=0, **changes)
    changed, verdicts = apply_decision_rhythm(inputs)
    return changed, verdicts[1].wake_reasons


def test_changed_task_deadline_warns_again_and_completed_task_clears_signature(engine_factory):
    """任务身份相同时，真实截止改变仍是新威胁；完成任务清除旧交付状态。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    state = engine.state.model_copy(deep=True)
    state.current_time = due - 1000
    agent = state.agents[1]
    agent.body_settled_at = state.current_time
    agent.last_decision_time = state.current_time
    agent.visible_task_ids = agent.known_task_ids = {task.task_id}
    due_map = {task.task_id: due}
    state, reasons = _warning_step(engine, state, task_due=due_map)
    assert "task_due_soon" in reasons and "commitment_threatened" in reasons
    original = set(state.agents[1].delivered_warning_signatures)
    state, reasons = _warning_step(engine, state, task_due=due_map)
    assert "task_due_soon" not in reasons and "commitment_threatened" not in reasons
    state, reasons = _warning_step(engine, state, task_due={task.task_id: due + 60})
    assert "task_due_soon" in reasons and "commitment_threatened" in reasons
    assert original.isdisjoint(state.agents[1].delivered_warning_signatures)
    signature = next(iter(state.agents[1].delivered_warning_signatures))
    assert json.loads(signature) == ["task", task.task_id, due + 60]
    state.paid_task_ids.add(task.task_id)
    state, reasons = _warning_step(engine, state, task_due={task.task_id: due + 60})
    assert not state.agents[1].delivered_warning_signatures
    assert "commitment_threatened" not in reasons


def test_changed_closing_time_and_new_local_commitment_warn_independently(engine_factory):
    """同地点闭店时刻改变、同一闭店新增受威胁任务分别触发新提醒。"""
    from zoneinfo import ZoneInfo
    engine = engine_factory()
    first = engine.scenario.tasks[0]
    second = next(task for task in engine.scenario.tasks[1:]
                  if task.required_location_id == first.required_location_id)
    state = engine.state.model_copy(deep=True)
    agent = state.agents[1]
    agent.location_id = first.required_location_id
    agent.last_decision_time = state.current_time
    agent.visible_task_ids = agent.known_task_ids = {first.task_id}
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(engine.scenario.timezone))
    close_minute = local.hour * 60 + local.minute + 10
    state.opening_exceptions[agent.location_id][local.date().isoformat()] = [(0, close_minute)]
    state, reasons = _warning_step(engine, state)
    assert "location_hours_change" in reasons and "commitment_threatened" in reasons
    old = set(state.agents[1].delivered_warning_signatures)
    state, reasons = _warning_step(engine, state)
    assert "location_hours_change" not in reasons and "commitment_threatened" not in reasons
    state.opening_exceptions[agent.location_id][local.date().isoformat()] = [(0, close_minute + 1)]
    state, reasons = _warning_step(engine, state)
    assert "location_hours_change" in reasons and "commitment_threatened" in reasons
    assert old.isdisjoint(state.agents[1].delivered_warning_signatures)
    state.agents[1].visible_task_ids.add(second.task_id)
    state.agents[1].known_task_ids.add(second.task_id)
    state, reasons = _warning_step(engine, state)
    assert "location_hours_change" in reasons and "commitment_threatened" in reasons
    closing = [json.loads(value) for value in state.agents[1].delivered_warning_signatures
               if json.loads(value)[0] == "closing"]
    assert len(closing) == 2
    assert {row[3].split(":")[1] for row in closing} == {first.task_id, second.task_id}
    state.paid_task_ids.update({first.task_id, second.task_id})
    state, reasons = _warning_step(engine, state)
    assert not state.agents[1].delivered_warning_signatures
    assert "commitment_threatened" not in reasons


@pytest.mark.parametrize("resolution", ["energy", "paused", "completed"])
def test_infeasibility_warns_once_clears_and_can_reappear(engine_factory, resolution):
    """持续同因不重报；能量恢复、暂停或终态解除威胁，重新不可行时重新交付。"""
    engine = engine_factory()
    started = act(engine, "infeasible-watch", "start_activity",
                  start_activity("观看", "内容：Sintel"))
    assert started.status == "in_progress"
    state = engine.state.model_copy(deep=True)
    state.agents[1].body.energy_kcal = 0
    state.agents[1].visible_task_ids = set()
    state.agents[1].last_decision_time = state.current_time
    state, reasons = _warning_step(engine, state)
    expected = {f"infeasible:{started.activity_id}:insufficient_energy"}
    assert state.agents[1].delivered_warning_signatures == expected
    assert "activity_infeasible" in reasons and "commitment_threatened" in reasons
    state.activities[started.activity_id].elapsed_seconds += 1
    state, reasons = _warning_step(engine, state)
    assert "activity_infeasible" not in reasons and "commitment_threatened" not in reasons
    assert state.agents[1].delivered_warning_signatures == expected
    activity = state.activities[started.activity_id]
    if resolution == "energy":
        state.agents[1].body.energy_kcal = 2000
    else:
        activity.status = ActivityStatus(resolution)
    state, reasons = _warning_step(engine, state)
    assert not state.agents[1].delivered_warning_signatures
    assert "activity_infeasible" not in reasons and "commitment_threatened" not in reasons
    if resolution != "completed":
        state.activities[started.activity_id].status = ActivityStatus.ACTIVE
        state.agents[1].body.energy_kcal = 0
        state, reasons = _warning_step(engine, state)
        assert state.agents[1].delivered_warning_signatures == expected
        assert "activity_infeasible" in reasons and "commitment_threatened" in reasons


def test_progress_report_due_is_in_enum_and_not_in_sleep_allowed():
    """播报节律唤醒原因已登记枚举且不在睡眠白名单。"""
    assert WakeReason.PROGRESS_REPORT_DUE in WakeReason
    assert WakeReason.PROGRESS_REPORT_DUE.value == "progress_report_due"
    assert WakeReason.PROGRESS_REPORT_DUE not in SLEEP_ALLOWED_REASONS


def test_progress_report_due_triggers_decision():
    """progress_report_due=True 产生唤醒。"""
    assert reasons(progress_report_due=True) == ["progress_report_due"]


def test_progress_report_due_is_filtered_during_sleep():
    """睡眠期间播报节律被过滤，不提供决策。"""
    verdict = evaluate_wake_reasons(WakeSnapshot(now=NOW, is_asleep=True, progress_report_due=True))
    assert not verdict.decision_offered
    assert verdict.wake_reasons == []


def test_scenario_progress_report_interval_default(engine_factory):
    """场景参数 progress_report_interval_seconds 缺省 1800。"""
    from worldmock.life.scenario import LifeScenario

    document = engine_factory().semantics.document.model_dump(mode="json")
    assert LifeScenario.model_validate(document).progress_report_interval_seconds == 1800


@pytest.mark.parametrize("value", [0, -1, -100])
def test_scenario_rejects_non_positive_progress_report_interval(engine_factory, value):
    """progress_report_interval_seconds 非正值被拒绝。"""
    from pydantic import ValidationError
    from worldmock.life.scenario import LifeScenario

    document = engine_factory().semantics.document.model_dump(mode="json")
    document["progress_report_interval_seconds"] = value
    with pytest.raises(ValidationError):
        LifeScenario.model_validate(document)


def test_next_progress_report_at_schedules_candidate():
    """next_progress_report_at 已设时，next_decision_time 以其为候选。"""
    report_at = NOW + 900
    query = NextDecisionQuery(now=NOW, next_progress_report_at=report_at)
    assert next_decision_time(query) == report_at


def test_next_progress_report_at_past_is_excluded():
    """过期的 next_progress_report_at 不成为候选。"""
    query = NextDecisionQuery(now=NOW, next_progress_report_at=NOW - 1)
    assert next_decision_time(query) is None


def test_next_progress_report_at_competes_with_other_candidates():
    """播报候选与重估间隔共同竞争，取最早。"""
    query = NextDecisionQuery(now=NOW, last_decision_time=NOW,
                              reconsideration_interval_seconds=600,
                              idle_reconsideration_interval_seconds=1200,
                              has_commitments=False,
                              next_progress_report_at=NOW + 300)
    assert next_decision_time(query) == NOW + 300
