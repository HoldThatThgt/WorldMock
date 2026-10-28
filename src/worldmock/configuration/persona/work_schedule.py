"""@brief 场景声明的工作安排按日期与世界时钟求值（docs/design/08-configuration.md「人物声明」）。

工作安排是人物配置上必填可空的 `work_schedule`：工作地点、每周七天的班次（周一为 "0"）、按 ISO 日期覆盖的例外与班次内
的休息时段，时刻为自当地零点起的分钟，区间一律半开；None 表示当日不当班。这里的函数都是纯函数，只读场景声明与时钟：
不从星期或任务分布推断班次，不看人物在不在场，也不带时区字段（复用场景唯一的 timezone）。
环境层 `duty` 与 `tomorrow`、零点计划输入「上班安排」都经这里取同一天的班次。

本模块只依赖标准库，按属性读取 input_models.WorkSchedule / WorkShift（weekly、exceptions、workplace、starts、
ends、breaks），因此加载检查、环境层与度量都可以导入它而不成环。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

#: 「上班安排」的两种日子（环境层 duty 与零点计划输入的 day）。
DAY_WORK = "工作日"
DAY_REST = "休息日"
DUTY_DAYS = (DAY_WORK, DAY_REST)

#: 中文星期名，下标是 date.weekday()（零点计划输入的「生成时刻」、上班安排的 note 与环境层 tomorrow 共用）。
WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

#: 当班日此刻的状态闭集（规格 4.4 的顺序）：只由时钟与班次决定，不含人物在不在场。
STATUS_BEFORE = "未到上班时间"
STATUS_ON_DUTY = "当班中"
STATUS_BREAK = "休息时段"
STATUS_AFTER = "已下班"
DUTY_STATUSES = (STATUS_BEFORE, STATUS_ON_DUTY, STATUS_BREAK, STATUS_AFTER)


def shift_on(schedule: Any, day: date) -> Any:
    """@brief 某个本地日期的班次：日期例外优先于每周声明；None 表示当日不当班（不从星期或任务推断）。"""
    key = day.isoformat()
    if key in schedule.exceptions:
        return schedule.exceptions[key] or None
    return schedule.weekly.get(str(day.weekday()))


def shift_span(shift: Any, day: date, zone: ZoneInfo) -> tuple[int, int]:
    """@brief 班次的起止时刻（按当地日历时间构造的半开区间）。"""
    midnight = datetime.combine(day, datetime.min.time(), zone)
    return (int((midnight + timedelta(minutes=shift.starts)).timestamp()),
            int((midnight + timedelta(minutes=shift.ends)).timestamp()))


def break_spans(shift: Any, day: date, zone: ZoneInfo) -> tuple[tuple[int, int], ...]:
    """@brief 班次内各休息时段的起止时刻（半开区间，按声明顺序）。"""
    midnight = datetime.combine(day, datetime.min.time(), zone)
    return tuple((int((midnight + timedelta(minutes=begin)).timestamp()),
                  int((midnight + timedelta(minutes=finish)).timestamp())) for begin, finish in shift.breaks)


def duty_status(shift: Any, day: date, now: int, zone: ZoneInfo) -> str:
    """@brief 当班日此刻的状态：只由时钟与班次决定，不看人物在不在场。"""
    start, end = shift_span(shift, day, zone)
    if now < start:
        return STATUS_BEFORE
    if now >= end:
        return STATUS_AFTER
    if any(begin <= now < finish for begin, finish in break_spans(shift, day, zone)):
        return STATUS_BREAK
    return STATUS_ON_DUTY


def day_arrangement(schedule: Any, day: date, places: Mapping[str, str]) -> dict[str, Any] | None:
    """@brief 某个本地日的上班安排（零点计划输入的形状，不含 status）；没有工作安排为 None。

    五个键依次是 day、hours、place、breaks、note：休息日的 hours、place 为 None、breaks 为空；note 是日期例外
    把这一天的上班或休息改得与每周声明不同时的一句说明，其余为 None。
    """
    if schedule is None:
        return None
    shift = shift_on(schedule, day)
    note = _exception_note(schedule, day)
    if shift is None:
        return {"day": DAY_REST, "hours": None, "place": None, "breaks": [], "note": note}
    return {"day": DAY_WORK, "hours": _span_text(shift.starts, shift.ends),
            "place": schedule.workplace,
            "breaks": [_span_text(begin, finish) for begin, finish in shift.breaks], "note": note}


def _exception_note(schedule: Any, day: date) -> str | None:
    """@brief 日期例外把这一天的上班或休息改得与每周声明不同时的一句说明；其余情形为 None。"""
    usual = schedule.weekly.get(str(day.weekday())) is not None
    if usual == (shift_on(schedule, day) is not None):
        return None
    name = WEEKDAY_NAMES[day.weekday()]
    return f"平常{name}上班，今天按工作安排休息" if usual else f"平常{name}休息，今天按工作安排上班"


def duty(schedule: Any, now: int, timezone_name: str, places: Mapping[str, str]) -> dict[str, Any] | None:
    """@brief 环境层 duty：此刻所在本地日的上班安排加状态；没有工作安排为 None，休息日的状态为 None。"""
    zone = ZoneInfo(timezone_name)
    day = datetime.fromtimestamp(now, zone).date()
    arrangement = day_arrangement(schedule, day, places)
    if arrangement is None:
        return None
    shift = shift_on(schedule, day)
    return {**arrangement, "status": None if shift is None else duty_status(shift, day, now, zone)}


def tomorrow(schedule: Any, now: int, timezone_name: str, places: Mapping[str, str]) -> dict[str, Any]:
    """@brief 环境层 tomorrow：此刻所在本地日的下一天的日期、星期名与上不上班；没有工作安排时 day 为 None。"""
    day = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date() + timedelta(days=1)
    arrangement = day_arrangement(schedule, day, places)
    return {"date": day.isoformat(), "weekday": WEEKDAY_NAMES[day.weekday()],
            "day": None if arrangement is None else arrangement["day"]}


def _span_text(start: int, end: int) -> str:
    """@brief 分钟区间的可读写法「HH:MM–HH:MM」（EN DASH，与零点计划输入的时段同一写法；1440 写作 24:00）。"""
    return f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"
