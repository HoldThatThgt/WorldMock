"""@brief 品类准入使用人工小样本，不读取正式环境或外部来源。"""

from __future__ import annotations

import json
from pathlib import Path

from tests.categories.conftest import raw_object, write_catalog
from worldmock.categories import refresh_manifest_identity

LOCATIONS_PATH = "test-map/locations.json"
CONTENT_PATHS = (
    "movies/items.json", "drama/items.json", "podcasts/items.json", "novels/items.json",
    "feed/items.json", "feed/feed-items.json", "jokes/items.json", "jokes/feed-items.json",
    "games/library/items.json", "social/weibo/feed-items.json", "social/zhihu/feed-items.json",
    "social/movie_reviews/feed-items.json", "sintel/media-identity.json",
)


def category_requirements() -> dict:
    """@brief 人工环境声明有限数量门槛，校验窗口与生产模型保持一致。"""
    return {
        "declared_at": "2026-01-01", "spec": "human-authored admission fixture",
        "meals": {"min_count": 4, "price_fen_min": 2000, "price_fen_max": 5000,
                  "duration_minutes_min": 30, "duration_minutes_max": 60,
                  "energy_databases": ["fixture-database"]},
        "location_required_kinds": ["home", "office", "restaurant", "convenience_store", "wet_market"],
        "location_min_per_kind": {"restaurant": 2},
        "home_pantry": {"breakfast_servings_min": 7, "morning_wash_minutes": 15},
        "office_affordances": ["pantry_water", "meeting_room", "colleagues_present"],
        "content_metadata": {"fields": ["title", "genre", "language", "age_rating"], "max_age_rating": 17},
        "work_materials": {"min_distinct": 3, "language": "zh", "horizon_days": 1},
        "lifestyle_fields": ["location_kinds", "time_window", "needs_partner", "effects"],
        "contacts": {"min_per_persona": 2, "colleague_reply_policy": "persona", "other_reply_policy": "model"},
    }


def write_category_catalog(root: Path) -> Path:
    """@brief 在单对象夹具上增添四份可消费正餐并绑定品类要求。"""
    write_catalog(root)
    contract_path = root / "category-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    food = next(row for row in contract["categories"] if row["category_id"] == "food")
    food["slots"]["consumable"] = {"schema": {"type": "boolean", "default": True}, "required": True}
    food["slots"]["use_modes"] = {"schema": {"type": "array", "default": ["eat"]}, "required": True}
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    rows = [raw_object(), *(raw_object(f"test-meal-{index}") for index in range(4))]
    (root / "objects.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["category_requirements"] = category_requirements()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    refresh_manifest_identity(root)
    return root


def write_category_corpora(root: Path) -> Path:
    """@brief 即时生成正餐、地点、联系人、活动和各内容池的最小可准入样本。"""
    meals = [{"meal_id": f"test-meal-{index}", "price_fen": 3000, "meal_duration_minutes": 40,
              "dine_in": True, "delivery": False,
              "energy_source": {"database": "fixture-database", "source_url": "fixture://meals",
                                "retrieved_at": "2026-01-01", "unit_conversion": "per 100 grams",
                                "energy_kcal_per_100g": 100}} for index in range(4)]
    lifestyle = [{"activity_id": identity, "typical_duration_minutes": 15,
                  "location_kinds": ["home"], "time_window": {"start_hour": 0, "end_hour": 24},
                  "needs_partner": False, "effects": {"entertainment": 0.2, "social": 0.0, "health": 0.1}}
                 for identity in ("wellness-morning-wash", "test-rest", "test-stretch")]
    materials = [{"material_id": f"test-material-{index}", "body": f"人工工作材料 {index}",
                  "language": "zh", "type": "scenario"} for index in range(3)]
    documents = {
        "meals/items.json": {"items": meals}, LOCATIONS_PATH: {"locations": _locations()},
        "scene-stock/home-pantry.json": {"items": [{"object_id": "milk", "quantity": 7}]},
        "scenario-work/materials.json": {"materials": materials},
        "lifestyle/items.json": {"items": lifestyle}, "scenario-contacts/contacts.json": _contacts(),
        "manifest.json": {"entries": [{"path": "ami-es2002/test-metadata.json"}]},
    }
    for relative, document in documents.items():
        _write(root / relative, document)
    for relative in CONTENT_PATHS:
        item = {"item_id": relative, "title": "人工内容样本", "genre": [relative], "language": "zh",
                "age_rating": 0, "location_kinds": ["anywhere"]}
        duration_key = "estimated_read_seconds" if relative.startswith(("novels/", "jokes/")) else "duration_seconds"
        item[duration_key] = 600
        _write(root / relative, item if relative.startswith("sintel/") else [item])
    return root


def _locations() -> list[dict]:
    """@brief 地点覆盖每个被测品类，餐馆显式保留两个。"""
    locations = [{"location_id": identity, "kind": kind, "business_kind": business,
                  "location_kinds": [kind if kind in {"home", "office"} else "venue"], "affordances": {}}
                 for identity, kind, business in (
                     ("home", "home", "home"), ("office", "office", "office"),
                     ("restaurant-a", "restaurant", "restaurant"), ("restaurant-b", "restaurant", "restaurant"),
                     ("shop", "convenience_store", "convenience_store"), ("market", "wet_market", "wet_market"))]
    locations[1]["affordances"] = {
        "pantry_water": {"cost_fen": 0, "duration_minutes": 5, "entertainment_relief_per_hour": 0.1},
        "meeting_room": True, "colleagues_present": True,
    }
    return locations


def _contacts() -> dict:
    """@brief 两个人物各有一个同事和一个家人，映射及策略显式声明。"""
    contacts = []
    for identity, peer in (("agent:1", "agent:2"), ("agent:2", "agent:1")):
        contacts.extend([
            {"persona_id": identity, "contact_type": "colleague", "mapped_persona_id": peer,
             "reply_policy": "persona"},
            {"persona_id": identity, "contact_type": "family", "mapped_persona_id": None, "reply_policy": "model"},
        ])
    return {"personas": [{"persona_id": "agent:1"}, {"persona_id": "agent:2"}], "contacts": contacts}


def _write(path: Path, value: object) -> None:
    """@brief 写入一份人工 JSON 声明。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
