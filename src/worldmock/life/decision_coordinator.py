"""@brief DecisionCoordinator 的独立生活运行职责。"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext, DirectToolCall
from worldmock.life.world_ops import _failure_draft
from worldmock.life.world_ops import _put_agent
from worldmock.life.world_ops import _request_sha
from worldmock.life.world_ops import _resource_flows_data

import logging
from worldmock.errors import BusinessRuleError, PreconditionError, ProtocolError
from worldmock.footprints.models import RESOURCE_FLOWS_FIELD, EventDraft
from worldmock.life import activity_control, day_planning, errands, same_moment
from worldmock.life.action_contract import START_TOOLS
from worldmock.life.action_evidence import _pending_causes, _remember_action, _result_draft, _result_event_data
from worldmock.life.guidance import rejection_guidance
from worldmock.life.business_feedback import business_feedback
from worldmock.life.decision_start import (
    defer_decision_starts,
    flush_decision_starts,
    pop_decision_start,
    response_self_note,
)
from worldmock.life.body import apply_relaxation_change
from worldmock.life.models import (
    ActionReceipt,
    ActionResult,
    ActionStatus,
    DecisionRoundState,
    PendingAction,
    REQUEST_MODELS,
    rejection_reason_text,
    SameMomentStage,
    StoredAction,
    WorldState,
    active_activities,
)
from worldmock.life.navigation_foreground import settle_navigation
from worldmock.life.observe import ObservationBuilder
from worldmock.life.purchase_phases import foreground_events
from worldmock.life.rhythm import consume_progress_report_opportunity, next_decision_at
from worldmock.life.session_actions import ActionHandlers
from worldmock.life.stay_records import stay_events
from worldmock.life.models import validated_payload
from worldmock.life.presentation import stage_presentation

from worldmock.life.engine_context import EngineContext
from worldmock.life.activity_titles import activity_snapshot, title_index

class DecisionCoordinator:
    """@brief 显式依赖上下文的DecisionCoordinator。"""

    def __init__(self, context: EngineContext, actions: ActionHandlers, observations: ObservationBuilder):
        """@brief 绑定状态、动作与观察，并建立独立轮次簿记。"""
        self.context, self.actions, self.observations = context, actions, observations
        self.round: DecisionRoundState | None = None
        self.pending_decision_starts: dict[int, dict] = {}

    def begin_decision_round(self, contexts: list["DecisionContext"], ready_agent_ids: tuple[int, ...]) -> None:
        """@brief 持久化观察顺序；decision_started 延迟到首个行动请求时写（docs/design/06-agent.md）。"""
        same_moment.require_budget_available(self.context.state)
        if self.round is not None or not contexts:
            raise ProtocolError("Decision round cannot be started")
        for context in contexts:
            self.validate_context(context)
        round_ids = {context.round_id for context in contexts}
        agent_ids = [context.agent_id for context in contexts]
        if len(round_ids) != 1 or len(agent_ids) != len(set(agent_ids)):
            raise ProtocolError("Decision round contexts disagree or repeat an agent")
        ready = list(ready_agent_ids)
        if agent_ids != ready:
            raise ProtocolError("Decision round contexts must match the scheduled agent order")
        round_id = contexts[0].round_id
        self.round = DecisionRoundState(
            round_id=round_id, expected_agent_ids=agent_ids, observed_version=self.context.state.version
        )
        self.context.callback_causes.clear()
        self.pending_decision_starts = defer_decision_starts(contexts, round_id, self.context.state)
        self.context.ops.log(
            self.context.state.current_time,
            "engine",
            "info",
            {"code": "DECISION_ROUND_BEGAN",
            "detail": f"Decision round opened for {len(agent_ids)} agents",
            "round_id": round_id},
        )

    def submit_action(self, call: "DirectToolCall") -> ActionReceipt:
        """@brief 严格登记宿主绑定的有类型工具调用，尚不改变生活状态。"""
        self._validate_call(call)
        request_sha = _request_sha(call.tool_name, call.request)
        existing = self._existing_action(call.action_id, call.tool_name, request_sha)
        if existing is not None:
            receipt = ActionReceipt.model_validate(existing.receipt)
            if existing.result is None:
                return receipt
            self._queue_action(call, request_sha, reused=True)
            return receipt
        return self._queue_action(call, request_sha, reused=False)

    def _queue_action(self, call: "DirectToolCall", request_sha: str, reused: bool) -> ActionReceipt:
        """@brief 追加请求证据并加入当前轮，重放动作仍不再次执行。

        action_requested.data.self_note 取本决策最近一次已落库模型响应的正文（去空白；空或缺失为 null），只记在本决策的
        第一条动作上；开始组的第二条为 null。
        """
        receipt = ActionReceipt(
            action_id=call.action_id,
            round_id=call.context.round_id,
            decision_id=call.context.decision_id,
            request_sha256=request_sha,
        )
        first = all(item.decision_id != call.context.decision_id for item in self.round.pending_actions)
        start = pop_decision_start(self.pending_decision_starts, call.context.agent_id, self.context.state.current_time)
        draft = EventDraft(
            event_type="action_requested",
            occurred_at=self.context.state.current_time,
            agent_id=call.context.agent_id,
            decision_id=call.context.decision_id,
            action_id=call.action_id,
            source_kind="model",
            data={
                "tool_name": call.tool_name,
                "request": call.request.model_dump(mode="json"),
                "request_sha256": request_sha,
                "reused": reused,
                "self_note": self._decision_self_note(call.context) if first else None,
            },
        )
        event = self.context.store.commit(
            validated_payload(self.context.state), [item for item in (start, draft) if item is not None])[-1]
        pending = PendingAction(
            run_id=call.context.run_id,
            round_id=call.context.round_id,
            decision_id=call.context.decision_id,
            action_id=call.action_id,
            agent_id=call.context.agent_id,
            observed_at=self.context.state.current_time,
            world_state_version=self.context.state.version,
            order=self.round.expected_agent_ids.index(call.context.agent_id),
            tool_name=call.tool_name,
            request=call.request.model_dump(mode="json"),
            request_sha256=request_sha,
            requested_event_id=event.event_id,
            reused=reused,
        )
        self.round.pending_actions.append(pending)
        return receipt

    def commit_decision_round(self, round_id: str) -> list[ActionResult]:
        """@brief 完整轮次按冻结顺序结算，一次提交当前世界与结果；每个决策只返回它第一条动作的结果。

        一个决策可以登记两条开始动作：两条结果都落库，返回值仍是每个决策一条，与框架核对的决策上下文条数一致。
        提交之前比较提交前后的世界一次：导航在不在前台、购买里截断的前台区间和停留进出事件都由此得出。
        """
        current = self.require_round(round_id)
        if {item.agent_id for item in current.pending_actions} != set(current.expected_agent_ids):
            logging.error("Decision round is incomplete")
            raise ProtocolError("Decision round does not contain every expected agent")
        staged = stage_presentation(self.context.state, self.observations.pending_presentation).model_copy(deep=True)
        drafts = flush_decision_starts(self.pending_decision_starts, self.context.state.current_time)
        results = []
        for group in _decision_groups(current.pending_actions):
            staged, settled, action_drafts = self._resolve(staged, group)
            for pending, result in zip(group, settled, strict=True):
                self._ops_action(pending, result)
                if not pending.reused:
                    staged = _remember_action(staged, pending, result)
            results.append(settled[0])
            drafts.extend(action_drafts)
        staged, settled = errands.settle(staged, drafts)
        drafts.extend(settled)
        from worldmock.life.callbacks import compare_callback_states

        outcome = compare_callback_states(self.context.state, staged, self.context.catalog, drafts,
                                          self.context.callback_causes)
        staged, navigation = settle_navigation(self.context.state, outcome.state, self.context.catalog)
        drafts = [*drafts, *outcome.drafts, *navigation, *foreground_events(self.context.state, staged),
                  *stay_events(self.context.state, staged, drafts)]
        staged = self.observations._snapshot_decision_references(staged, list(current.expected_agent_ids))
        staged.version = self.context.state.version + 1
        failed = any(draft.data.get("kind") == same_moment.BUDGET_EXHAUSTED_KIND for draft in drafts)
        if failed:
            drafts.append(_failure_draft(staged.current_time, "decision_budget_exhausted", round_id))
        events = self.context.store.commit(validated_payload(staged), drafts)
        self.context.state = staged
        self.observations.pending_presentation = {}
        self.round = None
        self._ops_committed(round_id, results, events)
        if failed:
            logging.error("Decision budget exhausted")
            raise ProtocolError("decision_budget_exhausted")
        return results


    def _ops_committed(self, round_id: str, results: list[ActionResult], events: list) -> None:
        """@brief 写决策轮结算操作日志，事件区间用于与足迹库互相跳转。"""
        self.context.ops.log(
            self.context.state.current_time,
            "engine",
            "info",
            {"code": "DECISION_ROUND_COMMITTED",
            "detail": f"Decision round committed {len(results)} actions",
            "round_id": round_id,
            "event_seq_first": events[0].event_seq if events else None,
            "event_seq_last": events[-1].event_seq if events else None},
        )

    def _ops_action(self, pending: PendingAction, result: ActionResult) -> None:
        """@brief 日志组件按调用判定：购买为 commerce，出行为 travel，其余为 engine。"""
        action = pending.request.get("action")
        component = "commerce" if action == "purchase" else "travel" if action == "travel" else "engine"
        accepted = result.status in {ActionStatus.ACCEPTED, ActionStatus.IN_PROGRESS, ActionStatus.COMPLETED}
        self.context.ops.log(result.occurred_at, component, "info" if accepted else "error",
            {"code": "ACTION_ACCEPTED" if accepted else "ACTION_REJECTED",
            "detail": f"Action {pending.tool_name} resulted in {result.status.value}",
            "agent_id": pending.agent_id, "decision_id": pending.decision_id, "round_id": pending.round_id,
            "action_id": pending.action_id, "tool_name": pending.tool_name,
            "error_code": result.error_code, "reused": pending.reused})

    def abort_decision_round(self, round_id: str, error: Exception | str) -> None:
        """@brief 保存失败结果和幂等身份，不执行本轮生活动作。"""
        current = self.require_round(round_id)
        staged = self.context.state.model_copy(deep=True)
        drafts = flush_decision_starts(self.pending_decision_starts, self.context.state.current_time)
        for pending in current.pending_actions:
            result = ActionResult(
                action_id=pending.action_id,
                agent_id=pending.agent_id,
                status=ActionStatus.FAILED,
                occurred_at=self.context.state.current_time,
                error_code="DECISION_ROUND_ABORTED",
                message=str(error),
            )
            if not pending.reused:
                staged = _remember_action(staged, pending, result)
            drafts.append(_result_draft(pending, result, "action_result"))
        staged.version = self.context.state.version + 1
        drafts.append(
            EventDraft(
                event_type="environment_event",
                occurred_at=self.context.state.current_time,
                visibility="system",
                data={"kind": "decision_round_aborted", "round_id": round_id, "error": str(error)},
            )
        )
        drafts.append(_failure_draft(self.context.state.current_time, error, round_id))
        self.context.store.commit(validated_payload(staged), drafts)
        self.context.state = staged
        self.round = None
        self.context.ops.log(
            self.context.state.current_time,
            "engine",
            "error",
            {"code": "DECISION_ROUND_ABORTED",
            "detail": f"Decision round aborted after {len(current.pending_actions)} actions: {error}",
            "round_id": round_id},
        )

    def validate_context(self, context: "DecisionContext") -> None:
        """@brief 拒绝运行、时刻、人物或世界版本过期的宿主上下文。"""
        from agentsociety2.society import DecisionContext

        if not isinstance(context, DecisionContext):
            raise ProtocolError("Context must use the AgentSociety2 DecisionContext type")
        if context.run_id != self.context.state.run_id or context.agent_id not in self.context.state.agents:
            raise ProtocolError("Decision context run or agent is invalid")
        if int(context.observed_at.timestamp()) != self.context.state.current_time:
            raise ProtocolError("Decision context observation time is stale")
        if context.world_state_version != self.context.state.version:
            raise ProtocolError("Decision context world version is stale")

    def _validate_call(self, call: "DirectToolCall") -> None:
        """@brief 拒绝任意字典请求、错误工具类型和非当前决策轮。"""
        from agentsociety2.society import DirectToolCall

        if not isinstance(call, DirectToolCall) or self.round is None:
            raise ProtocolError("Direct tool call requires an open decision round")
        if call.tool_name == "recall_task_summary":
            logging.error("Read-only query cannot be registered as a world action: %s", call.tool_name)
            raise ProtocolError("Read-only queries must use the immediate query channel")
        self.validate_context(call.context)
        if call.context.round_id != self.round.round_id:
            raise ProtocolError("Direct tool call belongs to another decision round")
        owned = [item for item in self.round.pending_actions if item.agent_id == call.context.agent_id]
        if owned and all((item.action_id != call.action_id for item in owned)) and not _start_pair(owned, call):
            raise ProtocolError("Agent already submitted its decision round action")
        expected = REQUEST_MODELS.get(call.tool_name)
        if expected is None or type(call.request) is not expected:
            raise ProtocolError("Direct tool call request type does not match its tool")

    def _existing_action(self, action_id: str, tool_name: str, request_sha: str) -> StoredAction | None:
        """@brief 对持久和本轮动作执行严格参数一致的幂等判断。"""
        stored = self.context.state.processed_actions.get(action_id)
        pending = next((item for item in self.round.pending_actions if item.action_id == action_id), None)
        if stored and (stored.tool_name != tool_name or stored.request_sha256 != request_sha):
            raise ProtocolError("Action identity was reused with different parameters")
        if pending and (pending.tool_name != tool_name or pending.request_sha256 != request_sha):
            raise ProtocolError("Action identity was reused with different parameters")
        if pending:
            receipt = ActionReceipt(
                action_id=action_id,
                round_id=pending.round_id,
                decision_id=pending.decision_id,
                request_sha256=request_sha,
            )
            return StoredAction(
                action_id=action_id,
                tool_name=tool_name,
                request_sha256=request_sha,
                receipt=receipt.model_dump(mode="json"),
            )
        return stored

    def require_round(self, round_id: str) -> DecisionRoundState:
        """@brief 返回指定开放轮，否则作为协议故障终止。"""
        if self.round is None or self.round.round_id != round_id:
            raise ProtocolError("Decision round is not open")
        return self.round

    def _resolve(self, state: WorldState,
                 group: list[PendingAction]) -> tuple[WorldState, list[ActionResult], list[EventDraft]]:
        """@brief 将一个决策登记的动作（一条，或一个开始组的两条）结算为业务成功或同一份稳定拒绝。

        重放：一组的动作要么都已有存档结果、要么都没有；一条有一条没有属于协议故障。
        """
        replayed = {pending.reused for pending in group}
        if replayed == {True}:
            return self._reused_results(state, group)
        if len(replayed) != 1:
            logging.error("Start group replay is inconsistent")
            raise ProtocolError("Start group replay is inconsistent")
        try:
            (changed, results) = self.actions.dispatch(state, group)
            return self._accept_results(state, changed, group, results)
        except BusinessRuleError as error:
            return self._reject_results(state, group, error)

    def _accept_results(self, state: WorldState, changed: WorldState, group: list[PendingAction],
                        results: list[ActionResult]) -> tuple[WorldState, list[ActionResult], list[EventDraft]]:
        """@brief 受理的每条动作各写受理与结果事件；同刻轮数、播报机会与同刻唤醒回调按组各做一次。"""
        first = group[0]
        changed = self._consume_report_opportunity(state, changed, first.agent_id)
        changed = self._after_success(changed, first.agent_id, first.tool_name)
        (changed, records) = activity_control.record_changes(state, changed, first, self.actions.record_context)
        index = title_index(self.context.catalog)
        drafts, result_drafts = [], []
        for pending, result in zip(group, results, strict=True):
            accepted = EventDraft(
                event_type="action_accepted", occurred_at=state.current_time,
                agent_id=pending.agent_id,
                decision_id=pending.decision_id,
                action_id=pending.action_id,
                activity_id=result.activity_id,
                caused_by=_pending_causes(pending),
                data=_result_event_data(result, changed, index),
            )
            result_draft = _result_draft(pending, result, "action_result")
            result_draft = result_draft.model_copy(
                update={"resource_changes": self.actions.resource_changes.pop(pending.action_id, {})})
            for activity in changed.activities.values():
                if activity.origin_action_id == pending.action_id:
                    activity.data["accepted_event_id"] = result_draft.event_id
            drafts.extend((accepted, result_draft))
            result_drafts.append(result_draft)
        result_draft = result_drafts[0]
        drafts.extend(records)
        drafts.extend(self._action_callbacks(changed, group, result_drafts,
            state.accounts[state.agents[first.agent_id].balance_account_id]))
        received = next((draft for pending, draft in zip(group, result_drafts, strict=True)
                         if pending.tool_name == START_TOOLS[0]), result_draft)
        drafts.extend(draft.model_copy(update={"caused_by": [received.event_id]})
            if draft.data.get("kind") == "goods_received" and not draft.caused_by else draft
            for draft in self.actions.drain_drafts())
        drafts.extend(self._same_moment_callbacks(changed, first, result_draft))
        return (changed, results, drafts)

    def _reject_results(self, state: WorldState, group: list[PendingAction],
                        error: BusinessRuleError) -> tuple[WorldState, list[ActionResult], list[EventDraft]]:
        """@brief 业务拒绝回滚领域转换；同一决策的每条动作写内容相同的拒绝，计数与真实同刻后续触发按组各做一次。

        拒绝脉冲施加之后立刻从身体分录缓冲取走，写在这组第一条 action_result 事件正文顶层的 resource_flows 上，不进
        ActionResult；没有施加脉冲时不写这个键（docs/design/05-recording-engine.md「事件与来源」）。
        """
        self.actions.resource_changes.clear()
        self.actions.drain_drafts()
        first = group[0]
        consumed = self._consume_report_opportunity(state, state, first.agent_id)
        (changed, rejected) = self._after_rejection(consumed, first, error)
        flows = _resource_flows_data(first.agent_id, state.current_time)
        activity = next(iter(active_activities(changed, first.agent_id)), None)
        data = {"error_code": str(error), **rejected.data}
        if activity is not None:
            data["activity"] = activity_snapshot(activity, title_index(self.context.catalog))
        results = [rejected.model_copy(update={"action_id": pending.action_id}) for pending in group]
        drafts = []
        for pending, result in zip(group, results, strict=True):
            drafts.append(EventDraft(
                event_type="action_rejected", occurred_at=state.current_time,
                agent_id=pending.agent_id,
                decision_id=pending.decision_id, action_id=pending.action_id,
                activity_id=activity.activity_id if activity else None,
                caused_by=_pending_causes(pending),
                data=dict(data),
            ))
            drafts.append(_result_draft(pending, result, "action_result"))
        first_result = drafts[1]
        if flows:
            first_result.data[RESOURCE_FLOWS_FIELD] = flows
        drafts.extend(self.actions.drain_drafts())
        drafts.extend(self._same_moment_callbacks(changed, first, first_result))
        return (changed, results, drafts)

    def _action_callbacks(self, state: WorldState, group: list[PendingAction], results: list[EventDraft],
                          balance_before: int) -> list[EventDraft]:
        """@brief 每条受理的开始调用求值一次动作回调并回链它自己的受理结果；先主调用、后副调用，与登记先后无关。

        触发的键取这条调用开始的活动记下的动作，出行的触发原因另带这次出行的出行方式；一次购买无论几件只触发一次。
        主调用的净减少提示包含已收的车费，同组副调用按主调用之后的余额计算。
        """
        if any(pending.tool_name not in START_TOOLS for pending in group):
            return []
        from worldmock.life.callbacks import CallbackCause, evaluate_callbacks

        drafts, before = [], balance_before
        ordered = sorted(zip(group, results, strict=True), key=lambda pair: START_TOOLS.index(pair[0].tool_name))
        for pending, result in ordered:
            started = state.activities[result.activity_id]
            cause = CallbackCause("action", started.data["action"], pending.agent_id, result.event_id,
                                  f"{pending.action_id}:0", started.travel_method_id)
            outcome = evaluate_callbacks(state, self.context.catalog, cause, balance_before=before)
            state.agents, state.accounts = outcome.state.agents, outcome.state.accounts
            drafts.extend(outcome.drafts)
            self.context.callback_causes.update(outcome.causes)
            before = None
        return drafts

    def _same_moment_callbacks(self, state: WorldState, pending: PendingAction,
                               result: EventDraft) -> list[EventDraft]:
        """@brief 同刻再决策类别只以本轮真实动作结果作为触发原因。"""
        agent = state.agents[pending.agent_id]
        if agent.next_decision_at != state.current_time:
            return []
        from worldmock.life.callbacks import CallbackCause, evaluate_callbacks
        from worldmock.life.schedule import wake_categories

        drafts = []
        for category in wake_categories(agent.pending_wake_reasons):
            cause = CallbackCause("event", category, agent.agent_id, result.event_id,
                                  f"wake:{pending.action_id}")
            outcome = evaluate_callbacks(state, self.context.catalog, cause)
            state.agents, state.accounts = outcome.state.agents, outcome.state.accounts
            self.context.callback_causes.update(outcome.causes)
            drafts.extend(outcome.drafts)
        return drafts

    def _consume_report_opportunity(self, before: WorldState, changed: WorldState, agent_id: int) -> WorldState:
        """@brief 播报机会给出后，受理和拒绝都消费所有当时到期的锚点。"""
        if "progress_report_due" not in before.agents[agent_id].pending_wake_reasons:
            return changed
        interval = self.context.config.progress_report_interval_seconds
        return consume_progress_report_opportunity(changed, agent_id, interval)

    def _after_success(self, state: WorldState, agent_id: int, tool_name: str) -> WorldState:
        """@brief 受理计入同刻轮数并清零连续拒绝，按同刻规则或决策节律安排下一次决策（第一章规则 2、4、7）。"""
        agent = same_moment.count_round(state.agents[agent_id])
        (next_at, reasons, stage) = self._round_schedule(state, agent, None)
        changed = agent.model_copy(
            update={
                "next_decision_at": next_at,
                "pending_wake_reasons": reasons,
                "same_moment_stage": stage,
            },
            deep=True,
        )
        return _put_agent(state, changed)

    def _round_schedule(
        self, state: WorldState, agent, rejection: str | None, rhythm_next=None, free: bool = False
    ) -> tuple[int, list[str], SameMomentStage]:
        """@brief 按同刻阶梯安排本轮之后的下一次决策（docs/design/04-simulation-engine.md）。

        六轮耗尽仍需再决策时，随本动作结果记录真实预算故障，提交后停止，时间不推进。

        :param rejection: 本轮业务拒绝的错误码（重放按存档结果），受理时为 None。
        :param free: 本轮的拒绝是免计的名称类拒绝（docs/design/04-simulation-engine.md，不计同刻轮数）。
        """
        now = state.current_time

        def by_rhythm() -> int:
            """@brief 节律候选以本次决策时刻为上次决策时刻（规则 7）；无候选取结算间隔。"""
            return next_decision_at(self.context, state, agent.model_copy(update={"last_decision_time": now}))

        outcome = same_moment.RoundOutcome(
            now=now,
            agent=agent,
            active=bool(active_activities(state, agent.agent_id)),
            rejected=rejection is not None,
            pending=bool(activity_control.pending_conclusions(state, agent.agent_id)),
        )
        if same_moment.budget_exhausted(outcome):
            code = (None if free else rejection) or same_moment.latest_rejection_code(state, agent.agent_id)
            self.actions.pending_event_drafts.append(same_moment.exhausted_event(outcome, code))
            return now, [], same_moment.next_stage(outcome)
        return same_moment.schedule_after_round(outcome, rhythm_next or by_rhythm)

    def _after_rejection(
        self, state: WorldState, pending: PendingAction, error: BusinessRuleError
    ) -> tuple[WorldState, ActionResult]:
        """@brief 业务拒绝计入同刻轮数与连续拒绝；未达上限同一秒再决策，服务拒绝脉冲每人每秒至多一次；名称类拒绝每人每秒
        前两次不计同刻轮数、不施加脉冲（docs/design/04-simulation-engine.md）。
        same_moment.rejection_counts 控制计数；结果 message 就是公开原因 reason，data 带 reason、fix、next_step 与逐项明细。"""
        counted = same_moment.rejection_counts(state, pending.agent_id, str(error))
        agent = state.agents[pending.agent_id]
        agent = same_moment.count_round(agent) if counted else agent
        (next_decision_at, reasons, stage) = self._round_schedule(state, agent, str(error), free=not counted)
        pulse_key = same_moment.rejection_pulse(state, agent.agent_id, str(error))
        body = agent.body if pulse_key is None else apply_relaxation_change(agent.body, 0.05)
        changed_agent = agent.model_copy(
            update={
                "next_decision_at": next_decision_at,
                "pending_wake_reasons": reasons, "same_moment_stage": stage, "body": body,
            }, deep=True,
        )
        changed = _put_agent(state, changed_agent)
        logging.error("Action rejected: %s", rejection_reason_text(error))
        guidance = rejection_guidance(error)
        guidance.update(
            business_feedback(
                pending.request, str(error), guidance, error.unmet if isinstance(error, PreconditionError) else [],
                self.actions._frozen_observation(pending),
            )
        )
        message = guidance["reason"]
        data = {
            "same_moment_round": changed_agent.same_moment_rounds,
            "max_same_moment_decisions": same_moment.OPEN_ROUNDS,
            "next_decision_at": next_decision_at,
            **guidance,
        }
        if isinstance(error, PreconditionError):
            data["unmet"] = error.unmet
            if error.suggested is not None:
                data["suggested"] = error.suggested
        result = ActionResult(
            action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.REJECTED, occurred_at=state.current_time,
            error_code=str(error), message=message, data=data,
        )
        return (changed, result)

    def _reused_results(
        self, state: WorldState, group: list[PendingAction]
    ) -> tuple[WorldState, list[ActionResult], list[EventDraft]]:
        """@brief 返回持久终态结果并只记录本次重放证据；重放计入同刻轮数（一组只加一次），按存档结果排期，
        不重做领域动作与脉冲（规则 9）。"""
        results = [ActionResult.model_validate(state.processed_actions[pending.action_id].result) for pending in group]
        drafts = [draft for pending, result in zip(group, results, strict=True)
                  for draft in _replay_drafts(state, pending, result)]
        agent = same_moment.count_round(state.agents[group[0].agent_id])
        rejection = results[0].error_code if results[0].status == ActionStatus.REJECTED else None
        (next_at, reasons, stage) = self._round_schedule(state, agent, rejection)
        agent = agent.model_copy(
            update={"next_decision_at": next_at, "pending_wake_reasons": reasons, "same_moment_stage": stage}, deep=True
        )
        changed = _put_agent(state, agent)
        return (changed, results, [*drafts, *self.actions.drain_drafts()])

    def _decision_self_note(self, context: "DecisionContext") -> str | None:
        """@brief 本决策最近一次已落库模型响应（phase=response）的正文，作为世界请求的 self_note；没有则为 None。

        从持久化模型证据读取（本人最近一次观察之后、同一决策身份），恢复后重放登记同样得到一致结果。
        """
        after = self.context.store.last_agent_event_seq("observation", context.agent_id)
        responses = [
            event
            for event in self.context.store.read_agent_events("model_response", context.agent_id, after)
            if event.decision_id == context.decision_id and event.data.get("phase") == "response"
        ]
        return response_self_note(responses[-1].data.get("response")) if responses else None


def _decision_groups(actions: list[PendingAction]) -> list[list[PendingAction]]:
    """@brief 已登记动作按决策分组：组按人物的冻结次序排列，组内保持登记次序。"""
    groups: dict[str, list[PendingAction]] = {}
    for pending in sorted(actions, key=lambda item: item.order):
        groups.setdefault(pending.decision_id, []).append(pending)
    return list(groups.values())


def _start_pair(owned: list[PendingAction], call: "DirectToolCall") -> bool:
    """@brief 同一人物本轮的第二条登记只在与已有的恰好一条组成开始组时接受：同一决策、两个开始工具各一个。"""
    return (len(owned) == 1 and owned[0].decision_id == call.context.decision_id
            and {owned[0].tool_name, call.tool_name} == set(START_TOOLS))


def _replay_drafts(state: WorldState, pending: PendingAction, result: ActionResult) -> list[EventDraft]:
    """@brief 一条重放动作的两条证据事件：按存档结果的受理或拒绝，以及带 reused 标记的结果。"""
    accepted_statuses = {ActionStatus.ACCEPTED, ActionStatus.IN_PROGRESS, ActionStatus.COMPLETED}
    data = result.model_dump(mode="json")
    data["reused"] = True
    shared = {"occurred_at": state.current_time, "agent_id": pending.agent_id, "decision_id": pending.decision_id,
              "action_id": pending.action_id, "activity_id": result.activity_id,
              "caused_by": [pending.requested_event_id]}
    kind = "action_accepted" if result.status in accepted_statuses else "action_rejected"
    return [EventDraft(event_type=kind, data=data, **shared),
            EventDraft(event_type="action_result", data=dict(data), **shared)]
