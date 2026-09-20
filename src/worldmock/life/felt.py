"""@brief 体感分级与情绪：饥饿/困意/娱乐/社交分级、社交与情绪账本、滞回判定。

纯函数库（stdlib + pydantic），不 import 本项目其他模块：
- 饥饿体感含昼夜因子（Scheer、Morris 与 Shea 2013 内源饥饿节律）与餐时项；
- 困意只做分级：原始压力值（两过程睡眠压力演化）由调用方传入，演化归 sleep.py；
- 社交需要按账本速率推进并夹到 [0, 1]；
- 情绪按事件加脉冲并以半衰期衰减：只影响体感呈现与记忆重要度加权，不直接
  改变任何动作可行性（docs/dev/SPEC-life-runtime.md「其他体感分级」）——本模块不耦合可行性判断，由调用方保证；
- 滞回以"上一次实际提供决策时的分级"为参照（docs/dev/SPEC-life-runtime.md「滞回规则」），非结算边界。
数值参数一律标注 experiment_assumption（明确引用文献的常数除外）。
分级区间为下闭上开 [lo, hi)，顶档取 ≥ 顶下界；分级文本为中文提示文本。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 饥饿锚定热量（场景参数默认值，docs/dev/SPEC-life-runtime.md「其他体感分级」）。
#: experiment_assumption：规格冻结的场景默认，无文献出处。
APPETITE_ANCHOR_KCAL_DEFAULT = 1200.0
#: 公式分母下限热量（800/400 kcal 作为世界事实记录，不进分级文本，docs/dev/SPEC-life-runtime.md「其他体感分级」）。
#: experiment_assumption：规格冻结值，无文献出处。
APPETITE_KCAL_FLOOR = 400.0
#: 昼夜项振幅（峰 1.17 / 谷 0.83，峰谷差 17%）。
#: 出处：Scheer、Morris 与 Shea 2013 内源饥饿节律（08:00 低谷、20:00 峰值）。
APPETITE_CIRCADIAN_AMPLITUDE = 0.17
#: 昼夜项峰值时刻（自午夜起的小时数）。出处：Scheer、Morris 与 Shea 2013。
APPETITE_CIRCADIAN_PEAK_HOUR = 20.0
#: 餐时项幅度（窗口内 +0.15，窗口外为零）。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」明示
#: 属实验假设。
APPETITE_MEAL_WINDOW_BONUS = 0.15

#: 饥饿分级阈值表（(下界, 分级文本)，区间 [lo, hi)，顶档 ≥ 末下界）。
#: experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」冻结阈值，无文献出处。
APPETITE_BANDS: tuple[tuple[float, str], ...] = (
    (0.15, "有点想吃"),
    (0.35, "饿"),
    (0.6, "很饿"),
    (0.85, "饿得难受"),
)
#: 饥饿分级基档（低于首下界）。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」。
APETITE_BASE_LEVEL = "不饿"

#: 困意分级阈值表（输入为 sleep.py 演化的原始压力值，域约 0-1.2）。
#: experiment_assumption：修订合同冻结阈值，无文献出处。
SLEEPINESS_BANDS: tuple[tuple[float, str], ...] = (
    (0.5, "有点乏"),
    (0.75, "困"),
    (0.9, "很困"),
    (1.0, "撑不住"),
)
#: 困意分级基档。experiment_assumption：修订合同冻结阈值。
SLEEPINESS_BASE_LEVEL = "清醒"

#: 娱乐需要分级阈值表。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」冻结阈值，无文献出处。
ENTERTAINMENT_BANDS: tuple[tuple[float, str], ...] = (
    (0.3, "有点想放松"),
    (0.6, "很想放松"),
)
#: 娱乐需要分级基档。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」。
ENTERTAINMENT_BASE_LEVEL = "不闷"

#: 社交需要分级阈值表。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」冻结阈值，无文献出处。
SOCIAL_BANDS: tuple[tuple[float, str], ...] = (
    (0.3, "想和人说说话"),
    (0.6, "有点孤单"),
)
#: 社交需要分级基档。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」。
SOCIAL_BASE_LEVEL = "不想找人"

#: 情绪分级阈值表（mood_valence ∈ [−1, 1]）。
#: experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」冻结阈值，无文献出处。
MOOD_BANDS: tuple[tuple[float, str], ...] = (
    (-0.4, "有点烦"),
    (-0.15, "平静"),
    (0.15, "挺好"),
    (0.4, "很开心"),
)
#: 情绪分级基档（低于 −0.4）。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」。
MOOD_BASE_LEVEL = "很烦"

#: 社交需要账本速率（每小时/每条）。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」明示
#: "数值为实验假设"；Humanoid Agents 的 social 需要与 The Sims 的 Social
#: motive 为结构依据。
SOCIAL_NEED_AWAKE_RATE = 0.04
SOCIAL_NEED_CONVERSATION_RATE = 0.5
SOCIAL_NEED_CALL_RATE = 0.4
SOCIAL_NEED_SHARED_MEAL_RATE = 0.3
SOCIAL_NEED_MESSAGE_RELIEF = 0.05

#: 情绪脉冲表（基础幅度，应用时再乘 (1 + 0.5·N_norm)）。
#: experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」明示"脉冲与半衰期是实验假设"；
#: OCC 评价理论与 PAD 心境模型为结构依据。
MOOD_PULSES: dict[str, float] = {
    "work_review_passed": 0.3,
    "work_review_returned": -0.3,
    "task_deadline_missed": -0.4,
    "message_received": 0.1,
    "discomfort_hourly": -0.1,
    "plan_segment_completed": 0.05,
    "service_refused": -0.05,
}
#: 情绪半衰期基准（分钟）。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」。
MOOD_HALFLIFE_MINUTES = 45.0
#: 半衰期神经质调制系数（× (1 + 0.3·N_norm)）。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」。
MOOD_HALFLIFE_NEUROTICISM_FACTOR = 0.3
#: 脉冲幅度神经质调制系数（× (1 + 0.5·N_norm)）。experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」。
MOOD_PULSE_NEUROTICISM_FACTOR = 0.5
#: 连续饿/困的不适脉冲触发时长的下界（小时，≥ 该值才计脉冲）。
#: experiment_assumption：docs/dev/SPEC-life-runtime.md「其他体感分级」冻结值。
MOOD_DISCOMFORT_TRIGGER_HOURS = 2.0


class MealWindows(BaseModel):
    """@brief 人物卡片声明的午餐/晚餐窗口（自午夜起的小时数，半开区间 [start, end)）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lunch: tuple[float, float] | None = None
    dinner: tuple[float, float] | None = None

    @model_validator(mode="after")
    def validate_windows(self) -> "MealWindows":
        """@brief 窗口须满足 0 ≤ start < end ≤ 24，且两窗口不相交。"""
        for name, window in (("lunch", self.lunch), ("dinner", self.dinner)):
            if window is not None and not 0 <= window[0] < window[1] <= 24:
                raise ValueError(f"{name} window must satisfy 0 <= start < end <= 24")
        if (
            self.lunch is not None
            and self.dinner is not None
            and self.lunch[1] > self.dinner[0]
            and self.dinner[1] > self.lunch[0]
        ):
            raise ValueError("dinner window must not overlap lunch window")
        return self


