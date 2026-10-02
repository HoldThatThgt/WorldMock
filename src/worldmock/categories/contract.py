"""@brief 品类树、属性继承与对象增量的严格解析。"""

from __future__ import annotations

import copy
import math
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from worldmock.categories.common import CategoryError, CategoryKind
from worldmock.categories.models import (
    AttributeSlot,
    AttributeValues,
    CategoryContract,
    CategoryContractDocument,
    CategoryNode,
    ResolvedCategory,
    ResolvedObject,
    ResolvedSlot,
)
from worldmock.categories.provenance import validate_numeric_sources

_CONTRACT_EXCLUDED_NUMBERS = frozenset({"format_version", "contract_version", "sources", "review"})


def build_category_contract(
    document: CategoryContractDocument,
    raw_document: dict[str, Any],
) -> CategoryContract:
    """@brief 从已校验原始模型构建冻结解析契约。

    :param document: Pydantic 校验后的契约文档。
    :param raw_document: 对应原始 JSON，用于精确来源覆盖。
    :returns: 完成树、槽位和值解析的冻结契约。
    :raises CategoryError: 结构、来源或继承无效。
    """
    validate_numeric_sources(raw_document, document.sources, _CONTRACT_EXCLUDED_NUMBERS)
    declarations = _index_categories(document.categories)
    _validate_pairing_keys(document, declarations)
    resolved: dict[str, ResolvedCategory] = {}
    active: set[str] = set()
    for category_id in declarations:
        _resolve_category(category_id, declarations, resolved, active)
    _validate_roots_and_person(resolved)
    return CategoryContract(
        contract_version=document.contract_version,
        categories=MappingProxyType(resolved),
        declarations=MappingProxyType(declarations),
        pairings=document.pairings,
        sources=document.sources,
        review=document.review,
    )


def resolve_object_values(declaration: Any, contract: CategoryContract) -> ResolvedObject:
    """@brief 把一条原始对象声明应用到指定品类并验证全部有效槽位。

    ``declaration`` 使用鸭子类型读取 object_id、name、category_id、values、
    provenance 与 version，避免 categories 反向依赖 catalog。

    :param declaration: 严格原始对象模型。
    :param contract: 冻结品类契约。
    :returns: 只读对象有效值。
    :raises CategoryError: 品类、增量、来源或槽位约束无效。
    """
    category = contract.require_category(declaration.category_id)
    if category.kind not in {CategoryKind.PRODUCT, CategoryKind.SERVICE, CategoryKind.CONTRACT}:
        raise CategoryError(
            "CATEGORY_OBJECT_KIND_INVALID",
            f"object {declaration.object_id} cannot belong to {category.kind.value} category",
        )
    raw = declaration.model_dump(mode="python", by_alias=True)
    validate_numeric_sources(raw, declaration.provenance, frozenset({"version", "provenance"}))
    values = _apply_values(
        dict(category.attributes), declaration.values, category.slots, "object", True,
    )
    return ResolvedObject(
        object_id=declaration.object_id,
        name=declaration.name,
        category_id=declaration.category_id,
        kind=category.kind,
        attributes=_freeze_mapping(values),
        provenance=declaration.provenance,
        version=declaration.version,
    )


def validate_category_attributes(
    contract: CategoryContract,
    category_id: str,
    attributes: Mapping[str, Any],
) -> Mapping[str, Any]:
    """@brief 以候选品类全部有效槽位校验一组已验证对象属性。

    :param contract: 候选冻结品类契约。
    :param category_id: 候选品类身份。
    :param attributes: 当前对象的已验证有效属性。
    :returns: 应用候选新默认值后的深只读属性。
    :raises CategoryError: 未知槽位、缺必填值或约束不满足。
    """
    category = contract.require_category(category_id)
    values = {key: _thaw_value(value) for key, value in category.attributes.items()}
    values.update({key: _thaw_value(value) for key, value in attributes.items()})
    _validate_values(values, category.slots, f"category {category_id} candidate", True)
    return _freeze_mapping(values)


def _index_categories(categories: tuple[CategoryNode, ...]) -> dict[str, CategoryNode]:
    """@brief 建立节点索引并拒绝重复身份、孤儿和根数量错误。"""
    indexed: dict[str, CategoryNode] = {}
    for node in categories:
        if node.category_id in indexed:
            raise CategoryError("CATEGORY_DUPLICATE", f"duplicate category {node.category_id}")
        indexed[node.category_id] = node
    roots = [node.category_id for node in categories if node.parent_id is None]
    if len(roots) != 1:
        raise CategoryError("CATEGORY_ROOT_COUNT_INVALID", f"expected one root, found {roots}")
    for node in categories:
        if node.parent_id is not None and node.parent_id not in indexed:
            raise CategoryError(
                "CATEGORY_PARENT_UNKNOWN",
                f"category {node.category_id} references unknown parent {node.parent_id}",
            )
    return indexed


