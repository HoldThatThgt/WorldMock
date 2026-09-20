"""@brief typed 闭集规则、舍入和库存配方反例。"""

from __future__ import annotations

from dataclasses import replace
from decimal import Context, FloatOperation, Inexact, ROUND_UP, Rounded, localcontext
from fractions import Fraction
import json
import math
from pathlib import Path

import pytest

from worldmock.categories import (
    CategoryError,
    InventoryTransformRule,
    TravelFareRule,
    evaluate_food_energy,
    evaluate_hourly_pay,
    evaluate_package_pay,
    evaluate_piece_pay,
    evaluate_purchase,
    evaluate_travel_fare,
    load_category_contract,
    load_exchange_rules,
    refresh_manifest_identity,
    scale_inventory_recipe,
    validate_rule_resource_domains,
)
from worldmock.catalog import load_catalog
from worldmock.catalog.models import ResourceDefinition, RuleEdge

from .conftest import contract_document, raw_object, review, rules_document, source, write_contract, write_rules


def test_rule_action_and_target_must_match_pairing(tmp_path: Path) -> None:
    """defined 配对不能引用另一目标或动作的规则。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    document = rules_document()
    document["rules"][0]["category_id"] = "food"
    with pytest.raises(CategoryError) as excinfo:
        load_exchange_rules(write_rules(tmp_path, document), contract)
    assert excinfo.value.code == "PAIRING_RULE_MISMATCH"


def test_operator_action_must_match_even_when_its_pairing_agrees(tmp_path: Path) -> None:
    """@brief 规则和配对同时改动作也不能把购买转账算子解释为使用动作。"""
    contract_payload = contract_document()
    contract_payload["pairings"][0]["action"] = "consume:use"
    contract = load_category_contract(write_contract(tmp_path, contract_payload))
    document = rules_document()
    document["rules"][0]["action"] = "consume:use"
    with pytest.raises(CategoryError) as excinfo:
        load_exchange_rules(write_rules(tmp_path, document), contract)
    assert excinfo.value.code == "EXCHANGE_RULE_ACTION_MISMATCH"


@pytest.mark.parametrize("reviewer_type", ["human", "delegated_agent"])
def test_rule_review_requires_authorization(tmp_path: Path, reviewer_type: str) -> None:
    """@brief 规则发布中的人工和委托审核同样不能省略授权。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    document = rules_document()
    document["review"]["reviewer_type"] = reviewer_type
    document["review"].pop("authorization")
    with pytest.raises(CategoryError) as excinfo:
        load_exchange_rules(write_rules(tmp_path, document), contract)
    assert excinfo.value.code == "EXCHANGE_RULES_SCHEMA_INVALID"


def test_unknown_operator_and_formula_string_rejected(tmp_path: Path) -> None:
    """未知算子和任意公式字段均不能进入规则模型。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    unknown = rules_document()
    unknown["rules"][0]["operator"] = "python_eval"
    with pytest.raises(CategoryError) as operator:
        load_exchange_rules(write_rules(tmp_path, unknown), contract)
    assert operator.value.code == "EXCHANGE_RULES_SCHEMA_INVALID"
    formula = rules_document()
    formula["rules"][0]["formula"] = "price * coefficient"
    with pytest.raises(CategoryError) as expression:
        load_exchange_rules(write_rules(tmp_path, formula), contract)
    assert expression.value.code == "EXCHANGE_RULES_SCHEMA_INVALID"


def test_exchange_rules_require_matching_contract_version_and_known_identity(tmp_path: Path) -> None:
    """规则文档版本必须与契约一致，未知规则查询必须失败。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    mismatched = rules_document()
    mismatched["contract_version"] = 2
    with pytest.raises(CategoryError) as version:
        load_exchange_rules(write_rules(tmp_path, mismatched), contract)
    assert version.value.code == "EXCHANGE_CONTRACT_VERSION_MISMATCH"
    rules = load_exchange_rules(write_rules(tmp_path, rules_document()), contract)
    with pytest.raises(CategoryError) as unknown:
        rules.require("unknown")
    assert unknown.value.code == "EXCHANGE_RULE_UNKNOWN"


