"""环境事件、承诺威胁与双重估间隔的精确边界合同。"""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 stage_day_plan, start_activity, wake_by_mail, pause_request, report_request)
from tests.life.test_commerce import _stage_open
from tests.life.test_day_plan import act, goal
from tests.life.test_same_moment_decisions import _finish, _observed_act, _supply_order
from tests.life.test_scheduling import _custom_engine
from worldmock.life import schedule as schedule_module
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (ActivityKind, ActivityState, ActivityStatus, PlanGoalInput, ReviseDayPlanRequest,
                                   active_activities)
from worldmock.life.rhythm import (
    _has_commitments, _next_progress_report_at, _progress_report_due, engine_rhythm_inputs, next_decision_candidates,
)
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
    "plan_reminder_due", "errand_reminder_due", "shift_start", "shift_end", "break_start", "break_end",
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
                            location_closing_at=NOW + 100, message_arrived=True)
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
    document = engine_factory().context.semantics.document.model_dump(mode="json")
    assert LifeScenario.model_validate(document).idle_reconsideration_interval_seconds == 10800
    document["idle_reconsideration_interval_seconds"] = document["reconsideration_interval_seconds"] - 1
    with pytest.raises(ValidationError, match="must not be shorter"):
        LifeScenario.model_validate(document)


def test_commitments_exclude_messages_history_and_completed_activities(engine_factory):
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    now = state.current_time
    agent = state.agents[1].model_copy(update={"visible_task_ids": set(), "day_plan": None})
    inputs = engine_rhythm_inputs(engine.context, state)
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
    state = engine.context.state.model_copy(deep=True)
    state.agents[1].last_decision_time = state.current_time
    message = Message(message_id="message-unread", kind="direct", sender_id="agent:2", recipient_id="agent:1",
                      text="到家了吗", created_at=state.current_time)
    state.agents[1].inbox = {message.message_id: message}
    changed, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine.context, state))
    assert "message_arrival" in rhythms[1].wake_reasons
    assert "message-unread" in changed.agents[1].read_inbox_ids
    _, again = apply_decision_rhythm(engine_rhythm_inputs(engine.context, changed))
    assert "message_arrival" not in again[1].wake_reasons


def test_game_turn_due_offers_decision_on_an_otherwise_silent_boundary(engine_factory):
    """游戏保持逐回合反馈：交互边界到期即提供决策，不经唤醒条件枚举（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」）。"""
    from worldmock.life.rhythm import apply_decision_rhythm

    engine = engine_factory()
    state = engine.observations._snapshot_decision_references(engine.context.state.model_copy(deep=True), [1])
    now = state.current_time
    game = ActivityState(activity_id="game", agent_id=1, location_id=state.agents[1].location_id,
                         kind=ActivityKind.PLAY_GAME, status=ActivityStatus.ACTIVE, started_at=now,
                         planned_end_at=now + 3600, last_settled_at=now, elapsed_seconds=0,
                         planned_duration_seconds=3600, next_interaction_at=now + 600,
                         origin_action_id="act", origin_decision_id="dec", origin_request_event_id="evt")
    state.activities["game"] = game
    _, quiet = apply_decision_rhythm(engine_rhythm_inputs(engine.context, state))
    assert not quiet[1].offered and quiet[1].wake_reasons == []
    state.activities["game"] = game.model_copy(update={"next_interaction_at": now})
    changed, due = apply_decision_rhythm(engine_rhythm_inputs(engine.context, state))
    assert due[1].offered and due[1].wake_reasons == [] and changed.agents[1].next_decision_at == now


def test_engine_schedules_exact_task_warning_and_appends_threat_reason(engine_factory):
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    task = engine.context.scenario.tasks[0]
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
    assert engine.context.state.current_time == due - 1800
    assert {"task_due_soon", "commitment_threatened"}.issubset(engine.context.state.agents[1].pending_wake_reasons)


