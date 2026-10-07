"""@brief 交互对象可读名称的唯一写法：私有绑定的 object 与工具层目标名（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。

观察投影（projection.interaction_bindings）按这里的写法给本轮可见对象命名，场景加载（load_checks）按同一写法预先
核对这些名称不会重复（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-场景加载时就该拒绝的问题 「可见标签唯一」），两处不各写一份格式。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

#: 私有绑定类别 → 名称前缀（名称写成「<前缀>：<名称>」）。
OBJECT_PREFIXES = {"location": "地点", "person": "人物", "contact": "联系人"}
#: 「自己」对象的名称（等待与睡觉两个受保护动作的对象）。
SELF_LABEL = "自己"


def object_label(kind: str, name: str) -> str:
    """@brief 带类别前缀的对象名称：「<前缀>：<名称>」。

    :param kind: 私有绑定类别（OBJECT_PREFIXES 的键）。
    :param name: 对象自身的可读名称（地点显示名、商品名、标题、称谓等）。
    """
    return f"{OBJECT_PREFIXES[kind]}：{name}"


START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")


_SIMULATED = "(模拟)"


_BRACKETS = str.maketrans({"[": "(", "]": ")", "【": "(", "】": ")", "〔": "(", "〕": ")", "〖": "(", "〗": ")",
                           "〈": "(", "〉": ")"})


_PREFIXES = tuple(f"{prefix}:" for prefix in OBJECT_PREFIXES.values())


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
    """@brief 在一组可写名称（活动名、任务名、回看名称、收件人、人物与消息名称）中解析所写的名称：精确匹配，再按归一写法、
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
