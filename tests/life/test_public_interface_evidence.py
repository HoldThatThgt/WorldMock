"""@brief 公共接口通过真实数据验证拒绝、事实守恒与可追溯呈现。"""

import json

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import menu_context, registered_world
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.activity_control import ending_phrase
from worldmock.life.activity_evidence import dedupe_revealed_cues
from worldmock.life.app_push import issue_app_push, release_app_pushes
from worldmock.life.basket import (delivery_expected_at, meal_duration, meal_portion, plan_delivery,
                                   purchase_order, validate_meal_inventory)
from worldmock.life.candidates import Exclusion
from worldmock.life.callable_index import IndexSources, callable_index
from worldmock.life.commerce import deliver_order
from worldmock.life.models import (ActionResult, ActivityState, ConsumeItem, ConsumeRequest, PurchaseItem,
                                   PurchaseOrderRequest, StartActivityRequest)
from worldmock.life.motivation import classify_action_event
from worldmock.life.name_resolution import Target, fill_in, selection_row
from worldmock.life.order_routing import merged_result, undeliverable
from worldmock.life.projection import ProjectionInputs, interaction_bindings
from worldmock.life.purchase_offers import Offer, order_key
from worldmock.life.retrieval import place_kinds
from worldmock.life.routing import RoutingNames, combination_issues
from worldmock.life.settled_text import activity_sentence
from worldmock.life.tool_layer import tool_layer
from worldmock.life.world_query import QueryWorldRequest, query_argument_report
from worldmock.scheduling.events import ScheduledEvent


def _activity(state, kind="wait", **updates):
    """@brief 使用真实活动模型，结束状态与呈现证据由各用例声明。"""
    return ActivityState(activity_id="evidence", agent_id=1, location_id="home", kind=kind,
                         status="active", started_at=state.current_time,
                         planned_end_at=state.current_time + 300, last_settled_at=state.current_time,
                         elapsed_seconds=0, planned_duration_seconds=300, origin_action_id="action",
                         origin_decision_id="decision", origin_request_event_id="request", **updates)


def _consume(mode="eat", quantity=1):
    """@brief 持有食品的请求保持现有严格合同。"""
    return ConsumeRequest(items=(ConsumeItem(product_id="beef-noodles", quantity=quantity),),
                          use_mode=mode, duration_seconds=1200)


def test_failed_activity_has_a_failed_terminal_phrase():
    _, state = registered_world()
    activity = _activity(state).model_copy(update={"status": "failed"})
    assert ending_phrase(activity) == "失败"


def test_revealed_cues_keep_only_new_interval_facts():
    _, state = registered_world()
    current = {"start_seconds": 30, "text": "current"}
    later = {"start_seconds": 45, "text": "later"}
    activity = _activity(state, data={"revealed": {"position_before_seconds": 30, "cues": [
        {"start_seconds": 29, "text": "already read"}, current, later, {}, "invalid"]}})
    dedupe_revealed_cues(activity)
    assert activity.data["revealed"]["cues"] == [current, later]


def test_push_requires_current_session_and_release_without_session_preserves_state():
    semantics, state = registered_world()
    assert state.sessions == {}
    event = ScheduledEvent(scheduled_id="push", kind="app_push", due_at=state.current_time,
                           sequence=1, agent_id=1, data={})
    with pytest.raises(IntegrityError, match="active session"):
        issue_app_push(state, event, semantics.catalog)
    assert release_app_pushes(state, 1, "c-short") is state
    assert state.sessions == {}


def test_delivery_quote_and_order_use_the_same_registered_route_and_clock():
    semantics, state = registered_world()
    plan = plan_delivery(semantics, "noodle-1", "home", state.current_time)
    assert delivery_expected_at(semantics, "noodle-1", "home", state.current_time) == plan.expected_at
    assert plan.expected_at > state.current_time


def test_non_food_use_mode_is_rejected_by_both_meal_interfaces():
    semantics, _ = registered_world()
    with pytest.raises(BusinessRuleError, match="^PRODUCT_USE_MODE_NOT_ALLOWED$"):
        meal_portion(semantics.catalog.foods["beef-noodles"], "use", 1)
    with pytest.raises(BusinessRuleError, match="^PRODUCT_USE_MODE_NOT_ALLOWED$"):
        meal_duration(semantics.catalog.foods, _consume("use"))


def test_missing_meal_inventory_is_rejected_without_changing_holdings():
    _, state = registered_world()
    agent = state.agents[1].model_copy(update={"inventory": {"home": {"beef-noodles": 0.5}}}, deep=True)
    before = agent.model_dump()
    with pytest.raises(BusinessRuleError, match="^PRODUCT_NOT_HELD$"):
        validate_meal_inventory(agent, _consume())
    assert agent.model_dump() == before


def test_excluded_candidate_reports_exact_object_kind_and_reason():
    excluded = Exclusion("office-exercise", "lifestyle", "ELIGIBILITY_LOCATION_KIND_MISMATCH")
    assert excluded.as_row() == {"object_id": "office-exercise", "kind": "lifestyle",
                                 "reason": "ELIGIBILITY_LOCATION_KIND_MISMATCH"}


def test_order_cannot_be_delivered_before_its_due_time():
    semantics, state = registered_world()
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", food_id="beef-noodles",
                                                       quantity=1, recipient_id="agent:1"),),
                                   fulfillment="delivery", destination_id="home", app_id="meituan")
    purchased = purchase_order(state, semantics, 1, request)
    order_id = next(iter(purchased.state.deliveries))
    before = purchased.state.model_dump()
    with pytest.raises(BusinessRuleError, match="^DELIVERY_NOT_DUE$"):
        deliver_order(purchased.state, order_id)
    assert purchased.state.model_dump() == before


