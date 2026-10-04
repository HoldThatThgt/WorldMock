"""@brief 七层提示共用的人读短语与中文种类表：带时区的时刻、本地日期、钟点、时长、金额、份数与个数。

依据 docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章「通用格式」：时刻字段用带时区的 ISO 8601（秒精度、场景时区）；
短语里的时刻用场景本地 HH:MM，跨日时加「M月D日」；数量、价格与时长写成人读短语（「约 12 分钟」「32 元」
「0.25 份」），不给原始秒数或以分为单位的整数。数字与单位之间留一个空格。各层构建器、公开结果与 settled 句
都从这里取短语，全仓只有这一套口径。

中文种类表（活动种类、场所类型、内部动作的说法、出行方式、浏览目录）也只在这里定义（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-时间线只出现中文名称 ）：环境层、交互
菜单与时间线渲染器 scripts/render_day.py 都取这几张表，环境层与交互菜单沿用的同名表就是这里的对象；
表的英文键合成 ENGLISH_KIND_NAMES，质量报告据此检查时间线里有没有漏出英文种类名。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

#: 活动种类（ActivityKind 的值）→ 时间线的中文种类名；带对象的活动由渲染器在种类名之外写上对象名称。
ACTIVITY_KIND_LABELS = {"wait": "空档", "sleep": "睡觉", "work": "工作", "watch": "看内容", "play_game": "玩游戏",
                        "travel": "出行", "read_mail": "读邮件", "browse": "浏览", "consume": "吃东西或用东西",
                        "lifestyle": "生活活动", "chat": "交谈", "call": "通话"}
#: 场所类型（地点 kind）→ 中文标签；覆盖仓库场景的全部地点类型，未知类型是场景缺陷。
PLACE_TYPES = {"home": "家", "office": "办公室", "merchant": "商户", "restaurant": "餐馆",
               "convenience_store": "便利店", "supermarket": "超市", "wet_market": "菜市场", "fitness": "健身房",
               "park": "公园"}
#: 内部标准动作的说法（环境层 fixed_rules 句子「这里不能<说法>」），按此顺序列出。
ACTION_PHRASES = {"travel_to": "出发去别处", "work": "工作", "watch": "看内容或听播客", "play": "玩游戏",
                  "chat": "当面交谈", "call": "打电话", "read": "看邮件", "browse": "浏览商品",
                  "do": "做生活活动", "purchase": "买东西", "consume": "吃东西或用东西", "wait": "等待",
                  "sleep": "睡觉"}
#: 出行方式 → 方式短语（交互菜单的出行方式、环境层路程中的出行方式、时间线出行行的动词）。
TRAVEL_WAYS = {"walking": "步行", "driving": "驾车"}
#: 商品目录与内容品类的公开名称，用于可调用索引、查询和时间线。
BROWSE_LABELS = {"product_catalog": "商品目录", "foods": "食品目录", "feature_film": "电影",
                 "drama_series": "剧集", "webnovel_excerpt": "网文", "news_feed": "新闻",
                 "joke_feed": "段子", "subtitle_experience": "字幕体验", "game_session": "游戏",
                 "podcast_episode": "播客"}
#: 上面各表的英文键：面向人读的时间线里不得出现的英文种类名（质量报告 timeline_labels 按它计数）。
ENGLISH_KIND_NAMES = frozenset({*ACTIVITY_KIND_LABELS, *PLACE_TYPES, *ACTION_PHRASES, *TRAVEL_WAYS, *BROWSE_LABELS})


def _local(epoch: int, timezone_name: str) -> datetime:
    """@brief 场景时区的本地时刻（秒精度）。"""
    return datetime.fromtimestamp(int(epoch), ZoneInfo(timezone_name))


def iso_time(epoch: int, timezone_name: str) -> str:
    """@brief 带时区偏移的 ISO 8601 时刻，例如「2026-09-28T10:33:00+08:00」。"""
    return _local(epoch, timezone_name).isoformat()


def local_date(epoch: int, timezone_name: str) -> str:
    """@brief 场景本地日期「YYYY-MM-DD」。"""
    return _local(epoch, timezone_name).date().isoformat()


def clock_phrase(epoch: int, now: int, timezone_name: str) -> str:
    """@brief 短语中的时刻：场景本地「HH:MM」；与 now 不在同一本地日期时前加「M月D日 」。"""
    moment = _local(epoch, timezone_name)
    if moment.date() == _local(now, timezone_name).date():
        return f"{moment:%H:%M}"
    return f"{moment.month}月{moment.day}日 {moment:%H:%M}"


def duration_phrase(seconds: float) -> str:
    """@brief 人读时长：不到一分钟写「不到 1 分钟」，不足一小时按分钟取整，一小时以上按半小时取整。"""
    if seconds < 60:
        return "不到 1 分钟"
    if seconds < 3600:
        return f"约 {round(seconds / 60)} 分钟"
    return f"约 {round(seconds / 1800) / 2:g} 小时"


def _decimal(value: float) -> str:
    """@brief 至多两位小数、去掉末尾的零的数字写法。"""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def money_phrase(fen: int) -> str:
    """@brief 以分为单位的金额写成「32 元」「32.5 元」。"""
    return f"{_decimal(fen / 100)} 元"


def portion_phrase(quantity: float) -> str:
    """@brief 份数写成「1 份」「0.25 份」。"""
    return f"{_decimal(quantity)} 份"


def count_phrase(count: int) -> str:
    """@brief 个数写成「90 个」（工具层可调用索引的品类计数，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ）。"""
    return f"{count} 个"


def _datetime(epoch: int, timezone_name: str) -> datetime:
    """@brief 将权威 UTC 秒转为场景时区的带时区日期时间。"""
    return datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))
