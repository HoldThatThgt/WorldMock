"""@brief 从冻结事件计算习惯、外部结构与刻意目标三层报告。"""

from __future__ import annotations

from worldmock.footprints.intervals import valid_interval
from worldmock.life.schedule import ACTIVITY_KIND_FAMILIES, LIFESTYLE_CATEGORY_FAMILIES, activity_family
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.life.schedule import INTENT_ACTIVITY_FAMILIES, ROUTINE_INTENT_KINDS, ran_in_goal_window

UNREPORTED_CODE_KEY = "code_sha256_unreported"
#: 外部约束闭集只保留营业、到达准入、路线与工作可用性；引导性拒绝（ACTIVITY_PRECONDITION_UNMET、
#: ACTIVITY_CONCLUSION_REQUIRED、SESSION_LIMIT_EXCEEDED）是人物选了带说明的动作，计入 other_rejections
#: （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「动作照常列出，做错了再说明」）。
EXTERNAL_CONSTRAINT_ERROR_CODES = frozenset({
    "MERCHANT_CLOSED", "MERCHANT_OPENING_UNKNOWN", "LOCATION_ACCESS_DENIED",
    "WORK_TASK_NOT_AVAILABLE", "WORK_SUBMISSION_LATE",
    "FROZEN_ROUTE_NOT_FOUND", "ROUTE_UNREACHABLE", "ROUTE_INFORMATION_UNKNOWN",
    "DELIVERY_ROUTE_NOT_FOUND", "DELIVERY_ROUTE_UNREACHABLE", "DELIVERY_ROUTE_INFORMATION_UNKNOWN",
})
_TERMINAL_STATUSES = {"completed", "rejected", "cancelled", "failed"}


def day_plan_metrics(events: list[dict[str, Any]], timezone: str) -> dict[str, Any]:
    """@brief 按代码摘要分组计算三层报告；未知样本不冒充零或成功。

    @param events 已冻结事件字典，可覆盖多个运行、人物和本地日。
    @param timezone 场景冻结的本地时区。
    @return 每个代码摘要的三层报告及外部约束受阻次数门禁。
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        groups[str(event.get("code_sha256") or UNREPORTED_CODE_KEY)].append(event)
    zone = ZoneInfo(timezone)
    return {code: _group_metrics(group, zone) for code, group in sorted(groups.items())}


def _group_metrics(events: list[dict], zone: ZoneInfo) -> dict:
    """@brief 在一个代码组内复用有序证据构造三个独立层次。"""
    ordered = sorted(events, key=_event_order)
    constraints = _external_constraints(ordered)
    value = constraints["block_count"] if constraints["evidence_complete"] else None
    return {
        "habitual_consistency": _habitual_consistency(ordered, zone),
        "external_constraints": constraints,
        "deliberate_goals": _deliberate_goals(ordered, zone),
        "gates": {"external_constraint_block_count": {
            "value": value, "comparison": "==", "threshold": 0, "pass": value == 0,
            "note": "External constraint blocks describe the run, not an engine validation defect"
            if value is not None else "Rejection evidence lacks action_id or error_code",
        }},
    }


def _event_order(event: dict) -> tuple:
    """@brief 使用运行身份和持久顺序稳定回放，事件标识仅用于最终稳定排序。"""
    return (str(event.get("run_id")), event.get("event_seq", 0),
            event.get("occurred_at", 0), str(event.get("event_id")))


def _number(value: Any) -> bool:
    """@brief 只接受有限的真实数值时间，布尔值不视为时间。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float("-inf") < value < float("inf")





def _progress_evidence(event: dict) -> dict | None:
    """@brief 核对实际正时长区间、人物、活动身份与可用活动快照；活动开始时刻（大致时刻判定用）不是数值时为 None。"""
    if event.get("event_type") != "activity_progress":
        return None
    data = event.get("data", {})
    activity = data.get("activity", {})
    start, end = data.get("interval_start"), data.get("interval_end")
    if not (valid_interval(start, end) and end <= event.get("occurred_at", 0)):
        return None
    if not event.get("activity_id") or activity.get("activity_id") != event["activity_id"]:
        return None
    if event.get("agent_id") is None or activity.get("agent_id") != event["agent_id"]:
        return None
    if activity.get("status") not in {"active", "paused", "completed", "truncated"}:
        return None
    started = activity.get("started_at")
    return {"start": start, "end": end, "activity": activity, "token": activity_family(activity),
            "started_at": started if _number(started) and started <= start else None}


