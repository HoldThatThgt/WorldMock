"""@brief 决策节律的引擎侧装配：唤醒判定、静默边界留痕与下一决策候选（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。

结算边界（activity_feedback_seconds=900）照常结算资源；是否提供决策只由唤醒
条件决定——判定委托 life.schedule.evaluate_wake_reasons，本模块负责从世界状态
装配 WakeSnapshot、在同一边界推进情绪账（脉冲与衰减）、计算下一决策候选
（schedule.next_decision_time）。静默边界在 activity_progress.data 留
decision_offered=false 与 wake_reasons=[]；提供决策的边界记录实际命中的全部
唤醒原因。滞回参照=上次实际提供决策时的分级（docs/dev/SPEC-life-runtime.md「滞回规则」）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.life.opening_hours import next_opening_change, opening_status
from worldmock.footprints.models import DECISION_OFFERED_FIELD, WAKE_REASONS_FIELD, validate_decision_rhythm
from worldmock.life import felt
from worldmock.life import schedule as rhythm_schedule
from worldmock.life.body import (
    apply_discomfort_pulse,
    apply_mood_pulse,
    apply_social_ledger,
    decay_mood,
    next_shared_body_boundary_seconds,
)
from worldmock.life.models import (ActivityKind, ActivityStatus, AgentState, WorldState,
                                   active_activities)
from worldmock.life.projection import felt_values
from worldmock.life.scenario import BodyConfig, RuntimeScenario

#: 饥饿不适档（docs/dev/SPEC-life-runtime.md「其他体感分级」连续两小时以上饿）：appetite 分级达到"饿"及以上。
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
    #: 没有在手承诺时的较长重估间隔。
    idle_reconsideration_interval_seconds: int = rhythm_schedule.IDLE_RECONSIDERATION_INTERVAL_SECONDS
    #: 播报节律间隔（docs/dev/SPEC-agent-interaction.md「播报节律」）：活跃对话距上次报告满该秒数触发唤醒。
    progress_report_interval_seconds: int = 1800
    #: 人物档案餐时窗口（agent_id -> MealWindows；滞回参照与观察同源）。
    meal_windows_by_agent: dict[int, felt.MealWindows] = field(default_factory=dict)
    #: 本 tick 由预排事件触发的唤醒旗标：agent_id -> {wake_reason 名}。
    arrival_flags: dict[int, set[str]] = field(default_factory=dict)
    #: 待注解的本人活动进度及唯一共同身体草稿。
    progress_data: dict[int, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRhythm:
    """@brief 单个人物在本边界的节律判定结果。"""

    offered: bool
    wake_reasons: list[str]
    next_decision_at: int


@dataclass(frozen=True, slots=True)
class RhythmBoundary:
    """@brief 本次共同边界的时刻、长度、到达事实和待提交草稿。"""

    target: int
    interval: int
    arrivals: dict[int, set[str]]
    drafts: list


def apply_decision_rhythm(inputs: RhythmInputs) -> tuple[WorldState, dict[int, AgentRhythm]]:
    """@brief 对每个人物执行边界节律：情绪账、唤醒判定与下一决策时刻；共同身体结算的唤醒原因写入时经闭集校验。"""
    state = inputs.state
    rhythms: dict[int, AgentRhythm] = {}
    agents = {}
    for agent_id in sorted(state.agents):
        agent = state.agents[agent_id]
        agent = _settle_mood(agent, inputs)
        warning_facts = _warning_signatures(agent, inputs)
        current = set().union(*warning_facts.values())
        delivered = set(agent.delivered_warning_signatures) & current
        verdict = rhythm_schedule.evaluate_wake_reasons(
            _wake_snapshot(agent, inputs, warning_facts, delivered))
        # 收件箱在唤醒判定之后才记为已读（docs/dev/SPEC-time-and-energy.md 第一章「人物之间的消息」）：
        # 未读消息在本边界成立 message_arrival，睡眠中由判定过滤。
        agent = _read_new_messages(agent)
        if _game_turn_due(inputs.state, agent):
            # 游戏保持逐回合反馈（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）：交互边界到期即提供决策，
            # 不经唤醒条件枚举；wake_reasons 记录实际命中的（通常为空）。
            verdict = rhythm_schedule.WakeVerdict(
                decision_offered=True, wake_reasons=verdict.wake_reasons)
        for data in inputs.progress_data.get(agent_id, []):
            if data.get("kind") == "body_settlement":
                data[DECISION_OFFERED_FIELD] = verdict.decision_offered
                data[WAKE_REASONS_FIELD] = [reason.value for reason in verdict.wake_reasons]
                validate_decision_rhythm(data)
            else:
                data["day_plan"] = agent.day_plan.model_dump(mode="json") if agent.day_plan else None
        offered = verdict.decision_offered
        reasons = [reason.value for reason in verdict.wake_reasons]
        if offered:
            delivered.update(_delivered_warning_signatures(warning_facts, verdict))
        if offered:
            next_at = state.current_time
        else:
            candidate = next_decision_candidates(agent, inputs)
            next_at = candidate if candidate is not None else (
                state.current_time + max(1, inputs.interval_seconds))
        agent = agent.model_copy(update={"next_decision_at": next_at,
                                         "pending_wake_reasons": reasons if offered else [],
                                         "delivered_warning_signatures": delivered},
                                 deep=True)
        agents[agent_id] = agent
        rhythms[agent_id] = AgentRhythm(offered, reasons, next_at)
    return state.model_copy(update={"agents": agents}, deep=True), rhythms


def next_decision_candidates(agent: AgentState, inputs: RhythmInputs) -> int | None:
    """@brief 计算某人物的下一决策时刻（schedule.next_decision_time 候选装配）。"""
    state = inputs.state
    activities = active_activities(state, agent.agent_id)
    felt_crossings: list[int] = []
    if not any(item.kind in {ActivityKind.CHAT, ActivityKind.CALL,
                             ActivityKind.PLAY_GAME, ActivityKind.SLEEP} for item in activities):
        from worldmock.life.activities import activity_factors
        hour = local_hour(state.current_time, inputs.scenario.timezone)
        factors = tuple((item.kind, activity_factors(item, agent, inputs.body_config))
                        for item in activities)
        seconds = next_shared_body_boundary_seconds(agent.body, factors,
                                                    inputs.body_config, hour)
        if seconds is not None:
            felt_crossings.append(state.current_time + seconds)
    arrivals = [item.due_at for item in state.pending_events
                if item.due_at > state.current_time and item.agent_id == agent.agent_id]
    return rhythm_schedule.next_decision_time(rhythm_schedule.NextDecisionQuery(
        now=state.current_time,
        planned_end=min((item.planned_end_at for item in activities), default=None),
        felt_band_crossing_times=tuple(felt_crossings),
        scheduled_arrival_times=tuple(arrivals),
        last_decision_time=agent.last_decision_time,
        reconsideration_interval_seconds=inputs.reconsideration_interval_seconds,
        idle_reconsideration_interval_seconds=inputs.idle_reconsideration_interval_seconds,
        has_commitments=_has_commitments(agent, inputs),
        location_hours_change_times=tuple(_location_change_times(agent, inputs)),
        next_task_due_at=_next_task_due(agent, inputs),
        location_closing_at=(_closing_at(agent, inputs)
                             if _commitment_signatures(agent, inputs) else None),
        next_progress_report_at=_next_progress_report_at(
            agent, state, inputs.progress_report_interval_seconds),
        is_asleep=any(item.kind == ActivityKind.SLEEP for item in activities),
    ))


def _settle_mood(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 边界情绪账：先按半衰期衰减，再叠加本区间事件脉冲（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    新收件箱消息的已读与脉冲不在此处，而在唤醒判定之后结算（_read_new_messages）。
    """
    now = inputs.state.current_time
    if agent.mood_updated_at is not None:
        agent = agent.model_copy(update={
            "body": decay_mood(agent.body, (now - agent.mood_updated_at) / 60)}, deep=True)
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
    agent = _goal_and_deadline_pulses(agent, inputs)
    return agent.model_copy(update={"mood_updated_at": now}, deep=True)


