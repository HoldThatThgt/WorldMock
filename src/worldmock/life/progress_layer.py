"""@brief 当前停留或路程的有序记录，只从真实人物可见事件和已交付提醒派生。

记录只读本段下界之后的事件，例外是带入的记录：用内容 app 或在地点做事的开始请求，在这项活动仍在进行或在其中结束的
每一段里都有记录（docs/design/06-agent.md「进度、待办与记忆」）。
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
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, WorldState
from worldmock.life.readable import clock_phrase, iso_time, local_date, money_phrase, portion_phrase
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
    """@brief 本段进度、浓缩元数据与实际人物可见来源。"""

    layer: dict
    meta: list[dict]
    event_ids: list[str]


@dataclass(frozen=True, slots=True)
class _Record:
    """@brief 一条记录及实际来源、排序位置和关联订单。"""

    record: dict
    sources: tuple[Event, ...]
    key: tuple
    orders: tuple[str, ...] = ()


def session_bound(store: FootprintStore, state: WorldState, agent_id: int) -> tuple[int, int]:
    """@brief 当前 session 的 (开始时刻, 事件序号下界)：下界是开启它的边界提交的末事件序号（运行开始为 0）。"""
    session = sessions.current_session(state, agent_id)
    if session is None:
        logging.error("Agent has no active session for the progress layer")
        raise IntegrityError("Agent has no active session")
    return session.started_at, session_lower(store, state, session)


def progress_view(context: LayerContext, store: FootprintStore, facts: ActivityFacts | None = None) -> ProgressView:
    """@brief 进度层 JSON 与私有浓缩元数据及真实来源事件身份。

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
    layer = {"layer": "进度层", "session_started_at": iso_time(started_at, context.config.timezone),
             "events": [item.record for item in records], "rejected_today": rejected_today(context, store)}
    return ProgressView(layer=layer, meta=meta,
                        event_ids=list(dict.fromkeys(event.event_id for item in records for event in item.sources)))


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
    """@brief 本段的世界请求记录与带入的记录：每条记录的 result 取这条请求最新的一条结果。

    开始活动的请求在活动结束后显示第二条结果：做过的内容 contents、付款后的订单、到达都写在第二条上，第一条的
    planned、content 与报价随之不再显示（docs/design/06-agent.md「进度、待办与记忆」）；活动目标的 state 只写终局说法与
    客观进度。开始请求在更早的段里、活动在本段仍在进行或在本段结束的用内容 app 或在地点做事，另带入一条记录。
    """
    requests = store.read_agent_events("action_requested", context.agent_id, after=lower)
    results = store.read_agent_events("action_result", context.agent_id, after=lower)
    return _session_calls(context, requests, results) + _carried_calls(context, store, requests, results)


def _session_calls(context: LayerContext, requests: list[Event], results: list[Event]) -> list[_Record]:
    """@brief 本段的每个 action_requested 连同它在本段的结果（第一条与最新一条作来源）与 self_note；没有结果的不出记录。"""
    records: list[_Record] = []
    for index, request in enumerate(requests):
        later = next((item.event_seq for item in requests[index + 1:] if item.action_id == request.action_id), None)
        own = [event for event in results if event.action_id == request.action_id
               and event.event_seq > request.event_seq and (later is None or event.event_seq < later)]
        if not own:
            continue
        record = _tool_record(context, request, own[-1])
        if request.data.get("self_note"):
            record["self_note"] = request.data["self_note"]
        records.append(_call_record(record, request, own))
    return records


def _carried_calls(context: LayerContext, store: FootprintStore, requests: list[Event],
                   results: list[Event]) -> list[_Record]:
    """@brief 带入的记录：开始请求与第一条结果按事件身份读出，结果取本段里的第二条，没有就取第一条；不带 self_note。

    只读当前世界与本段已经读出的事件，不为它扩大读取范围、不扫描下界之前的事件。
    """
    records: list[_Record] = []
    for activity in _carried_activities(context, requests, results):
        request = store.read_event(activity.origin_request_event_id)
        own = [store.read_event(activity.data["accepted_event_id"]),
               *(event for event in results if event.action_id == activity.origin_action_id)]
        records.append(_call_record(_tool_record(context, request, own[-1]), request, own))
    return records


