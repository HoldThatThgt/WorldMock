"""@brief 记忆层的组装：四个键的形状与次序、按渲染后字数执行的上限，以及「今天」接上一条后超出上限时的兜底回落。

规则见 docs/design/06-agent.md「记忆层的形状、上限与兜底」与 docs/design/09-io-formats.md「提示层的机器形状」：
记忆层是 {"layer":"记忆层","可查":…,"近三天":…,"今天":[…]}，经 render_layer_json 渲染后（含键名）合计至多
MEMORY_LAYER_CHARS 字；「今天」按决策次序只追加，接上一条后超出上限时一次回落到 MEMORY_BACKSTOP_CHARS 字以内。
本模块是纯函数库；按人物缓存递推状态的是 observation/layers/memory_layer.py。
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from worldmock.agents.prompt.narration import render_layer_json
from worldmock.foundation.time.readable import local_date

#: 渲染后的整个记忆层的字数上限（len，含键名；约 8.9k token，按 0.638 token/字）。
MEMORY_LAYER_CHARS = 14000
#: 兜底一次回落到的字数。
MEMORY_BACKSTOP_CHARS = 12000


@dataclass(frozen=True, slots=True)
class PublishedEntry:
    """@brief 一条已发布的今天条目在记忆层组装时用到的部分；由第 02 章的条目构建器填写。"""

    #: 模型看到的条目，形状见第 02 章（{"时刻","类别","核验","自述"}，空键省略）。
    item: dict
    #: 这次决策的世界请求恰是一个受理的 report_status。
    report_only: bool
    #: report_only 时这个 report_status 点名的活动身份；否则为空集。
    reported: frozenset[str]
    #: 这次决策受理的 conclude_activity 所收尾的活动身份；否则为空集。
    concluded: frozenset[str]


@dataclass(slots=True)
class _Slot:
    """@brief 「今天」的一项：条目或计数行，连同兜底要读的本地小时、类别串、条数与播报事实。"""

    item: dict
    hour: int
    category: str
    count: int
    report_only: bool
    reported: frozenset[str]
    line: bool


@dataclass(slots=True)
class TodayFold:
    """@brief 一个人物一天里「今天」的递推状态：当前各项（兜底改过的保持改后的样子）与已折入条目收尾的活动身份。"""

    slots: list[_Slot] = field(default_factory=list)
    concluded: set[str] = field(default_factory=set)


def earliest_date(start_time: datetime, timezone: str) -> str:
    """@brief 能查到的最早日期：运行开始时刻的场景本地日期 YYYY-MM-DD；分支沿用父运行的运行配置，所以全程不变。"""
    return local_date(int(start_time.timestamp()), timezone)


def assemble_layer(earliest: str | None, recent: dict, today: list[dict]) -> dict:
    """@brief 记忆层对象，键序即渲染顺序：layer、可查、近三天、今天；earliest 为 None 时「可查」是空对象。"""
    return {"layer": "记忆层", "可查": {} if earliest is None else {"最早日期": earliest},
            "近三天": recent, "今天": today}


def layer_chars(layer: dict) -> int:
    """@brief 记忆层的字数：render_layer_json 渲染出的紧凑 JSON 的 len，含键名、引号与标点。"""
    return len(render_layer_json(layer))


def require_budget(layer: dict) -> None:
    """@brief 记忆层超过 MEMORY_LAYER_CHARS 字时写英文错误日志并以 RuntimeError 停止；容量浓缩从不改记忆层。"""
    chars = layer_chars(layer)
    if chars > MEMORY_LAYER_CHARS:
        logging.error("Memory layer has %d characters, above its %d-character budget", chars, MEMORY_LAYER_CHARS)
        raise RuntimeError(f"Memory layer exceeds {MEMORY_LAYER_CHARS} characters")


def fold_entry(fold: TodayFold, entry: PublishedEntry, earliest: str | None, recent: dict) -> None:
    """@brief 把一条已发布的条目接到「今天」末尾；接上后记忆层超过 MEMORY_LAYER_CHARS 字时兜底回落。

    :param fold: 当天的递推状态，原地更新。
    :param entry: 第 k−1 次决策的条目。
    :param earliest: 这次观察「可查」的最早日期。
    :param recent: 这次观察的「近三天」。
    """
    item = copy.deepcopy(entry.item)
    fold.slots.append(_Slot(item, int(item["时刻"][:2]), item["类别"], 1, entry.report_only, entry.reported, False))
    fold.concluded |= entry.concluded
    before = _chars(fold, earliest, recent)
    if before > MEMORY_LAYER_CHARS:
        _backstop(fold, lambda: _chars(fold, earliest, recent) <= MEMORY_BACKSTOP_CHARS)
        logging.warning("Memory layer backstop rewrote today from %d to %d characters (target %d)",
                        before, _chars(fold, earliest, recent), MEMORY_BACKSTOP_CHARS)


def fold_items(fold: TodayFold) -> list[dict]:
    """@brief 「今天」此刻的各项（深拷贝，调用方改动不影响递推状态）。"""
    return [copy.deepcopy(slot.item) for slot in fold.slots]


def _chars(fold: TodayFold, earliest: str | None, recent: dict) -> int:
    """@brief 以当前各项组装的记忆层字数。"""
    return layer_chars(assemble_layer(earliest, recent, [slot.item for slot in fold.slots]))


def _backstop(fold: TodayFold, fits: Callable[[], bool]) -> None:
    """@brief 兜底：换掉已收尾活动的播报自述、换掉其余播报自述、其余只留核验、按小时合成计数行；每改一项重量一次，
    fits 为真即停。"""
    concluded = [slot for slot in fold.slots
                 if slot.report_only and slot.reported and slot.reported <= fold.concluded]
    if _blank(concluded, fits) or _blank([slot for slot in fold.slots if slot.report_only], fits):
        return
    if _blank([slot for slot in fold.slots if not slot.line], fits):
        return
    _merge_hours(fold, fits)


def _blank(slots: list[_Slot], fits: Callable[[], bool]) -> bool:
    """@brief 依次把各项的「自述」换成 ["…"]（没有自述或已是 ["…"] 的跳过）；fits 为真即返回真。"""
    for slot in slots:
        if slot.item.get("自述", ["…"]) == ["…"]:
            continue
        slot.item = {**slot.item, "自述": ["…"]}
        if fits():
            return True
    return False


def _merge_hours(fold: TodayFold, fits: Callable[[], bool]) -> None:
    """@brief 按组首项的先后，把本地小时与类别串相同的各项合成一行计数行，放在组首项的位置；fits 为真即停。"""
    for key in list(dict.fromkeys((slot.hour, slot.category) for slot in fold.slots)):
        members = [slot for slot in fold.slots if (slot.hour, slot.category) == key]
        if len(members) == 1 and members[0].line:
            continue
        first = next(index for index, slot in enumerate(fold.slots) if (slot.hour, slot.category) == key)
        kept = [slot for slot in fold.slots if (slot.hour, slot.category) != key]
        kept.insert(first, _line(key, sum(slot.count for slot in members)))
        fold.slots = kept
        if fits():
            return


def _line(key: tuple[int, str], count: int) -> _Slot:
    """@brief 一行计数行：{"时刻":"HH:00–HH′:00","类别":类别串,"条数":N}，23 时写 23:00–24:00。"""
    hour, category = key
    item = {"时刻": f"{hour:02d}:00–{hour + 1:02d}:00", "类别": category, "条数": count}
    return _Slot(item, hour, category, count, False, frozenset(), True)