def test_closed_inventory_requires_equal_quantities() -> None:
    """封闭库存域不允许转换凭空增加件数。"""
    payload = {
        "rule_id": "unseal",
        "category_id": "product",
        "action": "consume:use",
        "operator": "inventory_transform",
        "resource_id": "inventory_closed",
        "unit": "item",
        "conservation": "closed",
        "input_category_id": "product",
        "output_category_id": "product",
        "input_quantity": 1,
        "output_quantity": 2,
        "inventory_unit": "item",
    }
    with pytest.raises(Exception):
        InventoryTransformRule.model_validate(payload)


def test_inventory_transform_unit_must_match_resource_unit() -> None:
    """配方的对象库存单位必须与资源域单位一致。"""
    payload = {
        "rule_id": "unseal",
        "category_id": "product",
        "action": "consume:use",
        "operator": "inventory_transform",
        "resource_id": "inventory_closed",
        "unit": "item",
        "conservation": "closed",
        "input_category_id": "product",
        "output_category_id": "product",
        "input_quantity": 1,
        "output_quantity": 1,
        "inventory_unit": "box",
    }
    with pytest.raises(Exception):
        InventoryTransformRule.model_validate(payload)


def test_every_operator_rejects_the_wrong_resource_shape() -> None:
    """每个闭集算子都要求其实际资源形态，不能只比较单位和守恒字符串。"""
    catalog = load_catalog(Path(__file__).resolve().parents[2] / "catalog")
    rules = list(catalog.exchange_rules.rules.values())
    rules.append(InventoryTransformRule.model_validate({
        "rule_id": "unseal",
        "category_id": "durable",
        "action": "consume:use",
        "operator": "inventory_transform",
        "resource_id": "inventory_closed",
        "unit": "item",
        "conservation": "closed",
        "input_category_id": "durable",
        "output_category_id": "durable",
        "input_quantity": 1,
        "output_quantity": 1,
        "inventory_unit": "item",
    }))
    seen = set()
    for rule in rules:
        seen.add(rule.operator.value)
        wrong_kind = "account" if rule.operator.value in {
            "food_energy_source", "durable_use", "inventory_transform",
        } else "level"
        resource = _resource_for_rule(rule, wrong_kind)
        rule_set = replace(catalog.exchange_rules, rules={rule.rule_id: rule})
        with pytest.raises(CategoryError) as excinfo:
            validate_rule_resource_domains(rule_set, {rule.resource_id: resource})
        assert excinfo.value.code == "EXCHANGE_RESOURCE_DOMAIN_MISMATCH"
    assert seen == {
        "purchase_transfer", "food_energy_source", "durable_use", "travel_fare",
        "work_hourly", "work_piece", "work_package", "inventory_transform",
    }


@pytest.mark.parametrize(
    ("rule_id", "wrong_id"),
    [("purchase-product", "other_money"), ("consume-food", "other_energy")],
)
def test_fixed_runtime_resources_reject_same_domain_aliases(rule_id: str, wrong_id: str) -> None:
    """货币与身体能量算子不能静默绑定同形但运行引擎不读取的另一资源。"""
    catalog = load_catalog(Path(__file__).resolve().parents[2] / "catalog")
    rule = catalog.exchange_rules.require(rule_id).model_copy(update={"resource_id": wrong_id})
    kind = "account" if rule_id == "purchase-product" else "level"
    resource = _resource_for_rule(rule, kind)
    rule_set = replace(catalog.exchange_rules, rules={rule.rule_id: rule})
    with pytest.raises(CategoryError) as excinfo:
        validate_rule_resource_domains(rule_set, {wrong_id: resource})
    assert excinfo.value.code == "EXCHANGE_RESOURCE_DOMAIN_MISMATCH"


