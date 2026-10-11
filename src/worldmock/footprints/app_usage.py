"""@brief 只从实际应用快照和推送投影七种数字使用事实。"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.life.action_contract import ACTIVITY_ACTIONS

#: 可以带活动快照的事件：受理、拒绝、结果、进度和环境事件。拒绝带的是被拒那一刻仍在进行的那项活动，世界没有变。
_SNAPSHOT_EVENT_TYPES = frozenset({"action_accepted", "action_rejected", "action_result", "environment_event",
                                   "activity_progress"})


@dataclass
class _Projection:
    """@brief 一次事件前缀中的真实应用活动、区间和推送转换。"""

    #: 已发生的内容、触点和推送事实。
    facts: list[dict] = field(default_factory=list)
    #: 最后活动快照及其真实来源。
    activities: dict[str, tuple[dict, str]] = field(default_factory=dict)
    #: 只由正秒结算产生的实际执行区间。
    segments: dict[str, list[dict]] = field(default_factory=dict)
    #: 历史推送及人物所有权。
    pushes: dict[str, dict] = field(default_factory=dict)
    #: 人物当前实际会话。
    sessions: dict[int, str] = field(default_factory=dict)
    #: 各种转换只记录一次。
    opened: set[str] = field(default_factory=set)
    released: set[str] = field(default_factory=set)
    expired: set[str] = field(default_factory=set)


def project_app_usage(events: list[dict]) -> list[dict]:
    """@brief 持续应用活动输出实际区间，系统触点及支付通知只取独立事件。"""
    projection = _project(events)
    uses = [{**_common("app_use", activity["agent_id"], _app_binding(activity), source),
             "activity_id": activity["activity_id"], "status": activity["status"],
             "segments": projection.segments.get(activity["activity_id"], [])}
            for activity, source in projection.activities.values()]
    return uses + projection.facts


def _common(kind: str, agent_id: int, app_id: str, source: str) -> dict:
    """@brief 七种应用事实共用五个字段，不复制系统动作及步骤信息。"""
    return {"fact_kind": kind, "agent_id": agent_id, "activity_id": None,
            "app_id": app_id, "source_event_id": source}


def _app_binding(activity: dict) -> str | None:
    """@brief 只有真实交互活动的应用身份形成持续使用。"""
    return activity.get("data", {}).get("app_id")


def validate_activity_prefix(events: list[dict]) -> None:
    """@brief 全部消费者首先拒绝退役活动，避免由下游偶然字段错误决定结果。"""
    for event in events:
        rows = activity_snapshots(event["data"])
        nested = event["data"].get("data", {}).get("activity")
        if isinstance(nested, dict):
            rows.append(nested)
        if any(row.get("kind") not in {"sleep", "travel", "interact", "purchase"} for row in rows):
            _invalid_source("Activity snapshot uses a retired activity kind")
        if any("title" in row for row in rows):
            _invalid_source("Activity snapshot contains a retired title field")


def append_activity_segment(segments: list[dict], event: dict) -> None:
    """@brief 应用与活动导出共享区间合并，暂停时间不进入执行区间。"""
    start, end = event["data"]["interval_start"], event["data"]["interval_end"]
    if end <= start or end > event["occurred_at"] or (segments and start < segments[-1]["ended_at"]):
        _invalid_source("Activity progress interval is invalid")
    if segments and start == segments[-1]["ended_at"]:
        segments[-1]["ended_at"] = end
    else:
        segments.append({"started_at": start, "ended_at": end})


def _project(events: list[dict]) -> _Projection:
    """@brief 同次提交的完成先全部结算，再按真实会话结束使推送失效。"""
    validate_activity_prefix(events)
    projection = _Projection()
    commits = groupby(events, key=lambda event: (event["run_id"], event["state_version_after"]))
    for _, committed in commits:
        ended: set[str] = set()
        for event in committed:
            _visit(event, projection, ended)
        _expire_sessions(projection, ended)
    return projection


def _visit(event: dict, projection: _Projection, ended: set[str]) -> None:
    """@brief 活动、内容和会话全部取真实事件，不解释人物自述。"""
    validate_app_event_source(event)
    data = event["data"]
    if event["event_type"] == "environment_event" and data.get("kind") in {"app_touch", "payment_notification"}:
        _system_fact(event, projection)
    if event["event_type"] == "environment_event" and data.get("kind") == "app_push" and "push_id" in data:
        _delivered(event, projection)
    for activity in activity_snapshots(data):
        sessions = activity.get("session_ids", [])
        if event["event_type"] == "action_accepted" and sessions:
            _change_session(projection, activity["agent_id"], sessions[-1])
        if activity["kind"] == "interact" and _app_binding(activity) is not None:
            _app_snapshot(event, activity, projection)
        if event["event_type"] == "activity_progress" and data.get("content", {}).get("completed"):
            _release_completed(event, activity, projection)
        if activity["kind"] == "travel" and activity["status"] in {"completed", "cancelled", "failed", "truncated"}:
            ended.update(sessions[-1:])
    if event["event_type"] == "run_ended":
        ended.update(projection.sessions.values())


def validate_app_event_source(event: dict) -> None:
    """@brief 只有人物可见环境事件能提供活动和内容证据，旧分类载荷拒绝。"""
    data = event["data"]
    if event["event_type"] == "run_ended":
        if event.get("source_kind") != "environment" or event.get("visibility") != "system":
            _invalid_source("Run end must be a system-visible environment event")
    snapshots = activity_snapshots(data)
    if snapshots:
        if (event["event_type"] not in _SNAPSHOT_EVENT_TYPES
                or event.get("source_kind") != "environment" or event.get("visibility") != "agent"):
            _invalid_source("Activity snapshots must come from agent-visible environment events")
        if any(row["kind"] not in {"sleep", "travel", "interact", "purchase"} for row in snapshots):
            _invalid_source("Activity snapshot uses a retired activity kind")
        if any(row["agent_id"] != event["agent_id"] for row in snapshots):
            _invalid_source("Activity snapshot owner disagrees with its source event")
    if event["event_type"] == "activity_progress":
        if event.get("source_kind") != "environment" or event.get("visibility") != "agent":
            _invalid_source("Activity progress must be an agent-visible environment event")
        if "content" in data:
            fields = {"content_id", "app_id", "position_before_seconds", "position_after_seconds",
                      "completed", "completed_rounds_after"}
            if data["activity"]["kind"] != "interact" or set(data["content"]) != fields:
                _invalid_source("Content consumption must use the current interact content contract")
            if data["content"]["app_id"] != data["activity"]["data"]["app_id"]:
                _invalid_source("Content consumption app disagrees with its actual action")
    if event["event_type"] == "environment_event":
        if data.get("kind") == "app_push":
            _validate_push_source(event)
        elif data.get("kind") in {"app_touch", "payment_notification", "purchase_payment"}:
            _validate_system_source(event)


def _validate_push_source(event: dict) -> None:
    """@brief 推送正文为封闭字段集，系统空结算不能夹带投递事实。"""
    data = event["data"]
    empty = data.get("pushed") is False
    fields = ({"kind", "pushed", "session_id"} if empty else
              {"kind", "push_id", "app_id", "app", "content_id", "title", "summary",
               "session_id", "issued_at"})
    if set(data) not in (fields, fields | {"scheduled_id"}):
        _invalid_source("App push settlement fields disagree with its delivery state")
    visibility = "system" if empty else "agent"
    if event.get("source_kind") != "environment" or event.get("visibility") != visibility:
        message = ("Empty app push must be a system-visible environment event" if empty else
                   "App push must be an agent-visible environment event")
        _invalid_source(message)


def _invalid_source(message: str) -> None:
    """@brief 伪造来源是完整性故障，不能静默过滤后继续导出。"""
    logging.error(message)
    raise IntegrityError(message)


def activity_snapshots(data: dict) -> list[dict]:
    """@brief 读取单项快照和同次原生结果的并行活动快照。"""
    rows = [data["activity"]] if isinstance(data.get("activity"), dict) else []
    rows.extend(data.get("data", {}).get("activities", []))
    return rows


def _change_session(projection: _Projection, agent_id: int, session_id: str) -> None:
    """@brief 新真实会话使该人物旧会话尚未释放的推送失效。"""
    previous = projection.sessions.get(agent_id)
    if previous is not None and previous != session_id:
        _expire_sessions(projection, {previous})
    projection.sessions[agent_id] = session_id


def _expire_sessions(projection: _Projection, sessions: set[str]) -> None:
    """@brief 只统计实际结束会话中尚未完成释放的推送。"""
    projection.expired.update(identity for identity, push in projection.pushes.items()
                              if push["session_id"] in sessions and identity not in projection.released)


def _delivered(event: dict, projection: _Projection) -> None:
    """@brief 实际投递只产生一次事实，身份重放不重新计数。"""
    data = event["data"]
    if data["push_id"] in projection.pushes:
        return
    _change_session(projection, event["agent_id"], data["session_id"])
    projection.pushes[data["push_id"]] = {**data, "agent_id": event["agent_id"]}
    projection.facts.append(_push_fact("push_delivered", projection.pushes[data["push_id"]], event, None))


def _push_fact(kind: str, push: dict, event: dict, activity_id: str | None) -> dict:
    """@brief 推送转换保留原应用及会话身份，并引用真实转换事件。"""
    return {**_common(kind, push["agent_id"], push["app_id"], event["event_id"]), "activity_id": activity_id,
            "push_id": push["push_id"], "content_id": push["content_id"], "session_id": push["session_id"],
            "occurred_at": event["occurred_at"]}


def _app_snapshot(event: dict, activity: dict, projection: _Projection) -> None:
    """@brief 消费旧内容的证据先写出，再保存可能已自动接续的新快照。"""
    identity = activity["activity_id"]
    previous = projection.activities.get(identity)
    data = activity.get("data", {})
    binding = _app_binding(activity)
    pin = data.get("pinned_by_push_id")
    if event["event_type"] == "action_accepted" and pin is not None and pin not in projection.opened:
        if pin not in projection.pushes:
            _invalid_source("Opened push must have a prior delivery")
        push = projection.pushes[pin]
        if (push["agent_id"] != activity["agent_id"] or push["app_id"] != binding
                or push["session_id"] not in activity["session_ids"]):
            _invalid_source("Opened push disagrees with the actual app or session")
        projection.opened.add(pin)
        projection.facts.append(_push_fact("push_opened", push, event, identity))
    if event["event_type"] == "activity_progress":
        append_activity_segment(projection.segments.setdefault(identity, []), event)
        if "content" in event["data"]:
            old = previous[0]["data"] if previous is not None else data
            consumed = event["data"]["content"]
            consumed_pin = old.get("pinned_by_push_id") if old.get("content_id") == consumed["content_id"] else None
            projection.facts.append(_content_fact(event, activity, consumed_pin))
    projection.activities[identity] = activity, event["event_id"]


def _validate_system_source(event: dict) -> None:
    """@brief 自动触点、支付和通知使用封闭系统载荷，必须引用真实原因。"""
    data = event["data"]
    fields = {
        "app_touch": {"kind", "app_id", "action", "step", "rule_id"},
        "purchase_payment": {"kind", "activity_id", "order_ids", "amount_fen"},
        "payment_notification": {"kind", "app_id", "amount_fen", "order_ids", "title", "summary"},
    }
    if set(data) != fields[data["kind"]]:
        _invalid_source("System app event fields are outside the current contract")
    if event.get("source_kind") != "environment" or event.get("visibility") != "system":
        _invalid_source("System app event must be a system-visible environment event")
    if len(event.get("caused_by", [])) != 1:
        _invalid_source("System app event requires one actual cause event")
    if data["kind"] == "app_touch":
        if data["action"] is not None and data["action"] not in ACTIVITY_ACTIONS:
            _invalid_source("App touch action is invalid")
        allowed = {"navigation", "payment"} if data["rule_id"] is None else {"browse", "payment"}
        if data["step"] not in allowed:
            _invalid_source("App touch step disagrees with its source")
    else:
        order_ids = data["order_ids"]
        if (not isinstance(order_ids, list) or not order_ids or order_ids != sorted(set(order_ids))
                or any(not isinstance(identity, str) or not identity for identity in order_ids)
                or type(data["amount_fen"]) is not int or data["amount_fen"] <= 0):
            _invalid_source("Purchase payment order group or amount is invalid")
    if data["kind"] == "payment_notification" and (data["title"] != "支付成功"
            or not isinstance(data["summary"], str) or not data["summary"]):
        _invalid_source("Payment notification text is invalid")
    if "app_id" in data and (not isinstance(data["app_id"], str) or not data["app_id"]):
        _invalid_source("System app event requires an actual app identity")


def _system_fact(event: dict, projection: _Projection) -> None:
    """@brief 系统触点和通知没有活动身份，不进入人物公开经历。"""
    data = event["data"]
    fields = () if data["kind"] == "app_touch" else ("amount_fen", "order_ids", "title", "summary")
    projection.facts.append({**_common(data["kind"], event["agent_id"], data["app_id"], event["event_id"]),
                             "occurred_at": event["occurred_at"], **{key: data[key] for key in fields}})


def _content_fact(event: dict, activity: dict, push_id: str | None) -> dict:
    """@brief 内容行取实际结算对象和区间，不能取接续后的标题或位置。"""
    content = event["data"]["content"]
    return {**_common("content_consumed", activity["agent_id"], _app_binding(activity), event["event_id"]),
        "activity_id": activity["activity_id"], "content_id": content["content_id"],
        "interval_start": event["data"]["interval_start"], "interval_end": event["data"]["interval_end"],
        "position_before": content["position_before_seconds"], "position_after": content["position_after_seconds"],
        "completed": content["completed"], "push_id": push_id}


def _release_completed(event: dict, activity: dict, projection: _Projection) -> None:
    """@brief 实际完成释放当前会话全部匹配推送，不制造其他 app 的打开记录。"""
    content_id = event["data"]["content"]["content_id"]
    current = projection.sessions.get(activity["agent_id"])
    for identity, push in projection.pushes.items():
        if (push["agent_id"] == activity["agent_id"] and push["content_id"] == content_id
                and push["session_id"] == current and identity not in projection.released | projection.expired):
            projection.released.add(identity)
            projection.facts.append(_push_fact("push_released", push, event, activity["activity_id"]))


def app_usage_quality(events: list[dict], timezone_name: str) -> dict:
    """@brief 内容计实际秒数，推送漏斗沿真实转换统计，不推测亮屏。"""
    projection = _project(events)
    consumed = [row for row in projection.facts if row["fact_kind"] == "content_consumed"]
    advances = [row for row in consumed if row["position_after"] > row["position_before"]]
    content_events = [event for event in events if event["event_type"] == "activity_progress"
                      and "content" in event["data"]]
    total = sum(event["data"]["interval_end"] - event["data"]["interval_start"] for event in content_events)
    repeated = [event for event in content_events if event["data"]["content"]["completed_rounds_after"]
                - int(event["data"]["content"]["completed"]) >= 1]
    repeat_seconds = sum(event["data"]["interval_end"] - event["data"]["interval_start"] for event in repeated)
    zone = ZoneInfo(timezone_name)
    return {"content_contact": {"by_app": dict(sorted(Counter(row["app_id"] for row in advances).items())),
            "repeat_watch": len(repeated),
            "repeat_seconds_share": repeat_seconds / total if total else None},
        "app_pushes": {"pushed": len(projection.pushes), "opened_in_session": len(projection.opened),
            "consumed_pinned": len({row["push_id"] for row in advances if row["push_id"] is not None}),
            "released_by_completion": len(projection.released), "expired_with_session": len(projection.expired),
            "outside_window_violations": sum(not 8 <= datetime.fromtimestamp(row["issued_at"], zone).hour < 22
                                             for row in projection.pushes.values())}}
