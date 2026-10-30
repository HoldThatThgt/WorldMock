"""search_memory 的回答是纯函数（docs/design/06-agent.md「检索记忆」）：在合成的 MemorySources 上钉住时间写法与成立条件、
关键词匹配、按类别切分、今天条目、按日期、区间、月与年取单元、「未整理」的退路、6,400 字的装填与次序、标签与提示、结果形状。

合成的今天缺省是 2026-10-12（周一），此刻 11:50，可查的最早日期是 2026-10-08；查月、年的用例另给更晚的今天。
"""

from __future__ import annotations

import json

import pytest

from worldmock.foundation.errors import BusinessRuleError
from worldmock.simulation_engine.observation.memory.search import (
    MEMORY_SEARCH_RESULT_CHARS,
    MemorySources,
    SearchDigest,
    SearchEntry,
    answer_search,
)
from worldmock.simulation_engine.observation.wording.guidance import query_rejection
from worldmock.state.interaction.requests import SearchMemoryRequest
from worldmock.state.model.memory import MEMORY_CATEGORIES

TODAY, NOW, EARLIEST = "2026-10-12", "11:50", "2026-10-08"
_PARTS = ("核验", "当时查到", "自述")
_TOP_KEYS = ("类别", "时间", "关键词", "结果", "未列出")


def _entry(clock: str, *rows: tuple[str, str, str]) -> SearchEntry:
    """一条可检索的条目：rows 是（类别，部分，文字），部分是核验、当时查到或自述；类别串按首次出现的次序。"""
    categories = list(dict.fromkeys(category for category, _, _ in rows))

    def shape(category: str, picked: list[tuple[str, str, str]]) -> dict:
        """这一组行的全文形状。"""
        item: dict = {"时刻": clock, "类别": category}
        for part in _PARTS:
            texts = [text for _, kind, text in picked if kind == part]
            if texts:
                item[part] = texts
        return item

    full = shape("、".join(categories), list(rows))
    sliced = {category: shape(category, [row for row in rows if row[0] == category])
              for category in MEMORY_CATEGORIES if category in categories}
    return SearchEntry(clock=clock, full=full, sliced=sliced)


def _digest(period: str, unsorted: tuple[str, ...] = (), tier: str = "day", **texts: str) -> SearchDigest:
    """一期回顾（缺省是日回顾）：texts 与 unsorted 用 food、work、life 写类别，换成中文类别。"""
    names = dict(zip(("food", "work", "life"), MEMORY_CATEGORIES))
    return SearchDigest(tier, period, {names[key]: text for key, text in texts.items()},
                        frozenset(names[key] for key in unsorted))


def _sources(entries=(), digests=(), past=None, today: str = TODAY) -> MemorySources:
    """合成的检索输入；past 是往日日期 → 那天全部条目，today 是冻结观察的本地日期。"""
    days = dict(past or {})

    def past_entries(day: str) -> tuple[SearchEntry, ...]:
        """往日的全部条目；没有登记的日子不该被读。"""
        return tuple(days[day])

    return MemorySources(today, NOW, EARLIEST, tuple(entries), tuple(digests), past_entries)


def _ask(sources: MemorySources, time: str, keyword: str | None = None, category: str = "全部") -> dict:
    """一次检索的回答。"""
    return answer_search(SearchMemoryRequest(category=category, time=time, keyword=keyword), sources)


def _refusal(sources: MemorySources, time: str, keyword: str | None = None) -> dict:
    """一次被拒的检索经 query_rejection 写成的五字段。"""
    with pytest.raises(BusinessRuleError) as failure:
        _ask(sources, time, keyword)
    return query_rejection(failure.value)


_MORNING = _entry("08:00", ("饮食", "核验", "吃了豆浆油条"))
_LATE = _entry("09:30", ("工作", "核验", "开了周会"), ("工作", "自述", "进展·周会：定了排期"))
_NOON = _entry("11:40", ("生活", "核验", "查完淘宝·纸巾"), ("生活", "当时查到", "纸巾 12 元"))
_AFTER = _entry("11:55", ("生活", "核验", "此刻之后的一条"))


