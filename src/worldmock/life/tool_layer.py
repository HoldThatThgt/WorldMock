"""@brief 工具层：本轮可操作的目标、各目标的动作与工具，以及可用性的单一来源（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。

目标由本轮私有绑定（projection.interaction_bindings）与进行中、刚结束的活动组成；暂停的活动只在待办层。每个列出的动作与
工具都带 available 与 reason：reason 由 refusal_for 按环境受理的同一顺序判定（菜单→决策到期→同刻阶段→待收尾→名额与出发前
→准入组→对象自身条件；名称工具为阶段→待收尾→控制或驾车→提交或收件人条件），取拒绝时的原文，所以提示与拒绝一字不差；
列出即可提交，不可用的由环境以同一原因业务拒绝。受理路径调用本模块的资格判定，并共用这里调用的各个领域规则函数。
层里另附只列名称的可调用索引 callable_index（由 life.callable_index 构建）：它不是目标，不带动作与可用性。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from worldmock.catalog.affordances import engine_affordances
from worldmock.categories import Action
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life import activity_control, admission, giving, same_moment, social, social_actions
from worldmock.life.activities import location_kinds_allow
from worldmock.life.basket import (delivery_expected_at, held_quantity, meal_portion, plan_delivery,
                                  validate_meal_use, validate_purchase_order)
from worldmock.life.category_contract import require_item_rules
from worldmock.life.content import restarts_from_beginning
from worldmock.life.control_items import CONTROL_TOOLS
from worldmock.life.durations import content_remaining_seconds, derived_activity_duration
from worldmock.life.discovery import DiscoveryContext, place_row
from worldmock.life.guidance import Refusal, refusal_for_code, rejection_guidance
from worldmock.life.vocabulary import GIVE_WAY
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.inventory_recipe import RecipeContext, prepare_inventory_recipe
from worldmock.life.models import (ActivityKind, ActivityStartParams, ActivityStatus, ConsumeItem, ConsumeRequest,
                                   CONSUME_DURATION_CAP_SECONDS, DERIVED_DURATION_CAP_SECONDS,
                                   PurchaseItem, PurchaseOrderRequest, SameMomentStage, WorldState,
                                   active_activities)
from worldmock.life.opening_hours import opening_status
from worldmock.life.projection import food_energy_label, route_effort
from worldmock.life.readable import ACTIVITY_DOING_LABELS, clock_phrase, duration_phrase, money_phrase, portion_phrase
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.storefront_state import current_quote
from worldmock.life.travel import planned_travel
from worldmock.life.work import (contract_view, submission_refusal, validate_work_start, work_start_refusal,
                                 work_start_seconds, work_totals)

#: 私有绑定类别 → 工具层目标类别（场景联系人并入 person，邮件为 message，工作任务为 task）。
BINDING_KINDS = {"work_task": "task", "location": "location", "supply": "product", "holding": "product",
                 "catalog": "catalog", "content": "content", "game": "game", "lifestyle": "lifestyle",
                 "person": "person", "contact": "person", "mail": "message", "self": "self"}
#: 地点开放情况的说法。
_OPENING_STATES = {"open": "开着", "closed": "关着", "unknown": "开放时间不明"}
#: 供给来源类型的说法（DataSource.kind：事实或场景假设）。
_SOURCE_KINDS = {"fact": "事实", "scenario": "场景假设"}
@dataclass(frozen=True, slots=True)
class LayerContext:
    """@brief 一次观察构建各层所用的只读世界切片，由 ObservationBuilder 每次观察构造一次。

    :param names: activity_control.visible_names 的结果（会话名与活动名）。
    :param bindings: 本次观察最终的 interaction_bindings。
    :param menu: 本轮有效交互菜单。
    :param lifestyle_items: 冻结生活活动目录（生活活动的时长与受理闭集）。
    """

    state: WorldState
    scenario: RuntimeScenario
    rules: LifeRuleEngine
    semantics: ScenarioSemantics
    agent_id: int
    names: dict[str, dict[str, str]]
    bindings: list[dict]
    menu: InteractionMenu
    lifestyle_items: tuple = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    """@brief 一个待判定可用性的世界请求：start_activity 的一项（绑定行与所选动作），或以名称为参数的一项工具。"""

    tool: str
    binding: dict | None = None
    action: dict | None = None
    activity_id: str | None = None
    task_id: str | None = None
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
    if candidate.tool == "submit_work":
        return (admission.hands_free_refusal(_admission_context(context), "submit work")
                or submission_refusal(context.state, context.agent_id, context.rules.tasks[candidate.task_id]))
    return admission.hands_free_refusal(_admission_context(context), "send a message")


def _stage_refusal(context: LayerContext, candidate: Candidate) -> Refusal | None:
    """@brief 同刻受限轮复核：按与受理相同的请求类判定本阶段是否开放。"""
    stage = context.state.agents[context.agent_id].same_moment_stage
    if stage == SameMomentStage.OPEN:
        return None
    waiting = frozenset(item.activity_id for item in activity_control.pending_conclusions(context.state,
                                                                                          context.agent_id))
    actions = [candidate.action["canonical_action"]] if candidate.tool == "start_activity" else []
    ways = [candidate.action["label"]] if candidate.tool == "start_activity" else []
    named = ([context.state.activities[candidate.activity_id]]
             if candidate.tool in {"report_status", "conclude_activity"} else [])
    return same_moment.stage_refusal(stage, same_moment.request_kind(candidate.tool, actions, named, waiting, ways))


def _admission_context(context: LayerContext) -> admission.Context:
    """@brief 准入判定的世界、可读名称（地点名、本轮活动名）与冻结路线（下一步只指向可达地点），与受理一致。"""
    return admission.Context(state=context.state, agent_id=context.agent_id,
                             place_names={item.location_id: item.name for item in context.scenario.locations},
                             activity_names=context.names["activities"], tasks=context.rules.tasks,
                             routes=context.scenario.routes)


def _world_facts(context: LayerContext) -> admission.WorldFacts:
    """@brief 准入判定用的冻结目录（与受理取同一规则目录）。"""
    rules = context.rules
    return admission.WorldFacts(tasks=rules.tasks, supplies=rules.supplies, items=rules.items,
                                contents=rules.contents)


def _start_refusal(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief start_activity 的一项：名额与出发前、整组准入，再核对对象自身条件。"""
    request = ([action["canonical_action"]], [action["label"]])
    refusal = activity_control.start_admission_refusal(context.state, context.agent_id, request,
                                                       context.names["activities"])
    if refusal is not None:
        return refusal
    item = admission.selection_item(binding, action, _world_facts(context))
    refusal = admission.group_refusal(_admission_context(context), [item])
    if refusal is not None:
        return refusal
    check = _give_check if action["label"] == GIVE_WAY else _OBJECT_CHECKS[action["canonical_action"]]
    try:
        return check(context, binding, action)
    except BusinessRuleError as error:
        guidance = rejection_guidance(error)
        return Refusal(str(error), guidance["reason"], guidance["next_step"] or "")


