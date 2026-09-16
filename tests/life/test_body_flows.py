"""身体数值变动经资源分录结算的方向、钳制、证据流与重放一致性测试。"""

from __future__ import annotations

import pytest

from worldmock.life.body import (
    BODY_FLOW_OCCURRED_AT,
    BODY_FLOW_OWNER,
    ENTERTAINMENT_RESOURCE,
    ENERGY_RESOURCE,
    SLEEP_DEBT_RESOURCE,
    ActivityFactors,
    apply_food,
    body_resource_metas,
    interpolate,
    last_flows,
    minimum_energy_cost,
    settle_body,
)
from worldmock.life.models import ActivityKind, BodyState
from worldmock.life.scenario import load_scenario
from worldmock.resources import FlowEntry, ResourceLedger, audit_level_flows


@pytest.fixture(scope="module")
def body_config():
    """读取正式冻结身体参数。"""
    return load_scenario("scenarios/white-collar-12-weeks.json")[0].body


@pytest.fixture(autouse=True)
def _clean_flow_buffer():
    """隔离分录证据流：每个用例前后清空，只断言本用例产生的分录。"""
    last_flows()
    yield
    last_flows()


def test_resource_metas_declare_three_open_level_domains(body_config) -> None:
    """三个身体资源都是 open level 域，界与现行 clamp 语义一致。"""
    metas = {meta.resource_id: meta for meta in body_resource_metas(body_config)}

    assert set(metas) == {ENERGY_RESOURCE, SLEEP_DEBT_RESOURCE, ENTERTAINMENT_RESOURCE}
    assert metas[ENTERTAINMENT_RESOURCE].bounds_max == 1
    assert metas[ENTERTAINMENT_RESOURCE].bounds_min == 0
    assert metas[ENERGY_RESOURCE].bounds_max == body_config.maximum_energy_kcal
    assert metas[SLEEP_DEBT_RESOURCE].bounds_max is None
    for meta in metas.values():
        assert meta.kind == "level"
        assert meta.conservation == "open"


def test_food_intake_records_single_energy_source(body_config) -> None:
    """进食供能是能量资源的一笔 source，金额等于未截断的吸收热量。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=0, entertainment_need=0)
    result = apply_food(body, 200, body_config)
    flows = last_flows()

    expected = 200 * interpolate(body_config.food_efficiency_curve, 0)
    assert result.food_efficiency == pytest.approx(expected / 200)
    assert len(flows) == 1
    assert flows[0].resource == ENERGY_RESOURCE
    assert flows[0].direction == "source"
    assert flows[0].amount == expected
    assert flows[0].cause


def test_activity_energy_cost_records_combined_drain(body_config) -> None:
    """活动能量成本是一笔合并 drain，金额与领域成本函数完全一致。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=0, entertainment_need=0)
    settle_body(body, ActivityKind.WORK, 3600, body_config, ActivityFactors(1.5))
    energy_flows = [flow for flow in last_flows() if flow.resource == ENERGY_RESOURCE]

    assert len(energy_flows) == 1
    assert energy_flows[0].direction == "drain"
    assert energy_flows[0].amount == minimum_energy_cost(
        ActivityKind.WORK, 3600, body_config, 1.5)
    assert "work" in energy_flows[0].cause


def test_awake_activity_accrues_sleep_debt_as_source(body_config) -> None:
    """清醒累积睡眠债是对睡眠债资源的 source 流入。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=4, entertainment_need=0)
    settle_body(body, ActivityKind.WORK, 1800, body_config, ActivityFactors(1.5))
    debt_flows = [flow for flow in last_flows() if flow.resource == SLEEP_DEBT_RESOURCE]

    assert len(debt_flows) == 1
    assert debt_flows[0].direction == "source"
    assert debt_flows[0].amount == pytest.approx(body_config.awake_sleep_debt_per_hour * 0.5)
    assert debt_flows[0].cause


def test_sleep_recovers_debt_as_drain(body_config) -> None:
    """睡眠恢复降低睡眠债，是对睡眠债资源的 drain 流出。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=5, entertainment_need=0)
    result = settle_body(body, ActivityKind.SLEEP, 3600, body_config, ActivityFactors(0.95))
    debt_flows = [flow for flow in last_flows() if flow.resource == SLEEP_DEBT_RESOURCE]

    assert len(debt_flows) == 1
    assert debt_flows[0].direction == "drain"
    assert debt_flows[0].amount == pytest.approx(body_config.sleep_recovery_per_hour)
    assert result.after.sleep_debt_hours == pytest.approx(3)


