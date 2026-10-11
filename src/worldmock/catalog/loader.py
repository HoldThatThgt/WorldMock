"""@brief 严格 JSONL 目录加载、回调登记与内存地图索引。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn, TypeVar

from pydantic import ValidationError

from worldmock.base_models import DeepFrozenModel
from worldmock.catalog.admission import validate_manifest, validate_map, validate_registration, validate_route_limit
from worldmock.catalog.callback_models import (
    ActionTrigger, CallbackRule, CallbackState, EventTrigger, MonthlyTrigger, StateReminder, StateTrigger,
    TimeTrigger, TravelTrigger, VisibleHint,
)
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.graph import Graph
from worldmock.catalog.identity import read_catalog_file, read_manifest_payload
from worldmock.catalog.mapgen import generate_map, map_bytes
from worldmock.catalog.models import (
    App, Catalog, CatalogManifest, Content, Goods, Location, MapEdge, MapGraph, TravelMethod,
)
from worldmock.errors import reject_value
from worldmock.exchange.constants import RULES_VERSION, WALKING_TRAVEL_METHOD_ID
from worldmock.life.randomness import random_stream

_Model = TypeVar("_Model", bound=DeepFrozenModel)


def load_catalog(catalog_dir: Path | str, seed: int) -> Catalog:
    """@brief 读取登记的严格 JSONL，统一核对引用后用声明的距离和唯一种子生成内存地图。"""
    directory, manifest, rows = _registered_catalog(catalog_dir)
    if type(seed) is not int:
        raise RegistrationError("ENVIRONMENT_NUMBER_INVALID", "The environment seed must be a strict integer.")
    state = hashlib.sha256(f"random:{seed}".encode()).hexdigest()
    locations = [row.location_id for row in rows["locations"]]
    published = generate_map(locations, random_stream(state, "map").getrandbits(64), rows["map_edges"])
    validate_map(published, set(locations))
    graph = Graph(published)
    validate_route_limit(published.nodes, graph, rows["travel_methods"])
    return _catalog_view(directory, manifest, rows, published, graph)


def validate_catalog_files(catalog_dir: Path | str) -> None:
    """@brief 只验证目录文件与对象登记，不需要或虚构运行种子。"""
    _registered_catalog(catalog_dir)


def _registered_catalog(catalog_dir: Path | str) -> tuple[Path, CatalogManifest, dict]:
    """@brief 加载与纯校验共用唯一文件解析和对象准入阶段。"""
    directory = Path(catalog_dir)
    if directory.is_symlink():
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH", str(directory))
    directory = directory.resolve()
    payload = read_manifest_payload(directory)
    if type(payload.get("rules_version")) is int and payload["rules_version"] != RULES_VERSION:
        raise RegistrationError("RULES_VERSION_MISMATCH")
    manifest = parse_catalog_model(payload, CatalogManifest)
    validate_manifest(directory, manifest)
    listed = {entry.path for entry in manifest.files}
    rows = {
        "apps": read_catalog_rows(directory, "app-catalog.jsonl", App),
        "locations": read_catalog_rows(directory, "location.jsonl", Location),
        "goods": read_catalog_rows(directory, "merchant-catalog.jsonl", Goods),
        "contents": read_catalog_rows(directory, "content-catalog.jsonl", Content),
        "map_edges": _optional_rows(directory, "map.jsonl", listed, MapEdge),
        "travel_methods": _optional_rows(directory, "travel-method.jsonl", listed, TravelMethod),
    }
    rows.update(_read_callbacks(_optional_file(directory, "callbacks.jsonl", listed)))
    validate_registration(rows)
    return directory, manifest, rows


def parse_catalog_model(payload: object, model: type[_Model]) -> _Model:
    """@brief 严格模型错误转换为稳定登记码，未知字段优先于数字错误。"""
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        failures = error.errors(include_url=False)
        numeric = {"price_fen", "duration_seconds", "length_m", "size_bytes", "seed", "episode",
                   "browse_duration_seconds", "initial", "cap", "delta", "below", "amount_fen", "day",
                   "speed_meters_per_second", "start_price_fen", "price_per_km_fen"}
        if any(failure["type"] == "extra_forbidden" for failure in failures):
            code = "ENVIRONMENT_KIND_INVALID"
        elif any(failure["loc"] and failure["loc"][-1] in numeric for failure in failures):
            code = "ENVIRONMENT_NUMBER_INVALID"
        else:
            code = "ENVIRONMENT_KIND_INVALID"
        raise RegistrationError(code, str(error)) from error


def parse_catalog_objects(content: bytes, relative: str) -> tuple[dict, ...]:
    """@brief 逐行读取标准 UTF-8 JSON 对象，零字节为零行，拒绝任何 CR。"""
    if b"\r" in content:
        line = content[:content.index(b"\r")].count(b"\n") + 1
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", f"{relative}:{line} Catalog JSON must use LF line endings.")
    if not content:
        return ()
    lines = content.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    return tuple(_parse_jsonl_object(line, f"{relative}:{number}") for number, line in enumerate(lines, start=1))


def parse_catalog_lines(content: bytes, relative: str, model: type[_Model]) -> tuple[_Model, ...]:
    """@brief 严格行模型的任何失败均附上真实文件与行号。"""
    rows = []
    for number, payload in enumerate(parse_catalog_objects(content, relative), start=1):
        try:
            rows.append(parse_catalog_model(payload, model))
        except RegistrationError as error:
            raise RegistrationError(error.code, f"{relative}:{number} {error.detail or error.message}") from error
    return tuple(rows)


def read_catalog_rows(directory: Path, relative: str, model: type[_Model]) -> tuple[_Model, ...]:
    """@brief 经唯一安全文件边界读取真实 JSONL。"""
    return parse_catalog_lines(read_catalog_file(directory, relative), relative, model)


def _parse_jsonl_object(line: bytes, prefix: str) -> dict:
    """@brief 每行恰好一个标准 JSON 对象，拒绝 BOM 与非 JSON 数值。"""
    try:
        payload = json.loads(line.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError) as error:
        raise RegistrationError("ENVIRONMENT_LINE_INVALID", f"{prefix} {error}") from error
    if not isinstance(payload, dict):
        raise RegistrationError("ENVIRONMENT_LINE_INVALID", prefix)
    return payload


def _reject_json_constant(value: str) -> NoReturn:
    """@brief 拒绝标准 JSON 不允许的 NaN 与无穷值。"""
    reject_value(f"Catalog JSON must not contain the non-JSON number {value}.")


def _optional_file(directory: Path, relative: str, listed: set[str]) -> bytes:
    """@brief 可选文件只在清单登记时读取；没有登记就是不存在，按零行处理。"""
    return read_catalog_file(directory, relative) if relative in listed else b""


def _optional_rows(directory: Path, relative: str, listed: set[str], model: type[_Model]) -> tuple[_Model, ...]:
    """@brief 可选文件的严格行：没有登记时为零行，行失败详情带真实文件名与行号。"""
    return parse_catalog_lines(_optional_file(directory, relative, listed), relative, model)


def _read_callbacks(content: bytes) -> dict:
    """@brief 按状态与规则的唯一判别字段解析，不让其他形状错误掩盖数字错误。"""
    states, rules, rule_started = [], [], False
    objects = parse_catalog_objects(content, "callbacks.jsonl")
    for number, payload in enumerate(objects, start=1):
        try:
            if ("state" in payload) == ("rule_id" in payload):
                raise RegistrationError("ENVIRONMENT_KIND_INVALID")
            if "state" in payload:
                if rule_started:
                    raise RegistrationError("ENVIRONMENT_KIND_INVALID")
                states.append(parse_catalog_model(payload, CallbackState))
            else:
                rule_started = True
                rules.append(_parse_rule(payload))
        except RegistrationError as error:
            raise RegistrationError(error.code, f"callbacks.jsonl:{number} {error.detail or error.message}") from error
    return {"callback_states": tuple(states), "callback_rules": tuple(rules)}


def _parse_rule(payload: dict) -> CallbackRule:
    """@brief 先选真实触发器与提醒形状，再解析规则，避免普通联合模型的错误混淆。"""
    value = dict(payload)
    trigger = value.get("trigger")
    if not isinstance(trigger, dict):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID")
    kind = trigger.get("class")
    model = {"action": ActionTrigger, "state": StateTrigger, "event": EventTrigger}.get(kind)
    if kind == "action" and trigger.get("key") == "travel":
        model = TravelTrigger
    if kind == "time":
        model = MonthlyTrigger if trigger.get("key") == "monthly" else TimeTrigger
    if model is None:
        raise RegistrationError("ENVIRONMENT_KIND_INVALID")
    value["trigger"] = parse_catalog_model(trigger, model)
    if "notify" in value:
        notify = value["notify"]
        if not isinstance(notify, dict) or ("visible" in notify) == ("when" in notify):
            raise RegistrationError("ENVIRONMENT_KIND_INVALID")
        value["notify"] = parse_catalog_model(notify, VisibleHint if "visible" in notify else StateReminder)
    return parse_catalog_model(value, CallbackRule)


def _catalog_view(directory: Path, manifest: CatalogManifest, rows: dict, published: MapGraph, graph: Graph) -> Catalog:
    """@brief 登记后一次建立只读对象、地图与各类绑定索引。"""
    indexed = {name: MappingProxyType({getattr(row, key): row for row in rows[name]}) for name, key in (
        ("apps", "app_id"), ("locations", "location_id"), ("goods", "goods_id"), ("contents", "content_id"),
        ("travel_methods", "travel_method_id"), ("callback_states", "state"), ("callback_rules", "rule_id"))}
    return Catalog(manifest=manifest, source_dir=directory, map_data=published,
                   map_sha256=hashlib.sha256(map_bytes(published)).hexdigest(), graph=graph,
                   **indexed, **_indexes(rows))


def _indexes(rows: dict) -> dict:
    """@brief 按逐字类型匹配与声明 app 身份展开全量世界。"""
    apps, places, goods, contents = (rows[key] for key in ("apps", "locations", "goods", "contents"))
    kinds = sorted({place.kind for place in places})
    result = {
        "goods_by_store": {p.location_id: tuple(g.goods_id for g in goods if p.kind in g.store_kinds) for p in places},
        "goods_by_app": {a.app_id: tuple(g.goods_id for g in goods if a.app_id in g.app_ids) for a in apps},
        "stores_by_app": {a.app_id: tuple(p.location_id for p in places if a.app_id in p.app_ids) for a in apps},
        "apps_by_store": {p.location_id: p.app_ids for p in places},
        "contents_by_app": {a.app_id: tuple(c.content_id for c in contents if a.app_id in c.app_ids) for a in apps},
        "contents_by_location_kind": {
            kind: tuple(c.content_id for c in contents if kind in c.location_kinds) for kind in kinds},
        "apps_by_action": {"purchase": tuple(a.app_id for a in apps if "purchase" in a.actions)},
        "apps_by_travel_method": {
            key: tuple(a.app_id for a in apps if key in a.travel_method_ids)
            for key in (WALKING_TRAVEL_METHOD_ID, *(method.travel_method_id for method in rows["travel_methods"]))},
        "contents_by_series": {
            title: tuple(c.content_id for c in sorted(
                (c for c in contents if c.series is not None and c.series.title == title),
                key=lambda c: c.series.episode))
            for title in sorted({c.series.title for c in contents if c.series is not None})},
    }
    return {key: MappingProxyType(value) for key, value in result.items()}