def _travel_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 出行：派生的计划时长在域内且不短于一分钟，余额够付路费。"""
    agent = context.state.agents[context.agent_id]
    route, duration = _route(context, binding["object_id"], action["travel_mode"])
    if not 1 <= duration <= DERIVED_DURATION_CAP_SECONDS:
        return refusal_for_code("ROUTE_DURATION_INVALID")
    if duration < 60:
        return refusal_for_code("ACTIVITY_TOO_SHORT")
    if context.state.accounts[agent.balance_account_id] < route.fare_fen:
        return refusal_for_code("INSUFFICIENT_TRAVEL_FUNDS")
    return None


def _route(context: LayerContext, destination_id: str, mode: str):
    """@brief 从人物当前地点到目的地的冻结路线（与受理同一路线选择）。"""
    draft = ActivityStartParams(activity_type=ActivityKind.TRAVEL, planned_duration_seconds=1,
                                destination_id=destination_id, travel_mode=mode)
    return planned_travel(context.scenario, context.semantics, context.state.agents[context.agent_id].location_id,
                          draft, context.state.current_time)


def _work_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 开工：距截止与剩余合同都不少于 60 秒，且在合同开放区间内。"""
    task = context.rules.tasks[binding["object_id"]]
    worked, _ = work_totals(context.state, context.agent_id, task.task_id)
    refusal = work_start_refusal(task, worked, context.state.current_time)
    if refusal is not None:
        return refusal
    now = context.state.current_time
    validate_work_start(context.state.agents[context.agent_id], task, now,
                        admission.present_location(context.state, context.agent_id))
    return None


