"""@brief 品类契约共用的严格枚举、来源与审核模型。"""

from __future__ import annotations

import logging
import math
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

LOGGER = logging.getLogger(__name__)


class CategoryError(ValueError):
    """@brief 品类契约、配对或闭集算子失败，携带稳定英文错误码。"""

    def __init__(self, code: str, message: str):
        """@brief 构造并记录一条品类契约错误。

        :param code: 稳定英文错误码。
        :param message: 面向维护者的英文详情。
        """
        self.code = code
        self.message = message
        LOGGER.error("%s: %s", code, message)
        super().__init__(f"{code}: {message}")


class FrozenModel(BaseModel):
    """@brief 拒绝未知字段并禁止字段重新绑定的模型基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def freeze_containers(self) -> "FrozenModel":
        """@brief 递归冻结模型字段内的映射与序列。"""
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            frozen = _freeze_container(value)
            if frozen is not value:
                object.__setattr__(self, field_name, frozen)
        return self

    @field_serializer("*", mode="wrap", check_fields=False)
    def serialize_containers(self, value: Any, handler: Any) -> Any:
        """@brief 序列化前还原只读容器为 Pydantic 支持的结构。"""
        return handler(_thaw_container(value))


class Action(StrEnum):
    """@brief 品类配对支持的封闭动作集合。"""

    PURCHASE = "purchase"
    CONSUME_EAT = "consume:eat"
    CONSUME_USE = "consume:use"
    WORK_SETTLE = "work:settle"
    TRAVEL_WALKING = "travel:walking"
    TRAVEL_DRIVING = "travel:driving"


class PairingState(StrEnum):
    """@brief 配对声明的三态。"""

    DEFINED = "defined"
    NO_EFFECT = "no_effect"
    UNDEFINED = "undefined"


class CategoryKind(StrEnum):
    """@brief 品类树节点的对象形态。"""

    ENTITY = "entity"
    PERSON = "person"
    PRODUCT = "product"
    SERVICE = "service"
    CONTRACT = "contract"


class Conservation(StrEnum):
    """@brief 算子声明的开放或封闭守恒域。"""

    OPEN = "open"
    CLOSED = "closed"


class ProvenanceType(StrEnum):
    """@brief 静态声明来源的四种证据类型。"""

    FACT = "fact"
    ANCHORED = "anchored"
    ASSUMPTION = "assumption"
    SCENARIO = "scenario"


class ProducerType(StrEnum):
    """@brief 来源记录的生产者类型。"""

    HUMAN = "human"
    DATA_SOURCE = "data_source"
    MODEL = "model"


class ReviewerType(StrEnum):
    """@brief 契约或规则审核者的身份类型。"""

    HUMAN = "human"
    DELEGATED_AGENT = "delegated_agent"


class SourceRecord(FrozenModel):
    """@brief 一份来源记录，可覆盖一个或多个 JSON Pointer 子树。"""

    source_id: str = Field(pattern=r"\S")
    type: ProvenanceType
    producer: ProducerType
    source: str = Field(pattern=r"\S")
    artifact: str | None = Field(default=None, pattern=r"\S")
    method: str | None = Field(default=None, pattern=r"\S")
    paths: tuple[str, ...] = Field(min_length=1)
    note: str | None = None

    @model_validator(mode="after")
    def validate_model_evidence(self) -> "SourceRecord":
        """@brief 禁止模型直接产出 fact，并收紧模型锚定证据。"""
        if self.producer == ProducerType.MODEL and self.type == ProvenanceType.FACT:
            raise ValueError("model producers cannot declare fact provenance")
        if self.producer == ProducerType.MODEL and self.type == ProvenanceType.ANCHORED:
            if self.artifact is None or self.method is None:
                raise ValueError("model anchored provenance requires artifact and method")
        if len(set(self.paths)) != len(self.paths):
            raise ValueError("source paths must be unique within a record")
        if any(not path.startswith("/") for path in self.paths):
            raise ValueError("source paths must be absolute JSON Pointers")
        return self


class ReviewRecord(FrozenModel):
    """@brief 品类结构或规则发布的一份可审计审核记录。"""

    reviewer: str = Field(pattern=r"\S")
    reviewer_type: ReviewerType
    evidence: str = Field(pattern=r"\S")
    reason: str = Field(pattern=r"\S")
    authorization: str | None = Field(default=None, pattern=r"\S")

    @model_validator(mode="after")
    def validate_authorization(self) -> "ReviewRecord":
        """@brief 委托审查 agent 必须记录用户授权来源。"""
        if self.reviewer_type == ReviewerType.DELEGATED_AGENT and self.authorization is None:
            raise ValueError("delegated agent review requires authorization")
        return self


def require_finite_number(value: Any, label: str) -> int | float:
    """@brief 要求输入为非布尔有限 JSON 数值。

    :param value: 待检查的值。
    :param label: 错误信息中的字段名。
    :returns: 原始整数或浮点数。
    :raises CategoryError: 值不是有限 JSON 数值。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CategoryError("CATEGORY_NUMBER_INVALID", f"{label} must be a JSON number")
    if isinstance(value, float) and not math.isfinite(value):
        raise CategoryError("CATEGORY_NUMBER_INVALID", f"{label} must be finite")
    return value


def _freeze_container(value: Any) -> Any:
    """@brief 递归冻结原始声明中的容器，保留 Pydantic 模型实例。"""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_container(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_container(item) for item in value)
    return value


def _thaw_container(value: Any) -> Any:
    """@brief 把只读容器还原为可序列化的普通容器。"""
    if isinstance(value, Mapping):
        return {key: _thaw_container(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw_container(item) for item in value)
    return value
