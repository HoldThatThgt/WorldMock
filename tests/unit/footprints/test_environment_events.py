"""当前环境记录拒绝退役事件、伪造转账和开放正文。"""

import pytest
from pydantic import ValidationError

from worldmock.recording_engine.events.models import EventDraft


def fact(data, **changes):
    return EventDraft(event_type="environment_event", occurred_at=100, agent_id=1,
                      source_kind="environment", visibility="system", data=data, **changes)


def transfer(**changes):
    return {"kind": "callback_effect", "type": "transfer", "rule_id": "fee", "direction": "expense",
            "amount_fen": 100, "from_account_id": "agent:1", "to_account_id": "external:scenario",
            "status": "paid", **changes}


@pytest.mark.parametrize("kind", ["alarm_ring", "message_arrival", "mail_arrival", "model_npc_reply_pending",
                                  "model_npc_reply", "work_content_completed", "bill_due", "payroll",
                                  "goal_progressed"])
def test_retired_environment_event_is_rejected(kind):
    with pytest.raises(ValidationError, match="retired kind"):
        fact({"kind": kind})


def test_paid_transfer_has_exact_actual_account_changes():
    changes = {"account:agent:1": -100, "account:external:scenario": 100}
    assert fact(transfer(), caused_by=["actual-trigger"], resource_changes=changes).resource_changes == changes
    with pytest.raises(ValidationError, match="payment status"):
        fact(transfer(), caused_by=["actual-trigger"])
    with pytest.raises(ValidationError, match="payment status"):
        fact(transfer(status="unpaid"), caused_by=["actual-trigger"], resource_changes=changes)


@pytest.mark.parametrize("changes", [{"amount_fen": True}, {"amount_fen": "100"}, {"amount_fen": 0},
                                     {"from_account_id": "merchant"}, {"direction": "outgoing"}, {"legacy": 1}])
def test_transfer_fields_are_strict(changes):
    with pytest.raises(ValidationError):
        fact(transfer(**changes), caused_by=["actual-trigger"])


@pytest.mark.parametrize("causes", [[], ["one", "two"]])
def test_callback_requires_one_actual_trigger(causes):
    with pytest.raises(ValidationError, match="one actual"):
        fact(transfer(status="unpaid"), caused_by=causes)


def test_time_trigger_is_closed_independent_fact():
    data = {"kind": "time_trigger", "key": "hourly", "scheduled_id": "time-hourly-100"}
    assert fact(data).data == data
    for payload in ({**data, "extra": 1}, {**data, "key": "payroll"}):
        with pytest.raises(ValidationError):
            fact(payload)
    with pytest.raises(ValidationError, match="independent"):
        fact(data, caused_by=["invented-source"])


def test_weather_requires_actual_factor_and_canonical_date():
    data = {"kind": "weather", "local_date": "2026-01-05", "condition": "snow",
            "speed_factor": 0.6, "scheduled_id": "weather-2026-01-05"}
    assert fact(data).data == data
    for payload in ({**data, "speed_factor": 1}, {**data, "local_date": "20260105"}, {**data, "extra": 1}):
        with pytest.raises(ValidationError):
            fact(payload)
