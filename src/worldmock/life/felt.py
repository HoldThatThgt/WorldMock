"""@brief 饥饿、困意、放松需要与社交需要的体感分级。"""

from __future__ import annotations

from worldmock.base_models import FrozenModel
import math
import logging
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 饥饿锚定热量（场景参数默认值，docs/design/02-exchange-engine.md「身体共同结算」）。
#: experiment_assumption：规格冻结的场景默认，无文献出处。
APPETITE_ANCHOR_KCAL_DEFAULT = 1200.0
#: 食欲公式的分母下限热量（只用于 appetite 数值公式）。低能量分级的 400 kcal 保底档取身体配置的
#: 紧急阈值，不复用本常量（docs/design/02-exchange-engine.md「身体共同结算」）。
#: experiment_assumption：规格冻结值，无文献出处。
APPETITE_KCAL_FLOOR = 400.0
#: 「饿得发慌」档的能量上界：能量低于该值时饥饿分级只由能量决定（固定 100 千卡，不随体重变）。
#: experiment_assumption：登记在 docs/design/02-exchange-engine.md「身体共同结算」。
APPETITE_STARVING_KCAL = 100.0
#: 第六档饥饿分级文本（只由能量决定，不进 APPETITE_BANDS）。
APPETITE_STARVING_LEVEL = "饿得发慌"
#: 昼夜项振幅（峰 1.17 / 谷 0.83，峰谷差 17%）。
#: 出处：Scheer、Morris 与 Shea 2013 内源饥饿节律（08:00 低谷、20:00 峰值）。
APPETITE_CIRCADIAN_AMPLITUDE = 0.17
#: 昼夜项峰值时刻（自午夜起的小时数）。出处：Scheer、Morris 与 Shea 2013。
APPETITE_CIRCADIAN_PEAK_HOUR = 20.0
#: 餐时项幅度（窗口内 +0.15，窗口外为零）。
#: experiment_assumption：docs/design/02-exchange-engine.md「身体共同结算」明示
#: 属实验假设。
APPETITE_MEAL_WINDOW_BONUS = 0.15

#: 饥饿分级阈值表（(下界, 分级文本)，区间 [lo, hi)，顶档 ≥ 末下界），按 appetite 数值分级。
#: 两条低能量规则叠加在表外（appetite_level）：能量低于紧急阈值至少为顶档「饿得难受」，
#: 低于 APPETITE_STARVING_KCAL 为第六档「饿得发慌」。
#: experiment_assumption：docs/design/02-exchange-engine.md「身体共同结算」冻结阈值，无文献出处。
APPETITE_BANDS: tuple[tuple[float, str], ...] = (
    (0.15, "有点想吃"),
    (0.35, "饿"),
    (0.6, "很饿"),
    (0.85, "饿得难受"),
)
#: 饥饿分级基档（低于首下界）。experiment_assumption：docs/design/02-exchange-engine.md「身体共同结算」。
APETITE_BASE_LEVEL = "不饿"

#: 困意分级阈值表：输入是 sleep.sleepiness 归一后的困意 f = (S − L(t)) / (H(t) − L(t))，截断到 [0, 1.2]
#: （body 与 projection 先按两过程压力 S 与昼夜阈值算出 f 再分级）；f = 1 即模型自身的入睡条件。
#: experiment_assumption：档界按 KSS 等价刻度 K = 7 + 7.6·(f − 1) 在 K = 5.5、6.5、7.5、8.4 处切档，斜率 7.6 由
#: Shochat 等 2021 的两个锚点（就寝前 4 小时 KSS 约 4、入睡时约 7）对工作日稳态拟合（7.47–7.81）而来，是实验假设，
#: 登记在 docs/design/02-exchange-engine.md；「撑不住」取 1.18，留 0.02 余量不与截断上界 1.2 重合
#: （docs/design/04-simulation-engine.md）。sleep.py 的 GRADE_* 常数是同一组档界，由测试锁定一致。
SLEEPINESS_BANDS: tuple[tuple[float, str], ...] = (
    (0.80, "有点乏"),
    (0.93, "困"),
    (1.07, "很困"),
    (1.18, "撑不住"),
)
#: 困意分级基档（低于首档下界 0.80）。
SLEEPINESS_BASE_LEVEL = "清醒"

