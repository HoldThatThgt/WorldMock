"""查询的纯函数：时长的抽取、这几分钟用哪个 app、认出的各项怎样去重与展开、答案的排序与 20 件上限
（docs/design/04-simulation-engine.md「出行、内容与查询」）。"""

from __future__ import annotations

import pytest

from tests.data_paths import CATALOG_ROOT
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.simulation_engine.activities.query.query_activity import (
    QUERY_SECONDS_MAX,
    QUERY_SECONDS_MIN,
    query_app,
    query_seconds,
    query_targets,
)
from worldmock.simulation_engine.activities.query.query_answer import QUERY_PRODUCT_LIMIT, ordered_answer, queried_goods
from worldmock.foundation.randomness import random_stream

#: 单元用例的世界随机状态（任意固定文字）。
_STATE = "query-rules-random-state"
#: 一个开始时刻（纪元秒）。
_NOW = 1_767_571_200


@pytest.fixture(scope="module")
def catalog():
    """夹具环境的冻结目录（地图种子与本文件无关，取固定值）。"""
    return load_catalog(CATALOG_ROOT, 7)


def test_duration_is_drawn_once_per_action_between_300_and_600_seconds_inclusive():
    """若干个动作身份的时长都等于按 query-duration:{action_id} 独立算出的 randint(300, 600)；两端都取得到。"""
    drawn = {f"action-{index}": query_seconds(_STATE, f"action-{index}") for index in range(4000)}
    assert drawn == {key: random_stream(_STATE, f"query-duration:{key}").randint(300, 600) for key in drawn}
    assert (min(drawn.values()), max(drawn.values())) == (300, 600)
    assert (QUERY_SECONDS_MIN, QUERY_SECONDS_MAX) == (300, 600)
    assert query_seconds(_STATE, "action-7") == query_seconds(_STATE, "action-7")


def test_usable_prefer_is_the_app_and_scopes_the_answer(catalog):
    """意向 app 卖所查商品里的至少一件：它就是这几分钟的 app，答案只列经它能买的，不抽。"""
    assert query_app(catalog, _STATE, "a", ("lemon-tea",), "meituan") == ("meituan", True)
    assert query_app(catalog, _STATE, "a", ("lemon-tea", "online-notebook"), "bilibili") == ("bilibili", True)


@pytest.mark.parametrize("goods_ids,prefer,candidates", [
    (("lemon-tea",), None, ["eleme", "meituan"]),
    (("online-notebook",), "eleme", ["bilibili"]),
    (("lemon-tea", "online-notebook"), None, ["bilibili", "eleme", "meituan"]),
    (("beef-noodles",), "bilibili", ["meituan"]),
])
def test_unusable_prefer_draws_among_the_selling_apps_in_app_id_order(catalog, goods_ids, prefer, candidates):
    """意向用不上（没写，或写的下单 app 一件都不卖）：在卖其中任何一件的下单 app 的并集里按 app_id 升序、
    按 query-app:{action_id} 等概率抽一个，答案列出全部途径。"""
    for action_id in ("first", "second", "third", "fourth"):
        expected = random_stream(_STATE, f"query-app:{action_id}").choice(candidates)
        assert query_app(catalog, _STATE, action_id, goods_ids, prefer) == (expected, False)


@pytest.mark.parametrize("prefer", [None, "meituan"])
def test_goods_that_no_app_sells_leave_the_phone_without_an_app(catalog, prefer):
    """纸巾只有没登记 app 的四季便利卖：手机上没有 app，答案列出全部途径；类别下没有商品时同样。"""
    assert query_app(catalog, _STATE, "a", ("tissue",), prefer) == (None, False)
    assert query_app(catalog, _STATE, "a", (), prefer) == (None, False)


