"""真实门店动作、逐供给呈现凭据和共同检查点恢复的合同验证。"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall
from pydantic import ValidationError

from tests.life.conftest import advance_next, advance_until_ready, context, stage_day_plan
from tests.life.test_scheduling import _custom_engine
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.engine import LifeEngine
from worldmock.life.map_models import QuoteConfig
from worldmock.life.models import (
    ConsumeItem, ConsumeRequest, ContinueActivityRequest, DiscoverPlacesRequest,
    PurchaseItem, PurchaseOrderRequest, StartBasicActivityRequest, StartTravelRequest,
)
from worldmock.life.projection import visible_supplies
from worldmock.life.scenario import PriceChangeConfig
from worldmock.life.storefront_state import initial_supply_state, map_provenance, validate_storefront_state
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import apply_scheduled_effect
from worldmock.scheduling.events import ScheduledKind


PRODUCT = "food-6923644266066"
STORE = "poi-3538682402"
SUPPLY = f"supply-{STORE}-{PRODUCT}"
SCENARIO = Path("scenarios/white-collar-12-weeks.json")


def _price_source():
    """@brief 新调价有独立的假设来源，不能从初始报价重新推断。"""
    return {"kind": "scenario", "url": "scenario://storefront-recovery/price-change",
            "retrieved_at": "2026-09-20T00:00:00+08:00", "license": "WorldMock-project",
            "notice": "Controlled recovery test assumption", "fact_cutoff": "2026-09-20",
            "applicability": "Explicit simulated price change on January 5; not a retail observation"}


@pytest.fixture
def recovery_engine(tmp_path, request):
    """@brief 正式地图与引擎使用早晨窗口、非空资格和真实预排调价补货。"""
    def configure(document):
        document["start_at"] = "2026-01-05T08:00:00+08:00"
        document["agents"][0]["qualifications"] = ["test-store-member"]
        document["price_changes"] = [{"change_id": "recovery-price", "supply_id": SUPPLY,
            "price_version": "recovery-price-v2", "amount_fen": 991,
            "due_at": "2026-01-05T08:00:01+08:00", "source": _price_source()}]
        if getattr(request, "param", False):
            document["price_changes"].append({"change_id": "after-purchase-price", "supply_id": SUPPLY,
                "price_version": "recovery-price-v3", "amount_fen": 1001,
                "due_at": "2026-01-05T08:00:02+08:00", "source": {
                    **_price_source(), "url": "scenario://storefront-recovery/after-purchase-price"}})
        document["product_releases"] = [{"release_id": "recovery-stock", "supply_id": SUPPLY,
            "stock": 17, "due_at": "2026-01-05T08:00:01+08:00", "source": _price_source()}]
    engine = _custom_engine(SCENARIO.name, configure, tmp_path, run_id="storefront-recovery")
    stage_day_plan(engine)
    staged = engine.state.model_copy(deep=True)
    staged.opening_exceptions[STORE] = {"2026-01-06": [], "2026-01-07": [(540, 720), (780, 1380)]}
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        source_kind="scenario", data={"kind": "test_opening_exceptions_declared"})])
    engine.state = staged
    yield engine, tmp_path / "scenarios/custom.json"
    engine.close()


def _action(engine, name, tool, request):
    """@brief 人物先实际观察，再按真实上下文提交一次动作。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observation = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    result = engine.commit_decision_round(name)[0]
    assert result.status in {"completed", "in_progress"}, result.error_code
    return observation, result


def _discover(engine, name):
    """@brief 发现同商品的全部门店，并严格推进到下一秒再观察。"""
    now = engine.state.current_time
    _, result = _action(engine, name, "discover_places", DiscoverPlacesRequest(
        product_id=PRODUCT, business_kind=None, travel_mode="walking", max_travel_seconds=None, cursor=None))
    assert result.data["next_cursor"] is None and result.data["returned"] == 3
    rows = {item["supply_id"]: item for place in result.data["places"] for item in place["supplies"]}
    assert {item["product_id"] for item in rows.values()} == {PRODUCT}
    assert engine.state.agents[1].visible_supply_ids == set(rows)
    advance_next(engine)
    assert engine.state.current_time == now + 1
    observed = engine.observe(context(engine, f"{name}-feedback"))
    assert set(rows) <= {item["supply_id"] for item in observed["supplies"]}
    return result, observed


