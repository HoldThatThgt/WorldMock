"""@brief 食品购买各匹配阶段得到真正支持该食品的店铺与方式。"""

import pytest
from worldmock.errors import PreconditionError
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply
from tests.life.test_named_purchase import menu_context


def test_named_pickup_selects_the_actual_present_store_before_remote_options():
    context = menu_context("noodle-2")
    chosen = resolve_named_supply(context, PurchaseAsk("薯片", "购买", None, "noodle-2", "到手价最低"))
    assert chosen["store_id"] == "noodle-2" and chosen["selected_action"]["fulfillment"] == "pickup"
    assert chosen["app_id"] is None


def test_named_source_constrains_the_store_even_when_another_store_is_nearer():
    context = menu_context("cafe")
    chosen = resolve_named_supply(context, PurchaseAsk("牛肉面", "远程下单", "美团", "cafe", "最快拿到"))
    assert chosen["store_id"] == "noodle-1" and chosen["app_id"] == "meituan"
    with pytest.raises(PreconditionError, match="SUPPLY_SOURCE_MISMATCH"):
        resolve_named_supply(context, PurchaseAsk("二味小炒出售的牛肉面", "购买", "美团", "cafe", "最快拿到"))
