"""@brief 使用固定规则和精确 Decimal 舍入的六个交换算子。"""

import math
from decimal import Decimal, MAX_EMAX, MIN_EMIN, ROUND_CEILING, localcontext
from typing import Any

from worldmock.base_models import require_finite_number
from worldmock.errors import reject_value
from worldmock.exchange.constants import (
    DELIVERY_FEE_FEN, DELIVERY_METERS_PER_SECOND, DELIVERY_PREP_SECONDS, DRIVING_FARE_FEN_PER_METER,
    DRIVING_FARE_MINIMUM_FEN, DRIVING_METERS_PER_SECOND, GOODS_ENERGY_KCAL, GOODS_RECEIPT_EFFECTS,
    WALKING_METERS_PER_SECOND,
)
from worldmock.exchange.types import GoodsCategory


def evaluate_purchase(unit_price_fen: int, quantity: int) -> int:
    """@brief 按商品单价和正整数数量计算购买分数。"""
    for label, value in (("unit_price_fen", unit_price_fen), ("quantity", quantity)):
        if type(value) is not int or value <= 0:
            reject_value(f"{label} must be a positive integer")
    return unit_price_fen * quantity


def evaluate_goods_receipt(category: GoodsCategory | str, quantity: int) -> int | float:
    """@brief 食品返回提供热量，非食品返回每条订单行效果，燃油为七个驾驶日。"""
    if type(quantity) is not int or quantity <= 0:
        reject_value("quantity must be a positive integer")
    try:
        category = GoodsCategory(category)
    except ValueError:
        reject_value("Goods category is outside the fixed set")
    if category in GOODS_ENERGY_KCAL:
        return GOODS_ENERGY_KCAL[category] * quantity
    return GOODS_RECEIPT_EFFECTS[category]


def evaluate_travel_fare(mode: str, distance_m: int | float) -> int:
    """@brief 步行免费，驾车按固定下限和米费率向上取整。"""
    _speed(mode)
    distance = _non_negative(distance_m, "distance_m")
    if mode == "walking":
        return 0
    amount = max(Decimal(DRIVING_FARE_MINIMUM_FEN), _multiply_exact((distance, DRIVING_FARE_FEN_PER_METER)))
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def travel_seconds(mode: str, distance_m: int | float) -> int:
    """@brief 按固定出行速度把图距离向上取整为秒。"""
    return _ceil_ratio(_non_negative(distance_m, "distance_m"), _speed(mode))


def delivery_seconds(distance_m: int | float) -> int:
    """@brief 配送时长等于准备时间加向上取整的图上行程。"""
    return DELIVERY_PREP_SECONDS + _ceil_ratio(
        _non_negative(distance_m, "distance_m"), Decimal(str(DELIVERY_METERS_PER_SECOND)))


def delivery_fee_fen() -> int:
    """@brief 返回每单固定配送费。"""
    return DELIVERY_FEE_FEN


def _non_negative(value: Any, label: str) -> Decimal:
    """@brief 拒绝非有限数值和负数并保留十进制原始精度。"""
    number = require_finite_number(value, label)
    if number < 0:
        reject_value(f"{label} must be non-negative")
    return Decimal(number) if isinstance(number, int) else Decimal(str(number))


def _multiply_exact(values: tuple[Decimal, ...]) -> Decimal:
    """@brief 隔离调用方上下文，以完整有效位数精确相乘。"""
    precision = sum(len(value.as_tuple().digits) for value in values)
    with localcontext(prec=precision, Emax=MAX_EMAX, Emin=MIN_EMIN, clamp=0):
        return math.prod(values, start=Decimal(1))


def _ceil_ratio(distance: Decimal, speed: Decimal) -> int:
    """@brief 整数化十进制分数后精确计算上取整，避免中间除法舍入。"""
    numerator, denominator = distance.as_integer_ratio()
    speed_numerator, speed_denominator = speed.as_integer_ratio()
    top, bottom = numerator * speed_denominator, denominator * speed_numerator
    return (top + bottom - 1) // bottom


def _speed(mode: str) -> Decimal:
    """@brief 只接受步行和驾车两种固定出行方式。"""
    if mode == "walking":
        return Decimal(str(WALKING_METERS_PER_SECOND))
    if mode == "driving":
        return Decimal(str(DRIVING_METERS_PER_SECOND))
    reject_value("Travel mode is outside the fixed set")
