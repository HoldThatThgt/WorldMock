"""@brief 开始组：一个决策里一条或两条开始调用的展开、整组核验、固定次序的派发与逐动作结果。

一条 start_main_activity 与一条 start_side_activity 可以登记在同一个决策里。整组在同一个世界副本上按固定次序核验，
任何一项不成立两条都不开始、不扣款、不消耗随机流，两条动作得到同一份拒绝；全部成立后副活动与主活动同一时刻开始，
每条动作恰好开始一项活动。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic import ValidationError

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.footprints.models import EventDraft
from worldmock.life import activity_control, admission, same_moment, sessions
from worldmock.life.action_contract import ACTION_LABELS, START_TOOLS, TRAVEL_ACTION_MODES, TRAVEL_ACTION_NAMES
from worldmock.life.action_evidence import activity_orders, start_orders
from worldmock.life.activities import ActivityStartParams, schedule_feedback, start_activity
from worldmock.life.activity_titles import activity_snapshot, title_index
from worldmock.life.business_feedback import call_items
from worldmock.life.engine_context import EngineContext
from worldmock.life.error_contract import HOST_NAME_CODES, bind_report, host_defaults, items_fix
from worldmock.life.guidance import Refusal, refusal_for_code, refusal_from_error
from worldmock.life.interact import InteractivityRuntime
from worldmock.life.models import (ActionResult, ActionStatus, ActivityKind, PendingAction, REQUEST_MODELS,
                                   TravelMode, WorldState)
from worldmock.life.name_resolution import name_book, resolution_data, resolve_start
from worldmock.life.object_labels import resolve_name
from worldmock.life.purchase_offers import app_goods
from worldmock.life.request_errors import argument_report
from worldmock.life.storefront_runtime import StorefrontRuntime
from worldmock.life.travel import pay_travel_fare, travel_activity_data
from worldmock.life.weather import current_weather_factor
from worldmock.life.world_ops import _activity_origin, _put_activity, place_names

_LOGGER = logging.getLogger(__name__)
#: 开始组有两条调用时追加在原因后的一句（前面一个空格）。
GROUP_REJECTION_SUFFIX = " Neither start request was started."
#: 出行方式身份到固定出行动作。
_METHOD_ACTIONS = {method: action for action, method in TRAVEL_ACTION_MODES.items()}


@dataclass(frozen=True, slots=True)
class _Started:
    """@brief 组内一条名称已解析的开始调用：它在本决策已登记动作中的位置、登记的动作与解析结果。"""

    index: int
    pending: PendingAction
    call: admission.Call
    resolved: tuple[dict, ...]


class _Refused(Exception):
    """@brief 整组第一条不成立的核验：拒绝与负责调用的位置，由派发入口换成组拒绝。"""

    def __init__(self, refusal: Refusal, responsible: Sequence[int]):
        """@brief 记下拒绝与负责的调用位置。"""
        super().__init__(refusal.code)
        self.refusal, self.responsible = refusal, tuple(responsible)


def frozen_observation(context: EngineContext, pending: PendingAction) -> dict:
    """@brief 读取当前决策的已提交观察事件，作为名称与报价的唯一解析来源；缺失或不唯一时 OBSERVATION_NOT_FOUND。"""
    matches = context.store.read_decision_observations(pending.agent_id, pending.decision_id, pending.observed_at)
    if len(matches) != 1:
        _LOGGER.error("Decision observation is missing or ambiguous")
        raise BusinessRuleError("OBSERVATION_NOT_FOUND")
    return matches[0].data


def group_rejection(group: Sequence[PendingAction], refusal: Refusal,
                    responsible: Sequence[int]) -> PreconditionError:
    """@brief 整组只给一份拒绝：两条调用时原因追加一句；没有自带明细的业务拒绝为每条负责的调用写一条明细。

    :param responsible: 负责的调用在本决策已登记动作中的位置；没有问题的调用不出明细。
    """
    reason = refusal.reason + (GROUP_REJECTION_SUFFIX if len(group) == 2 else "")
    items = refusal.items or call_items(
        [(index, group[index].request.get("target")) for index in responsible], refusal.code, refusal.reason)
    return replace(refusal, reason=reason, items=tuple(items)).error()


class StartGroups:
    """@brief 开始组只操作当前决策的世界副本；取件的收货草稿与出行车费按动作暂存给决策协调。"""

    def __init__(self, context: EngineContext, storefront: StorefrontRuntime, drafts: list[EventDraft],
                 resource_changes: dict[str, dict[str, int]]):
        """@brief 绑定状态、购买职责与动作处理器的两个暂存容器。"""
        self.context, self.storefront = context, storefront
        self.event_drafts, self.resource_changes = drafts, resource_changes

    def dispatch(self, state: WorldState, group: Sequence[PendingAction]) -> tuple[WorldState, list[ActionResult]]:
        """@brief 整组核验全部成立后按固定次序派发，返回世界副本与各动作的受理结果（按登记次序）。

        :raises PreconditionError: 任何一项不成立时的组拒绝；调用方丢弃世界副本，两条动作写同一份拒绝。
        """
        everyone = tuple(range(len(group)))
        try:
            return self._start_all(state, self._verified(state, group))
        except _Refused as refused:
            _LOGGER.error("Start group rejected: %s", refused.refusal.code)
            raise group_rejection(group, refused.refusal, refused.responsible) from refused
        except BusinessRuleError as error:
            _LOGGER.error("Start group rejected by a world rule: %s", error)
            raise group_rejection(group, refusal_from_error(error), everyone) from error

    def _verified(self, state: WorldState, group: Sequence[PendingAction]) -> list[_Started]:
        """@brief 按固定次序核验整组：请求形状、待收尾、名称、指名商品、同刻阶段，再是槽位、睡眠、对象条件与余额。"""
        everyone, agent_id = tuple(range(len(group))), group[0].agent_id
        requests = self._requests(group)
        observation = frozen_observation(self.context, group[0])
        names = {row["activity_id"]: row["name"] for row in observation.get("activity_bindings", [])}
        waiting = activity_control.pending_conclusions(state, agent_id)
        if waiting:
            raise _Refused(activity_control.conclusion_refusal(waiting, names), everyone)
        started = [self._named_goods(item) for item in self._resolved(group, requests, observation)]
        stage = same_moment.stage_refusal(state.agents[agent_id].same_moment_stage, "start")
        if stage is not None:
            raise _Refused(stage, everyone)
        context = admission.Context(state, agent_id, place_names(self.context.catalog), names, self.context.inputs)
        blocked = admission.group_refusal(context, [item.call for item in started])
        if blocked is not None:
            raise _Refused(blocked.refusal, [item.index for item in started if item.call.tool in blocked.tools])
        return started

    def _requests(self, group: Sequence[PendingAction]) -> list:
        """@brief 逐条用请求模型复核，两条调用的全部形状问题合并成一次 ACTION_REQUEST_INVALID。"""
        requests, items = [], []
        for index, pending in enumerate(group):
            model = REQUEST_MODELS[pending.tool_name]
            try:
                requests.append(model.model_validate(pending.request))
            except ValidationError as error:
                _LOGGER.error("Start request validation rejected: %s", pending.tool_name)
                report = argument_report(pending.tool_name, error, model, pending.request, "ACTION_REQUEST_INVALID")
                items.extend(bind_report(report, index).items)
        if items:
            reason = "; ".join(item.why for item in items)
            fix = host_defaults("ARGUMENTS_INVALID")[1]
            raise _Refused(Refusal("ACTION_REQUEST_INVALID", reason, fix=fix, items=tuple(items)), ())
        return requests

    def _resolved(self, group: Sequence[PendingAction], requests: list, observation: dict) -> list[_Started]:
        """@brief 与宿主共用同一份冻结观察上的名称解析，全部名称问题合并成一次 ACTION_REQUEST_INVALID。"""
        book, started, items = name_book(observation), [], []
        for index, (pending, request) in enumerate(zip(group, requests, strict=True)):
            outcome = resolve_start(book, pending.tool_name, request)
            root = ("tool_calls", index, "function", "arguments")
            items.extend(replace(failure.item, path=(*root, *failure.item.path)) for failure in outcome.failures)
            call = admission.Call(pending.tool_name, request.action, outcome.rows)
            started.append(_Started(index, pending, call, outcome.resolved))
        if items:
            _LOGGER.error("Start request names are invalid")
            fixes = {code: host_defaults(code)[1] for code in HOST_NAME_CODES}
            refusal = Refusal("ACTION_REQUEST_INVALID", "; ".join(item.why for item in items),
                              fix=items_fix(items, len(items), fixes) or "", items=tuple(items))
            raise _Refused(refusal, ())
        return started

    def _named_goods(self, item: _Started) -> _Started:
        """@brief 指名商品：购买数组里未展示的名称在全量商品中找唯一商品，找不到即 SUPPLY_NOT_FOUND。"""
        if item.call.action != "purchase":
            return item
        goods, rows = tuple(self.context.catalog.goods.values()), []
        for row in item.call.rows:
            if row["object_id"] is None:
                canonical = resolve_name(row["object"], [entry.name for entry in goods]).name
                found = [entry for entry in goods if entry.name == canonical]
                if len(found) != 1:
                    _LOGGER.error("Named goods are unknown or ambiguous")
                    raise _Refused(refusal_for_code("SUPPLY_NOT_FOUND"), (item.index,))
                row = {**row, "object_id": found[0].goods_id}
            rows.append(row)
        return replace(item, call=replace(item.call, rows=tuple(rows)))

    def _start_all(self, state: WorldState, started: list[_Started]) -> tuple[WorldState, list[ActionResult]]:
        """@brief 派发次序固定，与两条调用在回复里的先后无关：出发先结束停留会话、开始路上会话，再开始副调用的
        活动，再开始主调用的活动，最后把新活动归入当前会话。出行的车费在此刻支付，购买只冻结商品履约记录。"""
        main = next((item for item in started if item.call.tool == START_TOOLS[0]), None)
        side = next((item for item in started if item.call.tool == START_TOOLS[1]), None)
        agent_id, changed = started[0].pending.agent_id, state
        if main is not None and main.call.action == "travel":
            changed = sessions.begin_transit(changed, agent_id, main.call.rows[0]["object_id"],
                                             place_names(self.context.catalog),
                                             opened_by_action_id=main.pending.action_id)
        for item in (side, main):
            if item is not None:
                changed = self._start(changed, item)
        created = [key for key in changed.activities if key not in state.activities]
        session = sessions.current_session(changed, agent_id)
        changed = sessions.assign(changed, session.session_id, created)
        return changed, [self._result(state, changed, item) for item in started]

    def _start(self, state: WorldState, item: _Started) -> WorldState:
        """@brief 一条调用恰好开始一项活动：出行、睡眠、购买或 interact。"""
        action, pending, rows = item.call.action, item.pending, item.call.rows
        if action == "travel":
            return self._start_travel(state, pending, rows[0])
        if action == "sleep":
            seconds = rows[0]["minutes"] * 60
            data = {"declared_duration_seconds": seconds, "oversleep_seconds": 0}
            return self._named(state, pending, ActivityStartParams(action, seconds, data=data), ACTION_LABELS[action])
        if action == "purchase":
            return self.storefront.prepare_purchase(state, pending, item.call.goods_ids)[0]
        changed, _, drafts = InteractivityRuntime(self.context).start(state, pending, rows[0])
        self.event_drafts.extend(drafts)
        return changed

    def _start_travel(self, state: WorldState, pending: PendingAction, row: dict) -> WorldState:
        """@brief 受理行程冻结天气、路线和车费，出发时只付款一次。"""
        method = row["selected_action"]["travel_method_id"]
        action, factor = _METHOD_ACTIONS[method], current_weather_factor(state)
        places = place_names(self.context.catalog)
        trip = admission.planned_trip(admission.Context(state, pending.agent_id, places, {}, self.context.inputs), row)
        changed, receipt = pay_travel_fare(state, pending.agent_id, trip)
        params = ActivityStartParams(action, trip.duration_seconds, row["object_id"], TravelMode(method),
                                     travel_activity_data(trip, factor))
        self.resource_changes[pending.action_id] = dict(receipt["resource_changes"])
        return self._named(changed, pending, params, f"{TRAVEL_ACTION_NAMES[action]}前往{row['object']}")

    def _named(self, state: WorldState, pending: PendingAction, params: ActivityStartParams,
               label: str) -> WorldState:
        """@brief 登记一项引擎确定的活动并使用真实开始时刻命名。"""
        agent, activity = start_activity(state.agents[pending.agent_id], params, state.current_time,
                                         _activity_origin(state, pending), self.context.body)
        name = activity_control.activity_name(label, "", state.current_time, state.timezone)
        activity = schedule_feedback(activity.model_copy(update={"name": name}), state.current_time,
                                     self.context.config.activity_feedback_seconds)
        return _put_activity(state, agent, activity)

    def _result(self, before: WorldState, changed: WorldState, item: _Started) -> ActionResult:
        """@brief 一条动作的受理结果：只含它开始的那项活动、它创建的商品履约记录与它在驿站领取的记录。"""
        pending = item.pending
        activity = next(row for key, row in changed.activities.items()
                        if key not in before.activities and row.origin_action_id == pending.action_id)
        app_id = activity.data["app_id"] if activity.kind == ActivityKind.INTERACT else None
        located = activity.kind == ActivityKind.INTERACT and app_id is None
        # 只有在地点做事的那条动作会在驿站取件；同组的副调用不带主调用领取的记录。
        orders = start_orders(before, changed, [activity]) if located else activity_orders(changed, [activity])
        data = {"activities": [activity_snapshot(activity, title_index(self.context.catalog))], "orders": orders}
        if app_id is not None:
            data["goods"] = app_goods(self.context.inputs, changed, pending.agent_id, app_id)
        return ActionResult(action_id=pending.action_id, agent_id=pending.agent_id, status=ActionStatus.IN_PROGRESS,
                            occurred_at=before.current_time, activity_id=activity.activity_id,
                            message="Activities started", data=resolution_data(data, item.resolved, ()))
