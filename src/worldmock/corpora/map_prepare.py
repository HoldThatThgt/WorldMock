"""@brief 为已审阅地点集合准备全部有向路线，核验真实出行方式后发布冻结地图。"""

from __future__ import annotations
from worldmock.catalog.loader import load_catalog
from worldmock.corpora.storefront_build import build_storefront_supplies
from worldmock.corpora.storefront import validate_assortments
from worldmock.categories import Action, TravelFareRule, evaluate_travel_fare
from worldmock.life.category_contract import require_runtime_rule

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from worldmock.errors import IntegrityError
from worldmock.life.map_data import load_map_dataset
from worldmock.life.map_models import MAP_SCHEMA_VERSION, MapDatasetDeclaration, MapDatasetReference


def prepare_map(destination: Path, frozen_root: Path, random_seed: int) -> MapDatasetReference:
    """@brief 复制已审阅的地点和供给，按其完整路线范围重新取得路由响应。"""
    source = Path(frozen_root).resolve()
    dataset_relative = f"zhangjiang-map/{random_seed}/dataset.json"
    declaration_path = source / dataset_relative
    declaration = MapDatasetDeclaration.model_validate(json.loads(declaration_path.read_text(encoding="utf-8")))
    reference = MapDatasetReference(dataset_id=declaration.dataset_id, version=declaration.version,
                                    path=dataset_relative, sha256=_sha(declaration_path))
    dataset = load_map_dataset(source, reference)
    target = Path(destination).resolve()
    folder = target / f"zhangjiang-map/{random_seed}"
    folder.mkdir(parents=True, exist_ok=True)
    for relative in (dataset.locations_file.path, dataset.supplies_file.path,
                     "zhangjiang-map/category-assignment.json",
                     "zhangjiang-map/supply-assumptions.json", "zhangjiang-map/zhangjiang-probe.osm"):
        copied = target / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes((source / relative).read_bytes())

    fare = load_catalog(source.parent / "catalog")
    assumptions = json.loads((source / "zhangjiang-map/supply-assumptions.json").read_text(encoding="utf-8"))
    supplies = build_storefront_supplies(fare, dataset.category_assignment, dataset.locations,
                                       assumptions, random_seed)
    _write_json(target / dataset.supplies_file.path,
                {"schema_version": MAP_SCHEMA_VERSION, "supplies": [item.model_dump(mode="json") for item in supplies]})
    routes = _prepare_routes(target, dataset, fare)
    _write_json(target / dataset.routes_file.path, {"schema_version": MAP_SCHEMA_VERSION, "routes": routes})
    payload = declaration.model_dump(mode="json")
    payload.update(version=declaration.version + 1, prepared_at=datetime.now(ZoneInfo(dataset.timezone)).isoformat())
    for name in ("locations", "supplies", "routes"):
        relative = payload[f"{name}_file"]["path"]
        payload[f"{name}_file"]["sha256"] = _sha(target / relative)
    _write_json(folder / "dataset.json", payload)
    result = reference.model_copy(update={"version": payload["version"], "sha256": _sha(folder / "dataset.json")})

    validate_assortments(load_map_dataset(target, result), fare)
    return result


def _prepare_routes(root: Path, dataset, fare) -> list[dict]:
    """@brief 按冻结路由配置逐条取数；公共实例请求间隔至少一秒。"""
    places = {item.location_id: item for item in dataset.locations}
    result = []
    last_request = 0.0
    for route in dataset.routes:
        elapsed = time.monotonic() - last_request
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        url = _route_url(route, places)
        last_request = time.monotonic()
        data = _fetch_response(url, route)
        relative = f"zhangjiang-map/routes/{route.route_id}.json"
        path = root / relative
        _write_json(path, data)
        amount = _route_fare(fare, route.mode.value, data)
        result.append(_route_metadata(route, url, data, path, amount))
    return result


def _fetch_response(url: str, route) -> dict:
    """@brief 网络或响应解析错误必须留下诊断，并停止不完整的发布。"""
    try:
        response = requests.get(url, timeout=60, headers={"User-Agent": "WorldMock/1.0 research map preparation"})
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        raise IntegrityError(f"Route response retrieval failed for {route.route_id}") from error
    _validate_response(data, route)
    return data


def _route_url(route, places: dict) -> str:
    """@brief 路由查询仅使用选定地点坐标与显式实例配置。"""
    origin, target = places[route.origin_id], places[route.destination_id]
    coordinates = f"{origin.longitude},{origin.latitude};{target.longitude},{target.latitude}"
    profile = "foot" if route.routing_profile == "foot" else "driving"
    query = urlencode({"alternatives": "false", "steps": "true", "overview": "full",
                       "geometries": "geojson", "generate_hints": "false"})
    return f"{route.routing_instance}/route/v1/{profile}/{coordinates}?{query}"


def _validate_response(data: dict, route) -> None:
    """@brief 不可达必须是 NoRoute，成功响应必须与实际道路出行方式一致。"""
    if not isinstance(data, dict):
        raise IntegrityError(f"Route preparation requires a JSON object for {route.route_id}")
    if data.get("code") == "NoRoute":
        return
    rows = data.get("routes")
    if data.get("code") != "Ok" or not isinstance(rows, list) or not rows:
        raise IntegrityError(f"Route preparation failed for {route.route_id}")
    value = rows[0]
    geometry = value.get("geometry", {})
    if (value.get("distance", 0) <= 0 or value.get("duration", 0) <= 0
            or geometry.get("type") != "LineString" or len(geometry.get("coordinates", [])) < 2):
        raise IntegrityError(f"Route response is incomplete for {route.route_id}")
    actual = {step.get("mode") for leg in value.get("legs", []) for step in leg.get("steps", [])}
    expected = "walking" if route.routing_profile == "foot" else "driving"
    if actual != {expected}:
        raise IntegrityError(f"Route response mode is invalid for {route.route_id}")


def _route_metadata(route, url: str, data: dict, path: Path, fare: int) -> dict:
    """@brief 保存响应身份、实际取得时刻、实例、配置及可达状态。"""
    now = datetime.now(ZoneInfo("UTC"))
    value = route.model_dump(mode="json")
    source = route.source.model_dump(mode="json")
    source.update(kind="fact", url=url, retrieved_at=now.isoformat(), fact_cutoff=now.date().isoformat())
    value.update(source_url=url, response_path=f"zhangjiang-map/routes/{route.route_id}.json",
                 response_sha256=_sha(path), source=source)
    if data["code"] == "NoRoute":
        value.update(status="unreachable", reason="NoRoute", distance_meters=None, duration_seconds=None, fare_fen=None)
    else:
        first = data["routes"][0]
        value.update(status="reachable", reason=None, distance_meters=first["distance"],
                     duration_seconds=round(first["duration"]), fare_fen=fare)
    return value


def _route_fare(catalog, mode: str, data: dict) -> int:
    """@brief 复用品类契约的出行算子，路线准备与运行结算使用相同规则。"""

    if data["code"] == "NoRoute":
        return 0
    category = catalog.objects["travel-service"].category_id
    match = require_runtime_rule(catalog, category, Action(f"travel:{mode}"))
    if not isinstance(match.rule, TravelFareRule):
        raise IntegrityError("Travel category resolved to an incompatible exchange rule")
    return evaluate_travel_fare(match.rule, data["routes"][0]["distance"], match.coefficient)


def _sha(path: Path) -> str:
    """@brief 计算冻结文件身份。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    """@brief 创建目录并保存可审阅 JSON，保持字段顺序和 Unicode。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
