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
    settle_work_anchors,
)
from tests.profile.conftest import make_card, make_window

#: 不写下班与到家锚点（两键为 null）的构造参数。
NO_WORK = {"leave_work": None, "arrive_home": None}


def test_card_field_completeness():
    """@brief 卡片字段完备性：十大块齐全，时间锚点七个键（五个有序锚点与下班、到家），schema v3。"""
    card = make_card()
    payload = card.model_dump()
    assert set(payload) == {"identity", "relationships", "situation", "time_anchors",
                            "chronotype_midsleep", "sleep_need_hours", "habits",
                            "tastes", "coping"}
    assert set(payload["identity"]) == {"age", "occupation", "living_arrangement"}
    assert set(payload["situation"]) == {"busy_this_week", "cares_about"}
    assert set(payload["time_anchors"]) == {"wake", "leave_home", "lunch", "dinner", "bedtime",
                                            "leave_work", "arrive_home"}
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


@pytest.mark.parametrize(("clock", "expected"), [
    ("08:20-08:30", (8.0, 8.5)),
    ("08:00-08:10", (8.0, 8.5)),
    ("23:59-00:01", (23.5, 24.5)),
    ("00:05-00:10", (0.0, 0.5)),
])
def test_short_clock_window_survives_half_hour_quantization(clock, expected):
    """@brief 原始非零分钟窗口不能被半小时格点取整抹去；跨午夜也保留包含区间。"""
    window = TimeWindow.from_clock_range(clock)
    assert (window.start_hour, window.end_hour) == expected


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
                    bedtime=make_window(23.0, 24.0), **NO_WORK)


def test_time_anchors_wraparound_bedtime_normalized():
    """@brief 回绕归一：晚餐后的"凌晨一点"就寝 1.0-1.5 / 1.0-2.0 -> 次日 25.0-25.5 / 25.0-26.0。"""
    anchors = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
                          lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                          bedtime=make_window(1.0, 1.5), **NO_WORK)
    assert (anchors.bedtime.start_hour, anchors.bedtime.end_hour) == (25.0, 25.5)
    assert (anchors.wake.start_hour, anchors.dinner.start_hour) == (7.0, 18.5)
    late = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
                       lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                       bedtime=make_window(1.0, 2.0), **NO_WORK)
    assert (late.bedtime.start_hour, late.bedtime.end_hour) == (25.0, 26.0)


def test_time_anchors_wraparound_falls_into_card_values():
    """@brief 归一结果落进 PersonaCard 字段值（下游拿到的是归一后的）。"""
    card = make_card(time_anchors=TimeAnchors(
        wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
        lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
        bedtime=make_window(1.0, 2.0), **NO_WORK))
    assert card.time_anchors.bedtime.start_hour == 25.0


def test_time_anchors_true_disorder_still_rejected():
    """@brief 同日乱序不可归一：8->7 的 7+24=31 越过 30 小时地平线，仍拒绝。"""
    with pytest.raises(ValidationError, match="out of order"):
        TimeAnchors(wake=make_window(8.0, 8.5), leave_home=make_window(7.0, 7.5),
                    lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                    bedtime=make_window(23.0, 24.0), **NO_WORK)
    with pytest.raises(ValidationError, match="out of order"):
        # 回绕读法 end 越过地平线（25.0-31.0）：不是回绕，拒绝
        TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
                    lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                    bedtime=make_window(1.0, 7.0), **NO_WORK)


def test_time_anchors_same_day_sequence_unchanged():
    """@brief 全同日有序序列零改写：原值原样通过（含相等起点的既有宽容）。"""
    anchors = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(7.0, 8.0),
                          lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                          bedtime=make_window(23.0, 24.0), **NO_WORK)
    starts = [anchors.wake.start_hour, anchors.leave_home.start_hour,
              anchors.lunch.start_hour, anchors.dinner.start_hour,
              anchors.bedtime.start_hour]
    assert starts == [7.0, 7.0, 12.0, 18.5, 23.0]


def test_time_anchors_multi_anchor_wraparound():
    """@brief 多锚点回绕：7,12,1.0,2.0,3.0 -> 7,12,25,26,27（逐级 +24 读作次日）。"""
    anchors = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(12.0, 12.5),
                          lunch=make_window(1.0, 1.5), dinner=make_window(2.0, 2.5),
                          bedtime=make_window(3.0, 3.5), **NO_WORK)
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
        "bedtime": {"start_hour": 1.0, "end_hour": 2.0},
        "leave_work": None, "arrive_home": None})
    assert (anchors.bedtime.start_hour, anchors.bedtime.end_hour) == (25.0, 26.0)


@pytest.mark.parametrize("payload", [None, []])
def test_time_anchors_reject_non_object_payload(payload):
    """@brief 非对象输入由模型边界拒绝，不在回绕归一中泄漏类型异常。"""
    with pytest.raises(ValidationError) as error:
        TimeAnchors.model_validate(payload)
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [((), "model_type")]


