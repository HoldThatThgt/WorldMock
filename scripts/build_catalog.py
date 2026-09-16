"""Build catalog data files (resources/objects JSONL + manifest + empty tombstones).

Self-contained regeneration: the generator consumes the frozen legacy-scenario
snapshot scripts/catalog-snapshot-legacy-scenario.json (semantic fields of
scenarios/white-collar-12-weeks.json as of git cd2586b, kept verbatim when the
scenarios were slimmed to schema 2) plus corpora/open-food-facts/{products,
china-100}.json (+ corpora/manifest.json) and emits the v1 static catalog per
docs/resource-registry-design.md (§3/§4.2/§5/§13): catalog/resources.jsonl,
catalog/objects.jsonl, catalog/manifest.json (1.0) and an empty
catalog/overrides/tombstones.jsonl.

It no longer reads scenarios/ — scenario ↔ catalog equivalence is owned by
scripts/migrate_scenarios.py parity assertions. The --check gate therefore
proves "catalog matches the generator" (snapshot + rules + corpora), not
"catalog matches the scenarios".

Hand-written files NOT touched by this generator (per §13, rules are human-authored):
  catalog/rules/off-foods.mapping.toml, catalog/rules/travel-fare.toml,
  catalog/rules/travel-time.toml, catalog/rules/motivation.toml,
  catalog/rules/persona-mapping.toml, catalog/README.md,
  catalog/snapshots/ —
  the generator only asserts these files exist.

Usage:
  python3 scripts/build_catalog.py           # write catalog data files
  python3 scripts/build_catalog.py --check   # regenerate in memory, compare bytes, exit 1 on drift
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "scripts/catalog-snapshot-legacy-scenario.json"
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


#: 能量-工作效率耦合因子的输出乘数曲线：emergency 以下 0.5、hungry 段下限 0.8、
#: hungry 阈值以上满效 1.0。阈值不新设，直接复用 body 的 hungry/emergency
#: 阈值（与 alert_rules 的 800/400 同源）；emergency 处的阶跃以 0.1 kcal
#: 过渡带编码——分段线性曲线无法表达严格跳变，带宽远小于任何分镜能耗。
ENERGY_EFFICIENCY_EMERGENCY_OUTPUT = 0.5
ENERGY_EFFICIENCY_HUNGRY_OUTPUT = 0.8
ENERGY_EFFICIENCY_STEP_BAND_KCAL = 0.1


def energy_efficiency_factor_curve(body: dict) -> list[dict]:
    """@brief 由 alert_rules 同源阈值构造能量-工作效率因子曲线点。"""
    hunger = body["hunger_threshold_kcal"]
    emergency = body["emergency_threshold_kcal"]
    return [
        {"input_value": 0, "output_value": ENERGY_EFFICIENCY_EMERGENCY_OUTPUT},
        {"input_value": emergency - ENERGY_EFFICIENCY_STEP_BAND_KCAL,
         "output_value": ENERGY_EFFICIENCY_EMERGENCY_OUTPUT},
        {"input_value": emergency, "output_value": ENERGY_EFFICIENCY_HUNGRY_OUTPUT},
        {"input_value": hunger, "output_value": 1.0},
    ]


def work_efficiency_factors(body: dict) -> list[dict]:
    """@brief 声明式工作效率因子列表：睡眠债因子（沿用冻结曲线）+ 能量因子。"""
    return [
        {"input_resource": "sleep_debt_hours", "curve": body["work_efficiency_curve"]},
        {"input_resource": "energy_kcal", "curve": energy_efficiency_factor_curve(body)},
    ]


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


def travel_object(transport_account_id: str) -> dict:
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
                "work_efficiency_factors": work_efficiency_factors(body),
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
                 "source": "legacy-scenario-body work_efficiency_curve/work_notes_multiplier; "
                           "energy factor = hunger-productivity coupling for survival-economy "
                           "feedback (hungry/emergency thresholds reused from alert_rules; "
                           "no external data, sensitivity-checked by evaluation layer)"},
            ],
            "version": 1,
        })
    return objects


# ---------------------------------------------------------------------------
# input verification
# ---------------------------------------------------------------------------

def verify_inputs(snapshot: dict, off_frozen: list, off_raw: dict,
                  manifest: dict) -> tuple[dict, str]:
    assert "scenarios/white-collar-12-weeks.json @ cd2586b" in snapshot["source"], \
        "frozen snapshot provenance marker drifted"
    products = snapshot["products"]
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
            f"{barcode}: snapshot kcal {product['energy_kcal_per_unit']} != frozen {frozen_kcal}"
        raw_kcal = raw_by_code[barcode].get("nutriments", {}).get("energy-kcal_serving")
        assert frozen_kcal == raw_kcal, \
            f"{barcode}: frozen kcal {frozen_kcal} != raw OFF {raw_kcal}"
    for name in ("products.json", "china-100.json"):
        entry = next(e for e in manifest["entries"] if e["path"] == f"open-food-facts/{name}")
        actual = sha256_of(OFF_DIR / name)
        assert actual == entry["sha256"], f"{name}: sha256 mismatch vs corpus manifest"

    # contract templates cover every legacy contract kind
    assert set(CONTRACT_FORMULAS) == {"hourly", "piece", "package"}

    assert (RULES_DIR / "off-foods.mapping.toml").is_file(), "missing catalog/rules/off-foods.mapping.toml"
    assert (RULES_DIR / "travel-fare.toml").is_file(), "missing catalog/rules/travel-fare.toml"
    assert (RULES_DIR / "travel-time.toml").is_file(), "missing catalog/rules/travel-time.toml"
    assert (RULES_DIR / "motivation.toml").is_file(), "missing catalog/rules/motivation.toml"
    assert (RULES_DIR / "persona-mapping.toml").is_file(), "missing catalog/rules/persona-mapping.toml"
    traffic_dir = ROOT / "catalog/snapshots/traffic"
    assert (traffic_dir / "amap-report-excerpt.md").is_file(), \
        "missing catalog/snapshots/traffic/amap-report-excerpt.md"
    assert (traffic_dir / "weekday-hour-factors.json").is_file(), \
        "missing catalog/snapshots/traffic/weekday-hour-factors.json"

    off_sha = sha256_of(OFF_FROZEN)
    body = snapshot["body"]
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
    snapshot = load_json(SNAPSHOT)
    off_frozen = load_json(OFF_FROZEN)
    off_raw = load_json(OFF_RAW)
    manifest = load_json(CORPUS_MANIFEST)
    parts, off_sha = verify_inputs(snapshot, off_frozen, off_raw, manifest)

    body = snapshot["body"]
    resources = build_resources(body)
    objects = ([product_object(p, {e["barcode"]: e for e in off_frozen}, off_sha)
                for p in parts["foods"]]
               + [product_object(p, {}, off_sha) for p in parts["items"]]
               + [travel_object(snapshot["transport_account_id"])]
               + contract_objects(body, snapshot["employer_account_id"]))

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
