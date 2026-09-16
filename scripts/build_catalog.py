"""Build catalog data files (resources/objects JSONL + manifest + empty tombstones).

Reads scenarios/white-collar-12-weeks.json (+ multi-person.json as cross-check)
and corpora/open-food-facts/{products,china-100}.json (+ corpora/manifest.json)
and emits the v1 static catalog per docs/resource-registry-design.md (§3/§4.2/§5/§13):
catalog/resources.jsonl, catalog/objects.jsonl, catalog/manifest.json (1.0) and
an empty catalog/overrides/tombstones.jsonl.

Hand-written files NOT touched by this generator (per §13, rules are human-authored):
  catalog/rules/off-foods.mapping.toml, catalog/rules/travel-fare.toml,
  catalog/README.md — the generator only asserts the rules files exist and that
  the travel fare formula reproduces every frozen route fare.

Usage:
  python3 scripts/build_catalog.py           # write catalog data files
  python3 scripts/build_catalog.py --check   # regenerate in memory, compare bytes, exit 1 on drift
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PRIMARY = ROOT / "scenarios/white-collar-12-weeks.json"
SCENARIO_SECONDARY = ROOT / "scenarios/multi-person.json"
OFF_DIR = ROOT / "corpora/open-food-facts"
OFF_FROZEN = OFF_DIR / "products.json"
OFF_RAW = OFF_DIR / "china-100.json"
CORPUS_MANIFEST = ROOT / "corpora/manifest.json"
RULES_DIR = ROOT / "catalog/rules"
HEAD = "cd2586b"


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# provenance coverage walker (§5: every static numeric value must be covered)
# ---------------------------------------------------------------------------

SKIP_KEYS = {"version", "provenance"}


def numeric_leaves(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SKIP_KEYS:
                continue
            yield from numeric_leaves(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from numeric_leaves(value, f"{prefix}[{index}]")
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        yield prefix, node


def covers(leaf_path: str, declared: str) -> bool:
    return leaf_path == declared or leaf_path.startswith(declared + ".") \
        or leaf_path.startswith(declared + "[")


def assert_full_coverage(entry_id: str, entry: dict) -> None:
    parts = entry.get("provenance", [])
    assert parts, f"{entry_id}: no provenance parts"
    for leaf_path, _ in numeric_leaves(entry):
        hits = [part for part in parts
                if any(covers(leaf_path, p) for p in part.get("paths", []))]
        assert hits, f"{entry_id}: numeric value {leaf_path} has no provenance"
        assert len(hits) == 1, f"{entry_id}: {leaf_path} covered by {len(hits)} parts (must be exactly 1)"


# ---------------------------------------------------------------------------
# resources.jsonl
# ---------------------------------------------------------------------------

FACT_SOURCES = {
    "basal": "Mifflin-St Jeor BMR 65kg male ≈1560kcal/day",
    "met": "Compendium of Physical Activities 2011",
    "dri": "中国居民膳食营养素参考摄入量",
}
FACT_ARTIFACT = "docs/resource-registry-design.md §5"
ENGINE_CLAMP = "legacy-engine-body clamp (src/worldmock/life/body.py:71,86,183,195)"


def build_resources(body: dict) -> list[dict]:
    energy = {
        "resource_id": "energy_kcal",
        "kind": "level",
        "unit": "kcal",
        "owner": "agent",
        "bounds": {"min": 0, "max": body["maximum_energy_kcal"]},
        "conservation": "open",
        "alert_rules": [
            {"below": body["hunger_threshold_kcal"], "code": "HUNGRY"},
            {"below": body["emergency_threshold_kcal"], "code": "EMERGENCY_ENERGY"},
        ],
        "drain_rules": {
            "basal_kcal_per_hour": body["basal_kcal_per_hour"],
            "activity_met": body["activity_met"],
            "food_efficiency_curve": body["food_efficiency_curve"],
        },
        "provenance": [
            {"part": "drain_rules.basal_kcal_per_hour", "paths": ["drain_rules.basal_kcal_per_hour"],
             "type": "fact", "source": FACT_SOURCES["basal"], "artifact": FACT_ARTIFACT,
             "note": "evidence snapshot pending"},
            {"part": "drain_rules.activity_met", "paths": ["drain_rules.activity_met"],
             "type": "fact", "source": FACT_SOURCES["met"], "artifact": FACT_ARTIFACT,
             "note": "evidence snapshot pending"},
            {"part": "drain_rules.food_efficiency_curve", "paths": ["drain_rules.food_efficiency_curve"],
             "type": "assumption",
             "source": "legacy-scenario-body food_efficiency_curve"},
            {"part": "bounds.max", "paths": ["bounds.max"],
             "type": "fact", "source": FACT_SOURCES["dri"], "artifact": FACT_ARTIFACT,
             "note": "evidence snapshot pending"},
            {"part": "bounds.min", "paths": ["bounds.min"],
             "type": "scenario", "source": ENGINE_CLAMP},
            {"part": "alert_rules", "paths": ["alert_rules"],
             "type": "scenario",
             "source": "legacy-scenario-body hunger/emergency thresholds; codes per "
                       "docs/resource-registry-design.md §3.1 (engine currently emits "
                       "lowercase hungry/emergency_energy, src/worldmock/life/body.py:126-129)"},
        ],
        "version": 1,
    }
    sleep = {
        "resource_id": "sleep_debt_hours",
        "kind": "level",
        "unit": "hours",
        "owner": "agent",
        "bounds": {"min": 0, "max": None},
        "conservation": "open",
        "drain_rules": {
            "awake_sleep_debt_per_hour": body["awake_sleep_debt_per_hour"],
            "sleep_recovery_per_hour": body["sleep_recovery_per_hour"],
        },
        "provenance": [
            {"part": "bounds.min", "paths": ["bounds.min"],
             "type": "scenario", "source": ENGINE_CLAMP},
            {"part": "drain_rules",
             "paths": ["drain_rules.awake_sleep_debt_per_hour", "drain_rules.sleep_recovery_per_hour"],
             "type": "assumption",
             "source": "legacy-scenario-body awake_sleep_debt_per_hour/sleep_recovery_per_hour"},
        ],
        "version": 1,
    }
    entertainment = {
        "resource_id": "entertainment_need",
        "kind": "level",
        "unit": "ratio",
        "owner": "agent",
        "bounds": {"min": 0, "max": 1},
        "conservation": "open",
        "drain_rules": {
            "entertainment_need_per_awake_hour": body["entertainment_need_per_awake_hour"],
            "entertainment_relief_per_hour": body["entertainment_relief_per_hour"],
        },
        "provenance": [
            {"part": "bounds", "paths": ["bounds.min", "bounds.max"],
             "type": "scenario", "source": ENGINE_CLAMP},
            {"part": "drain_rules.entertainment_need_per_awake_hour",
             "paths": ["drain_rules.entertainment_need_per_awake_hour"],
             "type": "assumption",
             "source": "legacy-scenario-body entertainment_need_per_awake_hour"},
            {"part": "drain_rules.entertainment_relief_per_hour",
             "paths": ["drain_rules.entertainment_relief_per_hour"],
             "type": "assumption",
             "source": "legacy-scenario-body entertainment_relief_per_hour"},
        ],
        "version": 1,
    }
    money = {
        "resource_id": "money_fen",
        "kind": "account",
        "unit": "fen",
        "owner": "global",
        "bounds": None,
        "conservation": "closed",
        "alert_rules": [],
        "provenance": [
            {"part": "type semantics (closed conservation, non-negative via ledger invariant)",
             "paths": [],
             "type": "scenario",
             "source": f"legacy-engine-ledger (src/worldmock/life/engine.py @ {HEAD})"},
        ],
        "version": 1,
    }
    return [money, energy, sleep, entertainment]


# ---------------------------------------------------------------------------
# objects.jsonl
# ---------------------------------------------------------------------------

def product_object(product: dict, off_index: dict, off_sha: str) -> dict:
    is_food = product["product_id"].startswith("food-")
    barcode = product["product_id"].removeprefix("food-")
    edges = [{
        "on": "purchase",
        "type": "transfer",
        "from": "{buyer}",
        "to": "{merchant}",
        "amount_fen": product["price_fen"],
        "account_roles": {"from": "buyer", "to": product["merchant_account_id"]},
    }]
    provenance = [
        {"part": "purchase price edge", "paths": ["edges[0].amount_fen"],
         "type": "scenario", "source": "legacy-scenario-price",
         "note": f"price_version {product['price_version']}"},
        {"part": "stock", "paths": ["stock.initial"],
         "type": "scenario", "source": "legacy-scenario-product"},
    ]
    if is_food:
        edges.append({
            "on": "consume",
            "type": "source",
            "resource": "energy_kcal",
            "amount": product["energy_kcal_per_unit"],
            "duration_s": product["use_duration_seconds"],
        })
        provenance.insert(0, {
            "part": "consume energy edge", "paths": ["edges[1].amount"],
            "type": "fact", "source": f"openfoodfacts:product/{barcode}",
            "artifact": f"corpora/open-food-facts/products.json (sha256 {off_sha})",
        })
        provenance.append({
            "part": "legacy timing/serving fields",
            "paths": ["edges[1].duration_s", "use_duration_seconds",
                      "delivery_delay_seconds", "serving.quantity"],
            "type": "scenario", "source": "legacy-scenario-product",
        })
        entry = {
            "object_id": product["product_id"],
            "name": product["name"],
            "object_kind": "product",
            "effect": product["effect"],
            "consumable": product["consumable"],
            "use_modes": product["use_modes"],
            "edges": edges,
            "stock": {"initial": product["initial_stock"], "restock": "none"},
            "use_duration_seconds": product["use_duration_seconds"],
            "delivery_delay_seconds": product["delivery_delay_seconds"],
            "serving": {"quantity": product["serving_quantity"], "unit": product["serving_unit"]},
            "required_location_id": product["required_location_id"],
            "provenance": provenance,
            "version": 1,
        }
    else:
        provenance.append({
            "part": "legacy timing fields",
            "paths": ["use_duration_seconds", "delivery_delay_seconds"],
            "type": "scenario", "source": "legacy-scenario-product",
        })
        entry = {
            "object_id": product["product_id"],
            "name": product["name"],
            "object_kind": "product",
            "effect": "durable",
            "legacy_effect": product["effect"],
            "consumable": product["consumable"],
            "use_modes": product["use_modes"],
            "edges": edges,
            "stock": {"initial": product["initial_stock"], "restock": "none"},
            "use_duration_seconds": product["use_duration_seconds"],
            "delivery_delay_seconds": product["delivery_delay_seconds"],
            "required_location_id": product["required_location_id"],
            "provenance": provenance,
            "version": 1,
        }
    return entry


def travel_object(routes: list[dict], transport_account_id: str) -> dict:
    return {
        "object_id": "travel-service",
        "name": "Travel service",
        "object_kind": "service",
        "edges": [{
            "on": "depart",
            "type": "transfer",
            "from": "{traveler}",
            "to": "{transport_operator}",
            "amount_fen": {"formula_ref": "travel-fare",
                           "params": {"m": "route.distance_meters", "mode": "route.mode"}},
            "account_roles": {"from": "traveler", "to": transport_account_id},
        }],
        "formula_ref": "travel-fare",
        "provenance": [
            {"part": "fare formula (declared in catalog/rules/travel-fare.toml)",
             "paths": [],
             "type": "scenario",
             "source": "legacy-scenario-routes; formula reproduces every frozen route "
                       "fare_fen (asserted by scripts/build_catalog.py)"},
        ],
        "version": 1,
    }


CONTRACT_FORMULAS = {
    "hourly": "min(pay_fen * worked_seconds // 3600, pay_fen * maximum_duration_seconds // 3600)",
    "piece": "pay_fen * piece_count (oracle piece_count)",
    "package": "pay_fen (fixed, paid on acceptance)",
}


def contract_objects(body: dict, employer_account_id: str) -> list[dict]:
    objects = []
    for kind in ("hourly", "piece", "package"):
        objects.append({
            "object_id": f"contract-{kind}",
            "name": f"{kind.capitalize()} work contract",
            "object_kind": "contract",
            "edges": [{
                "on": "settle",
                "type": "convert",
                "from": "{employer}",
                "to": "{worker}",
                "formula": CONTRACT_FORMULAS[kind],
                "account_roles": {"from": employer_account_id, "to": "worker"},
            }],
            "acceptance": {
                "required_effective_seconds": None,
                "materials_fully_revealed": True,
                "review_pass_required": True,
            },
            "efficiency": {
                "work_efficiency_curve": body["work_efficiency_curve"],
                "work_notes_multiplier": body["work_notes_multiplier"],
            },
            "provenance": [
                {"part": "settlement formula (src semantics, values are per task instance)",
                 "paths": [],
                 "type": "scenario",
                 "source": f"src/worldmock/life/work.py:198-211 _payment @ {HEAD}"},
                {"part": "acceptance gates (field semantics; numbers live in scenario task instances)",
                 "paths": [],
                 "type": "scenario",
                 "source": "legacy-scenario-tasks required_effective_seconds/input_sha256/oracle_sha256"},
                {"part": "efficiency params", "paths": ["efficiency"],
                 "type": "assumption",
                 "source": "legacy-scenario-body work_efficiency_curve/work_notes_multiplier"},
            ],
            "version": 1,
        })
    return objects


# ---------------------------------------------------------------------------
# input verification
# ---------------------------------------------------------------------------

def verify_inputs(scenario: dict, other: dict, off_frozen: list, off_raw: dict,
                  manifest: dict) -> tuple[dict, str]:
    assert other["products"] == scenario["products"], "scenario products diverge"
    assert other["body"] == scenario["body"], "scenario body diverge"
    products = scenario["products"]
    assert len(products) == 16, f"expected 16 products, got {len(products)}"
    foods = [p for p in products if p["product_id"].startswith("food-")]
    items = [p for p in products if p["product_id"].startswith("item-")]
    assert len(foods) == 8 and len(items) == 8, f"food/item split {len(foods)}/{len(items)}"

    frozen_by_barcode = {e["barcode"]: e for e in off_frozen}
    assert set(frozen_by_barcode) == {p["product_id"].removeprefix("food-") for p in foods}, \
        "OFF frozen allowlist does not match food barcodes"
    raw_by_code = {str(x.get("code")): x for x in off_raw["products"]}
    for product in foods:
        barcode = product["product_id"].removeprefix("food-")
        frozen_kcal = frozen_by_barcode[barcode]["energy_kcal_serving"]
        assert product["energy_kcal_per_unit"] == frozen_kcal, \
            f"{barcode}: scenario kcal {product['energy_kcal_per_unit']} != frozen {frozen_kcal}"
        raw_kcal = raw_by_code[barcode].get("nutriments", {}).get("energy-kcal_serving")
        assert frozen_kcal == raw_kcal, \
            f"{barcode}: frozen kcal {frozen_kcal} != raw OFF {raw_kcal}"
    for name in ("products.json", "china-100.json"):
        entry = next(e for e in manifest["entries"] if e["path"] == f"open-food-facts/{name}")
        actual = sha256_of(OFF_DIR / name)
        assert actual == entry["sha256"], f"{name}: sha256 mismatch vs corpus manifest"

    # travel formula reproduces every frozen route fare
    for route in scenario["routes"]:
        if route["mode"] == "walking":
            expected = 0
        elif route["mode"] == "driving":
            expected = max(500, math.ceil(route["distance_meters"] * 0.2))
        else:
            raise AssertionError(f"unknown mode {route['mode']}")
        assert route["fare_fen"] == expected, \
            f"{route['route_id']}: fare {route['fare_fen']} != formula {expected}"

    # contract templates cover every contract kind present in scenarios
    kinds = {t["contract_kind"] for t in scenario["tasks"]} | \
            {t["contract_kind"] for t in other["tasks"]}
    assert kinds <= set(CONTRACT_FORMULAS), f"unknown contract kinds {kinds - set(CONTRACT_FORMULAS)}"
    task_fields = {f for t in scenario["tasks"] + other["tasks"] for f in t}
    assert {"required_effective_seconds", "input_sha256", "oracle_sha256",
            "pay_fen", "maximum_duration_seconds"} <= task_fields, "task field names drifted"

    assert (RULES_DIR / "off-foods.mapping.toml").is_file(), "missing catalog/rules/off-foods.mapping.toml"
    assert (RULES_DIR / "travel-fare.toml").is_file(), "missing catalog/rules/travel-fare.toml"

    off_sha = sha256_of(OFF_FROZEN)
    body = scenario["body"]
    for key in ("maximum_energy_kcal", "hunger_threshold_kcal", "emergency_threshold_kcal",
                "basal_kcal_per_hour", "activity_met", "food_efficiency_curve",
                "awake_sleep_debt_per_hour", "sleep_recovery_per_hour",
                "entertainment_need_per_awake_hour", "entertainment_relief_per_hour",
                "work_efficiency_curve", "work_notes_multiplier"):
        assert key in body, f"body param missing: {key}"
    return {"foods": foods, "items": items}, off_sha


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build() -> dict[str, str]:
    scenario = load_json(SCENARIO_PRIMARY)
    other = load_json(SCENARIO_SECONDARY)
    off_frozen = load_json(OFF_FROZEN)
    off_raw = load_json(OFF_RAW)
    manifest = load_json(CORPUS_MANIFEST)
    parts, off_sha = verify_inputs(scenario, other, off_frozen, off_raw, manifest)

    body = scenario["body"]
    resources = build_resources(body)
    objects = ([product_object(p, {e["barcode"]: e for e in off_frozen}, off_sha)
                for p in parts["foods"]]
               + [product_object(p, {}, off_sha) for p in parts["items"]]
               + [travel_object(scenario["routes"], scenario["transport_account_id"])]
               + contract_objects(body, scenario["employer_account_id"]))

    assert len(resources) == 4, f"expected 4 resources, got {len(resources)}"
    assert len(objects) == 20, f"expected 20 objects (16+1+3), got {len(objects)}"
    for entry in resources + objects:
        assert_full_coverage(entry["resource_id"] if "resource_id" in entry
                             else entry["object_id"], entry)

    return {
        "catalog/resources.jsonl": "".join(
            json.dumps(e, ensure_ascii=False) + "\n" for e in resources),
        "catalog/objects.jsonl": "".join(
            json.dumps(e, ensure_ascii=False) + "\n" for e in objects),
        "catalog/manifest.json": json.dumps(
            {"version_major": 1, "version_minor": 0}, indent=2) + "\n",
        "catalog/overrides/tombstones.jsonl": "",
    }


def main(argv: list[str]) -> int:
    outputs = build()
    check_only = "--check" in argv
    status = []
    for relative, content in outputs.items():
        target = ROOT / relative
        if check_only:
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            if current != content:
                first = next(((i, a, b) for i, (a, b) in enumerate(
                    zip(current.splitlines() if current else [],
                        content.splitlines())) if a != b), None)
                status.append(f"DRIFT {relative} (first differing line: "
                              f"{first[0] + 1 if first else 'length'})")
                continue
            status.append(f"OK    {relative} (byte-identical)")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            status.append(f"WROTE {relative} ({len(content.splitlines())} lines)")
    for line in status:
        print(line)
    return 1 if any(line.startswith("DRIFT") for line in status) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
