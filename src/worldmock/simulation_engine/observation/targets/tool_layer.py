"""@brief 完整工具层和受理共用实际槽位、营业、内容与余额资格。"""

from __future__ import annotations
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.foundation.errors import BusinessRuleError
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.actions import admission
from worldmock.state.interaction.actions import START_TOOLS, start_tool
from worldmock.simulation_engine.observation.wording.content_prerequisite import app_time_state
from worldmock.simulation_engine.activities.content.content_select import (
    ContentRequest,
    OpeningDuration,
    eligible_contents,
    opening_duration,
    pinned_push_content,
)
from worldmock.simulation_engine.body.felt import sleep_debt_phrase
from worldmock.simulation_engine.observation.wording.guidance import Refusal
from worldmock.simulation_engine.activities.content.location_activities import running_series_note, unit_facts
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.kinds import ActivityKind, ActivityStatus, FulfillmentKind
from worldmock.simulation_engine.commerce.purchase.purchase_offers import quote_goods
from worldmock.foundation.time.readable import duration_phrase, interval_phrases, money_phrase
from worldmock.resource_engine.catalog.travel_methods import travel_method


@dataclass(frozen=True, slots=True)
class LayerContext:
    """@brief 一次观察直接读取环境输入与唯一世界（或沙盒状态副本），不创建语义副本。"""
    engine: Any
    agent_id: int
    names: dict
    bindings: list[dict]
    #: 本次观察算一次的本人未了活动；准入、待收尾、槽位与睡着判定共用。
    open_activities: tuple[ActivityState, ...]
    #: 沙盒内层重算时覆盖的世界副本；正常提交观察时为空。
    state_override: Any = None
    #: 本轮或日内通过 lookup_targets 懒加载的目标键集合 (kind, object_id)。
    loaded_target_keys: frozenset[tuple[str, str]] = frozenset()

    @property
    def state(self):
        """@brief 唯一当前世界，或沙盒投影覆盖状态。"""
        return self.engine.state if self.state_override is None else self.state_override

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
        """@brief 当前观察人物的声明输入。"""
        return self.engine.persona_by_id[self.agent_id]

    @property
    def config(self):
        """@brief 唯一运行配置。"""
        return self.engine.config

    @property
    def body(self):
        """@brief 当前观察人物的固定身体规则视图。"""
        return self.engine.bodies[self.agent_id]


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
    return admission.Context(context.state, context.agent_id, context.open_activities, places,
                             context.names["activities"], context.inputs)


def availability(context: LayerContext, candidate: Candidate) -> dict:
    """@brief 可用时原因为空，不可用原因与受理一致。"""
    refusal = refusal_for(context, candidate)
    return {"available": refusal is None, "reason": None if refusal is None else refusal.reason}


def refusal_for(context: LayerContext, candidate: Candidate) -> Refusal | None:
    """@brief 开始动作行仅按自身固有条件求值（全局槽位、睡眠与待收尾由顶层 gate 宣告）；活动工具保留待收尾与单条校验。"""
    if candidate.tool in START_TOOLS:
        row = {**candidate.binding, "selected_action": candidate.action}
        return admission.intrinsic_refusal(
            _admission_context(context), admission.Call(candidate.tool, candidate.action["action"], (row,)),
        )
    waiting = activity_control.pending_conclusions(context.open_activities)
    activity = None if candidate.activity_id is None else context.state.activities[candidate.activity_id]
    if waiting and candidate.tool != "conclude_activity":
        return activity_control.conclusion_refusal(waiting, context.names["activities"])
    return activity_control.control_refusal(_admission_context(context), candidate.tool, activity)


def _start_candidate(row: dict, action: dict) -> Candidate:
    """@brief 一条动作行对应的开始调用：属于哪个开始工具由对象类型与动作决定。"""
    return Candidate(start_tool(row["kind"], action["action"]), row, action)


def annotated_bindings(context: LayerContext) -> list[dict]:
    """@brief 每条动作（含 locked）的 note 是这一行唯一的一次可用性求值，工具层的动作行直接取它。"""
    return [{**row, "actions": [{**action, "note": availability(context, _start_candidate(row, action))["reason"]}
                                for action in row["actions"]]}
            for row in context.bindings]


def _gate(context: LayerContext) -> dict[str, Any]:
    """@brief 顶层门禁五键：主槽位占用、副槽位占用、睡眠中、在途与待收尾活动显示名称列表。"""
    waiting = activity_control.pending_conclusions(context.open_activities)
    return {
        "main_slot_busy": any(
            "first" in item.resource_claims and activity_control.occupies_slot(item)
            for item in context.open_activities
        ),
        "side_slot_busy": any(
            "second" in item.resource_claims and activity_control.occupies_slot(item)
            for item in context.open_activities
        ),
        "sleeping": any(
            item.kind == ActivityKind.SLEEP and item.status == ActivityStatus.ACTIVE
            for item in context.open_activities
        ),
        "in_transit": admission.present_location(context.state, context.agent_id) is None,
        "pending_conclusions": [context.names["activities"].get(item.activity_id, item.name) for item in waiting],
    }