@pytest.mark.parametrize("bound_to_location", [False, True])
def test_engine_schedules_closing_warning_only_for_local_task(engine_factory, bound_to_location):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tests.life.test_revision_contract import _commit_staged
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(engine.context.scenario.timezone))
    closing_minute = local.hour * 60 + local.minute + 17
    closing = int(local.replace(second=0).timestamp()) + 17 * 60
    state.pending_events = []
    task = engine.context.scenario.tasks[0]
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
        assert engine.context.state.current_time == closing - 900
        assert {"location_hours_change", "commitment_threatened"}.issubset(
            engine.context.state.agents[1].pending_wake_reasons)
    else:
        assert engine.scheduling_state().next_event_at.timestamp() != closing - 900
        step = 0
        while engine.context.state.current_time < closing:
            if engine.scheduling_state().ready_agent_ids:
                step += 1
                _keep_waiting(engine, f"closing-continue-{step}")
            else:
                advance_next(engine)
        assert "commitment_threatened" not in engine.context.state.agents[1].pending_wake_reasons


def _keep_waiting(engine, name: str) -> None:
    """就绪时先给刚结束的活动收尾（第七章「活动一结束就要结束它」），有进行中活动就播报，否则开始下一段等待。"""
    waiting = pending_conclusions(engine.context.state, 1)
    running = active_activities(engine.context.state, 1)
    if waiting:
        request = ("conclude_activity", conclude_request(*(activity_name(engine, item.activity_id)
                                                           for item in waiting)))
    elif running:
        request = ("report_status", report_request(
            *(activity_name(engine, item.activity_id) for item in running), summary="继续。"))
    else:
        request = ("start_activity", start_activity("等待", "自己"))
    assert act(engine, name, *request).status in {"completed", "in_progress"}


def _stage_task_warning(engine, task_ids: set[str], due: int, lead_seconds: int = 1901) -> None:
    """把真实引擎放到预警前并同步身体结算时钟。"""
    from worldmock.footprints.models import EventDraft

    state = engine.context.state.model_copy(deep=True)
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
    engine.context.store.commit(state.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system",
        source_kind="scenario", data={"kind": "test_warning_clock"})])
    engine.context.state = state


def _task_warning_ids(agent) -> set[str]:
    """读取已经交付的具体任务截止签名。"""
    return {row[1] for entry in agent.delivered_warning_signatures
            if (row := json.loads(entry))[0] == "task"}


def test_second_task_new_warning_is_not_hidden_by_first(engine_factory) -> None:
    """第二个任务进入预警窗口时，即使第一项仍未完成也再次提醒。"""
    engine = engine_factory()
    first, second = engine.context.scenario.tasks[:2]
    due = int(first.due_at.timestamp())
    engine.context.rules.tasks[second.task_id] = second.model_copy(update={
        "available_at": datetime.fromtimestamp(due - 3600, first.due_at.tzinfo),
        "due_at": datetime.fromtimestamp(due + 100, first.due_at.tzinfo)})
    _stage_task_warning(engine, {first.task_id, second.task_id}, due)
    started = act(engine, "two-task-wait", "start_activity",
                  start_activity("等待", "自己"))
    assert started.status == "in_progress"
    advance_next(engine)
    assert engine.context.state.current_time == due - 1800
    assert _task_warning_ids(engine.context.state.agents[1]) == {first.task_id}
    ended = act(engine, "first-warning-ack", "conclude_activity",
                conclude_request(activity_name(engine, started.activity_id), summary="结束等待。"))
    assert ended.status == "completed"
    assert act(engine, "first-warning-rest", "start_activity", start_activity("等待", "自己")).status == "in_progress"
    while engine.context.state.current_time < due - 1700:
        advance_next(engine)
    assert engine.context.state.current_time == due - 1700
    assert "task_due_soon" in engine.context.state.agents[1].pending_wake_reasons
    assert _task_warning_ids(engine.context.state.agents[1]) == {first.task_id, second.task_id}


def test_sleep_filtered_warning_is_delivered_after_waking(engine_factory) -> None:
    """睡眠区间经过预警时不标记交付，醒来且威胁仍在才标记。"""
    engine = engine_factory()
    task = engine.context.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    sleep_seconds = round(engine.context.state.agents[1].body.sleep_need_hours * 3600)
    _stage_task_warning(engine, {task.task_id}, due, sleep_seconds + 1750)
    started = act(engine, "sleep-warning", "start_activity",
                  start_activity("睡觉", "自己"))
    assert started.status == "in_progress"
    wake_by_mail(engine, offset=101)
    advance_next(engine)
    assert engine.context.state.activities[started.activity_id].status == ActivityStatus.ACTIVE
    assert _task_warning_ids(engine.context.state.agents[1]) == set()
    while engine.context.state.activities[started.activity_id].status == ActivityStatus.ACTIVE:
        advance_next(engine)
    assert engine.context.state.current_time == due - 1750
    assert engine.context.state.activities[started.activity_id].status == ActivityStatus.COMPLETED
    assert "task_due_soon" in engine.context.state.agents[1].pending_wake_reasons
    assert _task_warning_ids(engine.context.state.agents[1]) == {task.task_id}