@pytest.mark.parametrize(
    "operator", ["purchase_transfer", "travel_fare", "work_hourly", "work_piece", "work_package"],
)
def test_money_operators_require_a_closed_domain_even_when_resource_agrees(operator: str) -> None:
    """@brief 货币算子与资源共同声明开放域时仍不能产生或销毁资金。"""
    catalog = load_catalog(Path(__file__).resolve().parents[2] / "catalog")
    original = next(rule for rule in catalog.exchange_rules.rules.values() if rule.operator.value == operator)
    payload = original.model_dump(mode="json")
    payload["conservation"] = "open"
    rule = type(original).model_validate(payload)
    resource = _resource_for_rule(rule, "account")
    rule_set = replace(catalog.exchange_rules, rules={rule.rule_id: rule})
    with pytest.raises(CategoryError) as excinfo:
        validate_rule_resource_domains(rule_set, {rule.resource_id: resource})
    assert excinfo.value.code == "EXCHANGE_RESOURCE_DOMAIN_MISMATCH"


def _resource_for_rule(rule, kind: str) -> ResourceDefinition:
    """@brief 为算子构造单位与守恒完全一致、但形态错误的资源。"""
    payload = {
        "resource_id": rule.resource_id,
        "kind": kind,
        "unit": rule.unit,
        "owner": "test",
        "conservation": rule.conservation.value,
        "provenance": [{"part": "definition", "type": "scenario", "source": "fixture"}],
        "version": 1,
    }
    if kind == "level":
        payload["bounds"] = {"min": 0, "max": None}
    return ResourceDefinition.model_validate(payload)


def test_money_and_energy_rounding_operators() -> None:
    """购买向上、合同向下，食品能量保持有限值。"""
    assert evaluate_purchase(101, 2, 1.5) == 303
    assert evaluate_purchase(101, 1, 0.5) == 51
    assert evaluate_hourly_pay(100, 1801, 3600, 1) == 50
    assert evaluate_hourly_pay(100, 7200, 3600, 1) == 100
    assert evaluate_piece_pay(101, 3, 0.5) == 151
    assert evaluate_package_pay(101, True, 0.5) == 50
    assert evaluate_package_pay(101, False, 2) == 0
    assert evaluate_food_energy(185, 0.5) == 92.5


def test_unit_multipliers_preserve_large_integer_amounts_in_default_decimal_context() -> None:
    """@brief 默认二十八位精度下，单位系数不能丢失金额或库存数量的最低位。"""
    amount = 10**28 + 1
    with localcontext(Context(prec=28)):
        assert evaluate_purchase(amount, 1, 1) == amount
        assert evaluate_hourly_pay(amount, 3600, 3600, 1) == amount
        assert evaluate_piece_pay(amount, 1, 1) == amount
        assert evaluate_package_pay(amount, True, 1) == amount
        assert evaluate_travel_fare(_travel_rule(amount, 0.2), 1, 1) == amount
        delta = scale_inventory_recipe(_inventory_rule(amount, amount), 1, 1)
        assert delta.input_quantity == delta.output_quantity == amount


def test_integer_coefficients_do_not_acquire_a_decimal_string_conversion_limit() -> None:
    """@brief 算子已接受的整数系数不应因转为十进制字符串而被额外限制。"""
    coefficient = 10**5000 + 1
    assert evaluate_purchase(1, 1, coefficient) == coefficient
    assert evaluate_hourly_pay(1, 3600, 3600, coefficient) == coefficient
    assert evaluate_piece_pay(1, 1, coefficient) == coefficient
    assert evaluate_package_pay(1, True, coefficient) == coefficient
    assert evaluate_travel_fare(_travel_rule(1, 0), 1, coefficient) == coefficient
    delta = scale_inventory_recipe(_inventory_rule(1, 1), 1, coefficient)
    assert delta.input_quantity == delta.output_quantity == coefficient


