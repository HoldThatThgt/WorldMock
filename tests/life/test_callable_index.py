"""@brief 商品名称索引共用实际商品集合，并可按相同目录点名受理。"""

from worldmock.life.callable_index import callable_index
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply
from tests.life.purchase_worlds import menu_context


def test_every_goods_index_example_resolves_to_the_actual_fixed_menu():
    context = menu_context("noodle-2")
    index = callable_index(context)
    assert {row["category"] for row in index["goods"]} == {"正餐", "零食", "饮品"}
    for category in index["goods"]:
        for name in category["examples"]:
            row = resolve_named_supply(context, PurchaseAsk(name, None, None, "agent:1"))
            assert row["goods_id"] in context.semantics.catalog.goods_by_store[row["store_id"]]
            assert row["store_id"] == "noodle-2" and row["app_id"] is None
            assert row["payment_app_id"] is None and row["selected_action"]["fulfillment"] == "pickup"
