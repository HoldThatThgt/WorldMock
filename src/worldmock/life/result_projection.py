"""@brief 将完整结算证据投影为人物可见的公开结果（进度层 tool_call.result，docs/design/06-agent.md「进度层」）。

公开结果含 occurred_at（带时区 ISO）、status、error_code（恒写，没有时为 null）、message（原文；业务拒绝时就是 reason）
与 details（恒写，没有新事实时为 {}）。details 只放本次新产生的可读事实：购买的商品、份数、
付款、送达地点与预计时刻、收货人与所回应的消息，给予的商品、份数与收件人，计划时长（开始与恢复），到达，浏览得到的
商品的完整标签「<门店>出售的<商品>」，读完的邮件主题与正文（邮件正文只在阅读动作的结果中出现），回看，拒绝的原因、改法与下一步（没有时为 null）
及逐项明细（items，候选为可照填的请求），终局，已关联的订单，名称归一（resolved）与附带说明（notes）；不复述参数（购买
的方式就是请求的 ways[i]，不写），不含内部身份与资源账户。时刻字段（*_at）是带时区的 ISO 8601。
"""

from __future__ import annotations

from typing import Any

from worldmock.life.readable import duration_phrase, iso_time, money_phrase, portion_phrase
from worldmock.life.scenario import RuntimeScenario

#: 原样进入 details 的已公开键：回看由处理器以名称与人读时长写成，拒绝原因、改法、下一步与逐项明细由 life.guidance
#: 生成（docs/design/04-simulation-engine.md「错误合同」），终局、名称归一与附带说明只含可读名称；errands 是控制
#: 工具受理的事务变更回执 [{errand, change}]（只有名称，docs/design/04-simulation-engine.md 「回执」）。
_PASSTHROUGH_DETAILS = ("recall", "reason", "next_step", "fix", "items", "outcome", "linked_orders", "resolved",
                        "notes", "errands", "content")


def public_result(result: dict[str, Any], scenario: RuntimeScenario) -> dict[str, Any]:
    """@brief 一条 action_result 事件正文（ActionResult 的 JSON）的公开结果。

    :param result: action_result 事件的 data（含 status、error_code、message、occurred_at 与领域 data）。
    :param scenario: 场景（商品名、地点名与时区）。
    """
    payload = result.get("data") or {}
    public = {"occurred_at": iso_time(result["occurred_at"], scenario.timezone), "status": result["status"],
              "error_code": result.get("error_code"), "message": result["message"],
              "details": _details(result, scenario)}
    return public


def _details(result: dict[str, Any], scenario: RuntimeScenario) -> dict[str, Any]:
    """@brief details：本次新产生的可读事实，按闭集键逐项给出，没有的键不写。"""
    payload = result.get("data") or {}
    names = {item.goods_id: item.name for item in scenario.goods}
    places = {item.location_id: item.display_name for item in scenario.locations}
    details: dict[str, Any] = {}
    if payload.get("orders"):
        people = _people(scenario)
        details["purchases"] = [{**_purchase(order, names, places, scenario.timezone),
                                 "recipient": people[order["recipient_id"]],
                                 "request": payload.get("answering") if order["request_message_id"] else None}
                                for order in payload["orders"]]
    if result["status"] == "in_progress":
        details["planned"] = [{"name": item.get("name"), "duration": duration_phrase(item["planned_duration_seconds"])}
                              for item in _activity_rows(payload)]
    if payload.get("resumed"):
        details["planned"] = [{"name": item["name"], "duration": duration_phrase(item["planned_duration_seconds"])}
                              for item in payload["resumed"]]
    arrival = (payload.get("activity") or {}).get("data", {}).get("arrival")
    if arrival is not None:
        details["arrival"] = {"place": places[arrival["location_id"]],
                              "access": "进入" if arrival["access_status"] == "allowed" else "没能进去"}
    details.update({key: payload[key] for key in _PASSTHROUGH_DETAILS if key in payload})
    return details


def _activity_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 结果中的活动快照：单项结果的 activity；同一请求的多项活动为 activities（开始时的快照，或全部子活动结束后
    聚合终态中的各活动终态快照）。"""
    return [payload["activity"]] if "activity" in payload else payload.get("activities", [])






def _purchase(order: dict[str, Any], names: dict[str, str], places: dict[str, str],
              timezone_name: str) -> dict[str, Any]:
    """@brief 一个订单的公开事实：商品、份数、付款、送达地点与预计时刻（带时区 ISO）；方式是请求参数，不复述。"""
    return {"product": names[order["goods_id"]], "quantity": portion_phrase(order["quantity"]),
            "paid": money_phrase(order["total_price_fen"]), "destination": places[order["destination_id"]],
            "expected_at": iso_time(order["expected_at"], timezone_name)}


def _people(scenario: RuntimeScenario) -> dict[str, str]:
    """@brief 人物身份 → 可读名称：同世界人物取姓名，场景联系人取称谓。"""
    people = {f"agent:{item.agent_id}": item.name for item in scenario.agents}
    people.update({contact.contact_id: contact.salutation for item in scenario.agents for contact in item.contacts})
    return people
