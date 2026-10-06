"""@brief DecisionCoordinator 的独立生活运行职责。"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext, DirectToolCall
from worldmock.life.world_ops import _failure_draft
from worldmock.life.world_ops import _put_agent
from worldmock.life.world_ops import _request_sha

import logging
from worldmock.errors import BusinessRuleError, PreconditionError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life import activity_control, errands, same_moment, social_actions
from worldmock.life.action_evidence import _pending_causes, _remember_action, _result_draft, _result_event_data
from worldmock.life.guidance import rejection_guidance
from worldmock.life.business_feedback import business_feedback
from worldmock.life.vocabulary import WAYS
from worldmock.life.activity_titles import activity_title
from worldmock.life.decision_start import (
    defer_decision_starts,
    flush_decision_starts,
    pop_decision_start,
    response_self_note,
)
from worldmock.life.body import apply_mood_pulse
from worldmock.life.models import (
    ActionReceipt,
    ActionResult,
    ActionStatus,
    ActivityKind,
    DecisionRoundState,
    PendingAction,
    REQUEST_MODELS,
    rejection_reason_text,
    SameMomentStage,
    StoredAction,
    WorldState,
    active_activities,
)
from worldmock.life.observe import ObservationBuilder
from worldmock.life.rhythm import engine_rhythm_inputs, next_decision_candidates
from worldmock.life.session_actions import ActionHandlers
from worldmock.life.models import validated_payload
from worldmock.life.presentation import stage_presentation

from worldmock.life.engine_context import EngineContext
from worldmock.life.activity_titles import activity_title, activity_snapshot

_OPS_COMPONENTS = {"purchase": "commerce", "travel_to": "travel"}


class DecisionCoordinator:
    """@brief 显式依赖上下文的DecisionCoordinator。"""

    def __init__(self, context: EngineContext, actions: ActionHandlers, observations: ObservationBuilder):
        """@brief 绑定状态、动作与观察，并建立独立轮次簿记。"""
        self.context, self.actions, self.observations = context, actions, observations
        self.round: DecisionRoundState | None = None
        self.pending_decision_starts: dict[int, dict] = {}

    def begin_decision_round(self, contexts: list["DecisionContext"], ready_agent_ids: tuple[int, ...]) -> None:
        """@brief 持久化观察顺序；decision_started 延迟到首个行动请求时写（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第二章）。"""
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
        self.pending_decision_starts = defer_decision_starts(contexts, round_id, self.context.state)
        self.context.ops.log(
            self.context.state.current_time,
            "engine",
            "info",
            "DECISION_ROUND_BEGAN",
            f"Decision round opened for {len(agent_ids)} agents",
            round_id=round_id,
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

        action_requested.data.self_note 取本决策最近一次已落库模型响应的正文（去空白；空或缺失为 null）。
        """
        receipt = ActionReceipt(
            action_id=call.action_id,
            round_id=call.context.round_id,
            decision_id=call.context.decision_id,
            request_sha256=request_sha,
        )
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
                "self_note": self._decision_self_note(call.context),
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
        """@brief 按冻结顺序结算完整决策轮并一次提交所有状态与结果。

        全轮动作完成后合并收件人的同秒机会，避免自身排期覆盖（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「人物之间的消息」）。
        合并前把已全文呈现的消息记为已读，后续不重复唤醒（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章「直接消息截断」）。
        重放身份可能对应多条观察，不参与记已读，也不重做领域动作。
        """
        current = self.require_round(round_id)
        submitted = {item.agent_id for item in current.pending_actions}
        if submitted != set(current.expected_agent_ids):
            raise ProtocolError("Decision round does not contain every expected agent")
        staged = stage_presentation(self.context.state, self.observations.pending_presentation).model_copy(deep=True)
        staged = staged
        drafts: list[EventDraft] = flush_decision_starts(self.pending_decision_starts, self.context.state.current_time)
        results = []
        for pending in sorted(current.pending_actions, key=lambda item: item.order):
            (staged, result, action_drafts) = self._resolve(staged, pending)
            self._ops_action(pending, result)
            results.append(result)
            drafts.extend(action_drafts)
            if not pending.reused:
                staged = _remember_action(staged, pending, result)
        (staged, settled) = errands.settle(staged, drafts)
        drafts.extend(settled)
        recipients = {
            draft.agent_id
            for draft in drafts
            if draft.event_type == "environment_event"
            and draft.data.get("kind") == "message_arrival"
            and (draft.agent_id is not None)
        }
        presented = {
            item.agent_id: self._presented_messages(item) for item in current.pending_actions if not item.reused
        }
        staged = same_moment.read_observed_messages(staged, self.context.state, presented)
        staged = same_moment.merge_message_readiness(
            staged, recipients, self.context.scenario.max_same_moment_decisions
        )
        staged = same_moment.merge_partner_endings(
            self.context.state, staged, self.context.scenario.max_same_moment_decisions
        )
        staged = self.observations._snapshot_decision_references(staged, list(current.expected_agent_ids))
        staged.version = self.context.state.version + 1
        events = self.context.store.commit(validated_payload(staged), drafts)
        self.context.state = staged
        self.observations.pending_presentation = {}
        self.round = None
        self._ops_committed(round_id, results, events)
        return results

    def _presented_messages(self, pending: PendingAction) -> frozenset[str]:
        """@brief 本决策观察中已全文呈现的消息身份（观察事件的 presented_message_ids）。

        没有观察的引擎直驱提交什么也没呈现，返回空集；同一决策同一秒有多条观察是协议错误。
        """
        matches = [
            event
            for event in self.context.store.read_agent_events("observation", pending.agent_id)
            if event.decision_id == pending.decision_id and event.occurred_at == pending.observed_at
        ]
        if len(matches) > 1:
            raise ProtocolError("Decision observation is ambiguous")
        return frozenset(matches[0].data["presented_message_ids"]) if matches else frozenset()

    def _ops_committed(self, round_id: str, results: list[ActionResult], events: list) -> None:
        """@brief 写决策轮结算操作日志，事件区间用于与足迹库互相跳转。"""
        self.context.ops.log(
            self.context.state.current_time,
            "engine",
            "info",
            "DECISION_ROUND_COMMITTED",
            f"Decision round committed {len(results)} actions",
            round_id=round_id,
            event_seq_first=events[0].event_seq if events else None,
            event_seq_last=events[-1].event_seq if events else None,
        )

    def _ops_action(self, pending: PendingAction, result: ActionResult) -> None:
        """@brief 写动作受理或拒绝与资金变动操作日志；重放动作不重复记资金。

        start_activity 的组件按私有绑定的方式闭集（WAYS）映射的内部标准动作取，不按动词或短语反解。
        """
        valid_start = pending.tool_name == "start_activity" and result.error_code != "ACTION_REQUEST_INVALID"
        ways = [item["way"] for item in pending.request["items"]] if valid_start else []
        components = {
            _OPS_COMPONENTS.get(WAYS[way][1], "engine") for way in ways if isinstance(way, str) and way in WAYS
        }
        component = next(iter(components)) if len(components) == 1 else _OPS_COMPONENTS.get(pending.tool_name, "engine")
        accepted = result.status in {ActionStatus.ACCEPTED, ActionStatus.IN_PROGRESS, ActionStatus.COMPLETED}
        keys = {
            "agent_id": pending.agent_id,
            "decision_id": pending.decision_id,
            "round_id": pending.round_id,
            "action_id": pending.action_id,
            "tool_name": pending.tool_name,
            "error_code": result.error_code,
            "reused": pending.reused,
        }
        self.context.ops.log(
            result.occurred_at,
            component,
            "info" if accepted else "warn",
            "ACTION_ACCEPTED" if accepted else "ACTION_REJECTED",
            f"Action {pending.tool_name} resulted in {result.status.value}",
            **keys,
        )
        changes = result.data.get("resource_changes")
        if changes and (not pending.reused):
            self.context.ops.log(
                result.occurred_at,
                component,
                "info",
                "FUNDS_CHANGED",
                f"Action {pending.tool_name} moved account funds",
                account_changes=changes,
                **keys,
            )

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
            "DECISION_ROUND_ABORTED",
            f"Decision round aborted after {len(current.pending_actions)} actions: {error}",
            round_id=round_id,
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
        if call.tool_name in {"query_world", "recall_task_summary"}:
            logging.error("Read-only query cannot be registered as a world action: %s", call.tool_name)
            raise ProtocolError("Read-only queries must use the immediate query channel")
        self.validate_context(call.context)
        if call.context.round_id != self.round.round_id:
            raise ProtocolError("Direct tool call belongs to another decision round")
        owned = [item for item in self.round.pending_actions if item.agent_id == call.context.agent_id]
        if owned and all((item.action_id != call.action_id for item in owned)):
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

    def _resolve(self, state: WorldState, pending: PendingAction) -> tuple[WorldState, ActionResult, list[EventDraft]]:
        """@brief 将一个有类型请求结算为业务成功或稳定拒绝。"""
        if pending.reused:
            return self._reused_result(state, pending)
        try:
            (changed, result) = self.actions.dispatch(state, pending)
            (changed, _) = social_actions.end_partner_chats(state, changed)
            changed = self._after_success(changed, pending.agent_id, pending.tool_name)
            (changed, records) = activity_control.record_changes(state, changed, pending, self.actions.record_context)
            resolved = changed.activities.get(result.activity_id) if result.activity_id else None
            resolved_title = activity_title(resolved, self.context.titles) if resolved is not None else None
            accepted = EventDraft(
                event_type="action_accepted",
                occurred_at=state.current_time,
                agent_id=pending.agent_id,
                decision_id=pending.decision_id,
                action_id=pending.action_id,
                activity_id=result.activity_id,
                caused_by=_pending_causes(pending),
                data=_result_event_data(result, changed, resolved_title),
            )
            drafts = [accepted, _result_draft(pending, result, "action_result"), *records]
            drafts.extend(self.actions.drain_drafts())
            return (changed, result, drafts)
        except BusinessRuleError as error:
            (changed, result) = self._after_rejection(state, pending, error)
            agent = changed.agents[pending.agent_id]
            activity = next(iter(active_activities(changed, agent.agent_id)), None)
            data = {"error_code": str(error), **result.data}
            if activity is not None:
                data["activity"] = activity_snapshot(activity, self.context.titles, self.context.lifestyle_items)
            rejected = EventDraft(
                event_type="action_rejected",
                occurred_at=state.current_time,
                agent_id=pending.agent_id,
                decision_id=pending.decision_id,
                action_id=pending.action_id,
                activity_id=activity.activity_id if activity else None,
                caused_by=_pending_causes(pending),
                data=data,
            )
            drafts = [rejected, _result_draft(pending, result, "action_result")]
            drafts.extend(self.actions.drain_drafts())
            return (changed, result, drafts)

    def _after_success(self, state: WorldState, agent_id: int, tool_name: str) -> WorldState:
        """@brief 受理计入同刻轮数并清零连续拒绝，按同刻规则或决策节律安排下一次决策（第一章规则 2、4、7）。"""
        agent = same_moment.count_round(state.agents[agent_id])
        (next_at, reasons, stage) = self._round_schedule(state, agent, None)
        changed = agent.model_copy(
            update={
                "next_decision_at": next_at,
                "consecutive_rejections": 0,
                "pending_wake_reasons": reasons,
                "same_moment_stage": stage,
            },
            deep=True,
        )
        return _put_agent(state, changed)

    def _round_schedule(
        self, state: WorldState, agent, rejection: str | None, rhythm_next=None, free: bool = False
    ) -> tuple[int, list[str], SameMomentStage]:
        """@brief 按同刻阶梯安排本轮之后的下一次决策（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章规则 2–4、7）。

        受限轮用完仍要同一秒再决策时，暂存 decision_budget_exhausted 事件并技术停顿（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求
        第五章「失败后强制等待与跨决策预算」），随本动作的结果一起提交；事件带最近一次计入同刻轮数的拒绝码。

        :param rejection: 本轮业务拒绝的错误码（重放按存档结果），受理时为 None。
        :param free: 本轮的拒绝是免计的名称类拒绝（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-名称类拒绝不计同刻轮数 ，不计同刻轮数）。
        """
        now = state.current_time

        def by_rhythm() -> int:
            """@brief 节律候选以本次决策时刻为上次决策时刻（规则 7）；无候选取结算间隔。"""
            candidate = next_decision_candidates(
                agent.model_copy(update={"last_decision_time": now}), engine_rhythm_inputs(self.context, state)
            )
            return candidate if candidate is not None else now + self.context.scenario.activity_feedback_seconds

        outcome = same_moment.RoundOutcome(
            now=now,
            agent=agent,
            active=bool(active_activities(state, agent.agent_id)),
            rejected=rejection is not None,
            limit=self.context.scenario.max_same_moment_decisions,
            pending=bool(activity_control.pending_conclusions(state, agent.agent_id)),
        )
        if same_moment.budget_exhausted(outcome):
            code = (None if free else rejection) or same_moment.latest_rejection_code(state, agent.agent_id)
            (schedule, draft) = same_moment.exhausted_schedule(
                outcome, self.context.scenario.activity_feedback_seconds, code
            )
            self.actions.pending_event_drafts.append(draft)
            return schedule
        return same_moment.schedule_after_round(outcome, rhythm_next or by_rhythm)

    def _after_rejection(
        self, state: WorldState, pending: PendingAction, error: BusinessRuleError
    ) -> tuple[WorldState, ActionResult]:
        """@brief 业务拒绝计入同刻轮数与连续拒绝；未达上限同一秒再决策，服务拒绝脉冲每人每秒至多一次；名称类拒绝每人每秒
        前两次不计同刻轮数、不施加脉冲（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-名称类拒绝不计同刻轮数 ）。
        same_moment.rejection_counts 控制计数；结果 message 就是公开原因 reason，data 带 reason、fix、next_step 与逐项明细。"""
        counted = same_moment.rejection_counts(state, pending.agent_id, str(error))
        agent = state.agents[pending.agent_id]
        agent = same_moment.count_round(agent) if counted else agent
        (next_decision_at, reasons, stage) = self._round_schedule(state, agent, str(error), free=not counted)
        pulse_key = same_moment.rejection_pulse(state, agent.agent_id, str(error))
        body = agent.body if pulse_key is None else apply_mood_pulse(agent.body, pulse_key)
        changed_agent = agent.model_copy(
            update={
                "next_decision_at": next_decision_at, "consecutive_rejections": agent.consecutive_rejections + 1,
                "pending_wake_reasons": reasons, "same_moment_stage": stage, "body": body,
            }, deep=True,
        )
        changed = _put_agent(state, changed_agent)
        logging.info("Action rejected: %s", rejection_reason_text(error))
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
            "max_same_moment_decisions": self.context.scenario.max_same_moment_decisions,
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

    def _reused_result(
        self, state: WorldState, pending: PendingAction
    ) -> tuple[WorldState, ActionResult, list[EventDraft]]:
        """@brief 返回持久终态结果并只记录本次重放证据；重放计入同刻轮数，按存档结果排期，不重做领域动作与脉冲（规则 9）。"""
        stored = state.processed_actions[pending.action_id]
        result = ActionResult.model_validate(stored.result)
        accepted_statuses = {ActionStatus.ACCEPTED, ActionStatus.IN_PROGRESS, ActionStatus.COMPLETED}
        event_type = "action_accepted" if result.status in accepted_statuses else "action_rejected"
        data = result.model_dump(mode="json")
        data["reused"] = True
        first = EventDraft(
            event_type=event_type,
            occurred_at=state.current_time,
            agent_id=pending.agent_id,
            decision_id=pending.decision_id,
            action_id=pending.action_id,
            activity_id=result.activity_id,
            caused_by=[pending.requested_event_id],
            data=data,
        )
        second = EventDraft(
            event_type="action_result",
            occurred_at=state.current_time,
            agent_id=pending.agent_id,
            decision_id=pending.decision_id,
            action_id=pending.action_id,
            activity_id=result.activity_id,
            caused_by=[pending.requested_event_id],
            data=data,
        )
        agent = same_moment.count_round(state.agents[pending.agent_id])
        rejection = result.error_code if result.status == ActionStatus.REJECTED else None
        (next_at, reasons, stage) = self._round_schedule(state, agent, rejection)
        agent = agent.model_copy(
            update={"next_decision_at": next_at, "pending_wake_reasons": reasons, "same_moment_stage": stage}, deep=True
        )
        changed = _put_agent(state, agent)
        return (changed, result, [first, second, *self.actions.drain_drafts()])

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
