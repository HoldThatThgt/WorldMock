"""@brief 真实目录商品、付款、收货与检查点恢复的纯行为合同。"""

import pytest

from tests.life.purchase_worlds import ENVIRONMENT, at_time, registered_world
from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.exchange.operators import delivery_seconds
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.basket import purchase_order
from worldmock.life.body import last_flows
from worldmock.life.models import ActivityState, PurchaseItem, PurchaseOrderRequest, WorldState
from worldmock.scheduling.effects import apply_scheduled_effect, process_scheduled_events


def order_request(store="noodle-1", destination="home", app="meituan", fulfillment="delivery",
                  goods=("beef-noodles",), recipient="agent:1", payment="alipay"):
    return PurchaseOrderRequest(items=tuple(PurchaseItem(store_id=store, goods_id=item, quantity=1,
                                recipient_id=recipient) for item in goods), fulfillment=fulfillment,
                                destination_id=destination, app_id=app, payment_app_id=payment)


def test_store_purchase_settles_energy_and_receipt_once_in_the_payment_result():
    semantics, state = registered_world("store")
    before = state.model_dump_json()
    outcome = purchase_order(state, semantics, 1, order_request("store", "store", None, "pickup",
                                                              ("rice-ball",), payment=None))
    order = outcome.orders[0]
    assert order.app_id is order.payment_app_id is None
    assert order.status == "delivered" and order.received_at == order.created_at == state.current_time
    assert order.distance_meters == order.prep_seconds == order.delivery_fee_fen == 0
    assert outcome.scheduled == () and len(outcome.receipts) == 1
    assert outcome.state.agents[1].body.energy_kcal == 2400
    assert outcome.receipts[0].data["provided"] == 600 and outcome.receipts[0].data["applied"] == 400
    assert outcome.state.accounts["agent:1"] == state.accounts["agent:1"] - 1000
    assert "inventory" not in outcome.state.agents[1].model_dump()
    assert last_flows() == () and state.model_dump_json() == before


@pytest.mark.parametrize("destination", ["cafe", "home", "noodle-1", "noodle-2", "office", "park", "store"])
def test_delivery_receives_at_due_time_even_after_recipient_moves(destination):
    semantics, state = registered_world(destination)
    before = state.model_dump_json()
    refusals = []
    try:
        outcome = purchase_order(state, semantics, 1, order_request(destination=destination))
    except BusinessRuleError as error:
        refusals.append(str(error))
    assert refusals == [], "Delivery must accept every registered destination."
    order, event = outcome.orders[0], outcome.scheduled[0]
    distance = semantics.catalog.shortest_m("noodle-1", destination)
    assert order.expected_at == state.current_time + delivery_seconds(distance)
    assert order.total_price_fen == 3300 and order.status == "pending" and order.received_at is None
    assert order.payment_app_id == "alipay" and order.category == "meal"
    assert outcome.receipts == () and outcome.state.agents[1].body == state.agents[1].body
    restored = WorldState.model_validate_json(outcome.state.model_dump_json())
    restored.agents[1] = restored.agents[1].model_copy(update={"location_id": "office"})
    ready = at_time(restored, order.expected_at)
    first = apply_scheduled_effect(ready, event, semantics)
    second = apply_scheduled_effect(WorldState.model_validate_json(first.state.model_dump_json()), event, semantics)
    assert first.state.deliveries[order.order_id].received_at == order.expected_at
    assert first.state.agents[1].body.energy_kcal == 2400
    assert first.receipts[0].data["destination_id"] == destination
    assert first.receipts[0].data["recipient_location_id"] == "office"
    assert second.state is not ready and second.state == first.state and second.data == {}
    assert second.receipts == () and second.state.accounts == outcome.state.accounts
    assert last_flows() == () and state.model_dump_json() == before


def test_two_goods_rows_pay_one_delivery_fee_and_one_transfer(monkeypatch):
    import worldmock.life.basket as basket

    semantics, state = registered_world()
    calls = []
    original = basket.settle_money_transfer

    def record(*args):
        calls.append(args[1:])
        return original(*args)

    monkeypatch.setattr(basket, "settle_money_transfer", record)
    request = order_request(goods=("beef-noodles", "chips"))
    outcome = purchase_order(state, semantics, 1, request)
    assert outcome.total_price_fen == 4100
    assert [row.delivery_fee_fen for row in outcome.orders] == [500, 0]
    assert sum(row.total_price_fen for row in outcome.orders) == outcome.total_price_fen
    assert len(calls) == len(outcome.scheduled) == 1
    assert calls[0][:3] == ("agent:1", "merchant:noodle-1", 4100)
    first = apply_scheduled_effect(at_time(outcome.state, outcome.orders[0].expected_at), outcome.scheduled[0], semantics)
    assert len(first.receipts) == 2 and first.data.keys() == {"orders"}
    assert first.state.accounts == outcome.state.accounts and last_flows() == ()


