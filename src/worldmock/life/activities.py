"""@brief 活动的真实推进、固定槽位与显式生命周期控制。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from worldmock.errors import BusinessRuleError
from worldmock.life.action_contract import activity_slot
from worldmock.life.body import ActivityFactors, BodySettlement
from worldmock.life.body_config import BodyConfig
from worldmock.life.models import (
    ActivityChange, ActivityKind, ActivityState, ActivityStatus, AgentState, ChangeActivityRequest, TravelMode,
)


@dataclass(frozen=True, slots=True)
class ActivityStartParams:
    """@brief 引擎由已准入的对象生成的参数，不向人物暴露。"""

    #: 七个固定动作之一。
    action: str
    #: 引擎已经确定的实际计划时长。
    planned_duration_seconds: int
    #: 出行目的地点身份。
    destination_id: str | None = None
    #: 出行方式的冻结值。
    travel_mode: TravelMode | None = None
    #: 已确定的对象、内容或报价事实。
    data: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActivityAdvance:
    """@brief 一次真实推进与共同身体结算的收据。"""

    #: 新活动状态。
    activity: ActivityState
    #: 新人物状态。
    agent: AgentState
    #: 同一区间的共同身体收据。
    settlement: BodySettlement | None
    #: 本活动是否达到计划终点。
    completed: bool


@dataclass(frozen=True, slots=True)
class ActivityOrigin:
    """@brief 活动保持原动作、决策与请求因果身份。"""

    #: 原动作身份。
    action_id: str
    #: 原决策身份。
    decision_id: str
    #: 实际请求事件身份。
    request_event_id: str
    #: 同请求内活动的稳定次序。
    activity_ordinal: int = 1


def start_activity(agent: AgentState, request: ActivityStartParams, now: int,
                   origin: ActivityOrigin, config: BodyConfig) -> tuple[AgentState, ActivityState]:
    """@brief 按已派生时长开始一个固定槽位的活动。"""
    travel = request.travel_mode is not None
    if request.planned_duration_seconds < (1 if travel else 60):
        logging.error("Activity duration is too short")
        raise BusinessRuleError("ACTIVITY_TOO_SHORT")
    kind = ActivityKind.TRAVEL if travel else ActivityKind(request.action)
    suffix = f":{origin.activity_ordinal:04d}" if origin.activity_ordinal > 1 else ""
    activity = ActivityState(
        activity_id=f"activity-{origin.action_id}{suffix}", agent_id=agent.agent_id,
        location_id=agent.location_id, kind=kind, status=ActivityStatus.ACTIVE,
        started_at=now, planned_end_at=now + request.planned_duration_seconds, last_settled_at=now,
        elapsed_seconds=0, planned_duration_seconds=request.planned_duration_seconds,
        destination_id=request.destination_id, travel_mode=request.travel_mode,
        resource_claims={activity_slot(request.action)}, origin_action_id=origin.action_id,
        origin_decision_id=origin.decision_id, origin_request_event_id=origin.request_event_id,
        report_anchor_at=now, data={"action": request.action, **request.data},
    )
    return agent.model_copy(deep=True), activity


def change_activity(agent: AgentState, activity: ActivityState, request: ChangeActivityRequest,
                    now: int, feedback_seconds: int) -> tuple[AgentState, ActivityState]:
    """@brief 显式控制活动；睡眠只能由其计划终点结束。"""
    if activity.agent_id != agent.agent_id or activity.activity_id != request.activity_id:
        logging.error("Activity control does not own its target")
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if activity.kind == ActivityKind.SLEEP and activity.status == ActivityStatus.ACTIVE:
        logging.error("Sleep cannot be controlled before its planned end")
        raise BusinessRuleError("ACTIVITY_PAUSE_UNAVAILABLE")
    if request.operation == ActivityChange.RESUME:
        changed = _resume(activity, now)
        return agent.model_copy(deep=True), schedule_feedback(changed, now, feedback_seconds)
    _require_decision_boundary(agent, activity, now)
    if request.operation == ActivityChange.PAUSE:
        if activity.kind != ActivityKind.INTERACT:
            logging.error("Only interactions can be paused")
            raise BusinessRuleError("ACTIVITY_PAUSE_UNAVAILABLE")
        updates = {"status": ActivityStatus.PAUSED, "boundary_type": "paused",
                   "next_interaction_at": None, "paused_at": now}
    else:
        updates = {"status": ActivityStatus.CANCELLED, "ended_at": now, "boundary_type": "cancelled",
                   "next_interaction_at": None, "interruption_reason": "agent_cancelled"}
    return agent.model_copy(deep=True), activity.model_copy(update=updates, deep=True)


def schedule_feedback(activity: ActivityState, now: int, feedback_seconds: int) -> ActivityState:
    """@brief 反馈只结算状态，不提前终止睡眠、购买或行程。"""
    candidate = now + feedback_seconds
    next_at = candidate if activity.status == ActivityStatus.ACTIVE and candidate < activity.planned_end_at else None
    return activity.model_copy(update={"next_interaction_at": next_at, "boundary_type": None}, deep=True)


def advance_activity_shared(activity: ActivityState, agent: AgentState, now: int,
                            settlement: BodySettlement) -> ActivityAdvance:
    """@brief 身体只结算一次，每项活动独立推进真实经过秒数。"""
    stop = min(now, activity.planned_end_at)
    seconds = stop - activity.last_settled_at
    if activity.status != ActivityStatus.ACTIVE or seconds < 0:
        logging.error("Active activity settlement interval is invalid")
        raise ValueError("active activity settlement interval is invalid")
    elapsed = activity.elapsed_seconds + seconds
    completed = elapsed >= activity.planned_duration_seconds
    updates = {"last_settled_at": stop, "elapsed_seconds": elapsed}
    if activity.kind == ActivityKind.INTERACT and activity.data.get("content_id") is not None:
        updates["media_position_seconds"] = activity.media_position_seconds + seconds
    if completed:
        updates.update(status=ActivityStatus.COMPLETED, boundary_type="planned_end", ended_at=stop,
                       next_interaction_at=None)
    elif activity.next_interaction_at == stop:
        updates["boundary_type"] = "feedback"
    return ActivityAdvance(activity.model_copy(update=updates, deep=True), agent.model_copy(deep=True),
                           settlement, completed)


def truncate_activity(activity: ActivityState, agent: AgentState, now: int) -> tuple[ActivityState, AgentState]:
    """@brief 截止时截断实际剩余活动，保留已结算进度。"""
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        return activity, agent
    changed = activity.model_copy(update={"status": ActivityStatus.TRUNCATED, "ended_at": now,
        "boundary_type": "experiment_end", "interruption_reason": "experiment_end", "next_interaction_at": None},
        deep=True)
    return changed, agent.model_copy(deep=True)


def shared_activity_factors(activity: ActivityState, agent: AgentState, config: BodyConfig) -> ActivityFactors:
    """@brief 身体与边界预测复用同一强度和独立缓解速率。"""
    return activity_factors(activity, agent, config)


def activity_factors(activity: ActivityState, agent: AgentState, config: BodyConfig) -> ActivityFactors:
    """@brief 只读取固定动作和真实内容声明，不从标题推断效果。"""
    key = activity.data["exertion"] if activity.kind == ActivityKind.INTERACT else activity.data["action"]
    return ActivityFactors(config.activity_met[key], activity.data.get("relaxation_relief_per_hour", 0.0),
                           activity.data.get("social_relief_per_hour", 0.0))


def _resume(activity: ActivityState, now: int) -> ActivityState:
    """@brief 恢复保留真实进度，重新发布剩余活动终点。"""
    if activity.status != ActivityStatus.PAUSED:
        logging.error("Activity is not paused")
        raise BusinessRuleError("ACTIVITY_NOT_PAUSED")
    remaining = activity.planned_duration_seconds - activity.elapsed_seconds
    return activity.model_copy(update={"status": ActivityStatus.ACTIVE, "last_settled_at": now,
        "planned_end_at": now + remaining, "boundary_type": None, "report_anchor_at": now,
        "paused_at": None}, deep=True)


def at_decision_boundary(activity: ActivityState, now: int) -> bool:
    """@brief 控制在活动已结算到当前决策的边界执行。"""
    return activity.status == ActivityStatus.ACTIVE and now >= activity.last_settled_at


def _require_decision_boundary(agent: AgentState, activity: ActivityState, now: int) -> None:
    """@brief 拒绝未归属、未结算或已经终止的活动控制。"""
    if activity.agent_id != agent.agent_id or activity.last_settled_at != now:
        logging.error("Activity is not settled at the decision boundary")
        raise BusinessRuleError("ACTIVITY_FEEDBACK_NOT_DUE")
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        logging.error("Activity is already terminal")
        raise BusinessRuleError("ACTIVITY_ALREADY_TERMINAL")
