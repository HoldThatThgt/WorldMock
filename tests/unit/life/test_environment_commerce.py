"""商品报价、购买时间与付款收货的当前行为验证。"""

from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from worldmock.catalog.models import Goods, Location, OpeningHours
from worldmock.environment import load_environment
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.input_models import ModelMetadata
from worldmock.life.action_contract import binding_action
from worldmock.life.admission import Blocked, Call, Context, group_refusal
from worldmock.life.basket import activity_orders, quoted_basket
from worldmock.life.body_config import assemble_body_config
from worldmock.life.commerce import deliver_order
from worldmock.life.guidance import refusal_for_code
from worldmock.life.models import ActivityStatus, AgentState, BodyState, WorldState, WORLD_SCHEMA_VERSION
from worldmock.life.parcel import collect_parcels
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.receipt import settle_receipt
from worldmock.life.storefront_runtime import StorefrontRuntime


@pytest.fixture
def environment():
    path = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment"
    metadata = lambda config: ModelMetadata(requested_model=config.requested_model,
        metadata_url=config.metadata_url, context_window=262144, max_output_tokens=8192)
    return load_environment(path, metadata_loader=metadata)


def world(inputs, location="home", balance=200000, random_state="commerce-tests"):
    start = int(inputs.config.start_time.timestamp())
    body = BodyState(energy_kcal=1200.0, sleep_pressure=0.4, sleep_deficit_hours=0.0, sleep_need_hours=7.5,
                     chronotype_midsleep=4.0, relaxation_need=0.6, social_need=0.2,
                     social_need_growth_per_hour=0.06)
    agent = AgentState(agent_id=1, name="林舟", location_id=location, balance_account_id="agent:1", body=body,
                       body_settled_at=start, next_decision_at=start)
    return WorldState(schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=start,
        end_time=start + 86400 * 10, run_id="commerce-test", timezone=inputs.config.timezone,
        persona_sha256=inputs.persona_sha256, config_sha256=inputs.config_sha256,
        agents={1: agent}, accounts={"agent:1": balance, "external:scenario": -balance},
        random_seed=7, random_state=random_state)


def runtime(inputs):
    event = SimpleNamespace(action_id="buy", event_id="accepted-result", data={"status": "in_progress"})
    store = SimpleNamespace(read_agent_events=lambda event_type, agent_id: [event])
    context = SimpleNamespace(inputs=inputs, catalog=inputs.catalog, persona=inputs.persona,
                              config=inputs.config, body=assemble_body_config(inputs.persona), store=store, callback_causes={})
    return StorefrontRuntime(context)


def pending():
    return SimpleNamespace(agent_id=1, action_id="buy", decision_id="decision", requested_event_id="request")


def finish(state, activity):
    current = activity.planned_end_at
    activity = activity.model_copy(update={"elapsed_seconds": activity.planned_duration_seconds,
        "last_settled_at": current, "status": ActivityStatus.COMPLETED, "ended_at": current,
        "boundary_type": "planned_end"}, deep=True)
    agent = state.agents[1].model_copy(update={"body_settled_at": current}, deep=True)
    return state.model_copy(update={"current_time": current, "agents": {1: agent},
                                  "activities": {activity.activity_id: activity}}, deep=True), activity


def operate_payments(commerce, state, activity):
    drafts = []
    for moment in commerce.payment_boundaries(state, activity):
        state = state.model_copy(update={"current_time": moment}, deep=True)
        state, events = commerce.advance_payment(state, activity)
        drafts.extend(events)
    return state, drafts


def test_local_goods_choose_present_store_and_no_shipping(environment):
    state = world(environment, "store")
    offer = quote_goods(environment, state, 1, "lemon-tea")
    assert (offer.store_id, offer.app_id, offer.destination_id, offer.fulfillment) == ("store", None, "store", "pickup")
    assert offer.delivery_fee_fen == 0


