"""@brief 实际活动区间的能量、双过程睡眠与两项需要共同结算。"""
from __future__ import annotations
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from worldmock.exchange.body import RESOURCE_METAS
from worldmock.input_models import PersonaConfig
from worldmock.life import felt, sleep
from worldmock.life.body_config import BodyConfig
from worldmock.life.models import ActivityKind, BodyState
from worldmock.resources import FlowEntry, ResourceLedger, ResourceMeta
from worldmock.resources.models import DRAIN_DIRECTION, SOURCE_DIRECTION

ENERGY_RESOURCE = "energy_kcal"
SLEEP_DEFICIT_RESOURCE = "sleep_deficit_hours"
RELAXATION_RESOURCE = "relaxation_need"
SOCIAL_RESOURCE = "social_need"
BODY_FLOW_OWNER = "body"
BODY_FLOW_OCCURRED_AT = 0
FELT_SCAN_STEP_SECONDS = 900
FELT_SCAN_HORIZON_SECONDS = 86400
_flow_buffer: list[FlowEntry] = []

@dataclass(frozen=True, slots=True)
class BodySettlement:
    """@brief 一段真实区间的完整身体变化证据。"""
    #: 区间开始的身体状态。
    before: BodyState
    #: 区间结束的身体状态。
    after: BodyState
    #: 实际经过的秒数。
    duration_seconds: int
    #: 一次基础代谢的千卡消耗。
    basal_energy_kcal: float
    #: 最大活动强度产生的额外千卡消耗。
    extra_energy_kcal: float
    #: 食品经实际效率利用的千卡。
    food_energy_kcal: float
    #: 食品利用效率，无食品时为空。
    food_efficiency: float | None

@dataclass(frozen=True, slots=True)
class ActivityFactors:
    """@brief 内容声明的强度与两项独立的实际缓解速率。"""
    #: 固定代谢当量。
    met: float
    #: 每实际小时减少的放松需要。
    relaxation_relief_per_hour: float = 0.0
    #: 每实际小时减少的社交需要。
    social_relief_per_hour: float = 0.0

@dataclass(frozen=True, slots=True)
class BoundaryClock:
    """@brief 身体边界预测的本地时刻与餐时窗口。"""
    #: 自本地午夜起的小时数。
    hour_of_day: float
    #: 人物实际声明的午餐与晚餐窗口。
    meal_windows: felt.MealWindows | None = None

def last_flows() -> tuple[FlowEntry, ...]:
    """@brief 取回并清空实际发生的资源分录。"""
    pending = tuple(_flow_buffer)
    _flow_buffer.clear()
    return pending

def body_resource_metas(config: BodyConfig) -> tuple[ResourceMeta, ...]:
    """@brief 系统固定的身体资源定义不接受目录覆盖。"""
    return tuple(RESOURCE_METAS[name] for name in
                 (ENERGY_RESOURCE, SLEEP_DEFICIT_RESOURCE, RELAXATION_RESOURCE, SOCIAL_RESOURCE))

def _settle_ledger(body: BodyState, changes: dict[str, float], config: BodyConfig) -> BodyState:
    """@brief 每项资源只记录真实限幅差额。"""
    entries = [FlowEntry(name, BODY_FLOW_OWNER, SOURCE_DIRECTION if delta > 0 else DRAIN_DIRECTION,
                         abs(delta), f"settle:shared:{name}", BODY_FLOW_OCCURRED_AT)
               for name, delta in changes.items() if delta]
    balances = {(name, BODY_FLOW_OWNER): getattr(body, name) for name in changes}
    final = ResourceLedger(body_resource_metas(config)).apply(entries, balances)
    _flow_buffer.extend(entries)
    return body.model_copy(update={name: final[(name, BODY_FLOW_OWNER)] for name in changes}, deep=True)

def hourly_burn(config: BodyConfig, met: float) -> tuple[float, float]:
    """@brief 每小时基础代谢等于体重千克数，额外消耗只算代谢当量超出静坐的部分。

    @param config 含人物体重的身体结算视图
    @param met 共同区间内进行中活动的最大代谢当量，没有活动时为 1.0
    @return (每小时基础代谢千卡, 每小时额外消耗千卡)
    """
    basal_rate = config.mass_kg
    extra_rate = max(met - 1, 0) * config.mass_kg
    return basal_rate, extra_rate

def settle_body(body: BodyState, kind: ActivityKind, seconds: int, config: BodyConfig,
                factors: ActivityFactors | None = None) -> BodySettlement:
    """@brief 单活动复用共同结算路径，不重复自然增长。"""
    key = "idle" if kind == ActivityKind.INTERACT else kind.value
    if kind == ActivityKind.TRAVEL and factors is None:
        logging.error("Travel settlement requires its actual mode")
        raise ValueError("travel settlement requires its actual mode")
    actual = factors or ActivityFactors(config.activity_met[key])
    return settle_shared_body(body, ((kind, actual),), seconds, config)

