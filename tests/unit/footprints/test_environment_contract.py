"""@brief 当前记录合同的可观察投影与资金唯一来源。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.errors import IntegrityError
from worldmock.footprints.app_usage import project_app_usage
from worldmock.footprints.experiences import project_verified_experiences, validate_verified_experiences
from worldmock.footprints.invariants import _payment_invariants
from worldmock.footprints.ledger import validate_account_transition
from worldmock.footprints.models import Event, EventDraft


def _event(sequence, kind, data, *, at=100, visibility="agent", cause=None, changes=None):
    return Event(event_id=f"event-{sequence}", event_seq=sequence, run_id="run", event_type=kind,
                 occurred_at=at, recorded_at="2026-01-01T00:00:00Z", wall_elapsed_ms=sequence,
                 state_version_before=sequence - 1, state_version_after=sequence, agent_id=1,
                 caused_by=cause or [], data=data, visibility=visibility, resource_changes=changes or {})


def _activity(kind="interact", *, content=None):
    return {"activity_id": "activity", "agent_id": 1, "kind": kind, "status": "active",
            "elapsed_seconds": 0, "data": {"action": "interact", "app_id": "app", "content_id": content},
            "session_ids": ["session"]}


@pytest.mark.parametrize("content", [None, "content"])
def test_interact_progress_without_content_evidence_is_rejected(content):
    """每一项 interact 都有内容：一条没有内容证据的 interact 进度（快照里有没有内容身份都一样）不被跳过，已核验经历的
    投影以 Playback progress has no app content evidence 报运行故障。"""
    activity = _activity(content=content)
    events = [_event(1, "action_accepted", {"activity": activity}),
              _event(2, "activity_progress", {"activity": activity, "interval_start": 100,
                                               "interval_end": 110}, at=110)]
    with pytest.raises(IntegrityError, match="^Playback progress has no app content evidence$"):
        project_verified_experiences(events, agent_id=1, cutoff_seq=2)


def test_callback_touch_and_the_two_notifications_are_point_facts_with_exact_fields():
    touch = _event(1, "environment_event", {"kind": "app_touch", "app_id": "maps",
                    "action": "travel", "step": "browse", "rule_id": "fuel-rule"}, visibility="system",
                   cause=["request-result"])
    notification = _event(2, "environment_event", {"kind": "payment_notification", "app_id": "payment",
                           "amount_fen": 1234, "order_ids": ["order"], "title": "支付成功", "summary": "商品 12.34 元"},
                          visibility="system", cause=["payment-event"])
    placed = _event(3, "environment_event", {"kind": "order_notification", "app_id": "ordering",
                     "order_ids": ["order"], "title": "订单已下单", "summary": "已下单：商品"},
                    visibility="system", cause=["payment-event"])
    facts = project_app_usage([touch.model_dump(), notification.model_dump(), placed.model_dump()])
    common = {"fact_kind", "agent_id", "activity_id", "app_id", "source_event_id", "occurred_at"}
    assert [row["fact_kind"] for row in facts] == ["app_touch", "payment_notification", "order_notification"]
    assert set(facts[0]) == common
    assert set(facts[1]) == common | {"amount_fen", "order_ids", "title", "summary"}
    assert set(facts[2]) == common | {"order_ids", "title", "summary"}
    assert all(row["activity_id"] is None for row in facts)
    assert project_verified_experiences([touch, notification, placed], agent_id=1, cutoff_seq=3) == []


@pytest.mark.parametrize("step,rule_id", [("navigation", None), ("payment", None), ("navigation", "fuel-rule"),
                                          ("browse", ""), ("browse", 7)])
def test_app_touch_is_only_a_callback_footprint_and_the_automatic_touches_are_refused(step, rule_id):
    """出发和进入付款不再写时间点：没有回调规则身份的触点，以及步骤是导航的触点，都按来源不符拒绝。"""
    touch = _event(1, "environment_event", {"kind": "app_touch", "app_id": "maps", "action": "travel",
                                            "step": step, "rule_id": rule_id}, visibility="system",
                   cause=["request-result"])
    with pytest.raises(IntegrityError, match="^App touch step disagrees with its source$"):
        project_app_usage([touch.model_dump()])


@pytest.mark.parametrize("retired", ["wait", "action", "eat", "work", "chat"])
def test_retired_activity_is_rejected_before_any_downstream_projection(retired):
    from worldmock.footprints.export import reconstruct_activities
    from worldmock.footprints.quality import build_quality_report
    event = _event(1, "activity_progress", {"activity": _activity(retired)})
    message = "Activity snapshot uses a retired activity kind"
    for project in (lambda: project_app_usage([event.model_dump()]),
                    lambda: reconstruct_activities([event]),
                    lambda: project_verified_experiences([event], agent_id=1, cutoff_seq=1),
                    lambda: build_quality_report([event], [], {})):
        with pytest.raises(IntegrityError, match=message):
            project()


def test_observed_waited_is_rejected_as_an_invalid_experience_kind():
    with pytest.raises(IntegrityError, match="Observed experience kind is invalid"):
        validate_verified_experiences([{"kind": "waited"}], 1, 100)


def test_ledger_uses_envelope_and_rejects_retired_embedded_money():
    before = {"accounts": {"agent:1": 2000, "external:scenario": -2000}}
    after = {"accounts": {"agent:1": 1000, "external:scenario": -1000}}
    payment = EventDraft(event_type="environment_event", occurred_at=100,
                         data={"kind": "purchase_payment"},
                         resource_changes={"account:agent:1": -1000, "account:external:scenario": 1000})
    validate_account_transition(before, after, [payment], False)
    with pytest.raises(ValidationError, match="Event data carries retired resource_changes"):
        EventDraft(event_type="environment_event", occurred_at=100, data={"resource_changes": {}})
    with pytest.raises(IntegrityError, match="Account balances disagree"):
        validate_account_transition(before, after, [], False)


def _paid_order_events(purchase="purchase", identity="order", first=1, at=110):
    order = {"order_id": identity, "group_key": "group", "app_id": "ordering", "agent_id": 1, "paid_at": at,
             "payment_app_id": "payment", "total_price_fen": 1000}
    activity = {"activity_id": purchase, "kind": "purchase", "data": {"order_ids": [identity]}}
    start = _event(first, "action_result", {"status": "in_progress", "data": {"activities": [
                   {"activity_id": purchase, "kind": "purchase"}]}})
    progress = _event(first + 1, "activity_progress", {"activity": {"activity_id": purchase, "kind": "purchase",
                     "status": "completed", "ended_at": at}}, at=at)
    payment = _event(first + 2, "environment_event", {"kind": "purchase_payment", "activity_id": purchase,
                    "order_ids": [identity], "amount_fen": 1000}, at=at, visibility="system",
                   cause=[start.event_id], changes={"account:agent:1": -1000, "account:external:scenario": 1000})
    notification = _event(first + 3, "environment_event", {"kind": "payment_notification", "app_id": "payment",
                          "amount_fen": 1000, "order_ids": [identity], "title": "支付成功",
                          "summary": "商品 10.00 元"}, at=at, visibility="system", cause=[payment.event_id])
    payment = payment.model_copy(update={"state_version_after": progress.state_version_after})
    notification = notification.model_copy(update={"state_version_after": progress.state_version_after})
    state = {"deliveries": {identity: order}, "activities": {purchase: activity}}
    return state, [row.model_dump() for row in (start, progress, payment, notification)]


def _two_purchases_on_one_route():
    state, events = _paid_order_events()
    later, again = _paid_order_events("purchase-again", "order-again", first=5, at=120)
    merged = {key: {**state[key], **later[key]} for key in state}
    assert len({(order["group_key"], order["app_id"]) for order in merged["deliveries"].values()}) == 1
    return merged, events + again


def test_payments_on_the_same_route_and_ordering_app_are_counted_per_purchase_activity():
    state, events = _two_purchases_on_one_route()
    _payment_invariants(events, state, {event["event_id"]: event for event in events})


@pytest.mark.parametrize("fault,message", [
    ("missing_payment", "Purchase order payment count disagrees with actual payment"),
    ("other_purchase", "Purchase payment disagrees with its actual order"),
    ("partial_payment", "Purchase order has partial payment"),
])
def test_each_purchase_activity_requires_its_own_complete_order_payment(fault, message):
    state, events = _two_purchases_on_one_route()
    if fault == "missing_payment":
        del events[6:]
    elif fault == "other_purchase":
        events[6]["data"]["activity_id"] = "purchase"
    else:
        extra = {**state["deliveries"]["order-again"], "order_id": "order-unpaid", "paid_at": None,
                 "group_key": "another-group"}
        state["deliveries"]["order-unpaid"] = extra
        state["activities"]["purchase-again"]["data"]["order_ids"].append("order-unpaid")
    with pytest.raises(IntegrityError, match=f"^{message}$"):
        _payment_invariants(events, state, {event["event_id"]: event for event in events})


def _one_order_over_two_delivery_groups():
    """同一个下单 app 上两家店：两条记录分属两个配送组，是一张订单，一条付款事件列出两条记录。"""
    state, events = _paid_order_events()
    state["deliveries"]["order-b"] = {**state["deliveries"]["order"], "order_id": "order-b",
                                      "group_key": "another-group", "total_price_fen": 700}
    state["activities"]["purchase"]["data"]["order_ids"].append("order-b")
    for event in events[2:]:
        event["data"].update(order_ids=["order", "order-b"], amount_fen=1700)
    events[2]["resource_changes"] = {"account:agent:1": -1700, "account:external:scenario": 1700}
    return state, events


def test_one_order_spanning_two_delivery_groups_is_paid_by_exactly_one_payment():
    state, events = _one_order_over_two_delivery_groups()
    _payment_invariants(events, state, {event["event_id"]: event for event in events})


@pytest.mark.parametrize("fault,message", [
    ("payment_per_group", "Purchase order payment count disagrees with actual payment"),
    ("one_group_only", "Purchase payment disagrees with its actual order"),
    ("other_app", "Purchase payment disagrees with its actual order"),
])
def test_payment_must_cover_the_whole_order_and_not_one_delivery_group(fault, message):
    state, events = _one_order_over_two_delivery_groups()
    if fault == "payment_per_group":
        second = {**events[2], "event_id": "event-extra", "data": {**events[2]["data"], "order_ids": ["order-b"]}}
        events.insert(3, second)
    elif fault == "one_group_only":
        events[2]["data"]["order_ids"] = ["order"]
    else:
        state["deliveries"]["order-b"]["app_id"] = "another-ordering-app"
    with pytest.raises(IntegrityError, match=f"^{message}$"):
        _payment_invariants(events, state, {event["event_id"]: event for event in events})


def test_store_records_without_an_ordering_app_form_one_order_per_purchase():
    state, events = _one_order_over_two_delivery_groups()
    for order in state["deliveries"].values():
        order["app_id"] = None
    _payment_invariants(events, state, {event["event_id"]: event for event in events})


def test_paid_order_requires_actual_completion_and_exact_single_notification():
    state, events = _paid_order_events()
    sources = {event["event_id"]: event for event in events}
    _payment_invariants(events, state, sources)
    with pytest.raises(IntegrityError, match="Payment notification count"):
        _payment_invariants(events + [events[-1]], state, sources)
    events[-1]["data"]["amount_fen"] = 900
    with pytest.raises(IntegrityError, match="Payment notification disagrees"):
        _payment_invariants(events, state, sources)


def test_direct_payment_at_the_store_keeps_money_evidence_without_any_notification():
    state, events = _paid_order_events()
    state["deliveries"]["order"].update(payment_app_id=None, app_id=None)
    sources = {event["event_id"]: event for event in events}
    with pytest.raises(IntegrityError, match="^Payment notification count disagrees with actual payment$"):
        _payment_invariants(events, state, sources)
    events.pop()
    _payment_invariants(events, state, {event["event_id"]: event for event in events})


def test_direct_payment_inside_the_content_app_is_notified_by_that_ordering_app():
    state, events = _paid_order_events()
    state["deliveries"]["order"]["payment_app_id"] = None
    sources = {event["event_id"]: event for event in events}
    with pytest.raises(IntegrityError, match="^Payment notification disagrees with its actual purchase payment$"):
        _payment_invariants(events, state, sources)
    events[-1]["data"]["app_id"] = "ordering"
    _payment_invariants(events, state, sources)
    with pytest.raises(IntegrityError, match="^Payment notification count disagrees with actual payment$"):
        _payment_invariants(events[:-1], state, sources)


def test_wake_categories_count_each_category_once_per_real_boundary():
    from worldmock.footprints.quality import _decision_rhythm_quality
    boundary = _event(1, "environment_event", {"kind": "body_settlement", "decision_offered": True,
                      "wake_reasons": ["planned_end", "session_open", "reminder_due", "app_push"]})
    started = _event(2, "decision_started", {"round_id": "round", "round_order": 0,
                     "world_state_version": 1, "wake_reasons": ["planned_end", "session_open"]})
    result = _decision_rhythm_quality([boundary, started])
    assert result["wake_category_counts"] == {"reminder_due": 1, "time_boundary": 1}
    assert result["decision_started_wake_category_counts"] == {"time_boundary": 1}
    assert result["wake_reason_counts"]["planned_end"] == 1
    assert "boundaries_missing_decision_field" not in result
    assert "decision_budget_exhausted_events" not in result
