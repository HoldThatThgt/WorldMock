"""@brief 冻结时区内的自然日与实际秒区间。"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from worldmock.foundation.time.intervals import valid_interval

def window_days(window: dict) -> list[date]:
    """@brief 冻结窗口覆盖的本地日期，沿真实本地零点切分。"""
    return [day for day, _, _ in split_local_days(window["start"], window["end"], window["timezone"])]


def local_day_bounds(day: date, timezone: str | ZoneInfo) -> tuple[int, int]:
    """@brief 返回场景本地日的实际整数秒边界，夏令时日不强定为二十四小时。"""
    zone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return int(start.timestamp()), int(end.timestamp())


def split_local_days(start: int, end: int, timezone: str | ZoneInfo) -> list[tuple[date, int, int]]:
    """@brief 将正时长区间按场景本地零点切分，保留每段真实 epoch 秒。"""
    if not valid_interval(start, end):
        return []
    zone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    result = []
    cursor = start
    while cursor < end:
        day = datetime.fromtimestamp(cursor, zone).date()
        _, boundary = local_day_bounds(day, zone)
        stop = min(end, boundary)
        result.append((day, cursor, stop))
        cursor = stop
    return result
