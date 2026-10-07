"""@brief 购买和收货足迹来自当前真实结算，保留付款应用、货品和固定金额。"""

import pytest
from worldmock.footprints.models import Event
from worldmock.footprints.purchases import project_purchase_facts
from worldmock.life.basket import purchase_order
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest
from tests.life.purchase_worlds import registered_world


def result_event(outcome, moment):
    """@brief 以当前完整事件合同包装真实纯购买结算的订单结果。"""
    return Event(event_id="fact-result", event_seq=1, run_id="pure-food", event_type="action_result",
                 occurred_at=moment, recorded_at="2026-01-05T08:00:00+00:00", wall_elapsed_ms=1,
                 state_version_before=0, state_version_after=1, agent_id=1, action_id="goods-buy",
                 data={"status": "completed", "data": {
                     "orders": [order.model_dump(mode="json") for order in outcome.orders]}}).model_dump(mode="json")


@pytest.mark.parametrize("fulfillment,location,app,fee", [("delivery", "home", "meituan", 500),
                                                        ("pickup", "noodle-2", None, 0)])
def test_actual_order_facts_preserve_food_store_app_and_fixed_money(fulfillment, location, app, fee):
    semantics, state = registered_world(location)
    store = "noodle-1" if fulfillment == "delivery" else "noodle-2"
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id=store, goods_id="beef-noodles", quantity=1,
        recipient_id="agent:1"),), fulfillment=fulfillment, destination_id=location, app_id=app,
        payment_app_id="alipay")
    outcome = purchase_order(state, semantics, 1, request)
    event = result_event(outcome, state.current_time)
    rows = project_purchase_facts([event, event])
    assert len(rows) == 1
    row = rows[0]
    assert row["fact_kind"] == "purchase"
    assert {"goods_id", "category", "store_id", "app_id", "payment_app_id", "unit_price_fen", "delivery_fee_fen",
            "total_price_fen", "quantity"} <= row.keys()
    assert row["goods_id"] == "beef-noodles" and row["store_id"] == store and row["app_id"] == app
    assert row["payment_app_id"] == "alipay" and row["category"] == "meal"
    assert row["unit_price_fen"] == 2800 and row["delivery_fee_fen"] == fee
    assert row["total_price_fen"] == 2800 + fee and row["quantity"] == 1
    assert not ({"category_match", "supply_id", "food_id", "product_id", "quote"} & row.keys())


def test_pickup_purchase_and_receipt_keep_two_different_actual_sources():
    semantics, state = registered_world("noodle-2")
    outcome = purchase_order(state, semantics, 1, PurchaseOrderRequest(items=(PurchaseItem(
        store_id="noodle-2", goods_id="beef-noodles", quantity=1, recipient_id="agent:1"),),
        fulfillment="pickup", destination_id="noodle-2", app_id=None, payment_app_id="alipay"))
    result = result_event(outcome, state.current_time)
    draft = outcome.receipts[0].model_dump()
    draft["event_id"] = "received"
    draft["caused_by"] = [result["event_id"]]
    received = Event(**draft, event_seq=2, run_id="pure-food",
                     recorded_at="2026-01-05T08:00:00+00:00", wall_elapsed_ms=2,
                     state_version_before=0, state_version_after=1).model_dump(mode="json")
    rows = project_purchase_facts([result, received])
    assert [row["fact_kind"] for row in rows] == ["purchase", "receipt"]
    assert rows[0]["source_event_id"] == "fact-result" and rows[1]["source_event_id"] == "received"
    assert rows[1]["agent_id"] == 1 and rows[1]["quantity"] == 1
    assert rows[1]["provided"] == received["data"]["provided"]
    assert rows[1]["applied"] == received["data"]["applied"]
