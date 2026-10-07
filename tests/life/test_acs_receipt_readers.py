"""@brief 正餐目标、商品呈现与收货读取方直接核对生产纯合同，不导入生活引擎。"""

from dataclasses import replace
import subprocess
import sys

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import ENVIRONMENT, at_time, menu_context, registered_world
from worldmock.life.models import ActivityState, AgentState, PurchaseItem, PurchaseOrderRequest, WorldState
from worldmock.life.schedule import (
    DayPlan, GOAL_TIME_TOLERANCE_SECONDS, PlanGoal, progress_day_plan, progress_receipt_day_plan,
)


def _meal_plan(registered=100, around=None):
    """@brief 手工登记单个真实用餐意图，不以活动代替收货。"""
    return DayPlan(day_end=86400, goals=(PlanGoal(intent_kind="用餐", goal="午饭", registered_at=registered,
                                               around_at=around),))


def _receipt(category="meal", order="order-a", **changes):
    """@brief 调度调用方已经确认归本人的收货载荷；付款人不决定用餐归属。"""
    return {"kind": "goods_received", "category": category, "order_id": order, "payer_id": 2, "quantity": 1, **changes}


@pytest.mark.parametrize("moment", [100, 101, 86399])
def test_meal_goal_progresses_from_its_own_received_order_including_registration_second(moment):
    plan = _meal_plan()
    actual = progress_receipt_day_plan(plan, _receipt(), moment)
    assert actual.goals[0].progressed_at == moment
    assert actual.goals[0].progress_order_id == "order-a"
    assert actual.goals[0].progress_activity_id is None
    assert actual.goals[0].registered_at == 100
    assert plan.goals[0].progressed_at is None
    assert DayPlan.model_validate_json(actual.model_dump_json()) == actual


@pytest.mark.parametrize("category", ["snack", "drink", "leisure", "daily"])
def test_only_meal_receipts_progress_meal_goals(category):
    plan = _meal_plan()
    assert progress_receipt_day_plan(plan, _receipt(category), 200) is plan


@pytest.mark.parametrize("moment", [99, 86400, 86401])
def test_meal_receipt_must_be_registered_and_received_within_its_day(moment):
    plan = _meal_plan()
    assert progress_receipt_day_plan(plan, _receipt(), moment) is plan


@pytest.mark.parametrize("offset,progressed", [
    (-GOAL_TIME_TOLERANCE_SECONDS - 1, False), (-GOAL_TIME_TOLERANCE_SECONDS, True),
    (0, True), (GOAL_TIME_TOLERANCE_SECONDS, True), (GOAL_TIME_TOLERANCE_SECONDS + 1, False),
])
def test_meal_receipt_around_time_uses_the_receipt_second_and_includes_both_window_edges(offset, progressed):
    plan = _meal_plan(registered=0, around=43200)
    moment = 43200 + offset
    actual = progress_receipt_day_plan(plan, _receipt(), moment)
    assert (actual.goals[0].progressed_at == moment) is progressed
    assert (actual.goals[0].progress_order_id == "order-a") is progressed


def test_receipt_evidence_cannot_progress_again_or_progress_another_intent():
    plan = _meal_plan()
    first = progress_receipt_day_plan(plan, _receipt(), 200)
    assert progress_receipt_day_plan(first, _receipt(), 201) is first
    moved = DayPlan(day_end=86400, goals=(
        PlanGoal(intent_kind="外出", goal="出门", registered_at=100, progressed_at=150,
                 progress_order_id="order-a"), plan.goals[0]))
    assert progress_receipt_day_plan(moved, _receipt(), 200) is moved
    other = progress_receipt_day_plan(moved, _receipt(order="order-b"), 200)
    assert [goal.progress_order_id for goal in other.goals] == ["order-a", "order-b"]
    nonmeal = DayPlan(day_end=86400, goals=(PlanGoal(intent_kind="娱乐", goal="看视频", registered_at=100),))
    assert progress_receipt_day_plan(nonmeal, _receipt(), 200) is nonmeal


