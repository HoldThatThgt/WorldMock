"""@brief 交互对象可读名称的唯一写法：私有绑定的 object 与工具层目标名（docs/dev/SPEC-persona-prompt.md「工具层」）。

观察投影（projection.interaction_bindings）按这里的写法给本轮可见对象命名，场景加载（load_checks）按同一写法预先
核对这些名称不会重复（docs/dev/SPEC-dayrun-fidelity.md 8.1「可见标签唯一」），两处不各写一份格式。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

#: 私有绑定类别 → 名称前缀（名称写成「<前缀>：<名称>」）。
OBJECT_PREFIXES = {"work_task": "工作", "location": "地点", "holding": "持有物", "content": "内容", "game": "游戏",
                   "lifestyle": "活动", "person": "人物", "contact": "联系人", "mail": "邮件"}
#: 商品目录对象的名称（浏览动作的对象）。
CATALOG_LABEL = "商品目录"
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


def remote_holding_label(product: str, place: str) -> str:
    """@brief 他处持有物的名称：「持有物：<商品名>（<地点显示名>）」。"""
    return f"{object_label('holding', product)}（{place}）"


def mail_name(subject: str, sent_at: int, timezone: str) -> str:
    """@brief 场景邮件对象的名称部分：主题加场景时区的到达时刻（到秒），用来区分同主题邮件。"""
    return f"{subject}（{datetime.fromtimestamp(sent_at, ZoneInfo(timezone)):%Y年%m月%d日 %H:%M:%S}）"
