"""身体数值变动经资源分录结算的方向、钳制、证据流与重放一致性测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import pytest

from worldmock.life.body import (
    BODY_FLOW_OCCURRED_AT,
    BODY_FLOW_OWNER,
    ENTERTAINMENT_RESOURCE,
    ENERGY_RESOURCE,
    SLEEP_DEFICIT_RESOURCE,
    ActivityFactors,
    apply_food,
    body_resource_metas,
    last_flows,
    minimum_energy_cost,
    settle_body,
)
from worldmock.life.models import ActivityKind, BodyState
from worldmock.life.world_setup import load_scenario
from worldmock.resources import FlowEntry, ResourceLedger, audit_level_flows


@pytest.fixture(scope="module")
def body_config():
    """读取正式冻结身体参数。"""
    return load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0].body


@pytest.fixture(autouse=True)
def _clean_flow_buffer():
    """隔离分录证据流：每个用例前后清空，只断言本用例产生的分录。"""
    last_flows()
    yield
    last_flows()


def test_resource_metas_declare_three_open_level_domains(body_config) -> None:
    """能量与娱乐走 open level 分录域；不足账界为 [0, 24]（纯函数钳制）。"""
    metas = {meta.resource_id: meta for meta in body_resource_metas(body_config)}

    assert set(metas) == {ENERGY_RESOURCE, SLEEP_DEFICIT_RESOURCE, ENTERTAINMENT_RESOURCE}
    assert metas[ENTERTAINMENT_RESOURCE].bounds_max == 1
    assert metas[ENTERTAINMENT_RESOURCE].bounds_min == 0
    assert metas[ENERGY_RESOURCE].bounds_max == body_config.maximum_energy_kcal
    assert metas[SLEEP_DEFICIT_RESOURCE].bounds_max == 24.0
    for meta in metas.values():
        assert meta.kind == "level"
        assert meta.conservation == "open"


def test_food_intake_records_single_energy_source(body_config) -> None:
    """进食供能是能量资源的一笔 source，金额等于未截断的吸收热量。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    result = apply_food(body, 200, body_config)
    flows = last_flows()

    expected = 180
    assert result.food_efficiency == pytest.approx(expected / 200)
    assert len(flows) == 1
    assert flows[0].resource == ENERGY_RESOURCE
    assert flows[0].direction == "source"
    assert flows[0].amount == expected
    assert flows[0].cause


def test_activity_energy_cost_records_combined_drain(body_config) -> None:
    """活动能量成本是一笔合并 drain，金额与领域成本函数完全一致。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    settle_body(body, ActivityKind.ACTION, 3600, body_config, ActivityFactors(1.5))
    energy_flows = [flow for flow in last_flows() if flow.resource == ENERGY_RESOURCE]

    assert len(energy_flows) == 1
    assert energy_flows[0].direction == "drain"
    assert energy_flows[0].amount == minimum_energy_cost(
        ActivityKind.ACTION, 3600, body_config, 1.5)
    assert "action" in energy_flows[0].cause


def test_awake_activity_accrues_deficit_via_two_process(body_config) -> None:
    """清醒按 need/(24-need) 累积睡眠不足账（纯函数演化，不分录）。"""
    from worldmock.life import sleep as sleep_module

    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=4, entertainment_need=0)
    result = settle_body(body, ActivityKind.ACTION, 1800, body_config, ActivityFactors(1.5))

    expected = sleep_module.deficit_after_awake(4, 0.5, body.sleep_need_hours)
    assert result.after.sleep_deficit_hours == pytest.approx(expected)
    assert [flow for flow in last_flows() if flow.resource == SLEEP_DEFICIT_RESOURCE] == []


def test_sleep_recovers_deficit_and_decays_pressure(body_config) -> None:
    """睡眠恢复不足账 1h/h 并衰减两过程压力（纯函数演化，不分录）。"""
    from worldmock.life import sleep as sleep_module

    body = BodyState(energy_kcal=1600, sleep_pressure=0.4, sleep_deficit_hours=5, entertainment_need=0)
    result = settle_body(body, ActivityKind.SLEEP, 3600, body_config, ActivityFactors(0.95))

    assert result.after.sleep_deficit_hours == pytest.approx(4)
    assert result.after.sleep_pressure == pytest.approx(
        sleep_module.pressure_after_sleep(0.4, 1.0))
    assert [flow for flow in last_flows() if flow.resource == SLEEP_DEFICIT_RESOURCE] == []


def test_sleep_emits_no_entertainment_flow(body_config) -> None:
    """睡眠期间娱乐需要保持不变，不产生娱乐分录。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=1, entertainment_need=0.4)
    result = settle_body(body, ActivityKind.SLEEP, 3600, body_config, ActivityFactors(0.95))

    assert result.after.entertainment_need == 0.4
    assert [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE] == []