def test_delivery_chooses_lowest_price_then_arrival_then_identity(environment):
    state = world(environment)
    offer = quote_goods(environment, state, 1, "lemon-tea")
    stores = [key for key, goods in environment.catalog.goods_by_store.items()
              if "lemon-tea" in goods and environment.catalog.apps_by_store[key]]
    expected = min(stores, key=lambda key: (environment.catalog.shortest_m(key, "home"), key))
    assert offer.store_id == expected
    assert offer.destination_id == "home" and offer.delivery_fee_fen == 500
    assert offer.expected_at == state.current_time + 600 + (offer.distance_meters + 4) // 5


def test_closed_present_store_does_not_change_to_delivery(environment):
    catalog = environment.catalog
    closed = catalog.locations["store"].model_copy(update={"opens": OpeningHours(weekly={})})
    inputs = replace(environment, catalog=replace(catalog, locations=MappingProxyType({**catalog.locations,
                                                                                   "store": closed})))
    with pytest.raises(BusinessRuleError, match="LOCATION_CLOSED"):
        quote_goods(inputs, world(inputs, "store"), 1, "lemon-tea")


def test_no_remote_channel_requires_real_store(environment):
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET"):
        quote_goods(environment, world(environment), 1, "tissue")


def test_preview_courier_is_stable_local_midnight_and_has_no_route(environment):
    state = world(environment)
    offer = quote_goods(environment, state, 1, "online-notebook")
    assert offer == quote_goods(environment, state, 1, "online-notebook")
    arrival = datetime.fromtimestamp(offer.expected_at, ZoneInfo(state.timezone))
    created = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    assert arrival.time() == time.min
    assert 2 <= (arrival.date() - created.date()).days <= 5
    assert offer.store_id is None and offer.trip_id is None
    assert offer.path == ("parcel-station",) and offer.distance_meters == offer.prep_seconds == 0
    assert offer.delivery_fee_fen in {0, 500}


def test_request_freezes_preview_does_not_pay_or_generate_receipt(environment):
    state = world(environment)
    preview = quote_goods(environment, state, 1, "online-notebook")
    changed, activity = runtime(environment).prepare_purchase(state, pending(), ("online-notebook",))
    order = activity_orders(changed, activity)[0]
    assert changed.accounts == state.accounts and state.deliveries == {}
    assert order.expected_at == preview.expected_at and order.app_id == preview.app_id
    assert order.status == "pending" and order.paid_at is None and not changed.pending_events
    assert 60 <= activity.planned_duration_seconds <= 300 and activity.resource_claims == {"second"}
    assert activity.name.startswith("购买平台笔记本")
    assert "recipient_id" not in order.model_dump() and "seller_account_id" not in order.model_dump()
    restored = WorldState.model_validate(changed.model_dump(mode="json"))
    assert restored.activities[activity.activity_id].planned_duration_seconds == activity.planned_duration_seconds


@pytest.mark.parametrize("goods,label,total", [
    (("lemon-tea", "lemon-tea"), "购买柠檬茶×2（", 2400),
    (("playing-cards", "lemon-tea", "rice-ball", "lemon-tea", "lemon-tea"), "购买柠檬茶×3、扑克牌、饭团（", 6100),
])
def test_repeated_goods_are_separate_portions_of_one_purchase_activity(environment, goods, label, total):
    """同一商品写几次就是几份、份数不设上限：每份一条履约记录，记录按配送组再按商品身份编号，活动名把同一商品合写。"""
    state = world(environment, "store")
    assert quoted_basket(environment, state, 1, goods)[1] == total
    changed, activity = runtime(environment).prepare_purchase(state, pending(), goods)
    orders = activity_orders(changed, activity)
    assert [order.order_id for order in orders] == [f"order-buy-{index:04d}" for index in range(1, len(goods) + 1)]
    assert [order.goods_id for order in orders] == sorted(goods)
    assert all(order.quantity == 1 and order.status == "pending" for order in orders)
    assert activity.activity_id == "activity-buy-purchase" and activity.name.startswith(label)
    assert list(changed.activities) == [activity.activity_id] and changed.accounts == state.accounts
    assert not state.deliveries and not state.activities


