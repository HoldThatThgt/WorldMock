"""@brief 生活动作的并行准入：现场地点、目标在场、出行方式、占用与物品状态（docs/dev/SPEC-time-and-energy.md 第五章）。

一个 start_activity 请求整组判定：名额（activity_control.require_start_admission，含出发前须先结束或暂停进行中的
活动）之后，按目标在场与实际地点、出行方式、占用、物品状态的顺序报第一条不成立的准入，结果与组内先后无关。
出行中人物的 location_id 仍是出发地（路线身份与菜单解析用），判定现场资格时同时检查进行中的出行：有进行中出行即为
「途中」，途中不在任何地点、没有可用的持有物、不与任何人同在一处。互斥只由注册表声明的占用交集判定，不另设按动作名
的规则。同一对人物已有进行中交谈时，重复发起在目标判定这一步以 CHAT_ALREADY_ACTIVE 返回（第五章「交谈」），先于占用。
交互对象层用同一套判定为每个列出的动作写一句说明（action_note），所以说明与拒绝原因一致。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from worldmock.catalog.affordances import default_affordances
from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control
from worldmock.life.guidance import Refusal
from worldmock.life.interaction_menu import TRAVEL_WAYS, activity_phrase
from worldmock.life.models import ActivityKind, TravelMode, WorldState, active_activities
from worldmock.life.social_actions import SOCIAL_RESOURCE_CLAIMS, pair_chat

_UNMET = "ACTIVITY_PRECONDITION_UNMET"


@dataclass(frozen=True, slots=True)
class WorldFacts:
    """@brief 准入判定所需的冻结目录：任务、供给、可持有物与内容（引擎取自规则，投影取自场景）。"""

    tasks: Mapping[str, Any]
    supplies: Mapping[str, Any]
    items: Mapping[str, Any]
    contents: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Context:
    """@brief 一次准入判定的世界与可读名称：地点身份→地点名，活动身份→本轮活动名。"""

    state: WorldState
    agent_id: int
    place_names: Mapping[str, str]
    activity_names: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Item:
    """@brief 一项所选动作的准入事实。

    :param canonical: 映射后的内部标准动作。
    :param way: 人物所选的方式短语。
    :param label: 可读说法（做什么加对象名），只用于原因与说明。
    :param claims: 该项开始后占用的资源。
    :param place: 目标在场要求的地点身份（办公室工作、到店取得、限地点使用的持有物），None 表示不要求。
    :param partner: 当面交谈的对方人物身份，None 表示不是交谈。
    :param manual: 是否是需要手动操作的瞬时动作（远程下单）。
    :param holding: 是否使用手边持有物（食用、使用、制作）。
    """

    canonical: str
    way: str
    label: str
    claims: frozenset[str]
    place: str | None = None
    partner: int | None = None
    manual: bool = False
    holding: bool = False


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
    """@brief 把一项私有绑定（对象行与所选动作）换成准入事实：占用、目标在场、手动操作与持有物。"""
    canonical, object_id = action["canonical_action"], row["object_id"]
    item = Item(canonical=canonical, way=action["label"],
                label=f"{activity_phrase(action)}{activity_control.object_display_name(row)}",
                claims=_claims(canonical, action, object_id, facts))
    if canonical == "work" and object_id in facts.tasks:
        return replace(item, place=facts.tasks[object_id].required_location_id)
    if canonical == "purchase" and action.get("fulfillment") == "pickup" and object_id in facts.supplies:
        return replace(item, place=facts.supplies[object_id].location_id)
    if canonical == "purchase":
        return replace(item, manual=True)
    if canonical == "chat" and str(object_id).startswith("agent:"):
        return replace(item, partner=int(str(object_id).split(":", 1)[1]))
    if canonical == "consume":
        product = facts.items.get(object_id)
        return replace(item, holding=True, place=getattr(product, "required_location_id", None) or None)
    return item


def require_group(context: Context, items: Sequence[Item]) -> None:
    """@brief 名额之后的整组准入：目标在场与实际地点、出行方式、占用、物品状态，报第一条不成立的。

    :raises PreconditionError: ACTIVITY_PRECONDITION_UNMET（目标在场、实际地点、驾车时手动操作）、
        RESOURCE_CONFLICT（占用交集）、PRODUCT_NOT_HELD（途中没有可用的持有物），都带原因与下一步。
    """
    refusal = group_refusal(context, items)
    if refusal is not None:
        raise refusal.error()


def group_refusal(context: Context, items: Sequence[Item]) -> Refusal | None:
    """@brief require_group 的判定本身：第一条不成立的准入，全部成立时为 None。"""
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
    refusal = _claims_refusal(context, items)
    if refusal is None and present is None and any(item.holding for item in items):
        return Refusal("PRODUCT_NOT_HELD", "路上没有可用的持有物", "到达后再用")
    return refusal


def require_hands_free(context: Context, what: str) -> None:
    """@brief 驾车时需要手动操作的瞬时动作（发消息、提交成果）以 ACTIVITY_PRECONDITION_UNMET 拒绝；步行途中照常。"""
    refusal = _driving_refusal(context, what)
    if refusal is not None:
        raise refusal.error()


def action_note(context: Context, item: Item, names: Mapping[str, str]) -> str | None:
    """@brief 交互对象层的一句说明：单独选这一项时第一条不成立的准入的原因，全部成立时为 None。

    :param names: 活动身份→本轮可读名称（名额与出发前的原因据此点名活动）。
    """
    refusal = activity_control.start_admission_refusal(context.state, context.agent_id,
                                                       ([item.canonical], [item.way]), dict(names))
    refusal = refusal or group_refusal(context, [item])
    return refusal.reason if refusal is not None else None


def _presence_refusal(context: Context, item: Item, present: str | None, departs: bool) -> Refusal | None:
    """@brief 目标在场与实际地点：要在某地做的事需要人在那里，途中与同一请求出发都不算在场。"""
    if item.place is not None:
        place = context.place_names.get(item.place, "指定地点")
        if departs:
            return Refusal(_UNMET, f"{item.label}需要人在{place}，不能和出行一起开始", f"先在{place}做完再出发",
                           ("required_location_id",))
        if present != item.place:
            return Refusal(_UNMET, f"{item.label}需要在{place}", f"去{place}", ("required_location_id",))
    if item.partner is None:
        return None
    partner = context.state.agents.get(item.partner)
    where = present_location(context.state, item.partner) if partner is not None else None
    if not departs and present is not None and where == present:
        if pair_chat(context.state, context.agent_id, f"agent:{item.partner}") is not None:
            return Refusal("CHAT_ALREADY_ACTIVE", "你们已经在交谈了", "继续交谈，或先结束这次交谈")
        return None
    name = partner.name if partner is not None else "对方"
    step = f"去{context.place_names.get(where, '对方所在的地方')}" if where is not None else "等对方到了再说"
    return Refusal(_UNMET, f"要和{name}当面在一起才能交谈", "先交谈，再出发" if departs else step,
                   ("partner_present",))


def _hands_free_refusal(context: Context, item: Item, items: Sequence[Item]) -> Refusal | None:
    """@brief 出行方式：驾车途中或与驾车同一请求时，远程下单这类手动操作的瞬时动作不受理。"""
    if not item.manual:
        return None
    refusal = _driving_refusal(context, "下单")
    if refusal is None and any(other.canonical == "travel_to" and other.way == TRAVEL_WAYS["driving"]
                               for other in items):
        return Refusal(_UNMET, "开车时不能下单", "先下单，再出发", ("hands_free",))
    return refusal


def _driving_refusal(context: Context, what: str) -> Refusal | None:
    """@brief 正在驾车时需要手动操作的事不成立：点名到达后再做。"""
    destination = driving_destination(context.state, context.agent_id)
    if destination is None:
        return None
    place = context.place_names.get(destination, "目的地")
    return Refusal(_UNMET, f"开车时不能{what}", f"到达{place}后再{what}", ("hands_free",))


def _claims_refusal(context: Context, items: Sequence[Item]) -> Refusal | None:
    """@brief 占用：组内各项与已有进行中活动、组内各项之间的注册表占用交集非空即 RESOURCE_CONFLICT。"""
    running = active_activities(context.state, context.agent_id)
    for item in items:
        blocking = [activity for activity in running if activity.resource_claims & item.claims]
        if blocking:
            listed = "、".join(context.activity_names.get(activity.activity_id, activity.name) for activity in blocking)
            return Refusal("RESOURCE_CONFLICT", f"{item.label}和正在做的{listed}不能同时进行",
                           f"先结束或暂停{listed}", ("resource_claims",))
    for index, item in enumerate(items):
        for other in items[index + 1:]:
            if item.claims & other.claims:
                return Refusal("RESOURCE_CONFLICT", f"{item.label}和{other.label}不能同时进行",
                               "一次只选其中一件", ("resource_claims",))
    return None


def _claims(canonical: str, action: Mapping[str, Any], object_id: str, facts: WorldFacts) -> frozenset[str]:
    """@brief 该项开始后的占用：购买不占，交谈与通话按社交占用，其余按注册表（驾车、观看内容品类的条件占用）。"""
    if canonical == "purchase":
        return frozenset()
    if canonical in {"chat", "call"}:
        return SOCIAL_RESOURCE_CLAIMS
    view = default_affordances()
    kinds = [name for name, spec in view.kinds.items() if spec.action == canonical]
    if len(kinds) != 1:
        raise BusinessRuleError("ACTION_NOT_SUPPORTED")
    content = facts.contents.get(object_id) if canonical == "watch" else None
    return view.resource_claims(kinds[0], travel_mode=action.get("travel_mode"),
                                content_kind=getattr(content, "kind", None))