def _finish_activity(engine, identity, phase):
    """@brief 仅通过环境边界与明确继续动作完成活动。"""
    for step in range(64):
        if engine.state.activities[identity].status != "active":
            assert engine.state.activities[identity].status == "completed"
            return
        if engine.scheduling_state().ready_agent_ids:
            _action(engine, f"{phase}-continue-{step}", "continue_activity",
                    ContinueActivityRequest(activity_id=identity))
        else:
            advance_next(engine)
    pytest.fail("Activity did not complete through its declared event boundaries")


def _purchase_selected_supply(engine, fulfillment, discovery):
    """@brief 到店分支真实旅行并重新发现；配送分支在居所明确下单。"""
    if fulfillment == "pickup":
        place = next(row for row in discovery.data["places"] if row["location_id"] == STORE)
        route = place["route"]
        _, started = _action(engine, "travel-to-store", "start_travel", StartTravelRequest(
            destination_id=STORE, travel_mode="walking", planned_duration_seconds=route["duration_seconds"]))
        _finish_activity(engine, started.activity_id, "travel")
        assert engine.state.agents[1].location_id == STORE
        assert visible_supplies(engine.state, engine.state.agents[1], engine.scenario) == []
        _discover(engine, "discover-after-arrival")
    destination = STORE if fulfillment == "pickup" else "home"
    quote = engine.state.supply_quotes[SUPPLY]
    _, result = _action(engine, "buy-selected", "purchase", PurchaseOrderRequest(
        items=(PurchaseItem(supply_id=SUPPLY, quantity=1, price_version=quote.price_version),),
        fulfillment=fulfillment, destination_id=destination))
    order = result.data["orders"][0]
    assert (order["supply_id"], order["product_id"], order["location_id"]) == (SUPPLY, PRODUCT, STORE)
    assert order["total_price_fen"] == 991 and order["price_version"] == "recovery-price-v2"
    assert order["source"]["quote"] == _price_source()
    assert (order["route_id"] is not None) == (fulfillment == "delivery")
    return order["order_id"]


def _business_state(engine):
    """@brief 提取重开和分支必须精确保留的世界事实与呈现证据。"""
    payload = engine.state.model_dump(mode="json")
    fields = ("current_time", "accounts", "supply_stock", "supply_quotes", "opening_exceptions",
              "agents", "activities", "deliveries", "pending_events", "presentation_state")
    return {**{name: payload[name] for name in fields}, "map_dataset": copy.deepcopy(
        engine.store.manifest["map_dataset"])}


