"""@brief 指名购买的来源范围、可服务的供给与按偏好选定（docs/dev/SPEC-dayrun-fidelity.md 3.2、3.3、3.5）。

来源范围（source_scope）：from 为 null 是全部门店；是地点的显示名（精确、无损归一或去掉「地点：」前缀后唯一，「家」「住处」
指本人住处）时是该地点；是渠道称呼（去掉空白、不分大小写，其余逐字比较，name_resolution.channel_key）时是该渠道的成员门店，
并记下 channel_id；都不是时为 None（SOURCE_NOT_FOUND）。渠道只是门店集合的称呼（裁决 D8、S7），不带交付语义。

可服务（offers，分段匹配的 M3）：到店要人此刻就在那家店里（途中不在任何店里）且供给支持到店；远程下单要供给支持配送且能
送到收货地（在配送范围内、配送路线走得通，送达时刻按 basket.delivery_expected_at：最慢备货加出发时刻的计划驾车时长）；
「购买」人在出售它的店里、且东西在那里交得到收货人手上（handed_over：收货人是自己，或此刻也在那家店里）就到店，否则能送到
收货地就远程下单（系统不定一个交不到收货人手上的方式）；显式写的到店照所写处理。多于一个可服务的供给时按请求级 prefer
选定（choose）：最快拿到 远程下单取预计送达最早者、到店取从当前地点出发路线最短者，到手价最低 取到手价最低者（一份按当前报价
与购买配对计价）；并列时先比到手价、再比门店标识，最后比供给标识（确定性）。价格、余额、营业与库存仍由整单结算照常检查。
本模块只读世界、场景与语义，不改状态。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from worldmock.life.admission import present_location
from worldmock.life.basket import delivery_expected_at
from worldmock.life.interaction_menu import PURCHASE_WAYS, SYSTEM_PURCHASE_WAY
from worldmock.life.name_resolution import channel_key, rank, written_place
from worldmock.life.storefront_state import current_quote
from worldmock.life.wayfinding import shortest_paths

if TYPE_CHECKING:
    from worldmock.life.tool_layer import LayerContext

#: 请求级偏好（models.PURCHASE_PREFERENCES）的缺省值：prefer 为 null 时按最快。
#: 到店与远程下单两种方式短语。
PICKUP_WAY, DELIVERY_WAY = PURCHASE_WAYS["pickup"], PURCHASE_WAYS["delivery"]


@dataclass(frozen=True, slots=True)
class SourceScope:
    """@brief from 限定的来源范围。

    :param store_ids: 范围内的门店（地点身份）；None 表示全部门店（from 为 null）。
    :param channel_id: from 是渠道称呼时该渠道的 App 标识（订单记 channel_id），否则为 None。
    :param name: 范围的可读说法：地点显示名或所写的渠道称呼（from 为 null 时为 None）。
    """

    store_ids: frozenset[str] | None = None
    channel_id: str | None = None
    name: str | None = None

    def contains(self, location_id: str) -> bool:
        """@brief 地点是否在范围内。"""
        return self.store_ids is None or location_id in self.store_ids


@dataclass(frozen=True, slots=True)
class Offer:
    """@brief 一条此刻可服务的供给。

    :param supply: 场景供给。
    :param way: 此刻能用的方式（到店或远程下单）。
    :param speed: 快慢：远程下单为预计送达时刻，到店为从当前地点出发的路程秒数（人就在店里，为 0）。
    :param price: 到手价（一份，分）；报价未知或购买配对不是 defined 时为 None（排在最后）。
    :param expected_at: 远程下单的预计送达时刻；到店为 None。
    """

    supply: Any
    way: str
    speed: int
    price: int | None
    expected_at: int | None = None


def source_scope(context: LayerContext, source: str | None) -> SourceScope | None:
    """@brief from 决定的来源范围（3.3 解析规则 1）：null → 全部门店；地点显示名 → 该地点；渠道称呼 → 成员门店并记下
    channel_id；都不是 → None。"""
    if source is None:
        return SourceScope()
    places = {item.location_id: item.name for item in context.scenario.locations}
    place = written_place(source, tuple(places.values()), places.get(_home(context)))
    if place is not None:
        return SourceScope(frozenset(identity for identity, name in places.items() if name == place), None, place)
    key = channel_key(source)
    channel = next((item for item in context.semantics.purchase_channels
                    if key in {channel_key(name) for name in item.names}), None)
    if channel is None:
        return None
    return SourceScope(frozenset(channel.store_location_ids), channel.channel_id, source)


def near_sources(context: LayerContext, written: str, feasible: Callable[[frozenset[str]], bool]) -> list[str]:
    """@brief SOURCE_NOT_FOUND 的候选来源：有供给的门店的显示名与渠道称呼中与所写最相近的至多 3 个（范围内此刻能买到这件
    商品的在前，name_resolution.rank）；一个也不相近时取此刻能买到的来源。渠道取它的第一个称呼。

    :param feasible: 一个来源范围（门店身份集合）此刻能不能买到这件商品。
    """
    places = {item.location_id: item.name for item in context.scenario.locations}
    stores = sorted({supply.location_id for supply in context.scenario.supplies})
    entries = [*((places[store], (places[store],), frozenset({store})) for store in stores),
               *((channel.names[0], channel.names, frozenset(channel.store_location_ids))
                 for channel in context.semantics.purchase_channels if channel.names)]
    chosen = rank(written, entries, lambda entry: entry[1], lambda entry: feasible(entry[2]))
    return [entry[0] for entry in chosen or [entry for entry in entries if feasible(entry[2])][:3]]


def offers(context: LayerContext, supplies: Iterable[Any], way: str,
           destination_id: str | None) -> tuple[str, list[Offer]]:
    """@brief 可服务的供给与定下的方式（分段匹配的 M3）：到店、远程下单按所写方式；「购买」人在出售它的店里、且东西在那里
    交得到收货人手上（handed_over）就到店，否则远程下单到收货地。显式写的到店只看人在不在店里（人物自己选的方式；送给不在
    店里的人由整单结算以「暂时送不到<名>那里」拒绝）。

    :param destination_id: 收货地（收货人此刻的收货地点：送给自己为人此刻所在的地点）；收货人此刻没有收货地点时为 None。
    """
    candidates = list(supplies)
    if way == SYSTEM_PURCHASE_WAY:
        here = handed_over(_pickups(context, candidates), destination_id)
        return (PICKUP_WAY, here) if here else (DELIVERY_WAY, _deliveries(context, candidates, destination_id))
    if way == PICKUP_WAY:
        return way, _pickups(context, candidates)
    return way, _deliveries(context, candidates, destination_id)


def serves(context: LayerContext, supply: Any, way: str, destination_id: str | None) -> bool:
    """@brief 一条供给此刻能不能按所写方式（「购买」为到店或远程下单之一）把东西交到收货人手上（候选排序：可行的在前）：
    到店另要东西在店里交得到收货人手上（handed_over），与整单结算同一判定。"""
    chosen, served = offers(context, (supply,), way, destination_id)
    return bool(handed_over(served, destination_id) if chosen == PICKUP_WAY else served)


def handed_over(pickups: Iterable[Offer], destination_id: str | None) -> list[Offer]:
    """@brief 到店可服务的供给中，东西在店里就交得到收货人手上的：收货地就是那家店。送给自己时收货地是人此刻所在的地点，
    人在店里即成立；送给别人时是收货人此刻的收货地点——同世界人物此刻也在那家店里，或联系人的收货地点就是那家店（与给予的
    同处规则一致）——与整单结算（order_routing）到店时的核对同一判定。"""
    return [offer for offer in pickups if offer.supply.location_id == destination_id]


def choose(served: Sequence[Offer], prefer: str) -> Offer:
    """@brief 按偏好选定一条可服务的供给（3.5「收敛」第 5 条）：最快拿到 先比快慢，到手价最低 先比到手价；并列时比到手价、
    门店标识与供给标识。"""
    return min(served, key=lambda offer: order_key(offer, prefer))


def order_key(offer: Offer, prefer: str) -> tuple:
    """@brief 选定与候选排序的键：最快拿到 为（快慢，到手价，门店标识，供给标识），到手价最低 为（到手价，门店标识，供给标识）；
    到手价未知的排在最后。"""
    if prefer not in {"最快拿到", "到手价最低"}:
        logging.error("Purchase preference is outside the declared set")
        raise ValueError("purchase preference must be 最快拿到 or 到手价最低")
    tail = (offer.price if offer.price is not None else math.inf, offer.supply.location_id, offer.supply.supply_id)
    return tail if prefer == "到手价最低" else (offer.speed, *tail)


def landed_price(context: LayerContext, supply: Any) -> int | None:
    """@brief 一份的到手价：当前报价经购买配对计价（与公开供给行的 purchase_price_fen 同一入口）；报价未知或配对不是
    defined 时为 None。"""
    quote = current_quote(context.state, supply.supply_id)
    if quote is None or context.semantics.purchase_pairing(supply.product_id).state != "defined":
        return None
    return context.semantics.purchase_amount(supply.product_id, quote.price_fen, 1)


def nearest_store(context: LayerContext, location_ids: Iterable[str]) -> str:
    """@brief 从当前地点出发路线最短的门店（人物的 location_id，途中为出发地；可达路线图上的总路程，到不了的排在最后，
    同长按地点身份）。"""
    origin = context.state.agents[context.agent_id].location_id
    paths = shortest_paths(context.scenario.routes, origin)

    def distance(identity: str) -> float:
        """@brief 从当前地点到该门店的路程秒数（就在那里为 0，到不了为无穷）。"""
        return 0 if identity == origin else paths[identity][0] if identity in paths else math.inf
    return min(sorted(set(location_ids)), key=lambda identity: (distance(identity), identity))


def _pickups(context: LayerContext, supplies: Sequence[Any]) -> list[Offer]:
    """@brief 到店可服务的供给：支持到店，且人此刻就在那家店里（途中不在任何店里）。"""
    here = present_location(context.state, context.agent_id)
    return [Offer(supply, PICKUP_WAY, 0, landed_price(context, supply)) for supply in supplies
            if here is not None and supply.location_id == here and "pickup" in supply.fulfillment_modes]


def _deliveries(context: LayerContext, supplies: Sequence[Any], destination_id: str | None) -> list[Offer]:
    """@brief 远程下单可服务的供给：支持配送，且能送到收货地（有预计送达时刻）。"""
    if destination_id is None:
        return []
    rows = []
    for supply in supplies:
        if "delivery" not in supply.fulfillment_modes:
            continue
        expected = delivery_expected_at(context.semantics, supply, destination_id, context.state.current_time)
        if expected is not None:
            rows.append(Offer(supply, DELIVERY_WAY, expected, landed_price(context, supply), expected))
    return rows


def _home(context: LayerContext) -> str | None:
    """@brief 本人住处的地点身份（「家」「住处」按它解析）。"""
    return next((item.home_location_id for item in context.scenario.agents if item.agent_id == context.agent_id), None)