def _resolve_category(
    category_id: str,
    declarations: Mapping[str, CategoryNode],
    resolved: dict[str, ResolvedCategory],
    active: set[str],
) -> ResolvedCategory:
    """@brief 以深度优先方式解析一个节点并检测环。"""
    if category_id in resolved:
        return resolved[category_id]
    if category_id in active:
        raise CategoryError("CATEGORY_CYCLE", f"category cycle reaches {category_id}")
    active.add(category_id)
    node = declarations[category_id]
    parent = _resolved_parent(node, declarations, resolved, active)
    result = _build_resolved_category(node, parent)
    resolved[category_id] = result
    active.remove(category_id)
    return result


def _resolved_parent(
    node: CategoryNode,
    declarations: Mapping[str, CategoryNode],
    resolved: dict[str, ResolvedCategory],
    active: set[str],
) -> ResolvedCategory | None:
    """@brief 返回节点的已解析父节点，根返回空。"""
    if node.parent_id is None:
        return None
    return _resolve_category(node.parent_id, declarations, resolved, active)


def _build_resolved_category(
    node: CategoryNode,
    parent: ResolvedCategory | None,
) -> ResolvedCategory:
    """@brief 合并形态、槽位和值，形成一个只读有效节点。"""
    kind = _resolve_kind(node, parent)
    slots = dict(parent.slots) if parent is not None else {}
    for slot_id, slot in node.slots.items():
        slots[slot_id] = _merge_slot(slot_id, slots.get(slot_id), slot)
    values = dict(parent.attributes) if parent is not None else {}
    _apply_defaults(values, slots)
    values = _apply_values(values, node.values, slots, f"category {node.category_id}", False)
    lineage = (*parent.lineage, node.category_id) if parent is not None else (node.category_id,)
    return ResolvedCategory(
        category_id=node.category_id,
        name=node.name,
        parent_id=node.parent_id,
        kind=kind,
        slots=MappingProxyType(slots),
        attributes=_freeze_mapping(values),
        lineage=lineage,
    )


def _resolve_kind(node: CategoryNode, parent: ResolvedCategory | None) -> CategoryKind:
    """@brief 解析对象形态并拒绝跨 product/service/contract/person 继承。"""
    if parent is None:
        if node.kind != CategoryKind.ENTITY:
            raise CategoryError("CATEGORY_ROOT_KIND_INVALID", "root category kind must be entity")
        return CategoryKind.ENTITY
    kind = node.kind or parent.kind
    if kind == CategoryKind.ENTITY:
        raise CategoryError(
            "CATEGORY_KIND_MISSING",
            f"category {node.category_id} must enter a concrete kind subtree",
        )
    if parent.kind != CategoryKind.ENTITY and kind != parent.kind:
        raise CategoryError(
            "CATEGORY_CROSS_KIND_INHERITANCE",
            f"category {node.category_id} changes {parent.kind.value} to {kind.value}",
        )
    return kind


def _merge_slot(
    slot_id: str,
    inherited: ResolvedSlot | None,
    declared: AttributeSlot,
) -> ResolvedSlot:
    """@brief 校验并累积一个槽位的 JSON Schema 约束。"""
    schema = _thaw_value(declared.schema_)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise CategoryError(
            "CATEGORY_SLOT_SCHEMA_INVALID", f"slot {slot_id} has invalid JSON Schema: {error.message}",
        ) from error
    if inherited is None:
        return ResolvedSlot(slot_id, (_freeze_mapping(schema),), declared.required, declared.unit)
    if inherited.required and not declared.required:
        raise CategoryError("CATEGORY_SLOT_REQUIRED_LOOSENED", f"slot {slot_id} cannot become optional")
    if declared.unit is not None and inherited.unit not in (None, declared.unit):
        raise CategoryError("CATEGORY_SLOT_UNIT_CHANGED", f"slot {slot_id} changes inherited unit")
    unit = declared.unit or inherited.unit
    return ResolvedSlot(slot_id, (*inherited.schemas, _freeze_mapping(schema)), declared.required, unit)


def _apply_defaults(values: dict[str, Any], slots: Mapping[str, ResolvedSlot]) -> None:
    """@brief 首次声明槽位时应用 JSON Schema default。"""
    for slot_id, slot in slots.items():
        if slot_id in values:
            continue
        defaults = [schema["default"] for schema in slot.schemas if "default" in schema]
        if defaults:
            if any(default != defaults[0] for default in defaults[1:]):
                raise CategoryError("CATEGORY_SLOT_DEFAULT_CONFLICT", f"slot {slot_id} has conflicting defaults")
            values[slot_id] = _thaw_value(defaults[0])


