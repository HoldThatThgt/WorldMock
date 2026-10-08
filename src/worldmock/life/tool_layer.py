"""@brief 工具层：本轮可操作的目标、各目标的动作与工具，以及可用性的单一来源（docs/design/06-agent.md「工具层」）。

目标由本轮私有绑定（projection.interaction_bindings）与进行中、刚结束的活动组成；暂停的活动只在待办层。每个列出的动作与
工具都带 available 与 reason：reason 由 refusal_for 按环境受理的同一顺序判定（菜单→决策到期→同刻阶段→待收尾→名额与出发前
→准入组→对象自身条件；名称工具为阶段→待收尾→控制或驾车→提交或收件人条件），取拒绝时的原文，所以提示与拒绝一字不差；
列出即可提交，不可用的由环境以同一原因业务拒绝。受理路径调用本模块的资格判定，并共用这里调用的各个领域规则函数。
层里另附只列名称的可调用索引 callable_index（由 life.callable_index 构建）：它不是目标，不带动作与可用性。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, TYPE_CHECKING

from worldmock.catalog.affordances import engine_affordances
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life import activity_control, admission, same_moment, social, social_actions
from worldmock.life.basket import validate_purchase_order
from worldmock.exchange.constants import GOODS_ENERGY_KCAL
from worldmock.exchange.operators import delivery_fee_fen, delivery_seconds
from worldmock.exchange.types import ACTION_CATEGORY_LABELS, APP_USE_MANNER_LABELS, GOODS_CATEGORY_LABELS
from worldmock.life.content_select import (ContentRequest, content_duration_range, eligible_contents,
                                          required_work_location)
from worldmock.life.control_items import CONTROL_TOOLS
from worldmock.life.fuel import require_driving_days
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply
from worldmock.life.purchase_offers import choose, offers
from worldmock.life.work import work_location_refusal
from worldmock.life.durations import derived_activity_duration
from worldmock.life.guidance import Refusal, refusal_for_code, rejection_guidance
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.models import (ActivityKind, ActivityStartParams, ActivityStatus, DERIVED_DURATION_CAP_SECONDS,
                                   PurchaseItem, PurchaseOrderRequest, SameMomentStage, WorldState,
                                   active_activities)
from worldmock.life.projection import route_effort
from worldmock.life.readable import ACTIVITY_DOING_LABELS, clock_phrase, duration_phrase, money_phrase, portion_phrase
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.sessions import current_session
from worldmock.life.travel import planned_travel


if TYPE_CHECKING:
    from worldmock.life.rules import LifeRuleEngine

#: 私有绑定类别 → 工具层目标类别，动作与商品名称来自当前目录。
BINDING_KINDS = {"location": "location", "supply": "product", "action": "action",
                 "person": "person", "contact": "contact", "self": "self"}
@dataclass(frozen=True, slots=True)
class LayerContext:
    """@brief 一次观察构建各层所用的只读世界切片，由 ObservationBuilder 每次观察构造一次。

    :param names: activity_control.visible_names 的结果（会话名与活动名）。
    :param bindings: 本次观察最终的 interaction_bindings。
    :param menu: 本轮有效交互菜单。
    """

    state: WorldState
    scenario: RuntimeScenario
    rules: LifeRuleEngine
    semantics: ScenarioSemantics
    agent_id: int
    names: dict[str, dict[str, str]]
    bindings: list[dict]
    menu: InteractionMenu


@dataclass(frozen=True, slots=True)
class Candidate:
    """@brief 一个待判定可用性的世界请求：start_activity 的一项（绑定行与所选动作），或以名称为参数的一项工具。"""

    tool: str
    binding: dict | None = None
    action: dict | None = None
    activity_id: str | None = None
    recipient_id: str | None = None


def availability(context: LayerContext, candidate: Candidate) -> dict:
    """@brief 可用性：{"available": bool, "reason": str | None}；reason 为 null 当且仅当可用。"""
    refusal = refusal_for(context, candidate)
    return {"available": refusal is None, "reason": refusal.reason if refusal is not None else None}


def refusal_for(context: LayerContext, candidate: Candidate) -> Refusal | None:
    """@brief 单独提交这一项时环境受理的第一条拒绝（与受理同序同文），全部成立时为 None。"""
    agent = context.state.agents[context.agent_id]
    if candidate.tool == "start_activity" and candidate.action["canonical_action"] not in context.menu.allowed_actions:
        return refusal_for_code("INTERACTION_MENU_DENIED")
    if agent.next_decision_at > context.state.current_time:
        return refusal_for_code("ACTIVITY_FEEDBACK_NOT_DUE")
    refusal = _stage_refusal(context, candidate)
    if refusal is not None:
        return refusal
    waiting = activity_control.pending_conclusions(context.state, context.agent_id)
    if waiting and candidate.tool != "conclude_activity":
        return activity_control.conclusion_refusal(waiting, context.names["activities"])
    if candidate.tool == "start_activity":
        return _start_refusal(context, candidate.binding, candidate.action)
    if candidate.tool in CONTROL_TOOLS:
        activity = context.state.activities[candidate.activity_id]
        return activity_control.control_refusal(_admission_context(context), candidate.tool, activity)
    return admission.hands_free_refusal(_admission_context(context), "send a message")


def _stage_refusal(context: LayerContext, candidate: Candidate) -> Refusal | None:
    """@brief 同刻受限轮复核：按与受理相同的请求类判定本阶段是否开放。"""
    stage = context.state.agents[context.agent_id].same_moment_stage
    if stage == SameMomentStage.OPEN:
        return None
    waiting = frozenset(item.activity_id for item in activity_control.pending_conclusions(context.state,
                                                                                          context.agent_id))
    actions = [candidate.action["canonical_action"]] if candidate.tool == "start_activity" else []
    named = ([context.state.activities[candidate.activity_id]]
             if candidate.tool in {"report_status", "conclude_activity"} else [])
    return same_moment.stage_refusal(stage, same_moment.request_kind(candidate.tool, actions, named, waiting))


def _admission_context(context: LayerContext) -> admission.Context:
    """@brief 准入判定使用与受理相同的世界、可读名称和登记图。"""
    return admission.Context(state=context.state, agent_id=context.agent_id,
                             place_names={item.location_id: item.display_name for item in context.scenario.locations},
                             activity_names=context.names["activities"],
                             graph=context.semantics.catalog.graph, catalog=context.semantics.catalog,
                             scenario=context.scenario)


def _world_facts(context: LayerContext) -> admission.WorldFacts:
    """@brief 准入判定用的冻结目录（与受理取同一规则目录）。"""
    rules = context.rules
    config = next(item for item in context.scenario.agents if item.agent_id == context.agent_id)
    workplace = config.work_schedule.workplace_location_id if config.work_schedule is not None else None
    return admission.WorldFacts(context.semantics.catalog, context.scenario, context.state, context.agent_id)


def _start_refusal(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief start_activity 的一项：名额与出发前、整组准入，再核对对象自身条件。"""
    request = ([action["canonical_action"]], [action["label"]])
    refusal = activity_control.start_admission_refusal(context.state, context.agent_id, request,
                                                       context.names["activities"])
    if refusal is not None:
        return refusal
    if action["canonical_action"] == "purchase" and action.get("fulfillment") == "pickup":
        recipient = binding.get("recipient_id", f"agent:{context.agent_id}")
        try:
            resolved = resolve_named_supply(context, PurchaseAsk(binding["object"], None, None, recipient))
        except BusinessRuleError as error:
            guidance = rejection_guidance(error)
            return Refusal(str(error), guidance["reason"], guidance["next_step"] or "")
        binding = resolved
        action = resolved["selected_action"]
    item = admission.selection_item(binding, action, _world_facts(context))
    refusal = admission.group_refusal(_admission_context(context), [item])
    if refusal is not None:
        return refusal
    check = _OBJECT_CHECKS[action["canonical_action"]]
    try:
        return check(context, binding, action)
    except BusinessRuleError as error:
        guidance = rejection_guidance(error)
        return Refusal(str(error), guidance["reason"], guidance["next_step"] or "")


