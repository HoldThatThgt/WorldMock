"""@brief 静态资源注册表与交换对象的封闭数据模型。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

from pydantic import ConfigDict, Field, StrictInt, model_validator

from worldmock.categories import (
    AttributeValues,
    CategoryContract,
    CategoryError,
    ExchangeRule,
    ExchangeRuleSet,
    PairingResolution,
    PairingState,
    SourceRecord,
    resolve_pairing,
)
from worldmock.categories.common import FrozenModel

LOGGER = logging.getLogger(__name__)


class CatalogError(ValueError):
    """@brief 目录加载或构建失败，携带稳定英文错误码。"""

    def __init__(self, code: str, message: str):
        """@brief 以稳定错误码和英文详情构造目录故障。

        :param code: 稳定英文错误码，如 CATALOG_VERSION_NOT_MONOTONIC。
        :param message: 面向维护者的英文失败详情。
        """
        self.code = code
        self.message = message
        LOGGER.error("%s: %s", code, message)
        super().__init__(f"{code}: {message}")


class CatalogModel(FrozenModel):
    """@brief 拒绝未知字段并深度冻结目录条目的模型基类。"""


class ProvenanceType(StrEnum):
    """@brief 静态数值来源的四分类。"""

    FACT = "fact"
    ANCHORED = "anchored"
    ASSUMPTION = "assumption"
    SCENARIO = "scenario"


class ResourceKind(StrEnum):
    """@brief 资源的存量型与账户型形态。"""

    LEVEL = "level"
    ACCOUNT = "account"


class Conservation(StrEnum):
    """@brief 守恒域声明：开放（有源有漏）或封闭（零和）。"""

    OPEN = "open"
    CLOSED = "closed"


class ObjectKind(StrEnum):
    """@brief 交换对象的封闭类型。"""

    PRODUCT = "product"
    SERVICE = "service"
    CONTRACT = "contract"


class CatalogEntryKind(StrEnum):
    """@brief 目录条目的命名空间，墓碑按它定位目标。"""

    RESOURCE = "resource"
    OBJECT = "object"


def is_role_placeholder(endpoint: str) -> bool:
    """@brief 判断边端点是否为角色占位（形如 ``{buyer}``）。

    占位端点由场景声明的账户实例按 ``account_roles`` 绑定，不直接引用目录资源。
    """
    return endpoint.startswith("{") and endpoint.endswith("}")


class Provenance(CatalogModel):
    """@brief 溯源数组的一个元素：某数值部分来自哪个来源、落在哪些字段路径。"""

    part: str = Field(default="definition", pattern=r"\S")
    type: ProvenanceType
    source: str = Field(pattern=r"\S")
    artifact: str | None = Field(default=None, pattern=r"\S")
    method: str | None = Field(default=None, pattern=r"\S")
    paths: tuple[str, ...] = ()
    note: str | None = None


class ResourceBounds(CatalogModel):
    """@brief 存量型资源的硬上下界，max 为 null 表示无上界。"""

    min: float
    max: float | None = None

    @model_validator(mode="after")
    def validate_span(self) -> "ResourceBounds":
        """@brief 要求上界不小于下界。"""
        if self.max is not None and self.min > self.max:
            raise ValueError("resource bounds max must not be below min")
        return self


class AlertRule(CatalogModel):
    """@brief 资源越过阈值时抛出的警报规则。"""

    below: float | None = None
    above: float | None = None
    code: str = Field(pattern=r"\S")

    @model_validator(mode="after")
    def validate_threshold(self) -> "AlertRule":
        """@brief 要求恰好声明一个阈值方向。"""
        if (self.below is None) == (self.above is None):
            raise ValueError("alert rule requires exactly one of below or above")
        return self


class CurvePoint(CatalogModel):
    """@brief 效率/饱腹曲线上的一个插值点。"""

    input_value: float
    output_value: float


class DrainRules(CatalogModel):
    """@brief level 资源的动态参数：基础代谢、MET、曲线与恢复速率等。

    fact 型（basal/MET）与 assumption 型（曲线/恢复速率）参数同居于此，
    各自的溯源落在资源级溯源数组的对应 ``paths`` 上。
    """

    basal_kcal_per_hour: float | None = None
    activity_met: Mapping[str, float] | None = None
    food_efficiency_curve: tuple[CurvePoint, ...] | None = None
    awake_sleep_debt_per_hour: float | None = None
    sleep_recovery_per_hour: float | None = None
    entertainment_need_per_awake_hour: float | None = None
    entertainment_relief_per_hour: Mapping[str, float] | None = None


class EfficiencyFactor(CatalogModel):
    """@brief 声明式效率因子：一个输入资源名与其分段线性输出乘数曲线。

    引擎不感知任何具体因子：效率 = 各因子按其 ``input_resource`` 当前区间
    取值后的连乘。新增因果边（如"能量饥饿拖累效率"）只是目录里多一行因子。
    """

    input_resource: str = Field(pattern=r"\S")
    curve: tuple[CurvePoint, ...] = Field(min_length=1)


class ResourceDefinition(CatalogModel):
    """@brief 资源注册表中一条类型级资源的完整定义。"""

    resource_id: str = Field(pattern=r"\S")
    kind: ResourceKind
    unit: str = Field(pattern=r"\S")
    owner: str = Field(pattern=r"\S")
    bounds: ResourceBounds | None = None
    conservation: Conservation
    alert_rules: tuple[AlertRule, ...] = ()
    drain_rules: DrainRules | None = None
    provenance: tuple[Provenance, ...] = Field(min_length=1)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_shape(self) -> "ResourceDefinition":
        """@brief 要求存量型资源声明硬上下界且溯源 part 去重。"""
        if self.kind == ResourceKind.LEVEL and self.bounds is None:
            raise ValueError("level resources must declare bounds")
        parts = [entry.part for entry in self.provenance]
        if len(set(parts)) != len(parts):
            raise ValueError("resource provenance parts must be unique")
        return self


class AccountRoles(CatalogModel):
    """@brief 占位端点的角色标注：场景按角色名绑定账户实例。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_role: str = Field(alias="from", pattern=r"\S")
    to_role: str = Field(alias="to", pattern=r"\S")


