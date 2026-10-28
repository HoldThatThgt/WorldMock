"""@brief 目录纯测试的真实六文件环境复制与重新发布工具。"""

import json
import shutil
from pathlib import Path

import pytest

from worldmock.resource_engine.catalog.identity import refresh_manifest_identity
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.foundation.serialization import canonical_json


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    """@brief 复制真实人工目录，允许纯测试独立制造登记反例。"""
    target = tmp_path / "catalog"
    source = Path(__file__).resolve().parents[1] / "fixtures" / "environment" / "catalog"
    shutil.copytree(source, target)
    return target


@pytest.fixture
def loaded_catalog(catalog_dir: Path):
    """@brief 通过生产加载器登记真实人工目录，不导入运行引擎。"""
    return load_catalog(catalog_dir)


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
    ("goods.json", "chips", {"category": "dessert"}, "ENVIRONMENT_KIND_INVALID"),
    ("goods.json", "chips", {"price_fen": True}, "ENVIRONMENT_NUMBER_INVALID"),
    ("goods.json", "chips", {"price_fen": 0}, "ENVIRONMENT_NUMBER_INVALID"),
    ("goods.json", "chips", {"store_ids": []}, "GOODS_BINDING_MISSING"),
    ("goods.json", "chips", {"store_ids": ["home"]}, "GOODS_STORE_KIND_INVALID"),
    ("goods.json", "chips", {"store_ids": ["unknown"]}, "ENVIRONMENT_REF_UNKNOWN"),
    ("goods.json", "beef-noodles", {"store_ids": ["noodle-1"]}, "GOODS_BINDING_KIND_INCONSISTENT"),
    ("goods.json", "beef-noodles", {"store_ids": ["cafe"]}, "GOODS_STORE_KIND_INVALID"),
    ("goods.json", "tissue", {"store_ids": ["noodle-1", "noodle-2"]}, "GOODS_STORE_KIND_INVALID"),
    ("locations.json", "home", {"app_ids": ["meituan"]}, "ENVIRONMENT_KIND_INVALID"),
    ("locations.json", "cafe", {"app_ids": ["unknown"]}, "ENVIRONMENT_REF_UNKNOWN"),
    ("contents.json", "c-short", {"action_id": "unknown"}, "CONTENT_ACTION_MISSING"),
    ("contents.json", "c-short", {"app_ids": []}, "CONTENT_APP_BINDING_INVALID"),
    ("contents.json", "c-short", {"app_ids": ["keep"]}, "CONTENT_APP_BINDING_INVALID"),
    ("contents.json", "c-rest", {"app_ids": ["bilibili"]}, "CONTENT_APP_BINDING_INVALID"),
    ("contents.json", "c-short", {"kind": None}, "ENVIRONMENT_KIND_INVALID"),
    ("contents.json", "c-run", {"kind": "article"}, "ENVIRONMENT_KIND_INVALID"),
    ("contents.json", "c-short", {"summary": "字" * 201}, "CONTENT_SUMMARY_TOO_LONG"),
    ("contents.json", "c-short", {"summary_source": {"kind": "real", "url": None, "notice": None}},
     "CONTENT_SOURCE_URL_MISSING"),
    ("contents.json", "c-short", {"summary_source": {"kind": "real", "url": "", "notice": None}},
     "CONTENT_SOURCE_URL_MISSING"),
    ("contents.json", "c-short", {"duration_seconds": 59}, "ENVIRONMENT_NUMBER_INVALID"),
    ("contents.json", "c-short", {"series": {"title": "测试系列", "episode": 0}}, "ENVIRONMENT_NUMBER_INVALID"),
    ("apps.json", "meituan", {"name": " 家 \n"}, "APP_NAME_COLLIDES_LOCATION"),
    ("apps.json", "meituan", {"name": " 饿了么 "}, "ENVIRONMENT_ID_DUPLICATE"),
    ("apps.json", "eleme", {"payment_app_ids": []}, "APP_PURPOSE_INVALID"),
    ("apps.json", "bilibili", {"payment_app_ids": ["alipay"]}, "APP_PURPOSE_INVALID"),
    ("apps.json", "meituan", {"payment_app_ids": ["douyin"]}, "APP_PURPOSE_INVALID"),
    ("apps.json", "eleme", {"purposes": []}, "APP_PURPOSE_INVALID"),
    ("apps.json", "amap", {"purposes": ["navigation", "shopping"], "payment_app_ids": ["alipay"]},
     "APP_PURPOSE_INVALID"),
    ("actions.json", "run", {"manner": None}, "ACTION_APP_MANNER_INVALID"),
    ("actions.json", "quiet-rest", {"manner": "phone_continuous"}, "ACTION_APP_MANNER_INVALID"),
    ("actions.json", "run", {"name": " 抖 音 "}, "ACTION_NAME_COLLIDES"),
    ("actions.json", "run", {"name": "薯片"}, "ACTION_NAME_COLLIDES"),
    ("actions.json", "run", {"name": "家"}, "ACTION_NAME_COLLIDES"),
    ("actions.json", "run", {"name": "步行"}, "ACTION_NAME_COLLIDES"),
    ("actions.json", "run", {"name": "办公"}, "ACTION_NAME_COLLIDES"),
    ("contents.json", "c-drama-2", {"action_id": "study-course"}, "CONTENT_SERIES_INVALID"),
    ("contents.json", "c-drama-2", {"app_ids": ["douyin"]}, "CONTENT_SERIES_INVALID"),
    ("contents.json", "c-drama-2", {"series": {"title": "测试剧集", "episode": 1}}, "CONTENT_SERIES_INVALID"),
]
