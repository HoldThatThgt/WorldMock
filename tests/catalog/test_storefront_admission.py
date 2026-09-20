"""@brief 商品与供给分离后的目录结构和账户准入验证。"""

import pytest
from pydantic import ValidationError

from worldmock.catalog.admission import ScenarioAccountRef, ScenarioCatalogRefs
from worldmock.catalog.models import ExchangeObject


def product():
    return {"object_id": "umbrella", "name": "Umbrella", "object_kind": "product", "effect": "weather",
            "consumable": False, "use_modes": ["use"], "edges": [], "use_duration_seconds": 300,
            "required_location_id": None, "provenance": [{"type": "scenario", "source": "test"}], "version": 1}


def test_durable_product_has_no_fake_purchase_or_exchange_edge():
    obj = ExchangeObject.model_validate(product())
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
            "amount_fen": 500, "account_roles": {"from": "buyer", "to": "merchant:grocery"}}
    with pytest.raises(ValidationError, match="purchase price"):
        ExchangeObject.model_validate({**product(), "edges": [edge]})


def test_service_still_requires_an_actual_exchange():
    with pytest.raises(ValidationError, match="at least one edge"):
        ExchangeObject.model_validate({**product(), "object_kind": "service"})


def account(identity, role):
    return ScenarioAccountRef(account_id=identity, resource_id="money_fen", role=role, initial_value=0, unit="fen")


def test_two_sellers_can_have_the_same_business_role():
    refs = ScenarioCatalogRefs(accounts=(account("shop-a", "merchant:grocery"),
                                        account("shop-b", "merchant:grocery")))
    assert len(refs.accounts) == 2


def test_implicit_service_role_still_requires_unique_account():
    with pytest.raises(ValidationError, match="non-merchant"):
        ScenarioCatalogRefs(accounts=(account("a", "transport"), account("b", "transport")))
