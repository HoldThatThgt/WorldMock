"""购买、收货投影保留实际订单金额和两个不同的来源。"""

from tests.footprints.recording import recorded_engine
from tests.life.conftest import action_identity
from worldmock.recording_engine.projections.commerce.purchases import project_purchase_facts


def test_actual_basket_preserves_payment_goods_and_receipt_ownership(engine_factory):
    engine, _ = recorded_engine(engine_factory, content=False)
    events = [row.model_dump(mode="json") for row in list(engine.context.store.iter_events(
        engine.context.store.last_event_seq))]
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
    """一条 start_side_activity 购买调用（数组里几份商品都算）是一次尝试；同一工具的内容 app 调用不是购买尝试。"""
    engine, _ = recorded_engine(engine_factory)
    events = [row.model_dump(mode="json") for row in list(engine.context.store.iter_events(
        engine.context.store.last_event_seq))]
    attempts = [row for row in project_purchase_facts(events) if row["fact_kind"] == "order_attempt"]
    assert len(attempts) == 1
    assert attempts[0]["action"] == "purchase" and attempts[0]["status"] == "in_progress"
    assert attempts[0]["error_code"] is None
    requested = {row["event_id"]: row for row in events if row["event_type"] == "action_requested"}
    source = requested[attempts[0]["request_event_id"]]
    assert source["action_id"] == attempts[0]["action_id"] == action_identity("recorded-basket")
    assert source["data"]["tool_name"] == "start_side_activity"
    assert source["data"]["request"] == {"action": "purchase", "target": ["柠檬茶", "纸巾"], "prefer": None}
    side = [row["data"]["request"] for row in requested.values() if row["data"]["tool_name"] == "start_side_activity"]
    assert {"action": "interact", "target": ["哔哩哔哩"], "prefer": None} in side and len(side) >= 2
