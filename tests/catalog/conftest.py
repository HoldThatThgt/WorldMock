"""@brief 新环境目录纯测试的真实文件构造与重新发布工具。"""

import json
from pathlib import Path

from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.footprints.models import canonical_json


def read_payload(directory: Path, name: str) -> dict:
    """@brief 读取测试目录中的 JSON 对象。"""
    return json.loads((directory / name).read_bytes())


def write_payload(directory: Path, name: str, payload: dict, *, refresh: bool = True) -> None:
    """@brief 写入反例文件并按生产发布入口更新身份。"""
    (directory / name).write_bytes(canonical_json(payload) + b"\n")
    if refresh:
        refresh_manifest_identity(directory)


def edit_row(directory: Path, filename: str, identity: str, updates: dict) -> None:
    """@brief 在真实文件中按身份改写一个对象再重新发布。"""
    payload = read_payload(directory, filename)
    rows = next(iter(payload.values()))
    row = next(row for row in rows if identity in row.values())
    row.update(updates)
    write_payload(directory, filename, payload)


OBJECT_COUNTEREXAMPLES = [
    ("foods.json", "chips", {"category": "dessert"}, "ENVIRONMENT_KIND_INVALID"),
    ("foods.json", "chips", {"price_fen": True}, "ENVIRONMENT_NUMBER_INVALID"),
    ("foods.json", "chips", {"price_fen": 0}, "ENVIRONMENT_NUMBER_INVALID"),
    ("foods.json", "chips", {"store_ids": []}, "FOOD_BINDING_MISSING"),
    ("foods.json", "chips", {"store_ids": ["home"]}, "FOOD_STORE_NOT_FOOD_STORE"),
    ("foods.json", "chips", {"store_ids": ["unknown"]}, "ENVIRONMENT_REF_UNKNOWN"),
    ("foods.json", "beef-noodles", {"store_ids": ["noodle-1"]}, "FOOD_BINDING_KIND_INCONSISTENT"),
    ("locations.json", "home", {"app_ids": ["meituan"]}, "ENVIRONMENT_KIND_INVALID"),
    ("locations.json", "cafe", {"app_ids": ["unknown"]}, "ENVIRONMENT_REF_UNKNOWN"),
    ("contents.json", "c-short", {"app_ids": []}, "CONTENT_APP_MISSING"),
    ("contents.json", "c-short", {"summary": "字" * 201}, "CONTENT_SUMMARY_TOO_LONG"),
    ("contents.json", "c-short", {"summary_source": {"kind": "real", "url": None, "notice": None}},
     "CONTENT_SOURCE_URL_MISSING"),
    ("contents.json", "c-short", {"summary_source": {"kind": "real", "url": "", "notice": None}},
     "CONTENT_SOURCE_URL_MISSING"),
    ("contents.json", "c-short", {"duration_seconds": -1}, "ENVIRONMENT_NUMBER_INVALID"),
    ("contents.json", "c-short", {"series": {"title": "测试系列", "episode": 0}}, "ENVIRONMENT_NUMBER_INVALID"),
    ("apps.json", "meituan", {"name": " 家 \n"}, "APP_NAME_COLLIDES_LOCATION"),
    ("apps.json", "meituan", {"name": " 饿了么 "}, "ENVIRONMENT_ID_DUPLICATE"),
    ("work_topics.json", "topic-sales", {"target_hours": 0.001}, "ENVIRONMENT_NUMBER_INVALID"),
    ("work_topics.json", "topic-sales", {"target_hours": True}, "ENVIRONMENT_NUMBER_INVALID"),
]
