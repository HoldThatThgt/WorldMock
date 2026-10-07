"""@brief C4f 分支覆盖补测：scheduling/compile.py 窗口边界与 effects.py 各处理器。

编译窗口用窄窗口驱动"时刻在窗外"的跳过分支；效果处理器直接对引擎构建的
世界状态调用 apply_scheduled_effect（零真实网络、零模型调用）。
"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT

import json
import shutil
from pathlib import Path

import pytest

from worldmock.life.basket import purchase_order
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ModelIdentity, PurchaseItem, PurchaseOrderRequest, RunIdentity
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import apply_scheduled_effect
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MULTI_PERSON = ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios" / "multi-person.json"


@pytest.fixture(scope="module")
def runtime_scenario():
    semantics, _ = load_runtime_scenario(MULTI_PERSON)
    return semantics, semantics.scenario


def test_compile_schedule_skips_entries_outside_a_narrow_window(runtime_scenario):
    """@brief 窄窗口内的预排事件全部落在窗外，编译器返回空表。"""
    _, scenario = runtime_scenario
    start = int(scenario.start_at.timestamp())
    events = compile_schedule(scenario, start, start + 1)
    assert events == []


def test_compile_schedule_keeps_entries_strictly_inside_the_window(runtime_scenario):
    _, scenario = runtime_scenario
    start = int(scenario.start_at.timestamp())
    end = int(scenario.end_at.timestamp())
    events = compile_schedule(scenario, start, end)
    kinds = {event.kind for event in events}
    assert ScheduledKind.MAIL_ARRIVAL in kinds
    sequences = [event.sequence for event in events]
    assert sequences == sorted(sequences) == list(range(1, len(events) + 1))


def _identity(document, run_id):
    return RunIdentity(
        run_id=run_id,
        source_group_id=f"group-{run_id}",
        agent_ids=tuple(item["agent_id"] for item in document["agents"]),
        start_at=document["start_at"],
        end_at=document["end_at"],
        timezone=document["timezone"],
        code_sha256="2" * 64,
        model_identity=ModelIdentity(
            provider="openai_compatible",
            base_url="https://api.deepseek.com/beta",
            requested_model="deepseek-flash",
            thinking="enabled",
            tool_choice="auto",
            max_retries=0,
            prompt_sha256="0" * 64,
            tool_schema_sha256="1" * 64,
        ),
    )


@pytest.fixture
def engine(tmp_path):
    document = json.loads(MULTI_PERSON.read_text(encoding="utf-8"))
    document["corpus_root"] = str(ARTIFICIAL_ENVIRONMENT_ROOT / "corpora")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir(parents=True)
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT / "catalog", tmp_path / "catalog")
    path = scenarios / "custom.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return LifeEngine.create(path, tmp_path / "run", _identity(document, "effects"))


def test_delivery_arrival_projects_order_and_inventory(engine):
    request = PurchaseOrderRequest(
        items=(PurchaseItem(
            store_id="noodle-1",
            food_id="beef-noodles",
            quantity=1,
            recipient_id="agent:1",
        ),),
        fulfillment="delivery",
        destination_id="home",
        app_id="meituan",
    )
    outcome = purchase_order(engine.context.state, engine.context.semantics, 1, request)
    event = outcome.scheduled[0]
    arrival = outcome.state.model_copy(update={"current_time": event.due_at}, deep=True)
    result = apply_scheduled_effect(arrival, event, engine.context.semantics)
    assert result.data["orders"][0]["food_id"] == "beef-noodles"
    assert result.data["orders"][0]["store_id"] == "noodle-1"
    assert result.data["orders"][0]["app_id"] == "meituan"
    assert result.data["inventory_changes"][0]["location_id"] == "home"
    assert result.data["inventory_changes"][0]["quantity"] == 1


def test_calendar_flag_is_a_zero_state_passthrough(engine):
    flag = ScheduledEvent(scheduled_id="calendar-x", kind=ScheduledKind.CALENDAR_FLAG,
                          due_at=1, sequence=1, data={"date": "2026-01-24",
                                                      "flags": ["makeup_workday"]})
    result = apply_scheduled_effect(engine.context.state, flag, engine.context.semantics)
    assert result.data["flags"] == ["makeup_workday"]
    assert result.state is engine.context.state
    assert result.wake == "message_arrival"


def test_mail_arrival_extends_only_message_visibility(engine):
    before_mail = set(engine.context.state.agents[1].visible_message_ids)
    message = next(item for item in engine.context.scenario.messages
                   if item.recipient_agent_id == 1 and item.message_id not in before_mail)
    mail = ScheduledEvent(scheduled_id="mail-x", kind=ScheduledKind.MAIL_ARRIVAL,
                          due_at=int(message.sent_at.timestamp()), sequence=1, agent_id=1,
                          data={"message_id": message.message_id})
    result = apply_scheduled_effect(engine.context.state, mail, engine.context.semantics)
    assert set(result.state.agents[1].visible_message_ids) == before_mail | {message.message_id}
    assert result.data == {"message_id": message.message_id}
    assert result.wake == "message_arrival"


def test_unrecognized_scheduled_kind_rejects_instead_of_emitting_success(engine):
    before = engine.context.state.model_dump(mode="json")
    unknown = ScheduledEvent.model_construct(scheduled_id="unknown", kind="unsupported",
                                              due_at=1, sequence=1, agent_id=1, data={})
    with pytest.raises(IntegrityError, match="Unsupported scheduled event kind"):
        apply_scheduled_effect(engine.context.state, unknown, engine.context.semantics)
    assert engine.context.state.model_dump(mode="json") == before


def test_bill_due_settles_and_refuses_without_crashing(engine):
    payable = {"bill_id": "rent", "account_id": "agent:1",
               "payee_account_id": "merchant:noodle-1", "amount_fen": 1}
    event = ScheduledEvent(scheduled_id="bill-rent", kind=ScheduledKind.BILL_DUE,
                           due_at=1, sequence=1, data=payable)
    result = apply_scheduled_effect(engine.context.state, event, engine.context.semantics)
    assert result.data["bill_paid"] is True
    assert result.state.accounts["agent:1"] == engine.context.state.accounts["agent:1"] - 1
    assert result.state.accounts["merchant:noodle-1"] == engine.context.state.accounts["merchant:noodle-1"] + 1
    assert result.data["resource_changes"] == {
        "account:agent:1": -1, "account:merchant:noodle-1": 1}

    broke = dict(payable, bill_id="broke", amount_fen=900_000_000)
    event = ScheduledEvent(scheduled_id="bill-broke", kind=ScheduledKind.BILL_DUE,
                           due_at=1, sequence=1, data=broke)
    refused = apply_scheduled_effect(engine.context.state, event, engine.context.semantics)
    assert refused.data["bill_paid"] is False
    assert refused.data["resource_changes"] == {}
    assert refused.state is engine.context.state


def test_payroll_settles_and_refuses_without_crashing(engine):
    employer = engine.context.scenario.employer_account_id
    salary = {"payroll_id": "salary", "employer_account_id": employer,
              "agent_id": 1, "amount_fen": 1}
    event = ScheduledEvent(scheduled_id="payroll-salary", kind=ScheduledKind.PAYROLL,
                           due_at=1, sequence=1, data=salary)
    result = apply_scheduled_effect(engine.context.state, event, engine.context.semantics)
    assert result.data["payroll_paid"] is True
    assert result.state.accounts["agent:1"] == engine.context.state.accounts["agent:1"] + 1
    assert result.state.accounts[employer] == engine.context.state.accounts[employer] - 1

    huge = dict(salary, payroll_id="huge", amount_fen=900_000_000)
    event = ScheduledEvent(scheduled_id="payroll-huge", kind=ScheduledKind.PAYROLL,
                           due_at=1, sequence=1, data=huge)
    refused = apply_scheduled_effect(engine.context.state, event, engine.context.semantics)
    assert refused.data["payroll_paid"] is False
    assert refused.data["resource_changes"] == {}
    assert refused.state is engine.context.state
