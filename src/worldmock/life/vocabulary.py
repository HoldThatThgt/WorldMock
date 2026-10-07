"""@brief 人物动词、方式与同义闭集的唯一基础词表。"""

from __future__ import annotations

import unicodedata


LIFE_VERBS = ("travel", "interact", "obtain", "consume", "sleep")


WAYS: dict[str, tuple[str, str]] = {
    "步行": ("travel", "travel_to"), "驾车": ("travel", "travel_to"),
    "工作": ("interact", "work"), "打开": ("interact", "use_app"),
    "交谈": ("interact", "chat"), "通话": ("interact", "call"), "阅读": ("interact", "read"),
 "进行": ("interact", "do"), "等待": ("interact", "wait"),
    "到店": ("obtain", "purchase"), "远程下单": ("obtain", "purchase"), "购买": ("obtain", "purchase"),
    "食用": ("consume", "consume"), "使用": ("consume", "consume"), "给予": ("consume", "consume"),
    "睡觉": ("sleep", "sleep"),
}


ACTION_WAYS = {
    "work": "工作", "use_app": "打开", "chat": "交谈", "call": "通话",
    "read": "阅读", "do": "进行", "wait": "等待", "sleep": "睡觉",
}


PURCHASE_WAYS = {"pickup": "到店", "delivery": "远程下单"}


SYSTEM_PURCHASE_WAY = "购买"


NAMED_PURCHASE_WAYS = frozenset({*PURCHASE_WAYS.values(), SYSTEM_PURCHASE_WAY})


USE_WAYS = {"eat": "食用"}


GIVE_WAY = "给予"




VERB_WAY_SEPARATOR = "："



WAY_SYNONYMS: dict[str, frozenset[str]] = {key: frozenset(value) for key, value in {
    "走路": {"步行"}, "走过去": {"步行"}, "步行前往": {"步行"},
    "开车": {"驾车"}, "自驾": {"驾车"}, "驾车前往": {"驾车"}, "开车前往": {"驾车"},
    "上班": {"工作"}, "办公": {"工作"},
    "刷": {"打开"}, "刷一刷": {"打开"}, "用一下": {"打开"}, "打开看看": {"打开"},
    "聊天": {"交谈"}, "聊": {"交谈"}, "面谈": {"交谈"},
    "打电话": {"通话"}, "电话": {"通话"},
    "读": {"阅读"}, "看邮件": {"阅读"}, "查看": {"阅读"},
    "做": {"进行"}, "参加": {"进行"},
    "等": {"等待"}, "等着": {"等待"}, "等候": {"等待"},
    "到店购买": {"到店"}, "到店点单": {"到店"}, "堂食": {"到店"}, "自取": {"到店"},
    "外卖": {"远程下单"}, "点外卖": {"远程下单"}, "配送": {"远程下单"}, "送货上门": {"远程下单"},
    "网上下单": {"远程下单"}, "线上下单": {"远程下单"},
    "买": {"购买"}, "下单": {"购买"}, "下单购买": {"购买"}, "点单": {"购买"}, "订购": {"购买"},
    "吃": {"食用"}, "吃饭": {"食用"}, "用餐": {"食用"}, "进食": {"食用"},
    "送": {"给予"}, "给": {"给予"}, "赠送": {"给予"},
    "睡": {"睡觉"}, "睡眠": {"睡觉"}, "就寝": {"睡觉"},
}.items()}


def way_options(way: str) -> frozenset[str]:
    """@brief 所写方式可能指的方式：方式闭集中的原词（去掉空白、统一全半角后），加上同义表列出的；都不是时为空集（不是
    方式）。"""
    key = "".join(unicodedata.normalize("NFKC", way).split())
    return (frozenset({key}) if key in WAYS else frozenset()) | WAY_SYNONYMS.get(key, frozenset())


def way_verb(way: str) -> str:
    """@brief 方式及已声明同义方式唯一决定内部动词，表外值不推测。"""
    owners = {WAYS[option][0] for option in way_options(way)}
    return next(iter(owners)) if len(owners) == 1 else ""
