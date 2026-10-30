"""今天条目的 120 字取舍：规则 B 的例子与 V 10-12 的测量夹具、核验超过 120 字时的三步兜底与短名的去法（纯函数）。"""

import json
from pathlib import Path

import pytest

from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.observation.memory import entries
from worldmock.simulation_engine.observation.memory.entries import EntryLine, fit_items, fit_verified, short_name

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/today-entry-rule-b.json"
#: 例子表里代表任意不含标点的汉字串的字。
A, B, C, D, ONE, TWO = "甲", "乙", "丙", "丁", "一", "二"


@pytest.mark.parametrize("verified,items,shown", [
    (120, ["睡觉：一二三"], []),
    (119, ["睡觉：" + ONE * 27], ["…"]),
    (0, [ONE * 50], [ONE * 50]),
    (0, [ONE * 51], [ONE * 51]),
    (0, [A * 100, B * 100], [A * 79 + "…", B * 39 + "…"]),
    (112, [ONE * 30], [ONE * 7 + "…"]),
    (113, [ONE * 30], ["…"]),
    (80, ["收尾：" + ONE * 28 + "，" + TWO * 20], ["收尾：" + ONE * 28 + "…"]),
    (0, [A * 100, B * 30 + "，" + C * 69], [A * 88 + "…", B * 30 + "…"]),
    (75, [A * 40, B * 30], [A * 40, "…"]),
    (80, [A * 40, B * 30], [A * 38 + "…"]),
    (0, [A * 45, B * 60, C * 60, D * 10], [A * 45, B * 39 + "…", C * 34 + "…"]),
])
def test_rule_b_vectors(verified, items, shown):
    """规则 B 的十二个例子逐字节成立。"""
    assert fit_items(verified, items) == shown


def test_rule_b_reproduces_the_measured_v_entries():
    """V 10-12 全部 89 次决策的自述取舍逐条复现测量结果。"""
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 89
    for row in rows:
        assert fit_items(row["verified_chars"], row["items"]) == row["shown"], row


def test_items_up_to_fifty_characters_are_whole_in_the_first_pass():
    """余量 50 字时，50 字的项整项给；51 字的项首轮得 40 字，第二轮补到 50 字截断。"""
    assert fit_items(70, [ONE * 50]) == [ONE * 50]
    assert fit_items(70, [ONE * 51]) == [ONE * 49 + "…"]


def test_a_cut_needs_eight_characters_of_room():
    """余量 8 字时截成 7 字加「…」；余量 7 字时整项省略并补一项「…」。"""
    assert fit_items(112, [ONE * 30]) == [ONE * 7 + "…"]
    assert fit_items(113, [ONE * 30]) == ["…"]


@pytest.mark.parametrize("mark", list("，。；！？、"))
def test_cut_moves_back_to_a_mark_only_within_ten_characters_in_the_second_half(mark):
    """截断保留前 g−1 字：最后一个可截标点在后一半、舍去的字（含标点）恰 10 个时退到它之前，11 个时不退；在前一半时
    不退；六个标点逐个生效，「·」「：」不生效。"""
    assert entries._cut(ONE * 20 + mark + ONE * 20, 31) == ONE * 20 + "…"
    assert entries._cut(ONE * 19 + mark + ONE * 20, 31) == ONE * 19 + mark + ONE * 10 + "…"
    assert entries._cut(ONE * 5 + mark + ONE * 20, 13) == ONE * 5 + mark + ONE * 6 + "…"
    for other in "·：":
        assert entries._cut(ONE * 20 + other + ONE * 20, 31) == ONE * 20 + other + ONE * 9 + "…"


def test_refill_gives_saved_characters_to_the_first_cut_item(monkeypatch):
    """乙退到标点省下的 9 字回补给甲；不做标点回补时甲停在 79 字加「…」，合计 111 字。"""
    items = [A * 100, B * 30 + "，" + C * 69]
    assert fit_items(0, items) == [A * 88 + "…", B * 30 + "…"]
    monkeypatch.setattr(entries, "ENTRY_REFILL_PASSES", 0)
    shown = fit_items(0, items)
    assert shown == [A * 79 + "…", B * 30 + "…"] and sum(len(text) for text in shown) == 111


@pytest.mark.parametrize("length", [2, 10, 50, 51, 200])
def test_the_closing_ellipsis_never_shortens_verified_lines(length):
    """核验已占 120 字时任何自述都不渲染；119 字时任何长度至少 2 的单项自述都只剩一项「…」。"""
    assert fit_items(120, [ONE * length]) == []
    assert fit_items(120, [ONE * length, TWO * 5]) == []
    assert fit_items(119, [ONE * length]) == ["…"]


def _line(text: str, bare: str | None = None) -> EntryLine:
    """一行核验：短句与去标题行（缺省与短句相同）。"""
    return EntryLine("ending", text, text, text if bare is None else bare, "生活", ())


def test_verified_lines_within_120_are_kept_as_they_are():
    """三行合计恰 120 字照写，不换去标题行。"""
    lines = [_line(A * 40, "x"), _line(B * 40, "y"), _line(C * 40, "z")]
    assert fit_verified(lines) == [A * 40, B * 40, C * 40]


def test_verified_backstop_first_drops_title_lists():
    """两行带标题段、合计 130 字，去掉标题段后不超过 120 字：写两行去标题行，没有「另N项」。"""
    lines = [_line(A * 50 + "，《" + B * 12 + "》", A * 50), _line(C * 50 + "，《" + D * 12 + "》", C * 50)]
    assert sum(len(line.text) for line in lines) == 130
    assert fit_verified(lines) == [A * 50, C * 50]


def test_verified_backstop_keeps_whole_lines_and_counts_the_rest():
    """去标题后长度 60、50、30：写前两行加「另1项…」；60、50、30、25：写前两行加「另2项…」。"""
    lines = [_line(A * 70, A * 60), _line(B * 60, B * 50), _line(C * 30)]
    assert fit_verified(lines) == [A * 60, B * 50, "另1项…"]
    assert fit_verified([*lines, _line(D * 25)]) == [A * 60, B * 50, "另2项…"]


def test_verified_backstop_cuts_a_first_line_that_alone_is_too_long():
    """单行 130 字写前 119 字加「…」；118、10 两行写第一行前 115 字加「…」与「另1项…」，合计 120 字。"""
    assert fit_verified([_line(A * 130)]) == [A * 119 + "…"]
    shown = fit_verified([_line(A * 118), _line(B * 10)])
    assert shown == [A * 115 + "…", "另1项…"] and sum(len(text) for text in shown) == 120


def test_short_name_strips_one_start_suffix_and_one_leading_jinxing(caplog):
    """短名去掉第一个开始时刻后缀，再去掉开头的一个「进行」；名称为空是完整性故障。"""
    assert short_name("进行处理消息与收尾（10月12日 08:34 起）") == "处理消息与收尾"
    assert short_name("进行进行曲练习（1月5日 08:00 起）") == "进行曲练习"
    assert short_name("驾车前往练秋湖研发中心（10月12日 08:03 起）") == "驾车前往练秋湖研发中心"
    with pytest.raises(IntegrityError, match="^Activity has no frozen name$"):
        short_name("")
    assert "Activity has no frozen name" in caplog.text
