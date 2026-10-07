"""@brief 身体动力学常数与四种资源元信息的唯一定义。"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from worldmock.base_models import CurvePoint
from worldmock.resources.models import Conservation, ResourceMeta


@dataclass(frozen=True)
class BodyConstants:
    """@brief 系统固定的身体参数，不从环境加载系数。"""

    #: 能量池上限，单位千卡。
    maximum_energy_kcal: int = 2400
    #: 每小时基础代谢千卡。
    basal_kcal_per_hour: int = 65
    #: 开始饥饿的能量阈值。
    hunger_threshold_kcal: int = 800
    #: 紧急饥饿的能量阈值。
    emergency_threshold_kcal: int = 400
    #: 活动键对应的固定代谢当量。
    activity_met: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({
        "wait": 1.3, "sleep": 0.95, "travel:walking": 3.5, "travel:driving": 1.5,
    }))
    #: 每清醒小时增加的睡眠债小时。
    awake_sleep_debt_per_hour: int = 1
    #: 每睡眠小时消除的睡眠债小时。
    sleep_recovery_per_hour: int = 2
    #: 每清醒小时增加的娱乐需要。
    entertainment_need_per_awake_hour: float = 0.08
    #: 睡眠债小时对应的食品利用比例曲线。
    food_efficiency_curve: tuple[CurvePoint, ...] = (
        CurvePoint(input_value=0, output_value=0.9), CurvePoint(input_value=8, output_value=0.9),
        CurvePoint(input_value=16, output_value=0.85), CurvePoint(input_value=24, output_value=0.8),
    )


BODY_CONSTANTS = BodyConstants()
RESOURCE_METAS = MappingProxyType({
    "money_fen": ResourceMeta("money_fen", "account", Conservation.CLOSED),
    "energy_kcal": ResourceMeta("energy_kcal", "level", Conservation.OPEN, 0, BODY_CONSTANTS.maximum_energy_kcal),
    "sleep_debt_hours": ResourceMeta("sleep_debt_hours", "level", Conservation.OPEN),
    "entertainment_need": ResourceMeta("entertainment_need", "level", Conservation.OPEN),
})
