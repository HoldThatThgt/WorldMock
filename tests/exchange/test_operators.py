"""@brief 固定算子的真实金额、能量与时长边界证明。"""

from decimal import localcontext

import pytest

from worldmock.exchange.operators import (
    delivery_fee_fen, delivery_seconds, evaluate_food_energy, evaluate_purchase, evaluate_travel_fare, travel_seconds,
)


@pytest.mark.parametrize("category,fraction,expected", [
    ("snack", 0.5, 100), ("drink", 0.75, 75), ("meal", 0.25, 150), ("meal", 0, 0), ("meal", 1, 600),
])
def test_food_energy_uses_category_and_fraction(category, fraction, expected):
    """@brief 三类食品和食用比例分别决定能量，不读环境热量字段。"""
    assert evaluate_food_energy(category, fraction) == expected


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
    """@brief 调用方低精度 Decimal 上下文不能改变分数或比例能量。"""
    with localcontext(prec=2):
        assert evaluate_travel_fare("driving", 12345.67) == 2470
        assert evaluate_food_energy("drink", 0.1234567) == 12.34567
        assert travel_seconds("walking", 12000.01) == 10001
        assert delivery_seconds(5000.01) == 1601


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "1"])
def test_invalid_numeric_inputs_are_rejected(value):
    """@brief 数字边界拒绝布尔、负数、非有限值和字符串。"""
    with pytest.raises(ValueError):
        delivery_seconds(value)
    with pytest.raises(ValueError):
        evaluate_food_energy("meal", value)


def test_invalid_modes_categories_and_purchase_inputs_are_rejected():
    """@brief 算子拒绝未登记的类别、方式和非正整份购买。"""
    with pytest.raises(ValueError):
        evaluate_food_energy("dessert", 1)
    with pytest.raises(ValueError):
        travel_seconds("flying", 10)
    for price, quantity in [(True, 1), (0, 1), (10, 0), (10, 1.5)]:
        with pytest.raises(ValueError):
            evaluate_purchase(price, quantity)


def test_fixed_operators_keep_existing_decimal_settlement_results():
    """@brief 品类模块退役后用已核对的固定结果守住金额舍入与部分食品摄入。"""
    for price, count, expected in ((1, 1, 1), (800, 3, 2400), (123456789, 98765, 12193209765585)):
        assert evaluate_purchase(price, count) == expected
    for distance, expected in ((0, 500), (1, 500), (2499.9, 500), (2500, 500),
                               (2500.01, 501), (12345.67, 2470)):
        assert evaluate_travel_fare("driving", distance) == expected
    for fraction, expected in ((0, 0), (0.1, 60), (0.333333, 199.9998), (1, 600)):
        assert evaluate_food_energy("meal", fraction) == expected
