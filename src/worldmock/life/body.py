"""@brief 按实际活动区间结算能量、两过程睡眠、睡眠不足账与娱乐需要。

身体语义按 `docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则`「身体语义：两过程睡眠、睡眠不足账、
体感分级」重写：sleep_pressure 与 sleep_deficit_hours 的演化委托 life.sleep 的两过程
纯函数（18.2h/4.2h 时间常数、清醒累积 need/(24−need)、睡眠恢复 1h）；效率
曲线输入改为 sleep_deficit_hours（sleep.work_efficiency / food_utilization）。
娱乐需要在有缓解的活动进行时只按缓解下降、不再累积，共同区间与单活动两条路径以及边界预测同一口径
（entertainment_need_rate，docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ）。
能量与娱乐需要沿用 ResourceLedger 分录通道（docs/design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎「事件正文与资源证据」）；
社交需要账本（felt.apply_
social_need_ledger）与情绪脉冲/衰减（felt.apply_mood_pulse / decay_mood_
valence）由引擎在结算边界调用本模块的包装函数驱动。

能量不阻止任何活动：所有活动集合的结算都以 0 截断、不报错；低能量的代价只是饥饿上升与
工作变慢（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「能量不拦人」）。边界预测对困意与饥饿做同一种
跨档扫描，并把餐时窗口起止列为候选（同规格第八章「节律」）。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from worldmock.catalog.affordances import engine_affordances
from worldmock.life import felt, sleep
from worldmock.base_models import CurvePoint
from worldmock.life.models import (ActivityKind, BodyState)
from worldmock.life.scenario import BodyConfig
from worldmock.resources import FlowEntry, ResourceLedger, ResourceMeta
from worldmock.resources.models import (
    DRAIN_DIRECTION,
    LEVEL_KIND,
    OPEN_CONSERVATION,
    SOURCE_DIRECTION,
)


#: 恢复类活动闭集（sleep/wait/consume）——自可供性注册表 kinds.*.energy_floor_exempt 派生。
#: 只供内部动机评分按能量取舍（maximum_energy_duration_seconds），不代表世界规则：结算对所有活动
#: 集合都以 0 截断（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「删除的机制」）。
ENERGY_RECOVERY_ACTIVITY_KINDS = frozenset(
    ActivityKind(name) for name in engine_affordances().energy_recovery_kinds)

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

#: 自上次 last_flows()
#: 调用以来结算产生的分录证据流（docs/design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎「事件正文与资源证据」）。
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


#: 困意与饥饿跨档扫描的最大步长与扫描上限（秒）；步点变档后在该步内二分到整秒
#: （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第八章「节律」）。
FELT_SCAN_STEP_SECONDS = 900
FELT_SCAN_HORIZON_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class BoundaryClock:
    """@brief 边界预测的时刻输入：场景本地小时与人物的午餐、晚餐窗口。"""

    hour_of_day: float
    meal_windows: felt.MealWindows | None = None


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
    能量与娱乐需要维持分录证据流。能量消耗超过余额时以 0 截断，任何活动都不因
    透支报错。社交需要与情绪由引擎侧 apply_social_ledger 与 apply_mood_* 在同一
    结算边界另行推进。
    """
    if seconds < 0:
        raise ValueError("settlement duration cannot be negative")
    hours = seconds / 3600
    basal = config.basal_kcal_per_hour * hours
    met = factors.met if factors else config.activity_met[kind.value]
    extra = max(met - 1, 0) * config.mass_kg * hours
    cost = minimum_energy_cost(kind, seconds, config, met)
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
    """@brief 一个人物真实区间只结算一次基础消耗，运动负荷取最大值。

    消耗超过余额时能量以 0 截断：任何活动集合都照常结算，不因透支报错
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「删除的机制」）。
    """
    if seconds < 0:
        raise ValueError("settlement duration cannot be negative")
    hours = seconds / 3600
    sleeping = any(kind == ActivityKind.SLEEP for kind, _ in activities)
    met = max((factors.met for _, factors in activities), default=1.0)
    basal = config.basal_kcal_per_hour * hours
    extra = max(met - 1, 0) * config.mass_kg * hours
    cost = basal + extra
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


