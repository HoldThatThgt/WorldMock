"""@brief 人工门店树验证加载、完整商品集合、报价和实际购买合同。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT

CATALOG_ROOT = ARTIFICIAL_ENVIRONMENT_ROOT / "catalog"
from worldmock.catalog.loader import load_catalog, load_object_declarations
from worldmock.categories import AttributeValues, resolve_object_values
from worldmock.corpora.storefront import assortment_objects, frozen_price, validate_assortments
from worldmock.errors import IntegrityError
from worldmock.life.basket import purchase_order
from worldmock.life.map_data import load_map_dataset, select_map
from worldmock.life.map_models import CategoryAssignment, LocationConfig, MapDataset, MapDatasetReference, SupplyConfig
from worldmock.life.models import AgentState, BodyState, PurchaseOrderRequest, WORLD_SCHEMA_VERSION, WorldState
from worldmock.life.readable import PLACE_TYPES
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.storefront_state import initial_supply_state


_ROOTS = {"restaurant": ("prepared-meal",), "convenience_store": ("food",),
          "supermarket": ("food", "durable", "ingredient"), "wet_market": ("ingredient",),
          "merchant": ("food", "durable", "ingredient")}
_MINIMUMS = {"restaurant": (8, 1), "convenience_store": (40, 6), "supermarket": (80, 8),
             "wet_market": (8, 2), "merchant": (40, 6)}


def _source() -> dict:
    """@brief 每个值来自人工声明并使用明确时区。"""
    return {"kind": "scenario", "url": "fixture://storefront", "retrieved_at": "2026-01-01T00:00:00+08:00",
            "license": "CC0-1.0", "notice": "Artificial test data", "fact_cutoff": "2026-01-01",
            "applicability": "Engine tests only"}


def _catalog(kind: str, count: int, prefix: str = "product"):
    """@brief 只生成门店验收门槛所需的人工对象，共用已验证的最小品类契约。"""
    base = load_catalog(CATALOG_ROOT)
    categories = dict(base.category_contract.categories)
    if kind == "restaurant":
        leaves = ["prepared-meal"]
    elif kind == "wet_market":
        leaves = ["ingredient-a", "ingredient-b"]
    else:
        leaves = ["food-soda", "food-a", "food-b", "food-c", "food-d", "food-e"]
        if kind in {"merchant", "supermarket"}:
            leaves.extend(("durable-a", "ingredient-a"))
    declared = {*leaves, "food-soda", "food-dairy", "food-tea-juice"}
    for leaf in declared:
        if leaf in categories:
            continue
        parent = leaf.split("-")[0]
        original = categories[parent]
        categories[leaf] = replace(original, category_id=leaf, name=leaf, parent_id=parent,
                                   lineage=(*original.lineage, leaf))
    contract = replace(base.category_contract, categories=categories)
    template = base.objects["milk"]
    objects = {f"{prefix}-{index}": template.model_copy(update={
        "object_id": f"{prefix}-{index}", "name": f"Artificial product {index}",
        "category_id": leaves[index % len(leaves)], "attributes": {
            **template.attributes, "purchase_price_fen_min": 100, "purchase_price_fen_max": 1000}})
               for index in range(count)}
    return replace(base, category_contract=contract, objects=objects)


def _location(identity: str, kind: str) -> LocationConfig:
    """@brief 人工门店显式声明全天营业。"""
    calendar = {"effective_from": "2020-01-01T00:00:00+08:00", "effective_to": None,
                "reviewed_on": "2026-01-01", "still_valid_on": "2030-01-01", "source": _source(),
                "weekly": {str(day): [{"opens": 0, "closes": 1440}] for day in range(7)}, "exceptions": {}}
    return LocationConfig.model_validate({
        "location_id": identity, "name": identity, "kind": kind, "business_kind": kind,
        "location_kinds": ["venue"], "longitude": 0.3, "latitude": 0.4,
        "required_qualifications": [], "brand": None, "affordances": {}, "source": _source(),
        "opening_calendars": [calendar]})


def _dataset(catalog, kind: str, identity: str = "store") -> MapDataset:
    """@brief 人工地图保留门店完整商品声明与独立冻结供给。"""
    category_ids = tuple(sorted({item.category_id for item in catalog.objects.values()}))
    if kind == "restaurant":
        category_ids = ()
    assignment = CategoryAssignment.model_validate({
        "kinds": {place: _ROOTS[place] if place in _ROOTS else [] for place in PLACE_TYPES},
        "locations": [{"location_id": identity, "category_ids": category_ids,
                       "object_ids": list(catalog.objects), "price_position_percent": 50, "source": _source()}],
        "source": _source()})
    supplies = []
    for obj in catalog.objects.values():
        supplies.append(SupplyConfig.model_validate({
            "supply_id": f"supply-{obj.object_id}", "product_id": obj.object_id, "location_id": identity,
            "seller_account_id": "merchant", "initial_stock": 2, "fulfillment_modes": ["pickup"],
            "delivery_destination_ids": [], "delivery_delay_seconds": 0, "required_qualifications": [],
            "source": _source(), "quotes": [{"price_fen": frozen_price(7, identity, obj, 50),
            "price_version": "first", "effective_from": "2020-01-01T00:00:00+08:00", "effective_to": None,
            "reviewed_on": "2026-01-01", "still_valid_on": "2030-01-01", "source": _source()}]}))
    return MapDataset.model_validate({
        "schema_version": 3, "dataset_id": "artificial-store-map", "version": 1, "random_seed": 7,
        "coordinate_system": "WGS84", "timezone": "Asia/Shanghai", "bounds": [0, 0, 1, 1],
        "prepared_at": "2026-01-01T00:00:00+08:00", "source": _source(), "route_modes": ["walking"],
        "category_assignment_sha256": "1" * 64,
        **{f"{name}_file": {"path": f"{name}.json", "sha256": "1" * 64}
           for name in ("locations", "supplies", "routes")},
        "locations": [_location(identity, kind)], "supplies": supplies, "routes": [],
        "category_assignment": assignment})


def _redeclared(dataset, **changes):
    """@brief 只改当前人工门店声明，供反例保持其它条件成立。"""
    row = dataset.category_assignment.locations[0].model_copy(update=changes)
    assignment = dataset.category_assignment.model_copy(update={"locations": (row,)})
    return dataset.model_copy(update={"category_assignment": assignment})


@pytest.mark.parametrize("kind", sorted(_MINIMUMS))
def test_complete_assortments_have_actual_stock_and_required_richness(kind: str) -> None:
    """@brief 每种门店的最小合法集合都真实有供给，缺一件不能假装完整。"""
    minimum, _ = _MINIMUMS[kind]
    catalog = _catalog(kind, minimum)
    dataset = _dataset(catalog, kind)
    validate_assortments(dataset, catalog)
    declaration = dataset.category_assignment.locations[0]
    assert assortment_objects(declaration, catalog) == set(catalog.objects)
    assert len(dataset.supplies) == minimum and all(item.initial_stock > 0 for item in dataset.supplies)
    incomplete = dataset.model_copy(update={"supplies": dataset.supplies[1:]})
    with pytest.raises(IntegrityError, match="supply set differs from its complete assortment"):
        validate_assortments(incomplete, catalog)


@pytest.mark.parametrize("kind", sorted(_MINIMUMS))
def test_underfilled_assortments_are_rejected(kind: str) -> None:
    """@brief 完整供给也不能发布低于显式丰富度门槛的商品集合。"""
    catalog = _catalog(kind, _MINIMUMS[kind][0] - 1)
    with pytest.raises(IntegrityError, match="below the declared richness requirement"):
        validate_assortments(_dataset(catalog, kind), catalog)


def test_restaurant_menu_pairs_require_distinct_dishes() -> None:
    """@brief 两家餐馆供给完整时，复制菜单仍拒绝，双方独有三道菜才能通过。"""
    catalog = _catalog("restaurant", 11)
    first = _dataset(catalog, "restaurant", "first")
    second = _dataset(catalog, "restaurant", "second")
    rows = (first.category_assignment.locations[0], second.category_assignment.locations[0])
    dataset = first.model_copy(update={"locations": (*first.locations, *second.locations),
        "supplies": (*first.supplies, *(item.model_copy(update={"supply_id": f"other-{item.supply_id}"})
                                      for item in second.supplies)),
        "category_assignment": first.category_assignment.model_copy(update={"locations": rows})})
    with pytest.raises(IntegrityError, match="restaurant menu pair"):
        validate_assortments(dataset, catalog)
    first_ids, second_ids = tuple(catalog.objects)[:8], tuple(catalog.objects)[3:]
    declarations = (rows[0].model_copy(update={"object_ids": first_ids}),
                    rows[1].model_copy(update={"object_ids": second_ids}))
    changed = dataset.model_copy(update={
        "supplies": tuple(item for item in dataset.supplies
                          if item.product_id in (first_ids if item.location_id == "first" else second_ids)),
        "category_assignment": dataset.category_assignment.model_copy(update={"locations": declarations})})
    validate_assortments(changed, catalog)


def test_unknown_assortment_objects_are_rejected() -> None:
    """@brief 点名目录外商品不能被静默遗漏。"""
    catalog = _catalog("restaurant", 8)
    dataset = _redeclared(_dataset(catalog, "restaurant"), object_ids=(*catalog.objects, "unknown"))
    with pytest.raises(IntegrityError, match="references unknown catalog objects"):
        validate_assortments(dataset, catalog)


@pytest.mark.parametrize("bounds", [{"purchase_price_fen_min": 0},
    {"purchase_price_fen_min": 1001}, {"purchase_price_fen_min": 100.0}, {"purchase_price_fen_max": True}])
def test_frozen_quotes_require_positive_ordered_integer_bounds(bounds) -> None:
    """@brief 越界、倒置、浮点和布尔价格边界不能参与冻结报价或通过环境校验。"""
    catalog = _catalog("restaurant", 8)
    dataset = _dataset(catalog, "restaurant")
    obj = next(iter(catalog.objects.values()))
    broken = obj.model_copy(update={"attributes": {**obj.attributes, **bounds}})
    with pytest.raises(IntegrityError, match="positive integer purchase price bounds"):
        frozen_price(7, "store", broken, 50)
    changed = replace(catalog, objects={**catalog.objects, obj.object_id: broken})
    with pytest.raises(IntegrityError, match="positive integer purchase price bounds"):
        validate_assortments(dataset, changed)


def test_frozen_quotes_require_explicit_price_bounds() -> None:
    """@brief 没有价格边界的商品不能生成隐式报价。"""
    obj = next(iter(_catalog("restaurant", 8).objects.values())).model_copy(update={"attributes": {}})
    with pytest.raises(IntegrityError, match="positive integer purchase price bounds"):
        frozen_price(7, "store", obj, 50)


def test_price_formula_is_reproducible_and_respects_fixed_prices() -> None:
    """@brief 相同输入独立可重算、定位升高报价不降、相同上下界保持固定价格。"""
    obj = next(iter(_catalog("restaurant", 8).objects.values()))
    prices = [frozen_price(7, "store", obj, position) for position in (10, 50, 90)]
    assert 100 <= prices[0] < prices[1] < prices[2] <= 1000
    assert frozen_price(7, "store", obj, 50) == prices[1]
    assert len({frozen_price(seed, "store", obj, 50) for seed in range(20)}) > 1
    fixed = obj.model_copy(update={"attributes": {"purchase_price_fen_min": 713, "purchase_price_fen_max": 713}})
    assert all(frozen_price(7, place, fixed, 50) == 713 for place in ("first", "second"))


@pytest.mark.parametrize("kind", ["convenience_store", "supermarket"])
@pytest.mark.parametrize("root", ["food", "prepared-meal"])
def test_convenience_store_and_supermarket_cannot_select_a_whole_food_root(kind, root) -> None:
    """@brief 便利店和超市必须声明具体食品子品类。"""
    catalog = _catalog(kind, _MINIMUMS[kind][0])
    dataset = _dataset(catalog, kind)
    categories = dataset.category_assignment.locations[0].category_ids
    with pytest.raises(IntegrityError, match="must select specific food subcategories"):
        validate_assortments(_redeclared(dataset, category_ids=(*categories, root)), catalog)


def test_store_cannot_publish_products_outside_its_kind_admission() -> None:
    """@brief 餐馆不能把食品根下的包装商品加入正餐菜单。"""
    catalog = _catalog("restaurant", 8)
    dataset = _dataset(catalog, "restaurant")
    extra = next(iter(catalog.objects.values())).model_copy(update={"object_id": "packaged", "category_id": "food"})
    changed = replace(catalog, objects={**catalog.objects, extra.object_id: extra})
    with pytest.raises(IntegrityError, match="product outside its kind admission"):
        validate_assortments(_redeclared(dataset, object_ids=(*catalog.objects, "packaged")), changed)


@pytest.mark.parametrize("defect", ["missing", "extra", "unfulfilled"])
def test_store_supplies_must_equal_the_complete_declared_assortment(defect) -> None:
    """@brief 实际供给少一件、多一件或声明未落实均拒绝。"""
    catalog = _catalog("restaurant", 9)
    dataset = _dataset(catalog, "restaurant")
    if defect in {"missing", "unfulfilled"}:
        changed = dataset.model_copy(update={"supplies": dataset.supplies[1:]})
    else:
        changed = _redeclared(dataset, object_ids=tuple(catalog.objects)[1:])
    with pytest.raises(IntegrityError, match="supply set differs from its complete assortment"):
        validate_assortments(changed, catalog)


@pytest.mark.parametrize("defect", ["stock", "bounds", "position"])
def test_invalid_frozen_supply_is_rejected(defect) -> None:
    """@brief 完整集合仍必须有正库存、范围内报价和与定位一致的冻结价格。"""
    catalog = _catalog("restaurant", 8)
    dataset = _dataset(catalog, "restaurant")
    supply = dataset.supplies[0]
    if defect == "stock":
        broken = supply.model_copy(update={"initial_stock": 0})
        message = "positive stock"
    else:
        price = 1001 if defect == "bounds" else supply.quotes[0].price_fen + 1
        broken = supply.model_copy(update={"quotes": (supply.quotes[0].model_copy(update={"price_fen": price}),)})
        message = "outside its product price bounds" if defect == "bounds" else "declared price position"
    with pytest.raises(IntegrityError, match=message):
        validate_assortments(dataset.model_copy(update={"supplies": (broken, *dataset.supplies[1:])}), catalog)


def _publish(root, dataset) -> MapDatasetReference:
    """@brief 写出最小人工地图文件后交给实际生产加载器校验。"""
    raw = dataset.model_dump(mode="json")
    assignment = json.dumps(raw.pop("category_assignment")).encode("utf-8")
    (root / "category-assignment.json").write_bytes(assignment)
    raw["category_assignment_sha256"] = hashlib.sha256(assignment).hexdigest()
    for name in ("locations", "supplies", "routes"):
        content = json.dumps({"schema_version": 3, name: raw.pop(name)}).encode("utf-8")
        (root / f"{name}.json").write_bytes(content)
        raw[f"{name}_file"] = {"path": f"{name}.json", "sha256": hashlib.sha256(content).hexdigest()}
    content = json.dumps(raw).encode("utf-8")
    (root / "dataset.json").write_bytes(content)
    return MapDatasetReference(dataset_id=dataset.dataset_id, version=dataset.version,
                               path="dataset.json", sha256=hashlib.sha256(content).hexdigest())


def test_loaded_store_must_keep_every_declared_supply(tmp_path) -> None:
    """@brief 实际加载的人工门店不能通过场景选择丢弃已声明的一件供给。"""
    catalog = _catalog("restaurant", 8)
    pin = _publish(tmp_path, _dataset(catalog, "restaurant"))
    dataset = load_map_dataset(tmp_path, pin)
    document = SimpleNamespace(map_dataset=pin, timezone=dataset.timezone, random_seed=dataset.random_seed,
        locations=[SimpleNamespace(location_id="store")],
        supplies=[SimpleNamespace(supply_id=item.supply_id, initial_stock=None) for item in dataset.supplies[1:]],
        products=[SimpleNamespace(product_id=item.product_id) for item in dataset.supplies],
        accounts=[SimpleNamespace(account_id="merchant")])
    with pytest.raises(IntegrityError, match="complete declared supply sets"):
        select_map(dataset, document)


def test_every_published_product_can_really_be_bought_at_its_store() -> None:
    """@brief 人工门店每件商品通过实际付款、库存扣减和即时交付。"""
    catalog = _catalog("restaurant", 8)
    dataset = _dataset(catalog, "restaurant")
    now = int(datetime.fromisoformat("2026-01-05T12:00:00+08:00").timestamp())
    scenario = SimpleNamespace(supplies=dataset.supplies, locations=dataset.locations,
                              timezone=dataset.timezone, routes=dataset.routes)
    semantics = ScenarioSemantics(None, catalog, scenario, None, None, {}, None)
    agent = AgentState(agent_id=1, name="Test person", location_id="store", balance_account_id="agent:1",
                      body_settled_at=now, body=BodyState(energy_kcal=1800, entertainment_need=0.2), next_decision_at=now)
    state = WorldState(schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=now, end_time=now + 86400,
        run_id="artificial-store-purchase", timezone=dataset.timezone, scenario_sha256="a" * 64,
        content_manifest_sha256="b" * 64, agents={1: agent}, accounts={"merchant": 0, "agent:1": 10000},
        random_seed=7, random_state="test", **initial_supply_state(scenario, now))
    for supply in dataset.supplies:
        quote = state.supply_quotes[supply.supply_id]
        request = PurchaseOrderRequest(items=({"supply_id": supply.supply_id, "quantity": 1,
            "recipient_id": "agent:1", "price_version": quote.price_version},),
            fulfillment="pickup", destination_id="store")
        before = state.supply_stock[supply.supply_id]
        outcome = purchase_order(state, semantics, 1, request)
        assert outcome.total_price_fen == quote.price_fen
        assert outcome.state.supply_stock[supply.supply_id] == before - 1
        assert outcome.state.agents[1].inventory["store"][supply.product_id] == 1
        state = outcome.state


def test_price_ranges_inherit_and_preserve_explicit_overrides() -> None:
    """@brief 人工商品继承品类价格范围，同时保留合法对象覆盖并拒绝倒置范围。"""
    catalog = load_catalog(CATALOG_ROOT)
    obj = catalog.objects["milk"]
    category = catalog.category_contract.require_category(obj.category_id)
    for name in ("purchase_price_fen_min", "purchase_price_fen_max"):
        assert obj.attributes[name] == category.attributes[name]
    declaration = next(item for item in load_object_declarations(CATALOG_ROOT / "objects.jsonl")
                       if item.object_id == "milk")
    values = declaration.values.model_dump(mode="json")
    values["absolute"].update(purchase_price_fen_min=713, purchase_price_fen_max=713)
    explicit = declaration.model_copy(update={"values": AttributeValues.model_validate(values)})
    resolved = resolve_object_values(explicit, catalog.category_contract)
    assert resolved.attributes["purchase_price_fen_min"] == resolved.attributes["purchase_price_fen_max"] == 713
    values["absolute"].update(purchase_price_fen_min=748, purchase_price_fen_max=612)
    reversed_range = declaration.model_copy(update={"values": AttributeValues.model_validate(values)})
    with pytest.raises(ValueError, match="CATEGORY_PRICE_RANGE_INVALID"):
        resolve_object_values(reversed_range, catalog.category_contract)


def test_wet_market_cannot_select_food_categories() -> None:
    """@brief 菜市场的食材准入不会允许按品类展开包装食品。"""
    catalog = _catalog("wet_market", 8)
    template = next(iter(catalog.objects.values()))
    extra = template.model_copy(update={"object_id": "packaged", "category_id": "food-soda"})
    changed = replace(catalog, objects={**catalog.objects, extra.object_id: extra})
    dataset = _dataset(catalog, "wet_market")
    categories = dataset.category_assignment.locations[0].category_ids
    with pytest.raises(IntegrityError, match="product outside its kind admission"):
        validate_assortments(_redeclared(dataset, category_ids=(*categories, "food-soda")), changed)


@pytest.mark.parametrize("kind, removed, replacement, message", [
    ("merchant", "durable-a", "food-f", "require household goods"),
    ("convenience_store", "food-soda", "food-f", "require drinks")])
def test_required_household_goods_and_drinks_are_actual_products(kind, removed, replacement, message) -> None:
    """@brief 商品数量和品类数足够也不能缺少门店类型声明必须有的商品用途。"""
    catalog = _catalog(kind, _MINIMUMS[kind][0])
    original = catalog.category_contract.require_category("food")
    category = replace(original, category_id=replacement, name=replacement, parent_id="food",
                       lineage=(*original.lineage, replacement))
    contract = replace(catalog.category_contract,
                       categories={**catalog.category_contract.categories, replacement: category})
    objects = {key: value.model_copy(update={"category_id": replacement}) if value.category_id == removed else value
               for key, value in catalog.objects.items()}
    changed = replace(catalog, objects=objects, category_contract=contract)
    with pytest.raises(IntegrityError, match=message):
        validate_assortments(_dataset(changed, kind), changed)


def test_location_kind_admission_cannot_be_redeclared() -> None:
    """@brief 地点类别的准入根不能被环境悄悄改成另一用途。"""
    catalog = _catalog("restaurant", 8)
    dataset = _dataset(catalog, "restaurant")
    assignment = dataset.category_assignment.model_copy(update={
        "kinds": {**dataset.category_assignment.kinds, "restaurant": ("food",)}})
    with pytest.raises(IntegrityError, match="admission disagrees with the declared category roots"):
        validate_assortments(dataset.model_copy(update={"category_assignment": assignment}), catalog)


def test_convenience_price_positions_exceed_supermarket_positions() -> None:
    """@brief 两家人工门店集合和报价均正确时，便利店定位仍必须比超市高至少十点。"""
    convenience = _catalog("convenience_store", 40, "convenience")
    supermarket = _catalog("supermarket", 80, "supermarket")
    catalog = replace(supermarket, objects={**convenience.objects, **supermarket.objects})
    first, second = (_dataset(convenience, "convenience_store", "convenience"),
                     _dataset(supermarket, "supermarket", "supermarket"))
    rows = tuple(item.category_assignment.locations[0].model_copy(update={"category_ids": ()})
                 for item in (first, second))
    dataset = first.model_copy(update={
        "locations": (*first.locations, *second.locations), "supplies": (*first.supplies, *second.supplies),
        "category_assignment": first.category_assignment.model_copy(update={"locations": rows})})
    with pytest.raises(IntegrityError, match="at least ten points"):
        validate_assortments(dataset, catalog)
    raised = rows[0].model_copy(update={"price_position_percent": 60})
    supplies = tuple(item.model_copy(update={"quotes": (item.quotes[0].model_copy(update={
        "price_fen": frozen_price(7, "convenience", catalog.objects[item.product_id], 60)}),)})
        if item.location_id == "convenience" else item for item in dataset.supplies)
    valid = dataset.model_copy(update={"supplies": supplies,
        "category_assignment": dataset.category_assignment.model_copy(update={"locations": (raised, rows[1])})})
    validate_assortments(valid, catalog)
