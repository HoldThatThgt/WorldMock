"""显式工作声明的休息日期和半开班次边界；日期例外改了上不上班时的说明、键次序、国庆与调休的算例，以及明天。"""

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from worldmock.configuration.inputs.models import WorkSchedule
from worldmock.configuration.persona.work_schedule import (
    WEEKDAY_NAMES,
    day_arrangement,
    duty,
    duty_status,
    shift_on,
    tomorrow,
)

#: 上海时区（算例与样例环境的运行配置时区）。
_ZONE = "Asia/Shanghai"
#: 09:00–18:00、12:00–13:00 休息的班次（自当地零点起的分钟）。
_SHIFT = {"starts": 540, "ends": 1080, "breaks": [[720, 780]]}


def schedule():
    return WorkSchedule.model_validate({"workplace": "公司", "weekly": {
        "0": {"starts": 540, "ends": 1080, "breaks": [[720, 780]]}},
        "exceptions": {"2026-01-05": [], "2026-01-06": {"starts": 540, "ends": 1080, "breaks": [[720, 780]]}}})


def test_dates_override_weekly_shift_and_absent_declaration_has_no_duty():
    declared = schedule()
    assert shift_on(declared, date(2026, 1, 5)) is None
    assert shift_on(declared, date(2026, 1, 6)).starts == 540
    assert day_arrangement(declared, date(2026, 1, 5), {}) == {
        "day": "休息日", "hours": None, "place": None, "breaks": [], "note": "平常星期一上班，今天按工作安排休息"}
    assert day_arrangement(None, date(2026, 1, 5), {}) is None
    now = int(datetime(2026, 1, 5, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    assert duty(None, now, "Asia/Shanghai", {}) is None
    assert duty(declared, now, "Asia/Shanghai", {})["status"] is None


@pytest.mark.parametrize("hour,minute,status", [(8, 59, "未到上班时间"), (9, 0, "当班中"),
    (11, 59, "当班中"), (12, 0, "休息时段"), (12, 59, "休息时段"), (13, 0, "当班中"),
    (17, 59, "当班中"), (18, 0, "已下班")])
def test_shift_and_break_intervals_are_half_open(hour, minute, status):
    day, zone = date(2026, 1, 6), ZoneInfo("Asia/Shanghai")
    shift = shift_on(schedule(), day)
    now = int(datetime(2026, 1, 6, hour, minute, tzinfo=zone).timestamp())
    assert duty_status(shift, day, now, zone) == status


def _note_schedule():
    """说明判定用的工作安排：每周只声明星期一、星期三；例外覆盖六种情形。"""
    return WorkSchedule.model_validate({"workplace": "公司", "weekly": {"0": _SHIFT, "2": _SHIFT}, "exceptions": {
        "2026-01-05": [], "2026-01-06": _SHIFT, "2026-01-07": {"starts": 600, "ends": 1020, "breaks": []},
        "2026-01-08": [], "2026-01-11": _SHIFT, "2026-01-14": _SHIFT}})


@pytest.mark.parametrize("day,note", [
    (date(2026, 1, 5), "平常星期一上班，今天按工作安排休息"),
    (date(2026, 1, 6), "平常星期二休息，今天按工作安排上班"),
    (date(2026, 1, 7), None),
    (date(2026, 1, 8), None),
    (date(2026, 1, 11), "平常星期日休息，今天按工作安排上班"),
    (date(2026, 1, 12), None),
    (date(2026, 1, 13), None),
    (date(2026, 1, 14), None),
], ids=["monday-off", "tuesday-on", "wednesday-shorter", "thursday-off-as-usual", "sunday-on",
        "monday-as-usual", "tuesday-off-as-usual", "wednesday-same-shift"])
def test_note_is_written_only_when_an_exception_changes_work_or_rest(day, note):
    """说明只在日期例外把这一天的上班或休息改得与每周声明不同时写：本来上班的星期一休息、本来休息的星期二和星期日上班
    各写一句；同是上班只差时段、同是休息、与每周声明相同的例外，以及没有例外的日子，一律为 None。"""
    assert day_arrangement(_note_schedule(), day, {})["note"] == note


def test_arrangement_keys_end_with_the_note_and_duty_appends_status():
    """上班安排的键依次是 day、hours、place、breaks、note（工作日、休息日一样）；环境层 duty 在它之后再加 status。"""
    schedule = _note_schedule()
    for day in (date(2026, 1, 12), date(2026, 1, 5)):
        assert list(day_arrangement(schedule, day, {})) == ["day", "hours", "place", "breaks", "note"]
    now = int(datetime(2026, 1, 12, 10, tzinfo=ZoneInfo(_ZONE)).timestamp())
    assert list(duty(schedule, now, _ZONE, {})) == ["day", "hours", "place", "breaks", "note", "status"]


def _holiday_schedule():
    """国庆算例的工作安排：工作地点「公司」，星期一到星期五上班；10 月 1 日（星期四）休息，10 月 10 日（星期六）上班。"""
    return WorkSchedule.model_validate({"workplace": "公司", "weekly": {str(day): _SHIFT for day in range(5)},
                                        "exceptions": {"2026-10-01": [], "2026-10-10": _SHIFT}})


#: 算例里工作日的 duty（除说明与状态）。
_WORKDAY = {"day": "工作日", "hours": "09:00–18:00", "place": "公司", "breaks": ["12:00–13:00"]}


@pytest.mark.parametrize("moment,expected_duty,expected_tomorrow", [
    (datetime(2026, 9, 30, 21), {**_WORKDAY, "note": None, "status": "已下班"},
     {"date": "2026-10-01", "weekday": "星期四", "day": "休息日"}),
    (datetime(2026, 10, 1, 9), {"day": "休息日", "hours": None, "place": None, "breaks": [],
                                "note": "平常星期四上班，今天按工作安排休息", "status": None},
     {"date": "2026-10-02", "weekday": "星期五", "day": "工作日"}),
    (datetime(2026, 10, 9, 21), {**_WORKDAY, "note": None, "status": "已下班"},
     {"date": "2026-10-10", "weekday": "星期六", "day": "工作日"}),
    (datetime(2026, 10, 10, 0), {**_WORKDAY, "note": "平常星期六休息，今天按工作安排上班", "status": "未到上班时间"},
     {"date": "2026-10-11", "weekday": "星期日", "day": "休息日"}),
], ids=["wednesday-evening", "holiday-thursday", "friday-evening", "make-up-saturday"])
def test_holiday_and_make_up_day_read_as_the_worked_example(moment, expected_duty, expected_tomorrow):
    """国庆与调休的算例：四个时刻的 duty 与 tomorrow 逐键、按次序等于算例；10 月 10 日的上班安排按键名排序写成的 JSON
    恰是零点计划输入里的那一项。"""
    schedule = _holiday_schedule()
    now = int(moment.replace(tzinfo=ZoneInfo(_ZONE)).timestamp())
    assert list(duty(schedule, now, _ZONE, {}).items()) == list(expected_duty.items())
    assert list(tomorrow(schedule, now, _ZONE, {}).items()) == list(expected_tomorrow.items())
    assert json.dumps(day_arrangement(schedule, date(2026, 10, 10), {}), ensure_ascii=False, sort_keys=True) == (
        '{"breaks": ["12:00–13:00"], "day": "工作日", "hours": "09:00–18:00", '
        '"note": "平常星期六休息，今天按工作安排上班", "place": "公司"}')


@pytest.mark.parametrize("moment,declared,expected", [
    (datetime(2026, 9, 30, 23, 59, 59, tzinfo=ZoneInfo(_ZONE)), True,
     {"date": "2026-10-01", "weekday": "星期四", "day": "休息日"}),
    (datetime(2026, 10, 1, 0, 0, 0, tzinfo=ZoneInfo(_ZONE)), True,
     {"date": "2026-10-02", "weekday": "星期五", "day": "工作日"}),
    (datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc), True,
     {"date": "2026-10-02", "weekday": "星期五", "day": "工作日"}),
    (datetime(2026, 9, 30, 23, 59, 59, tzinfo=ZoneInfo(_ZONE)), False,
     {"date": "2026-10-01", "weekday": "星期四", "day": None}),
], ids=["last-second-of-the-day", "local-midnight", "utc-still-the-day-before", "no-work-schedule"])
def test_tomorrow_is_the_next_local_date_with_its_weekday_and_day_kind(moment, declared, expected):
    """明天是观察时刻所在本地日的下一天（按运行配置的时区算，与 UTC 的日期无关），带星期名与那天上不上班；
    没有工作安排时日期与星期照写，day 为 None。星期名恰是七个。"""
    schedule = _holiday_schedule() if declared else None
    assert tomorrow(schedule, int(moment.timestamp()), _ZONE, {}) == expected
    assert WEEKDAY_NAMES == ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


@pytest.mark.parametrize("zone,moment,expected", [
    ("America/New_York", datetime(2026, 3, 7, 23, 30), {"date": "2026-03-08", "weekday": "星期日", "day": "休息日"}),
    ("Europe/Berlin", datetime(2026, 3, 28, 23, 30), {"date": "2026-03-29", "weekday": "星期日", "day": "休息日"}),
], ids=["new-york-spring-forward", "berlin-spring-forward"])
def test_tomorrow_adds_a_calendar_day_across_a_daylight_saving_change(zone, moment, expected):
    """夏令时：加一天是在本地日期上加，不在时刻上加 86,400 秒。夏令时开始那一夜的前一晚 23:30，明天是下一个本地日
    （星期日、休息日）；那一天只有 23 小时，在时刻上加 86,400 秒会落到再下一天（星期一）。"""
    now = int(moment.replace(tzinfo=ZoneInfo(zone)).timestamp())
    assert tomorrow(_holiday_schedule(), now, zone, {}) == expected


def test_no_work_schedule_has_no_current_or_future_duty_edges():
    """未声明工作安排的人物没有班次起止，也不产生当前或未来唤醒边界。"""
    from types import SimpleNamespace
    from worldmock.simulation_engine.scheduling.clock.duty_edges import duty_edges, edges_at, edge_times_after

    inputs = SimpleNamespace(persona=SimpleNamespace(work_schedule=None), config=SimpleNamespace(timezone=_ZONE))
    now = int(datetime(2026, 1, 5, 9, tzinfo=ZoneInfo(_ZONE)).timestamp())
    assert list(duty_edges(inputs, 1, now)) == []
    assert edges_at(inputs, 1, now) == frozenset()
    assert edge_times_after(inputs, 1, now) == ()
