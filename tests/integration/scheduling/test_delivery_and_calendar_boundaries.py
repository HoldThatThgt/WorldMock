"""完整环境的真实收货因果、重复到期与本地日历排程。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from tests.life.conftest import purchase
from tests.life.environment_runtime import act, events, finish_activity, reach
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import process_scheduled_events


def test_calendar_declaration_is_compiled_at_real_local_midnight(engine_factory):
    def mutate(persona, config):
        config["calendar"]["makeup_work_dates"] = ["2026-01-06"]

    engine = engine_factory(mutate=mutate)
    flags = [item for item in compile_schedule(engine.context.inputs) if item.kind == "calendar_flag"]
    flagged = next(item for item in flags if item.data["date"] == "2026-01-06")
    assert flagged.data["flags"] == ["makeup_workday"]
    assert datetime.fromtimestamp(flagged.due_at, ZoneInfo(engine.context.state.timezone)).isoformat() == (
        "2026-01-06T00:00:00+08:00")


def test_actual_takeout_arrival_binds_one_receipt_and_replayed_delivery_emits_no_fact(engine_factory):
    engine = engine_factory()
    _, result = act(engine, "order-delivery", purchase("柠檬茶"))
    assert result.status == "in_progress"
    finish_activity(engine, result, "purchase-completion")
    paid = next(iter(engine.context.state.deliveries.values()))
    assert result.activity_id == f"activity-{result.action_id}-purchase"
    assert paid.order_id == f"order-{result.action_id}-0001"
    assert paid.fulfillment == "delivery" and paid.paid_at is not None and paid.received_at is None
    scheduled = next(item for item in engine.context.state.pending_events if item.kind == "delivery_arrival")
    balance = dict(engine.context.state.accounts)
    reach(engine, paid.expected_at)
    order = engine.context.state.deliveries[paid.order_id]
    assert order.status == "delivered" and order.received_at == paid.expected_at
    facts = events(engine)
    arrivals = [item for item in facts if item.data.get("kind") == "delivery_arrival"]
    receipts = [item for item in facts if item.data.get("kind") == "goods_received"]
    assert len(arrivals) == len(receipts) == 1 and receipts[0].caused_by == [arrivals[0].event_id]
    assert receipts[0].data["goods_id"] == paid.goods_id and engine.context.state.accounts == balance
    replay = engine.context.state.model_copy(update={"pending_events": [scheduled]}, deep=True)
    changed, drafts, wakes = process_scheduled_events(replay, replay.current_time, engine.context)
    assert drafts == [] and wakes == {} and changed.pending_events == []
    assert changed.agents == replay.agents and changed.accounts == replay.accounts
