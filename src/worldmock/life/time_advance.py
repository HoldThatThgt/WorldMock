"""@brief TimeAdvanceCoordinator 的独立生活运行职责。"""

from __future__ import annotations
from worldmock.life import sleep as sleep_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentsociety2.society import SchedulingState
from worldmock.life.readable import _datetime
from worldmock.life.world_ops import _body_settlement_data
from worldmock.life.world_ops import _put_agent
from worldmock.life.world_ops import _resource_flows_data

import hashlib
import random
from datetime import datetime
from worldmock.errors import ProtocolError
from worldmock.footprints.models import RESOURCE_FLOWS_FIELD, EventDraft
from worldmock.life import (
    activity_control,
    day_planning,
    errands,
    projection,
    reminders,
    same_moment,
    sessions,
    social_actions,
)
from worldmock.life.action_evidence import _origin_terminal_draft, _origin_terminal_result, _terminal_action_records
from worldmock.life.activity_titles import activity_title
from worldmock.life.activities import (
    ActivityAdvance,
    advance_activity_shared,
    end_sleep_by_reminder,
    shared_activity_factors,
    truncate_activity,
)
from worldmock.life.body import ActivityFactors, BoundaryClock, next_shared_body_boundary_seconds, settle_shared_body
from worldmock.life.action_run import settle_action
from worldmock.life.fuel import charge_driving_days
from worldmock.life.work import work_completion_draft
from worldmock.scheduling.events import ScheduledKind
from worldmock.life.models import (
    ActionResult,
    ActivityKind,
    ActivityStatus,
    PlanDayRequest,
    WorldState,
    active_activities,
)
from worldmock.life.observe import ObservationBuilder
from worldmock.life.rhythm import (
    RhythmBoundary,
    apply_engine_rhythm,
    awake_duty_edge_times,
    decide_engine_rhythm,
    hold_body_settlements,
    settle_engine_rhythm,
)
from worldmock.life.schedule import DayPlan, expired_plan_agent_ids
from worldmock.life.models import validated_payload
from worldmock.life.presentation import flush_presentation, stage_presentation
from worldmock.life.travel import persisted_trip, record_arrival, update_travel
from worldmock.scheduling.effects import process_scheduled_events

from worldmock.life.engine_context import EngineContext
from worldmock.life.activity_titles import activity_title, activity_snapshot
from worldmock.life.world_ops import place_names, meal_windows_by_agent

