"""工作效率因子列表的声明式合成测试：迁移等价、阈值数值、纯配置扩展性。

因果耦合以目录数据表达、引擎只做通用求值（单元 E 铁律）。本文件验收：
满能量时与迁移前单曲线引擎逐位一致；能量因子在 hungry/emergency 阈值处
的输出乘数；跨阈值区间的梯形积分；新增因果边 = 纯配置（不改任何 src）。
"""

from __future__ import annotations

import pytest

from worldmock.life.body import ActivityFactors, apply_food, settle_body
from worldmock.life.models import ActivityKind, BodyState, CurvePoint, EfficiencyFactor
from worldmock.life.scenario import BodyConfig, load_scenario


@pytest.fixture(scope="module")
def body_config() -> BodyConfig:
    """读取正式冻结身体参数（经目录装配，含睡眠债+能量两个因子）。"""
    return load_scenario("scenarios/white-collar-12-weeks.json")[0].body


@pytest.fixture(scope="module")
def sleep_only_config(body_config: BodyConfig) -> BodyConfig:
    """迁移前形态：因子列表只含睡眠债因子（曲线与旧单曲线一致）。"""
    sleep_factor = next(factor for factor in body_config.work_efficiency_factors
                        if factor.input_resource == "sleep_debt_hours")
    return body_config.model_copy(update={"work_efficiency_factors": (sleep_factor,)})


def _sleep_factor(config: BodyConfig) -> EfficiencyFactor:
    return next(factor for factor in config.work_efficiency_factors
                if factor.input_resource == "sleep_debt_hours")


def _legacy_sleep_efficiency(config: BodyConfig, start: float, end: float) -> float:
    """迁移前单曲线算法的独立重算（不调用生产实现，作逐位对照基准）。"""
    points = [(p.input_value, p.output_value) for p in _sleep_factor(config).curve]

    def value_at(x: float) -> float:
        if x <= points[0][0]:
            return points[0][1]
        if x >= points[-1][0]:
            return points[-1][1]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= x <= x1:
                return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
        raise AssertionError("legacy curve interval is missing")

    if end <= start:
        return value_at(start)
    cuts = [start] + [x for x, _ in points if start < x < end]
    cuts.append(end)
    area = 0.0
    for left, right in zip(cuts, cuts[1:]):
        area += (value_at(left) + value_at(right)) * (right - left) / 2
    return area / (end - start)


FULL_ENERGY = 2400  # 最大能量：能量因子全程钳制 1.0，即"无能量因子影响段"


def _hungry_factor(energy: float) -> float:
    """hungry 线性段（400-800 kcal）的规格公式：0.8 起步线性回到 1.0。"""
    return 0.8 + (energy - 400) / 400 * 0.2


def test_factors_assembled_from_catalog(body_config) -> None:
    """目录因子列表经 semantics/BodyConfig 装配通道原样流入引擎。"""
    factors = {factor.input_resource: [(p.input_value, p.output_value) for p in factor.curve]
               for factor in body_config.work_efficiency_factors}
    assert factors["sleep_debt_hours"] == [(0.0, 1.0), (8.0, 1.0), (16.0, 0.85), (24.0, 0.65)]
    assert factors["energy_kcal"] == [(0.0, 0.5), (399.9, 0.5), (400.0, 0.8), (800.0, 1.0)]
    assert body_config.source_kind == "catalog:2.0"


