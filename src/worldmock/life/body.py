"""@brief 按实际活动区间结算能量、睡眠不足和娱乐需要。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from worldmock.life.models import ActivityKind, BodyState, CurvePoint
from worldmock.life.scenario import BodyConfig


@dataclass(frozen=True, slots=True)
class BodySettlement:
    """@brief 一段活动的身体变化证据。"""

    before: BodyState
    after: BodyState
    duration_seconds: int
    basal_energy_kcal: float
    extra_energy_kcal: float
    food_energy_kcal: float
    food_efficiency: float | None
    work_efficiency: float


@dataclass(frozen=True, slots=True)
class ActivityFactors:
    """@brief 当前活动方式和人物偏好形成的结算系数。"""

    met: float
    entertainment_relief_per_hour: float = 0
    work_skill_factor: float = 1
    work_tool_factor: float = 1
    food_energy_kcal_per_hour: float = 0


def interpolate(points: tuple[CurvePoint, ...], value: float) -> float:
    """@brief 在线性节点间插值，并在两端钳制。"""
    if value <= points[0].input_value:
        return points[0].output_value
    if value >= points[-1].input_value:
        return points[-1].output_value
    for left, right in zip(points, points[1:]):
        if left.input_value <= value <= right.input_value:
            span = right.input_value - left.input_value
            ratio = (value - left.input_value) / span
            return left.output_value + ratio * (right.output_value - left.output_value)
    raise ValueError("curve interpolation interval is missing")


def settle_body(body: BodyState, kind: ActivityKind, seconds: int, config: BodyConfig,
                factors: ActivityFactors | None = None) -> BodySettlement:
    """@brief 结算连续活动，不允许活动把能量静默降到零以下。"""
    if seconds < 0:
        raise ValueError("settlement duration cannot be negative")
    hours = seconds / 3600
    basal = config.basal_kcal_per_hour * hours
    met = factors.met if factors else config.activity_met[kind.value]
    extra = max(met - 1, 0) * config.mass_kg * hours
    energy = body.energy_kcal - basal - extra
    recovery = {ActivityKind.SLEEP, ActivityKind.WAIT, ActivityKind.CONSUME}
    if energy < 0 and kind not in recovery:
        raise ValueError("activity would exhaust available energy")
    energy = max(0, energy)
    debt = _settle_sleep_debt(body.sleep_debt_hours, kind, hours, config)
    relief = factors.entertainment_relief_per_hour if factors else 0
    entertainment = _settle_entertainment(body.entertainment_need, kind, hours, config, relief)
    after = BodyState(energy_kcal=energy, sleep_debt_hours=debt, entertainment_need=entertainment)
    return BodySettlement(body.model_copy(deep=True), after, seconds, basal, extra, 0, None,
                          _work_efficiency(body.sleep_debt_hours, debt, kind, config, factors))


def apply_food(body: BodyState, calories: float, config: BodyConfig) -> BodySettlement:
    """@brief 使用进食开始时睡眠不足计算食品利用效率。"""
    if calories < 0:
        raise ValueError("food calories cannot be negative")
    efficiency = interpolate(config.food_efficiency_curve, body.sleep_debt_hours)
    gained = calories * efficiency
    energy = min(config.maximum_energy_kcal, body.energy_kcal + gained)
    after = body.model_copy(update={"energy_kcal": energy}, deep=True)
    return BodySettlement(body.model_copy(deep=True), after, 0, 0, 0, gained, efficiency,
                          interpolate(config.work_efficiency_curve, body.sleep_debt_hours))


def minimum_energy_cost(kind: ActivityKind, seconds: int, config: BodyConfig,
                        met: float | None = None) -> float:
    """@brief 计算受理活动前能够确定的完整能量成本。"""
    hours = seconds / 3600
    activity_met = config.activity_met[kind.value] if met is None else met
    return (config.basal_kcal_per_hour + max(activity_met - 1, 0) * config.mass_kg) * hours


def body_observation(body: BodyState, config: BodyConfig) -> dict:
    """@brief 生成不替人物做决定的身体提示。"""
    if body.energy_kcal < config.emergency_threshold_kcal:
        alert = "emergency_energy"
    elif body.energy_kcal < config.hunger_threshold_kcal:
        alert = "hungry"
    else:
        alert = None
    return {"energy_kcal": body.energy_kcal, "sleep_debt_hours": body.sleep_debt_hours,
            "entertainment_need": body.entertainment_need, "alert": alert}


def next_body_boundary_seconds(body: BodyState, kind: ActivityKind, config: BodyConfig,
                               factors: ActivityFactors | None = None) -> int | None:
    """@brief 预测能量阈值或当前活动效率曲线的首个未来整秒边界。"""
    actual = factors or ActivityFactors(config.activity_met[kind.value])
    burn = config.basal_kcal_per_hour + max(actual.met - 1, 0) * config.mass_kg
    net_burn = burn - actual.food_energy_kcal_per_hour
    candidates = _energy_boundaries(body.energy_kcal, net_burn, config)
    curve = _active_efficiency_curve(kind, config)
    debt_rate = (-config.sleep_recovery_per_hour if kind == ActivityKind.SLEEP
                 else config.awake_sleep_debt_per_hour)
    candidates.extend(_curve_boundaries(body.sleep_debt_hours, debt_rate, curve))
    return min(candidates) if candidates else None


def _energy_boundaries(energy: float, burn_per_hour: float, config: BodyConfig) -> list[int]:
    """@brief 返回首次严格低于尚未越过能量阈值的整秒数。"""
    if burn_per_hour <= 0:
        return []
    thresholds = (config.hunger_threshold_kcal, config.emergency_threshold_kcal)
    return [math.floor((energy - threshold) * 3600 / burn_per_hour) + 1
            for threshold in thresholds if energy >= threshold]


def _active_efficiency_curve(kind: ActivityKind, config: BodyConfig) -> tuple[CurvePoint, ...]:
    """@brief 返回当前活动实际使用的身体效率曲线。"""
    if kind == ActivityKind.WORK:
        return config.work_efficiency_curve
    if kind == ActivityKind.CONSUME:
        return config.food_efficiency_curve
    return ()


def _curve_boundaries(value: float, rate_per_hour: float,
                      points: tuple[CurvePoint, ...]) -> list[int]:
    """@brief 返回沿当前变化方向首次到达各曲线节点的整秒数。"""
    if rate_per_hour == 0:
        return []
    targets = [point.input_value for point in points
               if (rate_per_hour > 0 and point.input_value > value)
               or (rate_per_hour < 0 and point.input_value < value)]
    return [max(1, math.ceil(abs(target - value) * 3600 / abs(rate_per_hour)))
            for target in targets]


def _settle_sleep_debt(debt: float, kind: ActivityKind, hours: float, config: BodyConfig) -> float:
    """@brief 睡眠恢复与清醒积累互不混用。"""
    if kind == ActivityKind.SLEEP:
        return max(0, debt - config.sleep_recovery_per_hour * hours)
    return debt + config.awake_sleep_debt_per_hour * hours


def _settle_entertainment(value: float, kind: ActivityKind, hours: float, config: BodyConfig,
                          relief_per_hour: float) -> float:
    """@brief 娱乐按实际接触减少需要，其余清醒活动增加需要。"""
    if kind == ActivityKind.SLEEP:
        return value
    if relief_per_hour:
        relief = relief_per_hour * hours
        return max(0, value - relief)
    return min(1, value + config.entertainment_need_per_awake_hour * hours)


def _work_efficiency(start: float, end: float, kind: ActivityKind, config: BodyConfig,
                     factors: ActivityFactors | None) -> float:
    """@brief 对工作区间中的睡眠不足曲线做分段线性平均。"""
    if kind != ActivityKind.WORK or end <= start:
        base = interpolate(config.work_efficiency_curve, start)
        return _apply_work_factors(base, kind, factors)
    cuts = [start] + [point.input_value for point in config.work_efficiency_curve if start < point.input_value < end]
    cuts.append(end)
    area = 0.0
    for left, right in zip(cuts, cuts[1:]):
        a = interpolate(config.work_efficiency_curve, left)
        b = interpolate(config.work_efficiency_curve, right)
        area += (a + b) * (right - left) / 2
    return _apply_work_factors(area / (end - start), kind, factors)


def _apply_work_factors(value: float, kind: ActivityKind,
                        factors: ActivityFactors | None) -> float:
    """@brief 将冻结人物技能与实际工具效果乘入工作效率。"""
    if kind != ActivityKind.WORK or factors is None:
        return value
    if factors.work_skill_factor < 0 or factors.work_tool_factor < 0:
        raise ValueError("work efficiency factors cannot be negative")
    return value * factors.work_skill_factor * factors.work_tool_factor