def _timed_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 观看、游玩、阅读、浏览、等待：派生时长不短于一分钟。"""
    if _derived_seconds(context, binding, action) < 60:
        return refusal_for_code("ACTIVITY_DURATION_INVALID")
    return None


def _lifestyle_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 生活活动：在冻结目录中、时长不短于一分钟、此地环境合适。

    生活活动窗口已按当前地点施加地点类资格（地点类不符的条目不进窗口，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ），
    窗口里的条目不会再标「这里不适合做这件事」；这里仍与受理（rules.validate_activity_location）同一判定，
    给不经窗口的绑定（按名调用）同样的原因。
    """
    item = next((entry for entry in context.lifestyle_items if entry.activity_id == binding["object_id"]), None)
    if item is None:
        return refusal_for_code("LIFESTYLE_ACTIVITY_NOT_FOUND")
    if item.typical_duration_minutes * 60 < 60:
        return refusal_for_code("ACTIVITY_DURATION_INVALID")
    needed = context.rules.activity_location_kinds.get(item.activity_id)
    if needed is None:
        return refusal_for_code("LIFESTYLE_ACTIVITY_NOT_FOUND")
    present = admission.present_location(context.state, context.agent_id)
    location = context.rules.locations.get(present) if present is not None else None
    if not location_kinds_allow(frozenset(location.location_kinds) if location is not None else frozenset(), needed):
        return refusal_for_code("ELIGIBILITY_LOCATION_KIND_MISMATCH")
    return None


def _sleep_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 睡觉：按睡眠需要派生的时长不短于一分钟。"""
    if _derived_seconds(context, binding, action) < 60:
        return refusal_for_code("ACTIVITY_TOO_SHORT")
    return None


def _chat_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 交谈：不是接受对方邀请时，对方要醒着且不在游戏中。"""
    partner = binding["object_id"]
    invitation = social_actions.pair_chat(context.state, int(partner.split(":", 1)[1]), f"agent:{context.agent_id}")
    if invitation is None and not social.can_talk(social_actions.partner_presence(context.state, partner)):
        return refusal_for_code("CHAT_PARTNER_NOT_AVAILABLE")
    return None


def _no_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 通话：面向场景联系人，时长由场景派生，没有对象自身的条件。"""
    return None


def _purchase_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 取得：履约方式、报价、库存、营业、送达与余额，按整单结算的顺序判定。"""
    state, agent = context.state, context.state.agents[context.agent_id]
    supply = context.rules.supplies[binding["object_id"]]
    fulfillment = action["fulfillment"]
    present = admission.present_location(state, context.agent_id)
    valid = (supply.location_id == present and "pickup" in supply.fulfillment_modes if fulfillment == "pickup"
             else "delivery" in supply.fulfillment_modes and agent.location_id in supply.delivery_destination_ids)
    if not valid:
        return refusal_for_code("FULFILLMENT_NOT_SUPPORTED")
    quote = current_quote(state, supply.supply_id)
    if not (binding.get("quote") or {}).get("price_version") or quote is None:
        return refusal_for_code("SUPPLY_QUOTE_UNKNOWN")
    if state.supply_stock[supply.supply_id] < 1:
        return refusal_for_code("OUT_OF_STOCK")
    location = next(item for item in context.scenario.locations if item.location_id == supply.location_id)
    status = opening_status(location, state.current_time, context.scenario.timezone,
                            state.opening_exceptions[supply.location_id])
    if status != "open":
        return refusal_for_code("MERCHANT_OPENING_UNKNOWN" if status == "unknown" else "MERCHANT_CLOSED")
    if fulfillment == "delivery":
        plan_delivery(context.semantics, (supply,), agent.location_id, state.current_time)
    amount = context.semantics.purchase_amount(supply.product_id, quote.price_fen, 1)
    if state.accounts[agent.balance_account_id] < amount:
        return refusal_for_code("INSUFFICIENT_FUNDS")
    item = PurchaseItem(supply_id=supply.supply_id, quantity=1, price_version=binding["quote"]["price_version"],
                        recipient_id=f"agent:{context.agent_id}")
    validate_purchase_order(state, context.semantics, context.agent_id, PurchaseOrderRequest(
        items=(item,), fulfillment=fulfillment, destination_id=agent.location_id))
    return None


