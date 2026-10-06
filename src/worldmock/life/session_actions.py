"""@brief ActionHandlers 的独立生活运行职责。"""

from __future__ import annotations
from worldmock.life import sleep as sleep_module
from worldmock.life import social as social_domain
from worldmock.life.world_ops import _activity_origin
from worldmock.life.world_ops import _put_activity
from worldmock.life.world_ops import _put_agent

import logging
from dataclasses import replace
from pydantic import ValidationError
from worldmock.categories import Action
from worldmock.errors import BusinessRuleError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life import activity_control, admission, contact_exchange, errands, same_moment, sessions, social_actions
from worldmock.life.menu_execution import require_interaction_menu
from worldmock.life.guidance import Refusal, guided
from worldmock.life.request_errors import argument_report
from worldmock.life.activity_titles import activity_title
from worldmock.life.control_items import control_items
from worldmock.life.activities import change_activity, resource_claims, schedule_feedback, start_activity
from worldmock.life.basket import meal_duration, meal_food_rate, open_meal, validate_meal_inventory, validate_meal_use
from worldmock.life.body import apply_mood_pulse
from worldmock.life.category_contract import match_rows, require_item_rules
from worldmock.life.models import (
    ActionResult,
    ActionStatus,
    ActivityChange,
    ActivityKind,
    ActivityStartParams,
    ActivityStatus,
    ChangeActivityRequest,
    ConsumeRequest,
    PendingAction,
    REQUEST_MODELS,
    ReviseDayPlanRequest,
    SameMomentStage,
    SendMessageRequest,
    SubmitWorkActionRequest,
    StartSocialActivityRequest,
    SubmitWorkRequest,
    WorldState,
    active_activities,
)
from worldmock.life.inventory_recipe import RecipeContext, prepare_inventory_recipe
from worldmock.life.schedule import RemindTimeInvalid, day_plan_expired, registered_goals, revise_day_plan
from worldmock.life.storefront_runtime import StorefrontRuntime
from worldmock.life.travel import pay_travel_fare, planned_travel, require_travel_duration
from worldmock.life.work import (
    load_work_input,
    prepare_work_submission,
    settle_work,
    validate_work_duration,
    work_totals,
)
from worldmock.life.work_review import WorkReviewDecision, prepare_work_review, public_work_review, review_matches

import logging
from dataclasses import replace
from functools import partial
from worldmock.errors import BusinessRuleError
from worldmock.life import (
    activity_control,
    admission,
    errands,
    giving,
    memory_layer,
    order_routing,
    request_linking,
    routing,
    sessions,
)
from worldmock.life.activity_evidence import conclusion_outcome, discard_opened
from worldmock.life.basket import held_quantity, meal_portion
from worldmock.life.durations import capped_duration_seconds, derived_activity_duration
from worldmock.life.control_items import control_errands, control_items
from worldmock.life.guidance import Refusal, guided, refusal_for_code
from worldmock.life.error_contract import ErrorItem
from worldmock.life.vocabulary import GIVE_WAY, PURCHASE_WAYS
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
    CONSUME_DURATION_CAP_SECONDS,
    DERIVED_DURATION_CAP_SECONDS,
    ConsumeItem,
    ConsumeRequest,
    LIFESTYLE_TYPE_PREFIX,
    PauseActivityRequest,
    PendingAction,
    RecallTaskSummaryRequest,
    ReportStatusRequest,
    SendMessageActionRequest,
    SendMessageRequest,
    SessionTaskKind,
    StartActivityRequest,
    StartSocialActivityRequest,
    SubmitWorkRequest,
    SubmitWorkActionRequest,
    WorldState,
)
from worldmock.life.name_resolution import StartOutcome, name_book, resolution_data, resolve_start
from worldmock.life.object_labels import resolve_name
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_rows, resolve_named_supply
from worldmock.life.readable import iso_time
from worldmock.life.recipients import person_name, recipient_location
from worldmock.life.schedule import INTENT_ACTIVITY_FAMILIES, activity_family
from worldmock.life.tool_layer import Candidate as ToolCandidate, LayerContext, refusal_for
from worldmock.life.social_actions import social_duration_seconds
from worldmock.life.work import work_resume_cap, work_start_refusal, work_start_seconds, work_totals

