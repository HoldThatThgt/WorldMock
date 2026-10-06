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
from typing import Any, TYPE_CHECKING

from worldmock.catalog.affordances import engine_affordances
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life import activity_control, admission, giving, same_moment, social, social_actions
from worldmock.life.activities import location_kinds_allow
from worldmock.life.basket import (meal_portion,
                                  validate_meal_use, validate_purchase_order)
from worldmock.life.commerce import held_quantity
from worldmock.exchange.constants import APP_USE_DEFAULT_SECONDS, FOOD_CONSTANTS
from worldmock.exchange.operators import delivery_fee_fen, delivery_seconds
from worldmock.exchange.types import CONTENT_KIND_LABELS, FOOD_CATEGORY_LABELS
from worldmock.life.control_items import CONTROL_TOOLS
from worldmock.life.durations import derived_activity_duration
from worldmock.life.guidance import Refusal, refusal_for_code, rejection_guidance
from worldmock.life.vocabulary import GIVE_WAY
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.models import (ActivityKind, ActivityStartParams, ActivityStatus, ConsumeItem, ConsumeRequest,
                                   CONSUME_DURATION_CAP_SECONDS, DERIVED_DURATION_CAP_SECONDS,
                                   PurchaseItem, PurchaseOrderRequest, SameMomentStage, WorldState,
                                   active_activities)
from worldmock.life.projection import food_energy_label, route_effort
from worldmock.life.readable import ACTIVITY_DOING_LABELS, clock_phrase, duration_phrase, money_phrase, portion_phrase
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.sessions import current_session
from worldmock.life.travel import planned_travel
from worldmock.life.work import (contract_view, validate_work_start, work_start_refusal,
                                 work_start_seconds, work_totals)

if TYPE_CHECKING:
    from worldmock.life.rules import LifeRuleEngine

#: 私有绑定类别 → 工具层目标类别（联系人与人物独立，邮件为 message，工作任务为 task）。
BINDING_KINDS = {"work_topic": "task", "location": "location", "supply": "product", "holding": "holding",
                 "app": "app", "lifestyle": "lifestyle",
                 "person": "person", "contact": "contact", "mail": "message", "self": "self"}
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
    ways = [candidate.action["label"]] if candidate.tool == "start_activity" else []
    named = ([context.state.activities[candidate.activity_id]]
             if candidate.tool in {"report_status", "conclude_activity"} else [])
    return same_moment.stage_refusal(stage, same_moment.request_kind(candidate.tool, actions, named, waiting, ways))


def _admission_context(context: LayerContext) -> admission.Context:
    """@brief 准入判定使用与受理相同的世界、可读名称和登记图。"""
    return admission.Context(state=context.state, agent_id=context.agent_id,
                             place_names={item.location_id: item.display_name for item in context.scenario.locations},
                             activity_names=context.names["activities"], work_topics=context.rules.work_topics,
                             graph=context.semantics.catalog.graph)


def _world_facts(context: LayerContext) -> admission.WorldFacts:
    """@brief 准入判定用的冻结目录（与受理取同一规则目录）。"""
    rules = context.rules
    config = next(item for item in context.scenario.agents if item.agent_id == context.agent_id)
    workplace = config.work_schedule.workplace_location_id if config.work_schedule is not None else None
    return admission.WorldFacts(work_topics=rules.work_topics, items=context.semantics.catalog.foods,
                                workplace_location_id=workplace)


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
    """@brief 出行：派生时长为一秒至一天，余额够付固定车费。"""
    agent = context.state.agents[context.agent_id]
    route, duration = _route(context, binding["object_id"], action["travel_mode"])
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


