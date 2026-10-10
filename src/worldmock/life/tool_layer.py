"""@brief 完整工具层和受理共用实际名额、在场、营业及内容资格。"""

from __future__ import annotations
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control, admission, same_moment
from worldmock.life.action_contract import TRAVEL_ACTIONS, TRAVEL_ACTION_MODES
from worldmock.life.content_select import content_duration_range
from worldmock.life.guidance import Refusal, refusal_for_code, rejection_guidance
from worldmock.life.models import ActivityKind, ActivityStatus
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.readable import duration_phrase, money_phrase
from worldmock.life.travel import plan_trip
from worldmock.life.weather import current_weather_factor


@dataclass(frozen=True, slots=True)
class LayerContext:
    """@brief 一次观察直接读取环境输入与唯一世界，不创建语义副本。"""
    engine: Any
    agent_id: int
    names: dict
    bindings: list[dict]

    @property
    def state(self):
        """@brief 唯一当前世界。"""
        return self.engine.state

    @property
    def inputs(self):
        """@brief 唯一完整环境输入。"""
        return self.engine.inputs

    @property
    def catalog(self):
        """@brief 冻结完整目录。"""
        return self.engine.catalog

    @property
    def persona(self):
        """@brief 唯一人物输入。"""
        return self.engine.persona

    @property
    def config(self):
        """@brief 唯一运行配置。"""
        return self.engine.config

    @property
    def body(self):
        """@brief 固定身体规则视图。"""
        return self.engine.body


@dataclass(frozen=True, slots=True)
class Candidate:
    """@brief 单项开始或控制工具的资格输入。"""
    tool: str
    binding: dict | None = None
    action: dict | None = None
    activity_id: str | None = None


def _admission_context(context: LayerContext) -> admission.Context:
    """@brief 工具与受理使用同一在场、名额上下文。"""
    places = {key: item.display_name for key, item in context.catalog.locations.items()}
    return admission.Context(context.state, context.agent_id, places, context.names["activities"], context.catalog)


def availability(context: LayerContext, candidate: Candidate) -> dict:
    """@brief 可用时原因为空，不可用原因与受理一致。"""
    refusal = refusal_for(context, candidate)
    return {"available": refusal is None, "reason": None if refusal is None else refusal.reason}


def refusal_for(context: LayerContext, candidate: Candidate) -> Refusal | None:
    """@brief 同刻阶段、待收尾与真实目标条件按受理顺序判定。"""
    waiting = activity_control.pending_conclusions(context.state, context.agent_id)
    activity = None if candidate.activity_id is None else context.state.activities[candidate.activity_id]
    if waiting and candidate.tool != "conclude_activity":
        return activity_control.conclusion_refusal(waiting, context.names["activities"])
    named = () if activity is None else (activity,)
    actions = () if candidate.action is None else (candidate.action["action"],)
    kind = same_moment.request_kind(candidate.tool, actions, named,
                                    frozenset(item.activity_id for item in waiting))
    stage = same_moment.stage_refusal(context.state.agents[context.agent_id].same_moment_stage, kind)
    if stage is not None:
        return stage
    if candidate.tool != "start_activity":
        return activity_control.control_refusal(_admission_context(context), candidate.tool, activity)
    item = admission.selection_item(candidate.binding, candidate.action)
    refused = admission.group_refusal(_admission_context(context), (item,))
    if refused is not None:
        return refused
    try:
        _require_object(context, candidate.binding, candidate.action["action"])
    except BusinessRuleError as error:
        logging.error("Tool availability rejected: %s", error)
        guidance = rejection_guidance(error)
        return Refusal(str(error), guidance["reason"], guidance.get("fix"),
                       unmet=tuple(getattr(error, "unmet", ()) or ()))
    return None


def _require_object(context: LayerContext, row: dict, action: str) -> None:
    """@brief 对象条件只读取实际现场，不预演内容抽取。"""
    agent = context.state.agents[context.agent_id]
    if action in TRAVEL_ACTIONS:
        if row["object_id"] == agent.location_id:
            raise BusinessRuleError("ACTIVITY_PRECONDITION_UNMET")
        trip = _trip(context, row, action)
        if context.state.accounts[agent.balance_account_id] < trip.fare_fen:
            raise BusinessRuleError("INSUFFICIENT_TRAVEL_FUNDS")
        return
    if action == "purchase":
        offer = quote_goods(context.inputs, context.state, context.agent_id, row["goods_id"])
        total = context.catalog.goods[row["goods_id"]].price_fen + offer.delivery_fee_fen
        if context.state.accounts[agent.balance_account_id] < total:
            raise BusinessRuleError("INSUFFICIENT_FUNDS")
        return
    if row["kind"] == "location":
        location = context.catalog.locations[row["object_id"]]
        local = datetime.fromtimestamp(context.state.current_time, ZoneInfo(context.config.timezone))
        if not location.is_open(local):
            raise BusinessRuleError("LOCATION_CLOSED")
        if action == "sleep" and row["object_id"] != context.inputs.home_location_id:
            raise BusinessRuleError("ACTIVITY_PRECONDITION_UNMET")
        if action == "interact" and location.accepts_parcels and not _parcels_ready(context, row["object_id"]):
            raise BusinessRuleError("PARCEL_NOT_READY")
    if action == "interact" and _content_range(context, row) is None:
        raise BusinessRuleError("CONTENT_EXHAUSTED")


