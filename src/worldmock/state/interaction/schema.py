"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.foundation.models import FrozenModel
from collections.abc import Callable
from typing import Any
from pydantic import BaseModel, ConfigDict











def purpose_only_schema(purpose: str | None = None) -> Callable[[dict[str, Any]], None]:
    """@brief 模型可见参数 Schema 的 description 只写用途（docs/design/06-agent.md「提示与工具」）。

    去掉类 docstring 派生的开发说明（规则只在系统层写一次，数量上限由 Schema 与本地校验执行），purpose 非空时写这一句；
    工具参数对象本身不写说明，用途只在工具的 description。用作 ConfigDict 的 json_schema_extra。
    """

    def apply(schema: dict[str, Any]) -> None:
        """@brief 就地替换一个对象 Schema 的 description。"""
        schema.pop("description", None)
        if purpose is not None:
            schema["description"] = purpose

    return apply



class ToolRequestModel(FrozenModel):
    """@brief 工具参数严格保持 JSON 类型，不影响其他领域输入。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True,
                              json_schema_extra=purpose_only_schema())



_DEEPSEEK_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "oneOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "default",
})



def deepseek_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """@brief 直接生成并核验 DeepSeek strict 接受的 Pydantic Schema。

    :param model: 唯一工具参数模型。
    :returns: 未经过删除或改写的模型验证 Schema。
    """
    schema = model.model_json_schema(mode="validation")
    _validate_deepseek_schema_node(schema, "$")
    return schema



def _validate_deepseek_schema_node(value: Any, path: str) -> None:
    """@brief 递归拒绝服务端不支持或对象字段不完整的 Schema。"""
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_deepseek_schema_node(item, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    unsupported = sorted(set(value) & _DEEPSEEK_UNSUPPORTED_SCHEMA_KEYS)
    if unsupported:
        raise ValueError(f"DeepSeek strict schema uses unsupported keys at {path}: {unsupported}")
    if value.get("type") == "object":
        _validate_deepseek_object(value, path)
    for key, item in value.items():
        _validate_deepseek_schema_node(item, f"{path}.{key}")



def _validate_deepseek_object(value: dict[str, Any], path: str) -> None:
    """@brief 要求每个对象封闭且全部属性显式必填。"""
    properties = value.get("properties")
    required = value.get("required")
    if not isinstance(properties, dict) or set(required or []) != set(properties):
        raise ValueError(f"DeepSeek strict object properties are not all required at {path}")
    if value.get("additionalProperties") is not False:
        raise ValueError(f"DeepSeek strict object is not closed at {path}")
