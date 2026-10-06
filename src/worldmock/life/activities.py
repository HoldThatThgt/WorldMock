"""@brief 管理有时长活动的资源占用、进度、边界和中断。"""

from __future__ import annotations
from worldmock.life.social import CALL_MET, CHAT_MET

import logging
import math
from dataclasses import dataclass
from typing import Mapping

from worldmock.catalog.affordances import engine_affordances
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life import felt
from worldmock.life.body import (
    ActivityFactors,
    BodySettlement,
    settle_body,
)
from worldmock.exchange.constants import CONTENT_RELIEF_PER_HOUR
from worldmock.exchange.types import ContentKind
from worldmock.life.content import store_app_progress, validate_lifestyle_effects
from worldmock.life.models import (
    ActivityChange,
    ActivityKind,
    ActivityState,
    ActivityStatus,
    AgentState,
    ActivityStartParams,
    ChangeActivityRequest,
)
from worldmock.life.retrieval import feasible_kinds
from worldmock.life.scenario import BodyConfig


@dataclass(frozen=True, slots=True)
class ActivityAdvance:
    """@brief 一次活动推进产生的新状态与证据。"""

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


#: 到期反馈边界闭集（含生活活动）——自可供性注册表 kinds.*.feedback_boundary 派生
#: （M1：字面常量已删，注册表即事实）。
FEEDBACK_ACTIVITY_KINDS = frozenset(
    ActivityKind(name) for name in engine_affordances().feedback_kinds if name in ActivityKind._value2member_map_)

#: 可供性注册表视图（冻结文件加载一次；资源锁/缓解通道/MET 模板查表用）。
_AFFORDANCES = engine_affordances()

#: 生活活动 effects 娱乐缓解的每小时尺度（experiment_assumption：锚定目录既有
#: 娱乐缓解费率 watch 0.3 / play_game 0.4，effects 权重 1.0 的生活活动对标
#: 0.5/小时；docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」要求生活活动具备 relief 通道）。
LIFESTYLE_ENTERTAINMENT_SCALE_PER_HOUR = 0.5
#: 生活活动 effects 社交缓解的每小时尺度（experiment_assumption：锚定 felt
#: 账本交谈 0.5/小时上限，effects.social=1.0 的社交型活动对标半场交谈）。
LIFESTYLE_SOCIAL_SCALE_PER_HOUR = 0.5
#: 生活活动 effects 健康效应的每小时情绪脉冲幅度（experiment_assumption：
#: 锚定 felt.MOOD_PULSES 的 message_received +0.1；健康无独立需要池，落
#: 情绪通道——docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」情绪不改变动作可行性）。
LIFESTYLE_HEALTH_MOOD_PER_HOUR = 0.1


def lifestyle_relief_rates(effects: Mapping[str, float]) -> tuple[float, float, float]:
    """@brief 把活动的冻结效果转换为娱乐、社交与健康的每小时结算量。"""
    validate_lifestyle_effects(effects)
    return (effects["entertainment"] * LIFESTYLE_ENTERTAINMENT_SCALE_PER_HOUR,
            effects["social"] * LIFESTYLE_SOCIAL_SCALE_PER_HOUR,
            effects["health"] * LIFESTYLE_HEALTH_MOOD_PER_HOUR)


def _frozen_lifestyle_effects(activity: ActivityState) -> Mapping[str, float]:
    """@brief 只读取活动启动时冻结的效果，缺失时明确终止。"""
    try:
        return activity.data["effects"]
    except KeyError as error:
        logging.error("Lifestyle activity has no frozen effects: %s", activity.activity_id)
        raise IntegrityError("Lifestyle activity has no frozen effects") from error


def location_kinds_allow(place_location_kinds: frozenset[str], needed: frozenset[str]) -> bool:
    """@brief 活动环境资格：地点有显式环境、活动有声明且两者相容（受理与工具层可用性共用）。"""
    return bool(place_location_kinds and needed and feasible_kinds(place_location_kinds, needed))