def entertainment_need_rate(relief_per_hour: float, config: BodyConfig) -> float:
    """@brief 清醒区间娱乐需要的每小时净变化，两条结算路径与动机评分共用同一口径。

    有缓解的活动进行时只按缓解下降、不再累积清醒增长；没有缓解时按清醒速率累积
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 「累积口径统一为有缓解的活动进行时不再累积娱乐需要」）。
    睡眠区间不走本函数：娱乐需要在睡眠中不变。
    """
    if relief_per_hour > 0:
        return -relief_per_hour
    return config.entertainment_need_per_awake_hour


def _shared_entries(body: BodyState, activities: tuple[tuple[ActivityKind, ActivityFactors], ...],
                    hours: float, cost: float, config: BodyConfig) -> list[FlowEntry]:
    """@brief 同一身体通道先汇总效果再限幅，生成一份资源分录；娱乐缓解先求和，有缓解时不再累积。"""
    entries = []
    if cost > 0 and body.energy_kcal > 0:
        entries.append(FlowEntry(ENERGY_RESOURCE, BODY_FLOW_OWNER, DRAIN_DIRECTION,
                                 min(cost, body.energy_kcal), "settle:shared:energy_cost",
                                 BODY_FLOW_OCCURRED_AT))
    if any(kind == ActivityKind.SLEEP for kind, _ in activities):
        return entries
    relief = sum(factors.entertainment_relief_per_hour for _, factors in activities)
    change = entertainment_need_rate(relief, config) * hours
    actual = min(1.0, max(0.0, body.entertainment_need + change)) - body.entertainment_need
    if actual:
        direction = SOURCE_DIRECTION if actual > 0 else DRAIN_DIRECTION
        entries.append(FlowEntry(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER, direction,
                                 abs(actual), "settle:shared:entertainment_need",
                                 BODY_FLOW_OCCURRED_AT))
    return entries


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
    """@brief 计算一段活动的完整能量成本（结算与内部动机评分共用；受理不查能量）。"""
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
    """@brief 以同一完整成本函数求当前能量能够覆盖的最大整数墙钟秒数。

    只供内部动机评分（motivation_internal）按能量取舍；恢复类活动不受能量限制。
    受理、结算与恢复都不调用本函数（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「删除的机制」）。
    """
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


def apply_social_ledger(body: BodyState, exposure: felt.SocialExposure) -> BodyState:
    """@brief 按 felt 账本速率推进社交需要（「身体语义：两过程睡眠、睡眠不足账、体感分级」），返回新身体状态。"""
    need = felt.apply_social_need_ledger(body.social_need, exposure)
    return body.model_copy(update={"social_need": need}, deep=True)


def apply_mood_pulse(body: BodyState, pulse_key: str) -> BodyState:
    """@brief 按事件脉冲表叠加一次情绪脉冲（「身体语义：两过程睡眠、睡眠不足账、体感分级」）。"""
    amplitude = felt.MOOD_PULSES[pulse_key]
    valence = felt.apply_mood_pulse(body.mood_valence, amplitude, body.neuroticism_norm)
    return body.model_copy(update={"mood_valence": valence}, deep=True)