def test_today_forms_select_entries_by_clock() -> None:
    """「今天」取 00:00 到此刻；「今天 HH:MM」只取那一分钟；「今天 A~B」终点晚于此刻时截到此刻，「时间」照抄原文。"""
    sources = _sources([_MORNING, _LATE, _NOON, _AFTER])
    assert _ask(sources, "今天")["结果"] == [_NOON.full, _LATE.full, _MORNING.full]
    assert _ask(sources, "今天 09:30")["结果"] == [_LATE.full]
    clipped = _ask(sources, "今天 09:00~23:00")
    assert clipped["结果"] == [_NOON.full, _LATE.full] and clipped["时间"] == "今天 09:00~23:00"
    assert _ask(sources, "今天 08:01~09:29")["结果"] == []


def test_todays_date_is_the_same_as_today() -> None:
    """写今天的日期与写「今天」得到同样的结果，只有回显的「时间」不同。"""
    sources = _sources([_MORNING, _LATE], [_digest("2026-10-11", food="昨天的早饭。")])
    dated, today = _ask(sources, TODAY), _ask(sources, "今天")
    assert dated["时间"] == TODAY and {**dated, "时间": "今天"} == today


@pytest.mark.parametrize(("time", "reason"), [
    ("2026-02-30", "The time 2026-02-30 is not a real date or clock time."),
    ("今天 24:00", "The time 今天 24:00 is not a real date or clock time."),
    ("今天 12:60", "The time 今天 12:60 is not a real date or clock time."),
    ("２０２６-１０-０９", "The time ２０２６-１０-０９ is not a real date or clock time."),
    ("0000", "The time 0000 is not a real date or clock time."),
    ("2026-10-13", "The time 2026-10-13 starts after today 2026-10-12."),
    ("2026-11", "The time 2026-11 starts after today 2026-10-12."),
    ("2027", "The time 2027 starts after today 2026-10-12."),
    ("今天 12:00", "The time 今天 12:00 starts after the current time 11:50."),
    ("今天 12:00~13:00", "The time 今天 12:00~13:00 starts after the current time 11:50."),
    ("今天 10:00~09:00", "The time range 今天 10:00~09:00 ends before it starts."),
    ("2026-10-05~2026-10-01", "The time range 2026-10-05~2026-10-01 ends before it starts."),
    ("2026-09-01~2026-10-02", "The date range 2026-09-01~2026-10-02 spans more than 31 days."),
])
def test_invalid_times_are_memory_time_invalid(time: str, reason: str, caplog) -> None:
    """日历或钟点不成立（含非 ASCII 数字）、起点晚于今天或此刻、区间反向、超过 31 天：MEMORY_TIME_INVALID 的五字段，原因
    逐字，明细指向 time 参数并带原文；先写英文错误日志。"""
    refusal = _refusal(_sources(), time)
    assert refusal == {
        "error_code": "MEMORY_TIME_INVALID", "reason": reason,
        "fix": ("Use a real date, month or year that does not start after today, a range of at most 31 days, "
                "or a clock time of today that does not start after now."),
        "items": [{"path": ["tool_calls", 0, "function", "arguments", "time"], "written": time,
                   "error_code": "MEMORY_TIME_INVALID", "allowed_values": [],
                   "expected": "A past period in one of the declared time forms is required.", "why": reason,
                   "candidates": []}],
        "unmet": []}
    assert f"Memory search time is invalid: {reason}" in caplog.text


@pytest.mark.parametrize("time", ["2026-09-12~2026-10-12", "2026-10-01~2026-10-31", "2026-10", "2026", "今天 00:00",
                                  "今天 11:50", "2026-10-08~2026-10-08"])
