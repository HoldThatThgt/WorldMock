"""并行活动共同身体分段公式与真实引擎事件一致。"""

from __future__ import annotations

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.errors import BusinessRuleError
from worldmock.life.activities import (active_energy_entries, advance_activity_shared,
                                       combined_energy_cost, maximum_combined_duration)
from worldmock.life.body import (ActivityFactors, last_flows, maximum_shared_energy_duration,
                                 settle_shared_body, shared_energy_cost)
from worldmock.life.models import (ActivityKind, ActivityState, ActivityStatus, BodyState,
                                   StartActivityRequest, StartBasicActivityRequest,
                                   StartParallelActivitiesRequest, StartTargetedActivityRequest)
from worldmock.life.scenario import load_scenario
from worldmock.life.social_actions import (CALL_MET, CHAT_MET, advance_social_shared,
                                           social_activity_change)


def _watch_wait(reverse: bool = False) -> StartParallelActivitiesRequest:
    """构造同资源集合、相反顺序的两项活动。"""
    rows = [StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120),
            StartTargetedActivityRequest(activity_type="watch", target_id="sintel-en-subtitles",
                                         planned_duration_seconds=60, playback_speed=1)]
    return StartParallelActivitiesRequest(activities=tuple(reversed(rows) if reverse else rows))


def _first_body(engine, reverse: bool) -> dict:
    """真实决策启动组合后推进至首个播放结束点。"""
    ctx = context(engine, f"shared-{reverse}")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, f"parallel-{reverse}",
                                        "start_parallel_activities", _watch_wait(reverse)))
    accepted = engine.commit_decision_round(ctx.round_id)[0]
    assert accepted.status == "in_progress"
    while engine.state.current_time < min(item.planned_end_at for item in engine.state.activities.values()):
        advance_next(engine)
    events = engine.store.read_agent_events("environment_event", 1)
    return next(event.data for event in events if event.data.get("kind") == "body_settlement")


def test_real_parallel_body_uses_maximum_met_and_is_order_independent(engine_factory) -> None:
    """两种提交顺序都只扣一次基础能量，最大 MET 与共同身体状态一致。"""
    first = _first_body(engine_factory(), False)
    second = _first_body(engine_factory(), True)
    config = load_scenario("scenarios/white-collar-12-weeks.json")[0].body
    expected = shared_energy_cost(((60, config.activity_met["wait"]),
                                   (60, config.activity_met["watch"])), config)
    assert first["body_settlement"] == second["body_settlement"]
    assert first["body_settlement"]["basal_energy_kcal"] == pytest.approx(
        config.basal_kcal_per_hour / 60)
    assert first["body_settlement"]["extra_energy_kcal"] == pytest.approx(
        (max(config.activity_met["wait"], config.activity_met["watch"]) - 1) * config.mass_kg / 60,
        abs=0.01)
    before = first["body_settlement"]["before"]["energy_kcal"]
    after = first["body_settlement"]["after"]["energy_kcal"]
    assert before - after == pytest.approx(expected)


def test_shared_energy_duration_matches_actual_settlement_at_limit() -> None:
    """组合预测的最长整秒可行时长与每段实际结算使用同一最大 MET。"""
    config = load_scenario("scenarios/white-collar-12-weeks.json")[0].body
    existing = ((1800, config.activity_met["wait"]),)
    met = config.activity_met["travel:walking"]
    budget = shared_energy_cost(existing + ((1200, met),), config)
    accepted = maximum_shared_energy_duration(existing, met, budget, config, 3600)
    assert accepted == 1200
    assert shared_energy_cost(existing + ((accepted + 1, met),), config) > budget
    body = BodyState(energy_kcal=budget + 1, entertainment_need=0.3)
    first = settle_shared_body(body, ((ActivityKind.WAIT, ActivityFactors(config.activity_met["wait"])),
                                      (ActivityKind.TRAVEL, ActivityFactors(met))), accepted, config)
    last_flows()
    remaining = 1800 - accepted
    second = settle_shared_body(first.after,
                                ((ActivityKind.WAIT, ActivityFactors(config.activity_met["wait"])),),
                                remaining, config)
    last_flows()
    assert body.energy_kcal - second.after.energy_kcal == pytest.approx(budget)
    assert second.after.energy_kcal == pytest.approx(1)


