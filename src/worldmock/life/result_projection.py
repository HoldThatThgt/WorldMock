"""@brief 将完整结算证据投影为人物可见的公开结果（进度层 tool_call.result，docs/dev/SPEC-persona-prompt.md「进度层」）。

公开结果含 occurred_at（带时区 ISO）、status、error_code（恒写，没有时为 null）、message（原文）与 details（恒写，没有新
事实时为 {}），工作结果另带公开 work_review。details 只放本次新产生的可读事实：购买的商品、份数、付款、方式、送达地点与
预计时刻、收货人与所回应的消息，给予的商品、份数与收件人，计划时长（开始与恢复），到达，浏览得到的商品名称，读完的邮件主题与正文（邮件
正文只在阅读动作的结果中出现），回看，拒绝的原因与下一步，近似候选、终局与已关联的订单；不复述参数，不含内部身份与资源
账户。时刻字段（*_at）是带时区的 ISO 8601。
"""

from __future__ import annotations

from typing import Any

from worldmock.life.interaction_menu import PURCHASE_WAYS
from worldmock.life.readable import duration_phrase, iso_time, money_phrase, portion_phrase
from worldmock.life.scenario import RuntimeScenario

#: 工作公开评审的汇总字段，禁止带入隐藏参考、答案或完整结果树。
WORK_REVIEW_SUMMARY_FIELDS = (
    "accepted", "required_fact_total", "required_fact_passed", "required_fact_missing",
    "missing_content_categories",
)
#: 必需事实的公开判断；内部参考身份仅可转换为公开内容品类。
REFERENCE_ASSESSMENT_FIELDS = (
    "content_category", "covered", "source_supported", "contradiction", "source_excerpt",
    "rationale", "passed",
)
#: 实际交付项的公开判断；路径只指人物自己提交的内容。
DELIVERY_ASSESSMENT_FIELDS = (
    "delivery_path", "source_supported", "contradiction", "source_excerpt", "rationale", "passed",
)
#: 原样进入 details 的已公开键：回看由处理器以名称与人读时长写成，拒绝原因与下一步由 life.guidance 生成，
#: 近似候选与终局只含可读名称。
_PASSTHROUGH_DETAILS = ("recall", "reason", "next_step", "candidates", "outcome", "linked_orders")


def public_result(result: dict[str, Any], scenario: RuntimeScenario) -> dict[str, Any]:
    """@brief 一条 action_result 事件正文（ActionResult 的 JSON）的公开结果。

    :param result: action_result 事件的 data（含 status、error_code、message、occurred_at 与领域 data）。
    :param scenario: 场景（商品名、地点名与时区）。
    """
    payload = result.get("data") or {}
    public = {"occurred_at": iso_time(result["occurred_at"], scenario.timezone), "status": result["status"],
              "error_code": result.get("error_code"), "message": result["message"],
              "details": _details(result, scenario)}
    if "work_review" in payload:
        public["work_review"] = _public_review(payload["work_review"])
    return public


def _details(result: dict[str, Any], scenario: RuntimeScenario) -> dict[str, Any]:
    """@brief details：本次新产生的可读事实，按闭集键逐项给出，没有的键不写。"""
    payload = result.get("data") or {}
    names = {item.product_id: item.name for item in (*scenario.products, *scenario.inventory_items)}
    places = {item.location_id: item.name for item in scenario.locations}
    details: dict[str, Any] = {}
    if payload.get("orders"):
        people = _people(scenario)
        details["purchases"] = [{**_purchase(order, names, places, scenario.timezone),
                                 "recipient": people[order["recipient_id"]],
                                 "request": payload.get("answering") if order["request_message_id"] else None}
                                for order in payload["orders"]]
    if payload.get("given"):
        people = _people(scenario)
        details["given"] = [{"product": names[item["product_id"]], "quantity": portion_phrase(item["quantity"]),
                             "recipient": people[item["recipient_id"]]} for item in payload["given"]]
    if result["status"] == "in_progress":
        started = [payload["activity"]] if "activity" in payload else payload.get("activities", [])
        details["planned"] = [{"name": item.get("name"), "duration": duration_phrase(item["planned_duration_seconds"])}
                              for item in started]
    if payload.get("resumed"):
        details["planned"] = [{"name": item["name"], "duration": duration_phrase(item["planned_duration_seconds"])}
                              for item in payload["resumed"]]
    arrival = (payload.get("activity") or {}).get("data", {}).get("arrival")
    if arrival is not None:
        details["arrival"] = {"place": places[arrival["location_id"]],
                              "access": "进入" if arrival["access_status"] == "allowed" else "没能进去"}
    if "browse_result" in payload:
        details["browse_items"] = [names[item["product_id"]] for item in payload["browse_result"].get("items", [])]
    mail = _read_mail(payload)
    if mail is not None:
        details["mail"] = mail
    details.update({key: payload[key] for key in _PASSTHROUGH_DETAILS if key in payload})
    return details


