"""购买、收货投影保留实际订单金额和两个不同的来源。"""

from tests.footprints.recording import recorded_engine
from worldmock.footprints.purchases import project_purchase_facts


def test_actual_basket_preserves_payment_goods_and_receipt_ownership(engine_factory):
    engine, _ = recorded_engine(engine_factory, content=False)
    events = [row.model_dump(mode="json") for row in engine.context.store.read_events(
        engine.context.store.last_event_seq)]
    facts = project_purchase_facts(events)
    bought = [row for row in facts if row["fact_kind"] == "purchase"]
    received = [row for row in facts if row["fact_kind"] == "receipt"]
    assert len(bought) == len(received) == 2
    assert {row["goods_id"] for row in bought} == {"lemon-tea", "tissue"}
    assert sum(row["total_price_fen"] for row in bought) == 1800
    assert all(row["agent_id"] == 1 and row["quantity"] == 1 and row["delivery_fee_fen"] == 0 for row in bought)
    sources = {row["event_id"]: row for row in events}
    assert all(sources[row["source_event_id"]]["event_type"] == "action_result" for row in bought)
    assert all(sources[row["source_event_id"]]["data"]["kind"] == "goods_received" for row in received)
    assert {row["source_event_id"] for row in bought}.isdisjoint(row["source_event_id"] for row in received)
    assert {row["order_id"] for row in bought} == {row["order_id"] for row in received}
    assert project_purchase_facts(events) == facts


def test_real_purchase_attempt_is_one_current_action(engine_factory):
    engine, _ = recorded_engine(engine_factory, content=False)
    events = [row.model_dump(mode="json") for row in engine.context.store.read_events(
        engine.context.store.last_event_seq)]
    attempts = [row for row in project_purchase_facts(events) if row["fact_kind"] == "order_attempt"]
    assert len(attempts) == 1
    assert attempts[0]["action"] == "purchase" and attempts[0]["status"] == "in_progress"
    assert attempts[0]["error_code"] is None
