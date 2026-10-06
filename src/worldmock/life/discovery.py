"""@brief 从冻结地图与当前世界查询地点、路线和独立门店供给。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worldmock.life.travel import plan_trip


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


def supply_row(context: DiscoveryContext, store_id: str, food) -> dict:
    """@brief 从唯一目录构建固定菜单食品事实，不产生库存、营业或报价状态。"""
    catalog = context.semantics.catalog
    location = catalog.locations[store_id]
    return {"store_id": store_id, "food_id": food.food_id, "name": food.name,
            "location_name": location.display_name, "category": food.category.value,
            "price_fen": food.price_fen, "app_ids": list(catalog.apps_by_store[store_id])}


def route_row(context: DiscoveryContext, destination_id: str, mode: str) -> dict:
    """@brief 从登记图生成完整行程，时长与车费使用固定系统规则。"""
    trip = plan_trip(context.semantics.catalog.graph, context.agent.location_id, destination_id, mode)
    return {"trip_id": trip.trip_id, "path": list(trip.path), "origin_id": trip.origin_id,
            "destination_id": trip.destination_id, "mode": mode, "distance_meters": trip.distance_meters,
            "duration_seconds": trip.duration_seconds, "fare_fen": trip.fare_fen}
