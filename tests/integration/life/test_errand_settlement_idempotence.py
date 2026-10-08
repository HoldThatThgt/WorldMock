"""@brief 事务只使用登记后的本人真实收货，同一次提交的先后与重复排期保持幂等。"""

from agentsociety2.society import DirectToolCall

from tests.integration.footprints.test_errand_export import late_evening, note as engine_note
from tests.life.conftest import context, errand_add
from tests.integration.life.test_acs_amended_runtime import all_events, perform, pump
from tests.integration.life.test_acs_runtime import request
from tests.integration.life.test_food_errand_evidence import buy_request, food_world, note, progress
from tests.life.work_runtime import trip_to
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life import errands
from worldmock.life.basket import purchase_order
from worldmock.life.models import WorldState
from worldmock.scheduling.effects import process_scheduled_events


def test_a_replayed_action_adds_neither_payment_receipt_nor_errand_progress(engine_factory):
    """@brief 真实动作身份重放只返回原结果，钱、收货和事务都不重复结算。"""
    engine = engine_factory(mutate=late_evening)
    engine_note(engine, "note", [errand_add("买晚饭", "牛肉面", quantity=2)])
    trip_to(engine, "noodle-1", "visit-store")
    value = request("牛肉面", None, "购买")
    bought = perform(engine, "buy", value)
    assert bought.error_code is None, bought.data
    balances = engine.context.state.accounts.copy()
    before = [event for event in all_events(engine) if event.data.get("kind") in {"goods_received", "errand_progressed"}]
    ctx = context(engine, "replay")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, bought.action_id, "start_activity", value))
    assert engine.commit_decision_round("replay") == [bought]
    replay = next(event for event in all_events(engine) if event.event_type == "action_result" and event.action_id == bought.action_id and event.data.get("reused"))
    assert replay.data["data"]["orders"] == bought.data["orders"]
    assert engine.context.state.accounts == balances
    assert [event for event in all_events(engine) if event.data.get("kind") in {"goods_received", "errand_progressed"}] == before
    assert next(iter(engine.context.state.agents[1].errands.values())).progress == 1
    assert "inventory" not in engine.context.state.agents[1].model_dump()


def _arrival(state, semantics, scheduled):
    """@brief 恢复严格状态再到真实订单到期时刻，收货草稿只由调度消费者生成。"""
    state = WorldState.model_validate_json(state.model_dump_json())
    state.current_time = scheduled.due_at
    for agent in state.agents.values():
        agent.body_settled_at = state.current_time
    return process_scheduled_events(state, state.current_time, semantics)


def test_repeated_due_delivery_has_no_second_receipt_or_errand_evidence():
    semantics, state = food_world()
    state = note(state, semantics)
    ordered = purchase_order(state, semantics, 1, buy_request(2, fulfillment="delivery"))
    received, drafts, _ = _arrival(ordered.state, semantics, ordered.scheduled[0])
    actual, settled = errands.settle(received, drafts)
    assert progress(actual, 2) == 1 and len(settled) == 1
    assert settled[0].caused_by == [drafts[1].event_id]
    repeated = actual.model_copy(update={"pending_events": list(ordered.scheduled)}, deep=True)
    again, drafts, wakes = process_scheduled_events(repeated, repeated.current_time, semantics)
    assert again == actual and drafts == [] and wakes == {}
    unchanged, settled = errands.settle(again, drafts)
    assert unchanged is again and settled == [] and progress(unchanged, 2) == 1
    assert unchanged.accounts == ordered.state.accounts


def test_parallel_order_result_is_not_an_extra_copy_of_receipt_evidence():
    semantics, state = food_world()
    state = note(state, semantics)
    bought = purchase_order(state, semantics, 1, buy_request(2))
    orders = [order.model_dump(mode="json") for order in bought.orders]
    result = EventDraft(event_type="action_result", occurred_at=state.current_time, agent_id=1,
                        data={"data": {"orders": orders, "activity_results": [{"orders": orders}]}})
    actual, settled = errands.settle(bought.state, [result, *bought.receipts])
    assert progress(actual, 2) == 1 and len(settled) == 1
    assert settled[0].caused_by == [bought.receipts[0].event_id]


def test_purchase_commit_and_delivery_commit_each_credit_their_own_receipt_once(tmp_path):
    """@brief SQLite 两次提交各由不同真实收货推进一份，钱只在对应下单提交结算。"""
    semantics, state = food_world()
    state = note(state, semantics, quantity=3)
    pending = purchase_order(state, semantics, 1, buy_request(2, fulfillment="delivery"))
    pickup = purchase_order(pending.state, semantics, 1, buy_request(2))
    actual, settled = errands.settle(pickup.state, pickup.receipts)
    assert progress(actual, 2) == 1 and len(settled) == 1
    store = FootprintStore.create(tmp_path / "run", {"run_id": state.run_id, "agent_ids": [1, 2]},
                                  state.model_dump(mode="json"))
    try:
        actual.version = state.version + 1
        paid = EventDraft(event_type="action_result", occurred_at=state.current_time, agent_id=1,
            data={"data": {"orders": [row.model_dump(mode="json") for row in (*pending.orders, *pickup.orders)]},
                  "resource_changes": {f"account:{key}": value - state.accounts[key]
                    for key, value in actual.accounts.items() if value != state.accounts[key]}})
        pickup.receipts[0].caused_by = [paid.event_id]
        first = store.commit(actual.model_dump(mode="json"), [paid, *pickup.receipts, *settled])
        assert first[-1].caused_by == [first[1].event_id]
        received, drafts, _ = _arrival(actual, semantics, pending.scheduled[0])
        final, settled = errands.settle(received, drafts)
        assert progress(final, 2) == 2 and len(settled) == 1
        final.version = actual.version + 1
        second = store.commit(final.model_dump(mode="json"), [*drafts, *settled])
        assert second[-1].caused_by == [second[1].event_id]
        assert first[-1].state_version_after != second[-1].state_version_after
        assert final.accounts == pickup.state.accounts
    finally:
        store.close()


def test_same_commit_registration_uses_event_order_instead_of_equal_clock():
    semantics, state = food_world()
    bought = purchase_order(state, semantics, 1, buy_request(2))
    registered = note(bought.state, semantics, quantity=1)
    errand = next(iter(registered.agents[2].errands.values()))
    noted = EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=2,
                      data={"kind": "errand_registered", "errand": errand.model_dump(mode="json")})
    before, no_events = errands.settle(registered, [*bought.receipts, noted])
    assert before is registered and no_events == [] and progress(before, 2) == 0
    after, [completed] = errands.settle(registered, [noted, *bought.receipts])
    assert after.agents[2].errands == {}
    assert completed.caused_by == [bought.receipts[0].event_id]
    assert completed.occurred_at == noted.occurred_at


def test_two_actual_receipts_in_one_commit_credit_in_source_event_order():
    semantics, state = food_world()
    state = note(state, semantics)
    first = purchase_order(state, semantics, 1, buy_request(2))
    second = purchase_order(first.state, semantics, 1, buy_request(2))
    actual, settled = errands.settle(second.state, [*first.receipts, *second.receipts])
    assert [event.data["kind"] for event in settled] == ["errand_progressed", "errand_completed"]
    assert [event.data["errand"]["progress"] for event in settled] == [1, 2]
    assert [event.caused_by for event in settled] == [[first.receipts[0].event_id], [second.receipts[0].event_id]]
    assert actual.agents[2].errands == {} and progress(actual, 1) == 0
