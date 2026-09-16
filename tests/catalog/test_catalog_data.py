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
    assert len(by_id["energy_kcal"]["drain_rules"]["activity_met"]) == 10
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
    assert kinds == Counter({"product": 16, "service": 1, "contract": 3})
    foods = [o for o in objects if o["object_id"].startswith("food-")]
    durables = [o for o in objects if o["object_id"].startswith("item-")]
    assert len(foods) == 8 and len(durables) == 8
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


def test_manifest_and_overrides_layout():
    manifest = json.loads((ROOT / "catalog/manifest.json").read_text(encoding="utf-8"))
    assert manifest == {"version_major": 1, "version_minor": 0}
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
            assert part["type"] in ("fact", "assumption", "scenario")
        for leaf in _numeric_leaves(entry):
            hits = [p for p in parts if any(_covers(leaf, d) for d in p["paths"])]
            assert len(hits) == 1, f"{entry_id}.{leaf}: covered by {len(hits)} parts (need exactly 1)"


def test_real_catalog_loads_through_package():
    catalog = load_catalog(ROOT / "catalog")
    assert (catalog.version_major, catalog.version_minor) == (1, 0)
    assert set(catalog.resources) == {
        "money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need"}
    assert len(catalog.objects) == 20
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
