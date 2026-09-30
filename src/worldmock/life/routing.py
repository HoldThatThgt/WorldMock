"""@brief start_activity 的来源、去向与所回应的消息（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」「代点请求与
订单关联」）。

合法组合由 combination_issues 逐项判定，宿主本地校验按协议纠错、环境受理以业务拒绝兜底，两处共用：
- obtain：to 为「自己」或人物（收货人）；from 是可选约束（docs/dev/SPEC-dayrun-fidelity.md 3.2、3.3、3.5）：null、门店名
  或 App 称呼（渠道），由环境在指名购买中解析（named_purchase）；本轮列出的「<门店>出售的<商品>」写了另一个已知地点
  （冻结地点表的显示名，或「家」「住处」）作 from 时是 SUPPLY_SOURCE_MISMATCH，写的正是标签里的门店已在名称解析中按冗余
  换成 null，App 称呼与不认识的写法交给环境（包含该门店的渠道照常受理，其余以 SUPPLY_SOURCE_MISMATCH 或
  SOURCE_NOT_FOUND 拒绝）；方式为制作时 from 为 null，且只配 to=自己；
- consume：from 为「自己」（手边的持有物）；食用、使用只配 to=自己，给予只配人物；
- 其他动词：from、to 都为 null。
人物是本轮列出的人物与联系人，以及不论是否同处的同世界人物（私有绑定 person_bindings；不同处由给予与配送各自拒绝）。
同一请求中只有 to 不同时才允许重复目标。answering 为 null，或是本轮可见直接消息的规范名称（私有绑定 message_bindings），
且至少一项的 to 是它的发件人；它只关联 to 等于该发件人的那几项。to 写的名称不是任何人物时为 RECIPIENT_NOT_FOUND
（业务拒绝，宿主不纠正），列出的标签写了另一个已知地点作 from 时为 SUPPLY_SOURCE_MISMATCH（宿主纠正；绕过宿主时由环境
的指名购买带候选拒绝），其余不合法都是 ACTION_REQUEST_INVALID。每条不合法之处另带宿主协议纠错码（SUPPLY_SOURCE_MISMATCH，
其余为 ROUTE_FIELDS_INVALID）与给人物看的原因和改法（docs/dev/SPEC-dayrun-fidelity.md 第四章「错误合同」）；fill_route
给出一项（动词，方式）照填时合法的 from 与 to（候选用）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from worldmock.errors import BusinessRuleError
from worldmock.life.interaction_menu import GIVE_WAY

#: 「自己」目标的名称（去向为自己、来源为手边的持有物）。
SELF_NAME = "自己"
_INVALID = "ACTION_REQUEST_INVALID"
#: 去向不是任何人物的拒绝码（业务拒绝：宿主本地校验放行，由环境受理拒绝）。
RECIPIENT_NOT_FOUND = "RECIPIENT_NOT_FOUND"
#: 带来源与去向的两个动词。
_ROUTED_VERBS = frozenset({"obtain", "consume"})
#: 可以作为去向的私有绑定类别：同场人物、场景联系人与「自己」。
_PERSON_KINDS = frozenset({"person", "contact", "self"})
#: routed_selection 写在绑定行上的来源、去向与关联键（指名购买解析出供给后保留这些键）。
ROUTE_KEYS = ("from", "to", "recipient_id", "to_self", "request_message_id", "answering")
#: 宿主协议纠错码：来源、去向或 answering 不相配，与目标已写明门店而 from 写的是另一个地点（也是环境的名称类拒绝码）。
ROUTE_FIELDS_INVALID = "ROUTE_FIELDS_INVALID"
SUPPLY_SOURCE_MISMATCH = "SUPPLY_SOURCE_MISMATCH"
#: 列出的标签写了另一个地点作 from 的英文说明（协议错误串与日志）。
_OTHER_STORE_DETAIL = "start_activity from names another place than the store in the target"


@dataclass(frozen=True, slots=True)
class RoutingNames:
    """@brief 一次观察中判定来源与去向所需的名称：目标名 → 绑定类别，人物名（含「自己」）→ 身份，消息名 → （消息身份，
    发件人身份），以及冻结地点表的地点显示名与本人住处的显示名（判定 from 是不是另一个已知地点）。"""

    kinds: Mapping[str, str]
    people: Mapping[str, str]
    messages: Mapping[str, tuple[str, str]]
    places: tuple[str, ...] = ()
    home: str | None = None


@dataclass(frozen=True, slots=True)
class RouteIssue:
    """@brief 来源、去向或 answering 的一处不合法。

    :param code: 业务拒绝码（ACTION_REQUEST_INVALID、RECIPIENT_NOT_FOUND 或 SUPPLY_SOURCE_MISMATCH）。
    :param detail: 英文说明（协议错误串与日志）。
    :param index: 出错的一项（从 0 起）；整个请求的问题（数组不等长、重复目标、answering）为 None。
    :param field: 出错的参数名（from、to、targets 或 answering）。
    :param written: 人物所写的原文（null 写作「null」）。
    :param reason: 给人物看的一句原因。
    :param fix: 给人物看的一句改法。
    :param host_code: 宿主协议纠错码；RECIPIENT_NOT_FOUND 由环境拒绝，宿主放行，为空串。
    """

    code: str
    detail: str
    index: int | None
    field: str
    written: str
    reason: str
    fix: str
    host_code: str = ROUTE_FIELDS_INVALID


def routing_names(observation: Mapping[str, Any]) -> RoutingNames:
    """@brief 从观察的私有绑定（interaction_bindings、person_bindings、message_bindings）与冻结地点表（locations、
    home_location_id）取判定所需的名称。

    人物名包括不论是否同处的同世界人物；与本轮列出的对象同名时以列出的为准。
    """
    rows = observation.get("interaction_bindings") or []
    people = {row["name"]: row["person_id"] for row in observation.get("person_bindings") or []}
    people.update({row["object"]: row["object_id"] for row in rows if row["kind"] in _PERSON_KINDS})
    places = {row["location_id"]: row["name"] for row in observation.get("locations") or []
              if isinstance(row, Mapping) and isinstance(row.get("name"), str)}
    return RoutingNames(
        kinds={row["object"]: row["kind"] for row in rows},
        people=people,
        messages={row["name"]: (row["message_id"], row["sender_id"])
                  for row in observation.get("message_bindings") or []},
        places=tuple(places.values()), home=places.get(observation.get("home_location_id")))


def combination_error(request: Any, names: RoutingNames) -> tuple[str, str] | None:
    """@brief 来源、去向与 answering 的第一条不合法之处：（错误码，英文说明）；合法时为 None。

    ACTION_REQUEST_INVALID 优先于 RECIPIENT_NOT_FOUND 与 SUPPLY_SOURCE_MISMATCH：组合本身不合法时不再追究人物名与来源
    （后两者由环境在受理时带说明拒绝）。
    """
    issues = combination_issues(request, names)
    chosen = next((issue for issue in issues if issue.code == _INVALID), issues[0] if issues else None)
    return (chosen.code, chosen.detail) if chosen is not None else None


def combination_issues(request: Any, names: RoutingNames) -> list[RouteIssue]:
    """@brief 来源、去向与 answering 的全部不合法之处：数组不等长或目标重复时只报这一条（逐项无从对应），否则逐项报出，
    answering 的问题排在最后；合法时为空列表。"""
    count = len(request.targets)
    if len(request.from_) != count or len(request.to) != count:
        return [RouteIssue(_INVALID, "start_activity from and to must have one item per target", None, "from",
                           "", "from 与 to 要和 targets 逐项对应、一样长",
                           "每个目标各写一个 from 和一个 to，不需要时写 null")]
    pairs = list(zip(request.targets, request.to))
    if len(set(pairs)) != len(pairs):
        return [RouteIssue(_INVALID, "start_activity targets repeat only with different to", None, "targets", "",
                           "同一个目标只有在 to 不同时才能重复写", "去掉重复的一项，或者给它写另一个收货人")]
    items = zip(request.targets, request.actions, request.ways, request.from_, request.to)
    issues = [_item_error(index, item, names) for index, item in enumerate(items)]
    return [issue for issue in (*issues, _answering_error(request, names)) if issue is not None]


def fill_route(verb: str, way: str, written_to: str | None) -> tuple[str | None, str | None]:
    """@brief 一项（动词，方式）照填时合法的（from，to）（候选请求用）：obtain 的 from 为 null，to 取所写的收货人（没写或
    制作时为「自己」）；consume 的 from 为「自己」，食用、使用的 to 为「自己」，给予的 to 取所写的人物（没写人物时为 null，
    由人物补上）；其他动词都为 null。"""
    if verb not in _ROUTED_VERBS:
        return None, None
    if verb == "obtain":
        return None, SELF_NAME if way == "制作" or written_to is None else written_to
    if way == GIVE_WAY:
        return SELF_NAME, written_to if written_to not in (None, SELF_NAME) else None
    return SELF_NAME, SELF_NAME


def routed_selection(rows: list[dict], request: Any, names: RoutingNames) -> list[dict]:
    """@brief 给解析好的绑定行逐项写上来源与去向：from、to、recipient_id（不是任何人物时为 None）、to_self，以及被
    answering 关联时的 request_message_id 与 answering（未关联为 None）。"""
    linked = names.messages.get(request.answering) if request.answering is not None else None
    routed = []
    for row, source, destination in zip(rows, request.from_, request.to):
        recipient = names.people.get(destination) if destination is not None else None
        answered = linked is not None and recipient is not None and recipient == linked[1]
        routed.append({**row, "from": source, "to": destination, "recipient_id": recipient,
                       "to_self": recipient is not None and recipient == names.people.get(SELF_NAME),
                       "request_message_id": linked[0] if answered else None,
                       "answering": request.answering if answered else None})
    return routed


def require_recipients(selection: list[dict]) -> None:
    """@brief 去向写的名称不是任何人物时以 RECIPIENT_NOT_FOUND 拒绝（在按收货地解析指名购买与整组准入之前）。"""
    if any(row.get("to") is not None and row.get("recipient_id") is None for row in selection):
        logging.error("Start activity names a recipient that is not a known person")
        raise BusinessRuleError(RECIPIENT_NOT_FOUND)


def _written(value: str | None) -> str:
    """@brief 人物所写的原文；null 写作「null」。"""
    return "null" if value is None else value


def _item_error(index: int, item: tuple, names: RoutingNames) -> RouteIssue | None:
    """@brief 一项的来源与去向是否与动词、方式相配。"""
    target, verb, way, source, destination = item
    if verb not in _ROUTED_VERBS:
        if source is not None or destination is not None:
            field, value = ("from", source) if source is not None else ("to", destination)
            return RouteIssue(_INVALID, "start_activity from and to are null for this verb", index, field,
                              _written(value), f"{verb} 的 from 与 to 都要写 null", "from 与 to 写 null")
        return None
    if verb == "consume":
        return _consume_error(index, (way, source, destination), names)
    if way == "制作":
        if source is not None or destination != SELF_NAME:
            field, value = ("from", source) if source is not None else ("to", destination)
            return RouteIssue(_INVALID, "start_activity 制作 takes from null and to 自己", index, field,
                              _written(value), "制作的 from 写 null、to 写「自己」", "from 写 null，to 写「自己」")
        return None
    if source is not None and names.kinds.get(target) == "supply" and _other_place(target, source, names):
        return RouteIssue(SUPPLY_SOURCE_MISMATCH, _OTHER_STORE_DETAIL, index, "from", source,
                          f"目标已经写明了门店，from「{source}」是另一个地点", "from 写 null，或者写这家店所在的 App",
                          SUPPLY_SOURCE_MISMATCH)
    return _person_error(index, destination, names, allow_self=True)


def _other_place(label: str, source: str, names: RoutingNames) -> bool:
    """@brief 列出的标签「<门店>出售的<商品>」写的 from 是否是另一个已知地点：所写名称唯一对应冻结地点表中的地点（或
    「家」「住处」指的住处），且不是标签写明的门店；App 称呼与不认识的写法不在此列（交给环境按渠道与来源解析）。"""
    from worldmock.life.name_resolution import names_store, written_place  # 名称解析导入本模块的 fill_route

    place = written_place(source, names.places, names.home)
    return place is not None and not names_store(label, place)


def _consume_error(index: int, route: tuple, names: RoutingNames) -> RouteIssue | None:
    """@brief consume 一项：from 为「自己」；食用、使用的 to 为「自己」，给予的 to 为人物。"""
    way, source, destination = route
    if source != SELF_NAME:
        return RouteIssue(_INVALID, "start_activity consume takes from 自己", index, "from", _written(source),
                          "consume 的 from 要写「自己」（手边的持有物）", "from 写「自己」")
    if way == GIVE_WAY:
        return _person_error(index, destination, names, allow_self=False)
    if destination == SELF_NAME:
        return None
    return RouteIssue(_INVALID, "start_activity 食用 and 使用 take to 自己", index, "to", _written(destination),
                      "食用、使用的 to 要写「自己」", "to 写「自己」；要把东西给别人时方式写「给予」")


def _person_error(index: int, destination: str | None, names: RoutingNames, allow_self: bool) -> RouteIssue | None:
    """@brief 去向要是人物：本轮列出的人物与联系人或同世界人物（allow_self 时也可以是「自己」）。"""
    if destination is None:
        return RouteIssue(_INVALID, "start_activity obtain and consume need to", index, "to", "null",
                          "obtain 与 consume 要在 to 写收货人或「自己」", "to 写「自己」或人物的名称")
    if destination == SELF_NAME and not allow_self:
        return RouteIssue(_INVALID, "start_activity 给予 takes a person in to", index, "to", destination,
                          "给予的 to 要写一个人，不能是「自己」", "to 写要给的人的名称")
    if destination not in names.people:
        return RouteIssue(RECIPIENT_NOT_FOUND, "start_activity to is not a known person", index, "to", destination,
                          "to 写的名称不是任何人物", "to 照工具层写人物或联系人的名称", "")
    return None


def _answering_error(request: Any, names: RoutingNames) -> RouteIssue | None:
    """@brief answering 要是本轮可见的直接消息，且至少一项的 to 是它的发件人。"""
    if request.answering is None:
        return None
    message = names.messages.get(request.answering)
    if message is None:
        return RouteIssue(_INVALID, "start_activity answering is not a visible message", None, "answering",
                          request.answering, "answering 要写本轮可见的一条直接消息的名称",
                          "answering 照「<发件人> HH:MM 的消息」写，或者写 null")
    if not any(names.people.get(destination) == message[1] for destination in request.to if destination):
        return RouteIssue(_INVALID, "start_activity answering needs an item whose to is the message sender", None,
                          "answering", request.answering, "answering 回应的消息，要有一项的 to 是它的发件人",
                          "把给发件人的那一项 to 写成发件人，或者 answering 写 null")
    return None
