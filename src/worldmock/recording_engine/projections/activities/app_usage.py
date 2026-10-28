"""@brief 只从实际应用快照、前台区间事件、通知和推送投影八种数字使用事实。

应用快照来自自带 app 的活动：人物点名使用的内容 app 与查询所在的 app。
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
from typing import get_args
from zoneinfo import ZoneInfo

from worldmock.resource_engine.catalog.models import FoodTier, ReliefTier
from worldmock.foundation.errors import IntegrityError
from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS, APP_BOUND_ACTIONS
from worldmock.state.world.order_notices import ORDER_TITLES, PAYMENT_SUCCEEDED

#: 可以带活动快照的事件：受理、拒绝、结果、进度和环境事件。拒绝带的是被拒那一刻仍在进行的那项活动，世界没有变。
_SNAPSHOT_EVENT_TYPES = frozenset({"action_accepted", "action_rejected", "action_result", "environment_event",
                                   "activity_progress"})
#: 系统可见的 app 事件各自的字段闭集。
_SYSTEM_FIELDS = {
    "app_touch": {"kind", "app_id", "action", "step", "rule_id"},
    "app_foreground": {"kind", "app_id", "step", "interval_start", "interval_end"},
    "purchase_payment": {"kind", "activity_id", "order_ids", "amount_fen"},
    "payment_notification": {"kind", "app_id", "amount_fen", "order_ids", "title", "summary"},
    "order_notification": {"kind", "app_id", "order_ids", "title", "summary"},
}
#: 进入 app 事实的系统点事件及各自另带的字段；回调足迹只有发生时刻。
_POINT_FACT_FIELDS = {
    "app_touch": (),
    "payment_notification": ("amount_fen", "order_ids", "title", "summary"),
    "order_notification": ("order_ids", "title", "summary"),
}
#: 前台区间的三种步骤：导航、下单、支付。
_FOREGROUND_STEPS = frozenset({"navigation", "ordering", "payment"})
#: 内容证据 data.content 的字段闭集。
_CONTENT_FIELDS = frozenset({"content_id", "app_id", "position_before_seconds", "position_after_seconds",
                             "completed", "completed_rounds_after", "completion_effects"})
#: 完成效果三个成员各自的证据键与档位表（docs/design/05-recording-engine.md「事件与来源」）。
_COMPLETION_MEMBERS = {"food": (frozenset({"tier", "provided", "applied", "efficiency"}), get_args(FoodTier)),
                       "relaxation_relief": (frozenset({"tier", "provided", "applied"}), get_args(ReliefTier)),
                       "social_relief": (frozenset({"tier", "provided", "applied"}), get_args(ReliefTier))}


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
    #: 每项活动最后一份快照的状态；引擎代为使用的 app 行取所属活动的状态。
    statuses: dict[str, str] = field(default_factory=dict)
    #: 引擎代为使用的 app：按（活动，app）累计的前台区间、人物与最后一条来源事件。
    foreground: dict[tuple[str, str], dict] = field(default_factory=dict)
    #: 各 app_use 行第一条来源事件的事件身份，键是自带 app 的活动身份或（活动，app）。
    first_sources: dict[object, str] = field(default_factory=dict)


def project_app_usage(events: list[dict]) -> list[dict]:
    """@brief app 使用行在前、按各行第一条来源事件的先后排列，其余事实按事件先后跟在后面。

    自带 app 的活动（人物点名使用的内容 app、查询所在的 app）输出活动的实际执行区间；引擎代为使用的 app（导航、下单、支付）同一活动、同一 app 一行，
    区间是全部前台区间合并的结果，状态取所属活动的最后状态。
    """
    projection = _project(events)
    uses = {identity: {**_common("app_use", activity["agent_id"], _app_binding(activity), source),
                       "activity_id": identity, "status": activity["status"],
                       "segments": projection.segments.get(identity, [])}
            for identity, (activity, source) in projection.activities.items()}
    for (activity_id, app_id), row in projection.foreground.items():
        if activity_id not in projection.statuses:
            _invalid_source("Automatic app use disagrees with its foreground events")
        uses[activity_id, app_id] = {**_common("app_use", row["agent_id"], app_id, row["source"]),
                                     "activity_id": activity_id, "status": projection.statuses[activity_id],
                                     "segments": row["segments"]}
    order = {event["event_id"]: position for position, event in enumerate(events)}
    ranked = sorted(uses, key=lambda key: order[projection.first_sources[key]])
    return [uses[key] for key in ranked] + projection.facts


def _common(kind: str, agent_id: int, app_id: str, source: str) -> dict:
    """@brief 八种应用事实共用五个字段，不复制系统动作及步骤信息。"""
    return {"fact_kind": kind, "agent_id": agent_id, "activity_id": None,
            "app_id": app_id, "source_event_id": source}


def _app_binding(activity: dict) -> str | None:
    """@brief 自带 app 的活动（内容 app 的使用与查询）的应用身份；没有 app 时为 None，不形成持续使用。"""
    return activity.get("data", {}).get("app_id")


def validate_activity_prefix(events: list[dict]) -> None:
    """@brief 全部消费者首先拒绝退役活动，避免由下游偶然字段错误决定结果。"""
    for event in events:
        rows = activity_snapshots(event["data"])
        nested = event["data"].get("data", {}).get("activity")
        if isinstance(nested, dict):
            rows.append(nested)
        if any(row.get("kind") not in ACTIVITY_ACTIONS for row in rows):
            _invalid_source("Activity snapshot uses a retired activity kind")
        if any("title" in row for row in rows):
            _invalid_source("Activity snapshot contains a retired title field")


def append_activity_segment(segments: list[dict], event: dict,
                            reason: str = "Activity progress interval is invalid") -> None:
    """@brief 应用与活动导出共享区间合并：首尾相接的并成一段，暂停时间不进入执行区间；前台区间用同一合并。

    :param reason: 区间不为正、越过事件时刻或倒退时报的英文原因。
    """
    start, end = event["data"]["interval_start"], event["data"]["interval_end"]
    if end <= start or end > event["occurred_at"] or (segments and start < segments[-1]["ended_at"]):
        _invalid_source(reason)
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
    if event["event_type"] == "environment_event" and data.get("kind") in _POINT_FACT_FIELDS:
        _system_fact(event, projection)
    if event["event_type"] == "environment_event" and data.get("kind") == "app_foreground":
        _foreground(event, projection)
    if event["event_type"] == "environment_event" and data.get("kind") == "app_push" and "push_id" in data:
        _delivered(event, projection)
    for activity in activity_snapshots(data):
        projection.statuses[activity.get("activity_id")] = activity.get("status")
        sessions = activity.get("session_ids", [])
        if event["event_type"] == "action_accepted" and sessions:
            _change_session(projection, activity["agent_id"], sessions[-1])
        if activity["kind"] in APP_BOUND_ACTIONS and _app_binding(activity) is not None:
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
        if any(row["kind"] not in ACTIVITY_ACTIONS for row in snapshots):
            _invalid_source("Activity snapshot uses a retired activity kind")
        if any(row["agent_id"] != event["agent_id"] for row in snapshots):
            _invalid_source("Activity snapshot owner disagrees with its source event")
    if event["event_type"] == "activity_progress":
        if event.get("source_kind") != "environment" or event.get("visibility") != "agent":
            _invalid_source("Activity progress must be an agent-visible environment event")
        if "content" in data:
            if data["activity"]["kind"] != "interact" or set(data["content"]) != _CONTENT_FIELDS:
                _invalid_source("Content consumption must use the current interact content contract")
            if data["content"]["app_id"] != data["activity"]["data"]["app_id"]:
                _invalid_source("Content consumption app disagrees with its actual action")
            _validate_completion_effects(data["content"])
    if event["event_type"] == "environment_event":
        if data.get("kind") == "app_push":
            _validate_push_source(event)
        elif data.get("kind") in _SYSTEM_FIELDS:
            _validate_system_source(event)


def _validate_completion_effects(content: dict) -> None:
    """@brief 完成效果证据为 null，或是只出现在做完的地点内容上、恰含三个成员的对象；每个非空成员的键恰如合同、
    tier 在该成员的档位表内。不成立是运行故障。"""
    effects = content["completion_effects"]
    if effects is None:
        return
    if (not isinstance(effects, dict) or set(effects) != set(_COMPLETION_MEMBERS) or not content["completed"]
            or content["app_id"] is not None
            or not all(_completion_member(effects[name], *_COMPLETION_MEMBERS[name]) for name in _COMPLETION_MEMBERS)):
        _invalid_source("Activity completion effects must use the current location completion contract")


def _completion_member(evidence, keys: frozenset[str], tiers: tuple[str, ...]) -> bool:
    """@brief 一个成员的证据：没有声明为 null；声明了是键恰为 keys、tier 在 tiers 之内的对象。"""
    return evidence is None or (isinstance(evidence, dict) and set(evidence) == keys and evidence["tier"] in tiers)


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
    projection.first_sources.setdefault(identity, event["event_id"])


def _validate_system_source(event: dict) -> None:
    """@brief 回调足迹、前台区间、付款和通知使用封闭系统载荷，必须引用恰一条真实原因。"""
    data = event["data"]
    if set(data) != _SYSTEM_FIELDS[data["kind"]]:
        _invalid_source("System app event fields are outside the current contract")
    if event.get("source_kind") != "environment" or event.get("visibility") != "system":
        _invalid_source("System app event must be a system-visible environment event")
    if len(event.get("caused_by", [])) != 1:
        _invalid_source("System app event requires one actual cause event")
    if data["kind"] == "app_touch":
        _validate_callback_touch(data)
    elif data["kind"] == "app_foreground":
        _validate_foreground(event)
    else:
        _validate_order_event(data)
    if "app_id" in data and (not isinstance(data["app_id"], str) or not data["app_id"]):
        _invalid_source("System app event requires an actual app identity")


def _validate_callback_touch(data: dict) -> None:
    """@brief app 时间点只剩回调足迹：带着回调规则的身份，步骤只有浏览或付款；出行和付款不再写自动时间点。"""
    if data["action"] is not None and data["action"] not in ACTIVITY_ACTIONS:
        _invalid_source("App touch action is invalid")
    if not isinstance(data["rule_id"], str) or not data["rule_id"] or data["step"] not in {"browse", "payment"}:
        _invalid_source("App touch step disagrees with its source")


def _validate_foreground(event: dict) -> None:
    """@brief 前台区间属于一项活动，步骤在闭集内，区间为正并结束于事件时刻。"""
    data = event["data"]
    start, end = data["interval_start"], data["interval_end"]
    if not isinstance(event.get("activity_id"), str) or not event["activity_id"]:
        _invalid_source("System app event fields are outside the current contract")
    if (data["step"] not in _FOREGROUND_STEPS or type(start) is not int or type(end) is not int
            or start >= end or end != event["occurred_at"]):
        _invalid_source("App foreground step or interval is invalid")


def _validate_order_event(data: dict) -> None:
    """@brief 付款与两类通知都列出非空、升序、不重复的记录身份；金额为正整数，标题与正文各在自己的闭集内。"""
    order_ids = data["order_ids"]
    listed = (isinstance(order_ids, list) and bool(order_ids) and order_ids == sorted(set(order_ids))
              and all(isinstance(identity, str) and identity for identity in order_ids))
    written = isinstance(data.get("summary"), str) and bool(data["summary"])
    if data["kind"] == "order_notification":
        if not listed or data["title"] not in ORDER_TITLES or not written:
            _invalid_source("Order notification text or order set is invalid")
        return
    if not listed or type(data["amount_fen"]) is not int or data["amount_fen"] <= 0:
        _invalid_source("Purchase payment order group or amount is invalid")
    if data["kind"] == "payment_notification" and (data["title"] != PAYMENT_SUCCEEDED or not written):
        _invalid_source("Payment notification text is invalid")


def _system_fact(event: dict, projection: _Projection) -> None:
    """@brief 回调足迹和通知没有活动身份，不进入人物公开经历。"""
    data = event["data"]
    projection.facts.append({**_common(data["kind"], event["agent_id"], data["app_id"], event["event_id"]),
                             "occurred_at": event["occurred_at"],
                             **{key: data[key] for key in _POINT_FACT_FIELDS[data["kind"]]}})


def _foreground(event: dict, projection: _Projection) -> None:
    """@brief 引擎代为使用的 app：同一活动、同一 app 的前台区间按时间累计，首尾相接的并成一段。"""
    key = event["activity_id"], event["data"]["app_id"]
    row = projection.foreground.setdefault(key, {"agent_id": event["agent_id"], "segments": []})
    append_activity_segment(row["segments"], event, "App foreground step or interval is invalid")
    row["source"] = event["event_id"]
    projection.first_sources.setdefault(key, event["event_id"])


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