def _carried_activities(context: LayerContext, requests: list[Event], results: list[Event]) -> list[ActivityState]:
    """@brief 要带入本段的活动：本人的 interact 活动，本段下界之后没有开始它的请求，而它此刻进行中，或本段里有它的
    结果（更早的请求在后面的段里只可能再得到一条结果，即第二条）。暂停着的活动只在待办层，不带入。"""
    asked = {event.action_id for event in requests}
    ended = {event.action_id for event in results}
    return [activity for _, activity in sorted(context.state.activities.items())
            if activity.agent_id == context.agent_id and activity.kind == ActivityKind.INTERACT
            and activity.origin_action_id not in asked
            and (activity.status == ActivityStatus.ACTIVE or activity.origin_action_id in ended)]


def _call_record(record: dict, request: Event, own: list[Event]) -> _Record:
    """@brief 一条 tool_call 记录连同来源（请求、第一条与最新一条结果）、排序键与关联订单。"""
    sources = [request, own[0]] + ([own[-1]] if len(own) > 1 else [])
    return _Record(record, tuple(sources), _event_key(request.occurred_at, sources), _order_ids(own))


def _tool_record(context: LayerContext, request: Event, result: Event) -> dict:
    """@brief tool_call 记录：发生时刻、工具、参数（conclude_activity 略去看法与偏好）与 result，result 取最新一条结果。"""
    data = request.data
    return {"occurred_at": iso_time(request.occurred_at, context.config.timezone), "type": "tool_call",
            "tool": data["tool_name"], "arguments": _arguments(data["tool_name"], data["request"]),
            "result": public_result(result.data, context.inputs)}


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
    """@brief 只读取人物可见的真实送达、事务、内容推送和声明的回调提示。"""
    records: list[_Record] = []
    for event in store.read_agent_events("environment_event", context.agent_id, after=lower):
        if event.visibility != "agent":
            continue
        kind = event.data.get("kind")
        if kind in _ERRAND_RECORDS:
            records.append(_errand_record(context, event))
        elif kind == "delivery_arrival":
            records.extend(_delivery_records(context, event))
        elif kind == "app_push" and event.data.get("pushed", True):
            record = {"occurred_at": iso_time(event.occurred_at, context.config.timezone), "type": "app_push",
                      "app": context.catalog.apps[event.data["app_id"]].name,
                      "title": event.data["title"], "summary": event.data["summary"]}
            records.append(_Record(record, (event,), _event_key(event.occurred_at, [event])))
        elif kind == "callback_reminder":
            record = {"occurred_at": iso_time(event.occurred_at, context.config.timezone),
                      "type": "callback_reminder", "app": context.catalog.apps[event.data["app_id"]].name,
                      "text": event.data["text"]}
            records.append(_Record(record, (event,), _event_key(event.occurred_at, [event])))
        elif kind == "balance_decreased":
            record = {"occurred_at": iso_time(event.occurred_at, context.config.timezone),
                      "type": "balance_decreased", "message": f"余额减少了 {money_phrase(event.data['amount_fen'])}"}
            records.append(_Record(record, (event,), _event_key(event.occurred_at, [event])))
    return records


def _errand_record(context: LayerContext, event: Event) -> _Record:
    """@brief 个人事务事件派生的记录（docs/design/04-simulation-engine.md）：errand_reminded 为
    errand_reminder（提醒到点），
    errand_completed 为 errand_completed（环境确认办成），都只写事务名；来源是该事件，计入观察的进度来源。"""
    record = {"occurred_at": iso_time(event.occurred_at, context.config.timezone),
              "type": _ERRAND_RECORDS[event.data["kind"]], "errand": event.data["errand"]["name"]}
    return _Record(record, (event,), _event_key(event.occurred_at, [event]))