@pytest.mark.parametrize("arguments", [{}, {"items": None}, {"items": []}, {"items": [{}, {}, {}]}, None])
def test_malformed_activity_does_not_create_habit_evidence(arguments):
    assert classify_action_event("start_activity", arguments) == ()


def test_unlisted_location_selection_keeps_real_travel_mode():
    target = Target("地点：办公室", "location", "office", (("travel", "步行"),), (None,), (None,), False)
    row = selection_row(target, "travel", "步行")
    assert row["object_id"] == "office"
    assert row["selected_action"]["canonical_action"] == "travel_to"
    assert row["selected_action"]["travel_mode"] == "walking"


def test_wrong_way_candidate_uses_an_available_declared_offer():
    target = Target("地点：办公室", "location", "office", (("travel", "驾车"), ("travel", "步行")),
                    ("No car is held.", None), (None, "约 20 分钟"), False)
    candidate = fill_in(target, "interact", "打开", "办公室")
    assert candidate.tool == "start_activity"
    assert candidate.arguments["items"] == [{"target": "地点：办公室", "way": "步行", "from": None, "to": None}]
    assert candidate.note == "About 20 minutes."


def test_split_order_result_conserves_payment_inventory_and_message_link():
    def result(identity, price, changes):
        return ActionResult(action_id="purchase", agent_id=1, status="completed", occurred_at=1, message="Purchased",
                            data={"orders": [identity], "total_price_fen": price,
                                  "inventory_changes": [{"food_id": identity}], "resource_changes": changes})

    first = result("first", 1200, {"agent:1": -1200, "store:first": 1200})
    second = result("second", 800, {"agent:1": -800, "store:second": 800})
    merged = merged_result([first, second], "消息：午餐请求")
    assert merged.data == {"orders": ["first", "second"], "total_price_fen": 2000,
                           "inventory_changes": [{"food_id": "first"}, {"food_id": "second"}],
                           "resource_changes": {"agent:1": -2000, "store:first": 1200, "store:second": 800},
                           "answering": "消息：午餐请求"}
    assert "answering" not in first.data


def test_unavailable_recipient_receives_named_actionable_refusal():
    semantics, state = registered_world()
    error = undeliverable(state, semantics.scenario, 1, "agent:1")
    assert str(error) == "ACTIVITY_PRECONDITION_UNMET"
    assert state.agents[1].name in error.public_reason
    assert "Receive the item at your current location" in error.suggested["next_step"]


def test_ambiguous_visible_names_are_rejected_before_binding_selection():
    context = menu_context()
    food = context.semantics.catalog.foods["beef-noodles"]
    row = {"kind": "supply", "name": food.name, "object_id": "noodle-1:beef-noodles",
           "store_id": "noodle-1", "food_id": food.food_id, "app_ids": ["meituan"],
           "location_name": context.semantics.catalog.locations["noodle-1"].display_name}
    inputs = ProjectionInputs(context.state, context.scenario, context.state.agents[1],
                              extras={"product_rows": [row, dict(row)], "interaction_menu": context.menu})
    with pytest.raises(ValueError, match="labels must be unique"):
        interaction_bindings(inputs)


def test_purchase_preference_rejects_undeclared_sort_policy():
    semantics, _ = registered_world()
    offer = Offer("noodle-1", semantics.catalog.foods["beef-noodles"], "到店", 1200, 1800)
    with pytest.raises(ValueError, match="purchase preference must be"):
        order_key(offer, "arbitrary")


def test_location_activity_kinds_are_exact_immutable_facts(tmp_path):
    document = {"locations": [{"location_id": "home", "location_kinds": ["home"]},
                              {"location_id": "office", "location_kinds": ["office", "venue"]}]}
    (tmp_path / "locations.json").write_text(json.dumps(document), encoding="utf-8")
    places = place_kinds(tmp_path, "locations.json")
    assert dict(places) == {"home": frozenset({"home"}), "office": frozenset({"office", "venue"})}
    with pytest.raises(TypeError):
        places["home"] = frozenset({"office"})


def test_same_target_and_recipient_are_rejected_as_a_repeated_activity():
    request = StartActivityRequest.model_validate({"items": [
        {"target": "自己", "way": "等待", "from": None, "to": None}], "answering": None, "prefer": None})
    request = request.model_copy(update={"items": (request.items[0], request.items[0])})
    issues = combination_issues(request, RoutingNames({"自己": "self"}, {}, {}, self_id="agent:1"))
    repeated = [issue for issue in issues if "repeated target" in issue.reason]
    assert len(repeated) == 1 and repeated[0].index == 1 and repeated[0].field == "target"


def test_app_sentence_refuses_to_invent_progress_without_authenticated_evidence():
    context = menu_context()
    activity = _activity(context.state, "use_app", target_id="douyin")
    with pytest.raises(IntegrityError, match="authenticated progress evidence"):
        activity_sentence(context, activity)


def test_invalid_query_report_keeps_independent_parameter_errors():
    arguments = {"name": 7, "way": "未声明方式", "category": None, "from": None,
                 "available_only": True, "offset": 0}
    with pytest.raises(ValidationError) as raised:
        QueryWorldRequest.model_validate(arguments)
    context = menu_context()
    observation = {"tool_layer": tool_layer(context, callable_index(context, IndexSources()))}
    report = query_argument_report(observation, raised.value, arguments)
    assert {item.path for item in report.items} == {("name",), ("way",)}
    assert next(item.written for item in report.items if item.path == ("name",)) == 7
    assert next(item.written for item in report.items if item.path == ("way",)) == "未声明方式"