def _travel_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 出行：派生时长为一秒至一天，余额够付固定车费。"""
    agent = context.state.agents[context.agent_id]
    route, duration = _route(context, binding["object_id"], action["travel_mode"])
    if action["travel_mode"] == "driving":
        try:
            require_driving_days(context.state, context.semantics, context.agent_id,
                                 context.state.current_time + duration)
        except BusinessRuleError as error:
            return refusal_for_code(str(error))
    if not 1 <= duration <= DERIVED_DURATION_CAP_SECONDS:
        return refusal_for_code("ROUTE_DURATION_INVALID")
    if context.state.accounts[agent.balance_account_id] < route.fare_fen:
        return refusal_for_code("INSUFFICIENT_TRAVEL_FUNDS")
    return None


def _route(context: LayerContext, destination_id: str, mode: str):
    """@brief 从登记图计算人物当前位置到目的地的行程，与受理使用同一入口。"""
    draft = ActivityStartParams(activity_type=ActivityKind.TRAVEL, planned_duration_seconds=1,
                                destination_id=destination_id, travel_mode=mode)
    return planned_travel(context.scenario, context.semantics, context.state.agents[context.agent_id].location_id,
                          draft, context.state.current_time)




def _timed_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 观看、游玩、阅读、浏览、等待：派生时长不短于一分钟。"""
    if _derived_seconds(context, binding, action) < 60:
        return refusal_for_code("ACTIVITY_DURATION_INVALID")
    return None