#: 放松需要分级阈值表。experiment_assumption：docs/design/02-exchange-engine.md「放松与社交需要」冻结阈值，
#: 无文献出处。
RELAXATION_BANDS: tuple[tuple[float, str], ...] = (
    (0.3, "有点想放松"),
    (0.6, "很想放松"),
)
#: 放松需要分级基档。experiment_assumption：docs/design/02-exchange-engine.md「放松与社交需要」。
RELAXATION_BASE_LEVEL = "不闷"

#: 社交需要分级阈值表。experiment_assumption：docs/design/02-exchange-engine.md「放松与社交需要」冻结阈值，
#: 无文献出处。
SOCIAL_BANDS: tuple[tuple[float, str], ...] = (
    (0.3, "想和人说说话"),
    (0.6, "有点孤单"),
)
#: 社交需要分级基档。experiment_assumption：docs/design/02-exchange-engine.md「放松与社交需要」。
SOCIAL_BASE_LEVEL = "不想找人"

class MealWindows(FrozenModel):
    """@brief 人物卡片声明的午餐/晚餐窗口（自午夜起的小时数，半开区间 [start, end)）。"""


    lunch: tuple[float, float] | None = None
    dinner: tuple[float, float] | None = None

    @model_validator(mode="after")
    def validate_windows(self) -> "MealWindows":
        """@brief 窗口须满足 0 ≤ start < end ≤ 24，且两窗口不相交。"""
        for name, window in (("lunch", self.lunch), ("dinner", self.dinner)):
            if window is not None and not 0 <= window[0] < window[1] <= 24:
                logging.error("Felt input is invalid")
                raise ValueError(f"{name} window must satisfy 0 <= start < end <= 24")
        if (
            self.lunch is not None
            and self.dinner is not None
            and self.lunch[1] > self.dinner[0]
            and self.dinner[1] > self.lunch[0]
        ):
            logging.error("Felt input is invalid")
            raise ValueError("dinner window must not overlap lunch window")
        return self


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


def _require_time_hour(time_hour: float) -> None:
    """@brief 校验时刻为自午夜起的小时数 [0, 24)。"""
    if not 0 <= time_hour < 24:
        logging.error("Felt input is invalid")
        raise ValueError("time_hour must be within [0, 24)")


def appetite_circadian_multiplier(time_hour: float) -> float:
    """@brief 饥饿昼夜因子 1 + 0.17·cos(2π(t −
    20h)/24h)（docs/design/02-exchange-engine.md「身体共同结算」）。

    20:00 峰值 1.17、08:00 低谷 0.83，峰谷差 17%。
    出处：Scheer、Morris 与 Shea 2013 的内源饥饿节律。
    """
    _require_time_hour(time_hour)
    phase = 2.0 * math.pi * (time_hour - APPETITE_CIRCADIAN_PEAK_HOUR) / 24.0
    return 1.0 + APPETITE_CIRCADIAN_AMPLITUDE * math.cos(phase)


