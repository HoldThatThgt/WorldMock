"""环境事件、承诺威胁与双重估间隔的精确边界合同。"""

from tests.data_paths import ENVIRONMENT_ROOT
from tests.engine_fixture import engine_factory  # noqa: F401

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 stage_day_plan, start_activity, wake_by_mail, pause_request, report_request)
from worldmock.life import schedule as schedule_module
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
    "planned_end", "felt_band_change", "message_arrival", "delivery_arrival", "commitment_threatened", "reconsideration_interval",
    "person_arrived", "person_left", "activity_infeasible", "rejection_feedback",
    "discovery_result", "wake_from_sleep", "run_start", "progress_report_due", "no_active_activity",
    "plan_reminder_due", "errand_reminder_due", "shift_start", "shift_end", "break_start", "break_end",
    "app_push", "alarm_ring",
)


def reasons(**values):
    return [reason.value for reason in evaluate_wake_reasons(WakeSnapshot(now=NOW, **values)).wake_reasons]


def test_frozen_wake_and_intent_names():
    assert tuple(item.value for item in WakeReason) == FROZEN
    assert tuple(get_args(IntentKind)) == INTENT_KINDS


def test_sleep_allowed_reasons_include_plan_reminder_and_alarm():
    """当前睡眠允许集合包含计划提醒与闹钟，no_active_activity 不进入。"""
    assert SLEEP_ALLOWED_REASONS == {
        WakeReason.PLANNED_END, WakeReason.WAKE_FROM_SLEEP, WakeReason.RUN_START,
        WakeReason.PLAN_REMINDER_DUE, WakeReason.ALARM_RING,
    }
    assert WakeReason.NO_ACTIVE_ACTIVITY not in SLEEP_ALLOWED_REASONS


@pytest.mark.parametrize("fields,expected", [
    ({"planned_end": NOW}, ["planned_end"]),
    ({"felt_bands_now": {"appetite": 1}}, ["felt_band_change"]),
    ({"message_arrived": True}, ["message_arrival"]),
    ({"delivery_arrived": True}, ["delivery_arrival"]),
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
    {}, {"planned_end": NOW + 1},
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




def test_sleep_accepts_only_existing_sleep_reasons():
    snapshot = WakeSnapshot(now=NOW, is_asleep=True, has_commitments=True,
                            last_decision_time=NOW - 20000,
                            message_arrived=True)
    assert not evaluate_wake_reasons(snapshot).decision_offered
    assert reasons(is_asleep=True, planned_end=NOW, wake_from_sleep=True, run_start=True) == [
        "planned_end", "wake_from_sleep", "run_start"]
    assert reasons(is_asleep=True, no_active_activity=True) == []
    query = NextDecisionQuery(now=NOW, is_asleep=True, planned_end=NOW + 20000,
                              last_decision_time=NOW, has_commitments=True)
    assert next_decision_time(query) == NOW + 20000
    assert next_decision_time(NextDecisionQuery(now=NOW)) is None


def test_scenario_rejects_idle_interval_shorter_than_regular(engine_factory):
    document = engine_factory().context.semantics.document.model_dump(mode="json")
    assert LifeScenario.model_validate(document).idle_reconsideration_interval_seconds == 10800
    document["idle_reconsideration_interval_seconds"] = document["reconsideration_interval_seconds"] - 1
    with pytest.raises(ValidationError, match="must not be shorter"):
        LifeScenario.model_validate(document)


def test_commitments_exclude_messages_history_and_completed_activities(engine_factory):
    from tests.life.test_day_plan import act, goal
    engine = engine_factory(mutate=lambda document: document["agents"][0].update(work_content_ids=[], work_schedule=None))
    state = engine.context.state.model_copy(deep=True)
    now = state.current_time
    agent = state.agents[1].model_copy(update={"day_plan": None})
    inputs = engine_rhythm_inputs(engine.context, state)
    assert not _has_commitments(agent, inputs)
    pending = DayPlan(day_end=now + 1000, goals=[goal("工作", registered=now)])
    assert _has_commitments(agent.model_copy(update={"day_plan": pending}), inputs)
    history = pending.model_copy(update={"goals": (goal("工作", registered=now - 60,
                                         progressed_at=now, progress_activity_id="old"),)})
    assert not _has_commitments(agent.model_copy(update={"day_plan": history}), inputs)
    assert not _has_commitments(agent.model_copy(update={"day_plan": pending.model_copy(
        update={"day_end": now})}), inputs)
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
                                                    "kind": ActivityKind.ACTION})
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








def _keep_waiting(engine, name: str) -> None:
    """就绪时先给刚结束的活动收尾（第七章「活动一结束就要结束它」），有进行中活动就播报，否则开始下一段等待。"""
    from tests.life.test_day_plan import act, goal
    from worldmock.life.activity_control import pending_conclusions
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












def _warning_step(engine, state, **changes):
    """用引擎实际装配输入测试同一边界的威胁状态转换，不重复结算身体。"""
    from worldmock.life.rhythm import apply_decision_rhythm
    inputs = replace(engine_rhythm_inputs(engine.context, state), interval_seconds=0, **changes)
    changed, verdicts = apply_decision_rhythm(inputs)
    return changed, verdicts[1].wake_reasons






