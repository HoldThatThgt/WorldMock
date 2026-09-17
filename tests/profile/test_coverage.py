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
        content_genres=("悬疑", "纪录片"),
        food_kinds=("川菜", "面食"),
        media_kinds=("长视频", "短视频"),
    )


def test_full_coverage_passes():
    report = validate_coverage(make_card(), _targets())
    assert report.passed is True
    assert report.executable_ratio == 1.0
    assert report.missing == ()
    assert report.total_items == 2 + 2 + 2 + 2  # habits + genres + foods + media
    require_coverage(report)  # 不抛


def test_below_threshold_rejected_with_missing_items():
    """@brief 低于 80% 校验失败并列出缺失项：画像写了播客而世界里没有播客。"""
    card = make_card(tastes=Tastes(content_genres=("悬疑", "武侠"),
                                   food_preferences=("川菜", "意式"),
                                   media=("播客", "广播剧")))
    report = validate_coverage(card, _targets())
    assert report.total_items == 8 and report.covered_items == 4
    assert report.executable_ratio == pytest.approx(0.5, abs=1e-4)
    assert report.passed is False
    assert [item.value for item in report.missing] == ["武侠", "意式", "播客", "广播剧"]
    with pytest.raises(PersonaCoverageError, match="PERSONA_COVERAGE_FAILED.*播客"):
        require_coverage(report)


def test_threshold_boundary_passes_at_exactly_80_percent():
    """@brief 恰好 80%（4/5）通过门禁：低于阈值才失败。"""
    card = make_card(
        habits=(HabitSignature(trigger="下班到家", response="公园慢跑半小时",
                               time_window=make_window(19.0, 20.0)),),
        tastes=Tastes(content_genres=("悬疑",), food_preferences=("川菜",),
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
    assert report.covered_items == 1 + 2 + 2 + 2
    card_miss = make_card(habits=(HabitSignature(
        trigger="下班到家", response="冥想二十分钟",
        time_window=make_window(19.0, 20.0)),))
    report_miss = validate_coverage(card_miss, _targets())
    assert report_miss.missing[0].kind == "habit_response"
    assert report_miss.missing[0].value == "冥想二十分钟"


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
