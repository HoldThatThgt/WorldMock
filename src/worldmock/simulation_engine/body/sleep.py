"""@brief 两过程睡眠与睡眠不足账的纯函数库（修订合同 2026-09-18）。

sleep_pressure 按 Daan、Beersma 与 Borbély（1984）两过程模型演化：清醒时
以 18.2h 时间常数趋近 1，睡眠时以 4.2h 时间常数衰减；入睡阈值 H 与醒来
阈值 L 以 0.12 振幅绕昼夜相位摆动，困意为 (S−L)/(H−L) 截断到 [0, 1.2]。
sleep_deficit_hours 为独立账本：清醒按 sleep_need_hours/(24−sleep_need_hours)
每小时累积、睡眠每小时恢复 1h，钳制在 [0, 24]，并驱动食品
利用效率曲线。食品利用率只读取 WorldMock 固定身体常数。
"""

from __future__ import annotations

from worldmock.foundation.models import FrozenModel
from worldmock.exchange_engine.definitions.body_constants import BODY_CONSTANTS
import bisect
import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: 清醒过程时间常数（文献常数：两过程模型 Daan/Beersma/Borbély 1984，
#: 标准值取 npj Biological Timing and Sleep 2025 综述）。
AWAKE_TIME_CONSTANT_H = 18.2

#: 睡眠过程时间常数（文献常数：同上，S 过程衰减标准值）。
SLEEP_TIME_CONSTANT_H = 4.2

#: 入睡阈值 H(t) 基线（文献常数：两过程模型标准值）。
SLEEP_ONSET_THRESHOLD_BASE = 0.67

#: 醒来阈值 L(t) 基线（文献常数：两过程模型标准值）。
WAKE_THRESHOLD_BASE = 0.17

#: 两条阈值的昼夜摆动振幅（文献常数：两过程模型标准值）。
THRESHOLD_AMPLITUDE = 0.12

#: 默认自由日睡眠中点（experiment_assumption：MCTQ 口径默认 04:00）。
DEFAULT_CHRONOTYPE_MIDSLEEP_H = 4.0

#: 困意截断上界（experiment_assumption：规格冻结截断窗口 [0, 1.2]）。
SLEEPINESS_CLIP_MAX = 1.2

#: 困意分级边界（experiment_assumption：与 felt.SLEEPINESS_BANDS 同一组档界，由测试锁定一致；KSS 等价刻度的标定见
#: 该表注释与 docs/design/04-simulation-engine.md），区间左闭右开：
#: <0.80 清醒、0.80–0.93 有点乏、0.93–1.07 困、1.07–1.18 很困、>=1.18 撑不住。
GRADE_TIRED_MIN = 0.80
GRADE_SLEEPY_MIN = 0.93
GRADE_VERY_SLEEPY_MIN = 1.07
GRADE_CANNOT_STAY_AWAKE_MIN = 1.18

#: 默认每日睡眠需要量（experiment_assumption：规格默认 7.5h）。
DEFAULT_SLEEP_NEED_H = 7.5

#: 睡眠需要量允许区间（experiment_assumption：人物卡片可声明 7–9h）。
SLEEP_NEED_MIN_H = 7.0
SLEEP_NEED_MAX_H = 9.0

#: 睡眠每小时恢复的不足账小时数（experiment_assumption：规格冻结速率）。
DEFICIT_RECOVERY_PER_SLEEP_H = 1.0

#: 睡眠不足账上下限（experiment_assumption：规格冻结钳制区间 [0, 24]）。
SLEEP_DEFICIT_MAX_H = 24.0


class SleepinessGrade(StrEnum):
    """@brief 困意五档分级（英文代码，语义对应规格中文分级注释）。"""

    AWAKE = "awake"
    SLIGHTLY_TIRED = "slightly_tired"
    SLEEPY = "sleepy"
    VERY_SLEEPY = "very_sleepy"
    CANNOT_STAY_AWAKE = "cannot_stay_awake"