def tool_layer(context: LayerContext, index: dict) -> dict:
    """@brief 构造含顶层 gate、核心常驻/已载入目标与完整可调用索引的工具层。"""
    targets = [_binding_target(context, row) for row in context.bindings if _target_visible(context, row)]
    targets.extend(_activity_target(context, item) for item in context.state.activities.values()
                   if item.agent_id == context.agent_id and (item.status == ActivityStatus.ACTIVE
                   or activity_control.is_pending_conclusion(item)))
    return {"layer": "工具层", "gate": _gate(context), "targets": targets, "callable_index": index}


def _target_visible(context: LayerContext, row: dict) -> bool:
    """@brief 核心常驻工具层过滤：核心锚点/驿站枢纽/合格或置顶推送应用/进行中活动与已载入目标。"""
    kind = row["kind"]
    if kind == "location":
        return _location_visible(context, row["object_id"])
    if kind == "supply":
        return ("product", row["object_id"]) in context.loaded_target_keys
    if kind == "app":
        return _app_visible(context, row["object_id"])
    return True


def _location_visible(context: LayerContext, destination: str) -> bool:
    """@brief 核心常驻地点：当前点、同城家与工作地、本城驿站与跨城枢纽、一步直达异地枢纽或已检索载入地点。"""
    if ("location", destination) in context.loaded_target_keys:
        return True
    origin = context.state.agents[context.agent_id].location_id
    graph = context.catalog.graph
    place = context.catalog.locations[destination]
    anchors = {context.inputs.home_location_ids[context.agent_id]}
    workplace = context.inputs.workplace_location_ids[context.agent_id]
    if workplace is not None:
        anchors.add(workplace)
    if destination == origin or (destination in anchors and place.city_id == graph.city_of(origin)):
        return True
    if place.city_id == graph.city_of(origin) and (
        place.kind == "parcel_station" or place.accepts_parcels or bool(graph.intercity_edges_from(destination))
    ):
        return True
    return destination in dict(graph.intercity_edges_from(origin))


def _app_visible(context: LayerContext, app_id: str) -> bool:
    """@brief 内容应用：具备合格内容或置顶推送，或已检索载入时收录入核心常驻工具层。"""
    if ("app", app_id) in context.loaded_target_keys:
        return True
    stay = admission.present_location(context.state, context.agent_id)
    request = ContentRequest(context.agent_id, app_id, stay)
    eligible = eligible_contents(context.catalog, context.state, request)
    return bool(eligible) or pinned_push_content(eligible, context.state, request) is not None


def _visible_actions(context: LayerContext, row: dict) -> list[dict]:
    """@brief 过滤地点行下当前不可沿该路线使用的出行方式；其余动作保持原顺序。"""
    if row["kind"] != "location":
        return row["actions"]
    destination = row["object_id"]
    return [action for action in row["actions"] if action["action"] != "travel"
            or _travel_visible(context, destination, action["travel_method_id"])]


def _travel_visible(context: LayerContext, destination_id: str, method_id: str) -> bool:
    """@brief 出行方式可见当且仅当从当前地点到目的地点按方式隔离规则存在有效路线。"""
    origin = context.state.agents[context.agent_id].location_id
    method = travel_method(context.catalog, method_id)
    return context.catalog.graph.shortest_route(origin, destination_id, method_id, method.city_wide) is not None


def _portal_suffix(context: LayerContext, location_id: str) -> str:
    """@brief 本城地点连有跨城声明边时附加可达异地枢纽与许可方式后缀，按目的地点身份与方式身份升序。"""
    edges = context.catalog.graph.intercity_edges_from(location_id)
    if not edges:
        return ""
    routes = []
    for destination_id, edge in edges:
        methods = "、".join(travel_method(context.catalog, key).name for key in sorted(edge.travel_method_ids or ()))
        routes.append(f"{context.catalog.locations[destination_id].display_name}（{methods}）")
    return f"；城际可达：{'、'.join(routes)}"


def _binding_target(context: LayerContext, row: dict) -> dict:
    """@brief 公共行不带内部身份、类别推断、app 路由或支付渠道；凭状态而未开放的单元（私有绑定 locked 为真）不列出。

    目标的 state：地点行写当天的营业时段（中文），当前城市枢纽另附城际可达后缀；内容 app 行是
    content_prerequisite.app_time_state，全部耗尽或全都不合格时写状态；商品行为 None。
    """
    row = {**row, "actions": _visible_actions(context, row)}
    kind = "product" if row["kind"] == "supply" else row["kind"]
    local = datetime.fromtimestamp(context.state.current_time, ZoneInfo(context.config.timezone))
    agent = context.state.agents[context.agent_id]
    state = None
    if row["kind"] == "location":
        location = context.catalog.locations[row["object_id"]]
        intervals = location.opening_intervals(local.date())
        state = "全天营业" if location.opens is None else "、".join(interval_phrases(intervals)) or "今日休息"
        if location.city_id == context.catalog.graph.city_of(agent.location_id):
            state += _portal_suffix(context, location.location_id)
    elif row["kind"] == "app":
        stay = admission.present_location(context.state, context.agent_id)
        state = app_time_state(context.catalog, row["object_id"],
                               None if stay is None else context.catalog.locations[stay], local, agent)
    return {"name": row["object"], "kind": kind, "phase": None, "state": state,
            "actions": [_action_row(context, row, action) for action in row["actions"] if not action.get("locked")],
            "tools": []}


