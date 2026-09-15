"""@brief 依据分维度证据形成不使用任意总分的结论状态。"""

from __future__ import annotations

from typing import Any, Mapping

from worldmock.evaluation.models import JsonObject


def decide_result(
    support: Mapping[str, Any],
    causality: Mapping[str, Any],
    metrics: Mapping[str, Any],
    official: Mapping[str, Any],
    reviews: Mapping[str, Any],
) -> JsonObject:
    """@brief 汇总硬门禁、外部参照、真人轨迹和评审的方向性证据。"""
    human = metrics.get("human_reference")
    if not support.get("comparable"):
        return _not_comparable(support)
    evidence = {
        "causal_eligibility": _causal_eligibility(causality),
        "official_aggregate": _official_directions(official),
        "blind_review": _review_directions(reviews),
        "human_reference": _human_directions(human),
    }
    base_status = "human_reference_comparison" if human else "comparative_plausibility_only"
    outcome = _outcome(evidence)
    return {
        "status": base_status,
        "outcome": outcome,
        "evidence_by_dimension": evidence,
        "claim_boundary": _claim_boundary(base_status),
    }


def _not_comparable(support: Mapping[str, Any]) -> JsonObject:
    """@brief 返回共同观察范围不足的明确结果。"""
    return {
        "status": "not_comparable",
        "outcome": "no_comparison",
        "evidence_by_dimension": {},
        "missing_support": {
            "shared_channels": support.get("shared_channels", []),
            "shared_categories": support.get("shared_categories", []),
        },
        "claim_boundary": "No candidate comparison is supported by the declared observations.",
    }


def _causal_eligibility(causality: Mapping[str, Any]) -> JsonObject:
    """@brief 因果硬错误只取消对应候选资格，不证明另一方真实。"""
    left_errors = causality["left"]["hard_error_count"]
    right_errors = causality["right"]["hard_error_count"]
    return {
        "left_eligible": left_errors == 0,
        "right_eligible": right_errors == 0,
        "left_hard_errors": left_errors,
        "right_hard_errors": right_errors,
        "automatic_winner": None,
    }


def _official_directions(official: Mapping[str, Any]) -> JsonObject:
    """@brief 保留每个公开边际领域的支配或混合结果。"""
    values = {
        name: details["pair_alignment"]["result"]
        for name, details in official["domains"].items()
        if details["pair_alignment"]["status"] == "ok"
    }
    return {"directions": values, "interpretation": "descriptive_public_margins_only"}


def _review_directions(reviews: Mapping[str, Any]) -> JsonObject:
    """@brief 结合周块区间和 alpha 标记每个盲评维度的方向。"""
    reliability = reviews["reliability"]["by_dimension"]
    results: dict[str, JsonObject] = {}
    for name, summary in reviews["preference"]["by_dimension"].items():
        interval = summary["week_block_confidence_interval"]
        raw_direction = _interval_direction(interval)
        alpha = reliability[name]
        reliable = alpha["status"] == "ok" and alpha["alpha"] >= 0.80
        results[name] = {
            "raw_direction": raw_direction,
            "accepted_direction": raw_direction if reliable else "mixed",
            "reliability_status": "adequate" if reliable else "insufficient",
        }
    return {"directions": results, "reviewer_dependence": reviews["reviewer_dependence"]}


def _interval_direction(interval: Mapping[str, Any]) -> str:
    """@brief 只有周块区间排除零时才给出单向偏好。"""
    if interval.get("status") != "ok":
        return "mixed"
    if interval["lower"] > 0:
        return "left"
    if interval["upper"] < 0:
        return "right"
    return "mixed"


def _human_directions(value: Any) -> JsonObject | None:
    """@brief 对匹配真人轨迹逐指标选择距离更小的一方。"""
    if not isinstance(value, dict) or value.get("status") != "ok":
        return None
    directions: dict[str, str] = {}
    for name, comparison in value.items():
        if not isinstance(comparison, dict) or "left_distance" not in comparison:
            continue
        directions[name] = _smaller_distance(comparison)
    return {"directions": directions}


def _smaller_distance(value: Mapping[str, Any]) -> str:
    """@brief 比较两个候选到同一真人参照的距离。"""
    left = value.get("left_distance")
    right = value.get("right_distance")
    if left is None or right is None or left == right:
        return "mixed"
    return "left" if left < right else "right"


def _outcome(evidence: Mapping[str, Any]) -> str:
    """@brief 只有无硬错误且全部可用方向一致时声明更受支持。"""
    eligibility = evidence["causal_eligibility"]
    if not eligibility["left_eligible"] or not eligibility["right_eligible"]:
        return "mixed"
    directions = _direction_values(evidence)
    if directions and set(directions) == {"left"}:
        return "left_more_supported"
    if directions and set(directions) == {"right"}:
        return "right_more_supported"
    return "mixed"


def _direction_values(evidence: Mapping[str, Any]) -> list[str]:
    """@brief 收集外部边际、可靠盲评和真人参照的非空方向。"""
    values = list(evidence["official_aggregate"]["directions"].values())
    values.extend(
        item["accepted_direction"]
        for item in evidence["blind_review"]["directions"].values()
    )
    human = evidence.get("human_reference")
    if human:
        values.extend(human["directions"].values())
    return [value for value in values if value in {"left", "right", "mixed"}]


def _claim_boundary(status: str) -> str:
    """@brief 返回结果允许使用的明确声明边界。"""
    if status == "human_reference_comparison":
        return "Distances apply only to the supplied matched human observation window and support mask."
    return (
        "The result compares plausibility against public aggregates and blinded reviews; "
        "it is not empirical validation against matched target-population microdata."
    )