def test_targets_keep_the_written_order_and_count_each_goods_or_category_once():
    """认出的各行按所写次序去重：同一件商品、同一个类别只留第一次出现的那一项。"""
    rows = [{"kind": "supply", "object_id": "chips"}, {"kind": "named_supply", "object_id": "chips"},
            {"kind": "category", "object_id": None, "category": "snack"},
            {"kind": "category", "object_id": None, "category": "snack"},
            {"kind": "named_supply", "object_id": "online-notebook"}]
    assert query_targets(rows) == [{"kind": "goods", "goods_id": "chips"}, {"kind": "category", "category": "snack"},
                                   {"kind": "goods", "goods_id": "online-notebook"}]


def test_categories_expand_to_every_goods_and_a_scoped_answer_keeps_named_goods(catalog):
    """类别展开出全部商品，只在 app 里卖的也算；限定意向 app 时类别只展开它卖的，指名的商品照留。"""
    goods = [{"kind": "category", "category": "goods"}]
    assert queried_goods(catalog, goods) == ("online-notebook", "playing-cards", "tissue")
    assert queried_goods(catalog, goods, "meituan") == ("playing-cards",)
    assert queried_goods(catalog, goods, "bilibili") == ("online-notebook",)
    named = [{"kind": "goods", "goods_id": "tissue"}, *goods, {"kind": "goods", "goods_id": "tissue"}]
    assert queried_goods(catalog, named, "meituan") == ("playing-cards", "tissue")
    assert queried_goods(catalog, [{"kind": "category", "category": "meal"}]) == ("beef-noodles", "rice-ball")
    assert queried_goods(catalog, []) == ()


def _row(goods_id: str, store: str | None, available: bool, expected: int | None = None) -> dict:
    """手写的一行机器形式：可以下单的外卖行带预计送达，到店行与不可下单的行不带。"""
    route = None if store is None else ("delivery" if expected is not None else "pickup")
    return {"goods_id": goods_id, "route": route, "store_id": store, "app_ids": [], "price_fen": 100,
            "delivery_fee_fen": None if expected is None else 500, "expected_at": expected,
            "available": available, "reason": None if available else "store_without_app"}


def test_rows_inside_a_goods_put_orderable_rows_first_then_arrival_then_store():
    """一件商品之内：可以下单的在前；可下单行按送达时刻，到店行按此刻算；再按店铺身份，没有店的行取空串。"""
    rows = [_row("tea", "z-closed", False), _row("tea", None, False), _row("tea", "far", True, _NOW + 900),
            _row("tea", "here", True), _row("tea", "near", True, _NOW + 300), _row("tea", "a-closed", False)]
    answer = ordered_answer([rows], _NOW)
    assert [row["store_id"] for row in answer["rows"]] == ["here", "near", "far", None, "a-closed", "z-closed"]
    assert answer["omitted"] == 0


def test_goods_are_ordered_by_orderability_then_earliest_arrival_then_goods_id_and_capped_at_twenty():
    """23 件商品：有可下单行的在前，其间按最早送达、再按 goods_id；没有可下单行的排后；只列 20 件，其余只计件数。"""
    products = [[_row(f"late-{index:02d}", "far", True, _NOW + 900)] for index in range(8)]
    products += [[_row(f"none-{index}", "shop", False)] for index in range(4)]
    products += [[_row(f"soon-{index:02d}", "near", True, _NOW + 300), _row(f"soon-{index:02d}", "x", False)]
                 for index in range(10)]
    products.append([_row("here-goods", "here", True), _row("here-goods", "far", True, _NOW + 900)])
    answer = ordered_answer(list(reversed(products)), _NOW)
    listed = list(dict.fromkeys(row["goods_id"] for row in answer["rows"]))
    assert QUERY_PRODUCT_LIMIT == 20 and len(listed) == 20 and answer["omitted"] == 3
    assert listed == ["here-goods", *(f"soon-{index:02d}" for index in range(10)),
                      *(f"late-{index:02d}" for index in range(8)), "none-0"]
    assert len(answer["rows"]) == 2 + 10 * 2 + 8 + 1
    small = ordered_answer(products[:20], _NOW)
    assert small["omitted"] == 0 and len({row["goods_id"] for row in small["rows"]}) == 20
