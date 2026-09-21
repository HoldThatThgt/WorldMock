"""@brief 按实际活动区间结算能量、两过程睡眠、睡眠不足账与娱乐需要。

身体语义按 `docs/dev/SPEC-life-runtime.md`「身体语义：两过程睡眠、睡眠不足账、
体感分级」重写：sleep_pressure 与 sleep_deficit_hours 的演化委托 life.sleep 的两过程
纯函数（18.2h/4.2h 时间常数、清醒累积 need/(24−need)、睡眠恢复 1h）；效率
曲线输入改为 sleep_deficit_hours（sleep.work_efficiency / food_utilization）。
能量与娱乐需要沿用 ResourceLedger 分录通道（docs/design/05-recording-engine.md「事件正文与资源证据」）；社交需要账本（felt.apply_
social_need_ledger）与情绪脉冲/衰减（felt.apply_mood_pulse / decay_mood_
valence）由引擎在结算边界调用本模块的包装函数驱动。

body_observation 返回分级与数值两组：数值只进事件供评估与导出，分级才进
提示（「身体语义：两过程睡眠、睡眠不足账、体感分级」）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from worldmock.catalog.affordances import default_affordances
from worldmock.life import felt, sleep
from worldmock.life.models import ActivityKind, BodyState, CurvePoint
from worldmock.life.scenario import BodyConfig
from worldmock.resources import FlowEntry, ResourceLedger, ResourceMeta
from worldmock.resources.models import (
    DRAIN_DIRECTION,
    LEVEL_KIND,
    OPEN_CONSERVATION,
    SOURCE_DIRECTION,
)


#: 能量投影可穿透下界的活动闭集（sleep/wait/consume）——自可供性注册表
#: kinds.*.energy_floor_exempt 派生（M1：字面常量已删，注册表即事实）。
ENERGY_RECOVERY_ACTIVITY_KINDS = frozenset(
    ActivityKind(name) for name in default_affordances().energy_recovery_kinds)

#: 身体域 level 资源的分录标识（与 BodyState 字段及 tests/resources 夹具同名）。
#: 修订合同删除 sleep_debt_hours 资源：睡眠侧分录改为 sleep_deficit_hours。
ENERGY_RESOURCE = "energy_kcal"
SLEEP_DEFICIT_RESOURCE = "sleep_deficit_hours"
ENTERTAINMENT_RESOURCE = "entertainment_need"
#: 目录侧（catalog/objects.jsonl，冻结数据不可改）效率因子仍以资源标识
#: "sleep_debt_hours" 声明睡眠轴输入；这是目录数据身份而非状态字段别名——
#: 求值侧把该目录标识映射到后继状态字段 sleep_deficit_hours（节点值一致）。
CATALOG_SLEEP_RESOURCE = "sleep_debt_hours"
#: 结算函数不携带人物身份与墙钟：分录以固定 owner 与占位时间戳落地，
#: 引擎接线时以真实 agent 与 occurred_at 重盖（resource_flows 字典不含时间戳）。
BODY_FLOW_OWNER = "body"
BODY_FLOW_OCCURRED_AT = 0

#: 自上次 last_flows() 调用以来结算产生的分录证据流（docs/design/05-recording-engine.md「事件正文与资源证据」）。
_flow_buffer: list[FlowEntry] = []


def last_flows() -> tuple[FlowEntry, ...]:
    """@brief 取回并清空自上次调用以来结算产生的分录序列。"""
    pending = tuple(_flow_buffer)
    _flow_buffer.clear()
    return pending


def body_resource_metas(config: BodyConfig) -> tuple[ResourceMeta, ...]:
    """@brief 从身体参数组装三个 level 资源元信息（bounds 与现行 clamp 语义一致）。"""
    return (
        ResourceMeta(ENERGY_RESOURCE, LEVEL_KIND, OPEN_CONSERVATION, 0,
                     config.maximum_energy_kcal),
        ResourceMeta(SLEEP_DEFICIT_RESOURCE, LEVEL_KIND, OPEN_CONSERVATION, 0,
                     sleep.SLEEP_DEFICIT_MAX_H),
        ResourceMeta(ENTERTAINMENT_RESOURCE, LEVEL_KIND, OPEN_CONSERVATION, 0, 1),
    )


def _settle_via_ledger(config: BodyConfig, before: BodyState,
                       entries: list[FlowEntry]) -> tuple[float, float, float]:
    """@brief 经 ResourceLedger 纯函数结算分录，返回能量、不足账、娱乐期末余额。"""
    ledger = ResourceLedger(body_resource_metas(config))
    balances = {
        (ENERGY_RESOURCE, BODY_FLOW_OWNER): before.energy_kcal,
        (SLEEP_DEFICIT_RESOURCE, BODY_FLOW_OWNER): before.sleep_deficit_hours,
        (ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER): before.entertainment_need,
    }
    final = ledger.apply(entries, balances)
    return (final[(ENERGY_RESOURCE, BODY_FLOW_OWNER)],
            final[(SLEEP_DEFICIT_RESOURCE, BODY_FLOW_OWNER)],
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
    """@brief 结算连续活动：能量分录 + 两过程睡眠演化 + 不足账 + 娱乐分录。

    sleep_pressure 与 sleep_deficit_hours 按 life.sleep 的纯函数直接演化
    （不进 ResourceLedger——两者不是 open level 资源，钳制语义由纯函数承载）；
    能量与娱乐需要维持分录证据流。社交需要与情绪由引擎侧 apply_social_ledger
    与 apply_mood_* 在同一结算边界另行推进。
    """
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
        *entertainment_entries,
    ) if entry is not None]
    energy, deficit, entertainment = _settle_via_ledger(config, body, entries)
    if entertainment_capped:
        entertainment = 1.0
    if kind == ActivityKind.SLEEP:
        pressure = sleep.pressure_after_sleep(body.sleep_pressure, hours)
        deficit = sleep.deficit_after_sleep(deficit, hours)
    else:
        pressure = sleep.pressure_after_awake(body.sleep_pressure, hours)
        deficit = sleep.deficit_after_awake(deficit, hours, body.sleep_need_hours)
    after = BodyState(energy_kcal=energy, sleep_pressure=pressure,
                      sleep_deficit_hours=deficit, sleep_need_hours=body.sleep_need_hours,
                      chronotype_midsleep=body.chronotype_midsleep,
                      social_need=body.social_need, mood_valence=body.mood_valence,
                      neuroticism_norm=body.neuroticism_norm,
                      entertainment_need=entertainment)
    _flow_buffer.extend(entries)
    return BodySettlement(body.model_copy(deep=True), after, seconds, basal, extra, 0, None,
                          _work_efficiency(body, after, kind, config, factors))


def settle_shared_body(body: BodyState, activities: tuple[tuple[ActivityKind, ActivityFactors], ...],
                       seconds: int, config: BodyConfig) -> BodySettlement:
    """@brief 一个人物真实区间只结算一次基础消耗，运动负荷取最大值。"""
    if seconds < 0:
        raise ValueError("settlement duration cannot be negative")
    hours = seconds / 3600
    sleeping = any(kind == ActivityKind.SLEEP for kind, _ in activities)
    met = max((factors.met for _, factors in activities), default=1.0)
    basal = config.basal_kcal_per_hour * hours
    extra = max(met - 1, 0) * config.mass_kg * hours
    cost = basal + extra
    exempt = not activities or all(kind in ENERGY_RECOVERY_ACTIVITY_KINDS for kind, _ in activities)
    if cost > body.energy_kcal + 1e-9 and not exempt:
        raise ValueError("activity would exhaust available energy")
    entries = _shared_entries(body, activities, hours, cost, config)
    energy, _deficit, entertainment = _settle_via_ledger(config, body, entries)
    if sleeping:
        pressure = sleep.pressure_after_sleep(body.sleep_pressure, hours)
        deficit = sleep.deficit_after_sleep(body.sleep_deficit_hours, hours)
    else:
        pressure = sleep.pressure_after_awake(body.sleep_pressure, hours)
        deficit = sleep.deficit_after_awake(body.sleep_deficit_hours, hours, body.sleep_need_hours)
    after = body.model_copy(update={"energy_kcal": energy, "sleep_pressure": pressure,
                                    "sleep_deficit_hours": deficit,
                                    "entertainment_need": entertainment}, deep=True)
    _flow_buffer.extend(entries)
    work = next((factors for kind, factors in activities if kind == ActivityKind.WORK), None)
    efficiency = _work_efficiency(body, after, ActivityKind.WORK, config, work) if work else 1.0
    return BodySettlement(body.model_copy(deep=True), after, seconds, basal, extra, 0, None, efficiency)


def _shared_entries(body: BodyState, activities: tuple[tuple[ActivityKind, ActivityFactors], ...],
                    hours: float, cost: float, config: BodyConfig) -> list[FlowEntry]:
    """@brief 同一身体通道先汇总效果再限幅，生成一份资源分录。"""
    entries = []
    if cost > 0 and body.energy_kcal > 0:
        entries.append(FlowEntry(ENERGY_RESOURCE, BODY_FLOW_OWNER, DRAIN_DIRECTION,
                                 min(cost, body.energy_kcal), "settle:shared:energy_cost",
                                 BODY_FLOW_OCCURRED_AT))
    if any(kind == ActivityKind.SLEEP for kind, _ in activities):
        return entries
    relief = sum(factors.entertainment_relief_per_hour for _, factors in activities)
    change = (config.entertainment_need_per_awake_hour - relief) * hours
    actual = min(1.0, max(0.0, body.entertainment_need + change)) - body.entertainment_need
    if actual:
        direction = SOURCE_DIRECTION if actual > 0 else DRAIN_DIRECTION
        entries.append(FlowEntry(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER, direction,
                                 abs(actual), "settle:shared:entertainment_need",
                                 BODY_FLOW_OCCURRED_AT))
    return entries


def shared_energy_cost(activities: tuple[tuple[int, float], ...], config: BodyConfig) -> float:
    """@brief 按每个剩余结束点切分，用共同最大 MET 预测能量预算。"""
    endpoints = sorted({seconds for seconds, _ in activities if seconds > 0})
    previous = 0
    total = 0.0
    for endpoint in endpoints:
        met = max((value for seconds, value in activities if seconds > previous), default=1.0)
        hours = (endpoint - previous) / 3600
        total += (config.basal_kcal_per_hour + max(met - 1, 0) * config.mass_kg) * hours
        previous = endpoint
    return total


def maximum_shared_energy_duration(entries: tuple[tuple[int, float], ...], candidate_met: float,
                                   energy: float, config: BodyConfig, limit_seconds: int) -> int:
    """@brief 使用共同分段成本函数求候选最长可行整秒时长。"""
    if energy < 0 or limit_seconds < 0:
        raise ValueError("shared energy duration inputs are invalid")
    low, high = 0, limit_seconds
    while low < high:
        middle = (low + high + 1) // 2
        if shared_energy_cost(entries + ((middle, candidate_met),), config) <= energy:
            low = middle
        else:
            high = middle - 1
    return low


def apply_food(body: BodyState, calories: float, config: BodyConfig) -> BodySettlement:
    """@brief 以进食开始时睡眠不足账计算食品利用效率（sleep.food_utilization）。"""
    if calories < 0:
        raise ValueError("food calories cannot be negative")
    efficiency = sleep.food_utilization_efficiency(body.sleep_deficit_hours)
    gained = calories * efficiency
    projected = body.energy_kcal + gained
    capped = projected > config.maximum_energy_kcal
    amount = min(gained, config.maximum_energy_kcal - body.energy_kcal) if capped else gained
    entries = []
    if amount > 0:
        entries.append(FlowEntry(ENERGY_RESOURCE, BODY_FLOW_OWNER, SOURCE_DIRECTION, amount,
                                 "food:intake", BODY_FLOW_OCCURRED_AT))
    energy, _deficit, _entertainment = _settle_via_ledger(config, body, entries)
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


def felt_values(body: BodyState, hour_of_day: float,
                meal_windows: felt.MealWindows | None = None) -> dict[str, float]:
    """@brief 计算五个体感信号数值（appetite 含昼夜与餐时项，「身体语义：两过程睡眠、睡眠不足账、体感分级」）。"""
    appetite = felt.compute_appetite(body.energy_kcal, hour_of_day, meal_windows)
    sleepiness = sleep.sleepiness(body.sleep_pressure, hour_of_day,
                                   body.chronotype_midsleep)
    return {"appetite": appetite, "sleepiness": sleepiness,
            "entertainment_need": body.entertainment_need, "social_need": body.social_need,
            "mood_valence": body.mood_valence}


def felt_levels(body: BodyState, hour_of_day: float,
                meal_windows: felt.MealWindows | None = None) -> dict[str, str]:
    """@brief 计算五个体感分级文本（滞回判定与提示渲染的输入，「身体语义：两过程睡眠、睡眠不足账、体感分级」）。"""
    values = felt_values(body, hour_of_day, meal_windows)
    return felt.summarize_felt_levels(**values)


def body_observation(body: BodyState, config: BodyConfig, hour_of_day: float = 12.0,
                     meal_windows: felt.MealWindows | None = None) -> dict:
    """@brief 生成不替人物做决定的身体投影：分级与数值两组（「身体语义：两过程睡眠、睡眠不足账、体感分级」）。

    数值（values）只进观察事件供评估与导出；分级（levels）才进提示。
    hour_of_day 缺省正午仅供结构稳定调用方（引擎始终传场景本地时刻）。
    """
    if body.energy_kcal < config.emergency_threshold_kcal:
        alert = "emergency_energy"
    elif body.energy_kcal < config.hunger_threshold_kcal:
        alert = "hungry"
    else:
        alert = None
    return {"levels": felt_levels(body, hour_of_day, meal_windows),
            "values": {"energy_kcal": body.energy_kcal,
                       "sleep_pressure": body.sleep_pressure,
                       "sleepiness": sleep.sleepiness(body.sleep_pressure, hour_of_day,
                                                      body.chronotype_midsleep),
                       "sleep_deficit_hours": body.sleep_deficit_hours,
                       "entertainment_need": body.entertainment_need,
                       "social_need": body.social_need,
                       "mood_valence": body.mood_valence},
            "alert": alert}


def apply_social_ledger(body: BodyState, exposure: felt.SocialExposure) -> BodyState:
    """@brief 按 felt 账本速率推进社交需要（「身体语义：两过程睡眠、睡眠不足账、体感分级」），返回新身体状态。"""
    need = felt.apply_social_need_ledger(body.social_need, exposure)
    return body.model_copy(update={"social_need": need}, deep=True)


def apply_mood_pulse(body: BodyState, pulse_key: str) -> BodyState:
    """@brief 按事件脉冲表叠加一次情绪脉冲（「身体语义：两过程睡眠、睡眠不足账、体感分级」）。"""
    amplitude = felt.MOOD_PULSES[pulse_key]
    valence = felt.apply_mood_pulse(body.mood_valence, amplitude, body.neuroticism_norm)
    return body.model_copy(update={"mood_valence": valence}, deep=True)


def apply_discomfort_pulse(body: BodyState, continuous_hours: float) -> BodyState:
    """@brief 叠加连续饿/困的不适脉冲（不足 2 小时时 felt 返回 0，「身体语义：两过程睡眠、睡眠不足账、体感分级」）。"""
    amplitude = felt.discomfort_pulse(continuous_hours)
    if amplitude == 0:
        return body
    valence = felt.apply_mood_pulse(body.mood_valence, amplitude, body.neuroticism_norm)
    return body.model_copy(update={"mood_valence": valence}, deep=True)


def decay_mood(body: BodyState, elapsed_minutes: float) -> BodyState:
    """@brief 按半衰期衰减情绪（「身体语义：两过程睡眠、睡眠不足账、体感分级」），返回新身体状态。"""
    valence = felt.decay_mood_valence(body.mood_valence, elapsed_minutes,
                                      body.neuroticism_norm)
    return body.model_copy(update={"mood_valence": valence}, deep=True)


def initial_body(config: BodyConfig, hour_of_day: float,
                 sleep_need_hours: float = sleep.DEFAULT_SLEEP_NEED_H,
                 chronotype_midsleep: float = sleep.DEFAULT_CHRONOTYPE_MIDSLEEP_H,
                 neuroticism_norm: float = 0.0) -> BodyState:
    """@brief 构造场景起点身体：初始睡眠不足账与反推的初始 sleep_pressure。

    初始 sleep_pressure 由场景起点时刻按已睡时长反推（「身体语义：两过程睡眠、睡眠不足账、体感分级」）：experiment_
    assumption——缺省假设人物起点前刚睡满一个睡眠需要量（已睡 = sleep_need_hours，
    即正常睡醒），起点落在夜间时该假设同样确定性适用。
    """
    pressure = sleep.initial_sleep_pressure(hour_of_day, sleep_need_hours,
                                            chronotype_midsleep)
    return BodyState(energy_kcal=config.initial_energy_kcal, sleep_pressure=pressure,
                     sleep_deficit_hours=config.initial_sleep_debt_hours,
                     sleep_need_hours=sleep_need_hours,
                     chronotype_midsleep=chronotype_midsleep,
                     social_need=0.0, mood_valence=0.0,
                     neuroticism_norm=neuroticism_norm, entertainment_need=0.0)


def next_body_boundary_seconds(body: BodyState, kind: ActivityKind, config: BodyConfig,
                               factors: ActivityFactors | None = None,
                               hour_of_day: float | None = None) -> int | None:
    """@brief 预测能量阈值、体感分级或效率曲线的首个未来整秒边界。

    体感分级跨越候选覆盖困意（以当前时刻的昼夜阈值近似解 S(t') 跨越下一档，
    experiment_assumption 的确定性近似）与娱乐/社交分级边界；效率曲线沿用
    sleep_deficit_hours 轴上的节点。hour_of_day 为 None 时跳过昼夜相关候选。
    """
    actual = factors or ActivityFactors(config.activity_met[kind.value])
    burn = config.basal_kcal_per_hour + max(actual.met - 1, 0) * config.mass_kg
    net_burn = burn - actual.food_energy_kcal_per_hour
    candidates = _energy_boundaries(body.energy_kcal, net_burn, config)
    deficit_rate = (-1.0 if kind == ActivityKind.SLEEP
                    else body.sleep_need_hours / (24.0 - body.sleep_need_hours))
    candidates.extend(_curve_boundaries(body.sleep_deficit_hours, deficit_rate,
                                         _deficit_curve_points(kind)))
    candidates.extend(_felt_band_boundaries(body, kind, hour_of_day, config))
    return min(candidates) if candidates else None


def next_shared_body_boundary_seconds(body: BodyState,
                                      activities: tuple[tuple[ActivityKind, ActivityFactors], ...],
                                      config: BodyConfig, hour_of_day: float) -> int | None:
    """@brief 以实际共同结算的最大 MET 和合并效果预测下一个阈值。"""
    if any(kind in {ActivityKind.SLEEP, ActivityKind.PLAY_GAME,
                    ActivityKind.CHAT, ActivityKind.CALL} for kind, _ in activities):
        return None
    factors = ActivityFactors(max((item.met for _, item in activities), default=1.0),
                              entertainment_relief_per_hour=sum(
                                  item.entertainment_relief_per_hour for _, item in activities),
                              food_energy_kcal_per_hour=sum(
                                  item.food_energy_kcal_per_hour for _, item in activities))
    kinds = {kind for kind, _ in activities}
    kind = (ActivityKind.WORK if ActivityKind.WORK in kinds
            else ActivityKind.CONSUME if ActivityKind.CONSUME in kinds else ActivityKind.WAIT)
    return next_body_boundary_seconds(body, kind, config, factors, hour_of_day)


def _deficit_curve_points(kind: ActivityKind) -> tuple[CurvePoint, ...]:
    """@brief 返回当前活动用于边界预测的效率曲线节点（输入 sleep_deficit_hours）。"""
    if kind == ActivityKind.WORK:
        return tuple(CurvePoint(input_value=p.input_hours, output_value=p.output_factor)
                     for p in sleep.WORK_EFFICIENCY_CURVE)
    if kind == ActivityKind.CONSUME:
        return tuple(CurvePoint(input_value=p.input_hours, output_value=p.output_factor)
                     for p in sleep.FOOD_UTILIZATION_CURVE)
    return ()


def _felt_band_boundaries(body: BodyState, kind: ActivityKind,
                          hour_of_day: float | None, config: BodyConfig) -> list[int]:
    """@brief 体感分级预计跨越时刻：困意/娱乐/社交（awake 活动才有意义）。"""
    if kind == ActivityKind.SLEEP or hour_of_day is None:
        return []
    candidates: list[int] = []
    low = sleep.wake_threshold(hour_of_day, body.chronotype_midsleep)
    span = sleep.sleep_onset_threshold(hour_of_day, body.chronotype_midsleep) - low
    for edge in (sleep.GRADE_TIRED_MIN, sleep.GRADE_SLEEPY_MIN,
                 sleep.GRADE_VERY_SLEEPY_MIN, sleep.GRADE_CANNOT_STAY_AWAKE_MIN):
        pressure = min(max(edge, 0.0), sleep.SLEEPINESS_CLIP_MAX) * span + low
        hours = -math.log(1.0 - pressure) * sleep.AWAKE_TIME_CONSTANT_H \
            if 0.0 <= pressure < 1.0 else None
        if hours is not None and hours > 0:
            candidates.append(max(1, math.ceil(hours * 3600)))
    rates = ((body.entertainment_need, felt.ENTERTAINMENT_BANDS,
              config.entertainment_need_per_awake_hour),
             (body.social_need, felt.SOCIAL_BANDS, felt.SOCIAL_NEED_AWAKE_RATE))
    for value, bands, rate in rates:
        if rate <= 0:
            continue
        for bound, _level in bands:
            if bound > value:
                candidates.append(max(1, math.ceil((bound - value) / rate * 3600)))
    return candidates


def _energy_boundaries(energy: float, burn_per_hour: float, config: BodyConfig) -> list[int]:
    """@brief 返回首次严格低于尚未越过能量阈值的整秒数。"""
    if burn_per_hour <= 0:
        return []
    thresholds = (config.hunger_threshold_kcal, config.emergency_threshold_kcal)
    return [math.floor((energy - threshold) * 3600 / burn_per_hour) + 1
            for threshold in thresholds if energy >= threshold]


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

    sleep_deficit_hours 轴上的因子由 sleep.work_efficiency 曲线求值（修订合同
    「睡眠不足账与效率曲线」：效率曲线输入改为不足账，节点值不变）；其余输入资源沿用目录声明。
    区间取值按梯形平均（方向无关）；非 work 观察值取区间起点插值快照。最后乘入
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


def _resource_value(body: BodyState, resource: str) -> float:
    """@brief 读取身体资源余额；因子引用未知资源名属目录错误，fail-closed。"""
    if resource in (SLEEP_DEFICIT_RESOURCE, CATALOG_SLEEP_RESOURCE):
        return body.sleep_deficit_hours
    if resource == ENERGY_RESOURCE:
        return body.energy_kcal
    if resource == ENTERTAINMENT_RESOURCE:
        return body.entertainment_need
    raise ValueError(f"work efficiency factor references unknown resource {resource!r}")


def _curve_average(points: tuple[CurvePoint, ...], start: float, end: float) -> float:
    """@brief 对区间起止输入值在分段线性曲线上做梯形面积平均（方向无关）。"""
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
