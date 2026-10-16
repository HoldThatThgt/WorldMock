"""@brief 从已认证足迹构造确定性的运行后质量与成本报告。"""

from __future__ import annotations

from worldmock.agents.life_reflection import REFLECTION_TOOL
from worldmock.agents.life_truncation import RETRY_THINKING
from worldmock.life.action_contract import START_TOOLS
from worldmock.life.decision_start import PLAN_DAY_PURPOSE, is_action_request, request_tool_names
from worldmock.footprints.intervals import valid_interval, body_settlements
from worldmock.life.memory_evidence import MEMORY_WRITE_POINTS, valid_subjective_snapshots
from worldmock.footprints.models import is_sha256
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.evaluation.errand_metrics import conclusion_memories, errand_metrics
from worldmock.evaluation.goal_metrics import day_plan_metrics
from worldmock.footprints.display_wording import DisplayNames
from worldmock.footprints.experiences import project_verified_experiences, validate_verified_experiences
from worldmock.footprints.app_usage import app_usage_quality, validate_activity_prefix
from worldmock.footprints.models import (
    DECISION_OFFERED_FIELD,
    WAKE_REASONS_FIELD,
    Event,
)
from worldmock.input_warnings import INPUT_WARNING_CODES
from worldmock.footprints.timeline_labels import timeline_label_report
from worldmock.life.control_items import control_items
from worldmock.life.error_contract import ENVIRONMENT_NAME_CODES, HOST_NAME_CODES

from worldmock.life.models import ActionStatus

_ACTION_STATUSES = tuple(status.value for status in ActionStatus)
_ACTIVITY_STATUSES = ("active", "paused", "completed", "cancelled", "failed", "truncated")
_INTERRUPTED_STATUSES = {"cancelled", "failed", "truncated"}
#: 记忆写入点闭集（memory_updated.data.trigger 的取值与顺序）：结束活动与就寝反思。
#: 协议错误与运行故障文本中表示记忆的词：记忆本身，以及结束工具里记忆与看法的两个字段名（memories、views）。
_MEMORY_ERROR_WORDS = ("memory", "memories", "views")
#: 输出截断按用途分列时，无 purpose 的请求的分组名。
_DECISION_PURPOSE = "decision"

#: 模型调用按请求形状分的六类，model_usage.by_call_class 按这个次序逐类列出
#: （docs/design/05-recording-engine.md「完整导出认证」）。
CALL_CLASSES = ("fresh_decision_turn", "turn_with_history", "truncation_retry", "consolidation", "reflection",
                "plan_day")
#: 请求形状不属于六类、或终态响应没有对应请求事件的调用的分组名；只在出现时列在六类之后。
_UNRECOGNIZED_CLASS = "unrecognized"
#: 可计价响应按计价拆开的三种 token。
_PRICED_TOKEN_KEYS = ("cache_hit_input_tokens", "cache_miss_input_tokens", "output_tokens")
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


def build_quality_report(events: list[Event], activities: list[dict], manifest: dict, names: DisplayNames) -> dict:
    """@brief 汇总完成、异常、内容、效率、记忆、模型用量、实测加速比、当日目标、个人事务与收尾记忆、运行输入警告与输出截断。

    报告结构 9（docs/design/05-recording-engine.md「完整导出认证」）：input_warnings.code_counts 的键恰为运行输入警告码
    闭集的两个值；lineage 节报告父运行各段的结束原因与停机秒数。两个只报告分组 errand_metrics、conclusion_memories 紧随 day_plan_metrics；三者读同一份事件字典，
    按事件模型的 JSON 形状只构造一次。

    @param events 已经认证且按 event_seq 排序的完整事件。
    @param activities 已经认证的活动终态视图。
    @param manifest 对应运行的冻结清单。
    @param names 这次运行冻结的环境目录给出的显示名称，交给时间线标签检查。
    @return 可规范序列化且不把缺失测量记为零的质量报告。
    """
    validate_activity_prefix([event.model_dump(mode="json") for event in events])
    _validate_inputs(events, activities, manifest)
    rows = [{**event.model_dump(mode="json"), "code_sha256": manifest.get("code_sha256")} for event in events]
    return {
        "schema_version": 9,
        "run_id": manifest["run_id"],
        "source_group_id": manifest["source_group_id"],
        "event_range": {
            "first_event_seq": events[0].event_seq,
            "last_event_seq": events[-1].event_seq,
            "event_count": len(events),
            "activity_count": len(activities),
        },
        "run_outcome": _run_outcome(events),
        "actions": _action_quality(events),
        "activities": _activity_quality(activities, events),
        **app_usage_quality(rows, manifest["window"]["timezone"]),
        "conversion_efficiency": _efficiency_quality(events),
        "decision_rhythm": _decision_rhythm_quality(events),
        "memory_references": _memory_quality(events, manifest["window"]["timezone"]),
        "model_usage": _model_quality(events),
        "acceleration": _acceleration(events, manifest),
        "lineage": _lineage_quality(manifest),
        "day_plan_metrics": day_plan_metrics(rows, manifest["window"]["timezone"]),
        "errand_metrics": errand_metrics(rows, manifest["window"]["timezone"]),
        "conclusion_memories": conclusion_memories(rows),
        "input_warnings": _input_warning_quality(manifest),
        "timeline_labels": timeline_label_report(events, manifest, names),
        "name_rejections": _name_rejection_quality(events),
        "truncations": _truncation_quality(events),
    }


