"""@brief 不适脉冲与睡眠中的消息（docs/dev/SPEC-dayrun-fidelity.md 1.5，验收台账「不适脉冲与消息」）。

- 不适脉冲：两小时门槛只在节律结算段按连续不适时长判定一次，超出部分按 −0.1/小时计；只有实际施加了脉冲才记已计费小时。
- 睡眠中不读消息：边界判定段与决策轮提交在人物在睡时都不记已读；睡眠中送达的消息在醒来后的第一个边界才记已读，
  社交需要 −0.05、情绪 +0.1。
引擎用例都经真实推进与决策轮，读已提交的世界状态与共同身体结算。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.life.conftest import (advance_next, conclude_request, settle_day_plans, stage_day_plan, start_activity,
                                 submit_round)
from tests.life.test_day_planning import _decide, _late_engine
from tests.life.test_same_moment_decisions import _message_to, _with_inbox
from tests.life.test_sleep_billing import _settlement, _stage_body
from worldmock.life import same_moment
from worldmock.life.activity_control import is_pending_conclusion
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, active_activities
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs
from worldmock.life.schedule import PlanGoal
from worldmock.life.social import Message


def _keep_waiting(engine, agent_id: int) -> None:
    """只有 agent_id 就绪的决策：有待收尾先收尾，手上没有活动时再等待一段（等待时长即结算间隔）。"""
    while engine.scheduling_state().ready_agent_ids:
        assert engine.scheduling_state().ready_agent_ids == (agent_id,)
        pending = [item.name for item in engine.state.activities.values()
                   if item.agent_id == agent_id and is_pending_conclusion(item)]
        call = (("conclude_activity", conclude_request(*pending)) if pending
                else ("start_activity", start_activity("等待", "自己")))
        submit_round(engine, {agent_id: call}, f"keep-{agent_id}-{engine.state.version}")


def test_hunger_pulse_starts_after_two_hours_and_bills_only_what_it_applies(tmp_path: Path) -> None:
    """连续饿 1.75 小时不施加、不消耗计费小时；到 2.25 小时（边界间隔 0.25 小时）施加 −0.025，计费 0.25 小时。

    12:00 起能量 790 kcal、情绪 0、神经质 0：此后到 14:30 一直「很饿」、始终清醒，边界每 15 分钟一个；节律在 12:15 的
    第一个边界记下不适起点，所以 14:00 连续 1.75 小时、14:15 恰满 2 小时、14:30 连续 2.25 小时。"""
    engine = _late_engine(tmp_path, "2026-01-05T12:00:00+08:00", "hunger")
    settle_day_plans(engine)
    _stage_body(engine, energy_kcal=790.0, mood_valence=0.0, neuroticism_norm=0.0)
    start = engine.state.current_time
    rows = {}
    while engine.state.current_time < start + 10 * 900:
        _keep_waiting(engine, 1)
        advance_next(engine)
        settlement = _settlement(engine, engine.state.current_time)
        assert settlement["interval_end"] - settlement["interval_start"] == 900
        agent = engine.state.agents[1]
        rows[(engine.state.current_time - start) // 900] = (agent.discomfort_started_at, agent.body.mood_valence,
                                                            agent.discomfort_pulse_hours)

    assert {started for started, _, _ in rows.values()} == {start + 900}
    assert rows[8][1:] == (0.0, 0.0) and rows[9][1:] == (0.0, 0.0)
    assert all(rows[step][1:] == (0.0, 0.0) for step in range(1, 10))
    assert rows[10][1] == pytest.approx(-0.025) and rows[10][2] == pytest.approx(0.25)


def test_the_boundary_decision_segment_reads_no_message_while_asleep(engine_factory) -> None:
    """边界判定段：人物在睡时未读消息照样成立 message_arrival（睡眠中被过滤），但不记已读、不施加阅读效果；醒着时
    判定后记已读（时间与能量规格第一章「人物之间的消息」）。"""
    engine = engine_factory()
    state = engine.state.model_copy(deep=True)
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

    asleep, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine, state))

    assert "m-sleep" not in asleep.agents[1].read_inbox_ids and not rhythms[1].offered
    assert asleep.agents[1].body.social_need == body.social_need
    assert asleep.agents[1].body.mood_valence == body.mood_valence
    del state.activities["asleep"]
    awake, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine, state))
    assert "m-sleep" in awake.agents[1].read_inbox_ids and "message_arrival" in rhythms[1].wake_reasons


def test_a_round_observed_while_asleep_marks_no_message_read(engine_factory) -> None:
    """决策轮提交：轮前在睡的人物看到的消息不记已读（睡眠中不读）；本轮才开始睡的人物轮前醒着，全文看过的照常记已读。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    awake = _with_inbox(engine.state, 1, "m1")
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
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    _stage_body(engine, 2, social_need=0.5, mood_valence=0.0, neuroticism_norm=0.0)
    start = engine.state.current_time
    _, results = submit_round(engine, {1: _message_to(engine, 2, "早点休息"),
                                       2: ("start_activity", start_activity("睡觉", "自己"))}, "sleep-message")
    message_id = next(item.data["message_id"] for item in results if item.agent_id == 1)
    slept = next(item.activity_id for item in results if item.agent_id == 2)
    remind = start + 3600 + 5 * 60
    stage_day_plan(engine, 2, goals=(PlanGoal(intent_kind="晨间例程", goal="起床", registered_at=start,
                                              remind_at=remind),))
    asleep_boundaries = 0
    while engine.state.activities[slept].status == ActivityStatus.ACTIVE:
        _keep_waiting(engine, 1)
        before = engine.state.agents[2]
        advance_next(engine)
        after = engine.state.agents[2]
        if engine.state.activities[slept].status == ActivityStatus.ACTIVE:
            asleep_boundaries += 1
            assert message_id in after.inbox and message_id not in after.read_inbox_ids
            assert (after.body.social_need, after.body.mood_valence) == (0.5, 0.0) == (
                before.body.social_need, before.body.mood_valence)

    woke = engine.state.agents[2]
    assert engine.state.current_time == remind and asleep_boundaries >= 3
    assert message_id in woke.read_inbox_ids and 2 in engine.scheduling_state().ready_agent_ids
    assert woke.body.social_need == pytest.approx(0.5 - 0.05)
    assert woke.body.mood_valence == pytest.approx(0.1)
    assert {"message_arrival", "wake_from_sleep"} <= set(_settlement(engine, remind, 2)["wake_reasons"])
    assert not active_activities(engine.state, 2)