def require_location_kinds(place_location_kinds: frozenset[str], needed: frozenset[str]) -> None:
    """@brief 仅使用当前冻结地图和活动的显式声明判断活动环境资格。"""
    if location_kinds_allow(place_location_kinds, needed):
        return
    logging.getLogger(__name__).error("Activity location kinds do not satisfy the declared requirement")
    raise BusinessRuleError("ELIGIBILITY_LOCATION_KIND_MISMATCH")


def start_activity(agent: AgentState, request: ActivityStartParams, now: int, origin: ActivityOrigin,
                   config: BodyConfig) -> tuple[AgentState, ActivityState]:
    """@brief 出行最短一秒，其他主活动最短一分钟；能量不参与受理。"""
    minimum_seconds = 1 if request.activity_type == ActivityKind.TRAVEL else 60
    if request.planned_duration_seconds < minimum_seconds:
        raise BusinessRuleError("ACTIVITY_TOO_SHORT")
    suffix = f":{origin.activity_ordinal:04d}" if origin.activity_ordinal > 1 else ""
    activity_id = f"activity-{origin.action_id}{suffix}"
    activity = ActivityState(
        activity_id=activity_id,
        agent_id=agent.agent_id,
        location_id=agent.location_id,
        kind=request.activity_type,
        status=ActivityStatus.ACTIVE,
        started_at=now,
        planned_end_at=now + request.planned_duration_seconds,
        last_settled_at=now,
        elapsed_seconds=0,
        planned_duration_seconds=request.planned_duration_seconds,
        target_id=request.target_id,
        destination_id=request.destination_id,
        travel_mode=request.travel_mode,
        resource_claims=resource_claims(request),
        origin_action_id=origin.action_id,
        origin_decision_id=origin.decision_id,
        origin_request_event_id=origin.request_event_id,
        report_anchor_at=now,
    )
    return agent.model_copy(deep=True), activity


def change_activity(agent: AgentState, activity: ActivityState, request: ChangeActivityRequest,
                    now: int, feedback_seconds: int) -> tuple[AgentState, ActivityState]:
    """@brief 对暂停、恢复和退出执行显式状态转换。"""
    if activity.agent_id != agent.agent_id or activity.activity_id != request.activity_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if request.operation == ActivityChange.PAUSE:
        _require_decision_boundary(agent, activity, now)
        return _pause(agent, activity, now)
    if request.operation == ActivityChange.RESUME:
        resumed_agent, resumed = _resume(agent, activity, now, request.resume_cap_seconds)
        return resumed_agent, schedule_feedback(resumed, now, feedback_seconds)
    if activity.status == ActivityStatus.ACTIVE:
        _require_decision_boundary(agent, activity, now)
    return _cancel(agent, activity)


def schedule_feedback(activity: ActivityState, now: int, feedback_seconds: int) -> ActivityState:
    """@brief 为支持反馈的活动设置严格未来且早于完成的决策边界。"""
    if activity.status != ActivityStatus.ACTIVE or activity.kind not in FEEDBACK_ACTIVITY_KINDS:
        return activity.model_copy(deep=True)
    candidate = now + feedback_seconds
    next_at = candidate if candidate < activity.planned_end_at else None
    return activity.model_copy(update={"next_interaction_at": next_at, "boundary_type": None}, deep=True)


def advance_activity(activity: ActivityState, agent: AgentState, now: int,
                     config: BodyConfig) -> ActivityAdvance:
    """@brief 将活动推进到当前边界，并按实际活动秒数结算身体。"""
    if activity.status != ActivityStatus.ACTIVE:
        return ActivityAdvance(activity.model_copy(deep=True), agent.model_copy(deep=True), None, False)
    stop = min(now, activity.planned_end_at)
    seconds = stop - activity.last_settled_at
    if seconds < 0:
        raise ValueError("activity settlement time moved backwards")
    factors = activity_factors(activity, agent, config)
    settlement = settle_body(agent.body, activity.kind, seconds, config, factors)
    elapsed = activity.elapsed_seconds + seconds
    completed = elapsed >= activity.planned_duration_seconds
    updated_activity = _advanced_activity(activity, stop, elapsed, settlement, completed)
    updated_agent = agent.model_copy(update={"body": settlement.after,
                                             "body_settled_at": stop}, deep=True)
    updated_agent = _apply_lifestyle_felt_relief(updated_agent, activity, seconds)
    return ActivityAdvance(updated_activity, updated_agent, settlement, completed)


