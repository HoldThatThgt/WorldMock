"""@brief 个人事务只在登记后的本人真实收货时推进，订单和主观自述不替代收货。"""

import pytest

from tests.integration.footprints.test_errand_export import late_evening, note
from tests.life.conftest import context, errand_add, errand_cancel, report_request
from tests.integration.life.test_acs_amended_runtime import all_events, perform, pump
from tests.integration.life.test_acs_runtime import request
from tests.integration.life.test_food_errand_evidence import food_world, buy_request, note as pure_note, progress
from tests.life.work_runtime import trip_to
from worldmock.footprints.models import EventDraft
from worldmock.life import errands
from worldmock.life.basket import purchase_order
from worldmock.life.models import WorldState


def settlements(engine, identity=1):
    """@brief 当前人物实际事务进展与办成事件。"""
    return [event for event in all_events(engine) if event.agent_id == identity
            and event.data.get("kind") in {"errand_progressed", "errand_completed"}]


def receipt(engine, order_id):
    """@brief 已提交的唯一订单收货事实，不从已付款订单推断收货。"""
    [actual] = [event for event in all_events(engine) if event.data.get("kind") == "goods_received"
                and event.data["order_id"] == order_id]
    return actual


def assert_settlement(event, evidence, kind, added):
    """@brief 收货是唯一原因，人物、时间、版本及数量与真实证据一致。"""
    assert (event.data["kind"], event.data["quantity_added"]) == (kind, added)
    assert (event.event_type, event.source_kind) == ("environment_event", "environment")
    assert event.visibility == ("agent" if kind == "errand_completed" else "system")
    assert (event.decision_id, event.action_id, event.activity_id) == (None, None, None)
    assert event.agent_id == evidence.agent_id and event.caused_by == [evidence.event_id]
    assert (event.occurred_at, event.state_version_after) == (evidence.occurred_at, evidence.state_version_after)
    assert set(event.data) == {"kind", "errand", "quantity_added"}


def current_errand(engine):
    """@brief 仍在待办集合中的唯一真实事务。"""
    [item] = engine.context.state.agents[1].errands.values()
    return item


def test_a_remote_order_completes_only_in_the_commit_that_receives_it(engine_factory):
    engine = engine_factory(mutate=late_evening)
    note(engine, "note", [errand_add("买薯片", "薯片")])
    bought = perform(engine, "buy", request("薯片", "美团", "购买", payment="微信"))
    assert bought.error_code is None, bought.data
    [order] = bought.data["orders"]
    assert order["status"] == "pending" and current_errand(engine).progress == 0 and settlements(engine) == []
    pump(engine, lambda: engine.context.state.deliveries[order["order_id"]].status == "delivered")
    [completed] = settlements(engine)
    assert_settlement(completed, receipt(engine, order["order_id"]), "errand_completed", 1)
    assert engine.context.state.agents[1].errands == {}
    assert engine.observe(context(engine, "received"))["todo_layer"]["errands"] == []


def test_noting_then_buying_at_the_store_completes_with_no_inventory(engine_factory):
    engine = engine_factory(mutate=late_evening)
    trip_to(engine, "noodle-1", "visit")
    noted = note(engine, "note", [errand_add("晚饭", "牛肉面")])
    bought = perform(engine, "buy", request("牛肉面", None, "购买"))
    assert bought.error_code is None, bought.data
    [order] = bought.data["orders"]
    assert order["fulfillment"] == "pickup" and order["received_at"] == noted.occurred_at
    [completed] = settlements(engine)
    actual = receipt(engine, order["order_id"])
    assert_settlement(completed, actual, "errand_completed", 1)
    result = next(event for event in all_events(engine) if event.event_type == "action_result"
                  and event.action_id == bought.action_id)
    assert actual.caused_by == [result.event_id] and actual.state_version_after == result.state_version_after
    assert "inventory" not in engine.context.state.agents[1].model_dump()


def test_quantity_two_with_one_received_stays_open_and_a_second_receipt_completes(engine_factory):
    engine = engine_factory(mutate=late_evening)
    trip_to(engine, "noodle-1", "visit")
    note(engine, "note", [errand_add("两份晚饭", "牛肉面", quantity=2)])
    first = perform(engine, "first", request("牛肉面", None, "购买"))
    assert first.error_code is None
    [partial] = settlements(engine)
    assert_settlement(partial, receipt(engine, first.data["orders"][0]["order_id"]), "errand_progressed", 1)
    assert (current_errand(engine).quantity, current_errand(engine).progress) == (2, 1)
    [row] = engine.observe(context(engine, "partial"))["todo_layer"]["errands"]
    assert row["progress"] == "已取得 1 份"
    second = perform(engine, "second", request("牛肉面", None, "购买"))
    assert second.error_code is None
    assert_settlement(settlements(engine)[1], receipt(engine, second.data["orders"][0]["order_id"]),
                      "errand_completed", 1)
    assert engine.context.state.agents[1].errands == {}


def test_a_receipt_before_noting_does_not_count_for_a_new_errand(engine_factory):
    engine = engine_factory(mutate=late_evening)
    trip_to(engine, "noodle-1", "visit")
    first = perform(engine, "prior", request("牛肉面", None, "购买"))
    assert first.error_code is None
    note(engine, "note", [errand_add("新晚饭", "牛肉面")])
    assert current_errand(engine).progress == 0 and settlements(engine) == []
    second = perform(engine, "after", request("牛肉面", None, "购买"))
    assert second.error_code is None
    [completed] = settlements(engine)
    assert_settlement(completed, receipt(engine, second.data["orders"][0]["order_id"]), "errand_completed", 1)


