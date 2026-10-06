"""@brief 从真实活动、内容结算和会话边界投影 app 足迹及推送质量。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
import logging
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError


@dataclass
class _Projection:
    """@brief 一次认证事件前缀的投影状态；这些集合不写入运行或导出载荷。"""

    #: 按事件顺序产生的内容接触与推送事实。
    facts: list[dict] = field(default_factory=list)
    #: 每个实际 app 活动的最后一份权威快照及来源事件。
    activities: dict[str, tuple[dict, str]] = field(default_factory=dict)
    #: 每次实际投递的推送及其拥有者，保留历史身份。
    pushes: dict[str, dict] = field(default_factory=dict)
    #: 每个人最新实际会话身份，不从计划时间猜测切换。
    sessions: dict[int, str] = field(default_factory=dict)
    #: 曾打开、因完成释放、随真实会话结束失效的推送身份。
    opened: set[str] = field(default_factory=set)
    released: set[str] = field(default_factory=set)
    expired: set[str] = field(default_factory=set)


def project_app_usage(events: list[dict]) -> list[dict]:
    """@brief 投影五类封闭事实；活动一行，内容每次真实结算一行，推送转换只计首次。"""
    projection = _project(events)
    uses = [{"fact_kind": "app_use", "agent_id": activity["agent_id"],
             "activity_id": activity["activity_id"], "app_id": activity["target_id"],
             "started_at": activity["started_at"], "ended_at": activity["ended_at"],
             "status": activity["status"], "source_event_id": source}
            for activity, source in projection.activities.values()]
    return uses + projection.facts


def app_usage_quality(events: list[dict], timezone_name: str) -> dict:
    """@brief 从同一实际事实计接触次数与推送漏斗，不把停止导出等同于会话结束。"""
    projection = _project(events)
    consumed = [row for row in projection.facts if row["fact_kind"] == "content_consumed"]
    advances = [row for row in consumed if row["position_after"] > row["position_before"]]
    consumed_sources = {row["source_event_id"] for row in consumed}
    content_events = [event["data"]["content"] for event in events if event["event_id"] in consumed_sources]
    pinned = {row["push_id"] for row in advances if row["push_id"] is not None}
    zone = ZoneInfo(timezone_name)
    return {
        "content_contact": {
            "by_app": dict(sorted(Counter(row["app_id"] for row in advances).items())),
            "by_kind": dict(sorted(Counter(row["kind"] for row in advances).items())),
            "repeat_watch": sum(row["completed_rounds_after"] - int(row["completed"]) >= 1
                                for row in content_events),
        },
        "app_pushes": {
            "pushed": len(projection.pushes), "opened_in_session": len(projection.opened),
            "consumed_pinned": len(pinned), "released_by_completion": len(projection.released),
            "expired_with_session": len(projection.expired),
            "outside_window_violations": sum(not 8 <= datetime.fromtimestamp(row["issued_at"], zone).hour < 22
                                             for row in projection.pushes.values()),
        },
    }


def _project(events: list[dict]) -> _Projection:
    """@brief 会话到达在整次提交结算之后关闭，保留同一边界其他 app 的真实完成。"""
    projection = _Projection()
    commits = groupby(events, key=lambda event: (event["run_id"], event["state_version_after"]))
    for _, committed in commits:
        ended: set[str] = set()
        for event in committed:
            _visit(event, projection, ended)
        _expire_sessions(projection, ended)
    return projection


def _visit(event: dict, projection: _Projection, ended: set[str]) -> None:
    """@brief 只读取真实环境推送、权威活动快照与 content 结算，不解释模型文字。"""
    validate_app_event_source(event)
    data = event["data"]
    if event["event_type"] == "environment_event" and data.get("kind") == "app_push" and "push_id" in data:
        _delivered(event, projection)
    for activity in activity_snapshots(event["data"]):
        sessions = activity.get("session_ids", [])
        if event["event_type"] == "action_accepted" and sessions:
            _change_session(projection, activity["agent_id"], sessions[-1])
        if activity["kind"] == "use_app":
            _app_snapshot(event, activity, projection)
        if activity["kind"] == "travel" and activity["status"] in {"completed", "cancelled", "failed", "truncated"}:
            ended.update(sessions[-1:])
    if event["event_type"] == "run_ended":
        ended.update(projection.sessions.values())


def validate_app_event_source(event: dict) -> None:
    """@brief 活动结算与投递只认人物可见环境来源；无候选结算保持系统可见。"""
    data = event["data"]
    if event["event_type"] == "run_ended":
        if event.get("source_kind") != "environment" or event.get("visibility") != "system":
            _invalid_source("Run end must be a system-visible environment event")
    if activity_snapshots(data):
        if (event["event_type"] not in {"action_accepted", "action_result", "environment_event", "activity_progress"}
                or event.get("source_kind") != "environment" or event.get("visibility") != "agent"):
            _invalid_source("Activity snapshots must come from agent-visible environment events")
    if event["event_type"] == "activity_progress":
        if event.get("source_kind") != "environment" or event.get("visibility") != "agent":
            _invalid_source("Activity progress must be an agent-visible environment event")
        if "content" in data and data.get("activity", {}).get("kind") != "use_app":
            _invalid_source("Content consumption must belong to a use_app activity")
    if event["event_type"] != "environment_event" or data.get("kind") != "app_push":
        return
    empty = data.get("pushed") is False
    if empty:
        fields = {"kind", "pushed", "session_id"}
    else:
        fields = {"kind", "push_id", "app_id", "app", "content_id", "title", "content_kind", "summary",
                  "session_id", "issued_at"}
    # scheduled_id 是现有预排事件包装器写入的共同来源字段，不改变推送正文的封闭形状。
    if set(data) not in (fields, fields | {"scheduled_id"}):
        _invalid_source("App push settlement fields disagree with its delivery state")
    visibility = "system" if empty else "agent"
    if event.get("source_kind") != "environment" or event.get("visibility") != visibility:
        message = ("Empty app push must be a system-visible environment event" if empty else
                   "App push must be an agent-visible environment event")
        _invalid_source(message)


def _invalid_source(message: str) -> None:
    """@brief 拒绝来源不实的完整事件，不静默丢弃后继续发布足迹。"""
    logging.error(message)
    raise IntegrityError(message)


def activity_snapshots(data: dict) -> list[dict]:
    """@brief 单项活动取事件快照，并行开始与终态取实际 ActionResult.data.activities。"""
    rows = [data["activity"]] if isinstance(data.get("activity"), dict) else []
    rows.extend(data.get("data", {}).get("activities", []))
    return rows


def _change_session(projection: _Projection, agent_id: int, session_id: str) -> None:
    """@brief 推送或活动明确归入新会话时，仅让该人物旧会话尚未释放的推送失效。"""
    previous = projection.sessions.get(agent_id)
    if previous is not None and previous != session_id:
        _expire_sessions(projection, {previous})
    projection.sessions[agent_id] = session_id


def _expire_sessions(projection: _Projection, sessions: set[str]) -> None:
    """@brief 真实结束只统计尚未完成释放的推送，历史条目仍保留。"""
    projection.expired.update(identity for identity, push in projection.pushes.items()
                              if push["session_id"] in sessions and identity not in projection.released)


def _delivered(event: dict, projection: _Projection) -> None:
    """@brief 人物可见的实际投递生成事实；没有候选的系统结算不生成。"""
    data = event["data"]
    identity = data["push_id"]
    if identity in projection.pushes:
        return
    _change_session(projection, event["agent_id"], data["session_id"])
    projection.pushes[identity] = {**data, "agent_id": event["agent_id"]}
    projection.facts.append(_push_fact("push_delivered", projection.pushes[identity], event))


def _push_fact(kind: str, push: dict, event: dict) -> dict:
    """@brief 每个推送转换保留原推送身份、会话与触发转换的真实事件。"""
    return {"fact_kind": kind, "agent_id": push["agent_id"], "push_id": push["push_id"],
            "app_id": push["app_id"], "content_id": push["content_id"], "session_id": push["session_id"],
            "occurred_at": event["occurred_at"], "source_event_id": event["event_id"]}


def _app_snapshot(event: dict, activity: dict, projection: _Projection) -> None:
    """@brief 在下一内容快照覆盖旧游标之前，将刚完成的 content 与此前真实推送绑定。"""
    identity = activity["activity_id"]
    previous = projection.activities.get(identity)
    data = activity.get("data", {})
    pin = data.get("pinned_by_push_id")
    if event["event_type"] == "action_accepted" and pin in projection.pushes and pin not in projection.opened:
        projection.opened.add(pin)
        projection.facts.append(_push_fact("push_opened", projection.pushes[pin], event))
    if event["event_type"] == "activity_progress" and "content" in event["data"]:
        old = previous[0]["data"] if previous is not None else data
        consumed = event["data"]["content"]
        consumed_pin = old.get("pinned_by_push_id") if old.get("content_id") == consumed["content_id"] else None
        projection.facts.append(_content_fact(event, activity, consumed_pin))
        if consumed["completed"]:
            _release_completed(event, activity, projection)
    projection.activities[identity] = (activity, event["event_id"])


def _content_fact(event: dict, activity: dict, push_id: str | None) -> dict:
    """@brief 内容事实只取实际结算载荷，不读取接续后的内容标题或位置。"""
    content = event["data"]["content"]
    return {"fact_kind": "content_consumed", "agent_id": activity["agent_id"],
            "activity_id": activity["activity_id"], "app_id": content["app_id"],
            "content_id": content["content_id"], "kind": content["kind"],
            "position_before": content["position_before_seconds"],
            "position_after": content["position_after_seconds"], "completed": content["completed"],
            "push_id": push_id, "source_event_id": event["event_id"]}


def _release_completed(event: dict, activity: dict, projection: _Projection) -> None:
    """@brief 任一 app 完成内容后释放当前会话全部匹配推送，包含从未打开的其他 app 推送。"""
    content_id = event["data"]["content"]["content_id"]
    current = projection.sessions.get(activity["agent_id"])
    for identity, push in projection.pushes.items():
        if (push["agent_id"] == activity["agent_id"] and push["content_id"] == content_id
                and push["session_id"] == current and identity not in projection.released | projection.expired):
            projection.released.add(identity)
            projection.facts.append(_push_fact("push_released", push, event))
