"""@brief 严格目录加载、唯一登记校验，以及完整图和非图索引冻结。"""

from __future__ import annotations

import hashlib
import json
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
    ActionsFile, AppsFile, Catalog, CatalogData, CatalogManifest, ContentsFile, GoodsFile, LocationsFile, MapGraph,
)
from worldmock.exchange.types import AppPurpose, GOODS_CATEGORY_STORE_KINDS, LocationKind

_Model = TypeVar("_Model", bound=DeepFrozenModel)


def load_catalog(catalog_dir: Path | str) -> Catalog:
    """@brief 读取已发布文件并校验全部身份和绑定，运行加载不重生成图。"""
    directory = Path(catalog_dir).resolve()
    manifest = parse_catalog_model(read_manifest_payload(directory), CatalogManifest)
    validate_manifest(directory, manifest)
    data = CatalogData(
        apps=_read_model(directory, "apps.json", AppsFile).apps,
        locations=_read_model(directory, "locations.json", LocationsFile).locations,
        goods=_read_model(directory, "goods.json", GoodsFile).goods,
        actions=_read_model(directory, "actions.json", ActionsFile).actions,
        contents=_read_model(directory, "contents.json", ContentsFile).contents,
        map_data=_read_model(directory, "map.json", MapGraph),
    )
    validate_registration(data)
    return _catalog_view(directory, manifest, data)


def parse_catalog_model(payload: object, model: type[_Model]) -> _Model:
    """@brief 严格模型失败转换为相应稳定登记码，不忽略未知字段。"""
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        failures = error.errors(include_url=False)
        first = failures[0]
        field = first["loc"][-1] if first["loc"] else ""
        if any(failure["type"] == "extra_forbidden" for failure in failures):
            code = "ENVIRONMENT_KIND_INVALID"
        elif field == "store_ids" and first["type"] == "missing":
            code = "GOODS_BINDING_MISSING"
        elif field == "action_id" and first["type"] == "missing":
            code = "CONTENT_ACTION_MISSING"
        elif model is MapGraph and field == "length_m":
            code = "MAP_EDGE_INVALID"
        elif field in {"price_fen", "duration_seconds", "length_m", "size_bytes", "seed", "episode"}:
            code = "ENVIRONMENT_NUMBER_INVALID"
        else:
            code = "ENVIRONMENT_KIND_INVALID"
        raise RegistrationError(code, str(error)) from error


def _read_model(directory: Path, relative: str, model: type[_Model]) -> _Model:
    """@brief 读取并解析一个身份已经复核的严格 JSON 文件。"""
    content = read_catalog_file(directory, relative)
    if b"\r" in content:
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", "Catalog JSON must use LF line endings.")
    try:
        payload = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", str(error)) from error
    return parse_catalog_model(payload, model)


def _goods_indexes(data: CatalogData) -> dict:
    """@brief 展开固定类别供给规则并冻结各地点和类型的菜单。"""
    by_store = {
        place.location_id: tuple(goods.goods_id for goods in data.goods
                                 if place.kind in GOODS_CATEGORY_STORE_KINDS[goods.category]
                                 and (goods.store_ids == "all" or place.location_id in goods.store_ids))
        for place in data.locations
    }
    menus = {kind: next((by_store[place.location_id] for place in data.locations if place.kind == kind), ())
             for kind in LocationKind}
    return {"goods_by_store": MappingProxyType(by_store), "menus_by_kind": MappingProxyType(menus)}


def _content_indexes(data: CatalogData) -> dict:
    """@brief 动作、动作应用组合与系列索引使用已登记的单一内容身份。"""
    series = {(content.action_id, content.series.title) for content in data.contents if content.series is not None}
    return {
        "contents_by_action": MappingProxyType({
            action.action_id: tuple(content.content_id for content in data.contents
                                    if content.action_id == action.action_id) for action in data.actions}),
        "contents_by_action_app": MappingProxyType({
            (action.action_id, app_id): tuple(content.content_id for content in data.contents
                                            if content.action_id == action.action_id and app_id in content.app_ids)
            for action in data.actions for app_id in action.app_ids}),
        "contents_by_series": MappingProxyType({
            key: tuple(content.content_id for content in sorted(
                (content for content in data.contents
                 if content.series is not None and (content.action_id, content.series.title) == key),
                key=lambda content: content.series.episode)) for key in sorted(series)}),
    }


def _app_indexes(data: CatalogData) -> dict:
    """@brief 按应用索引店铺与动作，按用途索引应用。"""
    return {
        "stores_by_app": MappingProxyType({app.app_id: tuple(place.location_id for place in data.locations
                                                           if app.app_id in place.app_ids) for app in data.apps}),
        "apps_by_store": MappingProxyType({place.location_id: place.app_ids for place in data.locations}),
        "actions_by_app": MappingProxyType({app.app_id: tuple(action.action_id for action in data.actions
                                                            if app.app_id in action.app_ids) for app in data.apps}),
        "apps_by_purpose": MappingProxyType({purpose: tuple(app.app_id for app in data.apps
                                                           if purpose in app.purposes) for purpose in AppPurpose}),
    }


def _catalog_view(directory: Path, manifest: CatalogManifest, data: CatalogData) -> Catalog:
    """@brief 在登记通过后一次建立真实不可变对象和绑定索引。"""
    return Catalog(
        manifest=manifest, source_dir=directory,
        apps=MappingProxyType({row.app_id: row for row in data.apps}),
        locations=MappingProxyType({row.location_id: row for row in data.locations}),
        goods=MappingProxyType({row.goods_id: row for row in data.goods}),
        actions=MappingProxyType({row.action_id: row for row in data.actions}),
        contents=MappingProxyType({row.content_id: row for row in data.contents}),
        map_data=data.map_data, map_sha256=hashlib.sha256(read_catalog_file(directory, "map.json")).hexdigest(),
        graph=Graph(data.map_data), **_goods_indexes(data), **_content_indexes(data), **_app_indexes(data),
    )