def test_purchase_quantity_and_package_acceptance_are_strict() -> None:
    """购买份数必须是正整数，总包验收必须是布尔值。"""
    with pytest.raises(CategoryError) as quantity:
        evaluate_purchase(1, 0, 1)
    assert quantity.value.code == "EXCHANGE_INPUT_INVALID"
    with pytest.raises(CategoryError) as accepted:
        evaluate_package_pay(1, 1, 1)
    assert accepted.value.code == "EXCHANGE_INPUT_INVALID"


@pytest.mark.parametrize(
    "evaluator",
    [
        lambda amount: evaluate_purchase(amount, 1, 1),
        lambda amount: evaluate_hourly_pay(amount, 1, 1, 1),
        lambda amount: evaluate_piece_pay(amount, 1, 1),
        lambda amount: evaluate_package_pay(amount, True, 1),
    ],
)
@pytest.mark.parametrize("amount", [True, 3.5])
def test_money_operators_require_integer_fen(evaluator, amount) -> None:
    """所有货币算子都拒绝布尔和小数金额冒充整数分。"""
    with pytest.raises(CategoryError) as excinfo:
        evaluator(amount)
    assert excinfo.value.code == "EXCHANGE_INPUT_INVALID"


def test_food_energy_rejects_float_overflow() -> None:
    """有限输入乘法转为浮点结果后仍必须有限。"""
    with pytest.raises(CategoryError) as excinfo:
        evaluate_food_energy(1e308, 1e308)
    assert excinfo.value.code == "EXCHANGE_RESULT_INVALID"


def test_piece_count_is_required() -> None:
    """按件计酬缺少 oracle 实际件数时明确拒绝。"""
    with pytest.raises(CategoryError) as excinfo:
        evaluate_piece_pay(100, None, 1)
    assert excinfo.value.code == "EXCHANGE_PIECE_COUNT_MISSING"


def test_travel_uses_raw_distance_and_rounds_final_fen() -> None:
    """出行费率直接使用有限小数距离，只对最终分值向上取整。"""
    rule = TravelFareRule.model_validate({
        "rule_id": "drive",
        "category_id": "product",
        "action": "travel:driving",
        "operator": "travel_fare",
        "resource_id": "money_fen",
        "unit": "fen",
        "conservation": "closed",
        "minimum_fen": 0,
        "fen_per_meter": 0.2,
    })
    assert evaluate_travel_fare(rule, 2.1, 1) == 1
    assert evaluate_travel_fare(rule, 2500.1, 1) == 501


def test_travel_applies_minimum_before_coefficient_and_final_rounding() -> None:
    """@brief 低于最低费用时先采用最低金额，再按配对系数和分值向上取整。"""
    rule = _travel_rule(101, 0.2)
    assert evaluate_travel_fare(rule, 1, 1) == 101
    assert evaluate_travel_fare(rule, 1, 0.5) == 51
    assert evaluate_travel_fare(rule, 1000, 0.5) == 100