def test_thirty_one_day_range_and_periods_ending_after_today_are_valid(time: str) -> None:
    """恰好 31 天的区间、末日晚于今天的区间、包含今天的月与年、今天零点与此刻那一分钟都成立。"""
    assert list(_ask(_sources(), time))[:5] == list(_TOP_KEYS)


def test_all_time_without_keyword_is_memory_keyword_required(caplog) -> None:
    """时间写「全部」而关键词为 null：MEMORY_KEYWORD_REQUIRED，原因与改法取默认说法，明细指向 keyword。"""
    assert _refusal(_sources(), "全部") == {
        "error_code": "MEMORY_KEYWORD_REQUIRED", "reason": "Searching time 全部 requires a keyword.",
        "fix": "Write a keyword, or replace 全部 with a date, range, month or year.",
        "items": [{"path": ["tool_calls", 0, "function", "arguments", "keyword"], "written": None,
                   "error_code": "MEMORY_KEYWORD_REQUIRED", "allowed_values": [],
                   "expected": "A keyword is required when time is 全部.",
                   "why": "Searching time 全部 requires a keyword.", "candidates": []}],
        "unmet": []}
    assert "Memory search over all time has no keyword" in caplog.text


def test_keyword_words_match_case_insensitively_and_all_must_appear() -> None:
    """按空白（含全角空格）拆词，不分大小写，每个词都要出现；词不跨字符串拼接；键名与时刻不参与匹配。"""
    coffee = _entry("08:30", ("饮食", "核验", "在 Starbucks 买了拿铁"), ("饮食", "自述", "咖啡有点苦"))
    sources = _sources([coffee])
    assert _ask(sources, "今天", "starbucks 拿铁")["结果"] == [coffee.full]
    assert _ask(sources, "今天", "拿铁　STARBUCKS")["结果"] == [coffee.full]
    assert _ask(sources, "今天", "拿铁 奶茶")["结果"] == []
    assert _ask(sources, "今天", "拿铁咖啡")["结果"] == []
    for key in ("核验", "自述", "08:30", "饮食"):
        assert _ask(sources, "今天", key)["结果"] == []


def test_category_slices_today_entries_before_matching() -> None:
    """「工作、饮食」的条目查饮食只留饮食的行与项，「类别」写饮食；只在工作行里的词查饮食查不到。"""
    mixed = _entry("11:10", ("工作", "核验", "开会讨论预算"), ("饮食", "核验", "点了外卖盒饭"))
    sources = _sources([mixed])
    assert _ask(sources, "今天", category="饮食")["结果"] == [{"时刻": "11:10", "类别": "饮食", "核验": ["点了外卖盒饭"]}]
    assert _ask(sources, "今天", "预算", category="饮食")["结果"] == []
    assert _ask(sources, "今天", "预算", category="工作")["结果"] == [mixed.sliced["工作"]]


_UNSORTED_DAY = "2026-10-09"
_RAW = (_entry("07:50", ("生活", "核验", "在家醒来")), _entry("20:15", ("生活", "核验", "看了一集纪录片")),
        _entry("20:15", ("生活", "自述", "纪录片拍得真好")), _entry("24:00", ("生活", "核验", "睡觉跨过零点")))


def test_date_gives_digests_unsorted_raw_entries_and_nothing_for_no_record() -> None:
    """一天三类：饮食有日回顾给整项；生活「未整理」给那天这一类的全部原始条目（零点收尾条目在最前，同一时刻按条目次序
    倒排）；工作没有记录就没有这一类。"""
    sources = _sources(digests=[_digest(_UNSORTED_DAY, ("life",), food="早上吃了包子。")], past={_UNSORTED_DAY: _RAW})
    answer = _ask(sources, _UNSORTED_DAY)
    assert answer["结果"] == {
        "饮食": [{"日期": _UNSORTED_DAY, "类别": "饮食", "回顾": "早上吃了包子。"}],
        "生活": [{"日期": _UNSORTED_DAY, "类别": "生活", "未整理": True,
                  "条目": [_RAW[3].sliced["生活"], _RAW[2].sliced["生活"], _RAW[1].sliced["生活"],
                           _RAW[0].sliced["生活"]]}]}
    assert answer["未列出"] == [] and "提示" not in answer
    assert _ask(sources, _UNSORTED_DAY, category="工作")["结果"] == []