def _directory_snapshot(root):
    """@brief 同时核对父目录全部文件字节及文件夹身份，不能只检查业务状态和清单。"""
    return {path.relative_to(root).as_posix(): (
        hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
        for path in sorted(root.rglob("*"))}


def _finish_order_and_consume(engine, order_id, phase):
    """@brief 待配送时明确等待真实到达，之后按商品身份消费到完成。"""
    assert advance_until_ready(engine)
    order = engine.state.deliveries[order_id]
    if order.status == "pending":
        _, started = _action(engine, f"{phase}-await-delivery", "start_basic_activity", StartBasicActivityRequest(
            activity_type="wait", planned_duration_seconds=order.expected_at - engine.state.current_time))
        _finish_activity(engine, started.activity_id, f"{phase}-wait")
    order = engine.state.deliveries[order_id]
    assert order.status == "delivered"
    assert engine.state.agents[1].inventory[order.destination_id][PRODUCT] >= 1
    before = engine.state.agents[1].inventory[order.destination_id][PRODUCT]
    product = engine.rules.products[PRODUCT]
    _, started = _action(engine, f"{phase}-consume", "consume", ConsumeRequest(
        items=(ConsumeItem(product_id=PRODUCT, quantity=1),), use_mode="eat",
        duration_seconds=product.use_duration_seconds))
    _finish_activity(engine, started.activity_id, f"{phase}-eat")
    assert engine.state.activities[started.activity_id].data["consumed"][PRODUCT] == pytest.approx(1)
    assert engine.state.agents[1].inventory[order.destination_id][PRODUCT] == pytest.approx(before - 1)


@pytest.mark.parametrize("fulfillment", ["pickup", "delivery"])
def test_discovery_purchase_consumption_and_recovery_preserve_storefront_evidence(recovery_engine, tmp_path, fulfillment):
    """@brief 实际发现与下一秒新报价、购买、消费在关库重开和共同分支保持同一事实。"""
    original, scenario_path = recovery_engine
    discovery, observed = _discover(original, "discover-initial")
    selected = next(item for item in observed["supplies"] if item["supply_id"] == SUPPLY)
    assert selected["price_fen"] == 991 and selected["current_stock"] == 17
    assert selected["quote"]["source"] == _price_source()
    frozen = next(item for item in original.scenario.supplies if item.supply_id == SUPPLY)
    assert frozen.quotes[0].price_fen != selected["price_fen"]
    expected = _business_state(original)
    run_dir = original.store.run_dir
    original.close()
    stored = FootprintStore(run_dir)
    assert stored.state["supply_quotes"][SUPPLY]["price_fen"] == 991
    stored.close()
    reopened = LifeEngine.open(scenario_path, run_dir)
    branch = None
    try:
        assert _business_state(reopened) == expected
        assert reopened.state.agents[1].qualifications == ("test-store-member",)
        order_id = _purchase_selected_supply(reopened, fulfillment, discovery)
        expected = _business_state(reopened)
        reference = reopened.store.assets.put_json({"agent_id": 1}, {"kind": "storefront_recovery"})
        checkpoint = reopened.checkpoint({"agent:1": reference})
        _finish_order_and_consume(reopened, order_id, "parent")
        parent_after = _business_state(reopened)
        parent_files = _directory_snapshot(run_dir)
        child_store = reopened.store.restore(checkpoint.checkpoint_id, tmp_path / "restored-branch")
        child_store.close()
        assert _directory_snapshot(run_dir) == parent_files
        branch = LifeEngine.open(scenario_path, tmp_path / "restored-branch")
        assert _business_state(branch) == expected
        assert branch.state.run_id != reopened.state.run_id
        assert branch.store.manifest["parent_run_id"] == reopened.state.run_id
        _finish_order_and_consume(branch, order_id, "child")
        assert branch.state.supply_stock[SUPPLY] == reopened.state.supply_stock[SUPPLY] == 16
        assert branch.state.agents[1].inventory == reopened.state.agents[1].inventory
        assert branch.state.deliveries[order_id] == reopened.state.deliveries[order_id]
        assert _business_state(reopened) == parent_after
        assert _directory_snapshot(run_dir) == parent_files
        events = branch.store.read_agent_events("action_result", 1)
        recorded = next(event.data["data"] for event in events if event.action_id == "action-discover-initial")
        assert recorded == discovery.data
    finally:
        if branch is not None:
            branch.close()
        reopened.close()


def _damage(payload, case, supply):
    """@brief 每次只破坏一个需要在恢复时拒绝的门店领域不变量。"""
    agent = payload["agents"]["1"]
    if case == "quote-source":
        payload["supply_quotes"][supply]["source"] = {}
    elif case == "quote-price":
        payload["supply_quotes"][supply]["price_fen"] = 1
    elif case == "quote-version":
        payload["supply_quotes"][supply]["price_version"] = "never-declared"
    elif case == "quote-unrelated-source":
        payload["supply_quotes"][supply]["source"]["url"] = "scenario://unrelated-source"
    elif case == "quote-start":
        payload["supply_quotes"][supply]["effective_from"] += 1
    elif case == "quote-end":
        payload["supply_quotes"][supply]["effective_to"] = payload["current_time"] + 3600
    elif case == "quote-unknown":
        payload["supply_quotes"][supply] = None
    elif case == "opening-date":
        payload["opening_exceptions"]["home"] = {"not-a-date": []}
    elif case == "opening-reversed":
        payload["opening_exceptions"]["home"] = {"2026-01-05": [[900, 600]]}
    elif case == "opening-overlap":
        payload["opening_exceptions"]["home"] = {"2026-01-05": [[600, 900], [800, 1000]]}
    elif case == "qualification":
        agent["qualifications"] = ["undeclared-member"]
    else:
        agent["visible_supply_ids"] = [supply]
        agent["supply_browsed_at"] = {supply: payload["current_time"] + (case == "visible-future")}
        agent["supply_browsed_location"] = {supply: "missing-place" if case == "visible-place" else "home"}


@pytest.mark.parametrize("case,reason", [
    ("quote-source", "source"), ("opening-date", "opening"), ("opening-reversed", "opening"),
    ("opening-overlap", "opening"), ("qualification", "qualification"),
    ("visible-future", "visibility"), ("visible-place", "visibility"),
    ("quote-price", "quote timeline"), ("quote-version", "quote timeline"),
    ("quote-unrelated-source", "quote timeline"), ("quote-start", "quote timeline"),
    ("quote-end", "quote timeline"), ("quote-unknown", "quote timeline"),
])
def test_reopening_rejects_corrupted_storefront_facts(engine_factory, case, reason):
    """@brief SQLite 内容虽可解析，缺来源、损坏营业和伪造可见凭据仍必须拒绝恢复。"""
    engine = engine_factory()
    payload = engine.state.model_dump(mode="json")
    supply = engine.scenario.supplies[0].supply_id
    _damage(payload, case, supply)
    payload["version"] += 1
    engine.store.commit(payload, [EventDraft(
        event_type="environment_event", occurred_at=payload["current_time"], visibility="system",
        source_kind="scenario", data={"kind": "test_corrupted_storefront_snapshot"})])
    run_dir = engine.store.run_dir
    engine.close()
    with pytest.raises((IntegrityError, ValidationError), match=reason):
        LifeEngine.open(SCENARIO, run_dir)


def test_reopening_rejects_map_identity_substitution(engine_factory):
    """@brief 恢复不能把原世界供给配到另一版地图声明。"""
    engine = engine_factory()
    run_dir = engine.store.run_dir
    engine.close()
    path = run_dir / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["map_dataset"]["supplies_file"]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="map identity"):
        LifeEngine.open(SCENARIO, run_dir)


