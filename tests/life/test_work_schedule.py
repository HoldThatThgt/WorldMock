"""场景声明的工作安排按日期与世界时钟求值（docs/dev/SPEC-errands-workday.md 4.3、4.4）。

纯函数用例：班次按本地日期取（日期例外优先于每周声明，不从星期推断），起止与休息时段按当地日历时间构造的半开区间，
上班安排与此刻状态只由声明与时钟决定。声明经真实的场景模型 WorkSchedule 校验后求值。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.life.work_schedule_fixtures import STANDARD_SHIFT, declare
from worldmock.life.scenario import WorkSchedule, WorkShift
from worldmock.life.work_schedule import (DAY_REST, DAY_WORK, DUTY_DAYS, DUTY_STATUSES, STATUS_AFTER, STATUS_BEFORE,
                                          STATUS_BREAK, STATUS_ON_DUTY, break_spans, day_arrangement, duty,
                                          duty_status, shift_on, shift_span)

_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_ZONE = "Asia/Shanghai"
_PLACES = {"office": "张江高科技园区办公室（冻结场景锚点）", "home": "张江居所（冻结场景锚点）"}


def _schedule(**changes) -> WorkSchedule:
    """十二周场景人物 1 的工作安排（与场景日历一致，可按 declare 的关键字覆盖），经场景模型校验。"""
    document = json.loads(_FORMAL.read_text(encoding="utf-8"))
    declare(**changes)(document)
    return WorkSchedule.model_validate(document["agents"][0]["work_schedule"])


def _at(text: str) -> int:
    """上海当地时刻（ISO 写法，不带偏移）的时间戳。"""
    return int(datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(_ZONE)).timestamp())


def test_shift_on_prefers_exceptions_over_the_weekly_table() -> None:
    """某天的班次先看日期例外、再看每周声明（4.3）：例外为 null 的周一不当班，例外写了班次的周六当班；其余日子照每周
    声明，周末不当班；不从星期推断——把班次写在周六、周一写 null 时，周六当班而周一不当班。日历特殊日由例外给出。"""
    schedule = _schedule(exceptions={"2026-01-05": None, "2026-01-10": {**STANDARD_SHIFT, "breaks": []}})
    monday = schedule.weekly["0"]
    assert monday == WorkShift(starts=540, ends=1080, breaks=((720, 810),))
    assert shift_on(schedule, date(2026, 1, 12)) == monday
    assert shift_on(schedule, date(2026, 1, 5)) is None
    assert shift_on(schedule, date(2026, 1, 10)) == WorkShift(starts=540, ends=1080, breaks=())
    assert [shift_on(schedule, date(2026, 1, day)) for day in (17, 18)] == [None, None]
    assert shift_on(schedule, date(2026, 2, 14)) == monday
    assert shift_on(schedule, date(2026, 2, 16)) is None

    flipped = _schedule(weekly={"0": None, "5": STANDARD_SHIFT})
    assert shift_on(flipped, date(2026, 1, 12)) is None
    assert shift_on(flipped, date(2026, 1, 17)) == monday
    assert shift_on(flipped, date(2026, 1, 13)) == monday


def test_shift_and_break_spans_are_local_calendar_times() -> None:
    """班次与休息时段的起止是当地零点加分钟数的日历时间（4.3，与营业日历同一构造）：上海 09:00–18:00 与午休 12:00–13:30；
    1440 分钟落在次日零点；在夏令时切换日，零点到 24:00 的班次只有 23 个小时，不按固定秒数推算。"""
    shift = _schedule().weekly["0"]
    zone = ZoneInfo(_ZONE)
    day = date(2026, 1, 5)
    assert shift_span(shift, day, zone) == (_at("2026-01-05T09:00:00"), _at("2026-01-05T18:00:00"))
    assert break_spans(shift, day, zone) == ((_at("2026-01-05T12:00:00"), _at("2026-01-05T13:30:00")),)
    whole = WorkShift(starts=0, ends=1440, breaks=((0, 30), (1410, 1440)))
    assert shift_span(whole, day, zone) == (_at("2026-01-05T00:00:00"), _at("2026-01-06T00:00:00"))
    berlin = ZoneInfo("Europe/Berlin")
    start, end = shift_span(whole, date(2026, 3, 29), berlin)
    assert end - start == 23 * 3600
    assert break_spans(whole, date(2026, 3, 29), berlin)[1] == (end - 1800, end)


def test_duty_values_come_from_the_declaration_and_the_clock_alone() -> None:
    """上班安排与此刻状态只由声明与时钟决定（4.4）：没有工作安排为 None；休息日是固定的休息日对象、状态为 None；当班日写
    班次与休息时段（EN DASH，24:00 写法）和工作地点的显示名，不含内部身份；状态在班前、班内、休息时段、班后之间按半开
    区间切换。两组枚举按规格的顺序给出。"""
    schedule = _schedule()
    assert DUTY_DAYS == (DAY_WORK, DAY_REST) == ("工作日", "休息日")
    assert DUTY_STATUSES == (STATUS_BEFORE, STATUS_ON_DUTY, STATUS_BREAK, STATUS_AFTER) == (
        "未到上班时间", "当班中", "休息时段", "已下班")
    assert day_arrangement(None, date(2026, 1, 5), _PLACES) is None
    assert duty(None, _at("2026-01-05T10:00:00"), _ZONE, _PLACES) is None
    rest = {"day": "休息日", "hours": None, "place": None, "breaks": []}
    assert day_arrangement(schedule, date(2026, 1, 10), _PLACES) == rest
    assert duty(schedule, _at("2026-01-10T10:00:00"), _ZONE, _PLACES) == {**rest, "status": None}
    work = {"day": "工作日", "hours": "09:00–18:00", "place": _PLACES["office"], "breaks": ["12:00–13:30"]}
    assert day_arrangement(schedule, date(2026, 1, 5), _PLACES) == work
    statuses = {moment: duty(schedule, _at(f"2026-01-05T{moment}"), _ZONE, _PLACES)["status"]
                for moment in ("08:59:59", "09:00:00", "11:59:59", "12:00:00", "13:29:59", "13:30:00", "17:59:59",
                               "18:00:00")}
    assert statuses == {"08:59:59": "未到上班时间", "09:00:00": "当班中", "11:59:59": "当班中", "12:00:00": "休息时段",
                        "13:29:59": "休息时段", "13:30:00": "当班中", "17:59:59": "当班中", "18:00:00": "已下班"}
    assert duty_status(schedule.weekly["0"], date(2026, 1, 5), _at("2026-01-05T12:30:00"), ZoneInfo(_ZONE)) == "休息时段"
    late = _schedule(shift={"starts": 1200, "ends": 1440, "breaks": []})
    assert day_arrangement(late, date(2026, 1, 5), _PLACES)["hours"] == "20:00–24:00"
    assert "office" not in json.dumps(duty(schedule, _at("2026-01-05T10:00:00"), _ZONE, _PLACES), ensure_ascii=False)
