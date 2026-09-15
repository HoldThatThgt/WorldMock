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


_LEGACY_ACTIONS = {
    "food_order": ("purchase", "order", "order"),
    "coffee_order": ("purchase", "order", "order"),
    "grocery_purchase": ("purchase", "order", "order"),
    "navigation": ("travel", "travel", "route"),
}
_TARGETED_ACTIONS = {
    "work": ("work", "work", "task"),
    "watch": ("entertainment", "view", "content"),
    "play_game": ("entertainment", "play", "game"),
    "read_mail": ("work", "read", "message"),
    "browse": ("browsing", "browse", "content"),
}
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
            evidence.append(_from_event(event, actions[event.action_id].shape, "requested"))
        elif event.event_type == "action_result" and event.action_id in actions:
            evidence.extend(_world_result(event, actions[event.action_id]))
        elif event.event_type == "environment_event":
            item = _world_environment(event)
            if item is not None:
                evidence.append(item)
    return tuple(sorted(evidence, key=_evidence_order))


def _world_actions(
    events: Sequence[Event],
    activities: Mapping[str, JsonObject],
    activity_map: Mapping[str, str],
) -> dict[str, _Action]:
    """@brief 以 action_id 回链请求、结果和确实对象字段。"""
    by_action: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.action_id:
            by_action[event.action_id].append(event)
    result: dict[str, _Action] = {}
    for action_id, related in by_action.items():
        requested = next((item for item in related if item.event_type == "action_requested"), None)
        if requested is None:
            continue
        tool_name = _string(requested.data.get("tool_name"))
        request = _mapping(requested.data.get("request"))
        shape = _world_request_shape(tool_name, request, related, activities, activity_map)
        if tool_name is not None and shape is not None:
            result[action_id] = _Action(tool_name, request, shape)
    return result


def _world_request_shape(
    tool_name: str | None,
    request: Mapping[str, Any],
    related: Sequence[Event],
    activities: Mapping[str, JsonObject],
    activity_map: Mapping[str, str],
) -> _Shape | None:
    """@brief 映射一个 typed 请求并用结果中的对象字段回链。"""
    if tool_name == "purchase":
        attributes = _fulfillment(request.get("fulfillment"))
        return _Shape("purchase", "order", "order", _order_key(related), attributes)
    if tool_name == "start_travel":
        key = _activity_data_key(related, "route_id")
        return _Shape("travel", "travel", "route", key, _travel_mode(request.get("travel_mode")))
    if tool_name == "start_targeted_activity":
        return _targeted_shape(request, related)
    if tool_name == "submit_work":
        return _Shape("work", "submit", "task", _string(request.get("task_id")), {})
    if tool_name == "game_action":
        return _Shape("entertainment", "play", "game", _string(request.get("game_session_id")), {})
    if tool_name == "consume":
        category = _consume_category(request.get("use_mode"))
        return _Shape(category, "consume", "product", _string(request.get("product_id")), {})
    if tool_name in {"set_activity_state", "continue_activity", "change_playback_speed"}:
        return _control_shape(tool_name, request, activities, activity_map)
    return None


def _targeted_shape(request: Mapping[str, Any], related: Sequence[Event]) -> _Shape | None:
    """@brief 映射带明确目标的活动请求。"""
    activity_type = _string(request.get("activity_type"))
    mapped = _TARGETED_ACTIONS.get(activity_type or "")
    if mapped is None:
        return None
    category, interaction, object_type = mapped
    key = _string(request.get("target_id"))
    if activity_type == "play_game":
        key = _activity_data_key(related, "game_session_id") or key
    return _Shape(category, interaction, object_type, key, {})


def _control_shape(
    tool_name: str,
    request: Mapping[str, Any],
    activities: Mapping[str, JsonObject],
    activity_map: Mapping[str, str],
) -> _Shape | None:
    """@brief 将活动控制动作绑定到现有活动或其真实目标。"""
    activity_id = _string(request.get("activity_id"))
    row = activities.get(activity_id or "")
    if row is None:
        return None
    interaction = {
        "continue_activity": "continue",
        "change_playback_speed": "change",
    }.get(tool_name, str(request.get("operation")))
    base = _activity_shape(row, activity_map)
    if base is None or interaction not in {"continue", "change", "pause", "resume", "cancel"}:
        return None
    return _Shape(base.category, interaction, "activity", activity_id, base.attributes)


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
    if kind == "play_game":
        return _string(data.get("game_session_id")) or _string(row.get("target_id"))
    return _string(row.get("target_id"))


def _world_result(event: Event, action: _Action) -> list[PublicEvidence]:
    """@brief 投影动作终态，并为确实付款生成独立公开状态。"""
    if action.tool_name == "purchase":
        return _purchase_result(event, action)
    if action.tool_name == "submit_work":
        return _work_result(event, action)
    state = _result_state(event, action)
    rows = [_from_event(event, action.shape, state)]
    if action.tool_name == "start_travel" and _mapping(event.data.get("resource_changes")):
        payment = _Shape(action.shape.category, "pay", action.shape.object_type,
                         action.shape.object_key, action.shape.attributes)
        rows.append(_from_event(event, payment, "paid"))
    return rows


def _purchase_result(event: Event, action: _Action) -> list[PublicEvidence]:
    """@brief 仅依据实际订单对象投影订单、付款与履约状态。"""
    payload = _result_payload(event.data)
    order = _mapping(payload.get("order"))
    key = _string(order.get("order_id")) or action.shape.object_key
    attributes = _fulfillment(order.get("fulfillment")) or dict(action.shape.attributes)
    state = order.get("status")
    if state not in {"pending", "ready", "delivered", "cancelled"}:
        state = _result_state(event, action)
    order_shape = _Shape("purchase", "order", "order", key, attributes)
    rows = [_from_event(event, order_shape, str(state) if state is not None else None)]
    if _mapping(event.data.get("resource_changes")) and event.data.get("status") == "completed":
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
    resource_changes = _mapping(event.data.get("resource_changes"))
    if accepted is True and _positive_integer(payment) and resource_changes:
        paid = _Shape("work", "pay", "task", action.shape.object_key, {})
        rows.append(_from_event(event, paid, "paid"))
    return rows


def _result_state(event: Event, action: _Action) -> str | None:
    """@brief 将真实动作状态收敛为公开闭集状态。"""
    status = _RESULT_STATES.get(event.data.get("status"))
    if status in {"rejected", "cancelled", "failed", "unknown"}:
        return status
    if action.tool_name == "game_action":
        return _game_result_state(event)
    activity = _result_activity(event.data)
    if action.tool_name in {
        "start_travel", "start_targeted_activity", "consume", "set_activity_state",
        "continue_activity", "change_playback_speed",
    }:
        status = _ACTIVITY_RESULT_STATES.get(activity.get("status"))
    if status == "completed" and action.tool_name == "start_targeted_activity":
        kind = action.request.get("activity_type")
        if kind == "read_mail" and _mapping(_mapping(activity.get("data")).get("revealed")):
            return "read"
        if kind == "watch":
            return "viewed"
    return status


def _game_result_state(event: Event) -> str | None:
    """@brief 只用实际游戏 done 字段区分进行中和终局。"""
    done = _result_payload(event.data).get("done")
    if done is True:
        return "completed"
    if done is False:
        return "in_progress"
    return None


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


def _order_key(events: Sequence[Event]) -> str | None:
    """@brief 从动作的实际订单结果读取订单身份。"""
    for event in events:
        if event.event_type != "action_result":
            continue
        key = _string(_mapping(_result_payload(event.data).get("order")).get("order_id"))
        if key is not None:
            return key
    return None


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
