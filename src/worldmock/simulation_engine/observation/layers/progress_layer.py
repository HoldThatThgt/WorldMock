"""@brief 进度层：此刻还没了结的事，只从真实人物可见事件和已交付提醒派生（docs/design/06-agent.md「进度、待办与记忆」）。

每种记录各取各的下界：送达、到站与办成取已发布界 P_k = max(C_{k-1}, B_D)，序号不超过它的事实已写进记忆层「今天」或属于
前一天；app 推送、回调提醒、余额减少与事务提醒取上一次决策的观察与零点界中较大的一个，上一次决策提交里产生的不丢；计划提醒
取提醒时刻晚于上一次决策的时刻。开始请求的记录只有带入的记录，不看下界：占槽位的本人活动（进行中或待收尾，不分种类）与这段
停留或路程里做完的查询，请求与第一条结果按事件身份读出，结果取最新的一条。控制请求、被拒的请求与已收尾的活动不在这里，
记录不带 self_note；被拒原因只在 rejected_today。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import Event
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.activities.lifecycle import sessions
from worldmock.simulation_engine.activities.lifecycle.activity_control import slot_activities
from worldmock.state.planning.errands import COMPLETED_KIND, REMINDED_KIND
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.world import WorldState
from worldmock.foundation.time.readable import (
    clock_phrase,
    iso_time,
    local_date,
    money_phrase,
    portion_phrase,
)
from worldmock.simulation_engine.observation.layers.result_projection import public_result
from worldmock.simulation_engine.observation.memory.days import day_bound
from worldmock.simulation_engine.observation.memory.entries import AgentEvents, agent_events, decision_commits
from worldmock.recording_engine.projections.activities.session_bounds import session_lower
from worldmock.simulation_engine.observation.wording.settled_text import KitUse, record_lines
from worldmock.simulation_engine.observation.targets.tool_layer import LayerContext

#: 订单仍在途的状态（相关记录受保护）。
OPEN_ORDER_STATUSES = frozenset({"pending", "ready_for_pickup"})
#: 被进度层消费的两种个人事务事件（人物可见）→ 记录类型；其余四种事务事件只供审计与指标，不进进度层。
_ERRAND_RECORDS = {REMINDED_KIND: "errand_reminder", COMPLETED_KIND: "errand_completed"}
#: 记忆事实的环境事件种类：它们的事实写进记忆层「今天」的条目，进度层只写序号大于已发布界的。
FACT_EVENT_KINDS = frozenset({"delivery_arrival", COMPLETED_KIND})
#: 非记忆的环境事件种类：不进今天条目，进度层只写序号大于上一次决策观察（且大于零点界）的。
NOTICE_EVENT_KINDS = frozenset({REMINDED_KIND, "app_push", "callback_reminder", "balance_decreased"})


@dataclass(frozen=True, slots=True)
class ProgressView:
    """@brief 进度层、浓缩元数据与实际人物可见来源。"""

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


@dataclass(frozen=True, slots=True)
class _PreviousDecision:
    """@brief 上一次决策：它的观察事件序号与决策时刻。"""

    observation_seq: int
    decided_at: int


def session_bound(store: FootprintStore, state: WorldState, agent_id: int) -> tuple[int, int]:
    """@brief 当前 session 的 (开始时刻, 事件序号下界)：下界是开启它的边界提交的末事件序号（运行开始为 0）。"""
    session = sessions.current_session(state, agent_id)
    if session is None:
        logging.error("Agent has no active session for the progress layer")
        raise IntegrityError("Agent has no active session")
    return session.started_at, session_lower(store, state, session)


def progress_view(context: LayerContext, store: FootprintStore, published: int,
                  kits: Mapping[str, KitUse]) -> ProgressView:
    """@brief 进度层 JSON 与私有浓缩元数据及真实来源事件身份。

    :param published: 已发布界 P_k（memory_view 求出）：送达、到站与办成只写序号大于它的。
    :param kits: 同一次观察的 ActivityFacts.kits：带入记录的结果投影（public_result）写食材包句。
    """
    stay = session_bound(store, context.state, context.agent_id)
    cached = agent_events(store, context.agent_id, context.catalog.kit_states())
    previous = _previous_decision(store, cached, context.agent_id)
    now, zone = context.state.current_time, context.config.timezone
    day = day_bound(store, local_date(now, zone), zone, now)
    notice_after = max(0 if previous is None else previous.observation_seq, day)
    records = sorted([*_carried_records(context, store, stay, kits),
                      *_world_records(context, store, (published, notice_after)),
                      *_plan_reminders(context, None if previous is None else previous.decided_at)],
                     key=lambda item: item.key)
    request = _last_request(store, context.agent_id)
    meta = [{"protected": _protected(context, item, request),
             "lines": record_lines(context, item.record, list(item.sources))} for item in records]
    layer = {"layer": "进度层", "session_started_at": iso_time(stay[0], zone),
             "events": [item.record for item in records], "rejected_today": rejected_today(context, store)}
    return ProgressView(layer=layer, meta=meta,
                        event_ids=list(dict.fromkeys(event.event_id for item in records for event in item.sources)))


def notice_records(context: LayerContext, store: FootprintStore, after: int) -> list[dict]:
    """@brief 序号大于 after 的非记忆世界记录（推送、回调提醒、余额减少、事务提醒），按进度层的次序；同秒续接的 events 读它。"""
    items = _world_records(context, store, (store.last_event_seq, after))
    return [item.record for item in sorted(items, key=lambda item: item.key)]


def tool_call_record(context: LayerContext, request: Event, result: Event, kits: Mapping[str, KitUse]) -> dict:
    """@brief tool_call 记录：请求时刻、工具、请求原文与 result；result 是给定结果的公开投影，kits 是同一次观察的
    ActivityFacts.kits。"""
    data = request.data
    return {"occurred_at": iso_time(request.occurred_at, context.config.timezone), "type": "tool_call",
            "tool": data["tool_name"], "arguments": data["request"],
            "result": public_result(result.data, context.inputs, kits)}


def _previous_decision(store: FootprintStore, cached: AgentEvents, agent_id: int) -> _PreviousDecision | None:
    """@brief 上一次决策（第 02 章【E1】的最后一次决策）的观察（序号最大的一条）与决策时刻；没有决策时为 None，
    缺观察时 IntegrityError。"""
    decisions = decision_commits(store, cached, store.last_event_seq)
    if not decisions:
        return None
    last = decisions[-1]
    observations = store.read_decision_observations(agent_id, last.decision_id, last.occurred_at)
    if not observations:
        logging.error("Previous decision %s has no observation", last.decision_id)
        raise IntegrityError("Previous decision has no observation")
    return _PreviousDecision(max(event.event_seq for event in observations), last.occurred_at)


def _event_key(occurred_at: int, sources: list[Event]) -> tuple:
    """@brief 事件记录的排序键：发生时刻，推导记录之后，按最小来源序号。"""
    return (occurred_at, 1, 0, 0, min(event.event_seq for event in sources))


def _carried_records(context: LayerContext, store: FootprintStore, stay: tuple[int, int],
                     kits: Mapping[str, KitUse]) -> list[_Record]:
    """@brief 全部带入记录：每项带入活动一条；kits 交给结果投影。"""
    return [_carried_record(context, store, activity, kits) for activity in _carried_activities(context, store, stay)]


def _carried_activities(context: LayerContext, store: FootprintStore, stay: tuple[int, int]) -> list[ActivityState]:
    """@brief 要带入的本人活动：占槽位的（进行中或待收尾，不分种类），加上这段停留或路程里做完的查询。"""
    carried = {activity.activity_id: activity for activity in slot_activities(context.open_activities)}
    carried.update((activity.activity_id, activity) for activity in _finished_queries(context, store, stay))
    return list(carried.values())


def _finished_queries(context: LayerContext, store: FootprintStore, stay: tuple[int, int]) -> list[ActivityState]:
    """@brief 这段停留或路程里做完的本人查询：completed、结束不早于本段开始、终态结果序号大于本段下界。"""
    started_at, lower = stay
    finished = [activity for _, activity in sorted(context.state.activities.items())
                if activity.agent_id == context.agent_id and activity.kind == ActivityKind.QUERY
                and activity.status == ActivityStatus.COMPLETED and activity.ended_at >= started_at]
    return [activity for activity in finished
            if _terminal_result(store, activity, store.read_event(activity.data["accepted_event_id"])).event_seq
            > lower]


def _carried_record(context: LayerContext, store: FootprintStore, activity: ActivityState,
                    kits: Mapping[str, KitUse]) -> _Record:
    """@brief 一项带入活动的 tool_call 记录：请求与第一条结果按事件身份读出，结果取最新的一条（结束了就是终态结果）。"""
    request = store.read_event(activity.origin_request_event_id)
    own = [store.read_event(activity.data["accepted_event_id"])]
    if activity.ended_at is not None:
        own.append(_terminal_result(store, activity, own[0]))
    return _call_record(tool_call_record(context, request, own[-1], kits), request, own)


def _terminal_result(store: FootprintStore, activity: ActivityState, first: Event) -> Event:
    """@brief 已结束活动的终态结果：开始动作在第一条结果之后的那条 action_result，只从结束那一秒读起；缺失时 IntegrityError。"""
    after = max(first.event_seq, store.last_event_seq_before(activity.ended_at))
    terminal = next((event for event in store.read_agent_events("action_result", activity.agent_id, after)
                     if event.action_id == activity.origin_action_id), None)
    if terminal is None:
        logging.error("Ended activity %s has no terminal result", activity.activity_id)
        raise IntegrityError("Ended activity has no terminal result")
    return terminal


def _call_record(record: dict, request: Event, own: list[Event]) -> _Record:
    """@brief 一条 tool_call 记录连同来源（请求、第一条与最新一条结果）、排序键与关联订单。"""
    sources = [request, own[0]] + ([own[-1]] if len(own) > 1 else [])
    return _Record(record, tuple(sources), _event_key(request.occurred_at, sources), _order_ids(own))


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


def _world_records(context: LayerContext, store: FootprintStore, bounds: tuple[int, int]) -> list[_Record]:
    """@brief 人物可见的环境事件记录：记忆事实只取序号大于 bounds[0] 的，非记忆提示只取序号大于 bounds[1] 的。"""
    facts_after, notices_after = bounds
    records: list[_Record] = []
    for event in store.read_agent_events("environment_event", context.agent_id, after=min(bounds)):
        kind = event.data.get("kind")
        if event.visibility != "agent":
            continue
        if kind in FACT_EVENT_KINDS and event.event_seq > facts_after:
            records.extend(_delivery_records(context, event) if kind == "delivery_arrival"
                           else [_errand_record(context, event)])
        elif kind in NOTICE_EVENT_KINDS and event.event_seq > notices_after:
            records.extend(_notice_record(context, event))
    return records


def _notice_record(context: LayerContext, event: Event) -> list[_Record]:
    """@brief 一条非记忆世界记录：事务提醒、推送（不取 pushed 为假的）、回调提醒或余额减少。"""
    kind, zone = event.data["kind"], context.config.timezone
    if kind == REMINDED_KIND:
        return [_errand_record(context, event)]
    if kind == "app_push":
        if not event.data.get("pushed", True):
            return []
        record = {"occurred_at": iso_time(event.occurred_at, zone), "type": "app_push",
                  "app": context.catalog.apps[event.data["app_id"]].name,
                  "title": event.data["title"], "summary": event.data["summary"]}
    elif kind == "callback_reminder":
        record = {"occurred_at": iso_time(event.occurred_at, zone), "type": "callback_reminder",
                  "app": context.catalog.apps[event.data["app_id"]].name, "text": event.data["text"]}
    else:
        record = {"occurred_at": iso_time(event.occurred_at, zone), "type": "balance_decreased",
                  "message": f"余额减少了 {money_phrase(event.data['amount_fen'])}"}
    return [_Record(record, (event,), _event_key(event.occurred_at, [event]))]


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


def _plan_reminders(context: LayerContext, after: int | None) -> list[_Record]:
    """@brief 今日目标的提醒：提醒时刻晚于 after（上一次决策的时刻；None 时不设下界）、不晚于此刻的各一条。"""
    now, zone = context.state.current_time, context.config.timezone
    plan = context.state.agents[context.agent_id].day_plan
    records = []
    for order, goal in enumerate(plan.goals if plan is not None else ()):
        moment = goal.reminded_at
        if moment is not None and (after is None or moment > after) and moment <= now:
            record = {"occurred_at": iso_time(moment, zone), "type": "plan_reminder", "goal": goal.goal}
            records.append(_Record(record, (), (moment, 0, 1, order, 0)))
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

    同一决策的两条开始动作被拒时内容相同，只统计一次。只读本地零点之前最后一个事件之后的证据，不解码更早的历史。
    """
    day = local_date(context.state.current_time, context.config.timezone)
    after = day_bound(store, day, context.config.timezone, context.state.current_time)
    events = sorted((event for kind in ("model_response", "action_rejected", "action_accepted", "action_requested")
                     for event in store.read_agent_events(kind, context.agent_id, after)
                     if event.occurred_at <= context.state.current_time
                     and local_date(event.occurred_at, context.config.timezone) == day),
                    key=lambda item: item.event_seq)
    requests = [event for event in events if event.event_type == "action_requested"]
    rows: dict[tuple, dict] = {}
    rejected: set[str] = set()
    for event in events:
        if event.event_type == "action_accepted":
            _memo_accept(rows, event, next((item for item in requests if item.action_id == event.action_id), None))
            continue
        if event.event_type not in {"model_response", "action_rejected"}:
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
