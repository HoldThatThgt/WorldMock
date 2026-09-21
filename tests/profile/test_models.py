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
        TimeWindow(start_hour=30.0, end_hour=30.5)
    assert (TimeWindow(start_hour=24.0, end_hour=25.0).start_hour) == 24.0


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


def test_time_anchors_wraparound_bedtime_normalized():
    """@brief 回绕归一：晚餐后的"凌晨一点"就寝 1.0-1.5 / 1.0-2.0 -> 次日 25.0-25.5 / 25.0-26.0。"""
    anchors = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
                          lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                          bedtime=make_window(1.0, 1.5))
    assert (anchors.bedtime.start_hour, anchors.bedtime.end_hour) == (25.0, 25.5)
    assert (anchors.wake.start_hour, anchors.dinner.start_hour) == (7.0, 18.5)
    late = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
                       lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                       bedtime=make_window(1.0, 2.0))
    assert (late.bedtime.start_hour, late.bedtime.end_hour) == (25.0, 26.0)


def test_time_anchors_wraparound_falls_into_card_values():
    """@brief 归一结果落进 PersonaCard 字段值（下游拿到的是归一后的）。"""
    card = make_card(time_anchors=TimeAnchors(
        wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
        lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
        bedtime=make_window(1.0, 2.0)))
    assert card.time_anchors.bedtime.start_hour == 25.0


def test_time_anchors_true_disorder_still_rejected():
    """@brief 同日乱序不可归一：8->7 的 7+24=31 越过 30 小时地平线，仍拒绝。"""
    with pytest.raises(ValidationError, match="out of order"):
        TimeAnchors(wake=make_window(8.0, 8.5), leave_home=make_window(7.0, 7.5),
                    lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                    bedtime=make_window(23.0, 24.0))
    with pytest.raises(ValidationError, match="out of order"):
        # 回绕读法 end 越过地平线（25.0-31.0）：不是回绕，拒绝
        TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
                    lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                    bedtime=make_window(1.0, 7.0))


def test_time_anchors_same_day_sequence_unchanged():
    """@brief 全同日有序序列零改写：原值原样通过（含相等起点的既有宽容）。"""
    anchors = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(7.0, 8.0),
                          lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                          bedtime=make_window(23.0, 24.0))
    starts = [anchors.wake.start_hour, anchors.leave_home.start_hour,
              anchors.lunch.start_hour, anchors.dinner.start_hour,
              anchors.bedtime.start_hour]
    assert starts == [7.0, 7.0, 12.0, 18.5, 23.0]


def test_time_anchors_multi_anchor_wraparound():
    """@brief 多锚点回绕：7,12,1.0,2.0,3.0 -> 7,12,25,26,27（逐级 +24 读作次日）。"""
    anchors = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(12.0, 12.5),
                          lunch=make_window(1.0, 1.5), dinner=make_window(2.0, 2.5),
                          bedtime=make_window(3.0, 3.5))
    starts = [anchors.wake.start_hour, anchors.leave_home.start_hour,
              anchors.lunch.start_hour, anchors.dinner.start_hour,
              anchors.bedtime.start_hour]
    assert starts == [7.0, 12.0, 25.0, 26.0, 27.0]
    assert (anchors.lunch.end_hour, anchors.bedtime.end_hour) == (25.5, 27.5)


def test_time_anchors_wraparound_from_dict_payload():
    """@brief 字典载荷路径（再水合）同样归一：bedtime 1.0-2.0 -> 25.0-26.0。"""
    anchors = TimeAnchors.model_validate({
        "wake": {"start_hour": 7.0, "end_hour": 7.5},
        "leave_home": {"start_hour": 8.0, "end_hour": 8.5},
        "lunch": {"start_hour": 12.0, "end_hour": 13.0},
        "dinner": {"start_hour": 18.5, "end_hour": 19.5},
        "bedtime": {"start_hour": 1.0, "end_hour": 2.0}})
    assert (anchors.bedtime.start_hour, anchors.bedtime.end_hour) == (25.0, 26.0)


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


def test_from_clock_range_rejects_null_and_non_string():
    """@brief null/空串/非字符串窗口是合同违规：类型明确的 ValueError，非 AttributeError。"""
    for bad in (None, "", 5, ["19:00", "20:00"], {"start": 19}):
        with pytest.raises(ValueError, match="HH:MM-HH:MM"):
            TimeWindow.from_clock_range(bad)


def test_parse_clock_rejects_null_and_non_string():
    """@brief null/非字符串时钟值同样给类型明确的 ValueError（时型路径同型防御）。"""
    for bad in (None, 7.5, ["07", "30"]):
        with pytest.raises(ValueError, match="HH:MM"):
            parse_clock(bad)