def _circadian_cos(hour: float, midsleep_hour: float) -> float:
    """@brief 昼夜相位余弦 cos(2π(hour−t_min)/24h)，周期 24h 取模保持数值稳定。"""
    return math.cos(2.0 * math.pi * ((hour - midsleep_hour) % 24.0) / 24.0)


def sleep_onset_threshold(hour: float,
                          midsleep_hour: float = DEFAULT_CHRONOTYPE_MIDSLEEP_H) -> float:
    """@brief 入睡阈值 H(t) = 0.67 − 0.12·cos(2π(t−t_min)/24h)。

    hour 为自当日零点起的小时数（允许越界，按 24h 周期解释）；midsleep_hour
    为阈值最低点时刻，取人物 chronotype_midsleep，默认 04:00；返回 [0.55, 0.79]。
    """
    return SLEEP_ONSET_THRESHOLD_BASE - THRESHOLD_AMPLITUDE * _circadian_cos(hour, midsleep_hour)


def wake_threshold(hour: float,
                   midsleep_hour: float = DEFAULT_CHRONOTYPE_MIDSLEEP_H) -> float:
    """@brief 醒来阈值 L(t) = 0.17 − 0.12·cos(2π(t−t_min)/24h)。

    参数语义同 sleep_onset_threshold；返回 [0.05, 0.29]，与 H(t) 恒差 0.5。
    """
    return WAKE_THRESHOLD_BASE - THRESHOLD_AMPLITUDE * _circadian_cos(hour, midsleep_hour)


def sleepiness(pressure: float, hour: float,
               midsleep_hour: float = DEFAULT_CHRONOTYPE_MIDSLEEP_H) -> float:
    """@brief 困意 = (S − L(t)) / (H(t) − L(t))，截断到 [0, 1.2]。"""
    low = wake_threshold(hour, midsleep_hour)
    span = sleep_onset_threshold(hour, midsleep_hour) - low
    return min(max((pressure - low) / span, 0.0), SLEEPINESS_CLIP_MAX)


def sleepiness_grade(value: float) -> SleepinessGrade:
    """@brief 把困意数值映射到五档分级（区间左闭右开，>=1.18 撑不住；档界与 felt.SLEEPINESS_BANDS 相同）。"""
    if value < GRADE_TIRED_MIN:
        return SleepinessGrade.AWAKE
    if value < GRADE_SLEEPY_MIN:
        return SleepinessGrade.SLIGHTLY_TIRED
    if value < GRADE_VERY_SLEEPY_MIN:
        return SleepinessGrade.SLEEPY
    if value < GRADE_CANNOT_STAY_AWAKE_MIN:
        return SleepinessGrade.VERY_SLEEPY
    return SleepinessGrade.CANNOT_STAY_AWAKE


def pressure_after_awake(pressure: float, hours: float) -> float:
    """@brief 清醒推进：S(t+dt) = 1 − (1 − S(t))·exp(−dt/18.2h)，渐近趋近 1。"""
    if hours < 0:
        raise ValueError("awake duration cannot be negative")
    return 1.0 - (1.0 - pressure) * math.exp(-hours / AWAKE_TIME_CONSTANT_H)


def pressure_after_sleep(pressure: float, hours: float) -> float:
    """@brief 睡眠推进：S(t+dt) = S(t)·exp(−dt/4.2h)，指数衰减趋近 0。"""
    if hours < 0:
        raise ValueError("sleep duration cannot be negative")
    return pressure * math.exp(-hours / SLEEP_TIME_CONSTANT_H)


def initial_sleep_pressure(hour: float, slept_hours: float,
                           midsleep_hour: float = DEFAULT_CHRONOTYPE_MIDSLEEP_H) -> float:
    """@brief 由场景起点时刻与已睡时长反推初始 sleep_pressure，不假定零。

    假设人物在入睡阈值穿越时刻入睡（S = H(入睡时刻)），此后按睡眠过程
    衰减：S(起点) = H(起点 − 已睡时长)·exp(−已睡时长/4.2h)。已睡时长为
    零时返回 H(起点)，即人物恰在入睡阈值上。

    :param hour: 场景起点自当日零点起的小时数。
    :param slept_hours: 起点前已连续睡眠的小时数（>= 0）。
    :param midsleep_hour: 人物 chronotype_midsleep，默认 04:00。
    """
    if slept_hours < 0:
        raise ValueError("slept_hours cannot be negative")
    onset = sleep_onset_threshold(hour - slept_hours, midsleep_hour)
    return pressure_after_sleep(onset, slept_hours)


