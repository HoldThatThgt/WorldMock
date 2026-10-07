"""@brief 货币改道等价冒烟与 resource_flows 落迹正反例（docs/design/02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎「校验资源分录」与「分阶段验证与完成条件」）。

一天夹具（multi-person）期末六账户对账参照 tests/resources/test_equivalence_anchor.py
的模式：把运行足迹中全部携带 resource_changes 的事件改写为 TransferEntry 序列，
经货币域账本从全零重放，期末登记账户必须与引擎实际状态逐分一致，锚点吸收
-Σ期初注入且 closed 域守恒。
"""

from __future__ import annotations

from decimal import Decimal
from math import ceil

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (advance_next, advance_until_ready, conclude_request, context, pending_names,
                                 stage_day_plan, submit_round, start_request)
from worldmock.footprints.models import (
    RESOURCE_FLOWS_FIELD,
    EventDraft,
    validate_resource_flows,
)
from worldmock.life.models import StartActivityRequest
from worldmock.life.settlement import (
    SCENARIO_ANCHOR_OWNER,
    inject_initial_funds,
    money_ledger,
)
from worldmock.resources import ResourceMeta, TransferEntry, audit_closed_domain

MONEY = "money_fen"


def _wait() -> StartActivityRequest:
    """@brief 等待时长由场景反馈间隔派生。"""
    return start_request(targets=["自己"], actions=["interact"], ways=["等待"])


def _conclude_round(engine, round_id: str) -> list:
    """@brief 全部就绪人物在活动结束的那一秒给各自刚结束的活动收尾（规格第七章「活动一结束就要结束它」）。"""
    ready = engine.scheduling_state().ready_agent_ids
    contexts = [context(engine, round_id, agent_id) for agent_id in ready]
    engine.begin_decision_round(contexts)
    names = {item.agent_id: pending_names(engine.observe(item)) for item in contexts}
    assert ready and all(names.values()), names
    for item in contexts:
        engine.submit_action(DirectToolCall(item, f"action-{round_id}-{item.agent_id}", "conclude_activity",
                                            conclude_request(*names[item.agent_id])))
    results = engine.commit_decision_round(round_id)
    assert all(item.status == "completed" for item in results)
    return results


def _transfer_entries(engine) -> list[TransferEntry]:
    """把足迹资金事件改写为双边转账分录（单一付款方、逐对金额相等）。"""
    events = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
              if event.data.get("resource_changes")]
    entries: list[TransferEntry] = []
    for event in events:
        changes = {key.removeprefix("account:"): value
                   for key, value in event.data["resource_changes"].items()}
        payers = [owner for owner, value in changes.items() if value < 0]
        assert len(payers) == 1 and sum(changes.values()) == 0, (event.event_seq, changes)
        for owner, value in changes.items():
            if value > 0:
                entries.append(TransferEntry(MONEY, payers[0], owner, value,
                                             f"footprints:{event.event_seq}", event.occurred_at))
    return entries


def test_one_day_fixture_reconciles_six_accounts_against_ledger(engine_factory) -> None:
    """真实固定菜单直接购买并按图支付车费后，登记账户可从足迹分录逐分重放对账。"""
    from tests.life.test_acs_amended_runtime import perform
    from tests.life.test_acs_runtime import request
    def owns_vehicle(document):
        document["agents"][0].update(owns_car=True, initial_driving_days=2)
    engine = engine_factory("multi-person.json", mutate=owns_vehicle)
    stage_day_plan(engine, 1)
    stage_day_plan(engine, 2)
    purchase = perform(engine, "buy", request("牛肉面", "美团", "购买", payment="微信"))
    assert purchase.error_code is None, purchase.data
    [order] = purchase.data["orders"]
    assert (order["goods_id"], order["app_id"], order["payment_app_id"]) == ("beef-noodles", "meituan", "wechat")
    seller = f"merchant:{order['store_id']}"
    assert order["seller_account_id"] == seller
    price = order["total_price_fen"]
    assert price == engine.context.semantics.catalog.goods[order["goods_id"]].price_fen + 500
    origin = engine.context.state.agents[1].location_id
    meters = engine.context.semantics.catalog.graph.distance_meters(origin, "office")
    destination = next(place.display_name for place in engine.context.scenario.locations if place.location_id == "office")
    travel = perform(engine, "to-office", request(f"地点：{destination}", None, "驾车"))
    assert travel.error_code is None, travel.data
    fare = max(500, ceil(Decimal(meters) / 5))
    assert engine.context.state.activities[travel.activity_id].data["travel_payment"]["fare_fen"] == fare

    accounts = dict(engine.context.state.accounts)
    assert set(accounts) == set(engine.context.semantics.accounts)
    assert {"merchant:noodle-1", "merchant:noodle-2", "merchant:cafe", "merchant:store"} <= accounts.keys()
    entries = _transfer_entries(engine)
    assert len(entries) >= 1 + 1 + 1, "expected injection + purchase + fare transfers"

    ledger = money_ledger()
    replayed = ledger.validate_history({(MONEY, owner): 0 for owner in accounts}, entries)
    assert {key: value for key, value in replayed.items()
            if key[1] in accounts} == {(MONEY, owner): value for owner, value in accounts.items()}
    assert replayed[(MONEY, SCENARIO_ANCHOR_OWNER)] == -sum(accounts.values())

    report = audit_closed_domain(ledger, MONEY, {(MONEY, owner): 0 for owner in accounts},
                                 entries, expected_final=accounts)
    assert report.real_final_total == report.injection_total == sum(accounts.values())
    assert report.final_total == report.initial_total == 0

    settlement = engine.context.state.accounts[seller] - engine.context.semantics.accounts[seller]
    assert settlement == price
    assert engine.context.state.accounts["agent:1"] == (
        engine.context.semantics.accounts["agent:1"] - price - fare)
    transport = engine.context.scenario.transport_account_id
    assert engine.context.state.accounts[transport] - engine.context.semantics.accounts[transport] == fare


