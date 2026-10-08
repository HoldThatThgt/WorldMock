"""@brief 六文件目录登记往返、冻结索引和严格旧格式拒绝的行为证明。"""

import subprocess
import sys

import pytest

from tests.catalog.conftest import edit_row
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import channel_key


def test_catalog_registers_artificial_objects_and_indexes(loaded_catalog):
    """@brief 五类对象和反向索引均与手工期望相等。"""
    catalog = loaded_catalog
    assert (len(catalog.apps), len(catalog.locations), len(catalog.goods), len(catalog.actions),
            len(catalog.contents)) == (8, 7, 6, 7, 15)
    assert catalog.manifest.environment_id == "artificial-test"
    assert catalog.goods_by_store["noodle-1"] == ("beef-noodles", "chips", "lemon-tea")
    assert catalog.goods_by_store["noodle-2"] == catalog.goods_by_store["noodle-1"]
    assert catalog.menus_by_kind["restaurant"] == catalog.goods_by_store["noodle-1"]
    assert catalog.goods_by_store["cafe"] == ("chips", "lemon-tea")
    assert catalog.goods_by_store["store"] == ("chips", "lemon-tea", "playing-cards", "rice-ball", "tissue")
    assert all(catalog.goods_by_store[place] == () for place in ("home", "office", "park"))
    assert catalog.stores_by_app["meituan"] == ("cafe", "noodle-1")
    assert catalog.stores_by_app["eleme"] == ("cafe",)
    assert catalog.contents_by_action["browse-feed"] == (
        "c-article", "c-drama-1", "c-drama-2", "c-drama-3", "c-image", "c-live", "c-long", "c-short", "c-short-2")
    assert catalog.contents_by_action_app[("browse-feed", "douyin")] == (
        "c-live", "c-long", "c-short", "c-short-2")
    assert catalog.contents_by_action["office-work"] == ("topic-sales",)
    assert catalog.contents_by_action_app[("study-course", "bilibili")] == ("c-course",)
    assert catalog.actions_by_app["bilibili"] == ("browse-feed", "study-course")
    assert catalog.actions_by_app["amap"] == catalog.actions_by_app["alipay"] == ()
    assert catalog.apps_by_purpose == {"navigation": ("amap",), "payment": ("alipay", "wechat"),
                                       "shopping": ("eleme", "meituan")}
    assert catalog.contents_by_series[("browse-feed", "测试剧集")] == ("c-drama-1", "c-drama-2", "c-drama-3")
    assert catalog.apps_by_store["cafe"] == ("eleme", "meituan")
    assert catalog.location_kinds("restaurant") == ("venue",)
    assert catalog.location_kinds("park") == ("outdoor",)
    assert catalog.goods["beef-noodles"].price_fen == 2800


@pytest.mark.parametrize("field", [
    "apps", "locations", "goods", "actions", "contents", "menus_by_kind", "goods_by_store", "stores_by_app",
    "contents_by_action", "contents_by_action_app", "actions_by_app", "apps_by_purpose", "contents_by_series",
    "apps_by_store",
])
def test_catalog_indexes_are_frozen(loaded_catalog, field):
    """@brief 已登记对象与派生索引都不能跨运行被改写。"""
    with pytest.raises(TypeError):
        getattr(loaded_catalog, field)["fake"] = ()
    with pytest.raises(Exception):
        loaded_catalog.actions["run"].name = "changed"


@pytest.mark.parametrize("filename,identity,updates", [
    ("goods.json", "chips", {"kcal": 9000}),
    ("contents.json", "c-article", {"genre": "test"}),
    ("contents.json", "c-article", {"kind": "article"}),
    ("contents.json", "c-run", {"kind": None}),
    ("contents.json", "c-article", {"relief_per_hour": 0.9}),
    ("contents.json", "c-article", {"agent_id": 1}),
    ("locations.json", "home", {"latitude": 1.0, "longitude": 1.0}),
    *(("actions.json", "run", {field: value}) for field, value in (
        ("duration_seconds", 59), ("typical_duration_minutes", 20), ("time_window", [0, 1200]),
        ("location_kinds", ["outdoor"]), ("met_value", 9), ("effects", {}))),
])
def test_catalog_rejects_retired_object_fields(catalog_dir, filename, identity, updates):
    """@brief 数据集不能重新声明系统规则，额外时长字段优先按未知字段拒绝。"""
    edit_row(catalog_dir, filename, identity, updates)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_KIND_INVALID"):
        load_catalog(catalog_dir)


def test_channel_key_keeps_exact_name_identity():
    """@brief 比较仅去空白并忽略大小写，全半角仍然有区别。"""
    assert channel_key(" My APP \n") == "myapp"
    assert channel_key("Ａpp") != channel_key("App")


def test_catalog_load_does_not_import_complete_engine():
    """@brief 目录加载保持纯边界，不把完整运行器带入收集期。"""
    script = "import sys; import worldmock.catalog.loader; "
    script += "assert 'worldmock.life.engine' not in sys.modules; "
    script += "assert 'agentsociety2.society' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
