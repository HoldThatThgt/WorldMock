"""@brief 显示写法：一项活动或一条手机事件写成什么字，四处共用这一处实现。

查看器、导出的一天时间线、scripts/inspect_run.py 和人物零点做计划时读到的「已核验经历」句用同一套写法；
名称只取这次运行冻结的环境目录（docs/design/05-recording-engine.md「显示写法与检查工具」）。查看器的
tools/run-viewer-wording.js 是这里的镜像，两份实现由 tests/fixtures/display-wording-cases.json 约束。
写法不含时刻、状态和时长，由各处另写。名称取不到是运行故障，不给任何降级文字。
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NamedTuple

from worldmock.catalog.models import App, Content, Goods, Location, TravelMethod
from worldmock.errors import IntegrityError
from worldmock.exchange.constants import WALKING_TRAVEL_METHOD_ID, WALKING_TRAVEL_METHOD_NAME
from worldmock.life.readable import ACTIVITY_KIND_LABELS

#: 名称种类到名称表字段：查不到时英文错误里写的就是这个种类名。
_KIND_TABLES = MappingProxyType({"app": "apps", "content": "contents", "goods": "goods",
                                 "location": "locations", "travel_method": "travel_methods"})
#: 前台区间三个步骤的中文名；引擎只写这三个，闭集之外不另设分支。
_STEP_LABELS = MappingProxyType({"navigation": "导航", "ordering": "下单", "payment": "付款"})
#: 三种手机事件的种类文字。
_PHONE_LABELS = MappingProxyType({"app_foreground": "app 前台", "order_notification": "订单通知",
                                  "payment_notification": "消费通知"})


class ContentName(NamedTuple):
    """@brief 一条内容在显示里的名称。"""

    #: 内容标题。
    title: str
    #: 单元名：有系列取系列名，否则等于标题。
    unit: str
    #: 目录行是否带 series。
    series: bool


@dataclass(frozen=True)
class DisplayNames:
    """@brief 一次运行冻结的环境目录给出的全部显示名称，由 display_names 唯一构造。"""

    #: app 身份到名称，内容 app 和功能 app 都在。
    apps: Mapping[str, str]
    #: 内容身份到（标题，单元名，是否系列）。
    contents: Mapping[str, ContentName]
    #: 商品身份到名称。
    goods: Mapping[str, str]
    #: 地点身份到显示名（有分店时带全角括号）。
    locations: Mapping[str, str]
    #: 出行方式身份到名称，含引擎自带的步行。
    travel_methods: Mapping[str, str]


def display_names(apps: Iterable[App], contents: Iterable[Content], locations: Iterable[Location],
                  goods: Iterable[Goods], travel_methods: Iterable[TravelMethod]) -> DisplayNames:
    """@brief 由五份目录的行模型建出名称表；步行不在目录里，固定写「步行」。"""
    methods = {WALKING_TRAVEL_METHOD_ID: WALKING_TRAVEL_METHOD_NAME}
    methods.update((row.travel_method_id, row.name) for row in travel_methods)
    return DisplayNames(
        apps=MappingProxyType({row.app_id: row.name for row in apps}),
        contents=MappingProxyType({row.content_id: ContentName(
            row.title, row.title if row.series is None else row.series.title, row.series is not None)
            for row in contents}),
        goods=MappingProxyType({row.goods_id: row.name for row in goods}),
        locations=MappingProxyType({row.location_id: row.display_name for row in locations}),
        travel_methods=MappingProxyType(methods))


def display_name(names: DisplayNames, kind: str, identity: Any) -> Any:
    """@brief 按种类查一个身份的名称；内容返回 ContentName，其余返回名称文字。

    @param kind app、content、goods、location、travel_method 之一。
    @throws IntegrityError 身份不在冻结的环境目录里，先写同文的英文错误日志。
    """
    table = getattr(names, _KIND_TABLES[kind])
    if identity not in table:
        message = f"Displayed object is not registered in the frozen environment: {kind} {identity}"
        logging.error(message)
        raise IntegrityError(message)
    return table[identity]


def goods_items(goods_ids: Iterable[str], name_of: Callable[[str], str]) -> list[str]:
    """@brief 商品名列表的各项：按商品身份升序，同一商品的几份合写成「名称×份数」。

    @param name_of 身份到名称的函数；查不到时由它决定怎样失败。
    """
    counts = Counter(goods_ids)
    return [name_of(key) if count == 1 else f"{name_of(key)}×{count}" for key, count in sorted(counts.items())]


def event_content_id(data: Mapping[str, Any]) -> str | None:
    """@brief 一条受理事件或进度事件的正文指向的内容身份。

    带 data.content 时取其中的 content_id，也就是这一段真正放过或做过的那一条；否则取活动快照的
    data.content_id。快照没有这个键（购买、出行、睡眠、查询）时为空，这些种类的写法不读内容序列。
    """
    content = data.get("content")
    if content is not None:
        return content["content_id"]
    return data["activity"]["data"].get("content_id")


def activity_phrase(names: DisplayNames, snapshot: Mapping[str, Any], content_ids: Sequence[str | None],
                    orders: Mapping[str, Mapping[str, Any]]) -> str:
    """@brief 一项活动的写法：互动、购买、出行各有专门的写法，其余种类写中文种类表里的种类名。

    @param content_ids 这一行各条事件指向的内容身份，按事件先后；相邻重复在这里去掉。
    @param orders 订单身份到商品履约记录（读 goods_id 与 app_id）。
    @throws IntegrityError 名称取不到、订单记录缺失或种类不在中文种类表里。
    """
    kind = snapshot["kind"]
    if kind == "interact":
        return _interact_phrase(names, snapshot, _distinct(content_ids))
    if kind == "purchase":
        return _purchase_phrase(names, snapshot, orders)
    if kind == "travel":
        method = display_name(names, "travel_method", snapshot["travel_method_id"])
        return f"{method}去{display_name(names, 'location', snapshot['destination_id'])}"
    if kind not in ACTIVITY_KIND_LABELS:
        message = f"Unsupported activity kind for display: {kind}"
        logging.error(message)
        raise IntegrityError(message)
    return ACTIVITY_KIND_LABELS[kind]


def phone_phrase(names: DisplayNames, data: Mapping[str, Any]) -> str:
    """@brief 一条手机事件（app 前台区间、订单通知、消费通知）的写法；app 名按事件的 app_id 取。"""
    app = display_name(names, "app", data["app_id"])
    detail = _STEP_LABELS[data["step"]] if data["kind"] == "app_foreground" else data["title"]
    return f"{_PHONE_LABELS[data['kind']]} · {app} · {detail}"


def _distinct(content_ids: Sequence[str | None]) -> list[str | None]:
    """@brief 去掉相邻的同一条内容（按身份判断，不按标题文字），其余照先后保留。"""
    return [key for index, key in enumerate(content_ids) if index == 0 or key != content_ids[index - 1]]


def _titles(names: DisplayNames, content_ids: Iterable[str | None]) -> str:
    """@brief 各条内容的标题依次各加一个书名号，书名号之间不加字。"""
    return "".join(f"《{display_name(names, 'content', key).title}》" for key in content_ids)


def _interact_phrase(names: DisplayNames, snapshot: Mapping[str, Any], content_ids: list[str | None]) -> str:
    """@brief 用 app 写「使用<app 名>《标题》……」；在地点做事写「在<地点>：<单元名>」，系列再列各集。"""
    app_id = snapshot["data"]["app_id"]
    if app_id is not None:
        # 过渡写法：内容轨道并入之前，没有内容的 app 使用仍会产生，空的内容身份跳过不写（收尾时删去）。
        played = [key for key in content_ids if key is not None]
        return f"使用{display_name(names, 'app', app_id)}{_titles(names, played)}"
    place = display_name(names, "location", snapshot["location_id"])
    first = display_name(names, "content", content_ids[0])
    if not first.series:
        return f"在{place}：{first.unit}"
    return f"在{place}：{first.unit}{_titles(names, content_ids)}"


def _purchase_phrase(names: DisplayNames, snapshot: Mapping[str, Any],
                     orders: Mapping[str, Mapping[str, Any]]) -> str:
    """@brief 按订单分段：带下单 app 的订单按 app 身份升序，到店订单排最后，段间用「；」。"""
    grouped: dict[str | None, list[str]] = {}
    for order_id in snapshot["data"]["order_ids"]:
        if order_id not in orders:
            message = f"Purchase activity has no recorded order: {order_id}"
            logging.error(message)
            raise IntegrityError(message)
        grouped.setdefault(orders[order_id]["app_id"], []).append(orders[order_id]["goods_id"])
    channels = [*sorted(key for key in grouped if key is not None), *([None] if None in grouped else [])]
    segments = []
    for app_id in channels:
        items = "、".join(goods_items(grouped[app_id], lambda key: display_name(names, "goods", key)))
        channel = "（到店）" if app_id is None else f"（经{display_name(names, 'app', app_id)}下单）"
        segments.append(items + channel)
    return "购买" + "；".join(segments)