def _quote_timeline(engine):
    """@brief 用明确金额构造相邻声明、未知间隙及顺序故意反转的同秒调价。"""
    scenario = engine.scenario
    start = scenario.start_at
    supply = next(item for item in scenario.supplies if item.supply_id == SUPPLY)
    base = supply.quotes[0].model_dump(mode="json")
    quotes = tuple(QuoteConfig.model_validate({
        **base, "price_version": version, "price_fen": price,
        "effective_from": start + timedelta(seconds=begin),
        "effective_to": start + timedelta(seconds=end) if end is not None else None,
    }) for version, price, begin, end in (
        ("declaration-a", 111, -60, 10), ("declaration-b", 222, 10, 20),
        ("declaration-c", 333, 30, 40), ("declaration-d", 444, 40, None)))
    supply = supply.model_copy(update={"quotes": quotes})
    changes = tuple(PriceChangeConfig.model_validate({
        "change_id": identity, "supply_id": SUPPLY, "price_version": f"change-{identity}",
        "amount_fen": price, "due_at": start + timedelta(seconds=due), "source": _price_source(),
    }) for identity, due, price in (("z-last", 10, 992), ("a-first", 10, 991),
                                   ("middle", 15, 993), ("after-gap", 35, 994)))
    return scenario.model_copy(update={"price_changes": changes, "supplies": tuple(
        supply if item.supply_id == SUPPLY else item for item in scenario.supplies)})


