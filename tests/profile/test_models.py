"""@brief 卡片模型合同正反例：字段完备、格点与边界、旧合同拒绝。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.profile.models import (
    BIG_FIVE_DIMENSIONS,
    LEGACY_TEMPLATE_FIELDS,
    SCHEMA_VERSION,
    BigFiveProfile,
    PersonaCard,
    Relationship,
    Tastes,
    TimeAnchors,
    TimeWindow,
    parse_clock,
    quantize,
)
from tests.profile.conftest import make_card, make_window


def test_card_field_completeness():
    """@brief 卡片字段完备性：十大块齐全，schema v2。"""
    card = make_card()
    payload = card.model_dump()
    assert set(payload) == {"identity", "relationships", "situation", "time_anchors",
                            "chronotype_midsleep", "sleep_need_hours", "habits",
                            "tastes", "coping"}
    assert set(payload["identity"]) == {"age", "occupation", "living_arrangement"}
    assert set(payload["situation"]) == {"busy_this_week", "cares_about"}
    assert set(payload["time_anchors"]) == {"wake", "leave_home", "lunch", "dinner", "bedtime"}
    assert set(payload["tastes"]) == {"content_genres", "food_preferences", "media"}
    assert all(set(item) == {"trigger", "response", "time_window"} for item in payload["habits"])
    assert all(set(item) == {"name_or_appellation", "role", "intimacy", "channels"}
               for item in payload["relationships"])


def test_card_round_trips_through_json():
    card = make_card()
    assert PersonaCard.model_validate_json(card.model_dump_json()) == card


def test_time_window_grid_and_span():
    with pytest.raises(ValidationError, match="off the 0.5-hour grid"):
        TimeWindow(start_hour=7.1, end_hour=8.0)
    with pytest.raises(ValidationError, match="start < end"):
        TimeWindow(start_hour=8.0, end_hour=8.0)
    with pytest.raises(ValidationError, match="exceeds 12h"):
        TimeWindow(start_hour=6.0, end_hour=20.0)
    with pytest.raises(ValidationError):
        TimeWindow(start_hour=24.0, end_hour=25.0)


def test_time_window_cross_midnight():
    """@brief 就寝窗口可跨午夜："23:00-01:00" -> 23.0-25.0。"""
    window = TimeWindow.from_clock_range("23:00-01:00")
    assert (window.start_hour, window.end_hour) == (23.0, 25.0)
    with pytest.raises(ValueError, match="HH:MM-HH:MM"):
        TimeWindow.from_clock_range("23:00")
    with pytest.raises(ValueError):
        TimeWindow.from_clock_range("25:00-26:00")


def test_parse_clock():
    assert parse_clock("07:30") == 7.5
    assert parse_clock("00:00") == 0.0
    with pytest.raises(ValueError):
        parse_clock("7点")
    with pytest.raises(ValueError):
        parse_clock("07:70")


def test_time_anchors_must_be_ordered():
    """@brief 锚点起点不倒挂：起床 < 出门 < 午餐 < 晚餐 < 就寝。"""
    with pytest.raises(ValidationError, match="out of order"):
        TimeAnchors(wake=make_window(10.0, 10.5), leave_home=make_window(8.0, 8.5),
                    lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                    bedtime=make_window(23.0, 24.0))


def test_sleep_need_and_chronotype_bounds():
    assert make_card().sleep_need_hours == 7.5
    for bad in (6.5, 9.25, 8.3):
        with pytest.raises(ValidationError):
            make_card(sleep_need_hours=bad)
    with pytest.raises(ValidationError):
        make_card(chronotype_midsleep=4.1)  # off the 0.25 grid
    assert quantize(8.3, 7.0, 9.0, 0.25) == 8.25


def test_intimacy_scale_and_relationship_caps():
    with pytest.raises(ValidationError):
        Relationship(name_or_appellation="母亲", role="家人", intimacy=31, channels=("电话",))
    too_many = tuple(
        Relationship(name_or_appellation=f"朋友{index}", role="老友", intimacy=10,
                     channels=("微信",)) for index in range(5))
    with pytest.raises(ValidationError):
        make_card(relationships=too_many)


def test_list_caps_enforced():
    habits = make_card().habits + make_card().habits + make_card().habits
    with pytest.raises(ValidationError):
        make_card(habits=habits)
    with pytest.raises(ValidationError):
        make_card(tastes=Tastes(content_genres=tuple(f"题材{index}" for index in range(7)),
                                food_preferences=(), media=()))


def test_tastes_dedup_keeps_first_occurrence():
    tastes = Tastes(content_genres=("悬疑", "悬疑"), food_preferences=(), media=())
    assert tastes.content_genres == ("悬疑",)


def test_big_five_bounds():
    assert BigFiveProfile(openness=1, conscientiousness=7, extraversion=4,
                          agreeableness=4, neuroticism=4)
    with pytest.raises(ValidationError):
        BigFiveProfile(openness=8, conscientiousness=4, extraversion=4,
                       agreeableness=4, neuroticism=4)


def test_legacy_contract_constants():
    """@brief 旧合同明确删除：四个模板字段、schema v1。"""
    assert LEGACY_TEMPLATE_FIELDS == frozenset({
        "occupation", "routine_preference", "consumption_preference", "risk_preference"})
    assert SCHEMA_VERSION == 2
    assert BIG_FIVE_DIMENSIONS == ("openness", "conscientiousness", "extraversion",
                                   "agreeableness", "neuroticism")


def test_old_v1_profile_payload_is_rejected():
    """@brief 旧九参数档案（schema_version=1）编译明确报错，不静默兼容。"""
    from worldmock.profile.models import CompiledPersona
    old_payload = {
        "schema_version": 1,
        "big_five": {"openness": 4, "conscientiousness": 4, "extraversion": 4,
                     "agreeableness": 4, "neuroticism": 4},
        "weights": {"money_fen": 0.2, "energy_kcal": 0.2, "sleep_debt_hours": 0.2,
                    "entertainment_need": 0.2, "goal_task": 0.2},
        "tau": 2.0, "beta_present_bias": 0.75,
        "loss_aversion": {"money_fen": 2.0, "energy_kcal": 1.5},
        "comfort": {"balance_fen": 15000, "energy_kcal": 1000},
        "aspiration": {"daily_effective_work_seconds": 18000},
        "emotion_sensitivity": 0.5,
        "attention_prior": {},
        "source": None,
    }
    with pytest.raises(ValidationError, match="schema_version"):
        CompiledPersona.model_validate(old_payload)
