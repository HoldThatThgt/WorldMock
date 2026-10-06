"""@brief 封闭交换规则的严格模型与契约绑定校验。"""

from __future__ import annotations
from worldmock.base_models import DeepFrozenModel, require_finite_number

from dataclasses import dataclass
from enum import StrEnum
from worldmock.resources.models import Conservation
from types import MappingProxyType
from typing import Annotated, Any, Literal, Mapping

from pydantic import Field, StrictInt, field_validator, model_validator

from worldmock.categories.common import (
    Action,
    CategoryError,
    ReviewRecord,
    SourceRecord,
)
from worldmock.categories.models import CategoryContract
from worldmock.categories.provenance import NUMERIC_SOURCE_METADATA_ROOTS, validate_numeric_sources



class RuleOperator(StrEnum):
    """@brief 交换规则支持的封闭算子集合。"""

    PURCHASE_TRANSFER = "purchase_transfer"
    FOOD_ENERGY_SOURCE = "food_energy_source"
    DURABLE_USE = "durable_use"
    TRAVEL_FARE = "travel_fare"
    WORK_HOURLY = "work_hourly"
    WORK_PIECE = "work_piece"
    WORK_PACKAGE = "work_package"
    INVENTORY_TRANSFORM = "inventory_transform"


class RuleBase(DeepFrozenModel):
    """@brief 每条闭集规则共有的身份、目标与守恒声明。"""

    rule_id: str = Field(pattern=r"\S")
    category_id: str = Field(pattern=r"\S")
    action: Action
    resource_id: str = Field(pattern=r"\S")
    unit: str = Field(pattern=r"\S")
    conservation: Conservation


class PurchaseTransferRule(RuleBase):
    """@brief 购买时从买方账户向商家账户转移整数货币。"""

    operator: Literal[RuleOperator.PURCHASE_TRANSFER]
    from_role: str = Field(pattern=r"\S")
    to_role: str = Field(pattern=r"\S")


class FoodEnergyRule(RuleBase):
    """@brief 进食时按对象能量槽向开放身体资源注入能量。"""

    operator: Literal[RuleOperator.FOOD_ENERGY_SOURCE]
    energy_slot: str = Field(pattern=r"\S")
    duration_slot: str = Field(pattern=r"\S")


class DurableUseRule(RuleBase):
    """@brief 使用耐用品时保留对象效果槽语义。"""

    operator: Literal[RuleOperator.DURABLE_USE]
    effect_slot: str | None = Field(default=None, pattern=r"\S")


class TravelFareRule(RuleBase):
    """@brief 以最低金额和每米费率计算出行付款。"""

    operator: Literal[RuleOperator.TRAVEL_FARE]
    minimum_fen: StrictInt = Field(ge=0)
    fen_per_meter: int | float = Field(ge=0, allow_inf_nan=False)

    @field_validator("fen_per_meter", mode="before")
    @classmethod
    def validate_rate_input(cls, value: Any) -> Any:
        """@brief 在转换前要求费率为有限 JSON 数值。"""
        return require_finite_number(value, "fen_per_meter")


class WorkHourlyRule(RuleBase):
    """@brief 按实际秒数和合同最大时长结算时薪。"""

    operator: Literal[RuleOperator.WORK_HOURLY]
    rate_slot: str = Field(pattern=r"\S")
    maximum_duration_slot: str = Field(pattern=r"\S")


class WorkPieceRule(RuleBase):
    """@brief 按验收 oracle 给出的实际件数结算。"""

    operator: Literal[RuleOperator.WORK_PIECE]
    rate_slot: str = Field(pattern=r"\S")


class WorkPackageRule(RuleBase):
    """@brief 验收通过时支付一个总包金额。"""

    operator: Literal[RuleOperator.WORK_PACKAGE]
    amount_slot: str = Field(pattern=r"\S")


