"""@brief 七层提示共用的人读短语：带时区的时刻、本地日期、钟点、时长、金额与份数。

依据 docs/dev/SPEC-persona-prompt.md 第一章「通用格式」：时刻字段用带时区的 ISO 8601（秒精度、场景时区）；
短语里的时刻用场景本地 HH:MM，跨日时加「M月D日」；数量、价格与时长写成人读短语（「约 12 分钟」「32 元」
「0.25 份」），不给原始秒数或以分为单位的整数。数字与单位之间留一个空格。各层构建器、公开结果与 settled 句
都从这里取短语，全仓只有这一套口径。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _local(epoch: int, timezone_name: str) -> datetime:
    """@brief 场景时区的本地时刻（秒精度）。"""
    return datetime.fromtimestamp(int(epoch), ZoneInfo(timezone_name))


def iso_time(epoch: int, timezone_name: str) -> str:
    """@brief 带时区偏移的 ISO 8601 时刻，例如「2026-09-28T10:33:00+08:00」。"""
    return _local(epoch, timezone_name).isoformat()


def local_date(epoch: int, timezone_name: str) -> str:
    """@brief 场景本地日期「YYYY-MM-DD」。"""
    return _local(epoch, timezone_name).date().isoformat()


def clock_phrase(epoch: int, now: int, timezone_name: str) -> str:
    """@brief 短语中的时刻：场景本地「HH:MM」；与 now 不在同一本地日期时前加「M月D日 」。"""
    moment = _local(epoch, timezone_name)
    if moment.date() == _local(now, timezone_name).date():
        return f"{moment:%H:%M}"
    return f"{moment.month}月{moment.day}日 {moment:%H:%M}"


def duration_phrase(seconds: float) -> str:
    """@brief 人读时长：不到一分钟写「不到 1 分钟」，不足一小时按分钟取整，一小时以上按半小时取整。"""
    if seconds < 60:
        return "不到 1 分钟"
    if seconds < 3600:
        return f"约 {round(seconds / 60)} 分钟"
    return f"约 {round(seconds / 1800) / 2:g} 小时"


def _decimal(value: float) -> str:
    """@brief 至多两位小数、去掉末尾的零的数字写法。"""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def money_phrase(fen: int) -> str:
    """@brief 以分为单位的金额写成「32 元」「32.5 元」。"""
    return f"{_decimal(fen / 100)} 元"


def portion_phrase(quantity: float) -> str:
    """@brief 份数写成「1 份」「0.25 份」。"""
    return f"{_decimal(quantity)} 份"
