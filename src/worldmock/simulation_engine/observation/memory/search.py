"""@brief search_memory 的回答：在冻结观察的事件序号及以前，按类别与时间取今天条目和日、月、年回顾，可加关键词；只读，不写事件。"""

from __future__ import annotations

import calendar
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING, Literal

from worldmock.foundation.errors import BusinessRuleError
from worldmock.foundation.time.readable import clock_phrase, local_date
from worldmock.simulation_engine.observation.memory.days import past_day_entries, published_today, shift_date
from worldmock.simulation_engine.observation.memory.digests import registered_digests
from worldmock.simulation_engine.observation.memory.entries import TodayEntry, entry_full
from worldmock.simulation_engine.observation.memory.layout import earliest_date
from worldmock.simulation_engine.observation.wording.guidance import Refusal, refusal_for_code
from worldmock.state.interaction.errors import ErrorItem
from worldmock.state.interaction.requests import SearchMemoryRequest
from worldmock.state.model.memory import MEMORY_CATEGORIES, MEMORY_DIGEST_KEYS, MEMORY_DIGEST_TIERS

if TYPE_CHECKING:
    from worldmock.recording_engine.events.models import Event
    from worldmock.simulation_engine.world.context import EngineContext

#: 一次结果里放进去的回顾正文与条目字符串的字数（len）之和的上限。
MEMORY_SEARCH_RESULT_CHARS = 6400
#: 日期区间至多的天数（两端都含）。
MEMORY_RANGE_MAX_DAYS = 31
#: 三句提示：未列出的原因分别是「上限」「未整理」「按日放不下」。
_HINT_TOO_LARGE = "范围太大，换更短的时间、指定类别或加关键词"
_HINT_UNSORTED = "未整理的日子按日期单独查"
_HINT_NARROW_DAY = "加关键词缩小范围"
#: 区间、月、年与全部时间里「未整理」那一天的标签后缀。
_UNSORTED_SUFFIX = "（未整理，按日期单独查）"
#: 未列出的原因 → 提示句，按提示的次序。
_HINTS = {"上限": _HINT_TOO_LARGE, "未整理": _HINT_UNSORTED, "按日放不下": _HINT_NARROW_DAY}
#: 类别与时间都可以写的「全部」，时间的「今天」，以及「全部」类别分组结果里今天条目的键。
_ALL = "全部"
_TODAY = "今天"
#: 「今天 HH:MM」与「今天 HH:MM~HH:MM」，只认 ASCII 数字。
_TODAY_CLOCKS = re.compile(r"今天 ([0-9]{2}:[0-9]{2})(?:~([0-9]{2}:[0-9]{2}))?")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_RANGE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}~[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MONTH = re.compile(r"([0-9]{4})-([0-9]{2})")
_YEAR = re.compile(r"[0-9]{4}")
#: 回顾的级 → 结果项里期次的键。
_PERIOD_KEYS = {"day": "日期", "month": "月份", "year": "年份"}
#: 带关键词（与「全部」时间）时各写法取哪几级回顾：区间只取日回顾，月取日、月回顾，年与全部时间取三级。
_KEYWORD_TIERS = {"range": ("day",), "month": ("day", "month"), "year": MEMORY_DIGEST_TIERS,
                  "all": MEMORY_DIGEST_TIERS}


@dataclass(frozen=True, slots=True)
class SearchPeriod:
    """@brief 解析后的检索时间。"""

    kind: Literal["today", "all", "day", "range", "month", "year"]
    #: today：起始时刻 HH:MM；day、range、month、year：首日 YYYY-MM-DD；all：空串。
    first: str
    #: today：终止时刻 HH:MM（已截到此刻）；day、range、month、year：末日 YYYY-MM-DD（不截到昨天）；all：空串。
    last: str
    #: day：YYYY-MM-DD；month：YYYY-MM；year：YYYY；其余：空串。
    period: str


@dataclass(frozen=True, slots=True)
class SearchEntry:
    """@brief 一条可检索的条目。"""

    #: 「时刻」HH:MM，零点收尾条目为 24:00。
    clock: str
    #: 今天条目的全文形状（全部类别）。
    full: dict
    #: 类别 → 按这一类切分后的全文，「类别」写这一类；只含「类别」串里出现的类别。
    sliced: dict[str, dict]


@dataclass(frozen=True, slots=True)
class SearchDigest:
    """@brief 检索一侧的一期回顾（一个 memory_digest_generated 事件），类别换成中文。"""

    #: 回顾的级。
    tier: Literal["day", "month", "year"]
    #: 期：YYYY-MM-DD、YYYY-MM 或 YYYY。
    period: str
    #: 类别（中文）→ 回顾正文，只含有正文的类别。
    texts: dict[str, str]
    #: 「未整理」的类别（中文）。
    unsorted: frozenset[str]


@dataclass(frozen=True, slots=True)
class MemorySources:
    """@brief 一次检索的全部输入，都取自截止序号及以前的事件与冻结输入。"""

    #: 本地日期 YYYY-MM-DD。
    today: str
    #: 此刻 HH:MM。
    now: str
    #: 记忆层「可查」的最早日期。
    earliest: str
    #: 今天的已发布条目，按发布次序。
    entries: tuple[SearchEntry, ...]
    #: 截止序号及以前的全部回顾期，按事件序号。
    digests: tuple[SearchDigest, ...]
    #: 往日日期 → 那天全部条目（含零点收尾条目），按条目次序。
    past_entries: Callable[[str], tuple[SearchEntry, ...]]


@dataclass(frozen=True, slots=True)
class _Piece:
    """@brief 装填的一项：未列出时的标签、计入上限的字数与结果项。"""

    label: str
    size: int
    item: dict


@dataclass(frozen=True, slots=True)
class _Unit:
    """@brief 一个检索单元：whole 是一条今天条目或一段回顾；inline 是按日期查「未整理」那一类的原始条目，按条装填；
    label 是区间、月、年与全部时间里「未整理」的一天，只进未列出。"""

    kind: Literal["whole", "inline", "label"]
    #: 「全部」类别分组时的键：类别，或今天条目的「今天」。
    group: str
    #: whole 恰一项，inline 是按装填次序的各条，label 为空。
    pieces: tuple[_Piece, ...]
    #: label 单元的标签；其余为空串。
    label: str
    #: inline 单元结果项的前三个键；其余为空对象。
    header: dict
    #: 往日单元的排序键：期末日从新到旧、级、整项先于内联、类别次序；今天条目为空元组。
    order: tuple


def search_memory(context: EngineContext, agent_id: int, request: SearchMemoryRequest, observation: Event) -> dict:
    """@brief 检索记忆的回答：从冻结观察所在序号及以前的事件与冻结输入取数，再按请求作答。"""
    return answer_search(request, memory_sources(context, agent_id, observation))


def memory_sources(context: EngineContext, agent_id: int, observation: Event) -> MemorySources:
    """@brief 一次检索的输入：今天、此刻、最早日期、今天的已发布条目、截止序号及以前的回顾与往日条目的取法；不读世界状态。

    :param observation: 本次决策的冻结观察；它的 event_seq 是截止序号，occurred_at 是此刻。
    """
    inputs, store = context.inputs, context.store
    zone, cutoff, moment = inputs.config.timezone, observation.event_seq, observation.occurred_at
    published = published_today(inputs, store, agent_id, moment, cutoff)
    events = sorted((event for event in registered_digests(store, agent_id).values() if event.event_seq <= cutoff),
                    key=lambda event: event.event_seq)
    return MemorySources(
        today=local_date(moment, zone),
        now=clock_phrase(moment, moment, zone),
        earliest=earliest_date(inputs.config.start_time, zone),
        entries=tuple(_search_entry(entry) for entry in published.entries),
        digests=tuple(_search_digest(event.data) for event in events),
        past_entries=lambda day: tuple(_search_entry(entry)
                                       for entry in past_day_entries(inputs, store, agent_id, day, moment)),
    )


def _search_entry(entry: TodayEntry) -> SearchEntry:
    """@brief 今天条目换成检索的条目：全文与按各类切分的全文。"""
    full = entry_full(entry)
    return SearchEntry(clock=full["时刻"], full=full,
                       sliced={category: part for category in MEMORY_CATEGORIES
                               if (part := entry_full(entry, category)) is not None})


def _search_digest(data: dict) -> SearchDigest:
    """@brief 一条回顾登记事件的正文换成检索一侧的一期回顾：有正文的类别与「未整理」的类别都换成中文。"""
    texts = {MEMORY_CATEGORIES[index]: data["digests"][key]["text"]
             for index, key in enumerate(MEMORY_DIGEST_KEYS) if data["digests"][key] is not None}
    unsorted = frozenset(MEMORY_CATEGORIES[MEMORY_DIGEST_KEYS.index(key)] for key in data["fallback"])
    return SearchDigest(data["tier"], data["period"], texts, unsorted)


def parse_search_time(text: str, today: str, now: str) -> SearchPeriod:
    """@brief 解析检索时间（只认 ASCII 数字的完整匹配）；不成立时以 MEMORY_TIME_INVALID 拒绝，原因按检查次序取第一条：
    日历或钟点不成立、起点晚于今天或此刻、区间反向、区间超过 31 天。

    :param text: 请求的 time 原文。
    :param today: 冻结观察的本地日期 YYYY-MM-DD。
    :param now: 冻结观察的本地时刻 HH:MM。
    """
    if text == _ALL:
        return SearchPeriod("all", "", "", "")
    if text == _TODAY:
        return SearchPeriod("today", "00:00", now, "")
    clocks = _TODAY_CLOCKS.fullmatch(text)
    if clocks is not None:
        return _clock_period(text, clocks.group(1), clocks.group(2), now)
    parsed = _calendar(text)
    if parsed is None:
        raise _time_invalid(text, f"The time {text} is not a real date or clock time.")
    kind, first, last, period = parsed
    if first > date.fromisoformat(today):
        raise _time_invalid(text, f"The time {text} starts after today {today}.")
    if kind == "day" and first.isoformat() == today:
        return SearchPeriod("today", "00:00", now, "")
    if first > last:
        raise _time_invalid(text, f"The time range {text} ends before it starts.")
    if kind == "range" and (last - first).days + 1 > MEMORY_RANGE_MAX_DAYS:
        raise _time_invalid(text, f"The date range {text} spans more than 31 days.")
    return SearchPeriod(kind, first.isoformat(), last.isoformat(), period)


def _clock_period(text: str, start: str, end: str | None, now: str) -> SearchPeriod:
    """@brief 今天的一个时刻或一段时刻：终点晚于此刻时截到此刻。"""
    if not _real_clock(start) or (end is not None and not _real_clock(end)):
        raise _time_invalid(text, f"The time {text} is not a real date or clock time.")
    if start > now:
        raise _time_invalid(text, f"The time {text} starts after the current time {now}.")
    if end is not None and start > end:
        raise _time_invalid(text, f"The time range {text} ends before it starts.")
    return SearchPeriod("today", start, start if end is None else min(end, now), "")


def _real_clock(clock: str) -> bool:
    """@brief 两位补零的 HH:MM 是否是一天里的钟点。"""
    hour, minute = clock.split(":")
    return int(hour) <= 23 and int(minute) <= 59


def _calendar(text: str) -> tuple[str, date, date, str] | None:
    """@brief 日期类写法（单日、区间、月、年）的首日、末日与期；不是这几种写法或日历不成立时为 None。"""
    try:
        if _DAY.fullmatch(text):
            day = date.fromisoformat(text)
            return "day", day, day, text
        if _RANGE.fullmatch(text):
            first, last = (date.fromisoformat(part) for part in text.split("~"))
            return "range", first, last, ""
        month = _MONTH.fullmatch(text)
        if month is not None:
            year, number = int(month.group(1)), int(month.group(2))
            first = date(year, number, 1)
            return "month", first, first.replace(day=calendar.monthrange(year, number)[1]), text
        if _YEAR.fullmatch(text):
            return "year", date(int(text), 1, 1), date(int(text), 12, 31), text
    except ValueError:
        return None
    return None


def _time_invalid(text: str, reason: str) -> BusinessRuleError:
    """@brief MEMORY_TIME_INVALID：写明具体原因，明细指向 time 参数；先写英文错误日志。"""
    logging.error("Memory search time is invalid: %s", reason)
    item = ErrorItem(("time",), text, "MEMORY_TIME_INVALID",
                     expected="A past period in one of the declared time forms is required.", why=reason)
    return Refusal("MEMORY_TIME_INVALID", reason, items=(item,)).error()


def _keyword_required() -> BusinessRuleError:
    """@brief MEMORY_KEYWORD_REQUIRED：原因取默认原因，明细指向 keyword 参数；先写英文错误日志。"""
    logging.error("Memory search over all time has no keyword")
    refusal = refusal_for_code("MEMORY_KEYWORD_REQUIRED")
    item = ErrorItem(("keyword",), None, "MEMORY_KEYWORD_REQUIRED",
                     expected="A keyword is required when time is 全部.", why=refusal.reason)
    return replace(refusal, items=(item,)).error()


def answer_search(request: SearchMemoryRequest, sources: MemorySources) -> dict:
    """@brief 纯函数：解析时间 →「全部」时间没有关键词时拒绝 → 这一期早于最早日期时返回空结果 → 取单元 → 从新到旧装填
    → 组装结果。"""
    period = parse_search_time(request.time, sources.today, sources.now)
    if period.kind == "all" and request.keyword is None:
        raise _keyword_required()
    if period.kind not in ("today", "all") and period.last < sources.earliest:
        return _assemble(request, period, ([], [], []), sources.earliest)
    scope = None if request.category == _ALL else request.category
    words = tuple((request.keyword or "").split())
    return _assemble(request, period, _fill(_units(sources, period, scope, words)), None)


def _units(sources: MemorySources, period: SearchPeriod, scope: str | None, words: tuple[str, ...]) -> list[_Unit]:
    """@brief 按写法取单元：今天条目在前（按发布次序倒排），往日单元在后（期末日从新到旧）。

    不带关键词的月、年取各类的月单元、年单元；区间、带关键词的月与年、全部时间取往日部分里这一写法所取各级回顾的单元。
    :param scope: 指定的类别；「全部」类别为 None。
    """
    categories = MEMORY_CATEGORIES if scope is None else (scope,)
    if period.kind == "today":
        return _today_units(sources, period, scope, words)
    if period.kind == "day":
        return _date_units(sources, period.period, categories, words)
    yesterday = shift_date(sources.today, -1)
    first, last = ("", yesterday) if period.kind == "all" else (period.first, min(period.last, yesterday))
    if period.kind in ("month", "year") and not words:
        span = _month_units if period.kind == "month" else _year_units
        past = [unit for category in categories for unit in span(sources, period.period, last, category)]
    else:
        past = [unit for digest in sources.digests if digest.tier in _KEYWORD_TIERS[period.kind]
                and first <= _digest_span(digest)[0] and _digest_span(digest)[1] <= last
                for unit in _digest_units(digest, categories, words)]
    today = _today_units(sources, period, scope, words) if period.kind == "all" else []
    return [*today, *sorted(past, key=lambda unit: unit.order)]


def _month_units(sources: MemorySources, month: str, last: str, category: str) -> list[_Unit]:
    """@brief 某月某一类的月单元：有月回顾事件时，这一类有正文给月回顾整项、「未整理」换成该月往日部分这一类的日单元、
    没有记录不给；没有月回顾事件时（当月或这一期没有压过）给该月往日部分这一类的日单元。

    :param last: 往日部分的末日（昨天或更早）。
    """
    digest = _registered(sources, "month", month)
    if digest is not None and (category in digest.texts or category not in digest.unsorted):
        return _digest_units(digest, (category,), ())
    return [unit for item in sources.digests if item.tier == "day" and item.period[:7] == month and item.period <= last
            for unit in _digest_units(item, (category,), ())]


def _year_units(sources: MemorySources, year: str, last: str, category: str) -> list[_Unit]:
    """@brief 某年某一类的年单元：有年回顾事件时，这一类有正文给年回顾整项、「未整理」换成该年各月这一类的月单元、没有记录
    不给；没有年回顾事件时给该年各月这一类的月单元。

    :param last: 往日部分的末日（昨天或更早）。
    """
    digest = _registered(sources, "year", year)
    if digest is not None and (category in digest.texts or category not in digest.unsorted):
        return _digest_units(digest, (category,), ())
    return [unit for number in range(1, 13) for unit in _month_units(sources, f"{year}-{number:02d}", last, category)]


def _registered(sources: MemorySources, tier: str, period: str) -> SearchDigest | None:
    """@brief 截止序号及以前这一级这一期的回顾；没有登记时为 None。"""
    return next((item for item in sources.digests if item.tier == tier and item.period == period), None)


def _digest_span(digest: SearchDigest) -> tuple[str, str]:
    """@brief 一期回顾覆盖的首日与末日，与检索时间的日期、月、年写法同一读法（_calendar）：日回顾是那天，月回顾是该月
    1 日到月末，年回顾是 1 月 1 日到 12 月 31 日。登记事件的期次在写入与读取时都已按闭集校验为规范写法。"""
    _, first, last, _ = _calendar(digest.period)
    return first.isoformat(), last.isoformat()


def _today_units(sources: MemorySources, period: SearchPeriod, scope: str | None,
                 words: tuple[str, ...]) -> list[_Unit]:
    """@brief 今天条目：时刻段内（全部时间取全部）、这一类有行或项且匹配关键词的条目，最晚发布的在前。"""
    units = []
    for entry in reversed(sources.entries):
        if period.kind == "today" and not period.first <= entry.clock <= period.last:
            continue
        piece = _entry_piece(entry, scope, f"今天 {entry.clock}", words)
        if piece is not None:
            units.append(_Unit("whole", _TODAY, (piece,), "", {}, ()))
    return units


def _date_units(sources: MemorySources, day: str, categories: tuple[str, ...], words: tuple[str, ...]) -> list[_Unit]:
    """@brief 按日期查一个往日：各类有回顾的给整项，「未整理」的类给那天这一类的原始条目；整项在前，各自按类别次序。"""
    digest = _registered(sources, "day", day)
    if digest is None:
        return []
    units = [unit for unit in _digest_units(digest, categories, words) if unit.kind == "whole"]
    unsorted = [category for category in categories if category in digest.unsorted]
    entries = sources.past_entries(day) if unsorted else ()
    for category in unsorted:
        pieces = _raw_pieces(entries, day, category, words)
        if pieces:
            units.append(_Unit("inline", category, pieces, "", {"日期": day, "类别": category, "未整理": True},
                               _order(day, "day", 1, category)))
    return units


def _digest_units(digest: SearchDigest, categories: tuple[str, ...], words: tuple[str, ...]) -> list[_Unit]:
    """@brief 一期回顾各类的单元：有正文且匹配关键词的给整项（日、月、年回顾分别写日期、月份、年份）；日回顾「未整理」给
    标签单元，月、年回顾「未整理」自身不给单元（由月单元、年单元换成下一级）；没有记录的没有单元。"""
    units = []
    for category in categories:
        order = _order(_digest_span(digest)[1], digest.tier, 0, category)
        if category in digest.texts:
            text = digest.texts[category]
            if _matches(text, words):
                piece = _Piece(f"{digest.period} {category}", len(text),
                               {_PERIOD_KEYS[digest.tier]: digest.period, "类别": category, "回顾": text})
                units.append(_Unit("whole", category, (piece,), "", {}, order))
        elif category in digest.unsorted and digest.tier == "day":
            units.append(_Unit("label", category, (), f"{digest.period} {category}{_UNSORTED_SUFFIX}", {}, order))
    return units


def _raw_pieces(entries: tuple[SearchEntry, ...], day: str, category: str,
                words: tuple[str, ...]) -> tuple[_Piece, ...]:
    """@brief 「未整理」那天这一类的原始条目：按这一类切分并匹配关键词，按时刻从晚到早（零点收尾条目最先），
    时刻相同的按条目次序倒排。"""
    ordered = sorted(enumerate(entries), key=lambda pair: (pair[1].clock, pair[0]), reverse=True)
    pieces = (_entry_piece(entry, category, f"{day} {entry.clock} {category}", words) for _, entry in ordered)
    return tuple(piece for piece in pieces if piece is not None)


def _entry_piece(entry: SearchEntry, scope: str | None, label: str, words: tuple[str, ...]) -> _Piece | None:
    """@brief 一条条目在这一类下的结果项（scope 为 None 时是全文）：这一类没有它的行和项、或不匹配关键词时为 None；
    字数是核验、当时查到与自述各字符串的长度之和。"""
    item = entry.full if scope is None else entry.sliced.get(scope)
    if item is None:
        return None
    strings = [*item.get("核验", []), *item.get("当时查到", []), *item.get("自述", [])]
    if not _matches("\n".join(strings), words):
        return None
    return _Piece(label, sum(len(text) for text in strings), item)


def _matches(text: str, words: tuple[str, ...]) -> bool:
    """@brief 关键词的每个词经 casefold 后都是这段文字经 casefold 后的子串；没有关键词时恒匹配。"""
    folded = text.casefold()
    return all(word.casefold() in folded for word in words)


def _order(end: str, tier: str, kind: int, category: str) -> tuple[int, int, int, int]:
    """@brief 往日单元的排序键：期末日从新到旧，同一末日日回顾、月回顾、年回顾依次，整项先于内联，再按类别次序。"""
    return (-date.fromisoformat(end).toordinal(), MEMORY_DIGEST_TIERS.index(tier), kind,
            MEMORY_CATEGORIES.index(category))


def _fill(units: list[_Unit]) -> tuple[list[tuple[str, dict]], list[str], list[str]]:
    """@brief 按次序装填：标签单元只进未列出；整项与内联的条目放得下就放，遇到第一个放不下的就停，此后的整项与条目
    以标签进未列出。返回（分组键，结果项）、去重后的未列出与各条未列出的原因。"""
    room, stopped = MEMORY_SEARCH_RESULT_CHARS, False
    placed, unlisted, reasons = [], [], []
    for unit in units:
        if unit.kind == "label":
            unlisted.append(unit.label)
            reasons.append("未整理")
            continue
        kept = []
        for piece in unit.pieces:
            if not stopped and piece.size <= room:
                room -= piece.size
                kept.append(piece.item)
                continue
            stopped = True
            unlisted.append(piece.label)
            reasons.append("上限" if unit.kind == "whole" else "按日放不下")
        if kept:
            placed.append((unit.group, kept[0] if unit.kind == "whole" else {**unit.header, "条目": kept}))
    return placed, list(dict.fromkeys(unlisted)), reasons


def _assemble(request: SearchMemoryRequest, period: SearchPeriod, filled: tuple[list, list, list],
              earliest: str | None) -> dict:
    """@brief 结果的顶层：类别、时间、关键词原样照抄，结果、未列出，未列出非空时加提示；这一期早于最早日期时在未列出
    之后加最早日期。「全部」类别的往日结果按类别分组（全部时间另在最前加今天），其余是列表。"""
    placed, unlisted, reasons = filled
    if request.category != _ALL or period.kind == "today":
        result: list | dict = [item for _, item in placed]
    else:
        groups: dict[str, list] = {}
        for group, item in placed:
            groups.setdefault(group, []).append(item)
        result = {key: groups[key] for key in (_TODAY, *MEMORY_CATEGORIES) if key in groups}
    answer = {"类别": request.category, "时间": request.time, "关键词": request.keyword, "结果": result,
              "未列出": unlisted}
    hint = "；".join(sentence for reason, sentence in _HINTS.items() if reason in reasons)
    if hint:
        answer["提示"] = hint
    if earliest is not None:
        answer["最早日期"] = earliest
    return answer