def settle_shared_body(body: BodyState, activities: tuple[tuple[ActivityKind, ActivityFactors], ...],
                       seconds: int, config: BodyConfig) -> BodySettlement:
    """@brief 只结算一次自然增长和基础代谢，最大强度及缓解总和共同生效。"""
    if seconds < 0:
        logging.error("Body settlement duration cannot be negative")
        raise ValueError("body settlement duration cannot be negative")
    hours = seconds / 3600
    sleeping = any(kind == ActivityKind.SLEEP for kind, _ in activities)
    met = max((factors.met for _, factors in activities), default=1.0)
    basal_rate, extra_rate = hourly_burn(config, met)
    basal = basal_rate * hours
    extra = extra_rate * hours
    deficit = (sleep.deficit_after_sleep(body.sleep_deficit_hours, hours) if sleeping else
               sleep.deficit_after_awake(body.sleep_deficit_hours, hours, body.sleep_need_hours))
    changes = {ENERGY_RESOURCE: -min(basal + extra, body.energy_kcal),
               SLEEP_DEFICIT_RESOURCE: deficit - body.sleep_deficit_hours}
    if not sleeping:
        changes.update(_need_changes(body, activities, hours, config))
    after = _settle_ledger(body, changes, config)
    pressure = (sleep.pressure_after_sleep(body.sleep_pressure, hours) if sleeping else
                sleep.pressure_after_awake(body.sleep_pressure, hours))
    after = after.model_copy(update={"sleep_pressure": pressure}, deep=True)
    return BodySettlement(body.model_copy(deep=True), after, seconds, basal, extra, 0, None)

def _need_changes(body: BodyState, activities: tuple[tuple[ActivityKind, ActivityFactors], ...],
                  hours: float, config: BodyConfig) -> dict[str, float]:
    """@brief 自然增长与内容效果同时相加，暂停活动不进入区间集合。"""
    relaxation = sum(factor.relaxation_relief_per_hour for _, factor in activities)
    social = sum(factor.social_relief_per_hour for _, factor in activities)
    rates = {RELAXATION_RESOURCE: config.relaxation_need_per_awake_hour - relaxation,
             SOCIAL_RESOURCE: body.social_need_growth_per_hour - social}
    return {name: min(1.0, max(0.0, getattr(body, name) + rate * hours)) - getattr(body, name)
            for name, rate in rates.items()}

def apply_food(body: BodyState, calories: float, config: BodyConfig) -> BodySettlement:
    """@brief 收货当秒按实际睡眠不足账计算食品利用效率。"""
    if not math.isfinite(calories) or calories < 0:
        logging.error("Food calories must be finite and nonnegative")
        raise ValueError("food calories must be finite and nonnegative")
    efficiency = sleep.food_utilization_efficiency(body.sleep_deficit_hours)
    gained = calories * efficiency
    actual = min(gained, config.maximum_energy_kcal - body.energy_kcal)
    after = _settle_ledger(body, {ENERGY_RESOURCE: actual}, config)
    return BodySettlement(body.model_copy(deep=True), after, 0, 0, 0, gained, efficiency)

def apply_relaxation_change(body: BodyState, amount: float) -> BodyState:
    """@brief 真实拒绝和收货改变统一放松需要，不产生情绪量。"""
    if not math.isfinite(amount):
        logging.error("Relaxation change must be finite")
        raise ValueError("relaxation change must be finite")
    actual = min(1.0, max(0.0, body.relaxation_need + amount)) - body.relaxation_need
    if actual:
        _flow_buffer.append(FlowEntry(RELAXATION_RESOURCE, BODY_FLOW_OWNER,
            SOURCE_DIRECTION if actual > 0 else DRAIN_DIRECTION, abs(actual),
            "relaxation:event", BODY_FLOW_OCCURRED_AT))
    return body.model_copy(update={"relaxation_need": body.relaxation_need + actual}, deep=True)

def apply_discomfort_pulse(body: BodyState, billed_hours: float) -> BodyState:
    """@brief 连续不适超出两小时的新增部分每小时增加零点一放松需要。"""
    if not math.isfinite(billed_hours) or billed_hours < 0:
        logging.error("Discomfort hours must be finite and nonnegative")
        raise ValueError("discomfort hours must be finite and nonnegative")
    return apply_relaxation_change(body, 0.10 * billed_hours)

def initial_body(config: BodyConfig, hour_of_day: float, persona: PersonaConfig,
                 social_need_growth_per_hour: float) -> BodyState:
    """@brief 从真实人设初值与惯常起床时刻构造身体，不预置活动。"""
    wake_hour = (persona.chronotype_midsleep + persona.sleep_need_hours / 2) % 24
    awake_hours = (hour_of_day - wake_hour) % 24
    at_wake = sleep.initial_sleep_pressure(wake_hour, persona.sleep_need_hours, persona.chronotype_midsleep)
    return BodyState(energy_kcal=persona.body.initial_energy_kcal,
                     sleep_pressure=sleep.pressure_after_awake(at_wake, awake_hours),
                     sleep_deficit_hours=min(24.0, persona.body.initial_sleep_debt_hours),
                     sleep_need_hours=persona.sleep_need_hours, chronotype_midsleep=persona.chronotype_midsleep,
                     social_need=persona.initial_social_need, relaxation_need=persona.initial_relaxation_need,
                     social_need_growth_per_hour=social_need_growth_per_hour)