class TransferEdge(CatalogModel):
    """@brief 守恒域内账户到账户的转账边（端点可为角色占位）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    on: str = Field(pattern=r"\S")
    type: Literal["transfer"]
    from_account: str = Field(alias="from", pattern=r"\S")
    to_account: str = Field(alias="to", pattern=r"\S")
    resource: str = Field(pattern=r"\S")
    amount_fen: Annotated[int, Field(ge=0)]
    account_roles: AccountRoles | None = None
    unit: str | None = Field(default=None, pattern=r"\S")

    @model_validator(mode="after")
    def validate_endpoints(self) -> "TransferEdge":
        """@brief 要求两端不同，且占位端点必须携带角色标注。"""
        if self.from_account == self.to_account:
            raise ValueError("transfer edge endpoints must differ")
        if (is_role_placeholder(self.from_account) or is_role_placeholder(self.to_account)) \
                and self.account_roles is None:
            raise ValueError("role placeholder endpoints require account_roles")
        return self


class SourceEdge(CatalogModel):
    """@brief 向开放域资源池注入的边（amount 为 0 是合法事实）。"""

    on: str = Field(pattern=r"\S")
    type: Literal["source"]
    resource: str = Field(pattern=r"\S")
    amount: Annotated[float, Field(ge=0)]
    duration_s: int | None = Field(default=None, ge=1)
    unit: str | None = Field(default=None, pattern=r"\S")


class DrainEdge(CatalogModel):
    """@brief 从开放域资源池消耗的边。"""

    on: str = Field(pattern=r"\S")
    type: Literal["drain"]
    resource: str = Field(pattern=r"\S")
    amount: Annotated[float, Field(ge=0)]
    duration_s: int | None = Field(default=None, ge=1)
    unit: str | None = Field(default=None, pattern=r"\S")


class RuleEdge(CatalogModel):
    """@brief 编译对象对动态闭集规则的一条 typed 引用边。"""

    on: str = Field(pattern=r"\S")
    type: Literal["rule"]
    rule_id: str = Field(pattern=r"\S")
    coefficient: int | float = Field(ge=0, allow_inf_nan=False)


CompiledEdge = Annotated[
    TransferEdge | SourceEdge | DrainEdge | RuleEdge,
    Field(discriminator="type"),
]


class Serving(CatalogModel):
    """@brief 一份交换对象份量的数量与单位。"""

    quantity: float = Field(gt=0)
    unit: str = Field(pattern=r"\S")


class Acceptance(CatalogModel):
    """@brief 合同对象的验收门槛声明。"""

    required_effective_seconds: int | None = Field(default=None, ge=0)
    materials_fully_revealed: bool = False
    review_pass_required: bool = False


class Efficiency(CatalogModel):
    """@brief 合同对象的工作效率参数：声明式因子连乘加笔记倍率。"""

    work_efficiency_factors: tuple[EfficiencyFactor, ...] = Field(min_length=1)
    work_notes_multiplier: float = Field(gt=0)


class ObjectDeclaration(CatalogModel):
    """@brief `objects.jsonl` 中禁止 edges 与公式的原始对象增量。"""

    object_id: str = Field(pattern=r"\S")
    name: str = Field(pattern=r"\S")
    category_id: str = Field(pattern=r"\S")
    values: AttributeValues = Field(default_factory=AttributeValues)
    provenance: tuple[SourceRecord, ...] = Field(min_length=1)
    version: StrictInt = Field(ge=1)


class ExchangeObject(CatalogModel):
    """@brief 原始对象与冻结品类规则编译出的现有运行期有效视图。"""

    object_id: str = Field(pattern=r"\S")
    name: str = Field(pattern=r"\S")
    category_id: str = Field(pattern=r"\S")
    object_kind: ObjectKind
    attributes: Mapping[str, Any]
    effect: str | None = Field(default=None, pattern=r"\S")
    consumable: bool | None = None
    use_modes: tuple[str, ...] = ()
    edges: tuple[CompiledEdge, ...] = ()
    use_duration_seconds: int | None = Field(default=None, ge=1)
    serving: Serving | None = None
    required_location_id: str | None = Field(default=None, pattern=r"\S")
    acceptance: Acceptance | None = None
    efficiency: Efficiency | None = None
    provenance: tuple[SourceRecord, ...] = Field(min_length=1)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_purchase_edges(self) -> "ExchangeObject":
        """@brief 商品购买仅保留动态品类规则，不允许嵌入门店购买金额。"""
        if self.object_kind == ObjectKind.PRODUCT and any(
                edge.on == "purchase" and not isinstance(edge, RuleEdge) for edge in self.edges):
            LOGGER.error("Product definitions must not contain purchase price edges")
            raise ValueError("Product definitions must not contain purchase price edges")
        return self

    def attribute(self, name: str) -> Any:
        """@brief 返回一个已解析属性，不存在时明确失败。

        :param name: 槽位身份。
        :returns: 槽位有效值。
        """
        if name not in self.attributes:
            raise CatalogError(
                "CATALOG_ATTRIBUTE_MISSING", f"object {self.object_id} lacks attribute {name}",
            )
        return self.attributes[name]


class Tombstone(CatalogModel):
    """@brief 一条条目的弃用标记：保留可读，拒绝新场景引用。"""

    target_id: str = Field(pattern=r"\S")
    kind: CatalogEntryKind
    at_version: int = Field(ge=1)
    reason: str = Field(pattern=r"\S")
    superseded_by: str | None = Field(default=None, pattern=r"\S")


class MealRequirements(CatalogModel):
    """@brief 正餐品类的版本化要求：数量、价格与时长窗口、允许的热量数据库。"""

    min_count: int = Field(ge=1)
    price_fen_min: int = Field(ge=0)
    price_fen_max: int = Field(ge=1)
    duration_minutes_min: int = Field(ge=1)
    duration_minutes_max: int = Field(ge=1)
    energy_databases: tuple[str, ...] = Field(min_length=1)


class ContentMetadataRequirements(CatalogModel):
    """@brief 内容元数据品类的版本化要求：必备字段与白领场景年龄上限。"""

    fields: tuple[str, ...] = Field(min_length=1)
    max_age_rating: int = Field(ge=0)


class WorkMaterialRequirements(CatalogModel):
    """@brief 工作材料品类的版本化要求：去重下限、语言与模拟时域。"""

    min_distinct: int = Field(ge=1)
    language: str = Field(pattern=r"\S")
    horizon_days: int = Field(ge=1)


class ContactRequirements(CatalogModel):
    """@brief 联系人品类的版本化要求：每人物下限与两类回复策略。"""

    min_per_persona: int = Field(ge=1)
    colleague_reply_policy: str = Field(pattern=r"\S")
    other_reply_policy: str = Field(pattern=r"\S")


class PantryRequirements(CatalogModel):
    """@brief 家的可供性版本化要求：早餐储备份数与晨间洗漱时长。"""

    breakfast_servings_min: int = Field(ge=1)
    morning_wash_minutes: int = Field(ge=1)


class CategoryRequirements(CatalogModel):
    """@brief 修订合同《世界素材品类要求》的版本化声明（随目录版本冻结）。"""

    declared_at: str = Field(pattern=r"\S")
    spec: str = Field(pattern=r"\S")
    meals: MealRequirements
    location_required_kinds: tuple[str, ...] = Field(min_length=1)
    location_min_per_kind: Mapping[str, int] = Field(default_factory=dict)
    home_pantry: PantryRequirements
    office_affordances: tuple[str, ...] = Field(min_length=1)
    content_metadata: ContentMetadataRequirements
    work_materials: WorkMaterialRequirements
    lifestyle_fields: tuple[str, ...] = Field(min_length=1)
    contacts: ContactRequirements


class CatalogManifest(CatalogModel):
    """@brief 目录版本、品类内容身份与世界素材要求的冻结清单。"""

    version_major: int = Field(ge=1)
    version_minor: int = Field(ge=0)
    category_contract_version: int = Field(ge=1)
    category_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    exchange_rules_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    category_requirements: CategoryRequirements | None = None


@dataclass(frozen=True)
class Catalog:
    """@brief 目录加载后的冻结只读视图。"""

    version_major: int
    version_minor: int
    category_contract_version: int
    category_contract_sha256: str
    exchange_rules_sha256: str
    catalog_content_sha256: str
    resources: Mapping[str, ResourceDefinition]
    raw_objects: Mapping[str, ObjectDeclaration]
    objects: Mapping[str, ExchangeObject]
    tombstones: Mapping[str, Tombstone]
    category_contract: CategoryContract
    exchange_rules: ExchangeRuleSet
    source_dir: Path
    travel_time_rules_bytes: bytes
    category_requirements: CategoryRequirements | None = None

    def tombstoned_ids(self) -> frozenset[str]:
        """@brief 返回全部墓碑目标 id 的冻结集合。"""
        return frozenset(self.tombstones)

    def pairing(
        self,
        actor_category_id: str,
        target_category_id: str,
        action: str,
    ) -> PairingResolution:
        """@brief 查询目录冻结契约中的唯一最具体有向配对。"""
        return resolve_pairing(self.category_contract, actor_category_id, target_category_id, action)

    def rule_for(
        self,
        actor_category_id: str,
        target_category_id: str,
        action: str,
    ) -> tuple[ExchangeRule, int | float]:
        """@brief 返回 defined 配对的 typed rule 与显式系数。"""
        pairing = self.pairing(actor_category_id, target_category_id, action)
        if pairing.state != PairingState.DEFINED:
            code = "PAIRING_NO_EFFECT" if pairing.state == PairingState.NO_EFFECT else "PAIRING_UNDEFINED"
            raise CategoryError(code, f"pairing {actor_category_id}->{target_category_id} {action} is {pairing.state}")
        assert pairing.rule_id is not None and pairing.coefficient is not None
        return self.exchange_rules.require(pairing.rule_id), pairing.coefficient