_LOGGER = logging.getLogger(__name__)
RECALL_IN_MEMORY_LAYER = "已在记忆层"
_ENVIRONMENT_ROUTE_CODES = frozenset({routing.RECIPIENT_NOT_FOUND, routing.SUPPLY_SOURCE_MISMATCH})
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
        self, state: WorldState, agent_id: int, names: list[str], selected: list[ActivityState]
    ) -> list[dict]:
        """@brief 逐项投影真实结算终局与物品变化；人物总结不改变库存。"""
        ended = [state.activities[activity.activity_id] for activity in selected]
        product_names = {item.product_id: item.name for item in self.context.rules.items.values()}
        return [
            conclusion_outcome(activity, name, product_names, state.agents[agent_id])
            for (name, activity) in zip(names, ended, strict=True)
        ]

    def _conclude_activity(
        self, state: WorldState, pending: PendingAction, request: ConcludeActivityRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 结束所选活动（进行中、暂停中或待收尾）并写下总结；进行中与待收尾的由此释放名额；途中结束出行时路上的其他
        活动随之结束。

        进行中或暂停的活动先取消（agent_cancelled）再收尾；待收尾活动不改终态，只写总结并置 concluded_at。
        有待收尾活动而本次没有点名任何一项时以 ACTIVITY_CONCLUSION_REQUIRED 拒绝。记忆由宿主写入，环境只校验形状，
        并在结果中登记私有来源 memory_sources。进食收尾时丢弃开吃份量中没吃的部分，结果的 outcome 逐项写终局与份量，
        未完成的浏览写未取得浏览结果（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「进食」、第六章）。请求级的事务变更在活动名
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
                discard_opened(activities[activity.activity_id]), summary, state.current_time
            )
        changed = self._with_goal_summaries(
            changed.model_copy(update={"activities": activities}), list(zip(selected, summaries, strict=True))
        )
        if departed:
            (changed, _) = sessions.end_transit(
                changed, pending.agent_id, place_names(self.context.scenario), opened_by_action_id=pending.action_id
            )
        (changed, receipt) = self._apply_errands(changed, pending, changes)
        result = self._control_result(state, pending, "Activities concluded", requested, selected)
        data = {**result.data, "errands": receipt, "memory_sources": self._memory_sources(pending, requested, selected)}
        outcome = self._conclusion_outcome(changed, pending.agent_id, requested, selected)
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
        result = self._control_result(state, pending, "Activity paused", requested, selected)
        result = result.model_copy(update={"data": {**result.data, "errands": receipt}})
        return (changed, _with_names(result, zip(written, requested)))

    def _resume_cap(self, state: WorldState, agent_id: int, activity: ActivityState) -> int | None:
        """@brief 恢复的时长上限：工作取 min(距截止, 合同上限减已累计)，其余活动没有上限（规格第六章）。

        开工与恢复使用同一截止约束：不足 60 秒的恢复已由 activity_control.control_refusal 以 ACTIVITY_DURATION_INVALID
        拒绝（与工具层、待办层的可用性同一原因），到这里的上限至少 60 秒。
        """
        if activity.kind != ActivityKind.WORK:
            return None
        task = self.context.rules.task(activity.target_id)
        (worked, _) = work_totals(state, agent_id, task.task_id)
        return work_resume_cap(task, worked, state.current_time)

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
                resume_cap_seconds=self._resume_cap(state, pending.agent_id, activity),
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
        result = self._control_result(state, pending, "Activity status reported", requested, selected)
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
        轮数，docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-校验与错误 「环境复核」）。

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
        self, state: WorldState, pending: PendingAction, message: str, names: list[str], selected: list[ActivityState]
    ) -> ActionResult:
        """@brief 控制动作的结果：逐项的可读活动名与活动身份（私有），供评估逐活动投影。"""
        return ActionResult(
            action_id=pending.action_id,
            agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED,
            occurred_at=state.current_time,
            message=message,
            data={"task": list(names), "activity_ids": [item.activity_id for item in selected]},
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
        （activity_control.activity_name_refusal，docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析  第
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
        """@brief 活动回看：名称、地点、已做与有效时长、阶段与自述。

        地点取活动最近所属会话的说法：路上开始的活动（含出行本身）为「去<目的地>的路上」，停留中的为该地点；
        未归入会话的活动取开始地点。自述仍在记忆层时只写活动名与「已在记忆层」。
        """
        del agent_id
        activity = state.activities[row["activity_id"]]
        remembered = _remembered(observed, activity)
        if remembered is not None:
            return remembered
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
        return {**view, **_self_report(activity.progress_note)}

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

    def _recall_task(self, state: WorldState, agent_id: int, row: dict, observed: dict) -> dict:
        """@brief 任务回看：名称、截止（带时区 ISO）与最近一条相关活动自述；该自述仍在记忆层时只写那项活动在记忆层中的
        名称与「已在记忆层」。"""
        task = self.context.rules.task(row["task_id"])
        notes = [
            item
            for item in state.activities.values()
            if item.agent_id == agent_id
            and item.task_kind == SessionTaskKind.WORK_TASK
            and (item.task_ref == task.task_id)
            and item.progress_note
        ]
        latest = max(notes, key=lambda item: (item.note_updated_at or item.started_at, item.started_at), default=None)
        remembered = _remembered(observed, latest)
        if remembered is not None:
            return remembered
        view = {
            "name": row["task_name"],
            "due_at": iso_time(int(task.due_at.timestamp()), self.context.scenario.timezone),
        }
        return {**view, **_self_report(latest.progress_note if latest else "")}

    def read_task_summary(self, agent_id: int, request: RecallTaskSummaryRequest, observed: dict) -> dict:
        """@brief 即时只读回看冻结观察的任务，不登记动作、不推进世界。"""
        name = resolve_name(request.task_name, _recall_names(observed)).name
        candidates = (
            [("task", row) for row in observed["task_bindings"] if row["task_name"] == name]
            + [("goal", row) for row in observed["goal_bindings"] if row["task_name"] == name]
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
        views = {"task": self._recall_task, "goal": self._recall_goal, "activity": self._recall_activity}
        recall = views[kind](self.context.state, agent_id, row, observed)
        return {"recall": {"kind": kind, **recall}}

    def _plan_goal_for_activity(self, state: WorldState, agent_id: int, activity_id: str | None) -> str | None:
        """@brief 按既有活动家族规则将新活动绑定到当日首个匹配目标。"""
        plan = state.agents[agent_id].day_plan
        if plan is None or activity_id is None or state.current_time >= plan.day_end:
            return None
        activity = state.activities[activity_id]
        snapshot = {"kind": activity.kind.value}
        if activity.kind == ActivityKind.LIFESTYLE:
            target = (activity.target_id or "").removeprefix(LIFESTYLE_TYPE_PREFIX)
            item = next((entry for entry in self.context.lifestyle_items if entry.activity_id == target), None)
            if item is None:
                _LOGGER.error("Lifestyle goal category is unavailable")
                raise BusinessRuleError("LIFESTYLE_ACTIVITY_NOT_FOUND")
            snapshot["category"] = item.category
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

    def _start_consume_action(
        self, state: WorldState, pending: PendingAction, targets: list[str], bindings: list[dict]
    ) -> tuple[WorldState, ActionResult]:
        """@brief 从冻结商品目录推导食品进食或单件耐用品使用的数量和时长。

        食用每件取 min(1, 手边持有)，时长按份量折算（不少于 60 秒）后取最长；使用为一件与目录时长（basket.meal_portion，
        工具层同一口径，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「进食」）。手边一点都没有时以 PRODUCT_NOT_HELD 拒绝。
        """
        products = self.context.rules.items
        if any((item not in products for item in targets)):
            _LOGGER.error("Selected food product was not found")
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        mode = bindings[0]["selected_action"]["use_mode"]
        agent = state.agents[pending.agent_id]
        portions = [meal_portion(products[item], mode, held_quantity(agent, item)) for item in targets]
        if not 60 <= max((seconds for (_, seconds) in portions)) <= CONSUME_DURATION_CAP_SECONDS:
            _LOGGER.error("Derived food consumption duration is outside domain bounds")
            raise BusinessRuleError("ACTIVITY_DURATION_INVALID")
        if any((mode not in products[item].use_modes for item in targets)):
            _LOGGER.error("Selected objects cannot share a consumption mode")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        if mode == "use" and len(targets) != 1:
            _LOGGER.error("Only one held item can be used at a time")
            raise guided(
                "ACTION_REQUEST_INVALID",
                "Only one non-food item can be used at a time.",
                "Select one item.",
                fix="Submit one item.",
            )
        if any((portion <= 0 for (portion, _) in portions)):
            _LOGGER.error("Selected food is not held at hand")
            raise BusinessRuleError("PRODUCT_NOT_HELD")
        items = tuple(
            (ConsumeItem(product_id=item, quantity=portion) for (item, (portion, _)) in zip(targets, portions))
        )
        consume = ConsumeRequest(
            items=items, use_mode=mode, duration_seconds=max((seconds for (_, seconds) in portions))
        )
        return self._consume(state, pending, consume)

    def _give_action(self, state: WorldState, pending: PendingAction, binding: dict) -> tuple[WorldState, ActionResult]:
        """@brief 瞬时给予：把手边持有物交给去向中的人物，不产生活动、不增加任何人的摄入（life.giving）。"""
        (changed, given) = giving.give(state, self.context.scenario, pending.agent_id, binding)
        return (
            changed,
            ActionResult(
                action_id=pending.action_id,
                agent_id=pending.agent_id,
                status=ActionStatus.COMPLETED,
                occurred_at=state.current_time,
                message="Item handed over",
                data={"given": [given]},
            ),
        )

    def _start_purchase_action(
        self, state: WorldState, pending: PendingAction, bindings: list[dict]
    ) -> tuple[WorldState, ActionResult]:
        """@brief 使用各供给冻结报价和人物选择的履约方式，系统确定单件数量；按收货地拆成几笔整单依次结算。

        被 answering 关联、且已有同一（消息，收货人，商品）未取消订单的项以 REQUEST_ALREADY_HANDLED 拒绝。from 解析为渠道
        的项，订单载荷记该渠道的 channel_id（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店  解析规则 4），
        其余为 null。
        """
        request_linking.require_unhandled(state, self.context.scenario, self.context.rules.supplies, bindings)
        named = frozenset((item["object_id"] for item in bindings if item.get("named")))
        channels = {(item["object_id"], item["recipient_id"]): item.get("channel_id") for item in bindings}
        (changed, results) = (state, [])
        for purchase in order_routing.order_requests(
            state, self.context.semantics, self.context.rules.supplies, pending.agent_id, bindings
        ):
            (changed, result) = self.storefront._purchase(changed, pending, purchase, named, channels)
            results.append(result)
        answering = next((item["answering"] for item in bindings if item.get("answering")), None)
        return (changed, order_routing.merged_result(results, answering))

    def _start_social_action(
        self, state: WorldState, pending: PendingAction, action: str, target: str
    ) -> tuple[WorldState, ActionResult]:
        """@brief 启动 chat/call 动作对应的社交活动：时长由场景反馈间隔派生后截取（通话 600–3600 秒，交谈 60–7200 秒）。"""
        duration = social_duration_seconds(action, self.context.scenario.activity_feedback_seconds)
        social = StartSocialActivityRequest(kind=action, partner_id=target, duration_minutes=duration // 60)
        return self._start_social_activity(state, pending, social)

    def _start_self_action(
        self, state: WorldState, pending: PendingAction, action: str
    ) -> tuple[WorldState, ActionResult]:
        """@brief 启动 wait/sleep 动作对应的无目标自身活动。"""
        agent = state.agents[pending.agent_id]
        kind = ActivityKind.WAIT if action == "wait" else ActivityKind.SLEEP
        duration = derived_activity_duration(kind, agent, None, self.context.scenario.activity_feedback_seconds)
        return self._start(state, pending, ActivityStartParams(activity_type=kind, planned_duration_seconds=duration))

    def _start_lifestyle_action(
        self, state: WorldState, pending: PendingAction, target: str
    ) -> tuple[WorldState, ActionResult]:
        """@brief 启动 do 动作对应的生活方式目录活动。"""
        item = next((item for item in self.context.lifestyle_items if item.activity_id == target), None)
        if item is None:
            _LOGGER.error("Lifestyle item was not found in the frozen catalog")
            raise BusinessRuleError("LIFESTYLE_ACTIVITY_NOT_FOUND")
        duration = derived_activity_duration(
            ActivityKind.LIFESTYLE, state.agents[pending.agent_id], item,
            self.context.scenario.activity_feedback_seconds
        )
        params = ActivityStartParams(
            activity_type=ActivityKind.LIFESTYLE, planned_duration_seconds=duration, target_id=target
        )
        return self._start(state, pending, params)

    def _start_browse_action(
        self, state: WorldState, pending: PendingAction, target: str
    ) -> tuple[WorldState, ActionResult]:
        """@brief 启动 browse 动作对应的无既定目标浏览活动。"""
        duration = self._feedback_duration()
        params = ActivityStartParams(
            activity_type=ActivityKind.BROWSE, planned_duration_seconds=duration, target_id=target
        )
        return self._start(state, pending, params)

    def _feedback_duration(self) -> int:
        """@brief 使用共享规则计算场景反馈间隔对应的合法活动时长。"""
        return capped_duration_seconds(self.context.scenario.activity_feedback_seconds)

    def _targeted_duration(self, state: WorldState, agent_id: int, kind: ActivityKind, target: str) -> int:
        """@brief 合同、内容进度与反馈间隔共用单一时长规则，工作拒绝沿用共享原因与建议。"""
        if kind == ActivityKind.WORK:
            task = self.context.rules.task(target)
            worked, _ = work_totals(state, agent_id, target)
            refusal = work_start_refusal(task, worked, state.current_time)
            if refusal is not None:
                raise refusal.error()
            return work_start_seconds(task, worked, state.current_time)
        item = self.context.rules.content(target) if kind == ActivityKind.WATCH else None
        return derived_activity_duration(
            kind, state.agents[agent_id], item, self.context.scenario.activity_feedback_seconds
        )

    def _start_targeted_action(
        self, state: WorldState, pending: PendingAction, kind: ActivityKind, target: str
    ) -> tuple[WorldState, ActionResult]:
        """@brief 启动 work/watch/play/read 等指向单一目标的定时活动。"""
        duration = self._targeted_duration(state, pending.agent_id, kind, target)
        params = ActivityStartParams(activity_type=kind, planned_duration_seconds=duration, target_id=target)
        return self._start(state, pending, params)

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
        params = draft.model_copy(update={"planned_duration_seconds": duration})
        return self._start(state, pending, params)

    def _dispatch_start_action(
        self, state: WorldState, pending: PendingAction, action: str, targets: list[str], bindings: list[dict]
    ) -> tuple[WorldState, ActionResult]:
        """@brief 按 action 枚举路由到对应领域启动路径（各分支复用既有领域处理器）。"""
        targeted_kinds = {
            "work": ActivityKind.WORK,
            "watch": ActivityKind.WATCH,
            "play": ActivityKind.PLAY_GAME,
            "read": ActivityKind.READ_MAIL,
        }
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
        if action == "consume" and bindings[0]["selected_action"]["label"] == GIVE_WAY:
            return self._give_action(state, pending, bindings[0])
        if action == "consume":
            return self._start_consume_action(state, pending, targets, bindings)
        _LOGGER.error("Start activity action is unsupported: %s", action)
        raise BusinessRuleError("ACTION_NOT_SUPPORTED")

    def _frozen_observation(self, pending: PendingAction) -> dict:
        """@brief 读取当前决策的已提交观察事件，作为名称与报价的唯一解析来源；缺失或不唯一时 OBSERVATION_NOT_FOUND。"""
        events = self.context.store.read_agent_events("observation", pending.agent_id)
        matches = [
            event
            for event in events
            if event.decision_id == pending.decision_id and event.occurred_at == pending.observed_at
        ]
        if len(matches) != 1:
            _LOGGER.error("Decision observation is missing or ambiguous")
            raise BusinessRuleError("OBSERVATION_NOT_FOUND")
        return matches[0].data

    def _task_binding(self, pending: PendingAction, name: str) -> dict:
        """@brief 可读任务名称在当前决策观察中唯一对应的任务绑定（精确或无损归一后唯一，如「整理纪要」即「工作：整理纪要」）；
        找不到或对应不止一项时以 WORK_TASK_NOT_FOUND 拒绝。"""
        rows = self._frozen_observation(pending)["task_bindings"]
        match = resolve_name(name, [row["task_name"] for row in rows])
        matches = [row for row in rows if row["task_name"] == match.name]
        if len(matches) != 1:
            _LOGGER.error("Work task name is unavailable or ambiguous")
            raise BusinessRuleError("WORK_TASK_NOT_FOUND")
        return matches[0]

    def _task_id_for_name(self, pending: PendingAction, name: str) -> str:
        """@brief 从当前决策观察唯一解析工作合同的可读名称（精确或无损归一后唯一）。"""
        return self._task_binding(pending, name)["task_id"]

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

    def _submit_work_action(
        self, state: WorldState, pending: PendingAction, request: SubmitWorkActionRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 将人物可读任务名称（精确或无损归一后唯一）映射为合同身份，交给独立工作验收路径；驾车时不能提交成果
        （第五章「并行准入」）。名称经归一的写进结果 details.resolved。"""
        admission.require_hands_free(self._admission_context(state, pending.agent_id), "submit work")
        row = self._task_binding(pending, request.task_name)
        (changed, result) = self._submit_work(
            state, pending, SubmitWorkRequest(task_id=row["task_id"], deliverable=request.deliverable)
        )
        return (changed, _with_names(result, [(request.task_name, row["task_name"])]))

    def _refreshed_result(self, state: WorldState, result: ActionResult) -> ActionResult:
        """@brief 受理结果中的活动快照换成命名与归入会话之后的权威快照。"""
        data = dict(result.data)
        if result.activity_id is not None:
            data["activity"] = activity_snapshot(
                state.activities[result.activity_id], self.context.titles, self.context.lifestyle_items
            )
        if "activities" in data:
            data["activities"] = [
                activity_snapshot(
                    state.activities[row["activity_id"]], self.context.titles, self.context.lifestyle_items
                )
                for row in data["activities"]
            ]
        return result.model_copy(update={"data": data})

    def _task_link(self, state: WorldState, agent_id: int, activity: ActivityState) -> dict:
        """@brief 活动关联的任务：工作任务或当日首个匹配目标（按既有活动家族规则），否则不关联。"""
        goal = self._plan_goal_for_activity(state, agent_id, activity.activity_id)
        if activity.kind == ActivityKind.WORK and activity.target_id in self.context.rules.tasks:
            return {
                "task_kind": SessionTaskKind.WORK_TASK,
                "task_ref": activity.target_id,
                "plan_goal_intent_kind": goal,
            }
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
            name = activity_control.activity_name(
                activity_phrase(group[0]["selected_action"]),
                objects,
                state.current_time,
                self.context.scenario.timezone,
            )
            activities[activity_id] = activities[activity_id].model_copy(
                update={"name": name, **self._task_link(state, pending.agent_id, activities[activity_id])}, deep=True
            )
        changed = state.model_copy(update={"activities": activities})
        identities = [activity_id for (activity_id, _) in started]
        travel = next((activities[item] for item in identities if activities[item].kind == ActivityKind.TRAVEL), None)
        if travel is not None:
            changed = sessions.begin_transit(
                changed,
                pending.agent_id,
                travel.destination_id,
                place_names(self.context.scenario),
                opened_by_action_id=pending.action_id,
            )
        current = sessions.current_session(changed, pending.agent_id)
        changed = sessions.assign(changed, current.session_id, identities)
        return (changed, self._refreshed_result(changed, result))

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
                    changed, pending, action, [binding["object_id"]], [binding]
                )
                if result.activity_id is not None:
                    started.append((result.activity_id, [binding]))
                elif action != "purchase" and binding["selected_action"]["label"] != GIVE_WAY:
                    _LOGGER.error("Parallel action neither started an activity nor placed an order")
                    raise BusinessRuleError("ACTION_REQUEST_INVALID")
                results.append(result)
        except Exception:
            _LOGGER.error("Parallel activity start was rejected")
            del self.pending_event_drafts[draft_count:]
            raise
        status = ActionStatus.IN_PROGRESS if started else ActionStatus.COMPLETED
        instant = (
            "Purchases accepted" if all((result.data.get("orders") for result in results)) else "Items handed over"
        )
        message = "Activities started" if started else instant
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

    def _resolved_named(self, context: LayerContext, prefer: str, row: dict) -> dict:
        """@brief 一项指名购买按名称、来源、方式、收货人的收货地点与偏好解析出的供给绑定行（带该项的来源、去向与关联）。"""
        way = row["selected_action"]["label"]
        destination = recipient_location(context.state, self.context.scenario, context.agent_id, row["recipient_id"])
        if destination is None and way == PURCHASE_WAYS["delivery"]:
            _LOGGER.error("Named purchase recipient has no receiving place")
            raise order_routing.undeliverable(
                context.state, self.context.scenario, context.agent_id, row["recipient_id"]
            )
        receiver = (
            place_names(self.context.scenario)[destination]
            if destination is not None
            else f"{person_name(context.state, self.context.scenario, context.agent_id, row['recipient_id'])}那里"
        )
        ask = PurchaseAsk(
            row["object"],
            way,
            row["from"],
            destination,
            prefer,
            receiver,
            None if row["to_self"] else row["recipient_id"],
        )
        return {**resolve_named_supply(context, ask), **{key: row[key] for key in routing.ROUTE_KEYS}}

    def _named_selection(self, state: WorldState, pending: PendingAction, selection: list[dict]) -> list[dict]:
        """@brief 指名购买的占位项按名称解析为门店供给的绑定行，其余项原样：from 是可选约束（null、门店名或渠道称呼），收货地
        取该项收货人的收货地点（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「来源与去向」），多家可服务时按请求级 prefer 选定
        （docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法 、3.3、3.5，named_purchase），解析出的绑定行
        保留来源、去向与关联。列出的
        商品目标写了 from（渠道称呼、不认识的写法，或绕过宿主的另一个地点）时换成同名的占位项，一并按来源约束解析。

        各段拒绝（SUPPLY_NOT_FOUND、SOURCE_NOT_FOUND、SUPPLY_SOURCE_MISMATCH、SUPPLY_NOT_AT_STORE、
        FULFILLMENT_NOT_SUPPORTED 与 M3 的 ACTIVITY_PRECONDITION_UNMET）逐项写进一次拒绝的 items[]（第四章）；远程下单而
        收货人没有收货地点时以「暂时送不到<名>那里」拒绝；按当前报价解析，价格、余额、营业与库存由整单结算照常检查。
        """
        selection = [_source_placeholder(row) for row in selection]
        if all((row["kind"] != NAMED_SUPPLY_KIND for row in selection)):
            return selection
        agent = state.agents[pending.agent_id]
        context = LayerContext(
            state=state,
            scenario=self.context.scenario,
            rules=self.context.rules,
            semantics=self.context.semantics,
            agent_id=agent.agent_id,
            names={"sessions": {}, "activities": self._control_names(pending)},
            bindings=self._frozen_observation(pending)["interaction_bindings"],
            menu=resolve_interaction_menu(self.context.scenario, agent.agent_id, agent.location_id),
            lifestyle_items=self.context.lifestyle_items,
        )
        prefer = StartActivityRequest.model_validate(pending.request).prefer
        return resolve_named_rows(context, selection, partial(self._resolved_named, context, prefer))

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
        这里只防绕过宿主的请求（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析  第 4 条）。"""
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
        """@brief 在本轮冻结观察上逐项解析（对象，动词，方式）并写上去向（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析 ）：列出的
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
        names = routing.routing_names(observation)
        error = routing.combination_error(outcome.request, names)
        if error is not None and error[0] not in _ENVIRONMENT_ROUTE_CODES:
            _LOGGER.error("Start activity from, to or answering is invalid: %s", error[1])
            return None
        return routing.routed_selection(list(outcome.rows), outcome.request, names)

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
            tasks=self.context.rules.tasks,
            graph=self.context.semantics.catalog.graph,
        )

    @property
    def _world_facts(self) -> admission.WorldFacts:
        """@brief 准入判定用的冻结目录：任务、供给、可持有物与内容。"""
        return admission.WorldFacts(
            tasks=self.context.rules.tasks,
            supplies=self.context.rules.supplies,
            items=self.context.rules.items,
            contents=self.context.rules.contents,
        )

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
            self.context.lifestyle_items,
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
        （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」），再派发，结果与组内先后无关：合成一单或一餐的两项
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
            [admission.selection_item(row, row["selected_action"], self._world_facts) for row in selection],
        )
        if merged_selection(actions, labels):
            targets = [item["object_id"] for item in selection]
            (changed, result) = self._dispatch_start_action(state, pending, actions[0], targets, selection)
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
            "submit_work": self._submit_work_action,
            "send_message": self._send_message_action,
            "report_status": self._report_status,
            "pause_activity": self._pause_activity,
            "conclude_activity": self._conclude_activity,
            "revise_day_plan": self._revise_day_plan,
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
        ways = [row["selected_action"]["label"] for row in selection]
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
        return same_moment.request_kind(pending.tool_name, actions, named, waiting, ways)

    @property
    def record_context(self) -> activity_control.RecordContext:
        """@brief 写活动记录所用的快照与标题投影。"""
        return activity_control.RecordContext(
            snapshot=partial(activity_snapshot, index=self.context.titles, lifestyles=self.context.lifestyle_items),
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
        if request.activity_type == ActivityKind.WORK:
            validate_work_duration(
                state, self.context.rules.task(request.target_id), pending.agent_id, request.planned_duration_seconds
            )
        travel_context = self._travel_start_context(state, agent, request)
        claims = resource_claims(request, data.get("content_kind"))
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
        if request.activity_type == ActivityKind.LIFESTYLE:
            item = next(item for item in self.context.lifestyle_items if item.activity_id == request.target_id)
            data["effects"] = dict(item.effects)
        activity.data.update(data)
        if request.activity_type == ActivityKind.WATCH:
            activity.media_position_seconds = data["start_position_seconds"]
        changed = _put_activity(changed_state, agent, activity)
        result_data = {"activity": activity_snapshot(activity, self.context.titles, self.context.lifestyle_items)}
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
            self.context.rules.validate_activity_location(
                agent, current.kind, current.target_id, admission.present_location(state, pending.agent_id)
            )
            self._require_available_resources(state, pending.agent_id, current.resource_claims, current.activity_id)
        if current.kind in {ActivityKind.CHAT, ActivityKind.CALL}:
            (agent, activity) = social_actions.social_activity_change(
                agent, current, request.operation.value, state.current_time
            )
            changed = _put_activity(state, agent, activity)
            result = ActionResult(
                action_id=pending.action_id,
                agent_id=agent.agent_id,
                status=ActionStatus.COMPLETED,
                occurred_at=state.current_time,
                activity_id=activity.activity_id,
                message="Activity changed",
                data={"activity": activity_snapshot(activity, self.context.titles, self.context.lifestyle_items)},
            )
            return (changed, result)
        if request.operation.value == "change_speed" and current.kind == ActivityKind.WATCH:
            remaining = current.planned_duration_seconds - current.elapsed_seconds
            content = self.context.rules.content(current.target_id)
            if current.media_position_seconds + remaining * request.playback_speed > content.duration_seconds:
                raise BusinessRuleError("CONTENT_END_EXCEEDED")
        (agent, activity) = change_activity(
            agent, current, request, state.current_time, self.context.scenario.activity_feedback_seconds
        )
        changed = _put_activity(state, agent, activity)
        result = ActionResult(
            action_id=pending.action_id,
            agent_id=agent.agent_id,
            status=ActionStatus.COMPLETED,
            occurred_at=state.current_time,
            activity_id=activity.activity_id,
            message="Activity changed",
            data={"activity": activity_snapshot(activity, self.context.titles, self.context.lifestyle_items)},
        )
        return (changed, result)

    def _consume(
        self, state: WorldState, pending: PendingAction, request: ConsumeRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 启动一次多件进食或单件使用活动（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「商务与进食」：时长取最长、效果累加）。

        占用先于物品状态核对（第五章「并行准入」的判定顺序）；途中没有可用的持有物。食用在开始时把本次份量从库存
        移入活动数据 opened（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「进食」）。
        """
        agent = state.agents[pending.agent_id]
        products = self.context.rules.items
        action = Action(f"consume:{request.use_mode}")
        matches = require_item_rules(
            self.context.semantics.catalog, products, (item.product_id for item in request.items), action
        )
        validate_meal_use(agent, products, request)
        duration = meal_duration(products, request)
        start = ActivityStartParams(
            activity_type=ActivityKind.CONSUME, planned_duration_seconds=duration, target_id=request.items[0].product_id
        )
        self._require_available_resources(state, pending.agent_id, resource_claims(start))
        if admission.en_route(state, pending.agent_id):
            raise guided("PRODUCT_NOT_HELD", "Held items cannot be used during travel.", "Use the item after arrival.")
        recipe = prepare_inventory_recipe(
            agent,
            RecipeContext(self.context.semantics.catalog, products, self.context.scenario.recipe_output_bindings),
            request, matches[0],
        )
        if recipe is None:
            validate_meal_inventory(agent, request)
        (agent, opened) = open_meal(agent, request)
        origin = _activity_origin(state, pending)
        (agent, activity) = start_activity(agent, start, state.current_time, origin, self.context.semantics.body)
        activity = schedule_feedback(activity, state.current_time, self.context.scenario.activity_feedback_seconds)
        efficiency = sleep_module.food_utilization_efficiency(agent.body.sleep_deficit_hours)
        food_rate = meal_food_rate(products, request) * efficiency
        activity.data.update(request=request.model_dump(mode="json"), consumed={}, food_energy_kcal_per_hour=food_rate)
        if recipe is not None:
            activity.data["inventory_recipe"] = recipe.payload()
        if opened:
            activity.data["opened"] = opened
        changed = _put_activity(state, agent, activity)
        result = ActionResult(
            action_id=pending.action_id, agent_id=pending.agent_id, status=ActionStatus.IN_PROGRESS,
            occurred_at=state.current_time, activity_id=activity.activity_id, message="Product use started",
            data={
                "activity": activity_snapshot(activity, self.context.titles, self.context.lifestyle_items),
                "category_matches": match_rows(matches),
            },
        )
        return (changed, result)

    def _submit_work(
        self, state: WorldState, pending: PendingAction, request: SubmitWorkRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 独立验收结构化工作并支付唯一报酬。"""
        submission = prepare_work_submission(
            state, self.context.semantics, self.context.corpus_root, pending.agent_id, request
        )
        if pending.work_review is None:
            raise ProtocolError("Eligible work submission has no recorded semantic review")
        review = WorkReviewDecision.model_validate(pending.work_review)
        prepared = prepare_work_review(submission, self.context.store.manifest["model_identity"]["requested_model"])
        if not review_matches(review, prepared):
            raise ProtocolError("Work review identity disagrees with the current submission")
        outcome = settle_work(state, submission, review.accepted)
        feedback = public_work_review(review, submission.source)
        agent_after = outcome.state.agents[pending.agent_id].model_copy(
            update={"body": apply_mood_pulse(outcome.state.agents[pending.agent_id].body, "work_review_passed")},
            deep=True,
        )
        agents = dict(outcome.state.agents)
        agents[pending.agent_id] = agent_after
        outcome_state = outcome.state.model_copy(update={"agents": agents}, deep=True)
        data = {
            "task_id": request.task_id,
            "payment_fen": outcome.payment_fen,
            "worked_seconds": outcome.worked_seconds,
            "effective_seconds": outcome.effective_seconds,
            "work_review": feedback,
            "category_matches": match_rows((self.context.semantics.contract_match(submission.task),)),
            "resource_changes": {
                f"account:{self.context.rules.task(request.task_id).employer_account_id}": -outcome.payment_fen,
                f"account:{state.agents[pending.agent_id].balance_account_id}": outcome.payment_fen,
            },
        }
        result = ActionResult(
            action_id=pending.action_id,
            agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED,
            occurred_at=state.current_time,
            message="Work accepted and paid",
            data=data,
        )
        return (outcome_state, result)

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
        送达（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」）。"""
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
        （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「人物之间的消息」）；联系人回复请求带双方往来与对方收到的物品
        （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「联系人往来」）。
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
                    state, self.context.store, self.context.scenario, sent.message, contact
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

    def _start_social_activity(
        self, state: WorldState, pending: PendingAction, request: StartSocialActivityRequest
    ) -> tuple[WorldState, ActionResult]:
        """@brief 受理交谈（共同在场；对方正以本人为伙伴交谈即接受）或通话（联系人）（第五章「交谈」）。"""
        agent = state.agents[pending.agent_id]
        origin = _activity_origin(state, pending)
        if request.kind == "chat":
            try:
                started = social_actions.start_chat(state, agent, request, origin, f"invite-{pending.action_id}")
            except ValueError as error:
                raise BusinessRuleError("CHAT_PARTNER_NOT_AVAILABLE") from error
        else:
            started = social_actions.start_call(
                agent, self._contact(request.partner_id, agent.agent_id), request, origin, state.current_time
            )
        self._require_available_resources(state, pending.agent_id, started.activity.resource_claims)
        if started.invite is not None:
            state = self._deliver_social_message(state, started.invite)
        if started.accepted is not None:
            state = state.model_copy(
                update={"activities": {**state.activities, started.accepted.activity_id: started.accepted}}
            )
        changed = _put_activity(state, started.agent, started.activity)
        result = ActionResult(
            action_id=pending.action_id,
            agent_id=pending.agent_id,
            status=ActionStatus.IN_PROGRESS,
            occurred_at=state.current_time,
            activity_id=started.activity.activity_id,
            message="Social activity started",
            data={"activity": activity_snapshot(started.activity, self.context.titles, self.context.lifestyle_items)},
        )
        return (changed, result)

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
        （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「人物之间的消息」）。
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

    def public_work_review(self, pending: PendingAction) -> dict:
        """@brief 从本次系统评审生成可随动作结果公开的人物反馈。"""
        if pending.work_review is None:
            raise ProtocolError("Rejected work submission has no recorded semantic review")
        request = SubmitWorkActionRequest.model_validate(pending.request)
        task = self.context.rules.task(self._task_id_for_name(pending, request.task_name))
        source = load_work_input(self.context.corpus_root, task)
        review = WorkReviewDecision.model_validate(pending.work_review)
        return public_work_review(review, source)


def _source_placeholder(row: dict) -> dict:
    """@brief 列出的商品目标写了 from（与标签同一家店的已在名称解析中换成 null）：换成同名的指名购买占位行（保留来源、去向
    与关联），由受理时按来源约束解析（包含该门店的渠道照常受理并记下 channel_id，其余以 SUPPLY_SOURCE_MISMATCH 或
    SOURCE_NOT_FOUND 拒绝，docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法 （b）、3.3 解析规则 2）；其
    余行原样。"""
    if row["kind"] != "supply" or row.get("from") is None:
        return row
    return {
        **named_supply_row(row["object"], row["selected_action"]["label"]),
        **{key: row[key] for key in routing.ROUTE_KEYS},
    }


def _with_names(result: ActionResult, pairs) -> ActionResult:
    """@brief 受理结果写上名称归一 details.resolved：（所写名称，解析出的名称）逐对中两者不同的记一条；都照写时结果不变
    （docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析 ）。"""
    resolved = [{"written": written, "as": name} for written, name in pairs if written != name]
    return result.model_copy(update={"data": resolution_data(result.data, resolved, ())}) if resolved else result


def _recall_names(observed: dict) -> list[str]:
    """@brief 回看可以写的名称：任务名、待办层写出的目标文字、本轮活动名与记忆层 self_notes 所写的活动名（note_labels）。"""
    return [
        *(row["task_name"] for row in observed["task_bindings"]),
        *(row["task_name"] for row in observed["goal_bindings"]),
        *(row["name"] for row in observed["activity_bindings"]),
        *(label for row in observed["activity_bindings"] for label in row["note_labels"]),
    ]


def _self_report(note: str) -> dict:
    """@brief 回看中的人物自述原文（是否经核验由系统层规则统一说明，不逐条标注）；没有自述时为空。"""
    return {"progress_note": note} if note else {}


def _remembered(observed: dict, activity: ActivityState | None) -> dict | None:
    """@brief 活动的最新自述仍在本轮记忆层 self_notes 中时的回看结果：只写它在记忆层中的活动名与「已在记忆层」，
    不写其他回看事实；否则为 None（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章「唯一出现原则」）。"""
    if activity is None or not activity.progress_note:
        return None
    names = {row["activity_id"]: row["name"] for row in observed["activity_bindings"]}
    label = memory_layer.noted_label(observed["memory_layer"], names[activity.activity_id], activity.progress_note)
    return None if label is None else {"activity": label, "progress_note": RECALL_IN_MEMORY_LAYER}




def _parallel_result_data(results: list[ActionResult]) -> dict:
    """@brief 逐项派发的合并结果：开始的活动快照（按请求顺序）与逐项结果（activity_results，与请求逐项对应；瞬时的取得
    是它的订单结果，给予是它的给予记录）；资金变动、品类命中、订单、给予与关联消息汇总到顶层（驾车票价、货款与另一项
    同时入账）。"""
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
    matches = [match for result in results for match in result.data.get("category_matches", [])]
    if matches:
        data["category_matches"] = matches
    orders = [order for result in results for order in result.data.get("orders", [])]
    if orders:
        data["orders"] = orders
    given = [item for result in results for item in result.data.get("given", [])]
    if given:
        data["given"] = given
    answering = next((result.data["answering"] for result in results if "answering" in result.data), None)
    if answering is not None:
        data["answering"] = answering
    return data
