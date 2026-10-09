"""@brief 长周期活动的分维度统计与自然日块自助法。"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Callable, Mapping, Sequence

import numpy as np
from scipy.spatial.distance import jensenshannon
from scipy.stats import wasserstein_distance

from worldmock.evaluation.adapters import build_days
from worldmock.evaluation.models import DatasetView, DayRecord, EvaluationScenario, JsonObject
from worldmock.evaluation.randomness import checkpoint_random_state
from worldmock.life.randomness import random_stream


def shared_support(left: DatasetView, right: DatasetView) -> JsonObject:
    """@brief 共同观察按真实渠道、对象和字段判断，不按内容语义分类。"""
    channels = _activity_channels(left) & _activity_channels(right)
    public_fields, public_coverage = _shared_public_fields(left, right)
    return {
        "comparable": bool(channels or public_fields),
        "shared_channels": sorted(channels),
        "shared_public_object_types": sorted(public_fields),
        "public_fields_by_object_type": public_fields,
        "public_field_coverage": public_coverage,
        "fields": _shared_event_fields(left, right),
        "coverage": {"left": _support_coverage(left, channels), "right": _support_coverage(right, channels)},
    }


def _shared_public_fields(
    left: DatasetView, right: DatasetView
) -> tuple[JsonObject, JsonObject]:
    """@brief 按对象类型求公开字段交集并保留双方分母。"""
    left_present, left_totals = _public_presence(left)
    right_present, right_totals = _public_presence(right)
    categories = sorted(set(left_totals) | set(right_totals))
    shared: JsonObject = {}
    compared: dict[str, list[str]] = {}
    for action in categories:
        fields = sorted(set(left_present[action]) | set(right_present[action]))
        compared[action] = fields
        common = [field for field in fields if left_present[action][field]
                  and right_present[action][field]]
        if common:
            shared[action] = common
    coverage = {
        "left": _public_coverage(left_present, left_totals, compared),
        "right": _public_coverage(right_present, right_totals, compared),
    }
    return shared, coverage


def _public_presence(view: DatasetView) -> tuple[dict[str, Counter[str]], Counter[str]]:
    """@brief 按真实对象统计公开字段存在数，不把缺失字段推断成已知。"""
    present: dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for item in view.public_evidence:
        totals[item.object_type] += 1
        for field, value in item.field_values().items():
            if value is not None:
                present[item.object_type][field] += 1
    return present, totals


def _public_coverage(
    present: Mapping[str, Counter[str]],
    totals: Mapping[str, int],
    compared: Mapping[str, Sequence[str]],
) -> JsonObject:
    """@brief 返回字段存在数和类别内适用数，不把缺失计为零值。"""
    return {
        action: {
            field: {"present": present[action][field], "eligible": totals.get(action, 0)}
            for field in fields
        }
        for action, fields in compared.items()
    }


def _activity_channels(view: DatasetView) -> set[str]:
    """@brief 返回实际活动使用的非私有共同观察渠道。"""
    return {
        channel
        for activity in view.activities
        for channel in activity.channels
        if not channel.startswith("private_")
    }




def _shared_event_fields(left: DatasetView, right: DatasetView) -> list[str]:
    """@brief 返回两方事件具有相同证据语义的字段。"""
    fields = [
        "source_event_id", "person_id", "occurred_at", "ended_at", "event_family", "channel",
        "activity_id", "action", "source_pointer",
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


def _support_coverage(view: DatasetView, channels: set[str]) -> JsonObject:
    """@brief 分别报告真实观察与缺少共同渠道的分母。"""
    eligible = [item for item in view.activities if set(item.channels) & channels]
    visible = [item for item in view.events if not item.channel.startswith("private_")]
    return {
        "total_events": len(view.events), "visible_events": len(visible),
        "shared_channel_events": sum(item.channel in channels for item in visible),
        "private_events_excluded": len(view.events) - len(visible),
        "total_activities": len(view.activities), "shared_activities": len(eligible),
        "unsupported_activities": len(view.activities) - len(eligible),
    }


def evaluate_metrics(
    left: DatasetView,
    right: DatasetView,
    scenario: EvaluationScenario,
    human_reference: DatasetView | None = None,
) -> JsonObject:
    """@brief 计算规范要求的全部分维度指标。"""
    checkpoint_random_state(right.validation)
    support = shared_support(left, right)
    categories = tuple(sorted({item.action for view in (left, right) for item in view.activities}))
    channels = tuple(support["shared_channels"])
    left_common = _common_view(left, categories, channels, scenario)
    right_common = _common_view(right, categories, channels, scenario)
    blocks = date_blocks(scenario)
    metrics = {
        "week_hour_rhythm": _rhythm_metric(left_common, right_common, categories, scenario),
        "weekday_weekend": _weekday_weekend_metric(left_common, right_common, categories, scenario),
        "activity_volume_duration": _volume_duration_metric(left_common, right_common, categories, scenario),
    }
    return {
        "schema_version": "evaluation-metrics-2",
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
        if item.action in allowed_categories and set(item.channels) & allowed_channels
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
    blocks = date_blocks(scenario)
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
        "confidence_interval": _bootstrap_interval(
            "rhythm", block_values, scenario, checkpoint_random_state(right.validation)),
        "missing_reasons": missing,
    }


def _week_hour_histogram(view: DatasetView, categories: Sequence[str]) -> list[float]:
    """@brief 统计本地星期小时内的活动起点。"""
    allowed = set(categories)
    counts = np.zeros(168, dtype=float)
    for activity in view.activities:
        if activity.action not in allowed:
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
        if activity.action in allowed and local.date() in selected:
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
        "confidence_interval": _bootstrap_interval(
            "weekday_weekend", block_values, scenario, checkpoint_random_state(right.validation)),
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
    for block in date_blocks(scenario):
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
            "daily_count", _block_mean_differences(left_counts, right_counts, scenario), scenario,
            checkpoint_random_state(right.validation)
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
    for block in date_blocks(scenario):
        left_values = _block_durations(left, categories, groups, set(block))
        right_values = _block_durations(right, categories, groups, set(block))
        values.append(_mean_difference(left_values, right_values))
    return _bootstrap_interval("duration", values, scenario, checkpoint_random_state(right.validation))
































def _human_metrics(
    left: DatasetView,
    right: DatasetView,
    human: DatasetView,
    scenario: EvaluationScenario,
) -> JsonObject:
    """@brief 计算候选到匹配真人轨迹的共同距离。"""
    candidate_channels = _activity_channels(left) & _activity_channels(right)
    channels = tuple(sorted(candidate_channels & _activity_channels(human)))
    categories = tuple(sorted({item.action for view in (left, right, human) for item in view.activities}))
    if not categories or not channels:
        return {"status": "not_comparable", "reason": "no shared observation support"}
    left_common = _common_view(left, categories, channels, scenario)
    right_common = _common_view(right, categories, channels, scenario)
    human_common = _common_view(human, categories, channels, scenario)
    return {
        "status": "ok",
        "shared_channels": list(channels),
        "week_hour_rhythm": _human_rhythm(left_common, right_common, human_common, categories),
        "daily_activity_count": _human_daily_count(left_common, right_common, human_common, categories),
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






def _daily_counts(view: DatasetView, categories: Sequence[str]) -> list[float]:
    """@brief 统计共同类别活动在每天的起点数。"""
    allowed = set(categories)
    counts = Counter(
        view.window.localize(item.started_at).date()
        for item in view.activities
        if item.action in allowed
    )
    return [float(counts[day.calendar_date]) for day in view.days]


def _shared_duration_groups(
    left: DatasetView, right: DatasetView, categories: Sequence[str]
) -> set[str]:
    """@brief 返回双方共同的持续时间语义。"""
    allowed = set(categories)
    left_groups = {_duration_group(item.duration_kind) for item in left.activities if item.action in allowed}
    right_groups = {_duration_group(item.duration_kind) for item in right.activities if item.action in allowed}
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
        if item.action in allowed and _duration_group(item.duration_kind) in groups
    ]


def _block_durations(
    view: DatasetView, categories: Sequence[str], groups: set[str], selected: set[date]
) -> list[float]:
    """@brief 返回指定日期块内的可比活动时长。"""
    allowed = set(categories)
    return [
        item.duration_seconds
        for item in view.activities
        if item.action in allowed
        and _duration_group(item.duration_kind) in groups
        and view.window.localize(item.started_at).date() in selected
    ]


















def _jensen_shannon(left: Sequence[float], right: Sequence[float]) -> float | None:
    """@brief 以二为底计算零到一的 Jensen-Shannon distance。"""
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    if float(left_array.sum()) == 0 or float(right_array.sum()) == 0:
        return None
    return float(jensenshannon(left_array, right_array, base=2))


def date_blocks(scenario: EvaluationScenario) -> list[tuple[date, ...]]:
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


def _bootstrap_interval(name: str, values: Sequence[float | None],
                        scenario: EvaluationScenario, random_state: str) -> JsonObject:
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
    seed = random_stream(random_state, f"bootstrap-metric:{name}").getrandbits(64)
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