def _validate_inputs(events: list[Event], activities: list[dict], manifest: dict) -> None:
    """@brief 核对报告依赖的身份、顺序和窗口，不重复执行导出认证。"""
    if not events:
        raise IntegrityError("Quality report requires at least one authenticated event")
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("run_id"), str)
            or not isinstance(manifest.get("source_group_id"), str)
):
        raise IntegrityError("Quality report manifest has no run identity")
    window = manifest.get("window")
    if not isinstance(window, dict):
        raise IntegrityError("Quality report manifest has no run window")
    start, end = window.get("start"), window.get("end")
    if not _is_int(start) or not _is_int(end) or end <= start:
        raise IntegrityError("Quality report run window is invalid")
    if not isinstance(manifest.get("lineage"), list):
        raise IntegrityError("Quality report manifest has no lineage")
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


def _lineage_quality(manifest: dict) -> dict:
    """@brief 父运行各段的结束原因、丢弃事件数与停机秒数（分出时刻减父运行最后一条事件的落盘时刻），只读冻结清单的
    lineage，不计入 transport_or_terminal_failures。"""
    parents = []
    for entry in manifest["lineage"]:
        downtime = (datetime.fromisoformat(entry["branched_at"])
                    - datetime.fromisoformat(entry["last_recorded_at"])).total_seconds()
        parents.append({"run_id": entry["run_id"], "checkpoint_id": entry["checkpoint_id"],
                        "end_reason": entry["end_reason"], "end_error": entry["end_error"],
                        "discarded_events": entry["discarded_events"], "downtime_seconds": round(downtime, 3)})
    reasons = Counter(row["end_reason"] for row in parents)
    return {"segment_count": len(parents) + 1, "parents": parents,
            "end_reason_counts": {reason: reasons[reason] for reason in sorted(reasons)},
            "downtime_seconds": round(sum((row["downtime_seconds"] for row in parents), 0.0), 3),
            "method": "Parent segments read from the frozen run manifest lineage; downtime is branched_at minus "
                      "the parent's last recorded event"}


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
    shared = body_settlements(events)
    intervals = [(event.data.get("interval_start"), event.data.get("interval_end")) for event in shared]
    valid_intervals = [(start, end) for start, end in intervals
                       if valid_interval(start, end)]
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
        if valid_interval(start, end):
            by_agent[str(event.agent_id)].append((start, end))
    totals = {}
    for agent_id, intervals in by_agent.items():
        total, previous_end = 0, 0
        for start, end in sorted(intervals):
            total += max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
        totals[agent_id] = total
    return dict(sorted(totals.items()))




def _efficiency_quality(events: list[Event]) -> dict:
    """@brief 食品效率按真实收货提供千卡加权，不能拿进食时长替代。"""
    food, food_missing = _food_efficiency_samples(events)
    return {"food": _weighted_efficiency(food, food_missing, "no_food_receipt"),
            "method": "Calorie-weighted applied/provided values from persisted goods_received events"}


