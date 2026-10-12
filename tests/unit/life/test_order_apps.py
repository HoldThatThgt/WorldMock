"""下单 app 与意向 app：范围只有能接单的内容 app，规整与 detail 相同，唯一对上才算，认不出就是没有意向。"""

from __future__ import annotations

import pytest

from tests.data_paths import CATALOG_ROOT
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from worldmock.catalog.loader import load_catalog
from worldmock.life.order_apps import order_app_ids, preferred_app_id


def _catalog(root):
    """按生产入口加载一份目录（地图种子与本测试无关，取固定值）。"""
    return load_catalog(root / "catalog", 7)


def _register_at_cafe(root, *app_ids):
    """把给定的 app 也登记到三杯咖啡：它卖商品，这些 app 因此成为下单 app；不新增 app 行。"""
    rows = catalog_rows(root, "location.jsonl")
    cafe = next(row for row in rows if row["location_id"] == "cafe")
    cafe["app_ids"] = sorted({*cafe["app_ids"], *app_ids})
    write_catalog_rows(root, "location.jsonl", rows)


@pytest.fixture
def keep_orders(tmp_path):
    """副本一：现成的 Keep 也登记到三杯咖啡，它是唯一一个拉丁字母名称的下单 app。"""
    root = copy_environment(tmp_path / "keep")
    _register_at_cafe(root, "keep")
    return root


def test_fixture_order_apps_are_the_content_apps_that_take_orders():
    """哔哩哔哩直接卖商品，饿了么和美团被卖商品的店登记；抖音、Keep 只有内容，支付宝、微信、高德地图是功能 app。"""
    catalog = load_catalog(CATALOG_ROOT, 7)
    assert order_app_ids(catalog) == ("bilibili", "eleme", "meituan")


@pytest.mark.parametrize("written,expected", [
    ("饿了么", "eleme"), ("饿 了 么", "eleme"), ("饿了么（模拟）", "eleme"), ("美团", "meituan"),
    ("美 团", "meituan"), ("美团（模拟）", "meituan"), ("哔哩哔哩", "bilibili"),
    (None, None), ("美团外卖", None), ("支付宝", None), ("抖音", None), ("Keep", None), ("不存在", None),
])
def test_prefer_is_recognised_only_among_order_apps_with_lossless_normalization(written, expected):
    """规整统一全半角、括号与空白并忽略「（模拟）」；不认简称或近名，功能 app 与不卖东西的内容 app 都认不出。"""
    assert preferred_app_id(load_catalog(CATALOG_ROOT, 7), written) == expected


@pytest.mark.parametrize("written,expected", [("Keep", "keep"), ("Ｋｅｅｐ", "keep"), ("K eep", "keep"),
                                              ("keep", None), ("KEEP", None)])
def test_registering_an_app_at_a_selling_store_makes_it_an_order_app_without_case_folding(
        keep_orders, written, expected):
    """副本一：Keep 成了下单 app；全角与多余空白规整后对上，大小写不折叠。"""
    catalog = _catalog(keep_orders)
    assert order_app_ids(catalog) == ("bilibili", "eleme", "keep", "meituan")
    assert preferred_app_id(catalog, written) == expected


@pytest.mark.parametrize("written,expected", [("Keep", "keep"), ("Ｋｅｅｐ", "douyin"), ("K eep", None)])
def test_exact_name_wins_and_a_name_matching_two_order_apps_is_no_intention(keep_orders, written, expected):
    """副本二：抖音改名为全角的「Ｋｅｅｐ」并同样登记到三杯咖啡。原文相等优先，各认成各自；规整后同时对上两个就
    当作没有意向。"""
    rows = catalog_rows(keep_orders, "app-catalog.jsonl")
    next(row for row in rows if row["app_id"] == "douyin")["name"] = "Ｋｅｅｐ"
    write_catalog_rows(keep_orders, "app-catalog.jsonl", rows)
    _register_at_cafe(keep_orders, "douyin")
    catalog = _catalog(keep_orders)
    assert len(catalog.apps) == 8
    assert order_app_ids(catalog) == ("bilibili", "douyin", "eleme", "keep", "meituan")
    assert preferred_app_id(catalog, written) == expected
