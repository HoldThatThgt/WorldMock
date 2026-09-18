"""@brief 长周期活动的分维度统计与自然日块自助法。"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Callable, Mapping, Sequence

import numpy as np
from scipy.spatial.distance import jensenshannon
from scipy.stats import wasserstein_distance

from worldmock.evaluation.adapters import build_days
from worldmock.evaluation.models import DatasetView, DayRecord, EvaluationScenario, JsonObject


def shared_support(left: DatasetView, right: DatasetView) -> JsonObject:
    """@brief 返回双方实际共同支持的字段、渠道和活动类别。"""
    left_channels = _activity_channels(left)
    right_channels = _activity_channels(right)
    channels = left_channels & right_channels
    left_categories = _supported_categories(left, channels)
    right_categories = _supported_categories(right, channels)
    categories = left_categories & right_categories
    fields = _shared_event_fields(left, right)
    public_fields, public_coverage = _shared_public_fields(left, right)
    return {
        "comparable": bool((categories and channels) or public_fields),
        "shared_categories": sorted(categories),
        "shared_channels": sorted(channels),
        "shared_public_categories": sorted(public_fields),
        "public_fields_by_category": public_fields,
        "public_field_coverage": public_coverage,
        "fields": fields,
        "coverage": {
            "left": _support_coverage(left, categories, channels),
            "right": _support_coverage(right, categories, channels),
        },
    }


def _shared_public_fields(
    left: DatasetView, right: DatasetView
) -> tuple[JsonObject, JsonObject]:
    """@brief 按活动类别求公开证据字段交集并保留双方分母。"""
    left_present, left_totals = _public_presence(left)
    right_present, right_totals = _public_presence(right)
    categories = sorted(set(left_totals) | set(right_totals))
    shared: JsonObject = {}
    compared: dict[str, list[str]] = {}
    for category in categories:
        fields = sorted(set(left_present[category]) | set(right_present[category]))
        compared[category] = fields
        common = [field for field in fields if left_present[category][field]
                  and right_present[category][field]]
        if common:
            shared[category] = common
    coverage = {
        "left": _public_coverage(left_present, left_totals, compared),
        "right": _public_coverage(right_present, right_totals, compared),
    }
    return shared, coverage


def _public_presence(
    view: DatasetView,
) -> tuple[dict[str, Counter[str]], Counter[str]]:
    """@brief 统计每个类别各公开字段的实际存在数。"""
    present: dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for item in view.public_evidence:
        totals[item.category] += 1
        for field, value in item.field_values().items():
            if value is not None:
                present[item.category][field] += 1
    return present, totals


def _public_coverage(
    present: Mapping[str, Counter[str]],
    totals: Mapping[str, int],
    compared: Mapping[str, Sequence[str]],
) -> JsonObject:
    """@brief 返回字段存在数和类别内适用数，不把缺失计为零值。"""
    return {
        category: {
            field: {"present": present[category][field], "eligible": totals.get(category, 0)}
            for field in fields
        }
        for category, fields in compared.items()
    }


def _activity_channels(view: DatasetView) -> set[str]:
    """@brief 返回实际活动使用的非私有共同观察渠道。"""
    return {
        channel
        for activity in view.activities
        for channel in activity.channels
        if not channel.startswith("private_")
    }


def _supported_categories(view: DatasetView, channels: set[str]) -> set[str]:
    """@brief 返回指定共同渠道内的已知活动类别。"""
    return {
        activity.category
        for activity in view.activities
        if activity.category != "unknown" and set(activity.channels) & channels
    }


def _shared_event_fields(left: DatasetView, right: DatasetView) -> list[str]:
    """@brief 返回两方事件具有相同证据语义的字段。"""
    fields = [
        "source_event_id", "person_id", "occurred_at", "ended_at", "event_family", "channel",
        "activity_id", "category", "source_pointer",
    ]
    optional = {
        "action_id": lambda item: item.action_id,
        "decision_id": lambda item: item.decision_id,
        "caused_by": lambda item: item.caused_by,
        "resource_changes": lambda item: item.resource_changes,
        "state_version_before": lambda item: item.state_version_before,
        "state_version_after": lambda item: item.state_version_after,
    }
    for name, accessor in optional.items():
        if any(accessor(item) for item in left.events) and any(accessor(item) for item in right.events):
            fields.append(name)
    return fields


def _support_coverage(view: DatasetView, categories: set[str], channels: set[str]) -> JsonObject:
    """@brief 统计共同支持、未知类别与缺失渠道的分母。"""
    activities = list(view.activities)
    eligible = [item for item in activities if item.category in categories and set(item.channels) & channels]
    visible_events = [item for item in view.events if not item.channel.startswith("private_")]
    matched_events = [item for item in visible_events if item.channel in channels]
    return {
        "total_events": len(view.events),
        "visible_events": len(visible_events),
        "shared_channel_events": len(matched_events),
        "private_events_excluded": len(view.events) - len(visible_events),
        "total_activities": len(activities),
        "shared_activities": len(eligible),
        "unknown_activities": sum(item.category == "unknown" for item in activities),
        "unsupported_activities": len(activities) - len(eligible),
    }


def evaluate_metrics(
    left: DatasetView,
    right: DatasetView,
    scenario: EvaluationScenario,
    human_reference: DatasetView | None = None,
) -> JsonObject:
    """@brief 计算规范要求的全部分维度指标。"""
    support = shared_support(left, right)
    categories = tuple(support["shared_categories"])
    channels = tuple(support["shared_channels"])
    left_common = _common_view(left, categories, channels, scenario)
    right_common = _common_view(right, categories, channels, scenario)
    blocks = _date_blocks(scenario)
    metrics = {
        "week_hour_rhythm": _rhythm_metric(left_common, right_common, categories, scenario),
        "weekday_weekend": _weekday_weekend_metric(left_common, right_common, categories, scenario),
        "activity_volume_duration": _volume_duration_metric(left_common, right_common, categories, scenario),
        "burstiness_memory": _temporal_metric(left_common, right_common, categories, scenario),
        "category_diversity_transition": _diversity_metric(left_common, right_common, categories, scenario),
        "ten_minute_day_distance": _day_distance_metric(left_common, right_common, categories, scenario),
    }
    return {
        "schema_version": "evaluation-metrics-1",
        "support": support,
        "eligible_days": len(scenario.window.days),
        "eligible_blocks": len(blocks),
        "block_days": scenario.block_days,
        "bootstrap_repetitions": scenario.bootstrap_repetitions,
        "metrics": metrics,
        "human_reference": (
            _human_metrics(left_common, right_common, human_reference, scenario)
            if human_reference
            else None
        ),
    }


def _common_view(
    view: DatasetView,
    categories: Sequence[str],
    channels: Sequence[str],
    scenario: EvaluationScenario,
) -> DatasetView:
    """@brief 根据共同支持掩码建立统计视图。"""
    allowed_categories = set(categories)
    allowed_channels = set(channels)
    activities = tuple(
        item
        for item in view.activities
        if item.category in allowed_categories and set(item.channels) & allowed_channels
    )
    events = tuple(item for item in view.events if item.channel in allowed_channels)
    days = tuple(build_days(events, activities, scenario))
    return DatasetView(
        view.source_kind, view.dataset_id, view.run_id, view.window, events, activities,
        days, view.artifacts, view.validation, view.public_evidence,
    )


def _rhythm_metric(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算星期小时节律和块区间。"""
    left_hist = _week_hour_histogram(left, categories)
    right_hist = _week_hour_histogram(right, categories)
    blocks = _date_blocks(scenario)
    block_values = [
        _rhythm_block_distance(left, right, categories, block)
        for block in blocks
    ]
    missing = [] if len(scenario.window.days) >= 7 else ["incomplete_calendar_week"]
    distance = _jensen_shannon(left_hist, right_hist) if not missing else None
    return {
        "status": "ok" if distance is not None else "not_applicable",
        "left_distribution": left_hist,
        "right_distribution": right_hist,
        "jensen_shannon_distance": distance,
        "confidence_interval": _bootstrap_interval("rhythm", block_values, scenario),
        "missing_reasons": missing,
    }


