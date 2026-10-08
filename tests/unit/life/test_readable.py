"""七层共用的人读短语（docs/design/06-agent.md 第一章「通用格式」）。

时刻字段是带时区偏移、秒精度的 ISO 8601；日期是场景本地日期；短语里的时刻是本地 HH:MM，跨日加「M月D日 」；时长不到一分钟
写「不到 1 分钟」，不足一小时按分钟取整，一小时以上按半小时取整；金额以元、份数都至多两位小数并去掉末尾的零；数字与单位
之间一个空格。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.life.readable import clock_phrase, duration_phrase, iso_time, local_date, money_phrase, portion_phrase

ZONE = "Asia/Shanghai"


def _epoch(*parts: int, zone: str = ZONE) -> int:
    """场景时区本地时刻的 epoch 秒。"""
    return int(datetime(*parts, tzinfo=ZoneInfo(zone)).timestamp())


def test_iso_time_and_local_date_use_the_scene_timezone_with_offset() -> None:
    """时刻带场景时区偏移、秒精度；日期取场景本地日期（UTC 已是次日时仍按本地日期）。"""
    moment = _epoch(2026, 9, 28, 10, 33)
    assert iso_time(moment, ZONE) == "2026-09-28T10:33:00+08:00"
    assert iso_time(moment, "America/New_York") == "2026-09-27T22:33:00-04:00"
    assert local_date(_epoch(2026, 9, 28, 1, 0), ZONE) == "2026-09-28"
    assert local_date(_epoch(2026, 9, 28, 1, 0), "UTC") == "2026-09-27"


def test_clock_phrase_adds_the_date_only_across_local_days() -> None:
    """同一本地日期只写 HH:MM；与观察时刻不在同一本地日期时前加「M月D日 」（前一天与后一天都是）。"""
    now = _epoch(2026, 9, 28, 10, 33)
    assert clock_phrase(_epoch(2026, 9, 28, 0, 5), now, ZONE) == "00:05"
    assert clock_phrase(_epoch(2026, 9, 27, 23, 50), now, ZONE) == "9月27日 23:50"
    assert clock_phrase(_epoch(2026, 9, 29, 0, 10), now, ZONE) == "9月29日 00:10"


def test_duration_phrase_rounds_minutes_then_half_hours() -> None:
    """不到一分钟写「不到 1 分钟」；一小时以内按分钟取整；一小时起按半小时取整（整数不带小数点）。"""
    assert [duration_phrase(seconds) for seconds in (0, 59, 60, 719, 3599)] == [
        "不到 1 分钟", "不到 1 分钟", "约 1 分钟", "约 12 分钟", "约 60 分钟"]
    assert [duration_phrase(seconds) for seconds in (3600, 5400, 6.4 * 3600, 86_400)] == [
        "约 1 小时", "约 1.5 小时", "约 6.5 小时", "约 24 小时"]


def test_money_and_portion_phrases_keep_at_most_two_decimals() -> None:
    """金额从分换成元、份数照写，都至多两位小数并去掉末尾的零；零写「0」。"""
    assert [money_phrase(fen) for fen in (3200, 3250, 3255, 5, 0)] == [
        "32 元", "32.5 元", "32.55 元", "0.05 元", "0 元"]
    assert [portion_phrase(quantity) for quantity in (1, 0.25, 0.2, 1 / 3, 2.0, 0)] == [
        "1 份", "0.25 份", "0.2 份", "0.33 份", "2 份", "0 份"]


def test_count_phrase_for_the_callable_index() -> None:
    """@brief 工具层可调用索引的计数写成「N 个」，数字与量词之间一个空格。"""
    from worldmock.life.readable import count_phrase

    assert [count_phrase(count) for count in (1, 3, 90, 110)] == ["1 个", "3 个", "90 个", "110 个"]


def test_chinese_kind_tables_have_a_single_source() -> None:
    """中文种类表只在 readable 定义（docs/design/10-runtime.md ）：环境层的 PLACE_TYPES、ACTION_PHRASES 与实际
    消费方的 TRAVEL_WAYS 就是这里的对象；活动种类表覆盖 ActivityKind 的全部取值；各表使用中文，应用动作保留冻结的 app 字样，英文键
    合成 ENGLISH_KIND_NAMES。"""
    import re

    from worldmock.life import admission, environment_layer, name_resolution, projection, readable, tool_layer
    from worldmock.life.models import ActivityKind

    assert environment_layer.PLACE_TYPES is readable.PLACE_TYPES
    assert environment_layer.ACTION_PHRASES is readable.ACTION_PHRASES
    assert all(module.TRAVEL_WAYS is readable.TRAVEL_WAYS
               for module in (admission, environment_layer, name_resolution, projection))
    assert tool_layer.ACTIVITY_DOING_LABELS is readable.ACTIVITY_DOING_LABELS
    assert set(readable.ACTIVITY_KIND_LABELS) == {kind.value for kind in ActivityKind}
    tables = (readable.ACTIVITY_KIND_LABELS, readable.ACTIVITY_DOING_LABELS, readable.PLACE_TYPES,
              readable.ACTION_PHRASES, readable.TRAVEL_WAYS)
    assert all(re.fullmatch(r"[一-鿿]+", label) or (table is readable.ACTION_PHRASES and label == "用手机 app")
               for table in tables for label in table.values())
    assert readable.ENGLISH_KIND_NAMES == {key for table in tables for key in table}


def test_action_public_labels_use_catalog_actions_without_phone_inference():
    """@brief 单一中文词表保留统一动作标签，手机身份由实际应用与使用方式提供。"""
    from worldmock.life.readable import ACTIVITY_KIND_LABELS, ACTIVITY_DOING_LABELS, ACTION_PHRASES
    assert ACTIVITY_KIND_LABELS['action'] == '动作'
    assert ACTIVITY_DOING_LABELS['action'] == '做事'
    assert ACTION_PHRASES['do'] == '做工具层里的动作'
    assert all('use_app' not in table for table in (ACTIVITY_KIND_LABELS, ACTIVITY_DOING_LABELS, ACTION_PHRASES))
