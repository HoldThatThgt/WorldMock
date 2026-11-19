"""@brief 固定交换算子的收货、金额与时长边界证明。"""

from decimal import localcontext
from unittest.mock import Mock

import pytest

from worldmock.exchange_engine.commerce import operators
from worldmock.exchange_engine.commerce.operators import (
    delivery_fee_fen,
    delivery_seconds,
    delivery_weather_delay_seconds,
    evaluate_goods_receipt,
    evaluate_purchase,
    evaluate_travel_fare,
    goods_quality,
    travel_seconds,
)
from worldmock.exchange_engine.definitions.constants import WEATHER_FACTORS


@pytest.mark.parametrize("category,quantity,expected", [
    ("meal", 1, 600), ("meal", 3, 1800), ("snack", 1, 200), ("snack", 2, 400), ("drink", 1, 0.10),
    ("drink", 4, 0.10), ("goods", 1, 0.10), ("goods", 5, 0.10),
])
def test_goods_receipt_has_quantity_only_for_food(category, quantity, expected):
    """@brief 食品热量乘数量，非食品每条订单行只结算一次固定效果。"""
    assert evaluate_goods_receipt(category, quantity) == expected


def test_travel_and_delivery_round_up_with_fare_minimum():
    """@brief 非整除距离上取整，两个价格都为零的方式免费，起步价是最低车费。"""
    assert travel_seconds(1.2, 1) == 1
    assert travel_seconds(1.2, 121) == 101
    assert travel_seconds(7, 8) == 2
    assert delivery_seconds(6) == 602
    assert delivery_seconds(0) == 600
    assert delivery_fee_fen() == 500
    assert evaluate_travel_fare(0, 0, 7000) == 0
    assert evaluate_travel_fare(500, 200, 1) == 500
    assert evaluate_travel_fare(500, 200, 2501) == 501
    assert evaluate_purchase(2800, 3) == 8400


@pytest.mark.parametrize("weather,delay", [
    ("sunny", 0), ("cloudy", 0), ("light_rain", 149), ("heavy_rain", 360), ("snow", 560)])
def test_weather_lengthens_only_delivery_travel_and_rounds_only_the_final_delay(weather, delay):
    factor = WEATHER_FACTORS[weather]
    with localcontext(prec=1):
        assert delivery_weather_delay_seconds(4200, factor) == delay
        assert delivery_weather_delay_seconds(0, factor) == 0
    assert delivery_weather_delay_seconds(2100, WEATHER_FACTORS["heavy_rain"]) == 180
    assert delivery_weather_delay_seconds(1, WEATHER_FACTORS["heavy_rain"]) == 1
    assert delivery_weather_delay_seconds(4201, WEATHER_FACTORS["heavy_rain"]) == 361


@pytest.mark.parametrize("factor", [0, -1, 1.01, True, None])
def test_weather_delay_rejects_invalid_factor(factor, caplog):
    with pytest.raises(ValueError):
        delivery_weather_delay_seconds(4200, factor)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


@pytest.mark.parametrize("sample,quality", [(-2, "坏"), (2.499999, "坏"), (2.5, "中"),
                                           (3.499999, "中"), (3.5, "好"), (8, "好")])
def test_receipt_quality_grades_one_unclamped_normal_draw(sample, quality):
    stream = Mock()
    stream.gauss.return_value = sample
    assert goods_quality(4.25, stream) == quality
    stream.gauss.assert_called_once_with(4.25, 1)


def test_decimal_precision_does_not_change_settlement():
    """@brief 调用方低精度 Decimal 上下文不能改变金额、收货与时长。"""
    with localcontext(prec=2):
        assert evaluate_travel_fare(500, 200, 12346) == 2470
        assert evaluate_goods_receipt("meal", 1234567) == 740740200
        assert evaluate_goods_receipt("goods", 1234567) == 0.10
        assert travel_seconds(1.2, 12000.01) == 10001
        assert delivery_seconds(5000.01) == 1601


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "1"])
def test_invalid_distance_inputs_are_rejected_with_logs(value, caplog):
    """@brief 距离拒绝布尔、负数、非有限值和字符串并记录英文日志。"""
    with pytest.raises(ValueError):
        delivery_seconds(value)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


@pytest.mark.parametrize("quantity", [True, False, 0, -1, 1.5, float("nan"), float("inf"), "1", None])
def test_receipt_requires_positive_integer_quantity(quantity, caplog):
    """@brief 收货只接受正整数数量，没有份额或隐式换算。"""
    with pytest.raises(ValueError, match="quantity must be a positive integer"):
        evaluate_goods_receipt("meal", quantity)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


def test_invalid_modes_categories_and_purchase_inputs_are_rejected(caplog):
    """@brief 未登记类别、按名字写的出行方式以及非正整数购买被明确拒绝。"""
    with pytest.raises(ValueError, match="Goods category is outside the fixed set"):
        evaluate_goods_receipt("dessert", 1)
    for retired in ("walking", "driving", "flying"):
        with pytest.raises(ValueError, match="speed_meters_per_second must be a JSON number"):
            travel_seconds(retired, 10)
        with pytest.raises(ValueError, match="start_price_fen must be a non-negative integer"):
            evaluate_travel_fare(retired, 10, 10)
    for price, quantity in [(True, 1), (0, 1), (10, 0), (10, 1.5)]:
        with pytest.raises(ValueError):
            evaluate_purchase(price, quantity)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


def test_fixed_operators_keep_existing_decimal_settlement_results():
    """@brief 购买金额逐值保持精确结果；每公里 200 分、起步 500 分的车费按整数米向上取整到分。"""
    for price, count, expected in ((1, 1, 1), (800, 3, 2400), (123456789, 98765, 12193209765585)):
        assert evaluate_purchase(price, count) == expected
    for distance, expected in ((0, 500), (1, 500), (2499, 500), (2500, 500), (2501, 501), (12346, 2470)):
        assert evaluate_travel_fare(500, 200, distance) == expected
    assert not hasattr(operators, "evaluate_food_energy")
    assert not hasattr(operators, "_speed")
