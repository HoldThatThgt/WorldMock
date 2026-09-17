"""wait=边等边刷：被动娱乐缓解的分录证据、MET 不变性与净值效果测试。"""

from __future__ import annotations

import pytest

from worldmock.life.activities import ActivityOrigin, advance_activity, start_activity
from worldmock.life.body import (
    ENTERTAINMENT_RESOURCE,
    last_flows,
    minimum_energy_cost,
    settle_body,
)
from worldmock.life.models import ActivityKind, AgentState, BodyState, StartActivityRequest
from worldmock.life.scenario import load_scenario


@pytest.fixture(scope="module")
def body_config():
    """读取正式冻结身体参数（目录经生成器再生后含 wait 被动缓解费率）。"""
    return load_scenario("scenarios/white-collar-12-weeks.json")[0].body


@pytest.fixture(autouse=True)
def _clean_flow_buffer():
    """隔离分录证据流：每个用例前后清空，只断言本用例产生的分录。"""
    last_flows()
    yield
    last_flows()


def _body(need: float = 0.4) -> BodyState:
    return BodyState(energy_kcal=1600, sleep_debt_hours=0, entertainment_need=need)


def test_wait_settles_passive_relief_flow_entries(body_config) -> None:
    """wait 结算产生清醒累积 source 与被动缓解 drain 两笔娱乐分录。"""
    rate = body_config.entertainment_relief_per_hour["wait"]
    accrual_rate = body_config.entertainment_need_per_awake_hour

    result = settle_body(_body(0.4), ActivityKind.WAIT, 3600, body_config)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    by_cause = {flow.cause: flow for flow in flows}
    assert set(by_cause) == {"settle:wait:awake_entertainment_need",
                             "settle:wait:passive_entertainment"}
    assert by_cause["settle:wait:awake_entertainment_need"].direction == "source"
    assert by_cause["settle:wait:awake_entertainment_need"].amount == pytest.approx(accrual_rate)
    assert by_cause["settle:wait:passive_entertainment"].direction == "drain"
    assert by_cause["settle:wait:passive_entertainment"].amount == pytest.approx(rate)
    assert result.after.entertainment_need == pytest.approx(0.4 + accrual_rate - rate)


def test_wait_passive_rate_is_small_fraction_of_active_relief(body_config) -> None:
    """被动刷手机费率为正且远低于主动观看/游戏（约主动的 15%）。"""
    relief = body_config.entertainment_relief_per_hour

    assert 0 < relief["wait"] < relief["watch"]
    assert relief["wait"] == pytest.approx(0.05)
    assert relief["wait"] / relief["watch"] == pytest.approx(1 / 6, abs=0.05)


def test_wait_met_unchanged_by_passive_relief(body_config) -> None:
    """被动缓解不改变代谢：wait 的 MET 与完整能量成本保持冻结值。"""
    assert body_config.activity_met["wait"] == pytest.approx(1.3)
    expected = (body_config.basal_kcal_per_hour
                + max(body_config.activity_met["wait"] - 1, 0) * body_config.mass_kg)
    assert minimum_energy_cost(ActivityKind.WAIT, 3600, body_config) == pytest.approx(expected)


def test_wait_passive_relief_clamps_to_available_balance(body_config) -> None:
    """缓解量超过区间内可用娱乐需要时 drain 截断到期末余额，不为负。"""
    inflated = body_config.model_copy(update={"entertainment_relief_per_hour": {
        **body_config.entertainment_relief_per_hour, "wait": 0.5}})

    result = settle_body(_body(0.0), ActivityKind.WAIT, 3600, inflated)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    drain = next(flow for flow in flows
                 if flow.cause == "settle:wait:passive_entertainment")
    balance = body_config.entertainment_need_per_awake_hour  # 0 + 累积即全部可用
    assert drain.amount == pytest.approx(balance)
    assert result.after.entertainment_need == pytest.approx(0)


def test_wait_at_full_need_still_drains_and_not_pinned(body_config) -> None:
    """累积触顶后被动缓解依旧生效，期末按 drain 落地而非钉在 1。"""
    result = settle_body(_body(1.0), ActivityKind.WAIT, 3600, body_config)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert [flow.cause for flow in flows] == ["settle:wait:passive_entertainment"]
    assert result.after.entertainment_need == pytest.approx(
        1 - body_config.entertainment_relief_per_hour["wait"])


def test_wait_without_catalog_key_keeps_plain_accrual(body_config) -> None:
    """目录无 wait 键时退回纯清醒累积，旧夹具语义不受影响。"""
    legacy = body_config.model_copy(update={"entertainment_relief_per_hour": {
        key: value for key, value in body_config.entertainment_relief_per_hour.items()
        if key != "wait"}})

    result = settle_body(_body(0.4), ActivityKind.WAIT, 3600, legacy)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert [flow.direction for flow in flows] == ["source"]
    assert result.after.entertainment_need == pytest.approx(
        0.4 + body_config.entertainment_need_per_awake_hour)


def test_wait_leaves_lower_need_than_awake_activity_without_wait(body_config) -> None:
    """同一时长 wait 后的娱乐需要低于不带被动缓解的普通清醒活动。"""
    waited = settle_body(_body(0.4), ActivityKind.WAIT, 7200, body_config)
    browsed = settle_body(_body(0.4), ActivityKind.BROWSE, 7200, body_config)

    assert waited.after.entertainment_need < browsed.after.entertainment_need
    assert browsed.after.entertainment_need - waited.after.entertainment_need == pytest.approx(
        body_config.entertainment_relief_per_hour["wait"] * 2)


def _agent(need: float) -> AgentState:
    return AgentState(agent_id=1, name="Lin", location_id="home",
                      balance_account_id="agent:1", next_decision_at=0,
                      body=_body(need), skill_levels={"ami": 0.8},
                      entertainment_preferences={}, inventory={})


def test_engine_path_wait_activity_advances_with_passive_relief(body_config) -> None:
    """活动推进全路径（start→advance→factors）结算出被动缓解净值。"""
    request = StartActivityRequest(activity_type=ActivityKind.WAIT,
                                   planned_duration_seconds=3600)
    person, activity = start_activity(_agent(0.4), request, 0,
                                      ActivityOrigin("a", "d", "e"), body_config)

    result = advance_activity(activity, person, 3600, body_config)

    assert result.completed
    assert result.agent.body.entertainment_need == pytest.approx(
        0.4 + body_config.entertainment_need_per_awake_hour
        - body_config.entertainment_relief_per_hour["wait"])
