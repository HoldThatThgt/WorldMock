"""@brief 公共接口通过真实目录验证拒绝、付款守恒与实际内容证据。"""

import pytest
from pydantic import ValidationError

from tests.life.action_contract_world import contract_world
from tests.life.action_worlds import action_world, begin
from tests.life.purchase_worlds import registered_world
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.activity_control import ending_phrase
from worldmock.life.app_push import issue_app_push, release_app_pushes
from worldmock.life.basket import delivery_expected_at, plan_delivery, purchase_order
from worldmock.life.commerce import deliver_order
from worldmock.life.models import ActionResult, ActivityState, PurchaseItem, PurchaseOrderRequest, StartActivityRequest
from worldmock.life.name_resolution import StartItem, Target, fill_in, selection_row
from worldmock.life.order_routing import merged_result, undeliverable
from worldmock.life.routing import RoutingNames, combination_issues
from worldmock.life.settled_text import activity_sentence
from worldmock.life.world_query import QueryWorldRequest, attach_world_query, query_argument_report
from worldmock.scheduling.events import ScheduledEvent


def _activity(state, **updates):
    """@brief 创建真实原生等待，不填已退休target_id。"""
    return ActivityState(activity_id="evidence", agent_id=1, location_id="home", kind="wait", status="active",
        started_at=state.current_time, planned_end_at=state.current_time + 300,
        last_settled_at=state.current_time, elapsed_seconds=0, planned_duration_seconds=300,
        origin_action_id="action", origin_decision_id="decision", origin_request_event_id="request", **updates)


def test_failed_activity_has_a_failed_terminal_phrase():
    _, state = registered_world()
    assert ending_phrase(_activity(state).model_copy(update={"status": "failed"})) == "失败"


def test_push_requires_current_session_and_release_without_session_preserves_state():
    semantics, state = registered_world()
    assert state.sessions == {}
    event = ScheduledEvent(scheduled_id="push", kind="app_push", due_at=state.current_time,
                           sequence=1, agent_id=1, data={})
    with pytest.raises(IntegrityError, match="active session"):
        issue_app_push(state, event, semantics.catalog, semantics.scenario)
    assert release_app_pushes(state, 1, "c-short") is state
    assert state.sessions == {}


def test_delivery_quote_and_order_use_the_same_registered_route_and_clock():
    semantics, state = registered_world()
    plan = plan_delivery(semantics, "noodle-1", "home", state.current_time)
    assert delivery_expected_at(semantics, "noodle-1", "home", state.current_time) == plan.expected_at
    assert plan.expected_at > state.current_time


def test_order_cannot_be_delivered_before_its_due_time():
    semantics, state = registered_world()
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", goods_id="beef-noodles",
        quantity=1, recipient_id="agent:1"),), fulfillment="delivery", destination_id="home",
        app_id="meituan", payment_app_id="wechat")
    purchased = purchase_order(state, semantics, 1, request)
    before = purchased.state.model_dump()
    with pytest.raises(BusinessRuleError, match="^DELIVERY_NOT_DUE$"):
        deliver_order(purchased.state, semantics, purchased.orders[0].order_id)
    assert purchased.state.model_dump() == before




def test_unlisted_location_selection_keeps_real_travel_mode():
    target = Target("地点：办公室", "location", "office", (("travel", "步行"),), (None,), (None,), False, {"kind": "location", "object_id": "office", "actions": [
        {"verb": "travel", "label": "步行", "canonical_action": "travel_to", "travel_mode": "walking"}]})
    row = selection_row(target, "travel", "步行")
    assert row["object_id"] == "office"
    assert row["selected_action"]["canonical_action"] == "travel_to"
    assert row["selected_action"]["travel_mode"] == "walking"


def test_wrong_way_candidate_uses_an_available_declared_offer():
    target = Target("地点：办公室", "location", "office", (("travel", "驾车"), ("travel", "步行")),
                    ("No car is held.", None), (None, "约 20 分钟"), False)
    candidate = fill_in(target, StartItem("办公室", "进行", None, None, None))
    assert candidate.tool == "start_activity"
    assert candidate.arguments["items"] == [{"target": "地点：办公室", "way": "步行", "app": None,
                                               "to": None, "pay_app": None}]


def test_split_order_result_conserves_payment_and_message_link():
    def result(identity, price, changes):
        return ActionResult(action_id="purchase", agent_id=1, status="completed", occurred_at=1, message="Purchased",
                            data={"orders": [identity], "total_price_fen": price, "resource_changes": changes})
    first = result("first", 1200, {"agent:1": -1200, "store:first": 1200})
    second = result("second", 800, {"agent:1": -800, "store:second": 800})
    merged = merged_result([first, second], "消息：午餐请求")
    assert merged.data == {"orders": ["first", "second"], "total_price_fen": 2000,
        "resource_changes": {"agent:1": -2000, "store:first": 1200, "store:second": 800}, "answering": "消息：午餐请求"}
    assert "answering" not in first.data and "inventory_changes" not in merged.data


def test_unavailable_recipient_receives_named_actionable_refusal():
    semantics, state = registered_world()
    error = undeliverable(state, semantics.scenario, 1, "agent:1")
    assert str(error) == "ACTIVITY_PRECONDITION_UNMET" and state.agents[1].name in error.public_reason
    assert "Wait until" in error.suggested["next_step"]


def test_same_target_and_recipient_are_rejected_as_a_repeated_activity():
    request = StartActivityRequest.model_validate({"items": [{"target": "自己", "way": "等待", "app": None,
                                                            "to": None, "pay_app": None}], "answering": None})
    request = request.model_copy(update={"items": (request.items[0], request.items[0])})
    issues = combination_issues(request, RoutingNames({}, {}, {}, {}, {}, {}, "agent:1"))
    repeated = [issue for issue in issues if "repeated target" in issue.reason]
    assert len(repeated) == 1 and repeated[0].index == 1 and repeated[0].field == "target"


def test_action_sentence_refuses_to_invent_progress_without_authenticated_evidence():
    semantics, state = action_world()
    state, activity = begin(semantics, state)
    with pytest.raises(IntegrityError, match="authenticated progress evidence"):
        activity_sentence(contract_world()[0], activity)


def test_invalid_query_report_keeps_independent_parameter_errors():
    arguments = {"name": 7, "way": "未声明方式", "category": None, "app": None, "available_only": True, "offset": 0}
    with pytest.raises(ValidationError) as raised:
        QueryWorldRequest.model_validate(arguments)
    context, observation = contract_world()
    attach_world_query(observation, context)
    report = query_argument_report(observation, raised.value, arguments)
    assert {item.path for item in report.items} == {("name",), ("way",)}
    assert next(item.written for item in report.items if item.path == ("name",)) == 7
    assert next(item.written for item in report.items if item.path == ("way",)) == "未声明方式"
