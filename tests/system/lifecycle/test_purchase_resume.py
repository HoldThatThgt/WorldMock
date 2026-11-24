"""购买与配送恢复后沿用受理时冻结的付款与送达时刻。"""

from datetime import datetime, timedelta

import pytest

from tests.integration.runtime_fixtures import BRANCH_BALANCE, branch_of
from tests.life.conftest import purchase, stage_day_plan
from tests.life.environment_runtime import act, events, reach
from worldmock.simulation_engine.commerce.purchase.purchase_phases import PAYMENT, activity_phases


def at_store(persona, config):
    persona["initial_location"] = "四季便利"


def payment_events(engine):
    return [(row.occurred_at, row.data["order_ids"], row.data["amount_fen"])
            for row in events(engine, "environment_event") if row.data.get("kind") == "purchase_payment"]


def frozen_fields(engine, activity):
    """一次购买的记录上受理时冻结的付款抽取与到目前为止写下的付款时刻。"""
    return {key: (order.app_id, order.payment_app_id, order.payment_duration_seconds, order.payment_started_at,
                  order.payment_ended_at, order.paid_at, order.expected_at, order.weather_delay_seconds)
            for key in activity.data["order_ids"] for order in (engine.context.state.deliveries[key],)}


@pytest.mark.parametrize("kind", ["resume", "replay"])
def test_branch_from_a_checkpoint_inside_the_first_payment_phase_keeps_the_frozen_payments(
        engine_factory, tmp_path, kind):
    parent = engine_factory(mutate=at_store)
    stage_day_plan(parent)
    observed, result = act(parent, "three", purchase("纸巾", "牛肉面", "平台笔记本"))
    assert result.status == "in_progress", result.error_code
    activity = parent.context.state.activities[result.activity_id]
    phases = activity_phases(parent.context.state, activity)
    paying = [phase for phase in phases if phase.step == PAYMENT]
    assert len(paying) >= 2 and paying[0].started_at > activity.started_at
    reach(parent, paying[0].started_at)
    at_checkpoint = frozen_fields(parent, activity)
    assert [fields[3] for fields in at_checkpoint.values()].count(paying[0].started_at) == 1
    assert all(fields[4] is None and fields[5] is None for fields in at_checkpoint.values())
    child = branch_of(parent, tmp_path, kind, observed["observation_event_id"])
    try:
        restored = child.context.state.activities[result.activity_id]
        assert restored == parent.context.state.activities[result.activity_id]
        assert (restored.status, restored.last_settled_at) == ("active", paying[0].started_at)
        assert (restored.started_at, restored.planned_end_at) == (activity.started_at, activity.planned_end_at)
        assert frozen_fields(child, restored) == at_checkpoint
        assert activity_phases(child.context.state, restored) == phases
        reach(parent, activity.planned_end_at)
        reach(child, activity.planned_end_at)
        assert parent.context.state.activities[result.activity_id].status == "completed"
        assert child.context.state.activities[result.activity_id].status == "completed"
        finished = frozen_fields(parent, activity)
        assert frozen_fields(child, restored) == finished
        assert {fields[5] for fields in finished.values()} == {activity.planned_end_at}
        for phase in paying:
            (fields,) = [row for row in finished.values() if (row[3], row[4]) == (phase.started_at, phase.ended_at)]
            assert fields[2] == phase.ended_at - phase.started_at
        assert payment_events(child) == payment_events(parent) and len(payment_events(parent)) == 3
        spent = sum(amount for _, _, amount in payment_events(parent))
        assert parent.context.state.accounts["agent:1"] == 200000 - spent
        assert child.context.state.accounts["agent:1"] == (BRANCH_BALANCE if kind == "replay" else 200000) - spent
    finally:
        child.close()


