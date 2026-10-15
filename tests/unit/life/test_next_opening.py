"""内容前提的时间短语与关着的 app 的开放时间：当天有时段写当天的时段，没有时写之后七天里的下次开放，七天里都没有也写明；
拒绝的分句与工具层内容 app 目标的 state 用同一个写法，对同一刻、同一批全都不合格的内容下结论
（docs/design/04-simulation-engine.md「出行、内容与查询」，06-agent.md「提示与工具」）。

目录是钉钉例子的夹具副本经生产加载器读出的真实目录；2026 年 10 月 12 日是周一，17 日是周六。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from tests.life.prerequisite_inputs import (
    CLOCK, CLOCK_HOURS, WORKDAYS, app_content, dingtalk_environment, weekday_hours,
)
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import OpeningHours
from worldmock.life.content_prerequisite import (
    NEXT_OPENING_DAYS, app_prerequisite_refusal, app_time_state, time_phrase,
)

HOURS = "08:00–10:00, 17:00–20:00"
NEXT_MONDAY = f"not available today (next available Mon 10-19 {HOURS})"
NEXT_MONDAY_NOON = "not available today (next available Mon 10-19 12:00–13:00)"
NEVER = "not available today or in the next seven days"
GO_TO_OFFICE = "Go to 练秋湖研发中心."
AT_OFFICE = "Nothing in 钉钉 can start here now: it requires presence at 练秋湖研发中心"
LUNCH = app_content("dingtalk-lunch", "午间打卡", {"opens": weekday_hours(("12:00", "13:00"))})


def _hours(weekly: dict, exceptions: dict | None = None) -> OpeningHours:
    """一份开放时段声明：每周时段与按日期替换的特殊时段。"""
    return OpeningHours.model_validate({"weekly": weekly, "exceptions": exceptions or {}})


def _closed(*days: int) -> dict:
    """10 月给定各日整日不开的特殊日期。"""
    return {f"2026-10-{day:02d}": [] for day in days}


#: 打卡的每周时段：周一至周五 08:00–10:00 与 17:00–20:00。
CLOCK_WEEKLY = {day: CLOCK_HOURS for day in WORKDAYS}


def _clock(*closed_days: int) -> dict:
    """打卡行：每周时段与地点同 CLOCK，再把 10 月给定各日整日关掉。"""
    return app_content("dingtalk-clock", "考勤打卡", {"location_kinds": ["office"],
                       "opens": {"weekly": CLOCK_WEEKLY, "exceptions": _closed(*closed_days)}})


#: 打卡加两集前提相同（周一至周五 12:00–13:00）的系列。
NOON_SERIES = tuple(app_content(f"dingtalk-noon-{number}", f"午间课程第{number}讲",
                                {"opens": weekday_hours(("12:00", "13:00"))},
                                series={"episode": number, "title": "午间课程"}) for number in (1, 2))


def _catalog(tmp_path, *contents):
    """钉钉例子的夹具副本（内容缺省只有打卡）的真实目录。"""
    return load_catalog(dingtalk_environment(tmp_path / "input", *contents) / "catalog", 7)


def _stay(catalog, stay):
    """停留地点；None 为在路上。"""
    return None if stay is None else catalog.locations[stay]


def _refusal(catalog, when, stay):
    """钉钉在 2026 年 10 月某日给定本地时刻与停留地点的前提拒绝。"""
    return app_prerequisite_refusal(catalog, "dingtalk", _stay(catalog, stay), datetime(2026, 10, *when))


def _state(catalog, when, stay, app_id="dingtalk"):
    """内容 app 在 2026 年 10 月某日给定本地时刻与停留地点的目标 state。"""
    return app_time_state(catalog, app_id, _stay(catalog, stay), datetime(2026, 10, *when))


@pytest.mark.parametrize("opens,day,expected", [
    (_hours(CLOCK_WEEKLY), date(2026, 10, 12), f"available {HOURS} today"),
    (_hours(CLOCK_WEEKLY), date(2026, 10, 17), NEXT_MONDAY),
    (_hours(CLOCK_WEEKLY, _closed(12)), date(2026, 10, 12), f"not available today (next available Tue 10-13 {HOURS})"),
    (_hours(CLOCK_WEEKLY, {"2026-10-18": [["09:00", "18:00"]]}), date(2026, 10, 17),
     "not available today (next available Sun 10-18 09:00–18:00)"),
    (_hours({"sat": [["00:00", "24:00"]], "sun": [["00:00", "24:00"]]}), date(2026, 10, 14),
     "not available today (next available Sat 10-17 00:00–24:00)"),
    (_hours(CLOCK_WEEKLY), date(2026, 1, 3), f"not available today (next available Mon 01-05 {HOURS})"),
    (_hours(CLOCK_WEEKLY, _closed(19, 20, 21, 22, 23)), date(2026, 10, 17), NEVER),
], ids=["hours-today", "saturday-to-monday", "monday-closed-to-tuesday", "sunday-opened-by-exception",
        "weekends-only", "padded-month-and-day", "none-within-seven-days"])
def test_time_phrase_names_todays_hours_or_the_next_opening(opens, day, expected):
    """当天有时段写当天的时段；当天没有时写之后最早有时段的一天，星期是固定的 Mon 至 Sun，月日补零；特殊日期整日替换
    （关掉或打开一天）照旧生效；七天里都没有时写明。"""
    assert time_phrase(opens, day) == expected


def test_next_opening_is_found_on_the_seventh_day_after_today():
    """找到第七天为止，含第七天：12 日至 16 日关掉的打卡在 12 日找到 19 日（第七天）；每周表为空、只有 30 日特殊日期的
    内容在 23 日找到 30 日（第七天），在 22 日找不到。"""
    assert NEXT_OPENING_DAYS == 7
    assert time_phrase(_hours(CLOCK_WEEKLY, _closed(12, 13, 14, 15, 16)), date(2026, 10, 12)) == NEXT_MONDAY
    only = _hours({}, {"2026-10-30": [["08:00", "09:00"]]})
    assert time_phrase(only, date(2026, 10, 23)) == "not available today (next available Fri 10-30 08:00–09:00)"
    assert time_phrase(only, date(2026, 10, 22)) == NEVER


@pytest.mark.parametrize("contents,when,stay,reason,next_step,unmet", [
    ((CLOCK,), (17, 9), "office", f"Nothing in 钉钉 can start now: it is {NEXT_MONDAY}.",
     "Choose another action.", ("content_time",)),
    ((CLOCK,), (17, 9), "home", f"{AT_OFFICE} and is {NEXT_MONDAY}.", GO_TO_OFFICE,
     ("content_location", "content_time")),
    ((CLOCK, LUNCH), (17, 9), "office",
     f"Nothing in 钉钉 can start now: it is {NEXT_MONDAY}; or it is {NEXT_MONDAY_NOON}.",
     "Choose another action.", ("content_time",)),
    ((CLOCK, LUNCH), (17, 9), "home", f"{AT_OFFICE} and is {NEXT_MONDAY}; or it is {NEXT_MONDAY_NOON}.", GO_TO_OFFICE,
     ("content_location", "content_time")),
    ((_clock(19, 20, 21, 22, 23),), (17, 9), "office", f"Nothing in 钉钉 can start now: it is {NEVER}.",
     "Choose another action.", ("content_time",)),
    ((_clock(19, 20, 21, 22, 23),), (17, 9), "home", f"{AT_OFFICE} and is {NEVER}.", GO_TO_OFFICE,
     ("content_location", "content_time")),
], ids=["saturday-office", "saturday-home", "clock-and-lunch-saturday-office", "clock-and-lunch-saturday-home",
        "none-within-seven-days-office", "none-within-seven-days-home"])
def test_prerequisite_refusal_names_the_next_opening(tmp_path, contents, when, stay, reason, next_step, unmet):
    """只差时间、两项都差且今天没有时段时分句带下次开放；两条内容写的下次开放不同，去重后都留下，以「; or 」相连；
    下一步与未满足的成员照旧。"""
    refusal = _refusal(_catalog(tmp_path, *contents), when, stay)
    assert (refusal.code, refusal.reason, refusal.next_step, refusal.unmet) == (
        "CONTENT_PREREQUISITE_UNMET", reason, next_step, unmet)


@pytest.mark.parametrize("contents,when,stay,expected", [
    ((CLOCK,), (12, 8, 10), "home", f"available {HOURS} today"),
    ((CLOCK,), (12, 10, 0), "office", f"available {HOURS} today"),
    ((CLOCK,), (12, 12, 30), "office", f"available {HOURS} today"),
    ((CLOCK,), (12, 12, 30), "home", f"available {HOURS} today"),
    ((CLOCK,), (12, 12, 30), None, f"available {HOURS} today"),
    ((CLOCK,), (17, 9), "office", NEXT_MONDAY),
    ((CLOCK,), (17, 9), "home", NEXT_MONDAY),
    ((CLOCK, LUNCH), (12, 8, 10), "home", f"available {HOURS} today; or available 12:00–13:00 today"),
    ((CLOCK, LUNCH), (17, 9), "office", f"{NEXT_MONDAY}; or {NEXT_MONDAY_NOON}"),
    ((_clock(19, 20, 21, 22, 23),), (17, 9), "office", NEVER),
    ((CLOCK, *NOON_SERIES), (17, 9), "office", f"{NEXT_MONDAY}; or {NEXT_MONDAY_NOON}"),
], ids=["monday-home-0810", "monday-office-1000", "monday-office-1230", "monday-home-1230", "monday-road-1230",
        "saturday-office", "saturday-home", "clock-and-lunch-monday-home-0810", "clock-and-lunch-saturday-office",
        "none-within-seven-days", "clock-and-series-saturday-office"])
def test_app_time_state_names_the_opening_of_every_time_gated_content(tmp_path, contents, when, stay, expected):
    """内容全都不合格时，写了 opens 的每条内容按内容身份升序各写它的时间短语，只差地点时也写当天的时段；文字相同的只留
    第一次出现的（系列两集同一句只出现一次），以「; or 」相连。"""
    assert _state(_catalog(tmp_path, *contents), when, stay) == expected


def test_app_time_state_is_null_while_some_content_can_start(tmp_path):
    """只有打卡时周一 08:55 在办公室；打卡加午间打卡时周一 12:30 在路上；哔哩哔哩（内容都没有前提）；美团（没有登记
    内容）：都没有开放时间可写。"""
    assert _state(_catalog(tmp_path / "clock"), (12, 8, 55), "office") is None
    both = _catalog(tmp_path / "both", CLOCK, LUNCH)
    assert _state(both, (12, 12, 30), None) is None
    assert _state(both, (17, 9), "office", "bilibili") is None
    assert not both.contents_by_app.get("meituan")
    assert _state(both, (17, 9), "office", "meituan") is None


def test_app_time_state_is_null_when_no_content_declares_opens(tmp_path):
    """钉钉只有一条只限办公室、不限时段的内容：周一 12:30 人在家，原因叫她去办公室，目标 state 没有时间可写。"""
    catalog = _catalog(tmp_path, app_content("dingtalk-badge", "门禁登记", {"location_kinds": ["office"]}))
    assert _refusal(catalog, (12, 12, 30), "home").reason == f"{AT_OFFICE}."
    assert _state(catalog, (12, 12, 30), "home") is None
