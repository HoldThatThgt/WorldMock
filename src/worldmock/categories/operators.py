"""@brief 不执行任意表达式的闭集交换算子求值。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, MAX_EMAX, MIN_EMIN, ROUND_CEILING, ROUND_FLOOR, localcontext
import math
from typing import Any

from worldmock.categories.common import CategoryError
from worldmock.base_models import require_finite_number
from worldmock.exchange.operators import _multiply_exact
from worldmock.categories.rules import InventoryTransformRule, TravelFareRule


@dataclass(frozen=True)
class InventoryDelta:
    """@brief 一次库存配方按请求份数缩放后的正输入、正输出数量。"""

    input_category_id: str
    output_category_id: str
    input_quantity: Decimal
    output_quantity: Decimal
    inventory_unit: str


def evaluate_purchase(
    unit_price_fen: int,
    quantity: int,
    coefficient: int | float,
) -> int:
    """@brief 计算购买整单金额并向上取整分。"""
    price = _integer_non_negative(unit_price_fen, "unit_price_fen")
    count = _integer_positive(quantity, "quantity")
    factor = _decimal_non_negative(coefficient, "coefficient")
    amount = _multiply_exact((Decimal(price), count, factor))
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def evaluate_food_energy(
    energy_kcal: int | float,
    coefficient: int | float,
) -> float:
    """@brief 计算进食动作进入身体结算的有限能量。"""
    energy = _decimal_non_negative(energy_kcal, "energy_kcal")
    factor = _decimal_non_negative(coefficient, "coefficient")
    result = float(_multiply_exact((energy, factor)))
    if not math.isfinite(result):
        raise CategoryError("EXCHANGE_RESULT_INVALID", "food energy result must be finite")
    return result


def evaluate_travel_fare(
    rule: TravelFareRule,
    distance_meters: int | float,
    coefficient: int | float,
) -> int:
    """@brief 按原始有限距离计算费率并对最终金额向上取整分。"""
    distance = _decimal_non_negative(distance_meters, "distance_meters")
    factor = _decimal_non_negative(coefficient, "coefficient")
    rate_amount = _multiply_exact((distance, _decimal_non_negative(rule.fen_per_meter, "fen_per_meter")))
    amount = _multiply_exact((max(Decimal(rule.minimum_fen), rate_amount), factor))
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def evaluate_hourly_pay(
    hourly_rate_fen: int,
    actual_seconds: int,
    maximum_duration_seconds: int,
    coefficient: int | float,
) -> int:
    """@brief 以实际秒数和合同上限计算时薪并向下取整分。"""
    rate = _integer_non_negative(hourly_rate_fen, "hourly_rate_fen")
    actual = _integer_non_negative(actual_seconds, "actual_seconds")
    maximum = _integer_non_negative(maximum_duration_seconds, "maximum_duration_seconds")
    factor = _decimal_non_negative(coefficient, "coefficient")
    amount = _multiply_exact((Decimal(rate), Decimal(min(actual, maximum)), factor))
    with localcontext(prec=max(1, amount.adjusted() + 1), Emax=MAX_EMAX, Emin=MIN_EMIN, clamp=0):
        # 非负金额整除直接得到下整分，避免循环小数的中间舍入跨过整数边界。
        return int(amount // Decimal(3600))


def evaluate_piece_pay(
    rate_fen: int,
    accepted_pieces: int | None,
    coefficient: int | float,
) -> int:
    """@brief 按验收 oracle 的实际件数计算金额，缺件数时拒绝。"""
    rate = _integer_non_negative(rate_fen, "rate_fen")
    if accepted_pieces is None:
        raise CategoryError("EXCHANGE_PIECE_COUNT_MISSING", "accepted piece count is required")
    pieces = _integer_non_negative(accepted_pieces, "accepted_pieces")
    factor = _decimal_non_negative(coefficient, "coefficient")
    amount = _multiply_exact((Decimal(rate), Decimal(pieces), factor))
    return int(amount.to_integral_value(rounding=ROUND_FLOOR))


def evaluate_package_pay(
    amount_fen: int,
    accepted: bool,
    coefficient: int | float,
) -> int:
    """@brief 仅在总包验收通过时支付向下取整后的合同金额。"""
    package_amount = _integer_non_negative(amount_fen, "amount_fen")
    if not isinstance(accepted, bool):
        raise CategoryError("EXCHANGE_INPUT_INVALID", "package acceptance must be boolean")
    if not accepted:
        return 0
    factor = _decimal_non_negative(coefficient, "coefficient")
    amount = _multiply_exact((Decimal(package_amount), factor))
    return int(amount.to_integral_value(rounding=ROUND_FLOOR))


def scale_inventory_recipe(
    rule: InventoryTransformRule,
    servings: int | float,
    coefficient: int | float,
) -> InventoryDelta:
    """@brief 按请求份数和配对系数同比缩放库存配方双侧。"""
    count = _decimal_positive(servings, "servings")
    factor = _decimal_non_negative(coefficient, "coefficient")
    if factor == 0:
        raise CategoryError("EXCHANGE_INPUT_INVALID", "inventory recipe coefficient must be positive")
    return InventoryDelta(
        input_category_id=rule.input_category_id,
        output_category_id=rule.output_category_id,
        input_quantity=_multiply_exact((_decimal_positive(rule.input_quantity, "input_quantity"), count, factor)),
        output_quantity=_multiply_exact((_decimal_positive(rule.output_quantity, "output_quantity"), count, factor)),
        inventory_unit=rule.inventory_unit,
    )




def _decimal_non_negative(value: Any, label: str) -> Decimal:
    """@brief 把非布尔有限非负数转换为 Decimal。"""
    number = require_finite_number(value, label)
    decimal = Decimal(number) if isinstance(number, int) else Decimal(str(number))
    if decimal < 0:
        raise CategoryError("EXCHANGE_INPUT_INVALID", f"{label} must be non-negative")
    return decimal


def _decimal_positive(value: Any, label: str) -> Decimal:
    """@brief 把非布尔有限正数转换为 Decimal。"""
    decimal = _decimal_non_negative(value, label)
    if decimal == 0:
        raise CategoryError("EXCHANGE_INPUT_INVALID", f"{label} must be positive")
    return decimal


def _integer_non_negative(value: Any, label: str) -> int:
    """@brief 要求一个输入为非负整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CategoryError("EXCHANGE_INPUT_INVALID", f"{label} must be a non-negative integer")
    return value


def _integer_positive(value: Any, label: str) -> Decimal:
    """@brief 要求一个输入为正整数并转换为 Decimal。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CategoryError("EXCHANGE_INPUT_INVALID", f"{label} must be a positive integer")
    return Decimal(value)