def _read_new_messages(agent: AgentState) -> AgentState:
    """@brief 新收件箱消息计为已读：社交需要 −0.05/条 + 情绪 +0.1/条（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    在本边界的唤醒判定之后调用，使未读消息先成立 message_arrival（docs/dev/SPEC-time-and-energy.md 第一章「人物之间的消息」）。
    """
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
    """@brief 清醒社交需要增长 +0.04/h 与共同用餐 −0.3/h（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    CHAT/CALL 活动区间的账本由 life.social_actions.advance_social 在活动结算内
    推进（含清醒增长），此处跳过避免重复计数。
    """
    if _social_activity_covered(agent, inputs):
        return agent
    asleep = any(item.kind == ActivityKind.SLEEP
                 for item in active_activities(inputs.state, agent.agent_id))
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
        active_now = activity.status == ActivityStatus.ACTIVE
        if activity.kind in {ActivityKind.CHAT, ActivityKind.CALL} and (ended_here or active_now):
            return True
    return False


def _shared_meal(agent: AgentState, inputs: RhythmInputs) -> bool:
    """@brief 本区间是否共同用餐：进食活动进行中且同地点有他人在场。"""
    eating = any(item.kind == ActivityKind.CONSUME
                 and item.data.get("request", {}).get("use_mode") == "eat"
                 for item in active_activities(inputs.state, agent.agent_id))
    return eating and bool(_people_here(inputs.state, agent))


