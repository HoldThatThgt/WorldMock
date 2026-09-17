"""品类语料数据级测试：正餐/地点/内容元数据/工作材料/联系人/生活活动 v2。

与 tests/catalog/test_category_admission.py 的准入反例互补：这里断言冻结
语料本身的事实（来源字段、数量、坐标真实性、哈希登记、字段完整性）。
"""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPORA = ROOT / "corpora"


def _load(relative: str):
    return json.loads((CORPORA / relative).read_text(encoding="utf-8"))


def _manifest_index() -> dict:
    manifest = json.loads((CORPORA / "manifest.json").read_text(encoding="utf-8"))
    return {entry["path"]: entry for entry in manifest["entries"]}


# ---------------------------------------------------------------------------
# 正餐
# ---------------------------------------------------------------------------

def test_meals_corpus_holds_thirty_sourced_entries():
    document = _load("meals/items.json")
    meals = document["items"]
    assert len(meals) == 30
    ids = [meal["meal_id"] for meal in meals]
    assert len(set(ids)) == 30
    for meal in meals:
        source = meal["energy_source"]
        assert "FNDDS" in source["database"]
        assert source["fdc_id"] > 0 and source["food_description"]
        assert source["energy_kcal_per_100g"] > 0
        assert source["source_url"].startswith("https://api.nal.usda.gov/fdc/v1/food/")
        assert source["retrieved_at"] == "2026-09-18"
        assert "ml" not in meal["serving"]["unit"]
        derived = round(source["energy_kcal_per_100g"] * meal["serving"]["quantity"] / 100.0)
        assert derived == meal["energy_kcal_per_serving"]
        assert 1500 <= meal["price_fen"] <= 4500
        assert "scenario assumption" in meal["price_assumption"]
        assert 20 <= meal["meal_duration_minutes"] <= 40
        assert meal["dine_in"] or meal["delivery"]
        if meal["delivery"]:
            assert meal["delivery_delay_seconds"] >= 60


def test_meals_classes_cover_hot_dish_restaurant_and_takeout():
    classes = {meal["meal_class"] for meal in _load("meals/items.json")["items"]}
    assert {"hot_meal", "restaurant_dish", "takeout_combo"} <= classes


def test_meals_registered_in_corpus_manifest_with_fndds_license():
    entry = _manifest_index()["meals/items.json"]
    digest = hashlib.sha256((CORPORA / "meals/items.json").read_bytes()).hexdigest()
    assert entry["sha256"] == digest
    assert entry["source"]["license"].startswith("USDA FNDDS")
    assert "public domain" in entry["source"]["license"]


def test_meals_energy_matches_catalog_objects():
    objects = {}
    for line in (ROOT / "catalog/objects.jsonl").read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["object_id"].startswith("meal-"):
            objects[entry["object_id"]] = entry
    meals = _load("meals/items.json")["items"]
    assert len(objects) == 30
    for meal in meals:
        obj = objects[meal["meal_id"]]
        consume = next(edge for edge in obj["edges"] if edge["on"] == "consume")
        assert consume["amount"] == meal["energy_kcal_per_serving"]
        assert consume["duration_s"] == meal["meal_duration_minutes"] * 60
        purchase = next(edge for edge in obj["edges"] if edge["on"] == "purchase")
        assert purchase["amount_fen"] == meal["price_fen"]
        fact_part = next(p for p in obj["provenance"] if p["part"] == "consume energy edge")
        assert fact_part["type"] == "fact"
        assert str(meal["energy_source"]["fdc_id"]) in fact_part["source"]


# ---------------------------------------------------------------------------
# 地点（真实 OSM POI）
# ---------------------------------------------------------------------------

def _probe_node_positions() -> dict:
    text = (CORPORA / "zhangjiang-map/zhangjiang-probe.osm").read_text(encoding="utf-8")
    positions = {}
    for match in re.finditer(r'<node id="(\d+)"[^>]*?lat="([-\d.]+)" lon="([-\d.]+)"', text):
        positions[match.group(1)] = (float(match.group(2)), float(match.group(3)))
    return positions


