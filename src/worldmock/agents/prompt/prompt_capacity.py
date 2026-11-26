"""@brief 发送模型请求前计量完整上下文，执行日内阈值 Compaction 与 85% 物理上限进度层浓缩。

计量覆盖原生 tools、系统层、六层 JSON、后续对话消息与预留输出。主循环在请求输入令牌达到
compact_trigger_tokens 时先执行确定性日内 Compaction；若仍高于或跳过后接近 85% 物理上限，
由 fit_request 把最旧一段连续未受保护进度记录折为 condensed，仍超限则分层记录用量并报错。
"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from worldmock.agents.prompt.narration import render_layer_json
from worldmock.agents.prompt.prompt_schemas import USER_LAYER_NAMES
from worldmock.configuration.inputs.models import ModelMetadata
from worldmock.configuration.model.model_client import token_counter
from worldmock.configuration.model.model_metadata import model_context_length
from worldmock.foundation.errors import ProtocolError
from worldmock.state.decisions.day_session import FIVE_LAYER_KEYS, DaySessionState


CONTEXT_THRESHOLD = 0.85
#: 日内 Compaction 保留原文尾部的最大交互组数与最大令牌预算。
TAIL_MAX_GROUPS = 6
TAIL_MAX_TOKENS = 16000
#: 进度层的层名与折叠记录的类型。
_PROGRESS_LAYER = "进度层"
_CONDENSED = "condensed"
#: 工具层中可调用索引的键（容量故障记录单列它的用量）。
_INDEX_KEY = "callable_index"
_CLOCK_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


@dataclass(frozen=True, slots=True)
class ProgressBudget:
    """@brief 可浓缩的请求部分：user 消息中的层数组与进度层记录的私有元数据（受保护标记与 settled 句）。"""

    layers: tuple[dict, ...]
    meta: tuple[dict, ...]
    user_index: int = 1


@dataclass(frozen=True, slots=True)
class FittedRequest:
    """@brief 容量预检通过、实际要发送的请求，与发送前为它计量的输入 token 数。"""

    #: 实际发送的请求；低于阈值时就是传入的那个对象。
    request: dict[str, Any]
    #: request 的消息与工具定义的 token 数（不含预留输出），等于 prompt_token_count(request)。
    prompt_tokens: int


@dataclass(frozen=True, slots=True)
class CompactionInput:
    """@brief 日内确定性 Compaction 所需的请求切片、日会话状态与沙盒快照输入。"""

    model: str
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    day_session: DaySessionState
    snapshot: Mapping[str, Any]
    settled_day_events: tuple[dict[str, Any], ...] = ()
    before_tokens: int = 0
    compaction_count: int | None = None


@dataclass(frozen=True, slots=True)
class CompactionOutcome:
    """@brief 日内 Compaction 重建后的消息列表、刷新预算、五层基座与存证记录。"""

    messages: list[dict[str, Any]]
    budget: ProgressBudget
    last_observed_layers: dict[str, Any]
    compaction_count: int
    compacted_through_seq: int
    record: dict[str, Any]


def prompt_token_count(request: dict[str, Any]) -> int:
    """@brief 计量实际发送的消息与工具定义。"""
    try:
        return token_counter(model=request["model"], messages=request["messages"],
                             tools=request.get("tools"))
    except Exception as error:
        logging.error("Model request token count failed: %s", error)
        raise RuntimeError("model request token count failed") from error


def request_token_count(request: dict[str, Any]) -> int:
    """@brief 容量预检计入输入和明确预留的最大输出 token。"""
    reserve = request.get("max_tokens")
    if not isinstance(reserve, int) or reserve <= 0:
        logging.error("Model request output reserve is missing")
        raise RuntimeError("model request output reserve is missing")
    return prompt_token_count(request) + reserve


def fit_request(request: dict[str, Any], metadata: ModelMetadata | dict,
                budget: ProgressBudget | None = None) -> FittedRequest:
    """@brief 请求低于阈值时原样放进结果；否则按第四章步骤浓缩进度层，每步重计，仍超限即容量故障。"""
    frozen = metadata if isinstance(metadata, ModelMetadata) else ModelMetadata.model_validate(metadata)
    reserve = request.get("max_tokens")
    if type(reserve) is not int or not 0 < reserve <= frozen.max_output_tokens:
        logging.error("Model request output reserve exceeds verified service capacity")
        raise RuntimeError("model request output reserve exceeds verified service capacity")
    limit = model_context_length(request["model"], frozen) * CONTEXT_THRESHOLD
    measured = request_token_count(request)
    if measured < limit:
        return FittedRequest(request, measured - reserve)
    layers = list(budget.layers) if budget is not None else []
    index = next((i for i, layer in enumerate(layers) if layer.get("layer") == _PROGRESS_LAYER), None)
    if index is not None:
        records, meta = _progress_records(layers[index], budget.meta)
        for records, meta in _condensation_steps(records, meta):
            layers[index] = {**layers[index], "events": records}
            request = _with_user_layers(request, layers, budget.user_index)
            measured = request_token_count(request)
            if measured < limit:
                return FittedRequest(request, measured - reserve)
    _log_capacity_failure(request, layers, measured, limit)
    raise RuntimeError("model request capacity exceeded")


def select_compaction_tail(model: str, messages: Sequence[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]] | None:
    """@brief 倒序切出最多 6 组且不超 16000 令牌的完整交互尾部；无前序可折组时返回 None。"""
    if len(messages) <= 2:
        return None
    groups = _split_interaction_groups(messages[2:])
    if len(groups) <= 1:
        return None
    kept_start = len(groups) - 1
    for idx in range(len(groups) - 2, -1, -1):
        if len(groups) - idx > TAIL_MAX_GROUPS:
            break
        candidate = [msg for group in groups[idx:] for msg in group]
        if _count_message_tokens(model, candidate) > TAIL_MAX_TOKENS:
            break
        kept_start = idx
    if kept_start == 0:
        return None
    tail_messages = [copy.deepcopy(msg) for group in groups[kept_start:] for msg in group]
    return kept_start, tail_messages


def compact_day_session(params: CompactionInput) -> CompactionOutcome | None:
    """@brief 执行一次确定性日内 Compaction：重建静态前缀、折叠摘要、最新权威五层与保留尾部。"""
    selection = select_compaction_tail(params.model, params.messages)
    if selection is None:
        return None
    folded_groups, tail_messages = selection
    total_groups = len(_split_interaction_groups(params.messages[2:]))
    existing = _parse_user_layers(params.messages[1].get("content"))
    old_memory = _layer_by_name(existing, "记忆层")
    fold_summary = _build_fold_summary(params, old_memory)
    rebuilt_layers = _rebuild_user_layers(existing, old_memory, params.snapshot, fold_summary)
    rebuilt_messages = [
        {"role": "system", "content": params.day_session.system_prompt},
        {"role": "user", "content": render_layer_json(rebuilt_layers)},
        *tail_messages,
    ]
    before_tokens = params.before_tokens or prompt_token_count(
        {"model": params.model, "messages": params.messages, "tools": params.tools})
    after_tokens = prompt_token_count(
        {"model": params.model, "messages": rebuilt_messages, "tools": params.tools})
    base_count = params.compaction_count if params.compaction_count is not None else params.day_session.compaction_count
    new_count = base_count + 1
    folded_seq = fold_summary["folded_through_seq"]
    record = {
        "compaction_count": new_count, "compacted_through_seq": folded_seq,
        "before_tokens": before_tokens, "after_tokens": after_tokens,
        "folded_groups": folded_groups, "kept_tail_groups": total_groups - folded_groups,
    }
    last_layers = {key: copy.deepcopy(params.snapshot[key]) for key in FIVE_LAYER_KEYS if key in params.snapshot}
    budget = ProgressBudget(tuple(rebuilt_layers), tuple(copy.deepcopy(params.snapshot["progress_meta"])))
    return CompactionOutcome(rebuilt_messages, budget, last_layers, new_count, folded_seq, record)


def _split_interaction_groups(tail: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """@brief 将 messages[2:] 切分为完整的 (assistant, *tool) 交互组。"""
    groups: list[list[dict[str, Any]]] = []
    for msg in tail:
        role = msg.get("role")
        if role == "assistant":
            groups.append([msg])
        elif role == "tool" and groups:
            groups[-1].append(msg)
        else:
            logging.error("Invalid message role sequence at compaction boundary: %s", role)
            raise ProtocolError(f"Invalid message role sequence in day session: {role!r}")
    return groups


def _count_message_tokens(model: str, messages: list[dict[str, Any]]) -> int:
    """@brief 计量一段消息序列的输入令牌数。"""
    try:
        return token_counter(model=model, messages=messages)
    except Exception as error:
        logging.error("Tail message token count failed: %s", error)
        raise RuntimeError("model request token count failed") from error


def _parse_user_layers(content: Any) -> list[dict[str, Any]]:
    """@brief 从 user 消息 JSON 串解析六层对象列表；损坏时返回空列表并记录错误。"""
    if not isinstance(content, str) or not content.strip():
        return []
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as error:
        logging.error("Failed to parse user layers JSON: %s", error)
        return []
    if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
        return parsed
    return []


def _layer_by_name(layers: Sequence[dict[str, Any]], name: str) -> dict[str, Any]:
    """@brief 按层名提取唯一的层对象；缺失时记录错误并抛出 ProtocolError。"""
    found = [layer for layer in layers if layer.get("layer") == name]
    if len(found) != 1:
        logging.error("Compaction requires exactly one %s layer, found %d", name, len(found))
        raise ProtocolError(f"Day session user layers missing unique {name}")
    return found[0]


def _rebuild_user_layers(
    existing: Sequence[dict[str, Any]],
    old_memory: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    fold_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """@brief 按 USER_LAYER_NAMES 顺序重建六层列表，保持静态人设与可查/近三天不变。"""
    persona = copy.deepcopy(_layer_by_name(existing, "人设层"))
    new_memory = {
        "layer": "记忆层",
        "可查": copy.deepcopy(old_memory["可查"]),
        "近三天": copy.deepcopy(old_memory["近三天"]),
        "今天": copy.deepcopy(snapshot["memory_layer"]["今天"]),
        "折叠摘要": fold_summary,
    }
    by_name = {
        "人设层": persona,
        "记忆层": new_memory,
        "待办层": copy.deepcopy(snapshot["todo_layer"]),
        "工具层": copy.deepcopy(snapshot["tool_layer"]),
        "环境层": copy.deepcopy(snapshot["environment_layer"]),
        "进度层": copy.deepcopy(snapshot["progress_layer"]),
    }
    return [by_name[name] for name in USER_LAYER_NAMES]


def _build_fold_summary(params: CompactionInput, old_memory: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 从当日已提交事件、记忆层今天条目与最新待办/环境快照确定性生成折叠摘要。"""
    today_entries = list(params.snapshot["memory_layer"].get("今天") or old_memory.get("今天") or ())
    events = params.settled_day_events
    seqs = [ev["event_seq"] for ev in events if type(ev.get("event_seq")) is int]
    folded_seq = max(seqs, default=params.day_session.compacted_through_seq)
    until_clock = _latest_clock(events, today_entries, params.snapshot["environment_layer"])
    completed = _dedup(_activity_notes(events, today_entries))
    places = _dedup(_place_notes(events, today_entries))
    purchases = _dedup(_purchase_notes(events, today_entries))
    body_notes = _dedup(_body_and_balance_notes(events, params.snapshot["environment_layer"]))
    commitments = _dedup(_open_commitments(params.snapshot["todo_layer"]))
    return {
        "folded_through_seq": folded_seq,
        "folded_until_clock": until_clock,
        "completed_activities": completed,
        "places_visited": places,
        "purchases": purchases,
        "body_and_balance_notes": body_notes,
        "open_commitments": commitments,
    }