def _read_mail(payload: dict[str, Any]) -> dict[str, str] | None:
    """@brief 读完的邮件 {subject, body}：阅读活动完成时揭示的主题与正文；没有读完的阅读时为 None。"""
    rows = [payload["activity"]] if "activity" in payload else payload.get("activities", [])
    revealed = next((row["data"]["revealed"] for row in rows
                     if row.get("kind") == "read_mail" and "revealed" in (row.get("data") or {})), None)
    return None if revealed is None else {"subject": revealed["subject"], "body": revealed["body"]}


def _purchase(order: dict[str, Any], names: dict[str, str], places: dict[str, str],
              timezone_name: str) -> dict[str, Any]:
    """@brief 一个订单的公开事实：商品、份数、付款、方式、送达地点与预计时刻（带时区 ISO）。"""
    return {"product": names[order["product_id"]], "quantity": portion_phrase(order["quantity"]),
            "paid": money_phrase(order["total_price_fen"]), "way": PURCHASE_WAYS[order["fulfillment"]],
            "destination": places[order["destination_id"]],
            "expected_at": iso_time(order["expected_at"], timezone_name)}


def _people(scenario: RuntimeScenario) -> dict[str, str]:
    """@brief 人物身份 → 可读名称：同世界人物取姓名，场景联系人取称谓。"""
    people = {f"agent:{item.agent_id}": item.name for item in scenario.agents}
    people.update({contact.contact_id: contact.salutation for item in scenario.agents for contact in item.contacts})
    return people


def public_supply_row(row: dict[str, Any]) -> dict[str, Any]:
    """@brief 保留供给比较和购买字段，来源以可回溯引用呈现，完整声明仍在原始证据。"""
    result = {key: value for key, value in row.items() if key not in {"source", "quote", "category"}}
    result["source"] = _source_reference(row["source"])
    if row["quote"] is not None:
        quote = row["quote"]
        result["quote"] = {key: quote[key] for key in ("effective_from", "effective_to")}
        result["quote_source"] = _source_reference(quote["source"])
    else:
        result["quote"] = None
        result["quote_source"] = None
    return result


def _source_reference(source: dict[str, Any]) -> dict[str, Any]:
    """@brief 模型只需区分事实和假设并引用来源，许可全文随运行及导出清单保留。"""
    return {"kind": source["kind"], "reference": source["url"], "fact_cutoff": source["fact_cutoff"]}


def _public_review(review: dict[str, Any]) -> dict[str, Any]:
    """@brief 将工作评审及两种逐项判断逐层限制在既有公开字段集合。"""
    result = {key: review[key] for key in WORK_REVIEW_SUMMARY_FIELDS}
    result["required_fact_assessments"] = [
        {key: item[key] for key in REFERENCE_ASSESSMENT_FIELDS}
        for item in review["required_fact_assessments"]]
    result["delivery_assessments"] = [
        {key: item[key] for key in DELIVERY_ASSESSMENT_FIELDS}
        for item in review["delivery_assessments"]]
    return result
