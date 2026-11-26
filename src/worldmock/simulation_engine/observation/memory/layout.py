"""@brief 记忆层的组装：四个键的形状与次序、字数测量以及「今天」条目的无截断递推追加。

规则见 docs/design/06-agent.md 与 docs/design/09-io-formats.md「提示层的机器形状」：
记忆层是 {"layer":"记忆层","可查":…,"近三天":…,"今天":[…]}；「今天」按决策次序只追加，
上下文预算由日内阈值 Compaction 与跨日重置共同控制，不再在记忆层执行硬回填或条数改写。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime

from worldmock.agents.prompt.narration import render_layer_json
from worldmock.foundation.time.readable import local_date


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
    """@brief 「今天」的一项条目包装。"""

    item: dict


@dataclass(slots=True)
class TodayFold:
    """@brief 一个人物一天里「今天」的递推状态：当前各项与已折入条目收尾的活动身份。"""

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


def fold_entry(fold: TodayFold, entry: PublishedEntry, earliest: str | None = None, recent: dict | None = None) -> None:
    """@brief 把一条已发布的条目原样接到「今天」末尾；不再触发 14k 硬回填改写。"""
    del earliest, recent
    fold.slots.append(_Slot(copy.deepcopy(entry.item)))
    fold.concluded |= entry.concluded


def fold_items(fold: TodayFold) -> list[dict]:
    """@brief 「今天」此刻的各项（深拷贝，调用方改动不影响递推状态）。"""
    return [copy.deepcopy(slot.item) for slot in fold.slots]
