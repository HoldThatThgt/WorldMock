"""门店世界恢复和三种道路状态的拒绝合同。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.map_models import RouteConfig
from worldmock.life.models import ActivityStartParams
from worldmock.life.travel import route_for, validate_routes


@pytest.mark.parametrize("damage,reason", [
    ("stock-only", "stock and quote identities differ"),
    ("supply", "Runtime supply identities"),
    ("place", "Runtime opening exception locations"),
    ("quote-interval", "quote effective interval"),
    ("visible-supply", "visibility records"),
])
def test_open_rejects_invalid_storefront_state_before_exposing_world(engine_factory, damage, reason):
    """@brief 已落盘状态必须先通过结构和冻结选区核验，再允许观察或动作。"""
    engine = engine_factory()
    payload = engine.state.model_dump(mode="json")
    supply = next(iter(payload["supply_stock"]))
    if damage in {"stock-only", "supply"}:
        payload["supply_stock"].pop(supply)
        if damage == "supply":
            payload["supply_quotes"].pop(supply)
    elif damage == "place":
        payload["opening_exceptions"].pop("home")
    elif damage == "quote-interval":
        quote = payload["supply_quotes"][supply]
        quote["effective_to"] = quote["effective_from"]
    else:
        agent = payload["agents"]["1"]
        agent["visible_supply_ids"] = ["unknown-supply"]
        agent["supply_browsed_at"] = {"unknown-supply": payload["current_time"]}
        agent["supply_browsed_location"] = {"unknown-supply": "home"}
    payload["version"] += 1
    engine.store.commit(payload, [EventDraft(
        event_type="environment_event", occurred_at=payload["current_time"], visibility="system",
        source_kind="scenario", data={"kind": "test_storefront_state_corruption"})])
    run_dir = engine.store.run_dir
    engine.close()
    with pytest.raises((ValidationError, IntegrityError), match=reason):
        LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)


@pytest.mark.parametrize("status,reason", [
    ("unreachable", "ROUTE_UNREACHABLE"), ("unknown", "ROUTE_INFORMATION_UNKNOWN"),
])
def test_route_selection_rejects_explicit_non_reachable_status(engine_factory, status, reason):
    """@brief 地点仍存在时，不可达和资料未知必须拒绝出行且保留各自原因。"""
    engine = engine_factory()
    original = engine.scenario.routes[0]
    fields = {"status": status, "distance_meters": None,
        "duration_seconds": None, "fare_fen": None, "reason": "Controlled unavailable route"}
    if status == "unknown":
        fields.update(response_path=None, response_sha256=None)
    route = RouteConfig.model_validate({**original.model_dump(), **fields})
    scenario = engine.scenario.model_copy(update={"routes": (route,)})
    request = ActivityStartParams(activity_type="travel", planned_duration_seconds=300,
        destination_id=route.destination_id, travel_mode=route.mode)
    with pytest.raises(BusinessRuleError, match=reason):
        route_for(scenario, route.origin_id, request)
    assert engine.state.agents[1].location_id == "home"


@pytest.mark.parametrize("status,response,reason", [
    ("unreachable", {"code": "NoRoute"}, None),
    ("unreachable", {"code": "Ok", "routes": []}, "requires a NoRoute response"),
    ("reachable", {"code": "Ok", "routes": []}, "response has no route"),
    ("unknown", None, None),
])
def test_route_response_status_is_verified_without_fabricating_unknown_evidence(
        engine_factory, tmp_path, status, response, reason):
    """@brief 显式未知不读虚构文件，已知结果则必须具备与状态相符的原始响应。"""
    engine = engine_factory()
    fields = {"status": status}
    if status != "reachable":
        fields.update(distance_meters=None, duration_seconds=None, fare_fen=None,
                      reason="Controlled unavailable route")
    if response is not None:
        content = json.dumps(response).encode()
        (tmp_path / "route.json").write_bytes(content)
        fields.update(response_path="route.json", response_sha256=hashlib.sha256(content).hexdigest())
    else:
        fields.update(response_path=None, response_sha256=None)
    route = RouteConfig.model_validate({**engine.scenario.routes[0].model_dump(), **fields})
    scenario = engine.scenario.model_copy(update={"routes": (route,)})
    if reason:
        with pytest.raises(IntegrityError, match=reason):
            validate_routes(scenario, tmp_path)
    else:
        validate_routes(scenario, tmp_path)