def test_sleep_emits_no_entertainment_flow(body_config) -> None:
    """睡眠期间娱乐需要保持不变，不产生娱乐分录。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=1, entertainment_need=0.4)
    result = settle_body(body, ActivityKind.SLEEP, 3600, body_config, ActivityFactors(0.95))

    assert result.after.entertainment_need == 0.4
    assert [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE] == []


def test_relief_activity_drains_entertainment_need(body_config) -> None:
    """实际接触按人物偏好系数降低娱乐需要，是一笔 drain。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=0, entertainment_need=0.8)
    settle_body(body, ActivityKind.WATCH, 3600, body_config, ActivityFactors(1, 0.21))
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert len(flows) == 1
    assert flows[0].direction == "drain"
    assert flows[0].amount == pytest.approx(0.21)


def test_plain_awake_activity_accrues_entertainment_source(body_config) -> None:
    """无实际接触的清醒活动按清醒速率累积娱乐需要，是一笔 source。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=0, entertainment_need=0.2)
    result = settle_body(body, ActivityKind.BROWSE, 3600, body_config)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert len(flows) == 1
    assert flows[0].direction == "source"
    assert flows[0].amount == pytest.approx(body_config.entertainment_need_per_awake_hour)
    assert result.after.entertainment_need == pytest.approx(0.28)


def test_food_energy_cap_limits_source_amount_to_headroom(body_config) -> None:
    """进食超出最大能量时 source 金额截断到可用余量，期末钉在上限。"""
    headroom = 50
    body = BodyState(
        energy_kcal=body_config.maximum_energy_kcal - headroom,
        sleep_debt_hours=0,
        entertainment_need=0,
    )
    result = apply_food(body, 200, body_config)
    flows = last_flows()

    assert result.after.energy_kcal == body_config.maximum_energy_kcal
    assert len(flows) == 1
    assert flows[0].direction == "source"
    assert flows[0].amount == headroom


def test_sleep_recovery_beyond_debt_drains_only_the_debt(body_config) -> None:
    """恢复量超过现有睡眠债时 drain 金额截断到债务本身，期末为零。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=0.5, entertainment_need=0)
    result = settle_body(body, ActivityKind.SLEEP, 3600, body_config, ActivityFactors(0.95))
    debt_flows = [flow for flow in last_flows() if flow.resource == SLEEP_DEBT_RESOURCE]

    assert result.after.sleep_debt_hours == 0
    assert len(debt_flows) == 1
    assert debt_flows[0].amount == 0.5


def test_entertainment_relief_beyond_need_drains_only_the_need(body_config) -> None:
    """缓解量超过现有娱乐需要时 drain 金额截断到需要本身，期末为零。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=0, entertainment_need=0.05)
    result = settle_body(body, ActivityKind.WATCH, 3600, body_config, ActivityFactors(1, 0.21))
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert result.after.entertainment_need == 0
    assert len(flows) == 1
    assert flows[0].amount == 0.05


def test_entertainment_accrual_beyond_one_clamps_at_one(body_config) -> None:
    """累积越过娱乐上限时 source 金额截断到余量，期末钉在 1。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=0, entertainment_need=0.95)
    result = settle_body(body, ActivityKind.BROWSE, 3600, body_config)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert result.after.entertainment_need == 1
    assert len(flows) == 1
    assert flows[0].direction == "source"
    assert flows[0].amount == pytest.approx(1 - 0.95)