def advance_activity_shared(activity: ActivityState, agent: AgentState, now: int,
                            settlement: BodySettlement) -> ActivityAdvance:
    """@brief 在已结算共同身体的前提下只推进本活动进度与专有效果。"""
    stop = min(now, activity.planned_end_at)
    seconds = stop - activity.last_settled_at
    if activity.status != ActivityStatus.ACTIVE or seconds < 0:
        raise ValueError("active activity settlement interval is invalid")
    elapsed = activity.elapsed_seconds + seconds
    completed = elapsed >= activity.planned_duration_seconds
    changed = _advanced_activity(activity, stop, elapsed, settlement, completed)
    changed_agent = _apply_lifestyle_felt_relief(agent, activity, seconds)
    return ActivityAdvance(changed, changed_agent, settlement, completed)


def end_sleep_by_reminder(advance: ActivityAdvance, now: int) -> ActivityAdvance:
    """@brief 当日目标提醒到点结束睡眠（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「计划与提醒」）。

    按实际时长结算后 status=completed，ended_at 为提醒时刻，boundary_type 与 interruption_reason 都记
    plan_reminder，睡眠随即成为待收尾。计划结束与提醒同秒时睡眠已按 planned_end 完成，保持不变；
    非睡眠活动不被提醒停止。
    """
    activity = advance.activity
    if advance.completed or activity.kind != ActivityKind.SLEEP:
        return advance
    ended = activity.model_copy(update={"status": ActivityStatus.COMPLETED, "ended_at": now,
                                        "boundary_type": "plan_reminder", "interruption_reason": "plan_reminder",
                                        "next_interaction_at": None}, deep=True)
    return ActivityAdvance(ended, advance.agent, advance.settlement, True)


def _apply_lifestyle_felt_relief(agent: AgentState, activity: ActivityState,
                                 seconds: int) -> AgentState:
    """@brief 生活活动 effects 的社交缓解与健康情绪脉冲（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」）。

    娱乐缓解在 entertainment_relief_rate 走既有分录通道（有事件留痕）；社交
    需要不是分录资源（与 chat/call 同口径直接落账），健康无需要池、以正向
    情绪脉冲承载。幅度按实际推进小时数线性累积。
    """
    if activity.kind != ActivityKind.LIFESTYLE or seconds <= 0:
        return agent
    _entertainment_rate, social_rate, health_rate = lifestyle_relief_rates(
        _frozen_lifestyle_effects(activity))
    if social_rate <= 0 and health_rate <= 0:
        return agent
    hours = seconds / 3600
    body = agent.body
    social = max(0.0, body.social_need - social_rate * hours)
    valence = felt.apply_mood_pulse(body.mood_valence, health_rate * hours,
                                    body.neuroticism_norm)
    return agent.model_copy(update={"body": body.model_copy(
        update={"social_need": social, "mood_valence": valence})}, deep=True)


def truncate_activity(activity: ActivityState, agent: AgentState, now: int) -> tuple[ActivityState, AgentState]:
    """@brief 在实验终点明确标记未完成活动为截断。"""
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        return activity, agent
    changed = activity.model_copy(update={"status": ActivityStatus.TRUNCATED, "ended_at": now,
                                          "boundary_type": "experiment_end",
                                          "interruption_reason": "experiment_end",
                                          "next_interaction_at": None}, deep=True)
    return changed, agent.model_copy(deep=True)


