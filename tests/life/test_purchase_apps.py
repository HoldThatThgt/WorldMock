"""@brief 固定菜单 app 绑定、真实收货人以及原生方式路由。"""

import pytest
from worldmock.errors import BusinessRuleError
from worldmock.life.basket import purchase_order
from worldmock.life.guidance import rejection_guidance
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest, StartActivityRequest
from worldmock.life.order_routing import order_requests
from worldmock.life.routing import combination_issues, routing_names
from tests.life.purchase_worlds import ENVIRONMENT, menu_context, registered_world


def test_unbound_app_refuses_with_exact_reason_and_bound_app_next_step():
    semantics, state = registered_world()
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", food_id="beef-noodles",
        quantity=1, recipient_id="agent:1"),), fulfillment="delivery", destination_id="home", app_id="eleme")
    with pytest.raises(BusinessRuleError, match="APP_NOT_BOUND") as failure:
        purchase_order(state, semantics, 1, request)
    assert rejection_guidance(failure.value)["reason"] == "This store cannot be ordered through that app."
    assert rejection_guidance(failure.value)["next_step"] == "Order through 美团."
    assert failure.value.unmet == []


def test_delivery_to_other_person_uses_their_receiving_location():
    semantics, state = registered_world(scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    other = next(identity for identity in state.agents if identity != 1)
    agents = {**state.agents, other: state.agents[other].model_copy(update={"location_id": "park"})}
    state = state.model_copy(update={"agents": agents})
    binding = {"store_id": "noodle-1", "food_id": "beef-noodles", "app_id": "meituan",
               "recipient_id": f"agent:{other}", "selected_action": {"fulfillment": "delivery"}}
    requests = order_requests(state, semantics, 1, [binding])
    assert requests[0].destination_id == "park"
    outcome = purchase_order(state, semantics, 1, requests[0])
    assert outcome.orders[0].recipient_id == f"agent:{other}" and outcome.orders[0].destination_id == "park"


def test_native_purchase_routing_derives_way_without_action_field():
    context = menu_context()
    observation = {"interaction_bindings": context.bindings, "agent_id": 1}
    request = StartActivityRequest.model_validate({"items": [{"target": "牛肉面", "way": "远程下单",
        "from": "美团", "to": None}], "answering": None, "prefer": "最快拿到"})
    assert combination_issues(request, routing_names(observation)) == []
    assert "action" not in request.model_dump()["items"][0]