def test_checkpoint_restore_does_not_redeliver_same_task_warning(engine_factory) -> None:
    """共同检查点恢复后，持续成立的同一任务威胁不会再次唤醒。"""
    engine = engine_factory()
    task = engine.context.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    _stage_task_warning(engine, {task.task_id}, due)
    started = act(engine, "checkpoint-wait", "start_activity",
                  start_activity("等待", "自己"))
    advance_next(engine)
    assert _task_warning_ids(engine.context.state.agents[1]) == {task.task_id}
    ended = act(engine, "checkpoint-ack", "conclude_activity",
                conclude_request(activity_name(engine, started.activity_id), summary="结束等待。"))
    assert ended.status == "completed"
    assert act(engine, "checkpoint-rest", "start_activity", start_activity("等待", "自己")).status == "in_progress"
    reference = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "warning_restore_test"})
    checkpoint = engine.checkpoint({"agent:1": reference})
    assert checkpoint.checkpoint_id
    run_dir = engine.context.store.run_dir
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    try:
        assert _task_warning_ids(restored.context.state.agents[1]) == {task.task_id}
        wake_by_mail(restored, offset=60)
        while not restored.scheduling_state().ready_agent_ids:
            advance_next(restored)
        reasons = restored.context.state.agents[1].pending_wake_reasons
        assert "message_arrival" in reasons
        assert "task_due_soon" not in reasons
        assert "commitment_threatened" not in reasons
        assert _task_warning_ids(restored.context.state.agents[1]) == {task.task_id}
    finally:
        restored.close()


def _warning_step(engine, state, **changes):
    """用引擎实际装配输入测试同一边界的威胁状态转换，不重复结算身体。"""
    from worldmock.life.rhythm import apply_decision_rhythm
    inputs = replace(engine_rhythm_inputs(engine.context, state), interval_seconds=0, **changes)
    changed, verdicts = apply_decision_rhythm(inputs)
    return changed, verdicts[1].wake_reasons


def test_changed_task_deadline_warns_again_and_completed_task_clears_signature(engine_factory):
    """任务身份相同时，真实截止改变仍是新威胁；完成任务清除旧交付状态。"""
    engine = engine_factory()
    task = engine.context.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    state = engine.context.state.model_copy(deep=True)
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
    first = engine.context.scenario.tasks[0]
    second = next(task for task in engine.context.scenario.tasks[1:]
                  if task.required_location_id == first.required_location_id)
    state = engine.context.state.model_copy(deep=True)
    agent = state.agents[1]
    agent.location_id = first.required_location_id
    agent.last_decision_time = state.current_time
    agent.visible_task_ids = agent.known_task_ids = {first.task_id}
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(engine.context.scenario.timezone))
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


def test_activity_infeasible_has_no_source_and_threat_only_from_deadline_or_closing():
    """activity_infeasible 保留名称但没有来源：快照不再有该字段，全部标志齐开也不产生它。"""
    assert WakeReason.ACTIVITY_INFEASIBLE.value == "activity_infeasible"
    assert "activity_infeasible" not in WakeSnapshot.model_fields
    everything = reasons(planned_end=NOW, felt_bands_now={"appetite": 1}, message_arrived=True,
                         delivery_arrived=True, task_newly_visible=True, work_review_returned=True,
                         location_just_opened=True, people_present_now={"person"},
                         discovery_result_ready=True, wake_from_sleep=True, run_start=True,
                         progress_report_due=True, no_active_activity=True)
    assert "activity_infeasible" not in everything
    assert "commitment_threatened" not in everything
    assert "commitment_threatened" in reasons(next_task_due_at=NOW + 1800)
    assert "commitment_threatened" in reasons(has_commitments=True, location_closing_at=NOW + 900)


