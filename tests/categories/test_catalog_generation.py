"""冻结品类数据驱动生成的定向回归。"""

import json
from collections import Counter
from pathlib import Path

from worldmock.categories import resolve_object_values
from worldmock.categories.loader import load_category_contract, load_exchange_rules
from worldmock.catalog.loader import load_object_declarations

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "catalog"


def _raw_rows() -> list[dict]:
    """@brief 读取正式原生对象声明。"""
    return [json.loads(line) for line in (CATALOG / "objects.jsonl").read_text(
        encoding="utf-8").splitlines() if line]


def test_every_generated_object_resolves_under_frozen_contract() -> None:
    contract = load_category_contract(CATALOG / "category-contract.json")
    declarations = load_object_declarations(CATALOG / "objects.jsonl")
    resolved = [resolve_object_values(declaration, contract) for declaration in declarations]
    assert len(resolved) == 163
    assert len({item.object_id for item in resolved}) == 163
    assert all(item.category_id in contract.categories for item in resolved)


def test_rules_and_pairings_cover_every_formal_object_action() -> None:
    contract = load_category_contract(CATALOG / "category-contract.json")
    rules = load_exchange_rules(CATALOG / "exchange-rules.json", contract)
    assert len(rules.rules) == 8
    expected = {
        ("product", "purchase"),
        ("food", "consume:eat"),
        ("durable", "consume:use"),
        ("travel-service", "travel:walking"),
        ("travel-service", "travel:driving"),
        ("contract-hourly", "work:settle"),
        ("contract-piece", "work:settle"),
        ("contract-package", "work:settle"),
    }
    actual = {(item.target_category_id, item.action.value) for item in contract.pairings}
    assert actual == expected


def test_shared_values_are_not_repeated_in_raw_objects() -> None:
    rows = _raw_rows()
    shared_food = {
        "effect", "consumable", "use_modes", "stock_initial", "stock_restock",
        "delivery_delay_seconds", "required_location_id",
    }
    for row in rows:
        values = row["values"]["absolute"]
        if row["object_id"].startswith(("food-", "meal-")):
            assert not shared_food & set(values)
        if row["object_id"].startswith("item-"):
            assert values["effect"]
            assert any(
                "/values/absolute" in source["paths"] for source in row["provenance"]
            )
            assert not {"use_modes", "stock_initial", "stock_restock",
                        "use_duration_seconds", "delivery_delay_seconds"} & set(values)


def test_per_object_numeric_differences_and_source_types_are_preserved() -> None:
    rows = _raw_rows()
    foods = [row for row in rows if row["object_id"].startswith(("food-", "meal-"))]
    assert len(foods) == 151
    assert len({row["values"]["absolute"]["price_fen"] for row in foods}) > 30
    assert len({row["values"]["absolute"]["energy_kcal_per_serving"] for row in foods}) > 60
    for row in foods:
        types = Counter(source["type"] for source in row["provenance"])
        assert types["fact"] == 1
        assert types["scenario"] + types["anchored"] >= 1
        fact = next(source for source in row["provenance"] if source["type"] == "fact")
        assert fact["producer"] == "data_source"


def test_density_outliers_use_explicit_categories_without_widening_base_windows() -> None:
    rows = {row["object_id"]: row for row in _raw_rows()}
    assert rows["food-6943949415174"]["category_id"] == "food-light-snack"
    assert rows["food-6948939681928"]["category_id"] == "food-high-density-nut-snack"
    assert rows["food-6948939696106"]["category_id"] == "food-high-density-nut-snack"
    contract = json.loads((CATALOG / "category-contract.json").read_text(encoding="utf-8"))
    nodes = {node["category_id"]: node for node in contract["categories"]}
    snack = nodes["food-snack"]["slots"]["energy_density_kcal_per_100"]["schema"]
    nuts = nodes["food-nut-meat-snack"]["slots"]["energy_density_kcal_per_100"]["schema"]
    assert snack == {"type": "number", "minimum": 150, "maximum": 600}
    assert nuts == {"type": "number", "minimum": 100, "maximum": 650}


def test_contract_and_rules_record_real_delegated_review() -> None:
    for filename in ("category-contract.json", "exchange-rules.json"):
        document = json.loads((CATALOG / filename).read_text(encoding="utf-8"))
        review = document["review"]
        assert review["reviewer_type"] == "delegated_agent"
        assert "User request on 2026-09-20" in review["authorization"]
        assert review["reviewer"] != "human"
    contract = json.loads((CATALOG / "category-contract.json").read_text(encoding="utf-8"))
    assert all(not (source["producer"] == "model" and source["type"] == "fact")
               for source in contract["sources"])
