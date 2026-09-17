"""@brief 从已认证足迹构造确定性的运行后质量与成本报告。"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from worldmock.errors import IntegrityError
from worldmock.footprints.models import (
    DECISION_OFFERED_FIELD,
    INNER_MONOLOGUE_FIELD,
    WAKE_REASON_NAMES,
    WAKE_REASONS_FIELD,
    Event,
)

_ACTION_STATUSES = (
    "submitted", "accepted", "in_progress", "completed", "rejected", "cancelled", "failed", "unknown",
)
_ACTIVITY_STATUSES = ("active", "paused", "completed", "cancelled", "failed", "truncated")
_INTERRUPTED_STATUSES = {"cancelled", "failed", "truncated"}
_CONTENT_TYPES = {"watch", "read_mail", "play_game", "browse"}
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
    """@brief 汇总完成、异常、内容、效率、记忆、模型用量与实测加速比。

    @param events 已经认证且按 event_seq 排序的完整事件。
    @param activities 已经认证的活动终态视图。
    @param manifest 对应运行的冻结清单。
    @return 可规范序列化且不把缺失测量记为零的质量报告。
    """
    _validate_inputs(events, activities, manifest)
    return {
        "schema_version": 1,
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
        "activities": _activity_quality(activities),
        "content_contact": _content_quality(activities),
        "conversion_efficiency": _efficiency_quality(events),
        "decision_rhythm": _decision_rhythm_quality(events),
        "memory_references": _memory_quality(events),
        "model_usage": _model_quality(events),
        "acceleration": _acceleration(events, manifest),
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


def _activity_quality(activities: list[dict]) -> dict:
    """@brief 按权威活动终态区分完成、终态中断和截止点暂停。"""
    statuses = Counter(str(row.get("status")) for row in activities)
    unrecognized = {key: value for key, value in statuses.items() if key not in _ACTIVITY_STATUSES}
    interrupted = [row for row in activities if row.get("status") in _INTERRUPTED_STATUSES]
    reasons = Counter(row["interruption_reason"] for row in interrupted
                      if isinstance(row.get("interruption_reason"), str) and row["interruption_reason"])
    known = [row["elapsed_seconds"] for row in activities if _is_nonnegative_number(row.get("elapsed_seconds"))]
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
        "elapsed_seconds_complete": len(known) == len(activities),
        "activities_missing_elapsed_seconds": len(activities) - len(known),
    }


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
            "method": "Distinct activity sessions after the first session for the same agent and target",
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
    """@brief 保持恢复后的同一 activity 为一个会话，只比较独立观看会话。"""
    groups: dict[tuple[Any, str], list[str]] = defaultdict(list)
    missing = 0
    for row in rows:
        if row.get("activity_type") != "watch":
            continue
        target = row.get("target_id")
        if not isinstance(target, str) or not target:
            missing += 1
            continue
        groups[(row.get("agent_id"), target)].append(row["activity_id"])
    repeated = []
    for (agent_id, target), identities in sorted(groups.items(), key=lambda item: (str(item[0][0]), item[0][1])):
        if len(identities) > 1:
            repeated.append({"agent_id": agent_id, "target_id": target, "sessions": len(identities),
                             "repeated_sessions": len(identities) - 1,
                             "activity_ids": sorted(identities)})
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
        settlement = event.data.get("body_settlement")
        sample = _settlement_sample(settlement, "work_efficiency")
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
        settlement = consumption.get("food_settlement") if isinstance(consumption, dict) else None
        if settlement is None:
            continue
        sample = _settlement_sample(settlement, "food_efficiency")
        if sample is None:
            missing += 1
        else:
            samples.append(sample)
    return samples, missing


def _settlement_sample(settlement: Any, field: str) -> tuple[float, float] | None:
    """@brief 从单个结算提取正时长和有限效率，缺失时返回 None。"""
    if not isinstance(settlement, dict):
        return None
    duration, value = settlement.get("duration_seconds"), settlement.get(field)
    if not _is_positive_number(duration) or not _is_nonnegative_number(value):
        return None
    return float(duration), float(value)


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
    """@brief 统计静默边界、唤醒原因分布与独白存在情况，只透传原始计数。

    占比与存在率指标由 evaluation/behavior_metrics 从这些计数推导；旧事件
    （缺省增量字段）计入缺失分母，不被当作任何一种边界。
    """
    progresses = [event for event in events if event.event_type == "activity_progress"]
    offered = sum(event.data.get(DECISION_OFFERED_FIELD) is True for event in progresses)
    silent = sum(event.data.get(DECISION_OFFERED_FIELD) is False for event in progresses)
    known_reasons, unrecognized_reasons, reason_events = _wake_reason_distribution(progresses)
    started = [event for event in events if event.event_type == "decision_started"]
    with_monologue = sum(isinstance(event.data.get(INNER_MONOLOGUE_FIELD), str) for event in started)
    return {
        "activity_progress_events": len(progresses),
        "boundaries_with_decision_field": offered + silent,
        "boundaries_missing_decision_field": len(progresses) - offered - silent,
        "silent_boundaries": silent,
        "offered_boundaries": offered,
        "wake_reason_events": reason_events,
        "wake_reason_counts": dict(sorted(known_reasons.items())),
        "unrecognized_wake_reason_counts": dict(sorted(unrecognized_reasons.items())),
        "decision_started_events": len(started),
        "decision_started_with_monologue": with_monologue,
        "decision_started_missing_monologue": len(started) - with_monologue,
        "method": "Raw counts from activity_progress decision fields and decision_started monologue field",
    }


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
    """@brief 逐事件检查模型记忆引用、持久 episode 身份和完成关联。"""
    state = {
        "known": defaultdict(set), "finishes": {}, "updates": set(), "problems": [],
        "references_checked": 0, "episodes": 0, "update_events": 0, "visible": {}, "events": {},
    }
    for event in events:
        if event.event_type == "observation":
            _inspect_memory_observation(event, state)
        elif event.event_type == "model_response":
            _inspect_memory_response(event, state)
        elif event.event_type == "memory_updated":
            _inspect_memory_update(event, state)
        elif event.event_type == "run_failed":
            _inspect_memory_failure(event, state)
        state["events"][event.event_id] = event
    for key, event_id in state["finishes"].items():
        if key not in state["updates"]:
            _add_memory_problem(state, "finish_without_memory_update", event_id, key[0], [])
    return {
        "memory_update_events": state["update_events"],
        "persisted_episode_ids": state["episodes"],
        "references_checked": state["references_checked"],
        "problem_count": len(state["problems"]),
        "problems": state["problems"],
    }


def _inspect_memory_response(event: Event, state: dict) -> None:
    """@brief 检索只引用 episode；结束记忆还可引用本次实际观察到的环境事件。"""
    key = (event.agent_id, event.decision_id)
    calls = _response_tool_calls(event)
    for name, arguments in calls:
        if name not in {"memory_read", "finish"}:
            continue
        if arguments is None:
            _add_memory_problem(state, "invalid_memory_tool_arguments", event.event_id, event.agent_id, [])
            continue
        references = _memory_references(name, arguments)
        if references is None:
            _add_memory_problem(state, "invalid_memory_reference_shape", event.event_id, event.agent_id, [])
            continue
        state["references_checked"] += len(references)
        allowed = state["known"][event.agent_id]
        if name == "finish":
            allowed = allowed | state["visible"].get(key, set())
        missing = sorted(set(references) - allowed)
        if missing:
            _add_memory_problem(state, "unknown_memory_reference", event.event_id, event.agent_id, missing)
        if name == "finish" and event.data.get("error") is None:
            state["finishes"][key] = event.event_id
    error = event.data.get("error")
    if isinstance(error, str) and any(word in error.lower() for word in ("memory", "memories")):
        _add_memory_problem(state, "memory_protocol_error", event.event_id, event.agent_id, [])


def _inspect_memory_observation(event: Event, state: dict) -> None:
    """@brief 环境来源必须是本轮观察自身或其中真实可见且已经发生的本人结果。"""
    if event.visibility != "agent" or event.agent_id is None:
        return
    allowed = {event.event_id}
    for result in event.data.get("recent_action_results", []):
        identity = result.get("event_id") if isinstance(result, dict) else None
        source = state["events"].get(identity)
        if (source is None or source.event_type != "action_result" or source.agent_id != event.agent_id
                or source.visibility != "agent" or source.event_seq >= event.event_seq):
            _add_memory_problem(state, "invalid_observed_result_reference", event.event_id, event.agent_id,
                                [identity] if isinstance(identity, str) else [])
        else:
            allowed.add(identity)
    state["visible"][(event.agent_id, event.decision_id)] = allowed


def _inspect_memory_update(event: Event, state: dict) -> None:
    """@brief 累计持久 episode，并报告重复身份或缺少成功 finish。"""
    identities = event.data.get("episode_ids")
    valid = isinstance(identities, list) and all(isinstance(item, str) and item for item in identities)
    if not valid:
        _add_memory_problem(state, "invalid_memory_update", event.event_id, event.agent_id, [])
        return
    duplicates = sorted({item for item in identities if item in state["known"][event.agent_id]})
    if duplicates:
        _add_memory_problem(state, "duplicate_memory_episode_id", event.event_id, event.agent_id, duplicates)
    state["known"][event.agent_id].update(identities)
    state["episodes"] += len(identities)
    state["update_events"] += 1
    key = (event.agent_id, event.decision_id)
    state["updates"].add(key)
    if key not in state["finishes"]:
        _add_memory_problem(state, "memory_update_without_successful_finish", event.event_id, event.agent_id, [])


def _inspect_memory_failure(event: Event, state: dict) -> None:
    """@brief 将持久化的记忆相关运行故障纳入质量问题。"""
    error = event.data.get("error")
    if isinstance(error, str) and any(word in error.lower() for word in ("memory", "memories")):
        _add_memory_problem(state, "memory_run_failure", event.event_id, event.agent_id, [])


def _response_tool_calls(event: Event) -> list[tuple[str, dict | None]]:
    """@brief 从已保存 OpenAI-compatible 响应读取实际工具名和参数。"""
    response = event.data.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("choices"), list):
        return []
    result = []
    for choice in response["choices"]:
        message = choice.get("message") if isinstance(choice, dict) else None
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if isinstance(name, str):
                result.append((name, _decode_arguments(function.get("arguments"), event.event_id)))
    return result


def _decode_arguments(value: Any, event_id: str) -> dict | None:
    """@brief 接受提供商保存的对象或 JSON 字符串，解析错误形成可报告缺失。"""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        logging.error("Memory tool arguments are invalid JSON for event %s", event_id)
        return None
    return parsed if isinstance(parsed, dict) else None


def _memory_references(name: str, arguments: dict) -> list[str] | None:
    """@brief 提取 memory_read 或 finish 中明确声明的 episode 引用。"""
    if name == "memory_read":
        values = arguments.get("ids")
        return list(values) if isinstance(values, list) and all(isinstance(item, str) for item in values) else None
    memories = arguments.get("memories")
    if not isinstance(memories, list):
        return None
    references = []
    for memory in memories:
        values = memory.get("refs", []) if isinstance(memory, dict) else None
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            return None
        references.extend(values)
    return references


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
    """@brief 报告可计价覆盖；无响应、待定或缺计价字段时不声称总成本。"""
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
