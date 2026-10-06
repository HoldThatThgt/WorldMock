"""@brief 决策节律的引擎侧装配：唤醒判定、
静默边界留痕与下一决策候选（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」）。

结算边界（activity_feedback_seconds=900）照常结算资源；是否提供决策只由唤醒
条件决定——判定委托 life.schedule.evaluate_wake_reasons，本模块负责从世界状态
装配 WakeSnapshot、在同一边界推进情绪账（脉冲与衰减）、计算下一决策候选
（schedule.next_decision_time）。静默边界在 activity_progress.data 留
decision_offered=false 与 wake_reasons=[]；提供决策的边界记录实际命中的全部
唤醒原因。滞回参照=上次实际提供决策时的分级（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「滞回规则」）。
个人事务的提醒时刻只对清醒人物进入下一决策候选；判定为清醒人物提供决策的边界交付其全部到点的事务提醒
（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 ）；未完成事务不计入在手承诺（裁决 E6）。
班次与休息时段的起止（life.duty_edges）恰在边界时在唤醒判定里记对应成员；严格晚于此刻的起止进入下一决策候选
（睡眠中由 schedule.next_decision_time 剔除），清醒人物的起止另由 awake_duty_edge_times 进入下一时钟边界的候选，
睡眠判定复用 is_asleep（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界机制 ）。
"""

from __future__ import annotations
from worldmock.life.activities import shared_activity_factors
from worldmock.life import projection as projection_module
from worldmock.life.models import ActivityKind
from worldmock.life.activities import schedule_feedback
from worldmock.life.world_ops import _put_activity
from worldmock.footprints.models import EventDraft
from worldmock.life.world_ops import meal_windows_by_agent

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.footprints.models import DECISION_OFFERED_FIELD, WAKE_REASONS_FIELD, validate_decision_rhythm
from worldmock.life import duty_edges
from worldmock.life import errands
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
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, AgentState, WorldState, active_activities
from worldmock.life.projection import felt_values
from worldmock.life.scenario import BodyConfig, RuntimeScenario

#: 饥饿不适档（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」连续两小时以上饿）：appetite 分级达到"饿"及以上，
#: 含只由能量决定的第六档「饿得发慌」。
HUNGRY_BANDS = frozenset({"饿", "很饿", "饿得难受", felt.APPETITE_STARVING_LEVEL})
#: 困倦不适档：sleepiness 分级达到"困"及以上。
SLEEPY_BANDS = frozenset({"困", "很困", "撑不住"})