def _sleep_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 睡觉：按睡眠需要派生的时长不短于一分钟。"""
    if _derived_seconds(context, binding, action) < 60:
        return refusal_for_code("ACTIVITY_TOO_SHORT")
    return None






def _purchase_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 可用性只读核对真实报价，支付列表不替人物实际填写请求。"""
    catalog = context.semantics.catalog
    app_id = action.get("app_id")
    app = catalog.apps[app_id] if app_id is not None else None
    payment_id = action.get("payment_app_id")
    payment = (catalog.apps[payment_id].name if payment_id is not None else
               catalog.apps[app.payment_app_ids[0]].name if app and app.payment_app_ids else None)
    recipient = binding.get("recipient_id", f"agent:{context.agent_id}")
    resolved = resolve_named_supply(context, PurchaseAsk(binding["object"], app.name if app else None,
                                                        payment, recipient))
    item = PurchaseItem(store_id=resolved["store_id"], goods_id=resolved["goods_id"], quantity=1,
                        recipient_id=recipient)
    request = PurchaseOrderRequest(items=(item,), fulfillment=resolved["selected_action"]["fulfillment"],
                                   destination_id=resolved["destination_id"], app_id=app_id,
                                   payment_app_id=resolved["payment_app_id"])
    validate_purchase_order(context.state, context.semantics, context.agent_id, request)
    return None


def _action_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 合格内容和工作地点只影响可用性，不隐藏环境动作。"""
    request = ContentRequest(context.agent_id, binding["object_id"], action.get("app_id"))
    catalog = context.semantics.catalog
    required = required_work_location(catalog, context.scenario, context.state, request)
    if required is not None:
        place = next(item.display_name for item in context.scenario.locations if item.location_id == required)
        return work_location_refusal(catalog.actions[binding["object_id"]].name, place)
    if not eligible_contents(catalog, context.scenario, context.state, request):
        return refusal_for_code("CONTENT_EXHAUSTED")
    category = catalog.actions[binding["object_id"]].category
    return context.rules.action_location_refusal(category, admission.present_location(context.state, context.agent_id))


def _collect_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 领取本人真实到站的订单，不把通知当作收货。"""
    location_id = binding["object_id"]
    if admission.present_location(context.state, context.agent_id) != location_id:
        return refusal_for_code("ACTIVITY_PRECONDITION_UNMET")
    ready = any(order.status == "ready_for_pickup" and order.recipient_id == f"agent:{context.agent_id}"
                and order.destination_id == location_id for order in context.state.deliveries.values())
    return None if ready else refusal_for_code("PARCEL_NOT_READY")


#: 内部标准动作 → 对象自身条件的判定。
_OBJECT_CHECKS = {"travel_to": _travel_check, "wait": _timed_check, "sleep": _sleep_check,
                  "do": _action_check, "purchase": _purchase_check, "collect_parcel": _collect_check}


def _derived_seconds(context: LayerContext, binding: dict, action: dict) -> int:
    """@brief 等待与睡眠的持续时长由同一活动规则派生。"""
    canonical = action["canonical_action"]
    kinds = {"sleep": ActivityKind.SLEEP, "wait": ActivityKind.WAIT}
    return derived_activity_duration(kinds[canonical], context.state.agents[context.agent_id], None,
                                     context.scenario.activity_feedback_seconds)


def annotated_bindings(context: LayerContext) -> list[dict]:
    """@brief 私有绑定的副本：每个动作的 note 写成工具层该动作的 reason（可用时为 None），两处同源。"""
    result = []
    for row in context.bindings:
        actions = [{**action, "note": availability(context, Candidate("start_activity", row, action))["reason"]}
                   for action in row["actions"]]
        result.append({**row, "actions": actions})
    return result


