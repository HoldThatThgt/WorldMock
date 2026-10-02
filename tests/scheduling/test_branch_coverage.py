"""@brief C4f 分支覆盖补测：scheduling/compile.py 窗口边界与 effects.py 各处理器。

编译窗口用窄窗口驱动"时刻在窗外"的跳过分支；效果处理器直接对引擎构建的
世界状态调用 apply_scheduled_effect（零真实网络、零模型调用）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from worldmock.life.basket import purchase_order
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ModelIdentity, PurchaseOrderRequest, RunIdentity
from worldmock.life.semantics import load_runtime_scenario
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import apply_scheduled_effect
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MULTI_PERSON = PROJECT_ROOT / "scenarios" / "multi-person.json"


@pytest.fixture(scope="module")
def runtime_scenario():
    semantics, _ = load_runtime_scenario(MULTI_PERSON)
    return semantics, semantics.scenario


def test_compile_schedule_skips_entries_outside_a_narrow_window(runtime_scenario):
    """@brief 窄窗口内邮件/任务/截止全部落在窗外：跳过分支全部命中。"""
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
    document["corpus_root"] = str(PROJECT_ROOT / "corpora")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / "catalog", tmp_path / "catalog")
    path = scenarios / "custom.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return LifeEngine.create(path, tmp_path / "run", _identity(document, "effects"))


def test_delivery_arrival_projects_order_and_inventory(engine):
    product_id = "food-6923644266066"
    # 把世界时钟置于营业时段后再下单（购买校验商家营业时间）。
    staged = engine.state.model_copy(deep=True)
    staged.current_time = staged.current_time + 10 * 3600
    engine.state = staged
    supply = next(item for item in engine.scenario.supplies
                  if item.location_id == "market" and item.product_id == product_id)
    request = PurchaseOrderRequest(items=({"supply_id": supply.supply_id, "quantity": 1, "recipient_id": "agent:1",
                                           "price_version": engine.state.supply_quotes[supply.supply_id].price_version},),
                                    fulfillment="delivery", destination_id="home")
    outcome = purchase_order(engine.state, engine.semantics, 1, request)
    event = outcome.scheduled[0]
    outcome.state.current_time = event.due_at
    result = apply_scheduled_effect(outcome.state, event, engine.semantics)
    assert result.data["orders"][0]["product_id"] == product_id
    assert result.data["inventory_changes"][0]["location_id"] == "home"
    assert result.data["inventory_changes"][0]["quantity"] == 1


def test_task_expired_and_calendar_flag_are_zero_state_passthroughs(engine):
    expired = ScheduledEvent(scheduled_id="expired-x", kind=ScheduledKind.TASK_EXPIRED,
                             due_at=1, sequence=1, data={"task_id": "task-9"})
    result = apply_scheduled_effect(engine.state, expired, engine.semantics)
    assert result.data == {"task_id": "task-9"}
    assert result.state is engine.state

    flag = ScheduledEvent(scheduled_id="calendar-x", kind=ScheduledKind.CALENDAR_FLAG,
                          due_at=1, sequence=1, data={"date": "2026-01-24",
                                                      "flags": ["makeup_workday"]})
    result = apply_scheduled_effect(engine.state, flag, engine.semantics)
    assert result.data["flags"] == ["makeup_workday"]
    assert result.state is engine.state


def test_mail_and_work_events_extend_agent_visibility(engine):
    before_mail = set(engine.state.agents[1].visible_message_ids)
    message = next(item for item in engine.scenario.messages
                   if item.recipient_agent_id == 1 and item.message_id not in before_mail
                   and item.related_task_id is not None)
    mail = ScheduledEvent(scheduled_id="mail-x", kind=ScheduledKind.MAIL_ARRIVAL,
                          due_at=int(message.sent_at.timestamp()), sequence=1, agent_id=1,
                          data={"message_id": message.message_id})
    result = apply_scheduled_effect(engine.state, mail, engine.semantics)
    assert set(result.state.agents[1].visible_message_ids) == before_mail | {message.message_id}
    assert message.related_task_id in result.state.agents[1].visible_task_ids

    before_tasks = set(engine.state.agents[1].visible_task_ids)
    task = next(item for item in engine.scenario.tasks
                if item.assignee_agent_id in {"1", "agent:1"} and item.task_id not in before_tasks)
    work = ScheduledEvent(scheduled_id="work-x", kind=ScheduledKind.WORK_AVAILABLE,
                          due_at=int(task.available_at.timestamp()), sequence=1, agent_id=1,
                          data={"task_id": task.task_id})
    result = apply_scheduled_effect(engine.state, work, engine.semantics)
    assert set(result.state.agents[1].visible_task_ids) == before_tasks | {task.task_id}

def test_unrecognized_scheduled_kind_rejects_instead_of_emitting_success(engine):
    before = engine.state.model_dump(mode="json")
    unknown = ScheduledEvent.model_construct(scheduled_id="unknown", kind="unsupported",
                                              due_at=1, sequence=1, agent_id=1, data={})
    with pytest.raises(IntegrityError, match="Unsupported scheduled event kind"):
        apply_scheduled_effect(engine.state, unknown, engine.semantics)
    assert engine.state.model_dump(mode="json") == before


def test_bill_due_settles_and_refuses_without_crashing(engine):
    payable = {"bill_id": "rent", "account_id": "agent:1",
               "payee_account_id": "merchant:grocery", "amount_fen": 50_000}
    event = ScheduledEvent(scheduled_id="bill-rent", kind=ScheduledKind.BILL_DUE,
                           due_at=1, sequence=1, data=payable)
    result = apply_scheduled_effect(engine.state, event, engine.semantics)
    assert result.data["bill_paid"] is True
    assert result.state.accounts["agent:1"] == engine.state.accounts["agent:1"] - 50_000
    assert result.data["resource_changes"] == {
        "account:agent:1": -50_000, "account:merchant:grocery": 50_000}

    broke = dict(payable, bill_id="broke", amount_fen=900_000_000)
    event = ScheduledEvent(scheduled_id="bill-broke", kind=ScheduledKind.BILL_DUE,
                           due_at=1, sequence=1, data=broke)
    refused = apply_scheduled_effect(engine.state, event, engine.semantics)
    assert refused.data["bill_paid"] is False
    assert refused.data["resource_changes"] == {}
    assert refused.state is engine.state


def test_payroll_settles_and_refuses_without_crashing(engine):
    salary = {"payroll_id": "salary", "employer_account_id": "employer:zhangjiang",
              "agent_id": 1, "amount_fen": 100_000}
    event = ScheduledEvent(scheduled_id="payroll-salary", kind=ScheduledKind.PAYROLL,
                           due_at=1, sequence=1, data=salary)
    result = apply_scheduled_effect(engine.state, event, engine.semantics)
    assert result.data["payroll_paid"] is True
    assert result.state.accounts["agent:1"] == engine.state.accounts["agent:1"] + 100_000

    huge = dict(salary, payroll_id="huge", amount_fen=900_000_000)
    event = ScheduledEvent(scheduled_id="payroll-huge", kind=ScheduledKind.PAYROLL,
                           due_at=1, sequence=1, data=huge)
    refused = apply_scheduled_effect(engine.state, event, engine.semantics)
    assert refused.data["payroll_paid"] is False
    assert refused.data["resource_changes"] == {}
    assert refused.state is engine.state


def test_price_change_persists_world_quote_and_product_release_restocks(engine):
    supply = next(item for item in engine.scenario.supplies
                  if item.location_id == "market" and item.product_id == "food-6923644266066")
    old_version = engine.state.supply_quotes[supply.supply_id].price_version
    source = supply.source.model_dump(mode="json")
    product = engine.rules.product(supply.product_id)
    promo_price = product.purchase_price_fen_min
    change = ScheduledEvent(scheduled_id="price-promo", kind=ScheduledKind.PRICE_CHANGE,
                            due_at=engine.state.current_time, sequence=1,
                            data={"supply_id": supply.supply_id, "price_version": "promo-v1",
                                  "amount_fen": promo_price, "source": source})
    result = apply_scheduled_effect(engine.state, change, engine.semantics)
    assert result.state.supply_quotes[supply.supply_id].price_fen == promo_price
    assert result.state.supply_quotes[supply.supply_id].price_version == "promo-v1"
    assert engine.state.supply_quotes[supply.supply_id].price_version == old_version
    assert result.data["quote_before"]["price_version"] == old_version
    release = ScheduledEvent(scheduled_id="release-x", kind=ScheduledKind.PRODUCT_RELEASE,
                             due_at=engine.state.current_time, sequence=1,
                             data={"supply_id": supply.supply_id, "stock": 3, "source": source})
    before = engine.state.supply_stock[supply.supply_id]
    result = apply_scheduled_effect(engine.state, release, engine.semantics)
    assert result.state.supply_stock[supply.supply_id] == 3
    assert result.data["stock_before"] == before