@pytest.mark.parametrize("window, location, error_type", [
    ({"start_hour": 7.0}, ("wake", "end_hour"), "missing"),
    ({"start_hour": None, "end_hour": 7.5}, ("wake", "start_hour"), "float_type"),
    ({"start_hour": "morning", "end_hour": 7.5}, ("wake", "start_hour"), "float_parsing"),
    (None, ("wake",), "model_type"),
])
def test_time_anchors_preserve_field_errors_for_invalid_windows(window, location, error_type):
    """@brief 缺失时刻、非法数值与非法窗口形状保留精确字段错误，不被归一补齐。"""
    payload = make_card().time_anchors.model_dump()
    payload["wake"] = window
    with pytest.raises(ValidationError) as error:
        TimeAnchors.model_validate(payload)
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [(location, error_type)]


def test_time_anchors_reject_missing_window():
    """@brief 不完整锚点清单必须明确报告缺少窗口，不能补默认作息。"""
    payload = make_card().time_anchors.model_dump()
    del payload["lunch"]
    with pytest.raises(ValidationError) as error:
        TimeAnchors.model_validate(payload)
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [(("lunch",), "missing")]


def _anchors(**windows) -> TimeAnchors:
    """@brief 工厂卡片的五锚点（出门 8 点、就寝 23 点）加给定窗口；没给的下班、到家为 null。"""
    return TimeAnchors.model_validate({**make_card().time_anchors.model_dump(), **NO_WORK, **windows})


def test_work_anchors_are_required_nullable_and_outside_the_five_anchor_order():
    """@brief 下班与到家必填无缺省（缺键即拒）、null 合法；两者不进回绕归一的名字元组，也不进五锚点全序：带两个 null 时
    凌晨一点就寝照样归一为次日 25 点，下班、到家与午餐、晚餐不设先后。"""
    for name in NO_WORK:
        payload = make_card().time_anchors.model_dump()
        del payload[name]
        with pytest.raises(ValidationError) as error:
            TimeAnchors.model_validate(payload)
        assert [(item["loc"], item["type"]) for item in error.value.errors()] == [((name,), "missing")]
    assert (_anchors().leave_work, _anchors().arrive_home) == (None, None)
    late = TimeAnchors(wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
                       lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
                       bedtime=make_window(1.0, 2.0), **NO_WORK)
    assert (late.bedtime.start_hour, late.bedtime.end_hour) == (25.0, 26.0)
    assert (late.leave_work, late.arrive_home) == (None, None)
    before_lunch = _anchors(leave_work=make_window(11.0, 11.5), arrive_home=make_window(11.5, 12.0))
    after_dinner = _anchors(leave_work=make_window(20.0, 20.5), arrive_home=make_window(21.0, 21.5))
    assert (before_lunch.leave_work.start_hour, before_lunch.arrive_home.start_hour) == (11.0, 11.5)
    assert (after_dinner.leave_work.start_hour, after_dinner.arrive_home.start_hour) == (20.0, 21.0)


@pytest.mark.parametrize(("windows", "rejected"), [
    pytest.param({"leave_work": (7.0, 7.5)}, True, id="leave_work_before_leave_home"),
    pytest.param({"leave_work": (19.0, 19.5), "arrive_home": (18.5, 19.0)}, True,
                 id="arrive_home_before_leave_work"),
    pytest.param({"arrive_home": (7.5, 8.0)}, True, id="arrive_home_before_leave_home_without_leave_work"),
    pytest.param({"leave_work": (23.5, 24.0)}, True, id="leave_work_after_bedtime"),
    pytest.param({"arrive_home": (23.5, 24.0)}, True, id="arrive_home_after_bedtime"),
    pytest.param({"leave_work": (18.0, 18.5), "arrive_home": (1.0, 1.5)}, True, id="arrive_home_after_midnight"),
    pytest.param({"bedtime": (1.0, 2.0), "arrive_home": (24.0, 24.5)}, True,
                 id="arrive_home_past_midnight_before_the_normalised_bedtime"),
    pytest.param({"bedtime": (1.0, 2.0), "leave_work": (23.5, 24.5)}, True, id="leave_work_runs_past_midnight"),
    pytest.param({"arrive_home": (22.5, 24.5)}, True, id="arrive_home_runs_past_midnight_before_an_ordinary_bedtime"),
    pytest.param({"leave_work": (8.0, 8.5)}, False, id="leave_work_starts_with_leave_home"),
    pytest.param({"leave_work": (18.0, 18.5), "arrive_home": (18.0, 19.0)}, False,
                 id="arrive_home_starts_with_leave_work"),
    pytest.param({"arrive_home": (8.0, 8.5)}, False, id="arrive_home_starts_with_leave_home_without_leave_work"),
    pytest.param({"leave_work": (23.0, 23.5), "arrive_home": (23.0, 24.0)}, False, id="both_start_with_bedtime"),
    pytest.param({"bedtime": (1.0, 2.0), "leave_work": (23.5, 24.0)}, False, id="against_the_normalised_bedtime"),
])
def test_work_anchor_order_constraints_are_enforced(windows, rejected):
    """@brief 四条顺序约束（规格 4.1）：下班不早于出门；都非空时到家不早于下班；下班为 null 时到家不早于出门；任一不晚于
    归一后的就寝。各一例被拒且英文报错点名；相等起点合法；首版不支持跨午夜的下班与到家：01:00 到家按乱序拒绝，就寝回绕
    到次日时起点不晚于就寝的 24 点到家、跨过 24 点的下班窗口也被拒，终点恰为 24 点的窗口合法。"""
    given = {name: make_window(*span) for name, span in windows.items()}
    if rejected:
        with pytest.raises(ValidationError, match="work anchors out of order"):
            _anchors(**given)
        return
    anchors = _anchors(**given)
    assert {name: getattr(anchors, name) for name in given if name in NO_WORK} == {
        name: window for name, window in given.items() if name in NO_WORK}