@pytest.mark.parametrize(
    ("evaluator", "multiplier", "round_amount"),
    [
        pytest.param(lambda amount, factor: evaluate_purchase(amount, 3, factor), Fraction(3), math.ceil, id="purchase"),
        pytest.param(
            lambda amount, factor: evaluate_hourly_pay(amount, 7201, 3601, factor),
            Fraction(3601, 3600), math.floor, id="hourly",
        ),
        pytest.param(lambda amount, factor: evaluate_piece_pay(amount, 3, factor), Fraction(3), math.floor, id="piece"),
        pytest.param(lambda amount, factor: evaluate_package_pay(amount, True, factor), Fraction(1), math.floor, id="package"),
        pytest.param(
            lambda amount, factor: evaluate_travel_fare(_travel_rule(amount, 0.2), 1, factor),
            Fraction(1), math.ceil, id="travel-minimum",
        ),
        pytest.param(
            lambda amount, factor: evaluate_travel_fare(_travel_rule(0, amount), 1.25, factor),
            Fraction(5, 4), math.ceil, id="travel-rate",
        ),
    ],
)
@pytest.mark.parametrize(
    ("amount", "coefficient"),
    [
        (10**28 + 1, 1), (10**80 + 1, 0.5), (10**40 - 1, 0.1),
        (10**80 + 1, 1e-20), (10**28 + 1, 1e100), (3600 * 10**80 - 1, 1), (101, 0.5),
    ],
)
@pytest.mark.parametrize("precision", [3, 28, 80])
def test_money_operators_preserve_exact_amounts_independently_of_decimal_context(
    evaluator, multiplier: Fraction, round_amount, amount: int, coefficient: int | float, precision: int,
) -> None:
    """@brief 金额依据精确有理数取整，大整数与分数均不受调用方 Decimal 设置影响。"""
    expected = round_amount(Fraction(amount) * Fraction(str(coefficient)) * multiplier)
    with localcontext() as ambient:
        _restrict_decimal_context(ambient, precision)
        original_context = repr(ambient)
        assert evaluator(amount, coefficient) == expected
        assert repr(ambient) == original_context


@pytest.mark.parametrize("price", [10**28 + 1, 10**80 + 1])
@pytest.mark.parametrize("coefficient", [1, 0.5, 0.1])
def test_catalog_preserves_purchase_coefficient_for_exact_dynamic_quote_amount(
    valid_catalog_dir: Path, price: int, coefficient: int | float,
) -> None:
    """@brief 完整目录加载保留精确系数，供给大整数报价交给同一购买算子。"""
    declaration = raw_object()
    declaration["values"]["absolute"]["price_fen"] = price
    (valid_catalog_dir / "objects.jsonl").write_text(json.dumps(declaration) + "\n", encoding="utf-8")
    document = contract_document()
    document["pairings"][0]["coefficient"] = coefficient
    write_contract(valid_catalog_dir, document)
    refresh_manifest_identity(valid_catalog_dir)
    with localcontext() as ambient:
        _restrict_decimal_context(ambient, 3)
        original_context = repr(ambient)
        catalog = load_catalog(valid_catalog_dir)
        assert repr(ambient) == original_context
    obj = catalog.objects["milk"]
    purchase = next(edge for edge in obj.edges if isinstance(edge, RuleEdge))
    assert purchase.coefficient == coefficient
    assert evaluate_purchase(price, 3, purchase.coefficient) == math.ceil(
        Fraction(price) * 3 * Fraction(str(coefficient)))


@pytest.mark.parametrize(
    ("input_quantity", "output_quantity", "servings", "coefficient"),
    [
        (10**28 + 1, 10**28 + 1, 1, 1),
        (10**80 + 1, 10**80 + 1, 0.25, 0.1),
        (0.1, 0.3, 10**28 + 1, 0.2),
        (0.1, 0.2, 0.3, 1e100),
        (1, 2, 0.1, 10**28 + 1),
    ],
)
@pytest.mark.parametrize("precision", [3, 28, 80])
def test_recipe_scaling_preserves_exact_quantities_independently_of_decimal_context(
    input_quantity, output_quantity, servings, coefficient, precision: int,
) -> None:
    """@brief 配方两侧精确同比缩放，大整数和小数不受环境精度或陷阱影响。"""
    rule = _inventory_rule(input_quantity, output_quantity)
    multiplier = Fraction(str(servings)) * Fraction(str(coefficient))
    expected_input = Fraction(str(input_quantity)) * multiplier
    expected_output = Fraction(str(output_quantity)) * multiplier
    with localcontext() as ambient:
        _restrict_decimal_context(ambient, precision)
        original_context = repr(ambient)
        delta = scale_inventory_recipe(rule, servings, coefficient)
        assert Fraction(delta.input_quantity) == expected_input
        assert Fraction(delta.output_quantity) == expected_output
        assert repr(ambient) == original_context


