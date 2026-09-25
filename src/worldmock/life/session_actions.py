"""@brief 稳定工具面的 5 个会话动作处理器：启动、回看、播报、暂停、结束会话。"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

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
    SendMessageActionRequest,
    SendMessageRequest,
    SessionState,
    SessionStatus,
    SessionTaskKind,
    StartActivityRequest,
    StartSocialActivityRequest,
    SubmitWorkRequest,
    SubmitWorkActionRequest,
    WorldState,
)
from worldmock.life.schedule import INTENT_ACTIVITY_FAMILIES
from worldmock.life.travel import route_for
from worldmock.life.work import work_totals

_LOGGER = logging.getLogger(__name__)


class SessionActionMixin:
    """@brief 稳定工具面的 5 个会话动作：启动、回看、播报、暂停、结束都在此落地。"""

    def _start_activity(self, state: WorldState, pending: PendingAction,
                        request: StartActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 逐项解析冻结对象动作；兼容活动在同一临时状态中原子启动。"""
        if (not 1 <= len(request.targets) <= 2 or len(request.targets) != len(request.actions)
                or len(set(request.targets)) != len(request.targets)):
            _LOGGER.error("Start activity targets are invalid")
            raise BusinessRuleError("SESSION_LIMIT_EXCEEDED")
        bindings = self._resolve_interaction_bindings(pending, request)
        actions = [row["selected_action"]["canonical_action"] for row in bindings]
        labels = {row["selected_action"]["label"] for row in bindings}
        if len(bindings) == 1 or len(labels) == 1 and actions[0] in {"purchase", "consume"}:
            targets = [item["object_id"] for item in bindings]
            changed, result = self._dispatch_start_action(state, pending, actions[0], targets, bindings)
            session = self._open_session(pending, bindings, result, changed)
            sessions = {**changed.sessions, session.session_id: session}
            return changed.model_copy(update={"sessions": sessions}, deep=True), result
        return self._start_parallel_actions(state, pending, bindings)

    def _start_parallel_actions(self, state: WorldState, pending: PendingAction,
                                bindings: list[dict]) -> tuple[WorldState, ActionResult]:
        """@brief 对同一临时世界逐项启动，失败时丢弃整组状态与暂存事件。"""
        changed = state
        results = []
        draft_count = len(self._pending_event_drafts)
        try:
            for binding in bindings:
                action = binding["selected_action"]["canonical_action"]
                changed, result = self._dispatch_start_action(
                    changed, pending, action, [binding["object_id"]], [binding])
                if result.activity_id is None:
                    _LOGGER.error("Parallel action did not start a timed activity")
                    raise BusinessRuleError("ACTION_REQUEST_INVALID")
                session = self._open_session(pending, [binding], result, changed)
                changed = changed.model_copy(update={
                    "sessions": {**changed.sessions, session.session_id: session}}, deep=True)
                results.append(result)
        except Exception:
            _LOGGER.error("Parallel activity start was rejected")
            del self._pending_event_drafts[draft_count:]
            raise
        return changed, ActionResult(
            action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.IN_PROGRESS, occurred_at=state.current_time,
            message="Activities started", data={
                "activities": [result.data["activity"] for result in results],
                "activity_results": [result.data for result in results]})

    def _submit_work_action(self, state: WorldState, pending: PendingAction,
                            request: SubmitWorkActionRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将人物可读任务名称映射为合同身份，交给独立工作验收路径。"""
        task_id = self._task_id_for_name(pending, request.task_name)
        return self._submit_work(state, pending, SubmitWorkRequest(task_id=task_id,
                                                                   deliverable=request.deliverable))

    def _send_message_action(self, state: WorldState, pending: PendingAction,
                              request: SendMessageActionRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将人物可读收件人名称映射为联系人身份并发送正文。"""
        rows = self._frozen_observation(pending)["interaction_bindings"]
        matches = [row for row in rows if row["object"] == request.recipient_name
                   and row["kind"] in {"person", "contact"}]
        if len(matches) != 1:
            _LOGGER.error("Message recipient name is unavailable or ambiguous")
            raise BusinessRuleError("RECIPIENT_NOT_FOUND")
        return self._send_message(state, pending, SendMessageRequest(
            recipient_id=matches[0]["object_id"], text=request.text))

    def _task_id_for_name(self, pending: PendingAction, name: str) -> str:
        """@brief 从当前决策观察唯一解析工作合同的可读名称。"""
        rows = self._frozen_observation(pending)["task_bindings"]
        matches = [row for row in rows if row["task_name"] == name]
        if len(matches) != 1:
            _LOGGER.error("Work task name is unavailable or ambiguous")
            raise BusinessRuleError("WORK_TASK_NOT_FOUND")
        return matches[0]["task_id"]

    def _frozen_observation(self, pending: PendingAction) -> dict:
        """@brief 读取当前决策的已提交观察事件，作为名称与报价的唯一解析来源。"""
        events = self.store.read_agent_events("observation", pending.agent_id)
        matches = [event for event in events if event.decision_id == pending.decision_id
                   and event.occurred_at == pending.observed_at]
        if len(matches) != 1:
            _LOGGER.error("Decision observation is missing or ambiguous")
            raise BusinessRuleError("OBSERVATION_NOT_FOUND")
        return matches[0].data

    def _resolve_interaction_bindings(self, pending: PendingAction,
                                      request: StartActivityRequest) -> list[dict]:
        """@brief 把本轮人物所见名称与动作短语解析成内部对象、方式和冻结报价。"""
        rows = self._frozen_observation(pending).get("interaction_bindings")
        if not isinstance(rows, list):
            _LOGGER.error("Decision interaction bindings are unavailable")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        selected = []
        for name, action in zip(request.targets, request.actions):
            matches = [row for row in rows if row.get("object") == name]
            if len(matches) != 1:
                _LOGGER.error("Interactive object name is missing or ambiguous")
                raise BusinessRuleError("ACTION_REQUEST_INVALID")
            binding = matches[0]
            actions = [row for row in binding["actions"] if row.get("label") == action]
            if len(actions) != 1:
                _LOGGER.error("Interactive action is unavailable for selected object")
                raise BusinessRuleError("ACTION_REQUEST_INVALID")
            selected.append({**binding, "selected_action": actions[0]})
        return selected

    def _dispatch_start_action(self, state: WorldState, pending: PendingAction, action: str,
                               targets: list[str], bindings: list[dict]) -> tuple[WorldState, ActionResult]:
        """@brief 按 action 枚举路由到对应领域启动路径（各分支复用既有领域处理器）。"""
        targeted_kinds = {"work": ActivityKind.WORK, "watch": ActivityKind.WATCH,
                          "play": ActivityKind.PLAY_GAME, "read": ActivityKind.READ_MAIL}
        if action == "travel_to":
            return self._start_travel_action(state, pending, targets[0], bindings[0])
        if action in targeted_kinds:
            return self._start_targeted_action(state, pending, targeted_kinds[action], targets[0])
        if action == "browse":
            return self._start_browse_action(state, pending, targets[0])
        if action == "do":
            return self._start_lifestyle_action(state, pending, targets[0])
        if action in ("wait", "sleep"):
            return self._start_self_action(state, pending, action)
        if action in ("chat", "call"):
            return self._start_social_action(state, pending, action, targets[0])
        if action == "purchase":
            return self._start_purchase_action(state, pending, bindings)
        if action == "consume":
            return self._start_consume_action(state, pending, targets, bindings)
        _LOGGER.error("Start activity action is unsupported: %s", action)
        raise BusinessRuleError("ACTION_NOT_SUPPORTED")

    def _start_travel_action(self, state: WorldState, pending: PendingAction,
                             destination: str, binding: dict) -> tuple[WorldState, ActionResult]:
        """@brief 按冻结路线方式由引擎计算精确秒数并启动出行。"""
        mode = binding["selected_action"]["travel_mode"]
        draft = ActivityStartParams(activity_type=ActivityKind.TRAVEL, planned_duration_seconds=1,
                                    destination_id=destination, travel_mode=mode)
        route = route_for(self.scenario, state.agents[pending.agent_id].location_id, draft)
        duration = self.semantics.travel_planned_seconds(mode, route.duration_seconds, state.current_time)
        if not 1 <= duration <= 86_400:
            _LOGGER.error("Derived route duration is outside domain bounds")
            raise BusinessRuleError("ROUTE_DURATION_INVALID")
        params = draft.model_copy(update={"planned_duration_seconds": duration})
        return self._start(state, pending, params)

    def _start_targeted_action(self, state: WorldState, pending: PendingAction,
                                kind: ActivityKind, target: str) -> tuple[WorldState, ActionResult]:
        """@brief 启动 work/watch/play/read 等指向单一目标的定时活动。"""
        duration = self._targeted_duration(state, pending.agent_id, kind, target)
        params = ActivityStartParams(activity_type=kind, planned_duration_seconds=duration, target_id=target)
        return self._start(state, pending, params)

    def _targeted_duration(self, state: WorldState, agent_id: int,
                            kind: ActivityKind, target: str) -> int:
        """@brief 从合同剩余量、内容进度或场景反馈间隔派生目标活动时长。"""
        if kind == ActivityKind.WORK:
            task = self.rules.task(target)
            worked, _ = work_totals(state, agent_id, target)
            duration = min(task.maximum_duration_seconds - worked,
                           int(task.due_at.timestamp()) - state.current_time, 86_400)
        elif kind == ActivityKind.WATCH:
            item = self.rules.content(target)
            progress = state.agents[agent_id].content_progress.get(target)
            position = progress.position_seconds if progress else 0
            duration = item.duration_seconds - position if position < item.duration_seconds else item.duration_seconds
        elif kind == ActivityKind.PLAY_GAME and target in self.rules.contents:
            duration = self.rules.content(target).duration_seconds
        else:
            duration = self.scenario.activity_feedback_seconds
        if duration < 60 or duration > 86_400:
            _LOGGER.error("Derived activity duration is outside domain bounds")
            raise BusinessRuleError("ACTIVITY_DURATION_INVALID")
        return duration

    def _feedback_duration(self) -> int:
        """@brief 使用场景的结算间隔作为无独立时长合同活动的精确时长。"""
        duration = self.scenario.activity_feedback_seconds
        if duration < 60:
            _LOGGER.error("Activity feedback duration is below the domain minimum")
            raise BusinessRuleError("ACTIVITY_DURATION_INVALID")
        return duration

    def _start_browse_action(self, state: WorldState, pending: PendingAction,
                             target: str) -> tuple[WorldState, ActionResult]:
        """@brief 启动 browse 动作对应的无既定目标浏览活动。"""
        duration = self._feedback_duration()
        params = ActivityStartParams(activity_type=ActivityKind.BROWSE, planned_duration_seconds=duration,
                                     target_id=target)
        return self._start(state, pending, params)

    def _start_lifestyle_action(self, state: WorldState, pending: PendingAction,
                                target: str) -> tuple[WorldState, ActionResult]:
        """@brief 启动 do 动作对应的生活方式目录活动。"""
        item = next((item for item in self.lifestyle_items if item.activity_id == target), None)
        if item is None:
            _LOGGER.error("Lifestyle item was not found in the frozen catalog")
            raise BusinessRuleError("LIFESTYLE_ACTIVITY_NOT_FOUND")
        duration = item.typical_duration_minutes * 60
        if not 60 <= duration <= 86_400:
            _LOGGER.error("Derived lifestyle duration is outside domain bounds")
            raise BusinessRuleError("ACTIVITY_DURATION_INVALID")
        params = ActivityStartParams(activity_type=ActivityKind.LIFESTYLE, planned_duration_seconds=duration,
                                     target_id=target)
        return self._start(state, pending, params)

    def _start_self_action(self, state: WorldState, pending: PendingAction,
                           action: str) -> tuple[WorldState, ActionResult]:
        """@brief 启动 wait/sleep 动作对应的无目标自身活动。"""
        agent = state.agents[pending.agent_id]
        duration = round(agent.body.sleep_need_hours * 3600) if action == "sleep" else self._feedback_duration()
        kind = ActivityKind.WAIT if action == "wait" else ActivityKind.SLEEP
        return self._start(state, pending, ActivityStartParams(activity_type=kind,
                                                                planned_duration_seconds=duration))

    def _start_social_action(self, state: WorldState, pending: PendingAction,
                              action: str, target: str) -> tuple[WorldState, ActionResult]:
        """@brief 启动 chat/call 动作对应的社交活动。"""
        duration = self._feedback_duration()
        if not 600 <= duration <= 7200 and action == "call":
            _LOGGER.error("Derived call duration is outside domain bounds")
            raise BusinessRuleError("CALL_DURATION_OUT_OF_RANGE")
        social = StartSocialActivityRequest(kind=action, partner_id=target,
                                            duration_minutes=duration // 60)
        return self._start_social_activity(state, pending, social)

    def _start_purchase_action(self, state: WorldState, pending: PendingAction,
                                bindings: list[dict]) -> tuple[WorldState, ActionResult]:
        """@brief 使用各供给冻结报价和人物选择的履约方式，系统确定单件数量。"""
        agent = state.agents[pending.agent_id]
        supplies = [self.rules.supplies[item["object_id"]] for item in bindings]
        modes = {item["selected_action"].get("fulfillment") for item in bindings}
        if len(modes) != 1 or modes.pop() not in {"pickup", "delivery"}:
            _LOGGER.error("Purchase fulfillment selection is invalid")
            raise BusinessRuleError("FULFILLMENT_NOT_SUPPORTED")
        fulfillment = bindings[0]["selected_action"]["fulfillment"]
        valid = all(item.location_id == agent.location_id and "pickup" in item.fulfillment_modes
                    for item in supplies) if fulfillment == "pickup" else all(
                        "delivery" in item.fulfillment_modes
                        and agent.location_id in item.delivery_destination_ids for item in supplies)
        if not valid:
            _LOGGER.error("Selected supplies cannot use the chosen fulfillment")
            raise BusinessRuleError("FULFILLMENT_NOT_SUPPORTED")
        items = []
        for binding in bindings:
            quote = binding.get("quote")
            if not isinstance(quote, dict) or not quote.get("price_version"):
                _LOGGER.error("Frozen supply quote is unavailable")
                raise BusinessRuleError("SUPPLY_QUOTE_UNKNOWN")
            items.append(PurchaseItem(supply_id=binding["object_id"], quantity=1,
                                      price_version=quote["price_version"]))
        purchase = PurchaseOrderRequest(items=tuple(items), fulfillment=fulfillment,
                                        destination_id=agent.location_id)
        return self._purchase(state, pending, purchase)

    def _start_consume_action(self, state: WorldState, pending: PendingAction,
                               targets: list[str], bindings: list[dict]) -> tuple[WorldState, ActionResult]:
        """@brief 从冻结商品目录推导食品进食或单件耐用品使用的数量和时长。"""
        products = self.rules.items
        durations = [products[item].use_duration_seconds for item in targets if item in products]
        if len(durations) != len(targets):
            _LOGGER.error("Selected food product was not found")
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        if not 60 <= max(durations) <= 14_400:
            _LOGGER.error("Derived food consumption duration is outside domain bounds")
            raise BusinessRuleError("ACTIVITY_DURATION_INVALID")
        mode = bindings[0]["selected_action"]["use_mode"]
        if any(mode not in products[item].use_modes for item in targets) or mode == "use" and len(targets) != 1:
            _LOGGER.error("Selected objects cannot share a consumption mode")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        items = tuple(ConsumeItem(product_id=item, quantity=1) for item in targets)
        consume = ConsumeRequest(items=items, use_mode=mode, duration_seconds=max(durations))
        return self._consume(state, pending, consume)

    def _open_session(self, pending: PendingAction, bindings: list[dict],
                       result: ActionResult, state: WorldState) -> SessionState:
        """@brief 由启动结果派生稳定 SessionState；任务类型由 targets[0] 对照冻结目录推断。"""
        now = state.current_time
        session_id = hashlib.sha256(
            f"session:{pending.action_id}:{now}:{bindings[0]['object_id']}".encode()).hexdigest()
        targets = [binding["object_id"] for binding in bindings]
        target = targets[0]
        goal_kind = self._plan_goal_for_activity(state, pending.agent_id, result.activity_id)
        if target and target in self.rules.tasks:
            task_kind, task_ref = SessionTaskKind.WORK_TASK, target
        elif goal_kind is not None:
            task_kind, task_ref = SessionTaskKind.GOAL, goal_kind
        else:
            task_kind, task_ref = SessionTaskKind.NONE, None
        started = datetime.fromtimestamp(now, ZoneInfo(self.scenario.timezone)).strftime("%Y-%m-%d %H:%M:%S")
        label = bindings[0]["selected_action"]["label"]
        names = "、".join(binding["object"] for binding in bindings)
        session_name = f"{label} {names}（{started}）"
        if task_kind == SessionTaskKind.WORK_TASK:
            matches = [row for row in self._frozen_observation(pending)["task_bindings"]
                       if row["task_id"] == target]
            if len(matches) != 1:
                _LOGGER.error("Work session task binding is unavailable or ambiguous")
                raise BusinessRuleError("WORK_TASK_NOT_FOUND")
            task_name = matches[0]["task_name"]
        elif goal_kind is not None:
            plan = state.agents[pending.agent_id].day_plan
            goal = next(item for item in plan.goals if item.intent_kind == goal_kind)
            task_name = f"目标：{goal_kind}：{goal.goal}"
        else:
            task_name = f"{label} {names}"
        return SessionState(session_id=session_id, task_name=task_name, session_name=session_name,
                            plan_goal_intent_kind=goal_kind,
                            agent_id=pending.agent_id,
                            task_kind=task_kind, task_ref=task_ref,
                            status=SessionStatus.ACTIVE if result.activity_id else SessionStatus.CONCLUDED,
                            started_at=now, ended_at=None if result.activity_id else now,
                            targets=list(targets),
                            activity_ids=[result.activity_id] if result.activity_id else [])

    def _plan_goal_for_activity(self, state: WorldState, agent_id: int,
                                activity_id: str | None) -> str | None:
        """@brief 按既有活动家族规则将新会话绑定到当日首个匹配目标。"""
        plan = state.agents[agent_id].day_plan
        if plan is None or activity_id is None or state.current_time >= plan.day_end:
            return None
        activity = state.activities[activity_id]
        family = activity.kind.value
        if activity.kind == ActivityKind.LIFESTYLE:
            target = (activity.target_id or "").removeprefix(LIFESTYLE_TYPE_PREFIX)
            item = next((entry for entry in self.lifestyle_items if entry.activity_id == target), None)
            if item is None:
                _LOGGER.error("Lifestyle goal category is unavailable")
                raise BusinessRuleError("LIFESTYLE_ACTIVITY_NOT_FOUND")
            family = f"lifestyle:{item.category}"
        return next((goal.intent_kind for goal in plan.goals if goal.progressed_at is None
                     and goal.registered_at <= state.current_time
                     and family in INTENT_ACTIVITY_FAMILIES[goal.intent_kind]), None)

    def _recall_task_summary(self, state: WorldState, pending: PendingAction,
                              request: RecallTaskSummaryRequest) -> tuple[WorldState, ActionResult]:
        """@brief 只读回看会话或任务进展；不改变世界状态。"""
        agent = state.agents[pending.agent_id]
        data: dict = {}
        observed = self._frozen_observation(pending)
        sessions = [row for row in observed["session_bindings"] if row["session_name"] == request.task_name]
        tasks = [row for row in observed["task_bindings"] if row["task_name"] == request.task_name]
        goals = [row for row in observed["goal_bindings"] if row["task_name"] == request.task_name]
        if len(sessions) + len(tasks) + len(goals) != 1:
            _LOGGER.error("Recalled task name is unavailable or ambiguous")
            raise BusinessRuleError("TASK_NOT_FOUND")
        if sessions:
            from worldmock.life.projection import _session_fact_row

            session = state.sessions[sessions[0]["session_id"]]
            data = {"session": _session_fact_row(session)}
        elif tasks:
            task = self.rules.task(tasks[0]["task_id"])
            notes = [session for session in state.sessions.values()
                     if session.agent_id == pending.agent_id and session.task_kind == SessionTaskKind.WORK_TASK
                     and session.task_ref == task.task_id and session.progress_note]
            latest = max(notes, key=lambda session: (session.note_updated_at or session.started_at,
                                                     session.started_at), default=None)
            data = {"task": {"task_name": request.task_name, "required_skill": task.required_skill,
                              "due_at": task.due_at.isoformat(),
                              "progress_note": latest.progress_note if latest else ""}}
        else:
            if agent.day_plan is None:
                raise BusinessRuleError("PLAN_NOT_FOUND")
            goal = next((g for g in agent.day_plan.goals if g.intent_kind == goals[0]["intent_kind"]), None)
            if goal is None:
                raise BusinessRuleError("PLAN_NOT_FOUND")
            data = {"goal": {"task_name": goal.goal, "intent_kind": goal.intent_kind,
                             "progressed": goal.progressed_at is not None}}
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Task summary retrieved", data=data)
        return state, result

    def _sessions_for_names(self, state: WorldState, pending: PendingAction,
                            names: list[str]) -> list[SessionState]:
        """@brief 将一至两个可见会话名称绑定到人物自己的权威会话。"""
        if not 1 <= len(names) <= 2 or len(set(names)) != len(names):
            _LOGGER.error("Session control requires one or two distinct session names")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        rows = [row for row in self._frozen_observation(pending)["session_bindings"]
                if row["status"] in {SessionStatus.ACTIVE, SessionStatus.PAUSED}]
        sessions = []
        for name in names:
            matches = [row for row in rows if row["session_name"] == name or row["task_name"] == name]
            if len(matches) != 1:
                _LOGGER.error("Session name is unavailable or ambiguous")
                raise BusinessRuleError("SESSION_NOT_FOUND")
            session = state.sessions.get(matches[0]["session_id"])
            if session is None or session.agent_id != pending.agent_id:
                _LOGGER.error("Session identity is unavailable for agent")
                raise BusinessRuleError("SESSION_NOT_FOUND")
            sessions.append(session)
        return sessions

    def _session_with_summary(self, session: SessionState, summary: str, now: int,
                              status: SessionStatus | None = None) -> SessionState:
        """@brief 保留前次自述并写入新的主观进展；可同时变更会话阶段。"""
        history = list(session.note_history)
        if session.progress_note:
            history.append({"note": session.progress_note, "updated_at": session.note_updated_at})
        update = {"progress_note": summary, "note_updated_at": now, "note_history": history}
        if status is not None:
            update["status"] = status
        if status == SessionStatus.CONCLUDED:
            update["ended_at"] = now
        return session.model_copy(update=update, deep=True)

    def _with_goal_summaries(self, state: WorldState,
                             pairs: list[tuple[SessionState, str]]) -> WorldState:
        """@brief 将同一模型原文写入相关当日目标，保留客观推进证据。"""
        agent_id = pairs[0][0].agent_id
        agent = state.agents[agent_id]
        plan = agent.day_plan
        if plan is None:
            return state
        notes = {session.plan_goal_intent_kind: summary for session, summary in pairs
                 if session.plan_goal_intent_kind is not None}
        if not notes:
            return state
        goals = tuple(goal.model_copy(update={"progress_note": notes[goal.intent_kind]})
                      if goal.intent_kind in notes else goal for goal in plan.goals)
        changed_agent = agent.model_copy(update={"day_plan": plan.model_copy(update={"goals": goals})}, deep=True)
        agents = dict(state.agents)
        agents[agent_id] = changed_agent
        return state.model_copy(update={"agents": agents}, deep=True)

    def _report_status(self, state: WorldState, pending: PendingAction,
                        request: ReportStatusRequest) -> tuple[WorldState, ActionResult]:
        """@brief 逐项播报进展，暂停会话及活动在此恢复继续。"""
        selected = self._sessions_for_names(state, pending, request.task)
        if any(session.status not in {SessionStatus.ACTIVE, SessionStatus.PAUSED} for session in selected):
            _LOGGER.error("Reported session is not active or paused")
            raise BusinessRuleError("SESSION_NOT_ACTIVE")
        changed = state
        for session in selected:
            if session.status != SessionStatus.PAUSED:
                continue
            for activity_id in session.activity_ids:
                activity = changed.activities.get(activity_id)
                if activity is not None and activity.status.value == "paused":
                    changed, _ = self._change(changed, pending,
                                              ChangeActivityRequest(activity_id=activity_id,
                                                                    operation=ActivityChange.RESUME))
        sessions = dict(changed.sessions)
        for session, summary in zip(selected, request.summary, strict=True):
            sessions[session.session_id] = self._session_with_summary(
                session, summary, state.current_time, SessionStatus.ACTIVE)
        changed = changed.model_copy(update={"sessions": sessions}, deep=True)
        changed = self._with_goal_summaries(changed, list(zip(selected, request.summary, strict=True)))
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Session status reported",
                              data={"task": list(request.task),
                                    "activity_ids": [list(item.activity_ids) for item in selected]})
        return changed, result

    def _pause_activity(self, state: WorldState, pending: PendingAction,
                         request: PauseActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 播报进展并暂停会话所有活动；会话状态设为 paused。"""
        selected = self._sessions_for_names(state, pending, request.task)
        if any(session.status != SessionStatus.ACTIVE for session in selected):
            _LOGGER.error("Paused session is not active")
            raise BusinessRuleError("SESSION_NOT_ACTIVE")
        changed = state
        paused = 0
        for session in selected:
            for activity_id in session.activity_ids:
                activity = changed.activities.get(activity_id)
                if activity is not None and activity.status.value == "active":
                    changed, _ = self._change(changed, pending,
                                              ChangeActivityRequest(activity_id=activity_id,
                                                                    operation=ActivityChange.PAUSE))
                    paused += 1
        sessions = dict(changed.sessions)
        for session, summary in zip(selected, request.summary, strict=True):
            sessions[session.session_id] = self._session_with_summary(
                session, summary, state.current_time, SessionStatus.PAUSED)
        changed = changed.model_copy(update={"sessions": sessions}, deep=True)
        changed = self._with_goal_summaries(changed, list(zip(selected, request.summary, strict=True)))
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Session paused",
                              data={"task": list(request.task),
                                    "activity_ids": [list(item.activity_ids) for item in selected],
                                    "paused_activities": paused})
        return changed, result

    def _conclude_activity(self, state: WorldState, pending: PendingAction,
                            request: ConcludeActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 逐项播报进展并结束所选进行中会话及其活动。"""
        selected = self._sessions_for_names(state, pending, request.task)
        if any(session.status != SessionStatus.ACTIVE for session in selected):
            _LOGGER.error("Concluded session is not active")
            raise BusinessRuleError("SESSION_NOT_ACTIVE")
        changed = state
        cancelled = 0
        for session in selected:
            for activity_id in session.activity_ids:
                activity = changed.activities.get(activity_id)
                if activity is not None and activity.status.value in {"active", "paused"}:
                    changed, _ = self._change(changed, pending,
                                              ChangeActivityRequest(activity_id=activity_id,
                                                                    operation=ActivityChange.CANCEL))
                    cancelled += 1
        sessions = dict(changed.sessions)
        for session, summary in zip(selected, request.summary, strict=True):
            sessions[session.session_id] = self._session_with_summary(
                session, summary, state.current_time, SessionStatus.CONCLUDED)
        changed = changed.model_copy(update={"sessions": sessions}, deep=True)
        changed = self._with_goal_summaries(changed, list(zip(selected, request.summary, strict=True)))
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Session concluded",
                              data={"task": list(request.task),
                                    "activity_ids": [list(item.activity_ids) for item in selected],
                                    "cancelled_activities": cancelled})
        return changed, result
