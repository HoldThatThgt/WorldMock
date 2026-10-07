"""@brief 五字段活动请求的应用、支付、对方与消息关联纯校验。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from worldmock.errors import BusinessRuleError
from worldmock.exchange.types import ActionCategory, AppPurpose
from worldmock.life.error_contract import MAX_CANDIDATES, Candidate
from worldmock.life.guidance import refusal_for_code
from worldmock.life.vocabulary import WAYS, way_options

_INVALID = "ACTION_REQUEST_INVALID"
RECIPIENT_NOT_FOUND = "RECIPIENT_NOT_FOUND"
ROUTE_FIELDS_INVALID = "ROUTE_FIELDS_INVALID"
ROUTE_KEYS = ("app", "to", "pay_app", "app_id", "payment_app_id", "recipient_id", "counterpart",
              "to_self", "request_message_id", "answering")


@dataclass(frozen=True, slots=True)
class RoutingNames:
    """@brief 冻结观察的完整应用动作登记表、可收货人物及两种社交对象。"""

    actions: Mapping[str, Mapping[str, Any]]
    apps: Mapping[str, Mapping[str, Any]]
    people: Mapping[str, str]
    present_people: Mapping[str, str]
    contacts: Mapping[str, str]
    messages: Mapping[str, tuple[str, str]]
    self_id: str


@dataclass(frozen=True, slots=True)
class RouteIssue:
    """@brief 请求原始字段的业务码、准确原因、完整合法名单与可提交候选。"""

    code: str
    detail: str
    index: int | None
    field: str
    written: Any
    reason: str
    fix: str
    host_code: str = ROUTE_FIELDS_INVALID
    allowed_values: tuple = ()
    candidates: tuple[Candidate, ...] = ()


def routing_names(observation: Mapping[str, Any]) -> RoutingNames:
    """@brief 仅使用真实冻结事实，不从动作名猜通信方式或从用途猜所用应用。"""
    rows = observation["interaction_bindings"]
    present = {row["object"]: row["object_id"] for row in rows if row["kind"] == "person"}
    contacts = {row["object"]: row["object_id"] for row in rows if row["kind"] == "contact"}
    people = {row["name"]: row["person_id"] for row in observation["person_bindings"]}
    people.update(present)
    people.update(contacts)
    return RoutingNames(
        {row["name"]: row for row in observation["actions"]},
        {row["name"]: row for row in observation["apps"]}, people, present, contacts,
        {row["name"]: (row["message_id"], row["sender_id"]) for row in observation["message_bindings"]},
        f"agent:{observation['agent_id']}")


def combination_error(request: Any, names: RoutingNames) -> tuple[str, str] | None:
    """@brief 环境准入读取与宿主相同的完整校验中首个错误。"""
    issues = combination_issues(request, names)
    return (issues[0].code, issues[0].detail) if issues else None


def combination_issues(request: Any, names: RoutingNames) -> list[RouteIssue]:
    """@brief 所有独立的字段和消息关联错误一次全报，重复项不能部分受理。"""
    issues, seen = [], set()
    for index, item in enumerate(request.items):
        pair = (item.target, item.to)
        if pair in seen:
            reason = "A repeated target needs a different recipient."
            issues.append(RouteIssue(_INVALID, reason, index, "target", item.target, reason,
                                     "Keep one item for each target and recipient."))
        seen.add(pair)
        issues.extend(_item_errors(request, index, names))
    answer = _answering_error(request, names)
    if answer is not None:
        issues.append(answer)
    if issues:
        logging.error("Start activity route rejected: %s", [(issue.field, issue.code) for issue in issues])
    return issues


def routed_selection(rows: list[dict], request: Any, names: RoutingNames) -> list[dict]:
    """@brief 已校验请求加入亲手点名的应用身份、对方或收货人及实际消息关联。"""
    linked = names.messages.get(request.answering) if request.answering else None
    routed = []
    for row, item in zip(rows, request.items):
        app_id = None if item.app is None else names.apps[item.app]["app_id"]
        payment = None if item.pay_app is None else names.apps[item.pay_app]["app_id"]
        recipient = names.self_id if item.to is None else names.people.get(item.to)
        canonical = row["selected_action"]["canonical_action"]
        counterpart = recipient if canonical == "do" and names.actions[item.target]["category"] == "social" else None
        answered = linked is not None and recipient == linked[1]
        selected = {**row["selected_action"], "app_id": app_id, "payment_app_id": payment}
        if canonical == "travel_to":
            selected["navigation_app_id"] = app_id
        routed.append({**row, "selected_action": selected, "app": item.app, "to": item.to,
                       "pay_app": item.pay_app, "app_id": app_id, "payment_app_id": payment,
                       "recipient_id": recipient, "counterpart": counterpart, "to_self": item.to is None,
                       "request_message_id": linked[0] if answered else None,
                       "answering": request.answering if answered else None})
    return routed


def require_recipients(selection: list[dict]) -> None:
    """@brief 已点名但没有真实收货人物身份的请求不能进入结算。"""
    if any(row["to"] is not None and row["recipient_id"] is None for row in selection):
        logging.error("Start activity names an unknown recipient")
        raise BusinessRuleError(RECIPIENT_NOT_FOUND)


def _item_errors(request: Any, index: int, names: RoutingNames) -> list[RouteIssue]:
    """@brief 方式决定哪些键可用；动作类别决定社交对方，应用绑定决定线上路径。"""
    item = request.items[index]
    options = way_options(item.way)
    if not options:
        return []
    canonical = WAYS[next(iter(options))][1]
    if canonical in {"wait", "sleep", "collect_parcel"}:
        return [_null_issue(index, field, getattr(item, field)) for field in ("app", "to", "pay_app")
                if getattr(item, field) is not None]
    if canonical == "travel_to":
        issues = [_null_issue(index, field, getattr(item, field)) for field in ("to", "pay_app")
                  if getattr(item, field) is not None]
        return [*issues, *_app_issues(request, index, _purpose_names(names, AppPurpose.NAVIGATION))]
    if canonical == "purchase":
        return _purchase_issues(request, index, names)
    return _action_issues(request, index, names)


def _null_issue(index: int, field: str, written: Any) -> RouteIssue:
    """@brief 方式不接受的键精确声明只允许 JSON null。"""
    refusal = refusal_for_code(ROUTE_FIELDS_INVALID)
    return RouteIssue(refusal.code, refusal.reason, index, field, written, refusal.reason, refusal.fix,
                      allowed_values=(None,))


def _purpose_names(names: RoutingNames, purpose: AppPurpose) -> tuple[str, ...]:
    """@brief 用途候选按真实应用身份排序，保留完整名单。"""
    return tuple(row["name"] for row in sorted(names.apps.values(), key=lambda row: row["app_id"])
                 if purpose in row["purposes"])


def _bound_names(names: RoutingNames, app_ids) -> tuple[str, ...]:
    """@brief 动作或付款绑定只映射其明示应用身份。"""
    return tuple(row["name"] for row in sorted(names.apps.values(), key=lambda row: row["app_id"])
                 if row["app_id"] in app_ids)


def _app_issues(request: Any, index: int, offered: tuple[str, ...]) -> list[RouteIssue]:
    """@brief 不是完整登记名称或不属于该目标的应用，统一给可写应用名单。"""
    item = request.items[index]
    if item.app is not None and item.app not in offered:
        return [_name_issue(request, index, "app", "APP_NOT_OFFERED", offered)]
    return []


def _purchase_issues(request: Any, index: int, names: RoutingNames) -> list[RouteIssue]:
    """@brief 购物应用与付款绑定各自严格校验，收货人 null 只表示本人。"""
    item = request.items[index]
    issues = _app_issues(request, index, _purpose_names(names, AppPurpose.SHOPPING))
    offered = _purpose_names(names, AppPurpose.PAYMENT)
    if item.app is not None and item.app in names.apps:
        offered = _bound_names(names, names.apps[item.app]["payment_app_ids"])
    if item.app is not None and item.pay_app is None:
        issues.append(_name_issue(request, index, "pay_app", "PAYMENT_APP_REQUIRED", offered))
    elif item.pay_app is not None and item.pay_app not in offered:
        issues.append(_name_issue(request, index, "pay_app", "PAYMENT_APP_NOT_BOUND", offered))
    if item.to is not None and item.to not in names.people:
        refusal = refusal_for_code(RECIPIENT_NOT_FOUND)
        issues.append(RouteIssue(refusal.code, refusal.reason, index, "to", item.to, refusal.reason,
                                 refusal.fix, allowed_values=tuple(names.people)))
    return issues


def _action_issues(request: Any, index: int, names: RoutingNames) -> list[RouteIssue]:
    """@brief 每个登记动作均可按名进入校验，人物只点应用和对方而不点内容。"""
    item = request.items[index]
    action = names.actions.get(item.target)
    if action is None:
        return []
    offered = _bound_names(names, action["app_ids"])
    issues = _app_issues(request, index, offered)
    if action["app_ids"] and item.app is None:
        issues.append(_name_issue(request, index, "app", "APP_REQUIRED", offered))
    if item.pay_app is not None:
        issues.append(_null_issue(index, "pay_app", item.pay_app))
    if action["category"] != ActionCategory.SOCIAL:
        if item.to is not None:
            issues.append(_null_issue(index, "to", item.to))
        return issues
    counterparts = names.contacts if action["app_ids"] else names.present_people
    if item.to is None:
        issues.append(_name_issue(request, index, "to", "COUNTERPART_REQUIRED", tuple(counterparts)))
    elif item.to not in counterparts:
        code = "CONTACT_NOT_FOUND" if action["app_ids"] else "SOCIAL_PARTNER_NOT_FOUND"
        refusal = refusal_for_code(code)
        issues.append(RouteIssue(code, refusal.reason, index, "to", item.to, refusal.reason, refusal.fix,
                                 code, tuple(counterparts)))
    return issues


def _name_issue(request: Any, index: int, field: str, code: str, allowed: tuple) -> RouteIssue:
    """@brief 名称错误的候选只换错误字段，其余五键与回答关联保持提交值。"""
    item = request.items[index]
    written = getattr(item, field)
    items = [{key: getattr(row, key) for key in ("target", "way", "app", "to", "pay_app")}
             for row in request.items]
    candidates = tuple(Candidate("start_activity", {
        "items": [{**row, field: name} if position == index else dict(row) for position, row in enumerate(items)],
        "answering": request.answering})
                       for name in allowed[:MAX_CANDIDATES])
    refusal = refusal_for_code(code)
    return RouteIssue(code, refusal.reason, index, field, written, refusal.reason, refusal.fix, code,
                      allowed, candidates)


def _answering_error(request: Any, names: RoutingNames) -> RouteIssue | None:
    """@brief 回应只能关联实际未回复消息，且至少一项明确给该发件人。"""
    if request.answering is None:
        return None
    message = names.messages.get(request.answering)
    if message is None:
        reason = "answering requires a known unanswered message."
    elif not any(names.people.get(item.to) == message[1] for item in request.items if item.to is not None):
        reason = "An item must address the message sender."
    else:
        return None
    refusal = refusal_for_code(ROUTE_FIELDS_INVALID)
    return RouteIssue(ROUTE_FIELDS_INVALID, reason, None, "answering", request.answering, reason, refusal.fix)
