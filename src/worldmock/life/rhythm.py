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
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.life.opening_hours import next_opening_change, opening_status
from worldmock.footprints.models import DECISION_OFFERED_FIELD, WAKE_REASONS_FIELD, validate_decision_rhythm
from worldmock.life import felt
from worldmock.life import reminders
from worldmock.life import schedule as rhythm_schedule
from worldmock.life.activity_control import is_pending_conclusion
from worldmock.life.body import (
    BoundaryClock,
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

#: 饥饿不适档（docs/dev/SPEC-life-runtime.md「其他体感分级」连续两小时以上饿）：appetite 分级达到"饿"及以上，
#: 含只由能量决定的第六档「饿得发慌」。
HUNGRY_BANDS = frozenset({"饿", "很饿", "饿得难受", felt.APPETITE_STARVING_LEVEL})
#: 困倦不适档：sleepiness 分级达到"困"及以上。
SLEEPY_BANDS = frozenset({"困", "很困", "撑不住"})

#: 分级文本 → 序数表（WakeSnapshot 以 int 比较分级，序数保序即可）。
_BAND_ORDER = ("不饿", "有点想吃", "饿", "很饿", "饿得难受", felt.APPETITE_STARVING_LEVEL, "清醒", "有点乏", "困",
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
    #: 没有在手承诺时的较长重估间隔。
    idle_reconsideration_interval_seconds: int = rhythm_schedule.IDLE_RECONSIDERATION_INTERVAL_SECONDS
    #: 播报间隔（docs/dev/SPEC-agent-interaction.md「决策轮循环与播报节律」）：进行中活动距其播报锚点满该秒数触发唤醒。
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
    """@brief 对每个人物执行边界节律：先结算段（情绪账与目标推进），再判定段（唤醒与下一决策时刻）。"""
    settled = settle_decision_rhythm(inputs)
    return decide_decision_rhythm(replace(inputs, state=settled))


def settle_decision_rhythm(inputs: RhythmInputs) -> WorldState:
    """@brief 边界节律的结算段：情绪账、按当前计划计的目标推进，并给本人活动进度草稿标注 day_plan 快照。

    零点边界（docs/dev/SPEC-time-and-energy.md 第四章「运行时机」）只在 advance 内执行本段，旧日末段的推进计入旧计划；
    判定段在零点计划提交之后由零点节律入口执行，不再重复本段的记账。
    """
    state = inputs.state
    agents = {}
    for agent_id in sorted(state.agents):
        agent = _settle_mood(state.agents[agent_id], inputs)
        for data in inputs.progress_data.get(agent_id, []):
            if data.get("kind") != "body_settlement":
                data["day_plan"] = agent.day_plan.model_dump(mode="json") if agent.day_plan else None
        agents[agent_id] = agent
    return state.model_copy(update={"agents": agents}, deep=True)


def decide_decision_rhythm(inputs: RhythmInputs) -> tuple[WorldState, dict[int, AgentRhythm]]:
    """@brief 边界节律的判定段：唤醒判定、提醒记账与下一决策时刻；共同身体结算的唤醒原因写入时经闭集校验。"""
    state = inputs.state
    rhythms: dict[int, AgentRhythm] = {}
    agents = {}
    for agent_id in sorted(state.agents):
        agent = state.agents[agent_id]
        warning_facts = _warning_signatures(agent, inputs)
        current = set().union(*warning_facts.values())
        delivered = set(agent.delivered_warning_signatures) & current
        verdict = rhythm_schedule.evaluate_wake_reasons(
            _wake_snapshot(agent, inputs, warning_facts, delivered))
        # 收件箱在唤醒判定之后才记为已读（docs/dev/SPEC-time-and-energy.md 第一章「人物之间的消息」）：
        # 未读消息在本边界成立 message_arrival，睡眠中由判定过滤。到点的提醒记为已触发，只触发一次。
        agent = reminders.mark_reminded(read_new_messages(agent), state.current_time)
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
    seconds = _body_boundary_seconds(agent, activities, inputs)
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
        plan_reminder_times=reminders.reminder_times(agent, state.current_time),
        is_asleep=any(item.kind == ActivityKind.SLEEP for item in activities),
    ))


def _body_boundary_seconds(agent: AgentState, activities: list, inputs: RhythmInputs) -> int | None:
    """@brief 体感跨档、餐时窗口与能量阈值的下一候选（与引擎共同结算同源系数，交谈、通话照常预测）。"""
    from worldmock.life.activities import shared_activity_factors

    factors = tuple((item.kind, shared_activity_factors(item, agent, inputs.body_config))
                    for item in activities)
    clock = BoundaryClock(local_hour(inputs.state.current_time, inputs.scenario.timezone),
                          inputs.meal_windows_by_agent.get(agent.agent_id))
    return next_shared_body_boundary_seconds(agent.body, factors, inputs.body_config, clock)


