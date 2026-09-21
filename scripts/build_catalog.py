"""@brief 从冻结来源生成资源、原生对象、目录清单与空墓碑。

@file build_catalog.py
@note 输入为已冻结的场景快照、食品白名单、原始食品记录及语料；不读取 scenarios/。
      正式输出为 5 个资源、163 个对象（121 食品、30 正餐、8 耐用品、1 服务、3 合同）
      与绑定品类契约、闭集交换规则和世界素材品类要求的 4.0 目录清单。
      数值来源必须逐项覆盖；生成器不执行任意文本公式。

品类契约、交换规则、数值出行时长、其他规则与可供性声明由审核后的维护流程管理。
生成器不覆写这些文件，但读取其原始字节计算目录内容摘要。
来源快照、来源映射和说明文档保持原样；运行时场景准入独立检查实际实例绑定。

直接运行本脚本生成目录；增加 --check 时在内存重建、执行数据检查并逐字节比较，
发现不一致返回非零退出码。设计依据见 docs/dev/SPEC-category-contract.md。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldmock.categories.identity import refresh_manifest_identity  # noqa: E402

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
CATALOG_DIR = ROOT / "catalog"
RULES_DIR = CATALOG_DIR / "rules"
CATEGORY_CONTRACT = CATALOG_DIR / "category-contract.json"
EXCHANGE_RULES = CATALOG_DIR / "exchange-rules.json"
AFFORDANCES = CATALOG_DIR / "affordances.toml"
TOMBSTONES = CATALOG_DIR / "overrides/tombstones.jsonl"
HEAD = "cd2586b"

#: 修订合同《世界素材品类要求》的版本化声明：随 catalog/manifest.json 冻结，
#: 场景启动准入（admission.validate_world_categories）逐条执行，不满足即拒绝启动。
#: 阈值即合同原文（`docs/dev/SPEC-life-realism.md`「世界素材品类要求」）。
CATEGORY_REQUIREMENTS = {'declared_at': '2026-09-18',
 'spec': 'docs/dev/SPEC-life-realism.md 世界素材品类要求',
 'meals': {'min_count': 30,
           'price_fen_min': 1500,
           'price_fen_max': 4500,
           'duration_minutes_min': 20,
           'duration_minutes_max': 40,
           'energy_databases': ['USDA FNDDS', '中国食物成分表']},
 'location_required_kinds': ['home',
                             'office',
                             'restaurant',
                             'convenience_store',
                             'supermarket',
                             'wet_market',
                             'park',
                             'fitness'],
 'location_min_per_kind': {'restaurant': 2},
 'home_pantry': {'breakfast_servings_min': 7, 'morning_wash_minutes': 15},
 'office_affordances': ['pantry_water', 'meeting_room', 'colleagues_present'],
 'content_metadata': {'fields': ['title', 'genre', 'language', 'age_rating'], 'max_age_rating': 17},
 'work_materials': {'min_distinct': 20, 'language': 'zh', 'horizon_days': 84},
 'lifestyle_fields': ['location_kinds', 'time_window', 'needs_partner', 'effects'],
 'contacts': {'min_per_persona': 2,
              'colleague_reply_policy': 'persona',
              'other_reply_policy': 'model'}}

#: 食品扩容的冻结分类表（条码 → 品类标签），只用于选择继承的营养属性约束。
FOOD_CATEGORIES_BY_BARCODE = {
    '6901939621738': '无糖汽水',
    '6937003704014': '无糖汽水',
    '6974800253352': '无糖汽水',
    '6902083881085': '乳制品',
    '6907992100272': '乳制品',
    '6907992500942': '乳制品',
    '6907992507095': '乳制品',
    '6916196410414': '乳制品',
    '6923644223458': '乳制品',
    '6922577790068': '酸奶',
    '6902538006575': '茶饮果汁',
    '6909548098880': '茶饮果汁',
    '6921168593569': '茶饮果汁',
    '6972020770130': '茶饮果汁',
    '6914973603394': '零食',
    '6921351120305': '零食',
    '6924743919242': '零食',
    '6943949415174': '零食',
    '6950955909610': '零食',
    '6937962112714': '方便面',
    '6923167524186': '坚果肉零食',
    '6924773540416': '坚果肉零食',
    '6938360357264': '坚果肉零食',
    '6948939681928': '坚果肉零食',
    '6948939696106': '坚果肉零食',
}

#: 第二波食品扩容允许的品类标签闭集；门店价格不由商品生成器生成。
FOOD_CATEGORIES = frozenset({
    '汽水',
    '乳制品',
    '酸奶',
    '茶饮果汁',
    '零食',
    '方便面',
    '面包烘焙',
    '坚果肉零食',
    '咖啡',
})

CATEGORY_ID_BY_LABEL = {
    "汽水": "food-soda",
    "无糖汽水": "food-soda",
    "乳制品": "food-dairy",
    "酸奶": "food-yogurt",
    "茶饮果汁": "food-tea-juice",
    "零食": "food-snack",
    "方便面": "food-instant-noodle",
    "面包烘焙": "food-bakery",
    "坚果肉零食": "food-nut-meat-snack",
    "咖啡": "food-coffee",
}

BASELINE_FOOD_CATEGORIES = {
    "6923644266066": "food-dairy",
    "6928804010114": "food-soda",
    "6917878044729": "food-coffee",
    "6924743919211": "food-snack",
    "6920152460061": "food-instant-noodle",
    "6920459902387": "food-tea-juice",
    "6901209212215": "food-yogurt",
    "8935090914864": "food-nut-meat-snack",
}

FOOD_CATEGORY_OVERRIDES = {
    "6943949415174": "food-light-snack",
    "6948939681928": "food-high-density-nut-snack",
    "6948939696106": "food-high-density-nut-snack",
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 数值来源遍历：每个静态数值都必须被来源覆盖
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


def numeric_pointer_leaves(node, prefix=""):
    """@brief 收集原生增量对象中的数值叶 JSON Pointer。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if not prefix and key in {"version", "provenance"}:
                continue
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from numeric_pointer_leaves(value, f"{prefix}/{escaped}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from numeric_pointer_leaves(value, f"{prefix}/{index}")
    elif not isinstance(node, bool) and isinstance(node, (int, float)):
        yield prefix


def assert_raw_object_coverage(entry: dict) -> None:
    """@brief 要求对象的每个非版本数值由且只由一条来源覆盖。"""
    coverage: dict[str, list[str]] = {}
    for source in entry["provenance"]:
        for pointer in source["paths"]:
            for leaf in numeric_pointer_leaves(_resolve_pointer(entry, pointer), pointer):
                coverage.setdefault(leaf, []).append(source["source_id"])
    for leaf in numeric_pointer_leaves(entry):
        assert len(coverage.get(leaf, [])) == 1, (
            f"{entry['object_id']}: {leaf} coverage is {coverage.get(leaf, [])}")


def _resolve_pointer(document: dict, pointer: str):
    """@brief 解析生成器内部使用的简单 RFC 6901 指针。"""
    value = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


# ---------------------------------------------------------------------------
# resources.jsonl
# ---------------------------------------------------------------------------

FACT_SOURCES = {
    "basal": "Mifflin-St Jeor BMR 65kg male ≈1560kcal/day",
    "met": "Compendium of Physical Activities 2011",
    "dri": "中国居民膳食营养素参考摄入量",
}
FACT_ARTIFACT = "docs/design/03-resource-engine.md「来源分类」"
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


def build_energy_resource(body: dict) -> dict:
    """@brief 构造能量资源及其来源记录。"""
    return {
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
                       "docs/design/03-resource-engine.md「可计量资源定义」 (engine currently emits "
                       "lowercase hungry/emergency_energy, src/worldmock/life/body.py:126-129)"},
        ],
        "version": 1,
    }