def _action_row(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 公开动作行表达名称、真实价格、时长和可用性；可用性取 annotated_bindings 已写下的 note，
    原因为空即可用，不再求值第二次。"""
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
    return {"action": name, "detail": action["detail"], "available": action["note"] is None,
            "reason": action["note"], **facts}


def _app_facts(context: LayerContext, row: dict) -> dict:
    """@brief 内容 app 行的时长与推送说明都只在此刻合格的内容里算：时长写哪一种由 content_select.opening_duration
    判定、由 _app_duration 写成文字，没有合格内容时为 null；此刻打开会钉住某条推送时才写「有推送」。"""
    request = ContentRequest(context.agent_id, row["object_id"],
                             admission.present_location(context.state, context.agent_id))
    duration = opening_duration(context.catalog, context.state, request)
    pinned = pinned_push_content(eligible_contents(context.catalog, context.state, request), context.state, request)
    return {"duration": None if duration is None else _app_duration(duration),
            "note": None if pinned is None else f"有推送：《{pinned[0].title}》"}


def _app_duration(duration: OpeningDuration) -> str:
    """@brief 内容 app 行的时长文字：打开会先放一条已知内容时写「先放的一条<余量>」，否则写「每条<时长>」；
    最小值与最大值按秒比较，不等时两个短语用「–」相连。"""
    span = duration_phrase(duration.shortest) if duration.shortest == duration.longest else (
        f"{duration_phrase(duration.shortest)}–{duration_phrase(duration.longest)}")
    return f"先放的一条{span}" if duration.known_first else f"每条{span}"


def _travel_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 出行行的车费、按当前天气算出的用时与所选出行方式的体力说法（轻松或费力）。"""
    trip = admission.planned_trip(_admission_context(context), {**row, "selected_action": action})
    return {"price": money_phrase(trip.fare_fen), "duration": duration_phrase(trip.duration_seconds),
            "note": travel_method(context.catalog, action["travel_method_id"]).effort}


def _purchase_facts(context: LayerContext, row: dict) -> dict:
    """@brief 查询只计算稳定报价，不抽取未来购买过程时长；按没有意向的路线预览，实际以购买结果为准。"""
    goods = context.catalog.goods[row["goods_id"]]
    facts = []
    try:
        offer = quote_goods(context.inputs, context.state, context.agent_id, goods.goods_id, None)
        if offer.store_id is not None:
            store = context.catalog.locations[offer.store_id]
            facts.append(f"{store.display_name}（评分 {store.rating}）")
        if offer.fulfillment == FulfillmentKind.INSTANT:
            facts.append("运费0元，付款即收货")
        elif offer.fulfillment != FulfillmentKind.PICKUP:
            arrival = datetime.fromtimestamp(offer.expected_at, ZoneInfo(context.config.timezone))
            clock = arrival.strftime("%m月%d日 %H:%M:%S" if arrival.second else "%m月%d日 %H:%M")
            facts.append(f"运费{money_phrase(offer.delivery_fee_fen)}，预计{clock}到达")
    except BusinessRuleError as error:
        logging.error("Purchase preview is unavailable: %s", error)
    return {"price": money_phrase(goods.price_fen), "duration": "1–5 分钟", "quantity": "1 件",
            "note": "，".join(facts) if facts else None}


def _activity_target(context: LayerContext, activity) -> dict:
    """@brief 活动目标只有控制工具，客观进度使用统一短语；进行中的地点系列在客观进度前面另写第几集与供餐的说法
    （location_activities.running_series_note），以「；」相连，其余活动的 state 就是客观进度。"""
    tools = ("report_status", "pause_activity", "conclude_activity") if activity.status == ActivityStatus.ACTIVE else (
        "conclude_activity",)
    progress = activity_control.objective_progress(activity)
    note = running_series_note(context.catalog, activity)
    state = progress if note is None else f"{note}；{progress}"
    return {"name": context.names["activities"][activity.activity_id], "kind": "activity",
            "phase": activity_control.activity_phase(activity), "state": state,
            "actions": [], "tools": [{"tool": tool, **availability(context, Candidate(tool,
                activity_id=activity.activity_id))} for tool in tools]}


def with_bindings(context: LayerContext, bindings: list[dict]) -> LayerContext:
    """@brief 替换当前只读绑定，不复制世界或环境输入。"""
    return replace(context, bindings=bindings)
