"""实际动作、对象、独立付款与无分类内容的公开证据。"""

import json

import pytest

from worldmock.evaluation.adapters import parse_world_event
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.public_evidence import worldmock_activity_action, worldmock_public_evidence
from tests.integration.evaluation.test_adapters import _public_event


def action_events(action="interact", content="real-content", app="real-app", **extra):
    data = {"action": action, "content_id": content, "app_id": app, **extra}
    kind = "travel" if action in {"walk", "drive", "take_taxi", "take_bus"} else action
    snapshot = {"activity_id": "actual-activity", "agent_id": 1, "kind": kind,
                "status": "active", "data": data, "travel_mode": extra.get("travel_mode")}
    request = {"items": [{"action": action, "target": "TITLE_CANARY"}]}
    rows = [_public_event(1, "action_requested", {"tool_name": "start_activity", "request": request}, "actual"),
            _public_event(2, "action_result", {"status": "in_progress", "data": {"activities": [snapshot]}}, "actual")]
    return [parse_world_event(row) for row in rows]


@pytest.mark.parametrize("content,object_type,key", [("real-content", "content", "real-content"),
                                                       (None, "app", "real-app")])
def test_interaction_reads_actual_object_and_never_infers_category_or_device(content, object_type, key):
    rows = worldmock_public_evidence(action_events(content=content), [])
    assert {(row.action, row.interaction, row.object_type, row.object_key) for row in rows} == {
        ("interact", "interact", object_type, key)}
    assert all(row.attributes == {} for row in rows)
    assert "TITLE_CANARY" not in json.dumps([row.field_values() for row in rows])


@pytest.mark.parametrize("action,mode,public", [("walk", "walking", "walking"),
    ("drive", "driving", "driving"), ("take_taxi", "taxi", "driving"), ("take_bus", "bus", "transit")])
def test_each_real_travel_action_uses_the_actual_trip_and_public_mode(action, mode, public):
    rows = worldmock_public_evidence(action_events(action, trip_id="real-trip", travel_mode=mode), [])
    assert all(row.action == action and row.interaction == "travel" and row.object_key == "real-trip" for row in rows)
    assert all(row.attributes == {"travel_mode": public} for row in rows)


def test_sleep_start_has_no_additional_public_point_fact():
    assert worldmock_public_evidence(action_events("sleep"), []) == ()


@pytest.mark.parametrize("kind", ["app_touch", "payment_notification", "balance_decreased"])
def test_system_and_removed_subsystem_events_do_not_create_public_behavior(kind):
    event = parse_world_event(_public_event(1, "environment_event", {"kind": kind}))
    assert worldmock_public_evidence([event], []) == ()


def test_purchase_payments_are_only_real_independent_facts_even_without_payment_app():
    event = parse_world_event(_public_event(1, "environment_event", {"kind": "purchase_payment",
        "activity_id": "real-purchase", "order_ids": ["order-a", "order-b"], "amount_fen": 1200}))
    rows = worldmock_public_evidence([event], [])
    assert {(row.object_key, row.interaction, row.state) for row in rows} == {
        ("order-a", "pay", "paid"), ("order-b", "pay", "paid")}


def test_delivery_and_receipts_remain_distinct_actual_order_facts():
    rows = [_public_event(1, "environment_event", {"kind": "delivery_arrival", "orders": [
        {"order_id": "order-a", "status": "delivered", "fulfillment": "delivery"},
        {"order_id": "order-b", "status": "ready_for_pickup", "fulfillment": "station_pickup"}]}),
        _public_event(2, "environment_event", {"kind": "goods_received", "order_id": "order-a", "category": "meal"}),
        _public_event(3, "environment_event", {"kind": "goods_received", "order_id": "order-b", "category": "goods"})]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in rows], [])
    assert {(row.object_key, row.interaction, row.state) for row in evidence} == {
        ("order-a", "deliver", "delivered"), ("order-b", "notify", "ready_for_pickup"),
        ("order-a", "receive", "received"), ("order-b", "receive", "received")}
    assert [row.attributes["goods_category"] for row in evidence if row.interaction == "receive"] == ["meal", "goods"]


@pytest.mark.parametrize("kind", ["wait", "action"])
def test_retired_activity_kind_is_rejected_before_object_inference(kind):
    with pytest.raises(EvaluationError, match="Activity snapshot uses a retired activity kind"):
        worldmock_activity_action(kind, {})