class InventoryTransformRule(RuleBase):
    """@brief 一个输入品类到一个输出品类的原子库存转换。"""

    operator: Literal[RuleOperator.INVENTORY_TRANSFORM]
    input_category_id: str = Field(pattern=r"\S")
    output_category_id: str = Field(pattern=r"\S")
    input_quantity: int | float = Field(gt=0, allow_inf_nan=False)
    output_quantity: int | float = Field(gt=0, allow_inf_nan=False)
    inventory_unit: str = Field(pattern=r"\S")

    @field_validator("input_quantity", "output_quantity", mode="before")
    @classmethod
    def validate_quantity_input(cls, value: Any) -> Any:
        """@brief 在转换前要求配方数量为有限 JSON 数值。"""
        return require_finite_number(value, "inventory quantity")

    @model_validator(mode="after")
    def validate_inventory_conservation(self) -> "InventoryTransformRule":
        """@brief 封闭库存域要求输入输出数量相等。"""
        require_finite_number(self.input_quantity, "input_quantity")
        require_finite_number(self.output_quantity, "output_quantity")
        if self.unit != self.inventory_unit:
            raise ValueError("inventory transform unit must match its resource unit")
        if self.conservation == Conservation.CLOSED and self.input_quantity != self.output_quantity:
            raise ValueError("closed inventory transform requires equal input and output quantities")
        return self


ExchangeRule = Annotated[
    PurchaseTransferRule
    | FoodEnergyRule
    | DurableUseRule
    | TravelFareRule
    | WorkHourlyRule
    | WorkPieceRule
    | WorkPackageRule
    | InventoryTransformRule,
    Field(discriminator="operator"),
]

class ExchangeRulesDocument(DeepFrozenModel):
    """@brief `exchange-rules.json` 的严格原始模型。"""

    format_version: StrictInt = Field(ge=1, le=1)
    contract_version: StrictInt = Field(ge=1)
    rules: tuple[ExchangeRule, ...]
    sources: tuple[SourceRecord, ...] = Field(min_length=1)
    review: ReviewRecord


@dataclass(frozen=True)
class ExchangeRuleSet:
    """@brief 与一个契约版本绑定的只读闭集规则表。"""

    contract_version: int
    rules: Mapping[str, ExchangeRule]
    sources: tuple[SourceRecord, ...]
    review: ReviewRecord

    def require(self, rule_id: str) -> ExchangeRule:
        """@brief 返回规则，不存在时以稳定错误失败。

        :param rule_id: 待查规则身份。
        :returns: 对应 typed rule。
        """
        rule = self.rules.get(rule_id)
        if rule is None:
            raise CategoryError("EXCHANGE_RULE_UNKNOWN", f"exchange rule {rule_id} is not declared")
        return rule


def build_exchange_rules(
    document: ExchangeRulesDocument,
    raw_document: dict[str, Any],
    contract: CategoryContract,
) -> ExchangeRuleSet:
    """@brief 校验来源、动作、品类与配对引用并冻结规则表。

    :param document: Pydantic 校验后的规则文档。
    :param raw_document: 对应原始 JSON，用于来源精确覆盖。
    :param contract: 所绑定的冻结品类契约。
    :returns: 只读 typed rule 集合。
    :raises CategoryError: 版本、规则或引用不一致。
    """
    if document.contract_version != contract.contract_version:
        raise CategoryError("EXCHANGE_CONTRACT_VERSION_MISMATCH", "exchange rules bind another contract version")
    validate_numeric_sources(raw_document, document.sources, NUMERIC_SOURCE_METADATA_ROOTS)
    rules = _index_rules(document.rules, contract)
    _validate_pairing_references(contract, rules)
    return ExchangeRuleSet(
        contract_version=document.contract_version,
        rules=MappingProxyType(rules),
        sources=document.sources,
        review=document.review,
    )