def _goal_and_deadline_pulses(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 实际目标首次推进产生情绪脉冲，任务到期未交产生失约脉冲。"""
    now = inputs.state.current_time
    body = agent.body
    plan = agent.day_plan
    for data in inputs.progress_data.get(agent.agent_id, []):
        if (plan is None or "activity" not in data
                or data["activity"].get("agent_id") != agent.agent_id):
            continue
        changed = rhythm_schedule.progress_day_plan(
            plan, data["activity"], data["interval_start"], data["interval_end"])
        if changed != plan:
            body = apply_mood_pulse(body, "goal_progressed")
            plan = changed
    window_start = now - inputs.interval_seconds
    for task_id in sorted(agent.visible_task_ids):
        due = inputs.task_due.get(task_id)
        if (due is not None and task_id not in inputs.state.paid_task_ids
                and window_start < due <= now):
            body = apply_mood_pulse(body, "task_deadline_missed")
    return agent.model_copy(update={"body": body, "day_plan": plan}, deep=True)


def _has_commitments(agent: AgentState, inputs: RhythmInputs) -> bool:
    """@brief 在手承诺：未推进目标、可见未完成任务、暂停活动或等待之外的进行中活动。

    进行中的等待不算在手承诺（docs/dev/SPEC-time-and-energy.md 第一章「拒绝的情绪代价与重估间隔」）：
    只在等待（或无活动）且没有其余各项时返回假，此时用空闲重估间隔。
    """
    plan = agent.day_plan
    if (plan is not None and not rhythm_schedule.day_plan_expired(plan, inputs.state.current_time)
            and any(goal.progressed_at is None for goal in plan.goals)):
        return True
    if any(task_id not in inputs.state.paid_task_ids for task_id in agent.visible_task_ids):
        return True
    return any(activity.agent_id == agent.agent_id
               and (activity.status == ActivityStatus.PAUSED
                    or activity.status == ActivityStatus.ACTIVE and activity.kind != ActivityKind.WAIT)
               for activity in inputs.state.activities.values())


def _task_signature(task_id: str, due_at: int) -> str:
    """@brief 将任务身份和具体截止编码为稳定事实签名。"""
    return json.dumps(["task", task_id, due_at], ensure_ascii=False, separators=(",", ":"))


def _next_task_due(agent: AgentState, inputs: RhythmInputs,
                   eligible: set[str] | None = None) -> int | None:
    """@brief 返回尚未交付预警的所有可见未完成任务中的最近截止。"""
    signatures = eligible
    if signatures is None:
        signatures = set().union(*_warning_signatures(agent, inputs).values())
        signatures |= {_task_signature(task_id, due) for task_id, due in inputs.task_due.items()
                       if task_id in agent.visible_task_ids and task_id not in inputs.state.paid_task_ids}
        signatures -= agent.delivered_warning_signatures
    return min((due for task_id, due in inputs.task_due.items()
                if task_id in agent.visible_task_ids and task_id not in inputs.state.paid_task_ids
                and _task_signature(task_id, due) in signatures
                and due > inputs.state.current_time), default=None)


def _warning_signatures(agent: AgentState, inputs: RhythmInputs) -> dict[str, set[str]]:
    """@brief 从当前威胁事实生成任务、闭店和不可行签名。"""
    now = inputs.state.current_time
    task = {_task_signature(task_id, due) for task_id, due in inputs.task_due.items()
            if task_id in agent.visible_task_ids and task_id not in inputs.state.paid_task_ids
            and due - rhythm_schedule.TASK_DUE_SOON_SECONDS <= now < due}
    closing: set[str] = set()
    close_at = _closing_at(agent, inputs)
    if close_at is not None and close_at - rhythm_schedule.CLOSING_WARNING_SECONDS <= now < close_at:
        for commitment in _commitment_signatures(agent, inputs):
            closing.add(json.dumps(["closing", agent.location_id, close_at, commitment],
                                   ensure_ascii=False, separators=(",", ":")))
    return {"task": task, "closing": closing,
            "infeasible": _activity_infeasible_signatures(agent, inputs)}


def _commitment_signatures(agent: AgentState, inputs: RhythmInputs) -> set[str]:
    """@brief 仅由结构化地点任务证明当前地点的闭店威胁。"""
    locations = {task.task_id: task.required_location_id for task in inputs.scenario.tasks}
    return {f"task:{task_id}:{inputs.task_due[task_id]}"
            for task_id in agent.visible_task_ids
            if (task_id in inputs.task_due and task_id not in inputs.state.paid_task_ids
                and locations.get(task_id) == agent.location_id)}


def _delivered_warning_signatures(facts: dict[str, set[str]],
                                  verdict: rhythm_schedule.WakeVerdict) -> set[str]:
    """@brief 只把本次实际提供的具体预警标为已交付。"""
    reasons = set(verdict.wake_reasons)
    delivered: set[str] = set()
    if rhythm_schedule.WakeReason.TASK_DUE_SOON in reasons:
        delivered.update(facts["task"])
    if rhythm_schedule.WakeReason.LOCATION_HOURS_CHANGE in reasons:
        delivered.update(facts["closing"])
    if rhythm_schedule.WakeReason.ACTIVITY_INFEASIBLE in reasons:
        delivered.update(facts["infeasible"])
    return delivered


def _wake_snapshot(agent: AgentState, inputs: RhythmInputs, facts: dict[str, set[str]],
                   delivered: set[str]) -> rhythm_schedule.WakeSnapshot:
    """@brief 装配该边界的唤醒判定输入（与上一次实际决策比较）。"""
    state = inputs.state
    now = state.current_time
    activities = active_activities(state, agent.agent_id)
    completed_now = any(item.agent_id == agent.agent_id
                        and item.status in {ActivityStatus.COMPLETED, ActivityStatus.FAILED}
                        and item.ended_at == now for item in state.activities.values())
    levels = felt.summarize_felt_levels(
        **felt_values(agent, local_hour(now, inputs.scenario.timezone),
                      inputs.meal_windows_by_agent.get(agent.agent_id)))
    flags = inputs.arrival_flags.get(agent.agent_id, set())
    return rhythm_schedule.WakeSnapshot(
        now=now,
        planned_end=now if completed_now else None,
        felt_bands_now={key: _band_index(value) for key, value in levels.items()},
        felt_bands_at_last_decision={key: _band_index(value) for key, value
                                     in agent.felt_bands_at_last_decision.items()},
        message_arrived=any(message_id not in agent.read_inbox_ids
                            for message_id in agent.inbox) or "message_arrival" in flags,
        delivery_arrived="delivery_arrival" in flags,
        task_newly_visible=bool(set(agent.visible_task_ids) - agent.known_task_ids)
        or "task_available" in flags,
        next_task_due_at=_next_task_due(agent, inputs, facts["task"] - delivered),
        work_review_returned="work_review_result" in flags,
        last_decision_time=agent.last_decision_time,
        reconsideration_interval_seconds=inputs.reconsideration_interval_seconds,
        idle_reconsideration_interval_seconds=inputs.idle_reconsideration_interval_seconds,
        has_commitments=_has_commitments(agent, inputs),
        location_closing_at=(_closing_at(agent, inputs)
                             if facts["closing"] - delivered else None),
        location_just_opened=_just_opened(agent, inputs),
        people_present_now=frozenset(item["name"] for item in _people_here(state, agent)),
        people_present_before=agent.people_at_last_decision,
        activity_infeasible=bool(facts["infeasible"] - delivered),
        discovery_result_ready=("discovery_result" in agent.pending_wake_reasons
                                and agent.next_decision_at <= now),
        wake_from_sleep=_woke_from_sleep(state, agent.agent_id, now),
        run_start=agent.last_decision_time is None,
        progress_report_due=_progress_report_due(agent, state, inputs.progress_report_interval_seconds),
        no_active_activity=not activities,
        is_asleep=any(item.kind == ActivityKind.SLEEP for item in activities),
    )


def _people_here(state: WorldState, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 同地点他人（投影闭集，委托 life.projection）。"""
    from worldmock.life import projection as projection_module

    return projection_module.people_here_views(state, agent)


def _band_index(level: str) -> int:
    """@brief 分级文本 → 序数（未知文本取 −1，键只增不减地视为变化）。"""
    return _BAND_ORDER.index(level) if level in _BAND_ORDER else -1


def _closing_at(agent: AgentState, inputs: RhythmInputs) -> int | None:
    """@brief 以统一营业日历查找当前营业区间的下一次变化。"""
    location = _current_location(agent, inputs)
    if location is None:
        return None
    now = inputs.state.current_time
    overrides = inputs.state.opening_exceptions[location.location_id]
    if opening_status(location, now, inputs.scenario.timezone, overrides) != "open":
        return None
    return next_opening_change(location, now, inputs.scenario.timezone, overrides)


def _just_opened(agent: AgentState, inputs: RhythmInputs) -> bool:
    """@brief 用上一决策至本边界的真实营业变化判断刚开门，不按固定小时推断。"""
    location = _current_location(agent, inputs)
    if location is None:
        return False
    now = inputs.state.current_time
    before = agent.last_decision_time if agent.last_decision_time is not None else now - 1
    overrides = inputs.state.opening_exceptions[location.location_id]
    changed = next_opening_change(location, before, inputs.scenario.timezone, overrides)
    return (changed is not None and changed <= now
            and opening_status(location, now, inputs.scenario.timezone, overrides) == "open")


def _current_location(agent: AgentState, inputs: RhythmInputs):
    """@brief 当前地点配置。"""
    for location in inputs.scenario.locations:
        if location.location_id == agent.location_id:
            return location
    return None


def _location_change_times(agent: AgentState, inputs: RhythmInputs) -> list[int]:
    """@brief 下一决策直接调度营业状态变化，包括分钟边界、跨日和日期例外。"""
    location = _current_location(agent, inputs)
    if location is None:
        return []
    change = next_opening_change(location, inputs.state.current_time, inputs.scenario.timezone,
                                 inputs.state.opening_exceptions[location.location_id])
    return [change] if change is not None else []


def _activity_infeasible_signatures(agent: AgentState, inputs: RhythmInputs) -> set[str]:
    """@brief 用共同剩余成本和离散原因标识当前不可行活动。"""
    from worldmock.life.activities import active_energy_entries
    from worldmock.life.body import shared_energy_cost

    activities = active_activities(inputs.state, agent.agent_id)
    candidates = [item for item in activities if item.kind not in inputs.recovery_kinds
                  and item.kind not in {ActivityKind.PLAY_GAME, ActivityKind.CHAT, ActivityKind.CALL}
                  and item.planned_duration_seconds - item.elapsed_seconds >= 60]
    if not candidates:
        return set()
    cost = shared_energy_cost(active_energy_entries(inputs.state, agent.agent_id,
                                                    inputs.body_config), inputs.body_config)
    if cost <= agent.body.energy_kcal:
        return set()
    return {f"infeasible:{item.activity_id}:insufficient_energy" for item in candidates}


def _game_turn_due(state: WorldState, agent: AgentState) -> bool:
    """@brief 游戏交互边界到期（next_interaction_at == now 且活动进行中）。"""
    from worldmock.life.models import ActivityKind

    return any(item.kind == ActivityKind.PLAY_GAME
               and item.next_interaction_at == state.current_time
               for item in active_activities(state, agent.agent_id))


def _woke_from_sleep(state: WorldState, agent_id: int, now: int) -> bool:
    """@brief 睡眠活动恰在本边界结束（planned_end 到达）即 wake_from_sleep。"""
    for activity in state.activities.values():
        if (activity.agent_id == agent_id and activity.kind == ActivityKind.SLEEP
                and activity.status == ActivityStatus.COMPLETED
                and activity.ended_at == now):
            return True
    return False


def _progress_report_due(agent: AgentState, state: WorldState,
                          interval_seconds: int) -> bool:
    """@brief 人物有活跃对话且距上次播报已满间隔时触发播报节律（docs/dev/SPEC-agent-interaction.md「播报节律」）。

    @param agent 当前人物状态
    @param state 世界状态（sessions 字段由 Track 1 添加，缺失时返回 False）
    @param interval_seconds 播报间隔秒数
    """
    sessions = getattr(state, "sessions", {})
    now = state.current_time
    for session in sessions.values():
        if getattr(session, "agent_id", None) != agent.agent_id:
            continue
        if getattr(session, "status", None) != "active":
            continue
        anchor = (getattr(session, "note_updated_at", None)
                  or getattr(session, "started_at", now))
        if now - anchor >= interval_seconds:
            return True
    return False


def _next_progress_report_at(agent: AgentState, state: WorldState,
                              interval_seconds: int) -> int | None:
    """@brief 返回最早满足播报间隔的候选时刻（docs/dev/SPEC-agent-interaction.md「播报节律」）。

    @param agent 当前人物状态
    @param state 世界状态（sessions 字段由 Track 1 添加，缺失时返回 None）
    @param interval_seconds 播报间隔秒数
    """
    sessions = getattr(state, "sessions", {})
    now = state.current_time
    candidates: list[int] = []
    for session in sessions.values():
        if getattr(session, "agent_id", None) != agent.agent_id:
            continue
        if getattr(session, "status", None) != "active":
            continue
        anchor = (getattr(session, "note_updated_at", None)
                  or getattr(session, "started_at", now))
        candidates.append(anchor + interval_seconds)
    return min((t for t in candidates if t > now), default=None)


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
        idle_reconsideration_interval_seconds=engine.semantics.document.idle_reconsideration_interval_seconds,
        progress_report_interval_seconds=engine.semantics.document.progress_report_interval_seconds,
        recovery_kinds=engine.recovery_kinds,
        meal_windows_by_agent=engine.meal_windows_by_agent(),
        arrival_flags=arrival_flags or {},
        progress_data=progress_data or {})


def apply_engine_rhythm(engine, staged, boundary: RhythmBoundary) -> "WorldState":
    """@brief 边界节律：唤醒判定、静默留痕，并让被唤醒的活动对齐决策边界。"""
    from worldmock.life.engine import _put_activity
    from worldmock.life.models import ActivityStatus

    progress_data: dict[int, list[dict]] = {}
    for draft in boundary.drafts:
        if (draft.event_type == "activity_progress"
                or draft.event_type == "environment_event"
                and draft.data.get("kind") == "body_settlement") and draft.agent_id is not None:
            progress_data.setdefault(draft.agent_id, []).append(draft.data)
    staged, rhythms = apply_decision_rhythm(engine_rhythm_inputs(
        engine, staged, boundary.interval, boundary.arrivals, progress_data))
    from worldmock.life.activities import schedule_feedback

    for agent_id in rhythms:
        agent = staged.agents[agent_id]
        for activity in active_activities(staged, agent_id):
            if activity.next_interaction_at is not None and activity.next_interaction_at <= boundary.target:
                if activity.kind == ActivityKind.PLAY_GAME:
                    continue
                staged = _put_activity(staged, agent, schedule_feedback(
                    activity, boundary.target, engine.scenario.activity_feedback_seconds))
    return staged
