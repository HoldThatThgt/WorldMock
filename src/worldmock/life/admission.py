"""@brief 开始与恢复共用固定名额、睡眠约束和实际在场准入。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from worldmock.life.action_contract import TRAVEL_ACTIONS, activity_slot
from worldmock.life.guidance import Refusal
from worldmock.life.models import (
    ActivityKind, ActivityStatus, FulfillmentKind, WorldState, CONCLUDABLE_TERMINAL_STATUSES,
)
from worldmock.life.sessions import current_session


@dataclass(frozen=True, slots=True)
class Context:
    """@brief 准入直接读取唯一世界与目录，不装配旧场景层。"""

    #: 当前世界。
    state: WorldState
    #: 唯一人物身份。
    agent_id: int
    #: 完整地点显示名。
    place_names: Mapping[str, str]
    #: 本轮活动显示名。
    activity_names: Mapping[str, str] = field(default_factory=dict)
    #: 完整冻结目录。
    catalog: Any = None


@dataclass(frozen=True, slots=True)
class Item:
    """@brief 一个解析完毕的动作与其固定槽位和在场要求。"""

    #: 七个动作之一。
    action: str
    #: 目标完整名称。
    label: str
    #: 动作占用的固定名额。
    claims: frozenset[str]
    #: 要求本人实际停留的地点；应用交互为空。
    place: str | None = None
    #: 供应门店身份。
    site: str | None = None


def present_location(state: WorldState, agent_id: int) -> str | None:
    """@brief 路上不视为停留在起点或终点。"""
    session = current_session(state, agent_id)
    return session.location_id if session is not None and session.kind == "stay" else None


def require_group(context: Context, items: Sequence[Item]) -> None:
    """@brief 整组在任何实际派发之前复核，失败不保留部分活动。"""
    refusal = group_refusal(context, items)
    if refusal is not None:
        raise refusal.error()


def group_refusal(context: Context, items: Sequence[Item]) -> Refusal | None:
    """@brief 购买项合并为一个第二槽，睡眠额外要求第二槽为空。"""
    occupied = _occupants(context)
    claims = [item.claims for item in items if item.action != "purchase"]
    if any(item.action == "purchase" for item in items):
        claims.append(frozenset({"second"}))
    all_claims = [*occupied, *claims]
    if any(left & right for index, left in enumerate(all_claims) for right in all_claims[index + 1:]):
        return Refusal("RESOURCE_CONFLICT", "Activities require the same activity slot.",
                       "Conclude or pause the conflicting activity first.")
    sleeping = any(item.kind == ActivityKind.SLEEP and item.status == ActivityStatus.ACTIVE
                   for item in context.state.activities.values())
    if (sleeping or any(item.action == "sleep" for item in items)) and any("second" in row for row in all_claims):
        return Refusal("RESOURCE_CONFLICT", "Sleep requires the second activity slot to be empty.",
                       "Conclude or pause the second activity first.")
    return _presence_refusal(context, items)


def _occupants(context: Context) -> list[set[str]]:
    """@brief 进行中和待收尾占槽，暂停活动释放槽位。"""
    return [activity.resource_claims for activity in context.state.activities.values()
            if activity.agent_id == context.agent_id and (activity.status == ActivityStatus.ACTIVE
            or activity.status in CONCLUDABLE_TERMINAL_STATUSES and activity.concluded_at is None)]


def _presence_refusal(context: Context, items: Sequence[Item]) -> Refusal | None:
    """@brief 出发前只阻止真实依赖原地在场的活动。"""
    departs = any(item.action in TRAVEL_ACTIONS for item in items)
    present = present_location(context.state, context.agent_id)
    for item in items:
        if item.place is None or item.place == present and not departs:
            continue
        place = context.place_names[item.place]
        suffix = " and cannot start with travel" if departs else ""
        return Refusal("ACTIVITY_PRECONDITION_UNMET", f"{item.label} requires presence at {place}{suffix}.",
                       f"Go to {place}.", unmet=("activity_location",))
    if departs:
        blocking = [item for item in context.state.activities.values() if item.agent_id == context.agent_id
                    and item.status == ActivityStatus.ACTIVE and _requires_presence(context.state, item)]
        if blocking:
            names = ", ".join(context.activity_names.get(item.activity_id, item.name) for item in blocking)
            only_purchase = all(item.kind == ActivityKind.PURCHASE for item in blocking)
            operation = "Conclude" if only_purchase else "Conclude or pause"
            return Refusal("ACTIVITY_PRECONDITION_UNMET", f"{operation} {names} before departure.",
                           f"{operation} {names}.", unmet=("activities_before_departure",))
    return None


def _requires_presence(state: WorldState, activity) -> bool:
    """@brief 地点内容与真实到店订单要求在场，不从退役字段推测占用。"""
    return (activity.kind == ActivityKind.INTERACT and activity.data["app_id"] is None
            or activity.kind == ActivityKind.PURCHASE and any(
                state.deliveries[key].fulfillment == FulfillmentKind.PICKUP for key in activity.data["order_ids"]))


def selection_item(row: Mapping[str, Any], action: Mapping[str, Any], facts: Any = None) -> Item:
    """@brief 目标绑定只决定真实在场要求，槽位从动作定义读取。"""
    name = action["action"]
    place = row["object_id"] if row["kind"] == "location" and name in {"interact", "sleep"} else None
    return Item(name, row["object"], frozenset({activity_slot(name)}), place, row.get("store_id"))