def _settle_mood(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 边界情绪账：先按半衰期衰减，再叠加本区间事件脉冲（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    新收件箱消息的已读与脉冲不在此处，而在唤醒判定之后结算（read_new_messages）。
    """
    now = inputs.state.current_time
    if agent.mood_updated_at is not None:
        agent = agent.model_copy(update={
            "body": decay_mood(agent.body, (now - agent.mood_updated_at) / 60)}, deep=True)
    agent = _apply_awake_social_ledger(agent, inputs)
    levels = _felt_levels(agent, inputs)
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


def read_new_messages(agent: AgentState, only: set[str] | None = None) -> AgentState:
    """@brief 新收件箱消息计为已读：社交需要 −0.05/条 + 情绪 +0.1/条（docs/dev/SPEC-life-runtime.md「其他体感分级」）。

    边界在唤醒判定之后调用，使未读消息先成立 message_arrival
    （docs/dev/SPEC-time-and-energy.md 第一章「人物之间的消息」）。决策轮提交时以 only 限定为本轮观察展示过的
    消息（轮前收件箱）：看过的消息不在其后的边界再次唤醒，本轮内送达的消息仍未读。

    :param agent: 人物状态
    :param only: 只读这些消息身份；None 表示全部未读消息
    """
    fresh = [message for message_id, message in agent.inbox.items()
             if message_id not in agent.read_inbox_ids and (only is None or message_id in only)]
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
    """@brief 从当前威胁事实生成任务与闭店签名（能量不构成不可行，docs/dev/SPEC-time-and-energy.md「删除的机制」）。"""
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
    return {"task": task, "closing": closing}


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
    return delivered


def _wake_snapshot(agent: AgentState, inputs: RhythmInputs, facts: dict[str, set[str]],
                   delivered: set[str]) -> rhythm_schedule.WakeSnapshot:
    """@brief 装配该边界的唤醒判定输入（与上一次实际决策比较）。"""
    state = inputs.state
    now = state.current_time
    activities = active_activities(state, agent.agent_id)
    # 活动由世界在本边界结束（计划结束、到达或到达被拒、随出行结束）即待收尾：planned_end 必给决策，同秒收尾。
    completed_now = any(item.agent_id == agent.agent_id and is_pending_conclusion(item)
                        and item.ended_at == now for item in state.activities.values())
    levels = _felt_levels(agent, inputs)
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
        discovery_result_ready=("discovery_result" in agent.pending_wake_reasons
                                and agent.next_decision_at <= now),
        wake_from_sleep=_woke_from_sleep(state, agent.agent_id, now),
        run_start=agent.last_decision_time is None,
        progress_report_due=_progress_report_due(agent, state, inputs.progress_report_interval_seconds),
        no_active_activity=not activities,
        plan_reminder_due=reminders.reminder_due(agent, now),
        is_asleep=any(item.kind == ActivityKind.SLEEP for item in activities),
    )


def _people_here(state: WorldState, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 同地点他人（投影闭集，委托 life.projection）。"""
    from worldmock.life import projection as projection_module

    return projection_module.people_here_views(state, agent)


def _felt_levels(agent: AgentState, inputs: RhythmInputs) -> dict[str, str]:
    """@brief 边界时刻的体感分级：饥饿按同一能量与紧急阈值叠加低能量规则，与观察的决策参照同源。"""
    values = felt_values(agent, local_hour(inputs.state.current_time, inputs.scenario.timezone),
                         inputs.meal_windows_by_agent.get(agent.agent_id))
    return felt.summarize_felt_levels(
        values, felt.EnergyReading(agent.body.energy_kcal, inputs.body_config.emergency_threshold_kcal))


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


def _report_anchor(activity) -> int:
    """@brief 活动的播报锚点：开始、恢复或上次对该活动播报的时刻（未经受理路径构造的活动取开始时刻）。"""
    return activity.report_anchor_at if activity.report_anchor_at is not None else activity.started_at


def _progress_report_due(agent: AgentState, state: WorldState,
                          interval_seconds: int) -> bool:
    """@brief 本人某项进行中活动距其播报锚点已满间隔时触发播报（docs/dev/SPEC-time-and-energy.md 第八章「活动」）。

    只遍历进行中活动；暂停、待收尾与已收尾的活动不播报。

    @param agent 当前人物状态
    @param state 世界状态
    @param interval_seconds 播报间隔秒数
    """
    now = state.current_time
    return any(now - _report_anchor(activity) >= interval_seconds
               for activity in active_activities(state, agent.agent_id))


def _next_progress_report_at(agent: AgentState, state: WorldState,
                              interval_seconds: int) -> int | None:
    """@brief 本人进行中活动中最早的下一播报时刻（锚点加间隔，严格晚于当前时刻）。

    @param agent 当前人物状态
    @param state 世界状态
    @param interval_seconds 播报间隔秒数
    """
    now = state.current_time
    candidates = [_report_anchor(activity) + interval_seconds
                  for activity in active_activities(state, agent.agent_id)]
    return min((moment for moment in candidates if moment > now), default=None)


def local_hour(epoch: int, timezone_name: str) -> float:
    """@brief 场景时区自午夜起的小时数。"""
    moment = datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))
    return moment.hour + moment.minute / 60 + moment.second / 3600


