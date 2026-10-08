"""@brief 固定商品集合经生产发现、投影、准入到真实工具目标。"""

import json

from worldmock.life.callable_index import callable_index
from worldmock.life.discovery import DiscoveryContext, product_rows
from worldmock.life.tool_layer import tool_layer
from tests.life.purchase_worlds import menu_context


def test_same_kind_stores_share_goods_and_product_discovery_deduplicates_real_app_bindings():
    context = menu_context("noodle-2")
    catalog = context.semantics.catalog
    assert catalog.goods_by_store["noodle-1"] == catalog.goods_by_store["noodle-2"] == (
        "beef-noodles", "chips", "lemon-tea")
    rows = product_rows(DiscoveryContext(context.state, context.scenario, context.semantics, context.state.agents[1]))
    assert {row["goods_id"] for row in rows} == {"beef-noodles", "chips", "lemon-tea"}
    assert len(rows) == len({row["goods_id"] for row in rows})
    assert next(row for row in rows if row["goods_id"] == "chips")["app_ids"] == ["eleme", "meituan"]
    assert all(not ({"quote", "stock", "opening_status", "price_version"} & row.keys()) for row in rows)


def test_goods_tool_state_category_energy_apps_and_fixed_price():
    context = menu_context("noodle-2")
    layer = tool_layer(context, callable_index(context))
    targets = {target["name"]: target for target in layer["targets"]}
    product = targets["牛肉面"]
    assert product["kind"] == "product" and "正餐" in product["state"] and "600" in product["state"]
    assert {action["way"] for action in product["actions"]} == {"购买"}
    onsite = next(action for action in product["actions"] if action["app"] is None)
    delivery = next(action for action in product["actions"] if action["app"] == "美团")
    assert onsite["available"] is True and onsite["price"] == "28 元"
    assert delivery["available"] is True and delivery["price"] == "33 元"
    assert "taste_weights" not in json.dumps(layer, ensure_ascii=False)
    assert {row["category"] for row in layer["callable_index"]["goods"]} == {"正餐", "零食", "饮品"}
