"""并行活动共同身体分段公式与真实引擎事件一致。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import pytest

from worldmock.catalog.affordances import engine_affordances
from worldmock.errors import BusinessRuleError
from worldmock.life.activities import activity_factors, advance_activity_shared, shared_activity_factors
from worldmock.life.body import ActivityFactors, last_flows, settle_shared_body
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, BodyState
from worldmock.life.world_setup import load_scenario
from worldmock.life.social import CALL_MET, CHAT_MET, SOCIAL_RESOURCE_CLAIMS
from worldmock.life.social_actions import advance_social_shared, social_activity_change


def test_shared_settlement_truncates_overdrawn_activity_sets_at_zero() -> None:
    """共同结算对任何活动集合都以 0 截断：步行加等待的成本超过余额时只扣掉余额，不报错。"""
    config = load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0].body
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
    """构造直接公共进度接口所需的有效活动；占用取第五章占用表（交谈、通话占说与听，其余按注册表）。"""
    social = kind in {ActivityKind.CHAT, ActivityKind.CALL}
    claims = set(SOCIAL_RESOURCE_CLAIMS) if social else set(engine_affordances().resource_claims(kind.value))
    return ActivityState(activity_id=f"activity-{kind.value}", agent_id=1,
                         location_id="home", kind=kind,
                         status=ActivityStatus.ACTIVE, started_at=now, planned_end_at=now + duration,
                         last_settled_at=now, elapsed_seconds=0, planned_duration_seconds=duration,
                         resource_claims=claims, origin_action_id="action", origin_decision_id="decision",
                         origin_request_event_id="request")


def test_shared_activity_factors_use_social_met_and_declared_factors(engine_factory) -> None:
    """结算与边界预测共用的单项系数：交谈、通话取社交常量 MET，其余活动与声明系数一致。"""
    engine = engine_factory()
    config = engine.context.scenario.body
    agent = engine.context.state.agents[1]
    now = engine.context.state.current_time
    for kind, met in ((ActivityKind.CHAT, CHAT_MET), (ActivityKind.CALL, CALL_MET)):
        assert shared_activity_factors(_active(kind, now), agent, config) == ActivityFactors(met)
    wait = _active(ActivityKind.WAIT, now)
    assert shared_activity_factors(wait, agent, config) == activity_factors(wait, agent, config)


def test_shared_settlement_and_activity_progress_reject_invalid_intervals(engine_factory) -> None:
    """负区间与暂停推进在公共边界拒绝；能量耗尽的非恢复活动照常结算到 0。"""
    engine = engine_factory()
    config = engine.context.scenario.body
    agent = engine.context.state.agents[1]
    now = engine.context.state.current_time
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
    """社交共同推进完成和非法控制使用同一生命周期；缓解按双方交谈同时进行的秒数结算（第五章「交谈」）。

    同时进行的秒数按交谈账本（每小时 0.5），其余秒数按发起方 0.2/小时，清醒增长照常累计。
    """
    engine = engine_factory()
    config = engine.context.scenario.body
    agent = engine.context.state.agents[1]
    agent = agent.model_copy(update={"body": agent.body.model_copy(update={"social_need": 0.5})}, deep=True)
    now = engine.context.state.current_time
    chat = _active(ActivityKind.CHAT, now)
    settlement = settle_shared_body(agent.body, ((ActivityKind.CHAT, ActivityFactors(CHAT_MET)),), 60, config)
    finished = advance_social_shared(chat, agent, now + 60, settlement, 60)
    assert finished.activity.status == ActivityStatus.COMPLETED
    hour = 60 / 3600
    relief = {shared: advance_social_shared(chat, agent, now + 60, settlement, shared).agent.body.social_need
              for shared in (0, 30, 60)}
    assert finished.agent.body.social_need == relief[60] == pytest.approx(0.5 + (0.04 - 0.5) * hour)
    assert relief[0] == pytest.approx(0.5 + (0.04 - 0.2) * hour)
    assert relief[30] == pytest.approx(0.5 + (0.04 - 0.5 / 2 - 0.2 / 2) * hour)
    with pytest.raises(ValueError, match="interval is invalid"):
        advance_social_shared(chat, agent, now - 1, settlement, 0)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_ALREADY_TERMINAL"):
        social_activity_change(agent, finished.activity, "cancel", now + 60)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_CANNOT_PAUSE"):
        social_activity_change(agent, chat, "pause", now)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_OPERATION_UNKNOWN"):
        social_activity_change(agent, chat, "switch", now)
    assert social_activity_change(agent, chat, "resume", now) == (agent, chat)
