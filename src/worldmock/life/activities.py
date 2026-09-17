"""@brief 管理有时长活动的资源占用、进度、边界和中断。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from worldmock.catalog.affordances import default_affordances
from worldmock.errors import BusinessRuleError
from worldmock.life import felt
from worldmock.life.body import (
    ENERGY_RECOVERY_ACTIVITY_KINDS,
    ActivityFactors,
    BodySettlement,
    minimum_energy_cost,
    settle_body,
)
from worldmock.life.models import (
    ActivityChange,
    ActivityKind,
    ActivityState,
    ActivityStatus,
    AgentState,
    ChangeActivityRequest,
    ContinueActivityRequest,
    StartActivityRequest,
)
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


#: 到期反馈边界闭集（7 活动）——自可供性注册表 kinds.*.feedback_boundary 派生
#: （M1：字面常量已删，注册表即事实）。
FEEDBACK_ACTIVITY_KINDS = frozenset(
    ActivityKind(name) for name in default_affordances().feedback_kinds)

#: 可供性注册表视图（冻结文件加载一次；资源锁/缓解通道/MET 模板查表用）。
_AFFORDANCES = default_affordances()

#: 生活活动 effects 娱乐缓解的每小时尺度（experiment_assumption：锚定目录既有
#: 娱乐缓解费率 watch 0.3 / play_game 0.4，effects 权重 1.0 的生活活动对标
#: 0.5/小时；规格 L1600 要求生活活动具备 relief 通道）。
LIFESTYLE_ENTERTAINMENT_SCALE_PER_HOUR = 0.5
#: 生活活动 effects 社交缓解的每小时尺度（experiment_assumption：锚定 felt
#: 账本交谈 0.5/小时上限，effects.social=1.0 的社交型活动对标半场交谈）。
LIFESTYLE_SOCIAL_SCALE_PER_HOUR = 0.5
#: 生活活动 effects 健康效应的每小时情绪脉冲幅度（experiment_assumption：
#: 锚定 felt.MOOD_PULSES 的 message_received +0.1；健康无独立需要池，落
#: 情绪通道——规格 L1521 情绪不改变动作可行性）。
LIFESTYLE_HEALTH_MOOD_PER_HOUR = 0.1


@lru_cache(maxsize=1)
def lifestyle_effects() -> Mapping[str, Mapping[str, float]]:
    """@brief 生活活动目录的 effects 表（activity_id -> 各需要池效应权重）。

    读取 corpora/lifestyle/items.json 的 v2 字段（A10：location_kinds/
    time_window/needs_partner/effects）；目录条目由准入检查保证逐条携带
    effects，此处只做形状防御（缺 effects 的行按全零处理）。
    """
    root = Path(__file__).resolve().parents[3] / "corpora"
    payload = json.loads((root / "lifestyle/items.json").read_text(encoding="utf-8"))
    return {row["activity_id"]: {key: float(value) for key, value in
                             (row.get("effects") or {}).items()}
            for row in payload["items"]}


def lifestyle_relief_rates(activity_id: str) -> tuple[float, float, float]:
    """@brief 条目 effects 折算的 (娱乐缓解/小时, 社交缓解/小时, 健康情绪幅度/小时)。

    娱乐缓解走既有结算分录通道（settle_body 的 relief 因子）；社交与健康在
    活动推进时直接作用于身体（与 chat/call 的账本外直接落账同构）。
    """
    effects = lifestyle_effects().get(activity_id, {})
    return (effects.get("entertainment", 0.0) * LIFESTYLE_ENTERTAINMENT_SCALE_PER_HOUR,
            effects.get("social", 0.0) * LIFESTYLE_SOCIAL_SCALE_PER_HOUR,
            effects.get("health", 0.0) * LIFESTYLE_HEALTH_MOOD_PER_HOUR)


def start_activity(agent: AgentState, request: StartActivityRequest, now: int, origin: ActivityOrigin,
                   config: BodyConfig) -> tuple[AgentState, ActivityState]:
    """@brief 受理一个主活动，并拒绝资源冲突或不可完成能量预算。"""
    if request.planned_duration_seconds < 60:
        raise BusinessRuleError("ACTIVITY_TOO_SHORT")
    if agent.current_activity_id is not None:
        raise BusinessRuleError("RESOURCE_CONFLICT")
    met = _activity_met(request.activity_type, request.travel_mode, config, request.target_id)
    cost = minimum_energy_cost(request.activity_type, request.planned_duration_seconds, config, met)
    if cost > agent.body.energy_kcal and request.activity_type not in ENERGY_RECOVERY_ACTIVITY_KINDS:
        raise BusinessRuleError("INSUFFICIENT_ENERGY")
    activity_id = f"activity-{origin.action_id}"
    activity = ActivityState(
        activity_id=activity_id,
        agent_id=agent.agent_id,
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
        playback_speed=request.playback_speed,
        resource_claims=resource_claims(request),
        origin_action_id=origin.action_id,
        origin_decision_id=origin.decision_id,
        origin_request_event_id=origin.request_event_id,
    )
    return agent.model_copy(update={"current_activity_id": activity_id}, deep=True), activity


def change_activity(agent: AgentState, activity: ActivityState, request: ChangeActivityRequest,
                    now: int, feedback_seconds: int) -> tuple[AgentState, ActivityState]:
    """@brief 对暂停、恢复、退出和倍速变化执行显式状态转换。"""
    if activity.agent_id != agent.agent_id or activity.activity_id != request.activity_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if request.operation == ActivityChange.PAUSE:
        _require_decision_boundary(agent, activity, now)
        return _pause(agent, activity)
    if request.operation == ActivityChange.RESUME:
        resumed_agent, resumed = _resume(agent, activity, now)
        return resumed_agent, schedule_feedback(resumed, now, feedback_seconds)
    if request.operation == ActivityChange.CANCEL:
        if activity.status == ActivityStatus.ACTIVE:
            _require_decision_boundary(agent, activity, now)
        return _cancel(agent, activity)
    if activity.kind != ActivityKind.WATCH or activity.status != ActivityStatus.ACTIVE:
        raise BusinessRuleError("PLAYBACK_SPEED_NOT_ALLOWED")
    _require_decision_boundary(agent, activity, now)
    changed = activity.model_copy(update={"playback_speed": request.playback_speed}, deep=True)
    return agent.model_copy(deep=True), schedule_feedback(changed, now, feedback_seconds)


def continue_activity(agent: AgentState, activity: ActivityState, request: ContinueActivityRequest,
                      now: int, feedback_seconds: int) -> tuple[AgentState, ActivityState]:
    """@brief 在到期反馈边界保留资源槽并安排下一次反馈。"""
    if activity.activity_id != request.activity_id or activity.agent_id != agent.agent_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    _require_feedback_boundary(agent, activity, now)
    return agent.model_copy(deep=True), schedule_feedback(activity, now, feedback_seconds)


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
    current = None if completed else agent.current_activity_id
    updated_agent = agent.model_copy(update={"body": settlement.after, "current_activity_id": current}, deep=True)
    updated_agent = _apply_lifestyle_felt_relief(updated_agent, activity, seconds)
    return ActivityAdvance(updated_activity, updated_agent, settlement, completed)


def _apply_lifestyle_felt_relief(agent: AgentState, activity: ActivityState,
                                 seconds: int) -> AgentState:
    """@brief 生活活动 effects 的社交缓解与健康情绪脉冲（L1600）。

    娱乐缓解在 _entertainment_relief 走既有分录通道（有事件留痕）；社交
    需要不是分录资源（与 chat/call 同口径直接落账），健康无需要池、以正向
    情绪脉冲承载。幅度按实际推进小时数线性累积。
    """
    if activity.kind != ActivityKind.LIFESTYLE or seconds <= 0:
        return agent
    _entertainment_rate, social_rate, health_rate = lifestyle_relief_rates(
        activity.target_id or "")
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
    changed_agent = agent.model_copy(update={"current_activity_id": None}, deep=True)
    return changed, changed_agent


def resource_claims(request: StartActivityRequest) -> set[str]:
    """@brief 返回活动的明确资源锁集合（注册表 kinds.*.resource_claims 派生查表；
    position 恒占，travel 的 driving 条件锁由 conditional 声明表达）。"""
    return set(_AFFORDANCES.resource_claims(
        request.activity_type.value,
        travel_mode=request.travel_mode.value if request.travel_mode is not None else None))


def activity_factors(activity: ActivityState, agent: AgentState, config: BodyConfig) -> ActivityFactors:
    """@brief 从方式和人物内容偏好形成活动结算系数。"""
    met = _activity_met(activity.kind, activity.travel_mode, config, activity.target_id)
    preference = agent.entertainment_preferences.get(activity.target_id or "", 0)
    relief = _entertainment_relief(activity, preference, config)
    skill, tool = _work_factors(activity, agent, config)
    food = _food_energy_rate(activity)
    return ActivityFactors(met, relief, skill, tool, food)


def _entertainment_relief(activity: ActivityState, preference: float, config: BodyConfig) -> float:
    """@brief 按注册表缓解通道（kinds.*.relief.channel 四值）计算娱乐缓解速率。

    intrinsic：wait 的被动缓解（边等边刷）是活动类内在语义：settle 阶段在
    body.py 的 wait 分支直接按目录费率结算，不走偏好 gating 的 factors
    通道，避免与内容偏好相乘归零或双重计入。content_rate：watch 按条目
    费率/偏好/接触次数。preference_rate/none：偏好 x 目录费率（无费率
    键的活动得 0）。lifestyle 在通道为 none 时按条目 effects 折算缓解
    （修订合同 L1600：生活活动的 effects 真正缓解需要池；注册表通道仍为
    none——effects 是条目级数据，不在目录活动费率表内）。
    """
    if (activity.kind == ActivityKind.LIFESTYLE
            and _AFFORDANCES.relief_channel(activity.kind.value) == "none"):
        return lifestyle_relief_rates(activity.target_id or "")[0]
    channel = _AFFORDANCES.relief_channel(activity.kind.value)
    if channel == "intrinsic":
        return 0
    if channel == "content_rate":
        rate = activity.data.get("entertainment_relief_per_hour")
        count = activity.data.get("exposure_count")
        if not isinstance(rate, (int, float)) or not isinstance(count, int) or count < 1:
            raise ValueError("watch activity entertainment evidence is incomplete")
        return float(rate) * preference / count
    base = config.entertainment_relief_per_hour.get(activity.kind.value, 0)
    return preference * base


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
    if not isinstance(value, (int, float)) or value < 0:
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
    if activity.kind == ActivityKind.WATCH:
        media += settlement.duration_seconds * activity.playback_speed
    updates = {"last_settled_at": stop, "elapsed_seconds": elapsed, "effective_work_seconds": work,
               "media_position_seconds": media}
    if completed:
        updates.update(status=ActivityStatus.COMPLETED, boundary_type="planned_end", ended_at=stop,
                       next_interaction_at=None)
    elif activity.next_interaction_at == stop:
        boundary = "game_feedback" if activity.kind == ActivityKind.PLAY_GAME else "feedback"
        updates.update(boundary_type=boundary)
    return activity.model_copy(update=updates, deep=True)


def _pause(agent: AgentState, activity: ActivityState) -> tuple[AgentState, ActivityState]:
    """@brief 暂停活动并释放当前主活动槽位。"""
    if activity.status != ActivityStatus.ACTIVE or agent.current_activity_id != activity.activity_id:
        raise BusinessRuleError("ACTIVITY_NOT_ACTIVE")
    return (agent.model_copy(update={"current_activity_id": None}, deep=True),
            activity.model_copy(update={"status": ActivityStatus.PAUSED,
                                        "boundary_type": "paused",
                                        "next_interaction_at": None}, deep=True))


def _resume(agent: AgentState, activity: ActivityState, now: int) -> tuple[AgentState, ActivityState]:
    """@brief 从保留进度恢复活动并形成新的结束边界。"""
    if activity.status != ActivityStatus.PAUSED:
        raise BusinessRuleError("ACTIVITY_NOT_PAUSED")
    if agent.current_activity_id is not None:
        raise BusinessRuleError("RESOURCE_CONFLICT")
    remaining = activity.planned_duration_seconds - activity.elapsed_seconds
    changed = activity.model_copy(update={"status": ActivityStatus.ACTIVE, "last_settled_at": now,
                                          "planned_end_at": now + remaining,
                                          "boundary_type": None}, deep=True)
    return agent.model_copy(update={"current_activity_id": activity.activity_id}, deep=True), changed


def _cancel(agent: AgentState, activity: ActivityState) -> tuple[AgentState, ActivityState]:
    """@brief 退出活动并保留已结算进度。"""
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        raise BusinessRuleError("ACTIVITY_ALREADY_TERMINAL")
    changed = activity.model_copy(update={"status": ActivityStatus.CANCELLED,
                                          "ended_at": activity.last_settled_at,
                                          "boundary_type": "cancelled",
                                          "next_interaction_at": None,
                                          "interruption_reason": "agent_cancelled"}, deep=True)
    current = None if agent.current_activity_id == activity.activity_id else agent.current_activity_id
    return agent.model_copy(update={"current_activity_id": current}, deep=True), changed


def _require_feedback_boundary(agent: AgentState, activity: ActivityState, now: int) -> None:
    """@brief 要求活动属于人物且当前正处于显式反馈边界。"""
    if activity.agent_id != agent.agent_id or agent.current_activity_id != activity.activity_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if (activity.status != ActivityStatus.ACTIVE or activity.kind not in FEEDBACK_ACTIVITY_KINDS
            or activity.next_interaction_at != now or activity.boundary_type != "feedback"):
        raise BusinessRuleError("ACTIVITY_FEEDBACK_NOT_DUE")


def _require_decision_boundary(agent: AgentState, activity: ActivityState, now: int) -> None:
    """@brief 要求活动正处于通用反馈或游戏交互边界。"""
    if activity.kind == ActivityKind.PLAY_GAME:
        if (activity.agent_id != agent.agent_id or agent.current_activity_id != activity.activity_id
                or activity.status != ActivityStatus.ACTIVE or activity.next_interaction_at != now
                or activity.boundary_type != "game_feedback"):
            raise BusinessRuleError("ACTIVITY_FEEDBACK_NOT_DUE")
        return
    _require_feedback_boundary(agent, activity, now)
