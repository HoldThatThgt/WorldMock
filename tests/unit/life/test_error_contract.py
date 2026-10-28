"""@brief 商品购买真实拒绝和集中英文原因的一致性。"""

from datetime import datetime

import pytest

from tests.life.prerequisite_inputs import CLOCK, add_rows, app_content, dingtalk_environment, weekday_hours
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.simulation_engine.observation.wording.content_prerequisite import app_prerequisite_refusal
from worldmock.simulation_engine.observation.wording.guidance import (
    Refusal,
    default_reason,
    guided,
    refusal_for_code,
    rejection_guidance,
)
from worldmock.foundation.time.readable import interval_phrases


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
                                 "ORDER_DUPLICATE_ITEM", "ORDER_ITEM_COUNT_OUT_OF_RANGE", "TOO_MANY_TARGETS",
                                 "CONTENT_EXHAUSTED"])
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


def test_refusal_carries_only_the_next_step_its_author_wrote():
    """拒绝的下一步只有写它的地方写明的那一句；写不写「暂停」由那里按能否暂停决定，这里不另拼一句。"""
    error = Refusal("RESOURCE_CONFLICT", "Both activity slots are occupied.",
                    "Conclude one of 浏览哔哩哔哩、步行前往公司 first.").error()
    assert error.suggested == {"next_step": "Conclude one of 浏览哔哩哔哩、步行前往公司 first."}
    assert rejection_guidance(error) == {
        "reason": "Both activity slots are occupied.",
        "next_step": "Conclude one of 浏览哔哩哔哩、步行前往公司 first.",
        "fix": None,
    }


LUNCH = app_content("dingtalk-lunch", "午间打卡", {"opens": weekday_hours(("12:00", "13:00"))})
GO_TO_OFFICE = "Go to 练秋湖研发中心."
#: 「全都不合格时的拒绝」表的六种情形：钉钉的内容、本地时刻、停留地点、原因、下一步与未满足的成员。
PREREQUISITE_REFUSALS = [
    ((CLOCK,), (12, 8, 10), "home",
     "Nothing in 钉钉 can start here now: it requires presence at 练秋湖研发中心.",
     GO_TO_OFFICE, ("content_location",)),
    ((CLOCK,), (12, 12, 30), "office",
     "Nothing in 钉钉 can start now: it is available 08:00–10:00, 17:00–20:00 today.",
     "Choose another action.", ("content_time",)),
    ((CLOCK,), (17, 9, 0), "office",
     "Nothing in 钉钉 can start now: it is not available today (next available Mon 10-19 08:00–10:00, 17:00–20:00).",
     "Choose another action.", ("content_time",)),
    ((CLOCK,), (12, 12, 30), "home",
     "Nothing in 钉钉 can start here now: it requires presence at 练秋湖研发中心 during 08:00–10:00, 17:00–20:00 today.",
     GO_TO_OFFICE, ("content_location", "content_time")),
    ((CLOCK,), (17, 9, 0), "home",
     "Nothing in 钉钉 can start here now: it requires presence at 练秋湖研发中心 and is not available today "
     "(next available Mon 10-19 08:00–10:00, 17:00–20:00).",
     GO_TO_OFFICE, ("content_location", "content_time")),
    ((CLOCK, LUNCH), (12, 8, 10), "home",
     "Nothing in 钉钉 can start here now: it requires presence at 练秋湖研发中心; "
     "or it is available 12:00–13:00 today.",
     GO_TO_OFFICE, ("content_location", "content_time")),
]


def dingtalk_catalog(tmp_path, *contents):
    return load_catalog(dingtalk_environment(tmp_path / "input", *contents) / "catalog", 7)


def refusal_at(catalog, when, stay):
    """钉钉在给定本地时刻与停留地点（None 为在路上）的前提拒绝。"""
    place = None if stay is None else catalog.locations[stay]
    return app_prerequisite_refusal(catalog, "dingtalk", place, datetime(2026, 10, *when))


@pytest.mark.parametrize("contents,when,stay,reason,next_step,unmet", PREREQUISITE_REFUSALS,
                         ids=["location", "time", "no-hours-today", "both", "both-no-hours-today", "two-kinds"])
