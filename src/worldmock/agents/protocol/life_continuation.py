"""@brief 同秒续接的宿主侧：把环境交来的续接链组装成本次决策的起始消息（docs/design/06-agent.md「提示与工具」）。

消息 = 上一决策末次请求的全部消息（system、链首快照 user、此前各原生回合）＋ 上一决策的最终 assistant（reasoning_content、
content、tool_calls）＋ 对应其每个世界请求 tool_call_id 的各一条 tool 消息（开始组两条，changes 与 events 只写在最后一条上）。
tool 消息是紧凑 JSON {result, changes, events}，空键
省略：changes 相对于模型当前所见（链首快照依次叠加此前各 tool 消息的 changes）给出工具层目标的 updated 与 removed、
待办层暂停活动与个人事务（按名称）的 added 与 removed、今日计划与身体感受的整体替换、人设层关系与偏好的更新项，以最新
为准。记忆层与所在地点在链内不变（它们只随时间推进或 session 切换而变，两者都会断链）。原生 tools 不因续接改变。

发送前协议自检：续接链中每个 assistant 都须带非空 reasoning_content，缺失就断链（本决策此后都从新快照续写）；本次决策
内的 assistant 缺失时以英文错误失败。预算第 0 步断链后同样从新快照续写（prompt_capacity.fit_request）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from worldmock.agents.prompt.narration import render_layer_json
from worldmock.agents.prompt.prompt_capacity import ProgressBudget
from worldmock.agents.prompt.tool_groups import compact_render, expand_tool_layer


def reasoning_complete(messages: Sequence[Mapping[str, Any]]) -> bool:
    """@brief 消息中的每个 assistant 都带非空 reasoning_content（提供商协议：回传的思考缺失即 400）。"""
    return all(isinstance(item.get("reasoning_content"), str) and bool(item["reasoning_content"])
               for item in messages if item.get("role") == "assistant")


def layer_changes(head_layers: Sequence[dict], applied: Sequence[dict], current: Sequence[dict]) -> dict:
    """@brief 当前六层相对于模型所见（链首六层依次叠加 applied 中此前各 changes）的改动；没有改动的键不写。"""
    seen = _view(head_layers)
    for changes in applied:
        seen = _overlay(seen, changes)
    now = _view(current)
    result: dict[str, Any] = {}
    targets = _keyed_changes(seen["targets"], now["targets"], ("updated", "removed"))
    paused = _keyed_changes(seen["paused_activities"], now["paused_activities"], ("added", "removed"))
    errands = _keyed_changes(seen["errands"], now["errands"], ("added", "removed"))
    for key, value in (("targets", targets), ("paused_activities", paused), ("errands", errands)):
        if value:
            result[key] = value
    for key in ("day_plan", "body"):
        if now[key] != seen[key]:
            result[key] = now[key]
    for key in ("relationships", "preferences"):
        updated = [item for name, item in now[key].items() if seen[key].get(name) != item]
        if updated:
            result[key] = updated
    return result


def tool_message(tool_call_id: str, result: dict, changes: dict, events: list[dict]) -> dict:
    """@brief 对应上一决策世界请求的 tool 消息：紧凑 JSON {result, changes, events}，空键省略，经字段黑名单序列化。"""
    payload: dict[str, Any] = {"result": result}
    if changes:
        payload["changes"] = changes
    if events:
        payload["events"] = events
    return {"role": "tool", "tool_call_id": tool_call_id, "content": render_layer_json(payload)}


def continuation_messages(chain: Mapping[str, Any] | None, layers: Sequence[dict]) -> list[dict] | None:
    """@brief 由续接链与本次观察的六层组装本次决策的起始消息；没有链时为 None（从新快照构造）。"""
    if chain is None:
        return None
    messages = [dict(item) for item in chain["messages"]]
    applied = [changes for changes in (_earlier_changes(item) for item in messages[2:] if item.get("role") == "tool")
               if changes is not None]
    changes = layer_changes(json.loads(messages[1]["content"]), applied, layers)
    results = list(chain["tool_results"])
    earlier = [tool_message(item["tool_call_id"], item["result"], {}, []) for item in results[:-1]]
    last = tool_message(results[-1]["tool_call_id"], results[-1]["result"], changes, list(chain["events"]))
    return [*messages, dict(chain["assistant"]), *earlier, last]


def send_check(messages: list[dict], budget: ProgressBudget) -> ProgressBudget:
    """@brief 发送前协议自检（只对带续接链的请求）：本次决策内的 assistant 缺思考即英文错误失败；链中缺思考即断链。"""
    if budget.fresh_messages is None:
        return budget
    if not reasoning_complete(messages[budget.chain_length:]):
        logging.error("An assistant turn of the current decision has no reasoning_content")
        raise RuntimeError("An assistant turn of the current decision is missing reasoning_content")
    if reasoning_complete(messages[:budget.chain_length]):
        return budget
    logging.warning("Continuation chain has an assistant turn without reasoning_content; using a fresh snapshot")
    return end_continuation(messages, budget)


def end_continuation(messages: list[dict], budget: ProgressBudget) -> ProgressBudget:
    """@brief 提前结束本秒续接：原地把链前缀换成新快照消息，本决策此后的回合都从新快照续写；返回不带续接的预算。

    messages 是 AS2 本回合的消息列表，它随后成为本决策的消息历史，所以原地替换让后续回合不再带回传的思考。
    """
    messages[:budget.chain_length] = [dict(item) for item in budget.fresh_messages]
    return replace(budget, fresh_messages=None, chain_length=0)


def _earlier_changes(message: Mapping[str, Any]) -> dict | None:
    """@brief 链中此前一条续接 tool 消息的 changes（没有改动为空对象）；记忆查询与协议纠错的 tool 消息为 None。

    链中 tool 消息的正文都是宿主或 AS2 写的 JSON 对象；续接 tool 消息必有 result 键，另两种没有。
    """
    payload = json.loads(message["content"])
    return payload.get("changes", {}) if "result" in payload else None


def _view(layers: Sequence[dict]) -> dict:
    """@brief 六层中续接会改动的部分：目标、暂停活动与个人事务（按名称）、今日计划、身体感受、关系（按人物）与偏好
    （按对象）。"""
    by_name = {layer["layer"]: layer for layer in layers}
    persona, todo = by_name["人设层"], by_name["待办层"]
    tools = expand_tool_layer(compact_render(by_name["工具层"]))
    return {"targets": {item["name"]: item for item in tools["targets"]},
            "paused_activities": {item["name"]: item for item in todo["paused_activities"]},
            "errands": {item["name"]: item for item in todo["errands"]},
            "day_plan": todo["day_plan"], "body": by_name["环境层"]["body"],
            "relationships": {item["person"]: item for item in persona["relationships"]},
            "preferences": {item["subject"]: item for item in persona["preferences"]}}


def _overlay(seen: dict, changes: Mapping[str, Any]) -> dict:
    """@brief 把一条 changes 叠加到所见视图：先去掉 removed，再按名称写入 updated/added；整体替换项直接替换。"""
    view = dict(seen)
    for key in ("targets", "paused_activities", "errands", "relationships", "preferences"):
        view[key] = dict(seen[key])
    for key, written in (("targets", "updated"), ("paused_activities", "added"), ("errands", "added")):
        part = changes.get(key, {})
        for name in part.get("removed", []):
            view[key].pop(name, None)
        view[key].update({item["name"]: item for item in part.get(written, [])})
    for key in ("day_plan", "body"):
        if key in changes:
            view[key] = changes[key]
    for key, field in (("relationships", "person"), ("preferences", "subject")):
        view[key].update({item[field]: item for item in changes.get(key, [])})
    return view


def _keyed_changes(seen: dict[str, dict], now: dict[str, dict], names: tuple[str, str]) -> dict:
    """@brief 按名称比较：names[0] 为此刻新出现或内容变化的完整条目（此刻顺序），names[1] 为已不存在的名称（所见顺序）。"""
    written = [item for name, item in now.items() if seen.get(name) != item]
    removed = [name for name in seen if name not in now]
    return {key: value for key, value in zip(names, (written, removed)) if value}
