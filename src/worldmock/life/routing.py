"""@brief 逐项活动来源、本人或实名收货人及消息关联的统一纯校验。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from worldmock.errors import BusinessRuleError
from worldmock.life.interaction_menu import GIVE_WAY

_INVALID = "ACTION_REQUEST_INVALID"
RECIPIENT_NOT_FOUND = "RECIPIENT_NOT_FOUND"
ROUTE_KEYS = ("from", "to", "recipient_id", "to_self", "request_message_id", "answering")
ROUTE_FIELDS_INVALID = "ROUTE_FIELDS_INVALID"
SUPPLY_SOURCE_MISMATCH = "SUPPLY_SOURCE_MISMATCH"


@dataclass(frozen=True, slots=True)
class RoutingNames:
    """@brief 冻结观察中的对象类别、其他人物、消息和本人身份。"""

    kinds: Mapping[str, str]
    people: Mapping[str, str]
    messages: Mapping[str, tuple[str, str]]
    places: tuple[str, ...] = ()
    home: str | None = None
    self_id: str | None = None


@dataclass(frozen=True, slots=True)
class RouteIssue:
    """@brief 原始参数位置的来源或去向错误。"""

    code: str
    detail: str
    index: int | None
    field: str
    written: Any
    reason: str
    fix: str
    host_code: str = ROUTE_FIELDS_INVALID
    allowed_values: tuple = ()


def routing_names(observation: Mapping[str, Any]) -> RoutingNames:
    """@brief 从私有绑定取得其他人物和本人身份，姓名不可替代 null 本人。"""
    rows = observation.get("interaction_bindings") or []
    people = {row["name"]: row["person_id"] for row in observation.get("person_bindings") or []}
    people.update({row["object"]: row["object_id"] for row in rows if row["kind"] in {"person", "contact"}})
    places = {row["location_id"]: row["name"] for row in observation.get("locations") or []}
    own = next((row["object_id"] for row in rows if row["kind"] == "self"), None)
    own = own or (f"agent:{observation['agent_id']}" if "agent_id" in observation else None)
    return RoutingNames({row["object"]: row["kind"] for row in rows}, people,
                        {row["name"]: (row["message_id"], row["sender_id"])
                         for row in observation.get("message_bindings") or []},
                        tuple(places.values()), places.get(observation.get("home_location_id")), own)


def combination_error(request: Any, names: RoutingNames) -> tuple[str, str] | None:
    """@brief 环境准入读取同一完整纯校验的首条错误。"""
    issues = combination_issues(request, names)
    chosen = next((issue for issue in issues if issue.code == _INVALID), issues[0] if issues else None)
    return (chosen.code, chosen.detail) if chosen else None


def combination_issues(request: Any, names: RoutingNames) -> list[RouteIssue]:
    """@brief 所有活动项和消息关联错误一次全报。"""
    issues = []
    seen = set()
    for index, item in enumerate(request.items):
        pair = (item.target, item.to)
        if pair in seen:
            issues.append(_issue(index, "target", item.target, "A repeated target needs a different recipient."))
        seen.add(pair)
        issues.extend(_item_errors(index, item, names))
    answer = _answering_error(request, names)
    return [*issues, *([answer] if answer else [])]


def fill_route(verb: str, way: str, written_to: str | None) -> tuple[str | None, str | None]:
    """@brief 候选的本人来源由引擎推导，只有购买或给予接受实名他人。"""
    destination = written_to if way == GIVE_WAY or (verb == "obtain" and way != "制作") else None
    return None, destination if destination != "自己" else None


def routed_selection(rows: list[dict], request: Any, names: RoutingNames) -> list[dict]:
    """@brief 解析结果附加真实收货人身份；null 统一指本人当前地点。"""
    linked = names.messages.get(request.answering) if request.answering else None
    routed = []
    for row, item in zip(rows, request.items):
        recipient = names.self_id if item.to is None else names.people.get(item.to)
        answered = linked is not None and recipient == linked[1]
        routed.append({**row, "from": item.from_, "to": item.to, "recipient_id": recipient,
                       "to_self": item.to is None, "request_message_id": linked[0] if answered else None,
                       "answering": request.answering if answered else None})
    return routed


def require_recipients(selection: list[dict]) -> None:
    """@brief 实名收货人须在已知其他人物中。"""
    if any(row.get("to") is not None and row.get("recipient_id") is None for row in selection):
        logging.error("Start activity names an unknown recipient")
        raise BusinessRuleError(RECIPIENT_NOT_FOUND)


def _issue(index: int | None, field: str, written: Any, reason: str, allowed: tuple = ()) -> RouteIssue:
    """@brief 路由错误同时声明精确 JSON 合法闭集。"""
    return RouteIssue(_INVALID, reason, index, field, written, reason, "Correct the reported route parameter.",
                      allowed_values=allowed)


def _item_errors(index: int, item: Any, names: RoutingNames) -> list[RouteIssue]:
    """@brief 非购买来源必须 null，非转移去向必须 null，给予须实名他人。"""
    from worldmock.life.name_resolution import way_options, way_verb

    verb, options = way_verb(item.way), way_options(item.way)
    purchase = verb == "obtain" and "制作" not in options
    issues = []
    if not purchase and item.from_ is not None:
        issues.append(_issue(index, "from", item.from_, "This way requires from=null.", (None,)))
    if GIVE_WAY in options:
        if item.to is None:
            issues.append(_issue(index, "to", None, "Giving requires a known other person."))
        elif item.to not in names.people:
            issues.append(_person_error(index, item.to))
    elif not purchase and item.to is not None:
        issues.append(_issue(index, "to", item.to, "This way acts on the caller and requires to=null.", (None,)))
    elif purchase and item.to is not None and item.to not in names.people:
        issues.append(_person_error(index, item.to))
    if purchase and item.from_ is not None and names.kinds.get(item.target) == "supply":
        if _other_place(item.target, item.from_, names):
            store = item.target.split("出售的", 1)[0]
            reason = f"The target names store {store}, but from names the different place {item.from_}."
            issues.append(RouteIssue(SUPPLY_SOURCE_MISMATCH, reason, index, "from", item.from_, reason,
                                     f"Set from to null or {store}.", SUPPLY_SOURCE_MISMATCH))
    return issues


def _other_place(label: str, source: str, names: RoutingNames) -> bool:
    """@brief 目标门店与明确来源地点是否冲突。"""
    from worldmock.life.name_resolution import names_store, written_place

    place = written_place(source, names.places, names.home)
    return place is not None and not names_store(label, place)


def _person_error(index: int, destination: str) -> RouteIssue:
    """@brief 未知姓名与旧本人字符串均拒绝，不自动替换。"""
    reason = "The recipient is not a known other person; use null for the caller."
    return RouteIssue(RECIPIENT_NOT_FOUND, reason, index, "to", destination, reason,
                      "Use null for the caller or the exact name of a known other person.", ROUTE_FIELDS_INVALID)


def _answering_error(request: Any, names: RoutingNames) -> RouteIssue | None:
    """@brief 关联未回复消息须至少有一项给该消息发件人。"""
    if request.answering is None:
        return None
    message = names.messages.get(request.answering)
    if message is None:
        return _issue(None, "answering", request.answering, "answering requires a known unanswered message.")
    if not any(names.people.get(item.to) == message[1] for item in request.items if item.to is not None):
        return _issue(None, "answering", request.answering, "An item must address the message sender.")
    return None
