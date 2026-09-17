"""@brief 决策节律的引擎侧装配：唤醒判定、静默边界留痕与下一决策候选（L1447-1477）。

结算边界（activity_feedback_seconds=900）照常结算资源；是否提供决策只由唤醒
条件决定——判定委托 life.schedule.evaluate_wake_reasons，本模块负责从世界状态
装配 WakeSnapshot、在同一边界推进情绪账（脉冲与衰减）、计算下一决策候选
（schedule.next_decision_time）。静默边界在 activity_progress.data 留
decision_offered=false 与 wake_reasons=[]；提供决策的边界记录实际命中的全部
唤醒原因。滞回参照=上次实际提供决策时的分级（L1524-1525）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.footprints.models import DECISION_OFFERED_FIELD, WAKE_REASONS_FIELD
from worldmock.life import felt
from worldmock.life import schedule as rhythm_schedule
from worldmock.life.body import (
    apply_discomfort_pulse,
    apply_mood_pulse,
    apply_social_ledger,
    decay_mood,
    next_body_boundary_seconds,
)
from worldmock.life.models import ActivityKind, ActivityStatus, AgentState, WorldState
from worldmock.life.projection import felt_values
from worldmock.life.scenario import BodyConfig, RuntimeScenario

#: 饥饿不适档（L1518 连续两小时以上饿）：appetite 分级达到"饿"及以上。
HUNGRY_BANDS = frozenset({"饿", "很饿", "饿得难受"})
#: 困倦不适档：sleepiness 分级达到"困"及以上。
SLEEPY_BANDS = frozenset({"困", "很困", "撑不住"})

#: 分级文本 → 序数表（WakeSnapshot 以 int 比较分级，序数保序即可）。
_BAND_ORDER = ("不饿", "有点想吃", "饿", "很饿", "饿得难受", "清醒", "有点乏", "困",
               "很困", "撑不住", "不闷", "有点想放松", "很想放松", "不想找人",
               "想和人说说话", "有点孤单", "很烦", "有点烦", "平静", "挺好", "很开心")


@dataclass(frozen=True, slots=True)
class RhythmInputs:
    """@brief 一个结算边界的节律装配输入（引擎在 advance 边界装配）。"""

    state: WorldState
    scenario: RuntimeScenario
    body_config: BodyConfig
    #: 任务截止时刻表（task_id -> epoch 秒）。
    task_due: dict[str, int]
    #: 本结算间隔秒数（用于到期未交等区间穿越判定）。
    interval_seconds: int
    #: 距上一次实际决策的重估间隔（场景参数，缺省 5400）。
    reconsideration_interval_seconds: int
    #: 能量投影可穿透下界的恢复活动闭集（sleep/wait/consume）。
    recovery_kinds: frozenset
    #: 人物档案餐时窗口（agent_id -> MealWindows；滞回参照与观察同源）。
    meal_windows_by_agent: dict[int, felt.MealWindows] = field(default_factory=dict)
    #: 本 tick 由预排事件触发的唤醒旗标：agent_id -> {wake_reason 名}。
    arrival_flags: dict[int, set[str]] = field(default_factory=dict)
    #: 待注解的 activity_progress 草稿数据（agent_id -> draft.data）。
    progress_data: dict[int, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRhythm:
    """@brief 单个人物在本边界的节律判定结果。"""

    offered: bool
    wake_reasons: list[str]
    next_decision_at: int


def apply_decision_rhythm(inputs: RhythmInputs) -> tuple[WorldState, dict[int, AgentRhythm]]:
    """@brief 对每个人物执行边界节律：情绪账、唤醒判定与下一决策时刻。"""
    state = inputs.state
    rhythms: dict[int, AgentRhythm] = {}
    agents = {}
    for agent_id in sorted(state.agents):
        agent = state.agents[agent_id]
        agent = _settle_mood(agent, inputs)
        verdict = rhythm_schedule.evaluate_wake_reasons(_wake_snapshot(agent, inputs))
        if _game_turn_due(inputs.state, agent):
            # 游戏保持逐回合反馈（规格 L1473）：交互边界到期即提供决策，
            # 不经唤醒条件枚举；wake_reasons 记录实际命中的（通常为空）。
            verdict = rhythm_schedule.WakeVerdict(
                decision_offered=True, wake_reasons=verdict.wake_reasons)
        data = inputs.progress_data.get(agent_id)
        if data is not None:
            data[DECISION_OFFERED_FIELD] = verdict.decision_offered
            data[WAKE_REASONS_FIELD] = [reason.value for reason in verdict.wake_reasons]
        offered = verdict.decision_offered
        reasons = [reason.value for reason in verdict.wake_reasons]
        if offered:
            next_at = state.current_time
        else:
            candidate = next_decision_candidates(agent, inputs)
            next_at = candidate if candidate is not None else (
                state.current_time + max(1, inputs.interval_seconds))
        agent = agent.model_copy(update={"next_decision_at": next_at,
                                         "pending_wake_reasons": reasons if offered else []},
                                 deep=True)
        agents[agent_id] = agent
        rhythms[agent_id] = AgentRhythm(offered, reasons, next_at)
    return state.model_copy(update={"agents": agents}, deep=True), rhythms


def next_decision_candidates(agent: AgentState, inputs: RhythmInputs) -> int | None:
    """@brief 计算某人物的下一决策时刻（schedule.next_decision_time 候选装配）。"""
    state = inputs.state
    activity = state.activities.get(agent.current_activity_id or "")
    active = activity is not None and activity.status == ActivityStatus.ACTIVE
    felt_crossings: list[int] = []
    if active and activity.kind in {ActivityKind.CHAT, ActivityKind.CALL}:
        felt_crossings.append(activity.planned_end_at)
    elif active:
        from worldmock.life.activities import activity_factors

        hour = local_hour(state.current_time, inputs.scenario.timezone)
        factors = activity_factors(activity, agent, inputs.body_config)
        seconds = next_body_boundary_seconds(agent.body, activity.kind,
                                             inputs.body_config, factors, hour)
        if seconds is not None:
            felt_crossings.append(state.current_time + seconds)
    arrivals = [item.due_at for item in state.pending_events
                if item.due_at > state.current_time and item.agent_id == agent.agent_id]
    plan_starts = []
    if agent.day_plan is not None:
        plan_starts = [segment.start for segment in agent.day_plan.segments
                       if segment.start > state.current_time]
    return rhythm_schedule.next_decision_time(rhythm_schedule.NextDecisionQuery(
        now=state.current_time,
        planned_end=activity.planned_end_at if active else None,
        felt_band_crossing_times=tuple(felt_crossings),
        scheduled_arrival_times=tuple(arrivals),
        plan_block_starts=tuple(plan_starts),
        last_decision_time=agent.last_decision_time,
        reconsideration_interval_seconds=inputs.reconsideration_interval_seconds,
        location_hours_change_times=tuple(_location_change_times(agent, inputs)),
        is_asleep=active and activity.kind == ActivityKind.SLEEP,
    ))


def _settle_mood(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 边界情绪账：先按半衰期衰减，再叠加本区间事件脉冲（L1517-1519）。"""
    now = inputs.state.current_time
    if agent.mood_updated_at is not None:
        agent = agent.model_copy(update={
            "body": decay_mood(agent.body, (now - agent.mood_updated_at) / 60)}, deep=True)
    agent = _read_new_messages(agent)
    agent = _apply_awake_social_ledger(agent, inputs)
    levels = felt.summarize_felt_levels(
        **felt_values(agent, local_hour(now, inputs.scenario.timezone),
                      inputs.meal_windows_by_agent.get(agent.agent_id)))
    uncomfortable = levels["appetite"] in HUNGRY_BANDS or levels["sleepiness"] in SLEEPY_BANDS
    if uncomfortable:
        started = agent.discomfort_started_at if agent.discomfort_started_at is not None else now
        chargeable = max(0.0, (now - started) / 3600 - felt.MOOD_DISCOMFORT_TRIGGER_HOURS)
        new_charge = max(0.0, chargeable - agent.discomfort_pulse_hours)
        if new_charge > 0:
            agent = agent.model_copy(update={
                "body": apply_discomfort_pulse(agent.body, new_charge),
                "discomfort_pulse_hours": agent.discomfort_pulse_hours + new_charge}, deep=True)
        agent = agent.model_copy(update={"discomfort_started_at": started}, deep=True)
    else:
        agent = agent.model_copy(update={"discomfort_started_at": None,
                                         "discomfort_pulse_hours": 0.0}, deep=True)
    agent = _plan_and_deadline_pulses(agent, inputs)
    return agent.model_copy(update={"mood_updated_at": now}, deep=True)