def test_initial_injection_matches_semantics_accounts(engine_factory) -> None:
    """分录注资结果与语义初值账户逐分一致（含零初值账户）。"""
    engine = engine_factory("multi-person.json")
    assert engine.context.state.accounts == dict(engine.context.semantics.accounts)
    assert all(isinstance(value, int) for value in engine.context.state.accounts.values())
    accounts = dict(engine.context.semantics.accounts)
    accounts["agent:1"] = 0
    assert inject_initial_funds(accounts, 0) == accounts


def test_shared_body_event_carries_validated_body_flows(engine_factory) -> None:
    """共同身体结算只落一次分录，活动进度引用它且 owner 为人物账户。"""
    engine = engine_factory("multi-person.json")
    stage_day_plan(engine, 1)
    stage_day_plan(engine, 2)
    submit_round(engine, {1: ("start_activity", _wait()),
                          2: ("start_activity", _wait())}, "wait")
    advance_next(engine)
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    bodies = [event for event in events if event.event_type == "environment_event"
              and event.agent_id == 1 and event.data.get("kind") == "body_settlement"]
    progresses = [event for event in events if event.event_type == "activity_progress"
                  and event.agent_id == 1]
    assert len(bodies) == 1 and progresses
    assert all(event.data["body_settlement_event_id"] == bodies[0].event_id for event in progresses)
    flows = validate_resource_flows(bodies[0].data[RESOURCE_FLOWS_FIELD])
    assert {flow["resource"] for flow in flows} == {
        "energy_kcal", "entertainment_need"}  # 不足账由 sleep 纯函数演化，不分录
    assert {flow["owner"] for flow in flows} == {"agent:1"}
    assert all(flow["direction"] in ("source", "drain") and flow["amount"] > 0 for flow in flows)
    assert all(flow["cause"].startswith("settle:") for flow in flows)
    settlement = bodies[0].data["body_settlement"]
    drained = sum(flow["amount"] for flow in flows
                  if flow["resource"] == "energy_kcal" and flow["direction"] == "drain")
    assert drained == settlement["before"]["energy_kcal"] - settlement["after"]["energy_kcal"]


def test_non_settlement_events_never_carry_resource_flows(engine_factory) -> None:
    """动作事件和普通环境事件没有身体分录；共同结算事件独占公共身体分录。"""
    engine = engine_factory("multi-person.json")
    stage_day_plan(engine, 1)
    stage_day_plan(engine, 2)
    submit_round(engine, {1: ("start_activity", _wait()),
                          2: ("start_activity", _wait())}, "wait")
    advance_next(engine)
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    other = [event for event in events if event.event_type not in {"activity_progress", "environment_event"}
             or (event.event_type == "environment_event" and event.data.get("kind") != "body_settlement")]
    assert other and all(RESOURCE_FLOWS_FIELD not in event.data for event in other)
    assert any(RESOURCE_FLOWS_FIELD in event.data for event in events
               if event.event_type == "environment_event" and event.data.get("kind") == "body_settlement")


def test_advance_intervals_keep_flows_attributed_per_event(engine_factory) -> None:
    """两个区间各自取走本段分录，amount 与区间身体结算逐段对应。"""
    engine = engine_factory("multi-person.json")
    stage_day_plan(engine, 1)
    stage_day_plan(engine, 2)
    submit_round(engine, {1: ("start_activity", _wait()),
                          2: ("start_activity", _wait())}, "wait")
    # 第一段结束后当秒收尾再开始下一段（规格第七章），共同身体账跨区间连续。
    assert advance_until_ready(engine)
    _conclude_round(engine, "wait-end")
    submit_round(engine, {1: ("start_activity", _wait()),
                          2: ("start_activity", _wait())}, "keep-waiting")
    advance_next(engine)
    bodies = sorted(
        (event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
         if event.event_type == "environment_event" and event.agent_id == 1
         and event.data.get("kind") == "body_settlement"),
        key=lambda event: event.data["interval_end"])
    assert len(bodies) >= 2
    for event in bodies:
        drained = sum(flow["amount"] for flow in event.data[RESOURCE_FLOWS_FIELD]
                      if flow["resource"] == "energy_kcal" and flow["direction"] == "drain")
        settlement = event.data["body_settlement"]
        # 期初减期末是浮点减法；区间长度随体感跨档与餐时窗口边界变化，逐位相等只是巧合，按 1e-9 对应。
        assert drained == pytest.approx(
            settlement["before"]["energy_kcal"] - settlement["after"]["energy_kcal"], abs=1e-9)
    first, second = bodies[0], bodies[1]
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
