"""供给报价与品类闭集购买算子的真实运行整合契约。"""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.life.conftest import advance_next
from tests.life.test_category_runtime import (
    _action, _engine, _formal_fixture, _read_json, _replace_map_file, _save_fixture,
    _scenario_source, _set_pairing_coefficient, _set_pairing_undefined, _write_json,
)
from worldmock.categories import load_category_contract
from worldmock.errors import BusinessRuleError
from worldmock.life.basket import purchase_order
from worldmock.life.map_models import QuoteConfig
from worldmock.life.models import DiscoverPlacesRequest, PurchaseItem, PurchaseOrderRequest, SupplyQuoteState


PRODUCT = "food-6923644266066"
PRICES = {"supply-market-" + PRODUCT: 101, "supply-poi-3538682402-" + PRODUCT: 203}


@pytest.fixture
def priced_engine(tmp_path, request):
    """两家真实门店的同一商品有独立报价，购买系数为二分之一。"""
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    scenario["start_at"] = "2026-01-05T08:00:00+08:00"
    contract = _read_json(catalog_dir / "category-contract.json")
    configured = getattr(request, "param", {})
    _set_pairing_coefficient(contract, "product", "purchase", configured.get("coefficient", 0.5))
    _write_json(catalog_dir / "category-contract.json", contract)
    supplies = _read_json(Path("corpora/zhangjiang-map/supplies.json"))["supplies"]
    for supply in supplies:
        if supply["supply_id"] in PRICES:
            supply["quotes"][0].update(price_fen=PRICES[supply["supply_id"]], source=_scenario_source())
    _replace_map_file(path.parent.parent, scenario, "supplies", supplies)
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, "storefront-coefficient")
    yield engine, catalog_dir
    engine.close()


def _discover(engine, name="compare-storefronts"):
    """通过实际发现取得门店凭据，并等下一秒才能提交购买。"""
    now = engine.state.current_time
    result = _action(engine, name, "discover_places", DiscoverPlacesRequest(
        product_id=PRODUCT, business_kind=None, travel_mode="walking", max_travel_seconds=None, cursor=None))
    assert result.status.value == "completed", result.error_code
    rows = {row["supply_id"]: row for place in result.data["places"] for row in place["supplies"]}
    assert set(PRICES) <= set(rows)
    for identity in PRICES:
        assert rows[identity]["price_fen"] == engine.state.supply_quotes[identity].price_fen
    advance_next(engine)
    assert engine.state.current_time == now + 1
    return rows


def _order(engine, supply_id):
    """同一请求引用当前供给报价版本与三件数量。"""
    return PurchaseOrderRequest(items=(PurchaseItem(supply_id=supply_id, quantity=3,
        price_version=engine.state.supply_quotes[supply_id].price_version),),
        fulfillment="delivery", destination_id="home")