def test_activity_infeasible_and_commitment_threatened_have_no_retired_source():
    """activity_infeasible 保留名称但没有来源：快照不再有该字段，全部标志齐开也不产生它。"""
    assert WakeReason.ACTIVITY_INFEASIBLE.value == "activity_infeasible"
    assert "activity_infeasible" not in WakeSnapshot.model_fields
    everything = reasons(planned_end=NOW, felt_bands_now={"appetite": 1}, message_arrived=True,
                         delivery_arrived=True,
                         people_present_now={"person"},
                         discovery_result_ready=True, wake_from_sleep=True, run_start=True,
                         progress_report_due=True, no_active_activity=True)
    assert "activity_infeasible" not in everything
    assert "commitment_threatened" not in everything
    assert "next_task_due_at" not in WakeSnapshot.model_fields


def test_zero_energy_during_activity_raises_neither_infeasible_nor_threat(engine_factory):
    """能量为 0 时进行中的观看照常继续：不产生不可行预警，也不构成承诺威胁。"""
    from tests.life.test_day_plan import act, goal
    engine = engine_factory()
    started = act(engine, "zero-energy-watch", "start_activity",
                  start_activity("进行", "刷推荐", app=["哔哩哔哩"]))
    assert started.status == "in_progress"
    state = engine.context.state.model_copy(deep=True)
    state.agents[1].body.energy_kcal = 0
    state.agents[1].last_decision_time = state.current_time
    for _ in range(2):
        state, reasons = _warning_step(engine, state)
        assert "activity_infeasible" not in reasons and "commitment_threatened" not in reasons
        assert state.activities[started.activity_id].status == ActivityStatus.ACTIVE
        state.activities[started.activity_id].elapsed_seconds += 1


def _assert_rhythm_scheduled(engine) -> None:
    """@brief 有进行中活动时动作成功之后：下一次决策取决策节律的候选时刻（第一章规则 4、7），不另排下一秒，
    也不留待处理的唤醒原因。"""
    agent, now = engine.context.state.agents[1], engine.context.state.current_time
    assert agent.pending_wake_reasons == [] and agent.next_decision_at > now + 1
    assert agent.next_decision_at == next_decision_candidates(
        agent.model_copy(update={"last_decision_time": now}), engine_rhythm_inputs(engine.context, engine.context.state))


















def _stage_morning(engine, energy):
    """@brief 真实状态提交固定晨间身体边界，后续由实际引擎观察和动作验证。"""
    from worldmock.footprints.models import EventDraft
    state = engine.context.state.model_copy(deep=True)
    now = int(engine.context.scenario.start_at.timestamp()) + 8*3600
    state.current_time = now
    state.pending_events = [event for event in state.pending_events if event.due_at > now]
    for agent_id, agent in state.agents.items():
        update = {"sleep_pressure": 0.2, "entertainment_need": 0.0}
        if agent_id == 1:
            update["energy_kcal"] = energy
        state.agents[agent_id] = agent.model_copy(update={"body": agent.body.model_copy(update=update),
            "body_settled_at": now, "next_decision_at": now})
    state.version += 1
    engine.context.store.commit(state.model_dump(mode="json"), [EventDraft(event_type="environment_event",
        occurred_at=now, visibility="system", data={"kind": "test_morning_position"})])
    engine.context.state = state


@pytest.mark.parametrize(("energy", "band"), [(300.0, "饿得难受"), (60.0, "饿得发慌")])
def test_low_energy_bands_match_the_decision_reference(engine_factory, energy, band):
    """低能量下决策参照快照与唤醒判定传入同一能量与紧急阈值：分级同源，不误报 felt_band_change。
    08:00 昼夜谷底按公式只有「很饿」，两档都只能来自低能量规则。"""
    from tests.life.test_day_plan import act, goal


    engine = engine_factory()
    _stage_morning(engine, energy)
    started = act(engine, "low-energy-watch", "start_activity", start_activity("进行", "刷推荐", app=["哔哩哔哩"]))
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
    from tests.life.test_day_plan import act, goal
    for step in range(32):
        if engine.scheduling_state().ready_agent_ids:
            if engine.context.state.current_time == moment:
                return list(engine.context.state.agents[1].pending_wake_reasons)
            assert act(engine, f"{name}-{step}", "revise_day_plan", _REVISE).status == "completed"
        else:
            assert engine.context.state.current_time < moment
            advance_next(engine)
    pytest.fail("No decision was offered at the expected moment")


def test_report_anchor_moves_on_start_report_and_resume_and_paused_activity_never_reports(engine_factory):
    """播报锚点在开始、对该活动播报与恢复时写入当前时刻，暂停不动锚点；progress_report_due 只由进行中活动距其锚点
    满间隔触发，暂停的活动不再播报（第八章「活动」，第七章「播报与控制都作用于活动」）。"""
    from tests.life.test_day_plan import act, goal
    engine = engine_factory(mutate=_report_every_ten_minutes)
    try:
        stage_day_plan(engine)
        start = engine.context.state.current_time
        wait_id = act(engine, "anchor-wait", "start_activity", start_activity("等待", "自己")).activity_id
        assert engine.context.state.activities[wait_id].report_anchor_at == start
        wake_by_mail(engine, offset=300)
        assert _wake_at(engine, start + 300, "anchor-first") == ["message_arrival"]
        watched = act(engine, "anchor-watch", "start_activity",
                      start_activity("进行", "学课程", app=["哔哩哔哩"]))
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


def test_sleeping_person_gets_no_progress_report_wakeups(engine_factory):
    """睡眠是进行中活动，播报锚点照常到期，但睡眠中的边界不因 progress_report_due 提供决策；醒来时睡眠已结束待收尾，
    也不再为它播报（第八章「活动」，第一章不变项）。"""
    from tests.life.test_day_plan import act, goal
    engine = engine_factory(mutate=_report_every_ten_minutes)
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