def _apply_values(
    inherited: dict[str, Any],
    operations: AttributeValues,
    slots: Mapping[str, ResolvedSlot],
    owner: str,
    require_complete: bool,
) -> dict[str, Any]:
    """@brief 应用一层绝对值或数值增量，再校验全部有效约束。"""
    values = {key: _thaw_value(value) for key, value in inherited.items()}
    for slot_id, value in operations.absolute.items():
        _require_slot(slot_id, slots, owner)
        if slot_id in inherited and _thaw_value(value) == _thaw_value(inherited[slot_id]):
            raise CategoryError(
                "CATEGORY_VALUE_REDUNDANT", f"{owner} repeats inherited value for {slot_id}",
            )
        values[slot_id] = _thaw_value(value)
    _apply_numeric_operations(values, operations.relative, slots, owner, "relative")
    _apply_numeric_operations(values, operations.proportional, slots, owner, "proportional")
    _validate_values(values, slots, owner, require_complete)
    return values


def _apply_numeric_operations(
    values: dict[str, Any],
    changes: Mapping[str, int | float],
    slots: Mapping[str, ResolvedSlot],
    owner: str,
    operation: str,
) -> None:
    """@brief 对已有数值应用加法或乘法增量。"""
    for slot_id, change in changes.items():
        _require_slot(slot_id, slots, owner)
        base = values.get(slot_id)
        if isinstance(base, bool) or not isinstance(base, (int, float)):
            raise CategoryError(
                "CATEGORY_DELTA_BASE_INVALID", f"{owner} {operation} {slot_id} needs a numeric base",
            )
        value = base + change if operation == "relative" else base * change
        if isinstance(value, float) and not math.isfinite(value):
            raise CategoryError("CATEGORY_NUMBER_INVALID", f"{owner} {slot_id} becomes non-finite")
        if value == base:
            raise CategoryError(
                "CATEGORY_VALUE_REDUNDANT", f"{owner} {operation} leaves {slot_id} unchanged",
            )
        values[slot_id] = value


def _validate_values(
    values: dict[str, Any],
    slots: Mapping[str, ResolvedSlot],
    owner: str,
    require_complete: bool,
) -> None:
    """@brief 以全部祖先 JSON Schema 约束校验当前有效值。"""
    unknown = sorted(set(values) - set(slots))
    if unknown:
        raise CategoryError("CATEGORY_SLOT_UNKNOWN", f"{owner} defines unknown slots {unknown}")
    missing = sorted(slot_id for slot_id, slot in slots.items() if slot.required and slot_id not in values)
    if require_complete and missing:
        raise CategoryError("CATEGORY_REQUIRED_SLOT_MISSING", f"{owner} lacks required slots {missing}")
    for slot_id, value in values.items():
        for schema in slots[slot_id].schemas:
            try:
                Draft202012Validator(schema).validate(_thaw_value(value))
            except ValidationError as error:
                raise CategoryError(
                    "CATEGORY_SLOT_VALUE_INVALID", f"{owner} slot {slot_id}: {error.message}",
                ) from error
    lower = values.get("purchase_price_fen_min")
    upper = values.get("purchase_price_fen_max")
    if lower is not None and upper is not None and lower > upper:
        raise CategoryError("CATEGORY_PRICE_RANGE_INVALID", f"{owner} purchase price range is reversed")


def _require_slot(slot_id: str, slots: Mapping[str, ResolvedSlot], owner: str) -> None:
    """@brief 要求增量或绝对赋值引用已声明槽位。"""
    if slot_id not in slots:
        raise CategoryError("CATEGORY_SLOT_UNKNOWN", f"{owner} references unknown slot {slot_id}")


def _validate_pairing_keys(
    document: CategoryContractDocument,
    categories: Mapping[str, CategoryNode],
) -> None:
    """@brief 拒绝重复配对键和未知参与品类。"""
    keys: set[tuple[str, str, str]] = set()
    for pairing in document.pairings:
        if pairing.actor_category_id not in categories or pairing.target_category_id not in categories:
            raise CategoryError("PAIRING_CATEGORY_UNKNOWN", "pairing references an unknown category")
        key = (pairing.actor_category_id, pairing.target_category_id, pairing.action.value)
        if key in keys:
            raise CategoryError("PAIRING_DUPLICATE", f"duplicate pairing {key}")
        keys.add(key)


def _validate_roots_and_person(categories: Mapping[str, ResolvedCategory]) -> None:
    """@brief 要求 person 节点存在且位于 person 形态子树。"""
    person = categories.get("person")
    if person is None or person.kind != CategoryKind.PERSON:
        raise CategoryError("CATEGORY_PERSON_MISSING", "category person must declare the person subtree")


def _freeze_mapping(values: dict[str, Any]) -> Mapping[str, Any]:
    """@brief 深度冻结解析值，避免嵌套写入污染其他调用者。"""
    return MappingProxyType({key: _freeze_value(value) for key, value in values.items()})


def _freeze_value(value: Any) -> Any:
    """@brief 递归把字典和数组转换为只读映射与元组。"""
    if isinstance(value, dict):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    """@brief 为一次独立解析复制只读嵌套值。"""
    if isinstance(value, Mapping):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return copy.deepcopy(value)