def _week_hour_histogram(view: DatasetView, categories: Sequence[str]) -> list[float]:
    """@brief 统计本地星期小时内的活动起点。"""
    allowed = set(categories)
    counts = np.zeros(168, dtype=float)
    for activity in view.activities:
        if activity.category not in allowed:
            continue
        local = view.window.localize(activity.started_at)
        counts[local.weekday() * 24 + local.hour] += 1
    total = float(counts.sum())
    return (counts / total).tolist() if total else [0.0] * 168


def _rhythm_block_distance(
    left: DatasetView,
    right: DatasetView,
    categories: Sequence[str],
    block: tuple[date, ...],
) -> float | None:
    """@brief 计算一个连续自然日块的节律距离。"""
    selected = set(block)
    left_hist = _filtered_hour_histogram(left, categories, selected)
    right_hist = _filtered_hour_histogram(right, categories, selected)
    return _jensen_shannon(left_hist, right_hist)


def _filtered_hour_histogram(
    view: DatasetView, categories: Sequence[str], selected: set[date]
) -> list[float]:
    """@brief 统计指定日期块的星期小时分布。"""
    allowed = set(categories)
    counts = np.zeros(168, dtype=float)
    for activity in view.activities:
        local = view.window.localize(activity.started_at)
        if activity.category in allowed and local.date() in selected:
            counts[local.weekday() * 24 + local.hour] += 1
    total = float(counts.sum())
    return (counts / total).tolist() if total else [0.0] * 168


