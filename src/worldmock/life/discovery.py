"""@brief 从冻结地图与当前世界查询地点、路线和独立门店供给。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worldmock.life.opening_hours import opening_status
from worldmock.life.storefront_state import current_quote


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    """@brief 一次发现所用的只读世界、地图、人物及分时解释入口。"""

    state: Any
    scenario: Any
    semantics: Any
    agent: Any


def place_row(context: DiscoveryContext, location) -> dict:
    """@brief 地点分类、营业、访问资格和来源均作为事实呈现。"""
    required = set(location.required_qualifications)
    missing = sorted(required - set(context.agent.qualifications))
    status = opening_status(location, context.state.current_time, context.scenario.timezone,
                            context.state.opening_exceptions.get(location.location_id))
    return {"location_id": location.location_id, "name": location.name,
            "kind": location.kind, "business_kind": location.business_kind,
            "location_kinds": list(location.location_kinds),
            "opening_status": status,
            "access_allowed": not missing, "required_qualifications": sorted(required),
            "missing_qualifications": missing,
            "source": location.source.model_dump(mode="json"),
            "scenario_assumption": location.source.kind == "scenario"}


def supply_row(context: DiscoveryContext, supply) -> dict:
    """@brief 独立供给行使用世界当前报价，商品属性仅从引用的商品读取。"""
    product = next(item for item in context.scenario.products if item.product_id == supply.product_id)
    location = next(item for item in context.scenario.locations if item.location_id == supply.location_id)
    place = place_row(context, location)
    quote = current_quote(context.state, supply.supply_id)
    missing = sorted(set(supply.required_qualifications) - set(context.agent.qualifications))
    stock = context.state.supply_stock[supply.supply_id]
    pairing = context.semantics.purchase_pairing(product.product_id)
    defined = pairing.state == "defined"
    return {"supply_id": supply.supply_id, "product_id": supply.product_id,
            "name": product.name, "location_id": supply.location_id, "location_name": location.name,
            "seller_account_id": supply.seller_account_id,
            "price_fen": quote.price_fen if quote else None,
            "purchase_pairing_state": pairing.state.value,
            "purchase_coefficient": pairing.coefficient if defined else None,
            "purchase_price_fen": (context.semantics.purchase_amount(product.product_id, quote.price_fen, 1)
                                   if quote and defined else None),
            "price_version": quote.price_version if quote else None,
            "quote_status": "known" if quote else "unknown",
            "quote": quote.model_dump(mode="json") if quote else None, "current_stock": stock,
            "fulfillment_modes": list(supply.fulfillment_modes),
            "delivery_destination_ids": list(supply.delivery_destination_ids),
            "delivery_delay_seconds": supply.delivery_delay_seconds,
            "opening_status": place["opening_status"], "access_allowed": place["access_allowed"],
            "required_qualifications": sorted(supply.required_qualifications),
            "missing_qualifications": missing, "service_allowed": not missing,
            "purchase_allowed": (place["opening_status"] == "open" and place["access_allowed"]
                                 and not missing and quote is not None and stock > 0 and defined),
            "pickup_allowed": ("pickup" in supply.fulfillment_modes
                               and supply.location_id == context.agent.location_id),
            "energy_kcal_per_unit": product.energy_kcal_per_unit,
            "use_duration_seconds": product.use_duration_seconds,
            "source": supply.source.model_dump(mode="json"),
            "scenario_assumption": supply.source.kind == "scenario"}


def route_row(context: DiscoveryContext, destination_id: str, mode: str) -> dict:
    """@brief 只使用已准入的道路记录；当前位置无需虚构一段路线。"""
    if destination_id == context.agent.location_id:
        return {"route_id": None, "origin_id": destination_id, "destination_id": destination_id,
                "mode": mode, "status": "reachable", "distance_meters": 0, "duration_seconds": 0,
                "fare_fen": 0, "reason": "already_at_destination", "source": None}
    route = next(item for item in context.scenario.routes
                 if item.origin_id == context.agent.location_id and item.destination_id == destination_id
                 and item.mode.value == mode)
    fields = {"route_id", "origin_id", "destination_id", "mode", "status", "distance_meters",
              "duration_seconds", "fare_fen", "reason", "source", "routing_instance", "routing_profile"}
    row = route.model_dump(mode="json", include=fields)
    row["free_flow_duration_seconds"] = route.duration_seconds
    if route.status == "reachable":
        row["duration_seconds"] = context.semantics.travel_planned_seconds(
            mode, route.duration_seconds, context.state.current_time)
        row["travel_time_factor"] = context.semantics.travel_time_factor(mode, context.state.current_time)
    return row


def range_status(route: dict, maximum_seconds: int | None) -> str:
    """@brief 道路范围只比较真实预计时长，不把资料未知解释为超范围。"""
    if route["status"] != "reachable":
        return route["status"]
    if maximum_seconds is None:
        return "unrestricted"
    return "inside" if route["duration_seconds"] <= maximum_seconds else "outside"


def stage_visible_supplies(state, agent_id: int, supply_ids: set[str], scenario):
    """@brief 仅把实际呈现供给写成可见凭据；过期和换位置的旧凭据不继承。"""
    from worldmock.life.projection import visible_supplies

    if not supply_ids:
        return state
    agent = state.agents[agent_id]
    visible = set(visible_supplies(state, agent, scenario)) | supply_ids
    times = {identity: agent.supply_browsed_at[identity] for identity in visible - supply_ids}
    places = {identity: agent.supply_browsed_location[identity] for identity in visible - supply_ids}
    times.update({identity: state.current_time for identity in supply_ids})
    places.update({identity: agent.location_id for identity in supply_ids})
    updated = agent.model_copy(update={"visible_supply_ids": visible,
                                       "supply_browsed_at": times, "supply_browsed_location": places}, deep=True)
    return state.model_copy(update={"agents": {**state.agents, agent_id: updated}}, deep=True)