def test_relief_activity_drains_entertainment_need(body_config) -> None:
    """娱乐动作按固定速率降低娱乐需要，是一笔 drain。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0.8)
    settle_body(body, ActivityKind.ACTION, 3600, body_config, ActivityFactors(1, 0.3))
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert len(flows) == 1
    assert flows[0].direction == "drain"
    assert flows[0].amount == pytest.approx(0.3)


def test_plain_awake_activity_accrues_entertainment_source(body_config) -> None:
    """无实际接触的清醒活动按清醒速率累积娱乐需要，是一笔 source。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0.2)
    result = settle_body(body, ActivityKind.WAIT, 3600, body_config)
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
        sleep_pressure=0.0, sleep_deficit_hours=0,
        entertainment_need=0,
    )
    result = apply_food(body, 200, body_config)
    flows = last_flows()

    assert result.after.energy_kcal == body_config.maximum_energy_kcal
    assert len(flows) == 1
    assert flows[0].direction == "source"
    assert flows[0].amount == headroom


def test_sleep_recovery_beyond_deficit_clamps_at_zero(body_config) -> None:
    """恢复量超过现有不足账时期末钳制为零（下限零，docs/design/04-simulation-engine.md「睡眠不足账与效率曲线」）。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0.5, entertainment_need=0)
    result = settle_body(body, ActivityKind.SLEEP, 3600, body_config, ActivityFactors(0.95))

    assert result.after.sleep_deficit_hours == 0


def test_entertainment_relief_beyond_need_drains_only_the_need(body_config) -> None:
    """缓解量超过现有娱乐需要时 drain 金额截断到需要本身，期末为零。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0.05)
    result = settle_body(body, ActivityKind.ACTION, 3600, body_config, ActivityFactors(1, 0.3))
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert result.after.entertainment_need == 0
    assert len(flows) == 1
    assert flows[0].amount == 0.05


def test_entertainment_accrual_beyond_one_clamps_at_one(body_config) -> None:
    """累积越过娱乐上限时 source 金额截断到余量，期末钉在 1。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0.95)
    result = settle_body(body, ActivityKind.WAIT, 3600, body_config)
    flows = [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]

    assert result.after.entertainment_need == 1
    assert len(flows) == 1
    assert flows[0].direction == "source"
    assert flows[0].amount == pytest.approx(1 - 0.95)


@pytest.mark.parametrize("kind,relief", [
    (ActivityKind.ACTION, 0),
    (ActivityKind.SLEEP, 0),
    (ActivityKind.ACTION, 0.3),
    (ActivityKind.WAIT, 0),
])
def test_every_flow_carries_cause_owner_and_stamp(body_config, kind, relief) -> None:
    """每条分录都带非空 cause、固定 owner 与占位时间戳。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=2, entertainment_need=0.3)
    settle_body(body, kind, 1800, body_config, ActivityFactors(1.2, relief))
    flows = last_flows()

    assert flows
    for flow in flows:
        assert isinstance(flow.cause, str) and flow.cause
        assert flow.owner == BODY_FLOW_OWNER
        assert flow.occurred_at == BODY_FLOW_OCCURRED_AT
        assert flow.resource in {ENERGY_RESOURCE, SLEEP_DEFICIT_RESOURCE, ENTERTAINMENT_RESOURCE}