@pytest.mark.parametrize("kind", ["delivery_arrival", "purchase", "given", "consumed"])
def test_unreceived_order_or_retired_evidence_cannot_progress_a_meal_goal(kind):
    plan = _meal_plan()
    assert progress_receipt_day_plan(plan, _receipt(kind=kind), 200) is plan
    activity = {"activity_id": "old-meal", "kind": "consume", "status": "completed", "started_at": 101}
    assert progress_day_plan(plan, activity, 101, 200, ()) is plan


@pytest.mark.parametrize("evidence", [
    {"progressed_at": 200}, {"progress_activity_id": "a"}, {"progress_order_id": "o"},
    {"progressed_at": 200, "progress_activity_id": "a", "progress_order_id": "o"},
    {"progressed_at": 99, "progress_order_id": "o"},
    {"progressed_at": 100, "progress_activity_id": "a"},
])
def test_goal_rejects_missing_duplicate_or_premature_evidence(evidence):
    with pytest.raises(ValidationError):
        PlanGoal(intent_kind="用餐", goal="午饭", registered_at=100, **evidence)


def test_order_evidence_is_unique_within_a_day_but_independent_of_activity_identity():
    meal = PlanGoal(intent_kind="用餐", goal="午饭", registered_at=100, progressed_at=200, progress_order_id="same")
    other = PlanGoal(intent_kind="外出", goal="外出", registered_at=100, progressed_at=200, progress_order_id="same")
    with pytest.raises(ValidationError):
        DayPlan(day_end=1000, goals=(meal, other))
    travelled = other.model_copy(update={"progress_order_id": None, "progress_activity_id": "same"})
    assert DayPlan(day_end=1000, goals=(meal, travelled)).goals == (meal, travelled)


def _purchase(semantics, state, goods="beef-noodles", receiver="agent:1", payer=1):
    """@brief 到店的一件真实商品，只通过严格购买接口发生收货。"""
    from worldmock.life.basket import purchase_order
    item = PurchaseItem(store_id="noodle-2", goods_id=goods, quantity=1, recipient_id=receiver)
    request = PurchaseOrderRequest(items=(item,), fulfillment="pickup", destination_id="noodle-2",
                                   app_id=None, payment_app_id=None)
    return purchase_order(state, semantics, payer, request)


def test_real_receipt_from_another_payer_only_progresses_the_recipient_meal_goal():
    semantics, state = registered_world("noodle-2", scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    state.agents[2].location_id = "noodle-2"
    now = state.current_time
    plans = {identity: DayPlan(day_end=state.end_time, goals=(
        PlanGoal(intent_kind="用餐", goal="午饭", registered_at=now),)) for identity in (1, 2)}
    outcome = _purchase(semantics, state, receiver="agent:2")
    assert len(outcome.receipts) == 1
    event = outcome.receipts[0]
    assert event.agent_id == 2 and event.data["payer_id"] == 1
    for identity, plan in tuple(plans.items()):
        if event.agent_id == identity:
            plans[identity] = progress_receipt_day_plan(plan, event.data, event.occurred_at)
    assert plans[1].goals[0].progressed_at is None
    assert plans[2].goals[0].progress_order_id == outcome.orders[0].order_id
    assert plans[2].goals[0].progress_activity_id is None
    assert outcome.state.activities == {}


def test_pending_delivery_and_contact_receipt_have_no_meal_goal_evidence():
    from worldmock.life.basket import purchase_order
    from worldmock.life.receipt import settle_receipt
    semantics, state = registered_world()
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", goods_id="beef-noodles", quantity=1,
        recipient_id="contact-colleague"),), fulfillment="delivery", destination_id="office",
        app_id="meituan", payment_app_id="wechat")
    pending = purchase_order(state, semantics, 1, request)
    plan = DayPlan(day_end=state.end_time, goals=(PlanGoal(intent_kind="用餐", goal="午饭",
                                                        registered_at=state.current_time),))
    assert pending.orders[0].status == "pending" and pending.receipts == ()
    assert progress_receipt_day_plan(plan, pending.orders[0].model_dump(mode="json"), state.current_time) is plan
    received = settle_receipt(at_time(pending.state, pending.orders[0].expected_at), semantics, pending.orders[0].order_id)
    assert received.order.status == "delivered" and received.order.received_at == received.state.current_time
    assert received.receipts == () and received.state.agents[1].body == state.agents[1].body


