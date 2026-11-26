"""记忆层的组装（纯函数）：四个键的次序、「可查」、字数测量与「今天」无截断递推追加。

条目由 _entry 生成 PublishedEntry；固定部分缺省用 earliest="2026-10-08"、recent={}。
"""

from dataclasses import replace
from datetime import datetime

import pytest

from worldmock.agents.prompt.life_prompt import user_message
from worldmock.agents.prompt.narration import render_layer_json
from worldmock.simulation_engine.observation.memory import layout
from worldmock.simulation_engine.observation.memory.layout import (
    PublishedEntry,
    TodayFold,
    assemble_layer,
    earliest_date,
    fold_entry,
    fold_items,
    layer_chars,
)

EARLIEST = "2026-10-08"


def _entry(time, category, verified=(), notes=(), report=(), concluded=()) -> PublishedEntry:
    """一条已发布的条目：item 按今天条目的形状（空键省略），report 是播报点名的活动，concluded 是收尾的活动。"""
    item = {"时刻": time, "类别": category}
    if verified:
        item["核验"] = list(verified)
    if notes:
        item["自述"] = list(notes)
    return PublishedEntry(item, bool(report), frozenset(report), frozenset(concluded))


def _chars(items, recent=None) -> int:
    """以这些项组装的记忆层字数。"""
    return layer_chars(assemble_layer(EARLIEST, {} if recent is None else recent, list(items)))


def _fold(*entries, recent=None) -> TodayFold:
    """从空的递推状态起依次折入各条。"""
    fold = TodayFold()
    for entry in entries:
        fold_entry(fold, entry, EARLIEST, {} if recent is None else recent)
    return fold


def _sized(entries, total: int) -> list[PublishedEntry]:
    """加长最后一条第一行「核验」，使这些条目组成的记忆层恰为 total 字。"""
    gap = total - _chars(entry.item for entry in entries)
    last = entries[-1]
    item = {**last.item, "核验": [last.item["核验"][0] + "核" * gap, *last.item["核验"][1:]]}
    return [*entries[:-1], replace(last, item=item)]


def test_memory_layer_has_layer_available_recent_today_in_this_order():
    """记忆层四个键依次是 layer、可查、近三天、今天，值依次是记忆层、带最早日期的对象、空对象、空列表。"""
    layer = assemble_layer("2026-10-08", {}, [])
    assert list(layer) == ["layer", "可查", "近三天", "今天"]
    assert list(layer.values()) == ["记忆层", {"最早日期": "2026-10-08"}, {}, []]


def test_an_absent_earliest_date_gives_an_empty_available_object():
    """只读问答尚无快照时没有最早日期：「可查」是空对象，渲染出的空记忆层逐字固定。"""
    layer = assemble_layer(None, {}, [])
    assert layer["可查"] == {}
    assert render_layer_json(layer) == '{"layer":"记忆层","可查":{},"近三天":{},"今天":[]}'


@pytest.mark.parametrize("start,expected", [
    ("2026-10-08T07:00:00+08:00", "2026-10-08"), ("2026-10-08T00:00:00+08:00", "2026-10-08"),
    ("2026-10-07T23:00:00+08:00", "2026-10-07"), ("2026-10-08T01:00:00+08:00", "2026-10-08")])
def test_earliest_date_is_the_scenario_local_date_of_the_run_start(start, expected):
    """最早日期是运行开始时刻在场景时区的本地日期（UTC 仍是前一天的 01:00 也按本地日期）。"""
    assert earliest_date(datetime.fromisoformat(start), "Asia/Shanghai") == expected


def test_layer_chars_is_the_length_of_the_rendered_layer_with_its_keys():
    """字数是整层紧凑 JSON 的 len，含键名与标点；它正是记忆层在 user 消息里占的那一段。"""
    recent = {"饮食": [{"日期": "2026-10-07", "星期": "星期三", "回顾": "中午吃了牛肉面。"}]}
    layer = assemble_layer(EARLIEST, recent, [_entry("07:00", "生活", ["开始测试休息"]).item])
    assert layer_chars(layer) == len(render_layer_json(layer))
    persona = {"layer": "人设层", "identity": {"name": "林舟"}}
    progress = {"layer": "进度层", "events": []}
    assert render_layer_json(layer) in user_message([persona, layer, progress])["content"]


def test_entries_are_appended_unchanged_without_retired_backstop():
    """连续折入与超过 14000 字长序列均原样追加不回填改写，已退役回填常量与函数已移除。"""
    for retired in ("MEMORY_LAYER_CHARS", "MEMORY_BACKSTOP_CHARS", "require_budget", "_backstop", "_merge_hours"):
        assert not hasattr(layout, retired)
    entries = [_entry(f"0{hour}:00", "生活", [f"开始第{hour}件事"], [f"自述{hour}"]) for hour in range(5)]
    fold, previous = TodayFold(), None
    for entry in entries:
        fold_entry(fold, entry, EARLIEST, {})
        rendered = render_layer_json(assemble_layer(EARLIEST, {}, fold_items(fold)))
        assert previous is None or rendered.startswith(previous[:-2])
        previous = rendered
    assert fold_items(fold) == [entry.item for entry in entries]
    large = _sized([_entry("07:00", "生活", ["a"], ["自" * 50]), _entry("07:10", "生活", ["b"])], 14500)
    assert fold_items(_fold(*large)) == [entry.item for entry in large]
    assert _chars(fold_items(_fold(*large))) == 14500


def test_fold_items_are_copies():
    """改动 fold_items 返回的项与传入条目的 item，递推状态不受影响。"""
    entry = _entry("07:00", "生活", ["开始测试休息"], ["测试休息：歇一下。"])
    fold = _fold(entry)
    items = fold_items(fold)
    items[0]["核验"].append("改动")
    entry.item["自述"].append("改动")
    assert fold_items(fold) == [{"时刻": "07:00", "类别": "生活", "核验": ["开始测试休息"], "自述": ["测试休息：歇一下。"]}]
