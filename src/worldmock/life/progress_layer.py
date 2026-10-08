"""@brief 进度层：当前 session 的有序记录（docs/design/06-agent.md「进度层」）。

记录由已提交事件组装，写到本次观察这一刻：人物的世界请求连同最新结果与回复正文（self_note）、收到的直接消息
与交谈邀请（全文）和邮件（主题）、订单送达、个人事务的提醒到点与办成（只写事务名，由人物可见的事务事件派生），
以及由截止时刻与提醒写入时刻推导的截止预警与目标提醒。session 下界是开启它的边界提交的末事件序号（session_bounds，
与记忆层同一区间口径）：运行开始为 0，到达取到达所在的时钟推进提交，由决策引起的切换（出发、途中结束出行）取引起
切换的那条结果所在的提交；推导记录按时刻取，决策切换的 session 不含切换那一秒。同一秒内推导记录在前（截止在提醒前），
事件记录按最小来源事件序号。结果游标及其文本嗅探已删除：结果在整个 session 内保留。每条记录另有私有元数据
（是否受保护、settled 句），供宿主按预算浓缩。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from worldmock.errors import IntegrityError
from worldmock.footprints.models import Event
from worldmock.footprints.store import FootprintStore
from worldmock.life import schedule, sessions
from worldmock.life.control_items import CONTROL_TOOLS, control_request
from worldmock.life.errand_state import COMPLETED_KIND, REMINDED_KIND
from worldmock.life.models import WorldState
from worldmock.life.readable import iso_time, local_date
from worldmock.life.recipients import agent_identity, person_name
from worldmock.life.request_linking import order_label
from worldmock.life.result_projection import public_result
from worldmock.life.session_bounds import session_lower
from worldmock.life.settled_text import ActivityFacts, record_lines
from worldmock.life.tool_layer import LayerContext

#: 订单仍在途的状态（相关记录受保护）。
OPEN_ORDER_STATUSES = frozenset({"pending", "ready_for_pickup"})
#: 被进度层消费的两种个人事务事件（人物可见）→ 记录类型；其余四种事务事件只供审计与指标，不进进度层。
_ERRAND_RECORDS = {REMINDED_KIND: "errand_reminder", COMPLETED_KIND: "errand_completed"}


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


def session_bound(store: FootprintStore, state: WorldState, agent_id: int) -> tuple[int, int]:
    """@brief 当前 session 的 (开始时刻, 事件序号下界)：下界是开启它的边界提交的末事件序号（运行开始为 0）。"""
    session = sessions.current_session(state, agent_id)
    if session is None:
        logging.error("Agent has no active session for the progress layer")
        raise IntegrityError("Agent has no active session")
    return session.started_at, session_lower(store, state, session)


def progress_view(context: LayerContext, store: FootprintStore, facts: ActivityFacts | None = None) -> ProgressView:
    """@brief 进度层 JSON 与私有配套（progress_meta、progress_event_ids、presented_message_ids）。

    :param facts: 本人活动的暂停区间与结束事件（记忆层同一份）；结束活动的调用在 settled 句中只写在本 session 内结束的
        活动（起止含暂停区间，与记忆层同一写法），更早 session 结束的已在记忆层。
    """
    started_at, lower = session_bound(store, context.state, context.agent_id)
    records = _session_records(context, store, (started_at, lower))
    request_seq, request_time = _last_request(store, context.agent_id)
    here = None if facts is None else ActivityFacts(facts.pauses, {
        activity_id: event for activity_id, event in facts.endings.items() if event.event_seq > lower},
        facts.app_progress)
    meta = [{"protected": _protected(context, item, (request_seq, request_time)),
             "lines": record_lines(context, item.record, list(item.sources), here)} for item in records]
    layer = {"layer": "进度层", "session_started_at": iso_time(started_at, context.scenario.timezone),
             "events": [item.record for item in records], "rejected_today": rejected_today(context, store)}
    return ProgressView(layer=layer, meta=meta,
                        event_ids=[event.event_id for item in records for event in item.sources],
                        presented_message_ids=[item.message_id for item in records if item.message_id is not None])


def records_after(context: LayerContext, store: FootprintStore, after_seq: int) -> list[tuple[dict, list[str]]]:
    """@brief 当前 session 中来源事件序号都大于 after_seq 的事件记录及其来源身份（推导记录没有来源，不在其中）。"""
    bound = session_bound(store, context.state, context.agent_id)
    return [(item.record, [event.event_id for event in item.sources])
            for item in _session_records(context, store, bound)
            if item.sources and all(event.event_seq > after_seq for event in item.sources)]


def _session_records(context: LayerContext, store: FootprintStore, bound: tuple[int, int]) -> list[_Record]:
    """@brief 当前 session 的全部记录（事件序号在下界之后），按发生时刻、推导优先、最小来源序号排序。"""
    started_at, lower = bound
    records = _tool_records(context, store, lower)
    records += _world_records(context, store, lower)
    records += _derived_records(context, started_at)
    return sorted(records, key=lambda item: item.key)


def _event_key(occurred_at: int, sources: list[Event]) -> tuple:
    """@brief 事件记录的排序键：发生时刻，推导记录之后，按最小来源序号。"""
    return (occurred_at, 1, 0, 0, min(event.event_seq for event in sources))


def _tool_records(context: LayerContext, store: FootprintStore, lower: int) -> list[_Record]:
    """@brief 本 session 的世界请求：每个 action_requested 连同其最新结果（即时结果另作来源）与 self_note。

    开始活动的请求在活动结束后带终态结果：浏览得到的商品名、到达等事实只在终态结果里，唯一出现原则要求它们写在该调用的
    结果上（docs/design/06-agent.md「唯一出现原则」「进度层」）；活动目标的 state 只写终局说法与客观进度。
    """
    requests = store.read_agent_events("action_requested", context.agent_id, after=lower)
    results = store.read_agent_events("action_result", context.agent_id, after=lower)
    records: list[_Record] = []
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
    """@brief tool_call 记录：工具、参数（conclude_activity 略去看法与偏好）、最新结果与可选的 self_note。

    开始活动的请求其后的终态结果不带当时的订单，公开结果的 purchases 取即时结果的订单。
    """
    data = request.data
    result = public_result(results[-1].data, context.scenario)
    first = public_result(results[0].data, context.scenario)
    if "purchases" in first["details"] and "purchases" not in result["details"]:
        result["details"] = {"purchases": first["details"]["purchases"], **result["details"]}
    record = {"occurred_at": iso_time(request.occurred_at, context.scenario.timezone), "type": "tool_call",
              "tool": data["tool_name"], "arguments": _arguments(data["tool_name"], data["request"]), "result": result}
    if data.get("self_note"):
        record["self_note"] = data["self_note"]
    return record


def _arguments(tool: str, request: dict) -> dict:
    """@brief 请求参数原文；三个控制工具经 control_items 读出后按现行形状写出（每项活动名、自述，结束另带普通记忆），
    看法与偏好（views 派生的 relationship 与 preference 记忆）略去，只在人设层出现。"""
    if tool not in CONTROL_TOOLS:
        return request
    return control_request(tool, request)


def _order_ids(results: list[Event]) -> tuple[str, ...]:
    """@brief 结果中出现的订单身份（顶层与逐项结果）。"""
    identities = []
    for event in results:
        payload = event.data.get("data") or {}
        identities.extend(order["order_id"] for order in payload.get("orders", []))
    return tuple(dict.fromkeys(identities))


def _tool_calls(response: Event) -> list[dict]:
    """@brief 模型响应第一条选择里的工具调用列表（形状不符时为空）。"""
    raw = response.data.get("response")
    choices = raw.get("choices") if isinstance(raw, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _world_records(context: LayerContext, store: FootprintStore, lower: int) -> list[_Record]:
    """@brief 本人收到的消息（直接消息与交谈邀请带全文，邮件只有主题）、本人订单的送达，以及个人事务的提醒到点与办成。"""
    records: list[_Record] = []
    for event in store.read_agent_events("environment_event", context.agent_id, after=lower):
        kind = event.data.get("kind")
        if kind in _ERRAND_RECORDS:
            records.append(_errand_record(context, event))
        elif kind == "delivery_arrival":
            records.extend(_delivery_records(context, event))
        elif kind in {"message_arrival", "mail_arrival"}:
            records.append(_message_record(context, event))
        elif kind == "work_content_completed" and event.visibility == "agent":
            content = context.rules.contents[event.data["content_id"]]
            record = {"occurred_at": iso_time(event.occurred_at, context.scenario.timezone),
                      "type": "work_completed", "topic": content.title}
            records.append(_Record(record, (event,), _event_key(event.occurred_at, [event])))
        elif kind == "app_push" and event.visibility == "agent":
            record = {"occurred_at": iso_time(event.occurred_at, context.scenario.timezone), "type": "app_push",
                      "app": event.data["app"], "action": event.data["action"], "title": event.data["title"],
                      "summary": event.data["summary"]}
            records.append(_Record(record, (event,), _event_key(event.occurred_at, [event])))
    return records


def _errand_record(context: LayerContext, event: Event) -> _Record:
    """@brief 个人事务事件派生的记录（docs/design/04-simulation-engine.md ）：errand_reminded 为
    errand_reminder（提醒到点），
    errand_completed 为 errand_completed（环境确认办成），都只写事务名；来源是该事件，计入观察的进度来源。"""
    record = {"occurred_at": iso_time(event.occurred_at, context.scenario.timezone),
              "type": _ERRAND_RECORDS[event.data["kind"]], "errand": event.data["errand"]["name"]}
    return _Record(record, (event,), _event_key(event.occurred_at, [event]))


def _message_record(context: LayerContext, event: Event) -> _Record:
    """@brief message_arrival 记录：直接消息与交谈邀请取收件箱全文与发件人，交谈邀请另写回应方式「对<发起人>选择交谈
    即接受」（docs/design/04-simulation-engine.md「交谈」）；邮件到达时取场景主题与正文，发件人为 null。"""
    moment = iso_time(event.occurred_at, context.scenario.timezone)
    message_id = event.data["message_id"]
    if event.data["kind"] == "mail_arrival":
        message = context.rules.messages[message_id]
        record = {"occurred_at": moment, "type": "message_arrival", "sender": None, "kind": "mail",
                  "subject": message.subject, "body": message.body}
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
    names = {item_id: item.name for item_id, item in context.semantics.catalog.goods.items()}
    records = []
    for order in event.data.get("orders", []):
        owner = (int(order["recipient_id"].split(":", 1)[1])
                 if order["fulfillment"] == "station_pickup" else order["agent_id"])
        if owner != context.agent_id:
            continue
        label = order_label(order, names, (zone, now))
        receiver = ("" if order["recipient_id"] == agent_identity(context.agent_id) else
                    f"，收货人{person_name(context.state, context.scenario, context.agent_id, order['recipient_id'])}")
        messages = {"pending": "等待送达",
                    "ready_for_pickup": f"已到{places[order['destination_id']]}，请本人领取",
                    "delivered": f"已送到{places[order['destination_id']]}{receiver}"}
        record = {"occurred_at": iso_time(event.occurred_at, zone), "type": "delivery", "order": label,
                  "status": order["status"], "message": messages[order["status"]]}
        records.append(_Record(record, (event,), _event_key(event.occurred_at, [event]), (order["order_id"],)))
    return records


def _derived_records(context: LayerContext, started_at: int) -> list[_Record]:
    """@brief 由截止时刻与提醒写入时刻推导的记录：工具层每个任务的截止预警（提前量与唤醒同源，
    schedule.TASK_DUE_SOON_SECONDS）与到期，今日目标的提醒，各只一次。"""
    now, zone = context.state.current_time, context.scenario.timezone
    decided = sessions.current_session(context.state, context.agent_id).opened_by_action_id is not None

    def in_range(moment: int) -> bool:
        """@brief 时刻落在本 session 内且不晚于此刻；决策切换的 session 不含切换那一秒（已在前一 session）。"""
        return (moment > started_at if decided else moment >= started_at) and moment <= now

    records = []
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
    return any(context.state.deliveries[order].status in OPEN_ORDER_STATUSES
               for order in item.orders if order in context.state.deliveries)


def rejected_today(context: LayerContext, store: FootprintStore) -> list[dict]:
    """@brief 当日真实拒绝由持久证据派生，跨会话保留且不写检查点或人物记忆。"""
    day = local_date(context.state.current_time, context.scenario.timezone)
    events = [event for kind in ("model_response", "action_rejected", "action_accepted", "action_requested")
              for event in store.read_agent_events(kind, context.agent_id)
              if event.occurred_at <= context.state.current_time
              and local_date(event.occurred_at, context.scenario.timezone) == day]
    requests = {event.action_id: event for event in events if event.event_type == "action_requested"}
    rows: dict[tuple, dict] = {}
    for event in sorted(events, key=lambda item: item.event_seq):
        if event.event_type == "action_accepted" and not event.data.get("reused"):
            _memo_accept(rows, event, requests.get(event.action_id))
            continue
        if event.event_type not in {"model_response", "action_rejected"} or event.data.get("reused"):
            continue
        report = (event.data.get("protocol_feedback") if event.event_type == "model_response" else event.data)
        if isinstance(report, dict) and report.get("error_code"):
            request = requests.get(event.action_id)
            _memo_reject(rows, event, report, request, context.scenario.timezone)
    latest = sorted(rows.values(), key=lambda item: item["sequence"], reverse=True)[:8]
    return [{key: row[key] for key in ("written", "why", "times", "last", "accepted_as")} for row in latest]


def _memo_reject(rows: dict, event: Event, report: dict, request: Event | None, zone: str) -> None:
    """@brief 同一持久拒绝内重复位置只计一次，同位置同写法跨请求累加。"""
    items = report.get("items") or [{"path": ["tool_calls"], "written": None,
                                    "why": report.get("reason") or report["error_code"]}]
    seen = set()
    for item in items:
        path = tuple(item.get("path") or ["tool_calls"])
        written = item.get("written")
        key = (path, json.dumps(written, ensure_ascii=False, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        prior = rows.get(key)
        rows[key] = {"path": path, "tool": _memo_tool(event, request, path), "written": written,
                     "why": str(item.get("why") or report.get("reason") or report["error_code"])[:60],
                     "times": 1 if prior is None else prior["times"] + 1,
                     "last": iso_time(event.occurred_at, zone), "accepted_as": None, "sequence": event.event_seq}


def _memo_tool(event: Event, request: Event | None, path: tuple) -> str | None:
    """@brief 取得真实请求工具，协议拒绝从原始工具调用取得。"""
    if request is not None:
        return request.data.get("tool_name")
    calls = _tool_calls(event)
    index = path[1] if len(path) > 1 and path[0] == "tool_calls" and isinstance(path[1], int) else 0
    return calls[index].get("function", {}).get("name") if index < len(calls) else None


def _memo_accept(rows: dict, event: Event, request: Event | None) -> None:
    """@brief 后续同工具受理时记录实际修正后的参数值，不从人物自述推测成功。"""
    if request is None:
        return
    tool = request.data.get("tool_name")
    arguments = request.data.get("request") or {}
    for row in rows.values():
        if row["tool"] != tool or row["accepted_as"] is not None:
            continue
        path = list(row["path"])
        if "arguments" in path:
            path = path[path.index("arguments") + 1:]
        elif path == ["tool_calls"]:
            path = []
        value = arguments
        for part in path:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
                value = value[part]
            else:
                break
        else:
            row["accepted_as"] = value
            row["sequence"] = event.event_seq
