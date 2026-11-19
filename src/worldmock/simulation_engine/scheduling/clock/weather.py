"""@brief 世界天气按唯一随机根与本地日期产生，行程冻结当时天气系数。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.exchange_engine.definitions.constants import WEATHER_FACTORS
from worldmock.foundation.randomness import random_stream

#: 人物所见天气名称只来自实际世界天气。
WEATHER_LABELS = {"sunny": "晴", "cloudy": "多云", "light_rain": "小雨", "heavy_rain": "大雨", "snow": "雪"}


def local_weather_date(state) -> str:
    """@brief 当前绝对时刻换算为运行时区日期。"""
    return datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone)).date().isoformat()


def register_daily_weather(state) -> None:
    """@brief 在新建世界或推进到新一天的候选世界中，登记当天的天气供同秒报价和天气事件读取。"""
    date = local_weather_date(state)
    state.weather_by_date[date] = random_stream(state.random_state, f"weather:{date}").choice(tuple(WEATHER_FACTORS))


def current_weather(state) -> str:
    """@brief 当前天气必须已经初始化或由零点边界真实登记。"""
    return state.weather_by_date[local_weather_date(state)]


def current_weather_factor(state) -> float:
    """@brief 报价和受理共用世界实际天气系数。"""
    return WEATHER_FACTORS[current_weather(state)]
