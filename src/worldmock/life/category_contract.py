"""@brief 把冻结品类配对接入场景准入与生活动作受理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from worldmock.categories import (
    Action,
    CategoryError,
    InventoryTransformRule,
    PairingState,
    PurchaseTransferRule,
)
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.models import ContractKind

PERSON_CATEGORY_ID = "person"
CONTRACT_OBJECT_IDS = {
    ContractKind.HOURLY: "contract-hourly",
    ContractKind.PIECE: "contract-piece",
    ContractKind.PACKAGE: "contract-package",
}


@dataclass(frozen=True, slots=True)
class RuntimeCategoryRule:
    """@brief 一次动作实际命中的目标品类、声明系数与 typed 规则。"""

    category_id: str
    action: Action
    coefficient: int | float
    rule: Any

    def match(self) -> dict[str, str]:
        """@brief 形成足迹中唯一使用的品类命中统计行。"""
        return {
            "category_id": self.category_id,
            "rule_id": self.rule.rule_id,
            "action": self.action.value,
        }


@dataclass(frozen=True, slots=True)
class ReachablePair:
    """@brief 场景可能触达的一个有向动作目标及其来源。"""

    category_id: str
    action: Action
    source: str


def require_runtime_rule(catalog: Any, category_id: str, action: Action) -> RuntimeCategoryRule:
    """@brief 运行动作复查配对；未定义与已验证不作用均作无副作用拒绝。"""
    try:
        resolution = catalog.pairing(PERSON_CATEGORY_ID, category_id, action)
    except CategoryError as error:
        raise IntegrityError(f"Runtime category pairing failed: {error.code}") from error
    if resolution.state == PairingState.NO_EFFECT:
        raise BusinessRuleError("CATEGORY_PAIRING_NO_EFFECT")
    if resolution.state == PairingState.UNDEFINED:
        raise BusinessRuleError("CATEGORY_PAIRING_UNDEFINED")
    if resolution.rule_id is None or resolution.coefficient is None:
        raise IntegrityError("Defined category pairing has no rule or coefficient")
    rule = catalog.exchange_rules.require(resolution.rule_id)
    return RuntimeCategoryRule(category_id, action, resolution.coefficient, rule)


def optional_defined_rule(
    catalog: Any, category_id: str, action: Action,
) -> RuntimeCategoryRule | None:
    """@brief 组装时只解析 defined 配对，其他两态留给场景准入裁决。"""
    try:
        resolution = catalog.pairing(PERSON_CATEGORY_ID, category_id, action)
    except CategoryError as error:
        raise IntegrityError(f"Category pairing resolution failed: {error.code}") from error
    if resolution.state != PairingState.DEFINED:
        return None
    if resolution.rule_id is None or resolution.coefficient is None:
        raise IntegrityError("Defined category pairing has no rule or coefficient")
    rule = catalog.exchange_rules.require(resolution.rule_id)
    return RuntimeCategoryRule(category_id, action, resolution.coefficient, rule)


def require_item_rules(
    catalog: Any,
    products: Mapping[str, Any],
    object_ids: Iterable[str],
    action: Action,
) -> tuple[RuntimeCategoryRule, ...]:
    """@brief 按实际对象顺序复查动作配对，供结算前统一拒绝。"""
    matches = []
    for object_id in object_ids:
        product = products.get(object_id)
        if product is None:
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        matches.append(require_runtime_rule(catalog, product.category_id, action))
    return tuple(matches)


def match_rows(matches: Iterable[RuntimeCategoryRule]) -> list[dict[str, str]]:
    """@brief 去重同一动作中的等价配对命中并保留稳定顺序。"""
    rows: list[dict[str, str]] = []
    identities: set[tuple[str, str, str]] = set()
    for match in matches:
        row = match.match()
        identity = (row["category_id"], row["rule_id"], row["action"])
        if identity not in identities:
            identities.add(identity)
            rows.append(row)
    return rows


def admit_reachable_categories(document: Any, catalog: Any) -> tuple[ReachablePair, ...]:
    """@brief 从场景事实自行推导完整触达闭包，未定义或歧义在启动前拒绝。"""
    _validate_recipe_bindings(document, catalog)
    pairs = _initial_pairs(document, catalog)
    index = {(pair.category_id, pair.action): pair for pair in pairs}
    pending = list(index.values())
    while pending:
        pair = pending.pop(0)
        rule = _admit_pair(catalog, pair)
        if not isinstance(rule, InventoryTransformRule):
            continue
        output = _recipe_output(document, catalog, rule)
        for action in _consume_actions(output):
            key = (output.category_id, action)
            if key not in index:
                index[key] = ReachablePair(output.category_id, action, f"recipe:{rule.rule_id}")
                pending.append(index[key])
    return tuple(sorted(index.values(), key=lambda item: (item.category_id, item.action.value)))


def _initial_pairs(document: Any, catalog: Any) -> list[ReachablePair]:
    """@brief 收集全部商品、初始持有、未来发布、合同与路线的可能动作。"""
    pairs: list[ReachablePair] = []
    product_ids = {item.product_id for item in document.products}
    supplies = {item.supply_id: item for item in document.supplies}
    release_ids = {supplies[item.supply_id].product_id for item in document.product_releases}
    held_ids = {object_id for agent in document.agents
                for holding in agent.initial_inventory.values() for object_id in holding}
    for object_id in sorted(product_ids | release_ids | held_ids):
        obj = _require_object(catalog, object_id, "reachable object")
        if object_id in product_ids | release_ids:
            pairs.append(ReachablePair(obj.category_id, Action.PURCHASE, f"product:{object_id}"))
        pairs.extend(ReachablePair(obj.category_id, action, f"holding:{object_id}")
                     for action in _consume_actions(obj))
    pairs.extend(_task_pairs(document, catalog))
    return pairs


def _task_pairs(document: Any, catalog: Any) -> list[ReachablePair]:
    """@brief 把每种任务合同映射到冻结模板对象的品类。"""
    pairs = []
    for task in document.tasks:
        object_id = CONTRACT_OBJECT_IDS[task.contract_kind]
        obj = _require_object(catalog, object_id, "contract template")
        pairs.append(ReachablePair(obj.category_id, Action.WORK_SETTLE, f"task:{task.task_id}"))
    return pairs




def _consume_actions(obj: Any) -> tuple[Action, ...]:
    """@brief 从对象编译后的封闭使用方式推导可触达动作。"""
    actions = []
    for mode in obj.use_modes:
        try:
            actions.append(Action(f"consume:{mode}"))
        except ValueError as error:
            raise IntegrityError(f"Catalog object {obj.object_id} has an unknown use mode") from error
    return tuple(actions)


def _admit_pair(catalog: Any, pair: ReachablePair) -> Any | None:
    """@brief 准入一个推导配对；no_effect 合法，undefined 与歧义拒绝。"""
    try:
        resolution = catalog.pairing(PERSON_CATEGORY_ID, pair.category_id, pair.action)
    except CategoryError as error:
        raise IntegrityError(
            f"Scenario category admission rejected {pair.source}: {error.code}") from error
    if resolution.state == PairingState.UNDEFINED:
        raise IntegrityError(
            f"Scenario category admission rejected undefined {pair.action.value} for {pair.category_id}")
    if resolution.state == PairingState.NO_EFFECT:
        return None
    if resolution.rule_id is None or resolution.coefficient is None:
        raise IntegrityError("Defined category pairing has no rule or coefficient")
    rule = catalog.exchange_rules.require(resolution.rule_id)
    if isinstance(rule, PurchaseTransferRule) and (rule.from_role != "buyer" or rule.to_role != "merchant"):
        raise IntegrityError("Purchase transfer roles must be buyer and merchant")
    if isinstance(rule, InventoryTransformRule) and resolution.coefficient == 0:
        raise IntegrityError("Reachable inventory recipe coefficient must be positive")
    return rule


def _validate_recipe_bindings(document: Any, catalog: Any) -> None:
    """@brief 所有显式输出绑定必须指向库存转换规则与相容对象。"""
    rules = tuple(rule for rule in catalog.exchange_rules.rules.values()
                  if isinstance(rule, InventoryTransformRule))
    for category_id, object_id in document.recipe_output_bindings.items():
        matches = tuple(rule for rule in rules if rule.output_category_id == category_id)
        if not matches:
            raise IntegrityError(
                f"Recipe binding references unused output category {category_id}")
        for rule in matches:
            _validate_recipe_output(catalog, rule, object_id)


def _recipe_output(document: Any, catalog: Any, rule: InventoryTransformRule) -> Any:
    """@brief 为可触达配方取得显式输出对象，不按名称或目录顺序猜测。"""
    object_id = document.recipe_output_bindings.get(rule.output_category_id)
    if object_id is None:
        raise IntegrityError(
            f"Reachable inventory recipe output {rule.output_category_id} has no binding")
    return _validate_recipe_output(catalog, rule, object_id)


def _validate_recipe_output(catalog: Any, rule: InventoryTransformRule, object_id: str) -> Any:
    """@brief 核对输出对象的品类归属与库存单位。"""
    output = _require_object(catalog, object_id, "recipe output")
    contract = catalog.category_contract
    if not contract.is_descendant(output.category_id, rule.output_category_id):
        raise IntegrityError(f"Recipe output {object_id} does not belong to {rule.output_category_id}")
    if output.attribute("inventory_unit") != rule.inventory_unit:
        raise IntegrityError(f"Recipe output {object_id} uses another inventory unit")
    return output


def _require_object(catalog: Any, object_id: str, label: str) -> Any:
    """@brief 从编译目录取得一个对象并保留明确错误上下文。"""
    obj = catalog.objects.get(object_id)
    if obj is None:
        raise IntegrityError(f"{label} {object_id} is not in the catalog")
    return obj
