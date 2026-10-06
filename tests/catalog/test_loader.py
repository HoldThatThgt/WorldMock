"""@brief 人工目录登记往返和严格旧格式拒绝的行为证明。"""

import sys

import pytest

from tests.catalog.conftest import edit_row
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import channel_key


def test_catalog_registers_artificial_objects_and_indexes(loaded_catalog):
    """@brief 发布对象和五种绑定索引均与手工期望相等。"""
    catalog = loaded_catalog
    assert (len(catalog.apps), len(catalog.locations), len(catalog.foods), len(catalog.contents)) == (4, 7, 5, 6)
    assert catalog.manifest.environment_id == "artificial-test"
    assert catalog.foods_by_store["noodle-1"] == ("beef-noodles", "chips", "lemon-tea")
    assert catalog.foods_by_store["noodle-2"] == catalog.foods_by_store["noodle-1"]
    assert catalog.menus_by_kind["restaurant"] == catalog.foods_by_store["noodle-1"]
    assert catalog.foods_by_store["home"] == catalog.foods_by_store["office"] == catalog.foods_by_store["park"] == ()
    assert catalog.stores_by_app["meituan"] == ("cafe", "noodle-1")
    assert catalog.stores_by_app["eleme"] == ("cafe",)
    assert catalog.contents_by_app["douyin"] == ("c-live", "c-long", "c-short", "c-short-2")
    assert catalog.contents_by_app["bilibili"] == ("c-article", "c-image", "c-long")
    assert catalog.apps_by_store["cafe"] == ("eleme", "meituan")
    assert catalog.location_kinds("restaurant") == ("venue",)
    assert catalog.location_kinds("park") == ("outdoor",)
    assert catalog.work_topics["topic-sales"].target_seconds == 28800
    assert catalog.foods["beef-noodles"].price_fen == 2800
    assert not hasattr(catalog, "shortest_m")
    with pytest.raises(TypeError):
        catalog.apps["fake"] = catalog.apps["douyin"]


@pytest.mark.parametrize("filename,identity,updates", [
    ("foods.json", "chips", {"kcal": 9000}),
    ("contents.json", "c-article", {"genre": "test"}),
    ("locations.json", "home", {"latitude": 1.0, "longitude": 1.0}),
])
def test_catalog_rejects_retired_object_fields(catalog_dir, filename, identity, updates):
    """@brief 环境不能重新声明类别热量、内容类型扩展或地图坐标。"""
    edit_row(catalog_dir, filename, identity, updates)
    with pytest.raises(RegistrationError):
        load_catalog(catalog_dir)


def test_channel_key_keeps_exact_name_identity():
    """@brief 比较仅去空白并忽略大小写，全半角仍然有区别。"""
    assert channel_key(" My APP \n") == "myapp"
    assert channel_key("Ａpp") != channel_key("App")


def test_catalog_load_does_not_import_complete_engine():
    """@brief 目录加载保持纯边界，不把完整运行器带入收集期。"""
    assert "worldmock.life.engine" not in sys.modules
    assert "agentsociety2.society" not in sys.modules
