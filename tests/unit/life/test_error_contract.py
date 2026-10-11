"""@brief 商品购买真实拒绝和集中英文原因的一致性。"""

import pytest
from worldmock.life.guidance import Refusal, default_reason, guided, refusal_for_code, rejection_guidance


@pytest.mark.parametrize("code,reason", [
    ("LOCATION_CLOSED", "The location is closed at the current time."),
    ("ORDER_NOT_PAID", "The order has not been paid."),
    ("SUPPLY_NOT_FOUND", "No goods exist for the written name; changing spelling cannot create goods."),
])
def test_goods_rejection_reason_is_the_declared_exact_text(code, reason):
    assert default_reason(code) == reason
    assert rejection_guidance(refusal_for_code(code).error())["reason"] == reason


@pytest.mark.parametrize("code", ["MERCHANT_CLOSED", "SUPPLY_QUOTE_UNKNOWN", "OUT_OF_STOCK",
                                 "ORDER_DUPLICATE_SUPPLY", "BROWSE_TARGET_NOT_FOUND", "CATEGORY_PAIRING_NO_EFFECT",
                                 "FULFILLMENT_NOT_SUPPORTED", "SOURCE_NOT_FOUND", "APP_NOT_BOUND",
                                 "SUPPLY_NOT_AT_STORE", "CAR_REQUIRED", "DRIVING_DAYS_EXHAUSTED",
                                 "ALARM_TIME_INVALID", "MESSAGE_TEXT_TOO_LONG",
                                 "ORDER_DUPLICATE_ITEM", "ORDER_ITEM_COUNT_OUT_OF_RANGE", "TOO_MANY_TARGETS"])
def test_retired_food_and_store_rejection_codes_have_no_default(code):
    with pytest.raises(ValueError, match="no declared reason"):
        default_reason(code)


def test_guided_refusal_preserves_readable_remedy_and_keeps_unmet_conditions_private():
    unmet = ["sleepable_location"]
    error = guided("ACTIVITY_PRECONDITION_UNMET", "Sleeping is not possible at 五里公园.",
                   "Go to 家.", unmet, "Sleep where sleeping is possible.")
    unmet.append("private-condition")
    assert str(error) == "ACTIVITY_PRECONDITION_UNMET"
    assert error.unmet == ["sleepable_location"]
    assert str(error.__cause__) == "Sleeping is not possible at 五里公园."
    assert rejection_guidance(error) == {
        "reason": "Sleeping is not possible at 五里公园.",
        "next_step": "Go to 家.",
        "fix": "Sleep where sleeping is possible.",
    }


def test_guided_refusal_without_a_remedy_exposes_null_instead_of_inventing_a_step():
    error = guided("INSUFFICIENT_TRAVEL_FUNDS", "The actual bus fare is 200 fen; only 199 fen is available.")
    assert error.unmet == [] and error.suggested is None
    assert rejection_guidance(error) == {
        "reason": "The actual bus fare is 200 fen; only 199 fen is available.",
        "next_step": None,
        "fix": None,
    }


def test_busy_activity_refusal_names_the_actual_activities_to_release():
    error = Refusal("RESOURCE_CONFLICT", "Both activity slots are occupied.",
                    suggested={"conclude_or_pause": ["浏览哔哩哔哩", "步行前往公司"]}).error()
    assert rejection_guidance(error) == {
        "reason": "Both activity slots are occupied.",
        "next_step": "Conclude or pause 浏览哔哩哔哩, 步行前往公司 first.",
        "fix": None,
    }
