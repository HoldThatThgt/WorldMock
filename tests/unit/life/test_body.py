"""身体曲线和资源结算的独立数值测试。"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT

import math

import pytest

from worldmock.life.body import (
    ActivityFactors,
    apply_food,
    last_flows,
    maximum_energy_duration_seconds,
    minimum_energy_cost,
    settle_body,
)
from worldmock.life.models import ActivityKind, BodyState
from worldmock.life.world_setup import load_scenario


@pytest.fixture(scope="module")
def body_config():
    """读取正式冻结身体参数。"""
    return load_scenario(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json")[0].body




def test_awake_and_sleep_settle_independent_resources(body_config) -> None:
    """清醒增加睡眠不足，睡眠恢复不足且两者都消耗基础能量。"""
    initial = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=4, entertainment_need=0.2)
    awake = settle_body(initial, ActivityKind.ACTION, 3600, body_config, ActivityFactors(1.5))
    assert awake.after.energy_kcal == pytest.approx(1500.0)
    # 两过程：清醒按 need/(24-need) 累积不足账（7.5h 需要量即约 0.4545/h）
    assert awake.after.sleep_deficit_hours == pytest.approx(4 + 7.5 / 16.5)
    assert awake.after.entertainment_need == pytest.approx(0.28)
    asleep = settle_body(awake.after, ActivityKind.SLEEP, 3600, body_config, ActivityFactors(0.95))
    assert asleep.after.energy_kcal == pytest.approx(1435.0)
    assert asleep.after.sleep_deficit_hours == pytest.approx(4 + 7.5 / 16.5 - 1)
    assert asleep.after.entertainment_need == pytest.approx(0.28)


def test_entertainment_uses_fixed_app_content_relief(body_config) -> None:
    """娱乐动作按固定速率降低娱乐需要。"""
    body = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0.8)
    result = settle_body(body, ActivityKind.ACTION, 3600, body_config, ActivityFactors(1, 0.3))
    assert result.after.entertainment_need == pytest.approx(0.5)


def test_food_uses_sleep_debt_curve_and_energy_cap(body_config) -> None:
    """食物按进食时睡眠不足折算并遵守最大能量。"""
    body = BodyState(energy_kcal=2300, sleep_pressure=0.0, sleep_deficit_hours=12, entertainment_need=0)
    result = apply_food(body, 200, body_config)
    assert result.food_efficiency == pytest.approx(0.875)
    assert result.after.energy_kcal == 2400


def test_overdrawn_work_settles_to_zero_energy_without_error(body_config) -> None:
    """能量不拦人：工作一小时的成本远超余额时照常结算，能量以 0 截断，只扣掉余额。"""
    body = BodyState(energy_kcal=10, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    last_flows()
    result = settle_body(body, ActivityKind.ACTION, 3600, body_config, ActivityFactors(1.5))
    assert result.after.energy_kcal == 0
    assert result.basal_energy_kcal + result.extra_energy_kcal == pytest.approx(100.0)
    drains = [flow for flow in last_flows() if flow.resource == "energy_kcal"]
    assert [flow.amount for flow in drains] == [pytest.approx(10)]
    # 清醒过程照常推进：透支不影响睡眠压力与不足账的演化。
    assert result.after.sleep_deficit_hours == pytest.approx(7.5 / 16.5)
    assert result.after.sleep_pressure > body.sleep_pressure


@pytest.mark.parametrize("kind", [ActivityKind.ACTION, ActivityKind.TRAVEL,
                                  ActivityKind.WAIT, ActivityKind.SLEEP])
def test_every_activity_truncates_at_zero(body_config, kind) -> None:
    """恢复类与非恢复类活动同样以 0 截断；刚好不够或远远不够都不报错。"""
    factors = (ActivityFactors(3.5) if kind == ActivityKind.TRAVEL else
               ActivityFactors(1.5) if kind == ActivityKind.ACTION else None)
    cost = minimum_energy_cost(ActivityKind.WAIT, 60, body_config)
    for energy in (math.nextafter(cost - 1e-9, math.inf), cost - 1e-8, 0.0):
        body = BodyState(energy_kcal=max(energy, 0.0), sleep_pressure=0.0, sleep_deficit_hours=0,
                         entertainment_need=0)
        settled = settle_body(body, kind, 3600, body_config, factors)
        assert settled.after.energy_kcal == 0


@pytest.mark.parametrize("duration", [901, 902, 1801])
def test_exact_total_energy_survives_feedback_partition_rounding(body_config, duration: int) -> None:
    """按九百秒反馈分段时，精确总成本不会因累计舍入变成真实不足。"""
    energy = minimum_energy_cost(ActivityKind.WAIT, duration, body_config)
    body = BodyState(energy_kcal=energy, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    remaining = duration
    while remaining:
        seconds = min(900, remaining)
        body = settle_body(body, ActivityKind.WAIT, seconds, body_config).after
        remaining -= seconds

    assert 0 <= body.energy_kcal <= 1e-9


def test_energy_duration_uses_same_cost_at_sixty_second_boundary(body_config) -> None:
    """最大整数时长与领域成本函数在六十和六十一秒的边界完全一致。"""
    energy = minimum_energy_cost(ActivityKind.ACTION, 60, body_config, 1.5)

    assert maximum_energy_duration_seconds(ActivityKind.ACTION, energy, body_config, 1.5) == 60
    assert minimum_energy_cost(ActivityKind.ACTION, 61, body_config, 1.5) > energy


@pytest.mark.parametrize("kind", [ActivityKind.WAIT, ActivityKind.SLEEP])
def test_recovery_activity_duration_is_not_masked_by_zero_energy(body_config, kind) -> None:
    """等待和睡眠在零能量时仍保留既有恢复例外。"""
    assert maximum_energy_duration_seconds(kind, 0, body_config) == 86_400