@pytest.mark.parametrize("field,target", [("inventory", AgentState), ("contact_holdings", WorldState)])
def test_world_and_agent_reject_retired_inventory_fields(field, target):
    _, state = registered_world()
    row = state.agents[1].model_dump() if target is AgentState else state.model_dump()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        target.model_validate({**row, field: {}})
    assert field not in target.model_fields


def _chosen_goods_context(location="store"):
    """@brief 呈现纯测试明确提供支付选择，未连接的人物选择不据此声称通过。"""
    context = menu_context(location)
    choices = {"meituan": "wechat", "eleme": "alipay"}
    bindings = [{**row, "payment_app_id": choices[row["app_id"]]}
                if row["kind"] == "supply" and row.get("app_id") else row for row in context.bindings]
    return replace(context, bindings=bindings)


def test_goods_tool_query_and_observation_have_no_holding_or_consume_path():
    from worldmock.life.callable_index import callable_index
    from worldmock.life.prompt_schemas import TARGET_KINDS, TOOL_LAYER_SCHEMA
    from worldmock.life.projection import ProjectionInputs, build_observation
    from worldmock.life.tool_layer import BINDING_KINDS, tool_layer
    from worldmock.life.vocabulary import LIFE_VERBS, WAYS, way_options
    from worldmock.life.world_query import QueryWorldRequest, attach_world_query, query_world
    from jsonschema import Draft202012Validator
    context = _chosen_goods_context()
    layer = tool_layer(context, callable_index(context))
    Draft202012Validator(TOOL_LAYER_SCHEMA).validate(layer)
    assert "holding" not in BINDING_KINDS and "holding" not in TARGET_KINDS and "consume" not in LIFE_VERBS
    for way in ("食用", "使用", "给予", "吃", "吃饭", "用餐", "进食", "送", "给", "赠送"):
        assert way not in WAYS and not way_options(way)
    assert {row["category"] for row in layer["callable_index"]["goods"]} == {"正餐", "零食", "饮品", "休闲用品", "日常用品"}
    products = [row for row in layer["targets"] if row["kind"] == "product"]
    assert products and all({action["action"] for action in row["actions"]} == {"obtain"} for row in products)
    assert all("持有" not in row["state"] for row in products)
    assert any(row["state"].startswith("休闲用品") and "千卡" not in row["state"] for row in products)
    assert any(row["state"] == "日常用品" for row in products)
    observation = {"catalog_bindings": [], "tool_layer": layer, "home_location_id": "home",
                   "locations": [{"location_id": item.location_id, "name": item.name} for item in context.scenario.locations]}
    attach_world_query(observation, context)
    query = QueryWorldRequest.model_validate({"name": None, "way": None, "category": "日常用品",
        "app": None, "available_only": False, "offset": 0})
    result = query_world(observation, query)
    assert result["total_count"] == 1 and result["matches"][0]["state"] == "日常用品"
    projected = build_observation(ProjectionInputs(context.state, context.scenario, context.state.agents[1],
        extras={"activity_rows": {"control": [], "pending": []}, "interaction_menu": context.menu,
                "actions": list(context.semantics.catalog.actions.values()),
                "contents": list(context.semantics.catalog.contents.values())}))
    for retired in ("inventory", "available_inventory", "held_foods"):
        assert retired not in projected
    assert all(row["kind"] != "holding" for row in projected["interaction_bindings"])


