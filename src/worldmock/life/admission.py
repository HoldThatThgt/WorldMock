"""@brief 生活动作按实际现场、出行方式与资源占用进行整组准入；工具层和受理使用同一拒绝判定。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from worldmock.catalog.affordances import engine_affordances
from worldmock.catalog.graph import Graph
from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control
from worldmock.life.content_select import ContentRequest, eligible_contents, required_work_location
from worldmock.life.guidance import Refusal
from worldmock.life.wayfinding import go_step
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.interaction_menu import activity_phrase, merged_selection
from worldmock.life.models import ActivityKind, TravelMode, WorldState, active_activities
from worldmock.life.social import SOCIAL_RESOURCE_CLAIMS
from worldmock.life.social_actions import pair_chat

_UNMET = "ACTIVITY_PRECONDITION_UNMET"


@dataclass(frozen=True, slots=True)
class WorldFacts:
    """@brief 准入判定所需的冻结工作内容与工作地点。"""

    #: 已登记动作与内容唯一目录。
    catalog: Any
    #: 冻结场景声明的内容分配与工作地点。
    scenario: Any
    #: 本轮唯一世界状态。
    state: WorldState
    #: 本次执行动作的所有人。
    agent_id: int


@dataclass(frozen=True, slots=True)
class Context:
    """@brief 一次准入判定的世界与可读名称：地点身份→地点名，活动身份→本轮活动名。

    :param catalog: 冻结动作与内容目录；恢复暂停活动时核对本人剩余内容。
    :param graph: 已登记的连通图，下一步提示据此查询目标地点。
    """

    state: WorldState
    agent_id: int
    place_names: Mapping[str, str]
    graph: Graph
    #: 恢复内容资格使用同一目录与人物分配。
    catalog: Any
    scenario: Any
    activity_names: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Item:
    """@brief 一项所选动作的准入事实。

    :param canonical: 映射后的内部标准动作。
    :param way: 人物所选的方式短语。
    :param label: 可读说法（做什么加对象名），只用于原因与说明。
    :param claims: 该项开始后占用的资源。
    :param place: 目标在场要求的地点身份（工作地点、到店购买、到站领取），None 表示不要求。
    :param partner: 当面交谈的对方人物身份，None 表示不是交谈。
    :param manual: 是否是需要手动操作的瞬时动作（购物应用下单）。
    :param site: 是否要停在一个地点做（生活活动）：途中与同一请求出发都做不了。
    """

    canonical: str
    way: str
    label: str
    claims: frozenset[str]
    place: str | None = None
    partner: int | None = None
    manual: bool = False
    site: bool = False


def en_route(state: WorldState, agent_id: int) -> bool:
    """@brief 人物是否在途中：有进行中的出行。"""
    return any(item.kind == ActivityKind.TRAVEL for item in active_activities(state, agent_id))


def present_location(state: WorldState, agent_id: int) -> str | None:
    """@brief 人物的现场地点：途中为 None，否则是人物当前地点。"""
    return None if en_route(state, agent_id) else state.agents[agent_id].location_id


def driving_destination(state: WorldState, agent_id: int) -> str | None:
    """@brief 正在驾车时返回目的地身份，否则 None。"""
    return next((item.destination_id for item in active_activities(state, agent_id)
                 if item.kind == ActivityKind.TRAVEL and item.travel_mode == TravelMode.DRIVING), None)


def selection_item(row: Mapping[str, Any], action: Mapping[str, Any], facts: WorldFacts) -> Item:
    """@brief 把私有绑定和所选动作换成资源占用、在场与手动操作事实。"""
    canonical, object_id = action["canonical_action"], row["object_id"]
    item = Item(canonical=canonical, way=action["label"],
                label=f"{activity_phrase(action)}{activity_control.object_display_name(row)}",
                claims=_claims(canonical, action))
    if canonical == "purchase" and action.get("fulfillment") == "pickup":
        return replace(item, place=row["store_id"])
    if canonical == "purchase":
        return replace(item, manual=True)
    if canonical == "collect_parcel":
        return replace(item, place=object_id)
    if canonical == "do":
        request = ContentRequest(facts.agent_id, object_id, action.get("app_id"))
        place = required_work_location(facts.catalog, facts.scenario, facts.state, request)
        eligible = eligible_contents(facts.catalog, facts.scenario, facts.state, request)
        config = next(agent for agent in facts.scenario.agents if agent.agent_id == facts.agent_id)
        if place is None and eligible and all(content.content_id in config.work_content_ids for content in eligible):
            place = config.work_schedule.workplace_location_id
        registered = facts.catalog.actions[object_id]
        partner = row.get("counterpart")
        partner_id = int(partner.split(":", 1)[1]) if partner and partner.startswith("agent:") else None
        return replace(item, place=place, partner=partner_id if not registered.app_ids else None)
    return item


def require_group(context: Context, items: Sequence[Item]) -> None:
    """@brief 名额之后按目标在场、手动操作与占用判断整组动作；拒绝带原因与下一步。"""
    refusal = group_refusal(context, items)
    if refusal is not None:
        raise refusal.error()


def group_refusal(context: Context, items: Sequence[Item]) -> Refusal | None:
    """@brief require_group 的判定本身：第一条不成立的准入，全部成立时为 None。

    现场资格按整组开始之后人在哪里判定：同一请求出发时整组开始后人在路上，所以结果与组内先后无关。
    """
    present = present_location(context.state, context.agent_id)
    departs = any(item.canonical == "travel_to" for item in items)
    for item in items:
        refusal = _presence_refusal(context, item, present, departs)
        if refusal is not None:
            return refusal
    for item in items:
        refusal = _hands_free_refusal(context, item, items)
        if refusal is not None:
            return refusal
    return _claims_refusal(context, items)


def require_hands_free(context: Context, what: str) -> None:
    """@brief 驾车时需要手动操作的瞬时动作（发消息、购物应用下单）以 ACTIVITY_PRECONDITION_UNMET 拒绝；步行途中照常。"""
    refusal = hands_free_refusal(context, what)
    if refusal is not None:
        raise refusal.error()


def hands_free_refusal(context: Context, what: str) -> Refusal | None:
    """@brief require_hands_free 的判定本身：正在驾车时不成立（点名到达后再做），否则为 None。"""
    return _driving_refusal(context, what)


def _presence_refusal(context: Context, item: Item, present: str | None, departs: bool) -> Refusal | None:
    """@brief 目标在场与实际地点：要在某地做的事需要人在那里，途中与同一请求出发都不算在场；远程下单要能送到这里。"""
    return (_place_refusal(context, item, present, departs) or _site_refusal(item, present, departs)
            or _partner_refusal(context, item, present, departs))


def _place_refusal(context: Context, item: Item, present: str | None, departs: bool) -> Refusal | None:
    """@brief 要在指定地点做的事（工作内容、到店购买、到站领取）：人要在那里，且不和出行一起开始；
    下一步直接提示前往指定地点。"""
    if item.place is None:
        return None
    place = context.place_names[item.place]
    if departs:
        return Refusal(_UNMET, f"{item.label} requires presence at {place} and cannot start with travel.",
                       f"Complete the activity at {place} before departure.",
                       ("required_location_id",))
    if present != item.place:
        step = go_step(context.graph, context.place_names, _origin(context), item.place)
        return Refusal(_UNMET, f"{item.label} requires presence at {place}.", step, ("required_location_id",))
    return None


def _origin(context: Context) -> str:
    """@brief 下一步提示的出发地：人物的 location_id（途中为出发地，与工具层列出路线同一口径）。"""
    return context.state.agents[context.agent_id].location_id


def _site_refusal(item: Item, present: str | None, departs: bool) -> Refusal | None:
    """@brief 要停在一个地点做的生活活动：途中做不了，也不能和出行一起开始（开始后人在路上）。"""
    if not item.site or present is not None and not departs:
        return None
    if departs:
        return Refusal(_UNMET, f"{item.label} requires a stationary location and cannot start with travel.",
                       "Finish the activity before departure.",
                       ("location_kinds",))
    return Refusal(_UNMET, f"{item.label} requires a stationary location and cannot run during travel.",
                   "Wait until arrival before starting.", ("location_kinds",))


def _partner_refusal(context: Context, item: Item, present: str | None, departs: bool) -> Refusal | None:
    """@brief 当面交谈：对方要和本人同在一处；已在交谈为 CHAT_ALREADY_ACTIVE，接受时对方的交谈快结束为 ACTIVITY_TOO_SHORT。"""
    if item.partner is None:
        return None
    partner = context.state.agents.get(item.partner)
    where = present_location(context.state, item.partner) if partner is not None else None
    if not departs and present is not None and where == present:
        if pair_chat(context.state, context.agent_id, f"agent:{item.partner}") is not None:
            return Refusal("CHAT_ALREADY_ACTIVE", "You are already talking together.",
                           "Continue or conclude the current conversation.")
        return None
    name = partner.name if partner is not None else "the other person"
    step = (go_step(context.graph, context.place_names, _origin(context), where) if where is not None
            else "Wait until the other person arrives.")
    return Refusal(_UNMET, f"Talking with {name} requires both people at the same location.",
                   "Talk before departure." if departs else step,
                   ("partner_present",))


def _hands_free_refusal(context: Context, item: Item, items: Sequence[Item]) -> Refusal | None:
    """@brief 出行方式：驾车途中或与驾车同一请求时，远程下单这类手动操作的瞬时动作不受理。"""
    if not item.manual:
        return None
    refusal = _driving_refusal(context, "place an order")
    if refusal is None and any(other.canonical == "travel_to" and other.way == TRAVEL_WAYS["driving"]
                               for other in items):
        return Refusal(_UNMET, "Orders cannot be placed while driving.",
                       "Place the order before departure.", ("hands_free",))
    return refusal


def _driving_refusal(context: Context, what: str) -> Refusal | None:
    """@brief 正在驾车时需要手动操作的事不成立：点名到达后再做。"""
    destination = driving_destination(context.state, context.agent_id)
    if destination is None:
        return None
    place = context.place_names[destination]
    return Refusal(_UNMET, f"Cannot {what} while driving.", f"Wait until arrival at {place} to {what}.",
                   ("hands_free",))


def _claims_refusal(context: Context, items: Sequence[Item]) -> Refusal | None:
    """@brief 占用：组内各项与已有进行中活动、组内各项之间的注册表占用交集非空即 RESOURCE_CONFLICT。

    合成一单或一餐的两项是同一次动作（占用相同），只与已有进行中活动比较，组内不互相冲突。
    """
    running = active_activities(context.state, context.agent_id)
    for item in items:
        blocking = [activity for activity in running if activity.resource_claims & item.claims]
        if blocking:
            listed = "、".join(context.activity_names.get(activity.activity_id, activity.name) for activity in blocking)
            return Refusal("RESOURCE_CONFLICT", f"{item.label} cannot run together with {listed}.",
                           f"Conclude or pause {listed} first.", ("resource_claims",))
    if merged_selection([item.canonical for item in items], [item.way for item in items]):
        return None
    for index, item in enumerate(items):
        for other in items[index + 1:]:
            if item.claims & other.claims:
                return Refusal("RESOURCE_CONFLICT", f"{item.label} cannot run together with {other.label}.",
                               "Choose only one of the conflicting activities.", ("resource_claims",))
    return None




def _claims(canonical: str, action: Mapping[str, Any]) -> frozenset[str]:
    """@brief 购买不占资源，交谈与通话用社交占用，其余按注册表的条件占用。"""
    if canonical in {"purchase", "collect_parcel"}:
        return frozenset()
    view = engine_affordances()
    kinds = [name for name, spec in view.kinds.items() if spec.action == canonical]
    if len(kinds) != 1:
        raise BusinessRuleError("ACTION_NOT_SUPPORTED")
    return view.resource_claims(kinds[0], travel_mode=action.get("travel_mode"),
                                category=action.get("category"), manner=action.get("manner"))
