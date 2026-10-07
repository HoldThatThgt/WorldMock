"""@brief 严格目录加载、唯一登记校验，以及完整图和非图索引冻结。"""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar

from pydantic import ValidationError

from worldmock.base_models import DeepFrozenModel
from worldmock.catalog.admission import validate_manifest, validate_registration
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.graph import Graph
from worldmock.catalog.identity import read_catalog_file, read_manifest_payload
from worldmock.catalog.models import (
    AppsFile, Catalog, CatalogData, CatalogManifest, ContentsFile, FoodsFile, LocationsFile, MapGraph, WorkTopicsFile,
)
from worldmock.exchange.types import FOOD_STORE_KINDS, LocationKind

_Model = TypeVar("_Model", bound=DeepFrozenModel)


def load_catalog(catalog_dir: Path | str) -> Catalog:
    """@brief 读取已发布文件并校验全部身份和绑定，运行加载不重生成图。"""
    directory = Path(catalog_dir).resolve()
    manifest = parse_catalog_model(read_manifest_payload(directory), CatalogManifest)
    validate_manifest(directory, manifest)
    require_engine_rules(directory)
    data = CatalogData(
        apps=_read_model(directory, "apps.json", AppsFile).apps,
        locations=_read_model(directory, "locations.json", LocationsFile).locations,
        foods=_read_model(directory, "foods.json", FoodsFile).foods,
        contents=_read_model(directory, "contents.json", ContentsFile).contents,
        work_topics=_read_model(directory, "work_topics.json", WorkTopicsFile).work_topics,
        map_data=_read_model(directory, "map.json", MapGraph),
    )
    validate_registration(data)
    return _catalog_view(directory, manifest, data)


def parse_catalog_model(payload: object, model: type[_Model]) -> _Model:
    """@brief 严格模型失败转换为相应稳定登记码，不忽略未知字段。"""
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        first = error.errors(include_url=False)[0]
        field = first["loc"][-1] if first["loc"] else ""
        if field == "store_ids" and first["type"] == "missing":
            code = "FOOD_BINDING_MISSING"
        elif model is MapGraph and field == "length_m":
            code = "MAP_EDGE_INVALID"
        elif field in {"price_fen", "duration_seconds", "length_m", "target_hours", "size_bytes", "seed", "episode"}:
            code = "ENVIRONMENT_NUMBER_INVALID"
        else:
            code = "ENVIRONMENT_KIND_INVALID"
        raise RegistrationError(code, str(error)) from error


def _read_model(directory: Path, relative: str, model: type[_Model]) -> _Model:
    """@brief 读取并解析一个身份已经复核的严格 JSON 文件。"""
    try:
        payload = json.loads(read_catalog_file(directory, relative))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", str(error)) from error
    return parse_catalog_model(payload, model)


def require_engine_rules(directory: Path) -> None:
    """@brief 三份环境 TOML 副本必须逐字节等于引擎包资源。"""
    packaged = files("worldmock.data")
    for relative in ("affordances.toml", "rules/motivation.toml", "rules/persona-mapping.toml"):
        if read_catalog_file(directory, relative) != packaged.joinpath(Path(relative).name).read_bytes():
            raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH")


def _catalog_view(directory: Path, manifest: CatalogManifest, data: CatalogData) -> Catalog:
    """@brief 在登记通过后一次建立真实不可变对象和绑定索引。"""
    food_stores = {place.location_id for place in data.locations if place.kind in FOOD_STORE_KINDS}
    foods_by_store = {place.location_id: tuple(food.food_id for food in data.foods
                       if food.store_ids == "all" and place.location_id in food_stores
                       or food.store_ids != "all" and place.location_id in food.store_ids)
                      for place in data.locations}
    menus = {kind: next((foods_by_store[place.location_id] for place in data.locations if place.kind == kind), ())
             for kind in LocationKind}
    return Catalog(
        manifest=manifest, source_dir=directory,
        apps=MappingProxyType({row.app_id: row for row in data.apps}),
        locations=MappingProxyType({row.location_id: row for row in data.locations}),
        foods=MappingProxyType({row.food_id: row for row in data.foods}),
        contents=MappingProxyType({row.content_id: row for row in data.contents}),
        work_topics=MappingProxyType({row.topic_id: row for row in data.work_topics}),
        map_data=data.map_data, map_sha256=hashlib.sha256(read_catalog_file(directory, "map.json")).hexdigest(),
        graph=Graph(data.map_data),
        menus_by_kind=MappingProxyType(menus), foods_by_store=MappingProxyType(foods_by_store),
        stores_by_app=MappingProxyType({app.app_id: tuple(place.location_id for place in data.locations
            if app.app_id in place.app_ids) for app in data.apps}),
        contents_by_app=MappingProxyType({app.app_id: tuple(content.content_id for content in data.contents
            if app.app_id in content.app_ids) for app in data.apps}),
        apps_by_store=MappingProxyType({place.location_id: place.app_ids for place in data.locations}),
    )
