"""@brief 固定身体规则与单人人设质量组合的唯一运行视图。"""

from dataclasses import dataclass
from typing import Mapping

from worldmock.exchange.body import BODY_CONSTANTS
from worldmock.input_models import PersonaConfig


@dataclass(frozen=True, slots=True)
class BodyConfig:
    """@brief 固定身体参数只从系统常数装配，环境不声明系数。"""

    #: 人物实际质量，单位千克。
    mass_kg: float
    #: 身体能量上限，单位千卡。
    maximum_energy_kcal: int
    #: 普通饥饿阈值，单位千卡。
    hunger_threshold_kcal: int
    #: 紧急饥饿阈值，单位千卡。
    emergency_threshold_kcal: int
    #: 清醒每小时的放松需要自然增长。
    relaxation_need_per_awake_hour: float
    #: 七个动作和内容强度的固定代谢当量。
    activity_met: Mapping[str, float]


def assemble_body_config(persona: PersonaConfig) -> BodyConfig:
    """@brief 用已校验的人物质量与不可变系统常数形成结算视图。"""
    constants = BODY_CONSTANTS
    return BodyConfig(persona.body.mass_kg, constants.maximum_energy_kcal, constants.hunger_threshold_kcal,
                      constants.emergency_threshold_kcal, constants.relaxation_need_per_awake_hour,
                      constants.activity_met)
