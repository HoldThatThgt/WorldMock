"""显式工作声明的休息日期和半开班次边界。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from worldmock.input_models import WorkSchedule
from worldmock.life.work_schedule import day_arrangement, duty, duty_status, shift_on


def schedule():
    return WorkSchedule.model_validate({"workplace": "公司", "weekly": {
        "0": {"starts": 540, "ends": 1080, "breaks": [[720, 780]]}},
        "exceptions": {"2026-01-05": [], "2026-01-06": {"starts": 540, "ends": 1080, "breaks": [[720, 780]]}}})


def test_dates_override_weekly_shift_and_absent_declaration_has_no_duty():
    declared = schedule()
    assert shift_on(declared, date(2026, 1, 5)) is None
    assert shift_on(declared, date(2026, 1, 6)).starts == 540
    assert day_arrangement(declared, date(2026, 1, 5), {}) == {
        "day": "休息日", "hours": None, "place": None, "breaks": []}
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
