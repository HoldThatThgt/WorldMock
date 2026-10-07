"""@brief 实际固定食品价格不随时刻变化，订单恢复保留所有结算事实。"""

from worldmock.life.basket import purchase_order
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest, WorldState
from tests.life.purchase_worlds import registered_world, at_time


def test_food_fixed_price_is_independent_of_opening_time_and_quote_state():
    semantics, state = registered_world("noodle-2")
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-2", food_id="beef-noodles", quantity=2,
        recipient_id="agent:1"),), fulfillment="pickup", destination_id="noodle-2", app_id=None)
    late = at_time(state, state.current_time + 23 * 3600)
    for world in (state, late):
        outcome = purchase_order(world, semantics, 1, request)
        assert outcome.total_price_fen == 5600 and outcome.orders[0].unit_price_fen == 2800
        restored = WorldState.model_validate_json(outcome.state.model_dump_json())
        assert restored.deliveries == outcome.state.deliveries
        assert restored.agents[1].inventory["noodle-2"]["beef-noodles"] == 2
