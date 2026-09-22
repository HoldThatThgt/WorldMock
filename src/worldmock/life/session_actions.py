"""@brief 稳定工具面的 5 个会话动作处理器：启动、回看、播报、暂停、结束会话。"""

from __future__ import annotations

import hashlib

from worldmock.errors import BusinessRuleError
from worldmock.life.models import (
    ActionResult,
    ActionStatus,
    ActivityChange,
    ActivityKind,
    ActivityStartParams,
    ChangeActivityRequest,
    ConcludeActivityRequest,
    ConsumeItem,
    ConsumeRequest,
    LIFESTYLE_TYPE_PREFIX,
    PauseActivityRequest,
    PendingAction,
    PurchaseItem,
    PurchaseOrderRequest,
    RecallTaskSummaryRequest,
    ReportStatusRequest,
    SendMessageRequest,
    SessionState,
    SessionStatus,
    SessionTaskKind,
    StartActivityRequest,
    StartSocialActivityRequest,
    SubmitWorkRequest,
    WorldState,
)


class SessionActionMixin:
    """@brief 稳定工具面的 5 个会话动作：启动、回看、播报、暂停、结束都在此落地。"""

    def _start_activity(self, state: WorldState, pending: PendingAction,
                        request: StartActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 按 targets×action 路由到领域活动启动；建立对应 SessionState。"""
        action = request.action
        target = request.targets[0] if request.targets else None
        if len(request.targets) > 2:
            raise BusinessRuleError("SESSION_LIMIT_EXCEEDED")
        changed, result = self._dispatch_start_action(state, pending, request, action, target)
        session = self._open_session(pending, request, result, state.current_time)
        sessions = dict(changed.sessions)
        sessions[session.session_id] = session
        changed = changed.model_copy(update={"sessions": sessions}, deep=True)
        return changed, result

    def _dispatch_start_action(self, state: WorldState, pending: PendingAction,
                               request: StartActivityRequest, action: str,
                               target: str | None) -> tuple[WorldState, ActionResult]:
        """@brief 按 action 枚举路由到对应领域启动路径（各分支复用既有领域处理器）。"""
        targeted_kinds = {"work": ActivityKind.WORK, "watch": ActivityKind.WATCH,
                          "play": ActivityKind.PLAY_GAME, "read": ActivityKind.READ_MAIL}
        if action == "travel_to":
            return self._start_travel_action(state, pending, request)
        if action in targeted_kinds:
            return self._start_targeted_action(state, pending, request, targeted_kinds[action], target)
        if action == "browse":
            return self._start_browse_action(state, pending, request, target)
        if action == "do":
            return self._start_lifestyle_action(state, pending, request, target)
        if action in ("wait", "sleep"):
            return self._start_self_action(state, pending, request, action)
        if action in ("chat", "call"):
            return self._start_social_action(state, pending, request, action, target)
        if action == "submit":
            return self._start_submit_action(state, pending, request, target)
        if action == "purchase":
            return self._start_purchase_action(state, pending, request)
        if action == "consume":
            return self._start_consume_action(state, pending, request)
        if action == "message":
            return self._start_message_action(state, pending, request, target)
        raise BusinessRuleError("ACTION_NOT_SUPPORTED")

    def _start_travel_action(self, state: WorldState, pending: PendingAction,
                             request: StartActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 启动 travel_to 动作对应的出行活动。"""
        if not request.destination_id or not request.travel_mode:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        params = ActivityStartParams(activity_type=ActivityKind.TRAVEL,
                                     planned_duration_seconds=request.planned_duration_seconds or 1,
                                     destination_id=request.destination_id,
                                     travel_mode=request.travel_mode)
        return self._start(state, pending, params)

    def _start_targeted_action(self, state: WorldState, pending: PendingAction, request: StartActivityRequest,
                               kind: ActivityKind, target: str | None) -> tuple[WorldState, ActionResult]:
        """@brief 启动 work/watch/play/read 等指向单一目标的定时活动。"""
        duration = request.planned_duration_seconds
        if not target or not duration:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        params = ActivityStartParams(activity_type=kind, planned_duration_seconds=duration, target_id=target)
        return self._start(state, pending, params)

    def _start_browse_action(self, state: WorldState, pending: PendingAction, request: StartActivityRequest,
                             target: str | None) -> tuple[WorldState, ActionResult]:
        """@brief 启动 browse 动作对应的无既定目标浏览活动。"""
        duration = request.planned_duration_seconds
        if not duration:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        params = ActivityStartParams(activity_type=ActivityKind.BROWSE, planned_duration_seconds=duration,
                                     target_id=target or "product_catalog")
        return self._start(state, pending, params)

    def _start_lifestyle_action(self, state: WorldState, pending: PendingAction, request: StartActivityRequest,
                                target: str | None) -> tuple[WorldState, ActionResult]:
        """@brief 启动 do 动作对应的生活方式目录活动。"""
        duration = request.planned_duration_seconds
        if not target or not duration:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        params = ActivityStartParams(activity_type=ActivityKind.LIFESTYLE, planned_duration_seconds=duration,
                                     target_id=f"{LIFESTYLE_TYPE_PREFIX}{target}")
        return self._start(state, pending, params)

    def _start_self_action(self, state: WorldState, pending: PendingAction, request: StartActivityRequest,
                           action: str) -> tuple[WorldState, ActionResult]:
        """@brief 启动 wait/sleep 动作对应的无目标自身活动。"""
        duration = request.planned_duration_seconds
        if not duration:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        kind = ActivityKind.WAIT if action == "wait" else ActivityKind.SLEEP
        return self._start(state, pending, ActivityStartParams(activity_type=kind,
                                                                planned_duration_seconds=duration))

    def _start_social_action(self, state: WorldState, pending: PendingAction, request: StartActivityRequest,
                             action: str, target: str | None) -> tuple[WorldState, ActionResult]:
        """@brief 启动 chat/call 动作对应的社交活动。"""
        duration = request.planned_duration_seconds
        if not target or not duration:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        social = StartSocialActivityRequest(kind=action, partner_id=target,
                                            duration_minutes=max(1, min(120, duration // 60)))
        return self._start_social_activity(state, pending, social)

    def _start_submit_action(self, state: WorldState, pending: PendingAction, request: StartActivityRequest,
                             target: str | None) -> tuple[WorldState, ActionResult]:
        """@brief 将 submit 动作翻译为既有工作提交请求，复用其独立验收结算路径。"""
        if not target or request.deliverable is None:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        submit = SubmitWorkRequest(task_id=target, deliverable=request.deliverable)
        return self._submit_work(state, pending, submit)

    def _start_purchase_action(self, state: WorldState, pending: PendingAction,
                               request: StartActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将 purchase 动作翻译为既有整单购买请求；缺省目的地即视为到店自提。"""
        if not request.targets or request.quantity is None or not request.price_version:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        agent = state.agents[pending.agent_id]
        fulfillment = "delivery" if request.destination_id else "pickup"
        destination = request.destination_id or agent.location_id
        quantity = int(request.quantity)
        items = tuple(PurchaseItem(supply_id=item, quantity=quantity, price_version=request.price_version)
                      for item in request.targets)
        purchase = PurchaseOrderRequest(items=items, fulfillment=fulfillment, destination_id=destination)
        return self._purchase(state, pending, purchase)

    def _start_consume_action(self, state: WorldState, pending: PendingAction,
                              request: StartActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将 consume 动作翻译为既有进食请求；本轮只支持食品进食，时长由目录推导。"""
        if not request.targets:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        products = self.rules.items
        durations = [products[item].use_duration_seconds for item in request.targets if item in products]
        if len(durations) != len(request.targets):
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        quantity = request.quantity or 1
        items = tuple(ConsumeItem(product_id=item, quantity=quantity) for item in request.targets)
        consume = ConsumeRequest(items=items, use_mode="eat", duration_seconds=max(durations))
        return self._consume(state, pending, consume)

    def _start_message_action(self, state: WorldState, pending: PendingAction, request: StartActivityRequest,
                              target: str | None) -> tuple[WorldState, ActionResult]:
        """@brief 将 message 动作翻译为既有瞬时消息请求。"""
        if not target or not request.text:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        message = SendMessageRequest(recipient_id=target, text=request.text)
        return self._send_message(state, pending, message)

    def _open_session(self, pending: PendingAction, request: StartActivityRequest,
                      result: ActionResult, now: int) -> SessionState:
        """@brief 由启动结果派生稳定 SessionState；任务类型由 targets[0] 对照冻结目录推断。"""
        session_id = hashlib.sha256(f"session:{pending.action_id}:{now}".encode()).hexdigest()
        target = request.targets[0] if request.targets else None
        if target and target in self.rules.tasks:
            task_kind, task_ref = SessionTaskKind.WORK_TASK, target
        else:
            task_kind, task_ref = SessionTaskKind.NONE, None
        note = request.summary or ""
        return SessionState(session_id=session_id, agent_id=pending.agent_id,
                            task_kind=task_kind, task_ref=task_ref,
                            status=SessionStatus.ACTIVE, started_at=now,
                            targets=list(request.targets),
                            activity_ids=[result.activity_id] if result.activity_id else [],
                            note_updated_at=now if note else None,
                            progress_note=note)

    def _recall_task_summary(self, state: WorldState, pending: PendingAction,
                             request: RecallTaskSummaryRequest) -> tuple[WorldState, ActionResult]:
        """@brief 只读回看会话或任务进展；不改变世界状态。"""
        agent = state.agents[pending.agent_id]
        data: dict = {}
        if request.session_id is not None:
            session = state.sessions.get(request.session_id)
            if session is None or session.agent_id != pending.agent_id:
                raise BusinessRuleError("SESSION_NOT_FOUND")
            data = {"session": session.model_dump(mode="json")}
        elif request.task_kind == "work_task" and request.task_ref is not None:
            task = self.rules.tasks.get(request.task_ref)
            if task is None:
                raise BusinessRuleError("WORK_TASK_NOT_FOUND")
            data = {"task": {"task_id": task.task_id, "required_skill": task.required_skill,
                             "due_at": task.due_at.isoformat(),
                             "progress_note": agent.task_progress.get(task.task_id, "")}}
        elif request.task_kind == "goal" and request.task_ref is not None:
            if agent.day_plan is None:
                raise BusinessRuleError("PLAN_NOT_FOUND")
            goal = next((g for g in agent.day_plan.goals if g.kind == request.task_ref), None)
            if goal is None:
                raise BusinessRuleError("PLAN_NOT_FOUND")
            data = {"goal": goal.model_dump(mode="json")}
        else:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Task summary retrieved", data=data)
        return state, result

    def _report_status(self, state: WorldState, pending: PendingAction,
                       request: ReportStatusRequest) -> tuple[WorldState, ActionResult]:
        """@brief 播报进展并更新会话自述；仅更新 progress_note 与 note_history。"""
        if not request.session_ids:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        session_id = request.session_ids[0]
        session = state.sessions.get(session_id)
        if session is None or session.agent_id != pending.agent_id:
            raise BusinessRuleError("SESSION_NOT_FOUND")
        if session.status != SessionStatus.ACTIVE:
            raise BusinessRuleError("SESSION_NOT_ACTIVE")
        history = list(session.note_history)
        if session.progress_note:
            history.append({"note": session.progress_note, "updated_at": session.note_updated_at})
        updated = session.model_copy(update={"progress_note": request.summary,
                                             "note_updated_at": state.current_time,
                                             "note_history": history}, deep=True)
        sessions = {**state.sessions, session_id: updated}
        changed = state.model_copy(update={"sessions": sessions}, deep=True)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Session status reported",
                              data={"session_id": session_id,
                                    "activity_ids": list(session.activity_ids)})
        return changed, result

    def _pause_activity(self, state: WorldState, pending: PendingAction,
                        request: PauseActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 播报进展并暂停会话所有活动；会话状态设为 paused。"""
        if not request.session_ids:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        session_id = request.session_ids[0]
        session = state.sessions.get(session_id)
        if session is None or session.agent_id != pending.agent_id:
            raise BusinessRuleError("SESSION_NOT_FOUND")
        if session.status != SessionStatus.ACTIVE:
            raise BusinessRuleError("SESSION_NOT_ACTIVE")
        changed = state
        paused_ids: list[str] = []
        for activity_id in session.activity_ids:
            activity = changed.activities.get(activity_id)
            if activity is not None and activity.status.value == "active":
                changed, _ = self._change(changed, pending,
                                          ChangeActivityRequest(activity_id=activity_id,
                                                                operation=ActivityChange.PAUSE))
                paused_ids.append(activity_id)
        history = list(session.note_history)
        if session.progress_note:
            history.append({"note": session.progress_note, "updated_at": session.note_updated_at})
        updated = session.model_copy(update={"status": SessionStatus.PAUSED,
                                             "progress_note": request.summary,
                                             "note_updated_at": state.current_time,
                                             "note_history": history}, deep=True)
        sessions = {**changed.sessions, session_id: updated}
        changed = changed.model_copy(update={"sessions": sessions}, deep=True)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Session paused",
                              data={"session_id": session_id, "activity_ids": paused_ids})
        return changed, result

    def _conclude_activity(self, state: WorldState, pending: PendingAction,
                           request: ConcludeActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 播报进展并结束会话；会话状态设为 concluded。"""
        if not request.session_ids:
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        session_id = request.session_ids[0]
        session = state.sessions.get(session_id)
        if session is None or session.agent_id != pending.agent_id:
            raise BusinessRuleError("SESSION_NOT_FOUND")
        if session.status not in {SessionStatus.ACTIVE, SessionStatus.PAUSED}:
            raise BusinessRuleError("SESSION_NOT_ACTIVE")
        history = list(session.note_history)
        if session.progress_note:
            history.append({"note": session.progress_note, "updated_at": session.note_updated_at})
        updated = session.model_copy(update={"status": SessionStatus.CONCLUDED,
                                             "ended_at": state.current_time,
                                             "progress_note": request.summary,
                                             "note_updated_at": state.current_time,
                                             "note_history": history}, deep=True)
        sessions = {**state.sessions, session_id: updated}
        changed = state.model_copy(update={"sessions": sessions}, deep=True)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Session concluded",
                              data={"session_id": session_id,
                                    "activity_ids": list(session.activity_ids)})
        return changed, result
