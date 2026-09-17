"""Catalog data self-check: parse, counts, provenance coverage, regeneration stability."""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from worldmock.catalog.loader import load_catalog

ROOT = Path(__file__).resolve().parents[2]
SKIP_KEYS = {"version", "provenance"}


def _load(path: Path) -> list[dict]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def _numeric_leaves(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SKIP_KEYS:
                continue
            yield from _numeric_leaves(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _numeric_leaves(value, f"{prefix}[{index}]")
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        yield prefix


def _covers(leaf: str, declared: str) -> bool:
    return leaf == declared or leaf.startswith(declared + ".") or leaf.startswith(declared + "[")


def test_resources_catalog_shape():
    resources = _load(ROOT / "catalog/resources.jsonl")
    assert [r["resource_id"] for r in resources] == [
        "money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need"]
    by_id = {r["resource_id"]: r for r in resources}
    assert by_id["money_fen"]["kind"] == "account"
    assert by_id["money_fen"]["conservation"] == "closed"
    assert by_id["money_fen"]["bounds"] is None and by_id["money_fen"]["alert_rules"] == []
    for rid in ("energy_kcal", "sleep_debt_hours", "entertainment_need"):
        assert by_id[rid]["kind"] == "level"
        assert by_id[rid]["conservation"] == "open"
    assert by_id["energy_kcal"]["bounds"] == {"min": 0, "max": 2400}
    assert by_id["sleep_debt_hours"]["bounds"] == {"min": 0, "max": None}
    assert by_id["entertainment_need"]["bounds"] == {"min": 0, "max": 1}
    assert by_id["energy_kcal"]["drain_rules"]["basal_kcal_per_hour"] == 65
    lifestyle = json.loads(
        (ROOT / "corpora/lifestyle/items.json").read_text(encoding="utf-8"))["items"]
    lifestyle_met = {f"lifestyle:{item['activity_id']}": item["met_value"] for item in lifestyle}
    assert by_id["energy_kcal"]["drain_rules"]["activity_met"].items() >= lifestyle_met.items()
    assert len(by_id["energy_kcal"]["drain_rules"]["activity_met"]) == 10 + len(lifestyle_met)
    assert len(by_id["energy_kcal"]["drain_rules"]["food_efficiency_curve"]) == 4
    assert by_id["sleep_debt_hours"]["drain_rules"] == {
        "awake_sleep_debt_per_hour": 1, "sleep_recovery_per_hour": 2}
    assert set(by_id["entertainment_need"]["drain_rules"]) == {
        "entertainment_need_per_awake_hour", "entertainment_relief_per_hour"}
    assert by_id["energy_kcal"]["alert_rules"] == [
        {"below": 800, "code": "HUNGRY"}, {"below": 400, "code": "EMERGENCY_ENERGY"}]


def test_objects_catalog_shape():
    objects = _load(ROOT / "catalog/objects.jsonl")
    kinds = Counter(o["object_kind"] for o in objects)
    assert kinds == Counter({"product": 159, "service": 1, "contract": 3})
    foods = [o for o in objects if o["object_id"].startswith("food-")]
    meals = [o for o in objects if o["object_id"].startswith("meal-")]
    durables = [o for o in objects if o["object_id"].startswith("item-")]
    assert len(foods) == 121 and len(durables) == 8
    assert len(foods) >= 100, "food catalog must hold >=100 entries"
    assert len(meals) == 30, "meal catalog must hold the 30 contract meals"
    for food in foods:
        consume = [e for e in food["edges"] if e["on"] == "consume"]
        assert len(consume) == 1 and consume[0]["type"] == "source"
        assert consume[0]["resource"] == "energy_kcal"
        purchase = [e for e in food["edges"] if e["on"] == "purchase"][0]
        assert purchase["type"] == "transfer" and purchase["from"] == "{buyer}"
        assert purchase["account_roles"]["from"] == "buyer"
    for durable in durables:
        assert durable["effect"] == "durable"
        assert all(e["on"] != "consume" for e in durable["edges"])
    contracts = {o["object_id"]: o for o in objects if o["object_kind"] == "contract"}
    assert set(contracts) == {"contract-hourly", "contract-piece", "contract-package"}
    for contract in contracts.values():
        settle = [e for e in contract["edges"] if e["type"] == "convert"]
        assert len(settle) == 1
        assert settle[0]["from"] == "{employer}" and settle[0]["to"] == "{worker}"
        assert contract["acceptance"]["required_effective_seconds"] is None
    travel = next(o for o in objects if o["object_kind"] == "service")
    assert travel["formula_ref"] == "travel-fare"
    assert travel["edges"][0]["amount_fen"]["formula_ref"] == "travel-fare"
    assert (ROOT / "catalog/rules/travel-fare.toml").is_file()
    assert (ROOT / "catalog/rules/off-foods.mapping.toml").is_file()


def test_food_expansion_whitelist_and_spectrum():
    """扩容 25 条：条码/kcal/份量逐条对照 china-100.json，kcal/fen 谱系去单点。"""
    expansion = json.loads((ROOT / "corpora/foods/expansion.json").read_text(encoding="utf-8"))
    entries = expansion["entries"]
    assert len(entries) == 25
    raw = {str(p["code"]): p for p in
           json.loads((ROOT / "corpora/open-food-facts/china-100.json").read_text())["products"]}
    for entry in entries:
        assert entry["barcode"] in raw
        assert entry["name"] == (raw[entry["barcode"]].get("product_name_zh")
                                 or raw[entry["barcode"]].get("product_name"))
    objects = _load(ROOT / "catalog/objects.jsonl")
    foods = {o["object_id"]: o for o in objects if o["object_id"].startswith("food-")}
    ratios = {}
    for object_id, obj in foods.items():
        price = obj["edges"][0]["amount_fen"]
        kcal = obj["edges"][1]["amount"]
        ratios[object_id] = kcal / price
    band = {oid for oid, ratio in ratios.items() if 0.3 <= ratio <= 1.0}
    assert len(band) >= 6, "0.3-1.0 kcal/fen band must hold multiple options"
    top = max(ratios, key=ratios.get)
    near_top = [oid for oid, ratio in ratios.items() if ratio >= ratios[top] * 0.8]
    assert len(near_top) >= 2, "best kcal/fen must not be a single unique winner"
    for entry in entries:
        obj = foods[f"food-{entry['barcode']}"]
        assert obj["edges"][1]["amount"] == entry["energy_kcal_serving"]
        assert obj["serving"]["unit"] == entry["serving_size"]
        price_part = next(p for p in obj["provenance"] if p["part"] == "purchase price edge")
        assert price_part["type"] == "anchored"


def test_food_expansion2_whitelist_against_pool():
    """第二波扩容 88 条：条码/名称/kcal/份量逐条对照 china-pool.json，品类锚齐备。"""
    expansion2 = json.loads((ROOT / "corpora/foods/expansion2.json").read_text(encoding="utf-8"))
    entries = expansion2["entries"]
    assert len(entries) >= 70
    assert expansion2["derived_from"] == "corpora/open-food-facts/china-pool.json"
    pool = {str(p["code"]): p for p in json.loads(
        (ROOT / "corpora/open-food-facts/china-pool.json").read_text())["products"]}
    assert len(pool) >= 300
    wave1 = {e["barcode"] for e in json.loads(
        (ROOT / "corpora/foods/expansion.json").read_text())["entries"]}
    barcodes = [e["barcode"] for e in entries]
    assert len(set(barcodes)) == len(barcodes)
    assert not set(barcodes) & wave1
    for entry in entries:
        raw = pool[entry["barcode"]]
        assert entry["name"] == (raw.get("product_name_zh") or raw.get("product_name"))
        nut = raw.get("nutriments", {})
        kcal100 = (nut["energy-kcal_100g"] if entry["kcal_rule"].startswith("nutriments.energy-kcal_100g")
                   else nut["energy-kj_100g"] / 4.184)
        assert abs(kcal100 * entry["serving_quantity"] / 100 - entry["energy_kcal_serving"]) <= 0.05
        assert entry["energy_kcal_serving"] <= 800
        assert abs(float(raw["serving_quantity"]) - entry["serving_quantity"]) <= 0.01
    objects = _load(ROOT / "catalog/objects.jsonl")
    foods = {o["object_id"]: o for o in objects if o["object_id"].startswith("food-")}
    for entry in entries:
        obj = foods[f"food-{entry['barcode']}"]
        assert obj["edges"][1]["amount"] == entry["energy_kcal_serving"]
        assert obj["serving"]["unit"] == entry["serving_size"]
        consume_part = next(p for p in obj["provenance"] if p["part"] == "consume energy edge")
        assert consume_part["type"] == "fact"
        assert consume_part["source"] == f"openfoodfacts:product/{entry['barcode']}"
        price_part = next(p for p in obj["provenance"] if p["part"] == "purchase price edge")
        assert price_part["type"] == "anchored"
        assert entry["category"] in price_part["source"]


def test_food_catalog_kcal_per_fen_spectrum():
    """全目录 kcal/fen 谱系：[0.2,1.0] 每个 0.1 带至少 2 项，最高比 ≥0.9 且无单一致胜。"""
    objects = _load(ROOT / "catalog/objects.jsonl")
    foods = [o for o in objects if o["object_id"].startswith("food-")]
    assert len(foods) >= 100
    ratios = {o["object_id"]: o["edges"][1]["amount"] / o["edges"][0]["amount_fen"] for o in foods}
    top = max(ratios.values())
    assert top >= 0.9, f"max kcal/fen {top:.3f} below 0.9"
    band_counts = Counter(
        min(int(min(r, 0.99) * 10), 9) for r in ratios.values() if r >= 0.2)
    for band in range(2, 10):
        assert band_counts.get(band, 0) >= 2, f"band 0.{band} holds {band_counts.get(band, 0)} foods"
    near_top = [oid for oid, r in ratios.items() if r >= top * 0.8]
    assert len(near_top) >= 2, "best kcal/fen must not be a single unique winner"


def test_manifest_and_overrides_layout():
    manifest = json.loads((ROOT / "catalog/manifest.json").read_text(encoding="utf-8"))
    assert manifest["version_major"] == 2 and manifest["version_minor"] == 0
    requirements = manifest["category_requirements"]
    assert requirements["meals"]["min_count"] == 30
    assert requirements["location_min_per_kind"] == {"canteen_or_restaurant": 2}
    assert requirements["home_pantry"]["breakfast_servings_min"] == 7
    assert requirements["content_metadata"]["max_age_rating"] == 17
    assert requirements["work_materials"]["min_distinct"] == 20
    assert requirements["lifestyle_fields"] == [
        "location_kinds", "time_window", "needs_partner", "effects"]
    assert requirements["contacts"]["min_per_persona"] == 2
    tombstones = ROOT / "catalog/overrides/tombstones.jsonl"
    assert tombstones.is_file()
    assert tombstones.read_text(encoding="utf-8") == ""


def test_every_numeric_value_has_exactly_one_provenance_part():
    entries = _load(ROOT / "catalog/resources.jsonl") + _load(ROOT / "catalog/objects.jsonl")
    for entry in entries:
        entry_id = entry.get("resource_id") or entry.get("object_id")
        parts = entry["provenance"]
        assert parts, f"{entry_id}: no provenance"
        for part in parts:
            assert part["type"] in ("fact", "anchored", "assumption", "scenario")
        for leaf in _numeric_leaves(entry):
            hits = [p for p in parts if any(_covers(leaf, d) for d in p["paths"])]
            assert len(hits) == 1, f"{entry_id}.{leaf}: covered by {len(hits)} parts (need exactly 1)"


def test_real_catalog_loads_through_package():
    catalog = load_catalog(ROOT / "catalog")
    assert (catalog.version_major, catalog.version_minor) == (2, 0)
    assert set(catalog.resources) == {
        "money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need"}
    assert len(catalog.objects) == 163
    assert sum(1 for key in catalog.objects if key.startswith("meal-")) == 30
    assert set(catalog.formulas) == {"travel-fare", "travel-time"}
    fare = catalog.formulas["travel-fare"]
    assert fare.branches["walking"].amount == 0
    assert fare.branches["driving"].amount == "max(500, ceil(m * 0.2))"
    assert catalog.tombstoned_ids() == frozenset()


def test_regeneration_is_byte_stable():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_catalog.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("byte-identical") == 4
