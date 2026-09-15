"""@brief 使用官方中国时间利用聚合参照进行有边界的描述性诊断。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from worldmock.evaluation.models import ActivitySession, DatasetView, EvaluationScenario, JsonObject


def evaluate_official_reference(
    left: DatasetView, right: DatasetView, scenario: EvaluationScenario
) -> JsonObject:
    """@brief 比较候选的参与率和参与者分钟数与公开聚合边际。"""
    domains = scenario.official_reference["domains"]
    results: dict[str, JsonObject] = {}
    for domain, reference in sorted(domains.items()):
        left_value = _domain_diagnostic(left, domain, reference)
        right_value = _domain_diagnostic(right, domain, reference)
        results[domain] = {
            "label_zh": reference["label_zh"],
            "reference": {"urban": reference["urban"], "working_age": reference["working_age"]},
            "left": left_value,
            "right": right_value,
            "pair_alignment": _pair_alignment(left_value, right_value),
        }
    return {
        "schema_version": "official-aggregate-diagnostic-1",
        "reference_source": scenario.official_reference["source"],
        "domains": results,
        "limitations": [
            "Published aggregates are descriptive margins, not matched person-level ground truth.",
            "Fifteen-minute occupancy is reconstructed only for explicitly supported duration semantics.",
            "Missing observation support is reported as unsupported and is never converted to zero activity.",
        ],
    }


def _domain_diagnostic(
    view: DatasetView, domain: str, reference: Mapping[str, Any]
) -> JsonObject:
    """@brief 计算单个候选在一个官方活动领域的诊断值。"""
    supported = set(reference["supported_duration_kinds"])
    activities = [
        item
        for item in view.activities
        if domain in item.official_domains and item.duration_kind in supported
    ]
    if not activities:
        return {"status": "unsupported", "reason": "no_supported_duration_semantics"}
    slots = _occupied_slots(view, activities)
    if not slots:
        return {"status": "unsupported", "reason": "no_observed_duration"}
    people = {item.person_id for item in view.activities}
    denominator = len(people) * len(view.window.days)
    participant_days = {(person, day) for person, day, _ in slots}
    minutes = 15 * len(slots)
    rate = len(participant_days) / denominator if denominator else None
    participant_minutes = minutes / len(participant_days) if participant_days else None
    observed = {
        "participation_rate": rate,
        "participant_minutes": participant_minutes,
        "participant_days": len(participant_days),
        "eligible_person_days": denominator,
        "occupied_fifteen_minute_slots": len(slots),
    }
    return {
        "status": "ok",
        "observed": observed,
        "reference_deviation": _reference_deviation(observed, reference),
    }


def _occupied_slots(
    view: DatasetView, activities: list[ActivitySession]
) -> set[tuple[str, date, int]]:
    """@brief 将实际活动区间量化为本地十五分钟占用格。"""
    occupied: set[tuple[str, date, int]] = set()
    zone = ZoneInfo(view.window.timezone)
    for activity in activities:
        for start, end in _segments(activity):
            for day in view.window.days:
                day_start = datetime.combine(day, time.min, zone)
                for index in _overlapping_slots(start, end, day_start):
                    occupied.add((activity.person_id, day, index))
    return occupied


def _segments(activity: ActivitySession) -> tuple[tuple[datetime, datetime], ...]:
    """@brief 返回会话真实运行区间。"""
    if activity.segments is not None:
        return activity.segments
    return ((activity.started_at, activity.ended_at),)


def _overlapping_slots(start: datetime, end: datetime, day_start: datetime) -> tuple[int, ...]:
    """@brief 返回活动与一个自然日重叠的十五分钟格索引。"""
    day_end = day_start + timedelta(days=1)
    if start >= day_end or end <= day_start:
        return ()
    first = max(0, int((max(start, day_start) - day_start).total_seconds() // 900))
    last_seconds = (min(end, day_end) - day_start).total_seconds()
    last = min(95, max(first, int((last_seconds - 1e-9) // 900)))
    return tuple(range(first, last + 1))


def _reference_deviation(
    observed: Mapping[str, float | int | None], reference: Mapping[str, Any]
) -> JsonObject:
    """@brief 计算城镇与劳动年龄边际的绝对偏差。"""
    result: dict[str, JsonObject] = {}
    for cohort in ("urban", "working_age"):
        target = reference[cohort]
        rate = observed["participation_rate"]
        minutes = observed["participant_minutes"]
        result[cohort] = {
            "participation_rate_absolute": abs(float(rate) - target["participation_rate"]),
            "participant_minutes_absolute": abs(float(minutes) - target["participant_minutes"]),
        }
    return result


def _pair_alignment(left: JsonObject, right: JsonObject) -> JsonObject:
    """@brief 仅在双方同一领域都有支持时判断边际支配关系。"""
    if left["status"] != "ok" or right["status"] != "ok":
        return {"status": "not_applicable", "result": None}
    results = [
        _cohort_alignment(left["reference_deviation"][cohort], right["reference_deviation"][cohort])
        for cohort in ("urban", "working_age")
    ]
    if all(item == "left" for item in results):
        result = "left"
    elif all(item == "right" for item in results):
        result = "right"
    elif all(item == "tie" for item in results):
        result = "tie"
    else:
        result = "mixed"
    return {"status": "ok", "result": result, "cohort_results": results}


def _cohort_alignment(left: Mapping[str, float], right: Mapping[str, float]) -> str:
    """@brief 判断候选是否同时更接近参与率和参与者时长边际。"""
    fields = ("participation_rate_absolute", "participant_minutes_absolute")
    left_better = all(left[field] <= right[field] for field in fields)
    right_better = all(right[field] <= left[field] for field in fields)
    if left_better and any(left[field] < right[field] for field in fields):
        return "left"
    if right_better and any(right[field] < left[field] for field in fields):
        return "right"
    return "tie" if left_better and right_better else "mixed"