def _food_efficiency_samples(events: list[Event]) -> tuple[list[tuple[float, float]], int]:
    """@brief 每次食品收货以实际提供千卡和实际应用千卡形成效率样本。"""
    samples, missing = [], 0
    for event in events:
        data = event.data
        if (event.event_type != "environment_event" or data.get("kind") != "goods_received"
                or data.get("category") not in {"meal", "snack"}):
            continue
        provided, applied = data.get("provided"), data.get("applied")
        if not _is_positive_number(provided) or not _is_nonnegative_number(applied):
            missing += 1
        else:
            samples.append((float(provided), float(applied) / float(provided)))
    return samples, missing


def _weighted_efficiency(samples: list[tuple[float, float]], missing: int, empty_reason: str) -> dict:
    """@brief 输出已知加权值、覆盖情况和不可用原因。"""
    provided_kcal = sum(item[0] for item in samples)
    known_mean = sum(kcal * value for kcal, value in samples) / provided_kcal if provided_kcal else None
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
        "known_provided_kcal": provided_kcal if samples else None,
        "sample_count": len(samples),
        "missing_sample_count": missing,
        "unavailable_or_incomplete_reason": reason,
    }


def _decision_rhythm_quality(events: list[Event]) -> dict:
    """@brief 统计静默边界、唤醒原因分布与同刻轮次，只透传原始计数。

    占比与存在率指标由 evaluation/behavior_metrics 从这些计数推导；旧事件
    （缺省增量字段）计入缺失分母，不被当作任何一种边界。边界的唤醒原因取共同身体结算，
    决策轮的唤醒原因取 decision_started（同刻轮次不写共同身体结算），两份分列。跨决策预算耗尽的技术停顿
    （environment_event 种类 decision_budget_exhausted，docs/design/06-agent.md）单独计数。
    """
    boundaries = body_settlements(events)
    offered = sum(event.data.get(DECISION_OFFERED_FIELD) is True for event in boundaries)
    silent = sum(event.data.get(DECISION_OFFERED_FIELD) is False for event in boundaries)
    known_reasons, unrecognized_reasons, reason_events = _wake_reason_distribution(boundaries)
    started = [event for event in events if event.event_type == "decision_started"]
    started_known, started_unrecognized, _ = _wake_reason_distribution(started)
    return {
        "body_settlement_events": len(boundaries),
        "boundaries_with_decision_field": offered + silent,
        "silent_boundaries": silent,
        "offered_boundaries": offered,
        "wake_reason_events": reason_events,
        "wake_reason_counts": dict(sorted(known_reasons.items())),
        "wake_category_counts": _wake_category_distribution(boundaries),
        "unrecognized_wake_reason_counts": dict(sorted(unrecognized_reasons.items())),
        "decision_started_events": len(started),
        "same_moment_round_counts": _same_moment_round_counts(started),
        "decision_started_wake_reason_counts": dict(sorted(started_known.items())),
        "decision_started_wake_category_counts": _wake_category_distribution(started),
        "unrecognized_decision_started_wake_reason_counts": dict(sorted(started_unrecognized.items())),
        "method": "Raw counts from shared body settlement and decision started events",
    }




def _same_moment_round_counts(started: list[Event]) -> dict[str, int]:
    """@brief 同刻轮数分布：按（人物, 模拟秒）计 decision_started 轮数，返回 {轮数: 出现次数}，按轮数升序。"""
    rounds = Counter((event.agent_id, event.occurred_at) for event in started)
    distribution = Counter(rounds.values())
    return {str(count): distribution[count] for count in sorted(distribution)}





def _wake_reason_distribution(progresses: list[Event]) -> tuple[Counter, Counter, int]:
    """@brief 汇总各唤醒原因计数，冻结词表之外的成员单列，不并入已知分布。"""
    from worldmock.life.schedule import WAKE_TRIGGERS
    names = {trigger.name for trigger in WAKE_TRIGGERS}
    known: Counter = Counter()
    unrecognized: Counter = Counter()
    reason_events = 0
    for event in progresses:
        reasons = event.data.get(WAKE_REASONS_FIELD)
        if not isinstance(reasons, list):
            continue
        reason_events += 1
        for reason in reasons:
            if isinstance(reason, str) and reason in names:
                known[reason] += 1
            else:
                unrecognized[str(reason)] += 1
    return known, unrecognized, reason_events