def test_hungry_band_interval_average_with_skill(body_config) -> None:
    """hungry 段无节点区间按端点均值梯形平均，技能因子照常相乘。"""
    body = BodyState(energy_kcal=610, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    result = settle_body(body, ActivityKind.WORK, 600, body_config,
                         ActivityFactors(1.5, work_skill_factor=0.8))
    end = result.after.energy_kcal
    assert 400 < end < 610 < 800  # 区间落在 hungry 线性段内、无曲线节点
    assert result.work_efficiency == pytest.approx(
        (_hungry_factor(610) + _hungry_factor(end)) / 2 * 0.8)


@pytest.mark.parametrize(("debt", "hours", "skill", "tool"), [
    (0, 4, 1, 1),
    (0, 12, 1, 1),
    (6, 16, 1, 1),          # 睡眠债区间 6→14，跨节点 8
    (0, 12, 0.8, 1.25),     # 技能/工具因子沿用相乘
    (15, 8, 1, 1),          # 区间跨节点 16 且下探钳制段
])
def test_full_energy_migration_is_bit_identical(
    body_config, debt: float, hours: int, skill: float, tool: float,
) -> None:
    """满能量工作区间的效率与迁移前单曲线引擎逐位一致（==，非 approx）。"""
    body = BodyState(energy_kcal=FULL_ENERGY, sleep_pressure=0.0, sleep_deficit_hours=debt, entertainment_need=0)
    result = settle_body(body, ActivityKind.WORK, hours * 3600, body_config,
                         ActivityFactors(1.5, work_skill_factor=skill, work_tool_factor=tool))
    assert result.after.energy_kcal >= 800  # 前提：区间不进入能量因子影响段
    legacy = _legacy_sleep_efficiency(
        body_config, body.sleep_deficit_hours, result.after.sleep_deficit_hours)
    assert result.work_efficiency == legacy * skill * tool


def test_full_energy_equals_sleep_only_engine_bit_identically(
    body_config, sleep_only_config,
) -> None:
    """同一满能量区间，含能量因子的新引擎 == 只含睡眠因子的引擎。"""
    body = BodyState(energy_kcal=FULL_ENERGY, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    full = settle_body(body, ActivityKind.WORK, 12 * 3600, body_config, ActivityFactors(1.5))
    single = settle_body(body, ActivityKind.WORK, 12 * 3600, sleep_only_config,
                         ActivityFactors(1.5))
    assert full.work_efficiency == single.work_efficiency


def test_migration_snapshot_paths_bit_identical(body_config) -> None:
    """零时长区间与非 work 观察值在满能量下同样与迁移前逐位一致。"""
    zero = settle_body(BodyState(energy_kcal=FULL_ENERGY, sleep_pressure=0.0, sleep_deficit_hours=9,
                                 entertainment_need=0),
                       ActivityKind.WORK, 0, body_config)
    assert zero.work_efficiency == _legacy_sleep_efficiency(body_config, 9, 9)
    watch = settle_body(BodyState(energy_kcal=FULL_ENERGY, sleep_pressure=0.0, sleep_deficit_hours=10,
                                  entertainment_need=0),
                        ActivityKind.WATCH, 3600, body_config, ActivityFactors(1))
    assert watch.work_efficiency == _legacy_sleep_efficiency(body_config, 10, 10)
    food = apply_food(BodyState(energy_kcal=FULL_ENERGY, sleep_pressure=0.0, sleep_deficit_hours=12,
                                entertainment_need=0), 0, body_config)
    assert food.work_efficiency == _legacy_sleep_efficiency(body_config, 12, 12)


@pytest.mark.parametrize(("energy", "expected"), [
    (2400, 1.0),   # 满能量：阈值以上钳制满效
    (800, 1.0),    # hungry 阈值节点
    (600, 0.9),    # hungry 段中点：400→800 线性 0.8→1.0
    (500, 0.85),
    (400, 0.8),    # emergency 阈值节点
    (399.9, 0.5),  # 阶跃过渡带下沿
    (300, 0.5),    # emergency 段钳制
    (0, 0.5),
])
def test_energy_factor_threshold_outputs(body_config, energy: float, expected: float) -> None:
    """能量因子按 alert_rules 同源阈值输出乘数（睡眠债为零、快照取值）。"""
    body = BodyState(energy_kcal=energy, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    assert apply_food(body, 0, body_config).work_efficiency == pytest.approx(expected)


def test_work_interval_integrates_across_energy_thresholds(body_config) -> None:
    """跨阈值工作区间按梯形积分平均，而非端点快照或取小近似。"""
    energy_factor = next(factor for factor in body_config.work_efficiency_factors
                         if factor.input_resource == "energy_kcal")
    config = body_config.model_copy(update={"work_efficiency_factors": (energy_factor,)})
    seconds = 22200  # met=1 时每小时净耗 basal 65 kcal：600 → 198.75
    start, end = 600.0, 600.0 - 65 * seconds / 3600
    body = BodyState(energy_kcal=start, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    result = settle_body(body, ActivityKind.WORK, seconds, config, ActivityFactors(1))
    expected = (0.5 * (399.9 - end)                 # emergency 段平顶
                + (0.5 + 0.8) * (400 - 399.9) / 2   # 阶跃过渡带
                + (0.8 + 0.9) * (start - 400) / 2   # hungry 段线性
                ) / (start - end)
    assert result.work_efficiency == pytest.approx(expected)
    assert result.work_efficiency != pytest.approx(0.7)  # 端点快照平均是 0.7


def test_food_efficiency_and_non_work_physics_stay_independent(body_config) -> None:
    """食品利用效率不吃能量因子；非 work 结算物理量与能量水平无关。"""
    for energy in (2400, 600, 300):
        body = BodyState(energy_kcal=energy, sleep_pressure=0.0, sleep_deficit_hours=12, entertainment_need=0)
        assert apply_food(body, 200, body_config).food_efficiency == pytest.approx(0.875)
    low = settle_body(BodyState(energy_kcal=600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0),
                      ActivityKind.WATCH, 3600, body_config, ActivityFactors(1))
    high = settle_body(BodyState(energy_kcal=2400, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0),
                       ActivityKind.WATCH, 3600, body_config, ActivityFactors(1))
    assert low.after.sleep_deficit_hours == high.after.sleep_deficit_hours
    assert low.after.entertainment_need == high.after.entertainment_need
    assert low.after.energy_kcal - 600 == pytest.approx(high.after.energy_kcal - 2400)
    # 非 work 结算的 work_efficiency 是观察快照，统一含全部因子（与进食快照一致）；
    # 它不参与任何非 work 结算物理量或工作产出累计（仅 work 活动累计产出）。
    waited = settle_body(BodyState(energy_kcal=300, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0),
                         ActivityKind.WAIT, 600, body_config, ActivityFactors(1.3))
    assert waited.work_efficiency == pytest.approx(0.5)


def test_new_causal_edge_is_pure_catalog_data(body_config) -> None:
    """架构性质：不改 src，仅在因子列表追加数据行即新增因果边。"""
    body = BodyState(energy_kcal=FULL_ENERGY, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0.5)
    base = apply_food(body, 0, body_config).work_efficiency
    fake_entertainment = EfficiencyFactor(
        input_resource="entertainment_need",
        curve=(CurvePoint(input_value=0, output_value=1.0),
               CurvePoint(input_value=1, output_value=0.5)))
    with_entertainment = body_config.model_copy(update={"work_efficiency_factors": (
        *body_config.work_efficiency_factors, fake_entertainment)})
    assert apply_food(body, 0, with_entertainment).work_efficiency == pytest.approx(base * 0.75)

    debt_body = BodyState(energy_kcal=FULL_ENERGY, sleep_pressure=0.0, sleep_deficit_hours=12, entertainment_need=0)
    base_debt = apply_food(debt_body, 0, body_config).work_efficiency
    second_sleep = EfficiencyFactor(
        input_resource="sleep_debt_hours",
        curve=(CurvePoint(input_value=0, output_value=1.0),
               CurvePoint(input_value=16, output_value=0.5)))
    with_second = body_config.model_copy(update={"work_efficiency_factors": (
        *body_config.work_efficiency_factors, second_sleep)})
    assert apply_food(debt_body, 0, with_second).work_efficiency == pytest.approx(
        base_debt * 0.625)  # 第二条睡眠债曲线在 12h 处输出 0.625


def test_unknown_factor_resource_fails_closed(body_config) -> None:
    """因子引用未知资源名属目录错误：求值 fail-closed 拒绝运行。"""
    bogus = EfficiencyFactor(input_resource="mood_points",
                             curve=(CurvePoint(input_value=0, output_value=1.0),))
    config = body_config.model_copy(update={"work_efficiency_factors": (bogus,)})
    with pytest.raises(ValueError, match="unknown resource"):
        apply_food(BodyState(energy_kcal=FULL_ENERGY, sleep_pressure=0.0, sleep_deficit_hours=0,
                             entertainment_need=0), 0, config)
