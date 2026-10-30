"""记忆层的组装（纯函数）：四个键的次序、「可查」、按渲染后字数量的上限、「今天」的递推与超出上限时的三步兜底。

条目由 _entry 生成 PublishedEntry；字数靠加长「核验」字符串凑到要的值，用 layer_chars 核对。固定部分缺省用
earliest="2026-10-08"、recent={}。
"""

import logging
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
    require_budget,
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


def _warnings(caplog) -> list[str]:
    """记下的英文警告日志。"""
    return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]


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


def test_entries_are_appended_unchanged_while_the_layer_fits():
    """五条逐条接上：各项与传入的 item 相同，每接一条，前一次渲染去掉末尾 ]} 后是下一次渲染的前缀。"""
    entries = [_entry(f"0{hour}:00", "生活", [f"开始第{hour}件事"], [f"自述{hour}"]) for hour in range(5)]
    fold, previous = TodayFold(), None
    for entry in entries:
        fold_entry(fold, entry, EARLIEST, {})
        rendered = render_layer_json(assemble_layer(EARLIEST, {}, fold_items(fold)))
        assert previous is None or rendered.startswith(previous[:-2])
        previous = rendered
    assert fold_items(fold) == [entry.item for entry in entries]


def test_a_layer_of_exactly_14000_characters_is_not_backstopped(monkeypatch, caplog):
    """目标设成 14000：接上后恰 14,000 字不触发；多一个字时触发，第一条 10 字的自述换成「…」后回落到 13,992 字。"""
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", 14000)
    base = [_entry("07:00", "生活", ["a"], ["自" * 10]), _entry("07:10", "生活", ["b"]), _entry("07:20", "生活", ["c"])]
    exact = _sized(base, 14000)
    assert fold_items(_fold(*exact)) == [entry.item for entry in exact] and _warnings(caplog) == []
    over = _sized(base, 14001)
    items = fold_items(_fold(*over))
    assert items[0]["自述"] == ["…"] and items[1:] == [entry.item for entry in over[1:]]
    assert _warnings(caplog) == ["Memory layer backstop rewrote today from 14001 to 13992 characters (target 14000)"]


def _reports(first: tuple, second: tuple, concluded: tuple) -> list[PublishedEntry]:
    """播报 first 的条目、播报 second 的条目、收尾 concluded 的条目，三条的自述等长。"""
    return [_entry("07:00", "工作", ["开始甲"], ["进展·甲：" + "一" * 20], report=first),
            _entry("07:10", "工作", ["开始乙"], ["进展·乙：" + "二" * 20], report=second),
            _entry("07:20", "工作", ["收尾丙"], ["丙：" + "三" * 22], concluded=concluded)]


def _over_by_one(monkeypatch, entries) -> list[dict]:
    """把上限与目标都设成这些条目字数减一后递推：换掉任一项自述即达标。"""
    total = _chars(entry.item for entry in entries)
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", total - 1)
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", total - 1)
    return fold_items(_fold(*entries))


def _blanked(items) -> list[int]:
    """自述是 ["…"] 的项的位置。"""
    return [index for index, item in enumerate(items) if item.get("自述") == ["…"]]


@pytest.mark.parametrize("concluded,blanked", [("b", [1]), ("a", [0]), ("c", [0])],
                         ids=["concludes-b", "concludes-a", "concludes-neither"])
def test_the_backstop_first_blanks_report_notes_whose_activities_were_concluded(monkeypatch, concluded, blanked):
    """已收尾活动的播报先于更早的未收尾活动的播报；两项都未收尾时换掉列表里更早的那一项。点名两项的播报只在两项都收尾时
    归入前半。"""
    assert _blanked(_over_by_one(monkeypatch, _reports(("a",), ("b",), (concluded,)))) == blanked
    assert _blanked(_over_by_one(monkeypatch, _reports(("c",), ("a", "b"), ("a",)))) == [0]
    assert _blanked(_over_by_one(monkeypatch, _reports(("c",), ("a", "b"), ("a", "b")))) == [1]


