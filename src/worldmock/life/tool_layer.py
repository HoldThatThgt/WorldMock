"""@brief 完整工具层和受理共用实际槽位、营业、内容与余额资格。"""

from __future__ import annotations
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control, admission, same_moment
from worldmock.life.action_contract import START_TOOLS, start_tool
from worldmock.life.content_select import (ContentRequest, content_duration_range, eligible_contents,
                                           pinned_push_content)
from worldmock.life.felt import sleep_debt_phrase
from worldmock.life.guidance import Refusal
from worldmock.life.location_activities import unit_facts
from worldmock.life.models import ActivityStatus
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.readable import duration_phrase, interval_phrases, money_phrase
from worldmock.life.travel_methods import travel_method


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
    """@brief 工具与受理使用同一槽位与对象条件上下文。"""
    places = {key: item.display_name for key, item in context.catalog.locations.items()}
    return admission.Context(context.state, context.agent_id, places, context.names["activities"], context.inputs)


def availability(context: LayerContext, candidate: Candidate) -> dict:
    """@brief 可用时原因为空，不可用原因与受理一致。"""
    refusal = refusal_for(context, candidate)
    return {"available": refusal is None, "reason": None if refusal is None else refusal.reason}


def refusal_for(context: LayerContext, candidate: Candidate) -> Refusal | None:
    """@brief 待收尾、同刻阶段与真实目标条件按受理顺序判定；开始动作行按只含这一行的一组求值。"""
    waiting = activity_control.pending_conclusions(context.state, context.agent_id)
    activity = None if candidate.activity_id is None else context.state.activities[candidate.activity_id]
    if waiting and candidate.tool != "conclude_activity":
        return activity_control.conclusion_refusal(waiting, context.names["activities"])
    named = () if activity is None else (activity,)
    kind = same_moment.request_kind(candidate.tool, named, frozenset(item.activity_id for item in waiting))
    stage = same_moment.stage_refusal(context.state.agents[context.agent_id].same_moment_stage, kind)
    if stage is not None:
        return stage
    if candidate.tool not in START_TOOLS:
        return activity_control.control_refusal(_admission_context(context), candidate.tool, activity)
    row = {**candidate.binding, "selected_action": candidate.action}
    blocked = admission.group_refusal(
        _admission_context(context), (admission.Call(candidate.tool, candidate.action["action"], (row,)),))
    return None if blocked is None else blocked.refusal


def _start_candidate(row: dict, action: dict) -> Candidate:
    """@brief 一条动作行对应的开始调用：属于哪个开始工具由对象类型与动作决定。"""
    return Candidate(start_tool(row["kind"], action["action"]), row, action)


def annotated_bindings(context: LayerContext) -> list[dict]:
    """@brief 私有绑定仅附同源资格原因。"""
    return [{**row, "actions": [{**action, "note": availability(context, _start_candidate(row, action))["reason"]}
                                for action in row["actions"]]}
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
        state = "全天营业" if location.opens is None else "、".join(interval_phrases(intervals)) or "今日休息"
    return {"name": row["object"], "kind": kind, "phase": None, "state": state,
            "actions": [_action_row(context, row, action) for action in row["actions"]], "tools": []}


def _action_row(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 八个字段的公开动作行只表达调用里要写的名称、真实价格、时长和可用性。"""
    facts = {"price": None, "duration": None, "quantity": None, "note": None}
    name, agent = action["action"], context.state.agents[context.agent_id]
    if name == "travel":
        facts.update(_travel_facts(context, row, action))
    elif name == "purchase":
        facts.update(_purchase_facts(context, row))
    elif name == "sleep":
        facts["note"] = sleep_debt_phrase(agent.body.sleep_deficit_hours)
    elif row["kind"] == "location":
        facts.update(unit_facts([context.catalog.contents[key] for key in action["content_ids"]], agent))
    else:
        facts.update(_app_facts(context, row))
    return {"action": name, "detail": action["detail"], **availability(context, _start_candidate(row, action)),
            **facts}


def _app_facts(context: LayerContext, row: dict) -> dict:
    """@brief 内容 app 行的时长与推送说明都只在此刻合格的内容里算；此刻打开会钉住某条推送时才写「有推送」。"""
    request = ContentRequest(context.agent_id, row["object_id"],
                             admission.present_location(context.state, context.agent_id))
    duration = content_duration_range(context.catalog, context.state, request)
    pinned = pinned_push_content(eligible_contents(context.catalog, context.state, request), context.state, request)
    phrase = None
    if duration is not None:
        phrase = duration_phrase(duration[0]) if duration[0] == duration[1] else (
            f"{duration_phrase(duration[0])}–{duration_phrase(duration[1])}")
    return {"duration": phrase, "note": None if pinned is None else f"有推送：《{pinned[0].title}》"}


def _travel_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 出行行的车费、按当前天气算出的用时与所选出行方式的体力说法（轻松或费力）。"""
    trip = admission.planned_trip(_admission_context(context), {**row, "selected_action": action})
    return {"price": money_phrase(trip.fare_fen), "duration": duration_phrase(trip.duration_seconds),
            "note": travel_method(context.catalog, action["travel_method_id"]).effort}


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