@pytest.mark.parametrize("purchase_request,code", [
    (order_request(app="eleme"), "APP_NOT_BOUND"),
    (order_request(store="noodle-2"), "APP_NOT_BOUND"),
    (order_request(destination="unknown"), "DESTINATION_NOT_FOUND"),
    (order_request(goods=("beef-noodles", "beef-noodles")), "ORDER_DUPLICATE_ITEM"),
    (order_request(goods=("rice-ball",)), "SUPPLY_NOT_FOUND"),
    (order_request(goods=("beef-noodles", "chips", "lemon-tea")), "ORDER_ITEM_COUNT_OUT_OF_RANGE"),
    (order_request(payment=None), "PAYMENT_APP_REQUIRED"),
    (order_request(app="eleme", payment="wechat"), "PAYMENT_APP_NOT_BOUND"),
    (order_request(app="douyin"), "APP_NOT_OFFERED"),
])
def test_purchase_rejection_is_atomic_with_exact_code(purchase_request, code):
    semantics, state = registered_world()
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match=code):
        purchase_order(state, semantics, 1, purchase_request)
    assert state.model_dump_json() == before


def test_insufficient_funds_rejects_entire_order_before_receipt():
    semantics, state = registered_world(balance=3300)
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match="INSUFFICIENT_FUNDS"):
        purchase_order(state, semantics, 1, order_request(goods=("beef-noodles", "chips")))
    assert state.model_dump_json() == before and last_flows() == ()


def test_self_delivery_during_travel_is_refused_before_payment():
    semantics, state = registered_world()
    now = state.current_time
    activity = ActivityState(activity_id="travelling", agent_id=1, location_id="home", kind="travel",
        status="active", started_at=now, planned_end_at=now + 600, last_settled_at=now, elapsed_seconds=0,
        planned_duration_seconds=600, destination_id="office", travel_mode="walking",
        origin_action_id="a", origin_decision_id="d", origin_request_event_id="r")
    state.activities[activity.activity_id] = activity
    before = state.model_dump_json()
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET") as failure:
        purchase_order(state, semantics, 1, order_request())
    assert failure.value.unmet == ["delivery_destination"]
    assert state.model_dump_json() == before


def test_pending_order_checkpoint_restore_receives_once_and_keeps_payment_once(tmp_path):
    semantics, state = registered_world()
    store = FootprintStore.create(tmp_path / "run", {"run_id": state.run_id, "agent_ids": [1]},
                                  state.model_dump(mode="json"))
    outcome = purchase_order(state, semantics, 1, order_request())
    paid = outcome.state.model_copy(update={"version": state.version + 1})
    transfer = EventDraft(event_type="action_result", occurred_at=state.current_time, agent_id=1,
                         action_id="purchase-request", data={"orders": [o.model_dump(mode="json") for o in outcome.orders],
                         "resource_changes": {"account:agent:1": -3300, "account:merchant:noodle-1": 3300}})
    store.commit(paid.model_dump(mode="json"), [transfer])
    snapshot = store.assets.put_json(paid.model_dump(mode="json"), {"kind": "test_snapshot"})
    checkpoint = store.checkpoint(paid.model_dump(mode="json"), {"life": snapshot, "agent:1": snapshot})
    store.close()
    reopened = FootprintStore(tmp_path / "run")
    restored = WorldState.model_validate(reopened.get_checkpoint(checkpoint.checkpoint_id).state)
    ready = at_time(restored, outcome.orders[0].expected_at)
    received, drafts, wakes = process_scheduled_events(ready, ready.current_time, semantics)
    received.version = restored.version + 1
    events = reopened.commit(received.model_dump(mode="json"), drafts)
    arrival, receipt = events
    assert receipt.caused_by == [arrival.event_id] and receipt.agent_id == 1
    assert receipt.data["resource_flows"][0]["cause"] == "food:intake"
    assert received.accounts == paid.accounts and wakes == {1: {"delivery_arrival"}}
    repeated = received.model_copy(update={"pending_events": list(outcome.scheduled)}, deep=True)
    again, no_events, no_wakes = process_scheduled_events(repeated, ready.current_time, semantics)
    assert again == received and no_events == [] and no_wakes == {}
    assert reopened.last_event_seq == 4 and last_flows() == ()
    reopened.close()
