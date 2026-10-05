"""@brief 人物动词、方式与同义闭集的唯一基础词表。"""

from __future__ import annotations

import unicodedata


LIFE_VERBS = ("travel", "interact", "obtain", "consume", "sleep")


WAYS: dict[str, tuple[str, str]] = {
    "步行": ("travel", "travel_to"), "驾车": ("travel", "travel_to"),
    "工作": ("interact", "work"), "观看": ("interact", "watch"), "游玩": ("interact", "play"),
    "交谈": ("interact", "chat"), "通话": ("interact", "call"), "阅读": ("interact", "read"),
    "浏览": ("interact", "browse"), "进行": ("interact", "do"), "等待": ("interact", "wait"),
    "到店": ("obtain", "purchase"), "远程下单": ("obtain", "purchase"), "购买": ("obtain", "purchase"),
    "制作": ("obtain", "consume"),
    "食用": ("consume", "consume"), "使用": ("consume", "consume"), "给予": ("consume", "consume"),
    "接着看": ("interact", "watch"), "从头再看": ("interact", "watch"),
    "接着听": ("interact", "watch"), "从头再听": ("interact", "watch"),
    "睡觉": ("sleep", "sleep"),
}


ACTION_WAYS = {
    "work": "工作", "browse": "浏览", "watch": "观看", "play": "游玩", "chat": "交谈", "call": "通话",
    "read": "阅读", "do": "进行", "wait": "等待", "sleep": "睡觉",
}


PURCHASE_WAYS = {"pickup": "到店", "delivery": "远程下单"}


SYSTEM_PURCHASE_WAY = "购买"


NAMED_PURCHASE_WAYS = frozenset({*PURCHASE_WAYS.values(), SYSTEM_PURCHASE_WAY})


USE_WAYS = {"eat": "食用", "use": "使用"}


GIVE_WAY = "给予"


CONTENT_WAYS = {(False, False): "接着看", (False, True): "从头再看", (True, False): "接着听", (True, True): "从头再听"}


VERB_WAY_SEPARATOR = "："



WAY_SYNONYMS: dict[str, frozenset[str]] = {key: frozenset(value) for key, value in {
    "走路": {"步行"}, "走过去": {"步行"}, "步行前往": {"步行"},
    "开车": {"驾车"}, "自驾": {"驾车"}, "驾车前往": {"驾车"}, "开车前往": {"驾车"},
    "上班": {"工作"}, "办公": {"工作"},
    "看": {"观看", "接着看", "从头再看"}, "收看": {"观看", "接着看", "从头再看"}, "观看": {"接着看", "从头再看"},
    "继续看": {"接着看"}, "重看": {"从头再看"}, "重新看": {"从头再看"},
    "听": {"接着听", "从头再听"}, "收听": {"接着听", "从头再听"}, "继续听": {"接着听"}, "重听": {"从头再听"},
    "重新听": {"从头再听"},
    "玩": {"游玩"}, "玩游戏": {"游玩"},
    "聊天": {"交谈"}, "聊": {"交谈"}, "面谈": {"交谈"},
    "打电话": {"通话"}, "电话": {"通话"},
    "读": {"阅读"}, "看邮件": {"阅读"}, "查看": {"阅读", "浏览"},
    "逛": {"浏览"}, "逛逛": {"浏览"},
    "做": {"进行"}, "参加": {"进行"},
    "等": {"等待"}, "等着": {"等待"}, "等候": {"等待"},
    "到店购买": {"到店"}, "到店点单": {"到店"}, "堂食": {"到店"}, "自取": {"到店"},
    "外卖": {"远程下单"}, "点外卖": {"远程下单"}, "配送": {"远程下单"}, "送货上门": {"远程下单"},
    "网上下单": {"远程下单"}, "线上下单": {"远程下单"},
    "买": {"购买"}, "下单": {"购买"}, "下单购买": {"购买"}, "点单": {"购买"}, "订购": {"购买"},
    "做饭": {"制作"},
    "吃": {"食用"}, "吃饭": {"食用"}, "用餐": {"食用"}, "进食": {"食用"},
    "用": {"使用"},
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
