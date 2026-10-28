"""@brief 普通领域模型的唯一严格与字段冻结基类，以及公共非负曲线。"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator
from worldmock.foundation.errors import reject_value


class StrictModel(BaseModel):
    """@brief 拒绝未知字段，允许领域状态更新。"""

    model_config = ConfigDict(extra="forbid", frozen=False)


class FrozenModel(StrictModel):
    """@brief 拒绝未知字段并禁止字段重新绑定，不递归冻结容器。"""

    model_config = ConfigDict(frozen=True)


class CurvePoint(FrozenModel):
    """@brief 有限非负的曲线输入输出节点，布尔不能冒充数值。"""

    input_value: float = Field(ge=0, strict=True, allow_inf_nan=False)
    output_value: float = Field(ge=0, strict=True, allow_inf_nan=False)


class EfficiencyFactor(FrozenModel):
    """@brief 非空白资源名称对应的一条非空效率曲线。"""

    input_resource: str = Field(pattern=r"\S")
    curve: tuple[CurvePoint, ...] = Field(min_length=1)


class DeepFrozenModel(BaseModel):
    """@brief 拒绝未知字段并禁止字段重新绑定的模型基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def freeze_containers(self) -> "DeepFrozenModel":
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


def require_finite_number(value: Any, label: str) -> int | float:
    """@brief 要求输入为非布尔有限 JSON 数值。

    :param value: 待检查的值。
    :param label: 错误信息中的字段名。
    :returns: 原始整数或浮点数。
    :raises ValueError: 值不是有限 JSON 数值。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        reject_value(f"{label} must be a JSON number")
    if isinstance(value, float) and not math.isfinite(value):
        reject_value(f"{label} must be finite")
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
