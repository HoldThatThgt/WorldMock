"""@brief 固定交换算子的收货、金额与时长边界证明。"""

from decimal import localcontext

import pytest

from worldmock.exchange import operators
from worldmock.exchange.operators import (
    delivery_fee_fen, delivery_seconds, evaluate_goods_receipt, evaluate_purchase, evaluate_travel_fare,
    travel_seconds,
)


@pytest.mark.parametrize("category,quantity,expected", [
    ("meal", 1, 600), ("meal", 3, 1800), ("snack", 2, 400), ("drink", 4, 400),
    ("leisure", 1, 0.10), ("daily", 1, 0.05), ("leisure", 5, 0.10), ("daily", 5, 0.05),
])
def test_goods_receipt_has_quantity_only_for_food(category, quantity, expected):
    """@brief 食品热量乘数量，非食品每条订单行只结算一次固定效果。"""
    assert evaluate_goods_receipt(category, quantity) == expected


def test_travel_and_delivery_round_up_with_fare_minimum():
    """@brief 非整除距离上取整，步行免费，驾车有固定最低车费。"""
    assert travel_seconds("walking", 1) == 1
    assert travel_seconds("walking", 121) == 101
    assert travel_seconds("driving", 8) == 2
    assert delivery_seconds(6) == 602
    assert delivery_seconds(0) == 600
    assert delivery_fee_fen() == 500
    assert evaluate_travel_fare("walking", 7000) == 0
    assert evaluate_travel_fare("driving", 1) == 500
    assert evaluate_travel_fare("driving", 2501) == 501
    assert evaluate_purchase(2800, 3) == 8400


def test_decimal_precision_does_not_change_settlement():
    """@brief 调用方低精度 Decimal 上下文不能改变金额、收货与时长。"""
    with localcontext(prec=2):
        assert evaluate_travel_fare("driving", 12345.67) == 2470
        assert evaluate_goods_receipt("meal", 1234567) == 740740200
        assert evaluate_goods_receipt("daily", 1234567) == 0.05
        assert travel_seconds("walking", 12000.01) == 10001
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
    """@brief 未登记类别和方式以及非正整数购买被明确拒绝。"""
    with pytest.raises(ValueError, match="Goods category is outside the fixed set"):
        evaluate_goods_receipt("dessert", 1)
    with pytest.raises(ValueError):
        travel_seconds("flying", 10)
    for price, quantity in [(True, 1), (0, 1), (10, 0), (10, 1.5)]:
        with pytest.raises(ValueError):
            evaluate_purchase(price, quantity)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


def test_fixed_operators_keep_existing_decimal_settlement_results():
    """@brief 购买与驾车金额逐值保持原有精确舍入结果。"""
    for price, count, expected in ((1, 1, 1), (800, 3, 2400), (123456789, 98765, 12193209765585)):
        assert evaluate_purchase(price, count) == expected
    for distance, expected in ((0, 500), (1, 500), (2499.9, 500), (2500, 500),
                               (2500.01, 501), (12345.67, 2470)):
        assert evaluate_travel_fare("driving", distance) == expected
    assert not hasattr(operators, "evaluate_food_energy")
