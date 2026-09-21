"""等待活动不隐含观看或其他娱乐，需求只按清醒时间增长。"""

from __future__ import annotations

import pytest

from worldmock.life.activities import ActivityOrigin, advance_activity, start_activity
from worldmock.life.body import (ENTERTAINMENT_RESOURCE, last_flows,
                                 minimum_energy_cost, settle_body)
from worldmock.life.models import ActivityKind, AgentState, BodyState, StartActivityRequest
from worldmock.life.scenario import load_scenario


@pytest.fixture(scope="module")
def body_config():
    """读取正式冻结身体参数。"""
    return load_scenario("scenarios/white-collar-12-weeks.json")[0].body


@pytest.fixture(autouse=True)
def _clean_flow_buffer():
    """按测试隔离全局身体分录。"""
    last_flows()
    yield
    last_flows()


def _body(need: float = 0.4) -> BodyState:
    """建立测试人物的身体。"""
    return BodyState(energy_kcal=1600, sleep_pressure=0.0,
                     sleep_deficit_hours=0, entertainment_need=need)


def test_wait_has_only_awake_entertainment_accrual(body_config) -> None:
    """等待没有任何娱乐缓解分录，清醒需求按时间增加。"""
    result = settle_body(_body(), ActivityKind.WAIT, 3600, body_config)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]
    assert [(flow.direction, flow.cause) for flow in flows] == [
        ("source", "settle:wait:awake_entertainment_need")]
    assert result.after.entertainment_need == pytest.approx(
        0.4 + body_config.entertainment_need_per_awake_hour)


def test_wait_has_no_relief_catalog_entry(body_config) -> None:
    """正式目录没有将等待声明为可缓解娱乐需要的活动。"""
    assert "wait" not in body_config.entertainment_relief_per_hour
    assert body_config.entertainment_relief_per_hour["watch"] > 0


def test_wait_met_cost_remains_declared(body_config) -> None:
    """删去被动娱乐不改变等待的 MET 与能量消耗。"""
    met = body_config.activity_met["wait"]
    expected = body_config.basal_kcal_per_hour + max(met - 1, 0) * body_config.mass_kg
    assert minimum_energy_cost(ActivityKind.WAIT, 3600, body_config) == pytest.approx(expected)


def test_wait_at_full_need_stays_full(body_config) -> None:
    """需求已到上限时，等待既不提高上限也不会偷偷降低需求。"""
    result = settle_body(_body(1.0), ActivityKind.WAIT, 3600, body_config)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]
    assert flows == []
    assert result.after.entertainment_need == 1.0


def test_wait_matches_other_non_entertainment_activity(body_config) -> None:
    """同一清醒区间，等待和浏览的娱乐需求相同。"""
    waited = settle_body(_body(), ActivityKind.WAIT, 7200, body_config)
    browsed = settle_body(_body(), ActivityKind.BROWSE, 7200, body_config)
    assert waited.after.entertainment_need == browsed.after.entertainment_need


def test_direct_wait_activity_preserves_plain_accrual(body_config) -> None:
    """领域活动入口同样不添加隐藏的娱乐效果。"""
    agent = AgentState(agent_id=1, name="Lin", location_id="home",
                       balance_account_id="agent:1", body_settled_at=0,
                       next_decision_at=0, body=_body())
    request = StartActivityRequest(activity_type=ActivityKind.WAIT,
                                   planned_duration_seconds=3600)
    person, activity = start_activity(agent, request, 0,
                                      ActivityOrigin("a", "d", "e"), body_config)
    advanced = advance_activity(activity, person, 3600, body_config)
    assert advanced.completed
    assert advanced.agent.body.entertainment_need == pytest.approx(
        0.4 + body_config.entertainment_need_per_awake_hour)
