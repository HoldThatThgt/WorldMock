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


def register_daily_weather(state, catalog) -> None:
    """@brief 在新建世界或推进到新一天的候选世界中，按城市登记当天天气供同秒报价和天气事件读取。"""
    date = local_weather_date(state)
    cities = sorted({place.city_id for place in catalog.locations.values()})
    state.weather_by_date[date] = {
        city_id: random_stream(state.random_state, f"weather:{city_id}:{date}").choice(tuple(WEATHER_FACTORS))
        for city_id in cities
    }


def current_weather(state, city_id: str) -> str:
    """@brief 指定城市的当前天气必须已经初始化或由零点边界真实登记。"""
    return state.weather_by_date[local_weather_date(state)][city_id]


def city_weather_factor(state, city_id: str) -> float:
    """@brief 报价和同城业务读取指定城市实际天气系数。"""
    return WEATHER_FACTORS[current_weather(state, city_id)]


def trip_weather_factor(state, origin_city: str, destination_city: str) -> float:
    """@brief 所有出行统一按起终点所在城市天气系数较小值冻结天气系数。"""
    return min(city_weather_factor(state, origin_city), city_weather_factor(state, destination_city))