def test_the_backstop_then_keeps_only_verified_lines(monkeypatch):
    """播报都换完仍超：其余条目从最早的一条起「自述」换成「…」，「时刻」「类别」「核验」与键序不变。"""
    entries = [_entry("07:00", "工作", ["开始甲"], ["进展·甲：" + "一" * 20], report=("a",)),
               _entry("07:10", "生活", ["睡觉 昨23:00–07:10"], ["睡觉：" + "二" * 20]),
               _entry("07:20", "饮食", ["开始购买牛肉面"], ["正文：" + "三" * 20])]
    total = _chars(entry.item for entry in entries)
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", total - 1)
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", total - 30)
    items = fold_items(_fold(*entries))
    assert _blanked(items) == [0, 1] and items[2] == entries[2].item
    assert list(items[1]) == list(entries[1].item)
    assert {key: items[1][key] for key in ("时刻", "类别", "核验")} == {
        key: entries[1].item[key] for key in ("时刻", "类别", "核验")}


def test_the_backstop_never_adds_notes_and_never_drops_verified_lines(monkeypatch):
    """没有「自述」的条目兜底后仍没有；「自述」原本是「…」的条目跳过；第三步以前每条的「核验」逐字不变。"""
    entries = [_entry("07:00", "生活", ["开始测试休息"]), _entry("07:10", "生活", ["开始哔哩哔哩"], ["…"]),
               _entry("07:20", "生活", ["测试休息 07:00–07:15"], ["测试休息：" + "一" * 30]),
               _entry("07:30", "生活", ["开始测试闲聊"], ["正文：再聊一会儿。"])]
    items = _over_by_one(monkeypatch, entries)
    assert "自述" not in items[0] and items[1]["自述"] == ["…"] and _blanked(items) == [1, 2]
    assert items[3] == entries[3].item
    assert [item["核验"] for item in items] == [entry.item["核验"] for entry in entries]


def test_the_backstop_merges_same_hour_same_category_items_into_count_lines_last(monkeypatch):
    """07 时三条生活、一条工作、一条「工作、饮食」交错排列：先把三条生活合成一行放在第一条生活的位置；「工作」与「工作、
    饮食」各自成组，单条一组也合成，条数为 1。"""
    entries = [_entry("07:00", "生活", ["生活一"]), _entry("07:10", "工作", ["工作一"]),
               _entry("07:20", "生活", ["生活二"]), _entry("07:30", "工作、饮食", ["工作饮食一"]),
               _entry("07:40", "生活", ["生活三"])]
    first = {"时刻": "07:00–08:00", "类别": "生活", "条数": 3}
    after_first = [first, entries[1].item, entries[3].item]
    total = _chars(entry.item for entry in entries)
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", total - 1)
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", _chars(after_first))
    assert fold_items(_fold(*entries)) == after_first
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", 10)
    assert fold_items(_fold(*entries)) == [first, {"时刻": "07:00–08:00", "类别": "工作", "条数": 1},
                                           {"时刻": "07:00–08:00", "类别": "工作、饮食", "条数": 1}]


def test_count_lines_absorb_earlier_count_lines_and_write_23_to_24(monkeypatch):
    """先前触发留下 23 时「生活」两条的计数行，其后又有两条 23 时「生活」条目：再次触发到第三步时合成一行 4 条，位置是
    原计数行的位置；只由一行计数行组成的组（22 时）不动。"""
    fold = TodayFold()
    for entry in (_entry("22:30", "生活", ["二十二点"]), _entry("23:10", "生活", ["二十三点一"])):
        fold_entry(fold, entry, EARLIEST, {})
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", 10)
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", 10)
    fold_entry(fold, _entry("23:20", "生活", ["二十三点二"]), EARLIEST, {})
    earlier = [{"时刻": "22:00–23:00", "类别": "生活", "条数": 1}, {"时刻": "23:00–24:00", "类别": "生活", "条数": 2}]
    assert fold_items(fold) == earlier
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", 14000)
    fold_entry(fold, _entry("23:40", "生活", ["二十三点三"]), EARLIEST, {})
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", 10)
    fold_entry(fold, _entry("23:50", "生活", ["二十三点四"]), EARLIEST, {})
    assert fold_items(fold) == [earlier[0], {"时刻": "23:00–24:00", "类别": "生活", "条数": 4}]


def test_the_backstop_stops_at_exactly_12000_characters():
    """换掉一项后恰 12,000 字：之后的项都不动；换掉一项后 12,001 字：继续换下一项。"""
    first, second = _entry("07:00", "生活", ["a"], ["甲" * 2501]), _entry("07:10", "生活", ["b"], ["乙" * 30])
    exact = _sized([first, second, _entry("07:20", "生活", ["c"])], 14500)
    items = fold_items(_fold(*exact))
    assert _blanked(items) == [0] and items[1:] == [second.item, exact[2].item] and _chars(items) == 12000
    over = _sized([first, second, _entry("07:20", "生活", ["c"])], 14501)
    assert _blanked(fold_items(_fold(*over))) == [0, 1]