def _weekday_weekend_metric(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算普通工作日与周末的活动量差。"""
    left_counts = _daily_counts(left, categories)
    right_counts = _daily_counts(right, categories)
    left_contrast = _day_type_contrast(left.days, left_counts)
    right_contrast = _day_type_contrast(right.days, right_counts)
    block_values = _block_contrast_differences(left, right, categories, scenario)
    return {
        "status": "ok" if left_contrast is not None and right_contrast is not None else "not_applicable",
        "left": left_contrast,
        "right": right_contrast,
        "difference": _subtract(left_contrast, right_contrast),
        "confidence_interval": _bootstrap_interval("weekday_weekend", block_values, scenario),
        "missing_reasons": [] if block_values else ["missing_ordinary_day_type"],
    }


def _day_type_contrast(days: Sequence[DayRecord], values: Sequence[float]) -> float | None:
    """@brief 返回普通工作日均值减普通周末均值。"""
    weekdays = [value for day, value in zip(days, values) if day.calendar_class == "regular_workday"]
    weekends = [value for day, value in zip(days, values) if day.calendar_class == "ordinary_weekend"]
    if not weekdays or not weekends:
        return None
    return float(np.mean(weekdays) - np.mean(weekends))


def _block_contrast_differences(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> list[float | None]:
    """@brief 计算每个块的工作日周末差之差。"""
    left_map = dict(zip((day.calendar_date for day in left.days), _daily_counts(left, categories)))
    right_map = dict(zip((day.calendar_date for day in right.days), _daily_counts(right, categories)))
    classes = {day.calendar_date: day.calendar_class for day in left.days}
    values: list[float | None] = []
    for block in _date_blocks(scenario):
        left_value = _block_contrast(block, left_map, classes)
        right_value = _block_contrast(block, right_map, classes)
        values.append(_subtract(left_value, right_value))
    return values


def _block_contrast(
    block: Sequence[date], values: Mapping[date, float], classes: Mapping[date, str]
) -> float | None:
    """@brief 计算一个块内的普通工作日周末差。"""
    weekdays = [values[value] for value in block if classes[value] == "regular_workday"]
    weekends = [values[value] for value in block if classes[value] == "ordinary_weekend"]
    if not weekdays or not weekends:
        return None
    return float(np.mean(weekdays) - np.mean(weekends))


def _volume_duration_metric(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算活动量和可比时长分布。"""
    left_counts = _daily_counts(left, categories)
    right_counts = _daily_counts(right, categories)
    groups = _shared_duration_groups(left, right, categories)
    left_durations = _durations(left, categories, groups)
    right_durations = _durations(right, categories, groups)
    return {
        "status": "ok",
        "daily_count": _distribution_comparison(left_counts, right_counts),
        "duration_seconds": _duration_comparison(left_durations, right_durations, groups),
        "daily_count_confidence_interval": _bootstrap_interval(
            "daily_count", _block_mean_differences(left_counts, right_counts, scenario), scenario
        ),
        "duration_confidence_interval": _duration_block_interval(left, right, categories, groups, scenario),
    }


def _distribution_comparison(left: Sequence[float], right: Sequence[float]) -> JsonObject:
    """@brief 汇总两个数值分布并计算一阶 Wasserstein 距离。"""
    return {
        "left": _summary(left),
        "right": _summary(right),
        "wasserstein_distance": float(wasserstein_distance(left, right)) if left and right else None,
    }


def _duration_comparison(left: list[float], right: list[float], groups: set[str]) -> JsonObject:
    """@brief 仅在相同持续时间语义上比较时长。"""
    return {
        "status": "ok" if left and right else "not_applicable",
        "shared_duration_groups": sorted(groups),
        "left": _summary(left),
        "right": _summary(right),
        "wasserstein_distance": float(wasserstein_distance(left, right)) if left and right else None,
        "missing_reasons": [] if left and right else ["no_shared_duration_semantics"],
    }


def _duration_block_interval(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    groups: set[str],
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算块平均时长差区间。"""
    values: list[float | None] = []
    for block in _date_blocks(scenario):
        left_values = _block_durations(left, categories, groups, set(block))
        right_values = _block_durations(right, categories, groups, set(block))
        values.append(_mean_difference(left_values, right_values))
    return _bootstrap_interval("duration", values, scenario)


def _temporal_metric(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算每个活动类别的有限样本爆发性和记忆性。"""
    by_category: dict[str, JsonObject] = {}
    for category in categories:
        by_category[category] = _temporal_category(left, right, category, scenario)
    applicable = sum(item["status"] == "ok" for item in by_category.values())
    return {
        "status": "ok" if applicable else "not_applicable",
        "applicable_categories": applicable,
        "by_category": by_category,
    }


def _temporal_category(
    left: DatasetView,
    right: DatasetView,
    category: str,
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算单个类别的时间组织指标。"""
    left_times = _activity_times(left, category)
    right_times = _activity_times(right, category)
    left_burst = finite_sample_burstiness(left_times)
    right_burst = finite_sample_burstiness(right_times)
    left_memory = interval_memory(left_times)
    right_memory = interval_memory(right_times)
    blocks = _date_blocks(scenario)
    burst_values = _temporal_block_differences(left, right, category, blocks, finite_sample_burstiness)
    memory_values = _temporal_block_differences(left, right, category, blocks, interval_memory)
    return {
        "status": "ok" if left_burst is not None and right_burst is not None else "not_applicable",
        "burstiness": _paired_value(left_burst, right_burst),
        "memory": _paired_value(left_memory, right_memory),
        "burstiness_confidence_interval": _bootstrap_interval(f"burst:{category}", burst_values, scenario),
        "memory_confidence_interval": _bootstrap_interval(f"memory:{category}", memory_values, scenario),
    }


def finite_sample_burstiness(times: Sequence[datetime]) -> float | None:
    """@brief 按 Kim 与 Jo 的有限样本公式计算爆发性。"""
    intervals = _intervals(times)
    if len(intervals) < 2 or float(np.mean(intervals)) <= 0:
        return None
    count = len(intervals)
    ratio = float(np.std(intervals, ddof=0) / np.mean(intervals))
    root_plus = math.sqrt(count + 1)
    root_minus = math.sqrt(count - 1)
    denominator = (root_plus - 2) * ratio + root_minus
    return float((root_plus * ratio - root_minus) / denominator)


def interval_memory(times: Sequence[datetime]) -> float | None:
    """@brief 计算相邻事件间隔的 Pearson 相关。"""
    intervals = _intervals(times)
    if len(intervals) < 3:
        return None
    previous = np.asarray(intervals[:-1], dtype=float)
    following = np.asarray(intervals[1:], dtype=float)
    if float(np.std(previous)) == 0 or float(np.std(following)) == 0:
        return None
    return float(np.corrcoef(previous, following)[0, 1])


def _intervals(times: Sequence[datetime]) -> list[float]:
    """@brief 返回排序后的相邻非负秒间隔。"""
    ordered = sorted(item.timestamp() for item in times)
    return [following - previous for previous, following in zip(ordered, ordered[1:])]


def _temporal_block_differences(
    left: DatasetView,
    right: DatasetView,
    category: str,
    blocks: Sequence[tuple[date, ...]],
    metric: Callable[[Sequence[datetime]], float | None],
) -> list[float | None]:
    """@brief 计算时间组织指标的逐块差。"""
    values: list[float | None] = []
    for block in blocks:
        selected = set(block)
        left_value = metric(_block_activity_times(left, category, selected))
        right_value = metric(_block_activity_times(right, category, selected))
        values.append(_subtract(left_value, right_value))
    return values


def _diversity_metric(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算类别熵、有效类别数和转换熵。"""
    left_categories = _ordered_categories(left, categories)
    right_categories = _ordered_categories(right, categories)
    left_entropy = _entropy(Counter(left_categories))
    right_entropy = _entropy(Counter(right_categories))
    left_transitions = _transition_counter(left_categories)
    right_transitions = _transition_counter(right_categories)
    entropy_blocks = _entropy_block_differences(left, right, categories, scenario)
    return {
        "status": "ok" if left_categories and right_categories else "not_applicable",
        "category_entropy": _paired_value(left_entropy, right_entropy),
        "effective_categories": _paired_value(_effective(left_entropy), _effective(right_entropy)),
        "category_jensen_shannon_distance": _counter_distance(Counter(left_categories), Counter(right_categories)),
        "transition_entropy": _paired_value(_entropy(left_transitions), _entropy(right_transitions)),
        "transition_jensen_shannon_distance": _counter_distance(left_transitions, right_transitions),
        "confidence_interval": _bootstrap_interval("category_entropy", entropy_blocks, scenario),
    }


def _entropy_block_differences(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> list[float | None]:
    """@brief 计算逐块类别熵差。"""
    values: list[float | None] = []
    for block in _date_blocks(scenario):
        selected = set(block)
        left_values = _block_categories(left, categories, selected)
        right_values = _block_categories(right, categories, selected)
        values.append(_subtract(_entropy(Counter(left_values)), _entropy(Counter(right_values))))
    return values


def _day_distance_metric(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算十分钟向量的日间距离和跨周漂移。"""
    left_within = _within_type_hamming(left.days, categories)
    right_within = _within_type_hamming(right.days, categories)
    left_drift = _cross_week_hamming(left.days, categories)
    right_drift = _cross_week_hamming(right.days, categories)
    drift_values = [left_value - right_value for left_value, right_value in zip(left_drift, right_drift)]
    within_values = _within_block_differences(left, right, categories, scenario)
    return {
        "status": "ok" if left_within is not None and right_within is not None else "not_applicable",
        "within_day_type_hamming": _paired_value(left_within, right_within),
        "cross_week_drift": _paired_value(_mean(left_drift), _mean(right_drift)),
        "drift_pairs": min(len(left_drift), len(right_drift)),
        "within_day_type_confidence_interval": _bootstrap_interval(
            "within_day_type", within_values, scenario
        ),
        "confidence_interval": _bootstrap_interval("cross_week_drift", drift_values, scenario),
    }


def _within_block_differences(
    left: DatasetView,
    right: DatasetView,
    categories: tuple[str, ...],
    scenario: EvaluationScenario,
) -> list[float | None]:
    """@brief 以周块为单位计算同日型日间距离差。"""
    values: list[float | None] = []
    for block in _date_blocks(scenario):
        selected = set(block)
        left_days = [item for item in left.days if item.calendar_date in selected]
        right_days = [item for item in right.days if item.calendar_date in selected]
        left_value = _within_type_hamming(left_days, categories)
        right_value = _within_type_hamming(right_days, categories)
        values.append(_subtract(left_value, right_value))
    return values


def _within_type_hamming(days: Sequence[DayRecord], categories: Sequence[str]) -> float | None:
    """@brief 计算同类普通自然日之间的平均 Hamming distance。"""
    allowed = set(categories)
    distances: list[float] = []
    for day_type in ("regular_workday", "ordinary_weekend"):
        vectors = [_masked_slots(day.slot_categories, allowed) for day in days if day.calendar_class == day_type]
        distances.extend(_pairwise_hamming(vectors))
    return _mean(distances)


def _cross_week_hamming(days: Sequence[DayRecord], categories: Sequence[str]) -> list[float]:
    """@brief 比较连续周的相同星期。"""
    allowed = set(categories)
    ordered = sorted(days, key=lambda item: item.calendar_date)
    if not ordered:
        return []
    first = ordered[0].calendar_date
    weeks: dict[int, dict[int, DayRecord]] = {}
    for day in ordered:
        week_index = (day.calendar_date - first).days // 7
        weeks.setdefault(week_index, {})[day.calendar_date.weekday()] = day
    distances: list[float] = []
    for previous_index, following_index in zip(sorted(weeks), sorted(weeks)[1:]):
        previous = weeks[previous_index]
        following = weeks[following_index]
        shared = sorted(set(previous) & set(following))
        values = [
            _hamming(
                _masked_slots(previous[index].slot_categories, allowed),
                _masked_slots(following[index].slot_categories, allowed),
            )
            for index in shared
        ]
        if values:
            distances.append(float(np.mean(values)))
    return distances


def _pairwise_hamming(vectors: Sequence[tuple[str, ...]]) -> list[float]:
    """@brief 返回所有无序向量对的 Hamming distance。"""
    return [
        _hamming(vectors[left], vectors[right])
        for left in range(len(vectors))
        for right in range(left + 1, len(vectors))
    ]


def _masked_slots(values: Sequence[str], allowed: set[str]) -> tuple[str, ...]:
    """@brief 把非共同类别遮蔽为空闲。"""
    return tuple(value if value in allowed else "none" for value in values)


def _hamming(left: Sequence[str], right: Sequence[str]) -> float:
    """@brief 计算等长类别向量的归一化 Hamming distance。"""
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a != b for a, b in zip(left, right)) / len(left)


def _human_metrics(
    left: DatasetView,
    right: DatasetView,
    human: DatasetView,
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算候选到匹配真人轨迹的共同距离。"""
    candidate_channels = _activity_channels(left) & _activity_channels(right)
    channels = tuple(sorted(candidate_channels & _activity_channels(human)))
    candidate_categories = _supported_categories(left, set(channels))
    candidate_categories &= _supported_categories(right, set(channels))
    categories = tuple(sorted(candidate_categories & _supported_categories(human, set(channels))))
    if not categories or not channels:
        return {"status": "not_comparable", "reason": "no shared observation support"}
    left_common = _common_view(left, categories, channels, scenario)
    right_common = _common_view(right, categories, channels, scenario)
    human_common = _common_view(human, categories, channels, scenario)
    return {
        "status": "ok",
        "shared_categories": list(categories),
        "shared_channels": list(channels),
        "week_hour_rhythm": _human_rhythm(left_common, right_common, human_common, categories),
        "daily_activity_count": _human_daily_count(left_common, right_common, human_common, categories),
        "category_distribution": _human_category_distance(left_common, right_common, human_common, categories),
        "same_date_hamming": _human_day_distance(left_common, right_common, human_common, categories),
    }


def _human_rhythm(
    left: DatasetView, right: DatasetView, human: DatasetView, categories: tuple[str, ...]
) -> JsonObject:
    """@brief 比较星期小时节律到真人轨迹的距离。"""
    reference = _week_hour_histogram(human, categories)
    return {
        "left_distance": _jensen_shannon(_week_hour_histogram(left, categories), reference),
        "right_distance": _jensen_shannon(_week_hour_histogram(right, categories), reference),
    }


def _human_daily_count(
    left: DatasetView, right: DatasetView, human: DatasetView, categories: tuple[str, ...]
) -> JsonObject:
    """@brief 比较每日活动数到真人轨迹的距离。"""
    reference = _daily_counts(human, categories)
    return {
        "left_distance": float(wasserstein_distance(_daily_counts(left, categories), reference)),
        "right_distance": float(wasserstein_distance(_daily_counts(right, categories), reference)),
    }


def _human_category_distance(
    left: DatasetView, right: DatasetView, human: DatasetView, categories: tuple[str, ...]
) -> JsonObject:
    """@brief 比较活动类别分布到真人轨迹的距离。"""
    reference = Counter(_ordered_categories(human, categories))
    return {
        "left_distance": _counter_distance(Counter(_ordered_categories(left, categories)), reference),
        "right_distance": _counter_distance(Counter(_ordered_categories(right, categories)), reference),
    }


def _human_day_distance(
    left: DatasetView, right: DatasetView, human: DatasetView, categories: tuple[str, ...]
) -> JsonObject:
    """@brief 比较同日期十分钟向量到真人轨迹的距离。"""
    allowed = set(categories)
    reference = {day.calendar_date: _masked_slots(day.slot_categories, allowed) for day in human.days}
    left_values = [
        _hamming(_masked_slots(day.slot_categories, allowed), reference[day.calendar_date])
        for day in left.days
    ]
    right_values = [
        _hamming(_masked_slots(day.slot_categories, allowed), reference[day.calendar_date]) for day in right.days
    ]
    return {"left_distance": _mean(left_values), "right_distance": _mean(right_values)}


def _daily_counts(view: DatasetView, categories: Sequence[str]) -> list[float]:
    """@brief 统计共同类别活动在每天的起点数。"""
    allowed = set(categories)
    counts = Counter(
        view.window.localize(item.started_at).date()
        for item in view.activities
        if item.category in allowed
    )
    return [float(counts[day.calendar_date]) for day in view.days]


def _shared_duration_groups(
    left: DatasetView, right: DatasetView, categories: Sequence[str]
) -> set[str]:
    """@brief 返回双方共同的持续时间语义。"""
    allowed = set(categories)
    left_groups = {_duration_group(item.duration_kind) for item in left.activities if item.category in allowed}
    right_groups = {_duration_group(item.duration_kind) for item in right.activities if item.category in allowed}
    return left_groups & right_groups


def _duration_group(value: str) -> str:
    """@brief 把冻结的持续时间类型归入可比较语义。"""
    if value in {"observed_digital_session", "world_internet_session"}:
        return "digital_session"
    return value


def _durations(view: DatasetView, categories: Sequence[str], groups: set[str]) -> list[float]:
    """@brief 返回共同类别和持续时间语义的秒数。"""
    allowed = set(categories)
    return [
        item.duration_seconds
        for item in view.activities
        if item.category in allowed and _duration_group(item.duration_kind) in groups
    ]


def _block_durations(
    view: DatasetView, categories: Sequence[str], groups: set[str], selected: set[date]
) -> list[float]:
    """@brief 返回指定日期块内的可比活动时长。"""
    allowed = set(categories)
    return [
        item.duration_seconds
        for item in view.activities
        if item.category in allowed
        and _duration_group(item.duration_kind) in groups
        and view.window.localize(item.started_at).date() in selected
    ]


def _activity_times(view: DatasetView, category: str) -> list[datetime]:
    """@brief 返回一个类别的活动起点。"""
    return [item.started_at for item in view.activities if item.category == category]


def _block_activity_times(view: DatasetView, category: str, selected: set[date]) -> list[datetime]:
    """@brief 返回日期块内一个类别的活动起点。"""
    return [
        item.started_at
        for item in view.activities
        if item.category == category and view.window.localize(item.started_at).date() in selected
    ]


def _ordered_categories(view: DatasetView, categories: Sequence[str]) -> list[str]:
    """@brief 返回按时间排序的共同活动类别。"""
    allowed = set(categories)
    return [item.category for item in view.activities if item.category in allowed]


def _block_categories(view: DatasetView, categories: Sequence[str], selected: set[date]) -> list[str]:
    """@brief 返回日期块内按时间排序的共同类别。"""
    allowed = set(categories)
    return [
        item.category
        for item in view.activities
        if item.category in allowed and view.window.localize(item.started_at).date() in selected
    ]


def _transition_counter(categories: Sequence[str]) -> Counter[str]:
    """@brief 统计有向类别转换。"""
    return Counter(f"{left}->{right}" for left, right in zip(categories, categories[1:]))


def _entropy(counts: Counter[str]) -> float | None:
    """@brief 计算自然对数 Shannon entropy。"""
    total = sum(counts.values())
    if total == 0:
        return None
    probabilities = np.asarray([value / total for value in counts.values()], dtype=float)
    return float(-np.sum(probabilities * np.log(probabilities)))


def _effective(entropy: float | None) -> float | None:
    """@brief 把 Shannon entropy 转为有效类别数。"""
    return math.exp(entropy) if entropy is not None else None


def _counter_distance(left: Counter[str], right: Counter[str]) -> float | None:
    """@brief 计算两个离散计数分布的 Jensen-Shannon distance。"""
    keys = sorted(set(left) | set(right))
    if not keys:
        return None
    return _jensen_shannon([left[key] for key in keys], [right[key] for key in keys])


def _jensen_shannon(left: Sequence[float], right: Sequence[float]) -> float | None:
    """@brief 以二为底计算零到一的 Jensen-Shannon distance。"""
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    if float(left_array.sum()) == 0 or float(right_array.sum()) == 0:
        return None
    return float(jensenshannon(left_array, right_array, base=2))


def _date_blocks(scenario: EvaluationScenario) -> list[tuple[date, ...]]:
    """@brief 把声明窗口分成连续且不丢日的自然日块。"""
    days = scenario.window.days
    size = scenario.block_days
    return [tuple(days[index:index + size]) for index in range(0, len(days), size)]


def _block_mean_differences(
    left: Sequence[float], right: Sequence[float], scenario: EvaluationScenario
) -> list[float | None]:
    """@brief 计算同位置自然日块的均值差。"""
    size = scenario.block_days
    values: list[float | None] = []
    for index in range(0, len(left), size):
        values.append(_mean_difference(left[index:index + size], right[index:index + size]))
    return values


def _bootstrap_interval(name: str, values: Sequence[float | None], scenario: EvaluationScenario) -> JsonObject:
    """@brief 按块有放回抽样均值并返回 percentile interval。"""
    observed = np.asarray([value for value in values if value is not None], dtype=float)
    if len(observed) < 2:
        return {
            "status": "insufficient_blocks",
            "eligible_blocks": int(len(observed)),
            "lower": None,
            "upper": None,
            "repetitions": 0,
        }
    seed = _metric_seed(scenario.bootstrap_seed, name)
    generator = np.random.default_rng(seed)
    indices = generator.integers(0, len(observed), size=(scenario.bootstrap_repetitions, len(observed)))
    means = observed[indices].mean(axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return {
        "status": "ok",
        "eligible_blocks": int(len(observed)),
        "lower": float(lower),
        "upper": float(upper),
        "repetitions": scenario.bootstrap_repetitions,
    }


def _metric_seed(base: int, name: str) -> int:
    """@brief 从场景种子和指标名派生稳定种子。"""
    digest = hashlib.sha256(f"{base}:{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _summary(values: Sequence[float]) -> JsonObject:
    """@brief 返回可复核的分布摘要。"""
    if not values:
        return {"count": 0, "mean": None, "median": None, "q25": None, "q75": None}
    array = np.asarray(values, dtype=float)
    return {
        "count": len(values),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "q25": float(np.quantile(array, 0.25)),
        "q75": float(np.quantile(array, 0.75)),
    }


def _paired_value(left: float | None, right: float | None) -> JsonObject:
    """@brief 返回左右值和有定义时的差。"""
    return {"left": left, "right": right, "difference": _subtract(left, right)}


def _subtract(left: float | None, right: float | None) -> float | None:
    """@brief 只在两值都有定义时相减。"""
    return float(left - right) if left is not None and right is not None else None


def _mean(values: Sequence[float]) -> float | None:
    """@brief 返回非空数列均值。"""
    return float(np.mean(values)) if values else None


def _mean_difference(left: Sequence[float], right: Sequence[float]) -> float | None:
    """@brief 返回两个非空数列的均值差。"""
    if not left or not right:
        return None
    return float(np.mean(left) - np.mean(right))
