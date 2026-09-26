"""@brief 从两种认证输入提取不含原文的公开结构证据。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.models import JsonObject, PublicEvidence
from worldmock.footprints.models import Event
from worldmock.life.interaction_menu import WAYS


_LEGACY_ACTIONS = {
    "food_order": ("purchase", "order", "order"),
    "coffee_order": ("purchase", "order", "order"),
    "grocery_purchase": ("purchase", "order", "order"),
    "navigation": ("travel", "travel", "route"),
}
_TARGETED_ACTIONS = {
    "work": ("work", "work", "task"),
    "watch": ("entertainment", "view", "content"),
    "play": ("entertainment", "play", "game"),
    "read": ("work", "read", "message"),
    "browse": ("browsing", "browse", "content"),
}
#: 结果活动快照的 kind → 内部标准动作（start_activity 逐项证据按实际开始的活动分类）。
_ACTION_OF_KIND = {
    "travel": "travel_to", "work": "work", "watch": "watch", "play_game": "play", "read_mail": "read",
    "browse": "browse", "consume": "consume", "chat": "chat", "call": "call", "lifestyle": "do",
    "wait": "wait", "sleep": "sleep",
}
#: 活动控制工具 -> 公开交互动词（docs/dev/SPEC-agent-interaction.md「稳定工具面」），按 task[] 逐活动投影。
_SESSION_CONTROL_TOOLS = {
    "report_status": "report",
    "pause_activity": "pause",
    "conclude_activity": "conclude",
}
#: 公开终态取自活动快照的启动动作；其余动作以动作自身终态为准。
_ACTIVITY_BACKED_ACTIONS = frozenset({
    "travel_to", "work", "watch", "play", "read", "browse", "consume"})
_RESULT_STATES = {
    "accepted": "started",
    "in_progress": "started",
    "completed": "completed",
    "rejected": "rejected",
    "cancelled": "cancelled",
    "failed": "failed",
    "unknown": "unknown",
}
_ACTIVITY_RESULT_STATES = {
    "active": "started",
    "paused": "paused",
    "completed": "completed",
    "cancelled": "cancelled",
    "failed": "failed",
    "truncated": "cancelled",
}


@dataclass(frozen=True)
class _Shape:
    """@brief 保存一个内部对象的闭集公开投影。"""

    category: str
    interaction: str
    object_type: str
    object_key: str | None
    attributes: Mapping[str, str]


@dataclass(frozen=True)
class _Action:
    """@brief 保存 WorldMock 动作请求与实际对象回链。"""

    tool_name: str
    request: Mapping[str, Any]
    shape: _Shape
    action_name: str | None = None
    index: int = 0
    activity_status: str | None = None
    #: 该项实际开始的活动身份（start_activity 逐项；没有活动或未知时为 None，按位置对应）。
    activity_key: str | None = None


def labelkit_public_evidence(
    main_rows: Sequence[JsonObject],
    stream_rows: Sequence[JsonObject],
    categories: Mapping[str, str],
) -> tuple[PublicEvidence, ...]:
    """@brief 提取 Legacy 声明和结构事件，不把会话当作对象。"""
    evidence: list[PublicEvidence] = []
    for row in main_rows:
        item = _legacy_action(row, categories)
        if item is not None:
            evidence.append(item)
    for row in stream_rows:
        item = _legacy_stream_event(row, categories)
        if item is not None:
            evidence.append(item)
    return tuple(sorted(evidence, key=_evidence_order))


def _legacy_action(row: JsonObject, categories: Mapping[str, str]) -> PublicEvidence | None:
    """@brief 把 actionInfo 投影为声明，不推断业务终态。"""
    meta = _mapping(row.get("_meta"))
    sequence_id = _string(meta.get("id"))
    generation = _mapping(meta.get("generation"))
    sequence_class = _string(generation.get("sequence_class"))
    action = _mapping(row.get("actionInfo"))
    shape = _legacy_action_shape(sequence_class, action)
    if shape is None or sequence_id not in categories:
        return None
    stream = _mapping(meta.get("stream"))
    member_ids = tuple(item for item in stream.get("member_ids", []) if isinstance(item, str))
    return PublicEvidence(
        _milliseconds(action.get("timestamp")), categories[sequence_id], shape.interaction,
        shape.object_type, "declared", None, shape.attributes, member_ids, sequence_id,
    )


def _legacy_action_shape(sequence_class: str | None, action: Mapping[str, Any]) -> _Shape | None:
    """@brief 返回 Legacy 声明的闭集动作和非文本属性。"""
    if sequence_class == "check_in":
        interaction = {"start": "check_in", "end": "check_out"}.get(action.get("checkInType"))
        if interaction is None or action.get("checkInPoiType") != "COMPANY":
            return None
        return _Shape("work", interaction, "workplace", None, {"place_type": "workplace"})
    mapped = _LEGACY_ACTIONS.get(sequence_class or "")
    if mapped is None:
        return None
    category, interaction, object_type = mapped
    attributes = _legacy_action_attributes(sequence_class or "", action)
    return _Shape(category, interaction, object_type, None, attributes)


def _legacy_action_attributes(sequence_class: str, action: Mapping[str, Any]) -> JsonObject:
    """@brief 仅保留实际存在的履约和交通闭集值。"""
    if sequence_class in {"food_order", "coffee_order", "grocery_purchase"}:
        value = action.get("fulfillmentMode")
        return {"fulfillment_mode": value} if value in {"delivery", "pickup"} else {}
    value = action.get("travelMode")
    return {"travel_mode": value} if value in {"walking", "transit"} else {}


def _legacy_stream_event(row: JsonObject, categories: Mapping[str, str]) -> PublicEvidence | None:
    """@brief 投影一个 Legacy 原始结构事件。"""
    payload = _mapping(row.get("payload"))
    source = _mapping(_mapping(row.get("_meta")).get("event"))
    owner = _string(source.get("owner_sequence_id"))
    event_id = _string(source.get("event_id"))
    if owner not in categories or event_id is None:
        return None
    shape, state = _legacy_event_shape(payload)
    if shape is None:
        return None
    when = _iso_time(source.get("timestamp"))
    return PublicEvidence(
        when, categories[owner], shape.interaction, shape.object_type, state,
        shape.object_key, shape.attributes, (event_id,), owner,
    )


def _legacy_event_shape(payload: Mapping[str, Any]) -> tuple[_Shape | None, str | None]:
    """@brief 按 dataName 和闭集状态生成 Legacy 公开结构。"""
    name = payload.get("dataName")
    if name == "appUsageEvent":
        value = {"IN": ("open", "started"), "OUT": ("close", "completed")}.get(payload.get("state"))
        return (_Shape("", value[0], "application", None, {}), value[1]) if value else (None, None)
    if name == "screenMemoryEvent" and payload.get("status") == "Finish":
        return _legacy_screen_shape(payload), "viewed"
    if name == "pasteBoardEvent":
        return _Shape("", "copy", "content", None, {}), "completed"
    if name == "notificationEvent":
        return _Shape("", "notify", "message", None, {}), "received"
    if name == "commutePeriodEvent":
        return _legacy_commute_shape(payload)
    if name == "poiEvent" and payload.get("poiType") == "COMPANY":
        key = _string(payload.get("poiName"))
        return _Shape("", "visit", "workplace", key, {"place_type": "workplace"}), "observed"
    return None, None


def _legacy_screen_shape(payload: Mapping[str, Any]) -> _Shape:
    """@brief 仅在明确页面对象字段存在时保留屏幕对象身份。"""
    page_type = payload.get("pageId")
    object_types = {"attendance-result": "workplace", "route-overview": "route"}
    object_type = object_types.get(page_type, "content")
    key = _string(payload.get("entityId")) if page_type in object_types else None
    attributes = {"place_type": "workplace"} if page_type == "attendance-result" else {}
    return _Shape("", "view", object_type, key, attributes)


def _legacy_commute_shape(payload: Mapping[str, Any]) -> tuple[_Shape | None, str | None]:
    """@brief 投影确实记录的到达或离开公司事件。"""
    interaction = {
        "ARRIVE_COMPANY": "arrive",
        "LEAVE_COMPANY": "leave",
    }.get(payload.get("periodType"))
    if interaction is None:
        return None, None
    return _Shape("", interaction, "workplace", None, {"place_type": "workplace"}), "observed"


def worldmock_public_evidence(
    event_rows: Sequence[JsonObject],
    activity_rows: Sequence[JsonObject],
    activity_map: Mapping[str, str],
) -> tuple[PublicEvidence, ...]:
    """@brief 从严格事件和活动提取 WorldMock 公开结构事实。"""
    events = tuple(_event(row) for row in event_rows)
    activities = {str(row.get("activity_id")): row for row in activity_rows}
    actions = _world_actions(events, activities, activity_map)
    evidence: list[PublicEvidence] = []
    for event in events:
        if event.event_type == "action_requested" and event.action_id in actions:
            evidence.extend(_from_event(event, action.shape, "requested")
                            for action in actions[event.action_id])
        elif event.event_type == "action_result" and event.action_id in actions:
            for action in actions[event.action_id]:
                evidence.extend(_world_result(event, action))
        elif event.event_type == "environment_event":
            item = _world_environment(event)
            if item is not None:
                evidence.append(item)
    return tuple(sorted(evidence, key=_evidence_order))


def _world_actions(
    events: Sequence[Event],
    activities: Mapping[str, JsonObject],
    activity_map: Mapping[str, str],
) -> dict[str, tuple[_Action, ...]]:
    """@brief 以 action_id 回链请求、结果和确实对象字段。"""
    by_action: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.action_id:
            by_action[event.action_id].append(event)
    result: dict[str, tuple[_Action, ...]] = {}
    for action_id, related in by_action.items():
        requested = next((item for item in related if item.event_type == "action_requested"), None)
        if requested is None:
            continue
        tool_name = _string(requested.data.get("tool_name"))
        request = _mapping(requested.data.get("request"))
        if tool_name == "start_activity":
            selected = _start_shapes(request, related)
        elif tool_name in _SESSION_CONTROL_TOOLS:
            selected = _control_actions(tool_name, request, related, activities, activity_map)
        else:
            shape = _world_request_shape(tool_name, request)
            selected = (_Action(tool_name, request, shape),) if shape is not None else ()
        if selected:
            result[action_id] = selected
    return result


def _world_request_shape(
    tool_name: str | None,
    request: Mapping[str, Any],
) -> _Shape | None:
    """@brief 映射一个稳定工具请求并用结果中的对象字段回链。"""
    if tool_name == "submit_work":
        return _Shape("work", "submit", "task", _string(request.get("task_name")), {})
    if tool_name == "send_message":
        return _Shape("social", "message", "person", _string(request.get("recipient_name")), {})
    return None


def _start_shapes(request: Mapping[str, Any], related: Sequence[Event]) -> tuple[_Action, ...]:
    """@brief 把等长的可读对象、动词与方式数组逐项映射为独立公开证据。

    内部动作按结果中的活动快照 kind 分类（购买按实际订单）；没有结果活动的请求（如被拒绝）按私有绑定的方式闭集
    （WAYS）映射，不按动词或短语反解。
    """
    targets, ways = request.get("targets"), request.get("ways")
    if (not isinstance(targets, list) or not isinstance(ways, list)
            or not 1 <= len(targets) == len(ways) <= 2):
        return ()
    selected = []
    for index, (target, way) in enumerate(zip(targets, ways, strict=True)):
        action = _started_action(related, index, way)
        shape = _start_shape(action, way, target, related, index)
        if shape is not None:
            key = _string(_related_activity(related, index).get("activity_id"))
            selected.append(_Action("start_activity", request, shape, action, index, activity_key=key))
    return tuple(selected)


def _started_action(related: Sequence[Event], index: int, way: Any) -> str | None:
    """@brief 逐项的内部标准动作：结果活动快照的 kind，其次实际订单（购买），最后是方式在 WAYS 中的内部动作。"""
    kind = _string(_related_activity(related, index).get("kind"))
    if kind in _ACTION_OF_KIND:
        return _ACTION_OF_KIND[kind]
    if _order_key(related, index) is not None:
        return "purchase"
    return WAYS[way][1] if isinstance(way, str) and way in WAYS else None


def _start_shape(action: str | None, label: str, target: Any,
                 related: Sequence[Event], index: int) -> _Shape | None:
    """@brief 从真实结果活动取对象身份，未结算时仅保留人物可读名称。"""
    activity = _related_activity(related, index)
    key = _string(activity.get("target_id")) or _string(target)
    if action == "travel_to":
        route = _string(_mapping(activity.get("data")).get("route_id"))
        return _Shape("travel", "travel", "route", route,
                      _travel_mode(activity.get("travel_mode")))
    if action in _TARGETED_ACTIONS:
        category, interaction, object_type = _TARGETED_ACTIONS[action]
        return _Shape(category, interaction, object_type, key, {})
    if action == "purchase":
        return _Shape("purchase", "order", "order", _order_key(related, index), {})
    if action == "consume":
        mode = _mapping(_mapping(activity.get("data")).get("request")).get("use_mode")
        category = "product_use" if mode == "use" or not mode and label in {"使用", "制作"} else "eat"
        return _Shape(category, "consume", "product", key, {})
    if action in {"chat", "call"}:
        return _Shape("social", action, "person", key, {})
    return None


def _control_actions(
    tool_name: str,
    request: Mapping[str, Any],
    related: Sequence[Event],
    activities: Mapping[str, JsonObject],
    activity_map: Mapping[str, str],
) -> tuple[_Action, ...]:
    """@brief 按请求 task[] 逐活动投影：结果逐项回报的活动身份绑定到导出的真实活动快照。"""
    selected: list[_Action] = []
    for event in related:
        if event.event_type != "action_result":
            continue
        identities = _result_payload(event.data).get("activity_ids")
        if not isinstance(identities, list):
            continue
        for index, activity_id in enumerate(identities):
            key = _string(activity_id)
            row = activities.get(key) if key is not None else None
            base = _activity_shape(row, activity_map) if row is not None else None
            if base is None:
                continue
            shape = _Shape(base.category, _SESSION_CONTROL_TOOLS[tool_name], "activity",
                           key, base.attributes)
            selected.append(_Action(tool_name, request, shape, index=index,
                                    activity_status=_string(row.get("status"))))
        break
    return tuple(selected)


def _activity_shape(row: Mapping[str, Any], activity_map: Mapping[str, str]) -> _Shape | None:
    """@brief 从导出活动的真实目标字段生成对象投影。"""
    kind = _string(row.get("activity_type")) or _string(row.get("kind"))
    data = _mapping(row.get("data"))
    category = worldmock_activity_category(kind or "", data, activity_map)
    types = {
        "work": ("work", "task"), "watch": ("view", "content"),
        "play_game": ("play", "game"), "read_mail": ("read", "message"),
        "browse": ("browse", "content"), "travel": ("travel", "route"),
        "consume": ("consume", "product"),
    }
    mapped = types.get(kind or "")
    if mapped is None or category == "unknown":
        return None
    interaction, object_type = mapped
    key = _activity_object_key(kind or "", row, data)
    attributes = _travel_mode(row.get("travel_mode")) if kind == "travel" else {}
    return _Shape(category, interaction, object_type, key, attributes)


def _activity_object_key(kind: str, row: Mapping[str, Any], data: Mapping[str, Any]) -> str | None:
    """@brief 选择活动确实保存的对象身份。"""
    if kind == "travel":
        return _string(data.get("route_id"))
    return _string(row.get("target_id"))


def _world_result(event: Event, action: _Action) -> list[PublicEvidence]:
    """@brief 投影动作终态，并为确实付款生成独立公开状态。"""
    started = _start_action(action)
    if started == "purchase":
        return _purchase_result(event, action)
    if action.tool_name == "submit_work":
        return _work_result(event, action)
    state = _result_state(event, action)
    rows = [_from_event(event, action.shape, state)]
    if started == "travel_to" and _account_changes(event, action):
        payment = _Shape(action.shape.category, "pay", action.shape.object_type,
                         action.shape.object_key, action.shape.attributes)
        rows.append(_from_event(event, payment, "paid"))
    return rows


def _start_action(action: _Action) -> str | None:
    """@brief 返回逐项解析出的标准动作名。"""
    return action.action_name


def _purchase_result(event: Event, action: _Action) -> list[PublicEvidence]:
    """@brief 仅依据实际订单对象投影订单、付款与履约状态。

    与持续活动同一请求的取得：只由带着本项订单的结果投影；同一动作之后只描述活动的终态结果不是这项订单的结果，
    不投影（组合请求中瞬时的取得当场成单并付款，组合结果仍是进行中）。
    """
    payload = _result_payload(event.data)
    order = _item_order(payload, action.index)
    if not order and ("activity" in payload or "activities" in payload):
        return []
    key = _string(order.get("order_id")) or action.shape.object_key
    attributes = _fulfillment(order.get("fulfillment")) or dict(action.shape.attributes)
    state = order.get("status")
    if state not in {"pending", "ready", "delivered", "cancelled"}:
        state = _result_state(event, action)
    order_shape = _Shape("purchase", "order", "order", key, attributes)
    rows = [_from_event(event, order_shape, str(state) if state is not None else None)]
    if _account_changes(event, action) and event.data.get("status") in {"completed", "in_progress"}:
        payment = _Shape("purchase", "pay", "order", key, attributes)
        rows.append(_from_event(event, payment, "paid"))
    return rows


def _work_result(event: Event, action: _Action) -> list[PublicEvidence]:
    """@brief 依据独立验收布尔值和实际付款投影工作结果。"""
    payload = _result_payload(event.data)
    review = _mapping(payload.get("work_review"))
    accepted = review.get("accepted")
    if accepted is True:
        state = "accepted"
    elif accepted is False:
        state = "rejected"
    else:
        state = _result_state(event, action)
    rows = [_from_event(event, action.shape, state)]
    payment = payload.get("payment_fen")
    if accepted is True and _positive_integer(payment) and _account_changes(event, action):
        paid = _Shape("work", "pay", "task", action.shape.object_key, {})
        rows.append(_from_event(event, paid, "paid"))
    return rows


def _result_state(event: Event, action: _Action) -> str | None:
    """@brief 将真实动作状态收敛为公开闭集状态。"""
    status = _RESULT_STATES.get(event.data.get("status"))
    if status in {"rejected", "cancelled", "failed", "unknown"}:
        return status
    if action.tool_name == "conclude_activity":
        # 结束状态取活动的实际终态（完成、失败或已取消），不按工具名推断。
        return _ACTIVITY_RESULT_STATES.get(action.activity_status)
    if action.tool_name in _SESSION_CONTROL_TOOLS:
        return {"report_status": "started", "pause_activity": "paused"}[action.tool_name]
    started = _start_action(action)
    activity = (_activity_with_identity(event.data, action.activity_key) if action.activity_key is not None
                else _result_activity_at(event.data, action.index))
    if started in _ACTIVITY_BACKED_ACTIONS:
        status = _ACTIVITY_RESULT_STATES.get(activity.get("status"))
    if status == "completed":
        if started == "read" and _mapping(_mapping(activity.get("data")).get("revealed")):
            return "read"
        if started == "watch":
            return "viewed"
    return status


def _world_environment(event: Event) -> PublicEvidence | None:
    """@brief 投影邮件、订单配送和工作到期等外部公开事件。"""
    kind = event.data.get("kind")
    if kind == "mail_arrival":
        shape = _Shape("work", "notify", "message", _string(event.data.get("message_id")), {})
        return _from_event(event, shape, "received")
    if kind in {"delivery_ready", "delivery_arrival"}:
        order = _mapping(event.data.get("order"))
        key = _string(order.get("order_id")) or _string(event.data.get("order_id"))
        interaction = "deliver" if kind == "delivery_arrival" else "notify"
        state = order.get("status") if order.get("status") in {"ready", "delivered"} else kind.removeprefix("delivery_")
        shape = _Shape("purchase", interaction, "order", key, _fulfillment(order.get("fulfillment")))
        return _from_event(event, shape, str(state))
    if kind in {"work_available", "task_expired"}:
        interaction = "notify" if kind == "work_available" else "deadline"
        state = "available" if kind == "work_available" else "reached"
        shape = _Shape("work", interaction, "task", _string(event.data.get("task_id")), {})
        return _from_event(event, shape, state)
    return None


def _order_key(events: Sequence[Event], index: int = 0) -> str | None:
    """@brief 从动作的实际订单结果读取订单身份。"""
    for event in events:
        if event.event_type != "action_result":
            continue
        key = _string(_item_order(_result_payload(event.data), index).get("order_id"))
        if key is not None:
            return key
    return None


def _item_result(payload: Mapping[str, Any], index: int) -> Mapping[str, Any] | None:
    """@brief 逐项派发的组合结果中与请求第 index 项对应的逐项结果（activity_results 中带活动快照或订单的一项）。

    合成一单或一餐、单项请求与终态结果没有逐项结果，返回 None，沿用按位置对应。
    """
    results = payload.get("activity_results")
    item = _mapping(results[index]) if isinstance(results, list) and index < len(results) else {}
    return item if "activity" in item or "orders" in item else None


def _item_order(payload: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    """@brief 请求第 index 项的实际订单：组合结果取该项自己的订单，合成的一单按位置取，没有时为空对象。"""
    item = _item_result(payload, index)
    if item is not None:
        orders = item.get("orders")
        return _mapping(orders[0]) if isinstance(orders, list) and orders else {}
    orders = payload.get("orders")
    return _mapping(orders[index] if isinstance(orders, list) and index < len(orders) else payload.get("order"))


def _activity_data_key(events: Sequence[Event], field: str) -> str | None:
    """@brief 从动作结果活动快照读取指定真实身份。"""
    for event in events:
        activity = _result_activity(event.data)
        key = _string(_mapping(activity.get("data")).get(field))
        if key is not None:
            return key
    return None


def _result_payload(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """@brief 返回 ActionResult 的业务 data 对象。"""
    return _mapping(data.get("data"))


def _result_activity(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """@brief 返回动作结果中的权威活动快照。"""
    direct = _mapping(data.get("activity"))
    return direct or _mapping(_result_payload(data).get("activity"))


def _result_activity_at(data: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    """@brief 读取组合动作中与请求顺序对应的活动快照：逐项结果优先（瞬时的取得没有活动），其次按位置对应。"""
    item = _item_result(_result_payload(data), index)
    if item is not None:
        return _mapping(item.get("activity"))
    activities = _result_payload(data).get("activities")
    if isinstance(activities, list):
        return _mapping(activities[index]) if index < len(activities) else {}
    return _result_activity(data) if index == 0 else {}


def _activity_with_identity(data: Mapping[str, Any], identity: str) -> Mapping[str, Any]:
    """@brief 一条动作结果中身份为 identity 的活动快照（单项、逐项列表或逐项结果），没有时为空对象。"""
    payload = _result_payload(data)
    rows = [_result_activity(data), *(payload.get("activities") or []),
            *(_mapping(item).get("activity") for item in payload.get("activity_results") or [])]
    return next((_mapping(row) for row in rows if _mapping(row).get("activity_id") == identity), {})


def _account_changes(event: Event, action: _Action) -> bool:
    """@brief 只在逐项结果确有账户资金变化时投影付款。"""
    payload = _result_payload(event.data)
    results = payload.get("activity_results")
    if isinstance(results, list) and action.index < len(results):
        row = _mapping(results[action.index])
        changes = _mapping(row.get("resource_changes"))
    else:
        changes = _mapping(payload.get("resource_changes")) or _mapping(event.data.get("resource_changes"))
    return any(key.startswith("account:") and isinstance(value, (int, float)) and value != 0
               for key, value in changes.items())


def _related_activity(events: Sequence[Event], index: int) -> Mapping[str, Any]:
    """@brief 从动作结果链提取当前逐项活动的最新快照。

    逐项派发的组合结果记明了每一项的活动身份，之后的结果（如只含一项活动的终态）按身份对应；瞬时的取得没有活动。
    """
    item = next((found for event in events if event.event_type == "action_result"
                 for found in [_item_result(_result_payload(event.data), index)] if found is not None), None)
    identity = _string(_mapping(item.get("activity")).get("activity_id")) if item is not None else None
    if item is not None and identity is None:
        return {}
    for event in reversed(events):
        if event.event_type == "action_result":
            activity = (_activity_with_identity(event.data, identity) if identity is not None
                        else _result_activity_at(event.data, index))
            if activity:
                return activity
    return {}


def _from_event(event: Event, shape: _Shape, state: str | None) -> PublicEvidence:
    """@brief 从严格事件和闭集投影构造公开证据。"""
    return PublicEvidence(
        datetime.fromtimestamp(event.occurred_at, ZoneInfo("UTC")), shape.category,
        shape.interaction, shape.object_type, state, shape.object_key,
        shape.attributes, (event.event_id,), event.activity_id or event.action_id or shape.object_key,
    )


def _fulfillment(value: Any) -> JsonObject:
    """@brief 保留受支持的履约方式。"""
    return {"fulfillment_mode": value} if value in {"delivery", "pickup"} else {}


def _travel_mode(value: Any) -> JsonObject:
    """@brief 保留受支持的交通方式。"""
    return {"travel_mode": value} if value in {"walking", "driving", "transit"} else {}


def worldmock_activity_category(
    activity_type: str, data: Any, activity_map: Mapping[str, str]
) -> str:
    """@brief 按活动真实 use_mode 区分进食与非食品使用。"""
    if activity_type != "consume":
        return activity_map.get(activity_type, "unknown")
    if not isinstance(data, dict):
        raise EvaluationError("invalid_input", "consume activity data must be an object")
    request = data.get("request")
    if not isinstance(request, dict):
        raise EvaluationError("invalid_input", "consume activity must preserve its request")
    return _consume_category(request.get("use_mode"))


def _consume_category(value: Any) -> str:
    """@brief 把商品使用方式收敛到冻结的两个活动类别。"""
    if value == "eat":
        return "eat"
    if value == "use":
        return "product_use"
    raise EvaluationError("invalid_input", "consume use_mode must be eat or use")


def _positive_integer(value: Any) -> bool:
    """@brief 判断值是否为非布尔正整数。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _event(row: JsonObject) -> Event:
    """@brief 使用生产事件模型解析 WorldMock 输入。"""
    try:
        return Event.model_validate(row)
    except ValidationError as exc:
        raise EvaluationError("invalid_input", "WorldMock public evidence event is invalid") from exc


def _mapping(value: Any) -> dict[str, Any]:
    """@brief 将非对象值收敛为空对象。"""
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str | None:
    """@brief 返回非空字符串，否则表示缺失。"""
    return value if isinstance(value, str) and value else None


def _milliseconds(value: Any) -> datetime:
    """@brief 解析 Legacy actionInfo 的纪元毫秒时间。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationError("invalid_time", "Legacy public action timestamp must be epoch milliseconds")
    return datetime.fromtimestamp(value / 1000, ZoneInfo("UTC"))


def _iso_time(value: Any) -> datetime:
    """@brief 解析 Legacy 事件的带时区 ISO 时间。"""
    if not isinstance(value, str):
        raise EvaluationError("invalid_time", "Legacy public event timestamp must be ISO-8601")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None:
        raise EvaluationError("invalid_time", "Legacy public event timestamp must include timezone")
    return parsed


def _evidence_order(value: PublicEvidence) -> tuple[Any, ...]:
    """@brief 返回与来源身份无关的稳定内部排序键。"""
    return (
        value.occurred_at, value.category, value.interaction, value.object_type,
        value.state or "", value.object_key or "", value.source_event_ids,
    )
