"""@brief 统一解释营业声明、世界营业例外与报价半开生效区间。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from worldmock.life.models import OpeningInterval

OpeningOverrides = Mapping[str, Sequence[tuple[int, int]]]


def quote_at(supply, now: int):
    """@brief 返回时刻适用的唯一供给报价；未定义区间返回空。"""
    return next((item for item in supply.quotes if _effective(item, now)), None)


def opening_status(location, now: int, timezone_name: str,
                   overrides: OpeningOverrides | None = None) -> str:
    """@brief 按当地时区与日期例外计算 open、closed 或 unknown。"""
    calendar = next((item for item in location.opening_calendars if _effective(item, now)), None)
    if calendar is None:
        return "unknown"
    zone = ZoneInfo(timezone_name)
    current = datetime.fromtimestamp(now, zone)
    today = current.date()
    intervals, explicit = _intervals(calendar, today, overrides)
    if explicit and not intervals:
        return "closed"
    if _contains(intervals, today, now, zone):
        return "open"
    previous = today - timedelta(days=1)
    before, _ = _intervals(calendar, previous, overrides)
    return "open" if not explicit and _contains(before, previous, now, zone) else "closed"


def next_opening_change(location, now: int, timezone_name: str,
                        overrides: OpeningOverrides | None = None) -> int | None:
    """@brief 返回营业状态下一次真正变化的事件时刻，不产生周期轮询。"""
    zone = ZoneInfo(timezone_name)
    candidates: set[int] = set()
    for calendar in location.opening_calendars:
        candidates.add(int(calendar.effective_from.timestamp()))
        if calendar.effective_to is not None:
            candidates.add(int(calendar.effective_to.timestamp()))
        candidates.update(_calendar_boundaries(calendar, now, zone, overrides))
    for moment in sorted(value for value in candidates if value > now):
        before = opening_status(location, moment - 1, timezone_name, overrides)
        after = opening_status(location, moment, timezone_name, overrides)
        if before != after:
            return moment
    return None


def _effective(declaration, now: int) -> bool:
    """@brief 统一应用声明的半开生效区间。"""
    return (declaration.effective_from.timestamp() <= now
            and (declaration.effective_to is None or now < declaration.effective_to.timestamp()))


def _intervals(calendar, day: date,
               overrides: OpeningOverrides | None) -> tuple[tuple[OpeningInterval, ...], bool]:
    """@brief 世界例外优先于冻结例外，冻结例外优先于每周日历。"""
    key = day.isoformat()
    if overrides is not None and key in overrides:
        return tuple(OpeningInterval(opens=start, closes=end) for start, end in overrides[key]), True
    if key in calendar.exceptions:
        return calendar.exceptions[key], True
    return calendar.weekly[str(day.weekday())], False


def _contains(intervals: tuple[OpeningInterval, ...], day: date, now: int, zone: ZoneInfo) -> bool:
    """@brief 以当地日历时间构造区间，避免夏令时被固定秒数扭曲。"""
    midnight = datetime.combine(day, datetime.min.time(), zone)
    return any((midnight + timedelta(minutes=item.opens)).timestamp() <= now
               < (midnight + timedelta(minutes=item.closes)).timestamp() for item in intervals)


def _calendar_boundaries(calendar, now: int, zone: ZoneInfo,
                         overrides: OpeningOverrides | None) -> set[int]:
    """@brief 从每周周期、日期例外与有效期边界生成有限的营业事件候选。"""
    today = datetime.fromtimestamp(now, zone).date()
    start = max(today, calendar.effective_from.astimezone(zone).date())
    days = {start + timedelta(days=offset) for offset in range(-1, 9)}
    days.update(date.fromisoformat(value) for value in calendar.exceptions)
    if overrides is not None:
        days.update(date.fromisoformat(value) for value in overrides)
    days.update(day + timedelta(days=1) for day in tuple(days))
    result: set[int] = set()
    for day in days:
        midnight = datetime.combine(day, datetime.min.time(), zone)
        result.add(int(midnight.timestamp()))
        intervals, _ = _intervals(calendar, day, overrides)
        for interval in intervals:
            result.update(int((midnight + timedelta(minutes=value)).timestamp())
                          for value in (interval.opens, interval.closes))
    return result