def _consume_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 食用、使用与制作：按份量折算的时长、使用方式、品类规则与地点，再核对手边是否持有（食用有就行，不足一份
    也可以吃；使用要一件；配方另核输入）。份量与时长和受理共用 basket.meal_portion。"""
    agent, products = context.state.agents[context.agent_id], context.rules.items
    product = products.get(binding["object_id"])
    if product is None:
        return refusal_for_code("PRODUCT_NOT_FOUND")
    mode = action["use_mode"]
    portion, seconds = meal_portion(product, mode, held_quantity(agent, product.product_id))
    if not 60 <= seconds <= CONSUME_DURATION_CAP_SECONDS:
        return refusal_for_code("ACTIVITY_DURATION_INVALID")
    if mode not in product.use_modes:
        return refusal_for_code("ACTION_REQUEST_INVALID")
    if portion <= 0:
        return refusal_for_code("PRODUCT_NOT_HELD")
    request = ConsumeRequest(items=(ConsumeItem(product_id=product.product_id, quantity=portion),), use_mode=mode,
                             duration_seconds=seconds)
    matches = require_item_rules(context.semantics.catalog, products, [product.product_id], Action(f"consume:{mode}"))
    validate_meal_use(agent, products, request)
    recipe = prepare_inventory_recipe(agent, RecipeContext(context.semantics.catalog, products,
                                                           context.scenario.recipe_output_bindings),
                                      request, matches[0])
    if recipe is None and held_quantity(agent, product.product_id) + 1e-9 < portion:
        return refusal_for_code("PRODUCT_NOT_HELD")
    return None


def _give_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 给予：手边要持有这件东西，且身边至少有一个能给的人（与受理同一判定 giving.handover_refusal）；去向是否
    就是身边的人按受理时的去向判定。"""
    return (giving.held_refusal(context.state, context.agent_id, binding["object_id"])
            or giving.handover_refusal(context.state, context.scenario, context.agent_id))


#: 内部标准动作 → 对象自身条件的判定。
_OBJECT_CHECKS = {"travel_to": _travel_check, "work": _work_check, "watch": _timed_check, "play": _timed_check,
                  "read": _timed_check, "browse": _timed_check, "wait": _timed_check, "sleep": _sleep_check,
                  "do": _lifestyle_check, "chat": _chat_check, "call": _no_check, "purchase": _purchase_check,
                  "consume": _consume_check}


