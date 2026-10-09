"""@brief 世界天气按唯一随机根与本地日期产生，行程冻结当时天气系数。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.life.randomness import random_stream

#: 固定等概率天气及其对全部出行方式的速度系数。
WEATHER_FACTORS = {"sunny": 1.0, "cloudy": 1.0, "light_rain": 0.85, "heavy_rain": 0.7, "snow": 0.6}
#: 人物所见天气名称只来自实际世界天气。
WEATHER_LABELS = {"sunny": "晴", "cloudy": "多云", "light_rain": "小雨", "heavy_rain": "大雨", "snow": "雪"}


def local_weather_date(state) -> str:
    """@brief 当前绝对时刻换算为运行时区日期。"""
    return datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone)).date().isoformat()


def generated_weather(state, date: str) -> str:
    """@brief 同日期同随机根只确定一次天气，恢复不重新抽取。"""
    return random_stream(state.random_state, f"weather:{date}").choice(tuple(WEATHER_FACTORS))


def current_weather(state) -> str:
    """@brief 当前天气必须已经初始化或由零点边界真实登记。"""
    return state.weather_by_date[local_weather_date(state)]


def current_weather_factor(state) -> float:
    """@brief 报价和受理共用世界实际天气系数。"""
    return WEATHER_FACTORS[current_weather(state)]
