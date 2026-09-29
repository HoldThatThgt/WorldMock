"""@brief 从已认证足迹构造确定性的运行后质量与成本报告。"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from worldmock.errors import IntegrityError
from worldmock.evaluation.goal_metrics import day_plan_metrics
from worldmock.footprints.experiences import project_verified_experiences, validate_verified_experiences
from worldmock.footprints.models import (
    DECISION_OFFERED_FIELD,
    WAKE_REASON_NAMES,
    WAKE_REASONS_FIELD,
    Event,
)
from worldmock.profile.injection import INPUT_WARNING_CODES
from worldmock.footprints.timeline_labels import timeline_label_report
from worldmock.life.control_items import control_items
from worldmock.life.reply_guard import GUARD_PATTERNS

_ACTION_STATUSES = (
    "submitted", "accepted", "in_progress", "completed", "rejected", "cancelled", "failed", "unknown",
)
_ACTIVITY_STATUSES = ("active", "paused", "completed", "cancelled", "failed", "truncated")
_INTERRUPTED_STATUSES = {"cancelled", "failed", "truncated"}
_CONTENT_TYPES = {"watch", "read_mail", "play_game", "browse"}
_SUBJECTIVE_TYPES = frozenset({"commitment", "relationship", "preference", "goal", "conflict",
                              "resolution", "identity", "routine", "reflection"})
#: 记忆写入点闭集（memory_updated.data.trigger 的取值与顺序）：结束活动与就寝反思。
_WRITE_POINTS = ("activity_conclusion", "reflection")
#: 协议错误与运行故障文本中表示记忆的词：记忆本身，以及结束工具里记忆与看法的两个字段名（memories、views）。
_MEMORY_ERROR_WORDS = ("memory", "memories", "views")
#: 模型用量按用途分列时，无 purpose 的请求（人物决策的行动请求与记忆巩固、反思）的分组名。
_DECISION_PURPOSE = "decision"
_PRICING = {
    "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "checked_at": "2026-09-15",
    "currency": "USD",
    "unit": "per_million_tokens",
    "model": "deepseek-flash",
    "off_peak": {"cache_hit_input": 0.003, "cache_miss_input": 0.15, "output": 0.6},
    "peak": {"cache_hit_input": 0.006, "cache_miss_input": 0.3, "output": 1.2},
    "peak_utc": "Monday-Friday 01:00-04:00 and 06:00-10:00 UTC",
    "price_timestamp_basis": "Persisted model_request recorded_at",
    "basis": "Frozen public list-price estimate; not a provider invoice",
}


def build_quality_report(events: list[Event], activities: list[dict], manifest: dict) -> dict:
    """@brief 汇总完成、异常、内容、效率、记忆、模型用量、实测加速比与运行输入警告。

    @param events 已经认证且按 event_seq 排序的完整事件。
    @param activities 已经认证的活动终态视图。
    @param manifest 对应运行的冻结清单。
    @return 可规范序列化且不把缺失测量记为零的质量报告。
    """
    _validate_inputs(events, activities, manifest)
    return {
        "schema_version": 3,
        "run_id": manifest["run_id"],
        "source_group_id": manifest.get("source_group_id", manifest["run_id"]),
        "event_range": {
            "first_event_seq": events[0].event_seq,
            "last_event_seq": events[-1].event_seq,
            "event_count": len(events),
            "activity_count": len(activities),
        },
        "run_outcome": _run_outcome(events),
        "actions": _action_quality(events),
        "activities": _activity_quality(activities, events),
        "content_contact": _content_quality(activities),
        "conversion_efficiency": _efficiency_quality(events),
        "decision_rhythm": _decision_rhythm_quality(events),
        "memory_references": _memory_quality(events),
        "model_usage": _model_quality(events),
        "acceleration": _acceleration(events, manifest),
        "day_plan_metrics": day_plan_metrics(
            [{**event.model_dump(mode="json"), "code_sha256": manifest.get("code_sha256")} for event in events],
            manifest["window"]["timezone"],
        ),
        "input_warnings": _input_warning_quality(manifest),
        "timeline_labels": timeline_label_report(events, manifest),
        "contact_reply_guard": _contact_reply_guard_quality(events),
    }


def _validate_inputs(events: list[Event], activities: list[dict], manifest: dict) -> None:
    """@brief 核对报告依赖的身份、顺序和窗口，不重复执行导出认证。"""
    if not events:
        raise IntegrityError("Quality report requires at least one authenticated event")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("run_id"), str):
        raise IntegrityError("Quality report manifest has no run identity")
    window = manifest.get("window")
    if not isinstance(window, dict):
        raise IntegrityError("Quality report manifest has no run window")
    start, end = window.get("start"), window.get("end")
    if not _is_int(start) or not _is_int(end) or end <= start:
        raise IntegrityError("Quality report run window is invalid")
    sequences = [event.event_seq for event in events]
    if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
        raise IntegrityError("Quality report events are not in a strict sequence")
    if any(events[index].occurred_at > events[index + 1].occurred_at for index in range(len(events) - 1)):
        raise IntegrityError("Quality report event time moves backwards")
    identities = [row.get("activity_id") for row in activities if isinstance(row, dict)]
    if len(identities) != len(activities) or any(not isinstance(value, str) or not value for value in identities):
        raise IntegrityError("Quality report activity identity is invalid")
    if len(set(identities)) != len(identities):
        raise IntegrityError("Quality report activity identity is duplicated")


def _run_outcome(events: list[Event]) -> dict:
    """@brief 根据终态事件报告完成、失败或中断，不从时钟猜测成功。"""
    ended = [event.event_id for event in events if event.event_type == "run_ended"]
    failed = [event.event_id for event in events if event.event_type == "run_failed"]
    if failed:
        status = "failed"
    elif ended:
        status = "completed"
    else:
        status = "interrupted"
    return {
        "status": status,
        "run_ended_events": ended,
        "run_failed_events": failed,
        "completion_claim_requires_export_checkpoint": True,
    }


def _action_quality(events: list[Event]) -> dict:
    """@brief 分开统计请求尝试、唯一动作结果和业务拒绝。"""
    requests = [event for event in events if event.event_type == "action_requested"]
    identified = [event.action_id for event in requests if event.action_id]
    statuses: Counter[str] = Counter()
    missing_status = []
    unrecognized: Counter[str] = Counter()
    for event in events:
        if event.event_type != "action_result" or event.action_id is None:
            continue
        status = event.data.get("status")
        if not isinstance(status, str):
            missing_status.append(event.event_id)
        elif status in _ACTION_STATUSES:
            statuses[status] += 1
        else:
            unrecognized[status] += 1
    rejections = [event for event in events if event.event_type == "action_rejected"]
    codes = Counter(event.data["error_code"] for event in rejections
                    if isinstance(event.data.get("error_code"), str) and event.data["error_code"])
    return {
        "request_events": len(requests),
        "unique_action_ids": len(set(identified)),
        "request_events_missing_action_id": len(requests) - len(identified),
        "repeated_request_events": len(identified) - len(set(identified)),
        "reused_request_events": sum(event.data.get("reused") is True for event in requests),
        "result_status_counts": {status: statuses[status] for status in _ACTION_STATUSES},
        "result_events_missing_status": missing_status,
        "unrecognized_result_status_counts": dict(sorted(unrecognized.items())),
        "business_rejection_events": len(rejections),
        "business_rejection_codes": dict(sorted(codes.items())),
        "rejections_missing_error_code": len(rejections) - sum(codes.values()),
    }


def _activity_quality(activities: list[dict], events: list[Event]) -> dict:
    """@brief 按权威活动终态区分完成、终态中断和截止点暂停。"""
    statuses = Counter(str(row.get("status")) for row in activities)
    unrecognized = {key: value for key, value in statuses.items() if key not in _ACTIVITY_STATUSES}
    interrupted = [row for row in activities if row.get("status") in _INTERRUPTED_STATUSES]
    reasons = Counter(row["interruption_reason"] for row in interrupted
                      if isinstance(row.get("interruption_reason"), str) and row["interruption_reason"])
    known = [row["elapsed_seconds"] for row in activities if _is_nonnegative_number(row.get("elapsed_seconds"))]
    shared = _body_settlement_events(events)
    intervals = [(event.data.get("interval_start"), event.data.get("interval_end")) for event in shared]
    valid_intervals = [(start, end) for start, end in intervals
                       if _is_int(start) and _is_int(end) and end > start]
    person_seconds = _person_elapsed_seconds(shared)
    return {
        "status_counts": {status: statuses[status] for status in _ACTIVITY_STATUSES},
        "unrecognized_status_counts": dict(sorted(unrecognized.items())),
        "completed_activities": statuses["completed"],
        "terminal_interruptions": len(interrupted),
        "interruption_status_counts": {
            status: statuses[status] for status in sorted(_INTERRUPTED_STATUSES)
        },
        "interruption_reason_counts": dict(sorted(reasons.items())),
        "interruptions_missing_reason": len(interrupted) - sum(reasons.values()),
        "paused_at_cutoff": statuses["paused"],
        "known_elapsed_seconds": sum(known) if known else (0 if not activities else None),
        "person_elapsed_seconds_by_agent": person_seconds if len(valid_intervals) == len(shared) else None,
        "body_settlements_missing_interval": len(shared) - len(valid_intervals),
        "elapsed_seconds_complete": len(known) == len(activities),
        "activities_missing_elapsed_seconds": len(activities) - len(known),
    }


def _person_elapsed_seconds(events: list[Event]) -> dict[str, int]:
    """@brief 将共同结算按人取区间并集，多个并行活动不重复累计。"""
    by_agent: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for event in events:
        start, end = event.data.get("interval_start"), event.data.get("interval_end")
        if _is_int(start) and _is_int(end) and end > start:
            by_agent[str(event.agent_id)].append((start, end))
    totals = {}
    for agent_id, intervals in by_agent.items():
        total, previous_end = 0, 0
        for start, end in sorted(intervals):
            total += max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
        totals[agent_id] = total
    return dict(sorted(totals.items()))


def _content_quality(activities: list[dict]) -> dict:
    """@brief 汇总实际内容会话时长，并按人物与内容识别重复观看。"""
    rows = [row for row in activities if row.get("activity_type") in _CONTENT_TYPES]
    summaries = {kind: _contact_summary([row for row in rows if row.get("activity_type") == kind])
                 for kind in sorted(_CONTENT_TYPES)}
    overall = _contact_summary(rows)
    repeats, missing_targets = _watch_repeats(rows)
    return {
        "content_activity_types": sorted(_CONTENT_TYPES),
        "session_count": len(rows),
        "contact_seconds": overall["contact_seconds"],
        "known_contact_seconds": overall["known_contact_seconds"],
        "duration_complete": overall["duration_complete"],
        "sessions_missing_duration": overall["sessions_missing_duration"],
        "by_activity_type": summaries,
        "repeat_watch": {
            "repeated_sessions": sum(item["repeated_sessions"] for item in repeats),
            "groups": repeats,
            "watch_sessions_missing_target": missing_targets,
            "method": "Watch sessions that restarted the same content from the beginning (repeat=true)",
        },
    }


def _contact_summary(rows: list[dict]) -> dict:
    """@brief 对一组内容会话报告已知时长和完整性。"""
    durations = [row["elapsed_seconds"] for row in rows if _is_nonnegative_number(row.get("elapsed_seconds"))]
    complete = len(durations) == len(rows)
    known = sum(durations) if durations else (0 if not rows else None)
    return {
        "sessions": len(rows),
        "contact_seconds": sum(durations) if complete else None,
        "known_contact_seconds": known,
        "duration_complete": complete,
        "sessions_missing_duration": len(rows) - len(durations),
    }


def _watch_repeats(rows: list[dict]) -> tuple[list[dict], int]:
    """@brief 按人物与内容汇总观看会话，只把从头重看（data.repeat 为 true）计为重复观看；接着看不计
    （docs/dev/SPEC-time-and-energy.md 第七章「三层结构」）。恢复后的同一 activity 仍是一个会话。"""
    groups: dict[tuple[Any, str], list[str]] = defaultdict(list)
    repeats: dict[tuple[Any, str], list[str]] = defaultdict(list)
    missing = 0
    for row in rows:
        if row.get("activity_type") != "watch":
            continue
        target = row.get("target_id")
        if not isinstance(target, str) or not target:
            missing += 1
            continue
        groups[(row.get("agent_id"), target)].append(row["activity_id"])
        data = row.get("data")
        if isinstance(data, dict) and data.get("repeat") is True:
            repeats[(row.get("agent_id"), target)].append(row["activity_id"])
    repeated = []
    for key, identities in sorted(repeats.items(), key=lambda item: (str(item[0][0]), item[0][1])):
        repeated.append({"agent_id": key[0], "target_id": key[1], "sessions": len(groups[key]),
                         "repeated_sessions": len(identities), "activity_ids": sorted(identities)})
    return repeated, missing


def _efficiency_quality(events: list[Event]) -> dict:
    """@brief 使用每个真实结算区间加权工作与食品转换效率。"""
    work, work_missing = _work_efficiency_samples(events)
    food, food_missing = _food_efficiency_samples(events)
    return {
        "work": _weighted_efficiency(work, work_missing, "no_work_activity_progress"),
        "food": _weighted_efficiency(food, food_missing, "no_food_consumption_progress"),
        "method": "Duration-weighted values from persisted activity_progress settlements",
    }


def _work_efficiency_samples(events: list[Event]) -> tuple[list[tuple[float, float]], int]:
    """@brief 读取工作活动每个结算区间的时长与效率。"""
    samples = []
    missing = 0
    for event in events:
        if event.event_type != "activity_progress":
            continue
        activity = event.data.get("activity")
        if not isinstance(activity, dict) or activity.get("kind") != "work":
            continue
        start, end = event.data.get("interval_start"), event.data.get("interval_end")
        efficiency = event.data.get("activity_efficiency")
        sample = (float(end - start), float(efficiency)) if (
            _is_int(start) and _is_int(end) and end > start and _is_nonnegative_number(efficiency)
        ) else None
        if sample is None:
            missing += 1
        else:
            samples.append(sample)
    return samples, missing


def _food_efficiency_samples(events: list[Event]) -> tuple[list[tuple[float, float]], int]:
    """@brief 只读取实际食品使用区间，不给非食品消费制造效率。"""
    samples = []
    missing = 0
    for event in events:
        if event.event_type != "activity_progress" or "consumption" not in event.data:
            continue
        consumption = event.data.get("consumption")
        if not isinstance(consumption, dict) or consumption.get("use_mode") != "eat":
            continue
        items = consumption.get("items")
        if not isinstance(items, list) or not any(
            isinstance(item, dict) and _is_positive_number(item.get("interval_quantity")) for item in items
        ):
            continue
        start, end = event.data.get("interval_start"), event.data.get("interval_end")
        efficiency = consumption.get("food_efficiency")
        sample = (float(end - start), float(efficiency)) if (
            _is_int(start) and _is_int(end) and end > start and _is_nonnegative_number(efficiency)
        ) else None
        if sample is None:
            missing += 1
        else:
            samples.append(sample)
    return samples, missing


def _weighted_efficiency(samples: list[tuple[float, float]], missing: int, empty_reason: str) -> dict:
    """@brief 输出已知加权值、覆盖情况和不可用原因。"""
    duration = sum(item[0] for item in samples)
    known_mean = sum(seconds * value for seconds, value in samples) / duration if duration else None
    complete = bool(samples) and missing == 0
    reason = None
    if not samples:
        reason = empty_reason if missing == 0 else "all_efficiency_samples_missing"
    elif missing:
        reason = "some_efficiency_samples_missing"
    return {
        "available": bool(samples),
        "complete": complete,
        "weighted_mean": known_mean if complete else None,
        "known_weighted_mean": known_mean,
        "minimum": min((item[1] for item in samples), default=None),
        "maximum": max((item[1] for item in samples), default=None),
        "known_duration_seconds": duration if samples else None,
        "sample_count": len(samples),
        "missing_sample_count": missing,
        "unavailable_or_incomplete_reason": reason,
    }


def _decision_rhythm_quality(events: list[Event]) -> dict:
    """@brief 统计静默边界、唤醒原因分布与同刻轮次，只透传原始计数。

    占比与存在率指标由 evaluation/behavior_metrics 从这些计数推导；旧事件
    （缺省增量字段）计入缺失分母，不被当作任何一种边界。边界的唤醒原因取共同身体结算，
    决策轮的唤醒原因取 decision_started（同刻轮次不写共同身体结算），两份分列。跨决策预算耗尽的技术停顿
    （environment_event 种类 decision_budget_exhausted，docs/dev/SPEC-persona-prompt.md 第五章）单独计数。
    """
    boundaries = _body_settlement_events(events)
    offered = sum(event.data.get(DECISION_OFFERED_FIELD) is True for event in boundaries)
    silent = sum(event.data.get(DECISION_OFFERED_FIELD) is False for event in boundaries)
    known_reasons, unrecognized_reasons, reason_events = _wake_reason_distribution(boundaries)
    started = [event for event in events if event.event_type == "decision_started"]
    started_known, started_unrecognized, _ = _wake_reason_distribution(started)
    return {
        "body_settlement_events": len(boundaries),
        "boundaries_with_decision_field": offered + silent,
        "boundaries_missing_decision_field": len(boundaries) - offered - silent,
        "silent_boundaries": silent,
        "offered_boundaries": offered,
        "wake_reason_events": reason_events,
        "wake_reason_counts": dict(sorted(known_reasons.items())),
        "unrecognized_wake_reason_counts": dict(sorted(unrecognized_reasons.items())),
        "decision_started_events": len(started),
        "same_moment_round_counts": _same_moment_round_counts(started),
        "decision_started_wake_reason_counts": dict(sorted(started_known.items())),
        "unrecognized_decision_started_wake_reason_counts": dict(sorted(started_unrecognized.items())),
        "decision_budget_exhausted_events": sum(
            event.event_type == "environment_event" and event.data.get("kind") == "decision_budget_exhausted"
            for event in events),
        "method": "Raw counts from shared body settlement and decision started events",
    }


def _same_moment_round_counts(started: list[Event]) -> dict[str, int]:
    """@brief 同刻轮数分布：按（人物, 模拟秒）计 decision_started 轮数，返回 {轮数: 出现次数}，按轮数升序。"""
    rounds = Counter((event.agent_id, event.occurred_at) for event in started)
    distribution = Counter(rounds.values())
    return {str(count): distribution[count] for count in sorted(distribution)}


def _body_settlement_events(events: list[Event]) -> list[Event]:
    """@brief 共同身体结算是人物经过时长与边界的唯一来源。"""
    return [event for event in events if event.event_type == "environment_event"
            and event.data.get("kind") == "body_settlement"]


def _wake_reason_distribution(progresses: list[Event]) -> tuple[Counter, Counter, int]:
    """@brief 汇总各唤醒原因计数，冻结词表之外的成员单列，不并入已知分布。"""
    known: Counter = Counter()
    unrecognized: Counter = Counter()
    reason_events = 0
    for event in progresses:
        reasons = event.data.get(WAKE_REASONS_FIELD)
        if not isinstance(reasons, list):
            continue
        reason_events += 1
        for reason in reasons:
            if isinstance(reason, str) and reason in WAKE_REASON_NAMES:
                known[reason] += 1
            else:
                unrecognized[str(reason)] += 1
    return known, unrecognized, reason_events


def _memory_quality(events: list[Event]) -> dict:
    """@brief 按写入点（trigger）核对系统登记的记忆来源、持久 episode 身份与观察事实窗口。

    写入点只有结束活动（activity_conclusion，对应同一决策已登记的 conclude_activity）与就寝反思（reflection，
    对应醒来那一轮的观察）；收尾与反思同在一步时两项都核对（docs/design/05-recording-engine.md「质量报告」）。
    """
    state = {
        "known": defaultdict(set), "conclusions": {}, "requests": set(), "reflections": set(),
        "concluded": set(), "problems": [], "write_points": 0, "episodes": 0, "update_events": 0,
        "events": {}, "windows": {},
    }
    inspectors = {"observation": _inspect_memory_observation, "action_requested": _inspect_conclusion_request,
                  "model_response": _inspect_memory_response, "memory_updated": _inspect_memory_update,
                  "run_failed": _inspect_memory_failure}
    for event in events:
        inspector = inspectors.get(event.event_type)
        if inspector is not None:
            inspector(event, state)
        state["events"][event.event_id] = event
    for key, event_id in state["conclusions"].items():
        if key not in state["concluded"]:
            _add_memory_problem(state, "conclusion_without_memory_update", event_id, key[0], [])
    return {
        "memory_update_events": state["update_events"],
        "persisted_episode_ids": state["episodes"],
        "write_points_checked": state["write_points"],
        "problem_count": len(state["problems"]),
        "problems": state["problems"],
    }


def _inspect_conclusion_request(event: Event, state: dict) -> None:
    """@brief 登记每个决策的 conclude_activity 请求；经 control_items 读出（新旧形状同一口径）后带任何记忆或看法的，
    应在同一决策写出收尾记忆。"""
    if event.data.get("tool_name") != "conclude_activity":
        return
    key = (event.agent_id, event.decision_id)
    state["requests"].add(key)
    if any(item.memories for item in control_items("conclude_activity", event.data.get("request"))):
        state["conclusions"].setdefault(key, event.event_id)


def _inspect_memory_response(event: Event, state: dict) -> None:
    """@brief 人物决策的模型响应的协议错误提到记忆（含结束工具的 memories、views 字段）时记为记忆协议问题（记忆不再带
    引用，不核对身份）。

    带 purpose 的系统请求（工作验收、零点计划 plan_day）不是记忆写入点，其响应错误不计入记忆问题。
    """
    if event.data.get("purpose"):
        return
    error = event.data.get("error")
    if isinstance(error, str) and any(word in error.lower() for word in _MEMORY_ERROR_WORDS):
        _add_memory_problem(state, "memory_protocol_error", event.event_id, event.agent_id, [])


def _inspect_memory_observation(event: Event, state: dict) -> None:
    """@brief 观察中的事实窗口与进度层来源必须真实存在且已经发生；记下本决策的窗口与是否醒来（就寝反思来源）。

    进度层来源 progress_event_ids 的每个身份都须解析到本人更早提交的事件，否则记 invalid_observed_result_reference。
    """
    if event.visibility != "agent" or event.agent_id is None:
        return
    try:
        facts = validate_verified_experiences(
            event.data.get("verified_experiences"), event.agent_id, event.occurred_at)
    except IntegrityError:
        _add_memory_problem(state, "invalid_verified_experience_window", event.event_id, event.agent_id, [])
        facts = []
    for fact in facts:
        source = state["events"].get(fact["source_event_id"])
        if not _verified_source_matches(source, fact, event):
            _add_memory_problem(state, "invalid_verified_experience_source", event.event_id,
                                event.agent_id, [fact["source_event_id"]])
    for identity in event.data.get("progress_event_ids", []):
        source = state["events"].get(identity) if isinstance(identity, str) else None
        if source is None or source.agent_id != event.agent_id or source.event_seq >= event.event_seq:
            _add_memory_problem(state, "invalid_observed_result_reference", event.event_id, event.agent_id,
                                [identity] if isinstance(identity, str) else [])
    key = (event.agent_id, event.decision_id)
    state["windows"][key] = (event.event_id, {item["source_event_id"] for item in facts})
    wake_reasons = event.data.get("wake_reasons")
    if isinstance(wake_reasons, list) and "wake_from_sleep" in wake_reasons:
        state["reflections"].add(key)


def _verified_source_matches(source: Event | None, fact: dict, observation: Event) -> bool:
    """@brief 观察中的事实必须与该人物更早提交的规范来源逐字对应。"""
    if source is None or source.event_seq >= observation.event_seq:
        return False
    try:
        return fact in project_verified_experiences(
            [source], agent_id=observation.agent_id, cutoff_seq=source.event_seq)
    except IntegrityError:
        return False


def _inspect_memory_update(event: Event, state: dict) -> None:
    """@brief 区分本次新增、合法合并和事实视图刷新，并按写入点核对来源登记。"""
    data = event.data
    created, merged = data.get("created_episode_ids"), data.get("merged_episode_ids")
    if not _valid_memory_update_payload(data) or not _valid_write_points(data, event):
        _add_memory_problem(state, "invalid_memory_update", event.event_id, event.agent_id, [])
        return
    duplicates = sorted(set(created) & state["known"][event.agent_id])
    if duplicates:
        _add_memory_problem(state, "duplicate_memory_episode_id", event.event_id, event.agent_id, duplicates)
    unknown_merged = sorted(set(merged) - state["known"][event.agent_id])
    if unknown_merged:
        _add_memory_problem(state, "unknown_merged_memory_episode_id", event.event_id,
                            event.agent_id, unknown_merged)
    state["known"][event.agent_id].update(created)
    state["episodes"] += len(created)
    state["update_events"] += 1
    key = (event.agent_id, event.decision_id)
    window = state["windows"].get(key)
    if (window is None or data.get("observation_event_id") != window[0]
            or set(data.get("verified_event_ids", [])) != window[1]):
        _add_memory_problem(state, "invalid_memory_update_source_window", event.event_id, event.agent_id, [])
    _check_write_points(event, key, state)


def _valid_write_points(data: dict, event: Event) -> bool:
    """@brief trigger 是写入点闭集的有序无重复子列表，decision_id 与事件所属决策一致。"""
    trigger = data.get("trigger")
    if not isinstance(trigger, list) or any(point not in _WRITE_POINTS for point in trigger):
        return False
    ordered = sorted(trigger, key=_WRITE_POINTS.index)
    return (trigger == ordered and len(set(trigger)) == len(trigger)
            and isinstance(event.decision_id, str) and data.get("decision_id") == event.decision_id)


def _check_write_points(event: Event, key: tuple, state: dict) -> None:
    """@brief 每个写入点都要有对应登记：结束活动对应同一决策的 conclude_activity，反思对应醒来那一轮。

    写入了条目却没有任何写入点同样找不到来源。
    """
    trigger, data = event.data["trigger"], event.data
    state["write_points"] += len(trigger)
    missing = not trigger and bool(data["created_episode_ids"] or data["merged_episode_ids"])
    if "activity_conclusion" in trigger:
        if key in state["requests"]:
            state["concluded"].add(key)
        else:
            missing = True
    if "reflection" in trigger and key not in state["reflections"]:
        missing = True
    if missing:
        _add_memory_problem(state, "memory_update_without_source", event.event_id, event.agent_id, [])


def _valid_memory_update_payload(data: dict) -> bool:
    """@brief 核对封闭操作、文件身份与主观快照的严格对应。"""
    created, merged, episodes = (data.get(name) for name in
                                 ("created_episode_ids", "merged_episode_ids", "episodes"))
    if any(not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value)
           for value in (created, merged)) or not isinstance(episodes, list):
        return False
    identities = created + merged
    if len(identities) != len(set(identities)):
        return False
    if data.get("operation") == "append_episodes":
        if not identities:
            return False
    elif data.get("operation") != "refresh_summary" or identities:
        return False
    if data.get("operation") == "refresh_summary" and data.get("memory_before_sha256") == data.get("memory_sha256"):
        return False
    if any(not isinstance(data.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", data[name])
           for name in ("memory_before_sha256", "memory_sha256", "episodes_sha256", "state_sha256")):
        return False
    if (type(data.get("consolidated")) is not bool or not isinstance(data.get("observation_event_id"), str)
            or not data["observation_event_id"]):
        return False
    verified = data.get("verified_event_ids")
    if (not isinstance(verified, list) or any(not isinstance(item, str) or not item for item in verified)
            or len(verified) != len(set(verified))):
        return False
    return _valid_subjective_snapshots(episodes, identities)


def _snapshot_keys_valid(item: dict) -> bool:
    """@brief 快照键集：四个基本键，或另加 source 且来源为就寝反思（宿主为反思条目标注来源）。"""
    base = {"id", "type", "text", "epistemic_status"}
    return set(item) == base or (set(item) == base | {"source"} and item["source"] == "reflection")


def _valid_subjective_snapshots(episodes: list, identities: list[str]) -> bool:
    """@brief 主观快照与本次实际持久身份一一对应。"""
    if len(episodes) != len(identities):
        return False
    for item in episodes:
        if (not isinstance(item, dict) or not _snapshot_keys_valid(item)
                or item.get("id") not in identities or item.get("type") not in _SUBJECTIVE_TYPES
                or not isinstance(item.get("text"), str)
                or not item["text"] or item.get("epistemic_status") != "subjective"):
            return False
    return {item["id"] for item in episodes} == set(identities)


def _inspect_memory_failure(event: Event, state: dict) -> None:
    """@brief 将持久化的记忆相关运行故障纳入质量问题。"""
    error = event.data.get("error")
    if isinstance(error, str) and any(word in error.lower() for word in _MEMORY_ERROR_WORDS):
        _add_memory_problem(state, "memory_run_failure", event.event_id, event.agent_id, [])


def _add_memory_problem(state: dict, code: str, event_id: str, agent_id: Any, references: list[str]) -> None:
    """@brief 追加稳定排序的记忆问题，不复制原始模型文本。"""
    finding = {"code": code, "event_id": event_id, "agent_id": agent_id}
    if references:
        finding["reference_ids"] = references
    state["problems"].append(finding)


def _model_quality(events: list[Event]) -> dict:
    """@brief 去重调用身份，累计提供商实报 token 并估算公开标价成本。"""
    requests = [event for event in events if event.event_type == "model_request"]
    request_times = {event.data["call_id"]: event.recorded_at for event in requests
                     if isinstance(event.data.get("call_id"), str)}
    terminals: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.event_type == "model_response" and isinstance(event.data.get("call_id"), str):
            terminals[event.data["call_id"]].append(event)
    selected = [_select_terminal(rows) for _, rows in sorted(terminals.items())]
    usage = [_usage_entry(event, request_times.get(event.data["call_id"])) for event in selected
             if isinstance(event.data.get("response"), dict)]
    reported = [item for item in usage if item["tokens"] is not None]
    estimated = [item for item in usage if item["estimated_cost_usd"] is not None]
    request_ids = {event.data.get("call_id") for event in requests if isinstance(event.data.get("call_id"), str)}
    terminal_ids = set(terminals)
    failures = sum(event.data.get("phase") == "failure" for event in selected)
    pending = sorted(request_ids - terminal_ids)
    duplicates = sorted(identity for identity, rows in terminals.items() if len(rows) > 1)
    missing_responses = sum(not isinstance(event.data.get("response"), dict) for event in selected)
    return {
        "request_events": len(requests),
        "unique_request_call_ids": len(request_ids),
        "terminal_call_ids": len(terminals),
        "provider_responses": len(usage),
        "transport_or_terminal_failures": failures,
        "pending_call_ids": pending,
        "orphan_terminal_call_ids": sorted(terminal_ids - request_ids),
        "duplicate_terminal_call_ids": duplicates,
        "protocol_error_responses": sum(bool(event.data.get("error")) for event in selected),
        "token_usage": _aggregate_tokens(usage, reported, missing_responses + len(pending)),
        "estimated_cost": _aggregate_cost(usage, estimated, missing_responses, pending),
        "by_actual_model": _usage_by_model(usage),
        "by_purpose": _usage_by_purpose(requests, usage),
        "pricing_snapshot": _PRICING,
    }


def _select_terminal(events: list[Event]) -> Event:
    """@brief 重复终态只选最早含提供商响应的一条，避免重复计算实际账单。"""
    with_response = [event for event in events if isinstance(event.data.get("response"), dict)]
    return min(with_response or events, key=lambda event: event.event_seq)


def _usage_entry(event: Event, request_recorded_at: str | None) -> dict:
    """@brief 提取一次提供商响应的实际模型、实报 token 和可核价格估算。"""
    response = event.data["response"]
    usage = response.get("usage") if isinstance(response, dict) else None
    tokens = _token_fields(usage)
    model = response.get("model") if isinstance(response.get("model"), str) else None
    cost, band, reason = _estimate_cost(model, usage, request_recorded_at)
    return {
        "event_id": event.event_id,
        "purpose": event.data.get("purpose") or _DECISION_PURPOSE,
        "model": model,
        "tokens": tokens,
        "estimated_cost_usd": cost,
        "price_band": band,
        "price_timestamp": request_recorded_at,
        "cost_unavailable_reason": reason,
    }


def _token_fields(usage: Any) -> dict | None:
    """@brief 只接受提供商明确返回的非负整数 token，不以缺失补零。"""
    if not isinstance(usage, dict):
        return None
    names = ("prompt_tokens", "completion_tokens", "total_tokens")
    if any(not _is_nonnegative_int(usage.get(name)) for name in names):
        return None
    return {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"]}


def _estimate_cost(model: str | None, usage: Any,
                   request_recorded_at: str | None) -> tuple[float | None, str | None, str | None]:
    """@brief 按冻结官方峰谷价估算单次响应，任何计价字段缺失都返回未知。"""
    if model != _PRICING["model"]:
        return None, None, "actual_model_has_no_frozen_price"
    if not isinstance(usage, dict):
        return None, None, "provider_usage_missing"
    names = ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    if any(not _is_nonnegative_int(usage.get(name)) for name in names):
        return None, None, "cache_or_token_breakdown_missing"
    if usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"] != usage["prompt_tokens"]:
        return None, None, "cache_breakdown_disagrees_with_input_tokens"
    peak = _peak_band(request_recorded_at)
    if peak is None:
        return None, None, "recorded_wall_time_is_invalid"
    band = "peak" if peak else "off_peak"
    rates = _PRICING[band]
    amount = (usage["prompt_cache_hit_tokens"] * rates["cache_hit_input"]
              + usage["prompt_cache_miss_tokens"] * rates["cache_miss_input"]
              + usage["completion_tokens"] * rates["output"]) / 1_000_000
    return round(amount, 12), band, None


def _peak_band(value: str | None) -> bool | None:
    """@brief 按官方 UTC 工作日时段判断峰值计价，无法解析时返回 None。"""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        logging.error("Model request recorded_at is not a valid timestamp")
        return None
    if moment.tzinfo is None:
        logging.error("Model request recorded_at has no timezone")
        return None
    utc = moment.astimezone(timezone.utc)
    return utc.weekday() < 5 and (1 <= utc.hour < 4 or 6 <= utc.hour < 10)


def _aggregate_tokens(all_usage: list[dict], reported: list[dict], missing_calls: int = 0) -> dict:
    """@brief 同时给出已知合计、完整合计和覆盖分母。"""
    known = None
    if reported:
        known = {name: sum(item["tokens"][name] for item in reported)
                 for name in ("input_tokens", "output_tokens", "total_tokens")}
    provider_missing = len(all_usage) - len(reported)
    complete = known if all_usage and provider_missing == 0 and missing_calls == 0 else None
    return {
        "responses_with_reported_usage": len(reported),
        "provider_responses_missing_usage": provider_missing,
        "calls_without_provider_response": missing_calls,
        "known_tokens": known,
        "complete_tokens": complete,
    }


def _aggregate_cost(all_usage: list[dict], estimated: list[dict], missing_responses: int,
                    pending: list[str]) -> dict:
    """@brief 报告可计价覆盖；无响应、调用尚未结束或缺计价字段时不声称总成本。"""
    known = round(sum(item["estimated_cost_usd"] for item in estimated), 12) if estimated else None
    complete = bool(all_usage) and len(estimated) == len(all_usage) and missing_responses == 0 and not pending
    reasons = Counter(item["cost_unavailable_reason"] for item in all_usage
                      if item["cost_unavailable_reason"] is not None)
    if missing_responses:
        reasons["terminal_without_provider_response_cost_unknown"] += missing_responses
    if pending:
        reasons["pending_call_cost_unknown"] += len(pending)
    return {
        "known_estimate_usd": known,
        "complete_estimate_usd": known if complete else None,
        "estimated_responses": len(estimated),
        "responses_without_estimate": len(all_usage) - len(estimated),
        "complete": complete,
        "unavailable_reasons": dict(sorted(reasons.items())),
    }


def _usage_by_model(usage: list[dict]) -> list[dict]:
    """@brief 按实际返回模型汇总覆盖和已知 token，不用请求模型替代。"""
    groups: dict[str | None, list[dict]] = defaultdict(list)
    for item in usage:
        groups[item["model"]].append(item)
    result = []
    for model, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        reported = [item for item in rows if item["tokens"] is not None]
        estimated = [item for item in rows if item["estimated_cost_usd"] is not None]
        result.append({
            "actual_model": model,
            "responses": len(rows),
            "token_usage": _aggregate_tokens(rows, reported),
            "known_estimated_cost_usd": (
                round(sum(item["estimated_cost_usd"] for item in estimated), 12) if estimated else None
            ),
            "estimated_responses": len(estimated),
        })
    return result


def _usage_by_purpose(requests: list[Event], usage: list[dict]) -> list[dict]:
    """@brief 按请求用途分列：人物决策（无 purpose）、工作验收与零点计划 plan_day 各自的请求、响应与已知 token。"""
    counts = Counter(event.data.get("purpose") or _DECISION_PURPOSE for event in requests)
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in usage:
        groups[item["purpose"]].append(item)
    return [{"purpose": purpose, "request_events": counts[purpose], "provider_responses": len(groups[purpose]),
             "token_usage": _aggregate_tokens(groups[purpose],
                                              [item for item in groups[purpose] if item["tokens"] is not None])}
            for purpose in sorted(set(counts) | set(groups))]


def _contact_reply_guard_quality(events: list[Event]) -> dict:
    """@brief 联系人回复守卫的命中、重试与兜底计数（docs/dev/SPEC-dayrun-fidelity.md 7.3、第九章）。

    命中按尝试计（首次与重试各算一次），类别也按尝试计；重试与兜底按回复计。没有 attempts 证据的回复单列，
    不当作未命中。
    """
    replies = [event.data for event in events if event.event_type == "environment_event"
               and event.data.get("kind") == "model_npc_reply"]
    guarded = [data for data in replies if isinstance(data.get("attempts"), list)]
    attempts = [item for data in guarded for item in data["attempts"] if isinstance(item, dict)]
    matched = [item["matched"] if isinstance(item.get("matched"), list) else [] for item in attempts]
    return {
        "replies": len(replies),
        "replies_without_guard_evidence": len(replies) - len(guarded),
        "attempts": len(attempts),
        "hits": sum(bool(codes) for codes in matched),
        "retries": sum(len(data["attempts"]) > 1 for data in guarded),
        "fallbacks": sum(data.get("guard_applied") is True for data in guarded),
        "hits_by_category": {code: sum(code in codes for codes in matched) for code in GUARD_PATTERNS},
    }


def _acceleration(events: list[Event], manifest: dict) -> dict:
    """@brief 用每个运行分支观察到的最大墙钟耗时计算实际模拟加速比。"""
    start = manifest["window"]["start"]
    simulated = max(event.occurred_at for event in events) - start
    if simulated < 0:
        raise IntegrityError("Quality report events precede the run window")
    by_run: dict[str, int] = {}
    for event in events:
        by_run[event.run_id] = max(by_run.get(event.run_id, 0), event.wall_elapsed_ms)
    wall_ms = sum(by_run.values())
    reason = None
    speedup = None
    if simulated == 0:
        reason = "simulation_time_did_not_advance"
    elif wall_ms == 0:
        reason = "observed_wall_time_is_zero"
    else:
        speedup = round(simulated / (wall_ms / 1000), 6)
    return {
        "simulated_seconds": simulated,
        "observed_wall_seconds": round(wall_ms / 1000, 3),
        "speedup": speedup,
        "available": speedup is not None,
        "unavailable_reason": reason,
        "wall_seconds_by_run": {key: round(value / 1000, 3) for key, value in sorted(by_run.items())},
        "method": "Sum of maximum persisted wall_elapsed_ms for each represented run branch",
    }


def _input_warning_quality(manifest: dict) -> dict:
    """@brief 运行输入警告（docs/dev/SPEC-dayrun-fidelity.md 1.3）：按警告码闭集、按人物统计清单冻结的 input_warnings。

    清单没有记录警告（引擎直驱为 null，或清单版本 2 之前的运行没有这个键）时计数为 null，不记为零；闭集外的码单列。
    只报告，不放行。
    """
    frozen = manifest.get("input_warnings")
    method = "Warnings frozen into the run manifest input_warnings at run creation; reported, never gating"
    if not isinstance(frozen, list):
        return {"recorded": False, "warning_count": None, "code_counts": None, "unrecognized_code_counts": None,
                "agent_counts": None, "scenario_warning_count": None, "method": method}
    rows = [item for item in frozen if isinstance(item, dict)]
    codes = Counter(str(item.get("code")) for item in rows)
    agents = Counter(str(item["agent_id"]) for item in rows if item.get("agent_id") is not None)
    return {
        "recorded": True,
        "warning_count": len(rows),
        "code_counts": {code: codes[code] for code in INPUT_WARNING_CODES},
        "unrecognized_code_counts": {code: count for code, count in sorted(codes.items())
                                     if code not in INPUT_WARNING_CODES},
        "agent_counts": dict(sorted(agents.items())),
        "scenario_warning_count": sum(item.get("agent_id") is None for item in rows),
        "method": method,
    }


def _is_int(value: Any) -> bool:
    """@brief 排除 bool 后判断整数。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonnegative_int(value: Any) -> bool:
    """@brief 判断提供商计数是非负整数。"""
    return _is_int(value) and value >= 0


def _is_nonnegative_number(value: Any) -> bool:
    """@brief 判断统计值是有限非负数，不把 bool 当数值。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _is_positive_number(value: Any) -> bool:
    """@brief 判断统计时长是有限正数。"""
    return _is_nonnegative_number(value) and value > 0