def test_prerequisite_refusal_states_the_place_and_todays_hours(tmp_path, contents, when, stay, reason, next_step,
                                                                 unmet):
    refusal = refusal_at(dingtalk_catalog(tmp_path, *contents), when, stay)
    assert (refusal.code, refusal.reason, refusal.unmet) == ("CONTENT_PREREQUISITE_UNMET", reason, unmet)
    error = refusal.error()
    assert str(error) == "CONTENT_PREREQUISITE_UNMET" and error.unmet == list(unmet)
    assert rejection_guidance(error) == {"reason": reason, "next_step": next_step, "fix": None}


def test_prerequisite_refusal_is_absent_when_something_can_start_or_the_app_has_no_content(tmp_path):
    catalog = dingtalk_catalog(tmp_path, CLOCK, LUNCH)
    assert refusal_at(catalog, (12, 8, 55), "office") is None
    assert refusal_at(catalog, (12, 12, 30), None) is None
    assert refusal_at(catalog, (12, 13, 0), None).unmet == ("content_location", "content_time")
    moment = datetime(2026, 10, 17, 9)
    assert app_prerequisite_refusal(catalog, "meituan", None, moment) is None
    assert app_prerequisite_refusal(catalog, "bilibili", None, moment) is None


def test_prerequisite_refusal_names_every_matching_place_in_identity_order(tmp_path):
    """多个地点属于前提的类型时，按地点身份升序用 or 连接，人物从不看到类型原文；下一步取同一段地点。"""
    root = dingtalk_environment(tmp_path / "input")
    add_rows(root, "location.jsonl", "location_id", {"app_ids": [], "kind": "office", "location_id": "annex",
                                                      "name": "分部", "branch_label": "西区"})
    refusal = refusal_at(load_catalog(root / "catalog", 7), (12, 8, 10), "home")
    assert refusal.reason == ("Nothing in 钉钉 can start here now: "
                              "it requires presence at 分部（西区） or 练秋湖研发中心.")
    assert refusal.next_step == "Go to 分部（西区） or 练秋湖研发中心."
    assert "office" not in refusal.reason


def test_prerequisite_refusal_keeps_one_clause_for_identical_prerequisites(tmp_path):
    """文字相同的分句只留第一次出现的那个：同一系列各集前提相同，自然合成一句；次序照内容身份。"""
    hours = {"opens": weekday_hours(("12:00", "13:00"))}
    episodes = [app_content(f"dingtalk-week-{number}", f"周会第{number}段", hours,
                            series={"title": "周会", "episode": number}) for number in (1, 2)]
    late = app_content("dingtalk-a-late", "夜间内容", {"location_kinds": ["home"], "opens": weekday_hours(("22:00", "23:00"))})
    catalog = dingtalk_catalog(tmp_path, CLOCK, late, *episodes)
    refusal = refusal_at(catalog, (12, 10, 0), "park")
    assert refusal.reason == (
        "Nothing in 钉钉 can start here now: it requires presence at 家 during 22:00–23:00 today; "
        "or it requires presence at 练秋湖研发中心 during 08:00–10:00, 17:00–20:00 today; "
        "or it is available 12:00–13:00 today.")
    assert refusal.next_step == "Go to 家." and refusal.unmet == ("content_location", "content_time")


def test_prerequisite_code_has_a_default_reason_and_next_step_but_no_default_fix():
    code = "CONTENT_PREREQUISITE_UNMET"
    reason = "No content of this app can start at the current place and time."
    assert default_reason(code) == reason
    refusal = refusal_for_code(code)
    assert (refusal.reason, refusal.next_step, refusal.fix) == (reason, "Choose another action.", "")
    assert rejection_guidance(refusal.error()) == {"reason": reason, "next_step": "Choose another action.", "fix": None}


def test_interval_phrases_write_every_interval_with_an_en_dash():
    assert interval_phrases((("08:00", "10:00"), ("17:00", "24:00"))) == ["08:00–10:00", "17:00–24:00"]
    assert interval_phrases(()) == []
