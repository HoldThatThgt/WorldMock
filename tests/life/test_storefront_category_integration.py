"""独立门店报价经冻结供给绑定与品类算子完成真实购买。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentsociety2.society import DirectToolCall
from tests.life.conftest import (advance_next, conclude_pending, context, start_activity, submit_round,
                                 target_actions, tool_targets)
from tests.life.test_category_runtime import (
    _engine, _formal_fixture, _read_json, _replace_map_file, _save_fixture,
    _scenario_source, _set_pairing_coefficient, _set_pairing_undefined,
    _stage_open_market, _write_json,
)
from worldmock.categories import load_category_contract
from worldmock.errors import BusinessRuleError
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.basket import purchase_order
from worldmock.life.map_models import QuoteConfig
from worldmock.life.models import PurchaseItem, PurchaseOrderRequest, SupplyQuoteState
from worldmock.life.readable import money_phrase


PRODUCT = "food-6923644266066"
PRICES = {"supply-market-" + PRODUCT: 101, "supply-poi-3538682402-" + PRODUCT: 203}


@pytest.fixture
def priced_engine(tmp_path, request):
    """两家真实门店的同一商品有独立报价，购买系数为二分之一。"""
    path, directory, scenario = _formal_fixture(tmp_path)
    scenario["start_at"] = "2026-01-05T08:00:00+08:00"
    scenario["supplies"] = [row for row in scenario["supplies"] if row["supply_id"] in PRICES]
    contract = _read_json(directory / "category-contract.json")
    configured = getattr(request, "param", {})
    _set_pairing_coefficient(contract, "product", "purchase", configured.get("coefficient", 0.5))
    _write_json(directory / "category-contract.json", contract)
    supplies = _read_json(Path("corpora/zhangjiang-map/supplies.json"))["supplies"]
    for supply in supplies:
        if supply["supply_id"] in PRICES:
            supply["quotes"][0].update(price_fen=PRICES[supply["supply_id"]], source=_scenario_source())
    _replace_map_file(path.parent.parent, scenario, "supplies", supplies)
    _save_fixture(path, directory, scenario)
    engine = _engine(tmp_path, path, "storefront-coefficient")
    yield engine, directory
    engine.close()


def _offer(engine, identity: str, round_id: str):
    """从本轮真实观察取指定门店的可读供给、公开价格和冻结绑定。"""
    current = context(engine, round_id)
    engine.begin_decision_round([current])
    observed = engine.observe(current)
    available = [row for row in observed["interaction_bindings"] if row["kind"] == "supply"]
    assert identity in {row["object_id"] for row in available}, [
        row["object_id"] for row in available if PRODUCT in row["object_id"]
    ]
    binding = next(row for row in available if row["object_id"] == identity)
    public = tool_targets(observed)[binding["object"]]
    assert public["kind"] == "product"
    return current, observed, binding, public


def _buy(engine, identity: str, round_id: str):
    """人物仅选商品供给与取得方式「远程下单」（obtain），由引擎采用冻结报价与单件数量。"""
    current, observed, binding, public = _offer(engine, identity, round_id)
    assert ("obtain", "远程下单", "delivery") in [
        (row["verb"], row["label"], row["fulfillment"]) for row in binding["actions"]]
    engine.submit_action(DirectToolCall(current, f"buy-{round_id}", "start_activity",
                                        start_activity("远程下单", binding["object"])))
    return observed, binding, public, engine.commit_decision_round(round_id)[0]


def _internal_order(engine, identity: str) -> PurchaseOrderRequest:
    """领域层复查继续接受三件内部订单，模型入口只允许单件。"""
    return PurchaseOrderRequest(items=(PurchaseItem(
        supply_id=identity, quantity=3,
        price_version=engine.state.supply_quotes[identity].price_version),),
        fulfillment="delivery", destination_id="home")


def test_independent_quotes_use_each_supply_frozen_price_in_real_engine(priced_engine):
    """两家门店各按自己报价结算，人物无需回填价格版本或计算结果。"""
    engine, _ = priced_engine
    _stage_open_market(engine)
    seen_sellers = set()
    for index, (identity, price) in enumerate(PRICES.items()):
        if index:
            # 下单是瞬时动作，第二家门店在同一秒的再决策轮中购买（时间与能量规格第一章规则 2）。
            _stage_open_market(engine)
        supply = engine.rules.supplies[identity]
        before_accounts = dict(engine.state.accounts)
        before_stock = dict(engine.state.supply_stock)
        before_quotes = dict(engine.state.supply_quotes)
        observed, binding, public, result = _buy(engine, identity, f"store-{index}")
        expected = (price + 1) // 2
        assert target_actions(observed, public["name"])["远程下单"]["price"] == money_phrase(expected)
        assert binding["quote"]["price_version"] == before_quotes[identity].price_version
        assert result.status.value == "completed"
        assert result.data["total_price_fen"] == expected
        order = result.data["orders"][0]
        assert order["supply_id"] == identity
        assert order["product_id"] == PRODUCT
        assert order["source"]["quote"] == before_quotes[identity].source
        assert result.data["category_matches"] == [{
            "category_id": "food-dairy", "rule_id": "purchase-product", "action": "purchase",
        }]
        assert engine.state.accounts["agent:1"] == before_accounts["agent:1"] - expected
        assert engine.state.accounts[supply.seller_account_id] == (
            before_accounts[supply.seller_account_id] + expected)
        assert engine.state.supply_stock[identity] == before_stock[identity] - 1
        assert engine.state.supply_quotes == before_quotes
        text = json.dumps(observed["tool_layer"], ensure_ascii=False)
        assert '"supply_id"' not in text and '"price_version"' not in text and identity not in text
        seen_sellers.add(supply.seller_account_id)
    assert len(seen_sellers) == 2


@pytest.mark.parametrize("state", ["undefined", "no_effect"])
def test_purchase_pairing_recheck_refuses_without_domain_state_change(priced_engine, state):
    """内部整单领域算子仍对后来变更的品类配对进行无损复查。"""
    engine, directory = priced_engine
    identity = next(iter(PRICES))
    document = _read_json(directory / "category-contract.json")
    _set_pairing_undefined(document, "food-dairy", "purchase")
    if state == "no_effect":
        document["pairings"][-1].update(state="no_effect", review=document["review"])
    _write_json(directory / "category-contract.json", document)
    altered = load_category_contract(directory / "category-contract.json")
    semantics = replace(engine.semantics, catalog=replace(engine.semantics.catalog,
                        category_contract=altered))
    before = engine.state.model_dump(mode="json")
    with pytest.raises(BusinessRuleError, match=f"^CATEGORY_PAIRING_{state.upper()}$"):
        purchase_order(engine.state, semantics, 1, _internal_order(engine, identity))
    assert engine.state.model_dump(mode="json") == before


@pytest.mark.parametrize("priced_engine", [{"coefficient": 0}], indirect=True)
def test_defined_zero_coefficient_purchase_has_no_account_transfer(priced_engine):
    """零系数订单仍减库存并交货，但不改变账户。"""
    engine, _ = priced_engine
    _stage_open_market(engine)
    identity = next(iter(PRICES))
    accounts = dict(engine.state.accounts)
    stock = engine.state.supply_stock[identity]
    observed, _, public, result = _buy(engine, identity, "zero-amount")
    assert target_actions(observed, public["name"])["远程下单"]["price"] == money_phrase(0)
    assert result.status.value == "completed"
    assert result.data["total_price_fen"] == 0
    assert engine.state.accounts == accounts
    assert engine.state.supply_stock[identity] == stock - 1
    order_id = result.data["orders"][0]["order_id"]
    for step in range(64):
        if engine.state.deliveries[order_id].status == "delivered":
            break
        # 没有进行中活动时时钟不推进（时间与能量规格第一章规则 1）：等配送时以等待度过；
        # 等待按计划结束的那一秒先给它收尾（第七章「活动一结束就要结束它」）。
        if pending_conclusions(engine.state, 1):
            conclude_pending(engine, f"await-end-{step}")
        elif engine.scheduling_state().ready_agent_ids:
            submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, f"await-{step}")
        else:
            advance_next(engine)
    assert engine.state.deliveries[order_id].status == "delivered"
    assert engine.state.agents[1].inventory["home"][PRODUCT] >= 1
    assert engine.state.accounts == accounts


def test_zero_base_quote_is_invalid_in_frozen_and_persisted_models(priced_engine):
    """基础报价必须为正，品类系数可使实际付款为零。"""
    engine, _ = priced_engine
    supply = engine.rules.supplies[next(iter(PRICES))]
    with pytest.raises(ValidationError, match="price_fen"):
        QuoteConfig.model_validate({**supply.quotes[0].model_dump(mode="json"), "price_fen": 0})
    persisted = engine.state.supply_quotes[supply.supply_id].model_dump(mode="json")
    with pytest.raises(ValidationError, match="price_fen"):
        SupplyQuoteState.model_validate({**persisted, "price_fen": 0})
