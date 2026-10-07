"""@brief 固定食品菜单经生产发现、投影、菜单准入到真实工具目标。"""

import json

from worldmock.life.callable_index import IndexSources, callable_index
from worldmock.life.tool_layer import tool_layer
from tests.life.purchase_worlds import menu_context


def test_same_kind_stores_share_foods_with_real_distinct_bindings():
    context = menu_context("noodle-2")
    rows = [row for row in context.bindings if row["kind"] == "supply"]
    stores = {store: {row["food_id"] for row in rows if row["store_id"] == store}
              for store in ("noodle-1", "noodle-2")}
    assert stores["noodle-1"] == stores["noodle-2"] == {"beef-noodles", "chips", "lemon-tea"}
    assert len({row["object_id"] for row in rows}) == 9
    assert all(not ({"quote", "stock", "opening_status", "price_version"} & row.keys()) for row in rows)


def test_food_tool_state_category_energy_apps_and_fixed_price():
    context = menu_context("noodle-2")
    layer = tool_layer(context, callable_index(context, IndexSources()))
    targets = {target["name"]: target for target in layer["targets"]}
    meituan = targets["一心面馆出售的牛肉面"]
    onsite = targets["二味小炒出售的牛肉面"]
    assert meituan["state"] == "正餐，每份约 600 千卡；可用美团下单"
    assert onsite["state"] == "正餐，每份约 600 千卡"
    assert {action["way"] for action in meituan["actions"]} == {"到店", "远程下单"}
    assert onsite["actions"][0]["available"] is True
    assert onsite["actions"][0]["price"] == "28 元"
    delivery = next(action for action in meituan["actions"] if action["way"] == "远程下单")
    assert delivery["available"] is True and delivery["price"] == "33 元"
    assert "taste_weights" not in json.dumps(layer, ensure_ascii=False)
    assert {row["category"] for row in layer["callable_index"]["goods"]} == {"正餐", "零食", "饮品"}