def test_independent_quotes_share_exact_category_arithmetic_in_real_engine(priced_engine):
    engine, _ = priced_engine
    compared = _discover(engine)
    for identity, price in PRICES.items():
        assert compared[identity]["purchase_coefficient"] == 0.5
        assert compared[identity]["purchase_price_fen"] == (price + 1) // 2
    seen_sellers = set()
    for index, (identity, price) in enumerate(PRICES.items()):
        if index:
            _discover(engine, f"compare-store-{index}")
        supply = engine.rules.supplies[identity]
        before_accounts = dict(engine.state.accounts)
        before_stock = dict(engine.state.supply_stock)
        before_quotes = dict(engine.state.supply_quotes)
        result = _action(engine, f"buy-store-{index}", "purchase", _order(engine, identity))
        assert result.status.value == "completed", result.error_code
        expected = (price * 3 + 1) // 2
        assert expected != ((price + 1) // 2) * 3
        assert result.data["total_price_fen"] == expected
        order = result.data["orders"][0]
        assert (order["supply_id"], order["product_id"], order["seller_account_id"]) == (
            identity, PRODUCT, supply.seller_account_id)
        assert order["total_price_fen"] == expected
        assert order["source"]["quote"] == before_quotes[identity].source
        assert result.data["category_matches"] == [{
            "category_id": "food-dairy", "rule_id": "purchase-product", "action": "purchase"}]
        assert result.data["resource_changes"] == {
            "account:agent:1": -expected, f"account:{supply.seller_account_id}": expected}
        expected_accounts = dict(before_accounts)
        expected_accounts["agent:1"] -= expected
        expected_accounts[supply.seller_account_id] += expected
        assert engine.state.accounts == expected_accounts
        expected_stock = dict(before_stock)
        expected_stock[identity] -= 3
        assert engine.state.supply_stock == expected_stock
        assert engine.state.supply_quotes == before_quotes
        assert engine.state.deliveries[order["order_id"]].total_price_fen == expected
        seen_sellers.add(supply.seller_account_id)
        advance_next(engine)
    assert len(seen_sellers) == 2


@pytest.mark.parametrize("state", ["undefined", "no_effect"])
def test_purchase_pairing_recheck_refuses_without_any_domain_state_change(priced_engine, state):
    engine, catalog_dir = priced_engine
    _discover(engine)
    identity = next(iter(PRICES))
    document = _read_json(catalog_dir / "category-contract.json")
    _set_pairing_undefined(document, "food-dairy", "purchase")
    if state == "no_effect":
        pairing = document["pairings"][-1]
        pairing.update(state="no_effect", review=document["review"])
    _write_json(catalog_dir / "category-contract.json", document)
    altered = load_category_contract(catalog_dir / "category-contract.json")
    semantics = replace(engine.semantics, catalog=replace(engine.semantics.catalog, category_contract=altered))
    assert semantics.purchase_pairing(PRODUCT).state.value == state
    before = engine.state.model_dump(mode="json")
    with pytest.raises(BusinessRuleError, match=f"^CATEGORY_PAIRING_{state.upper()}$"):
        purchase_order(engine.state, semantics, 1, _order(engine, identity))
    assert engine.state.model_dump(mode="json") == before


@pytest.mark.parametrize("priced_engine", [{"coefficient": 0}], indirect=True)
def test_defined_zero_coefficient_purchase_fulfills_without_account_transfer(priced_engine):
    engine, _ = priced_engine
    rows = _discover(engine)
    identity = next(iter(PRICES))
    assert rows[identity]["price_fen"] == PRICES[identity]
    assert rows[identity]["purchase_coefficient"] == 0
    assert rows[identity]["purchase_price_fen"] == 0
    accounts = dict(engine.state.accounts)
    stock = dict(engine.state.supply_stock)
    result = _action(engine, "buy-zero-amount", "purchase", _order(engine, identity))
    assert result.status.value == "completed", result.error_code
    assert result.data["total_price_fen"] == 0
    assert result.data["orders"][0]["total_price_fen"] == 0
    assert result.data["category_matches"] == [{
        "category_id": "food-dairy", "rule_id": "purchase-product", "action": "purchase"}]
    assert engine.state.accounts == accounts
    assert engine.state.supply_stock[identity] == stock[identity] - 3
    assert {key: value for key, value in engine.state.supply_stock.items() if key != identity} == {
        key: value for key, value in stock.items() if key != identity}
    order_id = result.data["orders"][0]["order_id"]
    inventory_before = engine.state.agents[1].inventory.get("home", {}).get(PRODUCT, 0)
    for _ in range(32):
        if engine.state.deliveries[order_id].status == "delivered":
            break
        advance_next(engine)
    assert engine.state.deliveries[order_id].status == "delivered"
    assert engine.state.agents[1].inventory["home"][PRODUCT] == inventory_before + 3
    assert engine.state.accounts == accounts


def test_zero_base_quote_remains_invalid_in_frozen_and_persisted_models(priced_engine):
    engine, _ = priced_engine
    supply = engine.rules.supplies[next(iter(PRICES))]
    frozen = supply.quotes[0].model_dump(mode="json")
    with pytest.raises(ValidationError, match="price_fen"):
        QuoteConfig.model_validate({**frozen, "price_fen": 0})
    persisted = engine.state.supply_quotes[supply.supply_id].model_dump(mode="json")
    with pytest.raises(ValidationError, match="price_fen"):
        SupplyQuoteState.model_validate({**persisted, "price_fen": 0})
