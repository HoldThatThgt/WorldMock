"""@brief start_activity 的来源、去向与所回应的消息（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」「代点请求与
订单关联」）。

合法组合由 combination_error 判定，宿主本地校验按协议纠错、环境受理以业务拒绝兜底，两处共用：
- obtain：to 为「自己」或本轮列出的人物（收货人）；目标是本轮列出的「<门店>出售的<商品>」或方式为制作时 from 为 null；
  制作只配 to=自己；
- consume：from 为「自己」（手边的持有物）；食用、使用只配 to=自己，给予只配人物；
- 其他动词：from、to 都为 null。
同一请求中只有 to 不同时才允许重复目标。answering 为 null，或是本轮可见直接消息的规范名称（私有绑定 message_bindings），
且至少一项的 to 是它的发件人；它只关联 to 等于该发件人的那几项。to 写了本轮没有的人物名时为 RECIPIENT_NOT_FOUND，
其余不合法都是 ACTION_REQUEST_INVALID。
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
_NOT_FOUND = "RECIPIENT_NOT_FOUND"
#: 带来源与去向的两个动词。
_ROUTED_VERBS = frozenset({"obtain", "consume"})
#: 可以作为去向的私有绑定类别：同场人物、场景联系人与「自己」。
_PERSON_KINDS = frozenset({"person", "contact", "self"})
#: routed_selection 写在绑定行上的来源、去向与关联键（指名购买解析出供给后保留这些键）。
ROUTE_KEYS = ("from", "to", "recipient_id", "to_self", "request_message_id", "answering")


@dataclass(frozen=True, slots=True)
class RoutingNames:
    """@brief 一次观察中判定来源与去向所需的名称：目标名 → 绑定类别，人物名（含「自己」）→ 身份，消息名 → （消息身份，
    发件人身份）。"""

    kinds: Mapping[str, str]
    people: Mapping[str, str]
    messages: Mapping[str, tuple[str, str]]


def routing_names(observation: Mapping[str, Any]) -> RoutingNames:
    """@brief 从观察的私有绑定（interaction_bindings、message_bindings）取判定所需的名称。"""
    rows = observation.get("interaction_bindings") or []
    return RoutingNames(
        kinds={row["object"]: row["kind"] for row in rows},
        people={row["object"]: row["object_id"] for row in rows if row["kind"] in _PERSON_KINDS},
        messages={row["name"]: (row["message_id"], row["sender_id"])
                  for row in observation.get("message_bindings") or []})


def combination_error(request: Any, names: RoutingNames) -> tuple[str, str] | None:
    """@brief 来源、去向与 answering 的第一条不合法之处：（错误码，英文说明）；合法时为 None。

    ACTION_REQUEST_INVALID 优先于 RECIPIENT_NOT_FOUND：组合本身不合法时不再追究人物名。
    """
    count = len(request.targets)
    if len(request.from_) != count or len(request.to) != count:
        return _INVALID, "start_activity from and to must have one item per target"
    pairs = list(zip(request.targets, request.to))
    if len(set(pairs)) != len(pairs):
        return _INVALID, "start_activity targets repeat only with different to"
    items = zip(request.targets, request.actions, request.ways, request.from_, request.to)
    errors = [error for error in [*(_item_error(item, names) for item in items), _answering_error(request, names)]
              if error is not None]
    return next((error for error in errors if error[0] == _INVALID), errors[0] if errors else None)


def routed_selection(rows: list[dict], request: Any, names: RoutingNames) -> list[dict]:
    """@brief 给解析好的绑定行逐项写上来源与去向：from、to、recipient_id（本轮没有的人物为 None）、to_self，以及被
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
    """@brief 去向写了本轮没有的人物时以 RECIPIENT_NOT_FOUND 拒绝（在按收货地解析指名购买与整组准入之前）。"""
    if any(row.get("to") is not None and row.get("recipient_id") is None for row in selection):
        logging.error("Start activity names a recipient that is not visible")
        raise BusinessRuleError(_NOT_FOUND)


def _item_error(item: tuple, names: RoutingNames) -> tuple[str, str] | None:
    """@brief 一项的来源与去向是否与动词、方式相配。"""
    target, verb, way, source, destination = item
    if verb not in _ROUTED_VERBS:
        if source is not None or destination is not None:
            return _INVALID, "start_activity from and to are null for this verb"
        return None
    if verb == "consume":
        if source != SELF_NAME:
            return _INVALID, "start_activity consume takes from 自己"
        if way == GIVE_WAY:
            return _person_error(destination, names, allow_self=False)
        return None if destination == SELF_NAME else (_INVALID, "start_activity 食用 and 使用 take to 自己")
    if way == "制作":
        if source is not None or destination != SELF_NAME:
            return _INVALID, "start_activity 制作 takes from null and to 自己"
        return None
    if source is not None and names.kinds.get(target) == "supply":
        return _INVALID, "start_activity from is null when the target already names its store"
    return _person_error(destination, names, allow_self=True)


def _person_error(destination: str | None, names: RoutingNames, allow_self: bool) -> tuple[str, str] | None:
    """@brief 去向要是本轮列出的人物（allow_self 时也可以是「自己」）。"""
    if destination is None:
        return _INVALID, "start_activity obtain and consume need to"
    if destination == SELF_NAME and not allow_self:
        return _INVALID, "start_activity 给予 takes a person in to"
    if destination not in names.people:
        return _NOT_FOUND, "start_activity to is not a visible person"
    return None


def _answering_error(request: Any, names: RoutingNames) -> tuple[str, str] | None:
    """@brief answering 要是本轮可见的直接消息，且至少一项的 to 是它的发件人。"""
    if request.answering is None:
        return None
    message = names.messages.get(request.answering)
    if message is None:
        return _INVALID, "start_activity answering is not a visible message"
    if not any(names.people.get(destination) == message[1] for destination in request.to if destination):
        return _INVALID, "start_activity answering needs an item whose to is the message sender"
    return None