def next_shared_body_boundary_seconds(body: BodyState,
                                      activities: tuple[tuple[ActivityKind, ActivityFactors], ...],
                                      config: BodyConfig, clock: BoundaryClock) -> int | None:
    """@brief 用实际共同强度和缓解预测身体边界，睡眠仅接受自身终点。"""
    if any(kind == ActivityKind.SLEEP for kind, _ in activities):
        return None
    factors = ActivityFactors(max((item.met for _, item in activities), default=1.0),
                              sum(item.relaxation_relief_per_hour for _, item in activities),
                              sum(item.social_relief_per_hour for _, item in activities))
    projection = _FeltProjection(body, config, clock, factors)
    candidates = _energy_boundaries(body.energy_kcal, projection.burn_per_hour, config)
    meal_edges = _meal_window_boundaries(clock)
    candidates.extend(meal_edges)
    candidates.extend(value for value in (_first_band_change(projection.sleepiness_level),
                      _first_band_change(projection.appetite_level, meal_edges)) if value is not None)
    candidates.extend(_need_band_boundaries(projection))
    return min(candidates) if candidates else None

@dataclass(frozen=True, slots=True)
class _FeltProjection:
    """@brief 用共同区间真实系数预测未来体感。"""
    #: 当前身体。
    body: BodyState
    #: 固定身体参数。
    config: BodyConfig
    #: 本地时刻与餐时窗口。
    clock: BoundaryClock
    #: 真实活动的合并系数。
    factors: ActivityFactors

    @property
    def burn_per_hour(self) -> float:
        """@brief 最大强度只叠加一次基础代谢。"""
        basal_rate, extra_rate = hourly_burn(self.config, self.factors.met)
        return basal_rate + extra_rate

    def hour_after(self, seconds: int) -> float:
        """@brief 距当前秒数后的本地小时。"""
        return (self.clock.hour_of_day + seconds / 3600) % 24

    def sleepiness_level(self, seconds: int) -> str:
        """@brief 双过程睡眠压力与未来昼夜相位决定困意。"""
        pressure = sleep.pressure_after_awake(self.body.sleep_pressure, seconds / 3600)
        return felt.sleepiness_level(sleep.sleepiness(pressure, self.hour_after(seconds),
                                                    self.body.chronotype_midsleep))

    def appetite_level(self, seconds: int) -> str:
        """@brief 未来能量与实际餐时窗口决定饥饿分级。"""
        energy = max(self.body.energy_kcal - self.burn_per_hour * seconds / 3600, 0.0)
        appetite = felt.compute_appetite(energy, self.hour_after(seconds), self.clock.meal_windows)
        return felt.appetite_level(appetite, energy, self.config.emergency_threshold_kcal)

def _first_band_change(level_at: Callable[[int], str], breakpoints: Sequence[int] = ()) -> int | None:
    """@brief 以不超过 900 秒的步长正向扫描至多 24 小时，取首个分级变化的步点后在该步内二分到整秒。

    breakpoints 是分级可能跳变的时刻（距现在的整秒）：跳变前一秒与跳变那一秒也作步点，二分区间不跨跳变，
    跳变之前刚发生、又被跳变抵消的跨档也能扫到。升档、降档都算变化；扫描范围内分级不变时没有候选
    （docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）。
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
    """@brief 自然增长和缓解净速率同时预测升档与降档。"""
    body, factors = projection.body, projection.factors
    rates = ((body.relaxation_need, felt.RELAXATION_BANDS,
              projection.config.relaxation_need_per_awake_hour - factors.relaxation_relief_per_hour),
             (body.social_need, felt.SOCIAL_BANDS, body.social_need_growth_per_hour - factors.social_relief_per_hour))
    candidates = []
    for value, bands, rate in rates:
        for bound, _ in bands:
            if rate > 0 and value < bound:
                candidates.append(max(1, math.ceil((bound - value) / rate * 3600)))
            elif rate < 0 and value >= bound:
                candidates.append(max(1, math.floor((bound - value) / rate * 3600) + 1))
    return candidates

def _energy_boundaries(energy: float, burn_per_hour: float, config: BodyConfig) -> list[int]:
    """@brief 预测首次严格低于能量阈值的整秒。"""
    thresholds = (config.hunger_threshold_kcal, config.emergency_threshold_kcal, felt.APPETITE_STARVING_KCAL)
    return [math.floor((energy - threshold) * 3600 / burn_per_hour) + 1
            for threshold in thresholds if burn_per_hour > 0 and energy >= threshold]
