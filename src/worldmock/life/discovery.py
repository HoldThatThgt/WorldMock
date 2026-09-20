"""@brief 从冻结地图与当前世界查询地点、路线和独立门店供给。"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from worldmock.errors import BusinessRuleError
from worldmock.life.opening_hours import opening_status
from worldmock.life.storefront_state import current_quote


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    """@brief 一次发现所用的只读世界、地图、人物及分时解释入口。"""

    state: Any
    scenario: Any
    semantics: Any
    agent: Any


def discover_places(state, scenario, semantics, agent, request) -> dict:
    """@brief 保留所有匹配地点，分别解释超范围、不可达、未知和当前不可购买。"""
    context = DiscoveryContext(state, scenario, semantics, agent)
    matches = _matching_rows(scenario, request)
    identity = _cursor_identity(context, request, matches)
    offset = _cursor_offset(request.cursor, identity, len(matches))
    limit = semantics.candidate_constants.presentation_limit
    selected = matches[offset:offset + limit]
    places = {}
    for location, supply in selected:
        if location.location_id not in places:
            row = place_row(context, location)
            row["route"] = route_row(context, location.location_id, request.travel_mode)
            row["range_status"] = range_status(row["route"], request.max_travel_seconds)
            row["supplies"] = []
            places[location.location_id] = row
        if supply is not None:
            places[location.location_id]["supplies"].append(supply_row(context, supply))
    next_offset = offset + len(selected)
    cursor = (json.dumps({"offset": next_offset, "identity": identity}, separators=(",", ":"))
              if next_offset < len(matches) else None)
    return {"tool": "discover_places", "query": request.model_dump(mode="json"),
            "generated_at": state.current_time, "total": len(matches), "returned": len(selected),
            "returned_places": len(places), "next_cursor": cursor, "places": list(places.values())}


def _matching_rows(scenario, request) -> list[tuple]:
    """@brief 按地点与供给身份展开分页全集，不根据营业、库存或路线状态删除匹配。"""
    rows = []
    for location in sorted(scenario.locations, key=lambda item: item.location_id):
        if request.business_kind is not None and location.business_kind != request.business_kind:
            continue
        supplies = sorted((item for item in scenario.supplies if item.location_id == location.location_id
                          and (request.product_id is None or item.product_id == request.product_id)),
                         key=lambda item: item.supply_id)
        if supplies:
            rows.extend((location, supply) for supply in supplies)
        elif request.product_id is None:
            rows.append((location, None))
    return rows


def _cursor_identity(context: DiscoveryContext, request, matches: list[tuple]) -> str:
    """@brief 游标绑定地图、筛选与起点；世界版本和模拟时间变化不破坏下一页。"""
    dataset = context.scenario.map_dataset
    payload = {"dataset_id": dataset.dataset_id, "version": dataset.version,
               "files": [dataset.locations_file.sha256, dataset.supplies_file.sha256, dataset.routes_file.sha256],
               "selection": [(place.location_id, supply.supply_id if supply else None) for place, supply in matches],
               "origin": context.agent.location_id, "query": request.model_dump(mode="json", exclude={"cursor"})}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cursor_offset(cursor: str | None, identity: str, total: int) -> int:
    """@brief 拒绝跨地图、跨条件、跨起点或越界游标，不能静默重启第一页。"""
    if cursor is None:
        return 0
    try:
        data = json.loads(cursor)
    except (ValueError, TypeError):
        data = None
    if (not isinstance(data, dict) or set(data) != {"offset", "identity"} or data["identity"] != identity
            or isinstance(data["offset"], bool) or not isinstance(data["offset"], int)
            or not 0 < data["offset"] < total):
        logging.error("Discovery cursor does not match the frozen query")
        raise BusinessRuleError("DISCOVERY_CURSOR_INVALID")
    return data["offset"]


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
