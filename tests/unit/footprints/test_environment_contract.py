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


def test_directory_browse_has_real_app_use_without_content_or_wait_evidence():
    activity = _activity()
    events = [_event(1, "action_accepted", {"activity": activity}),
              _event(2, "activity_progress", {"activity": activity, "interval_start": 100,
                                               "interval_end": 110}, at=110)]
    facts = project_app_usage([event.model_dump() for event in events])
    assert facts == [{"fact_kind": "app_use", "agent_id": 1, "activity_id": "activity", "app_id": "app",
                      "source_event_id": "event-2", "status": "active",
                      "segments": [{"started_at": 100, "ended_at": 110}]}]
    assert project_verified_experiences(events, agent_id=1, cutoff_seq=2) == []


def test_system_app_touch_and_payment_notification_are_point_facts_with_exact_fields():
    touch = _event(1, "environment_event", {"kind": "app_touch", "app_id": "navigation",
                    "action": "walk", "step": "navigation", "rule_id": None}, visibility="system",
                   cause=["request-result"])
    notification = _event(2, "environment_event", {"kind": "payment_notification", "app_id": "payment",
                           "amount_fen": 1234, "order_ids": ["order"], "title": "支付成功", "summary": "商品 12.34 元"},
                          visibility="system", cause=["payment-event"])
    facts = project_app_usage([touch.model_dump(), notification.model_dump()])
    common = {"fact_kind", "agent_id", "activity_id", "app_id", "source_event_id", "occurred_at"}
    assert set(facts[0]) == common
    assert set(facts[1]) == common | {"amount_fen", "order_ids", "title", "summary"}
    assert all(row["activity_id"] is None for row in facts)
    assert project_verified_experiences([touch, notification], agent_id=1, cutoff_seq=2) == []


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


def _paid_group_events(purchase="purchase", identity="order", first=1, at=110):
    order = {"order_id": identity, "group_key": "group", "agent_id": 1, "paid_at": at,
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
    state, events = _paid_group_events()
    later, again = _paid_group_events("purchase-again", "order-again", first=5, at=120)
    merged = {key: {**state[key], **later[key]} for key in state}
    assert len({order["group_key"] for order in merged["deliveries"].values()}) == 1
    return merged, events + again


def test_payments_sharing_a_delivery_group_key_are_counted_per_purchase_activity():
    state, events = _two_purchases_on_one_route()
    _payment_invariants(events, state, {event["event_id"]: event for event in events})


@pytest.mark.parametrize("fault,message", [
    ("missing_payment", "Purchase group payment count disagrees with actual payment"),
    ("other_purchase", "Purchase payment disagrees with its actual order group"),
    ("partial_payment", "Purchase group has partial payment"),
])
def test_each_purchase_activity_requires_its_own_complete_group_payment(fault, message):
    state, events = _two_purchases_on_one_route()
    if fault == "missing_payment":
        del events[6:]
    elif fault == "other_purchase":
        events[6]["data"]["activity_id"] = "purchase"
    else:
        extra = {**state["deliveries"]["order-again"], "order_id": "order-unpaid", "paid_at": None}
        state["deliveries"]["order-unpaid"] = extra
        state["activities"]["purchase-again"]["data"]["order_ids"].append("order-unpaid")
    with pytest.raises(IntegrityError, match=message):
        _payment_invariants(events, state, {event["event_id"]: event for event in events})


def test_payment_group_requires_actual_completion_and_exact_single_notification():
    state, events = _paid_group_events()
    sources = {event["event_id"]: event for event in events}
    _payment_invariants(events, state, sources)
    with pytest.raises(IntegrityError, match="Payment notification count"):
        _payment_invariants(events + [events[-1]], state, sources)
    events[-1]["data"]["amount_fen"] = 900
    with pytest.raises(IntegrityError, match="Payment notification disagrees"):
        _payment_invariants(events, state, sources)


def test_bank_payment_keeps_money_evidence_without_app_notification():
    state, events = _paid_group_events()
    state["deliveries"]["order"]["payment_app_id"] = None
    events.pop()
    _payment_invariants(events, state, {event["event_id"]: event for event in events})


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


@pytest.mark.parametrize("contents,seconds", [([{"app_ids": ["app"]}], 60), ([], 120)])
def test_directory_browse_rejects_explicit_content_or_wrong_declared_duration(contents, seconds):
    from worldmock.footprints.frozen_environment import _validate_browse
    activity = {"activity_type": "interact", "data": {"app_id": "app", "content_id": None},
                "planned_duration_seconds": seconds}
    with pytest.raises(IntegrityError, match="Directory browse disagrees"):
        _validate_browse([activity], {"app": {"browse_duration_seconds": 60}}, contents)
