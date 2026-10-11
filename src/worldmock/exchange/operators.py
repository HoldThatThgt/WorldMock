"""@brief 使用固定规则和精确 Decimal 舍入的六个交换算子。"""

import math
from decimal import Decimal, MAX_EMAX, MIN_EMIN, localcontext
from typing import Any

from worldmock.base_models import require_finite_number
from worldmock.errors import reject_value
from worldmock.exchange.constants import (
    DELIVERY_FEE_FEN, DELIVERY_METERS_PER_SECOND, DELIVERY_PREP_SECONDS, GOODS_ENERGY_KCAL,
    GOODS_RELAXATION_RELIEF,
)
from worldmock.exchange.types import GoodsCategory


def evaluate_purchase(unit_price_fen: int, quantity: int) -> int:
    """@brief 按商品单价和正整数数量计算购买分数。"""
    for label, value in (("unit_price_fen", unit_price_fen), ("quantity", quantity)):
        if type(value) is not int or value <= 0:
            reject_value(f"{label} must be a positive integer")
    return unit_price_fen * quantity


def evaluate_goods_receipt(category: GoodsCategory | str, quantity: int) -> int | float:
    """@brief 零食食品每份提供热量，饮料商品每次实际收货缓解放松需要。"""
    if type(quantity) is not int or quantity <= 0:
        reject_value("quantity must be a positive integer")
    if category not in {"snack", "meal", "drink", "goods"}:
        reject_value("Goods category is outside the fixed set")
    if category in GOODS_ENERGY_KCAL:
        return GOODS_ENERGY_KCAL[category] * quantity
    return GOODS_RELAXATION_RELIEF


def evaluate_travel_fare(start_price_fen: int, price_per_km_fen: int, distance_m: int) -> int:
    """@brief 车费取起步价与按公里计价（向上取整到分）的较大值，全程整数运算，与天气无关。

    :param start_price_fen: 出行方式的起步价，分。
    :param price_per_km_fen: 出行方式的每公里价格，分。
    :param distance_m: 最短路的整数米数。
    """
    amounts = (("start_price_fen", start_price_fen), ("price_per_km_fen", price_per_km_fen), ("distance_m", distance_m))
    for label, value in amounts:
        if type(value) is not int or value < 0:
            reject_value(f"{label} must be a non-negative integer")
    return max(start_price_fen, (price_per_km_fen * distance_m + 999) // 1000)


def travel_seconds(speed_meters_per_second: int | float, distance_m: int | float, weather_factor: float = 1.0) -> int:
    """@brief 任何出行方式都按它的基础速度乘受理时的天气系数计时，十进制精确相乘后向上取整。

    :param speed_meters_per_second: 出行方式的基础速度；整数原样取，小数取它的最短十进制写法。
    """
    factor = _non_negative(weather_factor, "weather_factor")
    if factor <= 0:
        reject_value("weather_factor must be positive")
    speed = _non_negative(speed_meters_per_second, "speed_meters_per_second")
    if speed <= 0:
        reject_value("speed_meters_per_second must be positive")
    return _ceil_ratio(_non_negative(distance_m, "distance_m"), _multiply_exact((speed, factor)))


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