def test_date_with_keyword_inlines_only_matching_raw_entries() -> None:
    """带关键词按日期查：「未整理」的类只给匹配的原始条目，一条都不匹配就没有这一项；有回顾的类只在回顾匹配时出现。"""
    sources = _sources(digests=[_digest(_UNSORTED_DAY, ("life",), food="早上吃了包子。")], past={_UNSORTED_DAY: _RAW})
    assert _ask(sources, _UNSORTED_DAY, "纪录片")["结果"] == {"生活": [{
        "日期": _UNSORTED_DAY, "类别": "生活", "未整理": True,
        "条目": [_RAW[2].sliced["生活"], _RAW[1].sliced["生活"]]}]}
    assert _ask(sources, _UNSORTED_DAY, "包子")["结果"] == {
        "饮食": [{"日期": _UNSORTED_DAY, "类别": "饮食", "回顾": "早上吃了包子。"}]}
    assert _ask(sources, _UNSORTED_DAY, "火锅")["结果"] == {}


def test_range_and_month_with_keyword_give_matching_digests_and_list_unsorted_days() -> None:
    """区间加关键词只给匹配的日回顾，「未整理」的日子不做筛选、照样进未列出；月加关键词同时给该月往日里匹配的日回顾与匹配的
    月回顾（当月还没有月回顾时与同一段区间相同）。"""
    digests = [_digest("2026-10-08", life="去江边散步，风很大。"), _digest("2026-10-09", ("life",)),
               _digest("2026-10-10", life="在家看书。"), _digest("2026-10-11", life="又去江边散步了。")]
    sources = _sources(digests=digests)
    ranged = _ask(sources, "2026-10-08~2026-10-11", "江边", category="生活")
    assert ranged["结果"] == [{"日期": "2026-10-11", "类别": "生活", "回顾": "又去江边散步了。"},
                            {"日期": "2026-10-08", "类别": "生活", "回顾": "去江边散步，风很大。"}]
    assert ranged["未列出"] == ["2026-10-09 生活（未整理，按日期单独查）"]
    assert ranged["提示"] == "未整理的日子按日期单独查"
    month = _ask(sources, "2026-10", "江边", category="生活")
    assert month["结果"] == ranged["结果"] and month["未列出"] == ranged["未列出"]
    october = _digest("2026-10", tier="month", life="十月常去江边散步。")
    later = _ask(_sources(digests=[*digests, october], today="2026-11-03"), "2026-10", "江边", category="生活")
    assert later["结果"] == [{"月份": "2026-10", "类别": "生活", "回顾": "十月常去江边散步。"}, *ranged["结果"]]
    assert later["未列出"] == ranged["未列出"]


def test_month_prefers_month_digest_and_falls_back_to_day_digests() -> None:
    """不带关键词查某月：有月回顾时，这一类有正文给月回顾整项（不再给这一类的日回顾）；这一类「未整理」改给该月往日这一类的
    日单元（日回顾整项，「未整理」的日子进未列出）；没有记录的类不给；当月还没有月回顾时给该月往日的日单元。"""
    digests = [_digest("2026-10-08", food="喝了咖啡。", life="去江边散步。"), _digest("2026-10-09", ("life",)),
               _digest("2026-10-31", life="看了一场电影。"), _digest("2026-10", ("life",), tier="month", food="十月常喝咖啡。"),
               _digest("2026-11-02", food="吃了面。"), _digest("2026-11-05", life="在家看书。")]
    sources = _sources(digests=digests, today="2026-11-12")
    answer = _ask(sources, "2026-10")
    assert answer["结果"] == {
        "饮食": [{"月份": "2026-10", "类别": "饮食", "回顾": "十月常喝咖啡。"}],
        "生活": [{"日期": "2026-10-31", "类别": "生活", "回顾": "看了一场电影。"},
                 {"日期": "2026-10-08", "类别": "生活", "回顾": "去江边散步。"}]}
    assert answer["未列出"] == ["2026-10-09 生活（未整理，按日期单独查）"]
    assert _ask(sources, "2026-10", category="工作")["结果"] == []
    assert _ask(sources, "2026-11", category="饮食")["结果"] == [{"日期": "2026-11-02", "类别": "饮食", "回顾": "吃了面。"}]
    assert _ask(sources, "2026-11")["结果"] == {"饮食": [{"日期": "2026-11-02", "类别": "饮食", "回顾": "吃了面。"}],
                                                "生活": [{"日期": "2026-11-05", "类别": "生活", "回顾": "在家看书。"}]}


