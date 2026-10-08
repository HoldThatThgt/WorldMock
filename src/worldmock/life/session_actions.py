"""@brief ActionHandlers 的独立生活运行职责。"""

from __future__ import annotations
from worldmock.life import social as social_domain
from worldmock.life.world_ops import _activity_origin
from worldmock.life.world_ops import _put_activity
from worldmock.life.world_ops import _put_agent

import logging
from dataclasses import replace
from pydantic import ValidationError
from worldmock.errors import BusinessRuleError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life import activity_control, admission, contact_exchange, errands, same_moment, sessions, social_actions
from worldmock.life.menu_execution import require_interaction_menu
from worldmock.life.guidance import Refusal, guided
from worldmock.life.request_errors import argument_report
from worldmock.life.activity_titles import activity_title
from worldmock.life.control_items import control_items
from worldmock.life.activities import change_activity, resource_claims, schedule_feedback, start_activity
from worldmock.life.action_run import (
    ActionStart, action_content_result, initialize_action, prepare_action, resume_action,
)
from worldmock.life.alarm import set_alarm
from worldmock.life.fuel import require_driving_days
from worldmock.life.parcel import collect_parcels
from worldmock.life.body import apply_mood_pulse
from worldmock.life.models import (
    ActionResult,
    ActionStatus,
    ActivityChange,
    ActivityKind,
    ActivityStartParams,
    ActivityStatus,
    ChangeActivityRequest,
    PendingAction,
    REQUEST_MODELS,
    ReviseDayPlanRequest,
    SameMomentStage,
    SendMessageRequest,
    WorldState,
    active_activities,
)
from worldmock.life.schedule import RemindTimeInvalid, day_plan_expired, registered_goals, revise_day_plan
from worldmock.life.storefront_runtime import StorefrontRuntime
from worldmock.life.travel import pay_travel_fare, planned_travel, require_travel_duration

import logging
from dataclasses import replace
from functools import partial
from worldmock.errors import BusinessRuleError
from worldmock.life import (
    activity_control,
    admission,
    errands,
    memory_layer,
    order_routing,
    request_linking,
    routing,
    sessions,
)
from worldmock.life.activity_evidence import conclusion_outcome
from worldmock.life.durations import capped_duration_seconds, derived_activity_duration
from worldmock.life.control_items import control_errands, control_items
from worldmock.life.guidance import Refusal, guided, refusal_for_code
from worldmock.life.error_contract import ErrorItem
from worldmock.life.interaction_menu import (
    NAMED_SUPPLY_KIND,
    activity_phrase,
    merged_selection,
    named_supply_row,
    resolve_interaction_menu,
)
from worldmock.life.models import (
    ActionResult,
    ActionStatus,
    ActivityChange,
    ActivityKind,
    ActivityStartParams,
    ActivityState,
    ActivityStatus,
    ChangeActivityRequest,
    ConcludeActivityRequest,
    DERIVED_DURATION_CAP_SECONDS,
    PauseActivityRequest,
    PendingAction,
    RecallTaskSummaryRequest,
    ReportStatusRequest,
    SendMessageActionRequest,
    SendMessageRequest,
    SessionTaskKind,
    StartActivityRequest,
    WorldState,
)
from worldmock.life.name_resolution import StartOutcome, name_book, resolution_data, resolve_start
from worldmock.life.object_labels import resolve_name
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_rows, resolve_named_supply
from worldmock.life.readable import iso_time
from worldmock.life.recipients import person_name, recipient_location
from worldmock.life.schedule import INTENT_ACTIVITY_FAMILIES, activity_family
from worldmock.life.tool_layer import Candidate as ToolCandidate, LayerContext, refusal_for

_LOGGER = logging.getLogger(__name__)
RECALL_IN_MEMORY_LAYER = "已在记忆层"
_ENVIRONMENT_ROUTE_CODES = frozenset({routing.RECIPIENT_NOT_FOUND})
from functools import partial
from worldmock.life.engine_context import EngineContext
from worldmock.life.activity_titles import activity_title, activity_snapshot
from worldmock.life.world_ops import agent_config, place_names


