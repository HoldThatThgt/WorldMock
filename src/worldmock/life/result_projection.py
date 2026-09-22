"""@brief 将完整结算证据投影为人物下一次决策可见的公开结果。"""

from __future__ import annotations

import json
from typing import Any


#: 共同公开结果字段；结算身份用于关联已经发生的事实。
ACTION_RESULT_FIELDS = ("action_id", "occurred_at", "status", "error_code", "message")
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

#: 订单公开实际选定供给、报价和履约证据，不暴露账户余额或内部流水。
ORDER_FIELDS = (
    "order_id", "product_id", "supply_id", "location_id", "seller_account_id", "quantity",
    "price_version", "destination_id", "fulfillment", "created_at", "expected_at", "status",
    "total_price_fen", "route_id", "source",
)


def public_action_result(row: dict[str, Any]) -> dict[str, Any]:
    """@brief 严格选择共同字段与可选工作评审，不复制原始结算数据树。

    @param row 事件存储返回的完整动作结果证据行
    @return 可进入事实清单的公开结果
    """
    result = {key: row[key] for key in ACTION_RESULT_FIELDS}
    data = row["result"]["data"]
    if "work_review" in data:
        result["work_review"] = _public_review(data["work_review"])
    if "orders" in data:
        result["orders"] = [{key: order[key] for key in ORDER_FIELDS if key in order}
                            for order in data["orders"]]
    if "browse_result" in data:
        result["browse_result"] = _public_browse(data["browse_result"])
    if "activity" in data and "data" in data["activity"] and "arrival" in data["activity"]["data"]:
        result["arrival"] = _public_arrival(data["activity"]["data"]["arrival"])
    return result


def _public_arrival(arrival: dict[str, Any]) -> dict[str, Any]:
    """@brief 到达无论访问成功或拒绝均保留实际位置、资格原因及紧凑来源。"""
    result = {key: arrival[key] for key in ("location_id", "access_status", "missing_qualifications")}
    result["source"] = _source_reference(arrival["source"])
    return result


def _public_browse(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 有时长浏览完成的公开结果，保留实际供给购买凭据。"""
    result = {key: data[key] for key in ("action", "category", "returned", "total", "next_cursor", "note")
              if key in data}
    result["items"] = [public_supply_row(item) if "supply_id" in item else dict(item) for item in data["items"]]
    if "excluded" in data:
        result["excluded"] = _group_exclusions(data["excluded"])
    return result


def _group_exclusions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 按全部非身份属性合并排除行，逐项身份及其排除事实仍能无损还原。"""
    groups = {}
    for row in rows:
        facts = {key: value for key, value in row.items() if key != "object_id"}
        key = json.dumps(facts, sort_keys=True, separators=(",", ":"))
        if key not in groups:
            groups[key] = {**facts, "object_ids": []}
        groups[key]["object_ids"].append(row["object_id"])
    return list(groups.values())


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
