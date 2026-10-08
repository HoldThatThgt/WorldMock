"""@brief 配送预排只由真实商品订单收货产生，旧供给变更退出事件域。"""

import pytest

from tests.life.purchase_worlds import registered_world, at_time
from tests.integration.life.test_commerce import order_request
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


def test_actual_scheduled_payroll_transfers_fixed_salary_without_work_or_submission():
    """@brief 工资真实预排结算守恒，与任何工作内容或自述完成无关。"""
    from worldmock.scheduling.compile import compile_schedule
    from worldmock.scheduling.effects import process_scheduled_events
    semantics, state = registered_world()
    payroll = next(item for item in compile_schedule(semantics.scenario, state.current_time, state.end_time)
                   if item.kind == ScheduledKind.PAYROLL)
    state = at_time(state, payroll.due_at)
    state.pending_events = [payroll]
    balances = dict(state.accounts)
    changed, drafts, wakes = process_scheduled_events(state, state.current_time, semantics)
    amount, employer = payroll.data["amount_fen"], payroll.data["employer_account_id"]
    assert changed.accounts["agent:1"] == balances["agent:1"] + amount
    assert changed.accounts[employer] == balances[employer] - amount
    assert sum(changed.accounts.values()) == sum(balances.values()) and changed.activities == {}
    assert changed.agents[1].content_progress == {}
    assert drafts[0].data["payroll_paid"] is True and drafts[0].occurred_at == payroll.due_at
    assert drafts[0].data["resource_changes"] == {f"account:{employer}": -amount, "account:agent:1": amount}
