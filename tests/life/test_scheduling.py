"""@brief 配送预排只由真实商品订单收货产生，旧供给变更退出事件域。"""

import pytest

from tests.life.purchase_worlds import registered_world, at_time
from tests.life.test_commerce import order_request
from worldmock.life.basket import purchase_order
from worldmock.life.body import last_flows
from worldmock.life.models import WorldState
from worldmock.scheduling.events import ScheduledKind
from worldmock.scheduling.effects import apply_scheduled_effect


@pytest.mark.parametrize("value", ["price_change", "supply_quote_change", "product_release"])
def test_retired_food_supply_changes_are_not_schedulable(value):
    with pytest.raises(ValueError):
        ScheduledKind(value)


def test_real_delivery_event_after_json_restore_receives_actual_goods_once():
    semantics, state = registered_world()
    outcome = purchase_order(state, semantics, 1, order_request())
    order, event = outcome.orders[0], outcome.scheduled[0]
    restored = WorldState.model_validate_json(outcome.state.model_dump_json())
    ready = at_time(restored, order.expected_at)
    once = apply_scheduled_effect(ready, event, semantics)
    twice = apply_scheduled_effect(once.state, event, semantics)
    assert once.state.deliveries[order.order_id].received_at == order.expected_at
    assert once.receipts[0].data["goods_id"] == "beef-noodles"
    assert once.state.agents[1].body.energy_kcal == 2400
    assert twice.state == once.state and twice.data == {} and twice.receipts == ()
    assert once.state.accounts == outcome.state.accounts and last_flows() == ()
