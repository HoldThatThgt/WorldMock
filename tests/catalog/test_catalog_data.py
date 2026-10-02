"""正式 4.1 目录数据、原生对象增量与可复现身份的验收。"""

import hashlib
import json
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

from worldmock.categories import compute_catalog_identity
from worldmock.catalog.loader import load_catalog

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "catalog"
RAW_OBJECT_KEYS = {
    "object_id", "name", "category_id", "values", "provenance", "version",
}


def _load_jsonl(path: Path) -> list[dict]:
    """@brief 读取非空 JSONL 行。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path: Path) -> str:
    """@brief 返回文件原始字节的 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_resources_preserve_body_and_money_domains() -> None:
    resources = {row["resource_id"]: row for row in _load_jsonl(CATALOG / "resources.jsonl")}
    assert {"money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need"} <= set(resources)
    assert resources["money_fen"]["conservation"] == "closed"
    assert resources["energy_kcal"]["bounds"] == {"min": 0, "max": 2400}
    assert resources["sleep_debt_hours"]["bounds"] == {"min": 0, "max": None}
    assert resources["entertainment_need"]["bounds"] == {"min": 0, "max": 1}
    assert resources["inventory"] == {
        "resource_id": "inventory",
        "kind": "level",
        "unit": "item",
        "owner": "agent_location",
        "bounds": {"min": 0, "max": None},
        "conservation": "open",
        "alert_rules": [],
        "provenance": [{
            "part": "inventory non-negative lower bound",
            "paths": ["bounds.min"],
            "type": "scenario",
            "source": "LifeEngine agent-location inventory quantity domain; durable use is an open drain",
        }],
        "version": 1,
    }
    assert resources["energy_kcal"]["alert_rules"] == [
        {"below": 800, "code": "HUNGRY"},
        {"below": 400, "code": "EMERGENCY_ENERGY"},
    ]


def test_raw_objects_are_native_deltas_and_preserve_population() -> None:
    rows = _load_jsonl(CATALOG / "objects.jsonl")
    assert len(rows) == 172
    assert all(set(row) == RAW_OBJECT_KEYS for row in rows)
    assert all(set(row["values"]) == {"absolute", "relative", "proportional"} for row in rows)
    assert not any({"edges", "formula_ref", "object_kind"} & set(row) for row in rows)
    ids = [row["object_id"] for row in rows]
    assert len(set(ids)) == 172
    assert sum(object_id.startswith("food-") for object_id in ids) == 121
    assert sum(object_id.startswith("meal-") for object_id in ids) == 30
    assert sum(object_id.startswith("item-") for object_id in ids) == 8
    product_rows = [row for row in rows if row["category_id"] not in {
        "travel-service", "contract-hourly", "contract-piece", "contract-package",
    }]
    assert len(product_rows) == 168
    forbidden = {"price_fen", "price_version", "merchant_role", "stock_initial", "delivery_delay_seconds"}
    assert all(not forbidden & set(row["values"]["absolute"]) for row in product_rows)
    assert {object_id for object_id in ids if object_id.startswith("contract-")} == {
        "contract-hourly", "contract-piece", "contract-package",
    }
    assert "travel-service" in ids


def test_real_catalog_compiles_all_objects_and_rules() -> None:
    catalog = load_catalog(CATALOG)
    assert (catalog.version_major, catalog.version_minor) == (5, 3)
    assert catalog.category_contract_version == 3
    assert len(catalog.raw_objects) == len(catalog.objects) == 172
    assert len(catalog.category_contract.categories) == 28
    assert set(catalog.exchange_rules.rules) == {
        "purchase-product", "consume-food", "use-durable",
        "travel-walking", "travel-driving",
        "settle-hourly", "settle-piece", "settle-package",
        "cook-vegetable-stir-fry",
    }
    milk = catalog.raw_objects["food-6923644266066"]
    yogurt = catalog.raw_objects["food-6901209212215"]
    assert milk.category_id == "food-dairy"
    assert yogurt.category_id == "food-yogurt"


def test_frozen_product_values_survive_category_compilation() -> None:
    catalog = load_catalog(CATALOG)
    snapshot = json.loads(
        (ROOT / "scripts/catalog-snapshot-legacy-scenario.json").read_text(encoding="utf-8")
    )
    for expected in snapshot["products"]:
        actual = catalog.objects[expected["product_id"]].attributes
        assert not {"price_fen", "merchant_role", "stock_initial", "delivery_delay_seconds"} & set(actual)
        assert actual["effect"] == expected["effect"]
        assert actual["consumable"] is expected["consumable"]
        assert actual["use_modes"] == tuple(expected["use_modes"])
        assert actual["use_duration_seconds"] == expected["use_duration_seconds"]
        assert actual["required_location_id"] == expected["required_location_id"]
        if expected["product_id"].startswith("food-"):
            assert actual["energy_kcal_per_serving"] == expected["energy_kcal_per_unit"]
            assert actual["serving_quantity"] == expected["serving_quantity"]
            assert actual["serving_unit"] == expected["serving_unit"]


