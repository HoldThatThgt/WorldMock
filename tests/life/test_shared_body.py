"""并行活动共同身体分段公式与真实引擎事件一致。"""

# TODO: parallel activities removed from spec - most tests in this file are for parallel mechanics
# The shared body mechanics tested here (shared_energy_cost, settle_shared_body, etc.) were designed
# specifically for parallel activity execution, which is no longer in the 7-tool model.

from __future__ import annotations

import pytest

from tests.life.conftest import advance_next, context
from worldmock.errors import BusinessRuleError
from worldmock.life.activities import activity_factors, advance_activity_shared, shared_activity_factors
from worldmock.life.body import ActivityFactors, last_flows, settle_shared_body
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, BodyState
from worldmock.life.scenario import load_scenario
from worldmock.life.social_actions import (CALL_MET, CHAT_MET, advance_social_shared,
                                           social_activity_change)


# TODO: parallel activities removed from spec
# def _watch_wait(reverse: bool = False) -> StartParallelActivitiesRequest:
#     """构造同资源集合、相反顺序的两项活动。"""
#     rows = [StartActivityRequest(activity_type="wait", planned_duration_seconds=120),
#             StartActivityRequest(activity_type="watch", target_id="sintel-en-subtitles",
#                                  planned_duration_seconds=60, playback_speed=1)]
#     return StartParallelActivitiesRequest(activities=tuple(reversed(rows) if reverse else rows))


# TODO: parallel activities removed from spec
# def _first_body(engine, reverse: bool) -> dict:
#     """真实决策启动组合后推进至首个播放结束点。"""
#     ctx = context(engine, f"shared-{reverse}")
#     engine.begin_decision_round([ctx])
#     engine.observe(ctx)
#     engine.submit_action(DirectToolCall(ctx, f"parallel-{reverse}",
#                                         "start_parallel_activities", _watch_wait(reverse)))
#     accepted = engine.commit_decision_round(ctx.round_id)[0]
#     assert accepted.status == "in_progress"
#     while engine.state.current_time < min(item.planned_end_at for item in engine.state.activities.values()):
#         advance_next(engine)
#     events = engine.store.read_agent_events("environment_event", 1)
#     return next(event.data for event in events if event.data.get("kind") == "body_settlement")


# TODO: parallel activities removed from spec
# def test_real_parallel_body_uses_maximum_met_and_is_order_independent(engine_factory) -> None:
#     """两种提交顺序都只扣一次基础能量，最大 MET 与共同身体状态一致。"""
#     first = _first_body(engine_factory(), False)
#     second = _first_body(engine_factory(), True)
#     config = load_scenario("scenarios/white-collar-12-weeks.json")[0].body
#     expected = shared_energy_cost(((60, config.activity_met["wait"]),
#                                    (60, config.activity_met["watch"])), config)
#     assert first["body_settlement"] == second["body_settlement"]
#     assert first["body_settlement"]["basal_energy_kcal"] == pytest.approx(
#         config.basal_kcal_per_hour / 60)
#     assert first["body_settlement"]["extra_energy_kcal"] == pytest.approx(
#         (max(config.activity_met["wait"], config.activity_met["watch"]) - 1) * config.mass_kg / 60,
#         abs=0.01)
#     before = first["body_settlement"]["before"]["energy_kcal"]
#     after = first["body_settlement"]["after"]["energy_kcal"]
#     assert before - after == pytest.approx(expected)


def test_shared_settlement_truncates_overdrawn_activity_sets_at_zero() -> None:
    """共同结算对任何活动集合都以 0 截断：步行加等待的成本超过余额时只扣掉余额，不报错。"""
    config = load_scenario("scenarios/white-collar-12-weeks.json")[0].body
    walking = config.activity_met["travel:walking"]
    last_flows()
    body = BodyState(energy_kcal=5, entertainment_need=0.3)
    settled = settle_shared_body(body, ((ActivityKind.WAIT, ActivityFactors(config.activity_met["wait"])),
                                        (ActivityKind.TRAVEL, ActivityFactors(walking))), 1800, config)
    energy = [flow for flow in last_flows() if flow.resource == "energy_kcal"]
    assert settled.after.energy_kcal == 0
    assert [flow.amount for flow in energy] == [pytest.approx(5)]
    assert settled.basal_energy_kcal + settled.extra_energy_kcal == pytest.approx(
        (config.basal_kcal_per_hour + (walking - 1) * config.mass_kg) / 2)


def _active(kind: ActivityKind, now: int, duration: int = 60) -> ActivityState:
    """构造直接公共进度接口所需的有效活动。"""
    claims = {"position", "attention"} if kind in {ActivityKind.CHAT, ActivityKind.CALL} else {"position"}
    return ActivityState(activity_id=f"activity-{kind.value}", agent_id=1,
                         location_id="home", kind=kind,
                         status=ActivityStatus.ACTIVE, started_at=now, planned_end_at=now + duration,
                         last_settled_at=now, elapsed_seconds=0, planned_duration_seconds=duration,
                         resource_claims=claims, origin_action_id="action", origin_decision_id="decision",
                         origin_request_event_id="request")


def test_shared_activity_factors_use_social_met_and_declared_factors(engine_factory) -> None:
    """结算与边界预测共用的单项系数：交谈、通话取社交常量 MET，其余活动与声明系数一致。"""
    engine = engine_factory()
    config = engine.scenario.body
    agent = engine.state.agents[1]
    now = engine.state.current_time
    for kind, met in ((ActivityKind.CHAT, CHAT_MET), (ActivityKind.CALL, CALL_MET)):
        assert shared_activity_factors(_active(kind, now), agent, config) == ActivityFactors(met)
    wait = _active(ActivityKind.WAIT, now)
    assert shared_activity_factors(wait, agent, config) == activity_factors(wait, agent, config)


def test_shared_settlement_and_activity_progress_reject_invalid_intervals(engine_factory) -> None:
    """负区间与暂停推进在公共边界拒绝；能量耗尽的非恢复活动照常结算到 0。"""
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
    watched = settle_shared_body(empty, ((ActivityKind.WATCH, ActivityFactors(1.3)),), 60, config)
    assert watched.after.energy_kcal == 0


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
