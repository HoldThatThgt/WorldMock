"""@brief 购物支付绑定与收货地拆单使用真实登记目录。"""

import pytest

from tests.life.purchase_worlds import ENVIRONMENT, registered_world
from tests.integration.life.test_commerce import order_request
from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life.basket import purchase_order
from worldmock.life.guidance import rejection_guidance
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest
from worldmock.life.order_routing import order_requests


def test_unbound_app_refuses_with_exact_reason_and_bound_app_next_step():
    semantics, state = registered_world()
    with pytest.raises(BusinessRuleError, match="APP_NOT_BOUND") as failure:
        purchase_order(state, semantics, 1, order_request(app="eleme"))
    assert rejection_guidance(failure.value)["reason"] == "This store cannot be ordered through that app."
    assert rejection_guidance(failure.value)["next_step"] == "Order through 美团."
    assert failure.value.unmet == []


def test_delivery_to_other_person_uses_their_current_receiving_location():
    semantics, state = registered_world(scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    state.agents[2] = state.agents[2].model_copy(update={"location_id": "park"})
    binding = {"store_id": "noodle-1", "goods_id": "beef-noodles", "app_id": "meituan",
               "destination_id": "park",
               "payment_app_id": "alipay", "recipient_id": "agent:2",
               "selected_action": {"fulfillment": "delivery"}}
    requests = order_requests(state, semantics, 1, [binding])
    assert requests[0].destination_id == "park" and requests[0].payment_app_id == "alipay"
    outcome = purchase_order(state, semantics, 1, requests[0])
    assert outcome.orders[0].recipient_id == "agent:2" and outcome.orders[0].destination_id == "park"


def test_order_groups_use_store_destination_and_both_explicit_apps():
    semantics, state = registered_world()
    base = {"goods_id": "chips", "recipient_id": "agent:1", "destination_id": "home",
            "selected_action": {"fulfillment": "delivery"}}
    bindings = [{**base, "store_id": "noodle-1", "app_id": "meituan", "payment_app_id": "alipay"},
                {**base, "store_id": "noodle-1", "app_id": "meituan", "payment_app_id": "wechat"},
                {**base, "store_id": "cafe", "app_id": "eleme", "payment_app_id": "alipay"}]
    requests = order_requests(state, semantics, 1, bindings)
    assert len(requests) == 3
    assert [(r.items[0].store_id, r.app_id, r.payment_app_id) for r in requests] == [
        ("noodle-1", "meituan", "alipay"), ("noodle-1", "meituan", "wechat"), ("cafe", "eleme", "alipay")]


def test_same_goods_can_appear_twice_only_for_different_recipients():
    semantics, state = registered_world(scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    items = tuple(PurchaseItem(store_id="noodle-1", goods_id="beef-noodles", quantity=1,
                               recipient_id=f"agent:{identity}") for identity in (1, 2))
    purchase = PurchaseOrderRequest(items=items, fulfillment="delivery", destination_id="home",
                                    app_id="meituan", payment_app_id="alipay")
    outcome = purchase_order(state, semantics, 1, purchase)
    assert len(outcome.orders) == 2 and outcome.total_price_fen == 6100
    assert [order.delivery_fee_fen for order in outcome.orders] == [500, 0]


def test_pickup_for_other_person_requires_the_same_actual_store():
    semantics, state = registered_world("store", scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    before = state.model_dump_json()
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET") as failure:
        purchase_order(state, semantics, 1, order_request("store", "store", None, "pickup",
                                                        ("rice-ball",), recipient="agent:2", payment=None))
    assert "Wait until" in failure.value.suggested["next_step"]
    assert "give" not in failure.value.suggested["next_step"] and state.model_dump_json() == before


@pytest.mark.parametrize("payment", [None, "alipay", "wechat"])
def test_store_payment_app_is_explicit_and_has_no_separate_activity(payment):
    semantics, state = registered_world("store")
    outcome = purchase_order(state, semantics, 1, order_request("store", "store", None, "pickup",
                                                              ("rice-ball",), payment=payment))
    assert outcome.orders[0].payment_app_id == payment
    assert outcome.state.current_time == state.current_time and outcome.state.activities == {}


def test_nonpayment_app_is_refused_for_pickup():
    semantics, state = registered_world("store")
    with pytest.raises(BusinessRuleError, match="PAYMENT_APP_NOT_BOUND"):
        purchase_order(state, semantics, 1, order_request("store", "store", None, "pickup",
                                                        ("rice-ball",), payment="douyin"))


def test_cross_store_order_is_rejected_before_any_payment():
    semantics, state = registered_world()
    request = PurchaseOrderRequest(items=(
        PurchaseItem(store_id="noodle-1", goods_id="chips", quantity=1, recipient_id="agent:1"),
        PurchaseItem(store_id="cafe", goods_id="lemon-tea", quantity=1, recipient_id="agent:1")),
        fulfillment="delivery", destination_id="home", app_id="meituan", payment_app_id="alipay")
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match="ORDER_LOCATION_MISMATCH"):
        purchase_order(state, semantics, 1, request)
    assert state.model_dump_json() == before


@pytest.mark.parametrize("location,app,destination,code", [
    ("store", "meituan", "store", "APP_NOT_BOUND"),
    ("home", None, "store", "ACTIVITY_PRECONDITION_UNMET"),
    ("store", None, "home", "ACTIVITY_PRECONDITION_UNMET"),
])
def test_pickup_requires_caller_and_recipient_at_actual_store(location, app, destination, code):
    semantics, state = registered_world(location)
    with pytest.raises(BusinessRuleError, match=code):
        purchase_order(state, semantics, 1,
                       order_request("store", destination, app, "pickup", ("rice-ball",), payment=None))


def test_order_routing_rejects_recipient_without_receiving_place():
    from worldmock.life.models import ActivityState

    semantics, state = registered_world(scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    now = state.current_time
    state.activities["travelling"] = ActivityState(activity_id="travelling", agent_id=1, location_id="home",
        kind="travel", status="active", started_at=now, planned_end_at=now + 60, last_settled_at=now,
        elapsed_seconds=0, planned_duration_seconds=60, origin_action_id="a",
        origin_decision_id="d", origin_request_event_id="r")
    binding = {"store_id": "noodle-1", "goods_id": "chips", "app_id": "meituan", "payment_app_id": "alipay",
               "recipient_id": "agent:1", "selected_action": {"fulfillment": "delivery"}}
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET") as failure:
        order_requests(state, semantics, 1, [binding])
    assert failure.value.unmet == ["delivery_destination"]
    state.activities["travelling"] = state.activities["travelling"].model_copy(update={"agent_id": 2})
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET"):
        order_requests(state, semantics, 1, [{**binding, "recipient_id": "agent:2"}])


def test_multiple_order_results_merge_only_orders_money_and_answering():
    from worldmock.life.models import ActionResult
    from worldmock.life.order_routing import merged_result

    first = ActionResult(action_id="same-request", agent_id=1, status="completed", occurred_at=1,
        message="Purchase accepted", data={"orders": [{"order_id": "first"}], "total_price_fen": 600,
                                          "resource_changes": {"account:agent:1": -600, "account:merchant:store": 600}})
    second = first.model_copy(update={"data": {"orders": [{"order_id": "second"}], "total_price_fen": 800,
                                             "resource_changes": {"account:agent:1": -800,
                                                                  "account:merchant:cafe": 800}}})
    result = merged_result([first, second], "incoming-message")
    assert result.data == {"orders": [{"order_id": "first"}, {"order_id": "second"}], "total_price_fen": 1400,
        "resource_changes": {"account:agent:1": -1400, "account:merchant:store": 600, "account:merchant:cafe": 800},
        "answering": "incoming-message"}
    assert merged_result([first], None).data == first.data
