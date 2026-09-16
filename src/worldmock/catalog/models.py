"""@brief 静态资源注册表与交换对象的封闭数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CatalogError(ValueError):
    """@brief 目录加载或构建失败，携带稳定英文错误码。"""

    def __init__(self, code: str, message: str):
        """@brief 以稳定错误码和英文详情构造目录故障。

        :param code: 稳定英文错误码，如 CATALOG_VERSION_NOT_MONOTONIC。
        :param message: 面向维护者的英文失败详情。
        """
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class CatalogModel(BaseModel):
    """@brief 拒绝未知字段的目录条目模型基类。"""

    model_config = ConfigDict(extra="forbid")


class ProvenanceType(StrEnum):
    """@brief 静态数值来源的四分类。"""

    FACT = "fact"
    ANCHORED = "anchored"
    ASSUMPTION = "assumption"
    SCENARIO = "scenario"


class ResourceKind(StrEnum):
    """@brief 资源的两种形态：存量型与账户型。"""

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
    FORMULA = "formula"


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


class FormulaBinding(CatalogModel):
    """@brief 边金额对目录公式声明的引用绑定（公式 id + 入参路径）。"""

    formula_ref: str = Field(pattern=r"\S")
    params: Mapping[str, str] = Field(default_factory=dict)


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
    amount_fen: Annotated[int, Field(gt=0)] | FormulaBinding
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
    amount: Annotated[float, Field(ge=0)] | FormulaBinding
    duration_s: int | None = Field(default=None, ge=1)
    unit: str | None = Field(default=None, pattern=r"\S")


class DrainEdge(CatalogModel):
    """@brief 从开放域资源池消耗的边。"""

    on: str = Field(pattern=r"\S")
    type: Literal["drain"]
    resource: str = Field(pattern=r"\S")
    amount: Annotated[float, Field(ge=0)] | FormulaBinding
    duration_s: int | None = Field(default=None, ge=1)
    unit: str | None = Field(default=None, pattern=r"\S")


class ConvertEdge(CatalogModel):
    """@brief 时间与资源到资源的折算边（合同结算：端点可为账户占位）。

    端点是字面资源 id 时按资源折算（能量→货币）；是账户占位时按角色绑定
    账户实例，结算公式以内联表达式声明（参数取自任务实例）。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    on: str = Field(pattern=r"\S")
    type: Literal["convert"]
    from_endpoint: str = Field(alias="from", pattern=r"\S")
    to_endpoint: str = Field(alias="to", pattern=r"\S")
    amount: Annotated[float, Field(gt=0)] | None = None
    formula: str | None = Field(default=None, pattern=r"\S")
    account_roles: AccountRoles | None = None
    unit: str | None = Field(default=None, pattern=r"\S")

    @model_validator(mode="after")
    def validate_amount(self) -> "ConvertEdge":
        """@brief 要求静态数量与内联公式恰好取其一且端点合法。"""
        if (self.amount is None) == (self.formula is None):
            raise ValueError("convert edge requires exactly one of amount or formula")
        if self.from_endpoint == self.to_endpoint:
            raise ValueError("convert edge endpoints must differ")
        if (is_role_placeholder(self.from_endpoint) or is_role_placeholder(self.to_endpoint)) \
                and self.account_roles is None:
            raise ValueError("role placeholder endpoints require account_roles")
        return self


Edge = Annotated[
    TransferEdge | SourceEdge | DrainEdge | ConvertEdge,
    Field(discriminator="type"),
]


class Stock(CatalogModel):
    """@brief 交换对象的库存初值与补货策略。"""

    initial: float = Field(ge=0)
    restock: str = Field(default="none", pattern=r"\S")


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
    """@brief 合同对象的工作效率参数。"""

    work_efficiency_curve: tuple[CurvePoint, ...] = Field(min_length=1)
    work_notes_multiplier: float = Field(gt=0)