def test_year_prefers_year_digest_then_months_then_days() -> None:
    """不带关键词查某年：有年回顾时这一类有正文给年回顾整项；这一类「未整理」改给该年各月这一类的月单元（有月回顾的给月回顾，
    月回顾里「未整理」或没有月回顾的月份给日单元）；当年还没有年回顾时给各月的月单元。"""
    digests = [_digest("2026-10-15", life="去江边。"), _digest("2026-10", tier="month", life="十月常去江边。"),
               _digest("2026-11-03", life="看书。"), _digest("2026-11-04", ("life",)),
               _digest("2026-11", ("life",), tier="month"), _digest("2026-12-02", life="跑步。"),
               _digest("2026", ("life",), tier="year", food="这一年常喝咖啡。"),
               _digest("2027-01-20", food="吃了饺子。"), _digest("2027-01", tier="month", food="一月吃得清淡。"),
               _digest("2027-02-03", food="吃了汤圆。")]
    sources = _sources(digests=digests, today="2027-02-10")
    answer = _ask(sources, "2026")
    assert answer["结果"] == {
        "饮食": [{"年份": "2026", "类别": "饮食", "回顾": "这一年常喝咖啡。"}],
        "生活": [{"日期": "2026-12-02", "类别": "生活", "回顾": "跑步。"},
                 {"日期": "2026-11-03", "类别": "生活", "回顾": "看书。"},
                 {"月份": "2026-10", "类别": "生活", "回顾": "十月常去江边。"}]}
    assert answer["未列出"] == ["2026-11-04 生活（未整理，按日期单独查）"]
    assert _ask(sources, "2027", category="饮食")["结果"] == [
        {"日期": "2027-02-03", "类别": "饮食", "回顾": "吃了汤圆。"}, {"月份": "2027-01", "类别": "饮食", "回顾": "一月吃得清淡。"}]


def test_keyword_periods_search_every_tier_newest_first() -> None:
    """年加关键词给该年往日里匹配的日、月、年回顾：按末日从新到旧，末日相同时日回顾、月回顾、年回顾依次；「未整理」的日子进
    未列出；月加关键词给该月往日里匹配的日回顾与月回顾，不给年回顾。"""
    digests = [_digest("2026-10-15", food="喝了一杯咖啡。"), _digest("2026-10", tier="month", food="十月天天咖啡。"),
               _digest("2026-11-04", ("food",)), _digest("2026-11-05", food="吃了面。"),
               _digest("2026-12-31", food="跨年夜喝了咖啡。"), _digest("2026-12", tier="month", food="十二月咖啡喝得少了。"),
               _digest("2026", tier="year", food="这一年爱喝咖啡。")]
    sources = _sources(digests=digests, today="2027-01-05")
    answer = _ask(sources, "2026", "咖啡", category="饮食")
    assert answer["结果"] == [
        {"日期": "2026-12-31", "类别": "饮食", "回顾": "跨年夜喝了咖啡。"},
        {"月份": "2026-12", "类别": "饮食", "回顾": "十二月咖啡喝得少了。"},
        {"年份": "2026", "类别": "饮食", "回顾": "这一年爱喝咖啡。"},
        {"月份": "2026-10", "类别": "饮食", "回顾": "十月天天咖啡。"},
        {"日期": "2026-10-15", "类别": "饮食", "回顾": "喝了一杯咖啡。"}]
    assert answer["未列出"] == ["2026-11-04 饮食（未整理，按日期单独查）"]
    assert _ask(sources, "2026-12", "咖啡", category="饮食")["结果"] == answer["结果"][:2]
    grouped = _ask(sources, "2026", "咖啡")
    assert list(grouped["结果"]) == ["饮食"] and grouped["结果"]["饮食"] == answer["结果"]


