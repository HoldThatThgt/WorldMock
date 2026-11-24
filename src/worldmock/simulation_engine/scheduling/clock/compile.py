"""@brief 完整环境在既有排程中编译时间、天气、日历和应用推送。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.resource_engine.catalog.callback_models import MonthlyTrigger
from worldmock.exchange_engine.definitions.constants import PUSH_HOURS_LOCAL
from worldmock.state.planning.events import ScheduledEvent, ScheduledKind

#: 日历旗标沿用现行配置中的实际日期。
CALENDAR_FLAG_FIELDS = {
    "spring_festival": "spring_festival_dates", "makeup_workday": "makeup_work_dates",
    "overtime_opportunity": "overtime_opportunity_dates", "sick_leave": "sick_leave_date",
    "personal_leave": "personal_leave_date", "social_dinner_opportunity": "social_dinner_date",
}


def compile_schedule(inputs) -> list[ScheduledEvent]:
    """@brief 周期范围为起点排除、终点包含，重复本地整点保留不同绝对秒；支持单人与多数字人排程。"""
    config, catalog = inputs.config, inputs.catalog
    agent_ids = sorted(persona.agent_id for persona in inputs.personas)
    start, end = int(config.start_time.timestamp()), int(config.stop_time.timestamp())
    zone = ZoneInfo(config.timezone)
    rows: dict[str, tuple[int, ScheduledKind, dict, int]] = {}
    monthly = {(rule.trigger.day, rule.trigger.time) for rule in catalog.callback_rules.values()
               if isinstance(rule.trigger, MonthlyTrigger)}
    time_keys = {rule.trigger.key for rule in catalog.callback_rules.values()
                 if rule.trigger.trigger_class == "time"}
    flags = _calendar_flags(config.calendar)
    for instant in range(start + 60, end + 1, 60):
        local = datetime.fromtimestamp(instant, zone)
        if (local.day, local.strftime("%H:%M")) in monthly:
            _time_rows(rows, instant, "monthly", agent_ids)
        if local.minute:
            continue
        if "hourly" in time_keys:
            _time_rows(rows, instant, "hourly", agent_ids)
        if local.hour in PUSH_HOURS_LOCAL and local.fold == 0:
            for agent_id in agent_ids:
                identity = f"push-{agent_id}-{local.date().isoformat()}T{local.hour:02d}"
                rows[identity] = (instant, ScheduledKind.APP_PUSH, {}, agent_id)
        if local.hour == 0:
            if "daily" in time_keys:
                _time_rows(rows, instant, "daily", agent_ids)
            date = local.date().isoformat()
            for city_id in sorted({place.city_id for place in catalog.locations.values()}):
                rows[f"weather-{city_id}-{date}"] = (
                    instant, ScheduledKind.WEATHER, {"city_id": city_id, "local_date": date}, agent_ids[0],
                )
            if date in flags:
                rows[f"calendar-{date}"] = (instant, ScheduledKind.CALENDAR_FLAG,
                                            {"date": date, "flags": flags[date]}, agent_ids[0])
    ordered = sorted(rows.items(), key=lambda row: (row[1][0], row[0]))
    return [ScheduledEvent(scheduled_id=identity, due_at=row[0], kind=row[1], data=row[2],
                           agent_id=row[3], sequence=index)
            for index, (identity, row) in enumerate(ordered, start=1)]


def _time_rows(rows: dict, instant: int, key: str, agent_ids: list[int]) -> None:
    """@brief 单人环境保留既有事件标识，多人环境按人物分别登记订阅触发源。"""
    if len(agent_ids) == 1:
        rows[f"time-{key}-{instant}"] = (instant, ScheduledKind.TIME_TRIGGER, {"key": key}, agent_ids[0])
        return
    for agent_id in agent_ids:
        rows[f"time-{key}-{instant}-{agent_id}"] = (instant, ScheduledKind.TIME_TRIGGER, {"key": key}, agent_id)


def _calendar_flags(calendar) -> dict[str, list[str]]:
    """@brief 相同自然日的多个日历旗标只登记一条事实。"""
    flags = {}
    for flag, field in CALENDAR_FLAG_FIELDS.items():
        value = getattr(calendar, field)
        values = (value,) if isinstance(value, str) else value
        for date in values:
            flags.setdefault(date, []).append(flag)
    return {date: sorted(values) for date, values in flags.items()}