def _habitual_consistency(events: list[dict], zone: ZoneInfo) -> dict:
    """@brief 每个活动只取首次正时长结算，按地点和本地小时衡量家族重复性。"""
    contexts: dict[tuple, list[dict]] = defaultdict(list)
    seen = set()
    missing: Counter[str] = Counter()
    for event in events:
        evidence = _progress_evidence(event)
        if evidence is None:
            continue
        identity = (event.get("run_id"), event["agent_id"], event["activity_id"])
        if identity in seen:
            continue
        seen.add(identity)
        activity = evidence["activity"]
        if not evidence["token"] or not activity.get("location_id"):
            missing["missing_activity_family_or_location"] += 1
            continue
        local = datetime.fromtimestamp(evidence["start"], zone)
        family = (LIFESTYLE_CATEGORY_FAMILIES[activity["category"]] if activity["kind"] == "lifestyle"
                  else ACTIVITY_KIND_FAMILIES[activity["kind"]])
        family = "commute" if family == "outing" else family
        key = (*identity[:2], activity["location_id"], local.hour)
        contexts[key].append({"day": local.date().isoformat(), "family": family,
                              "activity_id": event["activity_id"], "event_id": event.get("event_id")})
    groups = {":".join(map(str, key)): _habit_group(key, samples) for key, samples in sorted(contexts.items())}
    pairs = sum(group["comparable_pair_count"] for group in groups.values())
    matches = sum(group["consistent_pair_count"] for group in groups.values())
    missing["contexts_without_two_days"] = sum(group["day_count"] < 2 for group in groups.values())
    if not seen:
        missing["no_positive_activity_settlement"] = 1
    return {"consistency_rate": matches / pairs if pairs else None, "comparable_pair_count": pairs,
            "groups": groups, "missing_samples": dict(sorted(missing.items())),
            "note": "Context repetition proxy; not evidence of a psychological habit"}


def _habit_group(key: tuple, samples: list[dict]) -> dict:
    """@brief 两个不同本地日以上的情境计算全部有序样本对及同家族对。"""
    days = len({sample["day"] for sample in samples})
    families = Counter(sample["family"] for sample in samples)
    pairs = len(samples) * (len(samples) - 1) if days >= 2 else 0
    matches = sum(count * (count - 1) for count in families.values()) if pairs else 0
    return {"run_id": key[0], "agent_id": key[1], "location_id": key[2], "local_hour": key[3],
            "day_count": days, "sample_count": len(samples), "samples": samples,
            "comparable_pair_count": pairs, "consistent_pair_count": matches,
            "consistency_rate": matches / pairs if pairs else None}


def _action_evidence(event: dict) -> tuple[bool, str | None] | None:
    """@brief 只从明确的拒绝或动作终态读取受阻证据。"""
    data = event.get("data", {})
    if data.get("reused"):
        return None
    rejected = event.get("event_type") == "action_rejected" or data.get("status") == "rejected"
    terminal = event.get("event_type") == "action_result" and data.get("status") in _TERMINAL_STATUSES
    if event.get("event_type") not in {"action_rejected", "action_result"} or not (rejected or terminal):
        return None
    code = data.get("error_code")
    code = code if isinstance(code, str) and code else None
    return rejected or code in EXTERNAL_CONSTRAINT_ERROR_CODES, code


