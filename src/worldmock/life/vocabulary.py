"""@brief 人物动词、七种方式与仍有实际用途的同义闭集。"""

from __future__ import annotations

import unicodedata


LIFE_VERBS = ("travel", "interact", "obtain", "sleep")
WAYS: dict[str, tuple[str, str]] = {
    "步行": ("travel", "travel_to"), "驾车": ("travel", "travel_to"),
    "睡觉": ("sleep", "sleep"), "等待": ("interact", "wait"),
    "购买": ("obtain", "purchase"), "进行": ("interact", "do"), "领取": ("obtain", "collect_parcel"),
}
ACTION_WAYS = {"do": "进行", "wait": "等待", "sleep": "睡觉", "purchase": "购买", "collect_parcel": "领取"}
VERB_WAY_SEPARATOR = "："
WAY_SYNONYMS: dict[str, frozenset[str]] = {key: frozenset(value) for key, value in {
    "走路": {"步行"}, "走过去": {"步行"}, "步行前往": {"步行"},
    "开车": {"驾车"}, "自驾": {"驾车"}, "驾车前往": {"驾车"}, "开车前往": {"驾车"},
    "做": {"进行"}, "参加": {"进行"},
    "等": {"等待"}, "等着": {"等待"}, "等候": {"等待"},
    "买": {"购买"}, "下单": {"购买"}, "下单购买": {"购买"}, "点单": {"购买"}, "订购": {"购买"},
    "睡": {"睡觉"}, "睡眠": {"睡觉"}, "就寝": {"睡觉"},
}.items()}


def way_options(way: str) -> frozenset[str]:
    """@brief 只把无损归一后的原词和明确登记的同义词解析为方式。"""
    key = "".join(unicodedata.normalize("NFKC", way).split())
    return (frozenset({key}) if key in WAYS else frozenset()) | WAY_SYNONYMS.get(key, frozenset())


def way_verb(way: str) -> str:
    """@brief 方式唯一决定内部动词，表外值不推测。"""
    owners = {WAYS[option][0] for option in way_options(way)}
    return next(iter(owners)) if len(owners) == 1 else ""