def validate_sleep_need_hours(sleep_need_hours: float) -> float:
    """@brief 校验睡眠需要量在允许区间 [7, 9] 内，非法值抛 ValueError。"""
    if not SLEEP_NEED_MIN_H <= sleep_need_hours <= SLEEP_NEED_MAX_H:
        raise ValueError(f"sleep_need_hours must be within [7, 9], got {sleep_need_hours}")
    return sleep_need_hours


def deficit_after_awake(deficit_hours: float, hours: float,
                        sleep_need_hours: float = DEFAULT_SLEEP_NEED_H) -> float:
    """@brief 清醒推进睡眠不足账：每小时累积 need/(24−need)，钳制到 [0, 24]。

    默认 7.5h 需要量对应约 0.4545 h/h；24−need 分母保证"清醒 need 小时 +
    睡眠 24−need 小时"的平衡日账目净零。
    """
    validate_sleep_need_hours(sleep_need_hours)
    if hours < 0:
        raise ValueError("awake duration cannot be negative")
    rate = sleep_need_hours / (24.0 - sleep_need_hours)
    return min(max(deficit_hours + rate * hours, 0.0), SLEEP_DEFICIT_MAX_H)


def deficit_after_sleep(deficit_hours: float, hours: float) -> float:
    """@brief 睡眠推进睡眠不足账：每小时恢复 1h，钳制到 [0, 24]。"""
    if hours < 0:
        raise ValueError("sleep duration cannot be negative")
    recovered = deficit_hours - DEFICIT_RECOVERY_PER_SLEEP_H * hours
    return min(max(recovered, 0.0), SLEEP_DEFICIT_MAX_H)


class EfficiencyCurvePoint(FrozenModel):
    """@brief 效率曲线节点：输入睡眠不足小时数，输出效率乘数。

    输入与输出均为有界有限值（allow_inf_nan=False，nan/inf 构造即报错，
    ValidationError 是 ValueError 子类）；输入必须非负。
    """


    input_hours: float = Field(ge=0, allow_inf_nan=False)
    output_factor: float = Field(ge=0, allow_inf_nan=False)


def validate_efficiency_curve(points: tuple[EfficiencyCurvePoint, ...]) -> None:
    """@brief 校验效率曲线节点：非空且输入严格递增，失败抛 ValueError。"""
    if len(points) == 0:
        raise ValueError("efficiency curve needs at least one point")
    for left, right in zip(points, points[1:]):
        if not right.input_hours > left.input_hours:
            raise ValueError("efficiency curve inputs must strictly increase: "
                             f"{left.input_hours} -> {right.input_hours}")


def interpolate_efficiency(points: tuple[EfficiencyCurvePoint, ...], value: float) -> float:
    """@brief 在节点间线性插值；两端之外取边界节点值（调用前先校验节点）。"""
    validate_efficiency_curve(points)
    inputs = [point.input_hours for point in points]
    if value <= inputs[0]:
        return points[0].output_factor
    if value >= inputs[-1]:
        return points[-1].output_factor
    index = bisect.bisect_right(inputs, value) - 1
    left, right = points[index], points[index + 1]
    ratio = (value - left.input_hours) / (right.input_hours - left.input_hours)
    return left.output_factor + ratio * (right.output_factor - left.output_factor)


def food_utilization_efficiency(deficit_hours: float) -> float:
    """@brief 食品利用效率乘数：按 sleep_deficit_hours 在冻结节点间插值。"""
    points = tuple(EfficiencyCurvePoint(input_hours=point.input_value, output_factor=point.output_value)
                   for point in BODY_CONSTANTS.food_efficiency_curve)
    return interpolate_efficiency(points, deficit_hours)