def _external_constraints(events: list[dict]) -> dict:
    """@brief 按运行和动作去重外部约束拒绝，并独立暴露协议拒绝与证据缺口。"""
    actions: dict[tuple, dict] = {}
    missing = []
    for event in events:
        evidence = _action_evidence(event)
        if evidence is None:
            continue
        rejected, code = evidence
        if rejected and (not event.get("action_id") or code is None):
            missing.append(event.get("event_id"))
        if not event.get("action_id"):
            continue
        key = (event.get("run_id"), event["action_id"])
        action = actions.setdefault(key, {"rejected": False, "code": None, "source": None})
        if rejected and not action["rejected"]:
            action.update(rejected=True, code=code, source=event.get("event_id"))
    blocked = [item for item in actions.values() if item["code"] in EXTERNAL_CONSTRAINT_ERROR_CODES]
    other = [item for item in actions.values() if item["rejected"] and item not in blocked]
    return {
        "block_count": len(blocked), "by_error_code": dict(sorted(Counter(item["code"] for item in blocked).items())),
        "source_event_ids": sorted(item["source"] for item in blocked), "evidence_complete": not missing,
        "observed_action_acceptance_rate":
            sum(not item["rejected"] for item in actions.values()) / len(actions) if actions else None,
        "terminal_action_count": len(actions), "missing_evidence_event_ids": sorted(set(missing)),
        "other_rejections": {"count": len(other),
                             "by_error_code": dict(sorted(Counter(item["code"] or "missing_error_code"
                                                                      for item in other).items())),
                             "source_event_ids": sorted(item["source"] for item in other)},
    }


def _plan_snapshot(event: dict) -> tuple[dict | None, bool]:
    """@brief 读取登记（零点计划的 day_plan_generated 与修订的动作结果）或结算后的完整当日目标对象，排除幂等重放。"""
    data = event.get("data", {})
    generated = event.get("event_type") == "environment_event" and data.get("kind") == "day_plan_generated"
    registration = generated or event.get("event_type") == "action_result" and data.get("status") == "completed"
    if generated:
        plan = data.get("day_plan")
    elif registration and not data.get("reused"):
        plan = data.get("data", {}).get("day_plan")
    elif event.get("event_type") == "activity_progress":
        plan = data.get("day_plan")
    else:
        return None, False
    if not isinstance(plan, dict) or not isinstance(plan.get("goals"), list) or not _number(plan.get("day_end")):
        return None, False
    return plan, registration


def _around_at(goal: dict) -> int | None:
    """@brief 登记快照里目标的大致时刻（当日绝对时刻）；没有或不是数值时为 None（不限时刻）。"""
    value = goal.get("around_at")
    return value if _number(value) else None


def _new_goal(event: dict, goal: dict, priority: int) -> dict:
    """@brief 每次成功登记的新承诺保存初始优先级、不可洗掉的首次登记身份、大致时刻与是否例行意图。"""
    return {"goal_instance_id": f"{event.get('event_id')}:{goal['intent_kind']}",
            "intent_kind": goal["intent_kind"], "goal": goal["goal"], "initial_priority": priority,
            "routine": goal["intent_kind"] in ROUTINE_INTENT_KINDS, "around_at": _around_at(goal),
            "registered_at": event["occurred_at"], "registration_event_id": event.get("event_id"),
            "progressed_at": None, "progress_activity_id": None, "progress_event_id": None,
            "status": "pending"}


def _revise_goals(day: dict, plan: dict, event: dict) -> None:
    """@brief 相同未推进条目保留实例（只改提醒或大致时刻的也保留，大致时刻取本次登记的值），删除或改写的实例保留为
    丢弃历史。"""
    current = {(goal["intent_kind"], goal["goal"]): goal for goal in day["goals"]
               if goal["status"] != "discarded"}
    incoming = {(goal["intent_kind"], goal["goal"]): goal for goal in plan["goals"]}
    for key, goal in current.items():
        if key not in incoming and goal["status"] == "pending":
            goal["status"] = "discarded"
        elif key in incoming and goal["status"] == "pending":
            goal["around_at"] = _around_at(incoming[key])
    for priority, goal in enumerate(plan["goals"], start=1):
        key = (goal["intent_kind"], goal["goal"])
        if key not in current:
            day["goals"].append(_new_goal(event, goal, priority))


def _matching_goal(day: dict, snapshot: dict) -> dict | None:
    """@brief 只匹配仍在清单中的原登记实例，不能让重新加入的文本复用旧证据。"""
    return next((goal for goal in day["goals"] if goal["status"] == "pending"
                 and goal["intent_kind"] == snapshot.get("intent_kind")
                 and goal["goal"] == snapshot.get("goal")
                 and goal["registered_at"] == snapshot.get("registered_at")), None)