def _wake_category_distribution(events: list[Event]) -> dict:
    """@brief 每条事实中同类触发只计一次，分类唯一取静态触发表。"""
    from worldmock.life.schedule import WAKE_TRIGGERS
    categories = {trigger.name: trigger.category for trigger in WAKE_TRIGGERS}
    counts = Counter(category for event in events
                     for category in {categories[reason] for reason in event.data.get(WAKE_REASONS_FIELD, [])
                                      if isinstance(reason, str) and reason in categories})
    return dict(sorted(counts.items()))


def _memory_quality(events: list[Event], timezone_name: str) -> dict:
    """@brief 按写入点（trigger）核对系统登记的记忆来源、持久 episode 身份与观察事实窗口。

    写入点只有结束活动（activity_conclusion，对应同一决策已登记的 conclude_activity）与就寝反思（reflection，
    只凭事件独立重算：当天第一次睡醒、归属前一天、排在观察之前且只登记一次；
    docs/design/05-recording-engine.md「事件与来源」）。本地日按运行清单的窗口时区分。
    """
    state = {
        "known": defaultdict(set), "conclusions": {}, "requests": set(), "wakes": defaultdict(set),
        "reflected": set(), "zone": ZoneInfo(timezone_name),
        "concluded": set(), "problems": [], "write_points": 0, "episodes": 0, "update_events": 0,
        "events": {}, "windows": {},
    }
    inspectors = {"observation": _inspect_memory_observation, "action_requested": _inspect_conclusion_request,
                  "model_response": _inspect_memory_response, "memory_updated": _inspect_memory_update,
                  "run_failed": _inspect_memory_failure, "activity_progress": _inspect_sleep_wake}
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
    """@brief 登记每个决策的 conclude_activity 请求；经 control_items 读出后带任何记忆或看法的，
    应在同一决策写出收尾记忆。"""
    if event.data.get("tool_name") != "conclude_activity":
        return
    key = (event.agent_id, event.decision_id)
    state["requests"].add(key)
    if any(item.memories for item in control_items("conclude_activity", event.data.get("request"))):
        state["conclusions"].setdefault(key, event.event_id)


def _inspect_memory_response(event: Event, state: dict) -> None:
    """@brief 人物决策的结构化错误路径指向 memories 或 views 时记为记忆协议问题。

    带 purpose 的系统请求（工作验收、零点计划 plan_day）不是记忆写入点，其响应错误不计入记忆问题。
    """
    if event.data.get("purpose"):
        return
    report = event.data.get("protocol_feedback")
    items = report["items"] if isinstance(report, dict) else []
    if any(part in {"memories", "views"} for item in items for part in item["path"]):
        _add_memory_problem(state, "memory_protocol_error", event.event_id, event.agent_id, [])


