"""世界素材品类准入测试：每条品类要求一个正例/反例，外加任务受理 Schema。

反例用最小语料树（复制真实语料的被检子集再定向破坏）驱动，
断言对应稳定错误码出现在违例列表中（fail-closed，不要求唯一违例）。
"""

import json
import shutil
from pathlib import Path

import pytest

from worldmock.catalog.admission import (
    CATEGORY_CONTACTS_BELOW_MIN,
    CATEGORY_CONTACTS_MAPPING_INVALID,
    CATEGORY_CONTENT_AGE_RATING_REJECTED,
    CATEGORY_CONTENT_METADATA_MISSING,
    CATEGORY_DRAMA_NOT_CHINESE,
    CATEGORY_HOME_AFFORDANCE_MISSING,
    CATEGORY_LOCATION_KIND_MISSING,
    CATEGORY_LIFESTYLE_FIELD_MISSING,
    CATEGORY_MEALS_BELOW_MIN,
    CATEGORY_MEALS_CATALOG_OBJECT_MISSING,
    CATEGORY_MEALS_FIELD_INVALID,
    CATEGORY_MEALS_MISSING,
    CATEGORY_MEALS_SOURCE_MISSING,
    CATEGORY_OFFICE_AFFORDANCE_MISSING,
    CATEGORY_REQUIREMENTS_UNDECLARED,
    CATEGORY_WORK_AMI_MISSING,
    CATEGORY_WORK_MATERIALS_BELOW_MIN,
    CATEGORY_WORK_MATERIALS_DUPLICATE,
    validate_task_assignees,
    validate_world_categories,
)
from worldmock.catalog.loader import load_catalog
from worldmock.categories import refresh_manifest_identity

ROOT = Path(__file__).resolve().parents[2]
CORPORA = ROOT / "corpora"
#: 品类准入读取的语料文件闭集（复制进临时树的最小集合）。
CHECKED_FILES = [
    "meals/items.json",
    "zhangjiang-map/locations.json",
    "scene-stock/home-pantry.json",
    "scenario-contacts/contacts.json",
    "scenario-work/materials.json",
    "lifestyle/items.json",
    "movies/items.json",
    "drama/items.json",
    "novels/items.json",
    "feed/items.json",
    "jokes/items.json",
    "sintel/media-identity.json",
    "manifest.json",
]


def _copy_corpora(tmp_path: Path) -> Path:
    """@brief 复制被检语料子集到临时树，返回临时语料根。"""
    root = tmp_path / "corpora"
    for relative in CHECKED_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CORPORA / relative, target)
    return root


def _mutate_json(root: Path, relative: str, mutate) -> None:
    """@brief 读取-修改-写回一个语料 JSON 文件。"""
    path = root / relative
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def real_catalog():
    """@brief 真实冻结目录视图。"""
    return load_catalog(ROOT / "catalog")


def _codes(result) -> set[str]:
    return {violation.code for violation in result.violations}


def test_real_catalog_passes_all_category_requirements(real_catalog):
    """真实目录 + 真实语料：品类准入全绿（load_catalog 真目录 + 准入全绿）。"""
    result = validate_world_categories(real_catalog, CORPORA)
    assert result.ok, [f"{v.code}: {v.detail}" for v in result.violations]


def test_requirements_undeclared_rejects_startup(catalog_dir, tmp_path):
    """manifest 未声明品类要求（v1 形态）时拒绝启动。"""
    legacy_catalog = load_catalog(catalog_dir)
    result = validate_world_categories(legacy_catalog, tmp_path)
    assert not result.ok
    assert CATEGORY_REQUIREMENTS_UNDECLARED in _codes(result)


