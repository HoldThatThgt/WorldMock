"""@brief 不适脉冲与睡眠中的消息（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-不适脉冲与睡眠中的消息 ，验收台账「不适脉冲与消息」）。

- 不适脉冲：两小时门槛只在节律结算段按连续不适时长判定一次，超出部分按 −0.1/小时计；只有实际施加了脉冲才记已计费小时。
- 睡眠中不读消息：边界判定段与决策轮提交在人物在睡时都不记已读；睡眠中送达的消息在醒来后的第一个边界才记已读，
  社交需要 −0.05、情绪 +0.1。
引擎用例都经真实推进与决策轮，读已提交的世界状态与共同身体结算。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.life.conftest import (activity_name, advance_next, conclude_request, report_request, settle_day_plans,
                                 stage_day_plan, start_activity, submit_round)
from tests.life.test_day_planning import _late_engine
from tests.life.test_same_moment_decisions import _message_to, _with_inbox
from tests.life.test_sleep_billing import _settlement, _stage_body
from worldmock.life import rhythm, same_moment
from worldmock.life.activity_control import is_pending_conclusion
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, AgentState, BodyState, active_activities
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs
from worldmock.life.schedule import PlanGoal
from worldmock.life.social import Message


def _keep_waiting(engine, agent_id: int) -> None:
    """只有 agent_id 就绪的决策：有待收尾先收尾，手上没有活动时再等待一段（等待时长即结算间隔）。"""
    while engine.scheduling_state().ready_agent_ids:
        assert engine.scheduling_state().ready_agent_ids == (agent_id,)
        pending = [item.name for item in engine.context.state.activities.values()
                   if item.agent_id == agent_id and is_pending_conclusion(item)]
        running = active_activities(engine.context.state, agent_id)
        if pending:
            call = ("conclude_activity", conclude_request(*pending))
        elif running:
            names = [activity_name(engine, item.activity_id) for item in running]
            call = ("report_status", report_request(*names, summary="还在等。"))
        else:
            call = ("start_activity", start_activity("等待", "自己"))
        submit_round(engine, {agent_id: call}, f"keep-{agent_id}-{engine.context.state.version}")


def test_hunger_pulse_starts_after_two_hours_and_bills_only_what_it_applies(tmp_path: Path) -> None:
    """连续饿满两小时以前不计费；跨过门槛后只按实际超出的时长施加情绪脉冲。

    当前世界还会出现 app 推送等更短边界，因此用真实边界累计连续时长，不假定每个边界固定十五分钟。"""
    engine = _late_engine(tmp_path, "2026-01-05T12:00:00+08:00", "hunger")
    settle_day_plans(engine)
    _stage_body(engine, energy_kcal=790.0, mood_valence=0.0, neuroticism_norm=0.0)
    start = engine.context.state.current_time
    rows = []
    for _ in range(64):
        _keep_waiting(engine, 1)
        advance_next(engine)
        settlement = _settlement(engine, engine.context.state.current_time)
        assert settlement["interval_end"] > settlement["interval_start"]
        agent = engine.context.state.agents[1]
        rows.append((engine.context.state.current_time, agent.discomfort_started_at,
                     agent.body.mood_valence, agent.discomfort_pulse_hours))
        if agent.discomfort_pulse_hours >= 0.25:
            break

    started = rows[0][1]
    assert started is not None and all(row[1] == started for row in rows)
    for at, _, mood, billed in rows:
        expected = max(0.0, (at - started) / 3600 - 2.0)
        assert billed == pytest.approx(expected)
        assert mood == pytest.approx(-0.1 * expected)
    assert rows[-1][3] >= 0.25


#: 直接调用节律结算段的不适计费用的边界时刻（任意纪元秒）。
_NOW = 1_767_600_000


def _uneasy(hours: float) -> AgentState:
    """连续不适已 hours 小时、尚未计费、情绪为 0（神经质 0）的人物。"""
    return AgentState(agent_id=1, name="Lin", location_id="home", balance_account_id="agent:1", body_settled_at=0,
                      next_decision_at=0, discomfort_started_at=_NOW - round(hours * 3600),
                      body=BodyState(energy_kcal=1600, entertainment_need=0.0))


@pytest.mark.parametrize(("appetite", "sleepiness", "counts"), [
    ("不饿", "清醒", False), ("有点想吃", "有点乏", False),
    ("饿", "清醒", True), ("很饿", "清醒", True), ("饿得难受", "清醒", True), ("饿得发慌", "有点乏", True),
    ("有点想吃", "困", True), ("不饿", "很困", True), ("不饿", "撑不住", True),
])
def test_discomfort_counts_only_the_hungry_and_sleepy_bands(appetite: str, sleepiness: str, counts: bool) -> None:
    """不适档不变（规格 1.5）：饥饿从「饿」起（饿、很饿、饿得难受、饿得发慌），困意从「困」起（困、很困、撑不住），
    「有点想吃」「有点乏」不算。已连续不适 2.25 小时的人物在不适档里按新增的 0.25 小时施加 −0.025 并记下这些计费小时；
    落在不适档之外时起点与已计费小时一并清零，不施加脉冲（下一次不适重新从两小时门槛算起）。"""
    settled = rhythm._settle_discomfort(_uneasy(2.25), {"appetite": appetite, "sleepiness": sleepiness}, _NOW)
    if counts:
        assert settled.discomfort_started_at == _NOW - 8100
        assert settled.discomfort_pulse_hours == pytest.approx(0.25)
        assert settled.body.mood_valence == pytest.approx(-0.025)
    else:
        assert (settled.discomfort_started_at, settled.discomfort_pulse_hours) == (None, 0.0)
        assert settled.body.mood_valence == 0.0


def test_each_boundary_is_billed_only_for_its_new_hours_past_the_threshold() -> None:
    """门槛只判一次、每个边界只施加新增的计费小时：连续不适 2.25 小时时计费 0.25 小时、施加 −0.025；下一个边界（2.5 小时）
    只再施加新增的 0.25 小时，不按累计的 0.5 小时重复施加；一个边界一次跨到 2.5 小时则按新增的 0.5 小时施加 −0.05；
    1.75 小时不施加、不计费。"""
    levels = {"appetite": "很饿", "sleepiness": "清醒"}
    first = rhythm._settle_discomfort(_uneasy(2.25), levels, _NOW)
    second = rhythm._settle_discomfort(first, levels, _NOW + 900)
    assert (first.discomfort_pulse_hours, second.discomfort_pulse_hours) == (pytest.approx(0.25), pytest.approx(0.5))
    assert (first.body.mood_valence, second.body.mood_valence) == (pytest.approx(-0.025), pytest.approx(-0.05))
    jumped = rhythm._settle_discomfort(_uneasy(2.5), levels, _NOW)
    assert (jumped.discomfort_pulse_hours, jumped.body.mood_valence) == (pytest.approx(0.5), pytest.approx(-0.05))
    early = rhythm._settle_discomfort(_uneasy(1.75), levels, _NOW)
    assert (early.discomfort_pulse_hours, early.body.mood_valence) == (0.0, 0.0)


def test_the_boundary_decision_segment_reads_no_message_while_asleep(engine_factory) -> None:
    """边界判定段：人物在睡时未读消息照样成立 message_arrival（睡眠中被过滤），但不记已读、不施加阅读效果；醒着时
    判定后记已读（时间与能量规格第一章「人物之间的消息」）。"""
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    now = state.current_time
    state.agents[1].last_decision_time = now
    state.agents[1].inbox = {"m-sleep": Message(message_id="m-sleep", kind="direct", sender_id="agent:2",
                                                recipient_id="agent:1", text="睡了吗", created_at=now)}
    state.activities["asleep"] = ActivityState(
        activity_id="asleep", agent_id=1, location_id=state.agents[1].location_id, kind=ActivityKind.SLEEP,
        status=ActivityStatus.ACTIVE, started_at=now - 600, planned_end_at=now + 3600, last_settled_at=now,
        elapsed_seconds=600, planned_duration_seconds=4200, origin_action_id="act", origin_decision_id="dec",
        origin_request_event_id="evt")
    body = state.agents[1].body

    asleep, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine.context, state))

    assert "m-sleep" not in asleep.agents[1].read_inbox_ids and not rhythms[1].offered
    assert asleep.agents[1].body.social_need == body.social_need
    assert asleep.agents[1].body.mood_valence == body.mood_valence
    del state.activities["asleep"]
    awake, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine.context, state))
    assert "m-sleep" in awake.agents[1].read_inbox_ids and "message_arrival" in rhythms[1].wake_reasons


def test_a_round_observed_while_asleep_marks_no_message_read(engine_factory) -> None:
    """决策轮提交：轮前在睡的人物看到的消息不记已读（睡眠中不读）；本轮才开始睡的人物轮前醒着，全文看过的照常记已读。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    awake = _with_inbox(engine.context.state, 1, "m1")
    now = awake.current_time
    sleep = ActivityState(activity_id="asleep", agent_id=1, location_id=awake.agents[1].location_id,
                          kind=ActivityKind.SLEEP, status=ActivityStatus.ACTIVE, started_at=now,
                          planned_end_at=now + 3600, last_settled_at=now, elapsed_seconds=0,
                          planned_duration_seconds=3600, origin_action_id="act", origin_decision_id="dec",
                          origin_request_event_id="evt")
    asleep = awake.model_copy(update={"activities": {**awake.activities, sleep.activity_id: sleep}}, deep=True)

    assert same_moment.read_observed_messages(asleep, asleep, {1: frozenset({"m1"})}) == asleep
    fell_asleep = same_moment.read_observed_messages(asleep, awake, {1: frozenset({"m1"})})
    assert fell_asleep.agents[1].read_inbox_ids == {"m1"}
    assert fell_asleep.agents[1].body.mood_valence > asleep.agents[1].body.mood_valence


