"""@brief 商品购买只依据明确应用和本人实际位置选择在售店铺。"""

import pytest
from tests.integration.life.test_named_purchase import menu_context
from worldmock.errors import PreconditionError
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply


def test_named_app_null_selects_only_the_actual_present_store():
    context = menu_context("noodle-2")
    chosen = resolve_named_supply(context, PurchaseAsk("薯片", None, None, "agent:1"))
    assert chosen["store_id"] == "noodle-2" and chosen["selected_action"]["fulfillment"] == "pickup"
    assert chosen["app_id"] is None
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET"):
        resolve_named_supply(menu_context(), PurchaseAsk("薯片", None, None, "agent:1"))


def test_explicit_app_excludes_a_closer_unbound_store():
    context = menu_context("noodle-2")
    chosen = resolve_named_supply(context, PurchaseAsk("薯片", "美团", "支付宝", "agent:1"))
    assert chosen["store_id"] != "noodle-2" and chosen["app_id"] == "meituan"
    assert chosen["app_id"] in context.semantics.catalog.apps_by_store[chosen["store_id"]]
    with pytest.raises(PreconditionError, match="SUPPLY_NOT_FOUND"):
        resolve_named_supply(context, PurchaseAsk("二味小炒出售的薯片", "美团", "支付宝", "agent:1"))
