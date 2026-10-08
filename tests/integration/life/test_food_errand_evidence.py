"""@brief 真实收货经事务证据提取与结算，只累计收货人的登记后商品数量。"""

import pytest

from tests.life.purchase_worlds import ENVIRONMENT, at_time, registered_world
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life import errands
from worldmock.life.basket import purchase_order
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest, WorldState
from worldmock.life.receipt import settle_receipt
from worldmock.scheduling.effects import process_scheduled_events


def food_world(location="noodle-1"):
    """@brief 实际多人物环境的世界，人物同处但没有任何持有字段。"""
    semantics, state = registered_world(location, scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    for agent in state.agents.values():
        agent.location_id = location
    return semantics, WorldState.model_validate(state.model_dump())


def note(state, semantics, quantity=2):
    """@brief 通过生产绑定、校验与新增入口记下每人的真实商品事务。"""
    changed = state.model_copy(deep=True)
    changes = [{"change": "新增", "name": "午饭事务", "item": semantics.catalog.goods["beef-noodles"].name,
                "quantity": quantity, "memo": None, "remind_time": None, "reason": None}]
    origin = errands.ErrandOrigin("note", "note-event", state.current_time, state.timezone)
    for identity, agent in state.agents.items():
        observed = errands.bindings(agent, semantics.catalog.goods)
        assert errands.change_issues(changes, observed) == []
        changed.agents[identity], _ = errands.apply_changes(agent, changes, observed, origin)
    return changed


def buy_request(recipient, destination="noodle-1", fulfillment="pickup"):
    """@brief 请求商品来自真实固定菜单，配送明确指定购物与支付应用。"""
    return PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", goods_id="beef-noodles", quantity=1,
                                                    recipient_id=f"agent:{recipient}"),),
        destination_id=destination, fulfillment=fulfillment,
        app_id="meituan" if fulfillment == "delivery" else None,
        payment_app_id="wechat" if fulfillment == "delivery" else None)


def payment_draft(state, orders, event_type="action_result"):
    """@brief 付款结果来自实际订单，不把订单状态推断为人物收货证据。"""
    return EventDraft(event_type=event_type, occurred_at=state.current_time, agent_id=1,
                      data={"data": {"orders": [row.model_dump(mode="json") for row in orders]}})


def progress(state, identity):
    """@brief 读取仍未完成事务的实际累计量。"""
    return next(iter(state.agents[identity].errands.values())).progress


def test_actual_pickup_credits_only_receipt_recipient_and_repeat_settlement_emits_no_evidence():
    semantics, state = food_world()
    state = note(state, semantics)
    bought = purchase_order(state, semantics, 1, buy_request(2))
    assert bought.receipts[0].agent_id == 2 and bought.receipts[0].data["payer_id"] == 1
    paid, no_events = errands.settle(bought.state, [payment_draft(bought.state, bought.orders)])
    assert paid is bought.state and no_events == []
    actual, events = errands.settle(paid, bought.receipts)
    assert progress(actual, 1) == 0 and progress(actual, 2) == 1
    assert len(events) == 1 and events[0].agent_id == 2
    assert events[0].caused_by == [bought.receipts[0].event_id]
    repeated = settle_receipt(actual, semantics, bought.orders[0].order_id)
    assert repeated.state is actual and repeated.receipts == ()
    unchanged, no_events = errands.settle(repeated.state, repeated.receipts)
    assert unchanged is actual and no_events == []


@pytest.mark.parametrize("event_type", ["action_result", "action_accepted"])
@pytest.mark.parametrize("fulfillment", ["pickup", "delivery"])
def test_actual_payment_orders_are_not_errand_evidence_even_when_delivered(event_type, fulfillment):
    semantics, state = food_world()
    state = note(state, semantics)
    bought = purchase_order(state, semantics, 1, buy_request(2, fulfillment=fulfillment))
    draft = payment_draft(bought.state, bought.orders, event_type)
    assert errands.evidence_rows(draft.event_type, draft.agent_id, draft.data) == []
    actual, events = errands.settle(bought.state, [draft])
    assert actual is bought.state and events == []
    assert progress(actual, 1) == progress(actual, 2) == 0