def test_zero_energy_during_activity_raises_neither_infeasible_nor_threat(engine_factory):
    """能量为 0 时进行中的观看照常继续：不产生不可行预警，也不构成承诺威胁。"""
    engine = engine_factory()
    started = act(engine, "zero-energy-watch", "start_activity",
                  start_activity("接着看", "内容：Sintel"))
    assert started.status == "in_progress"
    state = engine.context.state.model_copy(deep=True)
    state.agents[1].body.energy_kcal = 0
    state.agents[1].visible_task_ids = set()
    state.agents[1].last_decision_time = state.current_time
    for _ in range(2):
        state, reasons = _warning_step(engine, state)
        assert "activity_infeasible" not in reasons and "commitment_threatened" not in reasons
        assert not state.agents[1].delivered_warning_signatures
        assert state.activities[started.activity_id].status == ActivityStatus.ACTIVE
        state.activities[started.activity_id].elapsed_seconds += 1


def _assert_rhythm_scheduled(engine) -> None:
    """@brief 有进行中活动时动作成功之后：下一次决策取决策节律的候选时刻（第一章规则 4、7），不另排下一秒，
    也不留待处理的唤醒原因。"""
    agent, now = engine.context.state.agents[1], engine.context.state.current_time
    assert agent.pending_wake_reasons == [] and agent.next_decision_at > now + 1
    assert agent.next_decision_at == next_decision_candidates(
        agent.model_copy(update={"last_decision_time": now}), engine_rhythm_inputs(engine.context, engine.context.state))


def _start_browse(engine, name: str) -> tuple[dict, str]:
    """@brief 开始一次商品目录浏览（浏览是占名额的活动），返回该轮观察与活动身份。"""
    observed, result = _observed_act(engine, name, "start_activity", start_activity("浏览", "商品目录"))
    assert result.status == "in_progress", result.error_code
    _assert_rhythm_scheduled(engine)
    return observed, result.activity_id


def _conclude(engine, name: str, activity_id: str) -> dict:
    """@brief 一轮只结束点名的活动，返回该轮观察。"""
    observed, result = _observed_act(engine, name, "conclude_activity",
                                     conclude_request(activity_name(engine, activity_id)))
    assert result.status == "completed", result.error_code
    return observed


def _pinned(engine, observed: dict, browse_id: str) -> list[str]:
    """@brief 浏览结果回灌到观察：该次浏览返回的供给全部呈现，且排在未经浏览返回的供给之前（置顶）；返回置顶的前缀。

    直驱引擎没有宿主请求推进进度层截止点，更早的浏览结果仍在近期结果中，一并置顶。"""
    browsed = {row["supply_id"] for item in engine.context.state.activities.values() if item.kind == ActivityKind.BROWSE
               for row in item.data.get("revealed", {}).get("items", [])}
    order = _supply_order(observed)
    head = order[:next((index for index, supply_id in enumerate(order) if supply_id not in browsed), len(order))]
    returned = {row["supply_id"] for row in engine.context.state.activities[browse_id].data["revealed"]["items"]}
    assert returned and returned <= set(head)
    return head


def _browse_alone_to_its_end(engine) -> str:
    """@brief 独自浏览到计划结束：结束边界以 planned_end 唤醒并置顶结果；收尾后同一秒以 no_active_activity 再决策，
    该轮观察仍置顶同一批结果，并在该轮再开始一次浏览，返回其活动身份。"""
    _, browse_id = _start_browse(engine, "alone")
    _finish(engine, browse_id, "alone")
    assert engine.context.state.current_time == engine.context.state.activities[browse_id].planned_end_at
    ended = _conclude(engine, "alone-end", browse_id)
    assert ended["wake_reasons"] == ["planned_end", "no_active_activity"]
    pinned = _pinned(engine, ended, browse_id)
    assert engine.context.state.agents[1].next_decision_at == engine.context.state.current_time
    again, next_id = _start_browse(engine, "alone-again")
    assert again["wake_reasons"] == ["no_active_activity"] and _supply_order(again)[:len(pinned)] == pinned
    return next_id


def _end_browse_early(engine, browse_id: str) -> None:
    """@brief 本人提前结束浏览：浏览取消、没有结果，同一秒以 no_active_activity 再决策。"""
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert _conclude(engine, "early-end", browse_id)["wake_reasons"] == ["message_arrival"]
    assert engine.context.state.activities[browse_id].status == ActivityStatus.CANCELLED
    assert "revealed" not in engine.context.state.activities[browse_id].data
    agent = engine.context.state.agents[1]
    assert agent.pending_wake_reasons == ["no_active_activity"] and agent.next_decision_at == engine.context.state.current_time


