"""实际购买从受理到付款、收货和取消的原子行为。"""

from datetime import timedelta

import pytest

from tests.life.conftest import (
    action_identity, advance_next, conclude_request, errand_add, purchase, report_request, use_app,
)
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, finish_activity, reach


def at_store(persona, config):
    persona["initial_location"] = "四季便利"


def business_events(engine, kind):
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == kind]


def test_local_basket_pays_once_at_completion_and_receives_each_actual_item(engine_factory):
    engine = engine_factory(mutate=at_store)
    _, result = act(engine, "basket", purchase("柠檬茶", "纸巾"))
    assert result.status == "in_progress", result.error_code
    assert result.activity_id == f"activity-{action_identity('basket')}-purchase"
    activity = engine.context.state.activities[result.activity_id]
    orders = list(engine.context.state.deliveries.values())
    assert len(orders) == 2 and len(engine.context.state.activities) == 1
    # 一份一条商品履约记录，序号按配送组、组内按商品身份升序补零到四位；结果只带这条动作创建的记录
    assert {order.order_id: (order.goods_id, order.quantity) for order in orders} == {
        f"order-{action_identity('basket')}-0001": ("lemon-tea", 1),
        f"order-{action_identity('basket')}-0002": ("tissue", 1)}
    assert [row["order_id"] for row in result.data["orders"]] == sorted(order.order_id for order in orders)
    assert activity.name.startswith("购买柠檬茶、纸巾（")
    assert activity.resource_claims == {"second"}
    assert len({order.group_key for order in orders}) == 1
    assert engine.context.state.accounts["agent:1"] == 200000
    assert all(order.paid_at is None and order.received_at is None for order in orders)
    while engine.context.state.current_time < activity.planned_end_at:
        advance_next(engine)
        if engine.context.state.current_time < activity.planned_end_at:
            assert engine.context.state.accounts["agent:1"] == 200000
            assert business_events(engine, "purchase_payment") == []
            assert business_events(engine, "goods_received") == []
    payment = business_events(engine, "purchase_payment")
    assert len(payment) == 1 and payment[0].data["amount_fen"] == 1800
    assert engine.context.state.accounts == {"agent:1": 198200, "external:scenario": -198200}
    assert len(business_events(engine, "goods_received")) == 2
    assert all(order.paid_at == activity.planned_end_at and order.received_at == activity.planned_end_at
               and order.status == "delivered" for order in engine.context.state.deliveries.values())
    # 两条到店记录是一张到店订单：同一种付款方式、同一段付款时间；进入付款不再另写自动 app 触点
    assert len({(order.payment_app_id, order.payment_duration_seconds) for order in orders}) == 1
    notifications = business_events(engine, "payment_notification")
    assert business_events(engine, "app_touch") == []
    final = list(engine.context.state.deliveries.values())
    if orders[0].payment_app_id is None:
        assert notifications == []
        assert all((order.payment_duration_seconds, order.payment_started_at, order.payment_ended_at)
                   == (0, None, None) for order in final)
    else:
        assert len(notifications) == 1
        assert notifications[0].caused_by == [payment[0].event_id]
        assert notifications[0].data["amount_fen"] == 1800
        seconds = orders[0].payment_duration_seconds
        assert 10 <= seconds <= 20
        assert all((order.payment_started_at, order.payment_ended_at)
                   == (activity.planned_end_at - seconds, activity.planned_end_at) for order in final)
    _, concluded = act(engine, "basket-concluded", conclude_request(activity.name), "conclude_activity")
    assert concluded.status == "completed"
    assert engine.context.state.accounts["agent:1"] == 198200


