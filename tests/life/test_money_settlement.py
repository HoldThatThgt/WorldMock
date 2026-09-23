"""@brief 货币改道等价冒烟与 resource_flows 落迹正反例（docs/design/02-exchange-engine.md「校验资源分录」与「分阶段验证与完成条件」）。

一天夹具（multi-person）期末六账户对账参照 tests/resources/test_equivalence_anchor.py
的模式：把运行足迹中全部携带 resource_changes 的事件改写为 TransferEntry 序列，
经货币域账本从全零重放，期末六账户必须与引擎实际状态逐分一致，锚点吸收
-Σ期初注入且 closed 域守恒。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_until_ready, advance_next, context, stage_day_plan, submit_round
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


def _stage_open(engine) -> None:
    """把一天夹具移到商家营业时段并保留之后的真实后台事件。"""
    zone = ZoneInfo(engine.scenario.timezone)
    epoch = int(datetime(2026, 1, 5, 10, 0, tzinfo=zone).timestamp())
    staged = engine.state.model_copy(deep=True)
    staged.current_time = epoch
    staged.pending_events = [event for event in staged.pending_events if event.due_at > epoch]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(
            update={"next_decision_at": epoch, "body_settled_at": epoch}, deep=True)
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=epoch, visibility="system",
                       source_kind="scenario", data={"kind": "test_merchant_open"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def _wait() -> StartActivityRequest:
    """@brief 等待时长由场景反馈间隔派生。"""
    return StartActivityRequest(targets=["自己"], actions=["等待"])


def _choice(observation: dict, action: str, object_id: str | None = None,
            mode: str | None = None) -> StartActivityRequest:
    """@brief 在冻结观察中选可读对象和动作，报价与路线参数交给引擎。"""
    for row in observation["interaction_bindings"]:
        if object_id is not None and row["object_id"] != object_id:
            continue
        for offered in row["actions"]:
            if offered["canonical_action"] != action:
                continue
            if mode is not None and (offered.get("travel_mode") or offered.get("fulfillment")) != mode:
                continue
            return StartActivityRequest(targets=[row["object"]], actions=[offered["label"]])
    raise AssertionError(f"No observed choice for {action}: {object_id}")


def _selected_round(engine, round_id: str, agent_one_action: str,
                    object_id: str | None = None, mode: str | None = None):
    """@brief 观察所有就绪人物并提交本轮真实可读选择。"""
    ready = engine.scheduling_state().ready_agent_ids
    contexts = [context(engine, round_id, agent_id) for agent_id in ready]
    engine.begin_decision_round(contexts)
    observations = {item.agent_id: engine.observe(item) for item in contexts}
    for item in contexts:
        request = (_choice(observations[1], agent_one_action, object_id, mode)
                   if item.agent_id == 1 else _wait())
        engine.submit_action(DirectToolCall(item, f"action-{round_id}-{item.agent_id}",
                                            "start_activity", request))
    return sorted(engine.commit_decision_round(round_id), key=lambda item: item.agent_id)


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
            if value > 0:
                entries.append(TransferEntry(MONEY, payers[0], owner, value,
                                             f"footprints:{event.event_seq}", event.occurred_at))
    return entries


def test_one_day_fixture_reconciles_six_accounts_against_ledger(engine_factory) -> None:
    """一天夹具注资+购买+车费改道后，期末六账户可从足迹分录逐分重放对账。"""
    engine = engine_factory("multi-person.json")
    _stage_open(engine)
    stage_day_plan(engine, 1)
    stage_day_plan(engine, 2)
    assert _selected_round(engine, "browse", "browse")[0].status == "in_progress"
    advance_next(engine)
    purchase = _selected_round(engine, "buy", "purchase", mode="delivery")
    assert purchase[0].status in {"completed", "in_progress"}, purchase[0].error_code
    order = purchase[0].data["orders"][0]
    supply = next(item for item in engine.scenario.supplies if item.supply_id == order["supply_id"])
    price = order["total_price_fen"]
    # 修订决策节律：静默边界不再唤醒；循环推进直到出行发起者获得决策机会
    while 1 not in engine.scheduling_state().ready_agent_ids:
        ready_now = engine.scheduling_state().ready_agent_ids
        if ready_now:
            submit_round(engine, {
                agent_id: ("start_activity", _wait()) for agent_id in ready_now
            }, f"hold-{engine.state.current_time}")
        assert advance_until_ready(engine), "agent 1 never regained a decision"
    travel = _selected_round(engine, "to-office", "travel_to", "office", "driving")
    assert travel[0].status == "in_progress", travel[0].error_code

    accounts = dict(engine.state.accounts)
    # 修订合同正餐品类新增 merchant:restaurant 商家账户（v2 场景 7 个账本账户）。
    assert set(accounts) == set(engine.semantics.accounts)
    assert {"merchant:haode", "merchant:nonggongshang"} <= accounts.keys()
    assert "merchant:restaurant" in accounts
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

    settlement = engine.state.accounts[supply.seller_account_id] - \
        engine.semantics.accounts[supply.seller_account_id]
    assert settlement == price
    assert engine.state.accounts["agent:1"] == (
        engine.semantics.accounts["agent:1"] - price - 555)


def test_initial_injection_matches_semantics_accounts(engine_factory) -> None:
    """分录注资结果与语义初值账户逐分一致（含零初值账户）。"""
    engine = engine_factory("multi-person.json")
    assert engine.state.accounts == dict(engine.semantics.accounts)
    assert all(isinstance(value, int) for value in engine.state.accounts.values())
    accounts = dict(engine.semantics.accounts)
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
    events = engine.store.read_events(engine.store.last_event_seq)
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
    events = engine.store.read_events(engine.store.last_event_seq)
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
    # 第一段结束后开始下一段，共同身体账跨区间连续。
    assert advance_until_ready(engine)
    submit_round(engine, {1: ("start_activity", _wait()),
                          2: ("start_activity", _wait())}, "keep-waiting")
    advance_next(engine)
    bodies = sorted(
        (event for event in engine.store.read_events(engine.store.last_event_seq)
         if event.event_type == "environment_event" and event.agent_id == 1
         and event.data.get("kind") == "body_settlement"),
        key=lambda event: event.data["interval_end"])
    assert len(bodies) >= 2
    for event in bodies:
        drained = sum(flow["amount"] for flow in event.data[RESOURCE_FLOWS_FIELD]
                      if flow["resource"] == "energy_kcal" and flow["direction"] == "drain")
        settlement = event.data["body_settlement"]
        assert drained == settlement["before"]["energy_kcal"] - settlement["after"]["energy_kcal"]
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
