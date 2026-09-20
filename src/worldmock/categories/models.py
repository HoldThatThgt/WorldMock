"""@brief 品类契约的原始文档模型与冻结解析视图。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from worldmock.categories.common import (
    Action,
    CategoryKind,
    FrozenModel,
    PairingState,
    ReviewRecord,
    SourceRecord,
    require_finite_number,
)


class AttributeValues(FrozenModel):
    """@brief 一个品类层或对象层的绝对值、加法增量与乘法增量。"""

    absolute: Mapping[str, Any] = Field(default_factory=dict)
    relative: Mapping[str, int | float] = Field(default_factory=dict)
    proportional: Mapping[str, int | float] = Field(default_factory=dict)

    @field_validator("relative", "proportional", mode="before")
    @classmethod
    def validate_numeric_inputs(cls, values: Any) -> Any:
        """@brief 在 Pydantic 转换前拒绝布尔和字符串冒充数值。"""
        if isinstance(values, Mapping):
            for slot, value in values.items():
                require_finite_number(value, f"attribute delta {slot}")
        return values

    @model_validator(mode="after")
    def validate_operations(self) -> "AttributeValues":
        """@brief 同层同槽只允许一种操作，数值增量必须有限。"""
        groups = (set(self.absolute), set(self.relative), set(self.proportional))
        duplicates = (groups[0] & groups[1]) | (groups[0] & groups[2]) | (groups[1] & groups[2])
        if duplicates:
            raise ValueError(f"attribute operations conflict for slots: {sorted(duplicates)}")
        if any(not key.strip() for group in groups for key in group):
            raise ValueError("attribute slot names must not be blank")
        for operation, values in (("relative", self.relative), ("proportional", self.proportional)):
            for slot, value in values.items():
                require_finite_number(value, f"{operation}.{slot}")
        return self


class AttributeSlot(FrozenModel):
    """@brief 一个属性槽位的 JSON Schema、必填性与单位说明。"""

    schema_: Mapping[str, Any] = Field(alias="schema")
    required: StrictBool = False
    unit: str | None = Field(default=None, pattern=r"\S")

    model_config = FrozenModel.model_config | {"populate_by_name": True}


class CategoryNode(FrozenModel):
    """@brief 品类树中的一个原始单继承节点。"""

    category_id: str = Field(pattern=r"\S")
    name: str = Field(pattern=r"\S")
    parent_id: str | None = Field(default=None, pattern=r"\S")
    kind: CategoryKind | None = None
    slots: Mapping[str, AttributeSlot] = Field(default_factory=dict)
    values: AttributeValues = Field(default_factory=AttributeValues)


class PairingDeclaration(FrozenModel):
    """@brief 一条有向品类配对的显式三态声明。"""

    actor_category_id: str = Field(pattern=r"\S")
    target_category_id: str = Field(pattern=r"\S")
    action: Action
    state: PairingState
    rule_id: str | None = Field(default=None, pattern=r"\S")
    coefficient: int | float | None = None
    review: ReviewRecord | None = None

    @field_validator("coefficient", mode="before")
    @classmethod
    def validate_coefficient_input(cls, value: Any) -> Any:
        """@brief 在转换前要求系数为有限 JSON 数值或空值。"""
        return value if value is None else require_finite_number(value, "pairing coefficient")

    @model_validator(mode="after")
    def validate_state_shape(self) -> "PairingDeclaration":
        """@brief defined、no_effect 与 undefined 各自只接受合法字段组合。"""
        if self.state == PairingState.DEFINED:
            if self.rule_id is None or self.coefficient is None:
                raise ValueError("defined pairing requires rule_id and explicit coefficient")
            value = require_finite_number(self.coefficient, "pairing coefficient")
            if value < 0:
                raise ValueError("pairing coefficient must be non-negative")
        elif self.rule_id is not None or self.coefficient is not None:
            raise ValueError("non-defined pairing cannot carry rule_id or coefficient")
        if self.state == PairingState.NO_EFFECT and self.review is None:
            raise ValueError("no_effect pairing requires review evidence")
        return self


class CategoryContractDocument(FrozenModel):
    """@brief `category-contract.json` 的严格原始模型。"""

    format_version: StrictInt = Field(ge=1, le=1)
    contract_version: StrictInt = Field(ge=1)
    categories: tuple[CategoryNode, ...] = Field(min_length=1)
    pairings: tuple[PairingDeclaration, ...] = ()
    sources: tuple[SourceRecord, ...] = Field(min_length=1)
    review: ReviewRecord


@dataclass(frozen=True)
class ResolvedSlot:
    """@brief 一个槽位沿祖先链累积后的全部约束。"""

    slot_id: str
    schemas: tuple[Mapping[str, Any], ...]
    required: bool
    unit: str | None


@dataclass(frozen=True)
class ResolvedCategory:
    """@brief 一个品类节点的只读有效形态与属性值。"""

    category_id: str
    name: str
    parent_id: str | None
    kind: CategoryKind
    slots: Mapping[str, ResolvedSlot]
    attributes: Mapping[str, Any]
    lineage: tuple[str, ...]


@dataclass(frozen=True)
class CategoryContract:
    """@brief 已完成树、槽位、来源和配对结构校验的冻结契约。"""

    contract_version: int
    categories: Mapping[str, ResolvedCategory]
    declarations: Mapping[str, CategoryNode]
    pairings: tuple[PairingDeclaration, ...]
    sources: tuple[SourceRecord, ...]
    review: ReviewRecord

    def require_category(self, category_id: str) -> ResolvedCategory:
        """@brief 返回已解析品类，不存在时抛出稳定错误。

        :param category_id: 待查询的品类身份。
        :returns: 对应只读解析节点。
        """
        from worldmock.categories.common import CategoryError

        category = self.categories.get(category_id)
        if category is None:
            raise CategoryError("CATEGORY_UNKNOWN", f"category {category_id} is not declared")
        return category

    def is_descendant(self, category_id: str, ancestor_id: str) -> bool:
        """@brief 判断一个品类是否等于或继承自指定祖先。

        :param category_id: 待判断品类。
        :param ancestor_id: 候选祖先品类。
        :returns: 候选祖先是否出现在根到自身谱系中。
        """
        category = self.require_category(category_id)
        self.require_category(ancestor_id)
        return ancestor_id in category.lineage


class PairingResolution(FrozenModel):
    """@brief 一次配对查询的三态结果与实际命中声明。"""

    state: PairingState
    declaration: PairingDeclaration | None

    @property
    def rule_id(self) -> str | None:
        """@brief 返回 defined 配对引用的规则身份。"""
        return self.declaration.rule_id if self.declaration is not None else None

    @property
    def coefficient(self) -> int | float | None:
        """@brief 返回 defined 配对声明的显式系数。"""
        return self.declaration.coefficient if self.declaration is not None else None


@dataclass(frozen=True)
class ResolvedObject:
    """@brief 原始对象增量应用到品类后得到的只读对象值。"""

    object_id: str
    name: str
    category_id: str
    kind: CategoryKind
    attributes: Mapping[str, Any]
    provenance: tuple[SourceRecord, ...]
    version: int
