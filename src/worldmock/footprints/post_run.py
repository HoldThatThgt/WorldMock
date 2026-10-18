"""@brief 事后读取视图：导出、导出认证与行为评估在内存里只保留大事件里事后会读的部分（docs/design/05-recording-engine.md「完整导出认证」）。

events.jsonl 与运行库里的事件始终完整；视图只在内存里去掉下表的庞大载荷，表外的键（含以后新增的键）原样保留。
"""

from __future__ import annotations

from typing import Any

from worldmock.footprints.models import Event

#: 观察在视图里去掉的 data 键；其余键原样保留。
DROPPED_PAYLOADS = {
    "observation": frozenset({"activity_bindings", "persona_views", "tool_layer", "interaction_bindings",
                              "catalog_bindings", "memory_layer", "goal_bindings", "locations"}),
}
#: 请求消息里去掉的提示与思考原文。
_MESSAGE_TEXT_KEYS = frozenset({"content", "reasoning_content"})
#: 工具定义的 function 里去掉的说明与参数结构。
_TOOL_SCHEMA_KEYS = frozenset({"description", "parameters"})
#: 视图会缩小的事件类型；其余类型原样返回同一个对象。
REDUCED_EVENT_TYPES = frozenset({"observation", "model_request", "model_response"})


def post_run_view(event: Event) -> Event:
    """@brief 事件的事后读取视图：大事件换成缩小的 data，其余事件原样返回。"""
    if event.event_type not in REDUCED_EVENT_TYPES:
        return event
    return event.model_copy(update={"data": _post_run_data(event.event_type, event.data)})


def post_run_row(row: dict) -> dict:
    """@brief 已严格校验的事件行（json.loads 的结果）的事后读取视图，与 post_run_view 用同一规则。"""
    if row.get("event_type") not in REDUCED_EVENT_TYPES or not isinstance(row.get("data"), dict):
        return row
    return {**row, "data": _post_run_data(row["event_type"], row["data"])}


def _post_run_data(event_type: str, data: dict) -> dict:
    """@brief 按事件类型缩小 data：观察去掉庞大绑定与提示层，请求只留提纲，响应去掉候选的 message；任何形状都不抛错。"""
    kept = {key: value for key, value in data.items() if key not in DROPPED_PAYLOADS.get(event_type, ())}
    if event_type == "observation" and "errand_item_bindings" in kept:
        kept["errand_item_bindings"] = _reachable_item_bindings(data)
    if event_type != "observation" and isinstance(kept.get("request"), dict):
        kept["request"] = _request_outline(kept["request"])
    if event_type == "model_response" and isinstance(kept.get("response"), dict):
        kept["response"] = _without_messages(kept["response"])
    return kept


def _reachable_item_bindings(data: dict) -> Any:
    """@brief errand_item_bindings 里 errand_bindings 查得到的行；不是列表时原样返回，形状不规整的行一律保留。"""
    rows = data["errand_item_bindings"]
    if not isinstance(rows, list):
        return rows
    bindings = data.get("errand_bindings")
    used = ({row["product_id"] for row in bindings if isinstance(row, dict) and isinstance(row.get("product_id"), str)}
            if isinstance(bindings, list) else set())
    return [row for row in rows if not _unreferenced(row, used)]


def _unreferenced(row: Any, used: set[str]) -> bool:
    """@brief 一行商品绑定形状规整（对象、字符串 product_id、带 name）且没有被任何 errand_bindings 行引用。"""
    return (isinstance(row, dict) and isinstance(row.get("product_id"), str) and "name" in row
            and row["product_id"] not in used)


def _request_outline(request: dict) -> dict:
    """@brief 请求提纲：消息去掉 content 与 reasoning_content，工具的 function 去掉 description 与 parameters，其余原样。"""
    outline = dict(request)
    if isinstance(request.get("messages"), list):
        outline["messages"] = [_without(message, _MESSAGE_TEXT_KEYS) for message in request["messages"]]
    if isinstance(request.get("tools"), list):
        outline["tools"] = [_tool_outline(tool) for tool in request["tools"]]
    return outline


def _tool_outline(tool: Any) -> Any:
    """@brief 工具定义的提纲：function 是对象时去掉说明与参数结构，其余形状原样。"""
    if not isinstance(tool, dict) or not isinstance(tool.get("function"), dict):
        return tool
    return {**tool, "function": _without(tool["function"], _TOOL_SCHEMA_KEYS)}


def _without_messages(response: dict) -> dict:
    """@brief 提供商响应去掉每个候选的 message；choices 不是列表时原样返回。"""
    if not isinstance(response.get("choices"), list):
        return response
    return {**response, "choices": [_without(choice, frozenset({"message"})) for choice in response["choices"]]}


def _without(value: Any, keys: frozenset[str]) -> Any:
    """@brief 对象去掉给定的键（保持其余键的次序）；不是对象时原样返回。"""
    return {key: item for key, item in value.items() if key not in keys} if isinstance(value, dict) else value