class SocialExposure(BaseModel):
    """@brief 一个结算段内的社交暴露量（小时/条数由结算侧从时间线拆解）。

    交谈/通话/共同用餐的小时同时计入清醒小时（账本各项线性叠加，拆解归调用方）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    awake_hours: float = Field(default=0.0, ge=0)
    conversation_hours: float = Field(default=0.0, ge=0)
    call_hours: float = Field(default=0.0, ge=0)
    shared_meal_hours: float = Field(default=0.0, ge=0)
    messages_read: int = Field(default=0, ge=0)


def _clamp(value: float, low: float, high: float) -> float:
    """@brief 把 value 夹到闭区间 [low, high]。"""
    return max(low, min(high, value))


def _band_level(value: float, base_level: str, bands: tuple[tuple[float, str], ...]) -> str:
    """@brief 按下界阈值表分级：低于首下界取 base_level，否则取最大 lo ≤ value 档。"""
    lower_bounds = [band[0] for band in bands]
    index = bisect_right(lower_bounds, value) - 1
    if index < 0:
        return base_level
    return bands[index][1]


def _require_neuroticism_range(neuroticism_norm: float) -> None:
    """@brief 校验 N_norm 已归一到 [−1, 1]，越界即拒绝。"""
    if not -1.0 <= neuroticism_norm <= 1.0:
        raise ValueError("neuroticism_norm must be within [-1, 1]")


def _require_time_hour(time_hour: float) -> None:
    """@brief 校验时刻为自午夜起的小时数 [0, 24)。"""
    if not 0 <= time_hour < 24:
        raise ValueError("time_hour must be within [0, 24)")


def appetite_circadian_multiplier(time_hour: float) -> float:
    """@brief 饥饿昼夜因子 1 + 0.17·cos(2π(t − 20h)/24h)（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    20:00 峰值 1.17、08:00 低谷 0.83，峰谷差 17%。
    出处：Scheer、Morris 与 Shea 2013 的内源饥饿节律。
    """
    _require_time_hour(time_hour)
    phase = 2.0 * math.pi * (time_hour - APPETITE_CIRCADIAN_PEAK_HOUR) / 24.0
    return 1.0 + APPETITE_CIRCADIAN_AMPLITUDE * math.cos(phase)


def meal_window_bonus(time_hour: float, windows: MealWindows) -> float:
    """@brief 餐时项：t 落在任一声明窗口内为 +0.15，窗口外为 0（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    两窗口已由 MealWindows 校验不相交，故至多命中其一、不叠加。
    experiment_assumption：餐时项幅度属实验假设。
    """
    _require_time_hour(time_hour)
    for window in (windows.lunch, windows.dinner):
        if window is not None and window[0] <= time_hour < window[1]:
            return APPETITE_MEAL_WINDOW_BONUS
    return 0.0


def compute_appetite(
    energy_kcal: float,
    time_hour: float,
    meal_windows: MealWindows | None = None,
    appetite_anchor_kcal: float = APPETITE_ANCHOR_KCAL_DEFAULT,
) -> float:
    """@brief 计算饥饿体感 appetite（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    appetite = clamp((anchor − energy)/(anchor − 400), 0, 1) · 昼夜因子 + 餐时项。
    只夹基值分数，乘昼夜因子与加餐时项后可超过 1（上限 1.32）——顶档按
    ≥ 0.85 判"饿得难受"，与 docs/dev/SPEC-life-runtime.md「其他体感分级」的 800/400 kcal 阈值一致。
    """
    if appetite_anchor_kcal <= APPETITE_KCAL_FLOOR:
        raise ValueError("appetite_anchor_kcal must exceed 400 kcal")
    if energy_kcal < 0:
        raise ValueError("energy_kcal must be non-negative")
    _require_time_hour(time_hour)
    span = appetite_anchor_kcal - APPETITE_KCAL_FLOOR
    base = _clamp((appetite_anchor_kcal - energy_kcal) / span, 0.0, 1.0)
    windows = meal_windows if meal_windows is not None else MealWindows()
    return base * appetite_circadian_multiplier(time_hour) + meal_window_bonus(
        time_hour, windows
    )


def appetite_level(appetite: float) -> str:
    """@brief 饥饿分级文本（docs/dev/SPEC-life-runtime.md「其他体感分级」）：不饿/有点想吃/饿/很饿/饿得难受。"""
    return _band_level(appetite, APETITE_BASE_LEVEL, APPETITE_BANDS)


def sleepiness_level(sleepiness: float) -> str:
    """@brief 困意分级文本：清醒/有点乏/困/很困/撑不住（原始压力值由 sleep.py 传入）。"""
    return _band_level(sleepiness, SLEEPINESS_BASE_LEVEL, SLEEPINESS_BANDS)


def entertainment_need_level(need: float) -> str:
    """@brief 娱乐需要分级文本（docs/dev/SPEC-life-runtime.md「其他体感分级」）：不闷/有点想放松/很想放松。"""
    return _band_level(need, ENTERTAINMENT_BASE_LEVEL, ENTERTAINMENT_BANDS)


def social_need_level(need: float) -> str:
    """@brief 社交需要分级文本（docs/dev/SPEC-life-runtime.md「其他体感分级」）：不想找人/想和人说说话/有点孤单。"""
    return _band_level(need, SOCIAL_BASE_LEVEL, SOCIAL_BANDS)


def mood_level(valence: float) -> str:
    """@brief 情绪分级文本（docs/dev/SPEC-life-runtime.md「其他体感分级」）：很烦/有点烦/平静/挺好/很开心。"""
    return _band_level(valence, MOOD_BASE_LEVEL, MOOD_BANDS)


def summarize_felt_levels(
    appetite: float,
    sleepiness: float,
    entertainment_need: float,
    social_need: float,
    mood_valence: float,
) -> dict[str, str]:
    """@brief 汇总五个体感信号为分级文本字典（滞回判定与提示渲染的输入）。"""
    return {
        "appetite": appetite_level(appetite),
        "sleepiness": sleepiness_level(sleepiness),
        "entertainment": entertainment_need_level(entertainment_need),
        "social": social_need_level(social_need),
        "mood": mood_level(mood_valence),
    }


def apply_social_need_ledger(social_need: float, exposure: SocialExposure) -> float:
    """@brief 按账本速率推进社交需要并夹到 [0, 1]（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    清醒每小时 +0.04；与在场人物交谈每小时 −0.5、与联系人通话每小时 −0.4、
    共同用餐每小时 −0.3、收到并阅读一条来自人物或联系人的消息 −0.05。
    数值为实验假设（docs/dev/SPEC-life-runtime.md「其他体感分级」）。
    """
    if not 0 <= social_need <= 1:
        raise ValueError("social_need must be within [0, 1]")
    delta = (
        SOCIAL_NEED_AWAKE_RATE * exposure.awake_hours
        - SOCIAL_NEED_CONVERSATION_RATE * exposure.conversation_hours
        - SOCIAL_NEED_CALL_RATE * exposure.call_hours
        - SOCIAL_NEED_SHARED_MEAL_RATE * exposure.shared_meal_hours
        - SOCIAL_NEED_MESSAGE_RELIEF * exposure.messages_read
    )
    return _clamp(social_need + delta, 0.0, 1.0)


def normalize_neuroticism(raw_value: float, scale_low: float, scale_high: float) -> float:
    """@brief 把人物卡片原始神经质线性归一到 [−1, 1]（越界夹断）。

    量表上下界由调用方提供（人物卡片量纲归画像编译管，本模块不预设）。
    """
    if scale_high <= scale_low:
        raise ValueError("neuroticism scale bounds must satisfy low < high")
    ratio = (raw_value - scale_low) / (scale_high - scale_low)
    return _clamp(2.0 * ratio - 1.0, -1.0, 1.0)


def mood_halflife_minutes(neuroticism_norm: float) -> float:
    """@brief 情绪半衰期 45 分钟 × (1 + 0.3·N_norm)（docs/dev/SPEC-life-runtime.md「其他体感分级」）。"""
    _require_neuroticism_range(neuroticism_norm)
    factor = 1.0 + MOOD_HALFLIFE_NEUROTICISM_FACTOR * neuroticism_norm
    return MOOD_HALFLIFE_MINUTES * factor


def apply_mood_pulse(
    mood_valence: float, base_amplitude: float, neuroticism_norm: float
) -> float:
    """@brief 叠加一次情绪脉冲并夹到 [−1, 1]（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    幅度调制 × (1 + 0.5·N_norm)；base_amplitude 取 MOOD_PULSES 的基础幅度
    （不适脉冲取 discomfort_pulse(连续小时数)）。
    """
    if not -1.0 <= mood_valence <= 1.0:
        raise ValueError("mood_valence must be within [-1, 1]")
    _require_neuroticism_range(neuroticism_norm)
    factor = 1.0 + MOOD_PULSE_NEUROTICISM_FACTOR * neuroticism_norm
    return _clamp(mood_valence + base_amplitude * factor, -1.0, 1.0)


def discomfort_pulse(continuous_hours: float) -> float:
    """@brief 连续饿/困的不适脉冲基础幅度：不足 2 小时为 0，否则 −0.1/小时（docs/dev/SPEC-life-runtime.md「其他体感分级」）。"""
    if continuous_hours < 0:
        raise ValueError("continuous_hours must be non-negative")
    if continuous_hours < MOOD_DISCOMFORT_TRIGGER_HOURS:
        return 0.0
    return MOOD_PULSES["discomfort_hourly"] * continuous_hours


def decay_mood_valence(
    mood_valence: float, elapsed_minutes: float, neuroticism_norm: float
) -> float:
    """@brief 按半衰期指数衰减情绪（docs/dev/SPEC-life-runtime.md「其他体感分级」）：valence · 0.5^(elapsed / halflife)。"""
    if elapsed_minutes < 0:
        raise ValueError("elapsed_minutes must be non-negative")
    _require_neuroticism_range(neuroticism_norm)
    halflife = mood_halflife_minutes(neuroticism_norm)
    return mood_valence * 0.5 ** (elapsed_minutes / halflife)


def changed_felt_levels(
    current: Mapping[str, str], at_last_decision: Mapping[str, str]
) -> list[str]:
    """@brief 滞回判定：与"上一次实际提供决策时的分级"比较，返回变化的体感键。

    参照是决策而非结算（docs/dev/SPEC-life-runtime.md「滞回规则」）：结算边界重算产生的中间分级不进入
    参照，只要与决策时一致就不构成变化，天然抑制分级边界附近的抖动，不另设
    阈值参数。键只在任一侧出现也视为变化（首次观察/信号消失）。
    """
    names = set(current) | set(at_last_decision)
    return sorted(name for name in names if current.get(name) != at_last_decision.get(name))