def test_missing_meals_corpus(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "meals/items.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_MEALS_MISSING in _codes(result)


def test_meals_below_contract_minimum(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    _mutate_json(root, "meals/items.json", lambda doc: doc.update(items=doc["items"][:3]))
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_MEALS_BELOW_MIN in _codes(result)


def test_meals_missing_energy_source_annotation(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def drop_source(doc):
        doc["items"][0]["energy_source"] = {}

    _mutate_json(root, "meals/items.json", drop_source)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_MEALS_SOURCE_MISSING in _codes(result)


def test_meals_price_outside_window(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def bad_price(doc):
        doc["items"][0]["price_fen"] = 1200

    _mutate_json(root, "meals/items.json", bad_price)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_MEALS_FIELD_INVALID in _codes(result)


def test_meals_duration_outside_window(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def bad_duration(doc):
        doc["items"][1]["meal_duration_minutes"] = 15

    _mutate_json(root, "meals/items.json", bad_duration)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_MEALS_FIELD_INVALID in _codes(result)


def test_meals_without_dine_in_or_delivery(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def nowhere(doc):
        doc["items"][2]["dine_in"] = False
        doc["items"][2]["delivery"] = False

    _mutate_json(root, "meals/items.json", nowhere)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_MEALS_FIELD_INVALID in _codes(result)


def test_meals_catalog_object_missing(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    objects = []
    for line in (ROOT / "catalog/objects.jsonl").read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["object_id"] != "meal-0005":
            objects.append(line)
    assert len(objects) == 162
    catalog_dir = tmp_path / "catalog"
    shutil.copytree(ROOT / "catalog", catalog_dir)
    (catalog_dir / "objects.jsonl").write_text("\n".join(objects) + "\n", encoding="utf-8")
    refresh_manifest_identity(catalog_dir)
    stripped = load_catalog(catalog_dir)
    result = validate_world_categories(stripped, root)
    assert CATEGORY_MEALS_CATALOG_OBJECT_MISSING in _codes(result)


def test_location_required_kind_missing(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def drop_convenience(doc):
        doc["locations"] = [loc for loc in doc["locations"]
                            if loc["business_kind"] != "convenience_store"]

    _mutate_json(root, "zhangjiang-map/locations.json", drop_convenience)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_LOCATION_KIND_MISSING in _codes(result)


def test_location_restaurant_floor_of_two(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def keep_one_restaurant(doc):
        kept = 0
        for loc in doc["locations"]:
            if loc["business_kind"] == "restaurant":
                kept += 1
                if kept >= 2:
                    loc["business_kind"] = "general_retail"

    _mutate_json(root, "zhangjiang-map/locations.json", keep_one_restaurant)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_LOCATION_KIND_MISSING in _codes(result)


def test_home_pantry_below_seven_servings(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def shrink(doc):
        doc["items"] = doc["items"][:4]

    _mutate_json(root, "scene-stock/home-pantry.json", shrink)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_HOME_AFFORDANCE_MISSING in _codes(result)


def test_home_morning_wash_not_fifteen_minutes(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def wrong_duration(doc):
        for item in doc["items"]:
            if item["activity_id"] == "wellness-morning-wash":
                item["typical_duration_minutes"] = 20

    _mutate_json(root, "lifestyle/items.json", wrong_duration)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_HOME_AFFORDANCE_MISSING in _codes(result)


def test_office_affordance_missing(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def strip_office(doc):
        for loc in doc["locations"]:
            if loc["kind"] == "office":
                loc["affordances"].pop("pantry_water")

    _mutate_json(root, "zhangjiang-map/locations.json", strip_office)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_OFFICE_AFFORDANCE_MISSING in _codes(result)


def test_office_pantry_water_not_free(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def charge_water(doc):
        for loc in doc["locations"]:
            if loc["kind"] == "office":
                loc["affordances"]["pantry_water"]["cost_fen"] = 200

    _mutate_json(root, "zhangjiang-map/locations.json", charge_water)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_OFFICE_AFFORDANCE_MISSING in _codes(result)


def test_content_age_rating_eighteen_rejected(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def adult_movie(doc):
        doc[0]["age_rating"] = 18

    _mutate_json(root, "movies/items.json", adult_movie)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_CONTENT_AGE_RATING_REJECTED in _codes(result)


def test_content_metadata_field_missing(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def strip_genre(doc):
        doc[0].pop("genre")

    _mutate_json(root, "novels/items.json", strip_genre)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_CONTENT_METADATA_MISSING in _codes(result)


def test_drama_corpus_must_be_chinese(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def english_drama(doc):
        doc[0]["language"] = "en"

    _mutate_json(root, "drama/items.json", english_drama)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_DRAMA_NOT_CHINESE in _codes(result)


def test_work_materials_below_twenty(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def shrink(doc):
        doc["materials"] = doc["materials"][:12]

    _mutate_json(root, "scenario-work/materials.json", shrink)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_WORK_MATERIALS_BELOW_MIN in _codes(result)


def test_work_materials_duplicate_rejected(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def duplicate(doc):
        doc["materials"][5]["body"] = doc["materials"][4]["body"]

    _mutate_json(root, "scenario-work/materials.json", duplicate)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_WORK_MATERIALS_DUPLICATE in _codes(result)


def test_work_ami_metadata_retained_enforced(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def drop_ami(doc):
        doc["entries"] = [e for e in doc["entries"]
                          if not e["path"].startswith("ami-es2002/")]

    _mutate_json(root, "manifest.json", drop_ami)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_WORK_AMI_MISSING in _codes(result)


def test_lifestyle_v2_field_missing(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def strip_fields(doc):
        doc["items"][7].pop("effects")

    _mutate_json(root, "lifestyle/items.json", strip_fields)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_LIFESTYLE_FIELD_MISSING in _codes(result)


def test_lifestyle_time_window_inverted(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def invert(doc):
        doc["items"][9]["time_window"] = {"start_hour": 22, "end_hour": 6}

    _mutate_json(root, "lifestyle/items.json", invert)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_LIFESTYLE_FIELD_MISSING in _codes(result)


def test_contacts_below_two_per_persona(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def shrink(doc):
        doc["contacts"] = [c for c in doc["contacts"] if c["persona_id"] == "agent:1"][:2]

    _mutate_json(root, "scenario-contacts/contacts.json", shrink)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_CONTACTS_BELOW_MIN in _codes(result)


def test_contacts_colleague_mapping_invalid(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def break_mapping(doc):
        for contact in doc["contacts"]:
            if contact["contact_type"] == "colleague":
                contact["mapped_persona_id"] = "agent:99"
                contact["reply_policy"] = "model"

    _mutate_json(root, "scenario-contacts/contacts.json", break_mapping)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_CONTACTS_MAPPING_INVALID in _codes(result)


def test_task_assignee_agent_id_required():
    """tasks[].assignee_agent_id 必填的 Schema 支持：缺失即拒绝。"""
    missing = validate_task_assignees([
        {"task_id": "task-a", "contract_kind": "package"},
        {"task_id": "task-b", "assignee_agent_id": ""},
    ])
    assert not missing.ok
    assert all(v.code == "TASK_ASSIGNEE_MISSING" for v in missing.violations)
    assert len(missing.violations) == 2
    present = validate_task_assignees([
        {"task_id": "task-a", "assignee_agent_id": "agent:1"},
        {"task_id": "task-b", "assignee_agent_id": "agent:2", "input_path": "x.txt"},
    ])
    assert present.ok