def test_today_entries_are_newest_first() -> None:
    """今天条目按发布次序倒排：同一分钟的两条，后发布的在前。"""
    first = _entry("10:00", ("生活", "核验", "先到楼下取快递"))
    second = _entry("10:00", ("生活", "核验", "再去倒垃圾"))
    assert _ask(_sources([_MORNING, first, second]), "今天")["结果"] == [second.full, first.full, _MORNING.full]


def test_unsorted_day_overflow_lists_entry_labels_and_hints_keyword() -> None:
    """按日期查「未整理」的两类：内联条目从晚到早整条放入，放不下的那一条起以「YYYY-MM-DD HH:MM 类别」进未列出（零点收尾
    条目写 24:00），提示恰为「加关键词缩小范围」。"""
    food = (_entry("08:00", ("饮食", "核验", "早" * 1000)), _entry("12:00", ("饮食", "核验", "午" * 3000)),
            _entry("23:00", ("饮食", "核验", "夜" * 3000)))
    life = (_entry("21:00", ("生活", "核验", "看书")), _entry("24:00", ("生活", "核验", "睡觉跨过零点")))
    sources = _sources(digests=[_digest(_UNSORTED_DAY, ("food", "life"))], past={_UNSORTED_DAY: (*food, *life)})
    answer = _ask(sources, _UNSORTED_DAY)
    assert answer["结果"] == {"饮食": [{"日期": _UNSORTED_DAY, "类别": "饮食", "未整理": True,
                                        "条目": [food[2].sliced["饮食"], food[1].sliced["饮食"]]}]}
    assert answer["未列出"] == ["2026-10-09 08:00 饮食", "2026-10-09 24:00 生活", "2026-10-09 21:00 生活"]
    assert answer["提示"] == "加关键词缩小范围"


def test_range_lists_unsorted_days_and_stops_at_yesterday() -> None:
    """区间包含今天时往日只到昨天，今天的条目不在结果里；「未整理」的日子写「（未整理，按日期单独查）」，提示为第 2 句；
    首日就是今天的区间没有往日部分，结果为空。"""
    digests = [_digest("2026-10-08", life="去江边散步。"), _digest("2026-10-09", ("life",)),
               _digest("2026-10-11", life="在家看书。")]
    sources = _sources([_NOON], digests)
    answer = _ask(sources, "2026-10-08~2026-10-12", category="生活")
    assert answer["结果"] == [{"日期": "2026-10-11", "类别": "生活", "回顾": "在家看书。"},
                            {"日期": "2026-10-08", "类别": "生活", "回顾": "去江边散步。"}]
    assert answer["未列出"] == ["2026-10-09 生活（未整理，按日期单独查）"]
    assert answer["提示"] == "未整理的日子按日期单独查"
    assert _ask(sources, "2026-10-12~2026-10-20") == {"类别": "全部", "时间": "2026-10-12~2026-10-20", "关键词": None,
                                                      "结果": {}, "未列出": []}


