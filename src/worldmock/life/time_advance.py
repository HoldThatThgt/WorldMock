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
)
from worldmock.life.action_evidence import (_activity_terminal_draft, _activity_terminal_result,
                                            _terminal_action_records)
from worldmock.life.activities import (
    ActivityAdvance,
    advance_activity_shared,
    shared_activity_factors,
    schedule_feedback,
    truncate_activity,
)
from worldmock.life.body import ActivityFactors, BoundaryClock, next_shared_body_boundary_seconds, settle_shared_body
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
from worldmock.life.navigation_foreground import settle_navigation
from worldmock.life.presentation import flush_presentation, stage_presentation
from worldmock.life.purchase_phases import advance_phases, foreground_events, phase_boundaries
from worldmock.life.query_activity import complete_query
from worldmock.life.stay_records import stay_events
from worldmock.life.travel import persisted_trip, record_arrival, update_travel
from worldmock.scheduling.effects import process_scheduled_events

from worldmock.life.engine_context import EngineContext
from worldmock.life.activity_titles import activity_snapshot, progress_snapshot, title_index
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
        current = _datetime(self.context.state.current_time, self.context.config.timezone)
        if self.context.state.current_time >= self.context.state.end_time:
            return SchedulingState(current, (), None, self.context.state.version)
        ready = self._ready_agent_ids()
        next_at = _datetime(self._next_epoch(), self.context.config.timezone)
        return SchedulingState(current, ready, next_at, self.context.state.version)

    def _ready_agent_ids(self) -> tuple[int, ...]:
        """@brief 单人世界按实际到期状态返回人物身份，不产生多人竞争抽取。"""
        return tuple(agent_id for agent_id in self.context.state.agents if self._ready(agent_id))

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
        self.context.callback_causes.clear()
        staged = flush_presentation(stage_presentation(self.context.state, self.observations.pending_presentation))
        staged = staged.model_copy(update={"current_time": target_epoch}, deep=True)
        staged = same_moment.reset_same_moment(staged)
        (drafts, results) = self._advance_activities(staged, target_epoch)
        (staged, background, arrivals) = self._process_scheduled(staged, target_epoch)
        drafts.extend(background)
        (staged, credited) = errands.settle(staged, drafts)
        drafts.extend(credited)
        boundary = RhythmBoundary(target_epoch, interval, arrivals, drafts)
        if reminders.defers_rhythm(staged, target_epoch, self.context.config.timezone):
            staged = settle_engine_rhythm(self.context, staged, boundary)
            (drafts, self.midnight) = hold_body_settlements(boundary)
        else:
            staged = apply_engine_rhythm(self.context, staged, boundary)
            drafts.extend(self._wake_callbacks(staged, drafts))
        for agent_id in expired_plan_agent_ids(staged):
            staged = _put_agent(staged, staged.agents[agent_id].model_copy(update={"day_plan": None}, deep=True))
        if target_epoch == staged.end_time:
            drafts.extend(self._truncate(staged, target_epoch))
            staged = sessions.end_all(staged)
            staged, stopped = self._run_stop(staged)
            drafts.extend(stopped)
            drafts.append(
                EventDraft(
                    event_type="run_ended",
                    occurred_at=target_epoch,
                    visibility="system",
                    data={"end_time": target_epoch},
                )
            )
        self._commit_advance(staged, drafts)
        return results

    def _commit_advance(self, staged: WorldState, drafts: list[EventDraft]) -> None:
        """@brief 所有效果完成后比对阈值一次，再比较提交前后的世界写导航、购买前台区间与停留进出，提交唯一新世界。"""
        from worldmock.life.callbacks import compare_callback_states

        outcome = compare_callback_states(self.context.state, staged, self.context.catalog, drafts,
                                          self.context.callback_causes)
        staged, navigation = settle_navigation(self.context.state, outcome.state, self.context.catalog)
        drafts = [*drafts, *outcome.drafts, *navigation, *foreground_events(self.context.state, staged),
                  *stay_events(self.context.state, staged, drafts)]
        staged.version = self.context.state.version + 1
        self.context.commit_step(staged, drafts)
        self.observations.pending_presentation = {}

    def _run_stop(self, state: WorldState) -> tuple[WorldState, list[EventDraft]]:
        """@brief 正常截止只执行一次运行结束时间回调，不用于失败停止。"""
        from worldmock.life.callbacks import CallbackCause, evaluate_callbacks

        if state.run_stop_callbacks_applied:
            return state, []
        identity = self.context.persona.agent_id
        key = f"time-run_stop-{state.current_time}"
        source = EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=identity,
            visibility="system", source_kind="environment", data={"kind":"time_trigger", "key":"run_stop",
                                                                   "scheduled_id":key})
        outcome = evaluate_callbacks(state, self.context.catalog,
                                    CallbackCause("time", "run_stop", identity, source.event_id, key))
        self.context.callback_causes.update(outcome.causes)
        state = outcome.state.model_copy(update={"run_stop_callbacks_applied": True})
        return state, [source, *outcome.drafts]

    def _wake_callbacks(self, state: WorldState, drafts: list[EventDraft]) -> list[EventDraft]:
        """@brief 真实提供决策的边界按静态类别去重，以共同身体结算为直接原因。"""
        from worldmock.life.callbacks import CallbackCause, evaluate_callbacks
        from worldmock.life.schedule import wake_categories

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

    def decide_midnight_rhythm(self) -> None:
        """@brief 零点节律入口（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）：零点计划提交之后只执行判定段。

        判定该秒的唤醒原因与 next_decision_at（在手承诺按新计划计，新计划的提醒进入候选），写共同身体结算的唤醒字段，
        并与活动反馈对齐一次提交；心理需要只由同一实际消费区间结算。
        """
        boundary = self.midnight
        if boundary is None:
            raise ProtocolError("No midnight rhythm is pending")
        staged = decide_engine_rhythm(self.context, self.context.state.model_copy(deep=True), boundary)
        drafts = [*boundary.drafts, *self._wake_callbacks(staged, list(boundary.drafts))]
        self._commit_advance(staged, drafts)
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
            self.context.state, agent_id, request, (sources, causes), self.context.config.timezone
        )
        staged.version = self.context.state.version + 1
        self.context.commit_step(staged, [*evidence, draft])
        return plan

    def _require_midnight_decided(self) -> None:
        """@brief 零点判定段未执行时拒绝调度、推进与检查点（半结算的边界不能提供决策或封存）。"""
        same_moment.require_budget_available(self.context.state)
        if self.midnight is not None:
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
            settlement = settle_shared_body(agent.body, factors, target - start, self.context.body)
            body_event = EventDraft(event_type="environment_event", occurred_at=target, agent_id=agent_id,
                source_kind="environment", caused_by=list(dict.fromkeys(item.origin_request_event_id
                    for item in activities)), data={"kind":"body_settlement", "interval_start":start,
                    "interval_end":target, "activity_ids":[item.activity_id for item in activities],
                    "body_settlement":_body_settlement_data(settlement),
                    RESOURCE_FLOWS_FIELD:_resource_flows_data(agent_id,target)})
            drafts.append(body_event)
            state.agents[agent_id] = agent.model_copy(
                update={"body":settlement.after,"body_settled_at":target},deep=True)
            for activity in activities:
                progress, terminal = self._advance_one_activity(
                    state, activity, target, settlement, body_event.event_id)
                drafts.extend(progress)
                if terminal is not None:
                    result,event=terminal
                    results.append(result)
                    drafts.append(event)
            self._switch_session_on_arrival(state,agent_id,target)
        return drafts,results

    def _switch_session_on_arrival(self, state: WorldState, agent_id: int, target: int) -> None:
        """@brief 到达（含到达被拒）的边界：结束路上会话，在所到地点开始停留会话（就地写入本次推进的世界副本）。"""
        if sessions.arriving_transit(state, agent_id, target) is None:
            return
        changed = sessions.end_transit(state, agent_id, place_names(self.context.catalog))
        (state.sessions, state.activities) = (changed.sessions, changed.activities)

    def _shared_factors(self, activity, agent) -> ActivityFactors:
        """@brief 社交活动用冻结常量，其余活动复用自身声明系数（与边界预测同源）。"""
        return shared_activity_factors(activity, agent, self.context.body)

    def _advance_one_activity(self, state: WorldState, activity, target: int, settlement, body_event_id: str):
        """@brief 推进活动、付款或内容后引用唯一共同身体事件。"""
        advanced=advance_activity_shared(activity,state.agents[activity.agent_id],target,settlement)
        state.agents[activity.agent_id]=advanced.agent
        state.activities[activity.activity_id]=advanced.activity
        effects,merged=self._apply_activity_effect(state,advanced.activity,target,activity.last_settled_at)
        from worldmock.life.callbacks import settle_callback_dates

        outcome=settle_callback_dates(state,self.context.catalog,activity,(activity.last_settled_at,target))
        state.agents,state.accounts=outcome.state.agents,outcome.state.accounts
        effects.extend(outcome.drafts)
        self.context.callback_causes.update(outcome.causes)
        changed=state.activities[activity.activity_id]
        if changed.status in {ActivityStatus.COMPLETED,ActivityStatus.FAILED,ActivityStatus.CANCELLED}:
            state.agents[activity.agent_id]=state.agents[activity.agent_id].model_copy(
                update={"next_decision_at":target})
        data={"interval_start":activity.last_settled_at,"interval_end":target,
              "activity":progress_snapshot(changed,title_index(self.context.catalog)),
              "body_settlement_event_id":body_event_id}
        flows=[*merged.pop(RESOURCE_FLOWS_FIELD,[]),*_resource_flows_data(activity.agent_id,target)]
        data.update(merged)
        if flows:
            data[RESOURCE_FLOWS_FIELD]=flows
        draft=EventDraft(event_type="activity_progress",occurred_at=target,agent_id=activity.agent_id,
            decision_id=activity.origin_decision_id,action_id=activity.origin_action_id,
            activity_id=activity.activity_id,caused_by=[body_event_id,activity.origin_request_event_id],data=data)
        return [draft,*effects],self._terminal_for_progress(state,changed,target)

    def _terminal_for_progress(self, state: WorldState, activity, target: int):
        """@brief 活动终结后给开始它的那条动作写一次终态。"""
        if activity.status not in {
            ActivityStatus.COMPLETED,
            ActivityStatus.CANCELLED,
            ActivityStatus.FAILED,
            ActivityStatus.TRUNCATED,
        }:
            return None
        if activity.origin_action_id not in state.processed_actions:
            raise ProtocolError("Activity origin action is missing from processed actions")
        index = title_index(self.context.catalog)
        result = _activity_terminal_result(state, activity, target, index)
        state.processed_actions = _terminal_action_records(state, result)
        return (
            result,
            _activity_terminal_draft(state, activity, target, [], index),
        )

    def _apply_activity_effect(self,state:WorldState,activity,target:int,
                               since:int)->tuple[list[EventDraft], dict]:
        """@brief 真实推进后结算路线、购买的段边界与付款、内容与完成效果、做完的查询，读取各模块唯一状态；since 是这项
        活动上一次结算到的时刻。返回（草稿，要并进这条 activity_progress 事件 data 的键）：键只有内容证据 content 与
        完成效果的分录 resource_flows，没有的不出现。"""
        drafts=[]
        merged={}
        if activity.kind==ActivityKind.TRAVEL:
            trip=persisted_trip(self.context.catalog,activity)
            changed,agent=update_travel(activity,trip,state.agents[activity.agent_id])
            record_arrival(changed,agent)
            state.activities[activity.activity_id],state.agents[agent.agent_id]=changed,agent
        elif activity.kind==ActivityKind.PURCHASE:
            from worldmock.life.storefront_runtime import StorefrontRuntime

            changed=advance_phases(state,activity,since)
            if activity.status==ActivityStatus.COMPLETED:
                changed,completion=StorefrontRuntime(self.context).complete_purchase(changed,activity)
                drafts.extend(completion)
            state.agents,state.accounts,state.deliveries=changed.agents,changed.accounts,changed.deliveries
            state.activities,state.pending_events=changed.activities,changed.pending_events
        elif activity.kind==ActivityKind.INTERACT:
            from worldmock.life.interact import InteractivityRuntime

            runtime=InteractivityRuntime(self.context)
            changed,content=runtime.advance(state,activity,target)
            changed,content,flows=runtime.settle_completion(changed,activity,content)
            state.agents,state.activities,state.sessions=changed.agents,changed.activities,changed.sessions
            merged={key:value for key,value in (("content",content),(RESOURCE_FLOWS_FIELD,flows)) if value}
        elif activity.kind==ActivityKind.QUERY and activity.status==ActivityStatus.COMPLETED:
            state.activities[activity.activity_id]=complete_query(self.context.inputs,state,activity)
        changed=state.activities[activity.activity_id]
        if changed.status==ActivityStatus.ACTIVE and changed.next_interaction_at==target:
            state.activities[activity.activity_id]=schedule_feedback(changed,target,
                self.context.config.activity_feedback_seconds)
        return drafts,merged


    def _process_scheduled(
        self, state: WorldState, target: int
    ) -> tuple[WorldState, list[EventDraft], dict[int, set[str]]]:
        """@brief 委托唯一预排算法弹出并结算事件，保留运行日志与实际唤醒。"""
        due = [item for item in state.pending_events if item.due_at == target]
        state, drafts, arrivals = process_scheduled_events(state, target, self.context)
        for item in sorted(due, key=lambda value: (value.sequence, value.scheduled_id)):
            self.context.ops.log(
                target,
                "push" if item.kind.value == "app_push" else "engine",
                "info",
                {"code": "SCHEDULED_EVENT_DISPATCHED",
                "detail": f"Scheduled event {item.kind.value} dispatched",
                "agent_id": item.agent_id,
                "event_kind": item.kind.value,
                "scheduled_id": item.scheduled_id},
            )
        return (state, drafts, arrivals)

    def _truncate(self, state: WorldState, now: int) -> list[EventDraft]:
        """@brief 在实验终点截断仍进行或暂停的活动并保留实际进度：逐项写截断快照，并给开始它的动作写一次终态。"""
        drafts = []
        for activity_id, activity in state.activities.items():
            if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                continue
            (activity, agent) = truncate_activity(activity, state.agents[activity.agent_id], now)
            if activity.kind == ActivityKind.PURCHASE:
                from worldmock.life.storefront_runtime import StorefrontRuntime

                cancelled = StorefrontRuntime(self.context).cancel_purchase(state, activity)
                state.deliveries = cancelled.deliveries
            state.activities[activity_id] = activity
            state.agents[agent.agent_id] = agent
            drafts.append(
                activity_control.truncation_record(
                    activity, activity_snapshot(activity, title_index(self.context.catalog)), now
                )
            )
            index = title_index(self.context.catalog)
            result = _activity_terminal_result(state, activity, now, index)
            state.processed_actions = _terminal_action_records(state, result)
            drafts.append(_activity_terminal_draft(state, activity, now, [], index))
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
        （docs/design/04-simulation-engine.md）。"""
        candidates = [
            self.context.state.end_time,
            *reminders.boundary_candidates(self.context.state, self.context.config.timezone),
        ]
        candidates.extend(awake_duty_edge_times(self.context.state, self.context.inputs))
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
        clock = BoundaryClock(
            projection.hour_of_day(current, self.context.config.timezone),
            meal_windows_by_agent(self.context.persona)[agent_id],
        )
        factors = tuple(((item.kind, self._shared_factors(item, agent)) for item in activities))
        seconds = next_shared_body_boundary_seconds(agent.body, factors, self.context.body, clock)
        if seconds is None:
            return None
        candidate = current + seconds
        end = min((item.planned_end_at for item in activities), default=self.context.state.end_time)
        return candidate if candidate < end else None