def _derived_seconds(context: LayerContext, binding: dict, action: dict) -> int:
    """@brief 与受理共用派生秒数：内容取剩余或全长，TextWorld 游戏与阅读浏览等待取反馈间隔。"""
    canonical = action["canonical_action"]
    kinds = {"sleep": ActivityKind.SLEEP, "watch": ActivityKind.WATCH, "play": ActivityKind.PLAY_GAME,
             "read": ActivityKind.READ_MAIL, "browse": ActivityKind.BROWSE, "wait": ActivityKind.WAIT}
    target = binding["object_id"]
    item = context.rules.content(target) if canonical == "watch" else None
    return derived_activity_duration(kinds[canonical], context.state.agents[context.agent_id], item,
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
    callable_index（life.callable_index 按本轮呈现窗口构建，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ）：它不
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
    if kind == "task":
        task = context.rules.tasks[row["object_id"]]
        target["contract"] = contract_view(task, work_totals(context.state, context.agent_id, task.task_id),
                                           context.state.current_time, (context.scenario.timezone,
                                           context.rules.locations[task.required_location_id].name))
    target["actions"] = [_verb_action(context, row, action) for action in row["actions"]]
    if row["kind"] == "supply" and target["actions"]:
        first = target["actions"][0]
        first["note"] = "；".join(filter(None, (first["note"], _source_note(context, row["object_id"]))))
    if kind == "task":
        target["tools"] = [_tool_action(context, Candidate("submit_work", task_id=row["object_id"]))]
    elif kind == "person":
        target["tools"] = [_tool_action(context, Candidate("send_message", recipient_id=row["object_id"]))]
    else:
        target["tools"] = []
    return target


def _source_note(context: LayerContext, supply_id: str) -> str:
    """@brief 门店商品目标的来源标记：类型、来源引用与事实截止日，取供给声明的来源（地图与门店规格「地点发现与候选
    呈现」；docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 冻结条款修订 #24：落在工具层商品目标动作的 note，每个目标只写一次）。"""
    source = context.rules.supplies[supply_id].source
    return f"来源：{_SOURCE_KINDS[source.kind]}（{source.url}），事实截止 {source.fact_cutoff.isoformat()}"


def _tool_action(context: LayerContext, candidate: Candidate) -> dict:
    """@brief 一项以目标名称为参数的工具及其可用性。"""
    return {"tool": candidate.tool, **availability(context, candidate)}


def _verb_action(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief start_activity 的一项动词与方式：可用性，以及价格、耗时、数量与补充说明（没有的写 null）。"""
    facts = _action_facts(context, row, action)
    return {"action": action["verb"], "way": action["label"],
            **availability(context, Candidate("start_activity", row, action)), **facts}


def _action_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 动作的价格、耗时、数量与说明：出行写路费、时长与费力，取得写价格与送达，食用写份数与时长。"""
    facts = {"price": None, "duration": None, "quantity": None, "note": None}
    canonical = action["canonical_action"]
    if canonical == "travel_to":
        return {**facts, **_travel_facts(context, row, action)}
    if canonical == "purchase":
        return {**facts, **_purchase_facts(context, row, action)}
    if action["label"] == GIVE_WAY:
        return {**facts, "quantity": portion_phrase(giving.give_quantity(_held_at_row(context, row)))}
    if canonical == "consume":
        product = context.rules.items[row["object_id"]]
        agent = context.state.agents[context.agent_id]
        held = agent.inventory.get(row.get("location_id") or agent.location_id, {}).get(product.product_id, 0)
        portion, seconds = meal_portion(product, action["use_mode"], held)
        return {**facts, "duration": duration_phrase(seconds), "quantity": portion_phrase(portion)}
    if canonical == "watch":
        item = context.rules.content(row["object_id"])
        progress = context.state.agents[context.agent_id].content_progress.get(item.item_id)
        seconds = content_remaining_seconds(item, progress)
        return {**facts, "duration": duration_phrase(seconds)}
    seconds = _facts_seconds(context, row, action)
    return {**facts, "duration": duration_phrase(seconds) if seconds is not None and seconds >= 60 else None}


def _travel_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 出行的路费（免费不写）、按出发时刻折算的时长与费力程度。"""
    route, seconds = _route(context, row["object_id"], action["travel_mode"])
    key = engine_affordances().met_lookup_key("travel", travel_mode=action["travel_mode"])
    effort = route_effort(context.semantics.body.activity_met[key])
    return {"price": money_phrase(route.fare_fen) if route.fare_fen > 0 else None,
            "duration": duration_phrase(seconds), "note": f"费力程度：{effort}"}


def _purchase_facts(context: LayerContext, row: dict, action: dict) -> dict:
    """@brief 取得的价格（报价已知时）与份数；远程下单另写送到这里的预计时刻。"""
    price = (row.get("quote") or {}).get("purchase_price_fen")
    facts = {"price": money_phrase(price) if price is not None else None, "quantity": portion_phrase(1)}
    if action["fulfillment"] == "delivery":
        agent = context.state.agents[context.agent_id]
        expected = delivery_expected_at(context.semantics, context.rules.supplies[row["object_id"]],
                                        agent.location_id, context.state.current_time)
        if expected is not None:
            when = clock_phrase(expected, context.state.current_time, context.scenario.timezone)
            facts["note"] = f"预计 {when} 送到"
    return facts


def _facts_seconds(context: LayerContext, row: dict, action: dict) -> int | None:
    """@brief 有时长动作的计划秒数：工作取距截止与剩余合同，生活活动取目录时长，交谈与通话取场景派生，其余同受理。"""
    canonical = action["canonical_action"]
    if canonical == "work":
        task = context.rules.tasks[row["object_id"]]
        worked, _ = work_totals(context.state, context.agent_id, task.task_id)
        return work_start_seconds(task, worked, context.state.current_time)
    if canonical == "do":
        item = next((entry for entry in context.lifestyle_items if entry.activity_id == row["object_id"]), None)
        return derived_activity_duration(ActivityKind.LIFESTYLE, context.state.agents[context.agent_id], item,
                                         context.scenario.activity_feedback_seconds) if item is not None else None
    if canonical in {"chat", "call"}:
        return social_actions.social_duration_seconds(canonical, context.scenario.activity_feedback_seconds)
    return _derived_seconds(context, row, action)


def _state(context: LayerContext, row: dict) -> str | None:
    """@brief 目标此刻的可读状态：地点开放情况、持有份数与热量、食品热量、内容看过与否、在场的人在做什么、邮件已读与否。"""
    kind = row["kind"]
    if kind == "location":
        location = next(item for item in context.scenario.locations if item.location_id == row["object_id"])
        agent = context.state.agents[context.agent_id]
        status = place_row(DiscoveryContext(context.state, context.scenario, context.semantics, agent),
                           location)["opening_status"]
        return _OPENING_STATES[status]
    if kind in {"supply", "holding"}:
        return _product_state(context, row)
    if kind in {"content", "game"} and row["object_id"] in context.rules.contents:
        return _content_state(context, row["object_id"])
    if kind == "person":
        return _person_state(context, int(str(row["object_id"]).split(":", 1)[1]))
    if kind == "mail":
        return "已读" if row["object_id"] in context.state.agents[context.agent_id].read_message_ids else "未读"
    return None


def _held_at_row(context: LayerContext, row: dict) -> float:
    """@brief 持有物目标所在地点（他处持有物取其地点，否则取此刻所在地点）的持有份数。"""
    place = row.get("location_id") or admission.present_location(context.state, context.agent_id)
    return context.state.agents[context.agent_id].inventory.get(place, {}).get(row["object_id"], 0)


def _product_state(context: LayerContext, row: dict) -> str | None:
    """@brief 商品目标的状态：持有物写持有份数，食品另写每份热量。"""
    if row["kind"] == "supply":
        product = context.rules.items[context.rules.supplies[row["object_id"]].product_id]
        parts = []
    else:
        product = context.rules.items[row["object_id"]]
        parts = [f"持有 {portion_phrase(_held_at_row(context, row))}"]
    if "eat" in product.use_modes:
        parts.append(food_energy_label(product.energy_kcal_per_unit))
    return "，".join(parts) or None


def _content_state(context: LayerContext, item_id: str) -> str:
    """@brief 内容目标的状态：没看过、看过一部分或看完了（播客写「听」）。"""
    item = context.rules.content(item_id)
    verb = "听" if item.kind == "podcast_episode" else "看"
    progress = context.state.agents[context.agent_id].content_progress.get(item_id)
    if progress is None or progress.position_seconds <= 0:
        return f"没{verb}过"
    if restarts_from_beginning(progress.position_seconds, item.duration_seconds):
        return f"{verb}完了"
    return f"{verb}过一部分"


def _person_state(context: LayerContext, agent_id: int) -> str:
    """@brief 在场的人此刻在做什么（只写活动种类的说法，不含对方的身体与内部身份）。"""
    kinds = sorted({item.kind.value for item in active_activities(context.state, agent_id)})
    if not kinds:
        return "没在做什么"
    return "正在" + "、".join(ACTIVITY_DOING_LABELS[kind] for kind in kinds)


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
    state = f"{activity_control.ending_phrase(activity)}，{progress}" if pending else progress
    tools = [_tool_action(context, Candidate(tool, activity_id=activity.activity_id)) for tool in CONTROL_TOOLS]
    return {"name": context.names["activities"][activity.activity_id], "kind": "activity",
            "phase": activity_control.PHASE_PENDING if pending else activity_control.PHASE_ACTIVE,
            "state": state, "actions": [], "tools": tools}


def with_bindings(context: LayerContext, bindings: list[dict]) -> LayerContext:
    """@brief 换上最终私有绑定（动作 note 已按可用性填写）的同一上下文。"""
    return replace(context, bindings=bindings)