def validate_rule_resource_domains(
    rule_set: ExchangeRuleSet,
    resources: Mapping[str, Any],
) -> None:
    """@brief 对照目录资源定义校验每条算子的单位与守恒域。

    :param rule_set: 冻结规则集合。
    :param resources: 以资源身份索引的目录资源定义。
    :raises CategoryError: 资源未声明、单位或守恒域不一致。
    """
    for rule in rule_set.rules.values():
        resource = resources.get(rule.resource_id)
        if resource is None:
            raise CategoryError(
                "EXCHANGE_RESOURCE_UNKNOWN", f"rule {rule.rule_id} references unknown resource {rule.resource_id}",
            )
        expected_id, expected_kind, expected_conservation, expected_unit = _operator_resource_domain(rule)
        resource_kind = getattr(resource.kind, "value", resource.kind)
        resource_conservation = getattr(resource.conservation, "value", resource.conservation)
        actual = (resource.resource_id, resource_kind, resource_conservation, resource.unit)
        declared = (rule.resource_id, expected_kind, rule.conservation.value, rule.unit)
        expected = (expected_id or rule.resource_id, expected_kind, expected_conservation, expected_unit)
        if actual != declared or declared != expected:
            raise CategoryError(
                "EXCHANGE_RESOURCE_DOMAIN_MISMATCH",
                f"rule {rule.rule_id} requires domain {expected}, got declaration {declared} and resource {actual}",
            )


def _operator_resource_domain(rule: ExchangeRule) -> tuple[str | None, str, str, str]:
    """@brief 返回闭集算子实际需要的资源形态、守恒性与单位。"""
    money_rules = (PurchaseTransferRule, TravelFareRule, WorkHourlyRule, WorkPieceRule, WorkPackageRule)
    if isinstance(rule, money_rules):
        return "money_fen", "account", Conservation.CLOSED.value, "fen"
    if isinstance(rule, FoodEnergyRule):
        return "energy_kcal", "level", Conservation.OPEN.value, "kcal"
    if isinstance(rule, DurableUseRule | InventoryTransformRule):
        return None, "level", rule.conservation.value, "item"
    raise CategoryError("EXCHANGE_OPERATOR_UNKNOWN", f"unsupported operator {type(rule).__name__}")


def _index_rules(
    rules: tuple[ExchangeRule, ...],
    contract: CategoryContract,
) -> dict[str, ExchangeRule]:
    """@brief 建立规则索引并校验身份、品类与动作-算子一致性。"""
    indexed: dict[str, ExchangeRule] = {}
    for rule in rules:
        if rule.rule_id in indexed:
            raise CategoryError("EXCHANGE_RULE_DUPLICATE", f"duplicate exchange rule {rule.rule_id}")
        contract.require_category(rule.category_id)
        _validate_operator_action(rule)
        indexed[rule.rule_id] = rule
    return indexed


def _validate_operator_action(rule: ExchangeRule) -> None:
    """@brief 拒绝算子与动作语义不一致的规则。"""
    allowed = {
        RuleOperator.PURCHASE_TRANSFER: {Action.PURCHASE},
        RuleOperator.FOOD_ENERGY_SOURCE: {Action.CONSUME_EAT},
        RuleOperator.DURABLE_USE: {Action.CONSUME_USE},
        RuleOperator.TRAVEL_FARE: {Action.TRAVEL_WALKING, Action.TRAVEL_DRIVING},
        RuleOperator.WORK_HOURLY: {Action.WORK_SETTLE},
        RuleOperator.WORK_PIECE: {Action.WORK_SETTLE},
        RuleOperator.WORK_PACKAGE: {Action.WORK_SETTLE},
        RuleOperator.INVENTORY_TRANSFORM: {Action.CONSUME_USE},
    }
    if rule.action not in allowed[rule.operator]:
        raise CategoryError(
            "EXCHANGE_RULE_ACTION_MISMATCH",
            f"operator {rule.operator.value} cannot implement action {rule.action.value}",
        )


def _validate_pairing_references(
    contract: CategoryContract,
    rules: Mapping[str, ExchangeRule],
) -> None:
    """@brief 要求每条 defined 配对引用目标品类和动作一致的现有规则。"""
    for pairing in contract.pairings:
        if pairing.rule_id is None:
            continue
        rule = rules.get(pairing.rule_id)
        if rule is None:
            raise CategoryError(
                "PAIRING_RULE_UNKNOWN", f"pairing references unknown rule {pairing.rule_id}",
            )
        if rule.category_id != pairing.target_category_id or rule.action != pairing.action:
            raise CategoryError(
                "PAIRING_RULE_MISMATCH",
                f"pairing rule {rule.rule_id} has another target category or action",
            )