def _inspect_memory_observation(event: Event, state: dict) -> None:
    """@brief 观察中的事实窗口与进度层来源必须真实存在且已经发生；记下本决策的窗口。

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


def _inspect_sleep_wake(event: Event, state: dict) -> None:
    """@brief 记下每个人物真实睡醒的时刻：环境写下的活动进度，快照是已完成的睡眠。"""
    activity = event.data.get("activity")
    if (event.source_kind == "environment" and isinstance(activity, dict) and activity.get("kind") == "sleep"
            and activity.get("status") == "completed" and type(activity.get("ended_at")) is int):
        state["wakes"][event.agent_id].add(activity["ended_at"])


def _reflection_sourced(event: Event, key: tuple, state: dict) -> bool:
    """@brief 就寝反思登记的来源只凭事件重算：有对应的睡醒、是当天第一次睡醒、归属前一天、排在本决策的观察之前，
    并且该人物在这一时刻只登记这一次。"""
    filed, moment = event.data.get("reflection"), (event.agent_id, event.occurred_at)
    repeated = moment in state["reflected"]
    state["reflected"].add(moment)
    if not isinstance(filed, dict):
        return False
    wakes, zone = state["wakes"][event.agent_id], state["zone"]
    today = datetime.fromtimestamp(event.occurred_at, zone).date()
    earlier = any(wake < event.occurred_at and datetime.fromtimestamp(wake, zone).date() == today for wake in wakes)
    return (event.occurred_at in wakes and not earlier and not repeated and key not in state["windows"]
            and filed.get("date") == (today - timedelta(days=1)).isoformat())


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
    if data.get("reflection") is None and (window is None or data.get("observation_event_id") != window[0]
                                           or set(data.get("verified_event_ids", [])) != window[1]):
        _add_memory_problem(state, "invalid_memory_update_source_window", event.event_id, event.agent_id, [])
    _check_write_points(event, key, state)


def _valid_write_points(data: dict, event: Event) -> bool:
    """@brief trigger 是写入点闭集的有序无重复子列表，decision_id 与事件所属决策一致。"""
    trigger = data.get("trigger")
    if not isinstance(trigger, list) or any(point not in MEMORY_WRITE_POINTS for point in trigger):
        return False
    ordered = sorted(trigger, key=MEMORY_WRITE_POINTS.index)
    return (trigger == ordered and len(set(trigger)) == len(trigger)
            and isinstance(event.decision_id, str) and data.get("decision_id") == event.decision_id)


def _check_write_points(event: Event, key: tuple, state: dict) -> None:
    """@brief 每个写入点都要有对应登记：结束活动对应同一决策的 conclude_activity，反思对应当天第一次睡醒。

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
    if ("reflection" in trigger or data.get("reflection") is not None) and not _reflection_sourced(event, key, state):
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
    if any(not is_sha256(data.get(name))
           for name in ("memory_before_sha256", "memory_sha256", "episodes_sha256", "state_sha256")):
        return False
    if type(data.get("consolidated")) is not bool or not _valid_update_binding(data):
        return False
    verified = data.get("verified_event_ids")
    if (not isinstance(verified, list) or any(not isinstance(item, str) or not item for item in verified)
            or len(verified) != len(set(verified))):
        return False
    return valid_subjective_snapshots(episodes, identities)








def _valid_update_binding(data: dict) -> bool:
    """@brief 来源观察与就寝反思归属二选一：步尾登记绑定非空的观察，反思登记的观察为空并带归属日期与所属睡眠。"""
    observation, filed = data.get("observation_event_id"), data.get("reflection")
    if filed is None:
        return isinstance(observation, str) and bool(observation)
    return (observation is None and isinstance(filed, dict) and set(filed) == {"date", "sleep_activity_id"}
            and all(isinstance(value, str) and value for value in filed.values()))


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
    classes = {event.data["call_id"]: _call_class(event.data) for event in requests
               if isinstance(event.data.get("call_id"), str)}
    terminals: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.event_type == "model_response" and isinstance(event.data.get("call_id"), str):
            terminals[event.data["call_id"]].append(event)
    selected = [_select_terminal(rows) for _, rows in sorted(terminals.items())]
    usage = [_usage_entry(event, request_times.get(event.data["call_id"]),
                          classes.get(event.data["call_id"], _UNRECOGNIZED_CLASS)) for event in selected
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
        "by_call_class": _usage_by_call_class(requests, usage),
        "pricing_snapshot": _PRICING,
    }


def _select_terminal(events: list[Event]) -> Event:
    """@brief 重复终态只选最早含提供商响应的一条，避免重复计算实际账单。"""
    with_response = [event for event in events if isinstance(event.data.get("response"), dict)]
    return min(with_response or events, key=lambda event: event.event_seq)


