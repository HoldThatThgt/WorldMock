"""@brief 真实身体边界与固定唤醒条件的唯一装配。"""
from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo
from worldmock.footprints.models import EventDraft, validate_decision_rhythm
from worldmock.life import duty_edges, errands, felt, reminders
from worldmock.life.activities import shared_activity_factors, schedule_feedback
from worldmock.life.activity_control import is_pending_conclusion
from worldmock.life.body import BoundaryClock, apply_discomfort_pulse, next_shared_body_boundary_seconds
from worldmock.life.models import ActivityKind, ActivityStatus, WorldState, active_activities
from worldmock.life.projection import felt_values
from worldmock.life.schedule import NextDecisionQuery, WakeSnapshot, evaluate_wake_reasons, next_decision_time
from worldmock.life.world_ops import _put_activity, _resource_flows_data, meal_windows_by_agent

HUNGRY_BANDS = frozenset({"饿", "很饿", "饿得难受", felt.APPETITE_STARVING_LEVEL})
SLEEPY_BANDS = frozenset({"困", "很困", "撑不住"})

@dataclass(frozen=True, slots=True)
class RhythmBoundary:
    """@brief 一次实际共同边界的时刻、区间、到达事实与草稿。"""
    #: 当前绝对时刻。
    target: int
    #: 实际经过秒数。
    interval: int
    #: 本次真实到达的唤醒条件。
    arrivals: dict[int, set[str]]
    #: 同次提交的证据草稿。
    drafts: list[EventDraft]

def is_asleep(state: WorldState, agent_id: int) -> bool:
    """@brief 只有正在进行的真实睡眠构成睡眠状态。"""
    return any(item.kind == ActivityKind.SLEEP for item in active_activities(state, agent_id))

def local_hour(epoch: int, timezone_name: str) -> float:
    """@brief 运行时区中自午夜起的真实小时数。"""
    moment = datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))
    return moment.hour + moment.minute / 60 + moment.second / 3600

def awake_duty_edge_times(state: WorldState, inputs) -> list[int]:
    """@brief 睡眠不增加外部班次边界，清醒时读取同一工作安排。"""
    return [moment for agent_id in state.agents if not is_asleep(state, agent_id)
            for moment in duty_edges.edge_times_after(inputs, agent_id, state.current_time)]

def _felt_levels(context, state, agent) -> dict[str, str]:
    """@brief 观察与唤醒共用同一餐时窗口和四个体感。"""
    windows = meal_windows_by_agent(context.persona)[agent.agent_id]
    values = felt_values(agent, local_hour(state.current_time, context.config.timezone), windows)
    return felt.summarize_felt_levels(values,
        felt.EnergyReading(agent.body.energy_kcal, context.body.emergency_threshold_kcal))

def settle_engine_rhythm(context, staged: WorldState, boundary: RhythmBoundary) -> WorldState:
    """@brief 只结算实际清醒不适，不重复已经结算的社交和放松自然增长。"""
    changed = staged.model_copy(deep=True)
    for agent_id, agent in staged.agents.items():
        asleep_interval = any(draft.data.get("kind") == "body_settlement" and draft.agent_id == agent_id
            and any(staged.activities[key].kind == ActivityKind.SLEEP for key in draft.data["activity_ids"])
            for draft in boundary.drafts)
        if asleep_interval:
            agent = agent.model_copy(update={"discomfort_started_at": None, "discomfort_pulse_hours": 0.0}, deep=True)
        else:
            previous = context.state.agents[agent_id]
            before = _felt_levels(context, context.state, previous)
            agent = _settle_discomfort(agent, _felt_levels(context, staged, agent), boundary.target,
                                      boundary.target - boundary.interval, _has_discomfort(before))
        changed.agents[agent_id] = agent
        flows = _resource_flows_data(agent_id, boundary.target)
        for draft in boundary.drafts:
            if draft.agent_id == agent_id and draft.data.get("kind") == "body_settlement":
                draft.data["body_settlement"]["after"] = agent.body.model_dump(mode="json")
                draft.data["resource_flows"].extend(flows)
    return changed

def _has_discomfort(levels: dict[str, str]) -> bool:
    """@brief 饥饿或困意任一达到不适档就延续同一段清醒不适。"""
    return levels["appetite"] in HUNGRY_BANDS or levels["sleepiness"] in SLEEPY_BANDS


def _settle_discomfort(agent, levels: dict[str, str], now: int, interval_start: int, was_discomfort: bool):
    """@brief 先结算实际不适区间，再在缓解边界清除计时，初始不适从真实起点算起。"""
    started = agent.discomfort_started_at
    billed = agent.discomfort_pulse_hours
    if not was_discomfort or started is None:
        started, billed = (interval_start if was_discomfort else now), 0.0
    chargeable = max(0.0, (now - started) / 3600 - 2.0) if was_discomfort else 0.0
    new_charge = max(0.0, chargeable - billed)
    body = apply_discomfort_pulse(agent.body, new_charge) if new_charge else agent.body
    continuing = _has_discomfort(levels)
    return agent.model_copy(update={"body": body, "discomfort_started_at": started if continuing else None,
                                    "discomfort_pulse_hours": chargeable if continuing else 0.0}, deep=True)