def meal_window_bonus(time_hour: float, windows: MealWindows) -> float:
    """@brief 餐时项：t 落在任一声明窗口内为 +0.15，窗口外为
    0（docs/design/02-exchange-engine.md「身体共同结算」）。

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
    """@brief 计算饥饿体感 appetite（docs/design/02-exchange-engine.md「身体共同结算」）。

    appetite = clamp((anchor − energy)/(anchor − 400), 0, 1) · 昼夜因子 + 餐时项。
    只夹基值分数，乘昼夜因子与加餐时项后可超过 1（上限 1.32）——顶档按
    ≥ 0.85 判"饿得难受"，与 docs/design/02-exchange-engine.md「身体共同结算」的 800/400 kcal 阈值一致。
    """
    if appetite_anchor_kcal <= APPETITE_KCAL_FLOOR:
        logging.error("Felt input is invalid")
        raise ValueError("appetite_anchor_kcal must exceed 400 kcal")
    if energy_kcal < 0:
        logging.error("Felt input is invalid")
        raise ValueError("energy_kcal must be non-negative")
    _require_time_hour(time_hour)
    span = appetite_anchor_kcal - APPETITE_KCAL_FLOOR
    base = _clamp((appetite_anchor_kcal - energy_kcal) / span, 0.0, 1.0)
    windows = meal_windows if meal_windows is not None else MealWindows()
    return base * appetite_circadian_multiplier(time_hour) + meal_window_bonus(
        time_hour, windows
    )


@dataclass(frozen=True, slots=True)
class EnergyReading:
    """@brief 饥饿分级的能量输入：当前能量与身体配置的紧急阈值（两条低能量规则的判据）。"""

    energy_kcal: float
    emergency_kcal: float


def appetite_level(appetite: float, energy_kcal: float, emergency_kcal: float) -> str:
    """@brief 饥饿分级文本：不饿/有点想吃/饿/很饿/饿得难受/饿得发慌。

    先按 appetite 数值取五档（docs/design/02-exchange-engine.md「身体共同结算」），再叠加两条低能量规则
    （docs/design/02-exchange-engine.md「身体共同结算」）：能量低于紧急阈值时至少「饿得难受」，
    低于 APPETITE_STARVING_KCAL 时为「饿得发慌」；两条都只看能量，不受昼夜项与餐时项影响。
    """
    if energy_kcal < APPETITE_STARVING_KCAL:
        return APPETITE_STARVING_LEVEL
    if energy_kcal < emergency_kcal:
        return APPETITE_BANDS[-1][1]
    return _band_level(appetite, APETITE_BASE_LEVEL, APPETITE_BANDS)


def sleepiness_level(sleepiness: float) -> str:
    """@brief 困意分级文本：清醒/有点乏/困/很困/撑不住（输入为 sleep.sleepiness 归一后的困意，档界见 SLEEPINESS_BANDS）。"""
    return _band_level(sleepiness, SLEEPINESS_BASE_LEVEL, SLEEPINESS_BANDS)


def relaxation_need_level(need: float) -> str:
    """@brief 放松需要分级文本（docs/design/02-exchange-engine.md「放松与社交需要」）：不闷/有点想放松/很想放松。"""
    return _band_level(need, RELAXATION_BASE_LEVEL, RELAXATION_BANDS)


def social_need_level(need: float) -> str:
    """@brief 社交需要分级文本（docs/design/02-exchange-engine.md「放松与社交需要」）：不想找人/想和人说说话/有点孤单。
    """
    return _band_level(need, SOCIAL_BASE_LEVEL, SOCIAL_BANDS)


def sleep_debt_phrase(deficit_hours: float) -> str:
    """@brief 睡前提示：现在睡大约多久能补满欠的觉，只给半小时一档的时长，不给身体原值。

    欠的觉超过 12 小时写 12 小时以上，不到一刻钟写不欠觉，其余四舍五入到半小时。
    """
    if deficit_hours > 12:
        return "现在睡，12 小时以上才能补满"
    if deficit_hours < 0.25:
        return "现在不欠觉"
    steps = math.floor(deficit_hours * 2 + 0.5)
    return "现在睡，约半小时能补满" if steps == 1 else f"现在睡，约 {steps / 2:g} 小时能补满"


def summarize_felt_levels(values: Mapping[str, float], energy: EnergyReading) -> dict[str, str]:
    """@brief 观察和唤醒只读取同一组四个体感分级。"""
    return {
        "appetite": appetite_level(values["appetite"], energy.energy_kcal, energy.emergency_kcal),
        "sleepiness": sleepiness_level(values["sleepiness"]),
        "relaxation": relaxation_need_level(values["relaxation_need"]),
        "social": social_need_level(values["social_need"]),
    }


def changed_felt_levels(
    current: Mapping[str, str], at_last_decision: Mapping[str, str]
) -> list[str]:
    """@brief 滞回判定：与"上一次实际提供决策时的分级"比较，返回变化的体感键。

    参照是决策而非结算（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）：结算边界重算产生的中间分级不进入
    参照，只要与决策时一致就不构成变化，天然抑制分级边界附近的抖动，不另设
    阈值参数。键只在任一侧出现也视为变化（首次观察/信号消失）。
    """
    names = set(current) | set(at_last_decision)
    return sorted(name for name in names if current.get(name) != at_last_decision.get(name))