def tool_layer(context: LayerContext, index: dict[str, Any]) -> dict:
    """@brief 工具层 JSON：绑定目标（按绑定顺序）之后是进行中与刚结束的活动目标，名称全局唯一；另附只列名称的可调用索引
    callable_index（life.callable_index 按本轮呈现窗口构建，docs/design/04-simulation-engine.md ）：它不
    是目标，不带
    动作与条件，不参与目标名称的唯一性判定。"""
    targets = [_binding_target(context, row) for row in context.bindings]
    targets.extend(_activity_target(context, activity) for activity in _activity_rows(context))
    names = [target["name"] for target in targets]
    if len(names) != len(set(names)):
        logging.error("Tool layer target names are ambiguous")
        raise IntegrityError("Tool layer target names must be unique")
    return {"layer": "工具层", "targets": targets, "callable_index": index}


def _binding_target(context: LayerContext, row: dict) -> dict:
    """@brief 一个绑定对象的目标：名称、类别、状态、任务合同、动作与以其名称为参数的工具。"""
    kind = BINDING_KINDS[row["kind"]]
    target: dict[str, Any] = {"name": row["object"], "kind": kind, "phase": None, "state": _state(context, row)}
    target["actions"] = [_verb_action(context, row, action) for action in row["actions"]]
    if kind in {"person", "contact"}:
        target["tools"] = [_tool_action(context, Candidate("send_message", recipient_id=row["object_id"]))]
    else:
        target["tools"] = []
    return target




def _tool_action(context: LayerContext, candidate: Candidate) -> dict:
    """@brief 一项以目标名称为参数的工具及其可用性。"""
    return {"tool": candidate.tool, **availability(context, candidate)}