class TimeAdvanceCoordinator:
    """@brief 显式依赖上下文的TimeAdvanceCoordinator。"""

    def __init__(self, context: EngineContext, observations: ObservationBuilder):
        """@brief 绑定状态与浏览投影，零点边界只归推进协调。"""
        self.context, self.observations = context, observations
        self.midnight: RhythmBoundary | None = None

    def scheduling_state(self) -> "SchedulingState":
        """@brief 返回 AS2 使用的带时区当前时刻、就绪人物和下一边界。"""
        from agentsociety2.society import SchedulingState

        self._require_midnight_decided()
        current = _datetime(self.context.state.current_time, self.context.scenario.timezone)
        if self.context.state.current_time >= self.context.state.end_time:
            return SchedulingState(current, (), None, self.context.state.version)
        ready = self._ready_agent_ids()
        next_at = _datetime(self._next_epoch(), self.context.scenario.timezone)
        return SchedulingState(current, ready, next_at, self.context.state.version)

    def _ready_agent_ids(self) -> tuple[int, ...]:
        """@brief 以冻结随机状态为同秒竞争人物生成可恢复顺序。"""
        ready = sorted((agent.agent_id for agent in self.context.state.agents.values() if self._ready(agent.agent_id)))
        material = (
            f"{self.context.state.random_state}:{self.context.state.current_time}:{self.context.state.version}".encode()
        )
        seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        random.Random(seed).shuffle(ready)
        return tuple(ready)

    def advance(self, tick: int, target: datetime, round_open: bool) -> list[ActionResult]:
        """@brief 精确推进到下一事件边界并结算连续活动、后台事件与决策节律。"""
        if round_open:
            raise ProtocolError("World time cannot advance during an open decision round")
        self._require_midnight_decided()
        if any((self._ready(agent_id) for agent_id in self.context.state.agents)):
            raise ProtocolError("World time cannot advance while an agent decision is due")
        target_epoch = int(target.timestamp())
        if tick <= 0 or target_epoch - self.context.state.current_time != tick or target_epoch != self._next_epoch():
            raise ProtocolError("World advance must match the next scheduling boundary")
        interval = target_epoch - self.context.state.current_time
        staged = flush_presentation(stage_presentation(self.context.state, self.observations.pending_presentation))
        staged = staged
        staged = staged.model_copy(update={"current_time": target_epoch}, deep=True)
        staged = same_moment.reset_same_moment(staged)
        (drafts, results) = self._advance_activities(staged, target_epoch)
        (staged, background, arrivals) = self._process_scheduled(staged, target_epoch)
        drafts.extend(background)
        staged = day_planning.settle_receipt_goals(staged, drafts)
        (staged, credited) = errands.settle(staged, drafts)
        drafts.extend(credited)
        boundary = RhythmBoundary(target_epoch, interval, arrivals, drafts)
        if reminders.defers_rhythm(staged, target_epoch, self.context.scenario.timezone):
            staged = settle_engine_rhythm(self.context, staged, boundary)
            (drafts, self.midnight) = hold_body_settlements(boundary)
        else:
            staged = apply_engine_rhythm(self.context, staged, boundary)
        for agent_id in expired_plan_agent_ids(staged):
            staged = _put_agent(staged, staged.agents[agent_id].model_copy(update={"day_plan": None}, deep=True))
        if target_epoch == staged.end_time:
            drafts.extend(self._truncate(staged, target_epoch))
            staged = sessions.end_all(staged)
            drafts.append(
                EventDraft(
                    event_type="run_ended",
                    occurred_at=target_epoch,
                    visibility="system",
                    data={"end_time": target_epoch},
                )
            )
        staged.version = self.context.state.version + 1
        self.context.store.commit(validated_payload(staged), drafts)
        self.context.state = staged
        self.observations.pending_presentation = {}
        return results

    @property
    def midnight_rhythm_pending(self) -> bool:
        """@brief 零点边界已结算提交、判定段尚待零点计划提交之后执行。"""
        return self.midnight is not None

    def decide_midnight_rhythm(self) -> None:
        """@brief 零点节律入口（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「运行时机」）：零点计划提交之后只执行判定段。

        判定该秒的唤醒原因与 next_decision_at（在手承诺按新计划计，新计划的提醒进入候选），写共同身体结算的唤醒字段，
        并与活动反馈对齐一次提交；不再执行结算段的情绪账，已读消息与社交账不会重复记账。
        """
        boundary = self.midnight
        if boundary is None:
            raise ProtocolError("No midnight rhythm is pending")
        staged = decide_engine_rhythm(self.context, self.context.state.model_copy(deep=True), boundary)
        staged.version = self.context.state.version + 1
        self.context.store.commit(validated_payload(staged), list(boundary.drafts))
        self.context.state = staged
        self.midnight = None

    def register_day_plan(
        self, agent_id: int, request: PlanDayRequest, sources: list[str], evidence: list[EventDraft]
    ) -> DayPlan:
        """@brief 零点计划的系统登记入口（第四章）：不进人物工具面，不写 action_requested。

        校验目标，写 day_plan 与 plan_submitted_dates，与 day_plan_generated（带 data.day_plan 与 data.source_event_ids）
        及本次的模型证据一次提交；当日已登记为协议错误，调用方先按 plan_submitted_dates 拦下。
        """
        causes = [evidence[-1].event_id] if evidence else []
        (staged, plan, draft) = day_planning.registration(
            self.context.state, agent_id, request, (sources, causes), self.context.scenario.timezone
        )
        staged.version = self.context.state.version + 1
        self.context.store.commit(validated_payload(staged), [*evidence, draft])
        self.context.state = staged
        return plan

    def _require_midnight_decided(self) -> None:
        """@brief 零点判定段未执行时拒绝调度、推进与检查点（半结算的边界不能提供决策或封存）。"""
        if self.midnight is not None:
            raise ProtocolError("Midnight rhythm must be decided after the day plans are registered")

    def _advance_activities(self, state: WorldState, target: int) -> tuple[list[EventDraft], list[ActionResult]]:
        """@brief 每个人先结算共同身体，再独立推进全部正在执行的活动。"""
        drafts: list[EventDraft] = []
        results: list[ActionResult] = []
        for agent_id in sorted(state.agents):
            agent = state.agents[agent_id]
            interval_start = agent.body_settled_at
            if interval_start == target:
                continue
            activities = active_activities(state, agent_id)
            factors = tuple(((item.kind, self._shared_factors(item, agent)) for item in activities))
            settlement = settle_shared_body(agent.body, factors, target - interval_start, self.context.semantics.body)
            body_event = EventDraft(
                event_type="environment_event",
                occurred_at=target,
                agent_id=agent_id,
                source_kind="environment",
                caused_by=list(dict.fromkeys((item.origin_request_event_id for item in activities))),
                data={
                    "kind": "body_settlement",
                    "interval_start": interval_start,
                    "interval_end": target,
                    "activity_ids": [item.activity_id for item in activities],
                    "body_settlement": _body_settlement_data(settlement),
                    RESOURCE_FLOWS_FIELD: _resource_flows_data(agent_id, target),
                },
            )
            drafts.append(body_event)
            state.agents[agent_id] = agent.model_copy(
                update={"body": settlement.after, "body_settled_at": target}, deep=True
            )
            for activity in activities:
                (progress, terminal) = self._advance_one_activity(
                    state, activity, target, settlement, body_event.event_id
                )
                drafts.append(progress)
                content = progress.data.get("content")
                config = next(item for item in self.context.scenario.agents if item.agent_id == agent_id)
                if content is not None and content["completed"]:
                    completed = work_completion_draft(state, activity,
                        self.context.semantics.catalog.contents[content["content_id"]],
                        progress.event_id, config.work_content_ids)
                    if completed is not None:
                        drafts.append(completed)
                if terminal is not None:
                    (result, event) = terminal
                    results.append(result)
                    drafts.append(event)
            self._switch_session_on_arrival(state, agent_id, target)
        return (drafts, results)

    def _switch_session_on_arrival(self, state: WorldState, agent_id: int, target: int) -> None:
        """@brief 到达（含到达被拒）的边界：结束路上会话，在所到地点开始停留会话（就地写入本次推进的世界副本）。"""
        if sessions.arriving_transit(state, agent_id, target) is None:
            return
        (changed, _) = sessions.end_transit(state, agent_id, place_names(self.context.scenario))
        (state.sessions, state.activities) = (changed.sessions, changed.activities)

    def _shared_factors(self, activity, agent) -> ActivityFactors:
        """@brief 社交活动用冻结常量，其余活动复用自身声明系数（与边界预测同源）。"""
        return shared_activity_factors(activity, agent, self.context.semantics.body)

    def _advance_one_activity(self, state: WorldState, activity, target: int, settlement, body_event_id: str):
        """@brief 推进单项进度并引用已排在前面的唯一共同身体事件。"""
        agent = state.agents[activity.agent_id]
        before_consumed = {"media_position_seconds": activity.media_position_seconds}
        if activity.kind == ActivityKind.ACTION and activity.data["category"] == "social":
            advanced = social_actions.advance_social_shared(
                activity, agent, target, settlement, social_actions.shared_chat_seconds(state, activity, target)
            )
        else:
            advanced = advance_activity_shared(activity, agent, target, settlement)
        if reminders.reminder_due(agent, target):
            advanced = end_sleep_by_reminder(advanced, target)
        alarm_due = any(event.kind == ScheduledKind.ALARM_RING and event.agent_id == activity.agent_id
                        and event.due_at == target for event in state.pending_events)
        if alarm_due and not advanced.completed and activity.kind == ActivityKind.SLEEP:
            ended = advanced.activity.model_copy(update={"status": ActivityStatus.COMPLETED,
                "ended_at": target, "boundary_type": "alarm_ring", "interruption_reason": "alarm_ring",
                "next_interaction_at": None}, deep=True)
            advanced = ActivityAdvance(ended, advanced.agent, settlement, True)
        state.agents[activity.agent_id] = advanced.agent
        state.activities[activity.activity_id] = advanced.activity
        effects = self._apply_activity_effect(state, advanced.activity, before_consumed)
        if state.activities[activity.activity_id].status == ActivityStatus.COMPLETED:
            state.agents[activity.agent_id] = state.agents[activity.agent_id].model_copy(
                update={"next_decision_at": target}, deep=True)
        state.activities[activity.activity_id] = sessions.arrival_route_end(state, activity.activity_id, target)
        data = {
            "interval_start": activity.last_settled_at,
            "interval_end": target,
            "activity": activity_snapshot(
                state.activities[activity.activity_id], self.context.titles
            ),
            "body_settlement_event_id": body_event_id,
            **effects,
        }
        flows = _resource_flows_data(activity.agent_id, target)
        if flows:
            data[RESOURCE_FLOWS_FIELD] = flows
        draft = EventDraft(
            event_type="activity_progress",
            occurred_at=target,
            agent_id=activity.agent_id,
            decision_id=activity.origin_decision_id,
            action_id=activity.origin_action_id,
            activity_id=activity.activity_id,
            caused_by=[body_event_id, activity.origin_request_event_id],
            data=data,
        )
        return (draft, self._terminal_for_progress(state, state.activities[activity.activity_id], target))

    def _terminal_for_progress(self, state: WorldState, activity, target: int):
        """@brief 原动作的所有活动均终结后写一次聚合结算。"""
        if activity.status not in {
            ActivityStatus.COMPLETED,
            ActivityStatus.CANCELLED,
            ActivityStatus.FAILED,
            ActivityStatus.TRUNCATED,
        }:
            return None
        if activity.origin_action_id not in state.processed_actions:
            raise ProtocolError("Activity origin action is missing from processed actions")
        result = _origin_terminal_result(state, activity, target, activity_title(activity, self.context.titles))
        if result is None:
            return None
        state.processed_actions = _terminal_action_records(state, result)
        return (
            result,
            _origin_terminal_draft(state, activity, target, [], activity_title(activity, self.context.titles)),
        )

    def _apply_activity_effect(self, state: WorldState, activity, before_consumed: dict[str, float]) -> dict:
        """@brief 真实推进后结算路线、驾车日与统一内容证据。"""
        evidence = {}
        if activity.kind == ActivityKind.TRAVEL:
            trip = persisted_trip(self.context.semantics.catalog.graph, activity)
            activity, agent = update_travel(activity, trip, state.agents[activity.agent_id])
            record_arrival(activity, agent)
            state.activities[activity.activity_id] = activity
            state.agents[agent.agent_id] = agent
            if activity.travel_mode == "driving":
                seconds = activity.elapsed_seconds - self.context.state.activities[activity.activity_id].elapsed_seconds
                changed = charge_driving_days(state, self.context.semantics, activity.agent_id,
                                               activity.last_settled_at - seconds, activity.last_settled_at)
                state.agents = changed.agents
        if activity.kind == ActivityKind.ACTION:
            changed, evidence["content"] = settle_action(state, activity, self.context.semantics.catalog,
                self.context.scenario, before_consumed["media_position_seconds"])
            state.agents, state.activities, state.sessions = changed.agents, changed.activities, changed.sessions
        return evidence


    def _process_scheduled(
        self, state: WorldState, target: int
    ) -> tuple[WorldState, list[EventDraft], dict[int, set[str]]]:
        """@brief 委托唯一预排算法弹出并结算事件，保留运行日志与实际唤醒。"""
        due = [item for item in state.pending_events if item.due_at == target]
        state, drafts, arrivals = process_scheduled_events(state, target, self.context.semantics)
        for item in sorted(due, key=lambda value: (value.sequence, value.scheduled_id)):
            self.context.ops.log(
                target,
                "push" if item.kind.value == "app_push" else "engine",
                "info",
                "SCHEDULED_EVENT_DISPATCHED",
                f"Scheduled event {item.kind.value} dispatched",
                agent_id=item.agent_id,
                event_kind=item.kind.value,
                scheduled_id=item.scheduled_id,
            )
        return (state, drafts, arrivals)

    def _truncate(self, state: WorldState, now: int) -> list[EventDraft]:
        """@brief 在实验终点截断仍进行或暂停的活动并保留实际进度：逐项写截断快照，原始动作全部终结时写一次终态。"""
        drafts = []
        for activity_id, activity in state.activities.items():
            if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                continue
            (activity, agent) = truncate_activity(activity, state.agents[activity.agent_id], now)
            state.activities[activity_id] = activity
            state.agents[agent.agent_id] = agent
            drafts.append(
                activity_control.truncation_record(
                    activity, activity_snapshot(activity, self.context.titles), now
                )
            )
            result = _origin_terminal_result(state, activity, now, activity_title(activity, self.context.titles))
            if result is not None:
                state.processed_actions = _terminal_action_records(state, result)
                drafts.append(
                    _origin_terminal_draft(state, activity, now, [], activity_title(activity, self.context.titles))
                )
        return drafts

    def _ready(self, agent_id: int) -> bool:
        """@brief next_decision_at 已到的人物就绪（同刻机会只由其等于当前时刻表达，第一章规则 8）。"""
        if self.context.state.current_time >= self.context.state.end_time:
            return False
        agent = self.context.state.agents[agent_id]
        if agent.next_decision_at > self.context.state.current_time:
            return False
        return True

    def _next_epoch(self) -> int:
        """@brief 选择严格晚于当前时刻的最近活动、后台或节律边界，含场景本地零点与未触发的提醒（第四章），以及清醒人物
        的班次与休息时段起止：直接取生成器给出的严格晚于当前的时刻，睡眠中的人物不计入
        （docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界机制 ）。"""
        candidates = [
            self.context.state.end_time,
            *reminders.boundary_candidates(self.context.state, self.context.scenario.timezone),
        ]
        candidates.extend(awake_duty_edge_times(self.context.state, self.context.scenario))
        candidates.extend(
            (item.due_at for item in self.context.state.pending_events if item.due_at > self.context.state.current_time)
        )
        candidates.extend(
            (
                agent.next_decision_at
                for agent in self.context.state.agents.values()
                if agent.next_decision_at > self.context.state.current_time
            )
        )
        for activity in self.context.state.activities.values():
            if activity.status != ActivityStatus.ACTIVE:
                continue
            candidates.append(activity.planned_end_at)
            if activity.next_interaction_at and activity.next_interaction_at > self.context.state.current_time:
                candidates.append(activity.next_interaction_at)
        for agent_id in self.context.state.agents:
            body_boundary = self._body_boundary_at(agent_id)
            if body_boundary is not None:
                candidates.append(body_boundary)
        return min(candidates)

    def _body_boundary_at(self, agent_id: int) -> int | None:
        """@brief 按同人活动集合预测首个共同身体边界（含体感跨档与餐时窗口），空闲仍使用静息。"""
        agent = self.context.state.agents[agent_id]
        activities = active_activities(self.context.state, agent_id)
        current = self.context.state.current_time
        clock = BoundaryClock(
            projection.hour_of_day(current, self.context.scenario.timezone),
            meal_windows_by_agent(self.context.scenario)[agent_id],
        )
        factors = tuple(((item.kind, self._shared_factors(item, agent)) for item in activities))
        seconds = next_shared_body_boundary_seconds(agent.body, factors, self.context.semantics.body, clock)
        if seconds is None:
            return None
        candidate = current + seconds
        end = min((item.planned_end_at for item in activities), default=self.context.state.end_time)
        return candidate if candidate < end else None
