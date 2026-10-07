"""@brief 食品购买真实拒绝和集中英文原因的一致性。"""

import pytest
from worldmock.life.guidance import default_reason, refusal_for_code, rejection_guidance


@pytest.mark.parametrize("code,reason", [
    ("APP_NOT_BOUND", "This store cannot be ordered through that app."),
    ("FULFILLMENT_NOT_SUPPORTED", "This store has no delivery app."),
    ("ORDER_DUPLICATE_ITEM", "The same food from the same store appears twice in one order."),
    ("SUPPLY_NOT_AT_STORE", "The named source does not sell this food."),
    ("SOURCE_NOT_FOUND", "The source is not a known store or app name."),
    ("SUPPLY_NOT_FOUND", "No food exists for the written name; changing spelling cannot create a food."),
])
def test_food_rejection_reason_is_the_declared_exact_text(code, reason):
    assert default_reason(code) == reason
    assert rejection_guidance(refusal_for_code(code).error())["reason"] == reason


@pytest.mark.parametrize("code", ["MERCHANT_CLOSED", "SUPPLY_QUOTE_UNKNOWN", "OUT_OF_STOCK",
                                 "ORDER_DUPLICATE_SUPPLY", "BROWSE_TARGET_NOT_FOUND", "CATEGORY_PAIRING_NO_EFFECT"])
def test_retired_food_and_store_rejection_codes_have_no_default(code):
    with pytest.raises(ValueError, match="no declared reason"):
        default_reason(code)