def _restrict_decimal_context(context, precision: int) -> None:
    """@brief 设置会暴露环境耦合的精度、舍入、指数界限、陷阱和既有标志。"""
    context.prec = precision
    context.rounding = ROUND_UP
    context.Emax = 9
    context.Emin = -9
    context.clamp = 1
    context.traps[Inexact] = True
    context.traps[Rounded] = True
    context.flags[FloatOperation] = True


def _travel_rule(minimum_fen: int, fen_per_meter: int | float) -> TravelFareRule:
    """@brief 构造最低金额与每米费率均显式声明的出行规则。"""
    return TravelFareRule.model_validate({
        "rule_id": "drive", "category_id": "product", "action": "travel:driving",
        "operator": "travel_fare", "resource_id": "money_fen", "unit": "fen", "conservation": "closed",
        "minimum_fen": minimum_fen, "fen_per_meter": fen_per_meter,
    })


def _inventory_rule(input_quantity: int | float, output_quantity: int | float) -> InventoryTransformRule:
    """@brief 数量相等时声明封闭域，其余合法转换显式声明开放域。"""
    return InventoryTransformRule.model_validate({
        "rule_id": "unseal", "category_id": "product", "action": "consume:use",
        "operator": "inventory_transform", "resource_id": "inventory", "unit": "item",
        "conservation": "closed" if input_quantity == output_quantity else "open",
        "input_category_id": "product", "output_category_id": "product",
        "input_quantity": input_quantity, "output_quantity": output_quantity, "inventory_unit": "item",
    })


def test_recipe_scales_both_sides_once() -> None:
    """库存配方按份数同比缩放正输入和正输出。"""
    rule = InventoryTransformRule.model_validate({
        "rule_id": "unseal",
        "category_id": "product",
        "action": "consume:use",
        "operator": "inventory_transform",
        "resource_id": "inventory_closed",
        "unit": "item",
        "conservation": "closed",
        "input_category_id": "product",
        "output_category_id": "product",
        "input_quantity": 1,
        "output_quantity": 1,
        "inventory_unit": "item",
    })
    delta = scale_inventory_recipe(rule, 3, 0.5)
    assert delta.input_quantity == 1.5
    assert delta.output_quantity == 1.5
    fractional = scale_inventory_recipe(rule, 0.5, 2)
    assert fractional.input_quantity == fractional.output_quantity == 1
    with pytest.raises(CategoryError):
        scale_inventory_recipe(rule, 3, 0)


@pytest.mark.parametrize("field", ["input_quantity", "output_quantity"])
@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf")])
def test_recipe_quantities_reject_non_json_numbers(field: str, value) -> None:
    """配方数量在 Pydantic 转换前拒绝布尔、字符串与非有限数。"""
    payload = {
        "rule_id": "unseal",
        "category_id": "product",
        "action": "consume:use",
        "operator": "inventory_transform",
        "resource_id": "inventory_closed",
        "unit": "item",
        "conservation": "closed",
        "input_category_id": "product",
        "output_category_id": "product",
        "input_quantity": 1,
        "output_quantity": 1,
        "inventory_unit": "item",
    }
    payload[field] = value
    with pytest.raises(Exception):
        InventoryTransformRule.model_validate(payload)


@pytest.mark.parametrize("field", ["minimum_fen", "fen_per_meter"])
@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf")])
def test_travel_parameters_reject_non_json_numbers(field: str, value) -> None:
    """出行费率拒绝隐式数字转换与非有限数。"""
    payload = {
        "rule_id": "drive",
        "category_id": "product",
        "action": "travel:driving",
        "operator": "travel_fare",
        "resource_id": "money_fen",
        "unit": "fen",
        "conservation": "closed",
        "minimum_fen": 0,
        "fen_per_meter": 0.2,
    }
    payload[field] = value
    with pytest.raises(Exception):
        TravelFareRule.model_validate(payload)
