"""@brief 只读取现行逐项控制请求，旧控制记录直接拒绝。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from worldmock.errors import IntegrityError

#: 以活动名为参数的三个控制工具。
CONTROL_TOOLS = ("report_status", "pause_activity", "conclude_activity")
#: 看法与偏好两类记忆：请求里写在 views，存储为带 subject 的记忆，只在人设层出现（不进记忆层与进度层参数）。
VIEW_MEMORY_TYPES = frozenset({"relationship", "preference"})
#: 请求形状的记忆字段（不带对象）；存储形状在其后加 subject。
_MEMORY_KEYS = ("type", "text", "importance", "keywords")


@dataclass(frozen=True, slots=True)
class ControlItem:
    """@brief 控制工具请求的一项：活动名、人物自述与存储形状的全部记忆（含看法与偏好，播报与暂停没有记忆）。"""

    activity_name: str
    summary: str
    memories: tuple[dict[str, Any], ...] = ()

    @property
    def views(self) -> tuple[dict[str, Any], ...]:
        """@brief 看法与偏好（relationship、preference）。"""
        return tuple(memory for memory in self.memories if memory["type"] in VIEW_MEMORY_TYPES)

    @property
    def plain_memories(self) -> tuple[dict[str, Any], ...]:
        """@brief 看法与偏好以外的普通记忆。"""
        return tuple(memory for memory in self.memories if memory["type"] not in VIEW_MEMORY_TYPES)


def control_items(tool: str, request: Mapping[str, Any] | BaseModel) -> tuple[ControlItem, ...]:
    """@brief 读出控制工具请求的逐项内容，只接受严格 items 形状。

    :param tool: report_status、pause_activity 或 conclude_activity。
    :param request: 已校验的请求模型，或事件与导出里记录的请求参数（JSON 形状）。
    :returns: 按请求顺序的各项；播报与暂停的记忆为空。
    :raises IntegrityError: 不是控制工具，或请求形状认不出、字段缺失。
    """
    if tool not in CONTROL_TOOLS:
        logging.error("Invalid activity control request: %s", f"Tool is not an activity control tool: {tool}")
        raise IntegrityError(f"Tool is not an activity control tool: {tool}")
    body = request.model_dump(mode="json") if isinstance(request, BaseModel) else request
    if not isinstance(body, Mapping):
        logging.error("Invalid activity control request: %s", f"{tool} request is not an object")
        raise IntegrityError(f"{tool} request is not an object")
    if "items" in body:
        return tuple(_current_item(tool, _mapping(row, tool)) for row in _list(body["items"], tool))
    logging.error("Invalid activity control request: %s", f"{tool} request shape is not recognised")
    raise IntegrityError(f"{tool} request shape is not recognised")


def control_request(tool: str, request: Mapping[str, Any] | BaseModel, *, memories: bool) -> dict[str, Any]:
    """@brief 以现行形状重写的请求参数：每项活动名与自述；memories 为真时结束工具每项另带普通记忆（请求形状，没有
    subject），看法与偏好一律不带（进度层参数带普通记忆，交付窗口的意图与 dreaming 输入都不带）。"""
    rows = []
    for item in control_items(tool, request):
        row: dict[str, Any] = {"activity_name": item.activity_name, "summary": item.summary}
        if memories and tool == "conclude_activity":
            row["memories"] = [{key: memory[key] for key in _MEMORY_KEYS} for memory in item.plain_memories]
        rows.append(row)
    return {"items": rows}


def _current_item(tool: str, row: Mapping[str, Any]) -> ControlItem:
    """@brief 现行形状的一项：结束工具的 views 以 about 写作 subject，排在普通记忆之后。"""
    name, summary = _text(row, "activity_name", tool), _text(row, "summary", tool)
    if tool != "conclude_activity":
        return ControlItem(name, summary)
    stored = [_stored(_mapping(memory, tool), None, tool) for memory in _list(_field(row, "memories", tool), tool)]
    for view in _list(_field(row, "views", tool), tool):
        view = _mapping(view, tool)
        stored.append(_stored(view, _text(view, "about", tool), tool))
    return ControlItem(name, summary, tuple(stored))


def _stored(memory: Mapping[str, Any], subject: str | None, tool: str) -> dict[str, Any]:
    """@brief 接口合同「记忆字段」的存储形状 {type, text, importance, keywords, subject}。"""
    keywords = _list(_field(memory, "keywords", tool), tool)
    return {"type": _text(memory, "type", tool), "text": _text(memory, "text", tool),
            "importance": _field(memory, "importance", tool), "keywords": list(keywords), "subject": subject}


def _mapping(value: Any, tool: str) -> Mapping[str, Any]:
    """@brief 读取对象（一项或一条记忆）。"""
    if not isinstance(value, Mapping):
        logging.error("Invalid activity control request: %s", f"{tool} request entry is not an object")
        raise IntegrityError(f"{tool} request entry is not an object")
    return value


def _field(row: Mapping[str, Any], key: str, tool: str) -> Any:
    """@brief 读取必有字段，缺失即数据不一致。"""
    if key not in row:
        logging.error("Invalid activity control request: %s", f"{tool} request lacks {key}")
        raise IntegrityError(f"{tool} request lacks {key}")
    return row[key]


def _text(row: Mapping[str, Any], key: str, tool: str) -> str:
    """@brief 读取必有的文本字段。"""
    value = _field(row, key, tool)
    if not isinstance(value, str):
        logging.error("Invalid activity control request: %s", f"{tool} request field {key} must be text")
        raise IntegrityError(f"{tool} request field {key} must be text")
    return value


def _list(value: Any, tool: str) -> list[Any]:
    """@brief 读取列表字段。"""
    if not isinstance(value, list):
        logging.error("Invalid activity control request: %s", f"{tool} request field must be a list")
        raise IntegrityError(f"{tool} request field must be a list")
    return value
