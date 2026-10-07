"""@brief 实际商品名称、人物明确购物支付应用与原子拒绝。"""

from types import SimpleNamespace

import pytest
from tests.life.purchase_worlds import registered_world
from worldmock.errors import PreconditionError
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply
from worldmock.life.purchase_offers import Offer, choose


def menu_context(location="home"):
    semantics, state = registered_world(location)
    return SimpleNamespace(semantics=semantics, state=state, scenario=semantics.scenario, agent_id=1)


@pytest.mark.parametrize("target,app,payment,code", [
    ("不存在的商品", None, None, "SUPPLY_NOT_FOUND"),
    ("牛肉面", "不存在的店", "支付宝", "APP_NOT_OFFERED"),
    ("一心面馆出售的牛肉面", "饿了么", "支付宝", "SUPPLY_NOT_FOUND"),
    ("饭团", "美团", "支付宝", "SUPPLY_NOT_AT_STORE"),
    ("牛肉面", "支付宝", None, "APP_NOT_OFFERED"),
    ("牛肉面", "美团", None, "PAYMENT_APP_REQUIRED"),
    ("薯片", "饿了么", "微信", "PAYMENT_APP_NOT_BOUND"),
    ("牛肉面", None, "抖音", "PAYMENT_APP_NOT_BOUND"),
])
def test_named_purchase_rejects_unknown_goods_and_unbound_apps_atomically(target, app, payment, code):
    context = menu_context()
    before = context.state.model_dump_json()
    with pytest.raises(PreconditionError, match=code):
        resolve_named_supply(context, PurchaseAsk(target, app, payment, "agent:1"))
    assert context.state.model_dump_json() == before


def test_explicit_shopping_and_payment_apps_record_the_actual_identities():
    context = menu_context()
    binding = resolve_named_supply(context, PurchaseAsk("牛肉面", "美团", "支付宝", "agent:1"))
    assert binding["store_id"] == "noodle-1" and binding["goods_id"] == "beef-noodles"
    assert binding["app_id"] == "meituan" and binding["payment_app_id"] == "alipay"
    assert binding["destination_id"] == "home" and binding["expected_at"] > context.state.current_time


def test_onsite_name_purchase_has_no_app_and_guidance_uses_real_places():
    context = menu_context("noodle-2")
    binding = resolve_named_supply(context, PurchaseAsk("牛肉面", None, None, "agent:1"))
    assert binding["store_id"] == "noodle-2" and binding["app_id"] is None
    assert binding["payment_app_id"] is None
    assert binding["selected_action"]["fulfillment"] == "pickup"
    with pytest.raises(PreconditionError) as failure:
        resolve_named_supply(menu_context(), PurchaseAsk("牛肉面", None, None, "agent:1"))
    assert failure.value.unmet == ["required_location_id"]
    assert failure.value.suggested["next_step"] == "Go to 二味小炒."


def test_offers_choose_earliest_arrival_then_store_identity():
    goods = menu_context().semantics.catalog.goods["chips"]
    later = Offer("cafe", goods, "home", 1200, "delivery")
    earliest = Offer("noodle-1", goods, "home", 900, "delivery")
    assert choose((later, earliest)) == earliest
    equal = Offer("cafe", goods, "home", 900, "delivery")
    assert choose((earliest, equal)) == equal
