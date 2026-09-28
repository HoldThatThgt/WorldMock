"""@brief 稳定工具面的活动动作处理器：开始、回看、播报（含恢复）、暂停与结束活动。

活动是生命周期单位（docs/dev/SPEC-time-and-energy.md 第七章）：每人两个名额；播报、暂停与结束逐项点名活动
（「做什么＋对象＋开始时刻」）；会话是一段地点停留或一段路，由系统随出发与到达切换（life.sessions）。
名额、待收尾、名称与控制记录的规则在 life.activity_control，本模块只做处理器接线。
"""

from __future__ import annotations

import logging

from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control, admission, sessions
from worldmock.life.content import restarts_from_beginning
from worldmock.life.guidance import guided
from worldmock.life.interaction_menu import activity_phrase, merged_selection, resolve_selection
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
    SessionTaskKind,
    StartActivityRequest,
    StartSocialActivityRequest,
    SubmitWorkRequest,
    SubmitWorkActionRequest,
    WorldState,
)
from worldmock.life.schedule import INTENT_ACTIVITY_FAMILIES
from worldmock.life.travel import route_for
from worldmock.life.social_actions import social_duration_seconds
from worldmock.life.work import work_resume_cap, work_start_blocker, work_start_seconds, work_totals

_LOGGER = logging.getLogger(__name__)
#: 观看、游戏内容与生活活动派生时长的上限：超过时按此受理并在结果写明计划时长（裁决 #19）。
DERIVED_DURATION_CAP_SECONDS = 86_400