def _basket_refusal(environment, state, goods):
    """整组核验里只有这一条购买调用时的结论（槽位、对象条件与余额同一入口）。"""
    names = {key: row.display_name for key, row in environment.catalog.locations.items()}
    rows = tuple({"object": environment.catalog.goods[key].name, "kind": "supply", "object_id": key,
                  "selected_action": binding_action("purchase")} for key in goods)
    return group_refusal(Context(state, 1, names, {}, environment), [Call("start_side_activity", "purchase", rows)])


def test_basket_precheck_merges_delivery_fee_once(environment):
    """报价合并运费但不核对余额；余额由整组核验按合并后的总价核对一次。"""
    goods = ("lemon-tea", "playing-cards")
    groups, total = quoted_basket(environment, world(environment, "cafe"), 1, goods)
    assert len(groups) == 1 and total == 2700
    assert quoted_basket(environment, world(environment, "cafe", balance=2699), 1, goods)[1] == 2700
    assert _basket_refusal(environment, world(environment, "cafe", balance=2700), goods) is None
    assert _basket_refusal(environment, world(environment, "cafe", balance=2699), goods) == Blocked(
        refusal_for_code("INSUFFICIENT_FUNDS"), ("start_side_activity",))
    remote = ("lemon-tea", "online-notebook")
    fee_groups, fee_total = quoted_basket(environment, world(environment), 1, remote)
    assert fee_total == 2400 + 500 * sum(any(row.delivery_fee_fen for row in rows) for rows in fee_groups.values())
    assert _basket_refusal(environment, world(environment, balance=fee_total), remote) is None
    assert _basket_refusal(environment, world(environment, balance=fee_total - 1), remote) == Blocked(
        refusal_for_code("INSUFFICIENT_FUNDS"), ("start_side_activity",))


def test_normal_completion_pays_all_groups_and_receives_local_goods_once(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store"), pending(),
                                               ("lemon-tea", "playing-cards"))
    state, touches = operate_payments(commerce, state, activity)
    state, activity = finish(state, activity)
    changed, events = commerce.complete_purchase(state, activity)
    assert changed.accounts == {"agent:1": 197300, "external:scenario": -197300}
    payment = [event for event in events if event.data["kind"] == "purchase_payment"]
    receipts = [event for event in events if event.data["kind"] == "goods_received"]
    assert len(payment) == 1 and len(receipts) == 2
    assert payment[0].caused_by == ["accepted-result"]
    assert payment[0].resource_changes == {"account:agent:1": -2700, "account:external:scenario": 2700}
    assert all(receipt.caused_by == [payment[0].event_id] for receipt in receipts)
    assert all(order.paid_at == state.current_time and order.received_at == state.current_time
               for order in changed.deliveries.values())
    assert changed.agents[1].body.relaxation_need == pytest.approx(0.4)
    replayed, duplicate_events = commerce.complete_purchase(changed, activity)
    assert replayed.accounts == changed.accounts and duplicate_events == []
    for order in replayed.deliveries.values():
        assert settle_receipt(replayed, commerce.context, order.order_id).receipts == ()


def test_completion_insufficient_funds_cancels_entire_purchase_without_charge(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store"), pending(),
                                               ("lemon-tea", "online-notebook"))
    state, _ = operate_payments(commerce, state, activity)
    state, activity = finish(state, activity)
    state = state.model_copy(update={"accounts": {"agent:1": 0, "external:scenario": 0}}, deep=True)
    changed, events = commerce.complete_purchase(state, activity)
    assert changed.accounts == state.accounts and events == []
    assert all(order.status == "cancelled" and order.paid_at is None for order in changed.deliveries.values())
    assert changed.activities[activity.activity_id].status == ActivityStatus.FAILED
    assert changed.agents[1].body == state.agents[1].body


