"""@brief 交互对象可读名称的唯一写法：私有绑定的 object 与工具层目标名（docs/design/06-agent.md「工具层」）。

观察投影与名称解析共用全半角、括号和空白的无损归一，活动名可以省略开始时刻，地点名可以省略地点前缀。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")


_SIMULATED = "(模拟)"


_BRACKETS = str.maketrans({"[": "(", "]": ")", "【": "(", "】": ")", "〔": "(", "〕": ")", "〖": "(", "〗": ")",
                           "〈": "(", "〉": ")"})


#: 地点名称唯一允许省略的对象前缀。
_PREFIXES = ("地点:",)


HOME_WORDS = frozenset({"家", "住处"})


@dataclass(frozen=True, slots=True)
class NameMatch:
    """@brief 一个名称在一组可写名称里的解析：唯一时 name 为解析出的名称（照写的就是原文），否则为 None；matches 为同一级的
    全部匹配（多于一个即有歧义，没有即找不到）。"""

    name: str | None
    matches: tuple[str, ...]


def fold(text: str) -> str:
    """@brief 无损归一的基本写法：统一全半角（NFKC）与括号，去掉全部空白，忽略「（模拟）」。"""
    unified = unicodedata.normalize("NFKC", text).translate(_BRACKETS)
    return "".join(unified.split()).replace(_SIMULATED, "")


@lru_cache(maxsize=8192)
def _keys(text: str) -> tuple[str, str, str]:
    """@brief 名称逐级放宽的比对键：归一写法，再去掉活动开始时刻（START_SUFFIX），再去掉当前对象前缀。"""
    folded = fold(text)
    named = fold(START_SUFFIX.sub("", text))
    return folded, named, _unprefixed(named)


def _unprefixed(key: str) -> str:
    """@brief 去掉归一写法开头的类别前缀（只去 object_labels 的前缀，前缀后没有名称时不去）。"""
    return next((key[len(prefix):] for prefix in _PREFIXES if key.startswith(prefix) and len(key) > len(prefix)), key)


def bare_name(text: str) -> str:
    """@brief 名称的最宽比对键：归一、去掉开始时刻和当前对象前缀（候选相似度也按它计算）。"""
    return _keys(text)[2]


def resolve_name(written: str, names: Iterable[str]) -> NameMatch:
    """@brief 在一组可写名称（活动名、任务名、回看名称）中解析所写的名称：精确匹配，再按归一写法、
    去掉开始时刻、去掉当前对象前缀逐级放宽，第一级有匹配就停，唯一才受理。"""
    unique = tuple(dict.fromkeys(names))
    if written in unique:
        return NameMatch(written, (written,))
    keys = _keys(written)
    for level in (0, 1, 2):
        found = tuple(name for name in unique if _keys(name)[level] == keys[level])
        if found:
            return NameMatch(found[0] if len(found) == 1 else None, found)
    return NameMatch(None, ())