def _project_quote_events(engine, scenario, offset):
    """@brief 通过正式编译表和真实事件投影建立待恢复快照，独立于恢复核验函数。"""
    start = int(scenario.start_at.timestamp())
    state = engine.state.model_copy(update=initial_supply_state(scenario, start), deep=True)
    semantics = replace(engine.semantics, scenario=scenario)
    events = compile_schedule(scenario, start, int(scenario.end_at.timestamp()))
    for event in events:
        if event.due_at > start + offset:
            break
        if event.kind in {ScheduledKind.SUPPLY_QUOTE_CHANGE, ScheduledKind.PRICE_CHANGE}:
            state = state.model_copy(update={"current_time": event.due_at})
            state = apply_scheduled_effect(state, event, semantics).state
    return state.model_copy(update={"current_time": start + offset})


@pytest.mark.parametrize("offset,price,version,begin,end", [
    (0, 111, "declaration-a", -60, 10), (9, 111, "declaration-a", -60, 10),
    (10, 992, "change-z-last", 10, 20), (15, 993, "change-middle", 15, 20),
    (20, None, None, None, None), (29, None, None, None, None),
    (30, 333, "declaration-c", 30, 40), (35, 994, "change-after-gap", 35, 40),
    (40, 444, "declaration-d", 40, None),
])
def test_restore_checks_quote_boundaries_and_same_second_order_without_rewriting(
        engine_factory, offset, price, version, begin, end):
    """@brief 恢复遵循正式排序和半开区间，正确报价只被核验而不重置世界。"""
    engine = engine_factory()
    scenario = _quote_timeline(engine)
    state = _project_quote_events(engine, scenario, offset)
    before = state.model_dump(mode="json")
    quote = state.supply_quotes[SUPPLY]
    start = int(scenario.start_at.timestamp())
    if price is None:
        assert quote is None
    else:
        assert quote.price_fen == price and quote.price_version == version
        assert quote.effective_from == start + begin
        assert quote.effective_to == (start + end if end is not None else None)
        if version.startswith("change-"):
            assert quote.source == _price_source()
    validate_storefront_state(state, scenario, {"map_dataset": map_provenance(scenario)})
    assert state.model_dump(mode="json") == before


def test_restore_rejects_expired_quote_kept_in_declaration_gap(engine_factory):
    """@brief 未知间隙必须持久化为空，不能保留看似只读过滤后等价的过期报价。"""
    engine = engine_factory()
    scenario = _quote_timeline(engine)
    state = _project_quote_events(engine, scenario, 20)
    state.supply_quotes[SUPPLY] = _project_quote_events(engine, scenario, 15).supply_quotes[SUPPLY]
    with pytest.raises(IntegrityError, match="quote timeline"):
        validate_storefront_state(state, scenario, {"map_dataset": map_provenance(scenario)})


@pytest.mark.parametrize("recovery_engine", [True], indirect=True)
def test_reopening_after_price_change_preserves_paid_order_price(recovery_engine):
    """@brief 新报价到期后重开核验当前供给，已付款配送仍保持旧成交金额和来源。"""
    engine, scenario_path = recovery_engine
    discovery, _ = _discover(engine, "discover-before-purchase")
    order_id = _purchase_selected_supply(engine, "delivery", discovery)
    paid = engine.state.deliveries[order_id].model_copy(deep=True)
    advance_next(engine)
    assert engine.state.current_time == paid.created_at + 1
    quote = engine.state.supply_quotes[SUPPLY]
    assert (quote.price_fen, quote.price_version) == (1001, "recovery-price-v3")
    assert paid.price_version == "recovery-price-v2" and paid.total_price_fen == 991
    run_dir = engine.store.run_dir
    engine.close()
    reopened = LifeEngine.open(scenario_path, run_dir)
    try:
        assert reopened.state.supply_quotes[SUPPLY] == quote
        assert reopened.state.deliveries[order_id] == paid
        assert paid.source["quote"] == _price_source()
    finally:
        reopened.close()