@pytest.mark.parametrize("status,ending", [("completed", "完成"), ("cancelled", "提前结束"), ("failed", "失败")])
def test_conclusion_projects_only_real_activity_ending_without_item_changes(status, ending):
    from worldmock.life.activity_evidence import conclusion_outcome
    activity = ActivityState(activity_id="a", agent_id=1, location_id="home", kind="wait", status=status,
        started_at=100, ended_at=200, planned_end_at=200, last_settled_at=200, elapsed_seconds=100,
        planned_duration_seconds=100, origin_action_id="request", origin_decision_id="decision",
        origin_request_event_id="event")
    assert conclusion_outcome(activity, "等待") == {"activity": "等待", "outcome": ending}


def test_goods_result_receipt_progress_and_contact_received_use_real_goods_without_given():
    from worldmock.life.contact_exchange import _received
    from worldmock.life.progress_layer import OPEN_ORDER_STATUSES, _delivery_records
    from worldmock.life.result_projection import public_result
    from worldmock.life.settled_text import experience_sentence, purchase_sentence
    from worldmock.footprints.models import Event
    semantics, state = registered_world("noodle-2")
    outcome = _purchase(semantics, state)
    order = outcome.orders[0].model_dump(mode="json")
    result = {"occurred_at": state.current_time, "status": "completed", "message": "Purchase accepted",
              "data": {"orders": [order]}}
    public = public_result(result, semantics.scenario)
    assert public["details"]["purchases"][0]["product"] == "牛肉面"
    assert public["details"]["purchases"][0]["quantity"] == "1 份"
    assert "given" not in public["details"]
    assert OPEN_ORDER_STATUSES == {"pending", "ready_for_pickup"}
    context = replace(_chosen_goods_context("noodle-2"), state=outcome.state)
    event = Event(event_id="arrived", event_seq=1, run_id=state.run_id, event_type="environment_event",
                  occurred_at=state.current_time, agent_id=1, source_kind="environment", visibility="agent",
                  recorded_at="2026-01-05T00:00:00Z", wall_elapsed_ms=0, state_version_before=1, state_version_after=2,
                  data={"kind": "delivery_arrival", "scheduled_id": "delivery", "orders": [order]})
    [record] = _delivery_records(context, event)
    assert record.record["status"] == "delivered" and record.record["message"] == "已送到二味小炒"
    assert "牛肉面" in purchase_sentence(context, order, None)
    fact = {"kind": "received", "occurred_at": state.current_time, "goods_id": "beef-noodles", "quantity": 1}
    assert "收到牛肉面" in experience_sentence(context, fact)
    assert _received(outcome.state, semantics, "contact-colleague") == ()


def test_pure_receipt_reader_import_closure_has_no_life_engine_or_retired_module():
    modules = ("activity_control", "activity_evidence", "activity_validation", "admission", "callable_index",
        "contact_exchange", "control_items", "discovery", "guidance", "interaction_menu", "memory_layer",
        "name_resolution", "narration", "object_labels", "progress_layer", "prompt_schemas", "request_linking",
        "result_projection", "routing", "same_moment", "schedule", "session_actions", "settled_text", "tool_layer",
        "vocabulary", "world_query", "world_ops", "day_planning")
    code = ("import importlib, sys\n" +
        f"for name in {modules!r}: importlib.import_module('worldmock.life.'+name)\n" +
        "assert 'worldmock.life.engine' not in sys.modules\n" +
        "assert importlib.util.find_spec('worldmock.life.giving') is None\n")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tool", ["report_status", "pause_activity", "conclude_activity"])
