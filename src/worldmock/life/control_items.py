"""@brief 活动控制工具请求的归一读取：播报（report_status）、暂停（pause_activity）与结束（conclude_activity）逐项的活动名、
自述与记忆（docs/dev/SPEC-dayrun-fidelity.md 第六章「迁移」）。

现行请求一项一个对象：`{"items": [{activity_name, summary}]}`；结束的每项另带 memories（普通记忆
`{type, text, importance, keywords}`）与 views（看法与偏好 `{type, about, text, importance, keywords}`）。运行记录与
数据集里还有三代旧形状，都是 task[]、summary[] 两组平行数组：结束另带 memories[][] 且每条记忆带必填可空的 subject；
结束另带 memories[][] 而记忆没有 subject；结束也不带记忆。

凡是读这三个工具请求的地方（宿主与环境的受理、进度层、记忆层、看法与偏好、零点计划输入、记忆写入点、交付窗口、
质量报告）都经 control_items 读取，不直接读 task、memories；查看器（tools/run-viewer.html 的 controlItems）按同一规则读。
归一后每项一个 ControlItem：活动名、自述，以及接口合同「记忆字段」存储形状的记忆 `{type, text, importance, keywords,
subject}`——views 的 about 写作 subject，普通记忆的 subject 为 null；同一项先普通记忆、后看法，旧形状保持原来的顺序。
于是 views 写下的记忆与旧形状写 subject 的同一条记忆逐字节相同。形状认不出的请求按数据不一致失败，不猜测。
"""

from __future__ import annotations

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
    """@brief 读出控制工具请求的逐项内容，新旧形状得到同一结果。

    :param tool: report_status、pause_activity 或 conclude_activity。
    :param request: 已校验的请求模型，或事件与导出里记录的请求参数（JSON 形状）。
    :returns: 按请求顺序的各项；播报与暂停的记忆为空。
    :raises IntegrityError: 不是控制工具，或请求形状认不出、字段缺失。
    """
    if tool not in CONTROL_TOOLS:
        raise IntegrityError(f"Tool is not an activity control tool: {tool}")
    body = request.model_dump(mode="json") if isinstance(request, BaseModel) else request
    if not isinstance(body, Mapping):
        raise IntegrityError(f"{tool} request is not an object")
    if "items" in body:
        return tuple(_current_item(tool, _mapping(row, tool)) for row in _list(body["items"], tool))
    if "task" in body:
        return _parallel_items(tool, body)
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


def _parallel_items(tool: str, body: Mapping[str, Any]) -> tuple[ControlItem, ...]:
    """@brief 旧形状：task[] 与 summary[] 逐项对应，结束工具的 memories[i] 是第 i 项的记忆（没有 memories 时为空，
    没有 subject 的更早记录 subject 为 null）。"""
    names, summaries = _list(body["task"], tool), _list(_field(body, "summary", tool), tool)
    groups = _list(body.get("memories", [[] for _ in names]), tool) if tool == "conclude_activity" else [
        [] for _ in names]
    if len(summaries) != len(names) or len(groups) != len(names):
        raise IntegrityError(f"{tool} parallel task, summary and memories lengths disagree")
    if any(not isinstance(value, str) for value in (*names, *summaries)):
        raise IntegrityError(f"{tool} task and summary entries must be text")
    return tuple(ControlItem(name, summary, tuple(_legacy_memory(memory, tool) for memory in _list(group, tool)))
                 for name, summary, group in zip(names, summaries, groups))


def _legacy_memory(memory: Any, tool: str) -> dict[str, Any]:
    """@brief 旧形状的一条记忆：带 subject 的原样取对象，没有这个字段的更早记录对象为 null。"""
    row = _mapping(memory, tool)
    subject = row.get("subject")
    if subject is not None and not isinstance(subject, str):
        raise IntegrityError(f"{tool} memory subject must be text or null")
    return _stored(row, subject, tool)


def _stored(memory: Mapping[str, Any], subject: str | None, tool: str) -> dict[str, Any]:
    """@brief 接口合同「记忆字段」的存储形状 {type, text, importance, keywords, subject}。"""
    keywords = _list(_field(memory, "keywords", tool), tool)
    return {"type": _text(memory, "type", tool), "text": _text(memory, "text", tool),
            "importance": _field(memory, "importance", tool), "keywords": list(keywords), "subject": subject}


def _mapping(value: Any, tool: str) -> Mapping[str, Any]:
    """@brief 读取对象（一项或一条记忆）。"""
    if not isinstance(value, Mapping):
        raise IntegrityError(f"{tool} request entry is not an object")
    return value


def _field(row: Mapping[str, Any], key: str, tool: str) -> Any:
    """@brief 读取必有字段，缺失即数据不一致。"""
    if key not in row:
        raise IntegrityError(f"{tool} request lacks {key}")
    return row[key]


def _text(row: Mapping[str, Any], key: str, tool: str) -> str:
    """@brief 读取必有的文本字段。"""
    value = _field(row, key, tool)
    if not isinstance(value, str):
        raise IntegrityError(f"{tool} request field {key} must be text")
    return value


def _list(value: Any, tool: str) -> list[Any]:
    """@brief 读取列表字段。"""
    if not isinstance(value, list):
        raise IntegrityError(f"{tool} request field must be a list")
    return value