def test_fill_stops_at_the_first_unit_that_does_not_fit() -> None:
    """遇到第一个放不下的就停：较大的新项放不下后，较小的旧项也进未列出；恰好 6,400 字放得下，6,401 放不下；计数只算回顾
    正文与条目各字符串，不算时刻、类别与日期。"""
    digests = [_digest("2026-10-11", life="甲" * 6000), _digest("2026-10-10", life="乙" * 500),
               _digest("2026-10-09", life="丙" * 10)]
    answer = _ask(_sources(digests=digests), "2026-10-08~2026-10-11", category="生活")
    assert [item["日期"] for item in answer["结果"]] == ["2026-10-11"]
    assert answer["未列出"] == ["2026-10-10 生活", "2026-10-09 生活"]
    assert answer["提示"] == "范围太大，换更短的时间、指定类别或加关键词"
    exact = [_entry("09:00", ("生活", "核验", "丁" * 3000), ("生活", "当时查到", "戊" * 400)),
             _entry("10:00", ("生活", "自述", "己" * (MEMORY_SEARCH_RESULT_CHARS - 3400)))]
    assert _ask(_sources(exact), "今天")["未列出"] == []
    over = [exact[0], _entry("10:00", ("生活", "自述", "己" * (MEMORY_SEARCH_RESULT_CHARS - 3399)))]
    answer = _ask(_sources(over), "今天")
    assert answer["结果"] == [over[1].full] and answer["未列出"] == ["今天 09:00"]


def test_hints_join_in_order_and_labels_are_unique() -> None:
    """同时有「上限」与「未整理」时提示为两句、以「；」相接；同一分钟放不下的两条今天条目只留一个「今天 HH:MM」。"""
    digests = [_digest("2026-10-11", life="甲" * 6401), _digest("2026-10-09", ("life",)),
               _digest("2026-10-08", life="乙")]
    answer = _ask(_sources(digests=digests), "2026-10-08~2026-10-11", category="生活")
    assert answer["未列出"] == ["2026-10-11 生活", "2026-10-09 生活（未整理，按日期单独查）", "2026-10-08 生活"]
    assert answer["提示"] == "范围太大，换更短的时间、指定类别或加关键词；未整理的日子按日期单独查"
    entries = [_entry("09:00", ("生活", "核验", "早到的一条")), _entry("09:00", ("生活", "核验", "晚到的一条")),
               _entry("11:00", ("生活", "核验", "甲" * 6401))]
    answer = _ask(_sources(entries), "今天")
    assert answer["结果"] == [] and answer["未列出"] == ["今天 11:00", "今天 09:00"]


def test_all_time_searches_today_entries_and_every_digest() -> None:
    """时间写「全部」：今天条目里匹配的在前，往日的日、月、年回顾里匹配的在后（末日相同时日、月、年依次），「未整理」的日子
    进未列出，月、年回顾「未整理」的类自身不进；单一类别是一个列表，「全部」类别按类别分组并在最前加「今天」。"""
    today = _entry("08:30", ("饮食", "核验", "买了一杯咖啡"))
    digests = [_digest("2026-10-10", ("life",), food="下午喝了咖啡。"), _digest("2026-10-11", food="没喝咖啡，喝了茶。")]
    sources = _sources([today, _MORNING], digests)
    single = _ask(sources, "全部", "咖啡", category="饮食")
    assert single["结果"] == [today.sliced["饮食"], {"日期": "2026-10-11", "类别": "饮食", "回顾": "没喝咖啡，喝了茶。"},
                            {"日期": "2026-10-10", "类别": "饮食", "回顾": "下午喝了咖啡。"}]
    assert single["未列出"] == []
    grouped = _ask(sources, "全部", "咖啡")
    assert list(grouped["结果"]) == ["今天", "饮食"]
    assert grouped["结果"]["今天"] == [today.full] and grouped["结果"]["饮食"] == single["结果"][1:]
    assert grouped["未列出"] == ["2026-10-10 生活（未整理，按日期单独查）"]
    tiers = [_digest("2025-11", ("food",), tier="month"), _digest("2025-12-31", food="跨年夜喝了咖啡。"),
             _digest("2025-12", tier="month", food="十二月常喝咖啡。"), _digest("2025", tier="year", food="这一年爱喝咖啡。"),
             _digest("2026-01-02", ("food",)), _digest("2026-01-03", food="吃了面。")]
    every = _ask(_sources([today], tiers, today="2026-01-05"), "全部", "咖啡", category="饮食")
    assert every["结果"] == [today.sliced["饮食"], {"日期": "2025-12-31", "类别": "饮食", "回顾": "跨年夜喝了咖啡。"},
                           {"月份": "2025-12", "类别": "饮食", "回顾": "十二月常喝咖啡。"},
                           {"年份": "2025", "类别": "饮食", "回顾": "这一年爱喝咖啡。"}]
    assert every["未列出"] == ["2026-01-02 饮食（未整理，按日期单独查）"]