def _browse_beside_a_wait(engine) -> None:
    """@brief 在进行中的等待旁浏览：浏览先到计划结束，以 planned_end 唤醒并在该边界的观察置顶结果；收尾后等待仍在
    进行，下一次决策按决策节律，不在同一秒或下一秒；等待结束的边界以 planned_end 与 no_active_activity 唤醒。"""
    _, browse_id = _start_browse(engine, "beside")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    _, waiting = _observed_act(engine, "beside-wait", "start_activity", start_activity("等待", "自己"))
    assert waiting.status == "in_progress"
    _finish(engine, browse_id, "beside")
    assert engine.context.state.current_time == engine.context.state.activities[browse_id].planned_end_at
    ended = _conclude(engine, "beside-end", browse_id)
    assert ended["wake_reasons"] == ["planned_end"]
    _pinned(engine, ended, browse_id)
    _assert_rhythm_scheduled(engine)
    _finish(engine, waiting.activity_id, "wait")
    assert _conclude(engine, "wait-end", waiting.activity_id)["wake_reasons"] == ["planned_end", "no_active_activity"]


def _recorded_wake_reasons(engine) -> dict[str, list[list[str]]]:
    """@brief 人物 1 在决策开始、观察与共同身体结算中留下的全部唤醒原因。"""
    events = engine.context.store.read_agent_events
    return {
        "decision_started": [event.data["wake_reasons"] for event in events("decision_started", 1)],
        "observation": [event.data["wake_reasons"] for event in events("observation", 1)],
        "body_settlement": [event.data["wake_reasons"] for event in events("environment_event", 1)
                            if event.data.get("kind") == "body_settlement"],
    }


def test_browse_results_return_in_observation_never_as_discovery_result(engine_factory, monkeypatch):
    """discovery_result 保留在闭集中、当前没有触发来源（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章规则 6）：浏览是活动，
    结果随浏览结束回灌到随后的观察，不另排下一秒。独自浏览到计划结束、本人提前结束、在进行中的等待旁浏览三种情形里，
    决策开始、观察与共同身体结算的唤醒原因都不含它；判定函数仍把置位的标志映射为该原因（闭集保留），
    而节律为每个边界装配的判定输入从不置位。"""
    evaluated = []

    def recording(snapshot):
        verdict = evaluate_wake_reasons(snapshot)
        evaluated.append((snapshot, verdict))
        return verdict

    monkeypatch.setattr(schedule_module, "evaluate_wake_reasons", recording)
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    _end_browse_early(engine, _browse_alone_to_its_end(engine))
    _browse_beside_a_wait(engine)
    recorded = _recorded_wake_reasons(engine)
    assert all(recorded.values())
    assert not [reasons for rows in recorded.values() for reasons in rows if "discovery_result" in reasons]
    assert len(evaluated) >= len(recorded["body_settlement"]) > 0
    assert not [snapshot for snapshot, verdict in evaluated
                if snapshot.discovery_result_ready or WakeReason.DISCOVERY_RESULT in verdict.wake_reasons]
    assert reasons(discovery_result_ready=True) == ["discovery_result"]


@pytest.mark.parametrize(("energy", "band"), [(300.0, "饿得难受"), (60.0, "饿得发慌")])
def test_low_energy_bands_match_the_decision_reference(engine_factory, energy, band):
    """低能量下决策参照快照与唤醒判定传入同一能量与紧急阈值：分级同源，不误报 felt_band_change。
    08:00 昼夜谷底按公式只有「很饿」，两档都只能来自低能量规则。"""
    from tests.life.test_energy_feasibility import _stage_morning

    engine = engine_factory()
    _stage_morning(engine, energy)
    started = act(engine, "low-energy-watch", "start_activity", start_activity("接着看", "内容：Sintel"))
    assert started.status == "in_progress"
    assert engine.context.state.agents[1].felt_bands_at_last_decision["appetite"] == band
    state = engine.context.state.model_copy(deep=True)
    state, reasons = _warning_step(engine, state)
    assert "felt_band_change" not in reasons


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

    document = engine_factory().context.semantics.document.model_dump(mode="json")
    assert LifeScenario.model_validate(document).progress_report_interval_seconds == 1800


