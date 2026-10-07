"""@brief 宿主与环境共用的完整工具调用错误合同。"""

from __future__ import annotations
from worldmock.life.vocabulary import NAMED_PURCHASE_WAYS
from worldmock.life.vocabulary import way_options

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

_LOGGER = logging.getLogger(__name__)
ERROR_KEYS = ("error_code", "reason", "fix", "items", "unmet")
ITEM_KEYS = ("path", "written", "error_code", "allowed_values", "expected", "why", "candidates")
CANDIDATE_KEYS = ("tool", "arguments", "note")
MAX_CANDIDATES = 3
ACCEPTANCE_KEYS = ("resolved", "notes")
ARGUMENT_ROOT = ("tool_calls", 0, "function", "arguments")

_HOST_DEFAULTS = {
    "ARGUMENTS_NOT_JSON": ("Tool arguments are not a complete JSON object.", "Submit a complete JSON object."),
    "ARGUMENTS_INVALID": ("Tool arguments violate the request contract.", "Correct every reported parameter."),
    "TOOL_CALL_REQUIRED": ("The tool call batch is invalid.", "Submit one world request or only read queries."),
    "TOO_MANY_TARGETS": ("The activity request exceeds its item limit.", "Submit one or two activity items."),
    "NAME_NOT_FOUND": ("The name does not identify a known object.", "Use an exact known name or a candidate."),
    "WORK_TOPIC_NOT_FOUND": ("The named work topic is not assigned to you.",
                             "Use an exact known work topic name."),
    "NAME_AMBIGUOUS": ("The name identifies several known objects.", "Use a complete candidate name."),
    "WAY_NOT_OFFERED": ("The object does not support this way.", "Use one of the object's offered ways."),
    "ROUTE_FIELDS_INVALID": ("The route fields do not match the way.", "Correct from, to and answering."),
    "SUPPLY_SOURCE_MISMATCH": ("The source differs from the target store.", "Set from to null or the target store."),
    "SUBJECT_NOT_KNOWN": ("The relationship subject is unknown.", "Use a known person in about."),
    "OUTPUT_TRUNCATED": ("The model output was truncated.", "Submit one complete tool call."),
    "MODEL_SERVICE_FAILED": ("The model service failed before a response was available.",
                             "Restore the model service before retrying this decision."),
    "EVIDENCE_WRITE_FAILED": ("Model evidence could not be persisted.",
                             "Restore evidence persistence before retrying this decision."),
    "MEMORY_QUERY_FAILED": ("The memory query failed during retrieval.",
                           "Retry the read query after memory retrieval is available."),
    "TOOL_SERVICE_FAILED": ("The tool service failed before a result was available.",
                           "Restore the tool service before retrying this decision."),
}
HOST_ERROR_CODES = tuple(_HOST_DEFAULTS)
HOST_NAME_CODES = frozenset({"NAME_NOT_FOUND", "NAME_AMBIGUOUS", "WAY_NOT_OFFERED", "WORK_TOPIC_NOT_FOUND",
                             "SUPPLY_SOURCE_MISMATCH", "SUBJECT_NOT_KNOWN"})
ENVIRONMENT_NAME_CODES = frozenset({"SUPPLY_NOT_FOUND", "SUPPLY_NOT_AT_STORE", "FULFILLMENT_NOT_SUPPORTED",
                                    "SOURCE_NOT_FOUND", "SUPPLY_SOURCE_MISMATCH", "TASK_NOT_FOUND"})
NAME_REJECTION_CODES = HOST_NAME_CODES | ENVIRONMENT_NAME_CODES


@dataclass(frozen=True, slots=True)
class Candidate:
    """@brief 完整可提交请求及英文说明。"""

    tool: str
    arguments: dict[str, Any]
    note: str | None = None

    def payload(self) -> dict[str, Any]:
        """@brief 三个必填字段始终输出。"""
        return {"tool": self.tool, "arguments": self.arguments, "note": self.note}


def activity_candidate(target: str, way: str, route: tuple = (None, None), note: str | None = None) -> Candidate:
    """@brief 从目标、方式和路线构造完整的新活动工具请求。"""

    prefer = "最快拿到" if way_options(way) & NAMED_PURCHASE_WAYS else None
    return Candidate("start_activity", {
        "items": [{"target": target, "way": way, "from": route[0], "to": route[1]}],
        "answering": None, "prefer": prefer}, note)


@dataclass(frozen=True, slots=True)
class ErrorItem:
    """@brief 完整位置、JSON 原值、合法闭集和明确条件的一条错误。"""

    path: tuple[str | int, ...]
    written: Any
    error_code: str
    allowed_values: tuple[Any, ...] = ()
    expected: str = "A valid value satisfying the tool contract is required."
    why: str = "The parameter does not satisfy the declared contract."
    candidates: tuple[Candidate, ...] = ()

    def __post_init__(self) -> None:
        """@brief 所有错误保留非空路径与条件，候选限制三条。"""
        if not self.path or not self.expected or not self.why or len(self.candidates) > MAX_CANDIDATES:
            _LOGGER.error("Error item has invalid path, explanation or candidate count")
            raise ValueError("error items require a path and explanations, with at most three candidates")

    def payload(self) -> dict[str, Any]:
        """@brief JSON 原值和 null 类型保持原样。"""
        path = self.path if self.path[0] == "tool_calls" else (*ARGUMENT_ROOT, *self.path)
        return {"path": list(path), "written": self.written, "error_code": self.error_code,
                "allowed_values": list(self.allowed_values), "expected": self.expected, "why": self.why,
                "candidates": [candidate.payload() for candidate in self.candidates]}


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """@brief 一次原子拒绝的五字段合同。"""

    error_code: str
    reason: str
    fix: str | None = None
    items: tuple[ErrorItem, ...] = ()
    unmet: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """@brief 错误必须有可读原因。"""
        if not self.error_code or not self.reason:
            _LOGGER.error("Error report has no code or reason")
            raise ValueError("error report requires a code and reason")

    def payload(self) -> dict[str, Any]:
        """@brief 始终输出五个字段。"""
        return {"error_code": self.error_code, "reason": self.reason, "fix": self.fix,
                "items": [item.payload() for item in self.items], "unmet": list(self.unmet)}


def bind_report(report: ErrorReport, call_index: int) -> ErrorReport:
    """@brief 为纯校验反馈绑定本次真实工具调用下标。"""
    items = []
    for item in report.items:
        path = item.path
        if path[0] == "tool_calls":
            path = (path[0], call_index, *path[2:]) if len(path) > 1 else path
        else:
            path = ("tool_calls", call_index, "function", "arguments", *path)
        items.append(replace(item, path=path))
    return replace(report, items=tuple(items))


def host_defaults(code: str) -> tuple[str, str]:
    """@brief 读取协议错误码的英文原因和修正方法。"""
    if code not in _HOST_DEFAULTS:
        _LOGGER.error("Unknown host error code: %s", code)
        raise ValueError(f"unknown host error code: {code}")
    return _HOST_DEFAULTS[code]


def host_report(code: str, reason: str | None = None, fix: str | None = None,
                items: Sequence[ErrorItem] = ()) -> ErrorReport:
    """@brief 构造协议拒绝。"""
    default_reason, default_fix = host_defaults(code)
    return ErrorReport(code, reason or default_reason, fix or default_fix, tuple(items))


def items_fix(items: Sequence[ErrorItem], total: int, fixes: Mapping[str, str | None]) -> str | None:
    """@brief 合并不同错误码的修正方法。"""
    return "; ".join(str(fixes[code]) for code in dict.fromkeys(item.error_code for item in items)
                     if fixes.get(code)) or None
