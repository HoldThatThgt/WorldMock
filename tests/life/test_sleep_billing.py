"""@brief 睡眠计费：社交账按本区间共同身体结算所用的活动集合判定（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-区间事实只有一个来源社交账与共同用餐 ，
验收台账「睡眠计费」）。

引擎用例都经真实推进：LifeEngine.advance 先按推进前进行中的活动结算共同身体并写 body_settlement（activity_ids），
再由节律结算段按同一集合记社交账；断言读已提交的共同身体结算与世界状态。直接调用节律而没有草稿时，
走「ACTIVE，或 COMPLETED 且 ended_at == now」兜底。
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import (advance_next, conclude_request, context, layer_start, settle_day_plans,
                                 stage_day_plan, start_activity, submit_round)
from tests.life.test_acs_amended_runtime import perform
from tests.life.test_acs_runtime import request
from worldmock.life.activity_control import pending_conclusions
from tests.life.conftest import activity_name, report_request, wake_by_mail
from worldmock.footprints.models import EventDraft
from worldmock.life import rhythm
from worldmock.life.activity_control import is_pending_conclusion
from worldmock.life.felt import SOCIAL_NEED_AWAKE_RATE, SOCIAL_NEED_REMOTE_RATE
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, BodyState, active_activities
from worldmock.life.schedule import PlanGoal
from worldmock.life.sleep import SLEEP_TIME_CONSTANT_H


def _billing_decision(engine, name, call=None):
    """@brief 通过当前真实观察和动作入口应对睡眠计费边界。"""
    if call is None:
        pending = pending_conclusions(engine.context.state, 1)
        running = active_activities(engine.context.state, 1)
        if pending:
            call = ('conclude_activity', conclude_request(*(activity_name(engine, item.activity_id) for item in pending)))
        elif running:
            call = ('report_status', report_request(*(activity_name(engine, item.activity_id) for item in running)))
        else:
            call = ('start_activity', start_activity('等待', '自己'))
    return perform(engine, name, call[1], call[0])


def _start_sleep(engine, name):
    result = perform(engine, name, request("自己", None, "睡觉"))
    assert result.status == 'in_progress', result.error_code
    return result.activity_id


def _stage_body(engine, agent_id: int = 1, **fields) -> None:
    """把人物身体的若干数值改成给定值并经存储落盘（保持存储版本连续）。"""
    staged = engine.context.state.model_copy(deep=True)
    agent = staged.agents[agent_id]
    staged.agents[agent_id] = agent.model_copy(update={"body": agent.body.model_copy(update=fields)}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_body_staged"})])
    engine.context.state = staged


def _settlement(engine, moment: int, agent_id: int = 1) -> dict:
    """该人物在 moment 结束的唯一已提交共同身体结算正文。"""
    matches = [event.data for event in engine.context.store.read_agent_events("environment_event", agent_id)
               if event.data.get("kind") == "body_settlement" and event.data["interval_end"] == moment]
    assert len(matches) == 1
    return matches[0]


def _advance(engine, agent_id: int = 1) -> tuple[BodyState, BodyState, dict]:
    """推进一个真实边界，返回（推进前身体，推进后身体，本边界的共同身体结算正文）。"""
    before = engine.context.state.agents[agent_id].body
    advance_next(engine)
    return before, engine.context.state.agents[agent_id].body, _settlement(engine, engine.context.state.current_time, agent_id)


def _hours(settlement: dict) -> float:
    """共同身体结算区间的小时数。"""
    return (settlement["interval_end"] - settlement["interval_start"]) / 3600


def _assert_asleep_interval(engine, before: BodyState, after: BodyState, settlement: dict) -> None:
    """睡眠区间：社交需要不变，能量只按基础代谢乘小时数下降（睡眠 MET 不高于 1），睡眠压力按睡眠过程下降，
    娱乐需要不增长。"""
    hours = _hours(settlement)
    assert after.social_need == before.social_need
    assert after.energy_kcal == pytest.approx(before.energy_kcal - engine.context.semantics.body.basal_kcal_per_hour * hours)
    assert after.sleep_pressure == pytest.approx(before.sleep_pressure * math.exp(-hours / SLEEP_TIME_CONSTANT_H))
    assert after.sleep_pressure < before.sleep_pressure
    assert after.entertainment_need == before.entertainment_need


def _sleep_until_it_ends(engine, slept: str) -> tuple[BodyState, BodyState, dict]:
    """推进到睡眠结束的那个边界：途中每个区间都按睡眠计；返回结束那个边界的（推进前，推进后，结算正文）。"""
    while True:
        before, after, settlement = _advance(engine)
        assert slept in settlement["activity_ids"]
        _assert_asleep_interval(engine, before, after, settlement)
        if engine.context.state.activities[slept].status != ActivityStatus.ACTIVE:
            return before, after, settlement


def test_sleep_completing_at_a_boundary_leaves_social_need_unchanged(engine_factory) -> None:
    """睡眠在边界按计划完成：结束的那段区间仍按睡眠计，社交需要不变（旧口径推进后才看是否在睡，把它整段算成清醒）。"""
    engine = engine_factory()
    settle_day_plans(engine)
    _stage_body(engine, social_need=0.2, entertainment_need=0.3)
    slept = _start_sleep(engine, "night")
    end = engine.context.state.activities[slept].planned_end_at

    _, after, settlement = _sleep_until_it_ends(engine, slept)

    sleep = engine.context.state.activities[slept]
    assert (engine.context.state.current_time, sleep.status, sleep.ended_at, sleep.boundary_type) == (
        end, ActivityStatus.COMPLETED, end, "planned_end")
    assert settlement["interval_end"] == end and after.social_need == 0.2
    assert "wake_from_sleep" in settlement["wake_reasons"]


def test_sleep_ended_early_by_a_reminder_leaves_social_need_unchanged(engine_factory) -> None:
    """睡眠被当日目标提醒提前结束：提醒那一刻结束的区间同样按睡眠计，社交需要不变。"""
    engine = engine_factory()
    settle_day_plans(engine)
    _stage_body(engine, social_need=0.2, entertainment_need=0.3)
    slept = _start_sleep(engine, "nap")
    remind = engine.context.state.current_time + 2 * 3600 + 7 * 60
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="晨间例程", goal="起床", registered_at=engine.context.state.current_time,
                                           remind_at=remind),))

    _, after, settlement = _sleep_until_it_ends(engine, slept)

    sleep = engine.context.state.activities[slept]
    assert (engine.context.state.current_time, sleep.status, sleep.ended_at, sleep.boundary_type) == (
        remind, ActivityStatus.COMPLETED, remind, "plan_reminder")
    assert sleep.planned_end_at > remind and settlement["interval_end"] == remind
    assert after.social_need == 0.2 and "plan_reminder_due" in settlement["wake_reasons"]


def test_first_awake_interval_after_waking_accrues_a_quarter_hour(engine_factory) -> None:
    """醒后第一段清醒区间（醒来收尾、开始等待后的 15 分钟）社交需要只增加 0.04 × 0.25；醒来那一刻不跳。"""
    engine = engine_factory()
    settle_day_plans(engine)
    _stage_body(engine, social_need=0.2)
    slept = _start_sleep(engine, "night")
    _sleep_until_it_ends(engine, slept)
    woke = engine.context.state.current_time
    assert engine.context.state.agents[1].body.social_need == 0.2
    _billing_decision(engine, "woke")
    _billing_decision(engine, "up")
    assert [item.kind for item in active_activities(engine.context.state, 1)] == [ActivityKind.WAIT]

    before, after, settlement = _advance(engine)

    assert settlement["interval_start"] == woke and _hours(settlement) == 0.25
    assert slept not in settlement["activity_ids"] and before.social_need == 0.2
    assert after.social_need - before.social_need == pytest.approx(SOCIAL_NEED_AWAKE_RATE * 0.25)


def _nap_or_default(engine) -> None:
    """性质用例的决策脚本：醒来收尾后等待；第一次醒来满一小时后、手上没有活动的决策机会去小睡，由 65 分钟后的
    提醒叫醒；其余机会按 _decide 的缺省应对（有待收尾先收尾，有进行中活动时播报，都没有时等待）。"""
    while engine.scheduling_state().ready_agent_ids:
        now = engine.context.state.current_time
        sleeps = [item for item in engine.context.state.activities.values() if item.kind == ActivityKind.SLEEP]
        idle = not active_activities(engine.context.state, 1) and not any(
            is_pending_conclusion(item) for item in engine.context.state.activities.values())
        if idle and len(sleeps) == 1 and sleeps[0].ended_at is not None and now - sleeps[0].ended_at >= 3600:
            stage_day_plan(engine, goals=(PlanGoal(intent_kind="休息", goal="小睡起来", registered_at=now,
                                                   remind_at=now + 65 * 60),))
            _billing_decision(engine, f"nap-{now}", ("start_activity", start_activity("睡觉", "自己")))
        else:
            _billing_decision(engine, f"round-{now}-{engine.context.state.version}")


def test_social_ledger_awake_classification_follows_the_settlement_activity_ids(engine_factory) -> None:
    """性质：真实运行的每个边界，社交账都按该区间共同身体结算的 activity_ids 分清醒与睡眠——集合含睡眠则不增长，
    否则按区间小时数 +0.04/小时。覆盖跨零点的睡眠（零点路径）、按计划结束与被提醒结束的睡眠和醒后的等待。"""
    engine = engine_factory(mutate=lambda document: document.update(
        start_at='2026-01-05T23:30:00+08:00', end_at='2026-01-07T00:00:00+08:00',
        bills=[], payrolls=[], messages=[]))
    settle_day_plans(engine)
    _stage_body(engine, social_need=0.0)
    _start_sleep(engine, "bedtime")
    seen: list[tuple[bool, bool]] = []
    while sum(ended for _, ended in seen) < 2 or not all(asleep is False for asleep, _ in seen[-3:]):
        assert len(seen) < 60, seen
        before, after, settlement = _advance(engine)
        sleeps = [engine.context.state.activities[item] for item in settlement["activity_ids"]
                  if engine.context.state.activities[item].kind == ActivityKind.SLEEP]
        expected = 0.0 if sleeps else SOCIAL_NEED_AWAKE_RATE * _hours(settlement)
        assert after.social_need - before.social_need == pytest.approx(expected, abs=1e-12), settlement
        seen.append((bool(sleeps), any(item.ended_at == engine.context.state.current_time for item in sleeps)))
        _nap_or_default(engine)
    boundary_types = sorted(item.boundary_type for item in engine.context.state.activities.values()
                            if item.kind == ActivityKind.SLEEP)
    assert boundary_types == ["plan_reminder", "planned_end"]
    assert sum(asleep for asleep, _ in seen) >= 3 and sum(not asleep for asleep, _ in seen) >= 5
    midnight = _settlement(engine, int(datetime(2026, 1, 6, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()))
    assert [engine.context.state.activities[item].kind for item in midnight["activity_ids"]] == [ActivityKind.SLEEP]











def test_a_call_ending_at_the_boundary_is_billed_once_by_the_call_ledger(engine_factory) -> None:
    """通话在边界按计划结束：每个区间（含结束的那一段）的社交需要只按通话结算推进一次（清醒 +0.04、通话 −0.4，
    每小时）。节律结算段按本区间共同身体结算的 activity_ids 判定交谈与通话覆盖，不按推进后的状态：通话在本边界
    已结束，节律结算段也不再叠加一次清醒增长。"""
    def contact(document):
        document['agents'][0]['contacts'] = [{'contact_id': 'mother', 'salutation': '母亲', 'relation': '母亲',
            'closeness': 24, 'channels': ['phone'], 'reply_policy': 'none'}]
    engine = engine_factory(mutate=contact)
    settle_day_plans(engine)
    _stage_body(engine, social_need=0.8)
    _, results = submit_round(engine, {1: ("start_activity", request("语音通话", "微信", to="联系人：母亲"))}, "call")
    assert results[0].status == "in_progress", results[0].error_code
    called = results[0].activity_id
    end = engine.context.state.activities[called].planned_end_at
    rate = SOCIAL_NEED_AWAKE_RATE - SOCIAL_NEED_REMOTE_RATE
    intervals = []
    while engine.context.state.activities[called].status == ActivityStatus.ACTIVE:
        while engine.scheduling_state().ready_agent_ids:
            _billing_decision(engine, f"call-{engine.context.state.version}")
        before, after, settlement = _advance(engine)
        assert called in settlement["activity_ids"]
        assert after.social_need - before.social_need == pytest.approx(rate * _hours(settlement)), settlement
        intervals.append((settlement["interval_start"], settlement["interval_end"]))

    call = engine.context.state.activities[called]
    assert (call.status, call.ended_at, engine.context.state.current_time) == (ActivityStatus.COMPLETED, end, end)
    assert intervals[-1][1] == end and not active_activities(engine.context.state, 1)


def _sleep_activity(engine, status: ActivityStatus, ended_at: int | None) -> ActivityState:
    """人物 1 的一项睡眠活动（直接调用节律用，不经受理）。"""
    now = engine.context.state.current_time
    return ActivityState(activity_id="sleep-fallback", agent_id=1, location_id=engine.context.state.agents[1].location_id,
                         kind=ActivityKind.SLEEP, status=status, started_at=now - 3600, planned_end_at=now + 3600,
                         last_settled_at=now - 3600, elapsed_seconds=3600, planned_duration_seconds=7200,
                         ended_at=ended_at, origin_action_id="act", origin_decision_id="dec",
                         origin_request_event_id="evt")


@pytest.mark.parametrize(("status", "ended", "asleep"), [
    (ActivityStatus.ACTIVE, None, True),
    (ActivityStatus.COMPLETED, 0, True),
    (ActivityStatus.COMPLETED, -60, False),
    (ActivityStatus.CANCELLED, 0, False),
])
def test_ledger_without_a_draft_falls_back_to_activities_ending_now(engine_factory, status, ended, asleep) -> None:
    """没有共同身体草稿（直接调用节律结算段）时兜底为 ACTIVE、或 COMPLETED 且 ended_at == now 的本人活动，与
    _woke_from_sleep 同一口径；带草稿时只按草稿的 activity_ids，不看推进后的状态。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    staged = engine.context.state.model_copy(deep=True)
    staged.activities["sleep-fallback"] = _sleep_activity(engine, status, None if ended is None else now + ended)
    staged.agents[1] = staged.agents[1].model_copy(update={"body": staged.agents[1].body.model_copy(
        update={"social_need": 0.2})}, deep=True)

    settled = rhythm.settle_decision_rhythm(rhythm.engine_rhythm_inputs(engine.context, staged, 900))

    assert settled.agents[1].body.social_need - 0.2 == pytest.approx(0.0 if asleep else SOCIAL_NEED_AWAKE_RATE * 0.25)
    assert rhythm._woke_from_sleep(staged, 1, now) is (status == ActivityStatus.COMPLETED and ended == 0)
    for listed, expected in ((["sleep-fallback"], 0.0), ([], SOCIAL_NEED_AWAKE_RATE * 0.25)):
        draft = {1: [{"kind": "body_settlement", "activity_ids": listed}]}
        inputs = rhythm.engine_rhythm_inputs(engine.context, staged, 900, progress_data=draft)
        assert rhythm.settle_decision_rhythm(inputs).agents[1].body.social_need - 0.2 == pytest.approx(expected)
