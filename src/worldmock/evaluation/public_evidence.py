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
from worldmock.exchange.constants import WALKING_TRAVEL_METHOD_ID
from worldmock.footprints.models import Event
from worldmock.life.action_contract import (ACTIVITY_ACTIONS, MAIN_ACTIVITY_ACTIONS, SIDE_ACTIVITY_ACTIONS,
                                             START_TOOLS)
from worldmock.footprints.app_usage import validate_activity_prefix
from worldmock.errors import IntegrityError


_LEGACY_ACTIONS = {
    "food_order": ("purchase", "order", "order"),
    "coffee_order": ("purchase", "order", "order"),
    "grocery_purchase": ("purchase", "order", "order"),
    "navigation": ("travel", "travel", "route"),
}
#: 结果活动种类只取现行五类，环境动作类别由快照保存。
#: 活动控制工具 -> 公开交互动词（docs/design/04-simulation-engine.md「动作、名称与并行」），按请求
#: items[] 逐活动投影。
_SESSION_CONTROL_TOOLS = {
    "report_status": "report",
    "pause_activity": "pause",
    "conclude_activity": "conclude",
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

    action: str
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
    #: 这条开始调用实际开始的活动身份（没有活动或未知时为 None，按位置对应）。
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
    actual_action, interaction, object_type = mapped
    attributes = _legacy_action_attributes(sequence_class or "", action)
    return _Shape(actual_action, interaction, object_type, None, attributes)


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


def worldmock_public_evidence(events: Sequence[Event],
                              activity_rows: Sequence[JsonObject]) -> tuple[PublicEvidence, ...]:
    """@brief 先校验事件前缀，再从真实动作、对象和付款事实生成公开证据。"""
    try:
        validate_activity_prefix([event.model_dump(mode="json") for event in events])
    except IntegrityError as exc:
        raise EvaluationError("invalid_input", str(exc)) from exc
    activities = {str(row["activity_id"]): row for row in activity_rows}
    actions = _world_actions(events, activities)
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
        elif event.event_type == "activity_progress":
            evidence.extend(_content_progress(event))
    return tuple(sorted(evidence, key=_evidence_order))


def _world_actions(events: Sequence[Event],
                   activities: Mapping[str, JsonObject]) -> dict[str, tuple[_Action, ...]]:
    """@brief 以请求身份回链真实启动和控制结果。"""
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
        if tool_name in START_TOOLS:
            selected = _start_shapes(tool_name, request, related)
        elif tool_name in _SESSION_CONTROL_TOOLS:
            selected = _control_actions(tool_name, request, related, activities)
        else:
            selected = ()
        if selected:
            result[action_id] = selected
    return result




def _start_shapes(tool_name: str, request: Mapping[str, Any], related: Sequence[Event]) -> tuple[_Action, ...]:
    """@brief 一条开始调用恰好开始一项活动：动作直接读两个开始工具各自的参数，不从名称反解。"""
    main = tool_name == START_TOOLS[0]
    if set(request) != ({"action", "target", "detail"} if main else {"action", "target", "prefer"}):
        raise EvaluationError("invalid_input", "Start request arguments are outside the current contract")
    action = request["action"]
    if action not in (MAIN_ACTIVITY_ACTIONS if main else SIDE_ACTIVITY_ACTIONS):
        raise EvaluationError("invalid_input", "Start request action is outside the current closed set")
    activity = _related_activity(related, 0)
    shape = _start_shape(action, related, activity)
    if shape is None:
        return ()
    return (_Action(tool_name, request, shape, action, activity_key=_string(activity.get("activity_id"))),)


def _start_shape(action: str, related: Sequence[Event], activity: Mapping[str, Any]) -> _Shape | None:
    """@brief 实际快照提供对象，睡眠与查询沿用不生成开始点事实的规则。"""
    if action == "interact":
        return _activity_shape(activity) if activity else None
    if action == "purchase":
        return _Shape(action, "order", "order", _order_key(related), {})
    if action == "sleep":
        return None
    if action == "query":
        return None
    trip = _string(_mapping(activity.get("data")).get("trip_id"))
    return _Shape(action, "travel", "route", trip, _travel_mode(activity.get("travel_method_id")))


def _control_actions(tool_name: str, request: Mapping[str, Any], related: Sequence[Event],
                     activities: Mapping[str, JsonObject]) -> tuple[_Action, ...]:
    """@brief 控制事实取结果中的真实活动身份和状态。"""
    selected: list[_Action] = []
    for event in related:
        if event.event_type != "action_result":
            continue
        identities = _result_payload(event.data).get("activity_ids")
        if not isinstance(identities, list):
            continue
        for index, key in enumerate(identities):
            row = activities.get(key)
            base = _activity_shape(row) if row is not None else None
            if base is None:
                continue
            shape = _Shape(base.action, _SESSION_CONTROL_TOOLS[tool_name], "activity", key, {})
            selected.append(_Action(tool_name, request, shape, index=index,
                                    activity_status=_string(row.get("status"))))
        break
    return tuple(selected)


def _activity_shape(row: Mapping[str, Any]) -> _Shape | None:
    """@brief 内容交互使用真实内容，目录浏览使用真实应用，不推断设备或语义类别。"""
    kind = _string(row.get("activity_type")) or _string(row.get("kind"))
    data = _mapping(row.get("data"))
    action = worldmock_activity_action(kind or "", data)
    if kind == "travel":
        return _Shape(action, "travel", "route", _string(data.get("trip_id")),
                      _travel_mode(row.get("travel_method_id")))
    if kind != "interact":
        return None
    content = _string(data.get("content_id"))
    return _Shape(action, "interact", "content" if content else "app", content or _string(data.get("app_id")), {})




def _world_result(event: Event, action: _Action) -> list[PublicEvidence]:
    """@brief 购买付款只由独立付款事件证明，出行付款只取真实账户变化。"""
    if action.action_name == "purchase":
        return _purchase_result(event, action)
    rows = [_from_event(event, action.shape, _result_state(event, action))]
    if action.action_name == "travel" and _account_changes(event, action):
        shape = _Shape(action.shape.action, "pay", "route", action.shape.object_key, action.shape.attributes)
        rows.append(_from_event(event, shape, "paid"))
    return rows


def _start_action(action: _Action) -> str | None:
    """@brief 返回逐项解析出的标准动作名。"""
    return action.action_name


def _purchase_result(event: Event, action: _Action) -> list[PublicEvidence]:
    """@brief 每个真实订单独立投影，取消不会产生成功付款或收货。"""
    payload = _result_payload(event.data)
    orders = _mapping(_item_result(payload, action.index)).get("orders", payload.get("orders", []))
    rows = []
    for order in orders:
        shape = _Shape("purchase", "order", "order", order["order_id"], _fulfillment(order["fulfillment"]))
        rows.append(_from_event(event, shape, order["status"]))
    if not rows and event.data.get("status") == "rejected":
        rows.append(_from_event(event, action.shape, "rejected"))
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
    if started is not None:
        status = _ACTIVITY_RESULT_STATES.get(activity.get("status"))
    return status


def _world_environment(event: Event) -> tuple[PublicEvidence, ...]:
    """@brief 独立订单、成功付款、到站与真实收货保留点事实。"""
    kind = event.data.get("kind")
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
    if kind == "purchase_payment":
        return tuple(_from_event(event, _Shape("purchase", "pay", "order", order_id, {}), "paid")
                     for order_id in event.data["order_ids"])
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
    """@brief 资金变化只读事件顶层的权威账本。"""
    return any(key.startswith("account:agent:") and value < 0
               for key, value in event.resource_changes.items())


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
        datetime.fromtimestamp(event.occurred_at, ZoneInfo("UTC")), shape.action,
        shape.interaction, shape.object_type, state, shape.object_key,
        shape.attributes, (event.event_id,), event.activity_id or event.action_id or shape.object_key,
    )