def _wake_snapshot(context, state: WorldState, agent, boundary: RhythmBoundary) -> WakeSnapshot:
    """@brief 只由真实完成、到达和睡醒提供唤醒事实；session_open 就是本人有一段睡眠恰在本边界完成。"""
    now = state.current_time
    completed = [item for item in state.activities.values() if item.agent_id == agent.agent_id
                 and is_pending_conclusion(item) and item.ended_at == now]
    activities = active_activities(state, agent.agent_id)
    flags = boundary.arrivals.get(agent.agent_id, set())
    woke = any(item.kind == ActivityKind.SLEEP for item in completed)
    return WakeSnapshot(now=now, planned_end=now if completed else None, planned_end_is_sleep=woke,
        felt_bands_now=_felt_levels(context, state, agent),
        felt_bands_at_last_decision=agent.felt_bands_at_last_decision,
        delivery_arrived="delivery_arrival" in flags, last_decision_time=agent.last_decision_time,
        reconsideration_interval_seconds=context.config.reconsideration_interval_seconds,
        session_open=woke, progress_report_due=_progress_report_due(
            agent, state, context.config.progress_report_interval_seconds), no_active_activity=not activities,
        reminder_due=reminders.reminder_due(agent, now) or bool(errands.reminders_due(agent, now)),
        duty_boundary=bool(duty_edges.edges_at(context.inputs, agent.agent_id, now)),
        app_push_arrived="app_push" in flags, is_asleep=is_asleep(state, agent.agent_id))

def decide_engine_rhythm(context, staged: WorldState, boundary: RhythmBoundary) -> WorldState:
    """@brief 静默边界不调用模型；真实决策交付一次提醒并注解唯一身体草稿。"""
    changed = staged.model_copy(deep=True)
    for agent_id, agent in staged.agents.items():
        verdict = evaluate_wake_reasons(_wake_snapshot(context, staged, agent, boundary))
        if verdict.decision_offered and not is_asleep(staged, agent_id):
            agent = reminders.mark_reminded(agent, boundary.target)
        next_at = boundary.target if verdict.decision_offered else next_decision_at(context, staged, agent)
        changed.agents[agent_id] = agent.model_copy(update={"next_decision_at": next_at,
            "pending_wake_reasons": verdict.wake_reasons if verdict.decision_offered else []}, deep=True)
        for draft in boundary.drafts:
            if draft.agent_id == agent_id and draft.data.get("kind") == "body_settlement":
                draft.data.update(decision_offered=verdict.decision_offered, wake_reasons=verdict.wake_reasons)
                validate_decision_rhythm(draft.data)
        if verdict.decision_offered and not is_asleep(changed, agent_id):
            changed, drafts = errands.deliver_reminders(changed, agent_id)
            boundary.drafts.extend(drafts)
        for activity in active_activities(changed, agent_id):
            if activity.next_interaction_at is not None and activity.next_interaction_at <= boundary.target:
                changed = _put_activity(changed, changed.agents[agent_id], schedule_feedback(
                    activity, boundary.target, context.config.activity_feedback_seconds))
    return changed

def next_decision_at(context, state: WorldState, agent) -> int:
    """@brief 下一决策只取实际活动、身体、预排、提醒和班次边界。"""
    activities = active_activities(state, agent.agent_id)
    factors = tuple((item.kind, shared_activity_factors(item, agent, context.body)) for item in activities)
    clock = BoundaryClock(local_hour(state.current_time, context.config.timezone),
                          meal_windows_by_agent(context.persona)[agent.agent_id])
    seconds = next_shared_body_boundary_seconds(agent.body, factors, context.body, clock)
    candidate = next_decision_time(NextDecisionQuery(now=state.current_time,
        planned_end=min((item.planned_end_at for item in activities), default=None),
        felt_band_crossing_times=() if seconds is None else (state.current_time + seconds,),
        scheduled_arrival_times=tuple(item.due_at for item in state.pending_events
                                     if item.agent_id == agent.agent_id), last_decision_time=agent.last_decision_time,
        reconsideration_interval_seconds=context.config.reconsideration_interval_seconds,
        next_progress_report_at=_next_progress_report_at(agent, state, context.config.progress_report_interval_seconds),
        reminder_times=(*reminders.reminder_times(agent, state.current_time),
                        *errands.reminder_times(agent, state.current_time)),
        duty_edge_times=duty_edges.edge_times_after(context.inputs, agent.agent_id, state.current_time),
        is_asleep=is_asleep(state, agent.agent_id)))
    return state.end_time if candidate is None else min(candidate, state.end_time)

def apply_engine_rhythm(context, staged: WorldState, boundary: RhythmBoundary) -> WorldState:
    """@brief 每个边界先结算实际不适，再决定是否给人物机会。"""
    return decide_engine_rhythm(context, settle_engine_rhythm(context, staged, boundary), boundary)

def _report_anchor(activity) -> int:
    """@brief 真实开始、恢复或已给出播报机会的时刻是唯一锚点。"""
    return activity.report_anchor_at

def _progress_report_due(agent, state: WorldState, interval_seconds: int) -> bool:
    """@brief 仅正在进行的活动产生到点播报。"""
    return any(state.current_time - _report_anchor(item) >= interval_seconds
               for item in active_activities(state, agent.agent_id))

def _next_progress_report_at(agent, state: WorldState, interval_seconds: int) -> int | None:
    """@brief 严格晚于当前时刻的最早播报机会。"""
    candidates = (_report_anchor(item) + interval_seconds for item in active_activities(state, agent.agent_id))
    return min((moment for moment in candidates if moment > state.current_time), default=None)

def consume_progress_report_opportunity(state: WorldState, agent_id: int, interval_seconds: int) -> WorldState:
    """@brief 机会一旦给出即消费，不依赖人物是否成功调用工具。"""
    changed = state.model_copy(deep=True)
    for activity in active_activities(changed, agent_id):
        if state.current_time - _report_anchor(activity) >= interval_seconds:
            changed.activities[activity.activity_id] = activity.model_copy(
                update={"report_anchor_at": state.current_time})
    return changed

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
