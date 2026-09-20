"""@brief 原始对象增量到现有 ExchangeObject 有效视图的编译。"""

from __future__ import annotations

from typing import Any, Mapping

from worldmock.categories import (
    Action,
    CategoryContract,
    CategoryKind,
    DurableUseRule,
    ExchangeRuleSet,
    FoodEnergyRule,
    InventoryTransformRule,
    PairingState,
    PurchaseTransferRule,
    ResolvedObject,
    TravelFareRule,
    WorkHourlyRule,
    WorkPackageRule,
    WorkPieceRule,
    evaluate_food_energy,
    resolve_object_values,
    resolve_pairing,
)
from worldmock.catalog.models import (
    Acceptance,
    CatalogError,
    CompiledEdge,
    Efficiency,
    ExchangeObject,
    ObjectDeclaration,
    ObjectKind,
    RuleEdge,
    Serving,
    SourceEdge,
)


def compile_exchange_object(
    declaration: ObjectDeclaration,
    contract: CategoryContract,
    rules: ExchangeRuleSet,
) -> ExchangeObject:
    """@brief 解析对象增量并编译运行期字段与 typed edges。

    :param declaration: 严格 raw 对象声明。
    :param contract: 冻结品类契约。
    :param rules: 与契约绑定的闭集规则。
    :returns: 现有运行模块可消费的 ExchangeObject 有效视图。
    :raises CatalogError: 规则所需槽位缺失或类型错误。
    """
    resolved = resolve_object_values(declaration, contract)
    edges = _compile_edges(resolved, contract, rules)
    attributes = resolved.attributes
    return ExchangeObject(
        object_id=resolved.object_id,
        name=resolved.name,
        category_id=resolved.category_id,
        object_kind=ObjectKind(resolved.kind.value),
        attributes=attributes,
        effect=_effective_effect(resolved, edges, rules),
        consumable=attributes.get("consumable"),
        use_modes=tuple(attributes.get("use_modes", ())),
        edges=edges,
        use_duration_seconds=_effective_duration(edges, attributes),
        serving=_serving(attributes),
        required_location_id=attributes.get("required_location_id"),
        acceptance=_acceptance(attributes, resolved.kind),
        efficiency=_efficiency(attributes, resolved.kind),
        provenance=resolved.provenance,
        version=resolved.version,
    )


def _compile_edges(
    obj: ResolvedObject,
    contract: CategoryContract,
    rules: ExchangeRuleSet,
) -> tuple[CompiledEdge, ...]:
    """@brief 编译 person 对对象品类的全部 defined 动作边。"""
    edges: list[CompiledEdge] = []
    for action in Action:
        pairing = resolve_pairing(contract, "person", obj.category_id, action)
        if pairing.state != PairingState.DEFINED:
            continue
        assert pairing.rule_id is not None and pairing.coefficient is not None
        rule = rules.require(pairing.rule_id)
        edges.append(_edge_for_rule(obj, rule, pairing.coefficient, contract))
    return tuple(edges)


def _effective_effect(
    obj: ResolvedObject,
    edges: tuple[CompiledEdge, ...],
    rules: ExchangeRuleSet,
) -> str | None:
    """@brief 从已编译耐用品规则选择运行期效果槽。"""
    slot = "effect"
    for edge in edges:
        if isinstance(edge, RuleEdge) and edge.on == Action.CONSUME_USE.value:
            rule = rules.require(edge.rule_id)
            if isinstance(rule, DurableUseRule) and rule.effect_slot is not None:
                slot = rule.effect_slot
            break
    if slot not in obj.attributes:
        return None
    return _string_attribute(obj, slot)


def _effective_duration(
    edges: tuple[CompiledEdge, ...],
    attributes: Mapping[str, Any],
) -> int | None:
    """@brief 从食品能量编译边读取运行期使用时长。"""
    for edge in edges:
        if isinstance(edge, SourceEdge):
            return edge.duration_s
    return attributes.get("use_duration_seconds")


def _edge_for_rule(
    obj: ResolvedObject,
    rule: Any,
    coefficient: int | float,
    contract: CategoryContract,
) -> CompiledEdge:
    """@brief 将一条 typed rule 投影为字面边或动态规则引用边。"""
    if isinstance(rule, PurchaseTransferRule):
        return RuleEdge(on=rule.action.value, type="rule", rule_id=rule.rule_id, coefficient=coefficient)
    if isinstance(rule, FoodEnergyRule):
        energy = _number_attribute(obj, rule.energy_slot)
        duration = _positive_integer_attribute(obj, rule.duration_slot)
        return SourceEdge(
            on="consume", type="source", resource=rule.resource_id,
            amount=evaluate_food_energy(energy, coefficient), duration_s=duration, unit=rule.unit,
        )
    _validate_dynamic_rule_attributes(obj, rule, contract)
    return RuleEdge(on=rule.action.value, type="rule", rule_id=rule.rule_id, coefficient=coefficient)


