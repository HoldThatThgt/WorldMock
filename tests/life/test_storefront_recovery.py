"""真实门店动作、逐供给呈现凭据和共同检查点恢复的合同验证。"""

from __future__ import annotations
from worldmock.footprints.checkpoint import restore_branch

import copy
import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall
from pydantic import ValidationError

from tests.life.conftest import (advance_next, advance_until_ready, bring_to_window, conclude_request, context, stage_day_plan,
                                 start_request, report_request)
from tests.life.test_scheduling import _custom_engine
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.engine import LifeEngine
from worldmock.life.map_models import QuoteConfig
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
            "price_version": "recovery-price-v2", "amount_fen": 700,
            "due_at": "2026-01-05T08:00:01+08:00", "source": _price_source()}]
        if getattr(request, "param", False):
            document["price_changes"].append({"change_id": "after-purchase-price", "supply_id": SUPPLY,
                "price_version": "recovery-price-v3", "amount_fen": 720,
                "due_at": "2026-01-05T08:15:01+08:00", "source": {
                    **_price_source(), "url": "scenario://storefront-recovery/after-purchase-price"}})
        document["product_releases"] = [{"release_id": "recovery-stock", "supply_id": SUPPLY,
            "stock": 17, "due_at": "2026-01-05T08:00:01+08:00", "source": _price_source()}]
    engine = _custom_engine(SCENARIO.name, configure, tmp_path, run_id="storefront-recovery")
    stage_day_plan(engine)
    staged = engine.context.state.model_copy(deep=True)
    staged.opening_exceptions[STORE] = {"2026-01-06": [], "2026-01-07": [(540, 720), (780, 1380)]}
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        source_kind="scenario", data={"kind": "test_opening_exceptions_declared"})])
    engine.context.state = staged
    yield engine, tmp_path / "scenarios/custom.json"
    engine.close()


