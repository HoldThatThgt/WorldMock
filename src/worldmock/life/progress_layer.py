"""@brief 进度层：当前 session 的有序记录（docs/dev/SPEC-persona-prompt.md「进度层」）。

记录由已提交事件组装，写到本次观察这一刻：人物的世界请求连同最新结果与回复正文（self_note）、此前决策里成功的记忆查询
（结果只写条数）、收到的直接消息与交谈邀请（全文）和邮件（主题）、订单送达，以及由截止时刻与提醒写入时刻推导的截止预警
与目标提醒。session 下界：由决策引起的切换（出发、途中结束出行）取引起切换的那条结果的事件序号；运行开始与到达取 session
的开始时刻。同一秒内推导记录在前（截止在提醒前），事件记录按最小来源事件序号。结果游标及其文本嗅探已删除：结果在整个
session 内保留。每条记录另有私有元数据（是否受保护、settled 句），供宿主按预算浓缩。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from worldmock.errors import IntegrityError
from worldmock.footprints.models import Event
from worldmock.footprints.store import FootprintStore
from worldmock.life import sessions
from worldmock.life.models import WorldState
from worldmock.life.readable import iso_time
from worldmock.life.recipients import agent_identity, person_name
from worldmock.life.request_linking import order_label
from worldmock.life.result_projection import public_result
from worldmock.life.settled_text import PauseIntervals, record_lines
from worldmock.life.tool_layer import LayerContext

#: 记忆查询工具（结果只写条数）。
MEMORY_TOOLS = frozenset({"memory_recent", "memory_search", "memory_time_range"})
#: 截止预警提前的秒数（与唤醒同源）。
DUE_SOON_SECONDS = 1800
#: 订单仍在途的状态（相关记录受保护）。
_OPEN_ORDER_STATUSES = frozenset({"pending", "ready"})


@dataclass(frozen=True, slots=True)
class ProgressView:
    """@brief 一次观察的进度层与私有配套：layer 为进度层 JSON，meta 与 layer.events 一一对应，
    event_ids 为全部记录来源事件身份（记录顺序），presented_message_ids 为全文已呈现的收件箱消息。"""

    layer: dict
    meta: list[dict]
    event_ids: list[str]
    presented_message_ids: list[str]


@dataclass(frozen=True, slots=True)
class _Record:
    """@brief 组装中的一条记录：记录本身、来源事件、排序键与相关订单。"""

    record: dict
    sources: tuple[Event, ...]
    key: tuple
    orders: tuple[str, ...] = ()
    message_id: str | None = None


def session_bound(store: FootprintStore, state: WorldState, agent_id: int) -> tuple[int, int | None]:
    """@brief 当前 session 的下界：(session 开始时刻, 引起切换的那条结果的事件序号；时间下界的 session 为 None)。"""
    session = sessions.current_session(state, agent_id)
    if session is None:
        logging.error("Agent has no active session for the progress layer")
        raise IntegrityError("Agent has no active session")
    if session.opened_by_action_id is None:
        return session.started_at, None
    results = [event for event in store.read_agent_events("action_result", agent_id)
               if event.action_id == session.opened_by_action_id]
    if not results:
        logging.error("Session opening action has no committed result")
        raise IntegrityError("Session opening action has no committed result")
    return session.started_at, results[0].event_seq


def progress_view(context: LayerContext, store: FootprintStore, pauses: PauseIntervals | None = None) -> ProgressView:
    """@brief 进度层 JSON 与私有配套（progress_meta、progress_event_ids、presented_message_ids）。

    :param pauses: 本人活动的暂停区间（settled 句中结束活动的起止含暂停区间，与记忆层同一写法）。
    """
    started_at, after_seq = session_bound(store, context.state, context.agent_id)
    records = _session_records(context, store, (started_at, after_seq))
    request_seq, request_time = _last_request(store, context.agent_id)
    meta = [{"protected": _protected(context, item, (request_seq, request_time)),
             "lines": record_lines(context, item.record, list(item.sources), pauses)} for item in records]
    layer = {"layer": "进度层", "session_started_at": iso_time(started_at, context.scenario.timezone),
             "events": [item.record for item in records]}
    return ProgressView(layer=layer, meta=meta,
                        event_ids=[event.event_id for item in records for event in item.sources],
                        presented_message_ids=[item.message_id for item in records if item.message_id is not None])


def result_object(context: LayerContext, result_event: Event) -> dict:
    """@brief 一条 action_result 事件的公开结果（与进度层 tool_call.result 同一投影）。"""
    return public_result(result_event.data, context.scenario, context.state.current_time)


def records_after(context: LayerContext, store: FootprintStore, after_seq: int) -> list[tuple[dict, list[str]]]:
    """@brief 当前 session 中来源事件序号都大于 after_seq 的事件记录及其来源身份（推导记录没有来源，不在其中）。"""
    bound = session_bound(store, context.state, context.agent_id)
    return [(item.record, [event.event_id for event in item.sources])
            for item in _session_records(context, store, bound)
            if item.sources and all(event.event_seq > after_seq for event in item.sources)]


def _session_records(context: LayerContext, store: FootprintStore, bound: tuple[int, int | None]) -> list[_Record]:
    """@brief 当前 session 的全部记录，按发生时刻、推导优先、最小来源序号排序。"""
    started_at, after_seq = bound
    lower = after_seq if after_seq is not None else store.last_event_seq_before(started_at)

    def within(event: Event) -> bool:
        """@brief 事件在 session 区间内：序号下界之后，时间下界的 session 另要求不早于开始时刻。"""
        return event.event_seq > lower and (after_seq is not None or event.occurred_at >= started_at)

    records = _tool_records(context, store, lower, within)
    records += _memory_records(context, store, lower, within)
    records += _world_records(context, store, lower, within)
    records += _derived_records(context, bound)
    return sorted(records, key=lambda item: item.key)


def _event_key(occurred_at: int, sources: list[Event]) -> tuple:
    """@brief 事件记录的排序键：发生时刻，推导记录之后，按最小来源序号。"""
    return (occurred_at, 1, 0, 0, min(event.event_seq for event in sources))


def _tool_records(context: LayerContext, store: FootprintStore, lower: int, within) -> list[_Record]:
    """@brief 本 session 的世界请求：每个 action_requested 连同其最新结果（即时结果另作来源）与 self_note。

    开始活动的请求在活动结束后带终态结果：浏览得到的商品名、到达等事实只在终态结果里，唯一出现原则要求它们写在该调用的
    结果上（docs/dev/SPEC-persona-prompt.md「唯一出现原则」「进度层」）；活动目标的 state 只写终局说法与客观进度。
    """
    requests = [event for event in store.read_agent_events("action_requested", context.agent_id, after=lower)
                if within(event)]
    results = store.read_agent_events("action_result", context.agent_id, after=lower)
    records = []
    for index, request in enumerate(requests):
        later = next((item.event_seq for item in requests[index + 1:] if item.action_id == request.action_id), None)
        own = [event for event in results if event.action_id == request.action_id
               and event.event_seq > request.event_seq and (later is None or event.event_seq < later)]
        if not own:
            continue
        sources = [request, own[0]] + ([own[-1]] if len(own) > 1 else [])
        records.append(_Record(_tool_record(context, request, own), tuple(sources),
                               _event_key(request.occurred_at, sources), _order_ids(own)))
    return records


def _tool_record(context: LayerContext, request: Event, results: list[Event]) -> dict:
    """@brief tool_call 记录：工具、参数（conclude_activity 略去关系与偏好记忆）、最新结果与可选的 self_note。

    开始活动的请求其后的终态结果不带当时的订单，公开结果的 purchases 取即时结果的订单。
    """
    data = request.data
    result = public_result(results[-1].data, context.scenario, context.state.current_time)
    first = public_result(results[0].data, context.scenario, context.state.current_time)
    if "purchases" in first["details"] and "purchases" not in result["details"]:
        result["details"] = {"purchases": first["details"]["purchases"], **result["details"]}
    record = {"occurred_at": iso_time(request.occurred_at, context.scenario.timezone), "type": "tool_call",
              "tool": data["tool_name"], "arguments": _arguments(data["tool_name"], data["request"]), "result": result}
    if data.get("self_note"):
        record["self_note"] = data["self_note"]
    return record


def _arguments(tool: str, request: dict) -> dict:
    """@brief 请求参数原文；conclude_activity 的 memories 略去 relationship 与 preference 条目（只在人设层出现）。"""
    if tool != "conclude_activity":
        return request
    memories = [[item for item in group if item.get("type") not in {"relationship", "preference"}]
                for group in request.get("memories", [])]
    return {**request, "memories": memories}


def _order_ids(results: list[Event]) -> tuple[str, ...]:
    """@brief 结果中出现的订单身份（顶层与逐项结果）。"""
    identities = []
    for event in results:
        payload = event.data.get("data") or {}
        identities.extend(order["order_id"] for order in payload.get("orders", []))
    return tuple(dict.fromkeys(identities))


def _memory_records(context: LayerContext, store: FootprintStore, lower: int, within) -> list[_Record]:
    """@brief 此前决策里成功的记忆查询：模型响应中的查询调用配对下一请求中的工具结果，结果只写条数；失败的不记。"""
    agent_id, timezone_name = context.agent_id, context.scenario.timezone
    responses = [event for event in store.read_agent_events("model_response", agent_id, after=lower)
                 if within(event) and event.data.get("phase") == "response" and event.data.get("error") is None]
    requests = [event for event in store.read_agent_events("model_request", agent_id, after=lower) if within(event)]
    records = []
    for response in responses:
        following = next((event for event in requests if event.decision_id == response.decision_id
                          and event.event_seq > response.event_seq), None)
        if following is None:
            continue
        for call in _tool_calls(response):
            count = _episode_count(following, call.get("id"))
            if call.get("function", {}).get("name") not in MEMORY_TOOLS or count is None:
                continue
            records.append(_Record(_memory_record(response, call, count, timezone_name), (response, following),
                                   _event_key(response.occurred_at, [response, following])))
    return records


def _tool_calls(response: Event) -> list[dict]:
    """@brief 模型响应第一条选择里的工具调用列表（形状不符时为空）。"""
    raw = response.data.get("response")
    choices = raw.get("choices") if isinstance(raw, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _episode_count(request: Event, call_id: str | None) -> int | None:
    """@brief 下一请求中对应 tool_call_id 的工具消息里的条目数；失败（带 error）或找不到时为 None。"""
    messages = ((request.data.get("request") or {}).get("messages")) or []
    content = next((item.get("content") for item in messages if isinstance(item, dict)
                    and item.get("role") == "tool" and item.get("tool_call_id") == call_id), None)
    try:
        payload = json.loads(content) if isinstance(content, str) else None
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "error" in payload or not isinstance(payload.get("episodes"), list):
        return None
    return len(payload["episodes"])


def _memory_record(response: Event, call: dict, count: int, timezone_name: str) -> dict:
    """@brief 记忆查询的 tool_call 记录：参数原文，结果只写条数，时刻为该决策时刻。"""
    try:
        arguments = json.loads(call["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        arguments = {}
    moment = iso_time(response.occurred_at, timezone_name)
    return {"occurred_at": moment, "type": "tool_call", "tool": call["function"]["name"],
            "arguments": arguments if isinstance(arguments, dict) else {},
            "result": {"occurred_at": moment, "status": "completed", "error_code": None,
                       "message": f"Memory query returned {count} episodes", "details": {"episodes": count}}}


def _world_records(context: LayerContext, store: FootprintStore, lower: int, within) -> list[_Record]:
    """@brief 本人收到的消息（直接消息与交谈邀请带全文，邮件只有主题）与本人订单的送达。"""
    records = []
    for event in store.read_agent_events("environment_event", context.agent_id, after=lower):
        kind = event.data.get("kind")
        if not within(event) or kind not in {"message_arrival", "mail_arrival", "delivery_arrival"}:
            continue
        if kind == "delivery_arrival":
            records.extend(_delivery_records(context, event))
        else:
            records.append(_message_record(context, event))
    return records


def _message_record(context: LayerContext, event: Event) -> _Record:
    """@brief message_arrival 记录：直接消息与交谈邀请取收件箱全文与发件人，交谈邀请另写回应方式「对<发起人>选择交谈
    即接受」（docs/dev/SPEC-time-and-energy.md 第五章「交谈」）；邮件取场景主题，发件人为 null。"""
    moment = iso_time(event.occurred_at, context.scenario.timezone)
    message_id = event.data["message_id"]
    if event.data["kind"] == "mail_arrival":
        subject = context.rules.messages[message_id].subject
        record = {"occurred_at": moment, "type": "message_arrival", "sender": None, "kind": "mail",
                  "subject": subject}
        return _Record(record, (event,), _event_key(event.occurred_at, [event]))
    message = context.state.agents[context.agent_id].inbox[message_id]
    sender = _sender(context, message.sender_id)
    text = message.text if message.kind != "chat_invite" else f"{message.text}，对{sender}选择交谈即接受"
    record = {"occurred_at": moment, "type": "message_arrival", "sender": sender, "kind": message.kind,
              "text": text}
    return _Record(record, (event,), _event_key(event.occurred_at, [event]), message_id=message_id)


def _sender(context: LayerContext, sender_id: str) -> str:
    """@brief 发件人的可读名称：同世界人物取姓名，场景联系人取称谓。"""
    return person_name(context.state, context.scenario, context.agent_id, sender_id)


def _delivery_records(context: LayerContext, event: Event) -> list[_Record]:
    """@brief 一次送达事件中本人所下每个订单一条 delivery 记录：订单名称、状态与送达进展（收货人不是自己时写明收货人）。"""
    zone, now = context.scenario.timezone, context.state.current_time
    places = {item.location_id: item.name for item in context.scenario.locations}
    names = {item_id: item.name for item_id, item in context.rules.items.items()}
    records = []
    for order in event.data.get("orders", []):
        if order["agent_id"] != context.agent_id:
            continue
        label = order_label(order, names, (zone, now))
        receiver = ("" if order["recipient_id"] == agent_identity(context.agent_id) else
                    f"，收货人{person_name(context.state, context.scenario, context.agent_id, order['recipient_id'])}")
        messages = {"delivered": f"已送到{places[order['destination_id']]}{receiver}", "cancelled": "订单已取消",
                    "ready": f"可以到{places[order['location_id']]}取了"}
        record = {"occurred_at": iso_time(event.occurred_at, zone), "type": "delivery", "order": label,
                  "status": order["status"], "message": messages[order["status"]]}
        records.append(_Record(record, (event,), _event_key(event.occurred_at, [event]), (order["order_id"],)))
    return records


def _derived_records(context: LayerContext, bound: tuple[int, int | None]) -> list[_Record]:
    """@brief 由截止时刻与提醒写入时刻推导的记录：工具层每个任务的截止预警与到期，今日目标的提醒，各只一次。"""
    started_at, after_seq = bound
    now, zone = context.state.current_time, context.scenario.timezone

    def in_range(moment: int) -> bool:
        """@brief 时刻落在本 session 内且不晚于此刻；决策切换的 session 不含切换那一秒（已在前一 session）。"""
        return (moment > started_at if after_seq is not None else moment >= started_at) and moment <= now

    records = []
    tasks = [row for row in context.bindings if row["kind"] == "work_task"]
    for order, row in enumerate(tasks):
        due = int(context.rules.tasks[row["object_id"]].due_at.timestamp())
        for moment, stage in ((due - DUE_SOON_SECONDS, "due_soon"), (due, "expired")):
            if in_range(moment):
                record = {"occurred_at": iso_time(moment, zone), "type": "task_deadline", "task": row["object"],
                          "stage": stage}
                records.append(_Record(record, (), (moment, 0, 0, order, 0)))
    plan = context.state.agents[context.agent_id].day_plan
    for order, goal in enumerate(plan.goals if plan is not None else ()):
        if goal.reminded_at is not None and in_range(goal.reminded_at):
            record = {"occurred_at": iso_time(goal.reminded_at, zone), "type": "plan_reminder", "goal": goal.goal}
            records.append(_Record(record, (), (goal.reminded_at, 0, 1, order, 0)))
    return records


def _last_request(store: FootprintStore, agent_id: int) -> tuple[int, int | None]:
    """@brief 最近一次实际行动请求的事件序号与发生时刻（没有时为 0 与 None）。"""
    seq = store.last_agent_action_request_seq(agent_id)
    if seq == 0:
        return 0, None
    return seq, store.read_agent_events("model_request", agent_id, after=seq - 1)[0].occurred_at


def _protected(context: LayerContext, item: _Record, request: tuple[int, int | None]) -> bool:
    """@brief 受保护：上次行动请求之后的新记录，或与仍在途订单相关的记录（预算浓缩永不改动）。"""
    seq, moment = request
    if item.sources and any(event.event_seq > seq for event in item.sources):
        return True
    if not item.sources:
        occurred = int(datetime.fromisoformat(item.record["occurred_at"]).timestamp())
        if moment is None or occurred > moment:
            return True
    return any(context.state.deliveries[order].status in _OPEN_ORDER_STATUSES
               for order in item.orders if order in context.state.deliveries)
