"""@brief 从真实食品菜单与目录 app 绑定选择到店或配送购买。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from worldmock.catalog.models import Goods
from worldmock.exchange.operators import delivery_fee_fen, delivery_seconds
from worldmock.life.admission import present_location
from worldmock.life.name_resolution import delivery_sources, near_source_names, resolve_source
from worldmock.life.vocabulary import PURCHASE_WAYS, SYSTEM_PURCHASE_WAY

if TYPE_CHECKING:
    from worldmock.life.tool_layer import LayerContext

#: 购买方式的唯一人类可读短语。
PICKUP_WAY, DELIVERY_WAY = PURCHASE_WAYS["pickup"], PURCHASE_WAYS["delivery"]


@dataclass(frozen=True, slots=True)
class SourceScope:
    """@brief 来源限定的登记门店集合及实际 app 身份。"""

    store_ids: frozenset[str] | None = None
    app_id: str | None = None
    name: str | None = None

    def contains(self, store_id: str) -> bool:
        """@brief 判断实际门店是否属于请求来源。"""
        return self.store_ids is None or store_id in self.store_ids


@dataclass(frozen=True, slots=True)
class Offer:
    """@brief 固定菜单食品的一条当前可服务购买选择。"""

    store_id: str
    food: Goods
    way: str
    speed: int
    price: int
    app_id: str | None = None
    expected_at: int | None = None


def menu_goods(context: LayerContext) -> tuple[tuple[str, Goods], ...]:
    """@brief 已选门店中的实际目录食品，使用同品类共享固定菜单。"""
    selected = {place.location_id for place in context.scenario.locations}
    return tuple((store_id, food) for store_id, foods in context.semantics.catalog.goods_by_store.items()
                 if store_id in selected for food_id in foods for food in (context.semantics.catalog.goods[food_id],))


def source_scope(context: LayerContext, source: str | None) -> SourceScope | None:
    """@brief 地点名称限定单店，app 名称限定真实绑定店铺，空来源不限定。"""
    if source is None:
        return SourceScope()
    match = resolve_source(source, _places(context), _home(context), delivery_sources(context.semantics.catalog))
    return None if match is None else SourceScope(match.store_ids, match.app_id, match.name)


def near_sources(context: LayerContext, written: str, feasible: Callable[[frozenset[str]], bool]) -> list[str]:
    """@brief 返回实际目录来源的近名候选，不自动替换来源。"""
    return near_source_names(written, _places(context), (store for store, _ in menu_goods(context)),
                             delivery_sources(context.semantics.catalog), feasible)


def offers(context: LayerContext, foods: Iterable[tuple[str, Goods]], way: str,
           destination_id: str | None) -> tuple[str, list[Offer]]:
    """@brief 根据人在店内及 app 绑定选择实际到店或配送方式。"""
    candidates = tuple(foods)
    if way == SYSTEM_PURCHASE_WAY:
        pickups = handed_over(_pickups(context, candidates), destination_id)
        return (PICKUP_WAY, pickups) if pickups else (DELIVERY_WAY, _deliveries(context, candidates, destination_id))
    if way == PICKUP_WAY:
        return way, _pickups(context, candidates)
    return way, _deliveries(context, candidates, destination_id)


def serves(context: LayerContext, food: tuple[str, Goods], way: str, destination_id: str | None) -> bool:
    """@brief 判断同一实际菜单项是否可以在当前地点交到收货人手中。"""
    chosen, served = offers(context, (food,), way, destination_id)
    return bool(handed_over(served, destination_id) if chosen == PICKUP_WAY else served)


def handed_over(pickups: Iterable[Offer], destination_id: str | None) -> list[Offer]:
    """@brief 到店购买只有收货人也能在门店收货时即时交付。"""
    return [offer for offer in pickups if offer.store_id == destination_id]


def choose(served: Sequence[Offer], prefer: str) -> Offer:
    """@brief 按请求偏好及登记门店身份确定性选择一条实际购买。"""
    return min(served, key=lambda offer: order_key(offer, prefer))


def order_key(offer: Offer, prefer: str) -> tuple:
    """@brief 最快比较送达时间和到手价，最便宜比较含配送费价格；同值按店铺身份。"""
    if prefer not in {"最快拿到", "到手价最低"}:
        logging.error("Purchase preference is outside the declared set")
        raise ValueError("purchase preference must be 最快拿到 or 到手价最低")
    tail = (offer.price, offer.store_id)
    return tail if prefer == "到手价最低" else (offer.speed, *tail)


def nearest_store(context: LayerContext, store_ids: Iterable[str]) -> str:
    """@brief 按登记图米数与地点身份选择最近实际门店。"""
    origin = context.state.agents[context.agent_id].location_id
    return min(set(store_ids), key=lambda identity: (context.semantics.catalog.shortest_m(origin, identity), identity))


def _pickups(context: LayerContext, foods: Sequence[tuple[str, Goods]]) -> list[Offer]:
    """@brief 当前确实位于门店的到店购买，固定食品价格且没有 app。"""
    here = present_location(context.state, context.agent_id)
    return [Offer(store, food, PICKUP_WAY, 0, food.price_fen) for store, food in foods if store == here]


def _deliveries(context: LayerContext, foods: Sequence[tuple[str, Goods]],
                destination_id: str | None) -> list[Offer]:
    """@brief 绑定至少一个 app 的门店向任意已选收货地点配送，固定费用与时长。"""
    if destination_id is None:
        return []
    catalog = context.semantics.catalog
    rows = []
    for store, food in foods:
        apps = catalog.apps_by_store[store]
        if not apps:
            continue
        expected = context.state.current_time + delivery_seconds(catalog.shortest_m(store, destination_id))
        rows.append(Offer(store, food, DELIVERY_WAY, expected, food.price_fen + delivery_fee_fen(),
                          min(apps), expected))
    return rows


def _home(context: LayerContext) -> str | None:
    """@brief 本人真实住处的登记地点身份。"""
    return next(item.home_location_id for item in context.scenario.agents if item.agent_id == context.agent_id)


def _places(context: LayerContext) -> dict[str, str]:
    """@brief 已选地点身份及含分店名的显示名称。"""
    return {item.location_id: item.display_name for item in context.scenario.locations}
