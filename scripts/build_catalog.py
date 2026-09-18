"""Build catalog data files (resources/objects JSONL + manifest + empty tombstones).

Self-contained regeneration: the generator consumes the frozen legacy-scenario
snapshot scripts/catalog-snapshot-legacy-scenario.json (semantic fields of
scenarios/white-collar-12-weeks.json as of git cd2586b, kept verbatim when the
scenarios were slimmed to schema 2) plus corpora/open-food-facts/{products,
china-100,china-pool}.json, the food-catalog expansion whitelists
corpora/foods/expansion.json (25 barcodes derived from china-100.json) and
corpora/foods/expansion2.json (>=70 barcodes derived from the larger
china-pool.json raw pool; same off-foods mapping fallback; per-category
credible kcal density windows enforced at generation time; prices anchored to
frozen baseline foods +/-30% with fixed seeds per wave) and
corpora/manifest.json, plus corpora/meals/items.json (30 contract meals with
USDA FNDDS calorie provenance), then emits the static catalog per
docs/dev/PROPOSAL-resource-registry.md (§3/§4.2/§5/§13): catalog/resources.jsonl,
catalog/objects.jsonl (121 foods + 30 meals + 8 durables + travel + contracts),
catalog/manifest.json (2.0, carrying the versioned world-material
category_requirements declaration) and an empty
catalog/overrides/tombstones.jsonl.

It no longer reads scenarios/ — scenario ↔ catalog equivalence is owned by
scripts/migrate_scenarios.py parity assertions. The --check gate therefore
proves "catalog matches the generator" (snapshot + rules + corpora), not
"catalog matches the scenarios".

Hand-written files NOT touched by this generator (per §13, rules are human-authored):
  catalog/rules/off-foods.mapping.toml, catalog/rules/travel-fare.toml,
  catalog/rules/travel-time.toml, catalog/rules/motivation.toml,
  catalog/rules/persona-mapping.toml, catalog/affordances.toml,
  catalog/README.md, catalog/snapshots/ —
  the generator only asserts these files exist.

Usage:
  python3 scripts/build_catalog.py           # write catalog data files
  python3 scripts/build_catalog.py --check   # regenerate in memory, compare bytes, exit 1 on drift
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "scripts/catalog-snapshot-legacy-scenario.json"
OFF_DIR = ROOT / "corpora/open-food-facts"
OFF_FROZEN = OFF_DIR / "products.json"
OFF_RAW = OFF_DIR / "china-100.json"
OFF_POOL = OFF_DIR / "china-pool.json"
FOODS_DIR = ROOT / "corpora/foods"
LIFESTYLE_ITEMS = ROOT / "corpora/lifestyle/items.json"
FOOD_EXPANSION = FOODS_DIR / "expansion.json"
FOOD_EXPANSION2 = FOODS_DIR / "expansion2.json"
CORPUS_MANIFEST = ROOT / "corpora/manifest.json"
MEALS_CORPUS = ROOT / "corpora/meals/items.json"
RULES_DIR = ROOT / "catalog/rules"
HEAD = "cd2586b"

#: 修订合同《世界素材品类要求》的版本化声明：随 catalog/manifest.json 冻结，
#: 场景启动准入（admission.validate_world_categories）逐条执行，不满足即拒绝启动。
#: 阈值即合同原文（docs/agentsociety2-design.md §世界素材品类要求）。
CATEGORY_REQUIREMENTS = {
    "declared_at": "2026-09-18",
    "spec": "docs/agentsociety2-design.md world-material category table (L1635-1650)",
    "meals": {
        "min_count": 30,
        "price_fen_min": 1500,
        "price_fen_max": 4500,
        "duration_minutes_min": 20,
        "duration_minutes_max": 40,
        "energy_databases": ["USDA FNDDS", "\u4e2d\u56fd\u98df\u7269\u6210\u5206\u8868"],
    },
    "location_required_kinds": [
        "home", "office", "canteen_or_restaurant", "convenience_store",
        "supermarket_or_wet_market", "park_or_fitness",
    ],
    "location_min_per_kind": {"canteen_or_restaurant": 2},
    "home_pantry": {"breakfast_servings_min": 7, "morning_wash_minutes": 15},
    "office_affordances": ["pantry_water", "meeting_room", "colleagues_present"],
    "content_metadata": {
        "fields": ["title", "genre", "language", "age_rating"],
        "max_age_rating": 17,
    },
    "work_materials": {"min_distinct": 20, "language": "zh", "horizon_days": 84},
    "lifestyle_fields": ["location_kinds", "time_window", "needs_partner", "effects"],
    "contacts": {
        "min_per_persona": 2,
        "colleague_reply_policy": "persona",
        "other_reply_policy": "model",
    },
}

#: 食品扩容的价格锚定表（条码 → 品类标签 + 锚定对象）。锚定对象必须是
#: 冻结基线 8 条食品之一，价格按锚 ±30% 以固定种子采样（anchored 溯源）。
FOOD_EXPANSION_PRICE_ANCHORS: dict[str, tuple[str, str]] = {
    "6901939621738": ("无糖汽水", "food-6928804010114"),
    "6937003704014": ("无糖汽水", "food-6928804010114"),
    "6974800253352": ("无糖汽水", "food-6928804010114"),
    "6902083881085": ("乳制品", "food-6923644266066"),
    "6907992100272": ("乳制品", "food-6923644266066"),
    "6907992500942": ("乳制品", "food-6923644266066"),
    "6907992507095": ("乳制品", "food-6923644266066"),
    "6916196410414": ("乳制品", "food-6923644266066"),
    "6923644223458": ("乳制品", "food-6923644266066"),
    "6922577790068": ("酸奶", "food-6901209212215"),
    "6902538006575": ("茶饮果汁", "food-6920459902387"),
    "6909548098880": ("茶饮果汁", "food-6920459902387"),
    "6921168593569": ("茶饮果汁", "food-6920459902387"),
    "6972020770130": ("茶饮果汁", "food-6920459902387"),
    "6914973603394": ("零食", "food-6924743919211"),
    "6921351120305": ("零食", "food-6924743919211"),
    "6924743919242": ("零食", "food-6924743919211"),
    "6943949415174": ("零食", "food-6924743919211"),
    "6950955909610": ("零食", "food-6924743919211"),
    "6937962112714": ("方便面", "food-6920152460061"),
    "6923167524186": ("坚果肉零食", "food-8935090914864"),
    "6924773540416": ("坚果肉零食", "food-8935090914864"),
    "6938360357264": ("坚果肉零食", "food-8935090914864"),
    "6948939681928": ("坚果肉零食", "food-8935090914864"),
    "6948939696106": ("坚果肉零食", "food-8935090914864"),
}
FOOD_PRICE_SEED = 20260916
FOOD_PRICE_SPREAD = 0.3
FOOD2_PRICE_SEED = 20260917

#: 第二波食品扩容的价格品类表（品类标签 → 锚定对象）。锚定对象仍必须是冻结
#: 基线 8 条食品之一，价格按锚 ±30% 以固定种子采样（anchored 溯源）。
#: 面包烘焙无冻结同类，锚定到最接近的价格量级（红烧牛肉面 650 fen）。
FOOD2_CATEGORY_ANCHORS: dict[str, str] = {
    "汽水": "food-6928804010114",
    "乳制品": "food-6923644266066",
    "酸奶": "food-6901209212215",
    "茶饮果汁": "food-6920459902387",
    "零食": "food-6924743919211",
    "方便面": "food-6920152460061",
    "面包烘焙": "food-6920152460061",
    "坚果肉零食": "food-8935090914864",
    "咖啡": "food-6917878044729",
}

#: 品类可信 kcal 密度窗（kcal/100g 或 /100ml，闭区间）。筛选期执行，
#: 生成器按原始数据逐条复核——密度越窗的记录视为 OFF 数据缺陷，不得入选。
FOOD2_CATEGORY_DENSITY_WINDOWS: dict[str, tuple[float, float]] = {
    "汽水": (0, 15),
    "茶饮果汁": (0, 70),
    "乳制品": (30, 120),
    "酸奶": (40, 120),
    "零食": (150, 600),
    "方便面": (250, 550),
    "面包烘焙": (200, 450),
    "坚果肉零食": (100, 650),
    "咖啡": (200, 600),
}


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
FACT_ARTIFACT = "docs/dev/PROPOSAL-resource-registry.md §5"
ENGINE_CLAMP = "legacy-engine-body clamp (src/worldmock/life/body.py:71,86,183,195)"


#: 能量-工作效率耦合因子的输出乘数曲线：emergency 以下 0.5、hungry 段下限 0.8、
#: hungry 阈值以上满效 1.0。阈值不新设，直接复用 body 的 hungry/emergency
#: 阈值（与 alert_rules 的 800/400 同源）；emergency 处的阶跃以 0.1 kcal
#: 过渡带编码——分段线性曲线无法表达严格跳变，带宽远小于任何分镜能耗。
ENERGY_EFFICIENCY_EMERGENCY_OUTPUT = 0.5
ENERGY_EFFICIENCY_HUNGRY_OUTPUT = 0.8
ENERGY_EFFICIENCY_STEP_BAND_KCAL = 0.1

#: wait 的被动娱乐缓解费率：wait 语义为"边等边刷手机/听点什么"，被动消费
#: 远低于主动观看/游戏（约为 watch 0.30 的 15%），不 gating 于内容偏好。
WAIT_PASSIVE_ENTERTAINMENT_RELIEF_PER_HOUR = 0.05


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
                       "docs/dev/PROPOSAL-resource-registry.md §3.1 (engine currently emits "
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
            "entertainment_relief_per_hour": {
                **body["entertainment_relief_per_hour"],
                "wait": WAIT_PASSIVE_ENTERTAINMENT_RELIEF_PER_HOUR,
            },
        },
        "provenance": [
            {"part": "bounds", "paths": ["bounds.min", "bounds.max"],
             "type": "scenario", "source": ENGINE_CLAMP},
            {"part": "drain_rules.entertainment_need_per_awake_hour",
             "paths": ["drain_rules.entertainment_need_per_awake_hour"],
             "type": "assumption",
             "source": "legacy-scenario-body entertainment_need_per_awake_hour"},
            {"part": "drain_rules.entertainment_relief_per_hour",
             "paths": ["drain_rules.entertainment_relief_per_hour.watch",
                       "drain_rules.entertainment_relief_per_hour.play_game"],
             "type": "assumption",
             "source": "legacy-scenario-body entertainment_relief_per_hour"},
            {"part": "drain_rules.entertainment_relief_per_hour.wait",
             "paths": ["drain_rules.entertainment_relief_per_hour.wait"],
             "type": "assumption",
             "source": "passive scrolling baseline: wait = waiting while casually "
                       "browsing phone / listening to something"},
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


def expansion_food_object(entry: dict, price_fen: int, anchor_label: str,
                          anchor_object_id: str, expansion_sha: str,
                          artifact: str = "corpora/foods/expansion.json",
                          price_seed: int = FOOD_PRICE_SEED) -> dict:
    """@brief 由扩容白名单条目构造食品目录对象（consume 事实边 + purchase 锚定价边）。"""
    barcode = entry["barcode"]
    return {
        "object_id": f"food-{barcode}",
        "name": entry["name"],
        "object_kind": "product",
        "effect": "nutrition",
        "consumable": True,
        "use_modes": ["eat"],
        "edges": [
            {"on": "purchase", "type": "transfer", "from": "{buyer}", "to": "{merchant}",
             "amount_fen": price_fen,
             "account_roles": {"from": "buyer", "to": "merchant:grocery"}},
            {"on": "consume", "type": "source", "resource": "energy_kcal",
             "amount": entry["energy_kcal_serving"], "duration_s": 900},
        ],
        "stock": {"initial": 120, "restock": "none"},
        "use_duration_seconds": 900,
        "delivery_delay_seconds": 1800,
        "serving": {"quantity": 1, "unit": entry["serving_size"]},
        "required_location_id": None,
        "provenance": [
            {"part": "consume energy edge", "paths": ["edges[1].amount"],
             "type": "fact", "source": f"openfoodfacts:product/{barcode}",
             "artifact": f"{artifact} (sha256 {expansion_sha})",
             "note": f"derived via {entry['kcal_rule']} x serving {entry['serving_quantity']}"
                     " per catalog/rules/off-foods.mapping.toml fallback"},
            {"part": "purchase price edge", "paths": ["edges[0].amount_fen"],
             "type": "anchored", "source": f"category anchor {anchor_object_id} ({anchor_label})",
             "artifact": "catalog/objects.jsonl @ 1.0",
             "method": f"anchor_fen * uniform(1-{FOOD_PRICE_SPREAD}, 1+{FOOD_PRICE_SPREAD}),"
                       f" seed {price_seed}, barcode-sorted order",
             "note": "price_version catalog-food-expansion-2"
                     if artifact.endswith("expansion2.json") else
                     "price_version catalog-food-expansion-1"},
            {"part": "stock", "paths": ["stock.initial"],
             "type": "scenario", "source": "legacy-scenario-product"},
            {"part": "legacy timing/serving fields",
             "paths": ["edges[1].duration_s", "use_duration_seconds",
                       "delivery_delay_seconds", "serving.quantity"],
             "type": "scenario", "source": "legacy-scenario-product"},
        ],
        "version": 1,
    }


def load_expansion_entries() -> list[dict]:
    """@brief 读取并相互校验扩容白名单与价格锚定表。"""
    document = load_json(FOOD_EXPANSION)
    entries = document["entries"]
    by_barcode = {entry["barcode"]: entry for entry in entries}
    assert len(by_barcode) == len(entries) == 25, "expansion whitelist must hold 25 unique entries"
    assert set(by_barcode) == set(FOOD_EXPANSION_PRICE_ANCHORS), \
        "expansion whitelist and price anchor table disagree"
    return entries


def expansion_prices(entries: list[dict], frozen_foods: list[dict]) -> dict[str, int]:
    """@brief 以固定种子按品类锚定价 ±30% 采样每条扩容食品价格（条码序稳定）。"""
    anchors = {product["product_id"]: product["price_fen"] for product in frozen_foods}
    rng = random.Random(FOOD_PRICE_SEED)
    prices = {}
    for barcode in sorted(entry["barcode"] for entry in entries):
        _, anchor_object_id = FOOD_EXPANSION_PRICE_ANCHORS[barcode]
        factor = rng.uniform(1 - FOOD_PRICE_SPREAD, 1 + FOOD_PRICE_SPREAD)
        prices[barcode] = max(1, round(anchors[anchor_object_id] * factor))
    return prices


def verify_expansion(entries: list[dict], off_frozen: list[dict], off_raw: dict,
                     snapshot_foods: list[dict]) -> None:
    """@brief 核对扩容白名单与冻结原始数据、基线白名单的一致性。"""
    raw_by_code = {str(x.get("code")): x for x in off_raw["products"]}
    frozen_barcodes = {entry["barcode"] for entry in off_frozen}
    snapshot_ids = {product["product_id"] for product in snapshot_foods}
    for entry in entries:
        barcode = entry["barcode"]
        assert barcode not in frozen_barcodes, f"{barcode}: duplicates frozen baseline"
        assert f"food-{barcode}" not in snapshot_ids, f"{barcode}: duplicates snapshot food"
        raw = raw_by_code.get(barcode)
        assert raw is not None, f"{barcode}: not in china-100.json"
        nut = raw.get("nutriments", {})
        if entry["kcal_rule"].startswith("nutriments.energy-kcal_100g"):
            kcal100 = nut.get("energy-kcal_100g")
        else:
            assert "energy-kj_100g" in nut, f"{barcode}: rule/data mismatch"
            kcal100 = nut["energy-kj_100g"] / 4.184
        derived = kcal100 * entry["serving_quantity"] / 100.0
        assert abs(derived - entry["energy_kcal_serving"]) <= 0.05, \
            f"{barcode}: kcal {entry['energy_kcal_serving']} != derived {derived:.2f}"
        assert str(entry["name"]) == str(raw.get("product_name_zh") or raw.get("product_name")), \
            f"{barcode}: name drifted from raw OFF record"
        unit = entry["serving_size"].removeprefix(str(entry["serving_quantity"]))
        assert unit in ("g", "ml"), f"{barcode}: serving unit {unit!r} invalid"


def load_expansion2_entries() -> list[dict]:
    """@brief 读取并校验第二波扩容白名单（≥70 条，品类齐备，条码唯一）。"""
    document = load_json(FOOD_EXPANSION2)
    entries = document["entries"]
    barcodes = [entry["barcode"] for entry in entries]
    assert len(set(barcodes)) == len(barcodes), "wave-2 barcodes must be unique"
    assert len(entries) >= 70, f"wave-2 expansion must hold >=70 entries, got {len(entries)}"
    for entry in entries:
        assert entry["category"] in FOOD2_CATEGORY_ANCHORS, \
            f"{entry['barcode']}: price category {entry['category']!r} has no anchor"
    return entries


def verify_expansion2(entries: list[dict], off_pool: dict, off_frozen: list[dict],
                      wave1_barcodes: set[str], snapshot_foods: list[dict]) -> None:
    """@brief 核对第二波白名单与原始池、既有白名单的一致性（kcal/份量/密度窗逐条复核）。"""
    pool_by_code = {str(x.get("code")): x for x in off_pool["products"]}
    assert len(pool_by_code) >= 300, \
        f"raw pool must hold >=300 unique products, got {len(pool_by_code)}"
    kcal_pool = [x for x in off_pool["products"]
                 if (x.get("nutriments", {}).get("energy-kcal_100g") is not None
                     or x.get("nutriments", {}).get("energy-kj_100g") is not None)]
    assert len(kcal_pool) >= 300, \
        f"raw pool must hold >=300 kcal-bearing products, got {len(kcal_pool)}"
    frozen_barcodes = {entry["barcode"] for entry in off_frozen}
    snapshot_ids = {product["product_id"] for product in snapshot_foods}
    for entry in entries:
        barcode = entry["barcode"]
        assert barcode not in frozen_barcodes, f"{barcode}: duplicates frozen baseline"
        assert barcode not in wave1_barcodes, f"{barcode}: duplicates wave-1 expansion"
        assert f"food-{barcode}" not in snapshot_ids, f"{barcode}: duplicates snapshot food"
        raw = pool_by_code.get(barcode)
        assert raw is not None, f"{barcode}: not in china-pool.json"
        nut = raw.get("nutriments", {})
        if entry["kcal_rule"].startswith("nutriments.energy-kcal_100g"):
            kcal100 = nut.get("energy-kcal_100g")
        else:
            assert "energy-kj_100g" in nut, f"{barcode}: rule/data mismatch"
            kcal100 = nut["energy-kj_100g"] / 4.184
        window = FOOD2_CATEGORY_DENSITY_WINDOWS[entry["category"]]
        assert window[0] <= kcal100 <= window[1], \
            f"{barcode}: density {kcal100:.1f} outside credible window {window} for {entry['category']}"
        derived = kcal100 * entry["serving_quantity"] / 100.0
        assert abs(derived - entry["energy_kcal_serving"]) <= 0.05, \
            f"{barcode}: kcal {entry['energy_kcal_serving']} != derived {derived:.2f}"
        assert entry["energy_kcal_serving"] <= 800, f"{barcode}: per-serving kcal over 800"
        assert str(entry["name"]) == str(raw.get("product_name_zh") or raw.get("product_name")), \
            f"{barcode}: name drifted from raw OFF record"
        unit = entry["serving_size"].removeprefix(str(entry["serving_quantity"]))
        assert unit in ("g", "ml"), f"{barcode}: serving unit {unit!r} invalid"
        raw_sq = raw.get("serving_quantity")
        assert raw_sq not in (None, "") and abs(float(raw_sq) - entry["serving_quantity"]) <= 0.01, \
            f"{barcode}: serving_quantity {entry['serving_quantity']} != raw {raw_sq}"
        raw_ss = str(raw.get("serving_size", "")).lower()
        assert raw_ss.endswith(unit) or raw_ss.endswith(f"({unit})") or raw_ss.endswith(f" {unit})"), \
            f"{barcode}: serving unit disagrees with raw serving_size"


def expansion2_prices(entries: list[dict], frozen_foods: list[dict]) -> dict[str, int]:
    """@brief 以固定种子按品类锚定价 ±30% 采样第二波食品价格（条码序稳定）。"""
    anchors = {product["product_id"]: product["price_fen"] for product in frozen_foods}
    by_barcode = {entry["barcode"]: entry for entry in entries}
    rng = random.Random(FOOD2_PRICE_SEED)
    prices = {}
    for barcode in sorted(by_barcode):
        category = by_barcode[barcode]["category"]
        factor = rng.uniform(1 - FOOD_PRICE_SPREAD, 1 + FOOD_PRICE_SPREAD)
        prices[barcode] = max(1, round(anchors[FOOD2_CATEGORY_ANCHORS[category]] * factor))
    return prices


def load_meals() -> list[dict]:
    """@brief 读取正餐语料并做合同窗口校验（数量/价格/时长/来源逐条）。"""
    document = load_json(MEALS_CORPUS)
    meals = document["items"]
    assert len(meals) >= CATEGORY_REQUIREMENTS["meals"]["min_count"], \
        f"meals corpus holds {len(meals)} below contract minimum"
    for meal in meals:
        window = CATEGORY_REQUIREMENTS["meals"]
        assert window["price_fen_min"] <= meal["price_fen"] <= window["price_fen_max"], \
            f"{meal['meal_id']}: price outside 15-45 yuan window"
        assert window["duration_minutes_min"] <= meal["meal_duration_minutes"] \
            <= window["duration_minutes_max"], f"{meal['meal_id']}: duration outside window"
        assert meal["dine_in"] or meal["delivery"], f"{meal['meal_id']}: no dine-in nor delivery"
        source = meal["energy_source"]
        assert source["energy_kcal_per_100g"] > 0 and source["source_url"] \
            and source["retrieved_at"] and source["unit_conversion"], \
            f"{meal['meal_id']}: energy source incomplete"
        derived = round(source["energy_kcal_per_100g"] * meal["serving"]["quantity"] / 100.0)
        assert derived == meal["energy_kcal_per_serving"], \
            f"{meal['meal_id']}: kcal {meal['energy_kcal_per_serving']} != derived {derived}"
    return meals


def meal_object(meal: dict, meals_sha: str) -> dict:
    """@brief 由正餐语料条目构造目录对象（consume 事实边 + purchase 场景价边）。"""
    source = meal["energy_source"]
    duration_seconds = meal["meal_duration_minutes"] * 60
    return {
        "object_id": meal["meal_id"],
        "name": meal["name_zh"],
        "object_kind": "product",
        "effect": "nutrition",
        "consumable": True,
        "use_modes": ["eat"],
        "edges": [
            {"on": "purchase", "type": "transfer", "from": "{buyer}", "to": "{merchant}",
             "amount_fen": meal["price_fen"],
             "account_roles": {"from": "buyer", "to": "merchant:restaurant"}},
            {"on": "consume", "type": "source", "resource": "energy_kcal",
             "amount": meal["energy_kcal_per_serving"], "duration_s": duration_seconds},
        ],
        "stock": {"initial": 40, "restock": "none"},
        "use_duration_seconds": duration_seconds,
        "delivery_delay_seconds": meal["delivery_delay_seconds"] or 1800,
        "serving": {"quantity": meal["serving"]["quantity"], "unit": meal["serving"]["unit"]},
        "required_location_id": None,
        "provenance": [
            {"part": "consume energy edge", "paths": ["edges[1].amount"],
             "type": "fact",
             "source": f"USDA FNDDS fdc:{source['fdc_id']} ({source['food_description']})",
             "artifact": f"corpora/meals/items.json (sha256 {meals_sha})",
             "note": (f"kcal = {source['energy_kcal_per_100g']} kcal/100g x"
                      f" {meal['serving']['quantity']} g / 100; no ml-to-g conversion;"
                      " FNDDS recorded values not adjusted")},
            {"part": "purchase price edge", "paths": ["edges[0].amount_fen"],
             "type": "scenario", "source": meal["price_assumption"],
             "note": "price_version zhangjiang-2026-q1-meals"},
            {"part": "stock", "paths": ["stock.initial"],
             "type": "scenario", "source": "legacy-scenario-product"},
            {"part": "meal timing/serving fields",
             "paths": ["edges[1].duration_s", "use_duration_seconds",
                       "delivery_delay_seconds", "serving.quantity"],
             "type": "scenario",
             "source": "meal corpus scenario assumptions (duration 20-40 min per contract)"},
        ],
        "version": 1,
    }


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
    for name in ("products.json", "china-100.json", "china-pool.json"):
        entry = next(e for e in manifest["entries"] if e["path"] == f"open-food-facts/{name}")
        actual = sha256_of(OFF_DIR / name)
        assert actual == entry["sha256"], f"{name}: sha256 mismatch vs corpus manifest"
    for name in ("expansion.json", "expansion2.json"):
        foods_entry = next(e for e in manifest["entries"] if e["path"] == f"foods/{name}")
        assert sha256_of(FOODS_DIR / name) == foods_entry["sha256"], \
            f"foods/{name}: sha256 mismatch vs corpus manifest"
    meals_entry = next(e for e in manifest["entries"] if e["path"] == "meals/items.json")
    assert sha256_of(MEALS_CORPUS) == meals_entry["sha256"], \
        "meals/items.json: sha256 mismatch vs corpus manifest"

    # contract templates cover every legacy contract kind
    assert set(CONTRACT_FORMULAS) == {"hourly", "piece", "package"}

    assert (RULES_DIR / "off-foods.mapping.toml").is_file(), "missing catalog/rules/off-foods.mapping.toml"
    assert (RULES_DIR / "travel-fare.toml").is_file(), "missing catalog/rules/travel-fare.toml"
    assert (RULES_DIR / "travel-time.toml").is_file(), "missing catalog/rules/travel-time.toml"
    assert (RULES_DIR / "motivation.toml").is_file(), "missing catalog/rules/motivation.toml"
    assert (RULES_DIR / "persona-mapping.toml").is_file(), "missing catalog/rules/persona-mapping.toml"
    assert (ROOT / "catalog/affordances.toml").is_file(), "missing catalog/affordances.toml"
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
    lifestyle = load_json(LIFESTYLE_ITEMS)["items"]
    assert 180 <= len(lifestyle) <= 200, f"lifestyle catalog size {len(lifestyle)} out of range"
    assert all("Compendium 2011" in item["source"] for item in lifestyle), \
        "lifestyle item missing Compendium MET provenance"
    return {"foods": foods, "items": items}, off_sha


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def lifestyle_met_map() -> dict[str, float]:
    """@brief 生活活动目录 → lifestyle:<id> MET 键（键排序保证输出字节稳定）。"""
    items = load_json(LIFESTYLE_ITEMS)["items"]
    merged = {f"lifestyle:{item['activity_id']}": float(item["met_value"]) for item in items}
    assert len(merged) == len(items), "lifestyle activity_id collision in MET map"
    return dict(sorted(merged.items()))


def build() -> dict[str, str]:
    snapshot = load_json(SNAPSHOT)
    off_frozen = load_json(OFF_FROZEN)
    off_raw = load_json(OFF_RAW)
    off_pool = load_json(OFF_POOL)
    manifest = load_json(CORPUS_MANIFEST)
    parts, off_sha = verify_inputs(snapshot, off_frozen, off_raw, manifest)

    expansion_entries = load_expansion_entries()
    verify_expansion(expansion_entries, off_frozen, off_raw, parts["foods"])
    expansion_sha = sha256_of(FOOD_EXPANSION)
    prices = expansion_prices(expansion_entries, parts["foods"])

    expansion2_entries = load_expansion2_entries()
    verify_expansion2(expansion2_entries, off_pool, off_frozen,
                      {entry["barcode"] for entry in expansion_entries}, parts["foods"])
    expansion2_sha = sha256_of(FOOD_EXPANSION2)
    prices2 = expansion2_prices(expansion2_entries, parts["foods"])

    meals = load_meals()
    meals_sha = sha256_of(MEALS_CORPUS)

    # 基础活动 MET 来自冻结快照；生活活动 MET 来自语料目录（引擎按
    # lifestyle:<id> 查表，语义层校验基础键封闭 + lifestyle 键开放）。
    body = dict(snapshot["body"])
    body["activity_met"] = {**snapshot["body"]["activity_met"], **lifestyle_met_map()}
    resources = build_resources(body)
    frozen_food_objects = [product_object(p, {e["barcode"]: e for e in off_frozen}, off_sha)
                           for p in parts["foods"]]
    expansion_food_objects = [
        expansion_food_object(entry, prices[entry["barcode"]],
                              FOOD_EXPANSION_PRICE_ANCHORS[entry["barcode"]][0],
                              FOOD_EXPANSION_PRICE_ANCHORS[entry["barcode"]][1],
                              expansion_sha)
        for entry in sorted(expansion_entries, key=lambda item: item["barcode"])]
    expansion2_food_objects = [
        expansion_food_object(entry, prices2[entry["barcode"]],
                              entry["category"],
                              FOOD2_CATEGORY_ANCHORS[entry["category"]],
                              expansion2_sha,
                              artifact="corpora/foods/expansion2.json",
                              price_seed=FOOD2_PRICE_SEED)
        for entry in sorted(expansion2_entries, key=lambda item: item["barcode"])]
    meal_objects = [meal_object(meal, meals_sha) for meal in sorted(meals, key=lambda m: m["meal_id"])]
    objects = (frozen_food_objects + expansion_food_objects + expansion2_food_objects
               + meal_objects
               + [product_object(p, {}, off_sha) for p in parts["items"]]
               + [travel_object(snapshot["transport_account_id"])]
               + contract_objects(body, snapshot["employer_account_id"]))

    assert len(resources) == 4, f"expected 4 resources, got {len(resources)}"
    expected_objects = 45 + len(expansion2_entries) + len(meal_objects)
    assert len(objects) == expected_objects, (
        f"expected {expected_objects} objects (16+25+{len(expansion2_entries)}"
        f"+{len(meal_objects)} meals+1+3), got {len(objects)}")
    for entry in resources + objects:
        assert_full_coverage(entry["resource_id"] if "resource_id" in entry
                             else entry["object_id"], entry)

    return {
        "catalog/resources.jsonl": "".join(
            json.dumps(e, ensure_ascii=False) + "\n" for e in resources),
        "catalog/objects.jsonl": "".join(
            json.dumps(e, ensure_ascii=False) + "\n" for e in objects),
        "catalog/manifest.json": json.dumps(
            {"version_major": 2, "version_minor": 0,
             "category_requirements": CATEGORY_REQUIREMENTS},
            ensure_ascii=False, indent=2) + "\n",
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