def test_all_category_groups_past_items_and_omits_empty_categories() -> None:
    """「全部」类别的往日结果按饮食、工作、生活分组，没有项的类别不列；一项都没有时是空对象。"""
    digests = [_digest("2026-10-10", life="在家看书。", food="吃了面。"), _digest("2026-10-11", work="加了班。")]
    answer = _ask(_sources(digests=digests), "2026-10-08~2026-10-11")
    assert list(answer["结果"]) == ["饮食", "工作", "生活"]
    assert answer["结果"]["工作"] == [{"日期": "2026-10-11", "类别": "工作", "回顾": "加了班。"}]
    only_food = _ask(_sources(digests=[_digest("2026-10-10", food="吃了面。")]), "2026-10-10")
    assert list(only_food["结果"]) == ["饮食"]
    assert _ask(_sources(), "2026-10-08~2026-10-11")["结果"] == {}


@pytest.mark.parametrize(("time", "category", "empty"), [
    ("2026-10-07", "饮食", []), ("2026-10-01~2026-10-07", "全部", {}), ("2026-09", "生活", []), ("2025", "全部", {})])
def test_period_before_the_earliest_date_is_empty_with_the_earliest_date(time: str, category: str, empty) -> None:
    """这一期的末日早于最早日期：空结果，未列出为空，未列出之后加「最早日期」，没有提示；跨过最早日期的一期照常取。"""
    sources = _sources(digests=[_digest("2026-10-08", food="吃了面。", life="散步。")])
    answer = _ask(sources, time, category=category)
    assert list(answer) == [*_TOP_KEYS, "最早日期"]
    assert (answer["结果"], answer["未列出"], answer["最早日期"]) == (empty, [], EARLIEST)
    crossing = _ask(sources, "2026-10-01~2026-10-09", category="饮食")
    assert "最早日期" not in crossing and crossing["结果"] == [{"日期": "2026-10-08", "类别": "饮食", "回顾": "吃了面。"}]


def test_result_keys_are_fixed_and_never_include_result() -> None:
    """各种写法的顶层键都在类别、时间、关键词、结果、未列出、提示、最早日期之内，前五个次序固定，从来没有 result。"""
    digests = [_digest("2026-10-11", life="甲" * 6401), _digest("2026-10-10", ("life",))]
    sources = _sources([_MORNING, _LATE], digests, past={"2026-10-10": _RAW})
    for time, keyword in (("今天", None), ("今天 08:00", None), (TODAY, None), ("全部", "甲"), ("2026-10-10", None),
                          ("2026-10-08~2026-10-11", None), ("2026-10", None), ("2026", "甲"), ("2026-10-01", None)):
        answer = _ask(sources, time, keyword)
        assert list(answer)[:5] == list(_TOP_KEYS) and "result" not in answer
        assert set(answer) <= {*_TOP_KEYS, "提示", "最早日期"}


def test_answers_are_deterministic() -> None:
    """同一份输入两次回答逐字节相同。"""
    sources = _sources([_MORNING, _LATE, _NOON], [_digest(_UNSORTED_DAY, ("life",), food="吃了包子。")],
                       past={_UNSORTED_DAY: _RAW})
    for time in ("今天", _UNSORTED_DAY, "2026-10-08~2026-10-11"):
        first, second = _ask(sources, time), _ask(sources, time)
        assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