def apply_discomfort_pulse(body: BodyState, billed_hours: float) -> BodyState:
    """@brief 叠加连续饿/困的不适脉冲：−0.1/小时 × 本次计费小时，两小时门槛由 rhythm 判定一次，计费为 0 时不变
    （「身体语义：两过程睡眠、睡眠不足账、体感分级」）。"""
    amplitude = felt.discomfort_pulse(billed_hours)
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
    """@brief 构造场景起点身体：初始睡眠不足账与按作息反推的初始 sleep_pressure。

    experiment_assumption（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「作息初值」）：人物自起点之前最近一次惯常
    起床时刻 t_wake = (chronotype_midsleep + sleep_need_hours / 2) mod 24 起一直清醒。起床时刻的压力取
    睡满一个睡眠需要量后的衰减值（sleep.initial_sleep_pressure），此后按清醒过程推进到起点；不读
    bedtime_window，不预置任何活动。起点落在惯常睡眠时段内或接近它时，人物以困倦状态开始。
    """
    wake_hour = (chronotype_midsleep + sleep_need_hours / 2) % 24
    awake_hours = (hour_of_day - wake_hour) % 24
    at_wake = sleep.initial_sleep_pressure(wake_hour, sleep_need_hours, chronotype_midsleep)
    pressure = sleep.pressure_after_awake(at_wake, awake_hours)
    return BodyState(energy_kcal=config.initial_energy_kcal, sleep_pressure=pressure,
                     sleep_deficit_hours=config.initial_sleep_debt_hours,
                     sleep_need_hours=sleep_need_hours,
                     chronotype_midsleep=chronotype_midsleep,
                     social_need=0.0, mood_valence=0.0,
                     neuroticism_norm=neuroticism_norm, entertainment_need=0.0)


def next_body_boundary_seconds(body: BodyState, kind: ActivityKind, config: BodyConfig,
                               factors: ActivityFactors | None = None,
                               clock: BoundaryClock | None = None) -> int | None:
    """@brief 预测能量阈值、体感分级、餐时窗口或效率曲线的首个未来整秒边界。

    能量阈值为饥饿、紧急阈值与 felt.APPETITE_STARVING_KCAL（800、400、100 kcal）；困意与饥饿从当前状态
    做跨档扫描（升档、降档都给候选），午餐、晚餐窗口的起止是候选，娱乐按与结算同一口径的净速率、社交按清醒增长速率
    求下一档（有缓解的活动进行时娱乐需要不累积，不给升档候选，docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ）；
    效率曲线沿用 sleep_deficit_hours 轴上的节点（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第三章「让人物趁早吃」、
    第八章「节律」）。clock 为 None 或睡眠时跳过依赖时刻的体感候选（娱乐需要在睡眠中不增长）。
    """
    actual = factors or ActivityFactors(config.activity_met[kind.value])
    net_burn = _net_burn_per_hour(actual, config)
    candidates = _energy_boundaries(body.energy_kcal, net_burn, config)
    deficit_rate = (-1.0 if kind == ActivityKind.SLEEP
                    else body.sleep_need_hours / (24.0 - body.sleep_need_hours))
    candidates.extend(_curve_boundaries(body.sleep_deficit_hours, deficit_rate,
                                         _deficit_curve_points(kind)))
    if clock is not None and kind != ActivityKind.SLEEP:
        candidates.extend(_felt_band_boundaries(_FeltProjection(
            body, net_burn, clock, config, actual.entertainment_relief_per_hour)))
    return min(candidates) if candidates else None


def next_shared_body_boundary_seconds(body: BodyState,
                                      activities: tuple[tuple[ActivityKind, ActivityFactors], ...],
                                      config: BodyConfig, clock: BoundaryClock) -> int | None:
    """@brief 以实际共同结算的最大 MET 和合并效果预测下一个身体边界。

    交谈、通话照常预测（调用方按 CHAT_MET、CALL_MET 给出系数）；游戏按回合、睡眠按睡眠允许原因处理，
    不另做预测（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「让人物趁早吃」）。
    """
    if any(kind in {ActivityKind.SLEEP, ActivityKind.PLAY_GAME} for kind, _ in activities):
        return None
    factors = ActivityFactors(max((item.met for _, item in activities), default=1.0),
                              entertainment_relief_per_hour=sum(
                                  item.entertainment_relief_per_hour for _, item in activities),
                              food_energy_kcal_per_hour=sum(
                                  item.food_energy_kcal_per_hour for _, item in activities))
    kinds = {kind for kind, _ in activities}
    kind = (ActivityKind.WORK if ActivityKind.WORK in kinds
            else ActivityKind.CONSUME if ActivityKind.CONSUME in kinds else ActivityKind.WAIT)
    return next_body_boundary_seconds(body, kind, config, factors, clock)


