"""@brief 世界请求的派发接线、活动的生命周期控制与人物事务登记；开始组与沙盒调用共用 ToolInvocation。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import partial
from typing import Any

from pydantic import ValidationError

from worldmock.foundation.errors import BusinessRuleError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.simulation_engine.actions import admission
from worldmock.simulation_engine.actions.name_resolution import resolution_data
from worldmock.simulation_engine.actions.start_group import StartGroups, ToolInvocation, frozen_observation
from worldmock.simulation_engine.activities.content.interact import InteractivityRuntime
from worldmock.simulation_engine.activities.content.location_activities import unit_done
from worldmock.simulation_engine.activities.lifecycle import activity_control, sessions
from worldmock.simulation_engine.activities.lifecycle.activities import change_activity
from worldmock.simulation_engine.commerce.purchase.storefront_runtime import StorefrontRuntime
from worldmock.simulation_engine.evidence.activity import conclusion_outcome
from worldmock.simulation_engine.observation.layers import memory_layer
from worldmock.simulation_engine.observation.targets.object_labels import resolve_name
from worldmock.simulation_engine.observation.wording.activity_titles import activity_snapshot, title_index
from worldmock.simulation_engine.observation.wording.guidance import Refusal
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.simulation_engine.world.world_updates import _put_activity, _put_agent, place_names
from worldmock.state.decisions.results import ActionResult
from worldmock.state.interaction.actions import START_TOOLS
from worldmock.state.interaction.control_items import control_errands, control_items
from worldmock.state.interaction.errors import ErrorItem
from worldmock.state.interaction.request_errors import argument_report
from worldmock.state.interaction.requests import (
    ChangeActivityRequest,
    ConcludeActivityRequest,
    PauseActivityRequest,
    RecallTaskSummaryRequest,
    ReportStatusRequest,
    ReviseDayPlanRequest,
    REQUEST_MODELS,
)
from worldmock.state.planning.schedule import RemindTimeInvalid, day_plan_expired, registered_goals, revise_day_plan
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.kinds import ActionStatus, ActivityChange, ActivityKind, ActivityStatus
from worldmock.state.world.world import WorldState

_LOGGER = logging.getLogger(__name__)
RECALL_IN_MEMORY_LAYER = "已在记忆层"


class ActionHandlers:
    """@brief 所有领域转换只操作当前沙盒或重放的世界副本。"""

    def __init__(self, context: EngineContext, storefront: StorefrontRuntime):
        """@brief 绑定状态与购买职责，动作草稿与各动作的资金变化独立暂存。"""
        self.context, self.storefront = context, storefront
        self.pending_event_drafts: list[EventDraft] = []
        #: 各动作受理时的资金变化（出行车费），键为动作身份。
        self.resource_changes: dict[str, dict[str, int]] = {}
        #: 本次受理或消耗动作生成的阈下回调原因映射。
        self.callback_causes: dict[str, Any] = {}
        self.starts = StartGroups(context, storefront, self.pending_event_drafts, self.resource_changes)

    def _memory_sources(self, pending: ToolInvocation, names: list[str],
                        selected: list[ActivityState]) -> list[dict]:
        """@brief 系统登记的记忆来源：逐项的活动、所属会话与本轮观察中该活动的已核验经历。"""
        experiences = frozen_observation(pending).get("verified_experiences", [])
        return [
            {
                "task": name,
                "activity_id": activity.activity_id,
                "session_id": activity.session_ids[-1] if activity.session_ids else None,
                "verified_event_ids": list(dict.fromkeys(
                    row["source_event_id"] for row in experiences if row["activity_id"] == activity.activity_id
                )),
            }
            for name, activity in zip(names, selected, strict=True)
        ]

    def _conclusion_outcome(
        self, state: WorldState, names: list[str], selected: list[ActivityState]
    ) -> list[dict]:
        """@brief 逐项投影真实结算终局；人物总结不改变终局。"""
        ended = [state.activities[activity.activity_id] for activity in selected]
        return [conclusion_outcome(activity, name) for name, activity in zip(names, ended, strict=True)]

    def _conclude_activity(
        self, state: WorldState, pending: ToolInvocation, request: ConcludeActivityRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 结束并收尾所选活动；取消未完成搜索时同步清除悬挂循环。"""
        items = control_items("conclude_activity", request)
        written, summaries = [item.activity_name for item in items], [item.summary for item in items]
        requested, selected = self._named_activities(state, pending, written)
        changes = self._errand_changes(pending, "conclude_activity", request)
        if not all(activity_control.concludable(item) for item in selected):
            _LOGGER.error("Concluded activity is not active, paused or awaiting conclusion")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        waiting = activity_control.pending_conclusions(activity_control.open_activities(state, pending.agent_id))
        if waiting and not ({item.activity_id for item in waiting} & {item.activity_id for item in selected}):
            raise activity_control.conclusion_required(waiting, self._control_names(pending))
        changed, departed = self._cancel_selected(state, pending, selected)
        activities = dict(changed.activities)
        for activity, summary in zip(selected, summaries, strict=True):
            activities[activity.activity_id] = activity_control.concluded(
                activities[activity.activity_id], summary, state.current_time
            )
        changed = changed.model_copy(update={"activities": activities})
        owner = changed.agents[pending.agent_id]
        if any(item.kind == ActivityKind.SEARCH for item in selected) and owner.suspended_loop is not None:
            changed = _put_agent(changed, owner.model_copy(update={"suspended_loop": None}, deep=True))
        if departed:
            changed = sessions.end_transit(
                changed, pending.agent_id, place_names(self.context.catalog), opened_by_action_id=pending.action_id
            )
        changed, receipt = self._apply_errands(changed, pending, changes)
        result = self._control_result(state, pending, "Activities concluded", selected)
        data = {**result.data, "errands": receipt, "memory_sources": self._memory_sources(pending, requested, selected)}
        outcome = self._conclusion_outcome(changed, requested, selected)
        if outcome:
            data["outcome"] = outcome
        return changed, _with_names(result.model_copy(update={"data": data}), zip(written, requested))

    def _cancel_selected(self, state: WorldState, pending: ToolInvocation,
                         selected: list[ActivityState]) -> tuple[WorldState, bool]:
        """@brief 收尾前先将仍在进行中或暂停中的活动置为取消，返回新世界与是否结束路上会话。"""
        changed, departed = state, False
        for activity in selected:
            if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                departed = departed or (activity.kind == ActivityKind.TRAVEL and sessions.on_road(state, activity))
                changed, _ = self._change(
                    changed, pending,
                    ChangeActivityRequest(activity_id=activity.activity_id, operation=ActivityChange.CANCEL),
                )
        return changed, departed

    def _pause_activity(
        self, state: WorldState, pending: ToolInvocation, request: PauseActivityRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 暂停所选进行中交互活动并写入自述，再应用请求级个人事务变更。"""
        items = control_items("pause_activity", request)
        written = [item.activity_name for item in items]
        requested, selected = self._named_activities(state, pending, written)
        changes = self._errand_changes(pending, "pause_activity", request)
        context = self._admission_context(state, pending.agent_id, self._control_names(pending))
        for activity in selected:
            refusal = activity_control.control_refusal(context, "pause_activity", activity)
            if refusal is not None:
                raise refusal.error()
        changed = state
        for activity in selected:
            changed, _ = self._change(
                changed, pending,
                ChangeActivityRequest(activity_id=activity.activity_id, operation=ActivityChange.PAUSE),
            )
        changed = self._with_notes(changed, selected, [item.summary for item in items])
        changed, receipt = self._apply_errands(changed, pending, changes)
        result = self._control_result(state, pending, "Activity paused", selected)
        result = result.model_copy(update={"data": {**result.data, "errands": receipt}})
        return changed, _with_names(result, zip(written, requested))

    def _resume_at_start_location(
        self, state: WorldState, pending: ToolInvocation, activity: ActivityState
    ) -> WorldState:
        """@brief 在真实开始地点恢复暂停的活动，读取共享内容进度并归入当前停留会话。"""
        changed, _ = self._change(
            state, pending,
            ChangeActivityRequest(activity_id=activity.activity_id, operation=ActivityChange.RESUME),
        )
        if activity.kind == ActivityKind.INTERACT:
            changed = InteractivityRuntime(self.context).resume(changed, changed.activities[activity.activity_id])
        current = sessions.current_session(changed, pending.agent_id)
        return sessions.assign(changed, current.session_id, [activity.activity_id])

    def _report_status(
        self, state: WorldState, pending: ToolInvocation, request: ReportStatusRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 逐项播报进行中活动并重置播报锚点，或在开始地点恢复暂停活动。"""
        items = control_items("report_status", request)
        written = [item.activity_name for item in items]
        requested, selected = self._named_activities(state, pending, written)
        changes = self._errand_changes(pending, "report_status", request)
        names = self._control_names(pending)
        changed, resumed = state, []
        for name, activity in zip(requested, selected, strict=True):
            current = changed.activities[activity.activity_id]
            refusal = activity_control.control_refusal(
                self._admission_context(changed, pending.agent_id, names), "report_status", current
            )
            if refusal is not None:
                raise refusal.error()
            if current.status == ActivityStatus.PAUSED:
                changed = self._resume_at_start_location(changed, pending, current)
                planned = changed.activities[activity.activity_id].planned_end_at - changed.current_time
                resumed.append({"name": name, "planned_duration_seconds": planned})
        changed = self._with_notes(changed, selected, [item.summary for item in items], anchor=True)
        changed, receipt = self._apply_errands(changed, pending, changes)
        result = self._control_result(state, pending, "Activity status reported", selected)
        data = {**result.data, "errands": receipt, **({"resumed": resumed} if resumed else {})}
        return changed, _with_names(result.model_copy(update={"data": data}), zip(written, requested))

    def _apply_errands(
        self, state: WorldState, pending: ToolInvocation, changes: tuple[dict, ...]
    ) -> tuple[WorldState, list[dict]]:
        """@brief 把已复核的事务变更应用到人物状态并放回世界副本。"""
        if not changes:
            return state, []
        origin = errands.ErrandOrigin(
            pending.action_id, pending.requested_event_id, state.current_time, self.context.config.timezone
        )
        agent, receipt = errands.apply_changes(
            state.agents[pending.agent_id], changes, frozen_observation(pending), origin
        )
        return state.model_copy(update={"agents": {**state.agents, pending.agent_id: agent}}, deep=True), receipt

    def _errand_changes(self, pending: ToolInvocation, tool: str, request) -> tuple[dict, ...]:
        """@brief 在本轮冻结观察上复核控制请求里的个人事务变更。"""
        changes = control_errands(tool, request)
        if not changes:
            return changes
        issues = errands.change_issues(changes, frozen_observation(pending))
        if issues:
            _LOGGER.error("Errand changes are invalid on the frozen observation")
            raise errands.change_refusal(issues).error()
        return changes

    def _control_result(
        self, state: WorldState, pending: ToolInvocation, message: str, selected: list[ActivityState]
    ) -> ActionResult:
        """@brief 控制动作只登记实际活动身份供私有逐活动投影；结果不复述请求中的名称。"""
        return ActionResult(
            action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time,
            message=message, data={"activity_ids": [item.activity_id for item in selected]},
        )

    def _with_notes(
        self, state: WorldState, selected: list[ActivityState], summaries: list[str], anchor: bool = False
    ) -> WorldState:
        """@brief 把逐项自述写到活动，前一条推入活动历史。"""
        activities = dict(state.activities)
        for activity, summary in zip(selected, summaries, strict=True):
            activities[activity.activity_id] = activity_control.with_note(
                activities[activity.activity_id], summary, state.current_time, anchor
            )
        return state.model_copy(update={"activities": activities})

    def _named_activities(
        self, state: WorldState, pending: ToolInvocation, names: list[str]
    ) -> tuple[list[str], list[ActivityState]]:
        """@brief 在冻结观察中解析所写的活动名，再对应到当前世界中的本人权威活动。"""
        rows = frozen_observation(pending).get("activity_bindings", [])
        visible = [row["name"] for row in rows]
        refusal = activity_control.activity_name_refusal(names, visible)
        if refusal is not None:
            _LOGGER.error("Activity names are unavailable or ambiguous")
            raise refusal.error()
        resolved, selected = [], []
        for name in names:
            canonical = resolve_name(name, visible).name
            matches = [row for row in rows if row["name"] == canonical]
            activity = state.activities.get(matches[0]["activity_id"]) if len(matches) == 1 else None
            if activity is None or activity.agent_id != pending.agent_id:
                _LOGGER.error("Resolved activity name is not an activity of this person")
                raise BusinessRuleError("ACTION_REQUEST_INVALID")
            resolved.append(canonical)
            selected.append(activity)
        return resolved, selected

    def _control_names(self, pending: ToolInvocation) -> dict[str, str]:
        """@brief 冻结观察中人物所见的活动名（活动身份到名称），用于点名要先处理的活动。"""
        return {row["activity_id"]: row["name"] for row in frozen_observation(pending).get("activity_bindings", [])}

    def _recall_activity(self, state: WorldState, agent_id: int, row: dict, observed: dict) -> dict:
        """@brief 活动回看：名称、地点、实际已做时长、阶段与自述历史。"""
        del agent_id
        activity = state.activities[row["activity_id"]]
        remembered = _remembered(observed, activity)
        if remembered is not None:
            return {**remembered, "note_history": list(activity.note_history)}
        places = place_names(self.context.catalog)
        place = (sessions.place_label(state.sessions[activity.session_ids[-1]], places)
                 if activity.session_ids else places[activity.location_id])
        view = {"name": row["name"], "place": place, "phase": activity_control.activity_phase(activity),
                "objective_progress": activity_control.objective_progress(activity)}
        return {**view, "note_history": list(activity.note_history), **_self_report(activity.progress_note)}

    def _recall_goal(self, state: WorldState, agent_id: int, row: dict, observed: dict) -> dict:
        """@brief 目标回看：目标文字与人物意图。"""
        del observed
        plan = state.agents[agent_id].day_plan
        goal = None if plan is None else next(
            (item for item in plan.goals if item.intent_kind == row["intent_kind"]), None)
        if goal is None:
            raise BusinessRuleError("PLAN_NOT_FOUND")
        return {"goal": goal.goal, "intent_kind": goal.intent_kind}

    def read_task_summary(self, agent_id: int, request: RecallTaskSummaryRequest, observed: dict) -> dict:
        """@brief 在已提交世界状态下即时只读回看任务。"""
        return self.read_task_summary_on(self.context.state, agent_id, request, observed)

    def read_task_summary_on(self, state: WorldState, agent_id: int,
                             request: RecallTaskSummaryRequest, observed: dict) -> dict:
        """@brief 在给定世界状态与观察中即时只读回看任务，不登记动作、不推进世界。"""
        name = resolve_name(request.task_name, _recall_names(observed)).name
        candidates = (
            [("goal", row) for row in observed["goal_bindings"] if row["task_name"] == name]
            + [("activity", row) for row in observed["activity_bindings"]
               if name == row["name"] or name in row["note_labels"]]
        )
        if len(candidates) != 1:
            _LOGGER.error("Recalled task name is unavailable or ambiguous")
            reason = f"The task name {request.task_name} is unknown or ambiguous in this observation."
            item = ErrorItem(("task_name",), request.task_name, "TASK_NOT_FOUND",
                             expected="One exact known task, goal or activity name is required.", why=reason)
            raise Refusal("TASK_NOT_FOUND", reason, items=(item,)).error()
        kind, row = candidates[0]
        views = {"goal": self._recall_goal, "activity": self._recall_activity}
        return {"recall": {"kind": kind, **views[kind](state, agent_id, row, observed)}}

    def _revise_day_plan(
        self, state: WorldState, pending: ToolInvocation, request: ReviseDayPlanRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 替换当前完整目标清单及其提醒。"""
        agent = state.agents[pending.agent_id]
        if agent.day_plan is None or day_plan_expired(agent.day_plan, state.current_time):
            raise BusinessRuleError("PLAN_NOT_FOUND")
        try:
            goals = registered_goals(request.goals, state.current_time, self.context.config.timezone)
            plan = revise_day_plan(agent.day_plan, state.current_time, goals)
        except RemindTimeInvalid as error:
            raise BusinessRuleError("PLAN_REMIND_TIME_INVALID") from error
        except ValueError as error:
            _LOGGER.error("Day plan replacement is invalid")
            raise BusinessRuleError("PLAN_GOALS_INVALID") from error
        changed_agent = agent.model_copy(update={"day_plan": plan}, deep=True)
        changed = _put_agent(state, changed_agent)
        result = ActionResult(
            action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time,
            message="Day plan revised", data={"day_plan": plan.model_dump(mode="json")},
        )
        return changed, result

    def drain_drafts(self) -> list[EventDraft]:
        """@brief 取走动作结算中暂存的真实收货与履约事件草稿。"""
        drained = list(self.pending_event_drafts)
        self.pending_event_drafts.clear()
        return drained

    def drain_causes(self) -> dict[str, Any]:
        """@brief 取走动作结算中暂存的阈下回调原因映射。"""
        drained = dict(self.callback_causes)
        self.callback_causes.clear()
        return drained

    def _admission_context(self, state: WorldState, agent_id: int, names: dict | None = None) -> admission.Context:
        """@brief 观察与受理共用固定槽位和在场判定。"""
        return admission.Context(state, agent_id, activity_control.open_activities(state, agent_id),
                                 place_names(self.context.catalog), names or {}, self.context.inputs)

    def _change(self, state: WorldState, pending: ToolInvocation,
                request: ChangeActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 当前边界上明确变更活动：取消购买不付款，取消地点活动记一轮，app 使用不动共享进度。"""
        current = state.activities[request.activity_id]
        agent, activity = change_activity(state.agents[pending.agent_id], current, request,
                                          state.current_time, self.context.config.activity_feedback_seconds)
        located = current.kind == ActivityKind.INTERACT and current.data["app_id"] is None
        if located and request.operation == ActivityChange.CANCEL:
            agent = unit_done(agent, self.context.catalog, current.data["content_id"], state.current_time)
        changed = _put_activity(state, agent, activity)
        if current.kind == ActivityKind.PURCHASE and request.operation == ActivityChange.CANCEL:
            changed = self.storefront.cancel_purchase(changed, activity)
        return changed, ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time, activity_id=activity.activity_id,
            message="Activity changed", data={"activity": activity.model_dump(mode="json")})

    def dispatch(self, state: WorldState, group: Sequence[ToolInvocation]) -> tuple[WorldState, list[ActionResult]]:
        """@brief 在独立世界副本上执行一条或多条同次动作调用，返回新世界与各动作结果。"""
        tools = [pending.tool_name for pending in group]
        if not group or any(tool not in REQUEST_MODELS or tool in {"recall_task_summary", "search_world"}
                            for tool in tools):
            _LOGGER.error("Unsupported or read-only tool reached world dispatch")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        changed = state.model_copy(deep=True)
        self.resource_changes.clear()
        if all(tool in START_TOOLS for tool in tools):
            return self.starts.dispatch(changed, group)
        if len(group) != 1:
            _LOGGER.error("Control tool dispatch accepts exactly one invocation")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        changed, result = self._control(changed, group[0])
        return changed, [result]

    def _control(self, changed: WorldState, pending: ToolInvocation) -> tuple[WorldState, ActionResult]:
        """@brief 控制请求按请求形状、待收尾核验后交给各自处理器。"""
        model = REQUEST_MODELS[pending.tool_name]
        try:
            request = model.model_validate(pending.request)
        except ValidationError as error:
            _LOGGER.error("Tool request validation rejected: %s", pending.tool_name)
            report = argument_report(pending.tool_name, error, model, pending.request, "ACTION_REQUEST_INVALID")
            raise Refusal("ACTION_REQUEST_INVALID", report.reason, fix=report.fix or "", items=report.items).error()
        waiting = activity_control.pending_conclusions(activity_control.open_activities(changed, pending.agent_id))
        if waiting and pending.tool_name != "conclude_activity":
            raise activity_control.conclusion_required(waiting, self._control_names(pending))
        handlers = {"report_status": self._report_status, "pause_activity": self._pause_activity,
                    "conclude_activity": self._conclude_activity, "revise_day_plan": self._revise_day_plan}
        return handlers[pending.tool_name](changed, pending, request)

    @property
    def record_context(self) -> activity_control.RecordContext:
        """@brief 控制记录读取当前完整目录的实际显示名。"""
        index = title_index(self.context.catalog)
        return activity_control.RecordContext(partial(activity_snapshot, index=index), index)


def _with_names(result: ActionResult, pairs) -> ActionResult:
    """@brief 受理结果写上名称归一 details.resolved。"""
    resolved = [{"written": written, "as": name} for written, name in pairs if written != name]
    return result.model_copy(update={"data": resolution_data(result.data, resolved, ())}) if resolved else result


def _recall_names(observed: dict) -> list[str]:
    """@brief 回看可以写的名称：任务名、目标文字、本轮活动名与记忆层标注。"""
    return [
        *(row["task_name"] for row in observed["goal_bindings"]),
        *(row["name"] for row in observed["activity_bindings"]),
        *(label for row in observed["activity_bindings"] for label in row["note_labels"]),
    ]


def _self_report(note: str) -> dict:
    """@brief 回看中的人物自述原文；没有自述时为空。"""
    return {"progress_note": note} if note else {}


def _remembered(observed: dict, activity: ActivityState | None) -> dict | None:
    """@brief 活动最新自述已留在记忆层今天里时的去重回看结果。"""
    if activity is None or not activity.progress_note:
        return None
    label = memory_layer.noted_label(observed["memory_layer"], activity)
    return None if label is None else {"activity": label, "progress_note": RECALL_IN_MEMORY_LAYER}
