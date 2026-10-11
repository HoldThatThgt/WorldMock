"""@brief 世界请求的派发接线、活动的生命周期控制与人物事务登记；开始组的核验与派发在 start_group。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import partial

from pydantic import ValidationError

from worldmock.errors import BusinessRuleError
from worldmock.footprints.models import EventDraft
from worldmock.life import activity_control, admission, errands, memory_layer, same_moment, sessions
from worldmock.life.action_contract import START_TOOLS
from worldmock.life.activities import change_activity
from worldmock.life.activity_evidence import conclusion_outcome
from worldmock.life.activity_titles import activity_snapshot, title_index
from worldmock.life.control_items import control_errands, control_items
from worldmock.life.engine_context import EngineContext
from worldmock.life.error_contract import ErrorItem
from worldmock.life.guidance import Refusal
from worldmock.life.models import (
    ActionResult, ActionStatus, ActivityChange, ActivityKind, ActivityState, ActivityStatus,
    ChangeActivityRequest, ConcludeActivityRequest, PauseActivityRequest, PendingAction,
    RecallTaskSummaryRequest, ReportStatusRequest, ReviseDayPlanRequest, REQUEST_MODELS, WorldState,
)
from worldmock.life.name_resolution import resolution_data
from worldmock.life.object_labels import resolve_name
from worldmock.life.request_errors import argument_report
from worldmock.life.schedule import RemindTimeInvalid, day_plan_expired, registered_goals, revise_day_plan
from worldmock.life.start_group import StartGroups, frozen_observation
from worldmock.life.storefront_runtime import StorefrontRuntime
from worldmock.life.world_ops import _put_activity, _put_agent, place_names

_LOGGER = logging.getLogger(__name__)
RECALL_IN_MEMORY_LAYER = "已在记忆层"


class ActionHandlers:
    """@brief 所有领域转换只操作当前决策的世界副本。"""

    def __init__(self, context: EngineContext, storefront: StorefrontRuntime):
        """@brief 绑定状态与购买职责，动作草稿与各动作的资金变化独立暂存。"""
        self.context, self.storefront = context, storefront
        self.pending_event_drafts: list[EventDraft] = []
        #: 各动作受理时的资金变化（出行车费），键为动作身份。
        self.resource_changes: dict[str, dict[str, int]] = {}
        self.starts = StartGroups(context, storefront, self.pending_event_drafts, self.resource_changes)

    def _memory_sources(self, pending: PendingAction, names: list[str], selected: list[ActivityState]) -> list[dict]:
        """@brief 系统登记的记忆来源：逐项的活动、所属会话与本轮观察中该活动的已核验经历（模型不填身份）。"""
        experiences = self._frozen_observation(pending).get("verified_experiences", [])
        return [
            {
                "task": name,
                "activity_id": activity.activity_id,
                "session_id": activity.session_ids[-1] if activity.session_ids else None,
                "verified_event_ids": list(
                    dict.fromkeys(
                        (row["source_event_id"] for row in experiences if row["activity_id"] == activity.activity_id)
                    )
                ),
            }
            for (name, activity) in zip(names, selected, strict=True)
        ]

    def _conclusion_outcome(
        self, state: WorldState, names: list[str], selected: list[ActivityState]
    ) -> list[dict]:
        """@brief 逐项投影真实结算终局；人物总结不改变终局。"""
        ended = [state.activities[activity.activity_id] for activity in selected]
        return [
            conclusion_outcome(activity, name)
            for (name, activity) in zip(names, ended, strict=True)
        ]

    def _conclude_activity(
        self, state: WorldState, pending: PendingAction, request: ConcludeActivityRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 结束所选活动（进行中、暂停中或待收尾）并写下总结；进行中与待收尾的由此释放槽位；途中结束出行时路上的其他
        活动随之结束。

        进行中或暂停的活动先取消（agent_cancelled）再收尾；待收尾活动不改终态，只写总结并置 concluded_at。
        有待收尾活动而本次没有点名任何一项时以 ACTIVITY_CONCLUSION_REQUIRED 拒绝。记忆由宿主写入，环境只校验形状，
        并在结果中登记私有来源 memory_sources。结果的 outcome 逐项写实际终局，
        未完成的浏览写未取得浏览结果（docs/design/06-agent.md）。请求级的事务变更在活动名
        解析后复核、在结束与收尾之后应用，回执登记为 errands；其后的任何拒绝都使事务变更一并不生效。
        """
        items = control_items("conclude_activity", request)
        (written, summaries) = ([item.activity_name for item in items], [item.summary for item in items])
        (requested, selected) = self._named_activities(state, pending, written)
        changes = self._errand_changes(pending, "conclude_activity", request)
        if not all((activity_control.concludable(item) for item in selected)):
            _LOGGER.error("Concluded activity is not active, paused or awaiting conclusion")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        waiting = activity_control.pending_conclusions(state, pending.agent_id)
        if waiting and (not {item.activity_id for item in waiting} & {item.activity_id for item in selected}):
            raise activity_control.conclusion_required(waiting, self._control_names(pending))
        (changed, departed) = (state, False)
        for activity in selected:
            if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                departed = departed or (activity.kind == ActivityKind.TRAVEL and sessions.on_road(state, activity))
                (changed, _) = self._change(
                    changed,
                    pending,
                    ChangeActivityRequest(activity_id=activity.activity_id, operation=ActivityChange.CANCEL),
                )
        activities = dict(changed.activities)
        for activity, summary in zip(selected, summaries, strict=True):
            activities[activity.activity_id] = activity_control.concluded(
                activities[activity.activity_id], summary, state.current_time
            )
        changed = changed.model_copy(update={"activities": activities})
        if departed:
            changed = sessions.end_transit(
                changed, pending.agent_id, place_names(self.context.catalog), opened_by_action_id=pending.action_id
            )
        (changed, receipt) = self._apply_errands(changed, pending, changes)
        result = self._control_result(state, pending, "Activities concluded", selected)
        data = {**result.data, "errands": receipt, "memory_sources": self._memory_sources(pending, requested, selected)}
        outcome = self._conclusion_outcome(changed, requested, selected)
        if outcome:
            data["outcome"] = outcome
        return (changed, _with_names(result.model_copy(update={"data": data}), zip(written, requested)))

    def _pause_activity(
        self, state: WorldState, pending: PendingAction, request: PauseActivityRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 暂停所选进行中活动（让出槽位）并写入自述；路上的活动、交谈与通话不能暂停。

        每项先经 activity_control.control_refusal 判定，与工具层的可用性一字不差。请求级的事务变更在活动名解析后复核、
        在暂停后应用，回执登记为 errands。
        """
        items = control_items("pause_activity", request)
        written = [item.activity_name for item in items]
        (requested, selected) = self._named_activities(state, pending, written)
        changes = self._errand_changes(pending, "pause_activity", request)
        context = self._admission_context(state, pending.agent_id, self._control_names(pending))
        for activity in selected:
            refusal = activity_control.control_refusal(context, "pause_activity", activity)
            if refusal is not None:
                raise refusal.error()
        changed = state
        for activity in selected:
            (changed, _) = self._change(
                changed,
                pending,
                ChangeActivityRequest(activity_id=activity.activity_id, operation=ActivityChange.PAUSE),
            )
        changed = self._with_notes(changed, selected, [item.summary for item in items])
        (changed, receipt) = self._apply_errands(changed, pending, changes)
        result = self._control_result(state, pending, "Activity paused", selected)
        result = result.model_copy(update={"data": {**result.data, "errands": receipt}})
        return (changed, _with_names(result, zip(written, requested)))

    def _resume_at_start_location(
        self, state: WorldState, pending: PendingAction, activity: ActivityState
    ) -> WorldState:
        """@brief 在真实开始地点恢复暂停的活动，读取共享内容进度并归入当前停留会话。"""
        (changed, _) = self._change(
            state,
            pending,
            ChangeActivityRequest(
                activity_id=activity.activity_id,
                operation=ActivityChange.RESUME,
            ),
        )
        if activity.kind == ActivityKind.INTERACT:
            from worldmock.life.interact import InteractivityRuntime

            changed = InteractivityRuntime(self.context).resume(changed, changed.activities[activity.activity_id])
        current = sessions.current_session(changed, pending.agent_id)
        return sessions.assign(changed, current.session_id, [activity.activity_id])

    def _report_status(
        self, state: WorldState, pending: PendingAction, request: ReportStatusRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 逐项播报进行中活动并重置其播报锚点；点名暂停的活动即在其开始地点恢复它。

        每项先经 activity_control.control_refusal 判定（只接受进行中或暂停的活动；恢复只在实际开始地点，
        要有空槽位且与进行中活动的占用不冲突；多项逐项对已恢复前几项的状态判定），与工具层、
        待办层的可用性一字不差。恢复的活动在结果中登记
        resumed：活动名与恢复后的计划时长（秒，公开结果写成 details.planned）。请求级的事务变更在活动名解析后复核、在
        活动转换后应用，回执登记为 errands。
        """
        items = control_items("report_status", request)
        written = [item.activity_name for item in items]
        (requested, selected) = self._named_activities(state, pending, written)
        changes = self._errand_changes(pending, "report_status", request)
        names = self._control_names(pending)
        (changed, resumed) = (state, [])
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
        (changed, receipt) = self._apply_errands(changed, pending, changes)
        result = self._control_result(state, pending, "Activity status reported", selected)
        data = {**result.data, "errands": receipt, **({"resumed": resumed} if resumed else {})}
        return (changed, _with_names(result.model_copy(update={"data": data}), zip(written, requested)))

    def _apply_errands(
        self, state: WorldState, pending: PendingAction, changes: tuple[dict, ...]
    ) -> tuple[WorldState, list[dict]]:
        """@brief 把已复核的事务变更应用到人物状态并放回世界副本（与活动转换同批，处理器在最后一次活动转换之后调用）：
        来源为本动作与它的请求事件，受理时刻与场景时区换算提醒钟点。

        :returns: （世界副本，回执 [{errand, change}]，按请求顺序；没有变更时世界原样、回执为空列表）。
        """
        if not changes:
            return (state, [])
        origin = errands.ErrandOrigin(
            pending.action_id, pending.requested_event_id, state.current_time, self.context.config.timezone
        )
        (agent, receipt) = errands.apply_changes(
            state.agents[pending.agent_id], changes, self._frozen_observation(pending), origin
        )
        return (state.model_copy(update={"agents": {**state.agents, pending.agent_id: agent}}, deep=True), receipt)

    def _errand_changes(self, pending: PendingAction, tool: str, request) -> tuple[dict, ...]:
        """@brief 控制请求里的个人事务变更（经唯一读取入口 control_errands 读出）在本轮冻结观察上的环境复核：与宿主同一
        函数（errands.change_issues）、同一份观察，全部问题合并成一次 ACTION_REQUEST_INVALID（业务拒绝，照常计入同刻
        轮数，docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）。

        :returns: 通过复核的各条变更（八键字典，按请求顺序；没有变更时为空，不读观察），交给 _apply_errands。
        """
        changes = control_errands(tool, request)
        if not changes:
            return changes
        issues = errands.change_issues(changes, self._frozen_observation(pending))
        if issues:
            _LOGGER.error("Errand changes are invalid on the frozen observation")
            raise errands.change_refusal(issues).error()
        return changes

    def _control_result(
        self, state: WorldState, pending: PendingAction, message: str, selected: list[ActivityState]
    ) -> ActionResult:
        """@brief 控制动作只登记实际活动身份供私有逐活动投影；结果不复述请求中的名称。"""
        return ActionResult(
            action_id=pending.action_id,
            agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED,
            occurred_at=state.current_time,
            message=message,
            data={"activity_ids": [item.activity_id for item in selected]},
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
        changed = state.model_copy(update={"activities": activities})
        return changed

    def _named_activities(
        self, state: WorldState, pending: PendingAction, names: list[str]
    ) -> tuple[list[str], list[ActivityState]]:
        """@brief 所写的活动名逐项在本轮可见的活动名中按统一名称解析（精确或无损归一后唯一，如没写开始时刻「（9月28日
        08:25 起）」），再唯一对应人物自己的权威活动。

        名称类失败（找不到、有歧义）逐项收集，合并成一次 ACTION_REQUEST_INVALID，逐项写进 items
        （activity_control.activity_name_refusal，docs/design/04-simulation-engine.md
        ；宿主已在本轮纠正，
        这里只防
        绕过宿主的请求，与开始组名称问题的防御拒绝同一顶层码，照常计入同刻轮数）；名称都解析出来、对应的却不是
        本人的权威活动时（观察与世界对不上）只报这一条不带明细的 ACTION_REQUEST_INVALID。

        :returns: （解析出的活动名，权威活动），按请求顺序。
        """
        rows = self._frozen_observation(pending).get("activity_bindings", [])
        visible = [row["name"] for row in rows]
        refusal = activity_control.activity_name_refusal(names, visible)
        if refusal is not None:
            _LOGGER.error("Activity names are unavailable or ambiguous")
            raise refusal.error()
        (resolved, selected) = ([], [])
        for name in names:
            canonical = resolve_name(name, visible).name
            matches = [row for row in rows if row["name"] == canonical]
            activity = state.activities.get(matches[0]["activity_id"]) if len(matches) == 1 else None
            if activity is None or activity.agent_id != pending.agent_id:
                _LOGGER.error("Resolved activity name is not an activity of this person")
                raise BusinessRuleError("ACTION_REQUEST_INVALID")
            resolved.append(canonical)
            selected.append(activity)
        return (resolved, selected)

    def _activities_for_names(self, state: WorldState, pending: PendingAction, names: list[str]) -> list[ActivityState]:
        """@brief 将一至两个本轮可见的活动名唯一解析为人物自己的权威活动（名称解析同 _named_activities）。"""
        return self._named_activities(state, pending, names)[1]

    def _control_names(self, pending: PendingAction) -> dict[str, str]:
        """@brief 本轮冻结观察中人物所见的活动名（活动身份→名称），用于点名要先处理的活动。"""
        return {
            row["activity_id"]: row["name"] for row in self._frozen_observation(pending).get("activity_bindings", [])
        }

    def _recall_activity(self, state: WorldState, agent_id: int, row: dict, observed: dict) -> dict:
        """@brief 活动回看：名称、地点、实际已做时长、阶段与自述历史。

        地点取活动最近所属会话的说法：路上开始的活动（含出行本身）为「去<目的地>的路上」，停留中的为该地点；
        未归入会话的活动取开始地点。自述仍在记忆层时只写活动名与「已在记忆层」。
        """
        del agent_id
        activity = state.activities[row["activity_id"]]
        remembered = _remembered(observed, activity)
        if remembered is not None:
            return {**remembered, "note_history": list(activity.note_history)}
        places = place_names(self.context.catalog)
        place = (
            sessions.place_label(state.sessions[activity.session_ids[-1]], places)
            if activity.session_ids
            else places[activity.location_id]
        )
        view = {
            "name": row["name"],
            "place": place,
            "phase": activity_control.activity_phase(activity),
            "objective_progress": activity_control.objective_progress(activity),
        }
        return {**view, "note_history": list(activity.note_history), **_self_report(activity.progress_note)}

    def _recall_goal(self, state: WorldState, agent_id: int, row: dict, observed: dict) -> dict:
        """@brief 目标回看：目标文字与人物意图。"""
        del observed
        plan = state.agents[agent_id].day_plan
        goal = (
            None
            if plan is None
            else next((item for item in plan.goals if item.intent_kind == row["intent_kind"]), None)
        )
        if goal is None:
            raise BusinessRuleError("PLAN_NOT_FOUND")
        return {"goal": goal.goal, "intent_kind": goal.intent_kind}

    def read_task_summary(self, agent_id: int, request: RecallTaskSummaryRequest, observed: dict) -> dict:
        """@brief 即时只读回看冻结观察的任务，不登记动作、不推进世界。"""
        name = resolve_name(request.task_name, _recall_names(observed)).name
        candidates = (
            [("goal", row) for row in observed["goal_bindings"] if row["task_name"] == name]
            + [
                ("activity", row)
                for row in observed["activity_bindings"]
                if name == row["name"] or name in row["note_labels"]
            ]
        )
        if len(candidates) != 1:
            _LOGGER.error("Recalled task name is unavailable or ambiguous")
            reason = f"The task name {request.task_name} is unknown or ambiguous in this observation."
            item = ErrorItem(
                ("task_name",),
                request.task_name,
                "TASK_NOT_FOUND",
                expected="One exact known task, goal or activity name is required.",
                why=reason,
            )
            raise Refusal("TASK_NOT_FOUND", reason, items=(item,)).error()
        (kind, row) = candidates[0]
        views = {"goal": self._recall_goal, "activity": self._recall_activity}
        recall = views[kind](self.context.state, agent_id, row, observed)
        return {"recall": {"kind": kind, **recall}}

    def _frozen_observation(self, pending: PendingAction) -> dict:
        """@brief 读取当前决策的已提交观察事件，作为名称与报价的唯一解析来源；缺失或不唯一时 OBSERVATION_NOT_FOUND。"""
        return frozen_observation(self.context, pending)

    def _revise_day_plan(
        self, state: WorldState, pending: PendingAction, request: ReviseDayPlanRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 替换当前完整目标清单及其提醒（第四章「计划与提醒」）。"""
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
            action_id=pending.action_id,
            agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED,
            occurred_at=state.current_time,
            message="Day plan revised",
            data={"day_plan": plan.model_dump(mode="json")},
        )
        return (changed, result)

    def drain_drafts(self) -> list[EventDraft]:
        """@brief 取走动作结算中暂存的真实收货与履约事件草稿。"""
        drained = list(self.pending_event_drafts)
        self.pending_event_drafts.clear()
        return drained

    def _admission_context(self, state: WorldState, agent_id: int, names: dict | None = None) -> admission.Context:
        """@brief 观察与受理共用固定槽位和在场判定。"""
        return admission.Context(state, agent_id, place_names(self.context.catalog), names or {}, self.context.inputs)

    def _change(self, state: WorldState, pending: PendingAction,
                request: ChangeActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 当前边界上明确变更活动，购买取消不付款。"""
        current = state.activities[request.activity_id]
        agent, activity = change_activity(state.agents[pending.agent_id], current, request,
                                          state.current_time, self.context.config.activity_feedback_seconds)
        changed = _put_activity(state, agent, activity)
        if current.kind == ActivityKind.PURCHASE and request.operation == ActivityChange.CANCEL:
            changed = self.storefront.cancel_purchase(changed, activity)
        return changed, ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time, activity_id=activity.activity_id,
            message="Activity changed", data={"activity": activity.model_dump(mode="json")})

    def dispatch(self, state: WorldState, group: Sequence[PendingAction]) -> tuple[WorldState, list[ActionResult]]:
        """@brief 一个决策登记的动作在独立副本中执行，任何拒绝都回滚整组；返回各动作的结果（按登记次序）。

        :param group: 同一决策的已登记动作：一条世界请求，或两个开始工具各一条的开始组。
        """
        tools = [pending.tool_name for pending in group]
        if any(tool not in REQUEST_MODELS or tool == "recall_task_summary" for tool in tools):
            _LOGGER.error("Read-only or unknown tool reached world dispatch")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        changed = state.model_copy(deep=True)
        self.resource_changes.clear()
        if all(tool in START_TOOLS for tool in tools):
            return self.starts.dispatch(changed, group)
        changed, result = self._control(changed, group[0])
        return changed, [result]

    def _control(self, changed: WorldState, pending: PendingAction) -> tuple[WorldState, ActionResult]:
        """@brief 控制请求按请求形状、待收尾、同刻阶段的次序复核后交给各自的处理器。"""
        model = REQUEST_MODELS[pending.tool_name]
        try:
            request = model.model_validate(pending.request)
        except ValidationError as error:
            _LOGGER.error("Tool request validation rejected: %s", pending.tool_name)
            report = argument_report(pending.tool_name, error, model, pending.request, "ACTION_REQUEST_INVALID")
            raise Refusal("ACTION_REQUEST_INVALID", report.reason, fix=report.fix or "", items=report.items).error()
        waiting = activity_control.pending_conclusions(changed, pending.agent_id)
        if waiting and pending.tool_name != "conclude_activity":
            raise activity_control.conclusion_required(waiting, self._control_names(pending))
        named = (self._activities_for_names(changed, pending, [item.activity_name for item in request.items])
                 if pending.tool_name != "revise_day_plan" else [])
        kind = same_moment.request_kind(pending.tool_name, named, frozenset(item.activity_id for item in waiting))
        same_moment.require_stage_action(changed.agents[pending.agent_id].same_moment_stage, kind)
        handlers = {"report_status": self._report_status, "pause_activity": self._pause_activity,
                    "conclude_activity": self._conclude_activity, "revise_day_plan": self._revise_day_plan}
        return handlers[pending.tool_name](changed, pending, request)

    @property
    def record_context(self) -> activity_control.RecordContext:
        """@brief 控制记录读取当前完整目录的实际显示名。"""
        index = title_index(self.context.catalog)
        return activity_control.RecordContext(partial(activity_snapshot, index=index), index)


def _with_names(result: ActionResult, pairs) -> ActionResult:
    """@brief 受理结果写上名称归一 details.resolved：（所写名称，解析出的名称）逐对中两者不同的记一条；都照写时结果不变
    （docs/design/04-simulation-engine.md）。"""
    resolved = [{"written": written, "as": name} for written, name in pairs if written != name]
    return result.model_copy(update={"data": resolution_data(result.data, resolved, ())}) if resolved else result

def _recall_names(observed: dict) -> list[str]:
    """@brief 回看可以写的名称：任务名、待办层写出的目标文字、本轮活动名与记忆层 self_notes 所写的活动名（note_labels）。"""
    return [
        *(row["task_name"] for row in observed["goal_bindings"]),
        *(row["name"] for row in observed["activity_bindings"]),
        *(label for row in observed["activity_bindings"] for label in row["note_labels"]),
    ]

def _self_report(note: str) -> dict:
    """@brief 回看中的人物自述原文（是否经核验由系统层规则统一说明，不逐条标注）；没有自述时为空。"""
    return {"progress_note": note} if note else {}

def _remembered(observed: dict, activity: ActivityState | None) -> dict | None:
    """@brief 活动的最新自述仍在本轮记忆层 self_notes 中时的回看结果：只写它在记忆层中的活动名与「已在记忆层」，
    不写其他回看事实；否则为 None（docs/design/06-agent.md「进度、待办与记忆」）。"""
    if activity is None or not activity.progress_note:
        return None
    names = {row["activity_id"]: row["name"] for row in observed["activity_bindings"]}
    label = memory_layer.noted_label(observed["memory_layer"], names[activity.activity_id], activity.progress_note)
    return None if label is None else {"activity": label, "progress_note": RECALL_IN_MEMORY_LAYER}
