"""@brief 把 consume:use 的库存转换准备与完成提交保持在一个原子路径。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import logging
import math
from typing import Any, Mapping

from worldmock.categories import (Action, CategoryError, InventoryTransformRule, PairingState,
                                  scale_inventory_recipe)
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.category_contract import PERSON_CATEGORY_ID, RuntimeCategoryRule, require_runtime_rule
from worldmock.life.models import ActivityState, AgentState, ConsumeRequest
from worldmock.life.scenario import InventoryItemConfig
from worldmock.resources import ConvertEntry, ResourceLedger, ResourceLedgerError, ResourceMeta


@dataclass(frozen=True, slots=True)
class RecipeContext:
    """@brief 配方准备所需的冻结目录、对象视图与场景输出绑定。"""

    catalog: Any
    products: Mapping[str, InventoryItemConfig]
    output_bindings: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class InventoryRecipePlan:
    """@brief 一次活动完整结束时提交的已验证库存转换。"""

    rule_id: str
    input_object_id: str
    output_object_id: str
    input_quantity: Decimal
    output_quantity: Decimal
    resource_id: str
    inventory_unit: str

    def payload(self) -> dict[str, str]:
        """@brief 转为活动快照可稳定序列化的十进制字符串。"""
        return {
            "rule_id": self.rule_id,
            "input_object_id": self.input_object_id,
            "output_object_id": self.output_object_id,
            "input_quantity": str(self.input_quantity),
            "output_quantity": str(self.output_quantity),
            "resource_id": self.resource_id,
            "inventory_unit": self.inventory_unit,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "InventoryRecipePlan":
        """@brief 从已持久化活动数据恢复冻结计划。"""
        try:
            return cls(
                rule_id=str(payload["rule_id"]),
                input_object_id=str(payload["input_object_id"]),
                output_object_id=str(payload["output_object_id"]),
                input_quantity=Decimal(str(payload["input_quantity"])),
                output_quantity=Decimal(str(payload["output_quantity"])),
                resource_id=str(payload["resource_id"]),
                inventory_unit=str(payload["inventory_unit"]),
            )
        except (InvalidOperation, KeyError, ValueError) as error:
            raise IntegrityError("Inventory recipe activity payload is invalid") from error


def is_inventory_recipe(catalog: Any, product: InventoryItemConfig) -> bool:
    """@brief 物品的 consume:use 规则是否为库存转换配方（与 prepare_inventory_recipe 同一判据）。

    是则交互对象层列动词 obtain 的方式「制作」，否则列 consume 的「使用」（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章
    「请求形状与菜单」）；不支持使用方式或没有已定义 consume:use 配对的物品不是配方。只读配对，不抛业务拒绝。
    """
    if "use" not in product.use_modes:
        return False
    try:
        resolution = catalog.pairing(PERSON_CATEGORY_ID, product.category_id, Action("consume:use"))
    except CategoryError as error:
        raise IntegrityError(f"Runtime category pairing failed: {error.code}") from error
    if resolution.state != PairingState.DEFINED or resolution.rule_id is None:
        return False
    return isinstance(catalog.exchange_rules.require(resolution.rule_id), InventoryTransformRule)


def prepare_inventory_recipe(
    agent: AgentState,
    context: RecipeContext,
    request: ConsumeRequest,
    match: RuntimeCategoryRule,
) -> InventoryRecipePlan | None:
    """@brief 对库存转换做运行时复查并在启动活动前验证完整输入。"""
    if not isinstance(match.rule, InventoryTransformRule):
        return None
    item = request.items[0]
    try:
        delta = scale_inventory_recipe(match.rule, item.quantity, match.coefficient)
    except CategoryError as error:
        raise BusinessRuleError(error.code) from error
    source = _require_item(context.products, item.product_id)
    output_id = context.output_bindings.get(match.rule.output_category_id)
    if output_id is None:
        raise BusinessRuleError("INVENTORY_RECIPE_OUTPUT_UNBOUND")
    output = _require_item(context.products, output_id)
    _validate_categories(context.catalog, source, output, delta)
    plan = InventoryRecipePlan(
        match.rule.rule_id, source.product_id, output.product_id,
        delta.input_quantity, delta.output_quantity, match.rule.resource_id,
        delta.inventory_unit,
    )
    _inventory_amounts(agent, plan)
    return plan


def apply_inventory_recipe(
    agent: AgentState,
    products: Mapping[str, InventoryItemConfig],
    catalog: Any,
    activity: ActivityState,
) -> AgentState:
    """@brief 活动完整结束时一次扣输入、加输出并投影原使用效果。"""
    if activity.data.get("recipe_applied"):
        return agent
    plan = InventoryRecipePlan.from_payload(activity.data["inventory_recipe"])
    source = _require_item(products, plan.input_object_id)
    output = _require_item(products, plan.output_object_id)
    match = require_runtime_rule(catalog, source.category_id, Action.CONSUME_USE)
    if not isinstance(match.rule, InventoryTransformRule) or match.rule.rule_id != plan.rule_id:
        raise BusinessRuleError("INVENTORY_RECIPE_RULE_MISMATCH")
    _validate_categories(catalog, source, output, match.rule)
    inventory = _apply_inventory_ledger(agent, catalog, plan, activity)
    effects = set(agent.active_effects)
    effects.add(source.effect)
    activity.data["recipe_applied"] = True
    activity.data.setdefault("consumed", {})[source.product_id] = float(plan.input_quantity)
    return agent.model_copy(update={"inventory": inventory, "active_effects": effects}, deep=True)


def _apply_inventory_ledger(agent: AgentState, catalog: Any, plan: InventoryRecipePlan,
                            activity: ActivityState) -> dict[str, dict[str, float]]:
    """@brief 通过资源账本一次提交输入扣减和输出增加。"""
    resource = catalog.resources.get(plan.resource_id)
    if resource is None:
        raise BusinessRuleError("INVENTORY_RECIPE_RESOURCE_UNKNOWN")
    bounds = resource.bounds
    meta = ResourceMeta(resource.resource_id, resource.kind.value, resource.conservation.value,
                        bounds.min if bounds is not None else 0,
                        bounds.max if bounds is not None else None)
    input_owner = _inventory_owner(agent, plan.input_object_id)
    output_owner = _inventory_owner(agent, plan.output_object_id)
    input_amount, output_amount = _inventory_amounts(agent, plan)
    balances = {
        (plan.resource_id, input_owner): _held(agent, plan.input_object_id),
        (plan.resource_id, output_owner): _held(agent, plan.output_object_id),
    }
    try:
        entry = ConvertEntry(
            inputs=((plan.resource_id, input_owner, input_amount),),
            outputs=((plan.resource_id, output_owner, output_amount),),
            cause=activity.origin_action_id,
            occurred_at=activity.planned_end_at,
        )
        converted = ResourceLedger((meta,)).apply((entry,), balances)
    except ResourceLedgerError as error:
        logging.error("Inventory recipe settlement failed: %s", error.code)
        raise BusinessRuleError(error.code) from error
    expected = {key: value + (output_amount if key[1] == output_owner else -input_amount)
                for key, value in balances.items()}
    if converted != expected:
        raise BusinessRuleError("OUT_OF_BOUNDS")
    return _inventory_from_balances(agent, plan, converted, input_owner, output_owner)


def _inventory_amounts(agent: AgentState, plan: InventoryRecipePlan) -> tuple[float, float]:
    """@brief 受理和完成均复查真实库存，拒绝不能表示为有限库存变化的正数量。"""
    quantities = (plan.input_quantity, plan.output_quantity)
    if any(not value.is_finite() or value <= 0 for value in quantities):
        raise BusinessRuleError("EXCHANGE_INPUT_INVALID")
    held = _held(agent, plan.input_object_id)
    if Decimal(str(held)) < plan.input_quantity:
        raise BusinessRuleError("PRODUCT_NOT_HELD")
    input_amount, output_amount = (float(value) for value in quantities)
    if any(not math.isfinite(value) or value <= 0 for value in (input_amount, output_amount)):
        raise BusinessRuleError("INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE")
    output_held = _held(agent, plan.output_object_id)
    if plan.input_object_id != plan.output_object_id and (
            held - input_amount >= held or output_held + output_amount <= output_held
            or not math.isfinite(output_held + output_amount)):
        raise BusinessRuleError("INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE")
    return input_amount, output_amount


def _inventory_owner(agent: AgentState, object_id: str) -> str:
    """@brief 为账本形成地点内单个库存对象的稳定主体身份。"""
    return f"agent:{agent.agent_id}:location:{agent.location_id}:object:{object_id}"


def _inventory_from_balances(agent: AgentState, plan: InventoryRecipePlan,
                             balances: Mapping[tuple[str, str], float],
                             input_owner: str, output_owner: str) -> dict[str, dict[str, float]]:
    """@brief 把账本转换后的两个对象余额投影回人物地点库存。"""
    inventory = {location: dict(items) for location, items in agent.inventory.items()}
    local = inventory.setdefault(agent.location_id, {})
    local[plan.input_object_id] = balances[(plan.resource_id, input_owner)]
    local[plan.output_object_id] = balances[(plan.resource_id, output_owner)]
    return inventory


def _validate_categories(catalog: Any, source: InventoryItemConfig,
                         output: InventoryItemConfig, delta: Any) -> None:
    """@brief 复查输入输出品类及两端库存单位。"""
    contract = catalog.category_contract
    if not contract.is_descendant(source.category_id, delta.input_category_id):
        raise BusinessRuleError("INVENTORY_RECIPE_INPUT_CATEGORY_MISMATCH")
    if not contract.is_descendant(output.category_id, delta.output_category_id):
        raise BusinessRuleError("INVENTORY_RECIPE_OUTPUT_CATEGORY_MISMATCH")
    if source.inventory_unit != delta.inventory_unit or output.inventory_unit != delta.inventory_unit:
        raise BusinessRuleError("INVENTORY_RECIPE_UNIT_MISMATCH")


def _require_item(
    products: Mapping[str, InventoryItemConfig], object_id: str,
) -> InventoryItemConfig:
    """@brief 返回运行视图中的库存对象。"""
    item = products.get(object_id)
    if item is None:
        raise BusinessRuleError("PRODUCT_NOT_FOUND")
    return item


def _held(agent: AgentState, object_id: str) -> float:
    """@brief 返回人物当前位置的对象数量。"""
    return float(agent.inventory.get(agent.location_id, {}).get(object_id, 0))