def _read_new_messages(agent: AgentState) -> AgentState:
    """@brief 新收件箱消息计为已读：社交需要 −0.05/条 + 情绪 +0.1/条（L1513/L1518）。"""
    fresh = [message for message_id, message in agent.inbox.items()
             if message_id not in agent.read_inbox_ids]
    if not fresh:
        return agent
    body = apply_social_ledger(agent.body, felt.SocialExposure(messages_read=len(fresh)))
    for _message in fresh:
        body = apply_mood_pulse(body, "message_received")
    read = set(agent.read_inbox_ids) | {message.message_id for message in fresh}
    return agent.model_copy(update={"body": body, "read_inbox_ids": read}, deep=True)


def _apply_awake_social_ledger(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 清醒社交需要增长 +0.04/h 与共同用餐 −0.3/h（L1513-1515）。

    CHAT/CALL 活动区间的账本由 life.social_actions.advance_social 在活动结算内
    推进（含清醒增长），此处跳过避免重复计数。
    """
    if _social_activity_covered(agent, inputs):
        return agent
    activity = inputs.state.activities.get(agent.current_activity_id or "")
    active = activity is not None and activity.status == ActivityStatus.ACTIVE
    asleep = active and activity.kind == ActivityKind.SLEEP
    hours = 0.0 if asleep else inputs.interval_seconds / 3600
    shared = (hours if _shared_meal(agent, inputs) else 0.0)
    if hours <= 0 and shared <= 0:
        return agent
    body = apply_social_ledger(agent.body, felt.SocialExposure(
        awake_hours=hours, shared_meal_hours=shared))
    return agent.model_copy(update={"body": body}, deep=True)


def _social_activity_covered(agent: AgentState, inputs: RhythmInputs) -> bool:
    """@brief 本区间是否由 CHAT/CALL 活动结算推进（其内部已应用账本）。"""
    now = inputs.state.current_time
    for activity in inputs.state.activities.values():
        if activity.agent_id != agent.agent_id:
            continue
        ended_here = (activity.status == ActivityStatus.COMPLETED
                      and activity.ended_at == now)
        active_now = (activity.status == ActivityStatus.ACTIVE
                      and activity.activity_id == agent.current_activity_id)
        if activity.kind in {ActivityKind.CHAT, ActivityKind.CALL} and (ended_here or active_now):
            return True
    return False


def _shared_meal(agent: AgentState, inputs: RhythmInputs) -> bool:
    """@brief 本区间是否共同用餐：进食活动进行中且同地点有他人在场。"""
    from worldmock.life.models import ConsumeRequest

    activity = inputs.state.activities.get(agent.current_activity_id or "")
    if (activity is None or activity.status != ActivityStatus.ACTIVE
            or activity.kind != ActivityKind.CONSUME):
        return False
    request = activity.data.get("request")
    if not isinstance(request, dict) or request.get("use_mode") != "eat":
        return False
    return bool(_people_here(inputs.state, agent))


def _plan_and_deadline_pulses(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 完成计划段 +0.05；任务到期未交 −0.4（L1517-1518）。"""
    now = inputs.state.current_time
    body = agent.body
    if agent.day_plan is not None and any(
            segment.end == now for segment in agent.day_plan.segments):
        body = apply_mood_pulse(body, "plan_segment_completed")
    window_start = now - inputs.interval_seconds
    for task_id in sorted(agent.visible_task_ids):
        due = inputs.task_due.get(task_id)
        if (due is not None and task_id not in inputs.state.paid_task_ids
                and window_start < due <= now):
            body = apply_mood_pulse(body, "task_deadline_missed")
    return agent.model_copy(update={"body": body}, deep=True)


def _wake_snapshot(agent: AgentState, inputs: RhythmInputs) -> rhythm_schedule.WakeSnapshot:
    """@brief 装配该边界的唤醒判定输入（与上一次实际决策比较）。"""
    state = inputs.state
    now = state.current_time
    activity = state.activities.get(agent.current_activity_id or "")
    active = activity is not None and activity.status == ActivityStatus.ACTIVE
    completed_now = any(item.agent_id == agent.agent_id
                        and item.status == ActivityStatus.COMPLETED
                        and item.ended_at == now for item in state.activities.values())
    levels = felt.summarize_felt_levels(
        **felt_values(agent, local_hour(now, inputs.scenario.timezone),
                      inputs.meal_windows_by_agent.get(agent.agent_id)))
    plan_starts = ()
    if agent.day_plan is not None:
        plan_starts = tuple(segment.start for segment in agent.day_plan.segments)
    flags = inputs.arrival_flags.get(agent.agent_id, set())
    due = min((value for task_id, value in sorted(inputs.task_due.items())
               if task_id in agent.visible_task_ids
               and task_id not in state.paid_task_ids and value > now), default=None)
    return rhythm_schedule.WakeSnapshot(
        now=now,
        planned_end=(activity.planned_end_at
                     if active and activity.planned_end_at <= now
                     else (now if completed_now else None)),
        felt_bands_now={key: _band_index(value) for key, value in levels.items()},
        felt_bands_at_last_decision={key: _band_index(value) for key, value
                                     in agent.felt_bands_at_last_decision.items()},
        message_arrived=any(message_id not in agent.read_inbox_ids
                            for message_id in agent.inbox) or "message_arrival" in flags,
        delivery_arrived="delivery_arrival" in flags,
        task_newly_visible=bool(set(agent.visible_task_ids) - agent.known_task_ids)
        or "task_available" in flags,
        next_task_due_at=due,
        work_review_returned="work_review_result" in flags,
        plan_block_starts=plan_starts,
        last_decision_time=agent.last_decision_time,
        reconsideration_interval_seconds=inputs.reconsideration_interval_seconds,
        location_closing_at=_closing_at(agent, inputs),
        location_just_opened=_just_opened(agent, inputs),
        people_present_now=frozenset(item["name"] for item in _people_here(state, agent)),
        people_present_before=agent.people_at_last_decision,
        activity_infeasible=_activity_infeasible(agent, inputs),
        consecutive_rejections=agent.consecutive_rejections,
        wake_from_sleep=_woke_from_sleep(state, agent.agent_id, now),
        run_start=agent.last_decision_time is None,
        is_asleep=active and activity.kind == ActivityKind.SLEEP,
    )


def _people_here(state: WorldState, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 同地点他人（投影闭集，委托 life.projection）。"""
    from worldmock.life import projection as projection_module

    return projection_module.people_here_views(state, agent)


def _band_index(level: str) -> int:
    """@brief 分级文本 → 序数（未知文本取 −1，键只增不减地视为变化）。"""
    return _BAND_ORDER.index(level) if level in _BAND_ORDER else -1


def _closing_at(agent: AgentState, inputs: RhythmInputs) -> int | None:
    """@brief 当前地点的最近未来闭店时刻（构成 15 分钟预警窗口的输入）。"""
    location = _current_location(agent, inputs)
    if location is None:
        return None
    close = _today_epoch(inputs, location.close_hour % 24)
    return close if close > inputs.state.current_time else None


def _just_opened(agent: AgentState, inputs: RhythmInputs) -> bool:
    """@brief 当前地点在最近一个结算间隔内刚开门。"""
    location = _current_location(agent, inputs)
    if location is None:
        return False
    opened = _today_epoch(inputs, location.open_hour)
    now = inputs.state.current_time
    return now - inputs.interval_seconds < opened <= now


def _current_location(agent: AgentState, inputs: RhythmInputs):
    """@brief 当前地点配置。"""
    for location in inputs.scenario.locations:
        if location.location_id == agent.location_id:
            return location
    return None


def _today_epoch(inputs: RhythmInputs, hour: int) -> int:
    """@brief 场景时区当日某整点的 epoch 秒。"""
    local = datetime.fromtimestamp(inputs.state.current_time,
                                   ZoneInfo(inputs.scenario.timezone))
    return int(local.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp())


def _location_change_times(agent: AgentState, inputs: RhythmInputs) -> list[int]:
    """@brief 当前地点未来 24 小时内的开门/闭店时刻（下一决策候选）。"""
    now = inputs.state.current_time
    result: list[int] = []
    local = datetime.fromtimestamp(now, ZoneInfo(inputs.scenario.timezone))
    for location in inputs.scenario.locations:
        if location.location_id != agent.location_id:
            continue
        for hour in (location.open_hour % 24, location.close_hour % 24):
            moment = int(local.replace(hour=hour, minute=0, second=0,
                                       microsecond=0).timestamp())
            if moment > now:
                result.append(moment)
            else:
                result.append(moment + 86_400)
    return sorted(set(result))


def _activity_infeasible(agent: AgentState, inputs: RhythmInputs) -> bool:
    """@brief 能量已不允许当前活动继续（恢复活动与游戏/睡眠除外）。"""
    activity = inputs.state.activities.get(agent.current_activity_id or "")
    if activity is None or activity.status != ActivityStatus.ACTIVE:
        return False
    if activity.kind in inputs.recovery_kinds or activity.kind in {
            ActivityKind.SLEEP, ActivityKind.PLAY_GAME, ActivityKind.CHAT, ActivityKind.CALL}:
        return False
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    if remaining < 60:
        return False
    from worldmock.life.body import minimum_energy_cost

    cost = minimum_energy_cost(activity.kind, remaining, inputs.body_config)
    return cost > agent.body.energy_kcal


def _game_turn_due(state: WorldState, agent: AgentState) -> bool:
    """@brief 游戏交互边界到期（next_interaction_at == now 且活动进行中）。"""
    from worldmock.life.models import ActivityKind

    activity = state.activities.get(agent.current_activity_id or "")
    return (activity is not None and activity.status == ActivityStatus.ACTIVE
            and activity.kind == ActivityKind.PLAY_GAME
            and activity.next_interaction_at == state.current_time)


def _woke_from_sleep(state: WorldState, agent_id: int, now: int) -> bool:
    """@brief 睡眠活动恰在本边界结束（planned_end 到达）即 wake_from_sleep。"""
    for activity in state.activities.values():
        if (activity.agent_id == agent_id and activity.kind == ActivityKind.SLEEP
                and activity.status == ActivityStatus.COMPLETED
                and activity.ended_at == now):
            return True
    return False


def local_hour(epoch: int, timezone_name: str) -> float:
    """@brief 场景时区自午夜起的小时数。"""
    moment = datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))
    return moment.hour + moment.minute / 60 + moment.second / 3600


def engine_rhythm_inputs(engine, state, interval: int = 0,
                         arrival_flags: dict | None = None,
                         progress_data: dict | None = None) -> RhythmInputs:
    """@brief 引擎侧装配决策节律输入束（任务截止表、重估间隔与恢复闭集）。"""
    return RhythmInputs(
        state=state, scenario=engine.scenario, body_config=engine.semantics.body,
        task_due={task_id: int(task.due_at.timestamp())
                  for task_id, task in engine.rules.tasks.items()},
        interval_seconds=interval or engine.scenario.activity_feedback_seconds,
        reconsideration_interval_seconds=engine.semantics.document.reconsideration_interval_seconds,
        recovery_kinds=engine.recovery_kinds,
        meal_windows_by_agent=engine.meal_windows_by_agent(),
        arrival_flags=arrival_flags or {},
        progress_data=progress_data or {})


def apply_engine_rhythm(engine, staged, target: int, interval: int,
                        arrivals: dict[int, set[str]], drafts: list) -> "WorldState":
    """@brief 边界节律：唤醒判定、静默留痕，并让被唤醒的活动对齐决策边界。"""
    from worldmock.life.engine import _put_activity
    from worldmock.life.models import ActivityStatus

    progress_data: dict[int, dict] = {}
    for draft in drafts:
        if draft.event_type == "activity_progress" and draft.agent_id is not None:
            progress_data.setdefault(draft.agent_id, draft.data)
    staged, rhythms = apply_decision_rhythm(engine_rhythm_inputs(
        engine, staged, interval, arrivals, progress_data))
    for agent_id, rhythm in rhythms.items():
        if not rhythm.offered:
            agent = staged.agents[agent_id]
            activity = staged.activities.get(agent.current_activity_id or "")
            if (activity is not None and activity.status == ActivityStatus.ACTIVE
                    and activity.next_interaction_at is not None
                    and activity.next_interaction_at <= target):
                from worldmock.life.activities import schedule_feedback

                staged = _put_activity(staged, agent, schedule_feedback(
                    activity, target, engine.scenario.activity_feedback_seconds))
            continue
        agent = staged.agents[agent_id]
        activity = staged.activities.get(agent.current_activity_id or "")
        if (activity is not None and activity.status == ActivityStatus.ACTIVE
                and activity.next_interaction_at != target):
            aligned = activity.model_copy(
                update={"next_interaction_at": target, "boundary_type": "feedback"}, deep=True)
            staged = _put_activity(staged, agent, aligned)
    return staged