def _net_burn_per_hour(factors: ActivityFactors, config: BodyConfig) -> float:
    """@brief 共同净消耗（千卡/小时）：基础代谢加 MET 增量，减去进食的每小时净可用热量。"""
    burn = config.basal_kcal_per_hour + max(factors.met - 1, 0) * config.mass_kg
    return burn - factors.food_energy_kcal_per_hour


def _deficit_curve_points(kind: ActivityKind) -> tuple[CurvePoint, ...]:
    """@brief 返回当前活动用于边界预测的效率曲线节点（输入 sleep_deficit_hours）。"""
    if kind == ActivityKind.WORK:
        return tuple(CurvePoint(input_value=p.input_hours, output_value=p.output_factor)
                     for p in sleep.WORK_EFFICIENCY_CURVE)
    if kind == ActivityKind.CONSUME:
        return tuple(CurvePoint(input_value=p.input_hours, output_value=p.output_factor)
                     for p in sleep.FOOD_UTILIZATION_CURVE)
    return ()


@dataclass(frozen=True, slots=True)
class _FeltProjection:
    """@brief 从当前状态外推的体感：能量按当前共同净消耗推进，睡眠压力按清醒过程推进，娱乐需要按
    entertainment_need_rate 与结算同一口径推进（entertainment_relief_per_hour 为本区间活动的合并缓解速率）。"""

    body: BodyState
    net_burn_per_hour: float
    clock: BoundaryClock
    config: BodyConfig
    entertainment_relief_per_hour: float = 0

    def hour_after(self, seconds: int) -> float:
        """@brief 距现在 seconds 秒后的场景本地小时（按 24 小时取模）。"""
        return (self.clock.hour_of_day + seconds / 3600) % 24

    def sleepiness_level(self, seconds: int) -> str:
        """@brief 清醒 seconds 秒后的困意分级：当前压力按清醒过程推进，阈值取那一刻的昼夜相位。"""
        pressure = sleep.pressure_after_awake(self.body.sleep_pressure, seconds / 3600)
        return felt.sleepiness_level(
            sleep.sleepiness(pressure, self.hour_after(seconds), self.body.chronotype_midsleep))

    def appetite_level(self, seconds: int) -> str:
        """@brief seconds 秒后的饥饿分级：含昼夜项、餐时项与 400、100 千卡两个保底档。"""
        energy = min(max(self.body.energy_kcal - self.net_burn_per_hour * seconds / 3600, 0.0),
                     self.config.maximum_energy_kcal)
        appetite = felt.compute_appetite(energy, self.hour_after(seconds), self.clock.meal_windows)
        return felt.appetite_level(appetite, energy, self.config.emergency_threshold_kcal)


def _felt_band_boundaries(projection: _FeltProjection) -> list[int]:
    """@brief 体感分级预计跨越时刻：困意与饥饿的跨档扫描、餐时窗口起止，以及娱乐与社交的下一档。

    饥饿扫描以餐时窗口起止为跳变点：食欲的餐时项只在这些时刻跳变，困意没有跳变点。
    """
    meal_edges = _meal_window_boundaries(projection.clock)
    candidates = [seconds for seconds in (_first_band_change(projection.sleepiness_level),
                                          _first_band_change(projection.appetite_level, meal_edges))
                  if seconds is not None]
    candidates.extend(meal_edges)
    candidates.extend(_need_band_boundaries(projection))
    return candidates