def _parcels_ready(context: LayerContext, station_id: str) -> bool:
    """@brief 驿站只能领取本人已经真实到站的订单。"""
    return any(order.agent_id == context.agent_id and order.destination_id == station_id
               and order.status == "ready_for_pickup" for order in context.state.deliveries.values())


def _trip(context: LayerContext, row: dict, action: str):
    """@brief 固定规则与世界天气形成同源行程报价。"""
    origin = context.state.agents[context.agent_id].location_id
    return plan_trip(context.catalog.graph, origin, row["object_id"], TRAVEL_ACTION_MODES[action],
                     current_weather_factor(context.state))


def _content_range(context: LayerContext, row: dict):
    """@brief 内容时长范围由选择器只读计算，不消费随机流。"""
    return content_duration_range(context.inputs, context.state, context.agent_id, row["kind"], row["object_id"])


def annotated_bindings(context: LayerContext) -> list[dict]:
    """@brief 私有绑定仅附同源资格原因。"""
    return [{**row, "actions": [{**action, "note": availability(
        context, Candidate("start_activity", row, action))["reason"]} for action in row["actions"]]}
            for row in context.bindings]


def tool_layer(context: LayerContext, index: dict) -> dict:
    """@brief 目标顺序保持地点、商品、内容 app、活动。"""
    targets = [_binding_target(context, row) for row in context.bindings]
    targets.extend(_activity_target(context, item) for item in context.state.activities.values()
                   if item.agent_id == context.agent_id and (item.status == ActivityStatus.ACTIVE
                   or activity_control.is_pending_conclusion(item)))
    return {"layer": "工具层", "targets": targets, "callable_index": index}


def _binding_target(context: LayerContext, row: dict) -> dict:
    """@brief 公共行不带内部身份、类别推断、app 路由或支付渠道。"""
    kind = "product" if row["kind"] == "supply" else row["kind"]
    state = None
    if row["kind"] == "location":
        location = context.catalog.locations[row["object_id"]]
        local = datetime.fromtimestamp(context.state.current_time, ZoneInfo(context.config.timezone))
        intervals = location.opening_intervals(local.date())
        state = "全天营业" if location.opens is None else "、".join(f"{start}–{end}" for start, end in intervals) or "今日休息"
    return {"name": row["object"], "kind": kind, "phase": None, "state": state,
            "actions": [_action_row(context, row, action) for action in row["actions"]], "tools": []}


def _action_row(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 七个字段的公开动作只表达真实价格、时长和可用性。"""
    facts = {"price": None, "duration": None, "quantity": None, "note": None}
    name = action["action"]
    if name in TRAVEL_ACTIONS and row["object_id"] != context.state.agents[context.agent_id].location_id:
        trip = _trip(context, row, name)
        facts.update(price=money_phrase(trip.fare_fen), duration=duration_phrase(trip.duration_seconds),
                     note="费力" if name == "walk" else "轻松")
    elif name == "purchase":
        facts.update(_purchase_facts(context, row))
    elif name == "sleep":
        seconds = round(context.state.agents[context.agent_id].body.sleep_need_hours * 3600)
        facts["duration"] = duration_phrase(seconds)
    elif name == "interact":
        duration = _content_range(context, row)
        if duration is not None:
            facts["duration"] = duration_phrase(duration[0]) if duration[0] == duration[1] else (
                f"{duration_phrase(duration[0])}–{duration_phrase(duration[1])}")
        facts["note"] = _push_note(context, row)
    return {"action": name, **availability(context, Candidate("start_activity", row, action)), **facts}


def _purchase_facts(context: LayerContext, row: dict) -> dict:
    """@brief 查询只计算稳定报价，不抽取未来购买过程时长。"""
    goods = context.catalog.goods[row["goods_id"]]
    note = None
    try:
        offer = quote_goods(context.inputs, context.state, context.agent_id, goods.goods_id)
        arrival = datetime.fromtimestamp(offer.expected_at, ZoneInfo(context.config.timezone))
        if offer.fulfillment != "pickup":
            note = f"运费{money_phrase(offer.delivery_fee_fen)}，预计{arrival:%m月%d日 %H:%M}到达"
    except BusinessRuleError as error:
        logging.error("Purchase preview is unavailable: %s", error)
    return {"price": money_phrase(goods.price_fen), "duration": "1–5 分钟", "quantity": "1 件", "note": note}


def _push_note(context: LayerContext, row: dict) -> str | None:
    """@brief 推送只展示当前 app 的真实标题。"""
    if row["kind"] != "app":
        return None
    agent = context.state.agents[context.agent_id]
    from worldmock.life.sessions import current_session

    session = current_session(context.state, context.agent_id)
    push = None if session is None else session.app_pushes.get(row["object_id"])
    return None if push is None else f"有推送：《{context.catalog.contents[push.content_id].title}》"


def _activity_target(context: LayerContext, activity) -> dict:
    """@brief 活动目标只有控制工具，客观进度使用统一短语。"""
    tools = ("report_status", "pause_activity", "conclude_activity") if activity.status == ActivityStatus.ACTIVE else (
        "conclude_activity",)
    return {"name": context.names["activities"][activity.activity_id], "kind": "activity",
            "phase": activity_control.activity_phase(activity), "state": activity_control.objective_progress(activity),
            "actions": [], "tools": [{"tool": tool, **availability(context, Candidate(tool,
                activity_id=activity.activity_id))} for tool in tools]}


def with_bindings(context: LayerContext, bindings: list[dict]) -> LayerContext:
    """@brief 替换当前只读绑定，不复制世界或环境输入。"""
    return replace(context, bindings=bindings)
