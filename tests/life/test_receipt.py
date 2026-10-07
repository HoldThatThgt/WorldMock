"""@brief 收货归真实收货人、固定效果与无持有状态的纯合同。"""

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import ENVIRONMENT, at_time, registered_world
from tests.life.test_commerce import order_request
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.basket import purchase_order
from worldmock.life.body import last_flows
from worldmock.life.commerce import deliver_order
from worldmock.life.models import ActivityState, AgentState, DeliveryState, PurchaseItem, WorldState
from worldmock.life.receipt import settle_receipt
from worldmock.scheduling.effects import process_scheduled_events
from worldmock.scheduling.events import ScheduledEvent


def third_party_order(location="home", energy=1800, asleep=False):
    semantics, state = registered_world(scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    other = state.agents[2]
    state.agents[2] = other.model_copy(update={"location_id": "home",
                                              "body": other.body.model_copy(update={"energy_kcal": energy})})
    outcome = purchase_order(state, semantics, 1, order_request(recipient="agent:2"))
    ready = at_time(outcome.state, outcome.orders[0].expected_at)
    ready.agents[2] = ready.agents[2].model_copy(update={"location_id": location})
    if asleep:
        now = ready.current_time
        ready.activities["asleep"] = ActivityState(activity_id="asleep", agent_id=2, location_id=location,
            kind="sleep", status="active", started_at=now, planned_end_at=now + 600, last_settled_at=now,
            elapsed_seconds=0, planned_duration_seconds=600, origin_action_id="a",
            origin_decision_id="d", origin_request_event_id="r")
    return semantics, ready, outcome.orders[0]


@pytest.mark.parametrize("energy,applied", [(1800, 540), (1860, 540), (2000, 400), (2400, 0)])
def test_third_party_food_receipt_owns_exact_effect_and_drains_flow(energy, applied):
    semantics, ready, order = third_party_order(energy=energy)
    payer_before = ready.agents[1].body.model_dump()
    received, drafts, wakes = process_scheduled_events(ready, ready.current_time, semantics)
    arrival, receipt = drafts
    assert arrival.agent_id == 1 and receipt.agent_id == 2 and receipt.visibility == "agent"
    assert receipt.source_kind == "environment" and receipt.action_id is receipt.decision_id is None
    assert receipt.caused_by == [arrival.event_id]
    assert receipt.data["provided"] == 600 and receipt.data["efficiency"] == .9
    assert receipt.data["applied"] == applied and received.agents[2].body.energy_kcal == energy + applied
    assert received.agents[1].body.model_dump() == payer_before and received.accounts == ready.accounts
    flows = receipt.data["resource_flows"]
    assert flows == ([{"resource": "energy_kcal", "owner": "agent:2", "direction": "source",
                      "amount": applied, "cause": "food:intake"}] if applied else [])
    assert wakes == {1: {"delivery_arrival"}, 2: {"delivery_arrival"}}
    assert last_flows() == ()
    repeated = deliver_order(received, semantics, order.order_id)
    assert repeated.state is received and repeated.order is received.deliveries[order.order_id]
    assert repeated.receipts == () and last_flows() == ()


def test_delivery_receives_in_park_while_original_destination_remains_home():
    semantics, ready, order = third_party_order(location="park")
    received, drafts, _ = process_scheduled_events(ready, ready.current_time, semantics)
    receipt = drafts[1]
    assert received.deliveries[order.order_id].received_at == ready.current_time
    assert receipt.data["destination_id"] == "home" and receipt.data["recipient_location_id"] == "park"
    assert receipt.data["recipient_asleep"] is False


def test_sleeping_recipient_receives_now_without_waking_but_payer_still_wakes():
    semantics, ready, order = third_party_order(location="park", asleep=True)
    received, drafts, wakes = process_scheduled_events(ready, ready.current_time, semantics)
    assert received.deliveries[order.order_id].status == "delivered"
    assert received.deliveries[order.order_id].received_at == ready.current_time
    assert drafts[1].data["recipient_asleep"] is True
    assert received.activities["asleep"] == ready.activities["asleep"]
    assert wakes == {1: {"delivery_arrival"}} and received.agents[2].body.energy_kcal == 2340


def test_contact_receipt_has_only_delivered_order_without_body_or_goods_event():
    semantics, state = registered_world()
    contact = semantics.scenario.agents[0].contacts[0]
    outcome = purchase_order(state, semantics, 1,
                             order_request(destination="office", recipient=contact.contact_id))
    ready = at_time(outcome.state, outcome.orders[0].expected_at)
    received, drafts, wakes = process_scheduled_events(ready, ready.current_time, semantics)
    assert len(drafts) == 1 and drafts[0].data.keys() == {"kind", "scheduled_id", "orders"}
    order = received.deliveries[outcome.orders[0].order_id]
    assert order.status == "delivered" and order.received_at == ready.current_time
    assert received.agents == ready.agents and wakes == {1: {"delivery_arrival"}}
    assert "contact_holdings" not in received.model_dump() and last_flows() == ()


@pytest.mark.parametrize("need,applied", [(.04, .04), (.5, .1), (0, 0)])
def test_leisure_receipt_reduces_need_once_with_exact_resource_cause(need, applied):
    semantics, state = registered_world("store")
    state.agents[1].body.entertainment_need = need
    outcome = purchase_order(state, semantics, 1, order_request("store", "store", None, "pickup",
                                                              ("playing-cards",), payment=None))
    receipt = outcome.receipts[0]
    assert receipt.data["provided"] == .1 and receipt.data["applied"] == applied
    assert receipt.data["efficiency"] is None
    assert outcome.state.agents[1].body.entertainment_need == pytest.approx(need - applied)
    assert receipt.data["resource_flows"] == ([{"resource": "entertainment_need", "owner": "agent:1",
        "direction": "drain", "amount": applied, "cause": "goods:receipt"}] if applied else [])
    assert last_flows() == ()


@pytest.mark.parametrize("mood,applied", [(0, .05), (.99, .01), (1, 0)])
def test_daily_receipt_is_one_mood_pulse_without_resource_flow(mood, applied):
    semantics, state = registered_world("store")
    state.agents[1].body.mood_valence = mood
    outcome = purchase_order(state, semantics, 1, order_request("store", "store", None, "pickup",
                                                              ("tissue",), payment=None))
    receipt = outcome.receipts[0]
    assert receipt.data["provided"] == .05 and receipt.data["applied"] == pytest.approx(applied)
    assert receipt.data["efficiency"] is None and receipt.data["resource_flows"] == []
    assert outcome.state.agents[1].body.mood_valence == pytest.approx(mood + applied)
    assert last_flows() == ()


def test_orders_and_world_reject_inventory_and_retired_order_fields():
    semantics, state = registered_world("store")
    outcome = purchase_order(state, semantics, 1, order_request("store", "store", None, "pickup",
                                                              ("rice-ball",), payment=None))
    assert "inventory" not in AgentState.model_fields and "contact_holdings" not in WorldState.model_fields
    assert "food_id" not in DeliveryState.model_fields
    order = outcome.orders[0].model_dump(mode="json")
    for field, value in (("food_id", "rice-ball"), ("status", "ready"), ("status", "cancelled")):
        with pytest.raises(ValidationError):
            DeliveryState.model_validate({**order, field: value})
    for quantity in (0, 2, True, 1.0):
        with pytest.raises(ValidationError):
            PurchaseItem(store_id="store", goods_id="rice-ball", quantity=quantity, recipient_id="agent:1")
    with pytest.raises(ValidationError):
        AgentState.model_validate({**outcome.state.agents[1].model_dump(), "inventory": {}})
    with pytest.raises(ValidationError):
        WorldState.model_validate({**outcome.state.model_dump(), "contact_holdings": {}})


def test_receipt_rejects_missing_and_not_due_order_without_any_effect():
    semantics, state = registered_world()
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_FOUND"):
        settle_receipt(state, semantics, "missing")
    outcome = purchase_order(state, semantics, 1, order_request())
    before = outcome.state.model_dump_json()
    with pytest.raises(BusinessRuleError, match="DELIVERY_NOT_DUE"):
        settle_receipt(outcome.state, semantics, outcome.orders[0].order_id)
    assert outcome.state.model_dump_json() == before and last_flows() == ()


def test_delivery_event_requires_unique_nonempty_orders():
    semantics, state = registered_world()
    for identities in ([], ["same", "same"]):
        event = ScheduledEvent(scheduled_id="invalid", kind="delivery_arrival", due_at=state.current_time,
                               sequence=1, agent_id=1, data={"order_ids": identities})
        state.pending_events = [event]
        with pytest.raises(IntegrityError, match="unique nonempty"):
            process_scheduled_events(state, state.current_time, semantics)


@pytest.mark.parametrize("goods,category,provided", [("rice-ball", "meal", 600), ("chips", "snack", 200),
                                                      ("lemon-tea", "drink", 100)])
def test_every_food_category_uses_fixed_calories_with_actual_efficiency(goods, category, provided):
    semantics, state = registered_world("store")
    state.agents[1].body.energy_kcal = 1000
    outcome = purchase_order(state, semantics, 1,
                             order_request("store", "store", None, "pickup", (goods,), payment=None))
    receipt = outcome.receipts[0]
    assert receipt.data["category"] == category and receipt.data["quantity"] == 1
    assert receipt.data["provided"] == provided and receipt.data["applied"] == .9 * provided
    assert outcome.state.agents[1].body.energy_kcal == 1000 + .9 * provided
    assert last_flows() == ()


def test_pending_and_delivered_order_receipt_timestamps_are_strict():
    semantics, state = registered_world()
    order = purchase_order(state, semantics, 1, order_request()).orders[0].model_dump(mode="json")
    with pytest.raises(ValidationError, match="status and receipt timestamp disagree"):
        DeliveryState.model_validate({**order, "received_at": order["created_at"]})
    with pytest.raises(ValidationError, match="status and receipt timestamp disagree"):
        DeliveryState.model_validate({**order, "status": "delivered"})
    with pytest.raises(ValidationError, match="precedes order creation"):
        DeliveryState.model_validate({**order, "status": "delivered", "received_at": order["created_at"] - 1})


def test_travelling_recipient_receives_without_claiming_presence_at_a_location():
    semantics, ready, order = third_party_order()
    now = ready.current_time
    ready.activities["recipient-trip"] = ActivityState(activity_id="recipient-trip", agent_id=2, location_id="home",
        kind="travel", status="active", started_at=now, planned_end_at=now + 60, last_settled_at=now,
        elapsed_seconds=0, planned_duration_seconds=60, origin_action_id="a",
        origin_decision_id="d", origin_request_event_id="r")
    received, drafts, wakes = process_scheduled_events(ready, now, semantics)
    assert received.deliveries[order.order_id].received_at == now
    assert drafts[1].data["recipient_location_id"] is None and drafts[1].data["destination_id"] == "home"
    assert received.agents[2].body.energy_kcal == 2340 and wakes[2] == {"delivery_arrival"}