def test_locations_cover_all_required_kinds_with_real_osm_coordinates():
    document = _load("zhangjiang-map/locations.json")
    locations = document["locations"]
    covers = {}
    for location in locations:
        for kind in location["covers"]:
            covers.setdefault(kind, []).append(location)
        assert location["opening_hours"]["opens"] and location["opening_hours"]["closes"]
        assert location["opening_hours"]["basis"]
        assert location["affordances"]
    for kind in document["required_kinds"]:
        floor = document["min_per_kind"].get(kind, 1)
        assert len(covers.get(kind, [])) >= floor, kind
    positions = _probe_node_positions()
    for location in locations:
        origin = location["coordinate_source"]
        if origin["type"] == "osm-probe-node":
            lat, lon = positions[str(origin["osm"]["id"])]
            assert (location["latitude"], location["longitude"]) == (lat, lon), location["location_id"]


def test_location_affordances_declare_meal_and_grocery_links():
    document = _load("zhangjiang-map/locations.json")
    by_kind = {}
    for location in document["locations"]:
        by_kind.setdefault(location["kind"], []).append(location)
    assert len(by_kind["restaurant"]) >= 2
    supermarket = by_kind["supermarket"][0]
    assert supermarket["affordances"]["replenishes"].startswith("home pantry")
    office = by_kind["office"][0]
    water = office["affordances"]["pantry_water"]
    assert water["cost_fen"] == 0 and water["duration_minutes"] == 5
    assert water["entertainment_relief_per_hour"] > 0
    assert office["affordances"]["meeting_room"] and office["affordances"]["colleagues_present"]


def test_home_pantry_seven_breakfast_servings_reference_catalog_foods():
    pantry = _load("scene-stock/home-pantry.json")
    assert sum(entry["quantity"] for entry in pantry["items"]) == 7
    assert all(entry["role"] == "breakfast" for entry in pantry["items"])
    catalog_ids = set()
    for line in (ROOT / "catalog/objects.jsonl").read_text(encoding="utf-8").splitlines():
        catalog_ids.add(json.loads(line)["object_id"])
    for entry in pantry["items"]:
        assert entry["object_id"] in catalog_ids
        assert entry["object_id"].startswith("food-")
    assert pantry["replenish_via"]["kind"] == "supermarket"


# ---------------------------------------------------------------------------
# 内容元数据与 drama 整体替换
# ---------------------------------------------------------------------------

CONTENT_FILES = [
    ("movies/items.json", "list"),
    ("drama/items.json", "list"),
    ("novels/items.json", "list"),
    ("feed/items.json", "list"),
    ("jokes/items.json", "list"),
    ("sintel/media-identity.json", "single"),
]


def test_every_viewing_entry_carries_four_metadata_fields():
    for relative, mode in CONTENT_FILES:
        document = _load(relative)
        items = [document] if mode == "single" else document
        assert items, relative
        for item in items:
            assert isinstance(item["title"], str) and item["title"].strip(), (relative, item)
            assert isinstance(item["genre"], list) and item["genre"], (relative, item)
            assert isinstance(item["language"], str) and item["language"], (relative, item)
            assert isinstance(item["age_rating"], int) and item["age_rating"] >= 0
            assert item["age_rating"] < 18, (relative, item.get("item_id"))


def test_drama_corpus_replaced_with_chinese_short_drama():
    dramas = _load("drama/items.json")
    assert len(dramas) == 10
    raw = (CORPORA / "drama/items.json").read_text(encoding="utf-8")
    assert "visual-novels" not in raw, "旧英文视觉小说来源不得残留"
    assert "alpindale" not in raw
    for drama in dramas:
        assert drama["language"] == "zh"
        assert drama["source_work"].startswith("benozz/Chinese_drama_audio")
        assert drama["age_rating"] in (12, 16)
        assert drama["lines_selected"] == drama["lines_available_in_source"]
        assert drama["duration_seconds"] > 0
        timeline = CORPORA / drama["timeline_path"]
        assert timeline.is_file()
        index = _manifest_index()[drama["timeline_path"]]
        assert index["sha256"] == hashlib.sha256(timeline.read_bytes()).hexdigest()
        assert index["source"]["license"] == "Apache-2.0"


def test_drama_timelines_are_monotonic_srt_cues():
    for drama in _load("drama/items.json"):
        cues = _parse_srt(CORPORA / drama["timeline_path"])
        assert len(cues) == drama["lines_selected"], drama["item_id"]
        assert cues[-1][1] == drama["duration_seconds"], drama["item_id"]
        assert all(left[1] <= right[0] for left, right in zip(cues, cues[1:]))
        assert all(start < end for start, end in cues)


