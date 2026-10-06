"""@brief 商品与供给分离后的目录结构和账户准入验证。"""

import pytest
from pydantic import ValidationError

from tests.categories.conftest import raw_object, rules_document
from worldmock.catalog.admission import ScenarioAccountRef, ScenarioCatalogRefs
from worldmock.catalog.models import ExchangeObject, ObjectDeclaration
from worldmock.categories.rules import PurchaseTransferRule


def product():
    """@brief 返回无门店价格或库存声明的人工耐用品视图。"""
    return {
        "object_id": "umbrella", "name": "Umbrella", "category_id": "product", "object_kind": "product",
        "attributes": {"effect": "durable", "consumable": False, "use_modes": ["use"]},
        "effect": "durable", "consumable": False, "use_modes": ["use"], "edges": [],
        "provenance": raw_object()["provenance"], "version": 1,
    }


def test_durable_product_has_no_fake_purchase_or_exchange_edge():
    obj = ExchangeObject.model_validate({**product(), "edges": []})
    assert obj.edges == ()
    assert "stock" not in obj.model_dump()
    assert "delivery_delay_seconds" not in obj.model_dump()


@pytest.mark.parametrize("field,value", [("stock", {"initial": 10}), ("delivery_delay_seconds", 300),
                                          ("merchant_location_id", "store"), ("price_fen", 500),
                                          ("legacy_effect", "durable")])
def test_old_product_supply_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        ExchangeObject.model_validate({**product(), field: value})


def test_product_cannot_smuggle_a_purchase_price_edge():
    edge = {"on": "purchase", "type": "transfer", "from": "{buyer}", "to": "{merchant}",
            "resource": "money_fen", "amount_fen": 500, "account_roles": {"from": "buyer", "to": "merchant:grocery"}}
    with pytest.raises(ValidationError, match="purchase price"):
        ExchangeObject.model_validate({**product(), "edges": [edge]})


def test_raw_product_cannot_embed_any_exchange_edge():
    raw = raw_object("umbrella", "product")
    with pytest.raises(ValidationError, match="edges"):
        ObjectDeclaration.model_validate({**raw, "edges": []})


def account(identity, role):
    return ScenarioAccountRef(account_id=identity, resource_id="money_fen", role=role, initial_value=0, unit="fen")


def test_two_sellers_can_have_the_same_business_role():
    refs = ScenarioCatalogRefs(accounts=(account("shop-a", "merchant:grocery"),
                                        account("shop-b", "merchant:grocery")))
    assert len(refs.accounts) == 2


def test_implicit_service_role_still_requires_unique_account():
    with pytest.raises(ValidationError, match="non-merchant"):
        ScenarioCatalogRefs(accounts=(account("a", "transport"), account("b", "transport")))


def test_purchase_rule_rejects_the_removed_product_price_slot():
    rule = PurchaseTransferRule.model_validate(rules_document()["rules"][0])
    with pytest.raises(ValidationError, match="price_slot"):
        type(rule).model_validate({**rule.model_dump(mode="json"), "price_slot": "price_fen"})