def _active(kind: ActivityKind, now: int, duration: int = 60) -> ActivityState:
    """构造直接公共进度接口所需的有效活动。"""
    claims = {"position", "attention"} if kind in {ActivityKind.CHAT, ActivityKind.CALL} else {"position"}
    return ActivityState(activity_id=f"activity-{kind.value}", agent_id=1, kind=kind,
                         status=ActivityStatus.ACTIVE, started_at=now, planned_end_at=now + duration,
                         last_settled_at=now, elapsed_seconds=0, planned_duration_seconds=duration,
                         resource_claims=claims, origin_action_id="action", origin_decision_id="decision",
                         origin_request_event_id="request")


def test_combined_energy_public_predictors_include_social_met_and_candidate(engine_factory) -> None:
    """共同剩余预测读取每项社交 MET，候选与现有任务使用同一分段成本。"""
    engine = engine_factory()
    config = engine.scenario.body
    now = engine.state.current_time
    for kind, met in ((ActivityKind.CHAT, CHAT_MET), (ActivityKind.CALL, CALL_MET)):
        state = engine.state.model_copy(update={"activities": {kind.value: _active(kind, now)}}, deep=True)
        assert active_energy_entries(state, 1, config) == ((60, met),)
        assert combined_energy_cost(state, 1, config) == pytest.approx(shared_energy_cost(((60, met),), config))
        candidate = StartActivityRequest(activity_type="wait", planned_duration_seconds=120)
        expected = shared_energy_cost(((60, met), (120, config.activity_met["wait"])), config)
        assert combined_energy_cost(state, 1, config, candidate) == pytest.approx(expected)
        assert maximum_combined_duration(state, 1, config, candidate, 120) == 120


def test_shared_settlement_and_activity_progress_reject_invalid_intervals(engine_factory) -> None:
    """负区间、耗尽非恢复活动与暂停推进均在公共边界拒绝。"""
    engine = engine_factory()
    config = engine.scenario.body
    agent = engine.state.agents[1]
    now = engine.state.current_time
    wait = _active(ActivityKind.WAIT, now)
    settlement = settle_shared_body(agent.body, ((ActivityKind.WAIT, ActivityFactors(1.3)),), 60, config)
    advanced = advance_activity_shared(wait, agent, now + 60, settlement)
    assert advanced.activity.status == ActivityStatus.COMPLETED
    with pytest.raises(ValueError, match="interval is invalid"):
        advance_activity_shared(wait, agent, now - 1, settlement)
    with pytest.raises(ValueError, match="interval is invalid"):
        advance_activity_shared(wait.model_copy(update={"status": ActivityStatus.PAUSED}),
                                agent, now + 1, settlement)
    with pytest.raises(ValueError, match="negative"):
        settle_shared_body(agent.body, (), -1, config)
    empty = agent.body.model_copy(update={"energy_kcal": 0})
    with pytest.raises(ValueError, match="exhaust"):
        settle_shared_body(empty, ((ActivityKind.WATCH, ActivityFactors(1.3)),), 60, config)
    with pytest.raises(ValueError, match="inputs are invalid"):
        maximum_shared_energy_duration((), 1.3, -1, config, 60)


def test_social_shared_progress_and_control_public_boundaries(engine_factory) -> None:
    """社交共同推进完成和非法控制使用同一生命周期。"""
    engine = engine_factory()
    config = engine.scenario.body
    agent = engine.state.agents[1]
    now = engine.state.current_time
    chat = _active(ActivityKind.CHAT, now)
    settlement = settle_shared_body(agent.body, ((ActivityKind.CHAT, ActivityFactors(CHAT_MET)),), 60, config)
    finished = advance_social_shared(chat, agent, now + 60, settlement, True)
    assert finished.activity.status == ActivityStatus.COMPLETED
    assert finished.agent.body.social_need <= agent.body.social_need
    with pytest.raises(ValueError, match="interval is invalid"):
        advance_social_shared(chat, agent, now - 1, settlement, False)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_ALREADY_TERMINAL"):
        social_activity_change(agent, finished.activity, "cancel", now + 60)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_CANNOT_PAUSE"):
        social_activity_change(agent, chat, "pause", now)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_OPERATION_UNKNOWN"):
        social_activity_change(agent, chat, "switch", now)
    assert social_activity_change(agent, chat, "resume", now) == (agent, chat)
