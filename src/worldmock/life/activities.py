"""@brief 管理系统活动与统一动作的实际推进、占用和中断。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from worldmock.catalog.affordances import engine_affordances
from worldmock.errors import BusinessRuleError
from worldmock.exchange.constants import CATEGORY_MET, ENTERTAINMENT_RELIEF_PER_HOUR, LIFESTYLE_HEALTH_MOOD_PER_HOUR
from worldmock.exchange.types import ActionCategory
from worldmock.life import felt
from worldmock.life.body import ActivityFactors, BodySettlement, settle_body
from worldmock.life.models import (
    ActivityChange, ActivityKind, ActivityStartParams, ActivityState, ActivityStatus,
    AgentState, ChangeActivityRequest,
)
from worldmock.life.scenario import BodyConfig


@dataclass(frozen=True, slots=True)
class ActivityAdvance:
    """@brief 一次实际活动推进的新状态与身体证据。"""

    activity: ActivityState
    agent: AgentState
    settlement: BodySettlement | None
    completed: bool


@dataclass(frozen=True, slots=True)
class ActivityOrigin:
    """@brief 活动最初请求的动作、决策和请求事件身份。"""

    action_id: str
    decision_id: str
    request_event_id: str
    activity_ordinal: int = 1


_AFFORDANCES = engine_affordances()
FEEDBACK_ACTIVITY_KINDS = frozenset(ActivityKind(name) for name in _AFFORDANCES.feedback_kinds)


def start_activity(agent: AgentState, request: ActivityStartParams, now: int, origin: ActivityOrigin,
                   config: BodyConfig) -> tuple[AgentState, ActivityState]:
    """@brief 启动规则已派生时长的系统活动；数据集动作由统一执行器构造。"""
    minimum = 1 if request.activity_type == ActivityKind.TRAVEL else 60
    if request.planned_duration_seconds is None or request.planned_duration_seconds < minimum:
        raise BusinessRuleError("ACTIVITY_TOO_SHORT")
    suffix = f":{origin.activity_ordinal:04d}" if origin.activity_ordinal > 1 else ""
    activity = ActivityState(
        activity_id=f"activity-{origin.action_id}{suffix}", agent_id=agent.agent_id,
        location_id=agent.location_id, kind=request.activity_type, status=ActivityStatus.ACTIVE,
        started_at=now, planned_end_at=now + request.planned_duration_seconds, last_settled_at=now,
        elapsed_seconds=0, planned_duration_seconds=request.planned_duration_seconds,
        destination_id=request.destination_id, travel_mode=request.travel_mode,
        resource_claims=resource_claims(request), origin_action_id=origin.action_id,
        origin_decision_id=origin.decision_id, origin_request_event_id=origin.request_event_id,
        report_anchor_at=now)
    return agent.model_copy(deep=True), activity


def change_activity(agent: AgentState, activity: ActivityState, request: ChangeActivityRequest,
                    now: int, feedback_seconds: int) -> tuple[AgentState, ActivityState]:
    """@brief 对暂停、恢复和退出执行显式转换；动作恢复随后由执行器同步全局位置。"""
    if activity.agent_id != agent.agent_id or activity.activity_id != request.activity_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if request.operation == ActivityChange.PAUSE:
        _require_decision_boundary(agent, activity, now)
        return _pause(agent, activity, now)
    if request.operation == ActivityChange.RESUME:
        resumed_agent, resumed = _resume(agent, activity, now)
        return resumed_agent, schedule_feedback(resumed, now, feedback_seconds)
    if activity.status == ActivityStatus.ACTIVE:
        _require_decision_boundary(agent, activity, now)
    return _cancel(agent, activity)


def schedule_feedback(activity: ActivityState, now: int, feedback_seconds: int) -> ActivityState:
    """@brief 系统活动设置反馈边界，统一动作只有内容边界与既有播报机会。"""
    if activity.status != ActivityStatus.ACTIVE or activity.kind not in FEEDBACK_ACTIVITY_KINDS:
        return activity.model_copy(deep=True)
    candidate = now + feedback_seconds
    next_at = candidate if candidate < activity.planned_end_at else None
    return activity.model_copy(update={"next_interaction_at": next_at, "boundary_type": None}, deep=True)


def advance_activity(activity: ActivityState, agent: AgentState, now: int, config: BodyConfig) -> ActivityAdvance:
    """@brief 按真实经过秒数结算身体与本活动游标。"""
    if activity.status != ActivityStatus.ACTIVE:
        return ActivityAdvance(activity.model_copy(deep=True), agent.model_copy(deep=True), None, False)
    stop = min(now, activity.planned_end_at)
    seconds = stop - activity.last_settled_at
    if seconds < 0:
        logging.error("Activity settlement time moved backwards")
        raise ValueError("activity settlement time moved backwards")
    settlement = settle_body(agent.body, activity.kind, seconds, config, activity_factors(activity, agent, config))
    changed_agent = agent.model_copy(update={"body": settlement.after, "body_settled_at": stop}, deep=True)
    return advance_activity_shared(activity, changed_agent, now, settlement)


def advance_activity_shared(activity: ActivityState, agent: AgentState, now: int,
                            settlement: BodySettlement) -> ActivityAdvance:
    """@brief 共同身体结算后只推进本活动真实秒数及运动的固定情绪效果。"""
    stop = min(now, activity.planned_end_at)
    seconds = stop - activity.last_settled_at
    if activity.status != ActivityStatus.ACTIVE or seconds < 0:
        logging.error("Active activity settlement interval is invalid")
        raise ValueError("active activity settlement interval is invalid")
    elapsed = activity.elapsed_seconds + seconds
    completed = elapsed >= activity.planned_duration_seconds
    changed = _advanced_activity(activity, stop, elapsed, completed)
    changed_agent = _exercise_relief(agent, activity, seconds)
    return ActivityAdvance(changed, changed_agent, settlement, completed)


def end_sleep_by_reminder(advance: ActivityAdvance, now: int) -> ActivityAdvance:
    """@brief 提醒到点按已睡秒数结束睡眠；计划结束优先。"""
    if advance.completed or advance.activity.kind != ActivityKind.SLEEP:
        return advance
    ended = advance.activity.model_copy(update={
        "status": ActivityStatus.COMPLETED, "ended_at": now, "boundary_type": "plan_reminder",
        "interruption_reason": "plan_reminder", "next_interaction_at": None}, deep=True)
    return ActivityAdvance(ended, advance.agent, advance.settlement, True)


def _exercise_relief(agent: AgentState, activity: ActivityState, seconds: int) -> AgentState:
    """@brief 运动类别按实际小时施加系统固定情绪幅度，不读取环境效果权重。"""
    if activity.kind != ActivityKind.ACTION or activity.data["category"] != ActionCategory.EXERCISE or seconds <= 0:
        return agent
    valence = felt.apply_mood_pulse(agent.body.mood_valence, LIFESTYLE_HEALTH_MOOD_PER_HOUR * seconds / 3600,
                                   agent.body.neuroticism_norm)
    return agent.model_copy(update={"body": agent.body.model_copy(update={"mood_valence": valence})}, deep=True)


def truncate_activity(activity: ActivityState, agent: AgentState, now: int) -> tuple[ActivityState, AgentState]:
    """@brief 实验终点将仍在执行或暂停的活动标记截断。"""
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        return activity, agent
    changed = activity.model_copy(update={"status": ActivityStatus.TRUNCATED, "ended_at": now,
        "boundary_type": "experiment_end", "interruption_reason": "experiment_end", "next_interaction_at": None},
        deep=True)
    return changed, agent.model_copy(deep=True)


def resource_claims(request: ActivityStartParams) -> set[str]:
    """@brief 系统活动按固定注册表求占用，动作占用由准备结果求得。"""
    return set(_AFFORDANCES.resource_claims(
        request.activity_type.value, travel_mode=request.travel_mode.value if request.travel_mode else None))


def shared_activity_factors(activity: ActivityState, agent: AgentState, config: BodyConfig) -> ActivityFactors:
    """@brief 共同身体与边界预测使用相同的固定类别系数。"""
    return activity_factors(activity, agent, config)


def activity_factors(activity: ActivityState, agent: AgentState, config: BodyConfig) -> ActivityFactors:
    """@brief 动作 MET 从类别表读；系统活动从包内身体查表读。"""
    if activity.kind == ActivityKind.ACTION:
        met = CATEGORY_MET[ActionCategory(activity.data["category"])][activity.data["app_id"] is not None]
    else:
        met = config.activity_met[_AFFORDANCES.met_lookup_key(activity.kind.value, travel_mode=activity.travel_mode)]
    return ActivityFactors(met, entertainment_relief_rate(activity, config))


def entertainment_relief_rate(activity: ActivityState, config: BodyConfig) -> float:
    """@brief 只有娱乐类别按系统固定速率缓解娱乐需要。"""
    return (ENTERTAINMENT_RELIEF_PER_HOUR if activity.kind == ActivityKind.ACTION
            and activity.data["category"] == ActionCategory.ENTERTAINMENT else 0.0)


def _advanced_activity(activity: ActivityState, stop: int, elapsed: int, completed: bool) -> ActivityState:
    """@brief 推进累计秒数与动作游标；内容接续由执行器恢复进行中状态。"""
    media = activity.media_position_seconds
    if activity.kind == ActivityKind.ACTION:
        media += stop - activity.last_settled_at
    updates = {"last_settled_at": stop, "elapsed_seconds": elapsed, "media_position_seconds": media}
    if completed:
        updates.update(status=ActivityStatus.COMPLETED, boundary_type="planned_end", ended_at=stop,
                       next_interaction_at=None)
    elif activity.next_interaction_at == stop:
        updates.update(boundary_type="feedback")
    return activity.model_copy(update=updates, deep=True)


def _pause(agent: AgentState, activity: ActivityState, now: int) -> tuple[AgentState, ActivityState]:
    """@brief 暂停只释放占用，不写内容进度或消费时刻。"""
    if activity.status != ActivityStatus.ACTIVE:
        raise BusinessRuleError("ACTIVITY_NOT_ACTIVE")
    if activity.kind == ActivityKind.ACTION and activity.data["category"] == ActionCategory.SOCIAL:
        raise BusinessRuleError("SOCIAL_ACTIVITY_CANNOT_PAUSE")
    changed = activity.model_copy(update={"status": ActivityStatus.PAUSED, "boundary_type": "paused",
                                         "next_interaction_at": None, "paused_at": now}, deep=True)
    return agent.model_copy(deep=True), changed


def _resume(agent: AgentState, activity: ActivityState, now: int) -> tuple[AgentState, ActivityState]:
    """@brief 恢复系统活动保留余量；动作随后由执行器读同一内容最新全局位置。"""
    if activity.status != ActivityStatus.PAUSED:
        raise BusinessRuleError("ACTIVITY_NOT_PAUSED")
    remaining = activity.planned_duration_seconds - activity.elapsed_seconds
    changed = activity.model_copy(update={"status": ActivityStatus.ACTIVE, "last_settled_at": now,
        "planned_duration_seconds": activity.elapsed_seconds + remaining, "planned_end_at": now + remaining,
        "boundary_type": None, "report_anchor_at": now, "paused_at": None}, deep=True)
    return agent.model_copy(deep=True), changed


def _cancel(agent: AgentState, activity: ActivityState) -> tuple[AgentState, ActivityState]:
    """@brief 退出保留此前真实结算的内容位置。"""
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        raise BusinessRuleError("ACTIVITY_ALREADY_TERMINAL")
    changed = activity.model_copy(update={"status": ActivityStatus.CANCELLED, "ended_at": activity.last_settled_at,
        "boundary_type": "cancelled", "next_interaction_at": None, "interruption_reason": "agent_cancelled"}, deep=True)
    return agent.model_copy(deep=True), changed


def at_decision_boundary(activity: ActivityState, now: int) -> bool:
    """@brief 统一动作随每次决策可结束，非社交动作还可暂停。"""
    return (activity.status == ActivityStatus.ACTIVE
            and (activity.kind in FEEDBACK_ACTIVITY_KINDS or activity.kind == ActivityKind.ACTION)
            and now >= activity.last_settled_at)


def _require_decision_boundary(agent: AgentState, activity: ActivityState, now: int) -> None:
    """@brief 控制操作必须归本人且活动已经结算到当前决策。"""
    if activity.agent_id != agent.agent_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if not at_decision_boundary(activity, now):
        raise BusinessRuleError("ACTIVITY_FEEDBACK_NOT_DUE")
