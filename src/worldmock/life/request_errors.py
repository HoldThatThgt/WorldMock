"""@brief 宿主与环境从严格请求校验错误产生同一完整参数合同。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from worldmock.life.error_contract import ARGUMENT_ROOT, ErrorItem, ErrorReport, host_defaults

#: 严格请求模型可产生的类型与形状错误，统一解释具体 JSON 条件。
_VALIDATION_REASONS = {
    "missing": "This required field was omitted.",
    "extra_forbidden": "This field is not declared and must be removed.",
    "string_pattern_mismatch": "A non-blank string matching the declared format is required.",
    "literal_error": "The value is outside the declared allowed values.",
    "enum": "The value is outside the declared allowed values.",
    "string_type": "A JSON string is required.",
    "int_type": "A JSON integer is required.",
    "float_type": "A JSON number is required.",
    "bool_type": "A JSON boolean is required.",
    "list_type": "A JSON array is required.",
    "dict_type": "A JSON object is required.",
    "model_type": "A JSON object containing the declared fields is required.",
    "none_required": "This field must be null.",
    "finite_number": "A finite JSON number is required.",
}
#: 范围条件保留校验器提供的真实边界值。
_VALIDATION_BOUNDS = {
    "less_than_equal": ("at most", "le"), "greater_than_equal": ("at least", "ge"),
    "less_than": ("less than", "lt"), "greater_than": ("greater than", "gt"),
    "too_short": ("at least", "min_length"), "too_long": ("at most", "max_length"),
    "string_too_short": ("at least", "min_length"), "string_too_long": ("at most", "max_length"),
}


def argument_report(tool: str, error: ValidationError, model: type[BaseModel],
                    arguments: Any, code: str = "ARGUMENTS_INVALID") -> ErrorReport:
    """@brief 保留全部真实校验位置、原始 JSON、有限枚举与明确格式条件。

    :param tool: 被严格校验的工具名称。
    :param error: 严格请求模型产生的全部校验错误。
    :param model: 产生校验条件的实际请求模型。
    :param arguments: 提交的原始 JSON 参数。
    :param code: 本次校验在宿主或环境中的错误码。
    """
    schema = model.model_json_schema()
    items = []
    for row in error.errors(include_url=False):
        location = tuple(row["loc"])
        field = _schema_at(schema, location)
        allowed = _allowed(field)
        if tool == "start_activity" and location == ("prefer",):
            allowed = _purchase_preferences(arguments)
        written = arguments if not location else None if row["type"] == "missing" else row.get("input")
        items.append(ErrorItem(location or ARGUMENT_ROOT, written, code, tuple(allowed),
                               _expected(field, row), _why(row)))
    return ErrorReport(code, "; ".join(item.why for item in items),
                       host_defaults("ARGUMENTS_INVALID")[1], tuple(items))


def _schema_at(schema: dict, location: Sequence[Any]) -> dict:
    """@brief 从真实模型 Schema 沿完整位置解析字段，包括所有嵌套数组。"""
    current = schema
    for part in location:
        current = _resolve_schema(current, schema)
        current = current.get("items", {}) if isinstance(part, int) else current.get("properties", {}).get(part, {})
    return _resolve_schema(current, schema)


def _resolve_schema(node: dict, root: dict) -> dict:
    """@brief 解析 Pydantic 的局部 Schema 引用。"""
    if "$ref" in node:
        return root.get("$defs", {}).get(node["$ref"].rsplit("/", 1)[-1], {})
    return node


def _allowed(schema: dict) -> list[Any]:
    """@brief 有限闭集完整返回，包括真实 null，不能按候选上限裁剪。"""
    if "enum" in schema:
        return list(schema["enum"])
    if "const" in schema:
        return [schema["const"]]
    if schema.get("type") == "null":
        return [None]
    branches = schema.get("anyOf", [])
    if branches and all("enum" in part or "const" in part or part.get("type") == "null" for part in branches):
        return [value for part in branches for value in _allowed(part)]
    return []


def _purchase_preferences(arguments: dict) -> list[Any]:
    """@brief 活动方式合法后精确返回购买或非购买偏好闭集。"""
    from worldmock.life.interaction_menu import NAMED_PURCHASE_WAYS
    from worldmock.life.name_resolution import way_options

    rows = arguments.get("items")
    if not isinstance(rows, list) or not rows:
        return ["最快拿到", "到手价最低", None]
    options = [way_options(row["way"]) if isinstance(row, dict) and isinstance(row.get("way"), str)
               else frozenset() for row in rows]
    if any(not option for option in options):
        return ["最快拿到", "到手价最低", None]
    return ["最快拿到", "到手价最低"] if any(option & NAMED_PURCHASE_WAYS for option in options) else [None]


def _expected(schema: dict, row: Mapping[str, Any]) -> str:
    """@brief 类型、范围、格式和必填条件来自实际 Schema 与校验错误。"""
    parts = []
    if row["type"] == "missing":
        parts.append("This field is required")
    if row["type"] == "extra_forbidden":
        return "This field is not declared and must be removed."
    if schema:
        if "type" in schema:
            parts.append(f"JSON type: {schema['type']}")
        elif "anyOf" in schema:
            parts.append("A value satisfying one of the declared JSON types is required")
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "pattern"):
            if key in schema:
                parts.append(f"{key}: {schema[key]}")
    return "; ".join(parts) + "." if parts else _why(row)


def _why(row: Mapping[str, Any]) -> str:
    """@brief 英文校验原因不包含库转储、内部身份或验收答案。"""
    if row["type"] == "value_error":
        return str(row["ctx"]["error"])
    if row["type"] in _VALIDATION_BOUNDS:
        comparison, key = _VALIDATION_BOUNDS[row["type"]]
        units = " entries or characters" if "length" in key else ""
        return f"The value must be {comparison} {row['ctx'][key]}{units}."
    if row["type"] not in _VALIDATION_REASONS:
        logging.error("Undeclared request validation condition: %s", row["type"])
        raise ValueError(f"Undeclared request validation condition: {row['type']}")
    return _VALIDATION_REASONS[row["type"]]