def resource_claims(request: ActivityStartParams) -> set[str]:
    """@brief 从注册表取得活动占用；驾车按出行方式声明额外占用。"""
    return set(_AFFORDANCES.resource_claims(
        request.activity_type.value,
        travel_mode=request.travel_mode.value if request.travel_mode is not None else None))


def shared_activity_factors(activity: ActivityState, agent: AgentState,
                            config: BodyConfig) -> ActivityFactors:
    """@brief 共同身体结算与边界预测共用的单项系数：交谈、通话取社交常量 MET，其余复用活动声明系数。"""

    if activity.kind == ActivityKind.CHAT:
        return ActivityFactors(CHAT_MET)
    if activity.kind == ActivityKind.CALL:
        return ActivityFactors(CALL_MET)
    return activity_factors(activity, agent, config)


def activity_factors(activity: ActivityState, agent: AgentState, config: BodyConfig) -> ActivityFactors:
    """@brief 从活动方式和当前内容类型形成结算系数。"""
    met = _activity_met(activity.kind, activity.travel_mode, config, activity.target_id)
    relief = entertainment_relief_rate(activity, config)
    skill, tool = _work_factors(activity, agent, config)
    food = _food_energy_rate(activity)
    return ActivityFactors(met, relief, skill, tool, food)


def entertainment_relief_rate(activity: ActivityState, config: BodyConfig) -> float:
    """@brief app 按当前内容类型固定缓解，生活活动按冻结效果缓解。"""
    if activity.kind == ActivityKind.USE_APP:
        return content_entertainment_relief_rate(activity.data["content_kind"])
    if activity.kind == ActivityKind.LIFESTYLE:
        return lifestyle_relief_rates(_frozen_lifestyle_effects(activity))[0]
    return 0.0


def content_entertainment_relief_rate(kind: str) -> float:
    """@brief 内容类型只读系统固定缓解常数，不引入口味或接触次数。"""
    return CONTENT_RELIEF_PER_HOUR[ContentKind(kind)]


def _work_factors(activity: ActivityState, agent: AgentState,
                  config: BodyConfig) -> tuple[float, float]:
    """@brief 核对活动冻结的所需技能和工作工具倍率。"""
    if activity.kind != ActivityKind.WORK:
        return 1, 1
    required = activity.data.get("required_skill")
    skill = activity.data.get("skill_level")
    multiplier = activity.data.get("work_notes_multiplier")
    actual = agent.skill_levels.get(required, 0) if isinstance(required, str) else None
    if not isinstance(skill, (int, float)) or skill < 0 or skill != actual:
        raise ValueError("work activity skill evidence is invalid")
    if (not isinstance(multiplier, (int, float)) or multiplier < 1
            or multiplier != config.work_notes_multiplier):
        raise ValueError("work activity tool multiplier evidence is invalid")
    tool = float(multiplier) if "work_notes" in agent.active_effects else 1
    return float(skill), tool


def _food_energy_rate(activity: ActivityState) -> float:
    """@brief 读取消费活动按当前食物进度形成的每小时净可用热量。"""
    if activity.kind != ActivityKind.CONSUME:
        return 0
    value = activity.data.get("food_energy_kcal_per_hour")
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        logging.error("Consume activity food energy rate is invalid: %s", activity.activity_id)
        raise ValueError("consume activity food energy rate is invalid")
    return float(value)


def _activity_met(kind: ActivityKind, travel_mode, config: BodyConfig,
                  target_id: str | None = None) -> float:
    """@brief 按注册表 met_key 模板查冻结代谢当量（travel:{mode} / lifestyle:{item_id}）。"""
    return config.activity_met[
        _AFFORDANCES.met_lookup_key(kind.value, travel_mode=travel_mode, target_id=target_id)]