def _parse_srt(path: Path) -> list[tuple[int, int]]:
    pattern = re.compile(
        r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)")
    cues = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.search(line)
        if not match:
            continue
        g = [int(part) for part in match.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append((int(round(start)), int(round(end))))
    return cues


def test_sintel_identity_declares_genre_language_age_rating():
    identity = _load("sintel/media-identity.json")
    assert identity["title"] == "Sintel"
    assert identity["genre"] and identity["language"] == "en"
    assert 0 <= identity["age_rating"] < 18
    assert identity["age_rating_basis"]


# ---------------------------------------------------------------------------
# 工作材料与联系人
# ---------------------------------------------------------------------------

def test_work_materials_twenty_distinct_chinese_scenario_documents():
    document = _load("scenario-work/materials.json")
    materials = document["materials"]
    assert document["type"] == "scenario"
    assert len(materials) == 20
    assert len({m["material_id"] for m in materials}) == 20
    assert len({m["body"] for m in materials}) == 20
    for material in materials:
        assert material["language"] == "zh"
        assert material["type"] == "scenario"
        assert len(re.findall(r"[\u4e00-\u9fff]", material["body"])) > 80
        assert material["title"]
    personas = {m["target_persona"] for m in materials}
    assert personas == {"agent:1", "agent:2"}
    per_persona = {}
    for material in materials:
        per_persona[material["target_persona"]] = \
            per_persona.get(material["target_persona"], 0) + 1
    assert min(per_persona.values()) >= 8


def test_ami_es2002_metadata_retained_in_corpus_manifest():
    paths = {entry["path"] for entry in _manifest_index().values()}
    ami = sorted(p for p in paths if p.startswith("ami-es2002/"))
    assert len(ami) >= 4, "AMI ES2002a–d 语料元数据必须保留"
    assert any("ES2002" in p for p in ami)


def test_contacts_two_per_persona_with_valid_reply_policies():
    document = _load("scenario-contacts/contacts.json")
    personas = {p["persona_id"] for p in document["personas"]}
    assert personas == {"agent:1", "agent:2"}
    per_persona = {}
    for contact in document["contacts"]:
        per_persona.setdefault(contact["persona_id"], []).append(contact)
    for persona, contacts in per_persona.items():
        assert len(contacts) >= 2
    for contact in document["contacts"]:
        if contact["contact_type"] == "colleague":
            assert contact["reply_policy"] == "persona"
            assert contact["mapped_persona_id"] in personas
        else:
            assert contact["contact_type"] in ("old_friend", "family")
            assert contact["reply_policy"] == "model"
            assert contact["mapped_persona_id"] is None


# ---------------------------------------------------------------------------
# 生活活动 v2 Schema
# ---------------------------------------------------------------------------

def test_lifestyle_items_carry_v2_fields_with_valid_domains():
    document = _load("lifestyle/items.json")
    assert document["schema_version"] == 2
    items = document["items"]
    assert len(items) == 183
    closed = set(json.loads(
        (CORPORA / "lifestyle/enrichment.json").read_text(encoding="utf-8")
    )["location_kind_closed_set"])
    for item in items:
        assert item["location_kinds"] and set(item["location_kinds"]) <= closed
        window = item["time_window"]
        assert 0 <= window["start_hour"] < window["end_hour"] <= 24
        assert isinstance(item["needs_partner"], bool)
        assert set(item["effects"]) == {"entertainment", "social", "health"}
        assert all(0.0 <= v <= 1.0 for v in item["effects"].values())


def test_lifestyle_enrichment_covers_every_activity_exactly():
    items = _load("lifestyle/items.json")["items"]
    enrichment = json.loads(
        (CORPORA / "lifestyle/enrichment.json").read_text(encoding="utf-8"))["entries"]
    ids = {item["activity_id"] for item in items}
    assert ids == set(enrichment)
    for item in items:
        record = enrichment[item["activity_id"]]
        assert item["location_kinds"] == record["location_kinds"]
        assert item["time_window"] == record["time_window"]
        assert item["needs_partner"] == record["needs_partner"]
        assert item["effects"] == record["effects"]


def test_morning_wash_activity_declared_at_fifteen_minutes():
    wash = next(item for item in _load("lifestyle/items.json")["items"]
                if item["activity_id"] == "wellness-morning-wash")
    assert wash["typical_duration_minutes"] == 15
    assert "home" in wash["location_kinds"]
    assert wash["time_window"]["start_hour"] <= 6
