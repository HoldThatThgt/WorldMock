"""@brief 货币改道等价冒烟与 resource_flows 落迹正反例（设计文档 §9/§14.2）。

一天夹具（multi-person）期末六账户对账参照 tests/resources/test_equivalence_anchor.py
的模式：把运行足迹中全部携带 resource_changes 的事件改写为 TransferEntry 序列，
经货币域账本从全零重放，期末六账户必须与引擎实际状态逐分一致，锚点吸收
-Σ期初注入且 closed 域守恒。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import advance_next, submit_round
from worldmock.footprints.models import (
    RESOURCE_FLOWS_FIELD,
    EventDraft,
    validate_resource_flows,
)
from worldmock.life.models import (
    ContinueActivityRequest,
    FulfillmentKind,
    PurchaseRequest,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
    StartTravelRequest,
    TravelMode,
)
from worldmock.life.settlement import (
    SCENARIO_ANCHOR_OWNER,
    inject_initial_funds,
    money_ledger,
)
from worldmock.resources import ResourceMeta, TransferEntry, audit_closed_domain

MONEY = "money_fen"


def _stage_open(engine) -> None:
    """把一天夹具移到商家营业时段并保留之后的真实后台事件。"""
    zone = ZoneInfo(engine.scenario.timezone)
    epoch = int(datetime(2026, 1, 5, 10, 0, tzinfo=zone).timestamp())
    staged = engine.state.model_copy(deep=True)
    staged.current_time = epoch
    staged.pending_events = [event for event in staged.pending_events if event.due_at > epoch]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(update={"next_decision_at": epoch}, deep=True)
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=epoch, visibility="system",
                       source_kind="scenario", data={"kind": "test_merchant_open"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def _wait(seconds: int = 60) -> StartBasicActivityRequest:
    return StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=seconds)


def _transfer_entries(engine) -> list[TransferEntry]:
    """把足迹资金事件改写为双边转账分录（单一付款方、逐对金额相等）。"""
    events = [event for event in engine.store.read_events(engine.store.last_event_seq)
              if event.data.get("resource_changes")]
    entries: list[TransferEntry] = []
    for event in events:
        changes = {key.removeprefix("account:"): value
                   for key, value in event.data["resource_changes"].items()}
        payers = [owner for owner, value in changes.items() if value < 0]
        assert len(payers) == 1 and sum(changes.values()) == 0, (event.event_seq, changes)
        for owner, value in changes.items():
            if owner != payers[0]:
                entries.append(TransferEntry(MONEY, payers[0], owner, value,
                                             f"footprints:{event.event_seq}", event.occurred_at))
    return entries


def test_one_day_fixture_reconciles_six_accounts_against_ledger(engine_factory) -> None:
    """一天夹具注资+购买+车费改道后，期末六账户可从足迹分录逐分重放对账。"""
    engine = engine_factory("multi-person.json")
    _stage_open(engine)
    submit_round(engine, {
        1: ("start_targeted_activity", StartTargetedActivityRequest(
            activity_type="browse", target_id="product_catalog",
            planned_duration_seconds=60, playback_speed=1)),
        2: ("start_basic_activity", _wait()),
    }, "browse")
    advance_next(engine)
    product = engine.scenario.products[0]
    submit_round(engine, {
        1: ("purchase", PurchaseRequest(
            product_id=product.product_id, quantity=1,
            fulfillment=FulfillmentKind.DELIVERY, destination_id="home",
            price_version=product.price_version)),
        2: ("start_basic_activity", _wait()),
    }, "buy")
    advance_next(engine)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    submit_round(engine, {
        1: ("start_travel", StartTravelRequest(
            destination_id="office", travel_mode=TravelMode.DRIVING,
            planned_duration_seconds=216)),
    }, "to-office")

    accounts = dict(engine.state.accounts)
    assert len(accounts) == 6, f"expected six ledger accounts, got {sorted(accounts)}"
    entries = _transfer_entries(engine)
    assert len(entries) >= 1 + 1 + 1, "expected injection + purchase + fare transfers"

    ledger = money_ledger()
    replayed = ledger.validate_history({}, entries)
    assert {key: value for key, value in replayed.items()
            if key[1] in accounts} == {(MONEY, owner): value for owner, value in accounts.items()}
    assert replayed[(MONEY, SCENARIO_ANCHOR_OWNER)] == -sum(accounts.values())

    report = audit_closed_domain(ledger, MONEY, {}, entries, expected_final=accounts)
    assert report.real_final_total == report.injection_total == sum(accounts.values())
    assert report.final_total == report.initial_total == 0

    settlement = engine.state.accounts[product.merchant_account_id] - \
        engine.semantics.accounts[product.merchant_account_id]
    assert settlement == product.price_fen
    assert engine.state.accounts["agent:1"] == (
        engine.semantics.accounts["agent:1"] - product.price_fen - 555)


def test_initial_injection_matches_semantics_accounts(engine_factory) -> None:
    """分录注资结果与语义初值账户逐分一致（含零初值账户）。"""
    engine = engine_factory("multi-person.json")
    assert engine.state.accounts == dict(engine.semantics.accounts)
    assert all(isinstance(value, int) for value in engine.state.accounts.values())
    accounts = dict(engine.semantics.accounts)
    accounts["agent:1"] = 0
    assert inject_initial_funds(accounts, 0) == accounts


def test_activity_progress_carries_validated_body_flows(engine_factory) -> None:
    """活动推进事件落迹身体分录：owner 重盖为 agent 账户且载荷过校验。"""
    engine = engine_factory("multi-person.json")
    submit_round(engine, {1: ("start_basic_activity", _wait(3600)),
                          2: ("start_basic_activity", _wait(3600))}, "wait")
    advance_next(engine)
    progresses = [event for event in engine.store.read_events(engine.store.last_event_seq)
                  if event.event_type == "activity_progress"
                  and event.agent_id == 1 and RESOURCE_FLOWS_FIELD in event.data]
    assert progresses, "activity progress settlement must leave body flow evidence"
    flows = validate_resource_flows(progresses[0].data[RESOURCE_FLOWS_FIELD])
    assert {flow["resource"] for flow in flows} == {
        "energy_kcal", "sleep_debt_hours", "entertainment_need"}
    assert {flow["owner"] for flow in flows} == {"agent:1"}
    assert all(flow["direction"] in ("source", "drain") and flow["amount"] > 0 for flow in flows)
    assert all(flow["cause"].startswith("settle:") for flow in flows)
    settlement = progresses[0].data["body_settlement"]
    drained = sum(flow["amount"] for flow in flows
                  if flow["resource"] == "energy_kcal" and flow["direction"] == "drain")
    assert drained == settlement["before"]["energy_kcal"] - settlement["after"]["energy_kcal"]


def test_non_settlement_events_never_carry_resource_flows(engine_factory) -> None:
    """动作受理/结果与环境事件不是身体结算点，不得携带 resource_flows 字段。"""
    engine = engine_factory("multi-person.json")
    submit_round(engine, {1: ("start_basic_activity", _wait(3600)),
                          2: ("start_basic_activity", _wait(3600))}, "wait")
    advance_next(engine)
    events = [event for event in engine.store.read_events(engine.store.last_event_seq)
              if event.event_type != "activity_progress"]
    assert events
    assert all(RESOURCE_FLOWS_FIELD not in event.data for event in events)


def test_advance_intervals_keep_flows_attributed_per_event(engine_factory) -> None:
    """两个区间各自取走本段分录，amount 与区间身体结算逐段对应。"""
    engine = engine_factory("multi-person.json")
    submit_round(engine, {1: ("start_basic_activity", _wait(7200)),
                          2: ("start_basic_activity", _wait(7200))}, "wait")
    advance_next(engine)
    submit_round(engine, {
        1: ("continue_activity", ContinueActivityRequest(activity_id="activity-action-wait-1")),
        2: ("continue_activity", ContinueActivityRequest(activity_id="activity-action-wait-2")),
    }, "keep-waiting")
    advance_next(engine)
    progresses = sorted(
        (event for event in engine.store.read_events(engine.store.last_event_seq)
         if event.event_type == "activity_progress" and event.agent_id == 1
         and RESOURCE_FLOWS_FIELD in event.data),
        key=lambda event: event.data["interval_end"])
    assert len(progresses) == 2
    for event in progresses:
        drained = sum(flow["amount"] for flow in event.data[RESOURCE_FLOWS_FIELD]
                      if flow["resource"] == "energy_kcal" and flow["direction"] == "drain")
        settlement = event.data["body_settlement"]
        assert drained == settlement["before"]["energy_kcal"] - settlement["after"]["energy_kcal"]
    first, second = progresses
    assert first.data["interval_end"] == second.data["interval_start"]


@pytest.mark.parametrize("payload", [
    "not-a-list",
    [{"resource": "energy_kcal", "owner": "agent:1", "direction": "drain", "amount": 5}],
    [{"resource": "energy_kcal", "owner": "agent:1", "direction": "drain", "amount": 5,
      "cause": "settle:wait", "extra": 1}],
    [{"resource": "", "owner": "agent:1", "direction": "drain", "amount": 5, "cause": "x"}],
    [{"resource": "energy_kcal", "owner": "agent:1", "direction": "flow", "amount": 5,
      "cause": "settle:wait"}],
    [{"resource": "energy_kcal", "owner": "agent:1", "direction": "drain", "amount": 0,
      "cause": "settle:wait"}],
    [{"resource": "energy_kcal", "owner": "agent:1", "direction": "drain", "amount": -3,
      "cause": "settle:wait"}],
    [{"resource": "energy_kcal", "owner": "agent:1", "direction": "drain", "amount": True,
      "cause": "settle:wait"}],
    [{"resource": "energy_kcal", "owner": "agent:1", "direction": "drain",
      "amount": float("nan"), "cause": "settle:wait"}],
    [42],
])
def test_validate_resource_flows_rejects_invalid_payloads(payload) -> None:
    """校验函数对结构、词表与数值非法载荷 fail-closed。"""
    with pytest.raises(ValueError):
        validate_resource_flows(payload)


def test_validate_resource_flows_round_trips_the_canonical_shape() -> None:
    """合法分录字典列表原样通过并返回。"""
    payload = [{"resource": "energy_kcal", "owner": "agent:1", "direction": "drain",
                "amount": 1.5, "cause": "settle:wait:energy_cost"}]
    assert validate_resource_flows(payload) is payload
