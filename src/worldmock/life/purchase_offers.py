"""@brief 依据实际商品、购物应用与收货人只读选择履约店铺。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from worldmock.catalog.models import Goods
from worldmock.errors import PreconditionError
from worldmock.life.admission import present_location
from worldmock.life.basket import plan_recipient_delivery
from worldmock.life.recipients import recipient_location

if TYPE_CHECKING:
    from worldmock.life.tool_layer import LayerContext


@dataclass(frozen=True, slots=True)
class Offer:
    """@brief 一件商品的实际店铺、目的地、到达时刻及履约方式。"""

    store_id: str
    goods: Goods
    destination_id: str
    expected_at: int
    fulfillment: str


def menu_goods(context: LayerContext) -> tuple[tuple[str, Goods], ...]:
    """@brief 所选环境所有在售商品，身份按店铺和商品排序。"""
    catalog = context.semantics.catalog
    selected = {place.location_id for place in context.scenario.locations}
    return tuple((store, catalog.goods[identity]) for store in sorted(catalog.goods_by_store)
                 if store in selected for identity in sorted(catalog.goods_by_store[store]))


def nearest_store(context: LayerContext, store_ids) -> str:
    """@brief 去在售店铺时按图上最短距离和地点身份选店。"""
    origin = context.state.agents[context.agent_id].location_id
    return min(set(store_ids), key=lambda identity: (context.semantics.catalog.shortest_m(origin, identity), identity))


def offers(context: LayerContext, goods_id: str, app_id: str | None,
           recipient_id: str) -> tuple[Offer, ...]:
    """@brief 到店只能取当前店铺，在线只取所写应用的在售店铺。"""
    catalog = context.semantics.catalog
    stores = {store for store, goods in menu_goods(context) if goods.goods_id == goods_id}
    if app_id is None:
        here = present_location(context.state, context.agent_id)
        destination = recipient_location(context.state, context.scenario, context.agent_id, recipient_id)
        if here not in stores or destination != here:
            return ()
        return (Offer(here, catalog.goods[goods_id], here, context.state.current_time, "pickup"),)
    result = []
    for store in sorted(stores):
        if app_id not in catalog.apps_by_store[store]:
            continue
        try:
            plan = plan_recipient_delivery(context.state, context.semantics, context.agent_id, recipient_id, store)
        except PreconditionError as error:
            logging.error("Store cannot fulfill this recipient: %s", str(error))
            continue
        result.append(Offer(store, catalog.goods[goods_id], plan.destination_id, plan.expected_at, plan.kind.value))
    return tuple(result)


def choose(served: tuple[Offer, ...]) -> Offer:
    """@brief 在线按最早到达时刻选店，同刻按店铺身份稳定选择。"""
    return min(served, key=lambda offer: (offer.expected_at, offer.store_id))