def test_backstopped_items_keep_their_reduced_form_and_later_entries_append(monkeypatch):
    """触发以后再接两条（不再超限）：先前改过的项不恢复，新条目原样接在末尾，两次之间列表只追加。"""
    entries = _reports(("a",), ("b",), ("c",))
    total = _chars(entry.item for entry in entries)
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", total - 1)
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", total - 1)
    fold = _fold(*entries)
    reduced = fold_items(fold)
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", 14000)
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", 12000)
    later = [_entry("08:00", "生活", ["开始测试休息"]), _entry("08:10", "生活", ["开始测试闲聊"], ["正文：歇一会。"])]
    for entry in later:
        fold_entry(fold, entry, EARLIEST, {})
    assert _blanked(reduced) == [0]
    assert fold_items(fold) == [*reduced, *(entry.item for entry in later)]


def test_a_fold_rebuilt_from_the_first_entry_equals_the_incremental_fold(monkeypatch, caplog):
    """二十条条目、其中两处触发：逐条增量递推与每个 k 都从第一条重新递推，渲染出的记忆层逐字节相同。"""
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", 3000)
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", 1500)
    entries = [_entry(f"{8 + index // 3:02d}:{index % 3 * 20:02d}", ("生活", "工作")[index % 2], [f"第{index}件"],
                      ["记" * 200], report=("a",) if index % 4 == 0 else ()) for index in range(20)]
    fold = TodayFold()
    for count, entry in enumerate(entries, start=1):
        fold_entry(fold, entry, EARLIEST, {})
        rebuilt = _fold(*entries[:count])
        assert render_layer_json(assemble_layer(EARLIEST, {}, fold_items(fold))) == render_layer_json(
            assemble_layer(EARLIEST, {}, fold_items(rebuilt)))
    caplog.clear()
    _fold(*entries)
    assert len(_warnings(caplog)) == 2


def test_a_backstop_logs_one_warning_with_its_sizes(caplog):
    """一次触发恰写一条警告，前后两个数与 layer_chars 一致；三步做完仍超 12,000 字时照样保留做完以后的样子，警告里后一个
    数大于 12000。"""
    first, second = _entry("07:00", "生活", ["a"], ["甲" * 3000]), _entry("07:10", "生活", ["b"], ["乙" * 30])
    entries = _sized([first, second, _entry("07:20", "生活", ["c"])], 14100)
    fold = _fold(*entries)
    assert _warnings(caplog) == [
        f"Memory layer backstop rewrote today from 14100 to {_chars(fold_items(fold))} characters (target 12000)"]
    caplog.clear()
    recent = {"饮食": [{"日期": "2026-10-07", "星期": "星期三", "回顾": "饭" * 12500}]}
    fold = _fold(_entry("07:00", "生活", ["核" * 1600], ["自述"]), recent=recent)
    items = fold_items(fold)
    assert items == [{"时刻": "07:00–08:00", "类别": "生活", "条数": 1}]
    (warning,) = _warnings(caplog)
    after = int(warning.split(" to ")[1].split(" ")[0])
    assert after == _chars(items, recent) and after > 12000


def test_a_memory_layer_above_14000_characters_is_refused(caplog):
    """14,000 字的记忆层通过；14,001 字时以 RuntimeError 停止，并写英文错误日志。"""
    (entry,) = _sized([_entry("07:00", "生活", ["a"])], 14000)
    require_budget(assemble_layer(EARLIEST, {}, [entry.item]))
    (entry,) = _sized([_entry("07:00", "生活", ["a"])], 14001)
    with pytest.raises(RuntimeError) as refused:
        require_budget(assemble_layer(EARLIEST, {}, [entry.item]))
    assert str(refused.value) == "Memory layer exceeds 14000 characters"
    assert "Memory layer has 14001 characters, above its 14000-character budget" in caplog.text


def test_fold_items_are_copies():
    """改动 fold_items 返回的项与传入条目的 item，递推状态不受影响。"""
    entry = _entry("07:00", "生活", ["开始测试休息"], ["测试休息：歇一下。"])
    fold = _fold(entry)
    items = fold_items(fold)
    items[0]["核验"].append("改动")
    entry.item["自述"].append("改动")
    assert fold_items(fold) == [{"时刻": "07:00", "类别": "生活", "核验": ["开始测试休息"], "自述": ["测试休息：歇一下。"]}]
