"""@brief 身体数值只作为私有证据，公开层只读取同源体感文字。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.simulation_engine.body import felt
from worldmock.simulation_engine.body.sleep import sleepiness


def hour_of_day(epoch: int, timezone_name: str) -> float:
    """@brief 世界时钟换算为本地小数小时。"""
    local = local_datetime(epoch, timezone_name)
    return local.hour + local.minute / 60 + local.second / 3600


def local_datetime(epoch: int, timezone_name: str) -> datetime:
    """@brief 本地日期始终使用运行配置的时区。"""
    return datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))


def felt_values(agent, hour: float, meal_windows: felt.MealWindows | None = None) -> dict:
    """@brief 四个真实体感数值，不再保存情绪和娱乐需要。"""
    body = agent.body
    return {"appetite": felt.compute_appetite(body.energy_kcal, hour, meal_windows),
            "sleepiness": sleepiness(body.sleep_pressure, hour, body.chronotype_midsleep),
            "relaxation_need": body.relaxation_need, "social_need": body.social_need}


def body_projection(agent, hour: float, body, meal_windows: felt.MealWindows | None = None) -> dict:
    """@brief 体感数值与公开文字共用同一分级入口。"""
    values = felt_values(agent, hour, meal_windows)
    levels = felt.summarize_felt_levels(values, felt.EnergyReading(agent.body.energy_kcal,
                                                                 body.emergency_threshold_kcal))
    return {"levels": levels, "values": {**values, "energy_kcal": agent.body.energy_kcal,
                                          "sleep_pressure": agent.body.sleep_pressure,
                                          "sleep_deficit_hours": agent.body.sleep_deficit_hours}}