def _validate_dynamic_rule_attributes(
    obj: ResolvedObject,
    rule: Any,
    contract: CategoryContract,
) -> None:
    """@brief 要求动态闭集规则引用的对象槽位在编译期已经存在。"""
    if isinstance(rule, DurableUseRule):
        if rule.effect_slot is not None:
            _attribute(obj, rule.effect_slot)
    elif isinstance(rule, WorkHourlyRule):
        _require_declared_slots(contract, obj.category_id, (rule.rate_slot, rule.maximum_duration_slot))
    elif isinstance(rule, WorkPieceRule):
        _require_declared_slots(contract, obj.category_id, (rule.rate_slot,))
    elif isinstance(rule, WorkPackageRule):
        _require_declared_slots(contract, obj.category_id, (rule.amount_slot,))
    elif isinstance(rule, InventoryTransformRule):
        if not contract.is_descendant(obj.category_id, rule.input_category_id):
            raise CatalogError(
                "CATALOG_RECIPE_INPUT_CATEGORY_MISMATCH",
                f"object {obj.object_id} does not belong directly to recipe input category",
            )
        unit = _attribute(obj, "inventory_unit")
        if unit != rule.inventory_unit:
            raise CatalogError(
                "CATALOG_RECIPE_UNIT_MISMATCH", f"object {obj.object_id} has another inventory unit",
            )
    elif not isinstance(rule, TravelFareRule):
        raise CatalogError("CATALOG_RULE_OPERATOR_UNKNOWN", f"unsupported rule type {type(rule).__name__}")


def _require_declared_slots(
    contract: CategoryContract,
    category_id: str,
    slot_ids: tuple[str, ...],
) -> None:
    """@brief 要求任务实例输入所对应的槽位已由合同品类声明。"""
    slots = contract.require_category(category_id).slots
    missing = sorted(set(slot_ids) - set(slots))
    if missing:
        raise CatalogError(
            "CATALOG_RULE_SLOT_UNDECLARED", f"category {category_id} lacks rule slots {missing}",
        )


def _serving(attributes: Mapping[str, Any]) -> Serving | None:
    """@brief 同时存在份量与单位时构造份量视图，半声明拒绝。"""
    present = ("serving_quantity" in attributes, "serving_unit" in attributes)
    if not any(present):
        return None
    if not all(present):
        raise CatalogError("CATALOG_SERVING_INCOMPLETE", "serving quantity and unit must be declared together")
    return Serving(quantity=attributes["serving_quantity"], unit=attributes["serving_unit"])


def _acceptance(attributes: Mapping[str, Any], kind: CategoryKind) -> Acceptance | None:
    """@brief 为合同对象构造验收有效视图。"""
    if kind != CategoryKind.CONTRACT:
        return None
    return Acceptance(
        required_effective_seconds=attributes["acceptance_required_effective_seconds"],
        materials_fully_revealed=attributes["acceptance_materials_fully_revealed"],
        review_pass_required=attributes["acceptance_review_pass_required"],
    )


def _efficiency(attributes: Mapping[str, Any], kind: CategoryKind) -> Efficiency | None:
    """@brief 为合同对象构造效率参数有效视图。"""
    if kind != CategoryKind.CONTRACT:
        return None
    return Efficiency.model_validate({
        "work_efficiency_factors": attributes["work_efficiency_factors"],
        "work_notes_multiplier": attributes["work_notes_multiplier"],
    })


def _attribute(obj: ResolvedObject, slot: str) -> Any:
    """@brief 返回规则引用槽位，不存在时明确失败。"""
    if slot not in obj.attributes:
        raise CatalogError(
            "CATALOG_RULE_ATTRIBUTE_MISSING", f"object {obj.object_id} lacks rule slot {slot}",
        )
    return obj.attributes[slot]


def _number_attribute(obj: ResolvedObject, slot: str) -> int | float:
    """@brief 要求规则槽位为非布尔数值。"""
    value = _attribute(obj, slot)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogError("CATALOG_RULE_ATTRIBUTE_INVALID", f"object {obj.object_id} slot {slot} is not numeric")
    return value


def _string_attribute(obj: ResolvedObject, slot: str) -> str:
    """@brief 要求规则槽位为非空字符串。"""
    value = _attribute(obj, slot)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError("CATALOG_RULE_ATTRIBUTE_INVALID", f"object {obj.object_id} slot {slot} is not string")
    return value


def _integer_attribute(obj: ResolvedObject, slot: str) -> int:
    """@brief 要求规则槽位为非负整数。"""
    value = _attribute(obj, slot)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogError("CATALOG_RULE_ATTRIBUTE_INVALID", f"object {obj.object_id} slot {slot} is not integer")
    return value


def _positive_integer_attribute(obj: ResolvedObject, slot: str) -> int:
    """@brief 要求规则槽位为正整数。"""
    value = _integer_attribute(obj, slot)
    if value < 1:
        raise CatalogError("CATALOG_RULE_ATTRIBUTE_INVALID", f"object {obj.object_id} slot {slot} is not positive")
    return value