def test_early_conclusion_cancels_every_unpaid_item_without_receipt(engine_factory):
    def mutate(persona, config):
        at_store(persona, config)
        config["progress_report_interval_seconds"] = 1
    engine = engine_factory(mutate=mutate)
    _, result = act(engine, "cancel-basket", purchase("柠檬茶", "纸巾"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    activity = engine.context.state.activities[result.activity_id]
    assert activity.status == "active"
    _, ended = act(engine, "cancel", conclude_request(activity.name), "conclude_activity")
    assert ended.status == "completed"
    assert all(order.status == "cancelled" and order.paid_at is None and order.received_at is None
               for order in engine.context.state.deliveries.values())
    assert engine.context.state.accounts["agent:1"] == 200000
    assert business_events(engine, "purchase_payment") == business_events(engine, "goods_received") == []


def test_stop_boundary_truncates_and_cancels_every_unpaid_item(engine_factory):
    def mutate(persona, config):
        at_store(persona, config)
        config["stop_time"] = config["start_time"] + timedelta(hours=1)
    engine = engine_factory(mutate=mutate)
    stage_moment(engine, moment=engine.context.state.end_time - 10)
    _, result = act(engine, "truncated-basket", purchase("柠檬茶", "纸巾"))
    assert result.status == "in_progress"
    reach(engine, engine.context.state.end_time)
    assert engine.context.state.activities[result.activity_id].status == "truncated"
    assert all(order.status == "cancelled" and order.paid_at is None and order.received_at is None
               for order in engine.context.state.deliveries.values())
    assert engine.context.state.accounts["agent:1"] == 200000
    assert business_events(engine, "purchase_payment") == business_events(engine, "goods_received") == []
    assert not any(row.kind == "delivery_arrival" for row in engine.context.state.pending_events)


def test_actual_purchase_completion_rechecks_total_after_a_declared_callback(engine_factory, tmp_path):
    root = copy_environment(tmp_path / "input")
    write_catalog_rows(root, "callbacks.jsonl", [{
        "rule_id": "purchase-expense", "trigger": {"class": "action", "key": "purchase"},
        "effect": {"type": "transfer", "direction": "expense", "amount_fen": 199999}}])
    engine = engine_factory(root, mutate=at_store)
    _, result = act(engine, "unaffordable-end", purchase("柠檬茶", "纸巾"))
    assert result.status == "in_progress"
    # 一次购买写了两件商品，购买动作回调只触发一次，并回链这条动作的受理结果
    effects = business_events(engine, "callback_effect")
    assert [(row.data["rule_id"], row.data["status"]) for row in effects] == [("purchase-expense", "paid")]
    accepted = next(row for row in events(engine, "action_result") if row.action_id == result.action_id)
    assert effects[0].caused_by == [accepted.event_id]
    assert engine.context.state.accounts["agent:1"] == 1
    activity = engine.context.state.activities[result.activity_id]
    reach(engine, activity.planned_end_at)
    assert engine.context.state.activities[result.activity_id].status == "failed"
    assert all(order.status == "cancelled" and order.paid_at is None and order.received_at is None
               for order in engine.context.state.deliveries.values())
    assert engine.context.state.accounts["agent:1"] == 1
    assert business_events(engine, "purchase_payment") == business_events(engine, "goods_received") == []


def test_errand_completion_waits_for_real_receipt_and_counts_actual_units(engine_factory):
    def mutate(persona, config):
        at_store(persona, config)
        config["progress_report_interval_seconds"] = 60
    engine = engine_factory(mutate=mutate)
    _, using = act(engine, "keep", use_app("Keep"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    name = engine.context.state.activities[using.activity_id].name
    _, added = act(engine, "remember", conclude_request(name, errands=[errand_add("买纸巾", "纸巾", 2)]),
                   "conclude_activity")
    assert added.status == "completed", added.error_code
    for ordinal in (1, 2):
        _, bought = act(engine, f"paper-{ordinal}", purchase("纸巾"))
        held = list(engine.context.state.agents[1].errands.values())
        assert len(held) == 1 and held[0].progress == ordinal - 1
        finish_activity(engine, bought, f"paper-{ordinal}")
        if ordinal == 1:
            held = list(engine.context.state.agents[1].errands.values())
            assert held[0].progress == 1
        else:
            assert engine.context.state.agents[1].errands == {}
    progress = business_events(engine, "errand_progressed")
    completed = business_events(engine, "errand_completed")
    assert len(progress) == len(completed) == 1
    receipts = business_events(engine, "goods_received")
    assert progress[0].caused_by == [receipts[0].event_id]
    assert completed[0].caused_by == [receipts[1].event_id]


def test_takeaway_arrival_is_caused_by_its_payment_and_the_receipt_by_that_arrival(engine_factory):
    engine = engine_factory()
    _, result = act(engine, "takeaway", purchase("牛肉面"))
    assert result.status == "in_progress", result.error_code
    (order,) = engine.context.state.deliveries.values()
    assert order.fulfillment == "delivery" and order.destination_id == "home"
    accepted = next(row for row in events(engine, "action_result") if row.action_id == result.action_id)
    finish_activity(engine, result, "takeaway")
    (payment,) = business_events(engine, "purchase_payment")
    assert payment.caused_by == [accepted.event_id] and payment.data["order_ids"] == [order.order_id]
    (scheduled,) = [row for row in engine.context.state.pending_events if row.kind == "delivery_arrival"]
    assert scheduled.scheduled_id == f"delivery-{order.order_id}" and scheduled.due_at == order.expected_at
    assert scheduled.caused_by == [payment.event_id]
    reach(engine, order.expected_at)
    (arrival,) = business_events(engine, "delivery_arrival")
    assert arrival.occurred_at == order.expected_at and arrival.caused_by == [payment.event_id]
    assert [row["order_id"] for row in arrival.data["orders"]] == [order.order_id]
    (received,) = business_events(engine, "goods_received")
    assert received.caused_by == [arrival.event_id] and received.data["order_id"] == order.order_id
    assert engine.context.state.deliveries[order.order_id].status == "delivered"

