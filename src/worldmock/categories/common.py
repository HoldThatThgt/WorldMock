"""@brief 品类契约共用的严格枚举、来源与审核模型。"""

from __future__ import annotations

import logging
import math
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import Field, model_validator
from worldmock.base_models import DeepFrozenModel as _DeepFrozenModel

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


class SourceRecord(_DeepFrozenModel):
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


class ReviewRecord(_DeepFrozenModel):
    """@brief 品类结构或规则发布的一份可审计审核记录。"""

    reviewer: str = Field(pattern=r"\S")
    reviewer_type: ReviewerType
    evidence: str = Field(pattern=r"\S")
    reason: str = Field(pattern=r"\S")
    authorization: str = Field(pattern=r"\S")
