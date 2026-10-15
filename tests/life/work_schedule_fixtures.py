"""工作安排用例共用的场景声明（docs/design/06-agent.md#agent-环境层-duty ；不是测试模块，不被收集）。

仓库三份场景的全部人物都写 `work_schedule: null`，班次行为一律由用例经 `engine_factory(mutate=...)` 或场景副本构造：
weekday_schedule 按场景日历生成与时间窗一致的声明（周一至周五同一班次、周末不当班；时间窗内的补班日写班次例外，落在
工作日的春节、病假、事假日写不当班例外），declare 返回给 mutate 用的改写函数。三份场景都从 2026-01-05（周一）00:00
+08:00 开始：十二周白领场景的时间窗到 2026-03-30 00:00，含日历里的全部日期；两份多人场景的时间窗不含任何日历日期。
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

#: 工作地点：场景所选地点里的办公室（显示名取地图）。
OFFICE = "office"
#: 标准班次：09:00–18:00，午休 12:00–13:30（自当地零点起的分钟）。
STANDARD_SHIFT = {"starts": 540, "ends": 1080, "breaks": [[720, 810]]}
#: 必须有班次的日历字段与必须不当班的日历字段（同加载检查）。
_SHIFT_REQUIRED = ("makeup_work_dates",)
_OFF_DUTY = ("spring_festival_dates", "sick_leave_date", "personal_leave_date")


def window_dates(document: dict, field: str) -> list[date]:
    """场景日历某个字段里落在时间窗内的日期（不早于开始时刻的当地日期，且当地零点早于结束时刻），按日期排序。"""
    zone = ZoneInfo(document["timezone"])
    start = datetime.fromisoformat(document["start_at"])
    end = datetime.fromisoformat(document["end_at"])
    value = document["calendar"][field]
    days = [date.fromisoformat(text) for text in ([value] if isinstance(value, str) else value)]
    return sorted(day for day in days
                  if start.astimezone(zone).date() <= day and datetime.combine(day, time.min, zone) < end)


def weekday_schedule(document: dict, shift: dict | None = None, workplace: str = OFFICE) -> dict:
    """与场景日历一致的工作安排：周一至周五同一班次、周末 null；时间窗内的补班日写班次例外，落在工作日的春节、病假、
    事假日写 null 例外（周末本来不当班，不另写）。"""
    shift = dict(STANDARD_SHIFT if shift is None else shift)
    weekly = {str(day): (dict(shift) if day < 5 else None) for day in range(7)}
    exceptions: dict[str, dict | None] = {}
    for field in _SHIFT_REQUIRED:
        exceptions.update({day.isoformat(): dict(shift) for day in window_dates(document, field)})
    for field in _OFF_DUTY:
        exceptions.update({day.isoformat(): None for day in window_dates(document, field) if day.weekday() < 5})
    return {"workplace_location_id": workplace, "weekly": weekly, "exceptions": dict(sorted(exceptions.items()))}


def declare(agent_index: int = 0, *, shift: dict | None = None, workplace: str = OFFICE,
            weekly: dict | None = None, exceptions: dict | None = None):
    """返回 mutate(document)：给第 agent_index 个人物写上 weekday_schedule 生成的工作安排；weekly、exceptions 给出时按键
    覆盖到生成结果上（值为 null 即当日不当班）。"""
    def mutate(document: dict) -> None:
        """把工作安排写进场景文档副本的人物声明。"""
        schedule = weekday_schedule(document, shift, workplace)
        schedule["weekly"].update(weekly or {})
        schedule["exceptions"].update(exceptions or {})
        document["agents"][agent_index]["work_schedule"] = schedule

    return mutate