def _delivery_records(context: LayerContext, event: Event) -> list[_Record]:
    """@brief 本人每项真实送达或到站订单各一条，不显示自动下单或支付渠道。"""
    zone, now = context.config.timezone, context.state.current_time
    places = {key: item.display_name for key, item in context.catalog.locations.items()}
    names = {key: item.name for key, item in context.catalog.goods.items()}
    records = []
    for order in event.data.get("orders", []):
        if order["agent_id"] != context.agent_id:
            continue
        messages = {"pending": "等待送达",
                    "ready_for_pickup": f"已到{places[order['destination_id']]}，请本人领取",
                    "delivered": f"已送到{places[order['destination_id']]}"}
        record = {"occurred_at": iso_time(event.occurred_at, zone), "type": "delivery",
                  "order": order_label(order, names, (zone, now)),
                  "status": order["status"], "message": messages[order["status"]]}
        records.append(_Record(record, (event,), _event_key(event.occurred_at, [event]), (order["order_id"],)))
    return records


def order_label(order: dict, names: dict[str, str], clock: tuple[str, int]) -> str:
    """@brief 订单只用商品、份数和购买时刻命名。"""
    timezone_name, now = clock
    return (f"{names[order['goods_id']]} {portion_phrase(order['quantity'])}（"
            f"{clock_phrase(order['created_at'], now, timezone_name)} 购买）")


def _derived_records(context: LayerContext, started_at: int) -> list[_Record]:
    """@brief 由截止时刻与提醒写入时刻推导的记录：工具层每个任务的截止预警（提前量与唤醒同源，
    schedule.TASK_DUE_SOON_SECONDS）与到期，今日目标的提醒，各只一次。"""
    now, zone = context.state.current_time, context.config.timezone
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
    """@brief 当日真实拒绝由持久证据派生，跨会话保留且不写检查点或人物记忆。

    同一决策的两条开始动作被拒时内容相同，只统计一次。
    """
    day = local_date(context.state.current_time, context.config.timezone)
    events = sorted((event for kind in ("model_response", "action_rejected", "action_accepted", "action_requested")
                     for event in store.read_agent_events(kind, context.agent_id)
                     if event.occurred_at <= context.state.current_time
                     and local_date(event.occurred_at, context.config.timezone) == day),
                    key=lambda item: item.event_seq)
    requests = [event for event in events if event.event_type == "action_requested"]
    rows: dict[tuple, dict] = {}
    rejected: set[str] = set()
    for event in events:
        if event.event_type == "action_accepted" and not event.data.get("reused"):
            _memo_accept(rows, event, next((item for item in requests if item.action_id == event.action_id), None))
            continue
        if event.event_type not in {"model_response", "action_rejected"} or event.data.get("reused"):
            continue
        registered = []
        if event.event_type == "action_rejected":
            if event.decision_id in rejected:
                continue
            rejected.add(event.decision_id)
            registered = [item for item in requests if item.decision_id == event.decision_id]
        report = (event.data.get("protocol_feedback") if event.event_type == "model_response" else event.data)
        if isinstance(report, dict) and report.get("error_code"):
            _memo_reject(rows, event, report, registered, context.config.timezone)
    latest = sorted(rows.values(), key=lambda item: item["sequence"], reverse=True)[:8]
    return [{key: row[key] for key in ("written", "why", "times", "last", "accepted_as")} for row in latest]


def _memo_reject(rows: dict, event: Event, report: dict, registered: list[Event], zone: str) -> None:
    """@brief 同一持久拒绝内重复位置只计一次，同位置同写法跨请求累加。

    :param registered: 环境拒绝所属决策已登记的动作请求（按登记次序）；协议拒绝为空。
    """
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
        rows[key] = {"path": path, "tool": _memo_tool(event, registered, path), "written": written,
                     "why": str(item.get("why") or report.get("reason") or report["error_code"])[:60],
                     "times": 1 if prior is None else prior["times"] + 1,
                     "last": iso_time(event.occurred_at, zone), "accepted_as": None, "sequence": event.event_seq}


def _memo_tool(event: Event, registered: list[Event], path: tuple) -> str | None:
    """@brief 取得真实请求工具：环境拒绝取明细路径里调用位置对应的那条已登记动作，协议拒绝从原始工具调用取得。"""
    index = path[1] if len(path) > 1 and path[0] == "tool_calls" and isinstance(path[1], int) else 0
    if registered:
        return registered[min(index, len(registered) - 1)].data.get("tool_name")
    calls = _tool_calls(event)
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