def _record_progress(day: dict, plan: dict, event: dict, used: set) -> None:
    """@brief 将首个匹配目标关联到同人物同日本次实际正时长活动证据。

    匹配与推进判定同一口径（schedule.progress_day_plan）：按清单顺序取首个未推进、同家族、登记后正时长结算、且带大致
    时刻时活动在其前后容差内进行（schedule.ran_in_goal_window）的目标；该目标的推进快照须指向本次活动与区间，否则
    不记推进。
    """
    evidence = _progress_evidence(event)
    if evidence is None or not evidence["token"]:
        return
    identity = (day["run_id"], day["agent_id"], day["date"], event["activity_id"])
    if identity in used:
        return
    for snapshot in plan["goals"]:
        goal = _matching_goal(day, snapshot)
        if goal is None or evidence["token"] not in INTENT_ACTIVITY_FAMILIES.get(goal["intent_kind"], ()):
            continue
        lower = max(evidence["start"], goal["registered_at"])
        upper = min(evidence["end"], plan["day_end"])
        if not (lower < upper and ran_in_goal_window(goal["around_at"], evidence["started_at"], upper)):
            continue
        progressed = snapshot.get("progressed_at")
        if not (_number(progressed) and lower < progressed <= upper
                and snapshot.get("progress_activity_id") == event["activity_id"]):
            return
        goal.update(status="progressed", progressed_at=progressed, progress_activity_id=event["activity_id"],
                    progress_event_id=event.get("event_id"))
        used.add(identity)
        return


def _goal_counts(goals: list[dict]) -> dict:
    """@brief 统计全部登记实例，丢弃不会移除分母。"""
    count = len(goals)
    progressed = sum(goal["status"] == "progressed" for goal in goals)
    return {"goal_count": count, "progressed_count": progressed,
            "discarded_count": sum(goal["status"] == "discarded" for goal in goals),
            "family_progress_rate": progressed / count if count else None}


def _goal_summary(goals: list[dict]) -> dict:
    """@brief 推进比例只计非例行意图的目标，按首次登记的清单优先级输出，不从修订后的名次重排历史；例行意图
    （schedule.ROUTINE_INTENT_KINDS）的目标照常判定与记录，另计一行 routine_goals（
    docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-零点计划只写今天与平常不同的事 ）。"""
    counted = [goal for goal in goals if not goal["routine"]]
    priorities = sorted({goal["initial_priority"] for goal in counted})
    return {**_goal_counts(counted), "by_priority": {
        str(priority): _goal_counts([goal for goal in counted if goal["initial_priority"] == priority])
        for priority in priorities}, "routine_goals": _goal_counts([goal for goal in goals if goal["routine"]])}


def _deliberate_goals(events: list[dict], zone: ZoneInfo) -> dict:
    """@brief 回放全部成功目标登记和实际推进证据，保持运行、人物和本地日隔离。"""
    days: dict[tuple, dict] = {}
    used = set()
    for event in events:
        plan, registration = _plan_snapshot(event)
        if plan is None or event.get("agent_id") is None:
            continue
        local_day = datetime.fromtimestamp(plan["day_end"] - 1, zone).date().isoformat()
        key = (str(event.get("run_id")), event["agent_id"], local_day)
        if registration:
            if datetime.fromtimestamp(event["occurred_at"], zone).date().isoformat() != local_day:
                continue
            day = days.setdefault(key, {"run_id": key[0], "agent_id": key[1], "date": local_day, "goals": []})
            _revise_goals(day, plan, event)
        elif key in days:
            _record_progress(days[key], plan, event, used)
    rows = {":".join(map(str, key)): {**day, **_goal_summary(day["goals"])} for key, day in sorted(days.items())}
    goals = [goal for day in days.values() for goal in day["goals"]]
    return {"per_agent_day": rows, "summary": {"agent_day_count": len(rows), **_goal_summary(goals)},
            "reference": None,
            "notes": ["Family progress is actual activity evidence, not natural-language goal completion",
                      "No external empirical goal distribution is connected",
                      "family_progress_rate counts non-routine intents only; routine intents are in routine_goals",
                      *([] if any(not goal["routine"] for goal in goals)
                        else ["No registered non-routine goals; family_progress_rate is null"])]}