def test_another_payer_receipt_credits_only_the_actual_recipient():
    semantics, state = food_world()
    state = pure_note(state, semantics, quantity=3)
    for_other = purchase_order(state, semantics, 1, buy_request(2))
    actual, [first] = errands.settle(for_other.state, for_other.receipts)
    assert progress(actual, 1) == 0 and progress(actual, 2) == 1
    for_me = purchase_order(actual, semantics, 2, buy_request(1))
    final, [second] = errands.settle(for_me.state, for_me.receipts)
    assert progress(final, 1) == progress(final, 2) == 1
    assert first.agent_id == 2 and second.agent_id == 1
    assert second.caused_by == [for_me.receipts[0].event_id]


def test_a_contact_delivery_completes_no_simulated_persons_errand(engine_factory):
    def contacts(document):
        late_evening(document)
        document["agents"][0]["contacts"] = [{"contact_id": "colleague", "salutation": "王磊",
            "relation": "colleague", "closeness": 15, "channels": ["message"], "reply_policy": "model",
            "receive_location_id": "office"}]
    engine = engine_factory(mutate=contacts)
    note(engine, "note", [errand_add("买薯片", "薯片")])
    result = perform(engine, "contact", request("薯片", "美团", "购买", "联系人：王磊", "微信"))
    assert result.error_code is None, result.data
    [order] = result.data["orders"]
    pump(engine, lambda: engine.context.state.deliveries[order["order_id"]].status == "delivered")
    assert current_errand(engine).progress == 0 and settlements(engine) == []
    assert not [event for event in all_events(engine) if event.data.get("kind") == "goods_received"]
    assert "contact_holdings" not in engine.context.state.model_dump()


@pytest.mark.parametrize("kind", ["goal_progressed", "errand_claimed"])
def test_goals_and_claimed_completion_leave_the_errand_open(kind):
    semantics, state = food_world()
    state = pure_note(state, semantics)
    claim = EventDraft(event_type="environment_event", agent_id=1, occurred_at=state.current_time,
                       data={"kind": kind, "goods_id": "beef-noodles", "quantity": 9, "summary": "晚饭办成了"})
    actual, events = errands.settle(state, [claim])
    assert actual is state and events == [] and progress(actual, 1) == 0


def test_an_unaffordable_purchase_leaves_the_errand_and_world_unchanged():
    from worldmock.errors import BusinessRuleError
    semantics, state = food_world()
    state = pure_note(state, semantics)
    state.accounts["agent:1"] = 100
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match="INSUFFICIENT_FUNDS"):
        purchase_order(state, semantics, 1, buy_request(1))
    assert state.model_dump_json() == before and progress(state, 1) == 0


def test_a_completed_errand_frees_its_name_and_product_for_a_new_errand(engine_factory):
    engine = engine_factory(mutate=late_evening)
    trip_to(engine, "noodle-1", "visit")
    note(engine, "first-note", [errand_add("晚饭", "牛肉面")])
    result = perform(engine, "buy", request("牛肉面", None, "购买"))
    assert result.error_code is None and engine.context.state.agents[1].errands == {}
    again = note(engine, "again", [errand_add("晚饭", "牛肉面", quantity=2)])
    assert again.data["errands"] == [{"errand": "晚饭", "change": "新增"}]
    item = current_errand(engine)
    assert (item.product_id, item.quantity, item.progress, item.origin_action_id) == (
        "beef-noodles", 2, 0, again.action_id)


def test_a_receipt_and_cancel_in_one_commit_do_not_credit_a_cancelled_errand():
    semantics, state = food_world()
    state = pure_note(state, semantics)
    bought = purchase_order(state, semantics, 1, buy_request(1))
    observed = errands.bindings(bought.state.agents[1], semantics.catalog.goods)
    changed = bought.state.model_copy(deep=True)
    changed.agents[1], _ = errands.apply_changes(changed.agents[1], [errand_cancel("午饭事务", "不再跟踪")],
        observed, errands.ErrandOrigin("cancel", "cancel-event", state.current_time, state.timezone))
    actual, events = errands.settle(changed, bought.receipts)
    assert actual is changed and events == [] and actual.agents[1].errands == {}
    assert actual.deliveries[bought.orders[0].order_id].received_at == state.current_time


@pytest.mark.parametrize("before,completed", [(0.999999999, True), (0.9999995, False)])
def test_receipt_completion_tolerance_has_both_sides(before, completed):
    semantics, state = food_world()
    state = pure_note(state, semantics, quantity=2)
    item = next(iter(state.agents[1].errands.values()))
    state.agents[1].errands[item.errand_id] = item.model_copy(update={"progress": before})
    bought = purchase_order(state, semantics, 1, buy_request(1))
    evidence = bought.receipts[0].model_copy(
        update={"decision_id": "evidence-decision", "action_id": "evidence-action"})
    actual, [event] = errands.settle(bought.state, [evidence])
    assert event.data["errand"]["progress"] == round(before + 1, 9)
    assert event.caused_by == [bought.receipts[0].event_id]
    assert (event.decision_id, event.action_id) == (None, None)
    assert (event.data["kind"] == "errand_completed") is completed
    assert (actual.agents[1].errands == {}) is completed