def _work_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 开工：距截止与剩余合同都不少于 60 秒，且在合同开放区间内。"""
    topic = context.rules.work_topic(binding["object_id"], context.agent_id)
    worked = work_totals(context.state, context.agent_id, topic.topic_id)
    refusal = work_start_refusal(topic, worked)
    if refusal is not None:
        return refusal
    config = next(item for item in context.scenario.agents if item.agent_id == context.agent_id)
    validate_work_start(config, topic, admission.present_location(context.state, context.agent_id))
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
    if not location_kinds_allow(frozenset({location.kind}) if location is not None else frozenset(), needed):
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
    """@brief 真实食品购买直接复用整单校验，价格与 app 绑定均来自目录。"""
    agent = context.state.agents[context.agent_id]
    item = PurchaseItem(store_id=binding["store_id"], food_id=binding["food_id"], quantity=1,
                        recipient_id=f"agent:{context.agent_id}")
    app_id = binding.get("app_id") if action["fulfillment"] == "delivery" else None
    request = PurchaseOrderRequest(items=(item,), fulfillment=action["fulfillment"],
                                   destination_id=agent.location_id, app_id=app_id)
    validate_purchase_order(context.state, context.semantics, context.agent_id, request)
    return None


def _consume_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 实际食品食用要求手边持有、合法份量及固定类别时长。"""
    agent, foods = context.state.agents[context.agent_id], context.semantics.catalog.foods
    food = foods.get(binding["object_id"])
    if food is None:
        return refusal_for_code("PRODUCT_NOT_FOUND")
    portion, seconds = meal_portion(food, action["use_mode"], held_quantity(agent, food.food_id))
    if not 60 <= seconds <= CONSUME_DURATION_CAP_SECONDS:
        return refusal_for_code("ACTIVITY_DURATION_INVALID")
    if portion <= 0:
        return refusal_for_code("PRODUCT_NOT_HELD")
    request = ConsumeRequest(items=(ConsumeItem(product_id=food.food_id, quantity=portion),), use_mode="eat",
                             duration_seconds=seconds)
    validate_meal_use(agent, foods, request)
    return None