def _verb_action(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief start_activity 的一项动词与方式：可用性，以及价格、耗时、数量与补充说明（没有的写 null）。"""
    facts = _action_facts(context, row, action)
    return {"action": action["verb"], "way": action["label"],
            "app": context.semantics.catalog.apps[action["app_id"]].name if action.get("app_id") else None,
            **availability(context, Candidate("start_activity", row, action)), **facts}


def _action_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 动作价格、时长和数量均来自真实路线与购买规则。"""
    facts = {"price": None, "duration": None, "quantity": None, "note": None}
    canonical = action["canonical_action"]
    if canonical == "travel_to":
        return {**facts, **_travel_facts(context, row, action)}
    if canonical == "purchase":
        return {**facts, **_purchase_facts(context, row, action)}
    if canonical == "do":
        return {**facts, **_content_facts(context, row, action)}
    seconds = _facts_seconds(context, row, action)
    return {**facts, "duration": duration_phrase(seconds) if seconds is not None and seconds >= 60 else None}


def _travel_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 出行的固定车费、图上行程时长与费力程度。"""
    route, seconds = _route(context, row["object_id"], action["travel_mode"])
    key = engine_affordances().met_lookup_key("travel", travel_mode=action["travel_mode"])
    effort = route_effort(context.semantics.body.activity_met[key])
    return {"price": money_phrase(route.fare_fen),
            "duration": duration_phrase(seconds), "note": f"费力程度：{effort}"}


def _purchase_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 商品价格与送达或到站时刻来自受理会选中的同一店铺。"""
    catalog = context.semantics.catalog
    goods = catalog.goods[row["goods_id"]]
    app_id = action.get("app_id")
    facts = {"price": money_phrase(goods.price_fen + (delivery_fee_fen() if app_id else 0)),
             "quantity": portion_phrase(1)}
    if app_id is not None:
        served = offers(context, goods.goods_id, app_id, f"agent:{context.agent_id}")
        payments = "、".join(catalog.apps[identity].name for identity in catalog.apps[app_id].payment_app_ids)
        note = f"可用{payments}付款"
        if served:
            chosen = choose(served)
            when = clock_phrase(chosen.expected_at, context.state.current_time, context.scenario.timezone)
            edge = "到站" if chosen.fulfillment == "station_pickup" else "送到"
            note = f"预计 {when} {edge}；{note}"
        facts["note"] = note
    return facts


def _content_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 内容时长只读查询，不抽样、不改变世界和推送状态。"""
    app_id = action.get("app_id")
    request = ContentRequest(context.agent_id, row["object_id"], app_id)
    interval = content_duration_range(context.semantics.catalog, context.scenario, context.state, request)
    duration = None
    if interval:
        minimum, maximum = interval
        duration = (duration_phrase(minimum) if minimum == maximum else
                    f"{duration_phrase(minimum)}至{duration_phrase(maximum).removeprefix('约')}")
    notes = [APP_USE_MANNER_LABELS[action["manner"]]] if action.get("manner") is not None else []
    session = current_session(context.state, context.agent_id)
    push = session.app_pushes.get(app_id) if session is not None else None
    catalog = context.semantics.catalog
    if push and push.status != "released" and catalog.contents[push.content_id].action_id == row["object_id"]:
        notes.append(f"有推送：《{catalog.contents[push.content_id].title}》")
    if action["category"] == "social":
        notes.append("to 写对方")
    return {"duration": duration, "note": "；".join(notes) or None}


def _facts_seconds(context: LayerContext, row: dict, action: dict) -> int | None:
    """@brief 原生等待与睡眠的计划秒数；内容动作通过真实内容时长范围呈现。"""
    canonical = action["canonical_action"]
    if canonical in {"purchase", "collect_parcel"}:
        return None
    return _derived_seconds(context, row, action)


def _state(context: LayerContext, row: dict) -> str | None:
    """@brief 目标当前可读状态：商品类别与食品热量、动作类别与说明、人物状态。"""
    kind = row["kind"]
    if kind == "supply":
        return _product_state(context, row)
    if kind == "action":
        action = context.semantics.catalog.actions[row["object_id"]]
        return f"{ACTION_CATEGORY_LABELS[action.category]}；{action.description}"
    if kind == "person":
        return _person_state(context, int(str(row["object_id"]).split(":", 1)[1]))
    return None




def _product_state(context: LayerContext, row: dict) -> str:
    """@brief 商品目标写固定类别；食品另写每份热量，下单 app 来自真实店铺绑定。"""
    goods = context.semantics.catalog.goods[row["goods_id"]]
    state = GOODS_CATEGORY_LABELS[goods.category]
    if goods.category in GOODS_ENERGY_KCAL:
        state += f"，每份约 {GOODS_ENERGY_KCAL[goods.category]:g} 千卡"
    return state




def _person_state(context: LayerContext, agent_id: int) -> str:
    """@brief 在场人物的动作使用登记动作名，其余使用固定活动说法。"""
    names = {context.semantics.catalog.actions[item.data["action_id"]].name
             if item.kind == ActivityKind.ACTION else ACTIVITY_DOING_LABELS[item.kind.value]
             for item in active_activities(context.state, agent_id)}
    return "正在" + "、".join(sorted(names)) if names else "没在做什么"


def _activity_rows(context: LayerContext) -> list:
    """@brief 人物进行中与刚结束（待收尾）的活动，按开始时刻与身份排序；暂停的活动只在待办层。"""
    return sorted((item for item in context.state.activities.values()
                   if item.agent_id == context.agent_id
                   and (item.status == ActivityStatus.ACTIVE or activity_control.is_pending_conclusion(item))),
                  key=lambda item: (item.started_at, item.activity_id))


def _activity_target(context: LayerContext, activity) -> dict:
    """@brief 活动目标：名称取本轮可见活动名，阶段为进行中或刚结束，状态为客观进度（刚结束另写终局），三个控制工具。"""
    pending = activity_control.is_pending_conclusion(activity)
    progress = activity_control.objective_progress(activity)
    last_report = None
    if activity.kind == ActivityKind.ACTION:
        content_id = activity.data["content_id"]
        content = context.semantics.catalog.contents[content_id]
        entry = context.state.agents[context.agent_id].content_progress.get(content_id)
        position = (activity.media_position_seconds if pending else
                    entry.position_seconds if entry else activity.data["start_position_seconds"])
        progress = f"已做{duration_phrase(activity.elapsed_seconds)}，《{content.title}》还剩" + duration_phrase(
            max(0, int(content.duration_seconds - position)))
        if activity.data["category"] == "work_study":
            reports = [item for item in context.state.activities.values() if item.agent_id == context.agent_id
                       and item.kind == ActivityKind.ACTION and item.data["content_id"] == content_id
                       and item.progress_note]
            latest = max(reports, key=lambda item: (item.note_updated_at or item.started_at, item.activity_id),
                         default=None)
            last_report = latest.progress_note if latest is not None else None
    state = f"{activity_control.ending_phrase(activity)}，{progress}" if pending else progress
    tools = [_tool_action(context, Candidate(tool, activity_id=activity.activity_id)) for tool in CONTROL_TOOLS]
    return {"name": context.names["activities"][activity.activity_id], "kind": "activity",
            "phase": activity_control.PHASE_PENDING if pending else activity_control.PHASE_ACTIVE,
            "state": state, "actions": [], "tools": tools,
            **({"last_report": last_report} if last_report is not None else {})}


def with_bindings(context: LayerContext, bindings: list[dict]) -> LayerContext:
    """@brief 换上最终私有绑定（动作 note 已按可用性填写）的同一上下文。"""
    return replace(context, bindings=bindings)
