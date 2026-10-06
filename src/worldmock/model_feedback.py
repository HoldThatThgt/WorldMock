"""@brief 模型协议纠正消息、Schema 错误与工具调用参数的共享生成规则。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from worldmock.footprints.models import canonical_json
from worldmock.errors import WorldMockError


def correction_message(previous: Any, error: Exception, instruction: str) -> dict:
    """@brief 生成唯一紧凑 JSON 附回消息，业务自行选择回显和指令。"""
    content = {"PREVIOUS_INVALID_RESPONSE": previous, "VALIDATION_ERROR": str(error),
               "INSTRUCTION": instruction}
    return {"role": "user", "content": canonical_json(content).decode("utf-8")}


def schema_error_details(error: ValidationError, root: str = "$") -> list[dict]:
    """@brief 提取稳定的字段路径、类型和英文原因，不回传输入或上下文。"""
    return [{"field": ".".join(str(part) for part in item["loc"]) or root,
             "type": item["type"], "message": item["msg"]}
            for item in error.errors(include_url=False, include_context=False, include_input=False)]


def schema_feedback(error: ValidationError, root: str) -> str:
    """@brief 将同源 Schema 错误压成字段与原因组成的一行文字。"""
    return "; ".join(f"{item['field']}: {item['message']}" for item in schema_error_details(error, root))


def _field(value: Any, name: str) -> Any:
    """@brief 读取实际响应对象或已序列化响应的同一字段。"""
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)




def tool_arguments(calls: Any, tool: str, error_type: type[Exception]) -> str:
    """@brief 核对唯一指定工具调用并提取原始 JSON 参数。"""
    message = None
    if not isinstance(calls, list) or len(calls) != 1:
        message = f"response must contain exactly one {tool} tool call"
    else:
        function = _field(calls[0], "function")
        arguments = _field(function, "arguments")
        if _field(function, "name") != tool:
            message = f"the only tool call must be {tool}"
        elif not isinstance(arguments, str):
            message = f"{tool} arguments must be a JSON string"
        else:
            return arguments
    if not issubclass(error_type, WorldMockError):
        logging.getLogger(__name__).error("%s", message)
    raise error_type(message)
