"""出行用时与车费的两条公式：速度和价格来自出行方式，天气系数对全部方式生效，车费取较大值。"""

from decimal import localcontext
from math import ceil

import pytest

from tests.data_paths import CATALOG_ROOT
from worldmock.resource_engine.map.graph import Graph
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.resource_engine.catalog.models import MapEdge, MapGraph
from worldmock.exchange_engine.definitions.constants import WEATHER_FACTORS
from worldmock.exchange_engine.commerce.operators import delivery_seconds, evaluate_travel_fare, travel_seconds
from worldmock.simulation_engine.activities.travel.travel import plan_trip
from worldmock.resource_engine.catalog.travel_methods import WALKING, travel_methods


def _fixture_methods():
    """夹具环境的出行方式表，按名称索引：自带的步行加文件里的三行。"""
    return {method.name: method for method in travel_methods(load_catalog(CATALOG_ROOT, 7))}


def _plan(meters: int, name: str, weather: str = "sunny"):
    """在只有一条指定长度的边的图上，按夹具的一种出行方式规划行程。"""
    graph = Graph(MapGraph(nodes=("a", "b"), edges=(MapEdge(a="a", b="b", length_m=meters),)))
    return plan_trip(graph, "a", "b", _fixture_methods()[name], WEATHER_FACTORS[weather])


@pytest.mark.parametrize("meters,name,seconds,fare", [
    (1957, "步行", 1631, 0), (1957, "驾车", 280, 500), (1957, "打车", 280, 1400), (1957, "公共交通", 392, 200),
    (12600, "驾车", 1800, 2520), (12600, "打车", 1800, 3780), (12600, "公共交通", 2520, 200),
    (12600, "步行", 10500, 0),
])
def test_fixture_travel_methods_keep_the_fixed_durations_and_fares_to_the_fen(meters, name, seconds, fare):
    """夹具三行与自带的步行在晴天逐分保持引擎写死四种方式时的用时和车费。"""
    trip = _plan(meters, name)
    assert (trip.distance_meters, trip.duration_seconds, trip.fare_fen) == (meters, seconds, fare)


def test_fixture_rows_charge_the_retired_fixed_fares_on_every_meter():
    """夹具三行在 0 到 200,000 米的每一米上与修订前的车费算子逐分相同：驾车与打车按米计价取较大，公共交通固定 200 分。"""
    methods = _fixture_methods()
    retired = {"驾车": lambda meters: max(500, (meters * 2 + 9) // 10),
               "打车": lambda meters: max(1400, (meters * 3 + 9) // 10), "公共交通": lambda meters: 200}
    for name, expected in retired.items():
        method = methods[name]
        differing = [meters for meters in range(200_001) if evaluate_travel_fare(
            method.start_price_fen, method.price_per_km_fen, meters) != expected(meters)]
        assert differing == [], name
    assert all(evaluate_travel_fare(WALKING.start_price_fen, WALKING.price_per_km_fen, meters) == 0
               for meters in (0, 1, 1957, 200_000))


@pytest.mark.parametrize("start,per_km,meters,fare", [
    (500, 200, 2500, 500), (500, 200, 2501, 501), (500, 200, 2495, 500), (500, 200, 2496, 500),
    (1400, 300, 1957, 1400), (500, 200, 1957, 500), (500, 200, 12600, 2520), (1400, 300, 12600, 3780),
    (0, 300, 1, 1), (0, 300, 3334, 1001), (700, 0, 99_999, 700), (0, 0, 99_999, 0), (0, 0, 0, 0),
])
def test_fare_is_the_larger_of_the_start_price_and_the_per_kilometre_price_rounded_up(start, per_km, meters, fare):
    """车费取起步价与按公里计价的较大一个：按公里恰好等于起步价、差一分、起步价为零、每公里为零、两者都为零。"""
    assert evaluate_travel_fare(start, per_km, meters) == fare
    assert evaluate_travel_fare(start, per_km, meters) == max(start, ceil(per_km * meters / 1000))


@pytest.mark.parametrize("values", [(-1, 0, 10), (0, -1, 10), (0, 0, -1), (1.5, 0, 10), (0, 2.5, 10), (0, 0, 10.0),
                                    (True, 0, 10), (0, False, 10), ("5", 0, 10), (0, 0, None)])
def test_fare_inputs_are_strict_non_negative_integers(values, caplog):
    """车费全程整数运算：负数、小数、布尔和字符串都拒绝并留下英文日志。"""
    with pytest.raises(ValueError, match="must be a non-negative integer"):
        evaluate_travel_fare(*values)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


@pytest.mark.parametrize("weather,factor", sorted(WEATHER_FACTORS.items()))
def test_every_weather_factor_slows_dataset_defined_methods_and_walking_alike(weather, factor):
    """五种天气的系数对数据集定义的方式和自带的步行同样生效；车费不随天气变。"""
    expected = {"驾车": {1.0: 1800, 0.85: 2118, 0.7: 2572, 0.6: 3000}, "公共交通": {1.0: 2520, 0.85: 2965, 0.7: 3600, 0.6: 4200},
                "步行": {1.0: 10500, 0.85: 12353, 0.7: 15000, 0.6: 17500}}
    for name, seconds in expected.items():
        trip = _plan(12600, name, weather)
        assert trip.duration_seconds == seconds[factor], (name, weather)
        assert trip.fare_fen == _plan(12600, name).fare_fen


@pytest.mark.parametrize("speed,meters,factor,seconds", [
    (7, 12600, 1.0, 1800), (7, 12600, 0.85, 2118), (7, 12600, 0.7, 2572), (7, 12600, 0.6, 3000),
    (1.2, 62208, 0.6, 86400), (1.2, 62209, 0.6, 86402), (0.5, 25920, 0.6, 86400), (0.5, 25921, 0.6, 86404),
    (5, 1957, 1.0, 392), (1.2, 121, 1.0, 101), (1.2, 1, 1.0, 1), (7, 8, 1.0, 2), (2.5, 10, 1.0, 4), (3, 10, 1.0, 4),
    (0.1, 3, 0.7, 43), (1.2, 12000.01, 1.0, 10001), (7, 0, 1.0, 0),
])
def test_duration_rounds_up_with_exact_decimal_speed_and_weather(speed, meters, factor, seconds):
    """用时为距离除以（速度乘天气系数）向上取整：整数速度原样取，小数速度取最短十进制写法，不经二进制浮点相乘。"""
    assert travel_seconds(speed, meters, factor) == seconds


def test_duration_and_fare_ignore_the_callers_decimal_context():
    """调用方低精度的 Decimal 上下文不改变用时与车费。"""
    with localcontext(prec=2):
        assert travel_seconds(1.2, 12000.01) == 10001
        assert travel_seconds(7, 12600, 0.85) == 2118
        assert evaluate_travel_fare(500, 200, 12345) == 2469


def test_delivery_does_not_read_travel_weather():
    """外卖配送时长不乘天气系数。"""
    assert delivery_seconds(1000) == 800


@pytest.mark.parametrize("speed,weather", [(1.2, 0), (1.2, -1), (1.2, True), (0, 1), (-7, 1), (True, 1), ("7", 1),
                                           (float("inf"), 1), (float("nan"), 1), (None, 1)])
def test_invalid_speed_and_weather_are_rejected(speed, weather, caplog):
    """速度与天气系数必须是大于 0 的有限数值，布尔和字符串不接受。"""
    with pytest.raises(ValueError):
        travel_seconds(speed, 1000, weather)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)