def test_settle_work_anchors_demotes_in_the_fixed_order_with_one_text_each():
    """@brief 降级函数按固定顺序：先核下班（对出门与就寝），再核到家（对仍有效的下班，下班被置 null 后对出门，以及就寝）；
    每个被置 null 的锚点一条英文说明，合法的原样返回；就寝取归一后的窗口。先后合格而越过当日午夜的锚点（就寝回绕到次日
    时 24.5 点的到家、23.5–24.5 点的下班）同样被置 null，各一条「runs past midnight」说明；终点恰为 24 点的窗口合法。"""
    leave_home, bedtime = make_window(8.0, 8.5), make_window(23.0, 24.0)
    evening, later = make_window(18.0, 18.5), make_window(19.0, 19.5)
    assert settle_work_anchors(leave_home, bedtime, evening, later) == (evening, later, ())
    assert settle_work_anchors(leave_home, bedtime, make_window(7.0, 7.5), None) == (
        None, None, ("leave_work start 7 is outside leave_home 8..bedtime 23",))
    assert settle_work_anchors(leave_home, bedtime, later, evening) == (
        later, None, ("arrive_home start 18 is outside leave_work 19..bedtime 23",))
    assert settle_work_anchors(leave_home, bedtime, make_window(23.5, 24.0), evening) == (
        None, evening, ("leave_work start 23.5 is outside leave_home 8..bedtime 23",))
    assert settle_work_anchors(leave_home, bedtime, make_window(7.0, 7.5), make_window(23.5, 24.0)) == (
        None, None, ("leave_work start 7 is outside leave_home 8..bedtime 23",
                     "arrive_home start 23.5 is outside leave_home 8..bedtime 23"))
    late_bedtime, before_midnight = make_window(25.0, 26.0), make_window(23.0, 24.0)
    assert settle_work_anchors(leave_home, late_bedtime, None, make_window(24.5, 25.0)) == (
        None, None, ("arrive_home window 24.5..25 runs past midnight",))
    assert settle_work_anchors(leave_home, late_bedtime, make_window(23.5, 24.5), before_midnight) == (
        None, before_midnight, ("leave_work window 23.5..24.5 runs past midnight",))
    assert settle_work_anchors(leave_home, late_bedtime, evening, before_midnight) == (evening, before_midnight, ())


def test_the_midnight_bound_holds_for_any_bedtime_and_an_out_of_order_anchor_keeps_the_order_text():
    """@brief 「窗口终点不晚于当日 24 点」不取决于就寝是否回绕到次日（规格 4.1）：就寝 23 点时 22.5–24.5 点的到家、就寝恰为
    24 点时 23.5–24.5 点的下班都被置 null，各一条「runs past midnight」，卡片校验器以同一说明拒绝；既乱序又越过午夜的
    锚点只取一条，取起点越界的说明（就寝 23 点、23.5–24.5 点的下班）。"""
    leave_home, bedtime, evening = make_window(8.0, 8.5), make_window(23.0, 24.0), make_window(18.0, 18.5)
    assert settle_work_anchors(leave_home, bedtime, evening, make_window(22.5, 24.5)) == (
        evening, None, ("arrive_home window 22.5..24.5 runs past midnight",))
    with pytest.raises(ValidationError, match="work anchors out of order: arrive_home window 22.5..24.5 runs past"):
        _anchors(leave_work=evening, arrive_home=make_window(22.5, 24.5))
    assert settle_work_anchors(leave_home, make_window(24.0, 25.0), make_window(23.5, 24.5), None) == (
        None, None, ("leave_work window 23.5..24.5 runs past midnight",))
    assert settle_work_anchors(leave_home, bedtime, make_window(23.5, 24.5), None) == (
        None, None, ("leave_work start 23.5 is outside leave_home 8..bedtime 23",))
    with pytest.raises(ValidationError, match="leave_work start 23.5 is outside leave_home 8..bedtime 23"):
        _anchors(leave_work=make_window(23.5, 24.5))


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
    assert SCHEMA_VERSION == 3
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