#: 分级文本 → 序数表（WakeSnapshot 以 int 比较分级，序数保序即可）。
_BAND_ORDER = (
    "不饿",
    "有点想吃",
    "饿",
    "很饿",
    "饿得难受",
    felt.APPETITE_STARVING_LEVEL,
    "清醒",
    "有点乏",
    "困",
    "很困",
    "撑不住",
    "不闷",
    "有点想放松",
    "很想放松",
    "不想找人",
    "想和人说说话",
    "有点孤单",
    "很烦",
    "有点烦",
    "平静",
    "挺好",
    "很开心",
)


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
    #: 播报间隔（docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面「决策轮循环与播报节律」）
    #: ：进行中活动距其播报锚点满该秒数触发唤醒。
    progress_report_interval_seconds: int = 1800
    #: 人物档案餐时窗口（agent_id -> MealWindows；滞回参照与观察同源）。
    meal_windows_by_agent: dict[int, felt.MealWindows] = field(default_factory=dict)
    #: 本 tick 由预排事件触发的唤醒旗标：agent_id -> {wake_reason 名}。
    arrival_flags: dict[int, set[str]] = field(default_factory=dict)
    #: 待注解的本人活动进度及唯一共同身体草稿；草稿的 activity_ids 也是本区间社交账所按的活动集合。
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

    零点边界（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「运行时机」）只在 advance 内执行本段，旧日末段的推进计入旧计划；
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
        verdict = rhythm_schedule.evaluate_wake_reasons(_wake_snapshot(agent, inputs, warning_facts, delivered))
        # 收件箱在唤醒判定之后才记为已读（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「人物之间的消息」）：
        # 未读消息在本边界成立 message_arrival，睡眠中由判定过滤。睡眠中也不读（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-不适脉冲与睡眠中的消息 ）：
        # 睡眠中送达的消息保持未读，醒来后的第一个边界才记已读并施加阅读效果。到点的提醒记为已触发，只触发一次。
        if not is_asleep(state, agent_id):
            agent = read_new_messages(agent)
        agent = reminders.mark_reminded(agent, state.current_time)
        if _game_turn_due(inputs.state, agent):
            # 游戏保持逐回合反馈（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」）
            # ：交互边界到期即提供决策，
            # 不经唤醒条件枚举；wake_reasons 记录实际命中的（通常为空）。
            verdict = rhythm_schedule.WakeVerdict(decision_offered=True, wake_reasons=verdict.wake_reasons)
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
            next_at = candidate if candidate is not None else (state.current_time + max(1, inputs.interval_seconds))
        agent = agent.model_copy(
            update={
                "next_decision_at": next_at,
                "pending_wake_reasons": reasons if offered else [],
                "delivered_warning_signatures": delivered,
            },
            deep=True,
        )
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
    arrivals = [
        item.due_at
        for item in state.pending_events
        if item.due_at > state.current_time and item.agent_id == agent.agent_id
    ]
    return rhythm_schedule.next_decision_time(
        rhythm_schedule.NextDecisionQuery(
            now=state.current_time,
            planned_end=min((item.planned_end_at for item in activities), default=None),
            felt_band_crossing_times=tuple(felt_crossings),
            scheduled_arrival_times=tuple(arrivals),
            last_decision_time=agent.last_decision_time,
            reconsideration_interval_seconds=inputs.reconsideration_interval_seconds,
            idle_reconsideration_interval_seconds=inputs.idle_reconsideration_interval_seconds,
            has_commitments=_has_commitments(agent, inputs),
            next_task_due_at=_next_task_due(agent, inputs),
            next_progress_report_at=_next_progress_report_at(agent, state, inputs.progress_report_interval_seconds),
            plan_reminder_times=reminders.reminder_times(agent, state.current_time),
            errand_reminder_times=errands.reminder_times(agent, state.current_time),
            duty_edge_times=duty_edges.edge_times_after(inputs.scenario, agent.agent_id, state.current_time),
            is_asleep=any(item.kind == ActivityKind.SLEEP for item in activities),
        )
    )


def _body_boundary_seconds(agent: AgentState, activities: list, inputs: RhythmInputs) -> int | None:
    """@brief 体感跨档、餐时窗口与能量阈值的下一候选（与引擎共同结算同源系数，交谈、通话照常预测）。"""

    factors = tuple((item.kind, shared_activity_factors(item, agent, inputs.body_config)) for item in activities)
    clock = BoundaryClock(
        local_hour(inputs.state.current_time, inputs.scenario.timezone),
        inputs.meal_windows_by_agent.get(agent.agent_id),
    )
    return next_shared_body_boundary_seconds(agent.body, factors, inputs.body_config, clock)


