"""@brief 食品名字索引取实际可得固定菜单，与命名受理使用相同目录。"""

from worldmock.life.callable_index import IndexSources, callable_index
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply
from tests.life.purchase_worlds import menu_context


def test_every_food_index_example_resolves_to_the_actual_fixed_menu():
    context = menu_context("noodle-2")
    index = callable_index(context, IndexSources())
    assert {row["category"] for row in index["goods"]} == {"正餐", "零食", "饮品"}
    for category in index["goods"]:
        for name in category["examples"]:
            row = resolve_named_supply(context, PurchaseAsk(name, "购买", None, "noodle-2", "最快拿到"))
            assert row["goods_id"] in context.semantics.catalog.goods_by_store[row["store_id"]]