@pytest.mark.parametrize("kind,relief", [
    (ActivityKind.WORK, 0),
    (ActivityKind.SLEEP, 0),
    (ActivityKind.WATCH, 0.21),
    (ActivityKind.BROWSE, 0),
    (ActivityKind.WAIT, 0),
    (ActivityKind.CONSUME, 0),
])
def test_every_flow_carries_cause_owner_and_stamp(body_config, kind, relief) -> None:
    """每条分录都带非空 cause、固定 owner 与占位时间戳。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=2, entertainment_need=0.3)
    settle_body(body, kind, 1800, body_config, ActivityFactors(1.2, relief))
    flows = last_flows()

    assert flows
    for flow in flows:
        assert isinstance(flow.cause, str) and flow.cause
        assert flow.owner == BODY_FLOW_OWNER
        assert flow.occurred_at == BODY_FLOW_OCCURRED_AT
        assert flow.resource in {ENERGY_RESOURCE, SLEEP_DEBT_RESOURCE, ENTERTAINMENT_RESOURCE}


def test_last_flows_returns_each_settlement_exactly_once(body_config) -> None:
    """last_flows 取走增量后再次调用为空，结算后重新产生新分录。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=0, entertainment_need=0)
    settle_body(body, ActivityKind.WORK, 3600, body_config, ActivityFactors(1.5))
    first = last_flows()

    assert first
    assert all(isinstance(flow, FlowEntry) for flow in first)
    assert last_flows() == ()
    settle_body(BodyState(energy_kcal=1500, sleep_debt_hours=1, entertainment_need=0),
                ActivityKind.SLEEP, 1800, body_config, ActivityFactors(0.95))
    second = last_flows()

    assert second
    assert {flow.resource for flow in second} <= {
        ENERGY_RESOURCE, SLEEP_DEBT_RESOURCE, ENTERTAINMENT_RESOURCE}


def test_rejected_energy_settlement_emits_no_flows(body_config) -> None:
    """能量不足被拒绝的结算不留下任何分录。"""
    body = BodyState(energy_kcal=10, sleep_debt_hours=0, entertainment_need=0)
    with pytest.raises(ValueError, match="exhaust"):
        settle_body(body, ActivityKind.WORK, 3600, body_config, ActivityFactors(1.5))

    assert last_flows() == ()


def test_zero_duration_settlement_emits_no_flows(body_config) -> None:
    """零时长结算不产生分录，身体状态保持不变。"""
    body = BodyState(energy_kcal=1600, sleep_debt_hours=1, entertainment_need=0.2)
    result = settle_body(body, ActivityKind.WORK, 0, body_config, ActivityFactors(1.5))

    assert result.after.energy_kcal == 1600
    assert result.after.sleep_debt_hours == 1
    assert result.after.entertainment_need == 0.2
    assert last_flows() == ()


def test_full_history_replay_matches_incremental_settlement(body_config) -> None:
    """全历史重放期末与逐次直接结算一致（1e-9 容差），并通过域级流水对账。"""
    ledger = ResourceLedger(body_resource_metas(body_config))
    states = [BodyState(energy_kcal=2350, sleep_debt_hours=0.5, entertainment_need=0.95)]
    entries: list[FlowEntry] = []
    steps = [
        (ActivityKind.BROWSE, 3600, None),
        (ActivityKind.SLEEP, 3600, ActivityFactors(0.95)),
        (ActivityKind.WATCH, 3600, ActivityFactors(1, 0.21)),
    ]
    for kind, seconds, factors in steps:
        states.append(settle_body(states[-1], kind, seconds, body_config, factors).after)
        entries.extend(last_flows())
    states.append(apply_food(states[-1], 200, body_config).after)
    entries.extend(last_flows())

    initial = {
        (ENERGY_RESOURCE, BODY_FLOW_OWNER): states[0].energy_kcal,
        (SLEEP_DEBT_RESOURCE, BODY_FLOW_OWNER): states[0].sleep_debt_hours,
        (ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER): states[0].entertainment_need,
    }
    replayed = ledger.validate_history(initial, entries)
    final = states[-1]
    assert replayed[(ENERGY_RESOURCE, BODY_FLOW_OWNER)] == pytest.approx(
        final.energy_kcal, abs=1e-9)
    assert replayed[(SLEEP_DEBT_RESOURCE, BODY_FLOW_OWNER)] == pytest.approx(
        final.sleep_debt_hours, abs=1e-9)
    assert replayed[(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER)] == pytest.approx(
        final.entertainment_need, abs=1e-9)

    for resource, value in (
        (ENERGY_RESOURCE, final.energy_kcal),
        (SLEEP_DEBT_RESOURCE, final.sleep_debt_hours),
        (ENTERTAINMENT_RESOURCE, final.entertainment_need),
    ):
        rows = audit_level_flows(ledger, resource, initial, entries,
                                 expected_final={BODY_FLOW_OWNER: value})
        assert rows and rows[0].owner == BODY_FLOW_OWNER
