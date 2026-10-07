"""@brief 交互对象可读名称的唯一写法：私有绑定的 object 与工具层目标名（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。

观察投影（projection.interaction_bindings）按这里的写法给本轮可见对象命名，场景加载（load_checks）按同一写法预先
核对这些名称不会重复（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-场景加载时就该拒绝的问题 「可见标签唯一」），两处不各写一份格式。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime
from zoneinfo import ZoneInfo

#: 私有绑定类别 → 名称前缀（名称写成「<前缀>：<名称>」）。
OBJECT_PREFIXES = {"work_topic": "工作", "location": "地点", "app": "应用",
                   "lifestyle": "活动", "person": "人物", "contact": "联系人", "mail": "邮件"}
#: 「自己」对象的名称（等待与睡觉两个受保护动作的对象）。
SELF_LABEL = "自己"


def object_label(kind: str, name: str) -> str:
    """@brief 带类别前缀的对象名称：「<前缀>：<名称>」。

    :param kind: 私有绑定类别（OBJECT_PREFIXES 的键）。
    :param name: 对象自身的可读名称（地点显示名、商品名、标题、称谓等）。
    """
    return f"{OBJECT_PREFIXES[kind]}：{name}"


def supply_label(place: str, product: str) -> str:
    """@brief 门店商品的名称：「<地点显示名>出售的<商品名>」。"""
    return f"{place}出售的{product}"




def mail_name(subject: str, sent_at: int, timezone: str) -> str:
    """@brief 场景邮件对象的名称部分：主题加场景时区的到达时刻（到秒），用来区分同主题邮件。"""
    return f"{subject}（{datetime.fromtimestamp(sent_at, ZoneInfo(timezone)):%Y年%m月%d日 %H:%M:%S}）"


START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")


_MAIL_STAMP = re.compile(r"\(\d{4}年\d{1,2}月\d{1,2}日\d{1,2}:\d{2}:\d{2}\)$")


_SIMULATED = "(模拟)"


_BRACKETS = str.maketrans({"[": "(", "]": ")", "【": "(", "】": ")", "〔": "(", "〕": ")", "〖": "(", "〗": ")",
                           "〈": "(", "〉": ")"})


_PREFIXES = tuple(f"{prefix}:" for prefix in OBJECT_PREFIXES.values())


HOME_WORDS = frozenset({"家", "住处"})


_SOLD_BY = "出售的"


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
    """@brief 名称逐级放宽的比对键：归一写法；再去掉活动开始时刻（START_SUFFIX）与邮件到达时刻；再去掉类别前缀。"""
    folded = fold(text)
    named = _MAIL_STAMP.sub("", fold(START_SUFFIX.sub("", text)))
    return folded, named, _unprefixed(named)


def _unprefixed(key: str) -> str:
    """@brief 去掉归一写法开头的类别前缀（只去 object_labels 的前缀，前缀后没有名称时不去）。"""
    return next((key[len(prefix):] for prefix in _PREFIXES if key.startswith(prefix) and len(key) > len(prefix)), key)


def bare_name(text: str) -> str:
    """@brief 名称的最宽比对键：归一、去掉开始时刻与邮件时刻、去掉类别前缀（候选相似度也按它计算）。"""
    return _keys(text)[2]


def resolve_name(written: str, names: Iterable[str]) -> NameMatch:
    """@brief 在一组可写名称（活动名、任务名、回看名称、收件人、人物与消息名称）中解析所写的名称：精确匹配，再按归一写法、
    去掉开始时刻与邮件时刻、去掉类别前缀逐级放宽，第一级有匹配就停，唯一才受理。"""
    unique = tuple(dict.fromkeys(names))
    if written in unique:
        return NameMatch(written, (written,))
    keys = _keys(written)
    for level in (0, 1, 2):
        found = tuple(name for name in unique if _keys(name)[level] == keys[level])
        if found:
            return NameMatch(found[0] if len(found) == 1 else None, found)
    return NameMatch(None, ())


def names_store(label: str, store: str) -> bool:
    """@brief 所写的门店名是否就是标签「<门店>出售的<商品>」写明的门店（按归一写法比较，也认去掉「地点：」前缀的写法）。"""
    folded = fold(label)
    return any(folded.startswith(f"{key}{_SOLD_BY}") for key in (fold(store), bare_name(store)) if key)


def written_place(written: str, places: Sequence[str], home: str | None = None) -> str | None:
    """@brief 所写的地点名唯一对应的地点显示名：精确，再按归一写法与去掉「地点：」前缀逐级比对（resolve_name，第一级
    有匹配就停）；「家」「住处」指本人住处 home。对应不上或不止一个时为 None。

    :param places: 场景地点的显示名（冻结地点表）。
    """
    if bare_name(written) in HOME_WORDS and home is not None:
        return home
    return resolve_name(written, places).name
