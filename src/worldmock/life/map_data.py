"""@brief 加载并准入冻结地图文件、路线证据及场景选择范围。"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.life.map_models import (
    FileReference, MapDataset, MapDatasetDeclaration, MapDatasetReference, RouteConfig,
)


def load_map_dataset(root: Path, reference: MapDatasetReference) -> MapDataset:
    """@brief 校验地图版本、文件哈希、来源和内部关系后返回完整地图。"""
    try:
        declaration = MapDatasetDeclaration.model_validate(_read_file(root, reference))
        if (declaration.dataset_id, declaration.version) != (reference.dataset_id, reference.version):
            _fail("Map dataset identity does not match the scenario pin")
        data = declaration.model_dump(mode="python")
        for name in ("locations", "supplies", "routes"):
            payload = _read_file(root, getattr(declaration, f"{name}_file"))
            if set(payload) != {"schema_version", name} or payload["schema_version"] != 1:
                _fail(f"Map {name} file schema is invalid")
            data[name] = payload[name]
        dataset = MapDataset.model_validate(data)
        validate_map_dataset(dataset)
        for route in dataset.routes:
            _validate_route_response(root, route)
        return dataset
    except IntegrityError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as error:
        logging.exception("Map dataset loading failed")
        raise IntegrityError(f"Map dataset validation failed: {error}") from error


def _read_file(root: Path, reference: FileReference) -> dict[str, Any]:
    """@brief 拒绝越界路径、符号链接、篡改和非对象 JSON。"""
    base = Path(root).resolve()
    candidate = base / reference.path
    path = candidate.resolve()
    if base not in path.parents or not path.is_file():
        _fail("Map file path is invalid")
    current = candidate
    while current != base:
        if current.is_symlink():
            _fail("Map file path must not contain symbolic links")
        current = current.parent
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != reference.sha256:
        _fail("Map file hash mismatch")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        _fail("Map file must contain a JSON object")
    return payload


def validate_map_dataset(dataset: MapDataset) -> None:
    """@brief 校验地点、供给、路线关系与发布时的声明可信日期。"""
    _validate_sources(dataset)
    for name, field in (("locations", "location_id"), ("supplies", "supply_id"), ("routes", "route_id")):
        values = [getattr(item, field) for item in getattr(dataset, name)]
        if len(values) != len(set(values)):
            _fail(f"Map {name} identities must be unique")
    locations = {item.location_id for item in dataset.locations}
    _validate_locations(dataset)
    triples = [(item.location_id, item.product_id, item.seller_account_id) for item in dataset.supplies]
    if len(triples) != len(set(triples)):
        _fail("Supply location, product, and seller identities must be unique")
    for supply in dataset.supplies:
        if supply.location_id not in locations or not set(supply.delivery_destination_ids) <= locations:
            _fail("Supply references an unknown map location")
        _validate_dates(supply.quotes, dataset)
    keys = [(item.origin_id, item.destination_id, item.mode) for item in dataset.routes]
    if len(keys) != len(set(keys)):
        _fail("Map route lookup keys must be unique")
    for route in dataset.routes:
        if route.origin_id not in locations or route.destination_id not in locations:
            _fail("Map route references an unknown location")
        if route.mode not in dataset.route_modes:
            _fail("Map route uses an undeclared travel mode")


def _validate_locations(dataset: MapDataset) -> None:
    """@brief 地点必须落在声明选区内，营业日历必须具备有效的核对日期。"""
    west, south, east, north = dataset.bounds
    for location in dataset.locations:
        if not west <= location.longitude <= east or not south <= location.latitude <= north:
            _fail("Location coordinates are outside the map bounds")
        _validate_dates(location.opening_calendars, dataset)


def _validate_dates(declarations, dataset: MapDataset) -> None:
    """@brief 过期或来自发布未来的核对声明不得发布。"""
    prepared = dataset.prepared_at.astimezone(ZoneInfo(dataset.timezone)).date()
    for declaration in declarations:
        if not declaration.reviewed_on <= prepared <= declaration.still_valid_on:
            _fail("Map declaration is stale or reviewed after publication")


def _validate_sources(dataset: MapDataset) -> None:
    """@brief 地图内每份来源必须已经在发布时间前取得。"""
    sources = [dataset.source]
    for location in dataset.locations:
        sources.extend([location.source, *(item.source for item in location.opening_calendars)])
        if location.brand is not None:
            sources.append(location.brand.source)
    for supply in dataset.supplies:
        sources.extend([supply.source, *(item.source for item in supply.quotes)])
    sources.extend(route.source for route in dataset.routes)
    if any(source.retrieved_at > dataset.prepared_at for source in sources):
        _fail("Map source retrieval cannot follow publication")


def _validate_route_response(root: Path, route: RouteConfig) -> None:
    """@brief 冻结成功、不可达或未知的证据，不把缺数据伪装成不可达。"""
    if route.status == "unknown":
        return
    payload = _read_file(root, FileReference(path=route.response_path, sha256=route.response_sha256))
    if route.status == "unreachable":
        if payload.get("code") != "NoRoute":
            _fail("Unreachable route requires a frozen NoRoute response")
        return
    rows = payload.get("routes")
    if payload.get("code") != "Ok" or not isinstance(rows, list) or not rows:
        _fail("Reachable route requires a successful frozen response")
    row = rows[0]
    geometry = row.get("geometry", {})
    if geometry.get("type") != "LineString" or len(geometry.get("coordinates", [])) < 2:
        _fail("Frozen reachable route requires complete road geometry")
    if abs(float(row["distance"]) - route.distance_meters) > 0.1:
        _fail("Frozen route distance mismatch")
    if abs(float(row["duration"]) - route.duration_seconds) > 1:
        _fail("Frozen route duration mismatch")
    modes = {step.get("mode") for leg in row.get("legs", []) for step in leg.get("steps", [])}
    expected = "walking" if route.routing_profile == "foot" else "driving"
    if modes != {expected}:
        _fail("Frozen route response travel mode disagrees with its declared profile")


def select_map(dataset: MapDataset, document) -> tuple[tuple, tuple, tuple]:
    """@brief 将地图投影为场景选择的地点、路线与供给，并拒绝不完整引用。"""
    pin = document.map_dataset
    if (dataset.dataset_id, dataset.version) != (pin.dataset_id, pin.version):
        _fail("Selected map identity disagrees with the scenario pin")
    validate_map_dataset(dataset)
    if dataset.timezone != document.timezone:
        _fail("Scenario timezone disagrees with the selected map")
    location_ids = {item.location_id for item in document.locations}
    supply_ids = {item.supply_id for item in document.supplies}
    locations = tuple(item for item in dataset.locations if item.location_id in location_ids)
    supplies = tuple(item for item in dataset.supplies if item.supply_id in supply_ids)
    if len(locations) != len(location_ids) or len(supplies) != len(supply_ids):
        _fail("Scenario references an unknown map location or supply")
    routes = tuple(item for item in dataset.routes
                   if item.origin_id in location_ids and item.destination_id in location_ids)
    _validate_selected_routes(dataset, location_ids, routes)
    _validate_selected_supplies(document, supplies, location_ids)
    overrides = {item.supply_id: item.initial_stock for item in document.supplies}
    supplies = tuple(item if overrides[item.supply_id] is None else item.model_copy(
        update={"initial_stock": overrides[item.supply_id]}) for item in supplies)
    return locations, routes, supplies


def _validate_selected_routes(dataset: MapDataset, locations: set[str], routes: tuple) -> None:
    """@brief 选择范围的每个有向地点对与方式必须具有显式状态记录。"""
    expected = {(origin, target, mode) for origin in locations for target in locations
                if origin != target for mode in dataset.route_modes}
    actual = {(item.origin_id, item.destination_id, item.mode) for item in routes}
    if expected != actual:
        _fail("Selected map route coverage is incomplete")


def _validate_selected_supplies(document, supplies: tuple, locations: set[str]) -> None:
    """@brief 供给只引用本场景已选商品、地点和显式卖方账户。"""
    products = {item.product_id for item in document.products}
    accounts = {item.account_id for item in document.accounts}
    for item in supplies:
        if item.location_id not in locations or not set(item.delivery_destination_ids) <= locations:
            _fail("Selected supply references an unselected location")
        if item.product_id not in products:
            _fail("Selected supply references an unselected product")
        if item.seller_account_id not in accounts:
            _fail("Selected supply references an undeclared seller account")


def _fail(message: str) -> None:
    """@brief 输出英文诊断并拒绝地图准入。"""
    logging.error(message)
    raise IntegrityError(message)