def _fulfillment(value: Any) -> JsonObject:
    """@brief 保留受支持的履约方式。"""
    return {"fulfillment_mode": value} if value in {"delivery", "pickup", "station_pickup"} else {}


def _travel_mode(travel_method_id: Any) -> JsonObject:
    """@brief 只归类引擎自带的步行；数据集定义的出行方式不带类别，身份起成 driving、bus、taxi 也一样。"""
    return {"travel_mode": "walking"} if travel_method_id == WALKING_TRAVEL_METHOD_ID else {}


def worldmock_activity_action(activity_type: str, data: Any) -> str:
    """@brief 当前活动只读取真实启动动作，退役种类优先拒绝。"""
    if activity_type in {"wait", "action"}:
        raise EvaluationError("invalid_input", "Activity snapshot uses a retired activity kind")
    if activity_type not in ACTIVITY_ACTIONS:
        raise EvaluationError("invalid_input", "Activity type is outside the current closed set")
    action = _mapping(data).get("action")
    if action not in ACTIVITY_ACTIONS:
        raise EvaluationError("invalid_input", "Activity requires its actual action")
    if action != activity_type:
        raise EvaluationError("invalid_input", "Activity action disagrees with its kind")
    return action


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
        value.occurred_at, value.action, value.interaction, value.object_type,
        value.state or "", value.object_key or "", value.source_event_ids,
    )


def _content_progress(event: Event) -> tuple[PublicEvidence, ...]:
    """@brief 只有真实正向播放结算提供内容消费与完成事实。"""
    content = event.data.get("content")
    if not isinstance(content, dict) or content["position_after_seconds"] <= content["position_before_seconds"]:
        return ()
    shape = _Shape("interact", "interact", "content", content["content_id"], {})
    return (_from_event(event, shape, "completed" if content["completed"] else "in_progress"),)