def test_messages_delivered_during_sleep_are_read_at_the_first_boundary_after_waking(engine_factory) -> None:
    """人物 2 睡着时收到人物 1 的消息：睡眠中的每个边界都保持未读，社交需要与情绪都不因它变化；提醒叫醒的那个边界
    （醒来后第一个边界，人物已不在睡）才记已读并施加阅读效果：社交 −0.05、情绪 +0.1，且以 message_arrival 唤醒。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    _stage_body(engine, 2, social_need=0.5, mood_valence=0.0, neuroticism_norm=0.0)
    start = engine.context.state.current_time
    _, results = submit_round(engine, {1: _message_to(engine, 2, "早点休息"),
                                       2: ("start_activity", start_activity("睡觉", "自己"))}, "sleep-message")
    message_id = next(item.data["message_id"] for item in results if item.agent_id == 1)
    slept = next(item.activity_id for item in results if item.agent_id == 2)
    remind = start + 3600 + 5 * 60
    stage_day_plan(engine, 2, goals=(PlanGoal(intent_kind="晨间例程", goal="起床", registered_at=start,
                                              remind_at=remind),))
    asleep_boundaries = 0
    while engine.context.state.activities[slept].status == ActivityStatus.ACTIVE:
        _keep_waiting(engine, 1)
        before = engine.context.state.agents[2]
        advance_next(engine)
        after = engine.context.state.agents[2]
        if engine.context.state.activities[slept].status == ActivityStatus.ACTIVE:
            asleep_boundaries += 1
            assert message_id in after.inbox and message_id not in after.read_inbox_ids
            assert (after.body.social_need, after.body.mood_valence) == (0.5, 0.0) == (
                before.body.social_need, before.body.mood_valence)

    woke = engine.context.state.agents[2]
    assert engine.context.state.current_time == remind and asleep_boundaries >= 3
    assert message_id in woke.read_inbox_ids and 2 in engine.scheduling_state().ready_agent_ids
    assert woke.body.social_need == pytest.approx(0.5 - 0.05)
    assert woke.body.mood_valence == pytest.approx(0.1)
    assert {"message_arrival", "wake_from_sleep"} <= set(_settlement(engine, remind, 2)["wake_reasons"])
    assert not active_activities(engine.context.state, 2)
