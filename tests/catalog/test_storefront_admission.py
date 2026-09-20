"""@brief 商品与供给分离后的目录结构和账户准入验证。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.catalog.admission import ScenarioAccountRef, ScenarioCatalogRefs
from worldmock.catalog import load_catalog
from worldmock.catalog.models import ExchangeObject, ObjectDeclaration


def product():
    return load_catalog(Path("catalog")).objects["item-umbrella"].model_dump(mode="json")


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
    raw = load_catalog(Path("catalog")).raw_objects["item-umbrella"].model_dump(mode="json")
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
    rule = load_catalog(Path("catalog")).exchange_rules.require("purchase-product")
    with pytest.raises(ValidationError, match="price_slot"):
        type(rule).model_validate({**rule.model_dump(mode="json"), "price_slot": "price_fen"})
