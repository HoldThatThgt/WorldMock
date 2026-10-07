"""@brief 实际食品菜单的命名匹配、app 来源限定和确定性选择。"""

from types import SimpleNamespace

import pytest
from worldmock.errors import PreconditionError
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply
from worldmock.life.purchase_offers import Offer, choose
from tests.life.purchase_worlds import registered_world


def menu_context(location="home"):
    semantics, state = registered_world(location)
    return SimpleNamespace(semantics=semantics, state=state, scenario=semantics.scenario, agent_id=1)


@pytest.mark.parametrize("target,source,way,code", [
    ("不存在的食品", None, "购买", "SUPPLY_NOT_FOUND"),
    ("牛肉面", "不存在的店", "购买", "SOURCE_NOT_FOUND"),
    ("一心面馆出售的牛肉面", "饿了么", "远程下单", "SUPPLY_SOURCE_MISMATCH"),
    ("饭团", "美团", "购买", "SUPPLY_NOT_AT_STORE"),
    ("二味小炒出售的牛肉面", None, "远程下单", "FULFILLMENT_NOT_SUPPORTED"),
    ("牛肉面", "美团", "到店", "SUPPLY_SOURCE_MISMATCH"),
])
def test_named_purchase_rejects_food_source_and_way_mismatch(target, source, way, code):
    context = menu_context()
    before = context.state.model_dump_json()
    with pytest.raises(PreconditionError, match=code) as failure:
        resolve_named_supply(context, PurchaseAsk(target, way, source, "home", "最快拿到"))
    assert context.state.model_dump_json() == before
    if code == "FULFILLMENT_NOT_SUPPORTED":
        assert failure.value.public_reason == "This store has no delivery app."
        assert failure.value.suggested["next_step"] == "Go to 二味小炒."


def test_explicit_app_source_records_the_actual_app_identity():
    context = menu_context()
    binding = resolve_named_supply(context, PurchaseAsk("牛肉面", "远程下单", "美团", "home", "最快拿到"))
    assert binding["store_id"] == "noodle-1" and binding["goods_id"] == "beef-noodles"
    assert binding["app_id"] == "meituan" and "通过美团下单" in binding["purchase_note"]


def test_onsite_name_purchase_has_no_app_and_remote_guidance_uses_real_places():
    context = menu_context("noodle-2")
    binding = resolve_named_supply(context, PurchaseAsk("牛肉面", "购买", None, "noodle-2", "最快拿到"))
    assert binding["store_id"] == "noodle-2" and binding["app_id"] is None
    assert binding["selected_action"]["fulfillment"] == "pickup"
    with pytest.raises(PreconditionError) as failure:
        resolve_named_supply(menu_context(), PurchaseAsk("牛肉面", "到店", None, "home", "最快拿到"))
    assert failure.value.unmet == ["required_location_id"]
    assert failure.value.suggested["next_step"] == "Go to 二味小炒."


def test_preference_counts_delivery_fee_and_breaks_ties_by_store_id():
    food = menu_context().semantics.catalog.goods["chips"]
    pickup = Offer("noodle-2", food, "到店", 0, food.price_fen)
    delivery = Offer("noodle-1", food, "远程下单", 1200, food.price_fen + 500, "meituan")
    assert choose([delivery, pickup], "到手价最低") == pickup
    assert choose([delivery, pickup], "最快拿到") == pickup
    equal = Offer("cafe", food, "远程下单", delivery.speed, delivery.price, "eleme")
    assert choose([delivery, equal], "最快拿到") == equal
    assert choose([delivery, equal], "到手价最低") == equal
