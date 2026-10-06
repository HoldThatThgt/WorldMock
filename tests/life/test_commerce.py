"""@brief 真实目录食品、账本、订单、到期送达与恢复的纯购买行为。"""

import json

import pytest

from tests.life.purchase_worlds import at_time, registered_world
from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.exchange.constants import DELIVERY_FEE_FEN
from worldmock.exchange.operators import delivery_seconds
from worldmock.life.activities import ActivityOrigin, start_activity
from worldmock.life.basket import purchase_order
from worldmock.life.commerce import deliver_order
from worldmock.life.models import ActivityStartParams, PurchaseItem, PurchaseOrderRequest, WorldState
from worldmock.life.travel import plan_trip
from worldmock.scheduling.effects import apply_scheduled_effect


def order_request(store="noodle-1", destination="home", app="meituan", fulfillment="delivery", foods=("beef-noodles",)):
    return PurchaseOrderRequest(items=tuple(PurchaseItem(store_id=store, food_id=food, quantity=1,
                                recipient_id="agent:1") for food in foods), fulfillment=fulfillment,
                                destination_id=destination, app_id=app)


def test_no_app_store_pickup_is_immediate_local_holding():
    semantics, state = registered_world("noodle-2")
    before_energy = state.agents[1].body.energy_kcal
    outcome = purchase_order(state, semantics, 1, order_request("noodle-2", "noodle-2", None, "pickup"))
    order = outcome.orders[0]
    assert order.app_id is None and order.status == "delivered" and order.path == ("noodle-2",)
    assert order.distance_meters == order.prep_seconds == order.delivery_fee_fen == 0
    assert order.expected_at == order.created_at and outcome.scheduled == ()
    assert outcome.state.agents[1].inventory["noodle-2"]["beef-noodles"] == 1
    assert outcome.state.agents[1].body.energy_kcal == before_energy


@pytest.mark.parametrize("destination", ["cafe", "home", "noodle-1", "noodle-2", "office", "park", "store"])
def test_delivery_reaches_every_selected_place_and_survives_restore_once(destination):
    semantics, state = registered_world(destination)
    before = state.model_dump(mode="json")
    merchant_before = state.accounts["merchant:noodle-1"]
    outcome = purchase_order(state, semantics, 1, order_request(destination=destination))
    order, event = outcome.orders[0], outcome.scheduled[0]
    distance = semantics.catalog.shortest_m("noodle-1", destination)
    assert order.expected_at == state.current_time + delivery_seconds(distance)
    assert order.total_price_fen == semantics.catalog.foods["beef-noodles"].price_fen + DELIVERY_FEE_FEN
    assert order.path == semantics.catalog.graph.path("noodle-1", destination)
    assert outcome.state.accounts["merchant:noodle-1"] == merchant_before + 3300
    assert outcome.state.accounts["transport:service"] == state.accounts["transport:service"]
    restored = WorldState.model_validate_json(outcome.state.model_dump_json())
    agents = {1: restored.agents[1].model_copy(update={"location_id": "office"})}
    ready = at_time(restored.model_copy(update={"agents": agents}), order.expected_at)
    first = apply_scheduled_effect(ready, event, semantics)
    second = apply_scheduled_effect(WorldState.model_validate_json(first.state.model_dump_json()), event, semantics)
    assert first.state.agents[1].inventory[destination]["beef-noodles"] == 1
    assert second.state.agents[1].inventory == first.state.agents[1].inventory
    assert second.data["inventory_changes"] == [] and second.state.accounts == outcome.state.accounts
    assert state.model_dump(mode="json") == before


def test_multiple_food_rows_pay_one_fee_one_transfer_and_one_delivery(monkeypatch):
    import worldmock.life.basket as basket

    semantics, state = registered_world()
    calls = []
    original = basket.settle_money_transfer

    def record(*args):
        calls.append(args[1:])
        return original(*args)

    monkeypatch.setattr(basket, "settle_money_transfer", record)
    request = order_request(foods=("beef-noodles", "chips", "lemon-tea"))
    outcome = purchase_order(state, semantics, 1, request)
    goods = sum(semantics.catalog.foods[item.food_id].price_fen for item in request.items)
    assert outcome.total_price_fen == goods + 500
    assert [row.delivery_fee_fen for row in outcome.orders] == [500, 0, 0]
    assert sum(row.total_price_fen for row in outcome.orders) == outcome.total_price_fen
    assert len(calls) == len(outcome.scheduled) == 1
    assert calls[0][:3] == ("agent:1", "merchant:noodle-1", goods + 500)
    first = apply_scheduled_effect(at_time(outcome.state, outcome.orders[0].expected_at), outcome.scheduled[0], semantics)
    second = apply_scheduled_effect(WorldState.model_validate_json(first.state.model_dump_json()), outcome.scheduled[0], semantics)
    assert second.data["inventory_changes"] == []
    assert second.state.agents[1].inventory["home"] == {item.food_id: state.agents[1].inventory["home"].get(item.food_id, 0) + 1 for item in request.items}


@pytest.mark.parametrize("purchase_request,code", [
    (order_request(app="eleme"), "APP_NOT_BOUND"),
    (order_request(store="noodle-2", app="meituan"), "FULFILLMENT_NOT_SUPPORTED"),
    (order_request(store="noodle-2", destination="noodle-2", app="meituan", fulfillment="pickup"), "SUPPLY_SOURCE_MISMATCH"),
    (order_request(destination="unknown"), "DESTINATION_NOT_FOUND"),
    (order_request(foods=("beef-noodles", "beef-noodles")), "ORDER_DUPLICATE_ITEM"),
    (order_request(foods=("rice-ball",)), "SUPPLY_NOT_FOUND"),
])
def test_purchase_rejection_is_atomic_with_the_exact_code(purchase_request, code):
    semantics, state = registered_world("noodle-2")
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match=code):
        purchase_order(state, semantics, 1, purchase_request)
    assert state.model_dump_json() == before


def test_insufficient_funds_rejects_the_entire_multi_food_order():
    semantics, state = registered_world(balance=3300)
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match="INSUFFICIENT_FUNDS"):
        purchase_order(state, semantics, 1, order_request(foods=("beef-noodles", "chips")))
    assert state.model_dump_json() == before


def test_self_delivery_during_travel_is_refused_before_any_payment():
    semantics, state = registered_world()
    trip = plan_trip(semantics.catalog.graph, "home", "office", "walking")
    _, activity = start_activity(state.agents[1], ActivityStartParams(activity_type="travel", destination_id="office",
        travel_mode="walking", planned_duration_seconds=trip.duration_seconds), state.current_time,
        ActivityOrigin("a", "d", "r"), semantics.body)
    state.activities[activity.activity_id] = activity
    before = state.model_dump_json()
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET") as failure:
        purchase_order(state, semantics, 1, order_request())
    assert failure.value.unmet == ["delivery_destination"]
    assert failure.value.public_reason == "Place the order before departing or after arriving."
    assert state.model_dump_json() == before
