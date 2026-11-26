"""@brief TimeAdvanceCoordinator 的独立生活运行职责。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.time.readable import _datetime
from worldmock.recording_engine.events.models import RESOURCE_FLOWS_FIELD, EventDraft
from worldmock.simulation_engine.activities.content.interact import InteractivityRuntime
from worldmock.simulation_engine.activities.lifecycle import activity_control, sessions
from worldmock.simulation_engine.activities.lifecycle.activities import (
    advance_activity_shared,
    schedule_feedback,
    shared_activity_factors,
    truncate_activity,
)
from worldmock.simulation_engine.activities.travel.navigation_foreground import settle_navigation
from worldmock.simulation_engine.activities.travel.travel import persisted_trip, record_arrival, update_travel
from worldmock.simulation_engine.body.body import (
    ActivityFactors,
    BoundaryClock,
    next_shared_body_boundary_seconds,
    settle_shared_body,
)
from worldmock.simulation_engine.commerce.purchase.purchase_phases import (
    advance_phases,
    foreground_events,
    phase_boundaries,
)
from worldmock.simulation_engine.commerce.purchase.storefront_runtime import StorefrontRuntime
from worldmock.simulation_engine.evidence import memory_digest
from worldmock.simulation_engine.evidence.actions import (
    _activity_terminal_draft,
    _activity_terminal_result,
    prune_processed_actions,
    require_origin_action,
)
from worldmock.simulation_engine.evidence.stays import stay_events
from worldmock.simulation_engine.observation.layers import projection
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder
from worldmock.simulation_engine.observation.wording.activity_titles import (
    activity_snapshot,
    progress_snapshot,
    title_index,
)
from worldmock.simulation_engine.scheduling.callbacks.callbacks import (
    CallbackCause,
    compare_callback_states,
    evaluate_callbacks,
)
from worldmock.simulation_engine.scheduling.clock.effects import process_scheduled_events
from worldmock.simulation_engine.scheduling.clock.rhythm import (
    RhythmBoundary,
    apply_engine_rhythm,
    awake_duty_edge_times,
    decide_engine_rhythm,
    hold_body_settlements,
    settle_engine_rhythm,
)
from worldmock.simulation_engine.scheduling.clock.weather import register_daily_weather
from worldmock.simulation_engine.scheduling.plans import day_planning, errands, reminders
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.simulation_engine.world.world_updates import (
    _body_settlement_data,
    _put_agent,
    _resource_flows_data,
    meal_windows_by_agent,
    place_names,
)
from worldmock.state.decisions.results import ActionResult
from worldmock.state.decisions.round import SchedulingState
from worldmock.state.interaction.requests import PlanDayRequest
from worldmock.state.planning.schedule import DayPlan, expired_plan_agent_ids, wake_categories
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.world import WorldState, active_activities

_LOGGER = logging.getLogger(__name__)


def due_search_activities(state: WorldState) -> tuple:
    """@brief 返回到达或超过计划结束时刻、仍在进行中等待检索会话完成的搜索活动。"""
    if state.current_time >= state.end_time:
        return ()
    return tuple(sorted(
        (item for item in state.activities.values()
         if item.kind == ActivityKind.SEARCH and item.status == ActivityStatus.ACTIVE
         and item.planned_end_at <= state.current_time),
        key=lambda item: (item.planned_end_at, item.agent_id, item.activity_id),
    ))


class TimeAdvanceCoordinator:
    """@brief 显式依赖上下文的 TimeAdvanceCoordinator。"""

    def __init__(self, context: EngineContext, observations: ObservationBuilder):
        """@brief 绑定状态与浏览投影，零点边界只归推进协调。"""
        self.context, self.observations = context, observations
        self.midnight: RhythmBoundary | None = None

    def scheduling_state(self) -> "SchedulingState":
        """@brief 返回运行宿主使用的带时区当前时刻、按编号升序的就绪人物和下一边界。"""
        self._require_midnight_decided()
        current = _datetime(self.context.state.current_time, self.context.config.timezone)
        if self.context.state.current_time >= self.context.state.end_time:
            return SchedulingState(current, (), None, self.context.state.version)
        ready = self._ready_agent_ids()
        next_at = _datetime(self._next_epoch(), self.context.config.timezone)
        return SchedulingState(current, ready, next_at, self.context.state.version)

    def _ready_agent_ids(self) -> tuple[int, ...]:
        """@brief 按人物编号升序返回当前就绪等待屏障唤醒的人物。"""
        return tuple(agent_id for agent_id in sorted(self.context.state.agents) if self._ready(agent_id))

    def advance(self, tick: int, target: datetime) -> list[ActionResult]:
        """@brief 精确推进到下一事件边界并结算连续活动、后台事件与决策节律。"""
        self._require_ready_to_advance()
        target_epoch = int(target.timestamp())
        if tick <= 0 or target_epoch - self.context.state.current_time != tick or target_epoch != self._next_epoch():
            _LOGGER.error("World advance target disagrees with next scheduling boundary")
            raise ProtocolError("World advance must match the next scheduling boundary")
        interval = target_epoch - self.context.state.current_time
        self.context.callback_causes.clear()
        staged = self.context.state.model_copy(update={"current_time": target_epoch}, deep=True)
        if reminders.is_local_midnight(target_epoch, staged.timezone):
            register_daily_weather(staged, self.context.catalog)
        staged = prune_processed_actions(staged)
        drafts, results = self._advance_activities(staged, target_epoch)
        staged, background, arrivals = self._process_scheduled(staged, target_epoch)
        drafts.extend(background)
        staged, credited = errands.settle(staged, drafts)
        drafts.extend(credited)
        boundary = RhythmBoundary(target_epoch, interval, arrivals, drafts)
        staged, drafts = self._settle_rhythm(staged, target_epoch, boundary, drafts)
        for agent_id in expired_plan_agent_ids(staged):
            staged = _put_agent(staged, staged.agents[agent_id].model_copy(update={"day_plan": None}, deep=True))
        if target_epoch == staged.end_time:
            staged, end_drafts = self._finalize_run_end(staged, target_epoch)
            drafts.extend(end_drafts)
        self._commit_advance(staged, drafts)
        return results

    def _require_ready_to_advance(self) -> None:
        """@brief 零点判定、到期搜索或就绪人物未结清时拒绝推进时钟。"""
        self._require_midnight_decided()
        if due_search_activities(self.context.state):
            _LOGGER.error("World time cannot advance while a due search is pending")
            raise ProtocolError("World time cannot advance while a due search is pending")
        if any(self._ready(agent_id) for agent_id in self.context.state.agents):
            _LOGGER.error("World time cannot advance while an agent decision is due")
            raise ProtocolError("World time cannot advance while an agent decision is due")

    def _settle_rhythm(self, staged: WorldState, target_epoch: int, boundary: RhythmBoundary,
                       drafts: list[EventDraft]) -> tuple[WorldState, list[EventDraft]]:
        """@brief 零点延后节律判定段，其余边界直接判定并评估唤醒回调。"""
        if reminders.defers_rhythm(staged, target_epoch, self.context.config.timezone):
            staged = settle_engine_rhythm(self.context, staged, boundary)
            kept, self.midnight = hold_body_settlements(boundary)
            return staged, kept
        staged = apply_engine_rhythm(self.context, staged, boundary)
        drafts.extend(self._wake_callbacks(staged, drafts))
        return staged, drafts

    def _finalize_run_end(self, staged: WorldState, target_epoch: int) -> tuple[WorldState, list[EventDraft]]:
        """@brief 到达实验终点时截断剩余活动、清除悬挂循环、结束会话并触发停止回调。"""
        drafts = self._truncate(staged, target_epoch)
        staged = sessions.end_all(staged)
        for agent_id, agent in staged.agents.items():
            if agent.suspended_loop is not None:
                staged.agents[agent_id] = agent.model_copy(update={"suspended_loop": None}, deep=True)
        staged, stopped = self._run_stop(staged)
        drafts.extend(stopped)
        drafts.append(EventDraft(event_type="run_ended", occurred_at=target_epoch, visibility="system",
                                 data={"end_time": target_epoch}))
        return staged, drafts

    def _commit_advance(self, staged: WorldState, drafts: list[EventDraft]) -> None:
        """@brief 所有效果完成后比对阈值一次，再写导航、购买前台区间与停留进出，提交唯一新世界。"""
        outcome = compare_callback_states(self.context.state, staged, self.context.catalog, drafts,
                                          self.context.callback_causes)
        staged, navigation = settle_navigation(self.context.state, outcome.state, self.context.catalog)
        drafts = [*drafts, *outcome.drafts, *navigation, *foreground_events(self.context.state, staged),
                  *stay_events(self.context.state, staged, drafts)]
        staged.version = self.context.state.version + 1
        self.context.commit_step(staged, drafts)

    def _run_stop(self, state: WorldState) -> tuple[WorldState, list[EventDraft]]:
        """@brief 正常截止按人物编号只执行一次运行结束时间回调，不用于失败停止。"""
        if state.run_stop_callbacks_applied:
            return state, []
        multi = len(state.agents) > 1
        drafts: list[EventDraft] = []
        current = state
        for identity in sorted(current.agents):
            key = f"time-run_stop-{state.current_time}-{identity}" if multi else f"time-run_stop-{state.current_time}"
            source = EventDraft(
                event_type="environment_event", occurred_at=current.current_time, agent_id=identity,
                visibility="system", source_kind="environment",
                data={"kind": "time_trigger", "key": "run_stop", "scheduled_id": key},
            )
            outcome = evaluate_callbacks(current, self.context.catalog,
                                         CallbackCause("time", "run_stop", identity, source.event_id, key))
            self.context.callback_causes.update(outcome.causes)
            current = outcome.state
            drafts.extend((source, *outcome.drafts))
        return current.model_copy(update={"run_stop_callbacks_applied": True}), drafts

    def _wake_callbacks(self, state: WorldState, drafts: list[EventDraft]) -> list[EventDraft]:
        """@brief 真实提供决策的边界按静态类别去重，以共同身体结算为直接原因。"""
        emitted = []
        sources = [draft for draft in drafts if draft.data.get("kind") == "body_settlement"
                   and draft.data.get("decision_offered", False)]
        for source in sources:
            for category in wake_categories(source.data["wake_reasons"]):
                cause = CallbackCause("event", category, source.agent_id, source.event_id,
                                      f"wake:{source.agent_id}:{state.current_time}")
                outcome = evaluate_callbacks(state, self.context.catalog, cause)
                state.agents, state.accounts = outcome.state.agents, outcome.state.accounts
                self.context.callback_causes.update(outcome.causes)
                emitted.extend(outcome.drafts)
        return emitted

    @property
    def midnight_rhythm_pending(self) -> bool:
        """@brief 零点边界已结算提交、判定段尚待零点计划提交之后执行。"""
        return self.midnight is not None

    def decide_midnight_rhythm(self, decision: Any = None) -> None:
        """@brief 零点节律入口：零点计划提交之后执行判定段并清空全员旧日会话，一次提交。"""
        del decision
        boundary = self.midnight
        if boundary is None:
            _LOGGER.error("No midnight rhythm is pending")
            raise ProtocolError("No midnight rhythm is pending")
        staged = decide_engine_rhythm(self.context, self.context.state.model_copy(deep=True), boundary)
        for agent_id, agent in list(staged.agents.items()):
            staged.agents[agent_id] = agent.model_copy(update={"day_session": None}, deep=True)
        drafts = [*boundary.drafts, *self._wake_callbacks(staged, list(boundary.drafts))]
        self._commit_advance(staged, drafts)
        self.midnight = None

    def register_day_plan(
        self, agent_id: int, request: PlanDayRequest, sources: list[str], evidence: list[EventDraft]
    ) -> DayPlan:
        """@brief 零点计划的系统登记入口：校验目标并写 day_plan 与模型证据同次提交。"""
        causes = [evidence[-1].event_id] if evidence else []
        staged, plan, draft = day_planning.registration(
            self.context.state, agent_id, request, (sources, causes), self.context.config.timezone
        )
        staged.version = self.context.state.version + 1
        self.context.commit_step(staged, [*evidence, draft])
        return plan

    def register_memory_digest(self, agent_id: int, outcome: memory_digest.DigestOutcome,
                               evidence: list[EventDraft]) -> None:
        """@brief 回顾的系统登记入口：与本期模型证据同一次提交写入。"""
        if not evidence:
            _LOGGER.error("Memory digest registration requires model evidence")
            raise ProtocolError("Memory digest registration requires model evidence")
        draft = memory_digest.registration_draft(self.context, agent_id, outcome, [evidence[-1].event_id])
        staged = self.context.state.model_copy(deep=True)
        staged.version = self.context.state.version + 1
        self.context.commit_step(staged, [*evidence, draft])

    def _require_midnight_decided(self) -> None:
        """@brief 零点判定段未执行时拒绝调度、推进与检查点。"""
        if self.midnight is not None:
            _LOGGER.error("Midnight rhythm must be decided before advancing or scheduling")
            raise ProtocolError("Midnight rhythm must be decided after the day plans are registered")

    def _advance_activities(self, state: WorldState, target: int) -> tuple[list[EventDraft], list[ActionResult]]:
        """@brief 同一区间先结算共同身体一次，再推进各活动与真实效果。"""
        drafts, results = [], []
        for agent_id in sorted(state.agents):
            agent = state.agents[agent_id]
            start = agent.body_settled_at
            if start == target:
                continue
            activities = active_activities(state, agent_id)
            factors = tuple((item.kind, self._shared_factors(item, agent)) for item in activities)
            settlement = settle_shared_body(agent.body, factors, target - start, self.context.bodies[agent_id])
            body_event = EventDraft(event_type="environment_event", occurred_at=target, agent_id=agent_id,
                source_kind="environment", caused_by=list(dict.fromkeys(item.origin_request_event_id
                    for item in activities)), data={"kind": "body_settlement", "interval_start": start,
                    "interval_end": target, "activity_ids": [item.activity_id for item in activities],
                    "body_settlement": _body_settlement_data(settlement),
                    RESOURCE_FLOWS_FIELD: _resource_flows_data(agent_id, target)})
            drafts.append(body_event)
            state.agents[agent_id] = agent.model_copy(
                update={"body": settlement.after, "body_settled_at": target}, deep=True)
            for activity in activities:
                progress, terminal = self._advance_one_activity(
                    state, activity, target, settlement, body_event.event_id)
                drafts.extend(progress)
                if terminal is not None:
                    results.append(terminal[0])
                    drafts.append(terminal[1])
            self._switch_session_on_arrival(state, agent_id, target)
        return drafts, results

    def _switch_session_on_arrival(self, state: WorldState, agent_id: int, target: int) -> None:
        """@brief 到达的边界结束路上会话，在所到地点开始停留会话。"""
        if sessions.arriving_transit(state, agent_id, target) is None:
            return
        changed = sessions.end_transit(state, agent_id, place_names(self.context.catalog))
        state.sessions, state.activities = changed.sessions, changed.activities

    def _shared_factors(self, activity, agent) -> ActivityFactors:
        """@brief 活动与边界预测复用相同代谢当量及缓解速率。"""
        return shared_activity_factors(activity, agent, self.context.bodies[agent.agent_id])

    def _advance_one_activity(self, state: WorldState, activity, target: int, settlement, body_event_id: str):
        """@brief 推进活动、付款或内容后引用唯一共同身体事件，使用 wakeup_id 归因。"""
        advanced = advance_activity_shared(activity, state.agents[activity.agent_id], target, settlement)
        state.agents[activity.agent_id] = advanced.agent
        state.activities[activity.activity_id] = advanced.activity
        effects, merged = self._apply_activity_effect(state, advanced.activity, target, activity.last_settled_at)
        from worldmock.simulation_engine.scheduling.callbacks.callbacks import settle_callback_dates

        outcome = settle_callback_dates(state, self.context.catalog, activity, (activity.last_settled_at, target))
        state.agents, state.accounts = outcome.state.agents, outcome.state.accounts
        effects.extend(outcome.drafts)
        self.context.callback_causes.update(outcome.causes)
        changed = state.activities[activity.activity_id]
        if changed.status in {ActivityStatus.COMPLETED, ActivityStatus.FAILED, ActivityStatus.CANCELLED}:
            state.agents[activity.agent_id] = state.agents[activity.agent_id].model_copy(
                update={"next_decision_at": target})
        data = {"interval_start": activity.last_settled_at, "interval_end": target,
                "activity": progress_snapshot(changed, title_index(self.context.catalog)),
                "body_settlement_event_id": body_event_id}
        flows = [*merged.pop(RESOURCE_FLOWS_FIELD, []), *_resource_flows_data(activity.agent_id, target)]
        data.update(merged)
        if flows:
            data[RESOURCE_FLOWS_FIELD] = flows
        draft = EventDraft(event_type="activity_progress", occurred_at=target, agent_id=activity.agent_id,
            wakeup_id=activity.origin_wakeup_id, action_id=activity.origin_action_id,
            activity_id=activity.activity_id, caused_by=[body_event_id, activity.origin_request_event_id], data=data)
        return [draft, *effects], self._terminal_for_progress(state, changed, target)

    def _terminal_for_progress(self, state: WorldState, activity, target: int):
        """@brief 活动终结后给开始它的那条动作写一次终态。"""
        if activity.status not in {
            ActivityStatus.COMPLETED, ActivityStatus.CANCELLED, ActivityStatus.FAILED, ActivityStatus.TRUNCATED,
        }:
            return None
        require_origin_action(state, activity)
        index = title_index(self.context.catalog)
        return _activity_terminal_result(state, activity, target, index), _activity_terminal_draft(
            state, activity, target, [], index)

    def _apply_activity_effect(self, state: WorldState, activity, target: int,
                               since: int) -> tuple[list[EventDraft], dict]:
        """@brief 真实推进后结算路线、购买付款分段及内容；搜索到期不在时间推进中完成，留交完整搜索会话结算。"""
        drafts, merged = [], {}
        if activity.kind == ActivityKind.TRAVEL:
            trip = persisted_trip(self.context.catalog, activity)
            changed, agent = update_travel(activity, trip, state.agents[activity.agent_id])
            changed = record_arrival(changed, agent)
            state.activities[activity.activity_id], state.agents[agent.agent_id] = changed, agent
        elif activity.kind == ActivityKind.PURCHASE:
            changed = advance_phases(state, activity, since)
            if activity.status == ActivityStatus.COMPLETED:
                changed, completion = StorefrontRuntime(self.context).complete_purchase(changed, activity)
                drafts.extend(completion)
            state.agents, state.accounts, state.deliveries = changed.agents, changed.accounts, changed.deliveries
            state.activities, state.pending_events = changed.activities, changed.pending_events
        elif activity.kind == ActivityKind.INTERACT:
            runtime = InteractivityRuntime(self.context)
            changed, content = runtime.advance(state, activity, target)
            changed, content, flows = runtime.settle_completion(changed, activity, content)
            state.agents, state.activities, state.sessions = changed.agents, changed.activities, changed.sessions
            merged = {key: value for key, value in (("content", content), (RESOURCE_FLOWS_FIELD, flows)) if value}
        changed = state.activities[activity.activity_id]
        if changed.status == ActivityStatus.ACTIVE and changed.next_interaction_at == target:
            state.activities[activity.activity_id] = schedule_feedback(
                changed, target, self.context.config.activity_feedback_seconds)
        return drafts, merged

    def _process_scheduled(
        self, state: WorldState, target: int
    ) -> tuple[WorldState, list[EventDraft], dict[int, set[str]]]:
        """@brief 委托唯一预排算法弹出并结算事件，保留运行日志与实际唤醒。"""
        due = [item for item in state.pending_events if item.due_at == target]
        state, drafts, arrivals = process_scheduled_events(state, target, self.context)
        for item in sorted(due, key=lambda value: (value.sequence, value.scheduled_id)):
            self.context.ops.log(
                target, "push" if item.kind.value == "app_push" else "engine", "info",
                {"code": "SCHEDULED_EVENT_DISPATCHED", "detail": f"Scheduled event {item.kind.value} dispatched",
                 "agent_id": item.agent_id, "event_kind": item.kind.value, "scheduled_id": item.scheduled_id},
            )
        return state, drafts, arrivals

    def _truncate(self, state: WorldState, now: int) -> list[EventDraft]:
        """@brief 在实验终点截断仍进行或暂停的活动并保留实际进度。"""
        drafts = []
        for activity_id, activity in state.activities.items():
            if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                continue
            activity, agent = truncate_activity(activity, state.agents[activity.agent_id], now)
            if activity.kind == ActivityKind.PURCHASE:
                cancelled = StorefrontRuntime(self.context).cancel_purchase(state, activity)
                state.deliveries = cancelled.deliveries
            state.activities[activity_id] = activity
            state.agents[agent.agent_id] = agent
            index = title_index(self.context.catalog)
            drafts.append(activity_control.truncation_record(activity, activity_snapshot(activity, index), now))
            require_origin_action(state, activity)
            drafts.append(_activity_terminal_draft(state, activity, now, [], index))
        return drafts

    def _ready(self, agent_id: int) -> bool:
        """@brief next_decision_at 已到且未超模拟结束时刻的人物可进入屏障唤醒。"""
        if self.context.state.current_time >= self.context.state.end_time:
            return False
        agent = self.context.state.agents[agent_id]
        return agent.next_decision_at <= self.context.state.current_time

    def _next_epoch(self) -> int:
        """@brief 选择严格晚于当前时刻的最近活动、后台或节律边界。"""
        candidates = [self.context.state.end_time,
                      *reminders.boundary_candidates(self.context.state, self.context.config.timezone)]
        candidates.extend(awake_duty_edge_times(self.context.state, self.context.inputs))
        candidates.extend(item.due_at for item in self.context.state.pending_events
                          if item.due_at > self.context.state.current_time)
        candidates.extend(agent.next_decision_at for agent in self.context.state.agents.values()
                          if agent.next_decision_at > self.context.state.current_time)
        for activity in self.context.state.activities.values():
            if activity.status != ActivityStatus.ACTIVE:
                continue
            if activity.planned_end_at > self.context.state.current_time:
                candidates.append(activity.planned_end_at)
            if activity.kind == ActivityKind.PURCHASE:
                candidates.extend(phase_boundaries(self.context.state, activity))
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
        body_cfg = self.context.bodies[agent_id]
        clock = BoundaryClock(projection.hour_of_day(current, self.context.config.timezone),
                              meal_windows_by_agent(self.context.inputs.personas)[agent_id])
        factors = tuple((item.kind, self._shared_factors(item, agent)) for item in activities)
        seconds = next_shared_body_boundary_seconds(agent.body, factors, body_cfg, clock)
        if seconds is None:
            return None
        candidate = current + seconds
        end = min((item.planned_end_at for item in activities), default=self.context.state.end_time)
        return candidate if candidate < end else None
