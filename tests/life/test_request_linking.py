"""@brief 实际食品订单关联消息，恢复后仍拒绝重复代点。"""

import pytest
from worldmock.errors import PreconditionError
from worldmock.life.basket import purchase_order
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest, WorldState
from worldmock.life.request_linking import require_unhandled
from tests.life.purchase_worlds import registered_world


def test_real_food_order_keeps_message_link_and_rejects_duplicate_after_restore():
    semantics, state = registered_world()
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", food_id="beef-noodles", quantity=1,
        recipient_id="agent:1", request_message_id="meal-request"),), fulfillment="delivery",
        destination_id="home", app_id="meituan")
    outcome = purchase_order(state, semantics, 1, request)
    restored = WorldState.model_validate_json(outcome.state.model_dump_json())
    binding = {"food_id": "beef-noodles", "recipient_id": "agent:1", "request_message_id": "meal-request",
               "answering": "同事 12:00 的消息"}
    before = restored.model_dump_json()
    with pytest.raises(PreconditionError, match="REQUEST_ALREADY_HANDLED") as failure:
        require_unhandled(restored, semantics.scenario, [binding])
    assert "牛肉面" in failure.value.public_reason and failure.value.suggested["linked_orders"]
    assert restored.model_dump_json() == before
    require_unhandled(restored, semantics.scenario, [{**binding, "food_id": "chips"}])
