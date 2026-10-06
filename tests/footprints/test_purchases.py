"""@brief 从实际固定食品订单的结果事件提取购买事实与幂等身份。"""

import pytest
from worldmock.footprints.purchases import project_purchase_facts
from worldmock.life.basket import purchase_order
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest
from tests.life.purchase_worlds import registered_world


@pytest.mark.parametrize("fulfillment,location,app,fee", [("delivery", "home", "meituan", 500),
                                                        ("pickup", "noodle-2", None, 0)])
def test_actual_order_facts_preserve_food_store_app_and_fixed_money(fulfillment, location, app, fee):
    semantics, state = registered_world(location)
    store = "noodle-1" if fulfillment == "delivery" else "noodle-2"
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id=store, food_id="beef-noodles", quantity=1,
        recipient_id="agent:1"),), fulfillment=fulfillment, destination_id=location, app_id=app)
    outcome = purchase_order(state, semantics, 1, request)
    event = {"event_type": "action_result", "event_id": "fact-result", "action_id": "food-buy",
             "data": {"data": {"orders": [order.model_dump(mode="json") for order in outcome.orders]}}}
    rows = project_purchase_facts([event, event])
    assert len(rows) == 1
    row = rows[0]
    assert {"food_id", "store_id", "app_id", "unit_price_fen", "delivery_fee_fen",
            "total_price_fen", "quantity"} <= row.keys()
    assert row["food_id"] == "beef-noodles" and row["store_id"] == store and row["app_id"] == app
    assert row["unit_price_fen"] == 2800 and row["delivery_fee_fen"] == fee
    assert row["total_price_fen"] == 2800 + fee and row["quantity"] == 1
    assert not ({"category_match", "supply_id", "product_id", "quote"} & row.keys())
