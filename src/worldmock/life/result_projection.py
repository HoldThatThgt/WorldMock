"""@brief 将完整结算证据投影为人物下一次决策可见的公开结果。"""

from __future__ import annotations

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


def public_action_result(row: dict[str, Any]) -> dict[str, Any]:
    """@brief 严格选择共同字段与可选工作评审，不复制原始结算数据树。

    @param row 事件存储返回的完整动作结果证据行
    @return 可进入事实清单的公开结果
    """
    result = {key: row[key] for key in ACTION_RESULT_FIELDS}
    data = row["result"]["data"]
    if "work_review" in data:
        result["work_review"] = _public_review(data["work_review"])
    return result


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