def test_control_errand_reader_has_exact_seven_fields_and_rejects_retired_outcome(tool):
    from worldmock.errors import IntegrityError
    from worldmock.life.control_items import control_errands
    from worldmock.life.models import ErrandChange
    entry = {"change": "新增", "name": "补一份午饭", "item": "牛肉面", "quantity": 1,
             "memo": None, "remind_time": None, "reason": None}
    assert control_errands(tool, {"items": [], "errands": [entry]}) == (entry,)
    assert len(control_errands(tool, {"items": [], "errands": [entry]})[0]) == 7
    assert set(ErrandChange.model_fields) == set(entry)
    for outcome in ("取得", "吃掉", None):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            ErrandChange.model_validate({**entry, "outcome": outcome})
        with pytest.raises(IntegrityError, match="unknown fields"):
            control_errands(tool, {"items": [], "errands": [{**entry, "outcome": outcome}]})


def test_contact_received_uses_actual_received_time_and_registered_goods_only():
    from worldmock.life.basket import purchase_order
    from worldmock.life.receipt import settle_receipt
    from worldmock.life.contact_exchange import _received
    from worldmock.life.readable import clock_phrase
    semantics, state = registered_world()
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", goods_id="beef-noodles", quantity=1,
        recipient_id="contact-colleague"),), fulfillment="delivery", destination_id="office",
        app_id="meituan", payment_app_id="wechat")
    placed = purchase_order(state, semantics, 1, request)
    assert _received(placed.state, semantics, "contact-colleague") == ()
    order = placed.orders[0]
    now = order.expected_at + 120
    actual = settle_receipt(at_time(placed.state, now), semantics, order.order_id)
    [row] = _received(actual.state, semantics, "contact-colleague")
    assert row.item == "牛肉面" and row.quantity == "1 份"
    assert row.time == clock_phrase(now, now, semantics.scenario.timezone)
    assert actual.receipts == ()


def _with_receipt_obligations(state, goods_id="beef-noodles"):
    """@brief 各模拟人物各自登记同一商品事务与午饭目标，便于核对实际收货所有权。"""
    from worldmock.life.errand_state import ErrandState
    changed = state.model_copy(deep=True)
    for identity, agent in changed.agents.items():
        errand = ErrandState(errand_id=f"errand-{identity}", name="补一份午饭", product_id=goods_id, quantity=1,
            progress=0, memo=None, registered_at=state.current_time, remind_at=None, reminded_at=None,
            origin_action_id="remember", origin_event_id="remembered")
        plan = DayPlan(day_end=state.end_time, goals=(PlanGoal(intent_kind="用餐", goal="午饭",
            registered_at=state.current_time),))
        changed.agents[identity] = agent.model_copy(update={"errands": {errand.errand_id: errand}, "day_plan": plan})
    return changed


def test_actual_self_receipt_completes_its_errand_and_meal_goal_once_with_one_goal_pulse():
    from worldmock.life import errands
    from worldmock.life.day_planning import settle_receipt_goals
    semantics, state = registered_world("noodle-2")
    state = _with_receipt_obligations(state)
    placed = _purchase(semantics, state)
    mood = placed.state.agents[1].body.mood_valence
    goal_state = settle_receipt_goals(placed.state, placed.receipts)
    actual, completed = errands.settle(goal_state, placed.receipts)
    assert actual.agents[1].errands == {}
    [event] = completed
    assert event.data["kind"] == "errand_completed" and event.agent_id == 1
    assert event.caused_by == [placed.receipts[0].event_id]
    assert actual.agents[1].day_plan.goals[0].progress_order_id == placed.orders[0].order_id
    assert actual.agents[1].body.mood_valence == pytest.approx(mood + 0.05)
    repeated = settle_receipt_goals(actual, placed.receipts)
    repeated, no_events = errands.settle(repeated, placed.receipts)
    assert repeated == actual and no_events == []
    assert repeated.agents[1].body.mood_valence == actual.agents[1].body.mood_valence
    assert state.agents[1].errands and state.agents[1].day_plan.goals[0].progressed_at is None


