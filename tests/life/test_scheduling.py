"""@brief 食品预排只保留真实订单送达，旧供给变更不再属于事件域。"""

import pytest
from worldmock.scheduling.events import ScheduledKind
from worldmock.scheduling.effects import apply_scheduled_effect
from worldmock.life.basket import purchase_order
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest, WorldState
from tests.life.purchase_worlds import registered_world, at_time


@pytest.mark.parametrize("value", ["price_change", "supply_quote_change", "product_release"])
def test_retired_food_supply_changes_are_not_schedulable(value):
    with pytest.raises(ValueError):
        ScheduledKind(value)


def test_real_delivery_event_after_json_restore_adds_actual_food_once():
    semantics, state = registered_world()
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="cafe", food_id="sandwich", quantity=1,
        recipient_id="agent:1"),), fulfillment="delivery", destination_id="home", app_id=None)
    outcome = purchase_order(state, semantics, 1, request)
    order, event = outcome.orders[0], outcome.scheduled[0]
    restored = WorldState.model_validate_json(outcome.state.model_dump_json())
    ready = at_time(restored, order.expected_at)
    once = apply_scheduled_effect(ready, event, semantics)
    twice = apply_scheduled_effect(once.state, event, semantics)
    assert once.state.agents[1].inventory["home"]["sandwich"] == 1
    assert twice.state.agents[1].inventory == once.state.agents[1].inventory
    assert twice.data["inventory_changes"] == []