def _action(engine, name, tool, request):
    """@brief 人物先实际观察，再按真实上下文提交一次动作。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observation = engine.observe(decision)
    if callable(request):
        request = request(observation)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    result = engine.commit_decision_round(name)[0]
    assert result.status in {"completed", "in_progress"}, result.error_code
    return observation, result


def _choice(observation, action, object_id=None, mode=None):
    """@brief 从本轮冻结观察中取可读对象及其明确的动词与方式（按内部标准动作与出行、履约方式选定）。"""
    return next(start_request(targets=[row["object"]], actions=[offered["verb"]], ways=[offered["label"]])
                for row in observation["interaction_bindings"]
                if object_id is None or row["object_id"] == object_id
                for offered in row["actions"] if offered["canonical_action"] == action
                and (mode is None or (offered.get("travel_mode") or offered.get("fulfillment")) == mode))


def _discover(engine, name):
    """@brief 通过可读商品目录活动发现当前商品窗口并推进完成。"""
    bring_to_window(engine, [SUPPLY])
    _, result = _action(engine, name, "start_activity", lambda observed: _choice(observed, "browse"))
    assert result.status == "in_progress"
    _finish_activity(engine, result.activity_id, name)
    observed = engine.observe(context(engine, f"{name}-feedback"))
    assert SUPPLY in {item["supply_id"] for item in observed["supplies"]}
    assert SUPPLY in engine.context.state.agents[1].visible_supply_ids
    return result, observed


def _named(identity, build):
    """@brief 按本轮观察私有绑定中该活动的可读名称（同轮重名时带序号）构造控制请求。"""
    return lambda observed: build(next(row["name"] for row in observed["activity_bindings"]
                                       if row["activity_id"] == identity))


def _run_activity(engine, identity, phase):
    """@brief 通过环境边界与按活动名的进展播报把活动推进到完成。"""
    for step in range(64):
        if engine.context.state.activities[identity].status != "active":
            assert engine.context.state.activities[identity].status == "completed"
            return
        if engine.scheduling_state().ready_agent_ids:
            _action(engine, f"{phase}-continue-{step}", "report_status", _named(
                identity, lambda name: report_request(name, summary="继续进行。")))
        else:
            advance_next(engine)
    pytest.fail("Activity did not complete through its declared event boundaries")


def _conclude(engine, identity, phase):
    """@brief 活动按计划结束即成待收尾，人物在同一秒给它收尾（规格第七章「活动一结束就要结束它」）。"""
    _action(engine, f"{phase}-end-{identity}", "conclude_activity", _named(identity, conclude_request))
    assert engine.context.state.activities[identity].concluded_at == engine.context.state.current_time


def _finish_activity(engine, identity, phase):
    """@brief 推进活动到完成并当秒收尾。"""
    _run_activity(engine, identity, phase)
    _conclude(engine, identity, phase)


def _purchase_selected_supply(engine, fulfillment):
    """@brief 到店分支真实旅行并重新发现；配送分支在居所明确下单。"""
    if fulfillment == "pickup":
        _, started = _action(engine, "travel-to-store", "start_activity",
                             lambda observed: _choice(observed, "travel_to", STORE, "walking"))
        _run_activity(engine, started.activity_id, "travel")
        assert engine.context.state.agents[1].location_id == STORE
        assert visible_supplies(engine.context.state, engine.context.state.agents[1], engine.context.scenario) == []
        _conclude(engine, started.activity_id, "travel")
        _discover(engine, "discover-after-arrival")
    _, result = _action(engine, "buy-selected", "start_activity",
                        start_request(targets=[engine.context.rules.items[PRODUCT].name],
                                      actions=["obtain"],
                                      ways=["到店" if fulfillment == "pickup" else "远程下单"],
                                      from_=[engine.context.rules.locations[STORE].name]))
    order = result.data["orders"][0]
    assert (order["supply_id"], order["product_id"], order["location_id"]) == (SUPPLY, PRODUCT, STORE)
    assert order["total_price_fen"] == 700 and order["price_version"] == "recovery-price-v2"
    assert order["source"]["quote"] == _price_source()
    assert (order["route_id"] is not None) == (fulfillment == "delivery")
    return order["order_id"]


def _business_state(engine):
    """@brief 提取重开和分支必须精确保留的世界事实与呈现证据。"""
    payload = engine.context.state.model_dump(mode="json")
    fields = ("current_time", "accounts", "supply_stock", "supply_quotes", "opening_exceptions",
              "agents", "activities", "deliveries", "pending_events", "presentation_state")
    return {**{name: payload[name] for name in fields}, "map_dataset": copy.deepcopy(
        engine.context.store.manifest["map_dataset"])}


def _directory_snapshot(root):
    """@brief 同时核对父目录全部文件字节及文件夹身份，不能只检查业务状态和清单。"""
    return {path.relative_to(root).as_posix(): (
        hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
        for path in sorted(root.rglob("*"))}


def _finish_order_and_consume(engine, order_id, phase):
    """@brief 待配送时明确等待真实到达，之后按商品身份消费到完成。"""
    for step in range(64):
        assert advance_until_ready(engine)
        if engine.context.state.deliveries[order_id].status == "delivered":
            break
        _, started = _action(engine, f"{phase}-await-delivery-{step}", "start_activity",
                             lambda observed: _choice(observed, "wait"))
        _finish_activity(engine, started.activity_id, f"{phase}-wait")
    order = engine.context.state.deliveries[order_id]
    assert order.status == "delivered"
    assert engine.context.state.agents[1].inventory[order.destination_id][PRODUCT] >= 1
    before = engine.context.state.agents[1].inventory[order.destination_id][PRODUCT]
    _, started = _action(engine, f"{phase}-consume", "start_activity",
                         lambda observed: _choice(observed, "consume", PRODUCT))
    _finish_activity(engine, started.activity_id, f"{phase}-eat")
    assert engine.context.state.activities[started.activity_id].data["consumed"][PRODUCT] == pytest.approx(1)
    assert engine.context.state.agents[1].inventory[order.destination_id][PRODUCT] == pytest.approx(before - 1)


@pytest.mark.parametrize("fulfillment", ["pickup", "delivery"])
def test_discovery_purchase_consumption_and_recovery_preserve_storefront_evidence(
        recovery_engine, tmp_path, fulfillment):
    """@brief 实际发现与下一秒新报价、购买、消费在关库重开和共同分支保持同一事实。"""
    original, scenario_path = recovery_engine
    discovery, observed = _discover(original, "discover-initial")
    selected = next(item for item in observed["supplies"] if item["supply_id"] == SUPPLY)
    assert selected["price_fen"] == 700 and selected["current_stock"] == 17
    assert selected["quote"]["source"] == _price_source()
    frozen = next(item for item in original.context.scenario.supplies if item.supply_id == SUPPLY)
    assert frozen.quotes[0].price_fen != selected["price_fen"]
    expected = _business_state(original)
    run_dir = original.context.store.run_dir
    original.close()
    stored = FootprintStore(run_dir)
    assert stored.state["supply_quotes"][SUPPLY]["price_fen"] == 700
    stored.close()
    reopened = LifeEngine.open(scenario_path, run_dir)
    branch = None
    try:
        assert _business_state(reopened) == expected
        assert reopened.context.state.agents[1].qualifications == ("test-store-member",)
        order_id = _purchase_selected_supply(reopened, fulfillment)
        expected = _business_state(reopened)
        reference = reopened.context.store.assets.put_json({"agent_id": 1}, {"kind": "storefront_recovery"})
        checkpoint = reopened.checkpoint({"agent:1": reference})
        _finish_order_and_consume(reopened, order_id, "parent")
        parent_after = _business_state(reopened)
        parent_files = _directory_snapshot(run_dir)
        child_store = restore_branch(reopened.context.store, checkpoint.checkpoint_id, tmp_path / 'restored-branch')
        child_store.close()
        assert _directory_snapshot(run_dir) == parent_files
        branch = LifeEngine.open(scenario_path, tmp_path / "restored-branch")
        assert _business_state(branch) == expected
        assert branch.context.state.run_id != reopened.context.state.run_id
        assert branch.context.store.manifest["parent_run_id"] == reopened.context.state.run_id
        _finish_order_and_consume(branch, order_id, "child")
        assert branch.context.state.supply_stock[SUPPLY] == reopened.context.state.supply_stock[SUPPLY] == 16
        assert branch.context.state.agents[1].inventory == reopened.context.state.agents[1].inventory
        assert branch.context.state.deliveries[order_id] == reopened.context.state.deliveries[order_id]
        assert _business_state(reopened) == parent_after
        assert _directory_snapshot(run_dir) == parent_files
        events = branch.context.store.read_agent_events("action_result", 1)
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
    payload = engine.context.state.model_dump(mode="json")
    supply = engine.context.scenario.supplies[0].supply_id
    _damage(payload, case, supply)
    payload["version"] += 1
    engine.context.store.commit(payload, [EventDraft(
        event_type="environment_event", occurred_at=payload["current_time"], visibility="system",
        source_kind="scenario", data={"kind": "test_corrupted_storefront_snapshot"})])
    run_dir = engine.context.store.run_dir
    engine.close()
    with pytest.raises((IntegrityError, ValidationError), match=reason):
        LifeEngine.open(SCENARIO, run_dir)


def test_reopening_rejects_map_identity_substitution(engine_factory):
    """@brief 恢复不能把原世界供给配到另一版地图声明。"""
    engine = engine_factory()
    run_dir = engine.context.store.run_dir
    engine.close()
    path = run_dir / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["map_dataset"]["supplies_file"]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="map identity"):
        LifeEngine.open(SCENARIO, run_dir)


def _quote_timeline(engine):
    """@brief 用明确金额构造相邻声明、未知间隙及顺序故意反转的同秒调价。"""
    scenario = engine.context.scenario
    start = scenario.start_at
    supply = next(item for item in scenario.supplies if item.supply_id == SUPPLY)
    base = supply.quotes[0].model_dump(mode="json")
    quotes = tuple(QuoteConfig.model_validate({
        **base, "price_version": version, "price_fen": price,
        "effective_from": start + timedelta(seconds=begin),
        "effective_to": start + timedelta(seconds=end) if end is not None else None,
    }) for version, price, begin, end in (
        ("declaration-a", 650, -60, 10), ("declaration-b", 660, 10, 20),
        ("declaration-c", 670, 30, 40), ("declaration-d", 680, 40, None)))
    supply = supply.model_copy(update={"quotes": quotes})
    changes = tuple(PriceChangeConfig.model_validate({
        "change_id": identity, "supply_id": SUPPLY, "price_version": f"change-{identity}",
        "amount_fen": price, "due_at": start + timedelta(seconds=due), "source": _price_source(),
    }) for identity, due, price in (("z-last", 10, 701), ("a-first", 10, 700),
                                   ("middle", 15, 703), ("after-gap", 35, 704)))
    return scenario.model_copy(update={"price_changes": changes, "supplies": tuple(
        supply if item.supply_id == SUPPLY else item for item in scenario.supplies)})


def _project_quote_events(engine, scenario, offset):
    """@brief 通过正式编译表和真实事件投影建立待恢复快照，独立于恢复核验函数。"""
    start = int(scenario.start_at.timestamp())
    state = engine.context.state.model_copy(update=initial_supply_state(scenario, start), deep=True)
    semantics = replace(engine.context.semantics, scenario=scenario)
    events = compile_schedule(scenario, start, int(scenario.end_at.timestamp()))
    for event in events:
        if event.due_at > start + offset:
            break
        if event.kind in {ScheduledKind.SUPPLY_QUOTE_CHANGE, ScheduledKind.PRICE_CHANGE}:
            state = state.model_copy(update={"current_time": event.due_at})
            state = apply_scheduled_effect(state, event, semantics).state
    return state.model_copy(update={"current_time": start + offset})


@pytest.mark.parametrize("offset,price,version,begin,end", [
    (0, 650, "declaration-a", -60, 10), (9, 650, "declaration-a", -60, 10),
    (10, 701, "change-z-last", 10, 20), (15, 703, "change-middle", 15, 20),
    (20, None, None, None, None), (29, None, None, None, None),
    (30, 670, "declaration-c", 30, 40), (35, 704, "change-after-gap", 35, 40),
    (40, 680, "declaration-d", 40, None),
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
    order_id = _purchase_selected_supply(engine, "delivery")
    paid = engine.context.state.deliveries[order_id].model_copy(deep=True)
    # 下单是瞬时动作：人物先在同一秒开始等待（时间与能量规格第一章规则 2），时钟才推进到调价边界。
    _action(engine, "wait-after-order", "start_activity", lambda observed: _choice(observed, "wait"))
    advance_next(engine)
    assert engine.context.state.current_time == paid.created_at + 1
    quote = engine.context.state.supply_quotes[SUPPLY]
    assert (quote.price_fen, quote.price_version) == (720, "recovery-price-v3")
    assert paid.price_version == "recovery-price-v2" and paid.total_price_fen == 700
    run_dir = engine.context.store.run_dir
    engine.close()
    reopened = LifeEngine.open(scenario_path, run_dir)
    try:
        assert reopened.context.state.supply_quotes[SUPPLY] == quote
        assert reopened.context.state.deliveries[order_id] == paid
        assert paid.source["quote"] == _price_source()
    finally:
        reopened.close()


def test_failed_decision_commit_preserves_visible_supply_credentials(recovery_engine):
    """提交失败后保留观察缓存，重试同一轮不会丢失供给凭据或重复结算。"""
    import sqlite3

    engine, _ = recovery_engine
    _discover(engine, "before-failed-storefront-commit")
    decision = context(engine, "failed-storefront-commit")
    engine.begin_decision_round([decision])
    observation = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, "failed-storefront-discover", "start_activity",
                                         _choice(observation, "browse")))
    before_state = engine.context.state.model_dump(mode="json")
    before_store = copy.deepcopy(engine.context.store.state)
    before_seq = engine.context.store.last_event_seq
    before_visible = copy.deepcopy(engine.observations.pending_visible_supplies)
    before_presentation = copy.deepcopy(engine.observations.pending_presentation)
    assert before_visible and before_presentation
    engine.context.store.connection.executescript("""
        CREATE TRIGGER fail_storefront_commit BEFORE UPDATE ON world_state
        BEGIN SELECT RAISE(ABORT, 'Injected storefront commit failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match="Injected storefront commit failure"):
        engine.commit_decision_round(decision.round_id)
    assert engine.context.state.model_dump(mode="json") == before_state
    assert engine.context.store.state == before_store
    assert engine.context.store.last_event_seq == before_seq
    assert engine.observations.pending_visible_supplies == before_visible
    assert engine.observations.pending_presentation == before_presentation
    engine.context.store.connection.execute("DROP TRIGGER fail_storefront_commit")
    result = engine.commit_decision_round(decision.round_id)[0]
    assert result.status == "in_progress"
    assert engine.observations.pending_visible_supplies == {}
    # 呈现留痕随提交写入世界状态的本秒暂存（时间与能量规格第一章「同一秒内的呈现」），引擎缓存清空。
    assert engine.observations.pending_presentation == {}
    assert engine.context.state.same_moment_presentation[1] == before_presentation[1]
    assert engine.context.state.accounts == before_state["accounts"]
    assert engine.context.state.supply_stock == before_state["supply_stock"]