@pytest.mark.parametrize("value", [0, -1, -100])
def test_scenario_rejects_non_positive_progress_report_interval(engine_factory, value):
    """progress_report_interval_seconds 非正值被拒绝。"""
    from pydantic import ValidationError
    from worldmock.life.scenario import LifeScenario

    document = engine_factory().context.semantics.document.model_dump(mode="json")
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


def test_progress_report_due_counts_only_active_activities_from_their_anchor(engine_factory):
    """播报只针对本人进行中的活动：锚点 report_anchor_at（未经受理路径构造的活动取开始时刻）距今满间隔才到期；
    暂停、刚结束与他人的活动不参与；下一播报候选是进行中活动锚点加间隔中严格晚于现在的最早者（第八章「活动」）。"""
    engine = engine_factory("multi-person.json")
    state = engine.context.state.model_copy(deep=True)
    now, agent = state.current_time, state.agents[1]
    interval = engine.context.semantics.document.progress_report_interval_seconds

    def activity(key: str, anchor: int | None, *, status: ActivityStatus = ActivityStatus.ACTIVE,
                 agent_id: int = 1, started: int = now - 3600) -> ActivityState:
        """本人或他人的一段等待，锚点与状态可设。"""
        ended = now - 10 if status == ActivityStatus.COMPLETED else None
        return ActivityState(
            activity_id=key, agent_id=agent_id, location_id=state.agents[agent_id].location_id,
            kind=ActivityKind.WAIT, status=status, started_at=started, ended_at=ended, planned_end_at=now + 3600,
            last_settled_at=ended or started, elapsed_seconds=0, planned_duration_seconds=7200,
            origin_action_id=f"act-{key}", origin_decision_id="dec", origin_request_event_id="evt",
            report_anchor_at=anchor)

    state.activities = {"paused": activity("paused", now - 5000, status=ActivityStatus.PAUSED),
                        "ended": activity("ended", now - 5000, status=ActivityStatus.COMPLETED),
                        "other": activity("other", now - 5000, agent_id=2),
                        "reported": activity("reported", now - interval + 1)}
    assert not _progress_report_due(agent, state, interval)
    assert _next_progress_report_at(agent, state, interval) == now + 1
    _, reasons = _warning_step(engine, state)
    assert "progress_report_due" not in reasons
    state.activities["reported"].report_anchor_at = now - interval
    assert _progress_report_due(agent, state, interval)
    assert _next_progress_report_at(agent, state, interval) is None
    _, reasons = _warning_step(engine, state)
    assert "progress_report_due" in reasons
    state.activities["fresh"] = activity("fresh", None, started=now - 600)
    assert _next_progress_report_at(agent, state, interval) == now - 600 + interval


def _report_every_ten_minutes(document: dict) -> None:
    """@brief 播报间隔 10 分钟，等待一小时，使播报在等待期间多次到期。"""
    document["progress_report_interval_seconds"] = 600
    document["activity_feedback_seconds"] = 3600


_REVISE = ReviseDayPlanRequest(goals=[PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None, around_time=None)])


def _wake_at(engine, moment: int, name: str) -> list[str]:
    """@brief 推进到 moment 的决策机会并返回其唤醒原因；途中的其他机会以修订目标应答（不动播报锚点）。"""
    for step in range(32):
        if engine.scheduling_state().ready_agent_ids:
            if engine.context.state.current_time == moment:
                return list(engine.context.state.agents[1].pending_wake_reasons)
            assert act(engine, f"{name}-{step}", "revise_day_plan", _REVISE).status == "completed"
        else:
            assert engine.context.state.current_time < moment
            advance_next(engine)
    pytest.fail("No decision was offered at the expected moment")


