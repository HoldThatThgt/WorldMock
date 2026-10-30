"""@brief 只读取现行逐项控制请求，旧控制记录直接拒绝。

本模块是控制请求的唯一读取入口（docs/design/04-simulation-engine.md）：control_items 读逐项内容，control_errands 读请求级
的个人事务变更；两者对工具与请求形状用同一套检查，缺字段（含请求级 errands）即完整性故障。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from worldmock.foundation.errors import IntegrityError

#: 以活动名为参数的三个控制工具。
CONTROL_TOOLS = ("report_status", "pause_activity", "conclude_activity")
#: 看法与偏好两类记忆：请求里写在 views，存储为带 subject 的记忆；不进人设层，也不进进度层。
VIEW_MEMORY_TYPES = frozenset({"relationship", "preference"})
#: 一条个人事务变更的八个键（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」），全部必填，按这个顺序读出。
_ERRAND_KEYS = ("change", "name", "item", "quantity", "memo", "remind_time", "reason")


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
    """@brief 读出控制工具请求的逐项内容，只接受现行形状：items 与请求级 errands（列表，可为空）都在。

    缺 errands 的请求是旧形状记录，经本入口读取的每个读取方（环境处理器、同刻请求分类、宿主校验、记忆层、记忆证据、
    零点计划输入、质量报告与交付窗口）都随之以完整性故障拒绝，不留读取分支（docs/design/04-simulation-engine.md）。

    :param tool: report_status、pause_activity 或 conclude_activity。
    :param request: 已校验的请求模型，或事件与导出里记录的请求参数（JSON 形状）。
    :returns: 按请求顺序的各项；播报与暂停的记忆为空。
    :raises IntegrityError: 不是控制工具，或请求形状认不出、缺 errands 或 errands 不是列表、字段缺失。
    """
    body = _body(tool, request)
    if "items" in body:
        _list(_field(body, "errands", tool), tool)
        return tuple(_current_item(tool, _mapping(row, tool)) for row in _list(body["items"], tool))
    logging.error("Invalid activity control request: %s", f"{tool} request shape is not recognised")
    raise IntegrityError(f"{tool} request shape is not recognised")


def control_errands(tool: str, request: Mapping[str, Any] | BaseModel) -> tuple[dict[str, Any], ...]:
    """@brief 读出控制工具请求里请求级的个人事务变更（docs/design/04-simulation-engine.md），与 control_items 同一
    入口检查：工具须是三个控制工具之一，模型按 JSON 形状导出，请求须是对象。

    :param tool: report_status、pause_activity 或 conclude_activity。
    :param request: 已校验的请求模型，或事件与导出里记录的请求参数（JSON 形状）。
    :returns: 按请求顺序的各条变更，每条是只含七个键（change、name、item、quantity、memo、remind_time、reason，
        按此顺序）的普通字典，值照请求所写。
    :raises IntegrityError: 不是控制工具、请求不是对象、缺 errands、errands 不是列表，或某条变更不是对象、缺七键之一。
    """
    body = _body(tool, request)
    entries = [_mapping(entry, tool) for entry in _list(_field(body, "errands", tool), tool)]
    if any(set(entry) - set(_ERRAND_KEYS) for entry in entries):
        logging.error("Invalid errand change fields in %s request", tool)
        raise IntegrityError(f"{tool} errand change contains unknown fields")
    return tuple({key: _field(entry, key, tool) for key in _ERRAND_KEYS} for entry in entries)


def _body(tool: str, request: Mapping[str, Any] | BaseModel) -> Mapping[str, Any]:
    """@brief 控制请求的 JSON 形状：工具须是三个控制工具之一，模型按 JSON 形状导出，请求须是对象（两个读取入口共用）。"""
    if tool not in CONTROL_TOOLS:
        logging.error("Invalid activity control request: %s", f"Tool is not an activity control tool: {tool}")
        raise IntegrityError(f"Tool is not an activity control tool: {tool}")
    body = request.model_dump(mode="json") if isinstance(request, BaseModel) else request
    if not isinstance(body, Mapping):
        logging.error("Invalid activity control request: %s", f"{tool} request is not an object")
        raise IntegrityError(f"{tool} request is not an object")
    return body


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
    """@brief 记忆字段的存储形状 {type, text, importance, keywords, subject}（docs/design/06-agent.md）。"""
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
