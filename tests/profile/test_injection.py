"""运行输入警告与场景作息覆盖的纯函数（docs/dev/SPEC-dayrun-fidelity.md 1.3、2.1、8.1）。

每个警告码各一例：口味、内容与作息三类取 WC 白领、BIZ 商务两次全天运行的真实卡片与场景声明
（tests/fixtures/dayrun_fidelity_inputs.json，从数据集只读复制的最小记录），配送路线与显示名取真实场景与
BIZ 的冻结地图事实；游戏权重条目只按显式输入判定，与权重怎样计算无关。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.profile.conftest import make_card, make_window
from worldmock.errors import IntegrityError
from worldmock.life.scenario import AgentConfig, load_scenario
from worldmock.profile.compile import compile_persona
from worldmock.profile.coverage import CoverageTargets, validate_coverage
from worldmock.profile.extractor import PersonaExtraction
from worldmock.profile.injection import (
    INPUT_WARNING_CODES,
    agreement_warnings,
    apply_scenario_rhythm,
    coverage_warnings,
    delivery_route_warnings,
    frozen_input_warnings,
    game_weight_warnings,
    location_name_warnings,
    suspicious_name_patterns,
    taste_warnings,
)
from worldmock.profile.models import BigFiveProfile, HabitSignature, PersonaCard, Tastes, TimeAnchors
from worldmock.profile.taste import compute_taste_weights

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "dayrun_fidelity_inputs.json"
_SCENARIO = Path("scenarios/white-collar-12-weeks.json")


def _run(name: str) -> dict:
    """真实运行的最小记录：white_collar（WC 白领林悦）或 business（BIZ 商务赵铭）。"""
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))[name]


def _card(run: dict) -> PersonaCard:
    """运行冻结的结构化卡片补回当下处境（结构化卡片去掉了它，不影响作息与口味）。"""
    return PersonaCard.model_validate({**run["persona_card_struct"],
                                       "situation": {"busy_this_week": "上班", "cares_about": "作息"}})


def _agent(agent_id: int = 1, **rhythm) -> AgentConfig:
    """最小场景人物声明：只写给出的作息字段，model_fields_set 恰为显式声明的字段。"""
    return AgentConfig.model_validate({
        "agent_id": agent_id, "name": "林悦", "trait_source_kind": "experiment_assumption",
        "home_location_id": "home", "initial_balance_fen": 0, "skill_levels": {}, "qualifications": [],
        "interaction_menu": None, **rhythm})


def _codes(warnings: list[dict]) -> list[str]:
    """警告码序列。"""
    return [item["code"] for item in warnings]


def test_the_warning_code_set_is_the_closed_ten() -> None:
    """闭集恰为规格 1.3 的八个码加 8.1 的两个码，按登记顺序。"""
    assert INPUT_WARNING_CODES == (
        "TASTE_GENRES_UNMATCHED", "CONTENT_WEIGHTS_ALL_ZERO", "CONTENT_EMPTY_WITH_MEDIA_TASTES",
        "GAMES_WITHOUT_TASTE_WEIGHTS", "COVERAGE_BELOW_THRESHOLD", "CARD_FIELD_OVERRIDDEN", "CARD_FIELD_CONFLICT",
        "PERSONA_AGREEMENT_LOW", "DELIVERY_ROUTE_UNAVAILABLE", "LOCATION_NAME_SUSPICIOUS")


def test_wc_platform_word_taste_matches_no_genre_and_all_weights_are_zero() -> None:
    """TASTE_GENRES_UNMATCHED 与 CONTENT_WEIGHTS_ALL_ZERO（WC 真实卡片）：口味「B站视频」是平台词，与 20 条内容的题材没有
    交集，按 compute_taste_weights 精确匹配的权重全为 0（与运行注入的权重相同）；正文列出卡片 content_genres、media 与
    场景内容最常见的题材，media 里的「B站」不参与匹配。"""
    run = _run("white_collar")
    card = _card(run)
    weights = compute_taste_weights(card.tastes.content_genres, run["contents"])
    assert weights == run["taste_weights"] and set(weights.values()) == {0.0}

    warnings = taste_warnings(run["agent_id"], card, run["contents"], weights)

    assert _codes(warnings) == ["TASTE_GENRES_UNMATCHED", "CONTENT_WEIGHTS_ALL_ZERO"]
    details = warnings[0]["details"]
    assert details == {"card_content_genres": ["B站视频"], "card_media": ["瑞幸咖啡App", "美团外卖", "B站", "盒马App"],
                       "top_content_genres": details["top_content_genres"], "content_items": 20}
    assert details["top_content_genres"][0] == "剧情" and "B站视频" not in details["top_content_genres"]
    for warning in warnings:
        assert warning["agent_id"] == 1
        assert all(word in warning["message"] for word in ("B站视频", "瑞幸咖啡App", "剧情"))
    assert "20 条内容" in warnings[1]["message"]


def test_matching_tastes_raise_no_taste_warning() -> None:
    """反例：口味与某条内容的题材相交、有正权重时两条口味警告都不出现；没有卡片时权重全零照样警告。"""
    run = _run("white_collar")
    card = make_card(tastes=Tastes(content_genres=("剧情",), food_preferences=(), media=()))
    weights = compute_taste_weights(card.tastes.content_genres, run["contents"])
    assert any(weights.values())
    assert taste_warnings(1, card, run["contents"], weights) == []
    assert _codes(taste_warnings(1, None, run["contents"], None)) == ["CONTENT_WEIGHTS_ALL_ZERO"]


def test_biz_has_no_content_but_the_card_and_habits_listen_and_scroll() -> None:
    """CONTENT_EMPTY_WITH_MEDIA_TASTES（BIZ 真实卡片）：场景没有内容，卡片口味题材与带「听」「刷」「看」的习惯列为证据；
    有内容时不出现；卡片与习惯都没有看、刷、听时也不出现。"""
    run = _run("business")
    card = _card(run)

    warnings = taste_warnings(run["agent_id"], card, [], None)

    assert _codes(warnings) == ["CONTENT_EMPTY_WITH_MEDIA_TASTES"]
    assert warnings[0]["agent_id"] == 5
    assert warnings[0]["details"] == {"content_genres": ["科技资讯", "商业播客"], "media": [],
                                      "habits": ["查看邮件和钉钉", "听科技资讯和商业播客", "刷抖音"]}
    quiet = card.model_copy(update={"tastes": Tastes(content_genres=(), food_preferences=(), media=("钉钉",)),
                                    "habits": ()})
    assert taste_warnings(5, quiet, [], None) == []
    assert taste_warnings(5, None, [], None) == []


def test_a_habit_whose_trigger_watches_or_scrolls_counts_as_a_media_habit() -> None:
    """「习惯里有看、刷、听」看触发与反应两处：反应里没有这三个字、只在触发里刷手机的习惯也算，场景没有内容时照样
    CONTENT_EMPTY_WITH_MEDIA_TASTES，证据列出该习惯的反应；两处都没有时不出现。"""
    habit = HabitSignature(trigger="睡前刷手机时", response="提醒自己早点睡", time_window=make_window(22.0, 23.0))
    card = make_card(habits=(habit,), tastes=Tastes(content_genres=(), food_preferences=("川菜",), media=("钉钉",)))

    warnings = taste_warnings(3, card, [], None)

    assert _codes(warnings) == ["CONTENT_EMPTY_WITH_MEDIA_TASTES"]
    assert warnings[0]["details"] == {"content_genres": [], "media": [], "habits": ["提醒自己早点睡"]}
    quiet = card.model_copy(update={"habits": (habit.model_copy(update={"trigger": "睡前"}),)})
    assert taste_warnings(3, quiet, [], None) == []


def test_games_without_weight_entries_are_reported_from_the_explicit_table() -> None:
    """GAMES_WITHOUT_TASTE_WEIGHTS：场景有游戏、权重表里没有它们的条目才警告；权重为 0 的条目也算已有。"""
    warnings = game_weight_warnings(1, ("textworld-1201", "textworld-1202"), {"movie-0001": 0.5})

    assert _codes(warnings) == ["GAMES_WITHOUT_TASTE_WEIGHTS"]
    assert warnings[0]["details"] == {"games": 2, "missing_game_ids": ["textworld-1201", "textworld-1202"]}
    covered = {"movie-0001": 0.5, "textworld-1201": 0.0, "textworld-1202": 0.25}
    assert game_weight_warnings(1, ("textworld-1201", "textworld-1202"), covered) == []
    assert game_weight_warnings(1, (), None) == []
    assert _codes(game_weight_warnings(1, ("textworld-1201",), None)) == ["GAMES_WITHOUT_TASTE_WEIGHTS"]


def test_coverage_below_threshold_reuses_the_validate_report() -> None:
    """COVERAGE_BELOW_THRESHOLD：直接复用 validate_coverage 的报告；达到阈值时不警告。"""
    card = make_card(habits=(HabitSignature(trigger="通勤", response="听播客两集", time_window=make_window(8.0, 9.0)),),
                     tastes=Tastes(content_genres=("悬疑", "西部片"), food_preferences=("川菜",), media=("播客",)))
    targets = CoverageTargets(habit_response_keywords=("慢跑",), content_genres=("悬疑",), food_kinds=("川菜",))
    report = validate_coverage(card, targets)
    assert report.passed is False

    warnings = coverage_warnings(3, report)

    assert _codes(warnings) == ["COVERAGE_BELOW_THRESHOLD"]
    assert warnings[0]["details"] == {
        "executable_ratio": 0.4, "threshold": 0.8, "total_items": 5, "covered_items": 2,
        "missing": [{"kind": "habit_response", "value": "听播客两集"}, {"kind": "content_genre", "value": "西部片"},
                    {"kind": "media", "value": "播客"}]}
    assert coverage_warnings(3, validate_coverage(make_card(habits=()), CoverageTargets(
        content_genres=("悬疑", "纪录片"), food_kinds=("川菜", "面食"), media_kinds=("长视频", "短视频")))) == []


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


def test_biz_undeliverable_supplies_are_grouped_by_store_and_destination() -> None:
    """DELIVERY_ROUTE_UNAVAILABLE（BIZ 冻结地图事实）：25 项晚餐供给到家、1 项咖啡到办公室的驾车路线是 unknown，
    按（门店, 目的地, 路线状态）合并成两条警告并列出供给；门店就在目的地的配送不需路线。"""
    run = _run("business")
    rows = run["undeliverable"]
    supplies = [SimpleNamespace(supply_id=row["supply_id"], location_id=row["location_id"],
                                fulfillment_modes=("pickup", "delivery"),
                                delivery_destination_ids=(row["destination_id"], row["location_id"]))
                for row in rows]
    routes = {(row["location_id"], row["destination_id"]) for row in rows}
    scenario = SimpleNamespace(
        supplies=supplies, locations=[SimpleNamespace(location_id=key, name=name)
                                      for key, name in run["location_names"].items()],
        routes=[SimpleNamespace(origin_id=origin, destination_id=target, mode="driving", status="unknown")
                for origin, target in sorted(routes)])

    warnings = delivery_route_warnings(scenario)

    assert _codes(warnings) == ["DELIVERY_ROUTE_UNAVAILABLE", "DELIVERY_ROUTE_UNAVAILABLE"]
    assert [(item["details"]["location_id"], item["details"]["destination_id"], item["details"]["route_status"],
             len(item["details"]["supply_ids"])) for item in warnings] == [
        ("coffee", "office", "unknown", 1), ("dinner", "home", "unknown", 25)]
    assert all(item["agent_id"] is None for item in warnings)
    assert "鹅庄台湾美食" in warnings[1]["message"] and "supply-dinner-meal-0001" in warnings[1]["message"]


def test_a_real_scenario_route_that_turns_unknown_is_reported_and_reachable_ones_are_not() -> None:
    """真实场景：配送路线全部可达时没有警告；把其中一条驾车路线改成 unknown、删掉另一条（missing）后分别列出。"""
    scenario, _ = load_scenario(_SCENARIO)
    assert delivery_route_warnings(scenario) == []
    supply = next(item for item in scenario.supplies if "delivery" in item.fulfillment_modes
                  and any(target != item.location_id for target in item.delivery_destination_ids))
    target = next(item for item in supply.delivery_destination_ids if item != supply.location_id)
    routes = [route.model_copy(update={"status": "unknown"})
              if (route.origin_id, route.destination_id, route.mode) == (supply.location_id, target, "driving")
              else route for route in scenario.routes]

    unknown = delivery_route_warnings(scenario.model_copy(update={"routes": tuple(routes)}))
    missing = delivery_route_warnings(scenario.model_copy(update={"routes": tuple(
        route for route in scenario.routes
        if (route.origin_id, route.destination_id, route.mode) != (supply.location_id, target, "driving"))}))

    assert {(item["details"]["location_id"], item["details"]["destination_id"], item["details"]["route_status"])
            for item in unknown} == {(supply.location_id, target, "unknown")}
    assert supply.supply_id in unknown[0]["details"]["supply_ids"]
    assert {item["details"]["route_status"] for item in missing} == {"missing"}


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
    warning = game_weight_warnings(1, ("g1",), None)[0]
    assert frozen_input_warnings([warning]) == [warning]
    assert frozen_input_warnings(None) is None and frozen_input_warnings([]) == []
    with pytest.raises(IntegrityError, match="closed warning shape"):
        frozen_input_warnings([{**warning, "code": "SOMETHING_ELSE"}])
    with pytest.raises(IntegrityError, match="closed warning shape"):
        frozen_input_warnings([{**warning, "extra": True}])
