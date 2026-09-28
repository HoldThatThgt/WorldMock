"""@brief 管理有时长活动的资源占用、进度、边界和中断。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from worldmock.catalog.affordances import default_affordances
from worldmock.errors import BusinessRuleError
from worldmock.life import felt
from worldmock.life.body import (
    ActivityFactors,
    BodySettlement,
    settle_body,
)
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
    ActivityKind(name) for name in default_affordances().feedback_kinds)

#: 可供性注册表视图（冻结文件加载一次；资源锁/缓解通道/MET 模板查表用）。
_AFFORDANCES = default_affordances()

#: 生活活动 effects 娱乐缓解的每小时尺度（experiment_assumption：锚定目录既有
#: 娱乐缓解费率 watch 0.3 / play_game 0.4，effects 权重 1.0 的生活活动对标
#: 0.5/小时；docs/dev/SPEC-life-realism.md「动机层」要求生活活动具备 relief 通道）。
LIFESTYLE_ENTERTAINMENT_SCALE_PER_HOUR = 0.5
#: 生活活动 effects 社交缓解的每小时尺度（experiment_assumption：锚定 felt
#: 账本交谈 0.5/小时上限，effects.social=1.0 的社交型活动对标半场交谈）。
LIFESTYLE_SOCIAL_SCALE_PER_HOUR = 0.5
#: 生活活动 effects 健康效应的每小时情绪脉冲幅度（experiment_assumption：
#: 锚定 felt.MOOD_PULSES 的 message_received +0.1；健康无独立需要池，落
#: 情绪通道——docs/dev/SPEC-life-runtime.md「其他体感分级」情绪不改变动作可行性）。
LIFESTYLE_HEALTH_MOOD_PER_HOUR = 0.1


#: 语料根目录（仓库内 corpora/），地点类声明两侧共用同一读法。
_CORPORA_ROOT = Path(__file__).resolve().parents[3] / "corpora"
#: 生活活动语料相对路径（effects 与地点类声明同源）。
LIFESTYLE_ITEMS_RELATIVE = "lifestyle/items.json"


@lru_cache(maxsize=1)
def lifestyle_effects() -> Mapping[str, Mapping[str, float]]:
    """@brief 生活活动目录的 effects 表（activity_id -> 各需要池效应权重）。

    读取 corpora/lifestyle/items.json 的 v2 字段（A10：location_kinds/
    time_window/needs_partner/effects）；目录条目由准入检查保证逐条携带
    effects，此处只做形状防御（缺 effects 的行按全零处理）。
    """
    payload = json.loads((_CORPORA_ROOT / LIFESTYLE_ITEMS_RELATIVE).read_text(encoding="utf-8"))
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
    """@brief 受理一个主活动（最短 60 秒）；能量不参与受理（docs/dev/SPEC-time-and-energy.md「能量不拦人」）。"""
    if request.planned_duration_seconds < 60:
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
        playback_speed=request.playback_speed,
        resource_claims=resource_claims(request),
        origin_action_id=origin.action_id,
        origin_decision_id=origin.decision_id,
        origin_request_event_id=origin.request_event_id,
        report_anchor_at=now,
    )
    return agent.model_copy(deep=True), activity


def change_activity(agent: AgentState, activity: ActivityState, request: ChangeActivityRequest,
                    now: int, feedback_seconds: int) -> tuple[AgentState, ActivityState]:
    """@brief 对暂停、恢复、退出和倍速变化执行显式状态转换。"""
    if activity.agent_id != agent.agent_id or activity.activity_id != request.activity_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if request.operation == ActivityChange.PAUSE:
        _require_decision_boundary(agent, activity, now)
        return _pause(agent, activity, now)
    if request.operation == ActivityChange.RESUME:
        resumed_agent, resumed = _resume(agent, activity, now, request.resume_cap_seconds)
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
    """@brief 当日目标提醒到点结束睡眠（docs/dev/SPEC-time-and-energy.md 第四章「计划与提醒」）。

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
    """@brief 生活活动 effects 的社交缓解与健康情绪脉冲（docs/dev/SPEC-life-realism.md「动机层」）。

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
    return changed, agent.model_copy(deep=True)


def resource_claims(request: ActivityStartParams, content_kind: str | None = None) -> set[str]:
    """@brief 从注册表取得活动显式占用及条件占用（驾车按出行方式，观看按内容品类：播客只占 ears）。

    :param content_kind: 观看内容的品类（rules.validate_start 从场景内容取得并写入 activity.data）；其余活动为 None。
    """
    return set(_AFFORDANCES.resource_claims(
        request.activity_type.value,
        travel_mode=request.travel_mode.value if request.travel_mode is not None else None,
        content_kind=content_kind))


def shared_activity_factors(activity: ActivityState, agent: AgentState,
                            config: BodyConfig) -> ActivityFactors:
    """@brief 共同身体结算与边界预测共用的单项系数：交谈、通话取社交常量 MET，其余复用活动声明系数。"""
    from worldmock.life.social_actions import CALL_MET, CHAT_MET

    if activity.kind == ActivityKind.CHAT:
        return ActivityFactors(CHAT_MET)
    if activity.kind == ActivityKind.CALL:
        return ActivityFactors(CALL_MET)
    return activity_factors(activity, agent, config)


def activity_factors(activity: ActivityState, agent: AgentState, config: BodyConfig) -> ActivityFactors:
    """@brief 从方式和人物内容偏好形成活动结算系数。"""
    met = _activity_met(activity.kind, activity.travel_mode, config, activity.target_id)
    preference = agent.taste_weights.get(activity.target_id or "", 0)
    relief = _entertainment_relief(activity, preference, config)
    skill, tool = _work_factors(activity, agent, config)
    food = _food_energy_rate(activity)
    return ActivityFactors(met, relief, skill, tool, food)


def _entertainment_relief(activity: ActivityState, preference: float, config: BodyConfig) -> float:
    """@brief 按注册表缓解通道（kinds.*.relief.channel 四值）计算娱乐缓解速率。

    content_rate：watch 按条目费率、偏好和接触次数。preference_rate/none：
    偏好 x 目录费率（无费率
    键的活动得 0）。lifestyle 在通道为 none 时按条目 effects 折算缓解
    （docs/dev/SPEC-life-realism.md「动机层」：生活活动的 effects 真正缓解需要池；注册表通道仍为
    none——effects 是条目级数据，不在目录活动费率表内）。
    """
    if (activity.kind == ActivityKind.LIFESTYLE
            and _AFFORDANCES.relief_channel(activity.kind.value) == "none"):
        return lifestyle_relief_rates(activity.target_id or "")[0]
    channel = _AFFORDANCES.relief_channel(activity.kind.value)
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


def _pause(agent: AgentState, activity: ActivityState, now: int) -> tuple[AgentState, ActivityState]:
    """@brief 暂停指定活动并释放其显式资源；写入暂停时刻 paused_at（待办层按它排序）。"""
    if activity.status != ActivityStatus.ACTIVE:
        raise BusinessRuleError("ACTIVITY_NOT_ACTIVE")
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

    工具层的暂停与结束可用性与 _require_decision_boundary 共用这一判定（docs/dev/SPEC-persona-prompt.md「工具层」）。
    """
    if activity.status != ActivityStatus.ACTIVE:
        return False
    if activity.kind == ActivityKind.PLAY_GAME:
        return activity.next_interaction_at == now and activity.boundary_type == "game_feedback"
    return activity.kind in FEEDBACK_ACTIVITY_KINDS and now >= activity.last_settled_at


def _require_decision_boundary(agent: AgentState, activity: ActivityState, now: int) -> None:
    """@brief 要求活动正处于通用反馈或游戏交互边界。"""
    if activity.agent_id != agent.agent_id:
        raise BusinessRuleError("ACTIVITY_NOT_OWNED")
    if not at_decision_boundary(activity, now):
        raise BusinessRuleError("ACTIVITY_FEEDBACK_NOT_DUE")
