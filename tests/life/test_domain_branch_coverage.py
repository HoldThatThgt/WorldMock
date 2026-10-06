"""@brief life 领域簇行为测试：补 commerce/basket/social_actions/activities/
rules/content/work/work_review/game/travel/semantics/motivation/social
的缺分支与未覆盖公共接口（C4c 覆盖率达标单元）。

只新增测试，不改生产代码。断言对照的规格章节：`docs/CONTRACTS.md#contracts-worldmock-接口合同`「请求字段与结算检查」、
`docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则`「具体活动结算」「内容提供行为」「社交最小可行版」、
`docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像`「动机层」。
"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import asyncio
from collections.abc import Mapping
import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from worldmock.catalog import (
    Catalog,
    ExchangeObject,
    ResourceDefinition,
    load_catalog,
)
from worldmock.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.categories.models import PairingDeclaration
from worldmock.categories.rules import WorkPieceRule
from worldmock.life import felt, social
from worldmock.life.activities import (
    ActivityOrigin,
    activity_factors,
    advance_activity,
    change_activity,
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
from worldmock.life.commerce import deliver_order
from worldmock.life.content import (
    load_content_titles,
    load_lifestyle_items,
    load_subtitles,
    mail_headers,
    reveal_mail,
)
from worldmock.life.game import validate_game
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
    ActivityStartParams,
    FulfillmentKind,
    PurchaseItem,
    PurchaseOrderRequest,
    StartSocialActivityRequest,
    TravelMode,
    WorldState,
)
from worldmock.catalog.affordances import engine_affordances
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
from worldmock.life.models import DataSource
from worldmock.life.map_models import SupplyConfig
from worldmock.life.storefront_state import quote_state
from worldmock.life.scenario import (
    BodyConfig,
    ContentItemConfig,
    GameConfig,
    LifeScenario,
    RuntimeScenario,
    LocationConfig,
    MessageConfig,
    MotivationProfile,
    ProductConfig,
    ProductReference,
    RouteConfig,
    RuntimeWorkTask,
    parse_scenario_document,
)
from worldmock.life.semantics import ScenarioSemantics, assemble_semantics
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.life.social import SOCIAL_RESOURCE_CLAIMS
from worldmock.life.social_actions import advance_social, end_partner_chats, partner_presence, shared_chat_seconds, social_activity_change, start_call, start_chat
from worldmock.life.travel import update_travel
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
        "entertainment_relief_preference_floor": 0.5,
        "work_notes_multiplier": 1.2, "source_kind": "test"})


def _agent(**overrides) -> AgentState:
    payload = dict(
        agent_id=1, name="Lin", location_id="home", balance_account_id="agent:1",
        body_settled_at=0,
        next_decision_at=0,
        body=BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=4.0,
                       entertainment_need=0.5),
        skill_levels={"ami": 0.8}, taste_weights={"movie-0001": 0.8})
    payload.update(overrides)
    return AgentState(**payload)


def _product(product_id="food-1", kcal=600,
             duration=900, use_modes=("eat",), consumable=True, effect="fed",
             required_location=None) -> ProductConfig:
    serving = {"serving_quantity": 1.0, "serving_unit": "serving"} if "eat" in use_modes else {}
    return ProductConfig.model_validate({
        "purchase_price_fen_min": 1, "purchase_price_fen_max": 100_000,
        "product_id": product_id, "name": "Test food",
        "category_id": "food" if "eat" in use_modes else "durable", "inventory_unit": "item",
        "energy_kcal_per_unit": kcal if "eat" in use_modes else 0,
        "use_duration_seconds": duration, "use_modes": list(use_modes),
        "consumable": consumable, "effect": effect,
        "required_location_id": required_location,
        "source_kind": "test", **serving})


def _source(kind="scenario") -> dict:
    return {"kind": kind, "url": "scenario://domain-tests", "retrieved_at": "2023-11-15T00:00:00+08:00",
            "license": "test", "notice": "Domain fixture", "fact_cutoff": "2023-11-14",
            "applicability": "Explicit test fixture"}


def _supply(product_id="food-1", supply_id="supply-food-1", location="market", price=800):
    return SupplyConfig.model_validate({
        "supply_id": supply_id, "product_id": product_id, "location_id": location,
        "seller_account_id": "merchant:g", "initial_stock": 5,
        "fulfillment_modes": ["pickup", "delivery"], "delivery_destination_ids": ["home"],
        "delivery_delay_seconds": 600, "required_qualifications": [], "source": _source(),
        "quotes": [{"price_fen": price, "price_version": "v1", "effective_from": "2023-01-01T00:00:00+08:00",
                    "effective_to": None, "reviewed_on": "2023-11-14", "still_valid_on": "2027-01-01",
                    "source": _source()}]})


def _route(route_id="r1", origin="market", destination="home", mode="driving",
           seconds=600, fare=600, response_path="routes/a.json", response_sha256="a" * 64) -> RouteConfig:
    profile = "car" if mode == "driving" else "foot"
    return RouteConfig.model_validate({
        "route_id": route_id, "origin_id": origin, "destination_id": destination,
        "mode": mode, "status": "reachable", "distance_meters": 3000, "duration_seconds": seconds,
        "fare_fen": fare, "fare_source_kind": "scenario", "reason": None,
        "response_path": response_path, "response_sha256": response_sha256,
        "routing_instance": "https://routing.example", "routing_profile": profile,
        "source_url": f"https://routing.example/route/v1/{profile}/test", "source": _source("fact")})


def _location(location_id="market", opens=0, closes=1440) -> LocationConfig:
    return LocationConfig.model_validate({
        "location_id": location_id, "name": location_id, "kind": "shop", "business_kind": "grocery",
        "location_kinds": ["venue"], "longitude": 121.4, "latitude": 31.2,
        "required_qualifications": [], "brand": None, "affordances": {}, "source": _source(),
        "opening_calendars": [{"effective_from": "2023-01-01T00:00:00+08:00", "effective_to": None,
                               "reviewed_on": "2023-11-14", "still_valid_on": "2027-01-01", "source": _source(),
                               "weekly": {str(day): [{"opens": opens, "closes": closes}] for day in range(7)},
                               "exceptions": {}}]})


def _life_scenario(products=(), routes=(), locations=(), timezone=TIMEZONE) -> RuntimeScenario:
    return RuntimeScenario.model_construct(schema_version=6, products=tuple(products), routes=tuple(routes),
                                            locations=tuple(locations), supplies=(), timezone=timezone)


def _world(agent: AgentState, *, accounts=None, stock=None, current_time=CURRENT,
           activities=None, deliveries=None) -> WorldState:
    return WorldState.model_construct(
        agents={agent.agent_id: agent},
        accounts=accounts if accounts is not None else {"agent:1": 100_000, "merchant:g": 0},
        supply_stock=stock if stock is not None else {}, supply_quotes={},
        opening_exceptions={"home": {}, "market": {}},
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
        location_id="home", data={})
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
# commerce：单件购买/配送/按进度使用（`docs/CONTRACTS.md#contracts-worldmock-接口合同`「请求字段与结算检查」）
# ---------------------------------------------------------------------------

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
# basket：整单购买与多件进食（`docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像`「商务与进食」）
# ---------------------------------------------------------------------------

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
    """进食 1-3 件、非食品单件（`docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像`「商务与进食」）。"""
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
    """使用路径镜像 commerce 口径：耐用品完成全额、暂停不推进、完成时手边不够一件的消耗品拒绝（食用只按开吃份量结算，
    不在这里扣库存，见 test_dayrun_inventory_regressions）。"""
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
    wipes = _product(product_id="item-wipe", use_modes=("use",), consumable=True, effect="clean", kcal=0)
    starved = _use_activity(_consume(product_id="item-wipe", use_mode="use"), status=ActivityStatus.COMPLETED,
                            ended_at=900, elapsed_seconds=900, last_settled_at=900)
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_HELD"):
        apply_meal_progress(_agent(inventory={"home": {"item-wipe": 0.1}}),
                            {"item-wipe": wipes}, starved, _body())


# ---------------------------------------------------------------------------
# social + social_actions：社交最小可行版（`docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则`「社交最小可行版」）
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


def test_social_actions_start_chat_and_call_conflict_with_occupied_resources(engine_factory) -> None:
    """交谈、通话同占 talk：正在进行的通话与新的交谈、通话按 talk 的交集逐项拒绝；正在看的视频占 watch、screen，
    与交谈、通话不是同一种，照常受理（docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-占用声明）。"""
    busy = _agent()
    talking = _activity(activity_id="talking", kind=ActivityKind.CALL, resource_claims={"talk"})
    watch = _activity(resource_claims={"watch", "screen"})
    state = _social_state(busy, activities={talking.activity_id: talking})
    watching = _social_state(busy, activities={watch.activity_id: watch})
    state.agents[2] = _agent(agent_id=2, location_id="home")
    request = StartSocialActivityRequest(kind="chat", partner_id="agent:2",
                                         duration_minutes=10)
    chat = start_chat(state, busy, request, _origin(), "m1")
    contact = social.Contact(contact_id="c1", salutation="妈", relation="母亲",
                             closeness=20, channels=("phone",), reply_policy="template")
    call = start_call(busy, contact, request, _origin(), CURRENT)
    engine = engine_factory()
    for started in (chat, call):
        assert started.activity.resource_claims == SOCIAL_RESOURCE_CLAIMS == {"talk"}
        with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
            engine.actions._require_available_resources(state, busy.agent_id, started.activity.resource_claims)
        engine.actions._require_available_resources(watching, busy.agent_id, started.activity.resource_claims)


def _chat(activity_id: str, agent_id: int, partner: int, **overrides) -> ActivityState:
    """以另一人物为伙伴的进行中交谈（默认 0 秒开始、600 秒计划结束、尚未结算）。"""
    return _activity(activity_id=activity_id, agent_id=agent_id, kind=ActivityKind.CHAT,
                     data={"partner_id": f"agent:{partner}"}, **overrides)


def test_social_actions_choosing_chat_with_the_inviter_accepts() -> None:
    """对方正以本人为伙伴交谈时，对其选择交谈即接受：不再发邀请，计划结束对齐发起方；同一对人物再次发起以
    CHAT_ALREADY_ACTIVE 拒绝（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。"""
    invitation = _chat("a2", 2, 1, started_at=CURRENT - 60, last_settled_at=CURRENT, planned_end_at=CURRENT + 540)
    state = _social_state(_agent(), activities={"a2": invitation})
    state.agents[2] = _agent(agent_id=2, name="B")
    request = StartSocialActivityRequest(kind="chat", partner_id="agent:2", duration_minutes=10)
    accepted = start_chat(state, state.agents[1], request, _origin(), "m1")
    assert accepted.invite is None
    assert accepted.activity.planned_end_at == invitation.planned_end_at
    assert accepted.activity.data == {"partner_id": "agent:2", "accepted": True}
    assert accepted.accepted.activity_id == "a2" and accepted.accepted.data["accepted"] is True
    state.activities[accepted.activity.activity_id] = accepted.activity
    with pytest.raises(BusinessRuleError, match="CHAT_ALREADY_ACTIVE"):
        start_chat(state, state.agents[1], request, _origin(), "m2")
    # 接受以共同在场为前提：准入已先按目标在场拒绝，领域层遇到不在一处的接受是程序错误。
    apart = _social_state(_agent(), activities={"a2": invitation})
    apart.agents[2] = _agent(agent_id=2, name="B", location_id="office")
    with pytest.raises(ValueError, match="co-presence"):
        start_chat(apart, apart.agents[1], request, _origin(), "m3")
    # 发起方的交谈只剩不足一分钟：对齐后不足最短时长，以带原因与下一步的 ACTIVITY_TOO_SHORT 拒绝。
    late = _social_state(_agent(), activities={"a2": invitation.model_copy(update={"planned_end_at": CURRENT + 59})})
    late.agents[2] = _agent(agent_id=2, name="B")
    with pytest.raises(BusinessRuleError, match="ACTIVITY_TOO_SHORT") as refused:
        start_chat(late, late.agents[1], request, _origin(), "m4")
    assert (refused.value.public_reason, refused.value.suggested) == (
        "The other person's conversation has less than one minute remaining.", {"next_step": "Wait for the current conversation to end, then start a new one."})


def test_social_actions_shared_chat_seconds_counts_the_reciprocal_overlap() -> None:
    """接受带来的缓解按区间内双方交谈同时进行的秒数：对方以本人为伙伴的交谈在区间起点进行中才算，止于双方较早的
    结束；以推进前的状态判定，两人谁先推进结果都一样（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。"""
    mine, partner = _chat("a1", 1, 2), _chat("a2", 2, 1)

    def shared(other: ActivityState | None, target: int = 300) -> int:
        activities = {"a1": mine} if other is None else {"a1": mine, "a2": other}
        return shared_chat_seconds(_social_state(_agent(), activities=activities), mine, target)

    both = _social_state(_agent(), activities={"a1": mine, "a2": partner})
    assert shared_chat_seconds(both, mine, 300) == shared_chat_seconds(both, partner, 300) == 300
    assert shared(partner, 900) == 600  # 本人的计划结束封顶
    assert shared(partner.model_copy(update={"planned_end_at": 200})) == 200
    # 对方在本次推进中先结算完成的，按其结束时刻计。
    assert shared(partner.model_copy(update={"status": ActivityStatus.COMPLETED, "ended_at": 250})) == 250
    assert shared(partner.model_copy(update={"status": ActivityStatus.COMPLETED, "ended_at": 0})) == 0
    assert shared(partner.model_copy(update={"started_at": 100})) == 0  # 区间起点时还没有接受
    assert shared(_chat("a2", 2, 3)) == 0  # 对方在和别人交谈
    assert shared(None) == 0
    call = _activity(kind=ActivityKind.CALL, data={"contact_id": "c1"})
    assert shared_chat_seconds(both, call, 300) == 0


def test_social_actions_ending_a_chat_ends_the_partner_chat_the_same_second() -> None:
    """一方的交谈结束时，对方以其为伙伴的进行中交谈同秒结束：completed，boundary_type 与 interruption_reason
    都记 partner_ended（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）；没有这样的对方交谈时世界不变。"""
    mine, partner = _chat("a1", 1, 2), _chat("a2", 2, 1)
    ended = mine.model_copy(update={"status": ActivityStatus.CANCELLED, "ended_at": 300})
    before = _world(_agent(), activities={"a1": mine, "a2": partner}, current_time=300)
    changed, partners = end_partner_chats(before, before.model_copy(update={"activities": {"a1": ended,
                                                                                          "a2": partner}}))
    closed = changed.activities["a2"]
    assert partners == [2] and changed.activities["a1"] == ended
    assert (closed.status, closed.ended_at, closed.boundary_type, closed.interruption_reason) == (
        ActivityStatus.COMPLETED, 300, "partner_ended", "partner_ended")
    alone = _world(_agent(), activities={"a1": mine}, current_time=300)
    after = alone.model_copy(update={"activities": {"a1": ended}})
    assert end_partner_chats(alone, after) == (after, [])
    # 没有人物伙伴的交谈结束时不牵连任何人。
    stray = _activity(activity_id="a3", kind=ActivityKind.CHAT, data={})
    lone = _world(_agent(), activities={"a3": stray, "a2": partner}, current_time=300)
    finished = lone.model_copy(update={"activities": {
        "a3": stray.model_copy(update={"status": ActivityStatus.CANCELLED, "ended_at": 300}), "a2": partner}})
    assert end_partner_chats(lone, finished) == (finished, [])


def test_social_actions_advance_rejects_backwards_settlement_time() -> None:
    """社交活动结算时间倒流是程序错误，不是业务拒绝。"""
    backwards = _activity(kind=ActivityKind.CHAT, last_settled_at=900)
    with pytest.raises(ValueError, match="moved backwards"):
        advance_social(backwards, _agent(), 600, _body(), shared_seconds=0)


def test_social_actions_advance_skips_non_active_activity() -> None:
    """暂停的交谈推进为无结算空转，保留既有进度。"""
    paused = _activity(kind=ActivityKind.CHAT, status=ActivityStatus.PAUSED)
    result = advance_social(paused, _agent(), 600, _body(), shared_seconds=0)
    assert result.settlement is None and result.completed is False
    assert result.activity.status == ActivityStatus.PAUSED


def test_social_actions_advance_completes_chat_without_agent_activity_slot() -> None:
    """到计划终点完成交谈；终态归活动，人物不保存旧主活动槽。"""
    finishing = _activity(kind=ActivityKind.CHAT)
    result = advance_social(finishing, _agent(), 600, _body(), shared_seconds=0)
    assert result.completed is True
    assert result.activity.status == ActivityStatus.COMPLETED
    assert result.activity.boundary_type == "planned_end"
    assert result.agent.agent_id == 1 and result.agent.body_settled_at == 600


@pytest.mark.parametrize(("kind", "shared_seconds"), ((ActivityKind.CALL, 0), (ActivityKind.CHAT, 3600),
                                                      (ActivityKind.CHAT, 1800), (ActivityKind.CHAT, 0)))
def test_social_actions_advance_applies_felt_ledger_by_shared_seconds(kind, shared_seconds) -> None:
    """通话按通话账本；交谈中双方同时进行的秒数按交谈账本缓解，其余秒数按发起方 0.2/小时
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。"""
    start_need = 0.8
    agent = _agent(body=_agent().body.model_copy(update={"social_need": start_need}))
    activity = _activity(kind=kind, planned_end_at=3600, planned_duration_seconds=3600,
                         data={"partner_id": "agent:2"})
    result = advance_social(activity, agent, 3600, _body(), shared_seconds=shared_seconds)
    hours, shared = 1.0, shared_seconds / 3600
    if kind == ActivityKind.CALL:
        expected = felt.apply_social_need_ledger(start_need, felt.SocialExposure(awake_hours=hours, call_hours=hours))
    else:
        exposure = felt.SocialExposure(awake_hours=hours, conversation_hours=shared)
        expected = (felt.apply_social_need_ledger(start_need, exposure)
                    - social.CHAT_RELIEF_DECLINED_PER_HOUR * (hours - shared))
    assert result.agent.body.social_need == pytest.approx(expected)


def test_social_activity_change_cancel_pause_and_unknown_operations() -> None:
    """社交活动转换闭集：取消任意时刻、不可暂停、未知操作拒绝、恢复无操作。"""
    active = _activity(kind=ActivityKind.CALL)
    agent, cancelled = social_activity_change(_agent(), active,
                                              "cancel", 100)
    assert cancelled.status == ActivityStatus.CANCELLED
    assert cancelled.boundary_type == "cancelled"
    assert agent.agent_id == 1
    terminal = cancelled
    with pytest.raises(BusinessRuleError, match="ACTIVITY_ALREADY_TERMINAL"):
        social_activity_change(agent, terminal, "cancel", 100)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_CANNOT_PAUSE"):
        social_activity_change(_agent(), active, "pause", 100)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_OPERATION_UNKNOWN"):
        social_activity_change(_agent(), active, "rewind", 100)
    same_agent, same = social_activity_change(_agent(), active,
                                              "resume", 100)
    assert same.activity_id == "a1" and same_agent.agent_id == 1


# ---------------------------------------------------------------------------
# activities：活动转换闭集（`docs/CONTRACTS.md#contracts-worldmock-接口合同`「请求字段与结算检查」的「修改活动」行）
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
    agent = _agent()
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.CHANGE_SPEED,
                                    playback_speed=2.0)
    changed_agent, changed = change_activity(agent, watch, request, 1000, 300)
    assert changed.playback_speed == 2.0
    assert changed.next_interaction_at == 1300  # 严格未来且早于完成
    assert changed.activity_id == "a1" and changed_agent.agent_id == 1


def test_change_activity_speed_rejected_for_non_watch_activity() -> None:
    """倍速只对进行中的 WATCH 活动有意义（PLAY_GAME 边界也不受理）。"""
    game = _feedback_activity(kind=ActivityKind.PLAY_GAME, boundary_type="game_feedback")
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.CHANGE_SPEED,
                                    playback_speed=2.0)
    with pytest.raises(BusinessRuleError, match="PLAYBACK_SPEED_NOT_ALLOWED"):
        change_activity(_agent(), game, request, 1000, 300)


def test_advance_lifestyle_without_social_or_health_effects_skips_relief() -> None:
    """无社交/健康效应的生活活动只走常规结算，不落额外缓解。"""
    body = _body().model_copy(update={
        "activity_met": {**_body().activity_met, "lifestyle:walk": 1.5}})
    activity = _activity(kind=ActivityKind.LIFESTYLE, target_id="walk",
                         data={"effects": {"entertainment": 0.2, "social": 0.0, "health": 0.0}})
    restored = ActivityState.model_validate_json(activity.model_dump_json())
    result = advance_activity(restored, _agent(), 300, body)
    assert result.completed is False
    assert result.agent.body.social_need == _agent().body.social_need
    assert result.agent.body.entertainment_need == pytest.approx(0.5 - 0.1 / 12)


def test_change_activity_pause_at_game_feedback_boundary() -> None:
    """游戏交互边界（game_feedback）允许暂停；边界不符即拒绝。"""
    game = _feedback_activity(kind=ActivityKind.PLAY_GAME, boundary_type="game_feedback")
    agent = _agent()
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.PAUSE)
    _, paused = change_activity(agent, game, request, 1000, 300)
    assert paused.status == ActivityStatus.PAUSED
    wrong = _feedback_activity(kind=ActivityKind.PLAY_GAME, boundary_type="feedback")
    with pytest.raises(BusinessRuleError, match="ACTIVITY_FEEDBACK_NOT_DUE"):
        change_activity(agent, wrong, request, 1000, 300)


def test_change_activity_resume_conflicts_with_occupied_resources(engine_factory) -> None:
    """恢复前引擎校验同人的活动占用交集（两段观看都占 watch、screen），冲突时暂停进度不变。"""
    paused = _activity(status=ActivityStatus.PAUSED, resource_claims={"watch", "screen"})
    active = _activity(activity_id="occupied", resource_claims={"watch", "screen"})
    state = _world(_agent(), activities={"a1": paused, "occupied": active})
    engine = engine_factory()
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        engine.actions._require_available_resources(state, 1, paused.resource_claims, paused.activity_id)
    assert state.activities["a1"].status == ActivityStatus.PAUSED
    assert state.activities["a1"].elapsed_seconds == 0


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
    assert changed is done and agent == _agent()


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
# motivation：动机层（`docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像`「动机层」）
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
    "attention_prior": {"feature_film": 0.3, "podcast_episode": 0.05,
                        "news_feed": 0.2, "joke_feed": 0.1,
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
        purchase_pairing_states={item.product_id: "defined" for item in overrides.get("products", ())},
        products=(), supplies=(), supply_stock={}, supply_quotes={}, opening_exceptions={},
        lifestyle_items=(), activity_location_kinds={},
        locations={}, routes=(), tasks=(),
        task_totals={}, contents=(), content_progress={}, active_activities=(),
        habit_events=(), consecutive_rejections=0, max_same_moment_decisions=3,
        total_effective_seconds=0.0, current_time=CURRENT,
        end_time=CURRENT + 86400, timezone=TIMEZONE,
        planned_travel_seconds=lambda mode, base, departure: base,
        purchase_amount=lambda product, price, quantity: price * quantity)
    defaults.update(overrides)
    return MotivationContext(**defaults)


def test_motivation_transform_scales_cover_all_resources() -> None:
    """归一尺度闭集覆盖五类资源键。"""
    transforms = load_motivation_transforms(ENVIRONMENT_ROOT / "catalog" / "rules")
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
    context = _context(catalog_supplies=(_supply(),), supplies=())
    assert _browse_candidate(context, {"probe_duration_seconds": 30}) is None


def test_sleep_candidate_skips_when_session_falls_below_minimum() -> None:
    """小睡候选时长被钳到不足一分钟时不出候选。"""
    context = _context(agent=_agent(body=BodyState(energy_kcal=1600, sleep_pressure=0.0,
                                                   sleep_deficit_hours=0.0,
                                                   entertainment_need=0.5)))
    params = {"fallback_seconds": 30, "min_session_seconds": 0, "cap_seconds": 4 * 3600}
    assert _sleep_candidate(context, params) is None


def test_feasible_food_rows_drop_unroutable_delivery() -> None:
    """已准入地点的供给仍须具有明确可达的配送道路，不能虚构即时取得。"""
    distant = _product(product_id="food-far")
    supply = _supply(product_id="food-far", supply_id="supply-far")
    known = _context(products=(distant,), supplies=(supply,), locations={"market": _location("market")},
                     supply_stock={supply.supply_id: 5}, supply_quotes={supply.supply_id: quote_state(supply.quotes[0])},
                     opening_exceptions={"market": {}}, routes=())
    assert _feasible_food_rows(known) == []


def test_continue_candidate_covers_travel_lifestyle_and_consume_flows() -> None:
    """继续当前活动按活动类型取 MET 与资源流。"""
    context = _context(agent=_agent())
    travel = _activity(kind=ActivityKind.TRAVEL, travel_mode=TravelMode.DRIVING,
                       planned_end_at=3600, planned_duration_seconds=3600)
    assert _continue_candidate(context, travel) is not None
    lifestyle_body = _body().model_copy(update={
        "activity_met": {**_body().activity_met, "lifestyle:jog": 2.0}})
    lifestyle = _activity(kind=ActivityKind.LIFESTYLE, target_id="jog",
                          data={"effects": {"entertainment": 0.2, "social": 0.0, "health": 0.0}},
                          planned_end_at=3600, planned_duration_seconds=3600)
    assert _continue_candidate(_context(body=lifestyle_body), lifestyle) is not None
    consume = _activity(kind=ActivityKind.CONSUME,
                        planned_end_at=3600, planned_duration_seconds=3600,
                        data={"food_energy_kcal_per_hour": 600})
    candidate = _continue_candidate(context, consume)
    assert candidate is not None and candidate.energy_delta > -candidate.energy_delta


def test_continue_candidate_skips_when_remaining_or_energy_infeasible() -> None:
    """剩余不足一分钟或能量不可完成时不出继续候选。"""
    almost_done = _activity(elapsed_seconds=570)
    assert _continue_candidate(_context(), almost_done) is None
    long_work = _activity(kind=ActivityKind.WORK, planned_end_at=7200,
                          planned_duration_seconds=7200)
    exhausted = _agent(body=BodyState(energy_kcal=10, sleep_pressure=0.0,
                                      sleep_deficit_hours=4.0, entertainment_need=0.5))
    assert _continue_candidate(_context(agent=exhausted), long_work) is None


def test_continue_candidate_skips_social_boundary_kinds_without_met_lookup() -> None:
    """交谈/通话不出继续候选：引擎仅接受取消，且其 kind 不在注册表 MET 表内。

    run11 观察降级复现面：chat 以 ACTIVE 跨决策边界后，继续候选按 kind 查
    activity_met 抛 KeyError('chat')，使 motivation_internal 整段从观察消失。
    """
    chat = _activity(kind=ActivityKind.CHAT, planned_end_at=900,
                     planned_duration_seconds=900)
    call = _activity(kind=ActivityKind.CALL, planned_end_at=900,
                     planned_duration_seconds=900)
    assert _continue_candidate(_context(), chat) is None
    assert _continue_candidate(_context(), call) is None


def test_sleep_deficit_delta_recovers_only_for_sleep() -> None:
    """睡眠按 1h/h 清账，其余活动按清醒标度累积。"""
    context = _context(agent=_agent())
    sleeping = _sleep_deficit_delta(context, ActivityKind.SLEEP, 1.0)
    awake = _sleep_deficit_delta(context, ActivityKind.WORK, 1.0)
    assert sleeping < 0 < awake


def test_entertainment_delta_watch_without_evidence_is_rejected() -> None:
    """观看证据缺失时预估与结算一样拒绝，不能改算清醒累积。"""
    watch = _activity(kind=ActivityKind.WATCH, target_id="movie-0001", data={})
    with pytest.raises(ValueError, match="watch activity entertainment evidence is incomplete"):
        _entertainment_delta(_context(), watch, 1.0)


def test_travel_candidate_rejects_short_or_energy_infeasible_legs() -> None:
    """出行候选：分时计划不足一分钟或能量不可支撑时不出候选。"""
    context = _context(routes=(_route("r1", "home", "office"),))
    short = _context(routes=(_route("r1", "home", "office"),),
                     planned_travel_seconds=lambda mode, base, departure: 30)
    assert _travel_candidate(short, "work", "office") is None
    exhausted = _context(agent=_agent(body=BodyState(energy_kcal=10, sleep_pressure=0.0,
                                                     sleep_deficit_hours=4.0,
                                                     entertainment_need=0.5)),
                         routes=(_route("r1", "home", "office"),),
                         planned_travel_seconds=lambda mode, base, departure: 7200)
    assert _travel_candidate(exhausted, "work", "office") is None
    assert _travel_candidate(context, "work", "office") is not None


def _work_task(task_id="task-1", pay=50000, location="office", due_day=16,
               minimum_skill=0.3) -> RuntimeWorkTask:
    zone = ZoneInfo(TIMEZONE)
    return RuntimeWorkTask.model_validate({
        "task_id": task_id, "title": "完成周会纪要", "assignee_agent_id": "agent:1",
        "contract_kind": "package",
        "appointment": None, "available_at": datetime(2023, 11, 13, tzinfo=zone),
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
        "item_id": "tiny", "title": "Tiny film", "kind": "feature_film", "duration_seconds": 10,
        "timeline_path": "t.srt", "timeline_sha256": "d" * 64,
        "entertainment_relief_per_hour": 0.3, "experience_label": "Movie"})
    agent = _agent(taste_weights={"tiny": 0.8})
    family = next(item for item in engine_affordances().families_by_rank()
                  if item.name == "entertainment")
    context = _context(agent=agent, contents=(tiny,), taste_weights={"tiny": 0.8})
    assert _entertainment_candidates(context, _profile(), family) == []


# ---------------------------------------------------------------------------
# content：内容增量揭示（`docs/CONTRACTS.md#contracts-worldmock-接口合同`「请求字段与结算检查」的「使用」行）
# ---------------------------------------------------------------------------

def _write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_load_lifestyle_items_rejects_broken_catalogs(tmp_path) -> None:
    """生活目录闭集校验：缺 items/坏行/重复 id/身份不匹配逐项 fail-closed。"""
    good_row = {"activity_id": "walk", "name_zh": "散步", "category": "wellness",
                "typical_duration_minutes": 30, "description_zh": "走走",
                "met_value": 2.0, "effects": {"entertainment": 0.2, "social": 0.0, "health": 0.0}}
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
        "item_id": "c1", "title": "Test news", "kind": "news_feed", "duration_seconds": 600,
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


def test_content_configuration_rejects_blank_or_missing_titles() -> None:
    """@brief 内容声明必须提供非空标题，已验证标题直接用于呈现。"""
    item = _content_item("d" * 64)
    assert load_content_titles((item,)) == {"c1": "Test news"}
    blank = {**item.model_dump(), "title": "   "}
    with pytest.raises(ValidationError, match="content title must be a nonempty string"):
        ContentItemConfig.model_validate(blank)
    missing = item.model_dump()
    del missing["title"]
    with pytest.raises(ValidationError, match="Field required"):
        ContentItemConfig.model_validate(missing)


def test_load_subtitles_rejects_escaped_path(tmp_path) -> None:
    """内容读取限制在冻结语料目录，越界路径拒绝。"""
    escaped = _content_item("0" * 64).model_copy(update={"timeline_path": "../evil.srt"})
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_subtitles(escaped, tmp_path)


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
# work：任务受理与材料揭示（`docs/CONTRACTS.md#contracts-worldmock-接口合同`「请求字段与结算检查」的「提交工作」行）
# ---------------------------------------------------------------------------

def test_validate_work_start_requires_visible_task() -> None:
    """不可见任务不能开始工作。"""
    with pytest.raises(BusinessRuleError, match="WORK_TASK_NOT_VISIBLE"):
        validate_work_start(_agent(visible_task_ids=set()), _work_task(), CURRENT, "office")


def test_validate_work_start_en_route_is_not_at_the_workplace() -> None:
    """开工要人在任务地点；途中（现场地点为 None）不算在任何地点，location_id 仍是出发地的办公室也不行
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」的实际地点）。"""
    agent = _agent(location_id="office", visible_task_ids={"task-1"})
    validate_work_start(agent, _work_task(), CURRENT, "office")
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET") as caught:
        validate_work_start(agent, _work_task(), CURRENT, None)
    assert caught.value.unmet == ["required_location_id"]


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

    def oracle_task(payload: bytes) -> RuntimeWorkTask:
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
    task = RuntimeWorkTask.model_validate({
        "task_id": "task-piece", "title": "整理项目纪要", "assignee_agent_id": "agent:1",
        "contract_kind": "piece",
        "appointment": None, "available_at": datetime(2023, 11, 1, tzinfo=zone),
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
    rule = WorkPieceRule.model_validate({
        "rule_id": "test-work-piece", "category_id": "contract", "action": "work:settle",
        "operator": "work_piece", "resource_id": "money_fen", "unit": "fen",
        "conservation": "closed", "rate_slot": "work_rate_fen"})
    base = _semantics()
    pairing = PairingDeclaration.model_validate({
        "actor_category_id": "person", "target_category_id": "contract", "action": "work:settle",
        "state": "defined", "rule_id": rule.rule_id, "coefficient": 1})
    contract = replace(base.catalog.category_contract, pairings=(*base.catalog.category_contract.pairings, pairing))
    rules = replace(base.catalog.exchange_rules, rules={**base.catalog.exchange_rules.rules, rule.rule_id: rule})
    catalog = replace(base.catalog, category_contract=contract, exchange_rules=rules)
    semantics = replace(base, scenario=scenario, catalog=catalog)
    request = SubmitWorkRequest.model_construct(
        task_id="task-piece",
        deliverable=SimpleNamespace(model_dump=lambda **_k: {}))
    with pytest.raises(IntegrityError, match="EXCHANGE_PIECE_COUNT_MISSING"):
        prepare_work_submission(state, semantics, tmp_path, 1, request)


def test_load_work_input_rejects_missing_or_escaped_files(tmp_path) -> None:
    """工作输入文件必须存在于冻结根目录内。"""
    raw = "会议记录".encode()
    task = _work_task().model_copy(update={"input_sha256": _sha(raw)})
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_work_input(tmp_path, task)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(raw)
    escaped = task.model_copy(update={"input_path": "../outside.txt"})
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_work_input(tmp_path, escaped)


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
# work_review：独立验收协议（`docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则`「内容提供行为」）
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
    reviewer.purpose = "Work review"
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
    task = RuntimeWorkTask.model_construct(input_sha256="0" * 64, oracle_sha256="1" * 64)
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
# game：TextWorld 身份与重放（`docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则`「具体活动结算」的游戏行）
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


def test_game_path_rejects_escape(tmp_path) -> None:
    """游戏读取限制在冻结语料目录，越界路径拒绝。"""
    escaped = GameConfig.model_construct(
        game_id="g", seed=1, game_path="../outside.json",
        game_sha256="0" * 64, walkthrough_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="path is invalid"):
        validate_game(escaped, tmp_path)


# ---------------------------------------------------------------------------
# travel：路线校验与定位轨迹（`docs/CONTRACTS.md#contracts-worldmock-接口合同`「请求字段与结算检查」的「启动活动」行）
# ---------------------------------------------------------------------------







def test_update_travel_moves_location_only_on_arrival() -> None:
    """按完成比例推进距离，到达前不改变人物地点。"""
    from tests.life.test_travel_graph import travel_case

    _, _, state, trip = travel_case()
    midway = state.activities["travel-1"].model_copy(update={"elapsed_seconds": 100})
    changed_activity, same_agent = update_travel(midway, trip, state.agents[1])
    assert changed_activity.route_progress_meters == pytest.approx(trip.distance_meters * 100 / trip.duration_seconds)
    assert same_agent.location_id == "home"
    done = midway.model_copy(update={"elapsed_seconds": trip.duration_seconds})
    _, arrived = update_travel(done, trip, state.agents[1])
    assert arrived.location_id == "office"




# ---------------------------------------------------------------------------
# rules：封闭目录查找（`docs/CONTRACTS.md#contracts-worldmock-接口合同`「请求字段与结算检查」的启动活动行）
# ---------------------------------------------------------------------------

def test_rules_lookup_helpers_reject_unknown_ids(engine_factory) -> None:
    """商品/任务/游戏/字幕闭集查表：目录外身份业务拒绝。"""
    engine = engine_factory()
    rules = engine.context.rules
    real_product = engine.context.scenario.products[0].product_id
    assert rules.product(real_product).product_id == real_product
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        rules.product("missing-product")
    task = _work_task()
    rules.tasks = {task.task_id: task}
    real_task = task.task_id
    assert rules.task(real_task).task_id == real_task
    with pytest.raises(BusinessRuleError, match="WORK_TASK_NOT_FOUND"):
        rules.task("missing-task")
    game = GameConfig(game_id="test-game", title="Test game", seed=1, game_path="game.json",
                      game_sha256="1" * 64, walkthrough_sha256="2" * 64)
    rules.games = {game.game_id: game}
    real_game = game.game_id
    assert rules.game(real_game).game_id == real_game
    with pytest.raises(BusinessRuleError, match="GAME_NOT_FOUND"):
        rules.game("missing-game")
    real_content = engine.context.scenario.contents[0].item_id
    assert rules.cues(real_content)
    with pytest.raises(BusinessRuleError, match="CONTENT_TIMELINE_NOT_FOUND"):
        rules.cues("missing-content")


def test_rules_validate_start_work_duration_and_deadline(engine_factory) -> None:
    """工作启动：超出合同最大工时/会错过截止分别拒绝。"""
    engine = engine_factory()
    rules = engine.context.rules
    task = _work_task()
    rules.tasks = {task.task_id: task}
    agent = engine.context.state.agents[1].model_copy(update={
        "visible_task_ids": {task.task_id},
        "location_id": task.required_location_id,
        "skill_levels": {task.required_skill: task.minimum_skill + 0.1}})
    now = int(task.available_at.timestamp())
    over = ActivityStartParams(activity_type=ActivityKind.WORK,
                                target_id=task.task_id,
                                planned_duration_seconds=task.maximum_duration_seconds + 60)
    with pytest.raises(BusinessRuleError, match="WORK_DURATION_EXCEEDS_CONTRACT"):
        rules.validate_start(agent, over, now, task.required_location_id)
    near_deadline = int(task.due_at.timestamp()) - 60
    tight = ActivityStartParams(activity_type=ActivityKind.WORK, target_id=task.task_id,
                                 planned_duration_seconds=3600)
    with pytest.raises(BusinessRuleError, match="WORK_WOULD_MISS_DEADLINE"):
        rules.validate_start(agent, tight, near_deadline, task.required_location_id)


def test_rules_validate_start_browse_target_must_be_catalog(engine_factory) -> None:
    """浏览目标只有 product_catalog 一个合法值。"""
    engine = engine_factory()
    request = ActivityStartParams(activity_type=ActivityKind.BROWSE,
                                   target_id="something_else",
                                   planned_duration_seconds=60)
    agent = engine.context.state.agents[1]
    with pytest.raises(BusinessRuleError, match="BROWSE_TARGET_NOT_FOUND"):
        engine.context.rules.validate_start(agent, request, CURRENT, agent.location_id)
    fine = request.model_copy(update={"target_id": "product_catalog"})
    assert engine.context.rules.validate_start(agent, fine, CURRENT, agent.location_id) == {
        "catalog": "all_products"}


# ---------------------------------------------------------------------------
# semantics：目录组装准入（fail-closed）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _semantics() -> ScenarioSemantics:
    return load_runtime_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0]


def test_load_runtime_scenario_wraps_catalog_load_failures(tmp_path) -> None:
    """完整加载把目录装载错误包装为 IntegrityError（fail-closed）。"""
    with pytest.raises(IntegrityError, match="Catalog load failed"):
        load_runtime_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json",
                              catalog_dir=tmp_path / "missing-catalog")


def _resources_catalog(sem, mutate) -> Catalog:
    data = {key: value.model_dump() for key, value in sem.catalog.resources.items()}
    mutate(data)
    rebuilt = {key: ResourceDefinition.model_validate(value) for key, value in data.items()}
    return replace(sem.catalog, resources=MappingProxyType(rebuilt))






def _objects_catalog(sem, mutate) -> Catalog:
    data = {key: _thaw(value.model_dump()) for key, value in sem.catalog.objects.items()}
    mutate(data)
    rebuilt = {key: ExchangeObject.model_validate(value) for key, value in data.items()}
    return replace(sem.catalog, objects=MappingProxyType(rebuilt))


def _food_product_id(sem: ScenarioSemantics) -> str:
    """从测试显式环境选择有真实消费能量边的商品。"""
    return next(item.product_id for item in sem.scenario.products if item.energy_kcal_per_unit > 0)


def test_travel_fare_rejects_an_unknown_mode() -> None:
    """出行费率只接受冻结动作集合中的出行方式。"""
    from tests.life.test_travel_graph import travel_case
    from worldmock.life.travel import plan_trip

    catalog, _, _, _ = travel_case()
    with pytest.raises(ValueError, match="outside the fixed set"):
        plan_trip(catalog.graph, "home", "office", "biking")


def _at_hour(hour: int) -> int:
    return int(datetime(2026, 1, 5, hour, 0, tzinfo=ZoneInfo(TIMEZONE)).timestamp())






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
        assemble_semantics(sem.document, drifted, map_dataset=sem.scenario.map_dataset)


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
    ),
)
def test_assembly_rejects_object_drift(mutate, match) -> None:
    """对象表漂移（缺服务/缺角色注解/坏效率/坏商品边）逐项拒绝。"""
    sem = _semantics()
    with pytest.raises(IntegrityError, match=match):
        assemble_semantics(sem.document, _objects_drifted(sem, mutate), map_dataset=sem.scenario.map_dataset)


def test_assembly_rejects_duplicate_consume_edges() -> None:
    """同一商品两条消费边即拒绝（热量来源必须唯一）。"""

    sem = _semantics()
    def mutate(data):
        _duplicate_edge(data, _food_product_id(sem), "consume")

    with pytest.raises(IntegrityError, match="multiple consume edges"):
        assemble_semantics(sem.document, _objects_drifted(sem, mutate), map_dataset=sem.scenario.map_dataset)


def test_product_definition_rejects_purchase_price_edges() -> None:
    """商品定义不得重新携带门店购买价格，旧购买边直接拒绝。"""
    sem = _semantics()
    data = sem.catalog.objects[_food_product_id(sem)].model_dump(mode="json")
    data["edges"].append({"type": "transfer", "on": "purchase",
                          "from": "agent:1", "to": "merchant:g", "amount_fen": 100, "resource": "money_fen"})
    with pytest.raises(ValueError, match="purchase price edges"):
        ExchangeObject.model_validate(data)


def test_assembly_rejects_unknown_or_mistyped_product_references() -> None:
    """商品引用：目录外对象与非商品对象都拒绝。"""
    sem = _semantics()
    base = sem.document.products[0]
    unknown = base.model_copy(update={"product_id": "missing-object"})
    with pytest.raises(IntegrityError, match="not in the catalog"):
        assemble_semantics(sem.document.model_copy(update={"products": (unknown,)}),
                           load_catalog((ENVIRONMENT_ROOT / "catalog")), map_dataset=sem.scenario.map_dataset)
    mistyped = base.model_copy(update={"product_id": "travel-service"})
    with pytest.raises(IntegrityError, match="not a product"):
        assemble_semantics(sem.document.model_copy(update={"products": (mistyped,)}),
                           load_catalog((ENVIRONMENT_ROOT / "catalog")), map_dataset=sem.scenario.map_dataset)


def test_product_reference_rejects_storefront_stock_fields() -> None:
    """商品引用只负责商品身份，库存只能作为供给事实声明。"""
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        ProductReference.model_validate({"product_id": "test-product", "stock": 5})
    sem = _semantics()
    product = sem.scenario.products[0]
    assert not {"stock", "initial_stock", "price_fen", "price_version", "merchant_account_id"} & type(product).model_fields.keys()


def test_contract_payment_rejects_a_missing_template() -> None:
    """合同付款必须能取得对应的冻结模板对象。"""
    from worldmock.life.models import ContractKind
    sem = _semantics()
    shell = RuntimeWorkTask.model_construct(
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
    with pytest.raises(IntegrityError, match="exactly one employer account"):
        assemble_semantics(sem.document.model_copy(update={"accounts": trimmed, "tasks": ()}),
                           load_catalog((ENVIRONMENT_ROOT / "catalog")), map_dataset=sem.scenario.map_dataset)


def test_assembly_with_agentless_document_fails_closed() -> None:
    """@brief 清除人物后仍引用该人物的邮件必须在运行场景组装时拒绝。"""
    sem = _semantics()
    message = MessageConfig(message_id="test-mail", thread_id="test-thread", recipient_agent_id=1,
                            sent_at=sem.document.start_at, subject="Test subject", body="Test body")
    document = sem.document.model_copy(update={
        "agents": (), "messages": (message,), "tasks": (), "payrolls": (), "bills": ()})
    with pytest.raises(IntegrityError, match="unknown agent"):
        assemble_semantics(document, load_catalog(ENVIRONMENT_ROOT / "catalog"),
                           map_dataset=sem.scenario.map_dataset)