def test_last_flows_returns_each_settlement_exactly_once(body_config) -> None:
    """last_flows 取走增量后再次调用为空，结算后重新产生新分录。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    settle_body(body, ActivityKind.ACTION, 3600, body_config, ActivityFactors(1.5))
    first = last_flows()

    assert first
    assert all(isinstance(flow, FlowEntry) for flow in first)
    assert last_flows() == ()
    settle_body(BodyState(energy_kcal=1500, sleep_pressure=0.0, sleep_deficit_hours=1, entertainment_need=0),
                ActivityKind.SLEEP, 1800, body_config, ActivityFactors(0.95))
    second = last_flows()

    assert second
    assert {flow.resource for flow in second} <= {
        ENERGY_RESOURCE, SLEEP_DEFICIT_RESOURCE, ENTERTAINMENT_RESOURCE}


def test_overdrawn_settlement_drains_only_the_remaining_energy(body_config) -> None:
    """透支结算不报错：能量分录只扣掉余额，期末以 0 截断（docs/design/04-simulation-engine.md「能量不拦人」）。"""
    last_flows()
    body = BodyState(energy_kcal=10, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    result = settle_body(body, ActivityKind.ACTION, 3600, body_config, ActivityFactors(1.5))

    energy = [flow for flow in last_flows() if flow.resource == ENERGY_RESOURCE]
    assert [flow.amount for flow in energy] == [pytest.approx(10)]
    assert result.after.energy_kcal == 0


def test_zero_duration_settlement_emits_no_flows(body_config) -> None:
    """零时长结算不产生分录，身体状态保持不变。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=1, entertainment_need=0.2)
    result = settle_body(body, ActivityKind.ACTION, 0, body_config, ActivityFactors(1.5))

    assert result.after.energy_kcal == 1600
    assert result.after.sleep_deficit_hours == 1
    assert result.after.entertainment_need == 0.2
    assert last_flows() == ()


def test_full_history_replay_matches_incremental_settlement(body_config) -> None:
    """全历史重放期末与逐次直接结算一致（1e-9 容差），并通过域级流水对账。"""
    ledger = ResourceLedger(body_resource_metas(body_config))
    states = [BodyState(energy_kcal=2350, sleep_pressure=0.0, sleep_deficit_hours=0.5, entertainment_need=0.95)]
    entries: list[FlowEntry] = []
    steps = [
        (ActivityKind.WAIT, 3600, None),
        (ActivityKind.SLEEP, 3600, ActivityFactors(0.95)),
        (ActivityKind.ACTION, 3600, ActivityFactors(1, 0.3)),
    ]
    for kind, seconds, factors in steps:
        states.append(settle_body(states[-1], kind, seconds, body_config, factors).after)
        entries.extend(last_flows())
    states.append(apply_food(states[-1], 200, body_config).after)
    entries.extend(last_flows())

    initial = {
        (ENERGY_RESOURCE, BODY_FLOW_OWNER): states[0].energy_kcal,
        (ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER): states[0].entertainment_need,
    }
    replayed = ledger.validate_history(initial, entries)
    final = states[-1]
    assert replayed[(ENERGY_RESOURCE, BODY_FLOW_OWNER)] == pytest.approx(
        final.energy_kcal, abs=1e-9)
    assert replayed[(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER)] == pytest.approx(
        final.entertainment_need, abs=1e-9)

    for resource, value in (
        (ENERGY_RESOURCE, final.energy_kcal),
        (ENTERTAINMENT_RESOURCE, final.entertainment_need),
    ):
        rows = audit_level_flows(ledger, resource, initial, entries,
                                 expected_final={BODY_FLOW_OWNER: value})
        assert rows and rows[0].owner == BODY_FLOW_OWNER