def test_another_payer_actual_receipt_completes_only_the_recipients_errand_and_goal():
    from worldmock.life import errands
    from worldmock.life.day_planning import settle_receipt_goals
    semantics, state = registered_world("noodle-2", scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    state.agents[2].location_id = "noodle-2"
    state = _with_receipt_obligations(state)
    placed = _purchase(semantics, state, receiver="agent:1", payer=2)
    assert placed.receipts[0].agent_id == 1 and placed.receipts[0].data["payer_id"] == 2
    goal_state = settle_receipt_goals(placed.state, placed.receipts)
    actual, completed = errands.settle(goal_state, placed.receipts)
    assert actual.agents[1].errands == {} and len(completed) == 1 and completed[0].agent_id == 1
    assert actual.agents[1].day_plan.goals[0].progress_order_id == placed.orders[0].order_id
    assert actual.agents[1].body.mood_valence == pytest.approx(placed.state.agents[1].body.mood_valence + 0.05)
    assert actual.agents[2].errands == state.agents[2].errands
    assert actual.agents[2].day_plan == state.agents[2].day_plan
    assert actual.agents[2].body == placed.state.agents[2].body


def test_actual_contact_delivery_never_completes_buyers_errand_or_meal_goal():
    from worldmock.life import errands
    from worldmock.life.basket import purchase_order
    from worldmock.life.day_planning import settle_receipt_goals
    from worldmock.life.receipt import settle_receipt
    semantics, state = registered_world()
    state = _with_receipt_obligations(state)
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", goods_id="beef-noodles", quantity=1,
        recipient_id="contact-colleague"),), fulfillment="delivery", destination_id="office",
        app_id="meituan", payment_app_id="wechat")
    pending = purchase_order(state, semantics, 1, request)
    pending_goals = settle_receipt_goals(pending.state, pending.receipts)
    unchanged, events = errands.settle(pending_goals, pending.receipts)
    assert events == [] and unchanged.agents[1].day_plan == state.agents[1].day_plan
    order = pending.orders[0]
    received = settle_receipt(at_time(pending.state, order.expected_at), semantics, order.order_id)
    goals = settle_receipt_goals(received.state, received.receipts)
    actual, events = errands.settle(goals, received.receipts)
    assert received.order.status == "delivered" and received.receipts == () and events == []
    assert actual.agents[1].errands == state.agents[1].errands
    assert actual.agents[1].day_plan == state.agents[1].day_plan
    assert actual.agents[1].body == received.state.agents[1].body


@pytest.mark.parametrize("goods_id", ["chips", "lemon-tea", "playing-cards", "tissue"])
def test_actual_nonmeal_receipt_completes_goods_errand_without_a_meal_goal_pulse(goods_id):
    from worldmock.life import errands
    from worldmock.life.basket import purchase_order
    from worldmock.life.day_planning import settle_receipt_goals
    semantics, state = registered_world("store")
    state = _with_receipt_obligations(state, goods_id)
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="store", goods_id=goods_id, quantity=1,
        recipient_id="agent:1"),), fulfillment="pickup", destination_id="store", app_id=None, payment_app_id=None)
    placed = purchase_order(state, semantics, 1, request)
    goal_state = settle_receipt_goals(placed.state, placed.receipts)
    assert goal_state == placed.state
    actual, completed = errands.settle(goal_state, placed.receipts)
    assert actual.agents[1].errands == {} and completed[0].data["kind"] == "errand_completed"
    assert actual.agents[1].day_plan.goals[0].progressed_at is None
    assert actual.agents[1].body == placed.state.agents[1].body


def test_actual_receipt_without_a_day_plan_changes_no_goal_or_goal_mood():
    from worldmock.life.day_planning import settle_receipt_goals
    semantics, state = registered_world("noodle-2")
    assert state.agents[1].day_plan is None
    placed = _purchase(semantics, state)
    actual = settle_receipt_goals(placed.state, placed.receipts)
    assert actual == placed.state and actual.agents[1].day_plan is None
    assert actual.agents[1].body == placed.state.agents[1].body
