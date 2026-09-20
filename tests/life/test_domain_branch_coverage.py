"""@brief life 领域簇行为测试：补 commerce/basket/social_actions/activities/
rules/content/work/work_review/game/travel/semantics/motivation/narration/social
的缺分支与未覆盖公共接口（C4c 覆盖率达标单元）。

只新增测试，不改生产代码。断言对照的规格章节：`docs/CONTRACTS.md`「请求字段与结算检查」、
`docs/dev/SPEC-life-runtime.md`「具体活动结算」「内容提供行为」「社交最小可行版」、
`docs/dev/SPEC-life-realism.md`「动机层」。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from worldmock.catalog import (
    Catalog,
    ExchangeObject,
    ResourceDefinition,
    load_catalog,
)
from worldmock.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.life import felt, social
from worldmock.life.activities import (
    ActivityOrigin,
    activity_factors,
    advance_activity,
    change_activity,
    continue_activity,
    truncate_activity,
)
from worldmock.life.basket import (
    apply_meal_progress,
    change_inventory,
    meal_duration,
    meal_food_rate,
    purchase_order,
    validate_meal_use,
)
from worldmock.life.commerce import deliver_order, purchase
from worldmock.life.content import (
    load_content_titles,
    load_lifestyle_items,
    load_subtitles,
    mail_headers,
    reveal_mail,
)
from worldmock.life.game import (
    GameOutcomeUnknown,
    new_game_session,
    run_game_command,
    validate_game,
    verify_game_progress,
)
from worldmock.life.models import (
    ActivityChange,
    ActivityKind,
    ActivityState,
    ActivityStatus,
    AgentState,
    BodyState,
    ChangeActivityRequest,
    ConsumeItem,
    ConsumeRequest,
    ContinueActivityRequest,
    FulfillmentKind,
    GameProgress,
    PurchaseItem,
    PurchaseOrderRequest,
    PurchaseRequest,
    StartActivityRequest,
    StartSocialActivityRequest,
    TravelMode,
    WorldState,
)
from worldmock.catalog.affordances import default_affordances
from worldmock.life.motivation import (
    MotivationContext,
    MotivationTransforms,
    _browse_candidate,
    _continue_candidate,
    _entertainment_candidates,
    _entertainment_delta,
    _feasible_food_rows,
    _sleep_candidate,
    _sleep_deficit_delta,
    _softmax,
    _travel_candidate,
    _work_candidates,
    load_motivation_transforms,
)
from worldmock.life.narration import render_scene
from worldmock.life.scenario import (
    BodyConfig,
    ContentItemConfig,
    GameConfig,
    LifeScenario,
    LocationConfig,
    MessageConfig,
    MotivationProfile,
    ProductConfig,
    ProductReference,
    RouteConfig,
    WorkTaskConfig,
    parse_scenario_document,
)
from worldmock.life.semantics import (
    ScenarioSemantics,
    assemble_semantics,
    load_runtime_scenario,
)
from worldmock.life.social_actions import (
    advance_social,
    chat_accepted,
    partner_presence,
    social_activity_change,
    start_call,
    start_chat,
)
from worldmock.life.travel import (
    LocationTrackRequest,
    route_for,
    sample_location_track,
    update_travel,
    validate_routes,
)
from worldmock.life.work import (
    load_work_input,
    load_work_oracle,
    prepare_work_submission,
    validate_work_start,
)
from worldmock.life.work_review import (
    DeliveryAssessment,
    PreparedWorkReview,
    ReferenceAssessment,
    WorkReviewer,
    WorkReviewDecision,
    WorkReviewOutputError,
    parse_work_review,
    prepare_work_review,
    public_work_review,
)

TIMEZONE = "Asia/Shanghai"
#: 2023-11-14 22:13 +08：夜间，配合 0-24 营业口径的测试地点恒为营业中。
CURRENT = 1_700_000_000
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 共享构造器（独立构造的纯域夹具，不依赖引擎）
# ---------------------------------------------------------------------------

def _body() -> BodyConfig:
    return BodyConfig.model_validate({
        "mass_kg": 65, "initial_energy_kcal": 1600, "maximum_energy_kcal": 2400,
        "initial_sleep_debt_hours": 0,
        "basal_kcal_per_hour": 65, "awake_sleep_debt_per_hour": 0.125,
        "sleep_recovery_per_hour": 0.125, "entertainment_need_per_awake_hour": 0.02,
        "hunger_threshold_kcal": 800, "emergency_threshold_kcal": 400,
        "work_efficiency_factors": [
            {"input_resource": "sleep_debt_hours",
             "curve": [{"input_value": 0, "output_value": 1.0},
                       {"input_value": 16, "output_value": 0.5}]},
            {"input_resource": "energy_kcal",
             "curve": [{"input_value": 0, "output_value": 0.5},
                       {"input_value": 400, "output_value": 0.8},
                       {"input_value": 800, "output_value": 1.0}]}],
        "food_efficiency_curve": [{"input_value": 0, "output_value": 1.0},
                                  {"input_value": 16, "output_value": 0.5}],
        "activity_met": {"wait": 1.0, "sleep": 1.0, "work": 1.5, "watch": 1.2,
                         "play_game": 1.3, "travel:driving": 1.5, "travel:walking": 2.5,
                         "read_mail": 1.1, "browse": 1.2, "consume": 1.3},
        "entertainment_relief_per_hour": {"watch": 0.15, "play_game": 0.2},
        "work_notes_multiplier": 1.2, "source_kind": "test"})


def _agent(**overrides) -> AgentState:
    payload = dict(
        agent_id=1, name="Lin", location_id="home", balance_account_id="agent:1",
        next_decision_at=0,
        body=BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=4.0,
                       entertainment_need=0.5),
        skill_levels={"ami": 0.8}, taste_weights={"movie-0001": 0.8})
    payload.update(overrides)
    return AgentState(**payload)


def _product(product_id="food-1", price=800, kcal=600, location="market",
             duration=900, use_modes=("eat",), consumable=True, effect="fed",
             required_location=None) -> ProductConfig:
    serving = {"serving_quantity": 1.0, "serving_unit": "serving"} if "eat" in use_modes else {}
    return ProductConfig.model_validate({
        "product_id": product_id, "name": "Test food", "merchant_account_id": "merchant:g",
        "category_id": "food" if "eat" in use_modes else "durable",
        "merchant_location_id": location, "price_fen": price, "price_version": "v1",
        "initial_stock": 5, "energy_kcal_per_unit": kcal if "eat" in use_modes else 0,
        "use_duration_seconds": duration, "use_modes": list(use_modes),
        "consumable": consumable, "effect": effect,
        "required_location_id": required_location,
        "delivery_delay_seconds": 600, "source_kind": "test", "inventory_unit": "item",
        **serving})


def _route(route_id="r1", origin="market", destination="home", mode="driving",
           seconds=600, fare=600, response_path="routes/a.json",
           response_sha256="a" * 64) -> RouteConfig:
    return RouteConfig.model_validate({
        "route_id": route_id, "origin_id": origin, "destination_id": destination,
        "mode": mode, "distance_meters": 3000, "duration_seconds": seconds,
        "fare_fen": fare, "fare_source_kind": "scenario",
        "response_path": response_path, "response_sha256": response_sha256,
        "source_url": "https://example.com"})


def _location(location_id="market", open_hour=0, close_hour=24) -> LocationConfig:
    return LocationConfig.model_validate({
        "location_id": location_id, "name": location_id, "kind": "shop",
        "longitude": 121.4, "latitude": 31.2,
        "open_hour": open_hour, "close_hour": close_hour})


def _life_scenario(products=(), routes=(), locations=(), timezone=TIMEZONE) -> LifeScenario:
    return LifeScenario.model_construct(
        products=tuple(products), routes=tuple(routes),
        locations=tuple(locations), timezone=timezone)


def _world(agent: AgentState, *, accounts=None, stock=None, current_time=CURRENT,
           activities=None, deliveries=None) -> WorldState:
    return WorldState.model_construct(
        agents={agent.agent_id: agent},
        accounts=accounts if accounts is not None else {"agent:1": 100_000, "merchant:g": 0},
        merchant_stock=stock if stock is not None else {"food-1": 5},
        current_time=current_time, paid_task_ids=set(),
        activities=activities if activities is not None else {},
        deliveries=deliveries if deliveries is not None else {},
        pending_events=[], version=1)


def _activity(**overrides) -> ActivityState:
    payload = dict(
        activity_id="a1", agent_id=1, kind=ActivityKind.WATCH,
        status=ActivityStatus.ACTIVE, started_at=0, planned_end_at=600,
        last_settled_at=0, elapsed_seconds=0, planned_duration_seconds=600,
        origin_action_id="act", origin_decision_id="dec", origin_request_event_id="req",
        data={})
    payload.update(overrides)
    return ActivityState.model_construct(**payload)


def _origin() -> ActivityOrigin:
    return ActivityOrigin("act", "dec", "req")


def _consume(product_id="food-1", quantity=1, use_mode="eat", duration=900) -> ConsumeRequest:
    return ConsumeRequest(items=(ConsumeItem(product_id=product_id, quantity=quantity),),
                          use_mode=use_mode, duration_seconds=duration)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# commerce：单件购买/配送/按进度使用（`docs/CONTRACTS.md`「请求字段与结算检查」）
# ---------------------------------------------------------------------------

def test_commerce_purchase_rejects_unknown_product_without_mutation() -> None:
    """单件购买目录外商品按业务拒绝（PRODUCT_NOT_FOUND）。"""
    agent, scenario = _agent(), _life_scenario(products=[_product()])
    state = _world(agent)
    frozen = state.model_dump(mode="json")
    request = PurchaseRequest(product_id="missing", quantity=1, fulfillment="delivery",
                              destination_id="home", price_version="v1")
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        purchase(state, scenario, 1, request)
    assert state.model_dump(mode="json") == frozen


def test_commerce_pickup_purchase_delivers_immediately_and_schedules_nothing() -> None:
    """到店取货当场入库存且不产生配送事件（结算检查表：履约方式）。"""
    agent = _agent(location_id="market", inventory={})
    scenario = _life_scenario(products=[_product()], locations=[_location("market")])
    request = PurchaseRequest(product_id="food-1", quantity=2, fulfillment="pickup",
                              destination_id="market", price_version="v1")
    outcome = purchase(_world(agent), scenario, 1, request)
    assert outcome.delivery.status == "delivered"
    assert outcome.scheduled is None
    assert outcome.state.pending_events == []
    assert outcome.state.agents[1].inventory["market"]["food-1"] == 2
    assert outcome.state.accounts["agent:1"] == 100_000 - 2 * _product().price_fen
    assert outcome.state.merchant_stock["food-1"] == 3


@pytest.mark.parametrize(
    ("accounts", "locations", "destination", "fulfillment", "code"),
    (
        # 资金不足：拒绝整单且不动库存
        ({"agent:1": 100, "merchant:g": 0}, [_location("market")], "home", "delivery",
         "INSUFFICIENT_FUNDS"),
        # 商家按场景本地小时冻结营业：22 点对 23-24 营业的商家已关门
        ({"agent:1": 100_000, "merchant:g": 0}, [_location("market", open_hour=23, close_hour=24)],
         "home", "delivery", "MERCHANT_CLOSED"),
        # 目的地不在场景地点闭集
        ({"agent:1": 100_000, "merchant:g": 0}, [_location("market")], "nowhere", "delivery",
         "DESTINATION_NOT_FOUND"),
        # 取货地点必须同时是商家地点与人物所在地
        ({"agent:1": 100_000, "merchant:g": 0}, [_location("market")], "market", "pickup",
         "PICKUP_LOCATION_REQUIRED"),
    ),
)
def test_commerce_purchase_resource_boundaries(accounts, locations, destination,
                                               fulfillment, code) -> None:
    """单件购买的报价外资源边界逐项 fail-closed。"""
    agent = _agent(location_id="home")
    scenario = _life_scenario(products=[_product()], locations=locations)
    request = PurchaseRequest(product_id="food-1", quantity=1, fulfillment=fulfillment,
                              destination_id=destination, price_version="v1")
    with pytest.raises(BusinessRuleError, match=code):
        purchase(_world(agent, accounts=accounts), scenario, 1, request)


def test_commerce_delivery_requires_frozen_driving_route_across_locations() -> None:
    """跨地点配送没有唯一冻结驾驶路线时拒绝（不能静默瞬移商品）。"""
    agent = _agent(location_id="home")
    scenario = _life_scenario(products=[_product()], locations=[_location("market"),
                                                               _location("home")],
                              routes=[])
    request = PurchaseRequest(product_id="food-1", quantity=1, fulfillment="delivery",
                              destination_id="home", price_version="v1")
    with pytest.raises(BusinessRuleError, match="DELIVERY_ROUTE_NOT_FOUND"):
        purchase(_world(agent), scenario, 1, request)


def test_commerce_deliver_order_rejects_unknown_order() -> None:
    """到期配送对未知订单按业务拒绝。"""
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_FOUND"):
        deliver_order(_world(_agent()), "order-missing")


def _use_activity(request: ConsumeRequest, **overrides) -> ActivityState:
    data = {"request": request.model_dump(mode="json")}
    return _activity(kind=ActivityKind.CONSUME, planned_end_at=request.duration_seconds,
                     planned_duration_seconds=request.duration_seconds, data=data,
                     **overrides)


# ---------------------------------------------------------------------------
# basket：整单购买与多件进食（`docs/dev/SPEC-life-realism.md`「商务与进食」）
# ---------------------------------------------------------------------------

def _order(product_ids=("food-1",), destination="home",
           fulfillment=FulfillmentKind.DELIVERY, quantities=None) -> PurchaseOrderRequest:
    items = tuple(
        PurchaseItem(product_id=pid, quantity=(quantities or {}).get(pid, 1),
                     price_version="v1")
        for pid in product_ids)
    return PurchaseOrderRequest(items=items, fulfillment=fulfillment,
                                destination_id=destination)


def test_basket_pickup_order_adds_inventory_without_delivery_events() -> None:
    """整单取货逐件入库存且不排配送事件。"""
    agent = _agent(location_id="market")
    scenario = _life_scenario(products=[_product()], locations=[_location("market")])
    outcome = purchase_order(_world(agent), scenario, 1, _order(destination="market",
                                                                fulfillment=FulfillmentKind.PICKUP))
    assert all(order.status == "delivered" for order in outcome.orders)
    assert outcome.scheduled == ()
    assert outcome.state.agents[1].inventory["market"]["food-1"] == 1


def test_basket_order_rejects_item_count_out_of_contract_range() -> None:
    """整单件数必须 1-5：六件整单拒绝。"""
    agent = _agent()
    scenario = _life_scenario(products=[_product()], locations=[_location("market")])
    with pytest.raises(BusinessRuleError, match="ORDER_ITEM_COUNT_OUT_OF_RANGE"):
        purchase_order(_world(agent), scenario, 1, _order(product_ids=("food-1",) * 6))


def test_basket_order_rejects_unknown_product_item() -> None:
    """整单内任一件目录外商品即整单拒绝。"""
    agent = _agent()
    scenario = _life_scenario(products=[_product()], locations=[_location("market")])
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        purchase_order(_world(agent), scenario, 1, _order(product_ids=("missing",)))


def test_basket_order_rejects_mixed_merchants() -> None:
    """跨商家整单拒绝：一次付款一次配送无法跨商家成立。"""
    agent = _agent()
    other = _product(product_id="food-2")
    other = other.model_copy(update={"merchant_account_id": "merchant:other"})
    scenario = _life_scenario(products=[_product(), other], locations=[_location("market")])
    with pytest.raises(BusinessRuleError, match="ORDER_MERCHANT_MISMATCH"):
        purchase_order(_world(agent), scenario, 1,
                       _order(product_ids=("food-1", "food-2")))


def test_basket_order_requires_delivery_route_for_remote_merchant() -> None:
    """商家到目的地缺冻结驾驶路线时整单拒绝。"""
    agent = _agent(location_id="home")
    scenario = _life_scenario(products=[_product(location="market")],
                              locations=[_location("market"), _location("home")],
                              routes=[])
    with pytest.raises(BusinessRuleError, match="DELIVERY_ROUTE_NOT_FOUND"):
        purchase_order(_world(agent), scenario, 1, _order())


def test_basket_change_inventory_rejects_over_consume() -> None:
    """负值变动超过持有量即 PRODUCT_NOT_HELD（其他地点库存不救场）。"""
    agent = _agent(location_id="home", inventory={"home": {"food-1": 1.0},
                                                 "market": {"food-1": 5.0}})
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_HELD"):
        change_inventory(agent, "food-1", -2.0)


def test_basket_meal_duration_enforces_longest_and_catalog_contract() -> None:
    """多件进食时长取最长者；目录外商品与错误时长拒绝。"""
    products = {"food-1": _product(duration=900),
                "food-2": _product(product_id="food-2", duration=1500)}
    both = ConsumeRequest(items=(ConsumeItem(product_id="food-1", quantity=1),
                                 ConsumeItem(product_id="food-2", quantity=1)),
                          use_mode="eat", duration_seconds=1500)
    assert meal_duration(products, both) == 1500
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        meal_duration(products, ConsumeRequest(
            items=(ConsumeItem(product_id="missing", quantity=1),),
            use_mode="eat", duration_seconds=1500))
    with pytest.raises(BusinessRuleError, match="MEAL_DURATION_MUST_BE_LONGEST"):
        meal_duration(products, ConsumeRequest(
            items=(ConsumeItem(product_id="food-1", quantity=1),),
            use_mode="eat", duration_seconds=600))
    use = ConsumeRequest(items=(ConsumeItem(product_id="food-1", quantity=1),),
                         use_mode="use", duration_seconds=600)
    with pytest.raises(BusinessRuleError, match="PRODUCT_USE_DURATION_MISMATCH"):
        meal_duration(products, use)


@pytest.mark.parametrize(
    ("meal_request", "code"),
    (
        (ConsumeRequest(items=tuple(ConsumeItem(product_id="food-1", quantity=1)
                                    for _ in range(4)), use_mode="eat",
                        duration_seconds=900), "MEAL_ITEM_COUNT_OUT_OF_RANGE"),
        (ConsumeRequest(items=(ConsumeItem(product_id="food-1", quantity=1),
                               ConsumeItem(product_id="food-2", quantity=1)),
                        use_mode="use", duration_seconds=900), "NON_FOOD_USE_SINGLE_ITEM"),
    ),
)
def test_basket_validate_meal_item_count_and_non_food_single(meal_request, code) -> None:
    """进食 1-3 件、非食品单件（`docs/dev/SPEC-life-realism.md`「商务与进食」）。"""
    products = {"food-1": _product()}
    with pytest.raises(BusinessRuleError, match=code):
        validate_meal_use(_agent(), products, meal_request)


def test_basket_validate_meal_rejects_unknown_product() -> None:
    """进食校验对目录外商品拒绝。"""
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        validate_meal_use(_agent(), {}, _consume())


def test_basket_validate_meal_rejects_mode_and_location_violations() -> None:
    """使用方式不符与要求地点不符分别拒绝。"""
    mode_product = _product(use_modes=("eat",))
    with pytest.raises(BusinessRuleError, match="PRODUCT_USE_MODE_NOT_ALLOWED"):
        validate_meal_use(_agent(), {"food-1": mode_product},
                      _consume(use_mode="use"))
    bound = _product(product_id="food-k", required_location="kitchen")
    holder = _agent(inventory={"home": {"food-k": 1.0}})
    with pytest.raises(BusinessRuleError, match="PRODUCT_USE_LOCATION_REQUIRED"):
        validate_meal_use(holder, {"food-k": bound},
                      _consume(product_id="food-k"))


def test_basket_meal_food_rate_is_zero_for_non_eating_use() -> None:
    """非进食使用不产生食品热量速率。"""
    products = {"food-1": _product()}
    request = _consume(use_mode="use")
    assert meal_food_rate(products, request) == 0.0
    assert meal_food_rate(products, _consume()) == pytest.approx(600 * 3600 / 900)


def test_basket_apply_meal_progress_durable_completion_and_paused() -> None:
    """多件路径镜像 commerce 口径：耐用品完成全额、暂停不推进、超持拒绝。"""
    durable = _product(product_id="item-dry", use_modes=("use",), consumable=False,
                       effect="dry", kcal=0)
    products = {"item-dry": durable}
    request = _consume(product_id="item-dry", use_mode="use", duration=900)
    done = _use_activity(request, status=ActivityStatus.COMPLETED, ended_at=900,
                         elapsed_seconds=900, last_settled_at=900)
    agent = apply_meal_progress(_agent(inventory={"home": {"item-dry": 1.0}}),
                                products, done, _body())
    assert done.data["consumed"]["item-dry"] == pytest.approx(1.0)
    assert agent.active_effects == {"dry"}
    fresh_paused = _use_activity(request, status=ActivityStatus.PAUSED)
    apply_meal_progress(agent, products, fresh_paused, _body())
    assert fresh_paused.data["consumed"]["item-dry"] == pytest.approx(0.0)
    starved = _use_activity(_consume(), elapsed_seconds=450)
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_HELD"):
        apply_meal_progress(_agent(inventory={"home": {"food-1": 0.1}}),
                            {"food-1": _product()}, starved, _body())


# ---------------------------------------------------------------------------
# social + social_actions：社交最小可行版（`docs/dev/SPEC-life-runtime.md`「社交最小可行版」）
# ---------------------------------------------------------------------------

def _presence(agent_id="agent:2", location="home", awake=True, gaming=False) -> social.AgentPresence:
    return social.AgentPresence(agent_id=agent_id, name="B", location=location,
                                activity_family="idle", awake=awake, actively_gaming=gaming)


def test_social_chat_requires_positive_duration_and_partner_recipient() -> None:
    """交谈域校验：时长必须为正且邀请收件人必须是对方本人。"""
    invite = social.OutgoingMessage(message_id="m1", sender_id="agent:1",
                                    recipient_id="agent:2", text="聊聊？", created_at=1)
    with pytest.raises(ValueError, match="duration must be positive"):
        social.start_chat("home", _presence(), 0, invite)
    wrong = invite.model_copy(update={"recipient_id": "agent:9"})
    with pytest.raises(ValueError, match="must be the partner"):
        social.start_chat("home", _presence(), 10, wrong)


def _social_state(agent: AgentState, activities=None) -> WorldState:
    return _world(agent, activities=activities if activities is not None else {})


def test_social_actions_partner_presence_rejects_unknown_partner() -> None:
    """引擎侧解析对方在场：目录外人物即业务拒绝。"""
    with pytest.raises(BusinessRuleError, match="SOCIAL_PARTNER_NOT_FOUND"):
        partner_presence(_social_state(_agent()), "agent:9")


def test_social_actions_start_chat_and_call_require_idle_agent() -> None:
    """前台活动占用资源槽：已有主活动时交谈与通话都 RESOURCE_CONFLICT。"""
    busy = _agent(current_activity_id="a0")
    state = _social_state(busy)
    state.agents[2] = _agent(agent_id=2, location_id="home")
    request = StartSocialActivityRequest(kind="chat", partner_id="agent:2",
                                         duration_minutes=10)
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        start_chat(state, busy, request, _origin(), "m1")
    contact = social.Contact(contact_id="c1", salutation="妈", relation="母亲",
                             closeness=20, channels=("phone",), reply_policy="template")
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        start_call(busy, contact, request, _origin(), CURRENT)


def test_social_actions_chat_accepted_detects_partner_chat_activity() -> None:
    """接受语义（「社交最小可行版」）：对方存在以本方为伙伴的进行中 CHAT 活动即已接受。"""
    accepted = _activity(activity_id="a2", agent_id=2, kind=ActivityKind.CHAT,
                         data={"partner_id": "agent:1", "accepted": False})
    assert chat_accepted(_social_state(_agent(), activities={"a2": accepted}), 1) is True
    assert chat_accepted(_social_state(_agent()), 1) is False


def test_social_actions_advance_rejects_backwards_settlement_time() -> None:
    """社交活动结算时间倒流是程序错误，不是业务拒绝。"""
    backwards = _activity(kind=ActivityKind.CHAT, last_settled_at=900)
    with pytest.raises(ValueError, match="moved backwards"):
        advance_social(backwards, _agent(), 600, _body(), accepted=False)


def test_social_actions_advance_skips_non_active_activity() -> None:
    """暂停的交谈推进为无结算空转，保留既有进度。"""
    paused = _activity(kind=ActivityKind.CHAT, status=ActivityStatus.PAUSED)
    result = advance_social(paused, _agent(current_activity_id=None), 600, _body(),
                            accepted=False)
    assert result.settlement is None and result.completed is False
    assert result.activity.status == ActivityStatus.PAUSED


def test_social_actions_advance_completes_chat_and_clears_activity_slot() -> None:
    """到计划终点完成交谈：终态+planned_end 边界并释放主活动槽。"""
    finishing = _activity(kind=ActivityKind.CHAT)
    result = advance_social(finishing, _agent(current_activity_id="a1"), 600, _body(),
                            accepted=False)
    assert result.completed is True
    assert result.activity.status == ActivityStatus.COMPLETED
    assert result.activity.boundary_type == "planned_end"
    assert result.agent.current_activity_id is None


@pytest.mark.parametrize(("kind", "accepted"), ((ActivityKind.CALL, False),
                                                (ActivityKind.CHAT, True)))
def test_social_actions_advance_applies_felt_ledger_by_activity(kind, accepted) -> None:
    """通话/被接受的交谈按 felt 账本速率结算社交需要（「社交最小可行版」）。"""
    activity = _activity(kind=kind, data={"partner_id": "agent:2"})
    result = advance_social(activity, _agent(), 3600, _body(), accepted=accepted)
    start_need = _agent().body.social_need
    hours = 1.0
    if kind == ActivityKind.CALL:
        exposure = felt.SocialExposure(awake_hours=hours, call_hours=hours)
    else:
        exposure = felt.SocialExposure(awake_hours=hours, conversation_hours=hours)
    expected = felt.apply_social_need_ledger(start_need, exposure)
    assert result.agent.body.social_need == pytest.approx(expected)


def test_social_activity_change_cancel_pause_and_unknown_operations() -> None:
    """社交活动转换闭集：取消任意时刻、不可暂停、未知操作拒绝、恢复无操作。"""
    active = _activity(kind=ActivityKind.CALL)
    agent, cancelled = social_activity_change(_agent(current_activity_id="a1"), active,
                                              "cancel", 100)
    assert cancelled.status == ActivityStatus.CANCELLED
    assert cancelled.boundary_type == "cancelled"
    assert agent.current_activity_id is None
    terminal = cancelled
    with pytest.raises(BusinessRuleError, match="ACTIVITY_ALREADY_TERMINAL"):
        social_activity_change(agent, terminal, "cancel", 100)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_CANNOT_PAUSE"):
        social_activity_change(_agent(current_activity_id="a1"), active, "pause", 100)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_OPERATION_UNKNOWN"):
        social_activity_change(_agent(current_activity_id="a1"), active, "rewind", 100)
    same_agent, same = social_activity_change(_agent(current_activity_id="a1"), active,
                                              "resume", 100)
    assert same.activity_id == "a1" and same_agent.current_activity_id == "a1"


# ---------------------------------------------------------------------------
# activities：活动转换闭集（`docs/CONTRACTS.md`「请求字段与结算检查」的「修改活动」行）
# ---------------------------------------------------------------------------

def _feedback_activity(**overrides) -> ActivityState:
    payload = dict(next_interaction_at=1000, boundary_type="feedback")
    payload.update(overrides)
    return _activity(**payload)


def test_change_activity_rejects_foreign_activity() -> None:
    """活动属于调用者：他人活动拒绝（ACTIVITY_NOT_OWNED）。"""
    foreign = _activity(agent_id=2)
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.PAUSE)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_NOT_OWNED"):
        change_activity(_agent(), foreign, request, 1000, 300)


def test_change_activity_speed_changes_rate_at_feedback_boundary() -> None:
    """倍速变化只在 WATCH 活动反馈边界受理，并安排下一次反馈。"""
    watch = _feedback_activity(planned_end_at=2000,
                               data={"entertainment_relief_per_hour": 0.3,
                                     "exposure_count": 1})
    agent = _agent(current_activity_id="a1")
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.CHANGE_SPEED,
                                    playback_speed=2.0)
    changed_agent, changed = change_activity(agent, watch, request, 1000, 300)
    assert changed.playback_speed == 2.0
    assert changed.next_interaction_at == 1300  # 严格未来且早于完成
    assert changed_agent.current_activity_id == "a1"


def test_change_activity_speed_rejected_for_non_watch_activity() -> None:
    """倍速只对进行中的 WATCH 活动有意义（PLAY_GAME 边界也不受理）。"""
    game = _feedback_activity(kind=ActivityKind.PLAY_GAME, boundary_type="game_feedback")
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.CHANGE_SPEED,
                                    playback_speed=2.0)
    with pytest.raises(BusinessRuleError, match="PLAYBACK_SPEED_NOT_ALLOWED"):
        change_activity(_agent(current_activity_id="a1"), game, request, 1000, 300)


def test_continue_activity_rejects_activity_id_mismatch() -> None:
    """继续请求必须指向调用者自己的活动标识。"""
    watch = _feedback_activity(planned_end_at=2000)
    request = ContinueActivityRequest(activity_id="not-a1")
    with pytest.raises(BusinessRuleError, match="ACTIVITY_NOT_OWNED"):
        continue_activity(_agent(current_activity_id="a1"), watch, request, 1000, 300)


def test_advance_lifestyle_without_social_or_health_effects_skips_relief() -> None:
    """无社交/健康效应的生活活动只走常规结算，不落额外缓解。"""
    from worldmock.life.activities import lifestyle_effects
    zero = next(item_id for item_id, effects in lifestyle_effects().items()
                if effects.get("social", 0.0) <= 0 and effects.get("health", 0.0) <= 0)
    body = _body().model_copy(update={
        "activity_met": {**_body().activity_met, f"lifestyle:{zero}": 1.5}})
    activity = _activity(kind=ActivityKind.LIFESTYLE, target_id=zero)
    result = advance_activity(activity, _agent(), 300, body)
    assert result.completed is False
    assert result.agent.body.social_need == _agent().body.social_need


def test_change_activity_pause_at_game_feedback_boundary() -> None:
    """游戏交互边界（game_feedback）允许暂停；边界不符即拒绝。"""
    game = _feedback_activity(kind=ActivityKind.PLAY_GAME, boundary_type="game_feedback")
    agent = _agent(current_activity_id="a1")
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.PAUSE)
    _, paused = change_activity(agent, game, request, 1000, 300)
    assert paused.status == ActivityStatus.PAUSED
    wrong = _feedback_activity(kind=ActivityKind.PLAY_GAME, boundary_type="feedback")
    with pytest.raises(BusinessRuleError, match="ACTIVITY_FEEDBACK_NOT_DUE"):
        change_activity(agent, wrong, request, 1000, 300)


def test_continue_activity_ownership_and_boundary_rules() -> None:
    """继续活动要求所有权与恰在反馈边界。"""
    watch = _feedback_activity(planned_end_at=2000)
    request = ContinueActivityRequest(activity_id="a1")
    with pytest.raises(BusinessRuleError, match="ACTIVITY_NOT_OWNED"):
        continue_activity(_agent(current_activity_id="other"), watch, request, 1000, 300)
    not_due = _feedback_activity(planned_end_at=2000, next_interaction_at=1300)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_FEEDBACK_NOT_DUE"):
        continue_activity(_agent(current_activity_id="a1"), not_due, request, 1000, 300)
    _, kept = continue_activity(_agent(current_activity_id="a1"), watch, request, 1000, 300)
    assert kept.next_interaction_at == 1300


def test_change_activity_resume_conflicts_with_occupied_slot() -> None:
    """恢复暂停活动时主活动槽被占即 RESOURCE_CONFLICT。"""
    paused = _activity(status=ActivityStatus.PAUSED)
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.RESUME)
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        change_activity(_agent(current_activity_id="other"), paused, request, 1000, 300)


def test_change_activity_cancel_rejects_terminal_activity() -> None:
    """已终结活动不可再退出（ACTIVITY_ALREADY_TERMINAL）。"""
    done = _activity(status=ActivityStatus.COMPLETED, ended_at=600)
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.CANCEL)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_ALREADY_TERMINAL"):
        change_activity(_agent(), done, request, 1000, 300)


def test_advance_activity_skips_non_active_and_rejects_backwards_clock() -> None:
    """非活动态推进为空转；结算时刻倒流是程序错误。"""
    paused = _activity(status=ActivityStatus.PAUSED)
    result = advance_activity(paused, _agent(), 600, _body())
    assert result.completed is False and result.settlement is None
    backwards = _activity(last_settled_at=900)
    with pytest.raises(ValueError, match="moved backwards"):
        advance_activity(backwards, _agent(), 600, _body())


def test_truncate_activity_keeps_terminal_activity_untouched() -> None:
    """实验终点截断只作用于进行中/暂停活动，终态保持原样。"""
    done = _activity(status=ActivityStatus.COMPLETED, ended_at=600)
    changed, agent = truncate_activity(done, _agent(), 900)
    assert changed is done and agent.current_activity_id is None


@pytest.mark.parametrize(
    ("activity", "match"),
    (
        (_activity(kind=ActivityKind.WATCH, data={}), "entertainment evidence"),
        (_activity(kind=ActivityKind.WORK, data={"required_skill": "ami",
                                                 "skill_level": 0.9,
                                                 "work_notes_multiplier": 1.2}),
         "skill evidence is invalid"),
        (_activity(kind=ActivityKind.WORK, data={"required_skill": "ami",
                                                 "skill_level": 0.8,
                                                 "work_notes_multiplier": 5.0}),
         "tool multiplier evidence is invalid"),
        (_activity(kind=ActivityKind.CONSUME,
                   data={"food_energy_kcal_per_hour": -5}), "food energy rate is invalid"),
    ),
)
def test_activity_factors_rejects_incomplete_frozen_evidence(activity, match) -> None:
    """结算系数只消费受理时冻结的证据：证据缺失/失配是程序错误。"""
    with pytest.raises(ValueError, match=match):
        activity_factors(activity, _agent(), _body())


# ---------------------------------------------------------------------------
# motivation：动机层（`docs/dev/SPEC-life-realism.md`「动机层」）
# ---------------------------------------------------------------------------

CONTRACT_PROFILE = {
    "schema_version": 1,
    "big_five": {"openness": 4, "conscientiousness": 6, "extraversion": 3,
                 "agreeableness": 4, "neuroticism": 5},
    "weights": {"money_fen": 0.45, "energy_kcal": 0.15, "sleep_debt_hours": 0.15,
                "entertainment_need": 0.20, "goal_task": 0.05},
    "tau": 1.0,
    "beta_present_bias": 0.80,
    "loss_aversion": {"money_fen": 2.25, "energy_kcal": 1.50},
    "comfort": {"balance_fen": 20000, "energy_kcal": 1200},
    "aspiration": {"daily_effective_work_seconds": 14400},
    "emotion_sensitivity": 0.3,
    "attention_prior": {"feature_film": 0.3, "news_feed": 0.2, "joke_feed": 0.1,
                        "webnovel_excerpt": 0.05, "drama_series": 0.05,
                        "sport": 0.05, "hobby": 0.04, "outdoor": 0.03,
                        "wellness": 0.03, "household": 0.03, "learning": 0.03,
                        "pet": 0.03, "social": 0.03, "volunteer": 0.03},
}


def _profile() -> MotivationProfile:
    return MotivationProfile.model_validate(json.loads(json.dumps(CONTRACT_PROFILE)))


def _context(**overrides) -> MotivationContext:
    defaults = dict(
        agent=_agent(), balance_fen=20_000, body=_body(), home_location_id="home",
        products=(), merchant_stock={}, locations={}, routes=(), tasks=(),
        task_totals={}, contents=(), content_progress={}, active_activity=None,
        habit_events=(), consecutive_rejections=0, max_consecutive_rejections=3,
        total_effective_seconds=0.0, current_time=CURRENT,
        end_time=CURRENT + 86400, timezone=TIMEZONE,
        planned_travel_seconds=lambda mode, base: base)
    defaults.update(overrides)
    return MotivationContext(**defaults)


def test_motivation_transform_scales_cover_all_resources() -> None:
    """归一尺度闭集覆盖五类资源键。"""
    transforms = load_motivation_transforms(PROJECT_ROOT / "catalog" / "rules")
    assert transforms is not None
    scales = {resource: transforms.scale_of(resource)
              for resource in ("money_fen", "energy_kcal", "sleep_debt_hours",
                               "entertainment_need", "goal_task")}
    assert all(value > 0 for value in scales.values())
    with pytest.raises(KeyError):
        transforms.scale_of("unknown_resource")


@pytest.mark.parametrize(
    ("content", "match"),
    (
        ("not a toml [", "malformed"),
        ("[prospect]\nloss_exponent = 0.88\n", "is missing"),
        ("[prospect]\nloss_exponent = 0.88\n[homeostatic]\nloss_exponent = 0.5\n",
         "declared twice"),
    ),
)
def test_load_motivation_transforms_fails_closed_on_bad_rules(tmp_path, content, match) -> None:
    """动机规则在场即声明意图：坏 TOML/缺分组/重复键 fail-closed。"""
    (tmp_path / "motivation.toml").write_text(content, encoding="utf-8")
    with pytest.raises(IntegrityError, match=match):
        load_motivation_transforms(tmp_path)


def test_load_motivation_transforms_absent_file_disables_hints(tmp_path) -> None:
    """规则文件缺席返回 None：提示关闭而非报错。"""
    assert load_motivation_transforms(tmp_path) is None


def test_softmax_empty_value_list_returns_empty() -> None:
    """空值列表的 softmax 仍为空（无候选时的稳定约定）。"""
    assert _softmax([], 1.0) == []


def test_browse_candidate_requires_minimal_feasible_duration() -> None:
    """浏览候选的速览时长不足一分钟时不给候选。"""
    context = _context(catalog_products=(_product(),), products=())
    assert _browse_candidate(context, {"probe_duration_seconds": 30}) is None


def test_sleep_candidate_skips_when_session_falls_below_minimum() -> None:
    """小睡候选时长被钳到不足一分钟时不出候选。"""
    context = _context(agent=_agent(body=BodyState(energy_kcal=1600, sleep_pressure=0.0,
                                                   sleep_deficit_hours=0.0,
                                                   entertainment_need=0.5)))
    params = {"fallback_seconds": 30, "min_session_seconds": 0, "cap_seconds": 4 * 3600}
    assert _sleep_candidate(context, params) is None


def test_feasible_food_rows_drop_unknown_merchant_location_and_unroutable_delivery() -> None:
    """商家地点不在场景或无配送路线的食品不可行（不虚构可达性）。"""
    distant = _product(product_id="food-far")
    stocked = {"food-far": 5}
    context = _context(products=(distant,), locations={}, merchant_stock=stocked,
                       routes=(_route("r1", "office", "home"),))
    assert _feasible_food_rows(context) == []  # 商家地点不在场景闭集
    known = _context(products=(distant,), locations={"market": _location("market")},
                     merchant_stock=stocked, routes=())
    assert _feasible_food_rows(known) == []  # 商家到当前位置无配送路线


def test_continue_candidate_covers_travel_lifestyle_and_consume_flows() -> None:
    """继续当前活动按活动类型取 MET 与资源流。"""
    context = _context(agent=_agent())
    travel = _activity(kind=ActivityKind.TRAVEL, travel_mode=TravelMode.DRIVING,
                       planned_end_at=3600, planned_duration_seconds=3600)
    assert _continue_candidate(_context(active_activity=travel)) is not None
    lifestyle_body = _body().model_copy(update={
        "activity_met": {**_body().activity_met, "lifestyle:jog": 2.0}})
    lifestyle = _activity(kind=ActivityKind.LIFESTYLE, target_id="jog",
                          planned_end_at=3600, planned_duration_seconds=3600)
    assert _continue_candidate(_context(active_activity=lifestyle,
                                        body=lifestyle_body)) is not None
    consume = _activity(kind=ActivityKind.CONSUME,
                        planned_end_at=3600, planned_duration_seconds=3600,
                        data={"food_energy_kcal_per_hour": 600})
    candidate = _continue_candidate(_context(active_activity=consume))
    assert candidate is not None and candidate.energy_delta > -candidate.energy_delta


def test_continue_candidate_skips_when_remaining_or_energy_infeasible() -> None:
    """剩余不足一分钟或能量不可完成时不出继续候选。"""
    almost_done = _activity(elapsed_seconds=570)
    assert _continue_candidate(_context(active_activity=almost_done)) is None
    long_work = _activity(kind=ActivityKind.WORK, planned_end_at=7200,
                          planned_duration_seconds=7200)
    exhausted = _agent(body=BodyState(energy_kcal=10, sleep_pressure=0.0,
                                      sleep_deficit_hours=4.0, entertainment_need=0.5))
    assert _continue_candidate(_context(agent=exhausted, active_activity=long_work)) is None


def test_sleep_deficit_delta_recovers_only_for_sleep() -> None:
    """睡眠按 1h/h 清账，其余活动按清醒标度累积。"""
    context = _context(agent=_agent())
    sleeping = _sleep_deficit_delta(context, ActivityKind.SLEEP, 1.0)
    awake = _sleep_deficit_delta(context, ActivityKind.WORK, 1.0)
    assert sleeping < 0 < awake


def test_entertainment_delta_watch_without_evidence_falls_back_to_accrual() -> None:
    """观看活动缺费率证据时按清醒累积（证据错误在 activity_factors 层拦截）。"""
    watch = _activity(kind=ActivityKind.WATCH, target_id="movie-0001", data={})
    assert _entertainment_delta(_context(), watch, 1.0) > 0


def test_travel_candidate_rejects_short_or_energy_infeasible_legs() -> None:
    """出行候选：分时计划不足一分钟或能量不可支撑时不出候选。"""
    context = _context(routes=(_route("r1", "home", "office"),))
    short = _context(routes=(_route("r1", "home", "office"),),
                     planned_travel_seconds=lambda mode, base: 30)
    assert _travel_candidate(short, "work", "office") is None
    exhausted = _context(agent=_agent(body=BodyState(energy_kcal=10, sleep_pressure=0.0,
                                                     sleep_deficit_hours=4.0,
                                                     entertainment_need=0.5)),
                         routes=(_route("r1", "home", "office"),),
                         planned_travel_seconds=lambda mode, base: 7200)
    assert _travel_candidate(exhausted, "work", "office") is None
    assert _travel_candidate(context, "work", "office") is not None


def _work_task(task_id="task-1", pay=50000, location="office", due_day=16,
               minimum_skill=0.3) -> WorkTaskConfig:
    zone = ZoneInfo(TIMEZONE)
    return WorkTaskConfig.model_validate({
        "task_id": task_id, "assignee_agent_id": "agent:1", "contract_kind": "package",
        "available_at": datetime(2023, 11, 13, tzinfo=zone),
        "due_at": datetime(2023, 11, due_day, tzinfo=zone),
        "maximum_duration_seconds": 14400, "pay_fen": pay,
        "employer_account_id": "employer:1", "required_location_id": location,
        "required_skill": "ami", "minimum_skill": minimum_skill,
        "required_effective_seconds": 3600.0,
        "input_path": "work/in.txt", "input_sha256": "b" * 64,
        "oracle_path": "work/oracle.json", "oracle_sha256": "c" * 64})


def test_work_candidates_filter_due_and_skill_and_offer_travel_leg() -> None:
    """过期/技能不足任务被过滤；不在任务地点时给出行腿。"""
    past_due = _work_task(task_id="task-old", due_day=14)
    live = _work_task()
    second = _work_task(task_id="task-2")
    at_office = _context(tasks=(past_due, live),
                         agent=_agent(location_id="office"))
    travel_leg, work = _work_candidates(at_office, {"max_tasks": 2})
    assert travel_leg is None and [item.summary for item in work]
    at_home = _context(tasks=(past_due, live, second), agent=_agent(location_id="home"),
                       routes=(_route("r1", "home", "office"),))
    travel_leg, work = _work_candidates(at_home, {"max_tasks": 2})
    assert travel_leg is not None and work == []  # 出行腿只给一次
    hard = _work_task(task_id="task-hard", minimum_skill=0.95)
    assert _work_candidates(_context(tasks=(hard,)), {"max_tasks": 2}) == (None, [])


def test_entertainment_candidates_skip_items_shorter_than_a_minute() -> None:
    """短于一分钟的内容条目不出观看候选（min 会话槽约束）。"""
    tiny = ContentItemConfig.model_validate({
        "item_id": "tiny", "kind": "feature_film", "duration_seconds": 10,
        "timeline_path": "t.srt", "timeline_sha256": "d" * 64,
        "entertainment_relief_per_hour": 0.3, "experience_label": "Movie"})
    agent = _agent(taste_weights={"tiny": 0.8})
    family = next(item for item in default_affordances().families_by_rank()
                  if item.name == "entertainment")
    context = _context(agent=agent, contents=(tiny,), taste_weights={"tiny": 0.8})
    assert _entertainment_candidates(context, _profile(), family) == []


# ---------------------------------------------------------------------------
# narration：观察投影与提示合同（渲染上限/闭集）
# ---------------------------------------------------------------------------

def test_render_scene_with_empty_observation_is_blank() -> None:
    """观察字段全部缺席时场景段落为空串（渲染器不虚构事实）。"""
    assert render_scene({}) == ""


def test_render_scene_covers_day_plan_shapes_and_recent_string_result() -> None:
    """day_plan 三形态与字符串动作结果按合同渲染。"""
    assert "今天的计划：先工作" in render_scene({"day_plan": "先工作"})
    assert "今天的计划是上午工作" in render_scene(
        {"day_plan": {"summary": "上午工作"}})
    assert "今天当前处于「上午」" in render_scene(
        {"day_plan": {"current_segment": "上午"}})
    scene = render_scene({"recent_action_results": "你刚吃完饭"})
    assert scene.endswith("你刚吃完饭。")


def test_field_blacklist_tolerates_non_dict_route_entries() -> None:
    """routes 白名单检查只作用于字典条目，纯字符串条目跳过。"""
    assert render_scene({"routes": ["plain-entry"]}) == ""


# ---------------------------------------------------------------------------
# content：内容增量揭示（`docs/CONTRACTS.md`「请求字段与结算检查」的「使用」行）
# ---------------------------------------------------------------------------

def _write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_load_lifestyle_items_rejects_broken_catalogs(tmp_path) -> None:
    """生活目录闭集校验：缺 items/坏行/重复 id/身份不匹配逐项 fail-closed。"""
    good_row = {"activity_id": "walk", "name_zh": "散步", "category": "wellness",
                "typical_duration_minutes": 30, "description_zh": "走走",
                "met_value": 2.0}
    _write(tmp_path, "lifestyle/items.json", json.dumps({"items": [good_row]}).encode())
    items = load_lifestyle_items(tmp_path, {"lifestyle:walk": 2.0})
    assert [item.activity_id for item in items] == ["walk"]

    def broken(payload) -> None:
        _write(tmp_path, "lifestyle/items.json", payload)
        with pytest.raises(IntegrityError):
            load_lifestyle_items(tmp_path, {"lifestyle:walk": 2.0})

    broken(json.dumps({"nope": 1}).encode())  # items 缺席
    broken(json.dumps({"items": [1]}).encode())  # 行不是对象
    broken(json.dumps({"items": [{"activity_id": "walk"}]}).encode())  # 行字段缺失
    broken(json.dumps({"items": [good_row, dict(good_row)]}).encode())  # 重复 id
    _write(tmp_path, "lifestyle/items.json", json.dumps({"items": [good_row]}).encode())
    with pytest.raises(IntegrityError, match="do not match"):
        load_lifestyle_items(tmp_path, {"lifestyle:walk": 2.0, "lifestyle:other": 1.0})


def _content_item(timeline_sha256: str) -> ContentItemConfig:
    return ContentItemConfig.model_validate({
        "item_id": "c1", "kind": "news_feed", "duration_seconds": 600,
        "timeline_path": "tl.srt", "timeline_sha256": timeline_sha256,
        "entertainment_relief_per_hour": 0.2, "experience_label": "News"})


def test_load_subtitles_rejects_hash_mismatch_and_broken_timelines(tmp_path) -> None:
    """字幕身份与时间轴合同：哈希不符/坏块/重叠逐项拒绝。"""
    content = b"1\n00:00:00,000 --> 00:00:10,000\nhello\n\n2\n00:00:05,000 --> 00:00:20,000\nworld\n"
    _write(tmp_path, "tl.srt", content)
    with pytest.raises(IntegrityError, match="hash mismatch"):
        load_subtitles(_content_item("0" * 64), tmp_path)
    item = _content_item(_sha(content))
    with pytest.raises(IntegrityError, match="empty or overlaps"):
        load_subtitles(item, tmp_path)
    garbage = b"not an srt at all"
    _write(tmp_path, "tl.srt", garbage)
    broken = _content_item(_sha(garbage))
    with pytest.raises(IntegrityError, match="invalid"):
        load_subtitles(broken, tmp_path)
    valid = b"1\n00:00:00,000 --> 00:00:10,000\nhello\n\n2\n00:00:10,000 --> 00:00:20,000\nworld\n"
    _write(tmp_path, "tl.srt", valid)
    cues = load_subtitles(_content_item(_sha(valid)), tmp_path)
    assert [cue.text for cue in cues] == ["hello", "world"]


def test_load_content_titles_skips_blank_or_missing_titles(tmp_path) -> None:
    """标题是展示元数据：条目缺行或标题空白只是不注入，不失败。"""
    item = ContentItemConfig.model_construct(
        item_id="c1", kind="feature_film", duration_seconds=600,
        timeline_path="t.srt", timeline_sha256="d" * 64,
        entertainment_relief_per_hour=0.2, experience_label="X")
    _write(tmp_path, "titles.json",
           json.dumps([{"id": "c1", "title": "   "}, {"id": "c2", "title": "有名"}]).encode())
    sources = {"feature_film": ("titles.json", "id")}
    assert load_content_titles((item,), tmp_path, sources) == {}


def test_load_subtitles_rejects_escaped_and_symlinked_paths(tmp_path) -> None:
    """内容读取限制在冻结语料目录：越界路径与符号链接拒绝。"""
    escaped = _content_item("0" * 64).model_copy(update={"timeline_path": "../evil.srt"})
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_subtitles(escaped, tmp_path)
    outside = tmp_path / "outside.srt"
    outside.write_bytes(b"1\n00:00:00,000 --> 00:00:10,000\nx\n")
    link = tmp_path / "linked.srt"
    os.symlink(outside, link)
    symlinked = _content_item(_sha(b"1\n00:00:00,000 --> 00:00:10,000\nx\n")).model_copy(
        update={"timeline_path": "linked.srt"})
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_subtitles(symlinked, tmp_path)


def test_reveal_mail_requires_visible_message() -> None:
    """邮件正文只对已到达且读完的活动揭示：不可见即拒绝。"""
    agent = _agent(visible_message_ids=set())
    activity = _activity(kind=ActivityKind.READ_MAIL, target_id="m1")
    with pytest.raises(BusinessRuleError, match="MESSAGE_NOT_VISIBLE"):
        reveal_mail(activity, agent, {})


def test_mail_headers_show_body_only_for_read_messages() -> None:
    """收件箱头：已读展示正文，未读只有标题。"""
    zone = ZoneInfo(TIMEZONE)
    messages = {
        "m1": MessageConfig.model_validate({
            "message_id": "m1", "thread_id": "t1", "recipient_agent_id": 1,
            "sent_at": datetime(2023, 11, 1, tzinfo=zone), "subject": "s1", "body": "b1"}),
        "m2": MessageConfig.model_validate({
            "message_id": "m2", "thread_id": "t2", "recipient_agent_id": 1,
            "sent_at": datetime(2023, 11, 2, tzinfo=zone), "subject": "s2", "body": "b2"}),
    }
    agent = _agent(visible_message_ids={"m1", "m2"}, read_message_ids={"m1"})
    headers = mail_headers(agent, messages)
    assert [record["message_id"] for record in headers] == ["m1", "m2"]
    assert headers[0]["read"] is True and headers[0]["body"] == "b1"
    assert headers[1]["read"] is False and "body" not in headers[1]


# ---------------------------------------------------------------------------
# work：任务受理与材料揭示（`docs/CONTRACTS.md`「请求字段与结算检查」的「提交工作」行）
# ---------------------------------------------------------------------------

def test_validate_work_start_requires_visible_task() -> None:
    """不可见任务不能开始工作。"""
    with pytest.raises(BusinessRuleError, match="WORK_TASK_NOT_VISIBLE"):
        validate_work_start(_agent(visible_task_ids=set()), _work_task(), CURRENT)


def test_load_work_input_rejects_hash_mismatch_and_non_utf8(tmp_path) -> None:
    """工作输入身份与编码合同。"""
    raw = "会议记录".encode()
    _write(tmp_path, "work/in.txt", raw)
    task = _work_task().model_copy(update={"input_sha256": _sha(raw)})
    assert load_work_input(tmp_path, task) == "会议记录"
    broken = b"\xff\xfe broken"
    _write(tmp_path, "work/in.txt", broken)
    utf8_task = task.model_copy(update={"input_sha256": _sha(broken)})
    with pytest.raises(IntegrityError, match="not UTF-8"):
        load_work_input(tmp_path, utf8_task)
    _write(tmp_path, "work/in.txt", raw)
    drifted = task.model_copy(update={"input_sha256": "0" * 64})
    with pytest.raises(IntegrityError, match="hash mismatch"):
        load_work_input(tmp_path, drifted)


def test_load_work_oracle_rejects_broken_payloads(tmp_path) -> None:
    """oracle 非法 JSON/缺 expected/缺参考摘要/坏参考逐项拒绝。"""

    def oracle_task(payload: bytes) -> WorkTaskConfig:
        _write(tmp_path, "work/oracle.json", payload)
        return _work_task().model_copy(update={"oracle_sha256": _sha(payload)})

    with pytest.raises(IntegrityError, match="invalid JSON"):
        load_work_oracle(tmp_path, oracle_task(b"not json"))
    with pytest.raises(IntegrityError, match="expected object"):
        load_work_oracle(tmp_path, oracle_task(
            json.dumps({"expected": 1}).encode()))
    with pytest.raises(IntegrityError, match="source reference is missing"):
        load_work_oracle(tmp_path, oracle_task(
            json.dumps({"expected": {"summary": ["s1"]}, "decisions": [],
                        "action_items": []}).encode()))
    _write(tmp_path, "work/ref.xml", b"<not-xml")
    with pytest.raises(IntegrityError, match="invalid XML"):
        load_work_oracle(tmp_path, oracle_task(
            json.dumps({"expected": {"summary": ["s1"]}, "decisions": [],
                        "action_items": [],
                        "source_reference": "ref.xml"}).encode()))
    _write(tmp_path, "work/ref.xml", b"<meeting><item/></meeting>")
    with pytest.raises(IntegrityError, match="abstract is missing"):
        load_work_oracle(tmp_path, oracle_task(
            json.dumps({"expected": {"summary": ["s1"]}, "decisions": [],
                        "action_items": [],
                        "source_reference": "ref.xml"}).encode()))


def test_prepare_work_submission_rejects_piece_oracle_without_count(
        tmp_path) -> None:
    """计件合同 oracle 缺件数时付款换算拒绝。"""
    from worldmock.life.models import SubmitWorkRequest
    raw = "材料正文" * 20
    _write(tmp_path, "work/in.txt", raw.encode())
    reference = json.dumps({"summary_sentences": ["参考摘要句"]}).encode()
    _write(tmp_path, "work/ref.json", reference)
    oracle = json.dumps({"expected": {"summary": ["s1"], "decisions": [],
                                      "action_items": []},
                         "source_reference": "ref.json"}).encode()
    _write(tmp_path, "work/oracle.json", oracle)
    zone = ZoneInfo(TIMEZONE)
    task = WorkTaskConfig.model_validate({
        "task_id": "task-piece", "assignee_agent_id": "agent:1",
        "contract_kind": "piece",
        "available_at": datetime(2023, 11, 1, tzinfo=zone),
        "due_at": datetime(2023, 11, 30, tzinfo=zone),
        "maximum_duration_seconds": 14400, "pay_fen": 50000,
        "employer_account_id": "employer:1", "required_location_id": "office",
        "required_skill": "ami", "minimum_skill": 0.3,
        "required_effective_seconds": 100.0,
        "input_path": "work/in.txt", "input_sha256": _sha(raw.encode()),
        "oracle_path": "work/oracle.json", "oracle_sha256": _sha(oracle)})
    done = _activity(activity_id="w1", kind=ActivityKind.WORK, target_id="task-piece",
                     status=ActivityStatus.COMPLETED, ended_at=100,
                     planned_end_at=100, planned_duration_seconds=100,
                     elapsed_seconds=100, last_settled_at=100,
                     effective_work_seconds=100.0)
    agent = _agent(location_id="office", visible_task_ids={"task-piece"})
    state = _world(agent, accounts={"employer:1": 10 ** 9})
    state.activities["w1"] = done
    scenario = _life_scenario().model_copy(update={"tasks": (task,)})
    semantics = replace(_semantics(), scenario=scenario)
    request = SubmitWorkRequest.model_construct(
        task_id="task-piece",
        deliverable=SimpleNamespace(model_dump=lambda **_k: {}))
    with pytest.raises(IntegrityError, match="EXCHANGE_PIECE_COUNT_MISSING"):
        prepare_work_submission(state, semantics, tmp_path, 1, request)


def test_load_work_input_rejects_symlinked_files(tmp_path) -> None:
    """符号链接的工作输入文件在解析前拒绝。"""
    raw = "会议记录".encode()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(raw)
    (tmp_path / "work").mkdir()
    os.symlink(outside, tmp_path / "work" / "in.txt")
    task = _work_task().model_copy(update={"input_sha256": _sha(raw)})
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_work_input(tmp_path, task)


def test_prepare_work_submission_rejects_unknown_or_invisible_task(tmp_path) -> None:
    """提交前合同校验：目录外任务与不可见任务拒绝。"""
    from worldmock.life.models import SubmitWorkRequest
    task = _work_task()
    scenario = _life_scenario()
    scenario = scenario.model_copy(update={"tasks": (task,)})
    semantics = replace(_semantics(), scenario=scenario)
    agent = _agent(visible_task_ids=set())
    state = _world(agent)
    request = SubmitWorkRequest.model_construct(task_id="missing",
                                                deliverable=SimpleNamespace())
    with pytest.raises(BusinessRuleError, match="WORK_TASK_NOT_FOUND"):
        prepare_work_submission(state, semantics, tmp_path, 1, request)
    request = SubmitWorkRequest.model_construct(task_id="task-1",
                                                deliverable=SimpleNamespace())
    with pytest.raises(BusinessRuleError, match="WORK_TASK_NOT_VISIBLE"):
        prepare_work_submission(state, semantics, tmp_path, 1, request)


# ---------------------------------------------------------------------------
# work_review：独立验收协议（`docs/dev/SPEC-life-runtime.md`「内容提供行为」）
# ---------------------------------------------------------------------------

def _review_prepared(**overrides) -> PreparedWorkReview:
    defaults = dict(
        request={"model": "m", "messages": [], "stream": False, "tools": [],
                 "tool_choice": "required", "max_retries": 0, "extra_body": {}},
        source="source text and more words here",
        expected_reference_ids=("summary:0",), expected_delivery_paths=("summary",),
        input_sha256="0" * 64, oracle_sha256="1" * 64, deliverable_sha256="2" * 64,
        prompt_sha256="3" * 64, schema_sha256="4" * 64)
    defaults.update(overrides)
    return PreparedWorkReview(**defaults)


def _raw_review(arguments: str) -> dict:
    return {"choices": [{"message": {"tool_calls": [
        {"id": "call-1", "type": "function",
         "function": {"name": "submit_work_review", "arguments": arguments}}]}}]}


def _assessment_payload() -> dict:
    return {"reference_assessments": [
        {"reference_id": "summary:0", "required_fact_covered": True,
         "source_supported": True, "contradiction": False,
         "source_excerpt": "source text and more", "rationale": "ok"}],
        "delivery_assessments": [
            {"delivery_path": "summary", "source_supported": True, "contradiction": False,
             "source_excerpt": "source text and more", "rationale": "ok"}],
        "accepted": True}


class _StubClient:
    """@brief 只替换外部 LLM 客户端的测试桩（被测对象是 WorkReviewer.call 本身）。"""

    def __init__(self, result):
        self._result = result

    async def call(self, **_kwargs):
        return self._result


def _reviewer(result) -> WorkReviewer:
    reviewer = WorkReviewer.__new__(WorkReviewer)
    reviewer.client = _StubClient(result)
    return reviewer


def test_reviewer_call_serializes_model_and_dict_payloads() -> None:
    """验收客户端调用：模型对象与字典载荷都归一为可持久化字典。"""
    prepared = _review_prepared()
    payload = {"choices": []}
    model_like = SimpleNamespace(model_dump=lambda **_k: payload)
    assert asyncio.run(_reviewer(model_like).call(prepared)) == payload
    assert asyncio.run(_reviewer(dict(payload)).call(prepared)) == payload


@pytest.mark.parametrize(
    ("result", "match"),
    ((SimpleNamespace(model_dump=lambda **_k: ["not", "a", "dict"]), "not a serialized object"),
     ("a plain string", "not serializable")),
)
def test_reviewer_call_rejects_unserializable_payloads(result, match) -> None:
    """不可序列化的模型响应按协议错误拒绝。"""
    with pytest.raises(ProtocolError, match=match):
        asyncio.run(_reviewer(result).call(_review_prepared()))


@pytest.mark.parametrize(
    ("raw", "error", "match"),
    (
        ("not-a-dict", TypeError, "serialized response object"),
        ({}, WorkReviewOutputError, "exactly one choice"),
        ({"choices": [{"message": {"tool_calls": [
            {"id": "c", "type": "function",
             "function": {"name": "other_tool", "arguments": "{}"}}]}}]},
         WorkReviewOutputError, "must call submit_work_review"),
        ({"choices": [{"message": {"tool_calls": [
            {"id": "c", "type": "function",
             "function": {"name": "submit_work_review",
                          "arguments": ["not", "str"]}}]}}]},
         WorkReviewOutputError, "must be JSON text"),
    ),
)
def test_tool_arguments_rejects_malformed_responses(raw, error, match) -> None:
    """唯一工具调用协议：载荷形状逐项校验。"""
    with pytest.raises(error, match=match):
        parse_work_review(raw, _review_prepared())


def test_parse_work_review_rejects_wrong_delivery_identities() -> None:
    """交付路径身份闭集：越界路径给出确定性反馈。"""
    payload = _assessment_payload()
    payload["delivery_assessments"][0]["delivery_path"] = "decisions:0"
    with pytest.raises(WorkReviewOutputError, match="delivery_assessments.delivery_path"):
        parse_work_review(_raw_review(json.dumps(payload)), _review_prepared())


def test_public_work_review_rejects_invalid_reference_category() -> None:
    """公开反馈只接受 summary/decision/action_item 三类参考身份。"""
    reference = ReferenceAssessment.model_construct(
        reference_id="bogus:0", required_fact_covered=True, source_supported=True,
        contradiction=False, source_excerpt="source text and more", rationale="ok")
    delivery = DeliveryAssessment.model_construct(
        delivery_path="summary", source_supported=True, contradiction=False,
        source_excerpt="source text and more", rationale="ok")
    decision = WorkReviewDecision.model_construct(
        accepted=True, reported_accepted=True, input_sha256="0" * 64,
        oracle_sha256="1" * 64, deliverable_sha256="2" * 64,
        prompt_sha256="3" * 64, schema_sha256="4" * 64,
        reference_assessments=(reference,), delivery_assessments=(delivery,))
    with pytest.raises(ProtocolError, match="category is invalid"):
        public_work_review(decision, "source text and more words here")


def _submission(oracle: dict):
    from worldmock.life.work import WorkMetrics, WorkSubmission
    task = WorkTaskConfig.model_construct(input_sha256="0" * 64, oracle_sha256="1" * 64)
    from worldmock.life.models import MeetingMinutes, SubmitWorkRequest
    request = SubmitWorkRequest.model_construct(
        task_id="t", deliverable=MeetingMinutes.model_construct(
            summary="s", decisions=("d",), action_items=("a",)))
    return WorkSubmission(task=task, agent=_agent(), request=request,
                          metrics=WorkMetrics(1, 1.0, 1), oracle=oracle, source="src")


def test_prepare_work_review_reference_fact_contract() -> None:
    """参考事实合同：非列表拒绝、*NA* 跳过、全空拒绝。"""
    with pytest.raises(ProtocolError, match="reference facts are invalid"):
        prepare_work_review(_submission({"expected": {"summary": "not-a-list"}}), "m")
    prepared = prepare_work_review(_submission({
        "expected": {"summary": ["*NA*", "real fact"], "decisions": ["*NA*"],
                     "action_items": ["*NA*"]}}), "m")
    assert prepared.expected_reference_ids == ("summary:1",)
    with pytest.raises(ProtocolError, match="no reference facts"):
        prepare_work_review(_submission({
            "expected": {"summary": ["*NA*"], "decisions": ["*NA*"],
                         "action_items": ["*NA*"]}}), "m")


# ---------------------------------------------------------------------------
# game：TextWorld 身份与重放（`docs/dev/SPEC-life-runtime.md`「具体活动结算」的游戏行）
# ---------------------------------------------------------------------------

def _game_config(root: Path, *, game_sha: str | None = None,
                 walkthrough_sha: str | None = None,
                 game_path: str = "game.json") -> GameConfig:
    payload = {"metadata": {"walkthrough": ["look"]}}
    _write(root, "game.json", json.dumps(payload).encode())
    return GameConfig.model_construct(
        game_id="g", seed=1, game_path=game_path,
        game_sha256=game_sha or _sha(json.dumps(payload).encode()),
        walkthrough_sha256=walkthrough_sha or _sha(json.dumps(["look"]).encode()))


def test_validate_game_rejects_game_hash_drift(tmp_path) -> None:
    """游戏文件哈希漂移即拒绝。"""
    config = _game_config(tmp_path, game_sha="0" * 64)
    with pytest.raises(IntegrityError, match="game hash mismatch"):
        validate_game(config, tmp_path)


def test_validate_game_rejects_broken_metadata(tmp_path) -> None:
    """游戏元数据不可解析即拒绝。"""
    broken = b"not json"
    _write(tmp_path, "game.json", broken)
    config = GameConfig.model_construct(
        game_id="g", seed=1, game_path="game.json", game_sha256=_sha(broken),
        walkthrough_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="metadata is invalid"):
        validate_game(config, tmp_path)


def test_validate_game_rejects_walkthrough_hash_drift(tmp_path) -> None:
    """walkthrough 哈希不符即拒绝。"""
    config = _game_config(tmp_path, walkthrough_sha="0" * 64)
    with pytest.raises(IntegrityError, match="walkthrough hash mismatch"):
        validate_game(config, tmp_path)


def test_game_path_rejects_escape_and_symlinks(tmp_path) -> None:
    """游戏读取限制在冻结语料目录：越界路径与符号链接拒绝。"""
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    os.symlink(outside, link)
    escaped = GameConfig.model_construct(
        game_id="g", seed=1, game_path="../outside.json",
        game_sha256="0" * 64, walkthrough_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="path is invalid"):
        validate_game(escaped, tmp_path)
    symlinked = escaped.model_copy(update={"game_path": "link.json"})
    with pytest.raises(IntegrityError, match="path is invalid"):
        validate_game(symlinked, tmp_path)


class _StubGameState:
    """@brief TextWorld 公开状态的最小替身（被测对象是 game.py 自身逻辑）。"""

    def __init__(self, commands, moves=0, facts=()):
        self.admissible_commands = list(commands)
        self.moves = moves
        self.score = 0
        self.won = False
        self.lost = False
        self.facts = list(facts)


class _StubGameEnv:
    """@brief 可编程的 TextWorld 环境替身：记录关闭与步进行为。"""

    def __init__(self, state, step_outcomes=None, step_error=None):
        self._state = state
        self._step_outcomes = list(step_outcomes or [])
        self._step_error = step_error
        self.closed = False

    def reset(self):
        return self._state

    def step(self, _command):
        if self._step_error is not None:
            raise self._step_error
        return self._step_outcomes.pop(0)

    def close(self):
        self.closed = True


def _game_with_env(monkeypatch, env, root: Path) -> GameConfig:
    _write(root, "game.json", b"{}")
    monkeypatch.setattr("worldmock.life.game.textworld.start", lambda *_a, **_k: env)
    return GameConfig.model_construct(game_id="g", seed=1, game_path="game.json",
                                      game_sha256="0" * 64, walkthrough_sha256="0" * 64)


def test_run_game_command_rejects_terminal_progress(monkeypatch, tmp_path) -> None:
    """已通关/已失败会话不再接受命令。"""
    env = _StubGameEnv(_StubGameState(["look"]))
    config = _game_with_env(monkeypatch, env, tmp_path)
    progress = GameProgress.model_construct(
        session_id="s", game_id="g", commands=[], moves=0, score=0, won=True,
        admissible_commands=("look",), public_state_sha256="0" * 64)
    with pytest.raises(BusinessRuleError, match="GAME_ALREADY_TERMINAL"):
        run_game_command(progress, "look", config, tmp_path)


def test_run_game_command_rejects_replayed_state_drift(monkeypatch, tmp_path) -> None:
    """重放哈希与存档不符即完整性错误，且环境被关闭。"""
    state = _StubGameState(["look"])
    after = _StubGameState(["look"], moves=1)
    env = _StubGameEnv(state, step_outcomes=[(after, 0, False)])
    config = _game_with_env(monkeypatch, env, tmp_path)
    from worldmock.life.game import _state_hash
    progress = GameProgress.model_construct(
        session_id="s", game_id="g", commands=[], moves=0, score=0,
        admissible_commands=("look",), public_state_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="replay state hash mismatch"):
        run_game_command(progress, "look", config, tmp_path)
    assert env.closed is True
    good = progress.model_copy(
        update={"public_state_sha256": _state_hash(state)})
    outcome = run_game_command(good, "look", config, tmp_path)
    assert outcome.progress.commands == ["look"]


def test_run_game_command_reports_unknown_outcome(monkeypatch, tmp_path) -> None:
    """引擎已接收但无返回时按结果未知拒绝（不虚构进度）。"""
    state = _StubGameState(["look"])
    env = _StubGameEnv(state, step_error=RuntimeError("engine crashed"))
    config = _game_with_env(monkeypatch, env, tmp_path)
    from worldmock.life.game import _state_hash
    progress = GameProgress.model_construct(
        session_id="s", game_id="g", commands=[], moves=0, score=0,
        admissible_commands=("look",), public_state_sha256=_state_hash(state))
    with pytest.raises(GameOutcomeUnknown, match="outcome is unknown"):
        run_game_command(progress, "look", config, tmp_path)


def test_replay_rejects_stale_or_terminal_command_logs(monkeypatch, tmp_path) -> None:
    """重放校验：存档命令不可接受/终态后仍有命令都拒绝并关闭环境。"""
    stale_env = _StubGameEnv(_StubGameState(["go east"]))
    config = _game_with_env(monkeypatch, stale_env, tmp_path)
    stale = GameProgress.model_construct(
        session_id="s", game_id="g", commands=["go west"], moves=1, score=0,
        admissible_commands=("go east",), public_state_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="no longer admissible"):
        run_game_command(stale, "go east", config, tmp_path)
    assert stale_env.closed is True

    mid_terminal = _StubGameEnv(
        _StubGameState(["a", "b"]),
        step_outcomes=[(_StubGameState(["b"], moves=1), 0, True)])
    config = _game_with_env(monkeypatch, mid_terminal, tmp_path)
    logged = GameProgress.model_construct(
        session_id="s", game_id="g", commands=["a", "b"], moves=1, score=0,
        admissible_commands=("a", "b"), public_state_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="continues after terminal"):
        run_game_command(logged, "b", config, tmp_path)


def test_new_game_session_and_verify_progress_replay(monkeypatch, tmp_path) -> None:
    """新会话与检查点恢复都从冻结初态重放公开状态。"""
    state = _StubGameState(["look"], facts=["at start"])
    env = _StubGameEnv(state)
    config = _game_with_env(monkeypatch, env, tmp_path)
    progress = new_game_session("s", config, tmp_path)
    verify_game_progress(progress, config, tmp_path)
    drifted = progress.model_copy(update={"moves": 5})
    with pytest.raises(IntegrityError, match="checkpoint replay disagrees"):
        verify_game_progress(drifted, config, tmp_path)


# ---------------------------------------------------------------------------
# travel：路线校验与定位轨迹（`docs/CONTRACTS.md`「请求字段与结算检查」的「启动活动」行）
# ---------------------------------------------------------------------------

def test_route_for_requires_unique_frozen_route() -> None:
    """起点-终点-方式三元组必须命中唯一冻结路线。"""
    scenario = _life_scenario(routes=[_route("r1", "home", "office", "driving")])
    request = StartActivityRequest(activity_type=ActivityKind.TRAVEL,
                                   planned_duration_seconds=600,
                                   destination_id="office", travel_mode="walking")
    with pytest.raises(BusinessRuleError, match="FROZEN_ROUTE_NOT_FOUND"):
        route_for(scenario, "home", request)
    matched = route_for(scenario, "home", request.model_copy(
        update={"travel_mode": "driving"}))
    assert matched.route_id == "r1"


def _response_route(tmp_path: Path, payload: dict, *, sha: str | None = None,
                    path: str | None = None) -> RouteConfig:
    relative = path or "routes/a.json"
    _write(tmp_path, relative, json.dumps(payload).encode())
    return _route(response_path=relative, response_sha256=sha or _sha(
        json.dumps(payload).encode()))


def test_validate_routes_rejects_identity_and_measurement_drift(tmp_path) -> None:
    """路线文件身份与 OSRM 距离/时长逐项核验。"""
    good = _response_route(tmp_path, {"code": "Ok", "routes": [
        {"distance": 3000, "duration": 600}]})
    validate_routes(_life_scenario(routes=[good]), tmp_path)
    with pytest.raises(IntegrityError, match="hash mismatch"):
        validate_routes(_life_scenario(routes=[
            _response_route(tmp_path, {"code": "Ok", "routes": [
                {"distance": 3000, "duration": 600}]}, sha="0" * 64)]), tmp_path)
    with pytest.raises(IntegrityError, match="response is invalid"):
        validate_routes(_life_scenario(routes=[
            _response_route(tmp_path, {"broken": True})]), tmp_path)
    with pytest.raises(IntegrityError, match="not successful"):
        validate_routes(_life_scenario(routes=[
            _response_route(tmp_path, {"code": "NoRoute", "routes": [
                {"distance": 3000, "duration": 600}]})]), tmp_path)
    with pytest.raises(IntegrityError, match="distance mismatch"):
        validate_routes(_life_scenario(routes=[
            _response_route(tmp_path, {"code": "Ok", "routes": [
                {"distance": 4000, "duration": 600}]})]), tmp_path)
    with pytest.raises(IntegrityError, match="duration mismatch"):
        validate_routes(_life_scenario(routes=[
            _response_route(tmp_path, {"code": "Ok", "routes": [
                {"distance": 3000, "duration": 999}]})]), tmp_path)


def test_update_travel_moves_location_only_on_arrival() -> None:
    """按完成比例推进距离，到达前不改变人物地点。"""
    route = _route(destination="office")
    midway = _activity(kind=ActivityKind.TRAVEL, elapsed_seconds=300)
    changed_activity, same_agent = update_travel(midway, route, _agent())
    assert changed_activity.route_progress_meters == pytest.approx(1500.0)
    assert same_agent.location_id == "home"
    done = _activity(kind=ActivityKind.TRAVEL, elapsed_seconds=600)
    _, arrived = update_travel(done, route, _agent())
    assert arrived.location_id == "office"


def test_location_track_rejects_broken_geometry_and_paths(tmp_path) -> None:
    """定位轨迹：坏几何/缺几何/越界路径/符号链接逐项拒绝；好输入可重放。"""
    good = _response_route(tmp_path, {"code": "Ok", "routes": [
        {"distance": 3000, "duration": 600,
         "geometry": {"type": "LineString", "coordinates": [[121.4, 31.2], [121.5, 31.3]]}}]},
        path="routes/track.json")
    request = LocationTrackRequest(agent_id=1, route=good, departure_epoch=1000,
                                   arrival_epoch=1000 + 3600, sample_interval_seconds=600)
    events = sample_location_track(request, tmp_path)
    assert len(events) == 5
    replayed = sample_location_track(request, tmp_path)
    assert [event.event_id for event in replayed] == [event.event_id for event in events]
    invalid = _response_route(tmp_path, {"broken": True}, path="routes/bad.json")
    with pytest.raises(IntegrityError, match="response is invalid"):
        sample_location_track(LocationTrackRequest(
            agent_id=1, route=invalid, departure_epoch=1000, arrival_epoch=4600,
            sample_interval_seconds=600), tmp_path)
    missing = _response_route(tmp_path, {"code": "Ok", "routes": [
        {"geometry": {"type": "Point", "coordinates": [1, 2]}}]}, path="routes/none.json")
    with pytest.raises(IntegrityError, match="geometry is missing"):
        sample_location_track(LocationTrackRequest(
            agent_id=1, route=missing, departure_epoch=1000, arrival_epoch=4600,
            sample_interval_seconds=600), tmp_path)
    escaped = good.model_copy(update={"response_path": "../secret.json"})
    with pytest.raises(IntegrityError, match="path is invalid"):
        sample_location_track(LocationTrackRequest(
            agent_id=1, route=escaped, departure_epoch=1000, arrival_epoch=4600,
            sample_interval_seconds=600), tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    linked = tmp_path / "routes" / "linked.json"
    os.symlink(outside, linked)
    symlinked = good.model_copy(update={"response_path": "routes/linked.json"})
    with pytest.raises(IntegrityError, match="path is invalid"):
        sample_location_track(LocationTrackRequest(
            agent_id=1, route=symlinked, departure_epoch=1000, arrival_epoch=4600,
            sample_interval_seconds=600), tmp_path)


# ---------------------------------------------------------------------------
# rules：封闭目录查找（`docs/CONTRACTS.md`「请求字段与结算检查」的启动活动行）
# ---------------------------------------------------------------------------

def test_rules_lookup_helpers_reject_unknown_ids(engine_factory) -> None:
    """商品/任务/游戏/字幕闭集查表：目录外身份业务拒绝。"""
    engine = engine_factory()
    rules = engine.rules
    real_product = engine.scenario.products[0].product_id
    assert rules.product(real_product).product_id == real_product
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        rules.product("missing-product")
    real_task = engine.scenario.tasks[0].task_id
    assert rules.task(real_task).task_id == real_task
    with pytest.raises(BusinessRuleError, match="WORK_TASK_NOT_FOUND"):
        rules.task("missing-task")
    real_game = engine.scenario.games[0].game_id
    assert rules.game(real_game).game_id == real_game
    with pytest.raises(BusinessRuleError, match="GAME_NOT_FOUND"):
        rules.game("missing-game")
    real_content = engine.scenario.contents[0].item_id
    assert rules.cues(real_content)
    with pytest.raises(BusinessRuleError, match="CONTENT_TIMELINE_NOT_FOUND"):
        rules.cues("missing-content")


def test_rules_validate_start_work_duration_and_deadline(engine_factory) -> None:
    """工作启动：超出合同最大工时/会错过截止分别拒绝。"""
    engine = engine_factory()
    rules = engine.rules
    task = engine.scenario.tasks[0]
    agent = engine.state.agents[1].model_copy(update={
        "visible_task_ids": {task.task_id},
        "location_id": task.required_location_id,
        "skill_levels": {task.required_skill: task.minimum_skill + 0.1}})
    now = int(task.available_at.timestamp())
    over = StartActivityRequest(activity_type=ActivityKind.WORK,
                                target_id=task.task_id,
                                planned_duration_seconds=task.maximum_duration_seconds + 60)
    with pytest.raises(BusinessRuleError, match="WORK_DURATION_EXCEEDS_CONTRACT"):
        rules.validate_start(agent, over, now)
    near_deadline = int(task.due_at.timestamp()) - 60
    tight = StartActivityRequest(activity_type=ActivityKind.WORK, target_id=task.task_id,
                                 planned_duration_seconds=3600)
    with pytest.raises(BusinessRuleError, match="WORK_WOULD_MISS_DEADLINE"):
        rules.validate_start(agent, tight, near_deadline)


def test_rules_validate_start_browse_target_must_be_catalog(engine_factory) -> None:
    """浏览目标只有 product_catalog 一个合法值。"""
    engine = engine_factory()
    request = StartActivityRequest(activity_type=ActivityKind.BROWSE,
                                   target_id="something_else",
                                   planned_duration_seconds=60)
    with pytest.raises(BusinessRuleError, match="BROWSE_TARGET_NOT_FOUND"):
        engine.rules.validate_start(engine.state.agents[1], request, CURRENT)
    fine = request.model_copy(update={"target_id": "product_catalog"})
    assert engine.rules.validate_start(engine.state.agents[1], fine, CURRENT) == {
        "catalog": "all_products"}


# ---------------------------------------------------------------------------
# semantics：目录组装准入（fail-closed）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _semantics() -> ScenarioSemantics:
    document, _ = parse_scenario_document(Path("scenarios/white-collar-12-weeks.json"))
    return assemble_semantics(document, load_catalog(Path("catalog")))


def test_load_runtime_scenario_wraps_catalog_load_failures(tmp_path) -> None:
    """完整加载把目录装载错误包装为 IntegrityError（fail-closed）。"""
    with pytest.raises(IntegrityError, match="Catalog load failed"):
        load_runtime_scenario(Path("scenarios/white-collar-12-weeks.json"),
                              catalog_dir=tmp_path / "missing-catalog")


def _resources_catalog(sem, mutate) -> Catalog:
    data = {key: value.model_dump() for key, value in sem.catalog.resources.items()}
    mutate(data)
    rebuilt = {key: ResourceDefinition.model_validate(value) for key, value in data.items()}
    return replace(sem.catalog, resources=MappingProxyType(rebuilt))


def _travel_time_catalog(sem, mutate) -> Catalog:
    content = mutate(sem.catalog.travel_time_rules_bytes)
    return replace(sem.catalog, travel_time_rules_bytes=content)


def _objects_catalog(sem, mutate) -> Catalog:
    data = {key: _thaw(value.model_dump()) for key, value in sem.catalog.objects.items()}
    mutate(data)
    rebuilt = {key: ExchangeObject.model_validate(value) for key, value in data.items()}
    return replace(sem.catalog, objects=MappingProxyType(rebuilt))


MILK = "food-6923644266066"


def test_travel_fare_rejects_an_unknown_mode() -> None:
    """出行费率只接受冻结动作集合中的出行方式。"""
    sem = _semantics()
    with pytest.raises(IntegrityError, match="has no category action"):
        sem.travel_fare("biking", 100)


def _at_hour(hour: int) -> int:
    return int(datetime(2026, 1, 5, hour, 0, tzinfo=ZoneInfo(TIMEZONE)).timestamp())


@pytest.mark.parametrize(
    ("mutate", "match"),
    (
        (lambda data: b"", "unsupported shape"),
        (lambda data: data.replace(b"driving_05 = 1.00\n", b""), "closed branch set"),
        (lambda data: data.replace(b"driving_05 = 1.00", b'driving_05 = "fast"'), "not numeric"),
        (lambda data: data.replace(b"driving_05 = 1.00", b"driving_05 = 9.0"), "outside"),
    ),
)
def test_travel_time_profile_rejects_missing_nonnumeric_or_out_of_bounds(
        mutate, match) -> None:
    """分时数值表缺席、缺分支、非数值与越界逐项拒绝。"""
    sem = _semantics()
    with pytest.raises(IntegrityError, match=match):
        assemble_semantics(sem.document, _travel_time_catalog(sem, mutate))


@pytest.mark.parametrize(
    ("mutate", "match"),
    (
        (lambda data: data.replace(b"walking = 1.0\n", b""), "closed branch set"),
        (lambda data: data.replace(b"walking = 1.0", b"walking = 2.0"), "must be 1.0"),
        (lambda data: data.replace(b"driving_13 = 1.22\n", b""), "closed branch set"),
    ),
)
def test_assembly_rejects_travel_time_shape_drift(mutate, match) -> None:
    """组装准入：分时数值表形状漂移逐项拒绝。"""
    sem = _semantics()
    with pytest.raises(IntegrityError, match=match):
        assemble_semantics(sem.document, _travel_time_catalog(sem, mutate))


@pytest.mark.parametrize(
    ("mutate", "match"),
    (
        (lambda data: data.pop("energy_kcal"), "energy_kcal"),
        (lambda data: data["energy_kcal"].__setitem__("drain_rules", None), "drain rules"),
        (lambda data: data["energy_kcal"]["drain_rules"].__setitem__(
            "basal_kcal_per_hour", 0), "must be positive"),
        (lambda data: data["entertainment_need"]["drain_rules"].__setitem__(
            "entertainment_need_per_awake_hour", 2.0), "unit interval"),
        (lambda data: data["entertainment_need"]["drain_rules"][
            "entertainment_relief_per_hour"].pop("watch"), "must match"),
        (lambda data: data["entertainment_need"]["drain_rules"][
            "entertainment_relief_per_hour"].__setitem__("watch", -1), "must be positive"),
        (lambda data: data["energy_kcal"]["drain_rules"].__setitem__(
            "activity_met", None), "table is missing"),
        (lambda data: data["energy_kcal"]["drain_rules"]["activity_met"].__setitem__(
            "work", -1), "must be positive"),
        (lambda data: data["energy_kcal"]["drain_rules"].__setitem__(
            "food_efficiency_curve", []), "is empty"),
        (lambda data: data["energy_kcal"].__setitem__("alert_rules", []), "HUNGRY"),
    ),
)
def test_assembly_rejects_resource_drift(mutate, match) -> None:
    """资源表漂移（缺资源/缺规则/坏数值/键集不符）逐项拒绝。"""
    sem = _semantics()
    drifted = _resources_catalog(sem, mutate)
    with pytest.raises(IntegrityError, match=match):
        assemble_semantics(sem.document, drifted)


def _objects_drifted(sem: ScenarioSemantics, mutate) -> Catalog:
    data = {key: _thaw(value.model_dump()) for key, value in sem.catalog.objects.items()}
    mutate(data)
    rebuilt = {key: ExchangeObject.model_validate(value) for key, value in data.items()}
    return replace(sem.catalog, objects=MappingProxyType(rebuilt))


def _duplicate_edge(data: dict, object_id: str, on: str) -> None:
    """复制某对象一条边，使该 on 的编译边不唯一。"""
    matches = tuple(edge for edge in data[object_id]["edges"] if edge.get("on") == on)
    data[object_id]["edges"] = data[object_id]["edges"] + [dict(matches[0])]


def _thaw(value):
    """@brief 把只读目录嵌套值复制成测试可变结构。"""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(item) for item in value]
    return value


@pytest.mark.parametrize(
    ("mutate", "match"),
    (
        (lambda data: data.pop("travel-service"), "travel-service"),
        (lambda data: data.pop("contract-package"), "contract-package"),
        (lambda data: data["contract-hourly"]["attributes"].__setitem__(
            "work_notes_multiplier", 5.0), "disagree on work_notes_multiplier"),
        (lambda data: data["contract-hourly"]["attributes"][
            "work_efficiency_factors"][0].__setitem__("input_resource", "zzz"),
         "disagree on work_efficiency_factors"),
        (lambda data: data[MILK]["attributes"].__setitem__("price_fen", True),
         "price exchange failed"),
    ),
)
def test_assembly_rejects_object_drift(mutate, match) -> None:
    """对象表漂移（缺服务/缺角色注解/坏效率/坏商品边）逐项拒绝。"""
    sem = _semantics()
    with pytest.raises(IntegrityError, match=match):
        assemble_semantics(sem.document, _objects_drifted(sem, mutate))


def test_assembly_rejects_duplicate_consume_edges() -> None:
    """同一商品两条消费边即拒绝（热量来源必须唯一）。"""

    def mutate(data):
        _duplicate_edge(data, MILK, "consume")

    sem = _semantics()
    with pytest.raises(IntegrityError, match="multiple consume edges"):
        assemble_semantics(sem.document, _objects_drifted(sem, mutate))


def test_assembly_rejects_unknown_or_mistyped_product_references() -> None:
    """商品引用：目录外对象与非商品对象都拒绝。"""
    sem = _semantics()
    base = next(item for item in sem.document.products if item.product_id == MILK)
    unknown = base.model_copy(update={"product_id": "missing-object"})
    with pytest.raises(IntegrityError, match="not in the catalog"):
        assemble_semantics(sem.document.model_copy(update={"products": (unknown,)}),
                           load_catalog(Path("catalog")))
    mistyped = base.model_copy(update={"product_id": "travel-service"})
    with pytest.raises(IntegrityError, match="undefined purchase"):
        assemble_semantics(sem.document.model_copy(update={"products": (mistyped,)}),
                           load_catalog(Path("catalog")))


def test_assembly_rejects_stockless_product_reference() -> None:
    """对象与场景都没有库存来源的商品拒绝。"""
    sem = _semantics()
    base = next(item for item in sem.document.products if item.product_id == MILK)
    stockless = base.model_copy(update={"stock": None})

    def mutate(data):
        data[MILK]["stock"] = None

    drifted = _objects_catalog(sem, mutate)
    with pytest.raises(IntegrityError, match="no stock source"):
        assemble_semantics(sem.document.model_copy(update={"products": (stockless,)}),
                           drifted)


def test_contract_payment_rejects_a_missing_template() -> None:
    """合同付款必须能取得对应的冻结模板对象。"""
    from worldmock.life.models import ContractKind
    sem = _semantics()
    shell = WorkTaskConfig.model_construct(
        contract_kind=ContractKind.HOURLY, pay_fen=60000, maximum_duration_seconds=28800)
    missing = _objects_drifted(sem, lambda data: data.pop("contract-hourly"))
    with pytest.raises(IntegrityError, match="is missing"):
        replace(sem, catalog=missing).contract_payment(shell, 0, None)


def test_assembly_requires_unique_role_account_binding() -> None:
    """目录角色必须绑定恰好一个场景账户：抽走雇主账户即拒绝。"""
    sem = _semantics()
    employer_role = next(item.role for item in sem.document.accounts
                         if item.role.startswith("employer"))
    trimmed = tuple(item for item in sem.document.accounts if item.role != employer_role)
    with pytest.raises(IntegrityError, match="not declared"):
        assemble_semantics(sem.document.model_copy(update={"accounts": trimmed}),
                           load_catalog(Path("catalog")))


def test_assembly_with_agentless_document_fails_closed() -> None:
    """无人物文档在运行时场景校验处失败关闭（引用闭集仍遍历空集）。"""
    sem = _semantics()
    with pytest.raises(IntegrityError):
        assemble_semantics(sem.document.model_copy(update={"agents": ()}),
                           load_catalog(Path("catalog")))