class SessionActionMixin:
    """@brief 稳定工具面的活动动作：开始、回看、播报、暂停、结束都在此落地。"""

    @property
    def _place_names(self) -> dict[str, str]:
        """@brief 地点身份→场景中的可读地点名。"""
        return {item.location_id: item.name for item in self.scenario.locations}

    def _start_activity(self, state: WorldState, pending: PendingAction,
                        selection: list[dict] | None) -> tuple[WorldState, ActionResult]:
        """@brief 按「动词＋对象＋方式」解析好的冻结绑定整组准入后原子开始；新活动命名并归入会话。

        selection 是派发时解析的本轮绑定（_start_selection，菜单判定、受限轮复核与受理共用这一次解析），解析不出时
        以 ACTION_REQUEST_INVALID 拒绝。整组先按名额、目标在场与实际地点、出行方式、占用、物品状态的顺序判定
        （docs/dev/SPEC-time-and-energy.md 第五章「并行准入」），再派发，结果与组内先后无关：合成一单或一餐的两项
        一次派发，其余逐项派发，瞬时的取得（到店、远程下单）可与持续活动同一请求。
        """
        if selection is None:
            _LOGGER.error("Start activity selection is not offered in this round")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        actions = [row["selected_action"]["canonical_action"] for row in selection]
        labels = [row["selected_action"]["label"] for row in selection]
        names = self._control_names(pending)
        activity_control.require_start_admission(state, pending.agent_id, (actions, labels), names)
        admission.require_group(self._admission_context(state, pending.agent_id, names),
                                [admission.selection_item(row, row["selected_action"], self._world_facts)
                                 for row in selection])
        if merged_selection(actions, labels):
            targets = [item["object_id"] for item in selection]
            changed, result = self._dispatch_start_action(state, pending, actions[0], targets, selection)
            started = [(result.activity_id, selection)] if result.activity_id is not None else []
        else:
            changed, result, started = self._start_parallel_actions(state, pending, selection)
        return self._adopt_started(changed, pending, started, result)

    @property
    def _world_facts(self) -> admission.WorldFacts:
        """@brief 准入判定用的冻结目录：任务、供给、可持有物与内容。"""
        return admission.WorldFacts(tasks=self.rules.tasks, supplies=self.rules.supplies,
                                    items=self.rules.items, contents=self.rules.contents)

    def _admission_context(self, state: WorldState, agent_id: int,
                           names: dict[str, str] | None = None) -> admission.Context:
        """@brief 准入判定的世界与可读名称（地点名、本轮活动名）。"""
        return admission.Context(state=state, agent_id=agent_id, place_names=self._place_names,
                                 activity_names=names or {})

    def _start_selection(self, pending: PendingAction, request) -> list[dict] | None:
        """@brief start_activity 各项经本轮私有绑定解析的绑定行，菜单判定、受限轮复核与受理共用这一次解析；其他工具为空。

        目标重复或选择在本轮闭集内解析不出时为 None：不作菜单判定，待收尾要求之后由 start_activity 以
        ACTION_REQUEST_INVALID 拒绝。
        """
        return self._offered_selection(pending, request) if pending.tool_name == "start_activity" else []

    def _offered_selection(self, pending: PendingAction, request: StartActivityRequest) -> list[dict] | None:
        """@brief 在本轮冻结观察的私有绑定中逐项解析（对象，动词，方式）；目标重复或不在本轮闭集时为 None。"""
        if len(set(request.targets)) != len(request.targets):
            _LOGGER.error("Start activity targets repeat")
            return None
        return resolve_selection(self._frozen_observation(pending).get("interaction_bindings"),
                                 request.targets, request.actions, request.ways)

    def _start_parallel_actions(self, state: WorldState, pending: PendingAction, bindings: list[dict]
                                ) -> tuple[WorldState, ActionResult, list[tuple[str, list[dict]]]]:
        """@brief 对同一临时世界逐项派发，失败时丢弃整组状态与暂存事件。

        持续活动各自开始；瞬时的取得（到店、远程下单）当场成单，不产生活动（第五章「并行准入」：瞬时的生活动作
        不占名额）。返回合并结果与（活动身份，所属绑定）的逐项配对，供命名与归入会话。
        """
        changed = state
        results: list[ActionResult] = []
        started: list[tuple[str, list[dict]]] = []
        draft_count = len(self._pending_event_drafts)
        try:
            for binding in bindings:
                action = binding["selected_action"]["canonical_action"]
                changed, result = self._dispatch_start_action(
                    changed, pending, action, [binding["object_id"]], [binding])
                if result.activity_id is not None:
                    started.append((result.activity_id, [binding]))
                elif action != "purchase":
                    _LOGGER.error("Parallel action neither started an activity nor placed an order")
                    raise BusinessRuleError("ACTION_REQUEST_INVALID")
                results.append(result)
        except Exception:
            _LOGGER.error("Parallel activity start was rejected")
            del self._pending_event_drafts[draft_count:]
            raise
        status = ActionStatus.IN_PROGRESS if started else ActionStatus.COMPLETED
        message = "Activities started" if started else "Purchases accepted"
        return changed, ActionResult(
            action_id=pending.action_id, agent_id=pending.agent_id, status=status, occurred_at=state.current_time,
            message=message, data=_parallel_result_data(results)), started

    def _adopt_started(self, state: WorldState, pending: PendingAction, started: list[tuple[str, list[dict]]],
                       result: ActionResult) -> tuple[WorldState, ActionResult]:
        """@brief 新开始的活动按各自的绑定命名、关联任务或目标并归入当前会话；出行被受理即出发，同一请求的活动归入路上。

        :param started: （活动身份，所属绑定）逐项配对：合成一单或一餐时一项活动对应全部绑定，逐项派发时各对应自己的
            一项；瞬时的取得没有活动，不在其中。
        """
        if not started:
            return state, result
        activities = dict(state.activities)
        for activity_id, group in started:
            objects = "、".join(activity_control.object_display_name(item) for item in group)
            name = activity_control.activity_name(activity_phrase(group[0]["selected_action"]), objects,
                                                  state.current_time, self.scenario.timezone)
            activities[activity_id] = activities[activity_id].model_copy(
                update={"name": name, **self._task_link(state, pending.agent_id, activities[activity_id])},
                deep=True)
        changed = state.model_copy(update={"activities": activities})
        identities = [activity_id for activity_id, _ in started]
        travel = next((activities[item] for item in identities if activities[item].kind == ActivityKind.TRAVEL),
                      None)
        if travel is not None:
            changed = sessions.begin_transit(changed, pending.agent_id, travel.destination_id, self._place_names,
                                             opened_by_action_id=pending.action_id)
        current = sessions.current_session(changed, pending.agent_id)
        changed = sessions.assign(changed, current.session_id, identities)
        return changed, self._refreshed_result(changed, result)

    def _task_link(self, state: WorldState, agent_id: int, activity: ActivityState) -> dict:
        """@brief 活动关联的任务：工作任务或当日首个匹配目标（按既有活动家族规则），否则不关联。"""
        goal = self._plan_goal_for_activity(state, agent_id, activity.activity_id)
        if activity.kind == ActivityKind.WORK and activity.target_id in self.rules.tasks:
            return {"task_kind": SessionTaskKind.WORK_TASK, "task_ref": activity.target_id,
                    "plan_goal_intent_kind": goal}
        if goal is not None:
            return {"task_kind": SessionTaskKind.GOAL, "task_ref": goal, "plan_goal_intent_kind": goal}
        return {}

    def _refreshed_result(self, state: WorldState, result: ActionResult) -> ActionResult:
        """@brief 受理结果中的活动快照换成命名与归入会话之后的权威快照。"""
        data = dict(result.data)
        if result.activity_id is not None:
            data["activity"] = self._activity_snapshot(state.activities[result.activity_id])
        if "activities" in data:
            data["activities"] = [self._activity_snapshot(state.activities[row["activity_id"]])
                                  for row in data["activities"]]
        return result.model_copy(update={"data": data})

    def _submit_work_action(self, state: WorldState, pending: PendingAction,
                            request: SubmitWorkActionRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将人物可读任务名称映射为合同身份，交给独立工作验收路径；驾车时不能提交成果（第五章「并行准入」）。"""
        admission.require_hands_free(self._admission_context(state, pending.agent_id), "提交成果")
        task_id = self._task_id_for_name(pending, request.task_name)
        return self._submit_work(state, pending, SubmitWorkRequest(task_id=task_id,
                                                                   deliverable=request.deliverable))

    def _send_message_action(self, state: WorldState, pending: PendingAction,
                              request: SendMessageActionRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将人物可读收件人名称映射为联系人身份并发送正文；驾车时不能发消息，步行途中照常（第五章「并行准入」）。"""
        admission.require_hands_free(self._admission_context(state, pending.agent_id), "发消息")
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
        """@brief 读取当前决策的已提交观察事件，作为名称与报价的唯一解析来源；缺失或不唯一时 OBSERVATION_NOT_FOUND。"""
        events = self.store.read_agent_events("observation", pending.agent_id)
        matches = [event for event in events if event.decision_id == pending.decision_id
                   and event.occurred_at == pending.observed_at]
        if len(matches) != 1:
            _LOGGER.error("Decision observation is missing or ambiguous")
            raise BusinessRuleError("OBSERVATION_NOT_FOUND")
        return matches[0].data

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
        """@brief 从合同剩余量、内容进度或场景反馈间隔派生目标活动时长。

        工作要距截止与剩余合同都不少于 60 秒，否则以 ACTIVITY_DURATION_INVALID 返回原因与下一步（第八章「工作」）；
        观看已看完或剩余不足 60 秒时取全长（从头再看），否则取剩余（接着看）；观看与游戏内容超过 86400 秒按 86400
        秒受理（裁决 #19）。
        """
        if kind == ActivityKind.WORK:
            task = self.rules.task(target)
            worked, _ = work_totals(state, agent_id, target)
            blocker = work_start_blocker(task, worked, state.current_time)
            if blocker is not None:
                _LOGGER.error("Work start is closer than one minute to its deadline or contract end")
                raise guided("ACTIVITY_DURATION_INVALID", blocker, "提交已完成的部分")
            return work_start_seconds(task, worked, state.current_time)
        if kind == ActivityKind.WATCH:
            item = self.rules.content(target)
            progress = state.agents[agent_id].content_progress.get(target)
            position = progress.position_seconds if progress else 0
            restart = restarts_from_beginning(position, item.duration_seconds)
            duration = item.duration_seconds if restart else item.duration_seconds - position
        elif kind == ActivityKind.PLAY_GAME and target in self.rules.contents:
            duration = self.rules.content(target).duration_seconds
        else:
            duration = self.scenario.activity_feedback_seconds
        return _capped_duration(duration)

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
        duration = _capped_duration(item.typical_duration_minutes * 60)
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
        """@brief 启动 chat/call 动作对应的社交活动：时长由场景反馈间隔派生后截取（通话 600–3600 秒，交谈 60–7200 秒）。"""
        duration = social_duration_seconds(action, self.scenario.activity_feedback_seconds)
        social = StartSocialActivityRequest(kind=action, partner_id=target, duration_minutes=duration // 60)
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
        present = admission.present_location(state, pending.agent_id)
        valid = all(item.location_id == present and "pickup" in item.fulfillment_modes
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
        if any(mode not in products[item].use_modes for item in targets):
            _LOGGER.error("Selected objects cannot share a consumption mode")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        if mode == "use" and len(targets) != 1:
            _LOGGER.error("Only one held item can be used at a time")
            raise guided("ACTION_REQUEST_INVALID", "一次只能使用一件东西", "一次只选其中一件")
        items = tuple(ConsumeItem(product_id=item, quantity=1) for item in targets)
        consume = ConsumeRequest(items=items, use_mode=mode, duration_seconds=max(durations))
        return self._consume(state, pending, consume)

    def _plan_goal_for_activity(self, state: WorldState, agent_id: int,
                                activity_id: str | None) -> str | None:
        """@brief 按既有活动家族规则将新活动绑定到当日首个匹配目标。"""
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
        """@brief 只读回看任务、当日目标、活动或会话；结果随下一次观察进入 details.recall，不带身份。"""
        observed = self._frozen_observation(pending)
        name = request.task_name
        candidates = ([("task", row) for row in observed["task_bindings"] if row["task_name"] == name]
                      + [("goal", row) for row in observed["goal_bindings"] if row["task_name"] == name]
                      + [("activity", row) for row in observed["activity_bindings"] if row["name"] == name]
                      + [("session", row) for row in observed["session_bindings"] if row["name"] == name])
        if len(candidates) != 1:
            _LOGGER.error("Recalled task name is unavailable or ambiguous")
            raise BusinessRuleError("TASK_NOT_FOUND")
        kind, row = candidates[0]
        views = {"task": self._recall_task, "goal": self._recall_goal,
                 "activity": self._recall_activity, "session": self._recall_session}
        recall = views[kind](state, pending, row, observed)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Task summary retrieved", data={"recall": {"kind": kind, **recall}})
        return state, result

    def _recall_task(self, state: WorldState, pending: PendingAction, row: dict, observed: dict) -> dict:
        """@brief 任务回看：名称、截止与最近一条相关活动自述。"""
        del observed
        task = self.rules.task(row["task_id"])
        notes = [item for item in state.activities.values()
                 if item.agent_id == pending.agent_id and item.task_kind == SessionTaskKind.WORK_TASK
                 and item.task_ref == task.task_id and item.progress_note]
        latest = max(notes, key=lambda item: (item.note_updated_at or item.started_at, item.started_at),
                     default=None)
        view = {"name": row["task_name"],
                "due_at": sessions.moment_label(int(task.due_at.timestamp()), self.scenario.timezone)}
        return {**view, **_self_report(latest.progress_note if latest else "")}

    def _recall_goal(self, state: WorldState, pending: PendingAction, row: dict, observed: dict) -> dict:
        """@brief 目标回看：目标文字、意图与是否已推进。"""
        del observed
        plan = state.agents[pending.agent_id].day_plan
        goal = None if plan is None else next(
            (item for item in plan.goals if item.intent_kind == row["intent_kind"]), None)
        if goal is None:
            raise BusinessRuleError("PLAN_NOT_FOUND")
        return {"goal": goal.goal, "intent_kind": goal.intent_kind, "progressed": goal.progressed_at is not None}

    def _recall_activity(self, state: WorldState, pending: PendingAction, row: dict, observed: dict) -> dict:
        """@brief 活动回看：名称、地点、已做与有效时长、阶段与自述。

        地点取活动最近所属会话的说法：路上开始的活动（含出行本身）为「去<目的地>的路上」，停留中的为该地点；
        未归入会话的活动取开始地点。
        """
        del pending, observed
        activity = state.activities[row["activity_id"]]
        places = self._place_names
        place = (sessions.place_label(state.sessions[activity.session_ids[-1]], places) if activity.session_ids
                 else places[activity.location_id])
        view = {"name": row["name"], "place": place, "phase": activity_control.activity_phase(activity),
                "objective_progress": activity_control.objective_progress(activity)}
        return {**view, **_self_report(activity.progress_note)}

    def _recall_session(self, state: WorldState, pending: PendingAction, row: dict, observed: dict) -> dict:
        """@brief 会话回看：地点、起止与本段里的活动名。"""
        del pending
        session = state.sessions[row["session_id"]]
        names = {item["activity_id"]: item["name"] for item in observed["activity_bindings"]}
        timezone = self.scenario.timezone
        return {"place": sessions.place_label(session, self._place_names),
                "started_at": sessions.moment_label(session.started_at, timezone),
                "ended_at": (sessions.moment_label(session.ended_at, timezone)
                             if session.ended_at is not None else None),
                "activity_names": [names.get(item, state.activities[item].name) for item in session.activity_ids]}

    def _control_names(self, pending: PendingAction) -> dict[str, str]:
        """@brief 本轮冻结观察中人物所见的活动名（活动身份→名称），用于点名要先处理的活动。"""
        return {row["activity_id"]: row["name"]
                for row in self._frozen_observation(pending).get("activity_bindings", [])}

    def _activities_for_names(self, state: WorldState, pending: PendingAction,
                              names: list[str]) -> list[ActivityState]:
        """@brief 将一至两个本轮可见的活动名唯一解析为人物自己的权威活动。"""
        rows = self._frozen_observation(pending).get("activity_bindings", [])
        selected = []
        for name in names:
            matches = [row for row in rows if row["name"] == name]
            activity = state.activities.get(matches[0]["activity_id"]) if len(matches) == 1 else None
            if activity is None or activity.agent_id != pending.agent_id:
                _LOGGER.error("Activity name is unavailable or ambiguous")
                raise BusinessRuleError("ACTION_REQUEST_INVALID")
            selected.append(activity)
        return selected

    def _with_notes(self, state: WorldState, selected: list[ActivityState], summaries: list[str],
                    anchor: bool = False) -> WorldState:
        """@brief 把逐项自述写到活动（前一条推入历史），并同步到关联的当日目标。"""
        activities = dict(state.activities)
        for activity, summary in zip(selected, summaries, strict=True):
            activities[activity.activity_id] = activity_control.with_note(
                activities[activity.activity_id], summary, state.current_time, anchor)
        changed = state.model_copy(update={"activities": activities})
        return self._with_goal_summaries(changed, list(zip(selected, summaries, strict=True)))

    def _with_goal_summaries(self, state: WorldState,
                             pairs: list[tuple[ActivityState, str]]) -> WorldState:
        """@brief 将同一模型原文写入活动关联的当日目标，保留客观推进证据。"""
        agent = state.agents[pairs[0][0].agent_id]
        plan = agent.day_plan
        notes = {activity.plan_goal_intent_kind: summary for activity, summary in pairs
                 if activity.plan_goal_intent_kind is not None}
        if plan is None or not notes:
            return state
        goals = tuple(goal.model_copy(update={"progress_note": notes[goal.intent_kind]})
                      if goal.intent_kind in notes else goal for goal in plan.goals)
        changed_agent = agent.model_copy(update={"day_plan": plan.model_copy(update={"goals": goals})}, deep=True)
        return state.model_copy(update={"agents": {**state.agents, agent.agent_id: changed_agent}}, deep=True)

    def _control_result(self, state: WorldState, pending: PendingAction, message: str,
                        names: list[str], selected: list[ActivityState]) -> ActionResult:
        """@brief 控制动作的结果：逐项的可读活动名与活动身份（私有），供评估逐活动投影。"""
        return ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                            status=ActionStatus.COMPLETED, occurred_at=state.current_time, message=message,
                            data={"task": list(names), "activity_ids": [item.activity_id for item in selected]})

    def _report_status(self, state: WorldState, pending: PendingAction,
                        request: ReportStatusRequest) -> tuple[WorldState, ActionResult]:
        """@brief 逐项播报进行中活动并重置其播报锚点；点名暂停的活动即在其开始地点恢复它。

        每项先经 activity_control.control_refusal 判定（只接受进行中或暂停的活动；恢复只在开始地点，且与进行中
        活动的占用不冲突），与工具层、待办层的可用性一字不差。
        """
        selected = self._activities_for_names(state, pending, request.task)
        names = self._control_names(pending)
        changed = state
        for activity in selected:
            current = changed.activities[activity.activity_id]
            refusal = activity_control.control_refusal(
                self._admission_context(changed, pending.agent_id, names), "report_status", current)
            if refusal is not None:
                raise refusal.error()
            if current.status == ActivityStatus.PAUSED:
                changed = self._resume_at_start_location(changed, pending, current)
        changed = self._with_notes(changed, selected, request.summary, anchor=True)
        return changed, self._control_result(state, pending, "Activity status reported", request.task, selected)

    def _resume_at_start_location(self, state: WorldState, pending: PendingAction,
                                  activity: ActivityState) -> WorldState:
        """@brief 在开始地点恢复暂停的活动（地点与占用已由 control_refusal 判定），并归入那里的当前停留会话。

        工作恢复不越过合同截止（上限见 _resume_cap）。
        """
        changed, _ = self._change(state, pending, ChangeActivityRequest(
            activity_id=activity.activity_id, operation=ActivityChange.RESUME,
            resume_cap_seconds=self._resume_cap(state, pending.agent_id, activity)))
        current = sessions.current_session(changed, pending.agent_id)
        return sessions.assign(changed, current.session_id, [activity.activity_id])

    def _resume_cap(self, state: WorldState, agent_id: int, activity: ActivityState) -> int | None:
        """@brief 恢复的时长上限：工作取 min(距截止, 合同上限减已累计)，其余活动没有上限（规格第六章）。

        开工与恢复使用同一截止约束：距截止或剩余合同不足 60 秒时以 ACTIVITY_DURATION_INVALID 返回原因与下一步。
        """
        if activity.kind != ActivityKind.WORK:
            return None
        task = self.rules.task(activity.target_id)
        worked, _ = work_totals(state, agent_id, task.task_id)
        blocker = work_start_blocker(task, worked, state.current_time)
        if blocker is not None:
            _LOGGER.error("Work resume is closer than one minute to its deadline or contract end")
            raise guided("ACTIVITY_DURATION_INVALID", blocker, "提交已完成的部分")
        return work_resume_cap(task, worked, state.current_time)

    def _pause_activity(self, state: WorldState, pending: PendingAction,
                         request: PauseActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 暂停所选进行中活动（仍占名额）并写入自述；路上的活动、交谈与通话不能暂停。

        每项先经 activity_control.control_refusal 判定，与工具层的可用性一字不差。
        """
        selected = self._activities_for_names(state, pending, request.task)
        context = self._admission_context(state, pending.agent_id, self._control_names(pending))
        for activity in selected:
            refusal = activity_control.control_refusal(context, "pause_activity", activity)
            if refusal is not None:
                raise refusal.error()
        changed = state
        for activity in selected:
            changed, _ = self._change(changed, pending, ChangeActivityRequest(
                activity_id=activity.activity_id, operation=ActivityChange.PAUSE))
        changed = self._with_notes(changed, selected, request.summary)
        return changed, self._control_result(state, pending, "Activity paused", request.task, selected)

    def _conclude_activity(self, state: WorldState, pending: PendingAction,
                            request: ConcludeActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 结束所选活动并写下总结，释放名额；途中结束出行时路上的其他活动随之结束。

        进行中或暂停的活动先取消（agent_cancelled）再收尾；待收尾活动不改终态，只写总结并置 concluded_at。
        有待收尾活动而本次没有点名任何一项时以 ACTIVITY_CONCLUSION_REQUIRED 拒绝。记忆由宿主写入，环境只校验形状，
        并在结果中登记私有来源 memory_sources。
        """
        selected = self._activities_for_names(state, pending, request.task)
        if not all(activity_control.occupies_slot(item) for item in selected):
            _LOGGER.error("Concluded activity is not active, paused or awaiting conclusion")
            raise BusinessRuleError("ACTION_REQUEST_INVALID")
        waiting = activity_control.pending_conclusions(state, pending.agent_id)
        if waiting and not {item.activity_id for item in waiting} & {item.activity_id for item in selected}:
            raise activity_control.conclusion_required(waiting, self._control_names(pending))
        changed, departed = state, False
        for activity in selected:
            if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                departed = departed or activity.kind == ActivityKind.TRAVEL and sessions.on_road(state, activity)
                changed, _ = self._change(changed, pending, ChangeActivityRequest(
                    activity_id=activity.activity_id, operation=ActivityChange.CANCEL))
        activities = dict(changed.activities)
        for activity, summary in zip(selected, request.summary, strict=True):
            activities[activity.activity_id] = activity_control.concluded(
                activities[activity.activity_id], summary, state.current_time)
        changed = self._with_goal_summaries(changed.model_copy(update={"activities": activities}),
                                            list(zip(selected, request.summary, strict=True)))
        if departed:
            changed, _ = sessions.end_transit(changed, pending.agent_id, self._place_names,
                                              opened_by_action_id=pending.action_id)
        result = self._control_result(state, pending, "Activities concluded", request.task, selected)
        sources = self._memory_sources(pending, request.task, selected)
        return changed, result.model_copy(update={"data": {**result.data, "memory_sources": sources}})

    def _memory_sources(self, pending: PendingAction, names: list[str],
                        selected: list[ActivityState]) -> list[dict]:
        """@brief 系统登记的记忆来源：逐项的活动、所属会话与本轮观察中该活动的已核验经历（模型不填身份）。"""
        experiences = self._frozen_observation(pending).get("verified_experiences", [])
        return [{"task": name, "activity_id": activity.activity_id,
                 "session_id": activity.session_ids[-1] if activity.session_ids else None,
                 "verified_event_ids": list(dict.fromkeys(row["source_event_id"] for row in experiences
                                                          if row["activity_id"] == activity.activity_id))}
                for name, activity in zip(names, selected, strict=True)]


def _self_report(note: str) -> dict:
    """@brief 回看中的人物自述：有自述时带「你自己记下的，未经核验」标注。"""
    if not note:
        return {}
    return {"progress_note": note, "subjective_status": activity_control.SELF_REPORT_MARK}


def _capped_duration(duration: int) -> int:
    """@brief 非出行活动的派生时长：不足 60 秒以 ACTIVITY_DURATION_INVALID 拒绝，超过 86400 秒按 86400 秒受理。"""
    if duration < 60:
        _LOGGER.error("Derived activity duration is below the domain minimum")
        raise BusinessRuleError("ACTIVITY_DURATION_INVALID")
    return min(duration, DERIVED_DURATION_CAP_SECONDS)


def _parallel_result_data(results: list[ActionResult]) -> dict:
    """@brief 逐项派发的合并结果：开始的活动快照（按请求顺序）与逐项结果（activity_results，与请求逐项对应；瞬时的取得
    是它的订单结果）；资金变动、品类命中与订单汇总到顶层（驾车票价、货款与另一项同时入账）。"""
    changes: dict[str, int] = {}
    for result in results:
        for account, amount in result.data.get("resource_changes", {}).items():
            changes[account] = changes.get(account, 0) + amount
    data = {"activities": [result.data["activity"] for result in results if "activity" in result.data],
            "activity_results": [result.data for result in results]}
    if changes:
        data["resource_changes"] = changes
    matches = [match for result in results for match in result.data.get("category_matches", [])]
    if matches:
        data["category_matches"] = matches
    orders = [order for result in results for order in result.data.get("orders", [])]
    if orders:
        data["orders"] = orders
    return data
