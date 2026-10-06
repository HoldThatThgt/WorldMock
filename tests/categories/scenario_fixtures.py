"""@brief 品类发布使用可真实加载的人工场景、目录和双向路线。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from tests.categories.conftest import source
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from worldmock.categories import refresh_manifest_identity


def write_scenario_catalog(destination: Path) -> Path:
    """@brief 复制五对象人工目录并声明驾车规则，预留一个合法旧版本。"""
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT / "catalog", destination)
    contract = _read(destination / "category-contract.json")
    contract["contract_version"] = 2
    contract["pairings"].append({"actor_category_id": "person", "target_category_id": "service",
                                 "action": "travel:driving", "state": "defined",
                                 "rule_id": "test-driving", "coefficient": 1})
    _write(destination / "category-contract.json", contract)
    rules = _read(destination / "exchange-rules.json")
    rules["contract_version"] = 2
    rules["rules"].append({"rule_id": "test-driving", "category_id": "service", "action": "travel:driving",
                            "operator": "travel_fare", "resource_id": "money_fen", "unit": "fen",
                            "conservation": "closed", "minimum_fen": 0, "fen_per_meter": 0})
    rules["sources"].append(source("test-driving-values", ["/rules/2"]))
    _write(destination / "exchange-rules.json", rules)
    manifest = _read(destination / "manifest.json")
    manifest["version_minor"] = 1
    _write(destination / "manifest.json", manifest)
    refresh_manifest_identity(destination)
    return destination


def write_scenario_for_catalog(directory: Path, path: Path) -> Path:
    """@brief 把人工场景绑定目录版本，加入两个地点和有冻结证据的双向路线。"""
    root = path.parent / "corpora"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT / "corpora", root)
    scenario = _read(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/multi-person-half-day.json")
    _write_map_routes(root)
    _refresh_corpus_manifest(root)
    manifest = _read(directory / "manifest.json")
    scenario["corpus_root"] = "corpora"
    scenario["corpus_manifest_sha256"] = _digest(root / "manifest.json")
    scenario["catalog_version"] = {"major": manifest["version_major"], "minor": manifest["version_minor"]}
    scenario["locations"].append({"location_id": "office"})
    scenario["interaction_menus_by_location_kind"]["office"] = scenario["interaction_menus_by_location_kind"]["home"]
    scenario["map_dataset"]["sha256"] = _digest(root / scenario["map_dataset"]["path"])
    _write(path, scenario)
    return path


def _write_map_routes(root: Path) -> None:
    """@brief 地图中声明两个地点、一个出行方式及每个有向地点对。"""
    path = root / "artificial-map/locations.json"
    locations = _read(path)
    home = locations["locations"][0]
    office = {**home, "location_id": "office", "name": "Test Office", "kind": "office",
              "business_kind": "office", "longitude": 0.6, "latitude": 0.6, "location_kinds": ["office"]}
    locations["locations"].append(office)
    _write(path, locations)
    routes = [_route(root, origin, target, home["source"])
              for origin, target in (("home", "office"), ("office", "home"))]
    _write(root / "artificial-map/routes.json", {"schema_version": 3, "routes": routes})
    dataset_path = root / "artificial-map/dataset.json"
    dataset = _read(dataset_path)
    dataset["route_modes"] = ["driving"]
    dataset["locations_file"]["sha256"] = _digest(path)
    dataset["routes_file"]["sha256"] = _digest(root / "artificial-map/routes.json")
    _write(dataset_path, dataset)


def _route(root: Path, origin: str, target: str, provenance: dict) -> dict:
    """@brief 每条人工路线具有真实冻结响应、几何、距离、时长和方式依据。"""
    relative = f"artificial-map/{origin}-{target}-response.json"
    coordinates = [[0.5, 0.5], [0.6, 0.6]]
    if origin == "office":
        coordinates.reverse()
    response = {"code": "Ok", "routes": [{"distance": 100, "duration": 60,
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "legs": [{"steps": [{"mode": "driving"}]}]}]}
    _write(root / relative, response)
    return {"route_id": f"test-{origin}-{target}", "origin_id": origin, "destination_id": target,
            "mode": "driving", "status": "reachable", "distance_meters": 100, "duration_seconds": 60,
            "fare_fen": 0, "fare_source_kind": "scenario", "response_path": relative,
            "response_sha256": _digest(root / relative), "source_url": "fixture://routing/route/v1/driving",
            "routing_instance": "fixture://routing", "routing_profile": "car", "source": provenance}


def _refresh_corpus_manifest(root: Path) -> None:
    """@brief 仅刷新此次人工环境的字节身份，不触碰历史运行证据。"""
    path = root / "manifest.json"
    document = _read(path)
    template = document["entries"][0]
    document["entries"] = []
    for asset in sorted(root.rglob("*")):
        if asset.is_file() and asset != path:
            relative = asset.relative_to(root).as_posix()
            document["entries"].append({**template, "entry_id": relative, "path": relative,
                                        "sha256": _digest(asset), "size_bytes": asset.stat().st_size})
    _write(path, document)


def _read(path: Path) -> dict:
    """@brief 读取人工 JSON 声明。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    """@brief 写入人工 JSON 声明。"""
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    """@brief 计算人工资源的原始字节摘要。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()