def build_sleep_resource(body: dict) -> dict:
    """@brief 构造睡眠债资源及其来源记录。"""
    return {
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


def build_entertainment_resource(body: dict) -> dict:
    """@brief 构造娱乐需求资源及其来源记录。"""
    return {
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
             "paths": ["drain_rules.entertainment_relief_per_hour.watch",
                       "drain_rules.entertainment_relief_per_hour.play_game"],
             "type": "assumption",
             "source": "legacy-scenario-body entertainment_relief_per_hour"},
        ],
        "version": 1,
    }


def build_money_resource() -> dict:
    """@brief 构造全局货币资源。"""
    return {
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


def build_inventory_resource() -> dict:
    """@brief 构造位置库存数量域；正式目录允许开系统增减。"""
    return {
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


def build_resources(body: dict) -> list[dict]:
    """@brief 以稳定顺序构造全部正式资源。"""
    return [
        build_money_resource(),
        build_energy_resource(body),
        build_sleep_resource(body),
        build_entertainment_resource(body),
        build_inventory_resource(),
    ]


# ---------------------------------------------------------------------------
# objects.jsonl
# ---------------------------------------------------------------------------

def source_record(source_id: str, source_type: str, source: str,
                  paths: list[str], **details) -> dict:
    """@brief 构造严格来源记录；调用方必须提供真实生产者。"""
    record = {
        "source_id": source_id,
        "type": source_type,
        "producer": details.pop("producer", "data_source"),
        "source": source,
        "paths": paths,
    }
    record.update({key: value for key, value in details.items() if value is not None})
    return record


def raw_object(object_id: str, name: str, category_id: str,
               absolute: dict, provenance: list[dict]) -> dict:
    """@brief 构造无边、无公式、无品类结构的原生对象增量。"""
    return {
        "object_id": object_id,
        "name": name,
        "category_id": category_id,
        "values": {"absolute": absolute, "relative": {}, "proportional": {}},
        "provenance": provenance,
        "version": 1,
    }




def energy_density(raw: dict) -> float:
    """@brief 从 OFF 原始记录读取 kcal/100g 或 kcal/100ml。"""
    nutrients = raw.get("nutriments", {})
    kcal = nutrients.get("energy-kcal_100g")
    if kcal is not None:
        return float(kcal)
    kilojoules = nutrients.get("energy-kj_100g")
    assert kilojoules is not None, f"{raw.get('code')}: missing energy density"
    return float(kilojoules) / 4.184


def category_density_bounds(contract: dict) -> dict[str, tuple[float, float]]:
    """@brief 从冻结品类槽位解析每个食品叶的密度交集。"""
    nodes = {node["category_id"]: node for node in contract["categories"]}
    bounds: dict[str, tuple[float, float]] = {}
    category_ids = (set(CATEGORY_ID_BY_LABEL.values()) | set(BASELINE_FOOD_CATEGORIES.values())
                    | set(FOOD_CATEGORY_OVERRIDES.values()) | {"food", "prepared-meal"})
    for category_id in category_ids:
        minimum, maximum = 0.0, float("inf")
        current = nodes[category_id]
        while current is not None:
            slot = current.get("slots", {}).get("energy_density_kcal_per_100")
            if slot is not None:
                schema = slot["schema"]
                minimum = max(minimum, float(schema.get("minimum", minimum)))
                maximum = min(maximum, float(schema.get("maximum", maximum)))
            current = nodes.get(current["parent_id"])
        bounds[category_id] = (minimum, maximum)
    return bounds


def validate_density(object_id: str, category_id: str, density: float,
                     bounds: dict[str, tuple[float, float]]) -> None:
    """@brief 用品类槽位而非生成器常量校验食品密度。"""
    minimum, maximum = bounds[category_id]
    assert minimum <= density <= maximum, (
        f"{object_id}: density {density:.3f} outside category slot [{minimum}, {maximum}]")


def frozen_food_object(product: dict, raw: dict, off_sha: str,
                       bounds: dict[str, tuple[float, float]]) -> dict:
    """@brief 构造八条冻结基线食品的原生增量。"""
    barcode = product["product_id"].removeprefix("food-")
    category_id = BASELINE_FOOD_CATEGORIES[barcode]
    density = energy_density(raw)
    validate_density(product["product_id"], category_id, density, bounds)
    values = {
        "energy_kcal_per_serving": product["energy_kcal_per_unit"],
        "energy_density_kcal_per_100": density,
        "serving_unit": product["serving_unit"],
    }
    sources = [
        source_record(
            "off-energy", "fact", f"openfoodfacts:product/{barcode}",
            ["/values/absolute/energy_kcal_per_serving",
             "/values/absolute/energy_density_kcal_per_100"],
            artifact=f"corpora/open-food-facts/products.json (sha256 {off_sha})"),
    ]
    return raw_object(product["product_id"], product["name"], category_id, values, sources)


def durable_object(product: dict) -> dict:
    """@brief 构造八条耐用品的原生差异；共享时长由品类继承。"""
    values = {
        "effect": product["effect"],
        "consumable": product["consumable"],
    }
    if product["required_location_id"] is not None:
        values["required_location_id"] = product["required_location_id"]
    sources = [source_record(
        "legacy-scenario-product", "scenario", "frozen legacy scenario product declaration",
        ["/values/absolute"], artifact="scripts/catalog-snapshot-legacy-scenario.json",
        note="Only use attributes are retained; storefront facts are declared separately")]
    return raw_object(product["product_id"], product["name"], "durable", values, sources)


def expansion_food_object(entry: dict, context: dict,
                          bounds: dict[str, tuple[float, float]]) -> dict:
    """@brief 由扩容白名单直接构造原生食品增量。"""
    barcode = entry["barcode"]
    category_id = FOOD_CATEGORY_OVERRIDES.get(barcode, CATEGORY_ID_BY_LABEL[context["label"]])
    density = context["density"]
    validate_density(f"food-{barcode}", category_id, density, bounds)
    values = {
        "energy_kcal_per_serving": entry["energy_kcal_serving"],
        "energy_density_kcal_per_100": density,
        "serving_unit": entry["serving_size"],
    }
    energy = source_record(
        "off-energy", "fact", f"openfoodfacts:product/{barcode}",
        ["/values/absolute/energy_kcal_per_serving",
         "/values/absolute/energy_density_kcal_per_100"],
        artifact=f"{context['artifact']} (sha256 {context['sha256']})",
        note=f"derived via {entry['kcal_rule']} x serving {entry['serving_quantity']}")
    return raw_object(f"food-{barcode}", entry["name"], category_id, values, [energy])


def load_expansion_entries() -> list[dict]:
    """@brief 读取并相互校验扩容白名单与品类表。"""
    document = load_json(FOOD_EXPANSION)
    entries = document["entries"]
    by_barcode = {entry["barcode"]: entry for entry in entries}
    assert len(by_barcode) == len(entries) == 25, "expansion whitelist must hold 25 unique entries"
    assert set(by_barcode) == set(FOOD_CATEGORIES_BY_BARCODE), \
        "expansion whitelist and category table disagree"
    return entries




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
        assert entry["category"] in FOOD_CATEGORIES, \
            f"{entry['barcode']}: food category {entry['category']!r} is not declared"
    return entries


def verify_expansion2(entries: list[dict], off_pool: dict, forbidden: set[str],
                      bounds: dict[str, tuple[float, float]]) -> None:
    """@brief 核对第二波白名单与原始池、既有白名单、密度槽位的一致性。"""
    pool_by_code = {str(x.get("code")): x for x in off_pool["products"]}
    assert len(pool_by_code) >= 300, \
        f"raw pool must hold >=300 unique products, got {len(pool_by_code)}"
    kcal_pool = [x for x in off_pool["products"]
                 if (x.get("nutriments", {}).get("energy-kcal_100g") is not None
                     or x.get("nutriments", {}).get("energy-kj_100g") is not None)]
    assert len(kcal_pool) >= 300, \
        f"raw pool must hold >=300 kcal-bearing products, got {len(kcal_pool)}"
    for entry in entries:
        barcode = entry["barcode"]
        assert barcode not in forbidden, f"{barcode}: duplicates an existing food"
        raw = pool_by_code.get(barcode)
        assert raw is not None, f"{barcode}: not in china-pool.json"
        nut = raw.get("nutriments", {})
        if entry["kcal_rule"].startswith("nutriments.energy-kcal_100g"):
            kcal100 = nut.get("energy-kcal_100g")
        else:
            assert "energy-kj_100g" in nut, f"{barcode}: rule/data mismatch"
            kcal100 = nut["energy-kj_100g"] / 4.184
        category_id = CATEGORY_ID_BY_LABEL[entry["category"]]
        validate_density(f"food-{barcode}", category_id, kcal100, bounds)
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


def meal_object(meal: dict, meals_sha: str,
                bounds: dict[str, tuple[float, float]]) -> dict:
    """@brief 由正餐语料直接构造原生对象增量。"""
    source = meal["energy_source"]
    duration_seconds = meal["meal_duration_minutes"] * 60
    density = float(source["energy_kcal_per_100g"])
    validate_density(meal["meal_id"], "prepared-meal", density, bounds)
    values = {
        "energy_kcal_per_serving": meal["energy_kcal_per_serving"],
        "energy_density_kcal_per_100": density,
        "use_duration_seconds": duration_seconds,
        "serving_quantity": meal["serving"]["quantity"],
    }
    energy = source_record(
        "fndds-energy", "fact",
        f"USDA FNDDS fdc:{source['fdc_id']} ({source['food_description']})",
        ["/values/absolute/energy_kcal_per_serving",
         "/values/absolute/energy_density_kcal_per_100"],
        artifact=f"corpora/meals/items.json (sha256 {meals_sha})",
        note=(f"kcal = {density} kcal/100g x {meal['serving']['quantity']} g / 100; "
              "FNDDS recorded values not adjusted"))
    timing = source_record(
        "meal-shape", "scenario", "meal corpus serving and duration assumptions",
        ["/values/absolute/use_duration_seconds", "/values/absolute/serving_quantity"],
        artifact="corpora/meals/items.json")
    return raw_object(meal["meal_id"], meal["name_zh"], "prepared-meal", values, [energy, timing])


def travel_object() -> dict:
    """@brief 出行对象只声明身份；费率完整位于闭集规则。"""
    provenance = [source_record(
        "legacy-travel-service", "scenario", "frozen travel service identity",
        ["/category_id"], artifact="scripts/catalog-snapshot-legacy-scenario.json")]
    return raw_object("travel-service", "Travel service", "travel-service", {}, provenance)


def contract_objects() -> list[dict]:
    """@brief 三种合同对象只声明归属；共享效率与验收值由品类继承。"""
    objects = []
    for kind in ("hourly", "piece", "package"):
        provenance = [source_record(
            "legacy-contract-kind", "scenario", f"frozen {kind} contract identity",
            ["/category_id"], artifact="scripts/catalog-snapshot-legacy-scenario.json")]
        objects.append(raw_object(
            f"contract-{kind}", f"{kind.capitalize()} work contract",
            f"contract-{kind}", {}, provenance))
    return objects


# ---------------------------------------------------------------------------
# 冻结输入校验
# ---------------------------------------------------------------------------

def verify_snapshot_inputs(snapshot: dict, off_frozen: list, off_raw: dict) -> dict:
    """@brief 核对冻结场景快照、OFF 白名单与原始记录。"""
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
    return {"foods": foods, "items": items}


def verify_corpus_hashes(manifest: dict) -> str:
    """@brief 核对生成器输入语料与语料清单。"""
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
    return sha256_of(OFF_FROZEN)


def verify_handwritten_catalog_files() -> None:
    """@brief 确认手工维护的闭集规则与契约输入完整。"""
    assert (RULES_DIR / "off-foods.mapping.toml").is_file(), "missing catalog/rules/off-foods.mapping.toml"
    assert not (RULES_DIR / "travel-fare.toml").exists(), "legacy travel-fare formula must be deleted"
    assert (RULES_DIR / "travel-time.toml").is_file(), "missing catalog/rules/travel-time.toml"
    assert (RULES_DIR / "motivation.toml").is_file(), "missing catalog/rules/motivation.toml"
    assert (RULES_DIR / "persona-mapping.toml").is_file(), "missing catalog/rules/persona-mapping.toml"
    assert AFFORDANCES.is_file(), "missing catalog/affordances.toml"
    assert CATEGORY_CONTRACT.is_file(), "missing catalog/category-contract.json"
    assert EXCHANGE_RULES.is_file(), "missing catalog/exchange-rules.json"
    traffic_dir = ROOT / "catalog/snapshots/traffic"
    assert (traffic_dir / "amap-report-excerpt.md").is_file(), \
        "missing catalog/snapshots/traffic/amap-report-excerpt.md"
    assert (traffic_dir / "weekday-hour-factors.json").is_file(), \
        "missing catalog/snapshots/traffic/weekday-hour-factors.json"


def verify_body_and_lifestyle(snapshot: dict) -> None:
    """@brief 核对身体参数和生活活动目录的冻结输入。"""
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


def verify_inputs(snapshot: dict, off_frozen: list, off_raw: dict,
                  manifest: dict) -> tuple[dict, str]:
    """@brief 聚合所有冻结输入准入检查。"""
    parts = verify_snapshot_inputs(snapshot, off_frozen, off_raw)
    off_sha = verify_corpus_hashes(manifest)
    verify_handwritten_catalog_files()
    verify_body_and_lifestyle(snapshot)
    return parts, off_sha


# ---------------------------------------------------------------------------
# 生成入口
# ---------------------------------------------------------------------------

def lifestyle_met_map() -> dict[str, float]:
    """@brief 生活活动目录 → lifestyle:<id> MET 键（键排序保证输出字节稳定）。"""
    items = load_json(LIFESTYLE_ITEMS)["items"]
    merged = {f"lifestyle:{item['activity_id']}": float(item["met_value"]) for item in items}
    assert len(merged) == len(items), "lifestyle activity_id collision in MET map"
    return dict(sorted(merged.items()))


def expansion_context(entry: dict, raw: dict, checksum: str, wave: int) -> dict:
    """@brief 汇集一条扩容食品的品类与原始密度上下文。"""
    barcode = entry["barcode"]
    label = FOOD_CATEGORIES_BY_BARCODE[barcode] if wave == 1 else entry["category"]
    return {"label": label, "artifact": f"corpora/foods/expansion{'2' if wave == 2 else ''}.json",
            "sha256": checksum, "density": energy_density(raw)}


def build_objects(data: dict) -> list[dict]:
    """@brief 从冻结语料直接生成 163 条原生对象增量。"""
    frozen = [
        frozen_food_object(item, data["raw100"][item["product_id"].removeprefix("food-")],
                           data["off_sha"], data["bounds"])
        for item in data["parts"]["foods"]
    ]
    wave1 = [
        expansion_food_object(item,
                              expansion_context(item, data["raw100"][item["barcode"]],
                                                data["sha1"], 1), data["bounds"])
        for item in sorted(data["entries1"], key=lambda row: row["barcode"])
    ]
    wave2 = [
        expansion_food_object(item,
                              expansion_context(item, data["raw2"][item["barcode"]],
                                                data["sha2"], 2), data["bounds"])
        for item in sorted(data["entries2"], key=lambda row: row["barcode"])
    ]
    meals = [meal_object(item, data["meals_sha"], data["bounds"])
             for item in sorted(data["meals"], key=lambda row: row["meal_id"])]
    durables = [durable_object(item) for item in data["parts"]["items"]]
    return frozen + wave1 + wave2 + meals + durables + [travel_object()] + contract_objects()


def manifest_base() -> dict:
    """@brief 构造不含内容摘要的 4.0 清单声明。"""
    return {
        "version_major": 4,
        "version_minor": 0,
        "category_requirements": CATEGORY_REQUIREMENTS,
    }


def staged_manifest(outputs: dict[str, str]) -> str:
    """@brief 通过运行时唯一身份 helper 生成目录清单。"""
    with tempfile.TemporaryDirectory(prefix="worldmock-catalog-build-") as temporary:
        staged = Path(temporary) / "catalog"
        staged.mkdir()
        shutil.copy2(CATEGORY_CONTRACT, staged / CATEGORY_CONTRACT.name)
        shutil.copy2(EXCHANGE_RULES, staged / EXCHANGE_RULES.name)
        shutil.copy2(AFFORDANCES, staged / AFFORDANCES.name)
        shutil.copytree(RULES_DIR, staged / "rules")
        for relative, content in outputs.items():
            target = staged / relative.removeprefix("catalog/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        manifest = staged / "manifest.json"
        manifest.write_text(
            json.dumps(manifest_base(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        refresh_manifest_identity(staged)
        return manifest.read_text(encoding="utf-8")


def build_manifest(outputs: dict[str, str]) -> str:
    """@brief 生成与运行时身份算法一致的清单文本。"""
    return staged_manifest(outputs)


def load_build_inputs() -> dict:
    """@brief 一次读取生成器的全部冻结输入。"""
    return {
        "snapshot": load_json(SNAPSHOT),
        "off_frozen": load_json(OFF_FROZEN),
        "off_raw": load_json(OFF_RAW),
        "off_pool": load_json(OFF_POOL),
        "corpus_manifest": load_json(CORPUS_MANIFEST),
        "contract": load_json(CATEGORY_CONTRACT),
    }


def prepare_object_data(inputs: dict, parts: dict, off_sha: str,
                        bounds: dict[str, tuple[float, float]]) -> dict:
    """@brief 校验扩容语料并组装原生对象生成上下文。"""
    expansion_entries = load_expansion_entries()
    verify_expansion(expansion_entries, inputs["off_frozen"], inputs["off_raw"], parts["foods"])
    expansion_sha = sha256_of(FOOD_EXPANSION)

    expansion2_entries = load_expansion2_entries()
    forbidden = {entry["barcode"] for entry in inputs["off_frozen"] + expansion_entries}
    verify_expansion2(expansion2_entries, inputs["off_pool"], forbidden, bounds)
    expansion2_sha = sha256_of(FOOD_EXPANSION2)

    meals = load_meals()
    meals_sha = sha256_of(MEALS_CORPUS)
    return {
        "parts": parts, "off_sha": off_sha, "bounds": bounds,
        "entries1": expansion_entries, "entries2": expansion2_entries,
        "sha1": expansion_sha, "sha2": expansion2_sha,
        "raw100": {str(row["code"]): row for row in inputs["off_raw"]["products"]},
        "raw2": {str(row["code"]): row for row in inputs["off_pool"]["products"]},
        "meals": meals, "meals_sha": meals_sha,
    }


def build() -> dict[str, str]:
    """@brief 生成资源、原生对象、墓碑与内容身份清单。"""
    inputs = load_build_inputs()
    bounds = category_density_bounds(inputs["contract"])
    parts, off_sha = verify_inputs(
        inputs["snapshot"], inputs["off_frozen"], inputs["off_raw"], inputs["corpus_manifest"])

    # 基础活动 MET 来自冻结快照；生活活动 MET 来自语料目录（引擎按
    # lifestyle:<id> 查表，语义层校验基础键封闭 + lifestyle 键开放）。
    body = dict(inputs["snapshot"]["body"])
    body["activity_met"] = {**body["activity_met"], **lifestyle_met_map()}
    resources = build_resources(body)
    object_data = prepare_object_data(inputs, parts, off_sha, bounds)
    objects = build_objects(object_data)

    assert len(resources) == 5, f"expected 5 resources, got {len(resources)}"
    expected_objects = 45 + len(object_data["entries2"]) + len(object_data["meals"])
    assert len(objects) == expected_objects, (
        f"expected {expected_objects} objects (16+25+{len(object_data['entries2'])}"
        f"+{len(object_data['meals'])} meals+1+3), got {len(objects)}")
    for entry in resources:
        assert_full_coverage(entry["resource_id"], entry)
    for entry in objects:
        assert_raw_object_coverage(entry)

    outputs = {
        "catalog/resources.jsonl": "".join(
            json.dumps(e, ensure_ascii=False) + "\n" for e in resources),
        "catalog/objects.jsonl": "".join(
            json.dumps(e, ensure_ascii=False) + "\n" for e in objects),
        "catalog/overrides/tombstones.jsonl": "",
    }
    outputs["catalog/manifest.json"] = build_manifest(outputs)
    return outputs


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