def test_actual_pending_delivery_does_not_credit_and_restored_receipt_credits_once():
    semantics, state = food_world()
    state = note(state, semantics)
    bought = purchase_order(state, semantics, 1, buy_request(2, fulfillment="delivery"))
    paid, no_events = errands.settle(bought.state, [payment_draft(bought.state, bought.orders)])
    assert no_events == [] and progress(paid, 1) == progress(paid, 2) == 0
    restored = WorldState.model_validate_json(paid.model_dump_json())
    ready = at_time(restored, bought.orders[0].expected_at)
    received, drafts, _ = process_scheduled_events(ready, ready.current_time, semantics)
    actual, events = errands.settle(received, drafts)
    assert progress(actual, 1) == 0 and progress(actual, 2) == 1
    assert len(events) == 1 and events[0].caused_by == [drafts[1].event_id]
    restored = WorldState.model_validate_json(actual.model_dump_json())
    repeated = restored.model_copy(update={"pending_events": list(bought.scheduled)}, deep=True)
    again, drafts, _ = process_scheduled_events(repeated, ready.current_time, semantics)
    assert again == restored and drafts == []
    unchanged, events = errands.settle(again, drafts)
    assert unchanged is again and events == []


@pytest.mark.parametrize("kind", ["given", "consume", "errand_claimed", "goal_progressed"])
def test_retired_giving_consumption_and_self_reports_cannot_progress_errands(kind):
    semantics, state = food_world()
    state = note(state, semantics)
    bought = purchase_order(state, semantics, 1, buy_request(1))
    receipt = bought.receipts[0]
    data = {**receipt.data, "kind": kind, "summary": "午饭事务已经办成"}
    for event_type in ("environment_event", "activity_progress"):
        draft = EventDraft(event_type=event_type, occurred_at=state.current_time, agent_id=1, data=data)
        assert errands.evidence_rows(event_type, 1, data) == []
        actual, events = errands.settle(bought.state, [draft])
        assert actual is bought.state and events == [] and progress(actual, 1) == 0


def test_receipt_before_registration_in_same_commit_does_not_count_but_after_it_does():
    semantics, original = food_world()
    bought = purchase_order(original, semantics, 1, buy_request(1))
    state = note(bought.state, semantics)
    errand = next(iter(state.agents[1].errands.values()))
    registered = EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=1,
        data={"kind": "errand_registered", "errand": errand.model_dump(mode="json")})
    before, events = errands.settle(state, [*bought.receipts, registered])
    assert before is state and events == [] and progress(before, 1) == 0
    after, events = errands.settle(state, [registered, *bought.receipts])
    assert progress(after, 1) == 1 and len(events) == 1
    assert events[0].caused_by == [bought.receipts[0].event_id]


@pytest.mark.parametrize("identity", [None, True, 0, -1, "1"])
def test_goods_receipt_rejects_an_invalid_simulated_recipient(identity, caplog):
    semantics, state = food_world()
    bought = purchase_order(state, semantics, 1, buy_request(1))
    data = bought.receipts[0].data
    with pytest.raises(IntegrityError, match="no simulated recipient"):
        errands.evidence_rows("environment_event", identity, data)
    assert "Goods receipt evidence has no simulated recipient" in caplog.text


def test_two_actual_receipts_accumulate_quantity_and_remove_only_the_completed_recipient_errand():
    semantics, state = food_world()
    state = note(state, semantics)
    first = purchase_order(state, semantics, 1, buy_request(2))
    partial, events = errands.settle(first.state, first.receipts)
    assert progress(partial, 2) == 1 and events[0].data["kind"] == "errand_progressed"
    second = purchase_order(partial, semantics, 1, buy_request(2))
    assert second.orders[0].order_id != first.orders[0].order_id
    complete, events = errands.settle(second.state, second.receipts)
    assert complete.agents[2].errands == {} and progress(complete, 1) == 0
    assert events[0].data["kind"] == "errand_completed" and events[0].agent_id == 2
    assert events[0].caused_by == [second.receipts[0].event_id]


def test_unaffordable_actual_purchase_preserves_open_errand_quantity_and_all_world_bytes():
    semantics, state = food_world()
    state = note(state, semantics)
    state.accounts[state.agents[1].balance_account_id] = 0
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match="INSUFFICIENT_FUNDS"):
        purchase_order(state, semantics, 1, buy_request(1))
    assert state.model_dump_json() == before and progress(state, 1) == progress(state, 2) == 0