def test_cancel_before_completion_never_pays_schedules_or_receives(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store"), pending(), ("lemon-tea",))
    changed = commerce.cancel_purchase(state, activity)
    assert changed.accounts == state.accounts and not changed.pending_events
    assert next(iter(changed.deliveries.values())).status == "cancelled"
    assert commerce.complete_purchase(changed, activity)[1] == []


def test_courier_arrival_only_ready_then_collect_all_and_no_double_effect(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("online-notebook",))
    state, _ = operate_payments(commerce, state, activity)
    state, activity = finish(state, activity)
    state, _ = commerce.complete_purchase(state, activity)
    order = activity_orders(state, activity)[0]
    due = state.model_copy(update={"current_time": order.expected_at}, deep=True)
    arrived = deliver_order(due, commerce.context, order.order_id)
    assert arrived.order.status == "ready_for_pickup" and arrived.receipts == ()
    assert arrived.state.agents[1].body == state.agents[1].body
    agent = arrived.state.agents[1].model_copy(update={"location_id": "parcel-station"}, deep=True)
    at_station = arrived.state.model_copy(update={"agents": {1: agent}}, deep=True)
    outcome = collect_parcels(at_station, commerce.context, 1, "parcel-station")
    assert outcome.orders[0].status == "delivered" and len(outcome.receipts) == 1
    assert outcome.state.agents[1].body.relaxation_need == pytest.approx(0.5)
    with pytest.raises(PreconditionError, match="PARCEL_NOT_READY"):
        collect_parcels(outcome.state, commerce.context, 1, "parcel-station")


def test_unpaid_order_cannot_be_delivered_or_received(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("online-notebook",))
    order = activity_orders(state, activity)[0]
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_PAID"):
        deliver_order(state, commerce.context, order.order_id)
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_PAID"):
        settle_receipt(state, commerce.context, order.order_id)


@pytest.mark.parametrize("random_state", [f"payment-{index}" for index in range(12)])
def test_payment_segments_and_notifications_match_actual_selected_app(environment, random_state):
    commerce = runtime(environment)
    initial = world(environment, "store", random_state=random_state)
    state, activity = commerce.prepare_purchase(initial, pending(), ("lemon-tea", "online-notebook"))
    bounds = commerce.payment_boundaries(state, activity)
    touches = []
    for moment in bounds:
        state = state.model_copy(update={"current_time": moment}, deep=True)
        state, events = commerce.advance_payment(state, activity)
        touches.extend(events)
        assert state.accounts == initial.accounts
        assert all(order.paid_at is None for order in state.deliveries.values())
    state, activity = finish(state, activity)
    changed, events = commerce.complete_purchase(state, activity)
    groups = {order.group_key: order for order in changed.deliveries.values()}
    paid = [event for event in events if event.data["kind"] == "purchase_payment"]
    notices = [event for event in events if event.data["kind"] == "payment_notification"]
    assert len(paid) == len(groups)
    external = [order for order in groups.values() if order.payment_app_id is not None]
    assert len(touches) == len(notices) == len(external)
    assert [event.occurred_at for event in touches] == sorted(event.occurred_at for event in touches)
    for order in external:
        assert 10 <= order.payment_duration_seconds <= 20
        assert order.payment_ended_at - order.payment_started_at == order.payment_duration_seconds
        assert activity.started_at <= order.payment_started_at < order.payment_ended_at <= activity.planned_end_at
    for notice in notices:
        source = next(event for event in paid if event.event_id == notice.caused_by[0])
        assert notice.data["amount_fen"] == source.data["amount_fen"]
        assert notice.data["order_ids"] == source.data["order_ids"]
        assert notice.resource_changes == {}
    assert sum(changed.accounts.values()) == sum(initial.accounts.values())


def test_skipped_payment_boundary_is_rejected_without_backdated_touch(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store", random_state="payment-0"),
                                               pending(), ("lemon-tea", "online-notebook"))
    assert commerce.payment_boundaries(state, activity)
    state, activity = finish(state, activity)
    with pytest.raises(IntegrityError, match="boundary was skipped"):
        commerce.advance_payment(state, activity)
    with pytest.raises(IntegrityError, match="actual payment operation evidence"):
        commerce.complete_purchase(state, activity)
