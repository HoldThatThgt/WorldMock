"""@brief 覆盖率校验：persona_coverage.json 结构、80% 门禁与缺失项清单。"""

from __future__ import annotations

import pytest

from worldmock.profile.coverage import (
    COVERAGE_THRESHOLD,
    CoverageTargets,
    PersonaCoverageError,
    require_coverage,
    validate_coverage,
)
from worldmock.profile.models import HabitSignature, Tastes
from tests.profile.conftest import make_card, make_window


def _targets():
    return CoverageTargets(
        habit_response_keywords=("慢跑", "读几页小说"),
        food_kinds=("川菜", "面食"),
        action_names=("看长视频", "刷短视频"),
    )


def test_full_coverage_passes():
    report = validate_coverage(make_card(), _targets())
    assert report.passed is True
    assert report.executable_ratio == 1.0
    assert report.missing == ()
    assert report.total_items == 2 + 2 + 2  # 习惯、食品与媒介
    require_coverage(report)  # 不抛


def test_below_threshold_rejected_with_missing_items():
    """@brief 低于 80% 校验失败并列出缺失项：画像写了播客而世界里没有播客。"""
    card = make_card(tastes=Tastes(content_genres=("悬疑", "武侠"),
                                   food_preferences=("川菜", "意式"),
                                   media=("播客", "广播剧")))
    report = validate_coverage(card, _targets())
    assert report.total_items == 6 and report.covered_items == 3
    assert report.executable_ratio == pytest.approx(0.5, abs=1e-4)
    assert report.passed is False
    assert [item.value for item in report.missing] == ["意式", "播客", "广播剧"]
    with pytest.raises(PersonaCoverageError, match="PERSONA_COVERAGE_FAILED.*播客"):
        require_coverage(report)


def test_threshold_boundary_passes_at_exactly_80_percent():
    """@brief 恰好 80%（4/5）通过门禁：低于阈值才失败。"""
    card = make_card(
        habits=(HabitSignature(trigger="下班到家", response="公园慢跑半小时",
                               time_window=make_window(19.0, 20.0)),),
        tastes=Tastes(content_genres=("悬疑",), food_preferences=("川菜", "面食"),
                      media=("长视频", "未知媒介")))
    report = validate_coverage(card, _targets())
    assert report.total_items == 5 and report.covered_items == 4
    assert report.executable_ratio == COVERAGE_THRESHOLD
    assert report.passed is True


def test_habit_response_keyword_substring_matching():
    """@brief 习惯 response 命中：关键词是 response 的子串（"慢跑" in "公园慢跑半小时"）。"""
    card = make_card(habits=(HabitSignature(
        trigger="下班到家", response="公园慢跑半小时",
        time_window=make_window(19.0, 20.0)),))
    report = validate_coverage(card, _targets())
    assert report.covered_items == 1 + 2 + 2
    card_miss = make_card(habits=(HabitSignature(
        trigger="下班到家", response="冥想二十分钟",
        time_window=make_window(19.0, 20.0)),))
    report_miss = validate_coverage(card_miss, _targets())
    assert report_miss.missing[0].kind == "habit_response"
    assert report_miss.missing[0].value == "冥想二十分钟"


def test_app_name_substrings_no_longer_count_as_coverage_hits():
    """App 名子串不算覆盖命中（docs/design/04-simulation-engine.md ）：WC 白领卡片的口味「B站视频」与习惯「刷B站」
    不因应用名「B站」算覆盖（旧规则按双向子串判为命中，覆盖率 0.92 而口味权重全零）；媒介「B站」与已核验的应用名
    精确相等，仍算命中。"""
    card = make_card(
        habits=(HabitSignature(trigger="睡前", response="刷B站", time_window=make_window(22.0, 24.0)),),
        tastes=Tastes(content_genres=("B站视频",), food_preferences=("川菜",), media=("B站",)))
    targets = CoverageTargets(habit_response_keywords=("慢跑",), food_kinds=("川菜",),
                              app_names=("B站", "哔哩哔哩"))

    report = validate_coverage(card, targets)

    assert [(item.kind, item.value) for item in report.missing] == [
        ("habit_response", "刷B站")]
    assert report.covered_items == 2 and report.executable_ratio == 0.6667 and report.passed is False
    assert validate_coverage(make_card(habits=(), tastes=Tastes(
        content_genres=(), food_preferences=(), media=("哔哩哔哩",))), targets).passed is True


def test_empty_card_is_vacuously_covered():
    """@brief 无习惯且无口味：无可校验项，按缺省通过（两列表均可为空）。"""
    card = make_card(habits=(), tastes=Tastes(content_genres=(), food_preferences=(),
                                              media=()))
    report = validate_coverage(card, _targets())
    assert report.total_items == 0 and report.passed is True
    assert report.executable_ratio == 1.0


def test_report_serializes_to_coverage_json_shape():
    """@brief persona_coverage.json 结构：schema/ratio/passed/missing 字段齐全。"""
    payload = validate_coverage(make_card(), _targets()).model_dump()
    assert set(payload) == {"schema_version", "total_items", "covered_items",
                            "executable_ratio", "passed", "missing"}


def test_content_genres_stay_prose_and_do_not_enter_coverage():
    """@brief 卡片题材原样保留，但覆盖目标严格拒绝旧题材字段。"""
    from pydantic import ValidationError

    card = make_card(habits=(), tastes=Tastes(content_genres=("未知散文题材",), food_preferences=(), media=()))
    report = validate_coverage(card, CoverageTargets())
    assert card.tastes.content_genres == ("未知散文题材",)
    assert report.total_items == 0 and report.passed and report.missing == ()
    with pytest.raises(ValidationError, match="content_genres"):
        CoverageTargets.model_validate({"content_genres": ["旧题材"]})


@pytest.mark.parametrize("medium", ["推荐", "下班后刷推荐"])
def test_media_preferences_match_registered_action_names_in_both_directions(medium):
    """@brief 媒体偏好按真实动作名称双向子串匹配，不要求属于分类词表。"""
    card = make_card(habits=(), tastes=Tastes(content_genres=(), food_preferences=(), media=(medium,)))
    report = validate_coverage(card, CoverageTargets(action_names=("刷推荐",)))
    assert report.total_items == report.covered_items == 1
    assert report.passed and report.missing == ()


def test_media_app_names_require_the_full_registered_name():
    """@brief 媒体条目以完整应用名命中，应用名子串不能绕过真实动作覆盖。"""
    card = make_card(habits=(), tastes=Tastes(content_genres=(), food_preferences=(),
                                           media=("哔哩哔哩", "哔哩哔哩视频")))
    report = validate_coverage(card, CoverageTargets(app_names=("哔哩哔哩",)))
    assert report.covered_items == 1
    assert [(item.kind, item.value) for item in report.missing] == [("media", "哔哩哔哩视频")]


def test_coverage_targets_reject_the_retired_content_classification_field():
    """@brief 退役的媒体分类目标不能继续作为配置入口。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="media_kinds"):
        CoverageTargets.model_validate({"media_kinds": ["短视频"]})