def test_late_delivery_keeps_its_rainy_quote_after_midnight_and_restore(engine_factory, tmp_path):
    """大雨时下单，预计 23:59；次日零点放晴后仍在途。恢复后 00:02 才送达，两份只写一条延误说明。"""
    from tests.life.environment_inputs import copy_environment, write_catalog_rows
    from tests.life.current_world import stage_moment
    from tests.life.environment_runtime import finish_activity, observe_only
    from worldmock.foundation.errors import BusinessRuleError
    from worldmock.simulation_engine.commerce.fulfillment.commerce import deliver_order
    from worldmock.simulation_engine.commerce.fulfillment.receipt import settle_receipt
    from worldmock.simulation_engine.commerce.purchase.purchase_offers import quote_goods
    from worldmock.simulation_engine.scheduling.plans.day_plan_inputs import plan_inputs

    def late(persona, config):
        config["seed"] = 33
        config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
        config["stop_time"] = config["start_time"] + timedelta(days=1)

    root = copy_environment(tmp_path / "night")
    write_catalog_rows(root, "map.jsonl", [{"a": "home", "b": "noodle-1", "length_m": 4200}])
    engine = engine_factory(root, mutate=late)
    stage_moment(engine, engine.context.state.current_time + 32 * 60)
    stage_day_plan(engine)
    start = engine.context.state.current_time
    observed, bought = act(engine, "night-order", purchase("牛肉面", "牛肉面"))
    assert bought.status == "in_progress" and len(bought.data["orders"]) == 2
    assert all("weather_delay_seconds" not in row for row in bought.data["orders"])
    finish_activity(engine, bought, "night-purchase")
    state = engine.context.state
    assert state.weather_by_date["2026-01-05"] == {"test-city": "heavy_rain"}
    first, second = state.deliveries.values()
    assert {order.weather_delay_seconds for order in (first, second)} == {360}
    assert {order.expected_at for order in (first, second)} == {start + 27 * 60}
    shipment = next(item for item in state.pending_events if item.kind == "order_shipped")
    assert shipment.due_at == first.paid_at + (first.expected_at - first.paid_at + 9) // 10
    reach(engine, start + 28 * 60)
    child = branch_of(engine, tmp_path, "resume", observed["observation_event_id"])
    try:
        state = child.context.state
        assert state.weather_by_date["2026-01-06"] == {"test-city": "sunny"}
        fresh = quote_goods(child.context.inputs, state, 1, "beef-noodles", None)
        assert fresh.expected_at == state.current_time + 24 * 60 and fresh.weather_delay_seconds == 0
        assert state.deliveries[first.order_id] == first and first.expected_at < state.current_time < first.arrival_due_at()
        seeing = observe_only(child, "late-but-not-here")
        assert [row["status"] for row in seeing["todo_layer"]["orders"]] == ["pending"]
        assert seeing["todo_layer"]["orders"][0]["expected_at"].endswith("23:59:00+08:00")
        plan = plan_inputs(child.context, child.observations, child.midnight_rhythm_pending, 1)
        assert {row["预计送达"] for row in plan.payload["在途订单"]} == {"1月5日 23:59"}
        for layer in ("progress_layer", "memory_layer", "todo_layer"):
            assert all(key not in str(seeing[layer]) for key in ("weather_delay_seconds", "late_by_seconds", "00:02", "这份品质"))
        for attempt in (deliver_order, settle_receipt):
            with pytest.raises(BusinessRuleError, match="DELIVERY_NOT_DUE"):
                attempt(state, child.context, first.order_id)
        assert not any(row.data.get("kind") == "goods_received" for row in events(child))
        reach(child, start + 30 * 60)
        delivered = [row for row in events(child) if row.data.get("kind") == "delivery_arrival"]
        assert [(row.data["weather_delay_seconds"], row.data["late_by_seconds"]) for row in delivered] == [(360, 180)]
        assert all("weather_delay_seconds" not in row for row in delivered[0].data["orders"])
        assert {order.arrived_at for order in child.context.state.deliveries.values()} == {start + 30 * 60}
        notices = [row for row in events(child) if row.data.get("title") == "订单已送达"]
        assert [row.data["summary"] for row in notices] == ["牛肉面×2已送达。天气使配送多用了6分钟，比下单预计晚3分钟"]
        visible = observe_only(child, "after-arrival")
        records = visible["progress_layer"]["events"]
        assert [row["message"] for row in records if row["type"] == "delivery"] == [
            "已送到家。天气使配送多用了6分钟，比下单预计晚3分钟", "已送到家"]
        received = [event for event in events(child) if event.data.get("kind") == "goods_received"]
        assert {event.data["order_id"] for event in received} == {first.order_id, second.order_id}
        assert {event.event_id for event in received} <= set(visible["progress_event_ids"])
        assert [(row["store"], row["product"], row["quality"]) for row in records if row["type"] == "receipt"] == [
            ("一心面馆", "牛肉面", event.data["quality"]) for event in received]
        assert observe_only(child, "same-receipts")["progress_layer"] == visible["progress_layer"]
    finally:
        child.close()
