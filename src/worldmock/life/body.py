"""@brief 按实际活动区间经资源分录结算能量、睡眠不足和娱乐需要。

身体三参数即三个 open level 资源（设计文档 §3.2）：所有数值变动构造
FlowEntry 经 ResourceLedger.apply 纯函数结算（§9 结算通道统一），分录
方向按资源余额语义取 source（流入）/drain（流出）。外部行为与直接赋值
实现逐位等价：非钳制路径的分录金额就是原算式操作数（同一浮点运算），
触界路径按"先算后钳"落地——分录金额先钳到可用余量，期末再钉到现行
clamp 语义的边界值（进食截断到最大能量、睡眠债/娱乐下限 0、娱乐上限 1）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from worldmock.life.models import ActivityKind, BodyState, CurvePoint
from worldmock.life.scenario import BodyConfig
from worldmock.resources import FlowEntry, ResourceLedger, ResourceMeta
from worldmock.resources.models import (
    DRAIN_DIRECTION,
    LEVEL_KIND,
    OPEN_CONSERVATION,
    SOURCE_DIRECTION,
)


ENERGY_RECOVERY_ACTIVITY_KINDS = frozenset({
    ActivityKind.SLEEP,
    ActivityKind.WAIT,
    ActivityKind.CONSUME,
})

#: 身体域三个 level 资源的分录标识（与 BodyState 字段及 tests/resources 夹具同名）。
ENERGY_RESOURCE = "energy_kcal"
SLEEP_DEBT_RESOURCE = "sleep_debt_hours"
ENTERTAINMENT_RESOURCE = "entertainment_need"
#: 结算函数不携带人物身份与墙钟：分录以固定 owner 与占位时间戳落地，
#: wave 2 引擎接线时以真实 agent 与 occurred_at 重盖（resource_flows 字典不含时间戳）。
BODY_FLOW_OWNER = "body"
BODY_FLOW_OCCURRED_AT = 0

#: 自上次 last_flows() 调用以来结算产生的分录证据流（§9）。
_flow_buffer: list[FlowEntry] = []


def last_flows() -> tuple[FlowEntry, ...]:
    """@brief 取回并清空自上次调用以来结算产生的分录序列。

    结算函数把本区间分录追加进证据流；引擎接线（wave 2）在每次结算后
    调用本函数取走增量，写入足迹事件的 resource_flows 字段。
    """
    pending = tuple(_flow_buffer)
    _flow_buffer.clear()
    return pending


def body_resource_metas(config: BodyConfig) -> tuple[ResourceMeta, ...]:
    """@brief 从身体参数组装三个 level 资源元信息（bounds 与现行 clamp 语义一致）。"""
    return (
        ResourceMeta(ENERGY_RESOURCE, LEVEL_KIND, OPEN_CONSERVATION, 0,
                     config.maximum_energy_kcal),
        ResourceMeta(SLEEP_DEBT_RESOURCE, LEVEL_KIND, OPEN_CONSERVATION, 0, None),
        ResourceMeta(ENTERTAINMENT_RESOURCE, LEVEL_KIND, OPEN_CONSERVATION, 0, 1),
    )


def _settle_via_ledger(config: BodyConfig, before: BodyState,
                       entries: list[FlowEntry]) -> tuple[float, float, float]:
    """@brief 经 ResourceLedger 纯函数结算分录，返回能量、睡眠债、娱乐期末余额。"""
    ledger = ResourceLedger(body_resource_metas(config))
    balances = {
        (ENERGY_RESOURCE, BODY_FLOW_OWNER): before.energy_kcal,
        (SLEEP_DEBT_RESOURCE, BODY_FLOW_OWNER): before.sleep_debt_hours,
        (ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER): before.entertainment_need,
    }
    final = ledger.apply(entries, balances)
    return (final[(ENERGY_RESOURCE, BODY_FLOW_OWNER)],
            final[(SLEEP_DEBT_RESOURCE, BODY_FLOW_OWNER)],
            final[(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER)])


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
    cost = minimum_energy_cost(kind, seconds, config, met)
    projected = body.energy_kcal - cost
    close_to_zero = math.isclose(projected, 0, rel_tol=0, abs_tol=1e-9)
    if projected < 0 and not close_to_zero and kind not in ENERGY_RECOVERY_ACTIVITY_KINDS:
        raise ValueError("activity would exhaust available energy")
    relief = factors.entertainment_relief_per_hour if factors else 0
    entertainment_entries, entertainment_capped = _entertainment_entries(
        kind, body.entertainment_need, hours, config, relief)
    entries = [entry for entry in (
        _energy_cost_entry(kind, min(cost, body.energy_kcal)),
        _sleep_debt_entry(kind, body.sleep_debt_hours, hours, config),
        *entertainment_entries,
    ) if entry is not None]
    energy, debt, entertainment = _settle_via_ledger(config, body, entries)
    if entertainment_capped:
        entertainment = 1.0
    after = BodyState(energy_kcal=energy, sleep_debt_hours=debt, entertainment_need=entertainment)
    _flow_buffer.extend(entries)
    return BodySettlement(body.model_copy(deep=True), after, seconds, basal, extra, 0, None,
                          _work_efficiency(body, after, kind, config, factors))


def apply_food(body: BodyState, calories: float, config: BodyConfig) -> BodySettlement:
    """@brief 使用进食开始时睡眠不足计算食品利用效率。"""
    if calories < 0:
        raise ValueError("food calories cannot be negative")
    efficiency = interpolate(config.food_efficiency_curve, body.sleep_debt_hours)
    gained = calories * efficiency
    projected = body.energy_kcal + gained
    capped = projected > config.maximum_energy_kcal
    amount = min(gained, config.maximum_energy_kcal - body.energy_kcal) if capped else gained
    entries = []
    if amount > 0:
        entries.append(FlowEntry(ENERGY_RESOURCE, BODY_FLOW_OWNER, SOURCE_DIRECTION, amount,
                                 "food:intake", BODY_FLOW_OCCURRED_AT))
    energy, _debt, _entertainment = _settle_via_ledger(config, body, entries)
    if capped:
        energy = config.maximum_energy_kcal
    after = body.model_copy(update={"energy_kcal": energy}, deep=True)
    _flow_buffer.extend(entries)
    return BodySettlement(body.model_copy(deep=True), after, 0, 0, 0, gained, efficiency,
                          _factor_snapshot(config, body))


def minimum_energy_cost(kind: ActivityKind, seconds: int, config: BodyConfig,
                        met: float | None = None) -> float:
    """@brief 计算受理活动前能够确定的完整能量成本。"""
    hours = seconds / 3600
    activity_met = config.activity_met[kind.value] if met is None else met
    return (config.basal_kcal_per_hour + max(activity_met - 1, 0) * config.mass_kg) * hours


def maximum_energy_duration_seconds(
    kind: ActivityKind,
    energy_kcal: float,
    config: BodyConfig,
    met: float | None = None,
    limit_seconds: int = 86_400,
) -> int:
    """@brief 以同一完整成本函数求当前能量能够受理的最大整数墙钟秒数。"""
    if energy_kcal < 0:
        raise ValueError("available energy cannot be negative")
    if limit_seconds < 0:
        raise ValueError("energy duration limit cannot be negative")
    if kind in ENERGY_RECOVERY_ACTIVITY_KINDS:
        return limit_seconds
    low, high = 0, limit_seconds
    while low < high:
        middle = (low + high + 1) // 2
        if minimum_energy_cost(kind, middle, config, met) <= energy_kcal:
            low = middle
        else:
            high = middle - 1
    return low


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
    """@brief 返回当前活动用于边界预测的身体效率曲线（work 取睡眠债因子）。

    因子求值本身按区间梯形平均，与边界切分无关；能量因子的 400/800 节点
    与 hungry/emergency 阈值同值，已由能量阈值边界覆盖，此处无需重复预测。
    """
    if kind == ActivityKind.WORK:
        return _factor_curve(config, SLEEP_DEBT_RESOURCE)
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


def _energy_cost_entry(kind: ActivityKind, amount: float) -> FlowEntry | None:
    """@brief 活动能量成本是一笔合并 drain（基础代谢+活动 MET 增量，保持逐位等价）。"""
    if amount > 0:
        return FlowEntry(ENERGY_RESOURCE, BODY_FLOW_OWNER, DRAIN_DIRECTION, amount,
                         f"settle:{kind.value}:energy_cost", BODY_FLOW_OCCURRED_AT)
    return None


def _sleep_debt_entry(kind: ActivityKind, debt: float, hours: float,
                      config: BodyConfig) -> FlowEntry | None:
    """@brief 清醒累积睡眠债计 source，睡眠恢复降低睡眠债计 drain。"""
    if kind == ActivityKind.SLEEP:
        amount = min(config.sleep_recovery_per_hour * hours, debt)
        if amount > 0:
            return FlowEntry(SLEEP_DEBT_RESOURCE, BODY_FLOW_OWNER, DRAIN_DIRECTION, amount,
                             "settle:sleep:recovery", BODY_FLOW_OCCURRED_AT)
    else:
        amount = config.awake_sleep_debt_per_hour * hours
        if amount > 0:
            return FlowEntry(SLEEP_DEBT_RESOURCE, BODY_FLOW_OWNER, SOURCE_DIRECTION, amount,
                             f"settle:{kind.value}:awake_sleep_debt", BODY_FLOW_OCCURRED_AT)
    return None


def _entertainment_entries(kind: ActivityKind, need: float, hours: float, config: BodyConfig,
                           relief_per_hour: float) -> tuple[list[FlowEntry], bool]:
    """@brief 娱乐需要分录：实际接触计 drain，其余清醒活动计 source。

    :returns: (分录列表, 累积是否触及上限 1 需要钳制)。
    """
    if kind == ActivityKind.SLEEP:
        return [], False
    if relief_per_hour:
        amount = min(relief_per_hour * hours, need)
        if amount > 0:
            return [FlowEntry(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER, DRAIN_DIRECTION, amount,
                              f"settle:{kind.value}:entertainment_relief",
                              BODY_FLOW_OCCURRED_AT)], False
        return [], False
    accrual = config.entertainment_need_per_awake_hour * hours
    projected = need + accrual
    if projected <= 1:
        if accrual > 0:
            return [FlowEntry(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER, SOURCE_DIRECTION, accrual,
                              f"settle:{kind.value}:awake_entertainment_need",
                              BODY_FLOW_OCCURRED_AT)], False
        return [], False
    capped = min(accrual, 1 - need)
    if capped > 0:
        return [FlowEntry(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER, SOURCE_DIRECTION, capped,
                          f"settle:{kind.value}:awake_entertainment_need",
                          BODY_FLOW_OCCURRED_AT)], True
    return [], True


def _work_efficiency(before: BodyState, after: BodyState, kind: ActivityKind,
                     config: BodyConfig, factors: ActivityFactors | None) -> float:
    """@brief 按目录因子列表通用合成工作效率：各因子在输入资源区间上取值连乘。

    引擎不感知任何具体因子（因果耦合以目录数据表达）。work 区间对各因子
    的输入资源区间做分段线性梯形平均（与迁移前的睡眠债分段处理同款，
    方向无关）；区间退化或非 work 观察值取区间起点插值快照。最后乘入
    冻结人物技能与实际工具因子（沿用既有行为）。
    """
    if kind != ActivityKind.WORK:
        return _apply_work_factors(_factor_snapshot(config, before), kind, factors)
    product = 1.0
    for factor in config.work_efficiency_factors:
        product *= _curve_average(factor.curve,
                                  _resource_value(before, factor.input_resource),
                                  _resource_value(after, factor.input_resource))
    return _apply_work_factors(product, kind, factors)


def _factor_snapshot(config: BodyConfig, body: BodyState) -> float:
    """@brief 以给定身体状态对各效率因子取起点插值连乘。"""
    product = 1.0
    for factor in config.work_efficiency_factors:
        product *= interpolate(factor.curve, _resource_value(body, factor.input_resource))
    return product


def _factor_curve(config: BodyConfig, resource: str) -> tuple[CurvePoint, ...]:
    """@brief 取声明在指定输入资源上的效率因子曲线（未声明则空曲线）。"""
    for factor in config.work_efficiency_factors:
        if factor.input_resource == resource:
            return factor.curve
    return ()


def _resource_value(body: BodyState, resource: str) -> float:
    """@brief 读取身体资源余额；因子引用未知资源名属目录错误，fail-closed。"""
    if resource == SLEEP_DEBT_RESOURCE:
        return body.sleep_debt_hours
    if resource == ENERGY_RESOURCE:
        return body.energy_kcal
    if resource == ENTERTAINMENT_RESOURCE:
        return body.entertainment_need
    raise ValueError(f"work efficiency factor references unknown resource {resource!r}")


def _curve_average(points: tuple[CurvePoint, ...], start: float, end: float) -> float:
    """@brief 对区间起止输入值在分段线性曲线上做梯形面积平均（方向无关）。

    区间退化时取起点插值；否则对起止值构成的输入带按曲线节点切分，逐段
    梯形积分后按带宽平均——跨节点区间的效率由带内线性插值精确积分。
    """
    low, high = (start, end) if start <= end else (end, start)
    if high <= low:
        return interpolate(points, start)
    cuts = [low] + [point.input_value for point in points if low < point.input_value < high]
    cuts.append(high)
    area = 0.0
    for left, right in zip(cuts, cuts[1:]):
        a = interpolate(points, left)
        b = interpolate(points, right)
        area += (a + b) * (right - left) / 2
    return area / (high - low)


def _apply_work_factors(value: float, kind: ActivityKind,
                        factors: ActivityFactors | None) -> float:
    """@brief 将冻结人物技能与实际工具效果乘入工作效率。"""
    if kind != ActivityKind.WORK or factors is None:
        return value
    if factors.work_skill_factor < 0 or factors.work_tool_factor < 0:
        raise ValueError("work efficiency factors cannot be negative")
    return value * factors.work_skill_factor * factors.work_tool_factor