def _usage_entry(event: Event, request_recorded_at: str | None, call_class: str) -> dict:
    """@brief 提取一次提供商响应的调用类、实际模型、实报 token、按计价拆开的 token 和可核价格估算。"""
    response = event.data["response"]
    usage = response.get("usage") if isinstance(response, dict) else None
    tokens = _token_fields(usage)
    model = response.get("model") if isinstance(response.get("model"), str) else None
    cost, band, reason = _estimate_cost(model, usage, request_recorded_at)
    return {
        "event_id": event.event_id,
        "call_class": call_class,
        "model": model,
        "tokens": tokens,
        "priced_tokens": None if cost is None else {
            "cache_hit_input_tokens": usage["prompt_cache_hit_tokens"],
            "cache_miss_input_tokens": usage["prompt_cache_miss_tokens"],
            "output_tokens": usage["completion_tokens"]},
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


def _call_class(data: dict) -> str:
    """@brief 一次模型调用的类，只凭它的 model_request 事件判定，不读响应里的请求副本：purpose 为 plan_day 是零点计划；
    没有 purpose 时，行动请求（decision_start.is_action_request）消息恰为 system、user 两条是新决策回合，更多是带历史的
    回合；工具含 start_main_activity 且思考开关是截断重试的那一个（life_truncation.RETRY_THINKING）是截断重试；不带
    工具是记忆巩固；工具恰为 submit_reflection 是就寝反思；其余为 unrecognized。"""
    purpose, request = data.get("purpose"), data.get("request")
    if purpose == PLAN_DAY_PURPOSE:
        return "plan_day"
    if purpose is not None or not isinstance(request, dict):
        return _UNRECOGNIZED_CLASS
    if is_action_request(request):
        return "fresh_decision_turn" if _message_roles(request) == ["system", "user"] else "turn_with_history"
    names = request_tool_names(request)
    if START_TOOLS[0] in names and request.get("extra_body") == RETRY_THINKING:
        return "truncation_retry"
    if not names:
        return "consolidation"
    return "reflection" if names == [REFLECTION_TOOL] else _UNRECOGNIZED_CLASS


def _message_roles(request: dict) -> list:
    """@brief 请求消息逐条的 role；消息不是列表时为空列表，不是对象的消息记为 None。"""
    messages = request.get("messages")
    if not isinstance(messages, list):
        return []
    return [message.get("role") if isinstance(message, dict) else None for message in messages]


def _usage_by_call_class(requests: list[Event], usage: list[dict]) -> list[dict]:
    """@brief 按调用类分列：六类按 CALL_CLASSES 次序总是列出，unrecognized 只在出现时列在最后；每类给出请求事件数、
    提供商响应数、已知 token、可计价响应按计价拆开的 token、峰时占比与估算成本。"""
    counts = Counter(_call_class(event.data) for event in requests)
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in usage:
        groups[item["call_class"]].append(item)
    seen = counts[_UNRECOGNIZED_CLASS] or groups[_UNRECOGNIZED_CLASS]
    names = (*CALL_CLASSES, _UNRECOGNIZED_CLASS) if seen else CALL_CLASSES
    return [_call_class_usage(name, counts[name], groups[name]) for name in names]


def _call_class_usage(name: str, request_events: int, rows: list[dict]) -> dict:
    """@brief 一类调用的用量行：峰时占比是峰时可计价响应占可计价响应的比例（六位小数）；没有可计价响应时按计价拆开的
    token、峰时占比与估算成本为 null，不记为零。"""
    reported = [item for item in rows if item["tokens"] is not None]
    priced = [item for item in rows if item["priced_tokens"] is not None]
    peak = sum(item["price_band"] == "peak" for item in priced)
    return {
        "call_class": name,
        "request_events": request_events,
        "provider_responses": len(rows),
        "token_usage": _aggregate_tokens(rows, reported),
        "estimated_responses": len(priced),
        "priced_tokens": {key: sum(item["priced_tokens"][key] for item in priced)
                          for key in _PRICED_TOKEN_KEYS} if priced else None,
        "peak_responses": peak,
        "peak_share": round(peak / len(priced), 6) if priced else None,
        "known_estimated_cost_usd": round(sum(item["estimated_cost_usd"] for item in priced), 12) if priced else None,
    }


def _name_rejection_quality(events: list[Event]) -> dict:
    """@brief 名称类拒绝与其余协议错误分开计数（docs/design/05-recording-engine.md）。

    宿主一侧取人物决策（没有 purpose 的模型调用，按调用身份去重后的终态响应）的 protocol_feedback 错误码：
    名称类纠错码（error_contract.HOST_NAME_CODES）逐码计数，其余协议错误另计；环境一侧取 action_rejected
    按名称类拒绝码（ENVIRONMENT_NAME_CODES）逐码计数，并按同一人物同一模拟秒的结算先后用同刻规则分出免计（不计同刻
    轮数、不施加脉冲）的次数。只报告，不放行。
    """
    from worldmock.life.same_moment import free_rejection_flags
    host: Counter[str] = Counter()
    other: Counter[str] = Counter()
    for event in _terminal_responses(events):
        report = event.data.get("protocol_feedback")
        if isinstance(report, dict) and not event.data.get("purpose"):
            code = report["error_code"]
            (host if code in HOST_NAME_CODES else other)[code] += 1
    rejected = [event for event in events if event.event_type == "action_rejected"]
    codes = Counter(event.data.get("error_code") for event in rejected)
    moments: dict[tuple, list] = defaultdict(list)
    for event in rejected:
        moments[(event.agent_id, event.occurred_at)].append(event.data.get("error_code"))
    return {
        "host_protocol_errors": sum(host.values()),
        "host_codes": {code: host[code] for code in sorted(HOST_NAME_CODES)},
        "other_protocol_errors": sum(other.values()),
        "other_protocol_codes": dict(sorted(other.items())),
        "environment_rejections": sum(codes[code] for code in ENVIRONMENT_NAME_CODES),
        "environment_codes": {code: codes[code] for code in sorted(ENVIRONMENT_NAME_CODES)},
        "environment_free_rejections": sum(sum(free_rejection_flags(row)) for row in moments.values()),
        "method": "Host codes from persisted decision protocol_feedback; environment "
                  "codes from non-replayed action_rejected; free = first two name rejections per agent and second",
    }


def _terminal_responses(events: list[Event]) -> list[Event]:
    """@brief 按调用身份去重后的模型终态响应（重复终态取最早含提供商响应的一条，与模型用量同一口径）。"""
    terminals: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.event_type == "model_response" and isinstance(event.data.get("call_id"), str):
            terminals[event.data["call_id"]].append(event)
    return [_select_terminal(rows) for _, rows in sorted(terminals.items())]


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
    """@brief 运行输入警告（docs/design/08-configuration.md「校验与输入冻结」）：按警告码闭集、按人物统计清单冻结的
    input_warnings。

    清单没有记录警告（引擎直驱时为 null）时计数为 null，不记为零；闭集外的码单列。
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


def _truncation_quality(events: list[Event]) -> dict:
    """@brief 输出截断（docs/design/05-recording-engine.md）：提供商回复 finish_reason=length 的
    次数与按用途分列，
    以及这些回复实报的输出 token（截断回合整段丢弃，全部浪费）与其中的思考 token（completion_tokens_details.reasoning_tokens）。

    与 model_usage 同一去重：每个调用身份取一条终态。某次截断缺少对应计数时该合计为 null 并单列缺失次数，不记为零；
    没有截断时合计为 0。只报告，不放行。
    """
    terminals: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.event_type == "model_response" and isinstance(event.data.get("call_id"), str):
            terminals[event.data["call_id"]].append(event)
    truncated = [event for event in (_select_terminal(rows) for _, rows in sorted(terminals.items()))
                 if _finish_reason(event.data.get("response")) == "length"]
    output = [_usage_count(event.data["response"], ("completion_tokens",)) for event in truncated]
    reasoning = [_usage_count(event.data["response"], ("completion_tokens_details", "reasoning_tokens"))
                 for event in truncated]
    purposes = Counter(event.data.get("purpose") or _DECISION_PURPOSE for event in truncated)
    return {
        "truncated_responses": len(truncated),
        "purpose_counts": dict(sorted(purposes.items())),
        "wasted_output_tokens": None if None in output else sum(output),
        "reasoning_tokens": None if None in reasoning else sum(reasoning),
        "responses_missing_output_tokens": output.count(None),
        "responses_missing_reasoning_tokens": reasoning.count(None),
        "method": "Provider responses with finish_reason=length in model_response evidence, one terminal per call id; "
                  "tokens are the provider-reported completion_tokens and completion_tokens_details.reasoning_tokens; "
                  "reported, never gating",
    }


def _finish_reason(response: Any) -> str | None:
    """@brief 提供商回复第一个候选的 finish_reason；回复缺失或形状不符时为 None。"""
    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    reason = first.get("finish_reason") if isinstance(first, dict) else None
    return reason if isinstance(reason, str) else None


def _usage_count(response: dict, path: tuple[str, ...]) -> int | None:
    """@brief 沿键路径读取回复 usage 中提供商实报的非负整数计数；缺失或不是非负整数时为 None。"""
    value: Any = response.get("usage")
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value if _is_nonnegative_int(value) else None


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
