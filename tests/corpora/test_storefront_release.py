"""完整门店集合、确定性冻结报价和真实到店购买的行为门禁。"""

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from worldmock.catalog.loader import load_catalog, load_object_declarations
from worldmock.categories import AttributeValues, resolve_object_values
from worldmock.corpora.storefront import assortment_objects, frozen_price, validate_assortments
from worldmock.corpora.storefront_build import build_storefront_supplies
from worldmock.errors import IntegrityError
from worldmock.life.basket import purchase_order
from worldmock.life.map_data import load_map_dataset, select_map
from worldmock.life.map_models import MapDatasetReference
from worldmock.life.models import AgentState, BodyState, PurchaseOrderRequest, WORLD_SCHEMA_VERSION, WorldState
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.storefront_state import initial_supply_state


@pytest.fixture
def release():
    """真实冻结目录和种子地图，不生成额外测试商品。"""
    catalog = load_catalog(Path("catalog"))
    path = Path("corpora/zhangjiang-map/20260105/dataset.json")
    pin = MapDatasetReference(dataset_id="zhangjiang-map", version=3,
                              path="zhangjiang-map/20260105/dataset.json",
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return catalog, load_map_dataset(Path("corpora"), pin)


def test_complete_assortments_have_real_stock_and_distinct_menus(release):
    """每家门店实际供给严格等于声明集合，丰富度和双方独有菜单成立。"""
    catalog, dataset = release
    validate_assortments(dataset, catalog)
    counts = {item.location_id: len(assortment_objects(item, catalog))
              for item in dataset.category_assignment.locations}
    assert counts["poi-3538682402"] >= 40
    assert counts["poi-3538763552"] >= 80
    assert counts["poi-3538684972"] == 9
    assert len(dataset.supplies) == sum(counts.values()) == 431
    assert all(supply.initial_stock > 0 for supply in dataset.supplies)
    menus = [assortment_objects(item, catalog) for item in dataset.category_assignment.locations
             if item.location_id in {"poi-3538660453", "poi-3538682404", "poi-3538682408"}]
    assert all(len(first - second) >= 3 for first in menus for second in menus if first is not second)
    assert not any(identity.startswith("meal-") for declaration in dataset.category_assignment.locations
                   if declaration.location_id == "poi-3538682402"
                   for identity in assortment_objects(declaration, catalog))


def test_repeated_menus_are_rejected_even_with_valid_complete_supplies(release):
    """复制完整菜单不能依赖其它供给错误偶然阻止发布。"""
    catalog, dataset = release
    declarations = list(dataset.category_assignment.locations)
    indices = [index for index, item in enumerate(declarations)
               if item.location_id in {"poi-3538660453", "poi-3538682404"}]
    declarations[indices[1]] = declarations[indices[1]].model_copy(
        update={"object_ids": declarations[indices[0]].object_ids})
    assignment = dataset.category_assignment.model_copy(update={"locations": tuple(declarations)})
    assumptions = json.loads(Path("corpora/zhangjiang-map/supply-assumptions.json").read_text(encoding="utf-8"))
    supplies = build_storefront_supplies(catalog, assignment, dataset.locations, assumptions, dataset.random_seed)
    changed = dataset.model_copy(update={"category_assignment": assignment, "supplies": supplies})
    with pytest.raises(IntegrityError, match="restaurant menu pair"):
        validate_assortments(changed, catalog)


def test_price_formula_is_reproducible_order_independent_and_positioned(release):
    """固定身份独立计算全部报价，改变构建顺序不会重抽或改变门店价序。"""
    catalog, dataset = release
    declarations = {item.location_id: item for item in dataset.category_assignment.locations}
    for supply in dataset.supplies:
        obj = catalog.objects[supply.product_id]
        raw = json.dumps([dataset.random_seed, supply.location_id, obj.object_id],
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        jitter = int.from_bytes(hashlib.sha256(raw).digest(), "big") % 11 - 5
        lower, upper = (obj.attributes[key] for key in ("purchase_price_fen_min", "purchase_price_fen_max"))
        position = declarations[supply.location_id].price_position_percent
        assert supply.quotes[0].price_fen == lower + ((upper - lower) * (position + jitter) + 50) // 100
        assert lower <= supply.quotes[0].price_fen <= upper
        assert frozen_price(dataset.random_seed, supply.location_id, obj, 65) >= frozen_price(
            dataset.random_seed, "another-shop", obj, 55)
        assert frozen_price(dataset.random_seed, supply.location_id, obj, position) == supply.quotes[0].price_fen
    assumptions = json.loads(Path("corpora/zhangjiang-map/supply-assumptions.json").read_text(encoding="utf-8"))
    reversed_catalog = replace(catalog, objects=dict(reversed(list(catalog.objects.items()))))
    regenerated = build_storefront_supplies(reversed_catalog, dataset.category_assignment,
                                           tuple(reversed(dataset.locations)), assumptions, dataset.random_seed)
    assert regenerated == dataset.supplies


def test_different_scenario_seeds_have_independently_frozen_quotes(release):
    """同地区共享地点路线，每个实验种子钉定自己的报价文件。"""
    _, first = release
    path = Path("corpora/zhangjiang-map/20260106/dataset.json")
    pin = MapDatasetReference(dataset_id="zhangjiang-map", version=3,
                              path="zhangjiang-map/20260106/dataset.json",
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    second = load_map_dataset(Path("corpora"), pin)
    assert second.random_seed == 20260106
    assert first.locations == second.locations and first.routes == second.routes
    assert any(a.quotes[0].price_fen != b.quotes[0].price_fen
               for a, b in zip(first.supplies, second.supplies, strict=True))


def test_price_ranges_inherit_categories_preserve_overrides_and_allow_fixed_prices(release):
    """品类默认与子品类值真实继承，对象覆盖保持；合法相同上下界生成固定整数价格。"""
    catalog, dataset = release
    expected = {"ingredient-broccoli": (100, 2000), "ingredient-vegetable-kit": (500, 1500),
                "food-6923644266066": (612, 748)}
    for identity, bounds in expected.items():
        obj = catalog.objects[identity]
        assert tuple(obj.attributes[key] for key in ("purchase_price_fen_min", "purchase_price_fen_max")) == bounds
    obj = catalog.objects["food-6923644266066"]
    fixed = obj.model_copy(update={"attributes": {**obj.attributes, "purchase_price_fen_min": 713,
                                                  "purchase_price_fen_max": 713}})
    for declaration in dataset.category_assignment.locations:
        assert frozen_price(dataset.random_seed, declaration.location_id, fixed,
                            declaration.price_position_percent) == 713


def test_effective_price_range_rejects_reversed_object_override(release):
    """每个单独槽值合法仍不能覆盖成上下界倒挂的实际商品范围。"""
    catalog, _ = release
    declaration = next(item for item in load_object_declarations(Path("catalog/objects.jsonl"))
                       if item.object_id == "food-6923644266066")
    values = declaration.values.model_dump(mode="json")
    values["absolute"].update(purchase_price_fen_min=748, purchase_price_fen_max=612)
    changed = declaration.model_copy(update={"values": AttributeValues.model_validate(values)})
    with pytest.raises(ValueError, match="CATEGORY_PRICE_RANGE_INVALID"):
        resolve_object_values(changed, catalog.category_contract)


def test_selected_store_must_keep_every_declared_supply(release):
    """正式场景不能选择丰富地图却只运行二十条旧供给。"""
    _, dataset = release
    pin = SimpleNamespace(dataset_id=dataset.dataset_id, version=dataset.version)
    document = SimpleNamespace(map_dataset=pin, timezone=dataset.timezone, random_seed=dataset.random_seed,
                               locations=[SimpleNamespace(location_id=item.location_id) for item in dataset.locations],
                               supplies=[SimpleNamespace(supply_id=item.supply_id) for item in dataset.supplies[:20]])
    with pytest.raises(IntegrityError, match="complete declared supply sets"):
        select_map(dataset, document)


def test_out_of_range_frozen_quote_is_rejected(release):
    """即使供给集合完整，也不能发布越出商品范围的报价。"""
    catalog, dataset = release
    supply = dataset.supplies[0]
    upper = catalog.objects[supply.product_id].attributes["purchase_price_fen_max"]
    invalid = supply.model_copy(update={"quotes": (supply.quotes[0].model_copy(update={"price_fen": upper + 1}),)})
    changed = dataset.model_copy(update={"supplies": (invalid, *dataset.supplies[1:])})
    with pytest.raises(IntegrityError, match="outside its product price bounds"):
        validate_assortments(changed, catalog)


def test_every_published_product_can_really_be_bought_at_its_store(release):
    """正常营业时逐店购买全部声明商品，真实付款、扣本店库存和即时交付。"""
    catalog, dataset = release
    now = int(datetime.fromisoformat("2026-01-05T12:00:00+08:00").timestamp())
    scenario = SimpleNamespace(supplies=dataset.supplies, locations=dataset.locations,
                               timezone=dataset.timezone, routes=dataset.routes)
    semantics = ScenarioSemantics(None, catalog, scenario, None, None, {}, None)
    agent = AgentState(agent_id=1, name="测试人物", location_id="home", balance_account_id="agent:1",
                       body_settled_at=now, body=BodyState(energy_kcal=1800, entertainment_need=0.2),
                       next_decision_at=now)
    accounts = {item.seller_account_id: 0 for item in dataset.supplies}
    state = WorldState(schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=now,
                       end_time=now + 86400, run_id="complete-store-purchase", timezone=dataset.timezone,
                       scenario_sha256="a" * 64, content_manifest_sha256="b" * 64, agents={1: agent},
                       accounts={**accounts, "agent:1": 10_000_000}, random_seed=20260105, random_state="test",
                       **initial_supply_state(scenario, now))
    for supply in dataset.supplies:
        state.agents[1].location_id = supply.location_id
        quote = state.supply_quotes[supply.supply_id]
        request = PurchaseOrderRequest(items=({"supply_id": supply.supply_id, "quantity": 1,
                                              "recipient_id": "agent:1", "price_version": quote.price_version},),
                                       fulfillment="pickup", destination_id=supply.location_id)
        before = state.supply_stock[supply.supply_id]
        outcome = purchase_order(state, semantics, 1, request)
        assert outcome.total_price_fen == quote.price_fen
        assert outcome.state.supply_stock[supply.supply_id] == before - 1
        assert outcome.state.agents[1].inventory[supply.location_id][supply.product_id] >= 1
        state = outcome.state