def _give_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 给予：手边要持有这件东西，且身边至少有一个能给的人（与受理同一判定 giving.handover_refusal）；去向是否
    就是身边的人按受理时的去向判定。"""
    return (giving.held_refusal(context.state, context.agent_id, binding["object_id"])
            or giving.handover_refusal(context.state, context.scenario, context.agent_id))



def _app_check(context: LayerContext, binding: dict, action: dict) -> Refusal | None:
    """@brief 应用没有绑定内容时用与受理完全相同的拒绝合同。"""
    if not context.semantics.catalog.contents_by_app[binding["object_id"]]:
        return refusal_for_code("APP_CONTENT_EMPTY")
    return None


#: 内部标准动作 → 对象自身条件的判定。
_OBJECT_CHECKS = {"travel_to": _travel_check, "work": _work_check, "use_app": _app_check,
                  "read": _timed_check, "wait": _timed_check, "sleep": _sleep_check,
                  "do": _lifestyle_check, "chat": _chat_check, "call": _no_check, "purchase": _purchase_check,
                  "consume": _consume_check}


def _derived_seconds(context: LayerContext, binding: dict, action: dict) -> int:
    """@brief 持续时长与受理同源；应用固定约三十分钟，其余按活动规则计算。"""
    canonical = action["canonical_action"]
    kinds = {"sleep": ActivityKind.SLEEP, "use_app": ActivityKind.USE_APP,
             "read": ActivityKind.READ_MAIL, "wait": ActivityKind.WAIT}
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
        topic = context.rules.work_topic(row["object_id"], context.agent_id)
        target["contract"] = contract_view(topic, work_totals(context.state, context.agent_id, topic.topic_id))
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
        product = context.semantics.catalog.foods[row["object_id"]]
        agent = context.state.agents[context.agent_id]
        held = agent.inventory.get(row.get("location_id") or agent.location_id, {}).get(product.food_id, 0)
        portion, seconds = meal_portion(product, action["use_mode"], held)
        return {**facts, "duration": duration_phrase(seconds), "quantity": portion_phrase(portion)}
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
    """@brief 固定食品价格与配送费，配送时间由真实登记图计算。"""
    food = context.semantics.catalog.foods[row["food_id"]]
    delivery = action["fulfillment"] == "delivery"
    price = food.price_fen + (delivery_fee_fen() if delivery else 0)
    facts = {"price": money_phrase(price), "quantity": portion_phrase(1)}
    if delivery:
        agent = context.state.agents[context.agent_id]
        distance = context.semantics.catalog.shortest_m(row["store_id"], agent.location_id)
        expected = context.state.current_time + delivery_seconds(distance)
        when = clock_phrase(expected, context.state.current_time, context.scenario.timezone)
        facts["note"] = f"预计 {when} 送到"
    return facts


def _facts_seconds(context: LayerContext, row: dict, action: dict) -> int | None:
    """@brief 有时长动作的计划秒数：工作取距截止与剩余合同，生活活动取目录时长，交谈与通话取场景派生，其余同受理。"""
    canonical = action["canonical_action"]
    if canonical == "work":
        topic = context.rules.work_topic(row["object_id"], context.agent_id)
        worked = work_totals(context.state, context.agent_id, topic.topic_id)
        return work_start_seconds(topic, worked)
    if canonical == "do":
        item = next((entry for entry in context.lifestyle_items if entry.activity_id == row["object_id"]), None)
        return derived_activity_duration(ActivityKind.LIFESTYLE, context.state.agents[context.agent_id], item,
                                         context.scenario.activity_feedback_seconds) if item is not None else None
    if canonical in {"chat", "call"}:
        return social_actions.social_duration_seconds(canonical, context.scenario.activity_feedback_seconds)
    return _derived_seconds(context, row, action)


def _state(context: LayerContext, row: dict) -> str | None:
    """@brief 目标此刻的可读状态：持有份数与热量、食品热量、内容看过与否、在场的人在做什么、邮件已读与否。"""
    kind = row["kind"]
    if kind in {"supply", "holding"}:
        return _product_state(context, row)
    if kind == "app":
        return _app_state(context, row["object_id"])
    if kind == "person":
        return _person_state(context, int(str(row["object_id"]).split(":", 1)[1]))
    if kind == "mail":
        return "已读" if row["object_id"] in context.state.agents[context.agent_id].read_message_ids else "未读"
    return None


def _held_at_row(context: LayerContext, row: dict) -> float:
    """@brief 持有物目标所在地点（他处持有物取其地点，否则取此刻所在地点）的持有份数。"""
    place = row.get("location_id") or admission.present_location(context.state, context.agent_id)
    return context.state.agents[context.agent_id].inventory.get(place, {}).get(row["object_id"], 0)


def _product_state(context: LayerContext, row: dict) -> str:
    """@brief 食品目标写固定类别和每份热量，门店食品另写实际可用 app。"""
    food_id = row["food_id"] if row["kind"] == "supply" else row["object_id"]
    food = context.semantics.catalog.foods[food_id]
    state = f"{FOOD_CATEGORY_LABELS[food.category]}，每份约 {FOOD_CONSTANTS[food.category].energy_kcal:g} 千卡"
    if row["kind"] == "holding":
        state = f"持有 {portion_phrase(_held_at_row(context, row))}，{state}"
    else:
        apps = context.semantics.catalog.apps_by_store[row["store_id"]]
        if apps:
            names = "、".join(context.semantics.catalog.apps[app_id].name for app_id in apps)
            state += f"；可用{names}下单"
    return state




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
    last_report = None
    if activity.kind == ActivityKind.WORK:
        topic = context.rules.work_topic(activity.target_id, context.agent_id)
        contract = contract_view(topic, work_totals(context.state, context.agent_id, topic.topic_id))
        progress = f"已做{contract['done']}，还差{contract['remaining']}"
        reports = [item for item in context.state.activities.values() if item.agent_id == context.agent_id
                   and item.kind == ActivityKind.WORK and item.target_id == topic.topic_id and item.progress_note]
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


def _app_state(context: LayerContext, app_id: str) -> str:
    """@brief 当前会话未释放的推送显示标题与类型，否则显示真实内容条数。"""
    session = current_session(context.state, context.agent_id)
    push = session.app_pushes.get(app_id) if session is not None else None
    if push is not None and push.status != "released":
        content = context.semantics.catalog.contents[push.content_id]
        return f"有推送：《{content.title}》（{CONTENT_KIND_LABELS[content.kind]}）"
    return f"{len(context.semantics.catalog.contents_by_app[app_id])} 条内容"
