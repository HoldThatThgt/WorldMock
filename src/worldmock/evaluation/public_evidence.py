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
from worldmock.life.vocabulary import WAYS
from worldmock.exchange.types import ActionCategory, MANNER_DEVICE


_LEGACY_ACTIONS = {
    "food_order": ("purchase", "order", "order"),
    "coffee_order": ("purchase", "order", "order"),
    "grocery_purchase": ("purchase", "order", "order"),
    "navigation": ("travel", "travel", "route"),
}
#: 结果活动种类只取现行四类，环境动作类别由快照保存。
_ACTION_OF_KIND = {"travel": "travel_to", "action": "do", "wait": "wait", "sleep": "sleep"}
_ACTION_INTERACTIONS = {"entertainment": "view", "work_study": "work", "exercise": "do", "daily_care": "do"}
#: 活动控制工具 -> 公开交互动词（docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面「稳定工具面」），按请求
#: items[] 逐活动投影。
_SESSION_CONTROL_TOOLS = {
    "report_status": "report",
    "pause_activity": "pause",
    "conclude_activity": "conclude",
}
#: 公开终态取自活动快照的启动动作；其余动作以动作自身终态为准。
_ACTIVITY_BACKED_ACTIONS = frozenset({
    "travel_to", "do", "wait", "sleep"})
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
        return {"fulfillment_mode": value} if value in {"delivery", "pickup", "station_pickup"} else {}
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
    events: Sequence[Event],
    activity_rows: Sequence[JsonObject],
    activity_map: Mapping[str, str],
) -> tuple[PublicEvidence, ...]:
    """@brief 消费适配器已校验的事件和活动，提取 WorldMock 公开结构事实。"""
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
            evidence.extend(_world_environment(event))
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
    if tool_name == "send_message":
        return _Shape("social", "message", "person", _string(request.get("recipient_name")), {})
    return None


def _start_shapes(request: Mapping[str, Any], related: Sequence[Event]) -> tuple[_Action, ...]:
    """@brief 把等长的可读对象、动词与方式数组逐项映射为独立公开证据。

    内部动作按结果中的活动快照 kind 分类（购买按实际订单）；没有结果活动的请求（如被拒绝）按私有绑定的方式闭集
    （WAYS）映射，不按动词或短语反解。
    """
    items = request.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 2:
        return ()
    selected = []
    for index, item in enumerate(items):
        target, way = item["target"], item["way"]
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
    """@brief 类别只读实际动作快照；没有快照不能从目标名字猜环境动作。"""
    activity = _related_activity(related, index)
    if action == "travel_to":
        trip = _string(_mapping(activity.get("data")).get("trip_id"))
        return _Shape("travel", "travel", "route", trip, _travel_mode(activity.get("travel_mode")))
    if action == "do" and activity:
        return _activity_shape(activity, {})
    if action == "purchase":
        return _Shape("purchase", "order", "order", _order_key(related, index), {})
    return None


def _control_actions(
    tool_name: str,
    request: Mapping[str, Any],
    related: Sequence[Event],
    activities: Mapping[str, JsonObject],
    activity_map: Mapping[str, str],
) -> tuple[_Action, ...]:
    """@brief 按请求 items[] 逐活动投影，结果身份绑定到导出的真实活动快照。"""
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
    """@brief 动作对象取实际内容或社交对方，不公开标题、摘要及应用身份。"""
    kind = _string(row.get("activity_type")) or _string(row.get("kind"))
    data = _mapping(row.get("data"))
    category = worldmock_activity_category(kind or "", data, activity_map)
    if kind == "travel":
        return _Shape(category, "travel", "route", _string(data.get("trip_id")), _travel_mode(row.get("travel_mode")))
    if kind != "action":
        return None
    attributes = worldmock_activity_attributes(data)
    if category == "social":
        interaction = "call" if data.get("communication_mode") == "voice" else "chat"
        return _Shape(category, interaction, "person", _string(data.get("counterpart")), attributes)
    return _Shape(category, _ACTION_INTERACTIONS[category], "content", _string(data.get("content_id")), attributes)


