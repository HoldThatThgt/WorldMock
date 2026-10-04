"""@brief 普通领域模型的唯一严格与字段冻结基类，以及公共非负曲线。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
