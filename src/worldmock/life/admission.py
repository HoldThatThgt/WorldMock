"""@brief 开始组的槽位占用、睡眠约束、对象条件与余额核验；工具层的单行可用性与受理共用同一套判定。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control, location_activities
from worldmock.life.action_contract import START_TOOLS, activity_slot
from worldmock.life.basket import quoted_basket
from worldmock.life.content_select import content_duration_range
from worldmock.life.guidance import Refusal, default_reason, refusal_for_code, refusal_from_error
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, FulfillmentKind, WorldState
from worldmock.life.sessions import current_session
from worldmock.life.travel import Trip, plan_trip
from worldmock.life.travel_methods import travel_method
from worldmock.life.weather import current_weather_factor

_LOGGER = logging.getLogger(__name__)
_SLEEP_SLOT_REASON = "Sleep requires the second activity slot to be empty."


@dataclass(frozen=True, slots=True)
class Context:
    """@brief 准入直接读取唯一世界与环境输入，不装配旧场景层。"""

    #: 当前世界。
    state: WorldState
    #: 唯一人物身份。
    agent_id: int
    #: 完整地点显示名。
    place_names: Mapping[str, str]
    #: 本轮活动显示名。
    activity_names: Mapping[str, str] = field(default_factory=dict)
    #: 完整环境输入：冻结目录、运行配置与本人住处。
    inputs: Any = None


@dataclass(frozen=True, slots=True)
class Call:
    """@brief 一条名称已解析的开始调用。"""

    #: 开始工具名。
    tool: str
    #: 调用所写的动作。
    action: str
    #: 解析出的绑定行，各带 selected_action：主工具一行，副工具每个名称一行。
    rows: tuple[Mapping[str, Any], ...]

    @property
    def slot(self) -> str:
        """@brief 这条调用要占的槽位，由动作与所用内容 app 唯一给出。"""
        first = self.rows[0]
        return activity_slot(self.action, first["object_id"] if first["kind"] == "app" else None)

    @property
    def goods_ids(self) -> tuple[str, ...]:
        """@brief 购买调用按所写次序的商品身份，同一商品写几次就出现几次。"""
        return tuple(row["object_id"] for row in self.rows)


@dataclass(frozen=True, slots=True)
class Blocked:
    """@brief 整组第一条不成立的核验与负责的调用。"""

    #: 拒绝的码、原因、下一步、改法与未满足条件。
    refusal: Refusal
    #: 负责的开始工具名：业务明细落在这些调用上。
    tools: tuple[str, ...]


def present_location(state: WorldState, agent_id: int) -> str | None:
    """@brief 路上不视为停留在起点或终点。"""
    session = current_session(state, agent_id)
    return session.location_id if session is not None and session.kind == "stay" else None


def group_refusal(context: Context, calls: Sequence[Call]) -> Blocked | None:
    """@brief 整组按固定次序核验同槽位占用、睡眠约束、主调用与副调用的对象条件和余额；全部成立时为 None。

    核验读请求前的世界，所以与出发同组的购买报价、收货地点都按出发前的停留地点算。
    """
    main = next((call for call in calls if call.tool == START_TOOLS[0]), None)
    side = next((call for call in calls if call.tool == START_TOOLS[1]), None)
    for check in (_slot_refusal, _sleep_refusal, _main_refusal, _side_refusal, _funds_refusal):
        blocked = check(context, main, side)
        if blocked is not None:
            return blocked
    return None


def planned_trip(context: Context, row: Mapping[str, Any]) -> Trip:
    """@brief 出行行按出发前的地点、所选出行方式与当前天气形成同源行程报价。"""
    origin, catalog = context.state.agents[context.agent_id].location_id, context.inputs.catalog
    method = travel_method(catalog, row["selected_action"]["travel_method_id"])
    return plan_trip(catalog.graph, origin, row["object_id"], method, current_weather_factor(context.state))


def _occupant(context: Context, slot: str) -> ActivityState | None:
    """@brief 占着这个槽位的本人活动：进行中和待收尾占槽位，暂停的活动让出槽位。"""
    return next((activity for activity in context.state.activities.values()
                 if activity.agent_id == context.agent_id and slot in activity.resource_claims
                 and activity_control.occupies_slot(activity)), None)


def _name(context: Context, activity: ActivityState) -> str:
    """@brief 活动的本轮可读名称，没有时取活动自身的名称。"""
    return context.activity_names.get(activity.activity_id, activity.name)


def _slot_refusal(context: Context, main: Call | None, side: Call | None) -> Blocked | None:
    """@brief 同槽位占用：某条调用要占的槽位已有活动时点名占用者，能暂停的占用者下一步写结束或暂停。"""
    for call in (main, side):
        occupant = None if call is None else _occupant(context, call.slot)
        if occupant is None:
            continue
        name = _name(context, occupant)
        step = "Conclude or pause" if activity_control.pausable(context.state, occupant) else "Conclude"
        refusal = Refusal("RESOURCE_CONFLICT", f"The {call.slot} activity slot is occupied by {name}.",
                          f"{step} {name} first.", ("resource_claims",))
        return Blocked(refusal, (call.tool,))
    return None


def _sleep_refusal(context: Context, main: Call | None, side: Call | None) -> Blocked | None:
    """@brief 睡眠约束：睡眠要求槽位 2 空着，也不能与副调用同组；睡着时不能开始槽位 2 的活动。"""
    if main is not None and main.action == "sleep":
        if side is not None:
            refusal = Refusal("RESOURCE_CONFLICT", _SLEEP_SLOT_REASON, fix="Submit start_main_activity alone to sleep.")
            return Blocked(refusal, (main.tool, side.tool))
        if _occupant(context, "second") is not None:
            refusal = Refusal("RESOURCE_CONFLICT", _SLEEP_SLOT_REASON, "Conclude or pause the second activity first.")
            return Blocked(refusal, (main.tool,))
    asleep = any(activity.agent_id == context.agent_id and activity.kind == ActivityKind.SLEEP
                 and activity.status == ActivityStatus.ACTIVE for activity in context.state.activities.values())
    if asleep and side is not None:
        return Blocked(Refusal("RESOURCE_CONFLICT", _SLEEP_SLOT_REASON), (side.tool,))
    return None


def _main_refusal(context: Context, main: Call | None, side: Call | None) -> Blocked | None:
    """@brief 主调用的对象条件：出行、在地点做事与睡眠各按自己的次序判定。"""
    if main is None:
        return None
    if main.action == "travel":
        refusal = _travel_refusal(context, main.rows[0])
    elif main.action == "interact":
        refusal = _location_refusal(context, main.rows[0])
    else:
        refusal = _sleep_place_refusal(context)
    return None if refusal is None else Blocked(refusal, (main.tool,))


def _travel_refusal(context: Context, row: Mapping[str, Any]) -> Refusal | None:
    """@brief 出行：进行中的到店购买要先结束；路线时长超出范围按路线拒绝。"""
    state = context.state
    local = next((activity for activity in state.activities.values()
                  if activity.agent_id == context.agent_id and activity.status == ActivityStatus.ACTIVE
                  and activity.kind == ActivityKind.PURCHASE
                  and any(state.deliveries[key].fulfillment == FulfillmentKind.PICKUP
                          for key in activity.data["order_ids"])), None)
    if local is not None:
        name = _name(context, local)
        return Refusal("ACTIVITY_PRECONDITION_UNMET", f"Conclude {name} before departure.", f"Conclude {name}.",
                       ("activities_before_departure",))
    try:
        planned_trip(context, row)
    except BusinessRuleError as error:
        _LOGGER.error("Travel route was refused: %s", error)
        return refusal_from_error(error)
    return None


def _local_moment(context: Context) -> datetime:
    """@brief 当前时刻换算到运行时区的本地时刻，营业时段按它判定。"""
    return datetime.fromtimestamp(context.state.current_time, ZoneInfo(context.inputs.config.timezone))


def _location_refusal(context: Context, row: Mapping[str, Any]) -> Refusal | None:
    """@brief 在地点做事按营业、取件、单元被占的次序判定，第一条不成立的给出拒绝。"""
    location = context.inputs.catalog.locations[row["object_id"]]
    if not location.is_open(_local_moment(context)):
        return refusal_for_code("LOCATION_CLOSED")
    ready = any(order.agent_id == context.agent_id and order.destination_id == location.location_id
                and order.status == "ready_for_pickup" for order in context.state.deliveries.values())
    if location.accepts_parcels and not ready:
        return Refusal("PARCEL_NOT_READY", default_reason("PARCEL_NOT_READY"), unmet=("parcel_ready",))
    return location_activities.held_refusal(context.state, context.agent_id, row["selected_action"],
                                            context.activity_names)


def _sleep_place_refusal(context: Context) -> Refusal | None:
    """@brief 睡眠的地点判定：当前停留地点须标了 sleepable，声明了营业时段时此刻须在其内（关门只卡开始）。"""
    location = context.inputs.catalog.locations[context.state.agents[context.agent_id].location_id]
    if not location.sleepable:
        home = context.place_names[context.inputs.home_location_id]
        return Refusal("ACTIVITY_PRECONDITION_UNMET", f"Sleeping is not possible at {location.display_name}.",
                       f"Go to {home}.", ("sleepable_location",))
    if not location.is_open(_local_moment(context)):
        return refusal_for_code("LOCATION_CLOSED")
    return None


def _side_refusal(context: Context, main: Call | None, side: Call | None) -> Blocked | None:
    """@brief 副调用的对象条件：购买按逐份报价与出发同组的限制判定，内容 app 须有可开始的内容或目录浏览。"""
    if side is None:
        return None
    if side.action == "purchase":
        refusal = _purchase_refusal(context, main, side)
    elif content_duration_range(context.inputs, context.state, context.agent_id, side.rows[0]["object_id"]) is None:
        refusal = refusal_for_code("CONTENT_EXHAUSTED")
    else:
        refusal = None
    return None if refusal is None else Blocked(refusal, (side.tool,))


def _purchase_refusal(context: Context, main: Call | None, side: Call) -> Refusal | None:
    """@brief 购买：逐份报价的拒绝按数组次序取第一条；同组出发时报价里不能有到店购买。"""
    try:
        groups, _ = quoted_basket(context.inputs, context.state, context.agent_id, side.goods_ids)
    except BusinessRuleError as error:
        _LOGGER.error("Purchase quote was refused: %s", error)
        return refusal_from_error(error)
    departs = main is not None and main.action == "travel"
    if departs and any(offer.fulfillment == "pickup" for offers in groups.values() for offer in offers):
        return Refusal("ACTIVITY_PRECONDITION_UNMET", "A local purchase cannot start with departure.",
                       "Complete or conclude the local purchase before leaving.")
    return None


def _funds_refusal(context: Context, main: Call | None, side: Call | None) -> Blocked | None:
    """@brief 余额：全部货款加各配送组的运费再加同组出行的车费；没有同组购买时只核对车费。"""
    fare = planned_trip(context, main.rows[0]).fare_fen if main is not None and main.action == "travel" else 0
    purchases = side is not None and side.action == "purchase"
    goods = quoted_basket(context.inputs, context.state, context.agent_id, side.goods_ids)[1] if purchases else 0
    account = context.state.agents[context.agent_id].balance_account_id
    if context.state.accounts[account] >= fare + goods:
        return None
    if not purchases:
        return Blocked(refusal_for_code("INSUFFICIENT_TRAVEL_FUNDS"), (main.tool,))
    tools = (main.tool, side.tool) if fare else (side.tool,)
    return Blocked(refusal_for_code("INSUFFICIENT_FUNDS"), tools)