def worldmock_activity_attributes(data: Mapping[str, Any]) -> Mapping[str, str]:
    """@brief 只有快照真实带应用时按唯一使用方式表公开手机或其他设备。"""
    if data.get("app_id") is None:
        return {}
    manner = data.get("manner")
    if manner not in MANNER_DEVICE:
        raise EvaluationError("invalid_input", "Action with an app requires a declared use manner")
    return {"device": MANNER_DEVICE[manner]}


def _world_result(event: Event, action: _Action) -> list[PublicEvidence]:
    """@brief 投影动作终态，并为确实付款生成独立公开状态。"""
    started = _start_action(action)
    if started == "purchase":
        return _purchase_result(event, action)
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
    if state not in {"pending", "ready_for_pickup", "delivered"}:
        state = _result_state(event, action)
    order_shape = _Shape("purchase", "order", "order", key, attributes)
    rows = [_from_event(event, order_shape, str(state) if state is not None else None)]
    if _account_changes(event, action) and event.data.get("status") in {"completed", "in_progress"}:
        payment = _Shape("purchase", "pay", "order", key, attributes)
        rows.append(_from_event(event, payment, "paid"))
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
    return status


def _world_environment(event: Event) -> tuple[PublicEvidence, ...]:
    """@brief 收货与到站按每张真实订单分别取证，付款仍来自原始下单结果。"""
    kind = event.data.get("kind")
    if kind == "mail_arrival":
        shape = _Shape("work_study", "notify", "message", _string(event.data.get("message_id")), {})
        return (_from_event(event, shape, "received"),)
    if kind == "delivery_arrival":
        rows = []
        for order in event.data["orders"]:
            state = order["status"]
            interaction = "notify" if state == "ready_for_pickup" else "deliver"
            shape = _Shape("purchase", interaction, "order", order["order_id"], _fulfillment(order["fulfillment"]))
            rows.append(_from_event(event, shape, state))
        return tuple(rows)
    if kind == "goods_received":
        shape = _Shape("purchase", "receive", "order", event.data["order_id"],
                       {"goods_category": event.data["category"]})
        return (_from_event(event, shape, "received"),)
    if kind == "work_content_completed":
        shape = _Shape("work_study", "work", "content", event.data["content_id"], {})
        return (_from_event(event, shape, "completed"),)
    return ()


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
    return _mapping(orders[index]) if isinstance(orders, list) and index < len(orders) else {}


def _result_payload(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """@brief 返回 ActionResult 的业务 data 对象。"""
    return _mapping(data.get("data"))


def _result_activity(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """@brief 返回动作结果中的权威活动快照。"""
    return _mapping(_result_payload(data).get("activity"))


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
    return any(key.startswith("account:") and isinstance(value, (int, float))
               and not isinstance(value, bool) and value != 0 for key, value in changes.items())


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
    return {"fulfillment_mode": value} if value in {"delivery", "pickup", "station_pickup"} else {}


def _travel_mode(value: Any) -> JsonObject:
    """@brief 保留受支持的交通方式。"""
    return {"travel_mode": value} if value in {"walking", "driving", "transit"} else {}


def worldmock_activity_category(activity_type: str, data: Any, activity_map: Mapping[str, str]) -> str:
    """@brief 系统活动读既有映射，环境动作只读五种固定动作类别。"""
    if activity_type != "action":
        if activity_type not in {"wait", "sleep", "travel"}:
            raise EvaluationError("invalid_input", "Activity type is outside the current closed set")
        return activity_map.get(activity_type, "unknown")
    if not isinstance(data, dict) or data.get("category") not in ActionCategory:
        raise EvaluationError("invalid_input", "Action requires a fixed category in its snapshot")
    return data["category"]


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