def _settle_mood(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 边界情绪账：先按半衰期衰减，再叠加本区间事件脉冲（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）。

    新收件箱消息的已读与脉冲不在此处，而在唤醒判定之后结算（read_new_messages），睡眠中不读。
    """
    now = inputs.state.current_time
    if agent.mood_updated_at is not None:
        agent = agent.model_copy(update={"body": decay_mood(agent.body, (now - agent.mood_updated_at) / 60)}, deep=True)
    agent = _apply_awake_social_ledger(agent, inputs)
    agent = _settle_discomfort(agent, _felt_levels(agent, inputs), now)
    agent = _goal_and_deadline_pulses(agent, inputs)
    return agent.model_copy(update={"mood_updated_at": now}, deep=True)


def _settle_discomfort(agent: AgentState, levels: dict[str, str], now: int) -> AgentState:
    """@brief 连续饿或困的不适脉冲（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」，
    docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-不适脉冲与睡眠中的消息 ）。

    两小时门槛（felt.MOOD_DISCOMFORT_TRIGGER_HOURS）只在这里按连续不适时长判定一次：超出门槛的小时数减去已计费的，
    即本边界新增的计费小时，按 −0.1/小时施加（felt.discomfort_pulse 不再判门槛）；只有实际施加了脉冲（新增计费小时
    为正）才把这些小时记为已计费。不适解除时起点与已计费小时一并清零。

    @param agent 人物状态（已衰减情绪、已记社交账）
    @param levels 边界时刻的体感分级
    @param now 当前边界时刻
    """
    if levels["appetite"] not in HUNGRY_BANDS and levels["sleepiness"] not in SLEEPY_BANDS:
        return agent.model_copy(update={"discomfort_started_at": None, "discomfort_pulse_hours": 0.0}, deep=True)
    started = agent.discomfort_started_at if agent.discomfort_started_at is not None else now
    chargeable = max(0.0, (now - started) / 3600 - felt.MOOD_DISCOMFORT_TRIGGER_HOURS)
    new_charge = chargeable - agent.discomfort_pulse_hours
    update: dict[str, Any] = {"discomfort_started_at": started}
    if new_charge > 0:
        update.update(
            body=apply_discomfort_pulse(agent.body, new_charge),
            discomfort_pulse_hours=agent.discomfort_pulse_hours + new_charge,
        )
    return agent.model_copy(update=update, deep=True)


def read_new_messages(agent: AgentState, only: set[str] | None = None) -> AgentState:
    """@brief 新收件箱消息计为已读：社交需要 −0.05/条 + 情绪
    +0.1/条（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）。

    边界在唤醒判定之后调用，使未读消息先成立 message_arrival
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「人物之间的消息」）。决策轮提交时以 only 限定为本轮观察全文已呈现的
    消息（观察的 presented_message_ids 与轮前收件箱的交集，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章「直接消息截断」）：
    全文看过的消息不在其后的边界再次唤醒，没有全文呈现的与本轮内送达的消息仍未读。人物在睡时两条路径都不调用
    （is_asleep，docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-不适脉冲与睡眠中的消息 ）：睡眠中送达的消息在醒来后的第一个边界才读。

    :param agent: 人物状态
    :param only: 只读这些消息身份；None 表示全部未读消息
    """
    fresh = [
        message
        for message_id, message in agent.inbox.items()
        if message_id not in agent.read_inbox_ids and (only is None or message_id in only)
    ]
    if not fresh:
        return agent
    body = apply_social_ledger(agent.body, felt.SocialExposure(messages_read=len(fresh)))
    for _message in fresh:
        body = apply_mood_pulse(body, "message_received")
    read = set(agent.read_inbox_ids) | {message.message_id for message in fresh}
    return agent.model_copy(update={"body": body, "read_inbox_ids": read}, deep=True)


def _apply_awake_social_ledger(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 清醒社交需要增长 +0.04/h 与共同用餐
    −0.3/h（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）。

    是否在睡、交谈与通话覆盖、共同用餐都按本区间共同身体结算所用的活动集合判定（_interval_activities，
    docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-区间事实只有一个来源社交账与共同用餐 ），不在推进后重新推断：在本边界结束的睡眠整段仍按睡眠计，不计清醒增长；
    计费小时取本边界的区间（inputs.interval_seconds）。CHAT/CALL 活动区间的账本由 life.social_actions.advance_social
    在活动结算内推进（含清醒增长），此处跳过避免重复计数。
    """
    activities = _interval_activities(agent, inputs)
    if _social_activity_covered(activities):
        return agent
    asleep = any(item.kind == ActivityKind.SLEEP for item in activities)
    hours = 0.0 if asleep else inputs.interval_seconds / 3600
    shared = hours if _shared_meal(agent, inputs, activities) else 0.0
    if hours <= 0 and shared <= 0:
        return agent
    body = apply_social_ledger(agent.body, felt.SocialExposure(awake_hours=hours, shared_meal_hours=shared))
    return agent.model_copy(update={"body": body}, deep=True)


def _interval_activities(agent: AgentState, inputs: RhythmInputs) -> list[ActivityState]:
    """@brief 本结算区间的本人活动集合（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-区间事实只有一个来源社交账与共同用餐 「区间事实只有一个来源」）。

    取本边界共同身体结算草稿（RhythmInputs.progress_data，零点路径同样携带）的 activity_ids：推进前进行中、结算
    本区间共同身体所用的活动。_next_epoch 把每个活动的计划结束、未触发的提醒与预排事件都当作边界，区间内集合恒定。
    没有草稿时（直接调用或单测）兜底为进行中或恰在本边界结束（COMPLETED 且 ended_at == now）的本人活动，
    与 _woke_from_sleep 同一口径。

    @param agent 人物状态
    @param inputs 本边界的节律输入
    @return 本区间的活动（推进后的权威状态，按草稿顺序或活动身份顺序）
    """
    state = inputs.state
    for data in inputs.progress_data.get(agent.agent_id, []):
        if data.get("kind") == "body_settlement":
            return [state.activities[activity_id] for activity_id in data["activity_ids"]]
    now = state.current_time
    return [
        item
        for item in state.activities.values()
        if item.agent_id == agent.agent_id
        and (item.status == ActivityStatus.ACTIVE or item.status == ActivityStatus.COMPLETED and item.ended_at == now)
    ]


def _social_activity_covered(activities: list[ActivityState]) -> bool:
    """@brief 本区间是否由 CHAT/CALL 活动结算推进（其内部已应用账本）：区间活动集合里有交谈或通话。"""
    return any(item.kind in {ActivityKind.CHAT, ActivityKind.CALL} for item in activities)


def _shared_meal(agent: AgentState, inputs: RhythmInputs, activities: list[ActivityState]) -> bool:
    """@brief 本区间是否共同用餐：区间活动集合里有堂食的进食（在本边界吃完的一餐也算），且此刻同地点有他人在场。"""
    eating = any(
        item.kind == ActivityKind.CONSUME and item.data.get("request", {}).get("use_mode") == "eat"
        for item in activities
    )
    return eating and bool(_people_here(inputs.state, agent))


def is_asleep(state: WorldState, agent_id: int) -> bool:
    """@brief 人物此刻是否在睡：有进行中的睡眠活动（边界判定段与决策轮提交记已读共用这一口径，睡眠中不读消息）。

    @param state 世界状态（边界判定段取推进后的状态，决策轮取轮前观察所用的状态）
    @param agent_id 人物身份
    """
    return any(item.kind == ActivityKind.SLEEP for item in active_activities(state, agent_id))


def awake_duty_edge_times(state: WorldState, scenario: RuntimeScenario) -> list[int]:
    """@brief 下一时钟边界的班次与休息时段起止候选：每个清醒人物（is_asleep 为假）严格晚于此刻的起止时刻；睡眠中的
    人物不计入，不形成静默边界，睡过的起止醒来后不补（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界机制 ）。

    @param state 世界状态
    @param scenario 运行场景（人物的工作安排与时区）
    """
    return [moment for agent_id in state.agents if not is_asleep(state, agent_id)
            for moment in duty_edges.edge_times_after(scenario, agent_id, state.current_time)]


def _goal_and_deadline_pulses(agent: AgentState, inputs: RhythmInputs) -> AgentState:
    """@brief 实际目标首次推进产生情绪脉冲，任务到期未交产生失约脉冲。"""
    now = inputs.state.current_time
    body = agent.body
    plan = agent.day_plan
    for data in inputs.progress_data.get(agent.agent_id, []):
        if plan is None or "activity" not in data or data["activity"].get("agent_id") != agent.agent_id:
            continue
        changed = rhythm_schedule.progress_day_plan(
            plan, data["activity"], data["interval_start"], data["interval_end"]
        )
        if changed != plan:
            body = apply_mood_pulse(body, "goal_progressed")
            plan = changed
    window_start = now - inputs.interval_seconds
    for task_id in sorted(agent.visible_task_ids):
        due = inputs.task_due.get(task_id)
        if due is not None and task_id not in inputs.state.paid_task_ids and window_start < due <= now:
            body = apply_mood_pulse(body, "task_deadline_missed")
    return agent.model_copy(update={"body": body, "day_plan": plan}, deep=True)


def _has_commitments(agent: AgentState, inputs: RhythmInputs) -> bool:
    """@brief 在手承诺：未推进目标、可见未完成任务、暂停活动或等待之外的进行中活动。

    进行中的等待不算在手承诺（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「拒绝的情绪代价与重估间隔」）：
    只在等待（或无活动）且没有其余各项时返回假，此时用空闲重估间隔。
    """
    plan = agent.day_plan
    if (
        plan is not None
        and not rhythm_schedule.day_plan_expired(plan, inputs.state.current_time)
        and any(goal.progressed_at is None for goal in plan.goals)
    ):
        return True
    if any(task_id not in inputs.state.paid_task_ids for task_id in agent.visible_task_ids):
        return True
    return any(
        activity.agent_id == agent.agent_id
        and (
            activity.status == ActivityStatus.PAUSED
            or activity.status == ActivityStatus.ACTIVE
            and activity.kind != ActivityKind.WAIT
        )
        for activity in inputs.state.activities.values()
    )


def _task_signature(task_id: str, due_at: int) -> str:
    """@brief 将任务身份和具体截止编码为稳定事实签名。"""
    return json.dumps(["task", task_id, due_at], ensure_ascii=False, separators=(",", ":"))


def _next_task_due(agent: AgentState, inputs: RhythmInputs, eligible: set[str] | None = None) -> int | None:
    """@brief 返回尚未交付预警的所有可见未完成任务中的最近截止。"""
    signatures = eligible
    if signatures is None:
        signatures = set().union(*_warning_signatures(agent, inputs).values())
        signatures |= {
            _task_signature(task_id, due)
            for task_id, due in inputs.task_due.items()
            if task_id in agent.visible_task_ids and task_id not in inputs.state.paid_task_ids
        }
        signatures -= agent.delivered_warning_signatures
    return min(
        (
            due
            for task_id, due in inputs.task_due.items()
            if task_id in agent.visible_task_ids
            and task_id not in inputs.state.paid_task_ids
            and _task_signature(task_id, due) in signatures
            and due > inputs.state.current_time
        ),
        default=None,
    )


def _warning_signatures(agent: AgentState, inputs: RhythmInputs) -> dict[str, set[str]]:
    """@brief 仅记录本阶段仍然有效的真实工作截止预警签名。"""
    now = inputs.state.current_time
    task = {_task_signature(task_id, due) for task_id, due in inputs.task_due.items()
            if task_id in agent.visible_task_ids and task_id not in inputs.state.paid_task_ids
            and due - rhythm_schedule.TASK_DUE_SOON_SECONDS <= now < due}
    return {"task": task}




def _delivered_warning_signatures(facts: dict[str, set[str]], verdict: rhythm_schedule.WakeVerdict) -> set[str]:
    """@brief 只把本次实际提供的具体预警标为已交付。"""
    reasons = set(verdict.wake_reasons)
    delivered: set[str] = set()
    if rhythm_schedule.WakeReason.TASK_DUE_SOON in reasons:
        delivered.update(facts["task"])
    return delivered


def _wake_snapshot(
    agent: AgentState, inputs: RhythmInputs, facts: dict[str, set[str]], delivered: set[str]
) -> rhythm_schedule.WakeSnapshot:
    """@brief 装配该边界的唤醒判定输入（与上一次实际决策比较）。"""
    state = inputs.state
    now = state.current_time
    activities = active_activities(state, agent.agent_id)
    # 活动由世界在本边界结束（计划结束、到达或到达被拒、随出行结束）即待收尾：planned_end 必给决策，同秒收尾。
    completed_now = any(
        item.agent_id == agent.agent_id and is_pending_conclusion(item) and item.ended_at == now
        for item in state.activities.values()
    )
    levels = _felt_levels(agent, inputs)
    flags = inputs.arrival_flags.get(agent.agent_id, set())
    return rhythm_schedule.WakeSnapshot(
        now=now,
        planned_end=now if completed_now else None,
        felt_bands_now={key: _band_index(value) for key, value in levels.items()},
        felt_bands_at_last_decision={
            key: _band_index(value) for key, value in agent.felt_bands_at_last_decision.items()
        },
        message_arrived=any(message_id not in agent.read_inbox_ids for message_id in agent.inbox)
        or "message_arrival" in flags,
        delivery_arrived="delivery_arrival" in flags,
        task_newly_visible="task_available" in flags,
        next_task_due_at=_next_task_due(agent, inputs, facts["task"] - delivered),
        work_review_returned="work_review_result" in flags,
        last_decision_time=agent.last_decision_time,
        reconsideration_interval_seconds=inputs.reconsideration_interval_seconds,
        idle_reconsideration_interval_seconds=inputs.idle_reconsideration_interval_seconds,
        has_commitments=_has_commitments(agent, inputs),
        people_present_now=frozenset(item["name"] for item in _people_here(state, agent)),
        people_present_before=agent.people_at_last_decision,
        discovery_result_ready=("discovery_result" in agent.pending_wake_reasons and agent.next_decision_at <= now),
        wake_from_sleep=_woke_from_sleep(state, agent.agent_id, now),
        run_start=agent.last_decision_time is None,
        progress_report_due=_progress_report_due(agent, state, inputs.progress_report_interval_seconds),
        no_active_activity=not activities,
        plan_reminder_due=reminders.reminder_due(agent, now),
        errand_reminder_due=bool(errands.reminders_due(agent, now)),
        duty_edges_now=duty_edges.edges_at(inputs.scenario, agent.agent_id, now),
        is_asleep=any(item.kind == ActivityKind.SLEEP for item in activities),
    )


def _people_here(state: WorldState, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 同地点他人（投影闭集，委托 life.projection）。"""

    return projection_module.people_here_views(state, agent)


def _felt_levels(agent: AgentState, inputs: RhythmInputs) -> dict[str, str]:
    """@brief 边界时刻的体感分级：饥饿按同一能量与紧急阈值叠加低能量规则，与观察的决策参照同源。"""
    values = felt_values(
        agent,
        local_hour(inputs.state.current_time, inputs.scenario.timezone),
        inputs.meal_windows_by_agent.get(agent.agent_id),
    )
    return felt.summarize_felt_levels(
        values, felt.EnergyReading(agent.body.energy_kcal, inputs.body_config.emergency_threshold_kcal)
    )


def _band_index(level: str) -> int:
    """@brief 分级文本 → 序数（未知文本取 −1，键只增不减地视为变化）。"""
    return _BAND_ORDER.index(level) if level in _BAND_ORDER else -1






def _current_location(agent: AgentState, inputs: RhythmInputs):
    """@brief 当前地点配置。"""
    for location in inputs.scenario.locations:
        if location.location_id == agent.location_id:
            return location
    return None




def _game_turn_due(state: WorldState, agent: AgentState) -> bool:
    """@brief 游戏交互边界到期（next_interaction_at == now 且活动进行中）。"""

    return any(
        item.kind == ActivityKind.PLAY_GAME and item.next_interaction_at == state.current_time
        for item in active_activities(state, agent.agent_id)
    )


def _woke_from_sleep(state: WorldState, agent_id: int, now: int) -> bool:
    """@brief 睡眠活动恰在本边界结束（planned_end 到达）即 wake_from_sleep。"""
    for activity in state.activities.values():
        if (
            activity.agent_id == agent_id
            and activity.kind == ActivityKind.SLEEP
            and activity.status == ActivityStatus.COMPLETED
            and activity.ended_at == now
        ):
            return True
    return False


def _report_anchor(activity) -> int:
    """@brief 活动的播报锚点：开始、恢复或上次对该活动播报的时刻（未经受理路径构造的活动取开始时刻）。"""
    return activity.report_anchor_at if activity.report_anchor_at is not None else activity.started_at


def _progress_report_due(agent: AgentState, state: WorldState, interval_seconds: int) -> bool:
    """@brief 本人某项进行中活动距其播报锚点已满间隔时触发播报（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第八章「活动」）。

    只遍历进行中活动；暂停、待收尾与已收尾的活动不播报。

    @param agent 当前人物状态
    @param state 世界状态
    @param interval_seconds 播报间隔秒数
    """
    now = state.current_time
    return any(
        now - _report_anchor(activity) >= interval_seconds for activity in active_activities(state, agent.agent_id)
    )


def _next_progress_report_at(agent: AgentState, state: WorldState, interval_seconds: int) -> int | None:
    """@brief 本人进行中活动中最早的下一播报时刻（锚点加间隔，严格晚于当前时刻）。

    @param agent 当前人物状态
    @param state 世界状态
    @param interval_seconds 播报间隔秒数
    """
    now = state.current_time
    candidates = [_report_anchor(activity) + interval_seconds for activity in active_activities(state, agent.agent_id)]
    return min((moment for moment in candidates if moment > now), default=None)


def local_hour(epoch: int, timezone_name: str) -> float:
    """@brief 场景时区自午夜起的小时数。"""
    moment = datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))
    return moment.hour + moment.minute / 60 + moment.second / 3600


def engine_rhythm_inputs(
    context, state, interval: int = 0, arrival_flags: dict | None = None, progress_data: dict | None = None
) -> RhythmInputs:
    """@brief 引擎侧装配决策节律输入束（任务截止表、重估间隔与餐时窗口）。"""
    return RhythmInputs(
        state=state,
        scenario=context.scenario,
        body_config=context.semantics.body,
        task_due={task_id: int(task.due_at.timestamp()) for task_id, task in context.rules.tasks.items()},
        interval_seconds=interval or context.scenario.activity_feedback_seconds,
        reconsideration_interval_seconds=context.semantics.document.reconsideration_interval_seconds,
        idle_reconsideration_interval_seconds=context.semantics.document.idle_reconsideration_interval_seconds,
        progress_report_interval_seconds=context.semantics.document.progress_report_interval_seconds,
        meal_windows_by_agent=meal_windows_by_agent(context.scenario),
        arrival_flags=arrival_flags or {},
        progress_data=progress_data or {},
    )


def _boundary_progress_data(boundary: RhythmBoundary) -> dict[int, list[dict]]:
    """@brief 本边界待注解的本人活动进度与唯一共同身体结算草稿（按人物归组，注解就地写入草稿）。"""
    progress_data: dict[int, list[dict]] = {}
    for draft in boundary.drafts:
        if (
            draft.event_type == "activity_progress"
            or draft.event_type == "environment_event"
            and draft.data.get("kind") == "body_settlement"
        ) and draft.agent_id is not None:
            progress_data.setdefault(draft.agent_id, []).append(draft.data)
    return progress_data


def hold_body_settlements(boundary: RhythmBoundary) -> tuple[list[EventDraft], RhythmBoundary]:
    """@brief 零点边界：共同身体结算草稿连同唤醒字段留给零点节律入口，其余草稿随 advance 提交。

    该草稿在零点计划之后才提交，本边界的活动进度不能把它列为直接原因（caused_by 只指已提交事件），
    data.body_settlement_event_id 仍指向它。
    """
    held = [
        draft
        for draft in boundary.drafts
        if draft.event_type == "environment_event" and draft.data.get("kind") == "body_settlement"
    ]
    held_ids = {draft.event_id for draft in held}
    committed = [
        draft.model_copy(update={"caused_by": [cause for cause in draft.caused_by if cause not in held_ids]})
        for draft in boundary.drafts
        if draft.event_id not in held_ids
    ]
    return committed, replace(boundary, drafts=held)


def apply_engine_rhythm(context, staged, boundary: RhythmBoundary) -> "WorldState":
    """@brief 边界节律：结算段后接判定段（唤醒判定、静默留痕，并让被唤醒的活动对齐决策边界）。"""
    return decide_engine_rhythm(context, settle_engine_rhythm(context, staged, boundary), boundary)


def settle_engine_rhythm(context, staged, boundary: RhythmBoundary) -> "WorldState":
    """@brief 引擎侧结算段：情绪账、按当前计划计的目标推进与活动进度草稿的 day_plan 快照。"""
    return settle_decision_rhythm(
        engine_rhythm_inputs(context, staged, boundary.interval, boundary.arrivals, _boundary_progress_data(boundary))
    )


def decide_engine_rhythm(context, staged, boundary: RhythmBoundary) -> "WorldState":
    """@brief 引擎侧判定段：唤醒判定与共同身体结算草稿的唤醒字段，交付到点的个人事务提醒，并让被唤醒的活动对齐决策边界。"""

    staged, rhythms = decide_decision_rhythm(
        engine_rhythm_inputs(context, staged, boundary.interval, boundary.arrivals, _boundary_progress_data(boundary))
    )
    staged = _deliver_errand_reminders(staged, rhythms, boundary)
    for agent_id in rhythms:
        agent = staged.agents[agent_id]
        for activity in active_activities(staged, agent_id):
            if activity.next_interaction_at is not None and activity.next_interaction_at <= boundary.target:
                if activity.kind == ActivityKind.PLAY_GAME:
                    continue
                staged = _put_activity(
                    staged,
                    agent,
                    schedule_feedback(activity, boundary.target, context.scenario.activity_feedback_seconds),
                )
    return staged


def _deliver_errand_reminders(
    staged: WorldState, rhythms: dict[int, AgentRhythm], boundary: RhythmBoundary
) -> WorldState:
    """@brief 交付本边界的个人事务提醒（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 「调度口径」）：由唤醒判定决定，不看时钟——
    判定结果含 errand_reminder_due 的人物（清醒且有已到点未提醒的事务，本边界为他提供了决策机会，含刚醒来的边界）把
    全部到点的事务一并交付，提醒事件草稿追加到本边界的草稿（平常边界随 advance 提交，零点边界随零点节律入口提交）；
    睡眠中的人物判定被过滤，不交付也不消耗，留到醒来的边界。"""
    for agent_id, verdict in rhythms.items():
        if rhythm_schedule.WakeReason.ERRAND_REMINDER_DUE.value in verdict.wake_reasons:
            staged, drafts = errands.deliver_reminders(staged, agent_id)
            boundary.drafts.extend(drafts)
    return staged