def engine_rhythm_inputs(engine, state, interval: int = 0,
                         arrival_flags: dict | None = None,
                         progress_data: dict | None = None) -> RhythmInputs:
    """@brief 引擎侧装配决策节律输入束（任务截止表、重估间隔与餐时窗口）。"""
    return RhythmInputs(
        state=state, scenario=engine.scenario, body_config=engine.semantics.body,
        task_due={task_id: int(task.due_at.timestamp())
                  for task_id, task in engine.rules.tasks.items()},
        interval_seconds=interval or engine.scenario.activity_feedback_seconds,
        reconsideration_interval_seconds=engine.semantics.document.reconsideration_interval_seconds,
        idle_reconsideration_interval_seconds=engine.semantics.document.idle_reconsideration_interval_seconds,
        progress_report_interval_seconds=engine.semantics.document.progress_report_interval_seconds,
        meal_windows_by_agent=engine.meal_windows_by_agent(),
        arrival_flags=arrival_flags or {},
        progress_data=progress_data or {})


def _boundary_progress_data(boundary: RhythmBoundary) -> dict[int, list[dict]]:
    """@brief 本边界待注解的本人活动进度与唯一共同身体结算草稿（按人物归组，注解就地写入草稿）。"""
    progress_data: dict[int, list[dict]] = {}
    for draft in boundary.drafts:
        if (draft.event_type == "activity_progress"
                or draft.event_type == "environment_event"
                and draft.data.get("kind") == "body_settlement") and draft.agent_id is not None:
            progress_data.setdefault(draft.agent_id, []).append(draft.data)
    return progress_data


def hold_body_settlements(boundary: RhythmBoundary) -> tuple[list[EventDraft], RhythmBoundary]:
    """@brief 零点边界：共同身体结算草稿连同唤醒字段留给零点节律入口，其余草稿随 advance 提交。

    该草稿在零点计划之后才提交，本边界的活动进度不能把它列为直接原因（caused_by 只指已提交事件），
    data.body_settlement_event_id 仍指向它。
    """
    held = [draft for draft in boundary.drafts
            if draft.event_type == "environment_event" and draft.data.get("kind") == "body_settlement"]
    held_ids = {draft.event_id for draft in held}
    committed = [draft.model_copy(update={"caused_by": [cause for cause in draft.caused_by if cause not in held_ids]})
                 for draft in boundary.drafts if draft.event_id not in held_ids]
    return committed, replace(boundary, drafts=held)


def apply_engine_rhythm(engine, staged, boundary: RhythmBoundary) -> "WorldState":
    """@brief 边界节律：结算段后接判定段（唤醒判定、静默留痕，并让被唤醒的活动对齐决策边界）。"""
    return decide_engine_rhythm(engine, settle_engine_rhythm(engine, staged, boundary), boundary)


def settle_engine_rhythm(engine, staged, boundary: RhythmBoundary) -> "WorldState":
    """@brief 引擎侧结算段：情绪账、按当前计划计的目标推进与活动进度草稿的 day_plan 快照。"""
    return settle_decision_rhythm(engine_rhythm_inputs(
        engine, staged, boundary.interval, boundary.arrivals, _boundary_progress_data(boundary)))


def decide_engine_rhythm(engine, staged, boundary: RhythmBoundary) -> "WorldState":
    """@brief 引擎侧判定段：唤醒判定与共同身体结算草稿的唤醒字段，并让被唤醒的活动对齐决策边界。"""
    from worldmock.life.activities import schedule_feedback
    from worldmock.life.engine import _put_activity

    staged, rhythms = decide_decision_rhythm(engine_rhythm_inputs(
        engine, staged, boundary.interval, boundary.arrivals, _boundary_progress_data(boundary)))
    for agent_id in rhythms:
        agent = staged.agents[agent_id]
        for activity in active_activities(staged, agent_id):
            if activity.next_interaction_at is not None and activity.next_interaction_at <= boundary.target:
                if activity.kind == ActivityKind.PLAY_GAME:
                    continue
                staged = _put_activity(staged, agent, schedule_feedback(
                    activity, boundary.target, engine.scenario.activity_feedback_seconds))
    return staged
