"""@brief LifeAgent 的协议纠错（docs/dev/SPEC-dayrun-fidelity.md 第四章「错误合同」）。

宿主本地校验失败与 AS2 自身的协议错误（参数不是 JSON、没有工具调用、调用了未提供的工具）都写成共用错误结构
（worldmock.life.error_contract）回给模型：纠错正文是 {error_code, reason, fix, items, unmet, instruction}，instruction 固定
为「本次没有登记到世界；按 fix 修改后重新提交」，不带英文校验转储、input_value 与网址；错误码取宿主闭集
HOST_ERROR_CODES。宿主本地校验返回 HostFeedback：字符串本身是写进模型响应证据的协议错误串「<错误码>: <英文说明>」，
report 是回给模型的结构；AS2 产生的协议错误串（锁定提交 agent_runtime._parse_react_responses 与
_validate_react_tool_names 的原文）按原文判定错误码。截断（OUTPUT_TRUNCATED）的纠错由截断处理以同一入口 host_feedback
构造，经 feedback_content 写成纠错正文。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from pydantic import ValidationError

from worldmock.life.error_contract import (HOST_ERROR_CODES, ErrorItem, ErrorReport, host_defaults, host_report,
                                           items_fix)

#: 纠错正文的固定指示（docs/dev/SPEC-dayrun-fidelity.md 第四章）。
FEEDBACK_INSTRUCTION = "本次没有登记到世界；按 fix 修改后重新提交"
#: 逐项原文在纠错正文中的最长字符数（更长的截断并加省略号，只作定位）。
_WRITTEN_MAX_CHARS = 120
#: 类型不符的校验错误 → 应写成的类型。
_TYPE_WORDS = {
    "list_type": "数组", "tuple_type": "数组", "string_type": "字符串", "int_type": "整数", "int_parsing": "整数",
    "int_from_float": "整数", "float_type": "数字", "float_parsing": "数字", "bool_type": "true 或 false",
    "bool_parsing": "true 或 false", "dict_type": "对象", "model_type": "对象", "model_attributes_type": "对象",
    "none_required": "null",
}
#: 数值界限的校验错误 → （说法，界限键）。
_BOUND_WORDS = {"greater_than_equal": ("不小于", "ge"), "greater_than": ("大于", "gt"),
                "less_than_equal": ("不大于", "le"), "less_than": ("小于", "lt")}


class HostFeedback(str):
    """@brief 带错误结构的宿主协议错误串：字符串本身是写进模型响应证据的「<错误码>: <英文说明>」，report 是回给模型的
    统一结构（AS2 把 validate_react_decisions 的返回值原样交给 _protocol_feedback_content）。"""

    report: ErrorReport

    def __new__(cls, text: str, report: ErrorReport | None = None) -> "HostFeedback":
        """@brief 以证据文字构造；report 缺省只发生在复制时（随后按实例字典恢复）。"""
        feedback = super().__new__(cls, text)
        if report is not None:
            feedback.report = report
        return feedback


class HostIssue(NamedTuple):
    """@brief 逐项纠错的一项：明细、写给人物的一句原因与英文说明（证据用）。"""

    item: ErrorItem
    sentence: str
    detail: str


def host_feedback(report: ErrorReport, detail: str) -> HostFeedback:
    """@brief 一次宿主协议纠错：证据文字为「<错误码>: <英文说明>」，结构为 report。"""
    return HostFeedback(f"{report.error_code}: {detail}", report)


def merged_feedback(issues: Sequence[HostIssue], total: int, fixes: Mapping[str, str] | None = None) -> HostFeedback:
    """@brief 逐项的纠错合并成一次（名称类逐项报全部，docs/dev/SPEC-dayrun-fidelity.md 3.1 第 5 条）：码取第一项的码，
    原因逐项相连，改法按码写「第 N 项<改法>」（fixes 缺省取各码的默认改法），明细按请求中的先后排列。

    :param total: 请求的项数（决定原因与改法是否写序号）。
    """
    ordered = sorted(issues, key=lambda issue: issue.item.index)
    items = tuple(issue.item for issue in ordered)
    table = {item.error_code: host_defaults(item.error_code)[1] for item in items} | dict(fixes or {})
    code = items[0].error_code
    report = host_report(code, "；".join(issue.sentence for issue in ordered), items_fix(items, total, table),
                         items)
    return host_feedback(report, "; ".join(issue.detail for issue in ordered))


def argument_feedback(tool: str, error: ValidationError) -> HostFeedback:
    """@brief 参数形状不符合请求模型：ARGUMENTS_INVALID，原因按字段逐条写（不回传校验库的转储、input_value 与网址），
    明细逐字段写出参数名与所写的原文；英文完整说明只进证据。"""
    rows = error.errors(include_url=False)
    reasons = list(dict.fromkeys(_field_reason(row) for row in rows))
    items = tuple(dict.fromkeys(_field_item(row) for row in rows if row["loc"]))
    report = host_report("ARGUMENTS_INVALID", "；".join(reasons), items=items)
    return host_feedback(report, f"invalid {tool} arguments: {error}")


def feedback_report(error: str) -> ErrorReport:
    """@brief 一条协议错误串的错误结构：宿主本地校验带着结构；只剩文字、以宿主错误码开头的（如落库的证据）取该码的
    默认说法；AS2 的协议错误串按原文判定。"""
    report = getattr(error, "report", None)
    if isinstance(report, ErrorReport):
        return report
    code = str(error).split(": ", 1)[0]
    return host_report(code) if code in HOST_ERROR_CODES else _as2_report(str(error))


def feedback_content(error: str) -> str:
    """@brief 回给模型的纠错正文：统一错误结构五个键加固定 instruction（JSON，中文不转义）。"""
    return json.dumps({**feedback_report(error).payload(), "instruction": FEEDBACK_INSTRUCTION}, ensure_ascii=False)


def _as2_report(error: str) -> ErrorReport:
    """@brief AS2 协议错误串 → 错误结构（原文见锁定提交的 agent_runtime：_parse_react_responses、
    _build_react_decisions、_build_finish_decision 与 _validate_react_tool_names）。"""
    parsed = re.match(r"invalid tool arguments for (\S*): .*?line (\d+) column (\d+)", error, re.S)
    if parsed:
        tool, line, column = parsed.groups()
        return host_report("ARGUMENTS_NOT_JSON", f"「{tool}」的参数不是合法的 JSON（第 {line} 行第 {column} 列附近）")
    shape = re.match(r"(?:invalid tool arguments for|tool arguments for) (\S*)", error)
    if shape:
        return host_report("ARGUMENTS_NOT_JSON", f"「{shape.group(1)}」的参数不是一个 JSON 对象")
    unknown = re.fullmatch(r"unknown native tool calls: (.*)", error, re.S)
    if unknown:
        names = "、".join(f"「{name}」" for name in re.findall(r"'([^']*)'", unknown.group(1)))
        return host_report("TOOL_CALL_REQUIRED", f"{names or '所调用的工具'}不是这次提供的工具", "只调用这次请求里提供的工具")
    if error == "finish must be the only tool call in a response":
        return host_report("TOOL_CALL_REQUIRED", "finish 要单独调用", "这次回复只调用 finish 一个工具")
    if error == "finish requires a non-empty answer":
        return host_report("ARGUMENTS_INVALID", "finish 的 answer 不能是空的", "在 answer 里写出回答")
    if error == "finish memories must be a list":
        return host_report("TOOL_CALL_REQUIRED", "这次决策没有 finish 工具", "用这次提供的生活工具提交一个世界请求")
    if error in {"tool call ids must be non-empty and unique", "tool name must be non-empty"}:
        return host_report("TOOL_CALL_REQUIRED", "工具调用缺少名称或编号")
    return host_report("TOOL_CALL_REQUIRED")


def _path(location: Sequence[Any]) -> str:
    """@brief 校验错误位置的可读写法，如 memories[0][1].subject。"""
    text = ""
    for part in location:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
    return text


def _field_reason(row: Mapping[str, Any]) -> str:
    """@brief 一条校验错误写给人物的原因：缺字段、多字段、类型、格式、界限与取值闭集各有说法；请求模型自己的校验写明的
    原因照录（去掉校验库的前缀）。"""
    kind, path, context = row["type"], _path(row["loc"]), row.get("ctx") or {}
    subject = path or "参数"
    if kind == "missing":
        return f"{subject} 没有写"
    if kind == "extra_forbidden":
        return f"{subject} 不是这个工具的参数，要去掉"
    if kind in {"value_error", "assertion_error"} and "error" in context:
        return f"{subject}：{context['error']}"
    if kind in _TYPE_WORDS:
        return f"{subject} 要写成{_TYPE_WORDS[kind]}"
    if kind == "string_pattern_mismatch":
        return f"{subject}{_pattern_reason(str(context.get('pattern', '')))}"
    if kind in _BOUND_WORDS and _BOUND_WORDS[kind][1] in context:
        word, key = _BOUND_WORDS[kind]
        bound = context[key]
        return f"{subject} 要{word} {bound:g}" if isinstance(bound, float) else f"{subject} 要{word} {bound}"
    if kind in {"literal_error", "enum"} and "expected" in context:
        return f"{subject} 只能取 {context['expected']}"
    if kind in {"too_long", "too_short"}:
        limit = context.get("max_length" if kind == "too_long" else "min_length")
        unit = "个字符" if context.get("field_type") == "String" else "项"
        return f"{subject} {'最多' if kind == 'too_long' else '至少'} {limit} {unit}"
    return f"{subject} 的写法不对"


def _pattern_reason(pattern: str) -> str:
    """@brief 字符串格式不符的说法：非空白、HH:MM 时刻与带时区的 ISO 时刻各有说法。"""
    if pattern == r"\S":
        return " 不能是空白"
    if "[0-5][0-9]$" in pattern:
        return " 要写成 HH:MM，如 08:30"
    if pattern.startswith(r"^\d{4}-"):
        return " 要写成带时区的 ISO 时刻，如 2026-09-28T08:30:00+08:00"
    return " 的格式不对"


def _field_item(row: Mapping[str, Any]) -> ErrorItem:
    """@brief 一条校验错误的明细：序号取位置中的第一个数组下标（没有时为 0），field 为参数名，written 为所写的原文。"""
    location = row["loc"]
    index = next((part for part in location if isinstance(part, int)), 0)
    return ErrorItem(index, str(location[0]), _written(row), "ARGUMENTS_INVALID")


def _written(row: Mapping[str, Any]) -> str:
    """@brief 所写的原文：缺字段为空串，字符串照录，其余写成紧凑 JSON；过长的截断并加省略号。"""
    if row["type"] == "missing":
        return ""
    value = row.get("input")
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                                                             default=str)
    return text if len(text) <= _WRITTEN_MAX_CHARS else f"{text[:_WRITTEN_MAX_CHARS]}…"
