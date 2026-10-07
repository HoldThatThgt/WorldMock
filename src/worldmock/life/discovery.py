"""@brief 从冻结地图与当前世界查询地点、路线和统一商品集合。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from worldmock.errors import PreconditionError
from worldmock.life.travel import plan_trip
from worldmock.life.admission import present_location
from worldmock.life import basket
from worldmock.life.interaction_menu import binding_action

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    """@brief 一次发现所用的只读世界、地图、人物及分时解释入口。"""

    state: Any
    scenario: Any
    semantics: Any
    agent: Any


def place_row(context: DiscoveryContext, location) -> dict:
    """@brief 地点发现只呈现登记地点的身份、名称和类型。"""
    return {"location_id": location.location_id, "name": location.display_name, "kind": location.kind}


def supply_row(context: DiscoveryContext, store_id: str, goods) -> dict:
    """@brief 从唯一目录构建固定菜单商品事实。"""
    catalog = context.semantics.catalog
    location = catalog.locations[store_id]
    return {"store_id": store_id, "goods_id": goods.goods_id, "name": goods.name,
            "location_name": location.display_name, "category": goods.category.value,
            "price_fen": goods.price_fen, "app_ids": list(catalog.apps_by_store[store_id])}


def product_rows(context: DiscoveryContext) -> tuple[dict, ...]:
    """@brief 工具呈现与完整查询共用同一商品集合，每件商品合并真实购物应用绑定。"""
    catalog = context.semantics.catalog
    selected = {location.location_id for location in context.scenario.locations}
    present = present_location(context.state, context.agent.agent_id)
    rows = {}
    for store_id, goods in catalog.goods_by_store.items():
        if store_id not in selected or not _store_is_offered(context, store_id, present):
            continue
        for goods_id in goods:
            row = rows.setdefault(goods_id, supply_row(context, store_id, catalog.goods[goods_id]))
            row["app_ids"] = sorted(set(row["app_ids"]) | set(catalog.apps_by_store[store_id]))
    return tuple(row for _, row in sorted(rows.items()))


def _store_is_offered(context: DiscoveryContext, store_id: str, present: str | None) -> bool:
    """@brief 途中只有唯一配送规划确认到驿站的购物门店可以下单。"""
    if present == store_id:
        return True
    if not context.semantics.catalog.apps_by_store[store_id]:
        return False
    if present is not None:
        return True
    try:
        plan = basket.plan_recipient_delivery(context.state, context.semantics, context.agent.agent_id,
                                       f"agent:{context.agent.agent_id}", store_id)
    except PreconditionError as error:
        _LOGGER.error("Goods delivery is unavailable during travel: %s", error)
        return False
    return plan.kind == "station_pickup"


def product_bindings(context) -> list[dict]:
    """@brief 完整权限内商品转成与呈现相同的私有购买绑定，不改变窗口或世界。"""
    if "purchase" not in context.menu.allowed_actions:
        return []
    discovery = DiscoveryContext(context.state, context.scenario, context.semantics,
                                 context.state.agents[context.agent_id])
    return [{"object": row["name"], "kind": "supply", "object_id": row["goods_id"],
             "goods_id": row["goods_id"], "store_id": row["store_id"], "app_ids": row["app_ids"],
             "actions": [binding_action("购买", "purchase", app_id=None, fulfillment="pickup"),
                         *(binding_action("购买", "purchase", app_id=identity, fulfillment="delivery")
                           for identity in row["app_ids"])]} for row in product_rows(discovery)]


def route_row(context: DiscoveryContext, destination_id: str, mode: str) -> dict:
    """@brief 从登记图生成完整行程，时长与车费使用固定系统规则。"""
    trip = plan_trip(context.semantics.catalog.graph, context.agent.location_id, destination_id, mode)
    return {"trip_id": trip.trip_id, "path": list(trip.path), "origin_id": trip.origin_id,
            "destination_id": trip.destination_id, "mode": mode, "distance_meters": trip.distance_meters,
            "duration_seconds": trip.duration_seconds, "fare_fen": trip.fare_fen}
