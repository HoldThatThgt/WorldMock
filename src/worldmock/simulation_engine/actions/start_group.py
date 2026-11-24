"""@brief 开始组：沙盒与屏障重放期间开始调用的展开、准入、固定次序派发与结果构造。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from worldmock.foundation.errors import BusinessRuleError, PreconditionError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.projections.commerce.action_orders import activity_orders, start_orders
from worldmock.resource_engine.catalog.travel_methods import travel_method
from worldmock.simulation_engine.actions import admission
from worldmock.simulation_engine.actions.business_feedback import call_items
from worldmock.simulation_engine.actions.name_resolution import name_book, resolution_data, resolve_start
from worldmock.simulation_engine.activities.content.interact import InteractivityRuntime
from worldmock.simulation_engine.activities.lifecycle import activity_control, sessions
from worldmock.simulation_engine.activities.lifecycle.activities import (
    ActivityStartParams,
    schedule_feedback,
    start_activity,
)
from worldmock.simulation_engine.activities.travel.travel import pay_travel_fare, travel_activity_data
from worldmock.simulation_engine.body.sleep_plan import assumed_sleep, oversleep_seconds, sleep_seconds_in_window
from worldmock.simulation_engine.commerce.purchase.order_apps import preferred_app_id
from worldmock.simulation_engine.commerce.purchase.storefront_runtime import StorefrontRuntime
from worldmock.simulation_engine.observation.targets.object_labels import resolve_name
from worldmock.simulation_engine.observation.wording.activity_titles import activity_snapshot, title_index
from worldmock.simulation_engine.observation.wording.guidance import Refusal, refusal_for_code, refusal_from_error
from worldmock.simulation_engine.scheduling.clock.weather import current_weather_factor
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.simulation_engine.world.world_updates import _activity_origin, _put_activity, place_names
from worldmock.state.decisions.results import ActionResult
from worldmock.state.interaction.actions import START_TOOLS
from worldmock.state.interaction.errors import bind_report, host_defaults, items_fix
from worldmock.state.interaction.request_errors import argument_report
from worldmock.state.interaction.requests import REQUEST_MODELS
from worldmock.state.observation.labels import ACTION_LABELS
from worldmock.state.world.kinds import ActionStatus, ActivityKind, ActivityStatus
from worldmock.state.world.world import WorldState
from worldmock.exchange_engine.conditions.rejection_codes import HOST_NAME_CODES

_LOGGER = logging.getLogger(__name__)
#: 开始组有两条调用时追加在原因后的一句（前面一个空格）。
GROUP_REJECTION_SUFFIX = " Neither start request was started."


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """@brief 单次世界工具调用在沙盒与屏障提交之间传递的完整不可变上下文。"""

    #: 调用方人物编号。
    agent_id: int
    #: 所属唤醒身份。
    wakeup_id: str
    #: 动作身份。
    action_id: str
    #: 工具名称。
    tool_name: str
    #: 工具参数字典（不含 note）。
    request: dict[str, Any]
    #: 规范化请求摘要。
    request_sha256: str
    #: 对应的 action_requested 事件身份。
    requested_event_id: str
    #: 本次唤醒冻结的观察映射。
    observation: Mapping[str, Any]
    #: 模型附带自注，可为空。
    note: str | None = None


@dataclass(frozen=True, slots=True)
class _Started:
    """@brief 组内一条名称已解析的开始调用：位置、调用上下文与解析结果。"""

    index: int
    pending: ToolInvocation
    call: admission.Call
    resolved: tuple[dict, ...]


class _Refused(Exception):
    """@brief 整组第一条不成立的核验：拒绝与负责调用的位置，由派发入口换成组拒绝。"""

    def __init__(self, refusal: Refusal, responsible: Sequence[int]):
        """@brief 记下拒绝与负责的调用位置。"""
        super().__init__(refusal.code)
        self.refusal, self.responsible = refusal, tuple(responsible)


def frozen_observation(pending: ToolInvocation) -> Mapping[str, Any]:
    """@brief 取得当前唤醒随调用携带的冻结观察；缺失时以 OBSERVATION_NOT_FOUND 拒绝。"""
    if not isinstance(pending.observation, Mapping) or "agent_id" not in pending.observation:
        _LOGGER.error("Wakeup observation is missing for action %s", pending.action_id)
        raise BusinessRuleError("OBSERVATION_NOT_FOUND")
    return pending.observation


def group_rejection(group: Sequence[ToolInvocation], refusal: Refusal,
                    responsible: Sequence[int]) -> PreconditionError:
    """@brief 整组只给一份拒绝：两条调用时原因追加一句；没有自带明细的业务拒绝为每条负责调用写一条明细。"""
    reason = refusal.reason + (GROUP_REJECTION_SUFFIX if len(group) == 2 else "")
    items = refusal.items or call_items(
        [(index, group[index].request.get("target")) for index in responsible], refusal.code, refusal.reason)
    return replace(refusal, reason=reason, items=tuple(items)).error()


class StartGroups:
    """@brief 开始组只操作当前沙盒或重放的世界副本；取件收货与车费按动作暂存。"""

    def __init__(self, context: EngineContext, storefront: StorefrontRuntime, drafts: list[EventDraft],
                 resource_changes: dict[str, dict[str, int]]):
        """@brief 绑定状态、购买职责与动作处理器的两个暂存容器。"""
        self.context, self.storefront = context, storefront
        self.event_drafts, self.resource_changes = drafts, resource_changes

    def dispatch(self, state: WorldState, group: Sequence[ToolInvocation]) -> tuple[WorldState, list[ActionResult]]:
        """@brief 整组核验全部成立后按固定次序派发，返回世界副本与各动作的受理结果。"""
        everyone = tuple(range(len(group)))
        try:
            return self._start_all(state, self._verified(state, group))
        except _Refused as refused:
            _LOGGER.error("Start group rejected: %s", refused.refusal.code)
            raise group_rejection(group, refused.refusal, refused.responsible) from refused
        except BusinessRuleError as error:
            _LOGGER.error("Start group rejected by a world rule: %s", error)
            raise group_rejection(group, refusal_from_error(error), everyone) from error

    def _verified(self, state: WorldState, group: Sequence[ToolInvocation]) -> list[_Started]:
        """@brief 按固定次序核验整组：请求形状、待收尾、名称、指名商品，再是槽位、睡眠、对象条件与余额。"""
        everyone, agent_id = tuple(range(len(group))), group[0].agent_id
        requests = self._requests(group)
        observation = frozen_observation(group[0])
        names = {row["activity_id"]: row["name"] for row in observation.get("activity_bindings", [])}
        open_activities = activity_control.open_activities(state, agent_id)
        waiting = activity_control.pending_conclusions(open_activities)
        if waiting:
            raise _Refused(activity_control.conclusion_refusal(waiting, names), everyone)
        started = [self._named_goods(item) for item in self._resolved(group, requests, observation)]
        context = admission.Context(state, agent_id, open_activities, place_names(self.context.catalog),
                                    names, self.context.inputs)
        blocked = admission.group_refusal(context, [item.call for item in started])
        if blocked is not None:
            raise _Refused(blocked.refusal, [item.index for item in started if item.call.tool in blocked.tools])
        return started

    def _requests(self, group: Sequence[ToolInvocation]) -> list:
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

    def _resolved(self, group: Sequence[ToolInvocation], requests: list, observation: Mapping) -> list[_Started]:
        """@brief 在冻结观察上执行名称解析，并按 prefer 识别购买意向 app。"""
        book, started, items = name_book(observation), [], []
        for index, (pending, request) in enumerate(zip(group, requests, strict=True)):
            outcome = resolve_start(book, pending.tool_name, request)
            root = ("tool_calls", index, "function", "arguments")
            items.extend(replace(failure.item, path=(*root, *failure.item.path)) for failure in outcome.failures)
            prefer = preferred_app_id(self.context.catalog, request.prefer) if request.action == "purchase" else None
            call = admission.Call(pending.tool_name, request.action, outcome.rows, prefer)
            started.append(_Started(index, pending, call, outcome.resolved))
        if items:
            _LOGGER.error("Start request names are invalid")
            fixes = {code: host_defaults(code)[1] for code in HOST_NAME_CODES}
            code = items[0].error_code if len(group) == 1 else "ACTION_REQUEST_INVALID"
            refusal = Refusal(code, "; ".join(item.why for item in items),
                              fix=items_fix(items, len(items), fixes) or "", items=tuple(items))
            raise _Refused(refusal, ())
        return started

    def _named_goods(self, item: _Started) -> _Started:
        """@brief 购买数组里未展示的商品在全量目录中按唯一名称解析。"""
        if item.call.action != "purchase":
            return item
        goods, rows = tuple(self.context.catalog.goods.values()), []
        for row in item.call.rows:
            if row["kind"] == "named_supply":
                canonical = resolve_name(row["object"], [entry.name for entry in goods]).name
                found = [entry for entry in goods if entry.name == canonical]
                if len(found) != 1:
                    _LOGGER.error("Named goods are unknown or ambiguous")
                    raise _Refused(refusal_for_code("SUPPLY_NOT_FOUND"), (item.index,))
                row = {**row, "object_id": found[0].goods_id}
            rows.append(row)
        return replace(item, call=replace(item.call, rows=tuple(rows)))

    def _start_all(self, state: WorldState, started: list[_Started]) -> tuple[WorldState, list[ActionResult]]:
        """@brief 按固定次序开始副活动与主活动并归入当前停留或路上会话。"""
        main = next((item for item in started if item.call.tool == START_TOOLS[0]), None)
        side = next((item for item in started if item.call.tool == START_TOOLS[1]), None)
        agent_id, changed = started[0].pending.agent_id, state
        stay_location_id = admission.present_location(state, agent_id)
        if main is not None and main.call.action == "travel":
            changed = sessions.begin_transit(changed, agent_id, main.call.rows[0]["object_id"],
                                             place_names(self.context.catalog),
                                             opened_by_action_id=main.pending.action_id)
        for item in (side, main):
            if item is not None:
                changed = self._start(changed, item, stay_location_id)
        created = [key for key in changed.activities if key not in state.activities]
        session = sessions.current_session(changed, agent_id)
        changed = sessions.assign(changed, session.session_id, created)
        return changed, [self._result(state, changed, item) for item in started]

    def _start(self, state: WorldState, item: _Started, stay_location_id: str | None) -> WorldState:
        """@brief 开始单个动作：出行、睡眠、购买或 interact。"""
        pending, call = item.pending, item.call
        starts = {
            "travel": lambda: self._start_travel(state, pending, call.rows[0]),
            "sleep": lambda: self._start_sleep(state, pending, call.rows[0]["minutes"] * 60),
            "purchase": lambda: self.storefront.prepare_purchase(state, pending, call.goods_ids, call.prefer)[0],
            "interact": lambda: self._start_interact(state, pending, call.rows[0], stay_location_id),
        }
        return starts[call.action]()

    def _start_interact(self, state: WorldState, pending: ToolInvocation, row: dict,
                        stay_location_id: str | None) -> WorldState:
        """@brief 开始一次 interact 并收下它要写的事件草稿。"""
        changed, _, drafts = InteractivityRuntime(self.context).start(state, pending, row, stay_location_id)
        self.event_drafts.extend(drafts)
        return changed

    def _start_sleep(self, state: WorldState, pending: ToolInvocation, declared: int) -> WorldState:
        """@brief 受理睡眠并确定唯一定长及多睡秒数。"""
        slept = sleep_seconds_in_window(state.current_time, self._earlier_sleeps(state, pending.agent_id))
        extra = oversleep_seconds(slept, state.random_state, pending.action_id)
        data = {"declared_duration_seconds": declared, "oversleep_seconds": extra}
        params = ActivityStartParams("sleep", declared + extra, data=data)
        return self._named(state, pending, params, ACTION_LABELS["sleep"])

    def _earlier_sleeps(self, state: WorldState, agent_id: int) -> list[tuple[int, int]]:
        """@brief 运行开始前假定睡眠加上本人已完成的睡眠。"""
        config = self.context.config
        persona = self.context.persona_by_id[agent_id]
        local = config.start_time.astimezone(ZoneInfo(config.timezone))
        assumed = assumed_sleep(int(config.start_time.timestamp()), local.hour + local.minute / 60,
                                persona.chronotype_midsleep, persona.sleep_need_hours)
        return [assumed, *((item.started_at, item.ended_at) for item in state.activities.values()
                           if item.agent_id == agent_id and item.kind == ActivityKind.SLEEP
                           and item.status == ActivityStatus.COMPLETED)]

    def _start_travel(self, state: WorldState, pending: ToolInvocation, row: dict) -> WorldState:
        """@brief 受理行程并冻结路线、天气及车费。"""
        method = travel_method(self.context.catalog, row["selected_action"]["travel_method_id"])
        places = place_names(self.context.catalog)
        current = activity_control.open_activities(state, pending.agent_id)
        context = admission.Context(state, pending.agent_id, current, places, {}, self.context.inputs)
        trip = admission.planned_trip(context, row)
        changed, receipt = pay_travel_fare(state, pending.agent_id, trip)
        data = travel_activity_data(trip, method, current_weather_factor(state))
        params = ActivityStartParams("travel", trip.duration_seconds, row["object_id"], method.travel_method_id, data)
        self.resource_changes[pending.action_id] = dict(receipt["resource_changes"])
        return self._named(changed, pending, params, f"{method.name}前往{row['object']}")

    def _named(self, state: WorldState, pending: ToolInvocation, params: ActivityStartParams,
               label: str) -> WorldState:
        """@brief 登记引擎确定的活动并按实际开始时刻命名。"""
        agent, activity = start_activity(
            state.agents[pending.agent_id], params, state.current_time,
            _activity_origin(pending.action_id, pending.wakeup_id, pending.requested_event_id),
            self.context.bodies[pending.agent_id],
        )
        name = activity_control.activity_name(label, "", state.current_time, state.timezone)
        activity = schedule_feedback(activity.model_copy(update={"name": name}), state.current_time,
                                     self.context.config.activity_feedback_seconds)
        return _put_activity(state, agent, activity)

    def _result(self, before: WorldState, changed: WorldState, item: _Started) -> ActionResult:
        """@brief 构造单条动作的受理结果及其驿站取件快照。"""
        pending = item.pending
        activity = next(row for key, row in changed.activities.items()
                        if key not in before.activities and row.origin_action_id == pending.action_id)
        app_id = activity.data["app_id"] if activity.kind == ActivityKind.INTERACT else None
        located = activity.kind == ActivityKind.INTERACT and app_id is None
        orders = start_orders(before, changed, [activity]) if located else activity_orders(changed, [activity])
        data = {"activities": [activity_snapshot(activity, title_index(self.context.catalog))], "orders": orders}
        return ActionResult(action_id=pending.action_id, agent_id=pending.agent_id, status=ActionStatus.IN_PROGRESS,
                            occurred_at=before.current_time, activity_id=activity.activity_id,
                            message="Activities started", data=resolution_data(data, item.resolved, ()))
