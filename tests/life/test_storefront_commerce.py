"""@brief 固定商品价格与当前动作的收货草稿接线。"""

from types import SimpleNamespace

from tests.life.purchase_worlds import registered_world, at_time
from tests.life.test_commerce import order_request
from worldmock.life.basket import purchase_order
from worldmock.life.models import WorldState
from worldmock.life.storefront_runtime import StorefrontRuntime


def test_goods_fixed_price_and_receipt_are_independent_of_opening_time():
    semantics, state = registered_world("noodle-2")
    request = order_request("noodle-2", "noodle-2", None, "pickup", payment=None)
    late = at_time(state, state.current_time + 23 * 3600)
    for world in (state, late):
        outcome = purchase_order(world, semantics, 1, request)
        assert outcome.total_price_fen == outcome.orders[0].unit_price_fen == 2800
        restored = WorldState.model_validate_json(outcome.state.model_dump_json())
        assert restored.deliveries == outcome.state.deliveries
        assert restored.agents[1].body.energy_kcal == 2400
        assert restored.deliveries[outcome.orders[0].order_id].received_at == world.current_time


def test_storefront_result_has_no_holding_evidence_and_receipts_drain_once():
    semantics, state = registered_world("store")
    runtime = StorefrontRuntime(SimpleNamespace(semantics=semantics))
    pending = SimpleNamespace(agent_id=1, action_id="purchase-request")
    changed, result = runtime._purchase(state, pending,
                                       order_request("store", "store", None, "pickup", ("rice-ball",), payment=None))
    assert changed.deliveries[result.data["orders"][0]["order_id"]].status == "delivered"
    assert set(result.data) == {"orders", "total_price_fen", "resource_changes"}
    assert "inventory_changes" not in result.data and "given" not in result.data
    receipts = runtime.drain_receipts()
    assert len(receipts) == 1 and receipts[0].agent_id == 1
    assert runtime.drain_receipts() == [] and runtime.pending_receipts == []


def test_delivery_offer_time_matches_the_actual_paid_order():
    from worldmock.life.basket import delivery_expected_at

    semantics, state = registered_world()
    quote_time = delivery_expected_at(semantics, "noodle-1", "home", state.current_time)
    outcome = purchase_order(state, semantics, 1, order_request())
    assert outcome.orders[0].expected_at == quote_time