def _first_band_change(level_at: Callable[[int], str], breakpoints: Sequence[int] = ()) -> int | None:
    """@brief 以不超过 900 秒的步长正向扫描至多 24 小时，取首个分级变化的步点后在该步内二分到整秒。

    breakpoints 是分级可能跳变的时刻（距现在的整秒）：跳变前一秒与跳变那一秒也作步点，二分区间不跨跳变，
    跳变之前刚发生、又被跳变抵消的跨档也能扫到。升档、降档都算变化；扫描范围内分级不变时没有候选
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第八章「节律」）。
    """
    current = level_at(0)
    previous = 0
    step_ends = sorted({*range(FELT_SCAN_STEP_SECONDS, FELT_SCAN_HORIZON_SECONDS + 1, FELT_SCAN_STEP_SECONDS),
                        *(point for edge in breakpoints for point in (edge - 1, edge)
                          if 0 < point <= FELT_SCAN_HORIZON_SECONDS)})
    for step_end in step_ends:
        if level_at(step_end) != current:
            low, high = previous, step_end
            while high - low > 1:
                middle = (low + high) // 2
                if level_at(middle) == current:
                    low = middle
                else:
                    high = middle
            return high
        previous = step_end
    return None


def _meal_window_boundaries(clock: BoundaryClock) -> list[int]:
    """@brief 午餐、晚餐窗口起止的下一次到来（严格晚于现在的整秒），食欲的餐时项在这些时刻跳变。"""
    windows = clock.meal_windows
    if windows is None:
        return []
    candidates = []
    for window in (windows.lunch, windows.dinner):
        for edge in window or ():
            seconds = math.ceil(((edge - clock.hour_of_day) % 24) * 3600 - 1e-6)
            candidates.append(seconds if seconds > 0 else FELT_SCAN_HORIZON_SECONDS)
    return candidates


def _need_band_boundaries(projection: _FeltProjection) -> list[int]:
    """@brief 娱乐与社交需要到达更高一档的整秒。

    娱乐取与两条结算路径同一口径的净速率 entertainment_need_rate：有缓解的活动（观看、游戏、带缓解的生活活动）
    进行时只按缓解下降、不再累积，没有升档候选；没有缓解时按清醒速率累积（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ）。
    睡眠中娱乐需要不增长，调用方在睡眠时不做体感预测。社交按清醒增长速率。
    """
    body = projection.body
    candidates: list[int] = []
    rates = ((body.entertainment_need, felt.ENTERTAINMENT_BANDS,
              entertainment_need_rate(projection.entertainment_relief_per_hour, projection.config)),
             (body.social_need, felt.SOCIAL_BANDS, felt.SOCIAL_NEED_AWAKE_RATE))
    for value, bands, rate in rates:
        if rate <= 0:
            continue
        for bound, _level in bands:
            if bound > value:
                candidates.append(max(1, math.ceil((bound - value) / rate * 3600)))
    return candidates


def _energy_boundaries(energy: float, burn_per_hour: float, config: BodyConfig) -> list[int]:
    """@brief 返回首次严格低于尚未越过的能量阈值（饥饿、紧急与「饿得发慌」三档）的整秒数。"""
    if burn_per_hour <= 0:
        return []
    thresholds = (config.hunger_threshold_kcal, config.emergency_threshold_kcal,
                  felt.APPETITE_STARVING_KCAL)
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
    """@brief 娱乐需要分录：实际接触计 drain（有缓解时不再累积），其余清醒活动计 source。

    与共同区间路径同用 entertainment_need_rate 判定口径，同一活动在两条路径上的净效果相同
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ）。

    :returns: (分录列表, 累积是否触及上限 1 需要钳制)。
    """
    if kind == ActivityKind.SLEEP:
        return [], False
    rate = entertainment_need_rate(relief_per_hour, config)
    if rate < 0:
        amount = min(-rate * hours, need)
        if amount > 0:
            return [FlowEntry(ENTERTAINMENT_RESOURCE, BODY_FLOW_OWNER, DRAIN_DIRECTION, amount,
                              f"settle:{kind.value}:entertainment_relief",
                              BODY_FLOW_OCCURRED_AT)], False
        return [], False
    accrual = rate * hours
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