def test_report_anchor_moves_on_start_report_and_resume_and_paused_activity_never_reports(tmp_path):
    """播报锚点在开始、对该活动播报与恢复时写入当前时刻，暂停不动锚点；progress_report_due 只由进行中活动距其锚点
    满间隔触发，暂停的活动不再播报（第八章「活动」，第七章「播报与控制都作用于活动」）。"""
    engine = _custom_engine("white-collar-12-weeks.json", _report_every_ten_minutes, tmp_path, "report-anchor")
    try:
        stage_day_plan(engine)
        start = engine.context.state.current_time
        wait_id = act(engine, "anchor-wait", "start_activity", start_activity("等待", "自己")).activity_id
        assert engine.context.state.activities[wait_id].report_anchor_at == start
        wake_by_mail(engine, offset=300)
        assert _wake_at(engine, start + 300, "anchor-first") == ["message_arrival"]
        observed = engine.observe(context(engine, "anchor-content"))
        target, label = next((row["object"], action["label"]) for row in observed["interaction_bindings"]
                             if row["kind"] == "content" for action in row["actions"]
                             if action["canonical_action"] == "watch")
        watched = act(engine, "anchor-watch", "start_activity", start_activity(label, target))
        assert watched.status == "in_progress"
        watch_id = watched.activity_id
        assert engine.context.state.activities[watch_id].report_anchor_at == start + 300
        assert _wake_at(engine, start + 600, "anchor-due") == ["progress_report_due"]
        names = [activity_name(engine, wait_id), activity_name(engine, watch_id)]
        assert act(engine, "anchor-pause", "pause_activity", pause_request(
            names[0], summary="先停下。")).status == "completed"
        assert engine.context.state.activities[wait_id].report_anchor_at == start
        wake_by_mail(engine, offset=100)
        assert _wake_at(engine, start + 700, "anchor-paused") == ["message_arrival"]
        agent = engine.context.state.agents[1]
        assert not _progress_report_due(agent, engine.context.state, 600)
        assert _next_progress_report_at(agent, engine.context.state, 600) == start + 900
        assert act(engine, "anchor-note", "revise_day_plan", _REVISE).status == "completed"
        assert _wake_at(engine, start + 900, "anchor-watch-due") == ["progress_report_due"]
        resumed = act(engine, "anchor-resume", "report_status", report_request(*names, summary=["接着等。", "接着看。"]))
        assert resumed.status == "completed"
        now = engine.context.state.current_time
        assert engine.context.state.activities[wait_id].status == ActivityStatus.ACTIVE
        assert {engine.context.state.activities[key].report_anchor_at for key in (wait_id, watch_id)} == {now}
        assert _next_progress_report_at(engine.context.state.agents[1], engine.context.state, 600) == now + 600
    finally:
        engine.close()


def test_sleeping_person_gets_no_progress_report_wakeups(tmp_path):
    """睡眠是进行中活动，播报锚点照常到期，但睡眠中的边界不因 progress_report_due 提供决策；醒来时睡眠已结束待收尾，
    也不再为它播报（第八章「活动」，第一章不变项）。"""
    engine = _custom_engine("white-collar-12-weeks.json", _report_every_ten_minutes, tmp_path, "report-sleep")
    try:
        stage_day_plan(engine)
        start = engine.context.state.current_time
        sleep_id = act(engine, "report-sleep", "start_activity", start_activity("睡觉", "自己")).activity_id
        assert engine.context.state.activities[sleep_id].report_anchor_at == start
        due_while_asleep = []
        for _ in range(96):
            if engine.context.state.activities[sleep_id].status != ActivityStatus.ACTIVE:
                break
            assert engine.scheduling_state().ready_agent_ids == ()
            advance_next(engine)
            if engine.context.state.activities[sleep_id].status == ActivityStatus.ACTIVE:
                due_while_asleep.append(_progress_report_due(engine.context.state.agents[1], engine.context.state, 600))
        woke = engine.context.state.current_time
        assert engine.context.state.activities[sleep_id].status == ActivityStatus.COMPLETED
        assert any(due_while_asleep)  # 播报在睡眠中到期
        settlements = [event.data for event in engine.context.store.read_agent_events("environment_event", 1)
                       if event.data.get("kind") == "body_settlement" and start < event.data["interval_end"] < woke]
        assert settlements and all(not data["decision_offered"] and data["wake_reasons"] == []
                                   for data in settlements)
        reasons = engine.context.state.agents[1].pending_wake_reasons
        assert "wake_from_sleep" in reasons and "progress_report_due" not in reasons
        assert not _progress_report_due(engine.context.state.agents[1], engine.context.state, 600)
        assert _next_progress_report_at(engine.context.state.agents[1], engine.context.state, 600) is None
    finally:
        engine.close()