class ExchangeObject(CatalogModel):
    """@brief 一条交换对象条目：全部经济语义为一组边。"""

    object_id: str = Field(pattern=r"\S")
    name: str = Field(pattern=r"\S")
    object_kind: ObjectKind
    effect: str | None = Field(default=None, pattern=r"\S")
    legacy_effect: str | None = Field(default=None, pattern=r"\S")
    consumable: bool | None = None
    use_modes: tuple[str, ...] = ()
    edges: tuple[Edge, ...] = Field(min_length=1)
    stock: Stock | None = None
    use_duration_seconds: int | None = Field(default=None, ge=1)
    delivery_delay_seconds: int | None = Field(default=None, ge=1)
    serving: Serving | None = None
    required_location_id: str | None = Field(default=None, pattern=r"\S")
    formula_ref: str | None = Field(default=None, pattern=r"\S")
    acceptance: Acceptance | None = None
    efficiency: Efficiency | None = None
    provenance: tuple[Provenance, ...] = Field(min_length=1)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_provenance_coverage(self) -> "ExchangeObject":
        """@brief 要求每条边至少有一条去重后的溯源标注。"""
        parts = [entry.part for entry in self.provenance]
        if len(set(parts)) != len(parts):
            raise ValueError("object provenance parts must be unique")
        if len(self.provenance) < len(self.edges):
            raise ValueError("every edge requires at least one provenance entry")
        return self


class FormulaBranch(CatalogModel):
    """@brief 公式的一个分支：字面金额或待求值表达式。"""

    amount: int | str


class FormulaDeclaration(CatalogModel):
    """@brief rules/ 下 TOML 公式声明文件解析出的计费公式。"""

    formula_id: str = Field(pattern=r"\S")
    unit: str = Field(pattern=r"\S")
    branches: Mapping[str, FormulaBranch]
    params: Mapping[str, str] = Field(default_factory=dict)
    provenance: tuple[Provenance, ...] = ()
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_branches(self) -> "FormulaDeclaration":
        """@brief 公式至少要有一个分支。"""
        if not self.branches:
            raise ValueError("formula declaration requires at least one branch")
        return self


class Tombstone(CatalogModel):
    """@brief 一条条目的弃用标记：保留可读，拒绝新场景引用。"""

    target_id: str = Field(pattern=r"\S")
    kind: CatalogEntryKind
    at_version: int = Field(ge=1)
    reason: str = Field(pattern=r"\S")
    superseded_by: str | None = Field(default=None, pattern=r"\S")


class CatalogManifest(CatalogModel):
    """@brief 目录版本清单：major 语义变更，minor 纯增量。"""

    version_major: int = Field(ge=1)
    version_minor: int = Field(ge=0)


@dataclass(frozen=True)
class Catalog:
    """@brief 目录加载后的冻结只读视图。"""

    version_major: int
    version_minor: int
    resources: Mapping[str, ResourceDefinition]
    objects: Mapping[str, ExchangeObject]
    formulas: Mapping[str, FormulaDeclaration]
    tombstones: Mapping[str, Tombstone]

    @staticmethod
    def build(
        manifest: CatalogManifest,
        resources: dict[str, ResourceDefinition],
        objects: dict[str, ExchangeObject],
        formulas: dict[str, FormulaDeclaration],
        tombstones: dict[str, Tombstone],
    ) -> "Catalog":
        """@brief 用只读代理包装各映射，构造冻结视图。

        :param manifest: 已校验的目录版本清单。
        :param resources: 以 resource_id 为键的资源表。
        :param objects: 以 object_id 为键的对象表。
        :param formulas: 以 formula_id 为键的公式表。
        :param tombstones: 以 target_id 为键的墓碑表。
        :returns: 各映射不可再变更的冻结目录视图。
        """
        return Catalog(
            version_major=manifest.version_major,
            version_minor=manifest.version_minor,
            resources=MappingProxyType(resources),
            objects=MappingProxyType(objects),
            formulas=MappingProxyType(formulas),
            tombstones=MappingProxyType(tombstones),
        )

    def tombstoned_ids(self) -> frozenset[str]:
        """@brief 返回全部墓碑目标 id 的冻结集合。"""
        return frozenset(self.tombstones)
