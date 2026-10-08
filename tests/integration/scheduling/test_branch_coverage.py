"""@brief 实际登记环境的预排窗口、账单、工资、邮件与收货纯行为。"""

import pytest

from tests.life.purchase_worlds import ENVIRONMENT, at_time, registered_world
from tests.integration.life.test_commerce import order_request
from worldmock.errors import IntegrityError
from worldmock.life.basket import purchase_order
from worldmock.life.social import Message
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import apply_scheduled_effect, process_scheduled_events
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


@pytest.fixture(scope="module")
def runtime_scenario():
    semantics, _ = load_runtime_scenario(ENVIRONMENT / "scenarios/multi-person.json")
    return semantics, semantics.scenario


@pytest.fixture
def world():
    return registered_world(scenario_path=ENVIRONMENT / "scenarios/multi-person.json")


def test_compile_schedule_skips_entries_outside_a_narrow_window(runtime_scenario):
    _, scenario = runtime_scenario
    start = int(scenario.start_at.timestamp())
    assert compile_schedule(scenario, start, start + 1) == []


def test_compile_schedule_keeps_entries_strictly_inside_the_window(runtime_scenario):
    _, scenario = runtime_scenario
    start, end = int(scenario.start_at.timestamp()), int(scenario.end_at.timestamp())
    events = compile_schedule(scenario, start, end)
    assert ScheduledKind.MAIL_ARRIVAL in {event.kind for event in events}
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


def test_delivery_arrival_projects_order_and_receipt_without_inventory(world):
    semantics, state = world
    outcome = purchase_order(state, semantics, 1, order_request())
    event = outcome.scheduled[0]
    arrival = at_time(outcome.state, event.due_at)
    result = apply_scheduled_effect(arrival, event, semantics)
    order = result.data["orders"][0]
    assert order["goods_id"] == "beef-noodles" and order["category"] == "meal"
    assert order["store_id"] == "noodle-1" and order["app_id"] == "meituan"
    assert order["payment_app_id"] == "alipay" and order["received_at"] == event.due_at
    assert result.data.keys() == {"orders"} and len(result.receipts) == 1
    assert result.receipts[0].data["destination_id"] == "home"
    assert result.state.accounts == outcome.state.accounts


def test_calendar_flag_is_a_zero_state_passthrough(world):
    semantics, state = world
    event = ScheduledEvent(scheduled_id="calendar-x", kind="calendar_flag", due_at=state.current_time,
                           sequence=1, data={"date": "2026-01-24", "flags": ["makeup_workday"]})
    result = apply_scheduled_effect(state, event, semantics)
    assert result.data["flags"] == ["makeup_workday"] and result.state is state
    assert result.wake == "message_arrival"


def test_mail_arrival_extends_only_message_visibility(world):
    semantics, state = world
    before_mail = set(state.agents[1].visible_message_ids)
    message = next(item for item in semantics.scenario.messages
                   if item.recipient_agent_id == 1 and item.message_id not in before_mail)
    event = ScheduledEvent(scheduled_id="mail-x", kind="mail_arrival", due_at=int(message.sent_at.timestamp()),
                           sequence=1, agent_id=1, data={"message_id": message.message_id})
    result = apply_scheduled_effect(state, event, semantics)
    assert set(result.state.agents[1].visible_message_ids) == before_mail | {message.message_id}
    assert result.data == {"message_id": message.message_id} and result.wake == "message_arrival"
    assert result.state.accounts == state.accounts and result.state.agents[1].body == state.agents[1].body


def test_unrecognized_scheduled_kind_rejects_instead_of_emitting_success(world):
    semantics, state = world
    before = state.model_dump(mode="json")
    unknown = ScheduledEvent.model_construct(scheduled_id="unknown", kind="unsupported",
                                              due_at=state.current_time, sequence=1, agent_id=1, data={})
    with pytest.raises(IntegrityError, match="Unsupported scheduled event kind"):
        apply_scheduled_effect(state, unknown, semantics)
    assert state.model_dump(mode="json") == before


def test_bill_due_settles_and_refuses_without_crashing(world):
    semantics, state = world
    payable = {"bill_id": "rent", "account_id": "agent:1", "payee_account_id": "merchant:noodle-1", "amount_fen": 1}
    event = ScheduledEvent(scheduled_id="bill-rent", kind="bill_due", due_at=state.current_time, sequence=1, data=payable)
    result = apply_scheduled_effect(state, event, semantics)
    assert result.data["bill_paid"] is True
    assert result.state.accounts["agent:1"] == state.accounts["agent:1"] - 1
    assert result.state.accounts["merchant:noodle-1"] == state.accounts["merchant:noodle-1"] + 1
    assert result.data["resource_changes"] == {"account:agent:1": -1, "account:merchant:noodle-1": 1}
    refused = apply_scheduled_effect(state, event.model_copy(update={"data": {**payable, "amount_fen": 900_000_000}}),
                                      semantics)
    assert refused.data["bill_paid"] is False and refused.data["resource_changes"] == {}
    assert refused.state is state


def test_payroll_settles_and_refuses_without_crashing(world):
    semantics, state = world
    employer = semantics.scenario.employer_account_id
    salary = {"payroll_id": "salary", "employer_account_id": employer, "agent_id": 1, "amount_fen": 1}
    event = ScheduledEvent(scheduled_id="payroll-salary", kind="payroll", due_at=state.current_time,
                           sequence=1, data=salary)
    result = apply_scheduled_effect(state, event, semantics)
    assert result.data["payroll_paid"] is True
    assert result.state.accounts["agent:1"] == state.accounts["agent:1"] + 1
    assert result.state.accounts[employer] == state.accounts[employer] - 1
    refused = apply_scheduled_effect(state, event.model_copy(update={"data": {**salary, "amount_fen": 900_000_000}}),
                                      semantics)
    assert refused.data["payroll_paid"] is False and refused.data["resource_changes"] == {}
    assert refused.state is state


def test_message_arrival_adds_the_real_reply_to_recipient_inbox(world):
    semantics, state = world
    message = Message(message_id="reply", kind="direct", sender_id="contact-colleague", recipient_id="agent:1",
                      text="收到消息", created_at=state.current_time)
    event = ScheduledEvent(scheduled_id="reply-arrival", kind="message_arrival", due_at=state.current_time,
                           sequence=1, agent_id=1, data={"message": message.model_dump(mode="json")})
    changed, drafts, arrivals = process_scheduled_events(state.model_copy(update={"pending_events": [event]}),
                                                       state.current_time, semantics)
    assert changed.agents[1].inbox["reply"] == message and changed.accounts == state.accounts
    assert arrivals == {1: {"message_arrival"}}
    assert drafts[0].data["message_id"] == "reply"