def _latest_clock(events: Sequence[Mapping[str, Any]], entries: Sequence[Mapping[str, Any]],
                  env_layer: Mapping[str, Any]) -> str:
    """@brief 计算已折叠区间最晚事件本地时刻 HH:MM。"""
    clocks: list[str] = []
    for ev in events:
        candidate = ev.get("clock") or ev.get("时刻")
        if not isinstance(candidate, str) and isinstance(ev.get("occurred_at"), str):
            candidate = ev["occurred_at"][11:16]
        if isinstance(candidate, str) and _CLOCK_PATTERN.match(candidate):
            clocks.append(candidate)
    for entry in entries:
        clock = entry.get("时刻")
        if isinstance(clock, str) and _CLOCK_PATTERN.match(clock):
            clocks.append(clock)
    if clocks:
        return max(clocks)
    observed = env_layer.get("observed_at")
    if isinstance(observed, str) and len(observed) >= 16 and _CLOCK_PATTERN.match(observed[11:16]):
        return observed[11:16]
    return "00:00"


def _activity_notes(events: Sequence[Mapping[str, Any]], entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """@brief 提取当日已结束主/副活动摘要列表（按发生时刻升序）。"""
    notes: list[str] = []
    for ev in events:
        text = ev.get("completed_activity") or ev.get("activity_summary")
        if isinstance(text, str) and text.strip():
            clock = ev.get("clock")
            notes.append(f"{clock} {text.strip()}" if isinstance(clock, str) and clock else text.strip())
    for entry in entries:
        clock = entry.get("时刻", "")
        for line in entry.get("核验") or ():
            if isinstance(line, str) and line.strip():
                notes.append(f"{clock} {line.strip()}".strip())
    return notes


def _place_notes(events: Sequence[Mapping[str, Any]], entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """@brief 提取当日已抵达地点显示名列表（按访问先后）。"""
    places: list[str] = []
    for ev in events:
        place = ev.get("place") or ev.get("location_name")
        if isinstance(place, str) and place.strip():
            places.append(place.strip())
    for entry in entries:
        for line in entry.get("核验") or ():
            if isinstance(line, str) and line.startswith("到") and len(line) > 1:
                places.append(line.split("，", 1)[0].removeprefix("到").strip())
    return places


def _purchase_notes(events: Sequence[Mapping[str, Any]], entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """@brief 提取当日已完成购买商品与渠道摘要列表。"""
    items: list[str] = []
    for ev in events:
        purchase = ev.get("purchase_summary") or ev.get("purchase")
        if isinstance(purchase, str) and purchase.strip():
            items.append(purchase.strip())
    for entry in entries:
        for line in entry.get("核验") or ():
            if isinstance(line, str) and any(mark in line for mark in ("购买", "下单", "送到", "领取", "买")):
                items.append(line.strip())
    return items


def _body_and_balance_notes(events: Sequence[Mapping[str, Any]], env_layer: Mapping[str, Any]) -> list[str]:
    """@brief 提取当日关键消费、收货与体感状态变化摘要。"""
    notes: list[str] = []
    for ev in events:
        note = ev.get("body_or_balance_note") or ev.get("balance_note")
        if isinstance(note, str) and note.strip():
            notes.append(note.strip())
    body = env_layer.get("body")
    if isinstance(body, Mapping) and body:
        parts = [f"{key}:{val}" for key, val in body.items() if isinstance(val, str)]
        if parts:
            notes.append("当前体感：" + "，".join(parts))
    return notes


def _open_commitments(todo_layer: Mapping[str, Any]) -> list[str]:
    """@brief 从待办层提取当前尚未了结的目标、个人事务、在途订单与暂停活动摘要。"""
    items: list[str] = []
    for act in todo_layer.get("paused_activities") or ():
        if isinstance(act, Mapping) and act.get("name"):
            items.append(f"暂停活动：{act['name']}")
    for err in todo_layer.get("errands") or ():
        if isinstance(err, Mapping) and err.get("name"):
            items.append(f"个人事务：{err['name']}（{err.get('progress', '')}）")
    for order in todo_layer.get("orders") or ():
        if isinstance(order, Mapping) and order.get("order"):
            items.append(f"在途订单：{order['order']}")
    plan = todo_layer.get("day_plan")
    if isinstance(plan, Mapping):
        for goal in plan.get("goals") or ():
            if isinstance(goal, Mapping) and goal.get("goal"):
                items.append(f"今日目标[{goal.get('intent_kind', '')}]：{goal['goal']}")
    return items


def _dedup(items: Sequence[str]) -> list[str]:
    """@brief 保留顺序去重并过滤空串。"""
    return list(dict.fromkeys(item for item in items if isinstance(item, str) and item.strip()))


def _progress_records(layer: dict[str, Any], meta: tuple[dict, ...]) -> tuple[list[dict], list[dict]]:
    """@brief 复制进度层记录与逐条对齐的私有元数据；两者条数不一致是损坏的宿主观察。"""
    records = copy.deepcopy(layer["events"])
    if len(records) != len(meta):
        logging.error("Progress metadata does not align with progress records")
        raise RuntimeError("progress metadata does not align with progress records")
    return records, [dict(item) for item in meta]


def _condensation_steps(records: list[dict], meta: list[dict]):
    """@brief 逐步产出浓缩后的（记录, 元数据）：逐段把最旧一段连续的未受保护记录折为 condensed 记录。"""
    while (run := _oldest_foldable_run(records, meta)) is not None:
        start, end = run
        folded = records[start:end]
        lines = [line for item in meta[start:end] for line in item["lines"]]
        condensed = {"occurred_at": folded[0]["occurred_at"], "type": _CONDENSED,
                     "until_at": folded[-1]["occurred_at"], "lines": lines}
        records = [*records[:start], condensed, *records[end:]]
        meta = [*meta[:start], {"protected": False, "lines": lines}, *meta[end:]]
        yield records, meta


def _oldest_foldable_run(records: list[dict], meta: list[dict]) -> tuple[int, int] | None:
    """@brief 最旧一段连续的未受保护、非 condensed 记录的区间 [start, end)，紧邻其前的 condensed 记录并入；没有时为 None。"""
    for start, record in enumerate(records):
        if meta[start]["protected"] or record["type"] == _CONDENSED:
            continue
        end = start
        while end < len(records) and not meta[end]["protected"] and records[end]["type"] != _CONDENSED:
            end += 1
        if start > 0 and records[start - 1]["type"] == _CONDENSED:
            start -= 1
        return start, end
    return None


def _with_user_layers(request: dict[str, Any], layers: list[dict], user_index: int) -> dict[str, Any]:
    """@brief 只替换 user 消息中的层数组，不改其他消息与观察事件。"""
    candidate = copy.deepcopy(request)
    candidate["messages"][user_index] = {**candidate["messages"][user_index],
                                         "content": render_layer_json(layers)}
    return candidate


def _log_capacity_failure(request: dict[str, Any], layers: list[dict], measured: int, limit: float) -> None:
    """@brief 按系统层、工具定义、各 user 层、此后的原生消息与预留输出分层记录 token 用量。"""
    resolved_layers = layers
    if not resolved_layers and len(request.get("messages", [])) > 1:
        resolved_layers = _parse_user_layers(request["messages"][1].get("content"))
    counts: dict[str, int] = {
        "system": token_counter(model=request["model"], messages=request["messages"][:1]),
        "tools": token_counter(model=request["model"], messages=[], tools=request.get("tools")),
    }
    for layer in resolved_layers:
        counts[str(layer.get("layer"))] = token_counter(model=request["model"], text=render_layer_json(layer))
        if isinstance(layer.get(_INDEX_KEY), dict):
            counts[f"{layer['layer']}.{_INDEX_KEY}"] = token_counter(model=request["model"],
                                                                     text=render_layer_json(layer[_INDEX_KEY]))
    counts["followup_messages"] = token_counter(model=request["model"], messages=request["messages"][2:])
    counts["reserved_output"] = request["max_tokens"]
    logging.error("Model request capacity exceeded: total=%s threshold=%s parts=%s", measured, limit, counts)


def compare_usage(estimate: int, response: dict[str, Any] | None) -> None:
    """@brief 用供应商返回的实际输入 token 校核发送前的估算；估算是容量预检为同一请求计量的数，这里不再重数。"""
    usage = response.get("usage") if isinstance(response, dict) else None
    actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    if not isinstance(actual, int):
        return
    logging.info("Model prompt token estimate checked: estimated=%s actual=%s difference=%s",
                 estimate, actual, actual - estimate)