def test_compiled_objects_preserve_all_scenario_product_bindings() -> None:
    catalog = load_catalog(CATALOG)
    scenario = json.loads((ROOT / "scenarios/white-collar-12-weeks.json").read_text(encoding="utf-8"))
    assert len(scenario["products"]) == 168
    assert {row["product_id"] for row in scenario["products"]} == {
        object_id for object_id, obj in catalog.objects.items() if obj.object_kind.value == "product"
    }
    for expected in scenario["products"]:
        obj = catalog.objects[expected["product_id"]]
        values = obj.attributes
        assert values["use_duration_seconds"] > 0
        assert isinstance(values["consumable"], bool)
        if expected["product_id"].startswith(("food-", "meal-")):
            assert values["energy_kcal_per_serving"] >= 0
            assert values["energy_density_kcal_per_100"] >= 0
            assert values["serving_quantity"] > 0
            assert values["serving_unit"]
        else:
            assert values["effect"]


def test_food_value_spectrum_survives_native_generation() -> None:
    catalog = load_catalog(CATALOG)
    supplies = json.loads((ROOT / "corpora/zhangjiang-map/20260105/supplies.json").read_text(encoding="utf-8"))["supplies"]
    ratios = {
        row["supply_id"]: catalog.objects[row["product_id"]].attributes["energy_kcal_per_serving"]
        / row["quotes"][0]["price_fen"]
        for row in supplies if row["product_id"].startswith("food-") and row["location_id"] == "market"
    }
    top = max(ratios.values())
    assert top >= 0.9
    bands = Counter(min(int(min(ratio, 0.99) * 10), 9)
                    for ratio in ratios.values() if ratio >= 0.2)
    assert all(bands[band] >= 1 for band in range(2, 10))
    assert sum(ratio >= top * 0.8 for ratio in ratios.values()) >= 2


def test_density_windows_live_only_in_category_slots() -> None:
    source = (ROOT / "scripts/build_catalog.py").read_text(encoding="utf-8")
    assert "FOOD2_CATEGORY_DENSITY_WINDOWS" not in source
    contract = json.loads((CATALOG / "category-contract.json").read_text(encoding="utf-8"))
    nodes = {node["category_id"]: node for node in contract["categories"]}
    density_categories = {
        key for key, node in nodes.items()
        if "energy_density_kcal_per_100" in node["slots"]
    }
    assert {"food-soda", "food-dairy", "food-yogurt", "food-tea-juice",
            "food-snack", "food-instant-noodle", "food-bakery",
            "food-nut-meat-snack", "food-coffee"} <= density_categories


def test_travel_rules_are_closed_data_without_formula_text() -> None:
    assert not (CATALOG / "rules/travel-fare.toml").exists()
    rules = json.loads((CATALOG / "exchange-rules.json").read_text(encoding="utf-8"))
    by_id = {rule["rule_id"]: rule for rule in rules["rules"]}
    assert by_id["travel-walking"]["minimum_fen"] == 0
    assert by_id["travel-walking"]["fen_per_meter"] == 0
    assert by_id["travel-driving"]["minimum_fen"] == 500
    assert by_id["travel-driving"]["fen_per_meter"] == 0.2
    profile = tomllib.loads((CATALOG / "rules/travel-time.toml").read_text(encoding="utf-8"))
    assert set(profile) == {"version", "factors", "provenance"}
    assert len(profile["factors"]) == 25
    assert profile["factors"]["walking"] == 1.0
    assert all(isinstance(value, (int, float)) and not isinstance(value, bool)
               for value in profile["factors"].values())


def test_manifest_binds_contract_rules_and_full_content() -> None:
    manifest = json.loads((CATALOG / "manifest.json").read_text(encoding="utf-8"))
    identity = compute_catalog_identity(CATALOG, manifest)
    assert identity.category_contract_version == manifest["category_contract_version"] == 3
    assert identity.category_contract_sha256 == manifest["category_contract_sha256"]
    assert identity.exchange_rules_sha256 == manifest["exchange_rules_sha256"]
    assert identity.catalog_content_sha256 == manifest["catalog_content_sha256"]
    assert manifest["category_contract_sha256"] == _sha256(CATALOG / "category-contract.json")
    assert manifest["exchange_rules_sha256"] == _sha256(CATALOG / "exchange-rules.json")


def test_life_scenarios_bind_catalog_four_and_explicit_empty_recipes() -> None:
    for path in sorted((ROOT / "scenarios").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 4:
            continue
        assert payload["catalog_version"] == {"major": 5, "minor": 3}
        assert payload["recipe_output_bindings"] == {"prepared-meal-vegetable-stir-fry": "meal-0030"}


def test_regeneration_is_byte_stable() -> None:
    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_catalog.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT, check=False)
    assert process.returncode == 0, process.stdout + process.stderr
    assert process.stdout.count("byte-identical") == 4