class ActionHandlers:
    """@brief 显式依赖上下文的ActionHandlers。"""

    def __init__(self, context: EngineContext, storefront: StorefrontRuntime):
        """@brief 绑定状态与购买职责，动作草稿独立暂存。"""
        self.context, self.storefront = context, storefront
        self.pending_event_drafts: list[EventDraft] = []

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
        """@brief 结束所选活动（进行中、暂停中或待收尾）并写下总结；进行中与待收尾的由此释放名额；途中结束出行时路上的其他
        活动随之结束。

        进行中或暂停的活动先取消（agent_cancelled）再收尾；待收尾活动不改终态，只写总结并置 concluded_at。
        有待收尾活动而本次没有点名任何一项时以 ACTIVITY_CONCLUSION_REQUIRED 拒绝。记忆由宿主写入，环境只校验形状，
        并在结果中登记私有来源 memory_sources。结果的 outcome 逐项写实际终局，
        未完成的浏览写未取得浏览结果（docs/design/06-agent.md「进食」、第六章）。请求级的事务变更在活动名
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
        changed = self._with_goal_summaries(
            changed.model_copy(update={"activities": activities}), list(zip(selected, summaries, strict=True))
        )
        if departed:
            (changed, _) = sessions.end_transit(
                changed, pending.agent_id, place_names(self.context.scenario), opened_by_action_id=pending.action_id
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
        """@brief 暂停所选进行中活动（让出名额）并写入自述；路上的活动、交谈与通话不能暂停。

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
        """@brief 在开始地点恢复暂停的活动（地点与占用已由 control_refusal 判定），并归入那里的当前停留会话。

        工作恢复不越过合同截止（上限见 _resume_cap）。
        """
        (changed, _) = self._change(
            state,
            pending,
            ChangeActivityRequest(
                activity_id=activity.activity_id,
                operation=ActivityChange.RESUME,
            ),
        )
        current = sessions.current_session(changed, pending.agent_id)
        return sessions.assign(changed, current.session_id, [activity.activity_id])

    def _report_status(
        self, state: WorldState, pending: PendingAction, request: ReportStatusRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 逐项播报进行中活动并重置其播报锚点；点名暂停的活动即在其开始地点恢复它。

        每项先经 activity_control.control_refusal 判定（只接受进行中或暂停的活动；恢复只在开始地点，工作的恢复距截止与
        剩余合同都不少于 60 秒，要有空名额，且与进行中活动的占用不冲突；多项逐项对已恢复前几项的状态判定），与工具层、
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
            pending.action_id, pending.requested_event_id, state.current_time, self.context.scenario.timezone
        )
        (agent, receipt) = errands.apply_changes(
            state.agents[pending.agent_id], changes, self._frozen_observation(pending), origin
        )
        return (state.model_copy(update={"agents": {**state.agents, pending.agent_id: agent}}, deep=True), receipt)

    def _errand_changes(self, pending: PendingAction, tool: str, request) -> tuple[dict, ...]:
        """@brief 控制请求里的个人事务变更（经唯一读取入口 control_errands 读出）在本轮冻结观察上的环境复核：与宿主同一
        函数（errands.change_issues）、同一份观察，全部问题合并成一次 ACTION_REQUEST_INVALID（业务拒绝，照常计入同刻
        轮数，docs/design/04-simulation-engine.md 「环境复核」）。

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

    def _with_goal_summaries(self, state: WorldState, pairs: list[tuple[ActivityState, str]]) -> WorldState:
        """@brief 将同一模型原文写入活动关联的当日目标，保留客观推进证据。"""
        agent = state.agents[pairs[0][0].agent_id]
        plan = agent.day_plan
        notes = {
            activity.plan_goal_intent_kind: summary
            for (activity, summary) in pairs
            if activity.plan_goal_intent_kind is not None
        }
        if plan is None or not notes:
            return state
        goals = tuple(
            (
                goal.model_copy(update={"progress_note": notes[goal.intent_kind]})
                if goal.intent_kind in notes
                else goal
                for goal in plan.goals
            )
        )
        changed_agent = agent.model_copy(update={"day_plan": plan.model_copy(update={"goals": goals})}, deep=True)
        return state.model_copy(update={"agents": {**state.agents, agent.agent_id: changed_agent}}, deep=True)

    def _with_notes(
        self, state: WorldState, selected: list[ActivityState], summaries: list[str], anchor: bool = False
    ) -> WorldState:
        """@brief 把逐项自述写到活动（前一条推入历史），并同步到关联的当日目标。"""
        activities = dict(state.activities)
        for activity, summary in zip(selected, summaries, strict=True):
            activities[activity.activity_id] = activity_control.with_note(
                activities[activity.activity_id], summary, state.current_time, anchor
            )
        changed = state.model_copy(update={"activities": activities})
        return self._with_goal_summaries(changed, list(zip(selected, summaries, strict=True)))

    def _named_activities(
        self, state: WorldState, pending: PendingAction, names: list[str]
    ) -> tuple[list[str], list[ActivityState]]:
        """@brief 所写的活动名逐项在本轮可见的活动名中按统一名称解析（精确或无损归一后唯一，如没写开始时刻「（9月28日
        08:25 起）」），再唯一对应人物自己的权威活动。

        名称类失败（找不到、有歧义）逐项收集，合并成一次 ACTION_REQUEST_INVALID，逐项写进 items
        （activity_control.activity_name_refusal，docs/design/04-simulation-engine.md  第
        5 条；宿主已在本轮纠正，
        这里只防
        绕过宿主的请求，与 start_activity 的 _unresolved_start 同一顶层码，照常计入同刻轮数）；名称都解析出来、对应的却不是
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
        places = place_names(self.context.scenario)
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
        """@brief 目标回看：目标文字、意图与是否已推进。"""
        del observed
        plan = state.agents[agent_id].day_plan
        goal = (
            None
            if plan is None
            else next((item for item in plan.goals if item.intent_kind == row["intent_kind"]), None)
        )
        if goal is None:
            raise BusinessRuleError("PLAN_NOT_FOUND")
        return {"goal": goal.goal, "intent_kind": goal.intent_kind, "progressed": goal.progressed_at is not None}


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

    def _plan_goal_for_activity(self, state: WorldState, agent_id: int, activity_id: str | None) -> str | None:
        """@brief 按既有活动家族规则将新活动绑定到当日首个匹配目标。"""
        plan = state.agents[agent_id].day_plan
        if plan is None or activity_id is None or state.current_time >= plan.day_end:
            return None
        activity = state.activities[activity_id]
        snapshot = {"kind": activity.kind.value}
        snapshot["data"] = activity.data
        family = activity_family(snapshot)
        return next(
            (
                goal.intent_kind
                for goal in plan.goals
                if goal.progressed_at is None
                and goal.registered_at <= state.current_time
                and (family in INTENT_ACTIVITY_FAMILIES[goal.intent_kind])
            ),
            None,
        )



    def _start_purchase_action(self, state: WorldState, pending: PendingAction,
                               bindings: list[dict]) -> tuple[WorldState, ActionResult]:
        """@brief 实际食品绑定先全量校验，再在独立状态上结算全部请求。"""
        request_linking.require_unhandled(state, self.context.scenario, bindings)
        requests = order_routing.order_requests(state, self.context.semantics, pending.agent_id, bindings)
        changed, results = state, []
        for purchase in requests:
            changed, result = self.storefront._purchase(changed, pending, purchase)
            results.append(result)
        answering = next((item["answering"] for item in bindings if item.get("answering")), None)
        return changed, order_routing.merged_result(results, answering)


    def _start_self_action(
        self, state: WorldState, pending: PendingAction, action: str
    ) -> tuple[WorldState, ActionResult]:
        """@brief 启动 wait/sleep 动作对应的无目标自身活动。"""
        agent = state.agents[pending.agent_id]
        kind = ActivityKind.WAIT if action == "wait" else ActivityKind.SLEEP
        duration = derived_activity_duration(kind, agent, None, self.context.scenario.activity_feedback_seconds)
        return self._start(state, pending, ActivityStartParams(activity_type=kind, planned_duration_seconds=duration))



    def _feedback_duration(self) -> int:
        """@brief 使用共享规则计算场景反馈间隔对应的合法活动时长。"""
        return capped_duration_seconds(self.context.scenario.activity_feedback_seconds)



    def _start_travel_action(
        self, state: WorldState, pending: PendingAction, destination: str, binding: dict
    ) -> tuple[WorldState, ActionResult]:
        """@brief 按冻结路线方式由引擎计算精确秒数并启动出行。"""
        mode = binding["selected_action"]["travel_mode"]
        draft = ActivityStartParams(
            activity_type=ActivityKind.TRAVEL, planned_duration_seconds=1, destination_id=destination, travel_mode=mode
        )
        _, duration = planned_travel(
            self.context.scenario, self.context.semantics, state.agents[pending.agent_id].location_id,
            draft, state.current_time
        )
        if not 1 <= duration <= DERIVED_DURATION_CAP_SECONDS:
            _LOGGER.error("Derived route duration is outside domain bounds")
            raise BusinessRuleError("ROUTE_DURATION_INVALID")
        if mode == "driving":
            require_driving_days(state, self.context.semantics, pending.agent_id, state.current_time + duration)
        params = draft.model_copy(update={"planned_duration_seconds": duration,
            "navigation_app_id": binding["selected_action"].get("app_id")})
        return self._start(state, pending, params)

    def _dispatch_start_action(self, state: WorldState, pending: PendingAction, action: str,
                               bindings: list[dict]) -> tuple[WorldState, ActionResult]:
        """@brief 系统动作和已登记动作走各自唯一执行入口。"""
        target = bindings[0]["object_id"]
        if action == "travel_to":
            return self._start_travel_action(state, pending, target, bindings[0])
        if action == "do":
            return self._start_catalog_action(state, pending, bindings[0])
        if action in {"wait", "sleep"}:
            return self._start_self_action(state, pending, action)
        if action == "purchase":
            return self._start_purchase_action(state, pending, bindings)
        if action == "collect_parcel":
            return self._collect_parcel(state, pending, target)
        _LOGGER.error("Start activity action is unsupported: %s", action)
        raise BusinessRuleError("ACTION_NOT_SUPPORTED")

    def _start_catalog_action(self, state: WorldState, pending: PendingAction,
                              binding: dict) -> tuple[WorldState, ActionResult]:
        """@brief 只读选择一次内容，准入后初始化活动及社交事实。"""
        catalog, scenario = self.context.semantics.catalog, self.context.scenario
        origin = _activity_origin(state, pending)
        suffix = f":{origin.activity_ordinal:04d}" if origin.activity_ordinal > 1 else ""
        identity = f"activity-{origin.action_id}{suffix}"
        request = ActionStart(pending.agent_id, identity, binding["object_id"],
                              binding["selected_action"].get("app_id"), binding.get("counterpart"))
        prepared = prepare_action(state, catalog, scenario, request)
        claims = self.context.semantics.affordances.resource_claims(
            "action", category=prepared.data["category"], manner=prepared.data["manner"])
        self._require_available_resources(state, pending.agent_id, set(claims))
        changed = initialize_action(state, prepared, origin, catalog)
        activity = changed.activities[identity]
        if prepared.data["category"] == "social":
            changed = self._catalog_social(changed, activity)
        activity = schedule_feedback(
            changed.activities[identity], state.current_time, scenario.activity_feedback_seconds)
        changed.activities[identity] = activity
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.IN_PROGRESS, occurred_at=state.current_time, activity_id=identity,
            message="Activity started", data={"activity": activity_snapshot(activity, self.context.titles),
                "content": action_content_result(activity, catalog)})
        return changed, result

    def _catalog_social(self, state: WorldState, activity: ActivityState) -> WorldState:
        """@brief 社交对象来自本人请求，文字主题不创建语音通话事实。"""
        action = self.context.semantics.catalog.actions[activity.data["action_id"]]
        if activity.data["app_id"] is None:
            started = social_actions.start_chat(state, activity, f"invite-{activity.origin_action_id}")
            if started.invite is not None:
                state = self._deliver_social_message(state, started.invite)
            state = _put_activity(state, started.agent, started.activity)
            if started.accepted is not None:
                state.activities[started.accepted.activity_id] = started.accepted
            return state
        contact = self._contact(activity.data["counterpart"], activity.agent_id)
        social_actions.start_remote_social(activity, contact, action.communication_mode)
        return state

    def _collect_parcel(self, state: WorldState, pending: PendingAction,
                        station_id: str) -> tuple[WorldState, ActionResult]:
        """@brief 本人在站领取全部可取订单，只改变订单与收货效果。"""
        outcome = collect_parcels(state, self.context.semantics, pending.agent_id, station_id)
        self.storefront.pending_receipts.extend(outcome.receipts)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time, message="Parcels collected",
            data={"orders": [order.model_dump(mode="json") for order in outcome.orders]})
        return outcome.state, result

    def _set_alarm(self, state: WorldState, pending: PendingAction, request) -> tuple[WorldState, ActionResult]:
        """@brief 受理一次性默认闹钟，不创建活动或应用使用。"""
        changed, data = set_alarm(state, pending, request)
        return changed, ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time, message="Alarm set", data=data)

    def _frozen_observation(self, pending: PendingAction) -> dict:
        """@brief 读取当前决策的已提交观察事件，作为名称与报价的唯一解析来源；缺失或不唯一时 OBSERVATION_NOT_FOUND。"""
        matches = self.context.store.read_decision_observations(
            pending.agent_id, pending.decision_id, pending.observed_at)
        if len(matches) != 1:
            _LOGGER.error("Decision observation is missing or ambiguous")
            raise BusinessRuleError("OBSERVATION_NOT_FOUND")
        return matches[0].data



    def _send_message_action(
        self, state: WorldState, pending: PendingAction, request: SendMessageActionRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 将人物可读收件人名称（本轮人物与联系人，精确或无损归一后唯一，如「沈昊」即「联系人：沈昊」）映射为联系人
        身份并发送正文；驾车时不能发消息，步行途中照常（第五章「并行准入」）。名称经归一的写进结果 details.resolved。"""
        admission.require_hands_free(self._admission_context(state, pending.agent_id), "send a message")
        rows = [
            row
            for row in self._frozen_observation(pending)["interaction_bindings"]
            if row["kind"] in {"person", "contact"}
        ]
        match = resolve_name(request.recipient_name, [row["object"] for row in rows])
        matches = [row for row in rows if row["object"] == match.name]
        if len(matches) != 1:
            _LOGGER.error("Message recipient name is unavailable or ambiguous")
            raise BusinessRuleError("RECIPIENT_NOT_FOUND")
        (changed, result) = self._send_message(
            state, pending, SendMessageRequest(recipient_id=matches[0]["object_id"], text=request.text)
        )
        return (changed, _with_names(result, [(request.recipient_name, match.name)]))


    def _refreshed_result(self, state: WorldState, result: ActionResult) -> ActionResult:
        """@brief 受理结果中的活动快照换成命名与归入会话之后的权威快照。"""
        data = dict(result.data)
        if result.activity_id is not None:
            data["activity"] = activity_snapshot(
                state.activities[result.activity_id], self.context.titles
            )
        if "activities" in data:
            data["activities"] = [
                activity_snapshot(
                    state.activities[row["activity_id"]], self.context.titles
                )
                for row in data["activities"]
            ]
        return result.model_copy(update={"data": data})

    def _goal_link(self, state: WorldState, agent_id: int, activity: ActivityState) -> dict:
        """@brief 关联当日首个匹配目标，否则不关联目标。"""
        goal = self._plan_goal_for_activity(state, agent_id, activity.activity_id)
        if goal is not None:
            return {"task_kind": SessionTaskKind.GOAL, "task_ref": goal, "plan_goal_intent_kind": goal}
        return {}

    def _adopt_started(
        self, state: WorldState, pending: PendingAction, started: list[tuple[str, list[dict]]], result: ActionResult
    ) -> tuple[WorldState, ActionResult]:
        """@brief 新开始的活动按各自的绑定命名、关联任务或目标并归入当前会话；出行被受理即出发，同一请求的活动归入路上。

        :param started: （活动身份，所属绑定）逐项配对：合成一单或一餐时一项活动对应全部绑定，逐项派发时各对应自己的
            一项；瞬时的取得没有活动，不在其中。
        """
        if not started:
            return (state, result)
        activities = dict(state.activities)
        for activity_id, group in started:
            objects = "、".join((activity_control.object_display_name(item) for item in group))
            title = activity_title(activities[activity_id], self.context.titles)
            name = activity_control.activity_name(
                "" if title is not None else activity_phrase(group[0]["selected_action"]),
                title if title is not None else objects,
                state.current_time,
                self.context.scenario.timezone,
            )
            activities[activity_id] = activities[activity_id].model_copy(
                update={"name": name, **self._goal_link(state, pending.agent_id, activities[activity_id])}, deep=True
            )
        changed = state.model_copy(update={"activities": activities})
        identities = [activity_id for (activity_id, _) in started]
        current = sessions.current_session(changed, pending.agent_id)
        changed = sessions.assign(changed, current.session_id, identities)
        return (changed, self._refreshed_result(changed, result))

    def _depart_for_start(self, state: WorldState, pending: PendingAction, selection: list[dict]) -> WorldState:
        """@brief 整组准入后先建立实际路上会话，使各项内容只在真正开始的会话中选择一次。"""
        travel = next((row for row in selection
                       if row["selected_action"]["canonical_action"] == "travel_to"), None)
        if travel is None:
            return state
        return sessions.begin_transit(state, pending.agent_id, travel["object_id"],
                                      place_names(self.context.scenario), opened_by_action_id=pending.action_id)

    def _start_parallel_actions(
        self, state: WorldState, pending: PendingAction, bindings: list[dict]
    ) -> tuple[WorldState, ActionResult, list[tuple[str, list[dict]]]]:
        """@brief 对同一临时世界逐项派发，失败时丢弃整组状态与暂存事件。

        持续活动各自开始；瞬时的取得（到店、远程下单）当场成单，不产生活动（第五章「并行准入」：瞬时的生活动作
        不占名额）。返回合并结果与（活动身份，所属绑定）的逐项配对，供命名与归入会话。
        """
        changed = state
        results: list[ActionResult] = []
        started: list[tuple[str, list[dict]]] = []
        draft_count = len(self.pending_event_drafts)
        try:
            for binding in bindings:
                action = binding["selected_action"]["canonical_action"]
                (changed, result) = self._dispatch_start_action(
                    changed, pending, action, [binding]
                )
                if result.activity_id is not None:
                    started.append((result.activity_id, [binding]))
                elif action not in {"purchase", "collect_parcel"}:
                    _LOGGER.error("Parallel action neither started an activity nor placed an order")
                    raise BusinessRuleError("ACTION_REQUEST_INVALID")
                results.append(result)
        except Exception:
            _LOGGER.error("Parallel activity start was rejected")
            del self.pending_event_drafts[draft_count:]
            raise
        status = ActionStatus.IN_PROGRESS if started else ActionStatus.COMPLETED
        message = "Activities started" if started else "Purchases accepted"
        return (
            changed,
            ActionResult(
                action_id=pending.action_id,
                agent_id=pending.agent_id,
                status=status,
                occurred_at=state.current_time,
                message=message,
                data=_parallel_result_data(results),
            ),
            started,
        )

    def _resolved_named(self, context: LayerContext, row: dict) -> dict:
        """@brief 明确应用与真实收货人决定商品实际履约，不替人物选择应用。"""
        ask = PurchaseAsk(row["object"], row["app"], row["pay_app"], row["recipient_id"])
        route = {key: row[key] for key in routing.ROUTE_KEYS}
        return {**route, **resolve_named_supply(context, ask)}

    def _named_selection(self, state: WorldState, pending: PendingAction, selection: list[dict]) -> list[dict]:
        """@brief 每件商品都按明确应用重算实际店铺；所有失败在整组受理前返回。"""
        selection = [_purchase_placeholder(row) for row in selection]
        if all(row["kind"] != NAMED_SUPPLY_KIND for row in selection):
            return selection
        agent = state.agents[pending.agent_id]
        context = LayerContext(
            state=state, scenario=self.context.scenario, rules=self.context.rules,
            semantics=self.context.semantics, agent_id=agent.agent_id,
            names={"sessions": {}, "activities": self._control_names(pending)},
            bindings=self._frozen_observation(pending)["interaction_bindings"],
            menu=resolve_interaction_menu(self.context.scenario, agent.agent_id, agent.location_id))
        return resolve_named_rows(context, selection, partial(self._resolved_named, context))

    def _start_resolution(
        self, pending: PendingAction, placeholders: list[dict], selection: list[dict], result: ActionResult
    ) -> ActionResult:
        """@brief 受理结果写上名称归一（details.resolved：对象、方式、去向与所回应消息，指名购买的商品名与解析出的完整标签）
        与说明（details.notes：冗余的 from、to，以及指名购买按「购买」定下的方式与按 prefer 选定的门店）；没有归一与说明时
        结果不变。"""
        outcome = self._start_outcome(pending)
        named = [
            {"written": row["object"], "as": resolved["object"]}
            for (row, resolved) in zip(placeholders, selection)
            if row["kind"] == NAMED_SUPPLY_KIND and row["object"] != resolved["object"]
        ]
        chosen = [row["purchase_note"] for row in selection if row.get("purchase_note")]
        return result.model_copy(
            update={"data": resolution_data(result.data, [*outcome.resolved, *named], [*outcome.notes, *chosen])}
        )

    def _unresolved_start(self, pending: PendingAction) -> BusinessRuleError:
        """@brief 本轮解析不出的 start_activity：名称类失败逐项写进 ACTION_REQUEST_INVALID 的 items（候选可以照填，原因逐项
        相连）；数组形状或来源、去向与 answering 不合法时为不带明细的 ACTION_REQUEST_INVALID。宿主已在本轮纠正这些名称，
        这里只防绕过宿主的请求（docs/design/04-simulation-engine.md  第 4 条）。"""
        outcome = self._start_outcome(pending)
        if not outcome.failures:
            return BusinessRuleError("ACTION_REQUEST_INVALID")
        default = refusal_for_code("ACTION_REQUEST_INVALID")
        return Refusal(
            "ACTION_REQUEST_INVALID",
            "; ".join((failure.sentence for failure in outcome.failures)),
            default.next_step,
            fix=default.fix,
            items=tuple((failure.item for failure in outcome.failures)),
        ).error()

    def _start_outcome(self, pending: PendingAction) -> StartOutcome:
        """@brief 这次 start_activity 在本轮冻结观察上的名称解析结果（_offered_selection 的同一次解析，纯函数，可重算）。"""
        request = StartActivityRequest.model_validate(pending.request)
        return resolve_start(name_book(self._frozen_observation(pending)), request)

    def _offered_selection(self, pending: PendingAction, request: StartActivityRequest) -> list[dict] | None:
        """@brief 在本轮冻结观察上逐项解析（对象，动词，方式）并写上去向（docs/design/04-simulation-engine.md ）：列出的
        对象、
        目录绑定与冻结地点表中的名称精确或无损归一后唯一即选中（与宿主本地校验同一函数 name_resolution.resolve_start），
        指名购买为占位行；名称类失败或组合不合法时为 None。组合按归一后的请求判定（冗余的 from、to 已换成 null）；去向不是
        任何人物（RECIPIENT_NOT_FOUND）与列出的标签写了另一个地点作 from（SUPPLY_SOURCE_MISMATCH）照常选中，由受理时带
        说明拒绝。"""
        observation = self._frozen_observation(pending)
        outcome = resolve_start(name_book(observation), request)
        if outcome.request is None or outcome.failures:
            _LOGGER.error("Start activity names do not resolve in this round")
            return None
        self._require_routes(pending)
        names = routing.routing_names(observation)
        return routing.routed_selection(list(outcome.rows), outcome.request, names)

    def _require_routes(self, pending: PendingAction) -> None:
        """@brief 宿主和环境对同一冻结路由错误使用相同码、文字与候选。"""
        observed = self._frozen_observation(pending)
        outcome = self._start_outcome(pending)
        issues = routing.combination_issues(outcome.request, routing.routing_names(observed))
        if not issues:
            return
        items = tuple(ErrorItem(("items", issue.index, issue.field) if issue.index is not None else (issue.field,),
            issue.written, issue.host_code, issue.allowed_values, issue.reason, issue.detail, issue.candidates)
            for issue in issues)
        first = issues[0]
        raise Refusal(first.code, "; ".join(issue.reason for issue in issues), fix=first.fix, items=items).error()

    def _start_selection(self, pending: PendingAction, request) -> list[dict] | None:
        """@brief start_activity 各项经统一名称解析的绑定行，菜单判定、受限轮复核与受理共用这一次解析；其他工具为空。

        名称类失败（找不到、有歧义、动词与方式不相配），或来源、去向与 answering 不合法（routing.combination_error，去向
        写了本轮没有的人物、列出的标签写了另一个地点作 from 除外）时为 None：不作菜单判定，待收尾要求之后由 start_activity
        以 ACTION_REQUEST_INVALID 拒绝（名称类失败逐项写进 items，_unresolved_start）。解析出的各行写上去向与关联
        （routing.routed_selection）。
        """
        return self._offered_selection(pending, request) if pending.tool_name == "start_activity" else []

    def _admission_context(
        self, state: WorldState, agent_id: int, names: dict[str, str] | None = None
    ) -> admission.Context:
        """@brief 准入判定的世界、可读名称（地点名、本轮活动名）与冻结路线（下一步只指向可达地点）。"""
        return admission.Context(
            state=state,
            agent_id=agent_id,
            place_names=place_names(self.context.scenario),
            activity_names=names or {},
            graph=self.context.semantics.catalog.graph,
            catalog=self.context.semantics.catalog, scenario=self.context.scenario,
        )

    def _world_facts(self, agent_id: int) -> admission.WorldFacts:
        """@brief 准入判定用的冻结目录：任务、供给、可持有物与内容。"""
        config = agent_config(self.context.scenario, agent_id)
        workplace = config.work_schedule.workplace_location_id if config.work_schedule is not None else None
        return admission.WorldFacts(self.context.semantics.catalog, self.context.scenario,
                                    self.context.state, agent_id)

    def _require_individual_start(self, state: WorldState, pending: PendingAction, selection: list[dict]) -> None:
        """@brief 实际转换前汇总每項可独立判定的世界前置条件。"""
        agent = state.agents[pending.agent_id]
        context = LayerContext(
            state,
            self.context.scenario,
            self.context.rules,
            self.context.semantics,
            pending.agent_id,
            {"sessions": {}, "activities": self._control_names(pending)},
            selection,
            resolve_interaction_menu(self.context.scenario, pending.agent_id, agent.location_id),
        )
        request = StartActivityRequest.model_validate(pending.request)
        entries = []
        unmet = []
        refusals = []
        for index, row in enumerate(selection):
            refusal = refusal_for(context, ToolCandidate("start_activity", row, row["selected_action"]))
            if refusal is None:
                continue
            item = ErrorItem(
                ("items", index, "target"),
                request.items[index].target,
                refusal.code,
                expected=refusal.reason,
                why=refusal.reason,
            )
            entries.append((item, refusal.reason))
            unmet.extend(refusal.unmet)
            refusals.append(refusal)
        if entries:
            steps = "; ".join(dict.fromkeys((refusal.next_step for refusal in refusals if refusal.next_step)))
            fixes = "; ".join(dict.fromkeys((refusal.fix for refusal in refusals if refusal.fix)))
            suggested = {**(refusals[0].suggested or {}), "next_step": steps}
            combined = replace(
                refusals[0],
                reason="; ".join((reason for (_, reason) in entries)),
                next_step=steps,
                fix=fixes,
                suggested=suggested,
                items=tuple((item for (item, _) in entries)),
                unmet=tuple(dict.fromkeys(unmet)),
            )
            raise combined.error()

    def _start_activity(
        self, state: WorldState, pending: PendingAction, selection: list[dict] | None
    ) -> tuple[WorldState, ActionResult]:
        """@brief 按「动词＋对象＋方式」解析好的冻结绑定整组准入后原子开始；新活动命名并归入会话。

        selection 是派发时解析的本轮绑定（_start_selection，菜单判定、受限轮复核与受理共用这一次解析），解析不出时
        以 ACTION_REQUEST_INVALID 拒绝。整组先按名额、目标在场与实际地点、出行方式、占用、物品状态的顺序判定
        （docs/design/04-simulation-engine.md「并行准入」），再派发；
        结果与组内先后无关：合成一单或一餐的两项
        一次派发，其余逐项派发，瞬时的取得（到店、远程下单）可与持续活动同一请求。
        """
        if selection is None:
            _LOGGER.error("Start activity selection is not offered in this round")
            raise self._unresolved_start(pending)
        routing.require_recipients(selection)
        (placeholders, selection) = (selection, self._named_selection(state, pending, selection))
        actions = [row["selected_action"]["canonical_action"] for row in selection]
        labels = [row["selected_action"]["label"] for row in selection]
        names = self._control_names(pending)
        self._require_individual_start(state, pending, selection)
        activity_control.require_start_admission(state, pending.agent_id, (actions, labels), names)
        admission.require_group(
            self._admission_context(state, pending.agent_id, names),
            [admission.selection_item(row, row["selected_action"], self._world_facts(pending.agent_id))
             for row in selection],
        )
        state = self._depart_for_start(state, pending, selection)
        if merged_selection(actions, labels):
            targets = [item["object_id"] for item in selection]
            (changed, result) = self._dispatch_start_action(state, pending, actions[0], selection)
            started = [(result.activity_id, selection)] if result.activity_id is not None else []
        else:
            (changed, result, started) = self._start_parallel_actions(state, pending, selection)
        (changed, result) = self._adopt_started(changed, pending, started, result)
        return (changed, self._start_resolution(pending, placeholders, selection, result))

    def dispatch(self, state: WorldState, pending: PendingAction) -> tuple[WorldState, ActionResult]:
        """@brief 依据闭集工具名进入对应领域转换。"""
        if pending.tool_name in {"query_world", "recall_task_summary"}:
            logging.error("Read-only query reached world dispatch: %s", pending.tool_name)
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        model = REQUEST_MODELS[pending.tool_name]
        try:
            request = model.model_validate(pending.request)
        except ValidationError as error:
            logging.error("Tool request validation rejected: %s", pending.tool_name)
            report = argument_report(pending.tool_name, error, model, pending.request, "ACTION_REQUEST_INVALID")
            refusal = Refusal("ACTION_REQUEST_INVALID", report.reason, fix=report.fix or "", items=report.items)
            raise refusal.error() from error
        selection = self._start_selection(pending, request)
        if selection is not None:
            require_interaction_menu(
                self.context.scenario, state, pending, [row["selected_action"]["canonical_action"] for row in selection]
            )
        self._require_boundary_action(state, pending, request, selection)
        waiting = activity_control.pending_conclusions(state, pending.agent_id)
        if waiting and pending.tool_name != "conclude_activity":
            raise activity_control.conclusion_required(waiting, self._control_names(pending))
        if pending.tool_name == "start_activity":
            return self._start_activity(state, pending, selection)
        handlers = {
            "send_message": self._send_message_action,
            "report_status": self._report_status,
            "pause_activity": self._pause_activity,
            "conclude_activity": self._conclude_activity,
            "revise_day_plan": self._revise_day_plan,
            "set_alarm": self._set_alarm,
        }
        return handlers[pending.tool_name](state, pending, request)

    def _require_boundary_action(
        self, state: WorldState, pending: PendingAction, request, selection: list[dict] | None
    ) -> None:
        """@brief 决策须已到期；达到同刻上限后的受限轮只接受本轮开放的动作类（第一章「实现约定」）。

        :param selection: 派发时解析的 start_activity 绑定行（其他工具为空，解析不出时为 None）。
        """
        agent = state.agents[pending.agent_id]
        if agent.next_decision_at > state.current_time:
            raise BusinessRuleError("ACTIVITY_FEEDBACK_NOT_DUE")
        if agent.same_moment_stage != SameMomentStage.OPEN:
            same_moment.require_stage_action(
                agent.same_moment_stage, self._same_moment_request_kind(state, pending, request, selection)
            )

    def _same_moment_request_kind(
        self, state: WorldState, pending: PendingAction, request, selection: list[dict] | None
    ) -> str:
        """@brief 按已解析的对象与点名活动，交给 same_moment.request_kind 判定受限轮请求类。

        start_activity 的选择解析不出时以 ACTION_REQUEST_INVALID 拒绝（与受理同一判定，名称类失败逐项写进 items）。
        """
        if selection is None:
            logging.error("Start activity selection is not offered in this round")
            raise self._unresolved_start(pending)
        actions = [row["selected_action"]["canonical_action"] for row in selection]
        named = (
            self._activities_for_names(
                state, pending, [item.activity_name for item in control_items(pending.tool_name, request)]
            )
            if pending.tool_name in {"report_status", "conclude_activity"}
            else []
        )
        waiting = frozenset(
            (item.activity_id for item in activity_control.pending_conclusions(state, pending.agent_id))
        )
        return same_moment.request_kind(pending.tool_name, actions, named, waiting)

    @property
    def record_context(self) -> activity_control.RecordContext:
        """@brief 写活动记录所用的快照与标题投影。"""
        return activity_control.RecordContext(
            snapshot=partial(activity_snapshot, index=self.context.titles),
            title=partial(activity_title, index=self.context.titles),
        )

    def _travel_start_context(self, state: WorldState, agent, request: ActivityStartParams):
        """@brief 在扣款前按唯一发布图复查计划时长。"""
        if request.activity_type != ActivityKind.TRAVEL:
            return None
        return require_travel_duration(
            self.context.scenario, self.context.semantics, agent.location_id, request, state.current_time
        )

    def _start(
        self, state: WorldState, pending: PendingAction, request: ActivityStartParams
    ) -> tuple[WorldState, ActionResult]:
        """@brief 启动普通活动并初始化内容、路线或游戏身份。"""
        agent = state.agents[pending.agent_id]
        data = self.context.rules.validate_start(
            agent, request, state.current_time, admission.present_location(state, pending.agent_id)
        )
        travel_context = self._travel_start_context(state, agent, request)
        claims = resource_claims(request)
        self._require_available_resources(state, pending.agent_id, claims)
        origin = _activity_origin(state, pending)
        (agent, activity) = start_activity(agent, request, state.current_time, origin, self.context.semantics.body)
        activity.resource_claims = claims
        activity = schedule_feedback(activity, state.current_time, self.context.scenario.activity_feedback_seconds)
        changed_state = state
        payment = None
        if travel_context is not None:
            (changed_state, payment) = pay_travel_fare(
                state, self.context.scenario, pending.agent_id, travel_context
            )
            data.update(travel_payment=payment, resource_changes=payment["resource_changes"])
        activity.data.update(data)
        changed = _put_activity(changed_state, agent, activity)
        result_data = {"activity": activity_snapshot(activity, self.context.titles)}
        if payment is not None:
            result_data.update(travel_payment=payment, resource_changes=payment["resource_changes"])
        result = ActionResult(
            action_id=pending.action_id,
            agent_id=agent.agent_id,
            status=ActionStatus.IN_PROGRESS,
            occurred_at=state.current_time,
            activity_id=activity.activity_id,
            message="Activity started",
            data=result_data,
        )
        return (changed, result)


    def _require_available_resources(
        self, state: WorldState, agent_id: int, claims: set[str], exclude_id: str | None = None
    ) -> None:
        """@brief 以全部 ACTIVE 活动显式声明校验同人资源交集；冲突时点名占着的活动。"""
        blocking = [
            item.name
            for item in active_activities(state, agent_id)
            if item.activity_id != exclude_id and item.resource_claims & claims
        ]
        if blocking:
            listed = "、".join(blocking)
            raise guided(
                "RESOURCE_CONFLICT",
                f"This activity cannot run together with {listed}.",
                f"Conclude or pause {listed} first.",
                ["resource_claims"],
            )

    def _change(
        self, state: WorldState, pending: PendingAction, request: ChangeActivityRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 转换人物已有活动并保留已完成进度。"""
        if request.activity_id not in state.activities:
            raise BusinessRuleError("ACTIVITY_NOT_FOUND")
        agent = state.agents[pending.agent_id]
        current = state.activities[request.activity_id]
        if request.operation == ActivityChange.RESUME:
            self._require_available_resources(state, pending.agent_id, current.resource_claims, current.activity_id)
        (agent, activity) = change_activity(
            agent, current, request, state.current_time, self.context.scenario.activity_feedback_seconds
        )
        changed = _put_activity(state, agent, activity)
        if request.operation == ActivityChange.RESUME and current.kind == ActivityKind.ACTION:
            changed = resume_action(changed, activity, self.context.semantics.catalog, self.context.scenario)
            activity = changed.activities[activity.activity_id]
        result_data = {"activity": activity_snapshot(activity, self.context.titles)}
        if activity.kind == ActivityKind.ACTION:
            result_data["content"] = action_content_result(activity, self.context.semantics.catalog)
        result = ActionResult(
            action_id=pending.action_id,
            agent_id=agent.agent_id,
            status=ActionStatus.COMPLETED,
            occurred_at=state.current_time,
            activity_id=activity.activity_id,
            message="Activity changed",
            data=result_data,
        )
        return (changed, result)



    def _revise_day_plan(
        self, state: WorldState, pending: PendingAction, request: ReviseDayPlanRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 替换未推进的当日目标及其提醒，已推进目标保持不变（第四章「计划与提醒」）。"""
        agent = state.agents[pending.agent_id]
        if agent.day_plan is None or day_plan_expired(agent.day_plan, state.current_time):
            raise BusinessRuleError("PLAN_NOT_FOUND")
        try:
            goals = registered_goals(request.goals, state.current_time, self.context.scenario.timezone)
            plan = revise_day_plan(agent.day_plan, state.current_time, goals)
        except RemindTimeInvalid as error:
            raise BusinessRuleError("PLAN_REMIND_TIME_INVALID") from error
        except ValueError as error:
            raise BusinessRuleError(
                "PLAN_GOAL_ALREADY_PROGRESSED" if "already progressed" in str(error) else "PLAN_GOALS_INVALID"
            ) from error
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

    def _send_message(
        self, state: WorldState, pending: PendingAction, request: SendMessageRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 瞬时消息：登记收件方 inbox 与 message_arrival
        送达（docs/design/04-simulation-engine.md「社交接口」）。"""
        agent = state.agents[pending.agent_id]
        contacts = agent_config(self.context.scenario, agent.agent_id).contacts
        outgoing = social_domain.OutgoingMessage(
            message_id=f"message-{pending.action_id}",
            sender_id=f"agent:{agent.agent_id}",
            recipient_id=request.recipient_id,
            text=request.text,
            created_at=state.current_time,
        )
        world_agent_ids = {f"agent:{agent_id}" for agent_id in self.context.state.agents}
        try:
            sent = social_domain.send_message(outgoing, world_agent_ids, contacts)
        except ValueError as error:
            if "recipient not found" in str(error):
                raise BusinessRuleError("RECIPIENT_NOT_FOUND") from error
            raise BusinessRuleError("MESSAGE_TEXT_TOO_LONG") from error
        state = self._register_delivered_message(state, agent, contacts, request, sent)
        result = ActionResult(
            action_id=pending.action_id,
            agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED,
            occurred_at=state.current_time,
            message="Message delivered",
            data={"message_id": sent.message.message_id, "recipient_id": request.recipient_id},
        )
        return (state, result)

    def _register_delivered_message(
        self, state: WorldState, agent, contacts, request: SendMessageRequest, sent
    ) -> WorldState:
        """@brief 送达登记：收件方 inbox、发给联系人的消息记入本人 outbox、model 联系人回复待办与 message_arrival 事件。

        同世界收件人的 message_arrival 草稿登记其同秒就绪，由 commit_decision_round 在本轮全部动作结算后合并
        （docs/design/04-simulation-engine.md「人物之间的消息」）；联系人回复请求带双方往来与对方收到的物品
        （docs/design/06-agent.md「联系人往来」）。
        """
        drafts: list[EventDraft] = []
        recipient_agent = next(
            (item for item in state.agents.values() if f"agent:{item.agent_id}" == request.recipient_id), None
        )
        if recipient_agent is not None:
            inbox = dict(state.agents[recipient_agent.agent_id].inbox)
            inbox[sent.message.message_id] = sent.message
            state = _put_agent(
                state, state.agents[recipient_agent.agent_id].model_copy(update={"inbox": inbox}, deep=True)
            )
        elif any((contact.contact_id == request.recipient_id for contact in contacts)):
            contact = next((item for item in contacts if item.contact_id == request.recipient_id))
            state = contact_exchange.record_outgoing(state, agent.agent_id, sent.message)
            if contact.reply_policy == "model":
                reply = contact_exchange.reply_request(
                    state, self.context.semantics, sent.message, contact
                )
                drafts.append(
                    EventDraft(
                        event_type="environment_event", occurred_at=state.current_time, agent_id=agent.agent_id,
                        source_kind="model", visibility="system",
                        data={"kind": "model_npc_reply_pending", "request": reply.model_dump(mode="json")},
                    )
                )
            elif contact.reply_policy == "template":
                state = social_actions.schedule_template_reply(state, sent.message, contact)
        else:
            raise BusinessRuleError("RECIPIENT_NOT_FOUND")
        arrival = EventDraft(
            event_type="environment_event", occurred_at=state.current_time,
            agent_id=recipient_agent.agent_id if recipient_agent else None, source_kind="environment",
            data={
                "kind": "message_arrival",
                "message_id": sent.message.message_id,
                "recipient_id": request.recipient_id,
                "occurred_at": state.current_time,
            },
        )
        drafts.append(arrival)
        self.pending_event_drafts.extend(drafts)
        return state


    def _contact(self, contact_id: str, agent_id: int):
        """@brief 查找人物声明的联系人（找不到即业务拒绝）。"""
        contacts = agent_config(self.context.scenario, agent_id).contacts
        matches = [item for item in contacts if item.contact_id == contact_id]
        if len(matches) != 1:
            raise BusinessRuleError("CONTACT_NOT_FOUND")
        return matches[0]

    def _deliver_social_message(self, state: WorldState, sent) -> WorldState:
        """@brief 把交谈邀请送达对方 inbox 并留 message_arrival 事件草稿，返回新的世界副本。

        不就地改动传入的状态：整组开始被拒时连同暂存草稿一起丢弃，对方不会收到幽灵邀请。
        该草稿登记清醒收件人的同秒就绪，由 commit_decision_round 在本轮全部动作结算后合并
        （docs/design/04-simulation-engine.md「人物之间的消息」）。
        """
        recipient = next(
            (item for item in state.agents.values() if f"agent:{item.agent_id}" == sent.message.recipient_id), None
        )
        if recipient is not None:
            inbox = {**recipient.inbox, sent.message.message_id: sent.message}
            state = _put_agent(state, recipient.model_copy(update={"inbox": inbox}, deep=True))
        self.pending_event_drafts.append(
            EventDraft(
                event_type="environment_event",
                occurred_at=state.current_time,
                agent_id=recipient.agent_id if recipient else None,
                source_kind="environment",
                data={
                    "kind": "message_arrival",
                    "message_id": sent.message.message_id,
                    "recipient_id": sent.message.recipient_id,
                    "occurred_at": sent.arrival.occurred_at,
                },
            )
        )
        return state

    def drain_drafts(self) -> list[EventDraft]:
        """@brief 取走动作结算中暂存的送达/模型回复待办事件草稿。"""
        drained = list(self.pending_event_drafts)
        self.pending_event_drafts.clear()
        return drained



def _purchase_placeholder(row: dict) -> dict:
    """@brief 所有商品行统一走商品名与实际应用的购买选择。"""
    if row["kind"] != "supply":
        return row
    return {**named_supply_row(row["object"], "购买"), **{key: row[key] for key in routing.ROUTE_KEYS}}


def _with_names(result: ActionResult, pairs) -> ActionResult:
    """@brief 受理结果写上名称归一 details.resolved：（所写名称，解析出的名称）逐对中两者不同的记一条；都照写时结果不变
    （docs/design/04-simulation-engine.md ）。"""
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
    不写其他回看事实；否则为 None（docs/design/06-agent.md「唯一出现原则」）。"""
    if activity is None or not activity.progress_note:
        return None
    names = {row["activity_id"]: row["name"] for row in observed["activity_bindings"]}
    label = memory_layer.noted_label(observed["memory_layer"], names[activity.activity_id], activity.progress_note)
    return None if label is None else {"activity": label, "progress_note": RECALL_IN_MEMORY_LAYER}




def _parallel_result_data(results: list[ActionResult]) -> dict:
    """@brief 逐项派发合并真实活动快照、订单、资金变动与关联消息，顺序与请求一致。"""
    changes: dict[str, int] = {}
    for result in results:
        for account, amount in result.data.get("resource_changes", {}).items():
            changes[account] = changes.get(account, 0) + amount
    data = {
        "activities": [result.data["activity"] for result in results if "activity" in result.data],
        "activity_results": [result.data for result in results],
    }
    if changes:
        data["resource_changes"] = changes
    orders = [order for result in results for order in result.data.get("orders", [])]
    if orders:
        data["orders"] = orders
    answering = next((result.data["answering"] for result in results if "answering" in result.data), None)
    if answering is not None:
        data["answering"] = answering
    return data
