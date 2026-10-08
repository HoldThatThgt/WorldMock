"""@brief 六个运行输入警告与场景作息覆盖的纯函数合同。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.profile.conftest import make_card, make_window
from worldmock.errors import IntegrityError
from worldmock.life.scenario import AgentConfig
from worldmock.life.world_setup import load_scenario
from worldmock.profile.compile import compile_persona
from worldmock.profile.coverage import CoverageTargets, validate_coverage
from worldmock.profile.extractor import PersonaExtraction
from worldmock.profile.injection import (
    INPUT_WARNING_CODES,
    RHYTHM_FIELDS,
    agreement_warnings,
    apply_scenario_rhythm,
    coverage_warnings,
    frozen_input_warnings,
    location_name_warnings,
    suspicious_name_patterns,
    content_empty_warnings,
)
from worldmock.profile.models import BigFiveProfile, HabitSignature, PersonaCard, Tastes, TimeAnchors

_FIXTURE = Path(__file__).resolve().parents[3] / 'tests' / "fixtures" / "dayrun_fidelity_inputs.json"
_SCENARIO = ENVIRONMENT_ROOT / "scenarios/single-person.json"


def _run(name: str) -> dict:
    """真实运行的最小记录：white_collar（WC 白领林悦）或 business（BIZ 商务赵铭）。"""
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))[name]


def _card(run: dict) -> PersonaCard:
    """运行冻结的结构化卡片补回当下处境（结构化卡片去掉了它，不影响作息与口味）；记录早于卡片结构 3，时间锚点同样补上
    为 null 的下班、到家两键（只读记录本身不改写）。"""
    struct = run["persona_card_struct"]
    return PersonaCard.model_validate({
        **struct, "time_anchors": {**struct["time_anchors"], "leave_work": None, "arrive_home": None},
        "situation": {"busy_this_week": "上班", "cares_about": "作息"}})


def _agent(agent_id: int = 1, **rhythm) -> AgentConfig:
    """最小场景人物声明：只写给出的作息字段，model_fields_set 恰为显式声明的字段。"""
    return AgentConfig.model_validate({
        "agent_id": agent_id, "name": "林悦", "trait_source_kind": "experiment_assumption",
        "home_location_id": "home", "initial_balance_fen": 0,
        "owns_car": False, "initial_driving_days": 0,
        "interaction_menu": None, "work_schedule": None, **rhythm})


def _codes(warnings: list[dict]) -> list[str]:
    """警告码序列。"""
    return [item["code"] for item in warnings]


def test_the_warning_code_set_is_the_closed_six() -> None:
    """@brief 闭集恰为冻结规格的六个码。"""
    assert INPUT_WARNING_CODES == (
        "CONTENT_EMPTY_WITH_MEDIA_TASTES", "COVERAGE_BELOW_THRESHOLD", "CARD_FIELD_OVERRIDDEN",
        "CARD_FIELD_CONFLICT", "PERSONA_AGREEMENT_LOW", "LOCATION_NAME_SUSPICIOUS")


def test_empty_content_warns_for_media_and_habits_only() -> None:
    """@brief 无应用绑定内容时，媒体与习惯发警告，散文题材不参与。"""
    empty = SimpleNamespace(contents={})
    habit = HabitSignature(trigger="听完以后", response="提醒自己早点睡", time_window=make_window(22.0, 24.0))
    card = make_card(habits=(habit,), tastes=Tastes(content_genres=("悬疑",), food_preferences=(),
                                                 media=("短视频", "钉钉")))
    warnings = content_empty_warnings(5, card, empty)
    assert _codes(warnings) == ["CONTENT_EMPTY_WITH_MEDIA_TASTES"]
    assert warnings[0]["details"] == {"media": ["短视频", "钉钉"], "habits": ["提醒自己早点睡"]}
    assert content_empty_warnings(5, card, SimpleNamespace(contents={"content": SimpleNamespace(app_ids=("douyin",))})) == []
    quiet = make_card(habits=(), tastes=Tastes(content_genres=("悬疑",), food_preferences=(), media=("钉钉",)))
    assert content_empty_warnings(5, quiet, empty)[0]["details"] == {"media": ["钉钉"], "habits": []}
    assert content_empty_warnings(5, None, empty) == []


def test_coverage_below_threshold_reuses_the_validate_report() -> None:
    """COVERAGE_BELOW_THRESHOLD：直接复用 validate_coverage 的报告；达到阈值时不警告。"""
    card = make_card(habits=(HabitSignature(trigger="通勤", response="听播客两集", time_window=make_window(8.0, 9.0)),),
                     tastes=Tastes(content_genres=("悬疑", "西部片"), food_preferences=("川菜",), media=("播客",)))
    targets = CoverageTargets(habit_response_keywords=("慢跑",), food_kinds=("川菜",))
    report = validate_coverage(card, targets)
    assert report.passed is False

    warnings = coverage_warnings(3, report)

    assert _codes(warnings) == ["COVERAGE_BELOW_THRESHOLD"]
    assert warnings[0]["details"] == {
        "executable_ratio": 0.3333, "threshold": 0.8, "total_items": 3, "covered_items": 1,
        "missing": [{"kind": "habit_response", "value": "听播客两集"},
                    {"kind": "media", "value": "播客"}]}
    assert coverage_warnings(3, validate_coverage(make_card(habits=()), CoverageTargets(
        food_kinds=("川菜", "面食"), action_names=("看长视频", "刷短视频")))) == []


@pytest.mark.parametrize("medium", ["音乐", "播客", "游戏", "任意媒体偏好"])
def test_every_declared_media_preference_warns_when_no_content_is_bound(medium):
    """@brief 口味声明本身就是媒体偏好，不由标签或动词词表筛掉。"""
    card = make_card(habits=(), tastes=Tastes(content_genres=(), food_preferences=(), media=(medium,)))
    warnings = content_empty_warnings(1, card, SimpleNamespace(contents={}))
    assert _codes(warnings) == ["CONTENT_EMPTY_WITH_MEDIA_TASTES"]
    assert warnings[0]["details"] == {"media": [medium], "habits": []}


def test_content_empty_warning_does_not_infer_media_from_other_prose():
    """@brief 没有媒体偏好或媒体动词的习惯时，散文题材与日常习惯不触发缺内容警告。"""
    habit = HabitSignature(trigger="睡前", response="安静休息", time_window=make_window(22.0, 24.0))
    card = make_card(habits=(habit,), tastes=Tastes(content_genres=("任意题材",), food_preferences=(), media=()))
    assert content_empty_warnings(1, card, SimpleNamespace(contents={})) == []


def test_persona_compiler_agreement_warnings_are_passed_through() -> None:
    """PERSONA_AGREEMENT_LOW：画像编译（真实 compile_persona，打桩三次一致性结果）把低于 2/3 的字段写进 warnings，
    逐条透传为输入警告；没有编译警告时不出现。"""
    extraction = PersonaExtraction(card=make_card(), big_five=BigFiveProfile(
        openness=5, conscientiousness=4, extraversion=6, agreeableness=4, neuroticism=3),
        runs=3, agreement=0.8, per_field={"habits": 1 / 3, "coping": 1.0}, warning=True,
        model="deepseek-flash", prompt_version="card-v1")
    compiled = compile_persona("陈曦，32岁，产品经理。" * 10, extraction=extraction)

    warnings = agreement_warnings(1, compiled.warnings)

    assert _codes(warnings) == ["PERSONA_AGREEMENT_LOW"]
    assert warnings[0]["details"] == {"compiler_warning": "field_agreement_below_two_thirds: ['habits']"}
    assert agreement_warnings(1, ()) == []


def test_wc_scenario_rhythm_overrides_the_extracted_card_values() -> None:
    """CARD_FIELD_OVERRIDDEN（WC 真实卡片与场景声明）：睡眠中点 4.0（映射缺省补齐）被场景 4.75 覆盖，午餐、晚餐窗口取场景值；
    就寝窗口与睡眠需要两边相同，不算覆盖；未声明的起床、出门仍是卡片值。"""
    run = _run("white_collar")
    card = _card(run)
    agent = _agent(**run["scenario_rhythm"])

    result = apply_scenario_rhythm(card, agent)

    assert [(item["code"], item["details"]["field"]) for item in result.warnings] == [
        ("CARD_FIELD_OVERRIDDEN", "chronotype_midsleep"), ("CARD_FIELD_OVERRIDDEN", "lunch_window"),
        ("CARD_FIELD_OVERRIDDEN", "dinner_window")]
    assert result.warnings[0]["details"] == {"field": "chronotype_midsleep", "card_value": 4.0,
                                             "scenario_value": 4.75, "applied_value": 4.75}
    assert result.card.chronotype_midsleep == 4.75 and result.card.sleep_need_hours == 8.0
    anchors = result.card.time_anchors
    assert (anchors.lunch.start_hour, anchors.lunch.end_hour) == (12.0, 12.5)
    assert (anchors.dinner.start_hour, anchors.dinner.end_hour) == (18.5, 19.5)
    assert (anchors.wake, anchors.leave_home, anchors.bedtime) == (
        card.time_anchors.wake, card.time_anchors.leave_home, card.time_anchors.bedtime)


def test_only_explicitly_declared_fields_are_overridden() -> None:
    """只覆盖显式声明的字段：场景只声明睡眠中点时其余字段仍是卡片值，AgentConfig 的缺省（7.5 小时、04:00）不进卡片；
    经 dump/validate 往返的对象把全部字段记为已设，所以覆盖必须在原始加载对象上做。"""
    card = make_card(sleep_need_hours=8.0, chronotype_midsleep=3.0)
    agent = _agent(chronotype_midsleep=4.75)
    assert agent.sleep_need_hours == 7.5

    result = apply_scenario_rhythm(card, agent)

    assert result.card == card.model_copy(update={"chronotype_midsleep": 4.75})
    assert [item["details"]["field"] for item in result.warnings] == ["chronotype_midsleep"]
    round_trip = AgentConfig.model_validate(agent.model_dump())
    assert apply_scenario_rhythm(card, round_trip).card.sleep_need_hours == 7.5
    assert apply_scenario_rhythm(card, _agent()).card is card
    assert apply_scenario_rhythm(card, _agent(lunch_window=None)).card is card


def test_values_are_quantised_to_the_card_grid() -> None:
    """按卡片格点量化：中点与睡眠需要 0.25 小时（23.9 点写成 0 点），窗口 0.5 小时；量化后与卡片相同的字段不算覆盖。"""
    card = make_card()
    agent = _agent(chronotype_midsleep=23.9, sleep_need_hours=7.6, lunch_window=(12.2, 13.8),
                   dinner_window=(18.6, 19.6))

    result = apply_scenario_rhythm(card, agent)

    assert result.card.chronotype_midsleep == 0.0 and result.card.sleep_need_hours == 7.5
    lunch = result.card.time_anchors.lunch
    assert (lunch.start_hour, lunch.end_hour) == (12.0, 14.0)
    assert result.card.time_anchors.dinner == card.time_anchors.dinner
    assert [item["details"]["field"] for item in result.warnings] == ["chronotype_midsleep", "lunch_window"]
    assert result.warnings[1]["details"]["scenario_value"] == [12.2, 13.8]
    assert result.warnings[1]["details"]["applied_value"] == [12.0, 14.0]


def test_out_of_order_window_keeps_the_card_value_and_records_a_conflict() -> None:
    """CARD_FIELD_CONFLICT：场景午餐 06:00 落在卡片出门 08:00 之前，锚点乱序，午餐保留卡片值并记冲突；同时声明的晚餐照常覆盖。"""
    card = make_card()
    agent = _agent(lunch_window=(6.0, 6.5), dinner_window=(19.0, 20.0))

    result = apply_scenario_rhythm(card, agent)

    assert result.card.time_anchors.lunch == card.time_anchors.lunch
    dinner = result.card.time_anchors.dinner
    assert (dinner.start_hour, dinner.end_hour) == (19.0, 20.0)
    assert [(item["code"], item["details"]["field"]) for item in result.warnings] == [
        ("CARD_FIELD_CONFLICT", "lunch_window"), ("CARD_FIELD_OVERRIDDEN", "dinner_window")]
    assert result.warnings[0]["details"] == {"field": "lunch_window", "card_value": [12.0, 13.0],
                                             "scenario_value": [6.0, 6.5], "reason": "anchor_order"}
    TimeAnchors.model_validate(result.card.time_anchors.model_dump())


def test_windows_that_only_fit_together_are_both_applied_and_the_result_is_a_fixed_point() -> None:
    """按锚点顺序反复落槽直到不动点：午餐 19:00 单独放不进（晚于卡片晚餐 18:30），先落晚餐 20:00 后午餐也能落下；
    对结果再覆盖一次卡片不变（恢复时据此核对一致）。"""
    card = make_card()
    agent = _agent(lunch_window=(19.0, 20.0), dinner_window=(20.0, 21.0))

    result = apply_scenario_rhythm(card, agent)

    anchors = result.card.time_anchors
    assert (anchors.lunch.start_hour, anchors.dinner.start_hour) == (19.0, 20.0)
    assert _codes(list(result.warnings)) == ["CARD_FIELD_OVERRIDDEN", "CARD_FIELD_OVERRIDDEN"]
    again = apply_scenario_rhythm(result.card, agent)
    assert again.card == result.card and again.warnings == ()
    conflicted = apply_scenario_rhythm(card, _agent(lunch_window=(6.0, 6.5)))
    assert apply_scenario_rhythm(conflicted.card, _agent(lunch_window=(6.0, 6.5))).card == conflicted.card


def test_unrepresentable_and_after_midnight_windows() -> None:
    """量化后退化的窗口（12:06–12:12 → 12:00–12:00）写不成卡片窗口，记冲突并保留卡片值；凌晨的就寝窗口经回绕归一读作次日。"""
    card = make_card()

    narrow = apply_scenario_rhythm(card, _agent(lunch_window=(12.1, 12.2)))
    late = apply_scenario_rhythm(card, _agent(bedtime_window=(0.5, 5.0)))

    assert narrow.card is card and narrow.warnings[0]["details"]["reason"] == "window_off_grid"
    bedtime = late.card.time_anchors.bedtime
    assert (bedtime.start_hour, bedtime.end_hour) == (24.5, 29.0)
    assert late.warnings[0]["details"]["applied_value"] == [24.5, 29.0]


def _work_card(leave_work: tuple[float, float] | None, arrive_home: tuple[float, float] | None) -> PersonaCard:
    """工厂卡片（出门 08:00、晚餐 18:30、就寝 23:00）换上给定的下班、到家窗口（None 为 null）。"""
    card = make_card()
    anchors = {name: getattr(card.time_anchors, name) for name in TimeAnchors.model_fields}
    anchors.update(leave_work=None if leave_work is None else make_window(*leave_work),
                   arrive_home=None if arrive_home is None else make_window(*arrive_home))
    return card.model_copy(update={"time_anchors": TimeAnchors.model_validate(anchors)})


def _slots(card: PersonaCard) -> tuple[list[float] | None, list[float] | None]:
    """卡片下班、到家槽的 [start, end]（null 为 None）。"""
    return tuple(None if window is None else [window.start_hour, window.end_hour]
                 for window in (card.time_anchors.leave_work, card.time_anchors.arrive_home))


def test_a_null_card_slot_takes_the_scenario_work_window_and_is_reported_as_overridden() -> None:
    """场景作息覆盖的字段为七项、窗口槽为五个（docs/design/06-agent.md#agent-场景作息覆盖 ）：卡片下班、到家槽为 null 时场景窗口
    照常落下，各记一条 CARD_FIELD_OVERRIDDEN，details.field 取新字段名、card_value 为 null，正文把卡片值写成 null；
    卡片槽非空时同样覆盖并带原值；警告码闭集仍为 10，警告照常通过冻结前的闭集校验。"""
    assert RHYTHM_FIELDS == ("chronotype_midsleep", "sleep_need_hours", "lunch_window", "leave_work_window",
                             "arrive_home_window", "dinner_window", "bedtime_window")
    card = make_card()
    agent = _agent(leave_work_window=(18.0, 18.5), arrive_home_window=(19.0, 19.5))

    result = apply_scenario_rhythm(card, agent)

    assert _slots(result.card) == ([18.0, 18.5], [19.0, 19.5])
    assert result.card == _work_card((18.0, 18.5), (19.0, 19.5))
    assert [item["details"] for item in result.warnings] == [
        {"field": "leave_work_window", "card_value": None, "scenario_value": [18.0, 18.5],
         "applied_value": [18.0, 18.5]},
        {"field": "arrive_home_window", "card_value": None, "scenario_value": [19.0, 19.5],
         "applied_value": [19.0, 19.5]}]
    assert _codes(list(result.warnings)) == ["CARD_FIELD_OVERRIDDEN", "CARD_FIELD_OVERRIDDEN"]
    assert result.warnings[0]["message"] == (
        "Scenario leave_work_window overrides the extracted card value: card null, "
        "scenario [18, 18.5], applied [18, 18.5].")
    assert frozen_input_warnings(result.warnings) == list(result.warnings) and len(INPUT_WARNING_CODES) == 6
    earlier = apply_scenario_rhythm(_work_card((18.5, 19.0), None), _agent(leave_work_window=(17.5, 18.0)))
    assert _slots(earlier.card) == ([17.5, 18.0], None)
    assert earlier.warnings[0]["details"]["card_value"] == [18.5, 19.0]


def test_a_scenario_window_violating_the_work_anchor_order_keeps_the_card_value_as_a_conflict() -> None:
    """覆盖与冲突规则不变并一体适用于新槽（规格 4.2，不降级卡片锚点）：场景下班早于卡片出门时 null 槽保持 null 并记冲突、
    card_value 为 null；场景到家早于卡片下班时保留卡片到家；场景就寝早于卡片到家时就寝保留卡片值、冲突落在
    bedtime_window 上，卡片到家原样不动。三例的卡片都不变，冲突正文写出新的顺序约束。"""
    card = make_card()
    early_leave = apply_scenario_rhythm(card, _agent(leave_work_window=(6.0, 6.5)))
    worked = _work_card((18.0, 18.5), (19.0, 19.5))
    early_home = apply_scenario_rhythm(worked, _agent(arrive_home_window=(17.0, 17.5)))
    late_home = _work_card(None, (21.0, 21.5))
    early_bed = apply_scenario_rhythm(late_home, _agent(bedtime_window=(20.0, 21.0)))

    assert early_leave.card is card and early_home.card is worked and early_bed.card is late_home
    assert [item["details"] for item in early_leave.warnings] == [
        {"field": "leave_work_window", "card_value": None, "scenario_value": [6.0, 6.5], "reason": "anchor_order"}]
    assert early_leave.warnings[0]["message"].endswith("keeping card value null.")
    assert "work departure cannot precede departure" in early_leave.warnings[0]["message"]
    assert "home arrival cannot precede either departure" in early_leave.warnings[0]["message"]
    assert [item["details"] for item in early_home.warnings] == [
        {"field": "arrive_home_window", "card_value": [19.0, 19.5], "scenario_value": [17.0, 17.5],
         "reason": "anchor_order"}]
    assert [item["details"] for item in early_bed.warnings] == [
        {"field": "bedtime_window", "card_value": [23.0, 24.0], "scenario_value": [20.0, 21.0],
         "reason": "anchor_order"}]
    assert _slots(early_bed.card) == (None, [21.0, 21.5])
    assert {item["code"] for item in (*early_leave.warnings, *early_home.warnings, *early_bed.warnings)} == {
        "CARD_FIELD_CONFLICT"}


def test_work_windows_declared_together_land_together_and_the_result_is_a_fixed_point() -> None:
    """按落槽顺序反复落槽直到不动点：场景下班 19:00 单独放不进（晚于卡片到家 18:30），先落到家 19:30 后下班也能落下，
    两条覆盖；对结果再覆盖一次卡片不变、没有警告。有冲突时同样是不动点：下班冲突、到家照常落下，再覆盖一次卡片不变。"""
    card = _work_card((18.0, 18.5), (18.5, 19.0))
    agent = _agent(leave_work_window=(19.0, 19.5), arrive_home_window=(19.5, 20.0))

    result = apply_scenario_rhythm(card, agent)

    assert _slots(result.card) == ([19.0, 19.5], [19.5, 20.0])
    assert [(item["code"], item["details"]["field"]) for item in result.warnings] == [
        ("CARD_FIELD_OVERRIDDEN", "leave_work_window"), ("CARD_FIELD_OVERRIDDEN", "arrive_home_window")]
    again = apply_scenario_rhythm(result.card, agent)
    assert again.card == result.card and again.warnings == ()
    mixed = _agent(leave_work_window=(6.0, 6.5), arrive_home_window=(19.5, 20.0))
    conflicted = apply_scenario_rhythm(make_card(), mixed)
    assert _slots(conflicted.card) == (None, [19.5, 20.0])
    assert _codes(list(conflicted.warnings)) == ["CARD_FIELD_CONFLICT", "CARD_FIELD_OVERRIDDEN"]
    repeated = apply_scenario_rhythm(conflicted.card, mixed)
    assert repeated.card == conflicted.card and _codes(list(repeated.warnings)) == ["CARD_FIELD_CONFLICT"]


def test_an_unrepresentable_work_window_is_a_conflict_on_a_null_slot() -> None:
    """量化后退化的下班窗口（18:06–18:12 → 18:00–18:00）写不成卡片窗口：null 槽保持 null，记 window_off_grid 冲突、
    card_value 为 null，正文写 null；同时声明的到家照常落下。"""
    card = make_card()

    result = apply_scenario_rhythm(card, _agent(leave_work_window=(18.1, 18.2), arrive_home_window=(19.0, 19.5)))

    assert _slots(result.card) == (None, [19.0, 19.5])
    assert result.warnings[0]["details"] == {"field": "leave_work_window", "card_value": None,
                                             "scenario_value": [18.1, 18.2], "reason": "window_off_grid"}
    assert result.warnings[0]["message"].endswith("keeping card value null.")
    assert [(item["code"], item["details"]["field"]) for item in result.warnings[1:]] == [
        ("CARD_FIELD_OVERRIDDEN", "arrive_home_window")]


def test_windows_that_each_fit_alone_but_not_together_land_in_slot_order() -> None:
    """落槽顺序为午餐、下班、到家、晚餐、就寝（接口合同「场景作息覆盖」，规格 4.2 一体适用于新槽）：两个声明的窗口各自都放得
    进卡片、合起来放不下时，顺序在前的槽落下，在后的槽保留卡片值并记冲突——下班 22:00 与就寝 21:00 同时声明，下班落下、
    冲突记在 bedtime_window；午餐 17:00 与晚餐 13:00 同时声明，午餐落下、冲突记在 dinner_window。结果是不动点。"""
    card = make_card()
    agent = _agent(leave_work_window=(22.0, 22.5), bedtime_window=(21.0, 22.0))
    for alone in (_agent(leave_work_window=(22.0, 22.5)), _agent(bedtime_window=(21.0, 22.0)),
                  _agent(lunch_window=(17.0, 18.0)), _agent(dinner_window=(13.0, 14.0))):
        assert _codes(list(apply_scenario_rhythm(card, alone).warnings)) == ["CARD_FIELD_OVERRIDDEN"]

    result = apply_scenario_rhythm(card, agent)
    meals = apply_scenario_rhythm(card, _agent(lunch_window=(17.0, 18.0), dinner_window=(13.0, 14.0)))

    assert _slots(result.card) == ([22.0, 22.5], None)
    assert result.card.time_anchors.bedtime == card.time_anchors.bedtime
    assert [(item["code"], item["details"]["field"]) for item in result.warnings] == [
        ("CARD_FIELD_CONFLICT", "bedtime_window"), ("CARD_FIELD_OVERRIDDEN", "leave_work_window")]
    assert result.warnings[0]["details"] == {"field": "bedtime_window", "card_value": [23.0, 24.0],
                                             "scenario_value": [21.0, 22.0], "reason": "anchor_order"}
    assert apply_scenario_rhythm(result.card, agent).card == result.card
    assert (meals.card.time_anchors.lunch, meals.card.time_anchors.dinner) == (make_window(17.0, 18.0),
                                                                               card.time_anchors.dinner)
    assert [(item["code"], item["details"]["field"]) for item in meals.warnings] == [
        ("CARD_FIELD_CONFLICT", "dinner_window"), ("CARD_FIELD_OVERRIDDEN", "lunch_window")]


def test_a_conflicting_scenario_leave_work_keeps_the_non_null_card_slot_and_the_card_arrive_home() -> None:
    """冲突时该槽保留卡片值、覆盖不降级任何卡片锚点（规格 4.2）在卡片下班槽非空时同样成立：场景只声明下班 19:00，晚于卡片的
    到家 18:30——卡片对象原样返回（下班仍是 18:00–18:30、到家仍是 18:30–19:00），恰好一条 CARD_FIELD_CONFLICT，
    card_value 为卡片的下班，正文写「保留卡片值 [18, 18.5]」，没有覆盖；场景下班早于卡片出门时同样保留非空的卡片下班。"""
    card = _work_card((18.0, 18.5), (18.5, 19.0))

    result = apply_scenario_rhythm(card, _agent(leave_work_window=(19.0, 19.5)))

    assert result.card is card and _slots(result.card) == ([18.0, 18.5], [18.5, 19.0])
    assert [(item["code"], item["details"]) for item in result.warnings] == [
        ("CARD_FIELD_CONFLICT", {"field": "leave_work_window", "card_value": [18.0, 18.5],
                                 "scenario_value": [19.0, 19.5], "reason": "anchor_order"})]
    assert result.warnings[0]["message"].endswith("keeping card value [18, 18.5].")
    worked = _work_card((18.0, 18.5), (19.0, 19.5))
    early = apply_scenario_rhythm(worked, _agent(leave_work_window=(6.0, 6.5)))
    assert early.card is worked and _slots(early.card) == ([18.0, 18.5], [19.0, 19.5])
    assert [(item["code"], item["details"]["card_value"]) for item in early.warnings] == [
        ("CARD_FIELD_CONFLICT", [18.0, 18.5])]


def test_a_scenario_bedtime_before_the_card_leave_work_keeps_the_card_leave_work() -> None:
    """覆盖不降级任何卡片锚点（规格 4.2）对卡片的下班同样成立：场景就寝 20:00 早于卡片的下班 21:00（到家为 null）——冲突记在
    bedtime_window 上，就寝保留卡片值 23:00–24:00，卡片的下班原样不动，卡片对象原样返回，没有覆盖。"""
    card = _work_card((21.0, 21.5), None)

    result = apply_scenario_rhythm(card, _agent(bedtime_window=(20.0, 21.0)))

    assert result.card is card and _slots(result.card) == ([21.0, 21.5], None)
    assert result.card.time_anchors.bedtime == make_window(23.0, 24.0)
    assert [(item["code"], item["details"]) for item in result.warnings] == [
        ("CARD_FIELD_CONFLICT", {"field": "bedtime_window", "card_value": [23.0, 24.0],
                                 "scenario_value": [20.0, 21.0], "reason": "anchor_order"})]


def test_suspicious_display_names_are_reported_only_for_coordinates_and_identifiers() -> None:
    """LOCATION_NAME_SUSPICIOUS：BIZ 的 11 个带经纬度的显示名逐个警告；WC 的「7-Eleven（日常采买）」这类名字与普通店名不算；
    「（31.2, 121.5）」「星巴克-sim-01」「poi-node-1」按规格的两种形态识别。"""
    business = _run("business")["location_names"]
    locations = [SimpleNamespace(location_id=key, name=name) for key, name in business.items()]

    warnings = location_name_warnings(SimpleNamespace(locations=locations))

    assert len(warnings) == 11 and set(_codes(warnings)) == {"LOCATION_NAME_SUSPICIOUS"}
    assert all("东经" in item["details"]["name"] and item["details"]["patterns"] == ["coordinates"]
               for item in warnings)
    white_collar = _run("white_collar")["location_names"]
    assert location_name_warnings(SimpleNamespace(locations=[
        SimpleNamespace(location_id=key, name=name) for key, name in white_collar.items()])) == []
    assert suspicious_name_patterns("便利店（31.2, 121.5）") == ["coordinates"]
    assert suspicious_name_patterns("星巴克-sim-01") == ["identifier"]
    assert suspicious_name_patterns("客户园区 task-client-a") == ["identifier"]
    assert suspicious_name_patterns("poi-node-1（东经121.5°）") == ["coordinates", "identifier"]
    for plain in ("7-Eleven（日常采买）", "星巴克（浦东南路 855 号）", "e-bike 租赁点", "增庆菜馆（炒菜.拌菜.盖饭）"):
        assert suspicious_name_patterns(plain) == []


def test_frozen_warnings_keep_the_closed_shape_and_null_means_not_computed() -> None:
    """冻结前校验：闭集形状原样通过，None 保持 None（引擎直驱没有计算警告），未知码或多余键即内部合同违约。"""
    warning = agreement_warnings(1, ("field_agreement_below_two_thirds",))[0]
    assert frozen_input_warnings([warning]) == [warning]
    assert frozen_input_warnings(None) is None and frozen_input_warnings([]) == []
    with pytest.raises(IntegrityError, match="closed warning shape"):
        frozen_input_warnings([{**warning, "code": "SOMETHING_ELSE"}])
    with pytest.raises(IntegrityError, match="closed warning shape"):
        frozen_input_warnings([{**warning, "extra": True}])


@pytest.mark.parametrize("code", ["TASTE_GENRES_UNMATCHED", "CONTENT_WEIGHTS_ALL_ZERO",
                                  "GAMES_WITHOUT_TASTE_WEIGHTS", "DELIVERY_ROUTE_UNAVAILABLE"])
def test_retired_warning_codes_are_rejected_at_manifest_boundary(code):
    """@brief 真实清单冻结边界拒绝四个退役警告码，不能随旧画像或旧路线复活。"""
    warning = agreement_warnings(1, ("compiler note",))[0]
    with pytest.raises(IntegrityError, match="closed warning shape"):
        frozen_input_warnings([{**warning, "code": code}])