@pytest.mark.parametrize("kind,action", [("interact", "interact"), ("purchase", "purchase"),
    ("sleep", "sleep"), ("travel", "walk"), ("travel", "drive"), ("travel", "take_taxi"), ("travel", "take_bus")])
def test_activity_action_is_taken_directly_from_the_current_closed_contract(kind, action):
    assert worldmock_activity_action(kind, {"action": action}) == action
    with pytest.raises(EvaluationError, match="actual action"):
        worldmock_activity_action(kind, {})


def test_unknown_activity_kind_and_wrong_actual_action_are_rejected():
    with pytest.raises(EvaluationError, match="outside the current closed set"):
        worldmock_activity_action("invented", {"action": "interact"})
    with pytest.raises(EvaluationError, match="disagrees"):
        worldmock_activity_action("travel", {"action": "interact"})


@pytest.mark.parametrize("kind", ["mail_arrival", "work_content_completed"])
def test_retired_subsystem_facts_are_rejected_before_public_projection(kind):
    """退役事件在真实 Event 读取边界拒绝，不能成为隐式公开行为。"""
    with pytest.raises(ValueError, match="Environment event uses a retired kind"):
        _public_event(1, "environment_event", {"kind": kind})


def test_actual_system_callback_effect_does_not_create_public_behavior():
    """有效回调钳位由系统持有，不能推断用户公开动作。"""
    from worldmock.footprints.models import Event
    event = Event.model_validate({**_public_event(1, "environment_event", {"kind": "test_system_fact"}),
        "visibility": "system", "caused_by": ["real-trigger"], "data": {
            "kind": "callback_effect", "type": "state_delta", "rule_id": "actual-rule",
            "state": "energy", "delta": -1, "before": 100, "after": 99}})
    assert worldmock_public_evidence([event], []) == ()


@pytest.mark.parametrize("status", ["pending", "cancelled", "failed", "delivered"])
def test_order_snapshots_do_not_imply_payment_or_receipt(status):
    """接受、取消、失败和配送状态只有实际订单点事实，没有推断付款或收货。"""
    rows = [_public_event(1, "action_requested", {"tool_name": "start_activity", "request": {
        "items": [{"action": "purchase", "target": "实际商品"}]}}, "purchase-action"),
        _public_event(2, "action_result", {"status": "completed", "data": {"orders": [
            {"order_id": "actual-order", "status": status, "fulfillment": "pickup"}]}}, "purchase-action")]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in rows], [])
    assert {(row.interaction, row.state) for row in evidence} == {("order", "requested"), ("order", status)}
    assert all(row.object_key == "actual-order" for row in evidence)


def test_rejected_purchase_without_an_order_keeps_no_invented_order_identity():
    rows = [_public_event(1, "action_requested", {"tool_name": "start_activity", "request": {
        "items": [{"action": "purchase", "target": "实际商品"}]}}, "rejected-action"),
        _public_event(2, "action_result", {"status": "rejected", "data": {}}, "rejected-action")]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in rows], [])
    assert {row.state for row in evidence} == {"requested", "rejected"}
    assert all(row.object_key is None for row in evidence)


@pytest.mark.parametrize("before,after", [(0, 0), (1, 0), (0, 1)])
def test_public_content_progress_requires_actual_positive_position(before, after):
    row = _public_event(1, "activity_progress", {"interval_start": 1, "interval_end": 2, "content": {
        "content_id": "actual-content", "position_before_seconds": before, "position_after_seconds": after,
        "completed": False}},
        activity_id="actual-activity")
    evidence = worldmock_public_evidence([parse_world_event(row)], [])
    assert len(evidence) == (1 if after > before else 0)
    if evidence:
        assert evidence[0].action == "interact" and evidence[0].object_key == "actual-content"


@pytest.mark.parametrize("items", [[], [{"way": "进行", "target": "对象"}],
                                    [{"action": "wait", "target": "自己"}]])
def test_public_projection_rejects_non_current_start_request_shape(items):
    row = _public_event(1, "action_requested", {"tool_name": "start_activity", "request": {"items": items}},
                        "invalid-action")
    with pytest.raises((EvaluationError, ValueError)):
        worldmock_public_evidence([parse_world_event(row)], [])


def test_public_projection_rejects_retired_activity_before_reading_other_fields():
    row = _public_event(1, "activity_progress", {"activity": {"kind": "work"}})
    with pytest.raises(EvaluationError, match="retired activity"):
        worldmock_public_evidence([parse_world_event(row)], [])