def _advanced_activity(activity: ActivityState, stop: int, elapsed: int,
                       settlement: BodySettlement, completed: bool) -> ActivityState:
    """@brief 投影活动本身的累计进度。"""
    work = activity.effective_work_seconds
    media = activity.media_position_seconds
    if activity.kind == ActivityKind.WORK:
        work += settlement.duration_seconds * settlement.work_efficiency
    if activity.kind == ActivityKind.USE_APP:
        media += stop - activity.last_settled_at
    updates = {"last_settled_at": stop, "elapsed_seconds": elapsed, "effective_work_seconds": work,
               "media_position_seconds": media}
    if completed:
        updates.update(status=ActivityStatus.COMPLETED, boundary_type="planned_end", ended_at=stop,
                       next_interaction_at=None)
    elif activity.next_interaction_at == stop:
        updates.update(boundary_type=None if activity.kind == ActivityKind.USE_APP else "feedback")
    return activity.model_copy(update=updates, deep=True)


def _pause(agent: AgentState, activity: ActivityState, now: int) -> tuple[AgentState, ActivityState]:
    """@brief 暂停指定活动并释放其显式资源；写入暂停时刻 paused_at（待办层按它排序）。"""
    if activity.status != ActivityStatus.ACTIVE:
        raise BusinessRuleError("ACTIVITY_NOT_ACTIVE")
    if activity.kind == ActivityKind.USE_APP:
        agent = store_app_progress(agent, activity)
    return (agent.model_copy(deep=True),
            activity.model_copy(update={"status": ActivityStatus.PAUSED,
                                        "boundary_type": "paused",
                                        "next_interaction_at": None,
                                        "paused_at": now}, deep=True))


def _resume(agent: AgentState, activity: ActivityState, now: int,
            cap: int | None) -> tuple[AgentState, ActivityState]:
    """@brief 从保留进度恢复活动并形成新的结束边界；恢复即重置播报锚点并清空暂停时刻。

    有上限时剩余时长截到上限（工作不越过合同截止）。
    """
    if activity.status != ActivityStatus.PAUSED:
        raise BusinessRuleError("ACTIVITY_NOT_PAUSED")
    remaining = activity.planned_duration_seconds - activity.elapsed_seconds
    if cap is not None:
        remaining = min(remaining, cap)
    changed = activity.model_copy(update={"status": ActivityStatus.ACTIVE, "last_settled_at": now,
                                          "planned_duration_seconds": activity.elapsed_seconds + remaining,
                                          "planned_end_at": now + remaining,
                                          "boundary_type": None, "report_anchor_at": now,
                                          "paused_at": None}, deep=True)
    return agent.model_copy(deep=True), changed


def _cancel(agent: AgentState, activity: ActivityState) -> tuple[AgentState, ActivityState]:
    """@brief 退出活动并保留已结算进度。"""
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        raise BusinessRuleError("ACTIVITY_ALREADY_TERMINAL")
    changed = activity.model_copy(update={"status": ActivityStatus.CANCELLED,
                                          "ended_at": activity.last_settled_at,
                                          "boundary_type": "cancelled",
                                          "next_interaction_at": None,
                                          "interruption_reason": "agent_cancelled"}, deep=True)
    return agent.model_copy(deep=True), changed


def at_decision_boundary(activity: ActivityState, now: int) -> bool:
    """@brief 进行中的活动此刻能否暂停或结束：游戏须在交互边界，其余须是支持反馈的活动且已结算到此刻之前。

    工具层的暂停与结束可用性与 _require_decision_boundary 共用这一判定（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。
    """
    if activity.status != ActivityStatus.ACTIVE:
        return False
    return (activity.kind in FEEDBACK_ACTIVITY_KINDS or activity.kind == ActivityKind.USE_APP) and (
        now >= activity.last_settled_at)


def _require_decision_boundary(agent: AgentState, activity: ActivityState, now: int) -> None:
    """@brief 要求活动正处于通用反馈或游戏交互边界。"""
    if activity.agent_id != agent.agent_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if not at_decision_boundary(activity, now):
        raise BusinessRuleError("ACTIVITY_FEEDBACK_NOT_DUE")
