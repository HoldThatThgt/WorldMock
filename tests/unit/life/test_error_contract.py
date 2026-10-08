"""@brief 商品购买真实拒绝和集中英文原因的一致性。"""

import pytest
from worldmock.life.guidance import default_reason, refusal_for_code, rejection_guidance


@pytest.mark.parametrize("code,reason", [
    ("APP_NOT_BOUND", "This store cannot be ordered through that app."),
    ("ORDER_DUPLICATE_ITEM", "The same goods for the same recipient appear twice in one order."),
    ("SUPPLY_NOT_AT_STORE", "The named app does not sell these goods."),
    ("SUPPLY_NOT_FOUND", "No goods exist for the written name; changing spelling cannot create goods."),
])
def test_goods_rejection_reason_is_the_declared_exact_text(code, reason):
    assert default_reason(code) == reason
    assert rejection_guidance(refusal_for_code(code).error())["reason"] == reason


@pytest.mark.parametrize("code", ["MERCHANT_CLOSED", "SUPPLY_QUOTE_UNKNOWN", "OUT_OF_STOCK",
                                 "ORDER_DUPLICATE_SUPPLY", "BROWSE_TARGET_NOT_FOUND", "CATEGORY_PAIRING_NO_EFFECT",
                                 "FULFILLMENT_NOT_SUPPORTED", "SOURCE_NOT_FOUND"])
def test_retired_food_and_store_rejection_codes_have_no_default(code):
    with pytest.raises(ValueError, match="no declared reason"):
        default_reason(code)
