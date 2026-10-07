"""@brief 环境目录与对象绑定的唯一登记校验器。"""

from collections import deque
from pathlib import Path

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import REQUIRED_CATALOG_FILES, compute_catalog_identity
from worldmock.catalog.models import CatalogData, CatalogManifest, MapGraph, channel_key
from worldmock.exchange.constants import MAP_MIN_NODES, PUSH_SUMMARY_MAX_CHARS, RULES_VERSION
from worldmock.exchange.types import FOOD_STORE_KINDS, LocationKind, SummarySourceKind


def validate_manifest(directory: Path, manifest: CatalogManifest) -> None:
    """@brief 校验清单闭集、规则版本、所有实际文件以及完整身份。"""
    if manifest.rules_version != RULES_VERSION:
        raise RegistrationError("RULES_VERSION_MISMATCH")
    listed = [entry.path for entry in manifest.files]
    if len(listed) != len(set(listed)):
        raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    if not REQUIRED_CATALOG_FILES.issubset(listed):
        raise RegistrationError("ENVIRONMENT_FILE_UNLISTED")
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*")
              if path.is_file() or path.is_symlink()}
    if set(listed) != REQUIRED_CATALOG_FILES or actual - set(listed) - {"manifest.json"}:
        raise RegistrationError("ENVIRONMENT_STALE_FILE")
    if listed != sorted(listed):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID")
    identity = compute_catalog_identity(directory, manifest.model_dump(mode="json"))
    if identity.files != manifest.files or identity.catalog_content_sha256 != manifest.catalog_content_sha256:
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH")


def validate_registration(data: CatalogData) -> None:
    """@brief 建立索引前校验身份、绑定、摘要、主题和发布图。"""
    groups = ((data.apps, "app_id", "name"), (data.locations, "location_id", "display_name"),
              (data.foods, "food_id", "name"), (data.contents, "content_id", None),
              (data.work_topics, "topic_id", "topic"))
    for entries, identifier, name in groups:
        _check_identities(entries, identifier, name)
    app_ids = {app.app_id for app in data.apps}
    _check_locations(data, app_ids)
    _check_foods(data)
    _check_contents(data, app_ids)
    _check_apps(data)
    for topic in data.work_topics:
        if topic.target_seconds < 60:
            raise RegistrationError("ENVIRONMENT_NUMBER_INVALID")
    validate_map(data.map_data, {place.location_id for place in data.locations})


def _check_identities(entries: tuple, identifier: str, name: str | None) -> None:
    """@brief 同文件身份和要求唯一的可读名不得重复，条目按身份排序。"""
    ids = [getattr(entry, identifier) for entry in entries]
    names = [channel_key(getattr(entry, name)) for entry in entries] if name else []
    if len(ids) != len(set(ids)) or len(names) != len(set(names)):
        raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    if ids != sorted(ids):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID")


def _check_bindings(values: tuple[str, ...], registered: set[str]) -> None:
    """@brief 绑定为已登记身份的无重复排序列表。"""
    if not set(values).issubset(registered):
        raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
    if len(values) != len(set(values)):
        raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    if values != tuple(sorted(values)):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID")


def _check_locations(data: CatalogData, app_ids: set[str]) -> None:
    """@brief 只有食品店铺可以绑定已登记的应用。"""
    for place in data.locations:
        _check_bindings(place.app_ids, app_ids)
        if place.app_ids and place.kind not in FOOD_STORE_KINDS:
            raise RegistrationError("ENVIRONMENT_KIND_INVALID")


def _check_foods(data: CatalogData) -> None:
    """@brief 食品只绑定食品店铺，而且同种店铺全部绑定或全部不绑定。"""
    locations = {place.location_id: place for place in data.locations}
    by_kind = {kind: {place.location_id for place in data.locations if place.kind == kind}
               for kind in FOOD_STORE_KINDS}
    for food in data.foods:
        if food.store_ids == "all":
            continue
        if not food.store_ids:
            raise RegistrationError("FOOD_BINDING_MISSING")
        _check_bindings(food.store_ids, set(locations))
        if any(locations[store].kind not in FOOD_STORE_KINDS for store in food.store_ids):
            raise RegistrationError("FOOD_STORE_NOT_FOOD_STORE")
        bound = set(food.store_ids)
        if any(bound & stores and not stores.issubset(bound) for stores in by_kind.values()):
            raise RegistrationError("FOOD_BINDING_KIND_INCONSISTENT")


def _check_contents(data: CatalogData, app_ids: set[str]) -> None:
    """@brief 内容必须绑定应用，摘要长度与真实来源符合固定要求。"""
    for content in data.contents:
        if not content.app_ids:
            raise RegistrationError("CONTENT_APP_MISSING")
        _check_bindings(content.app_ids, app_ids)
        if len(content.summary) > PUSH_SUMMARY_MAX_CHARS:
            raise RegistrationError("CONTENT_SUMMARY_TOO_LONG")
        if content.summary_source.kind == SummarySourceKind.REAL and not content.summary_source.url:
            raise RegistrationError("CONTENT_SOURCE_URL_MISSING")


def _check_apps(data: CatalogData) -> None:
    """@brief 每个应用都有店铺或内容绑定，名称不得与地点显示名碰撞。"""
    bound = {app for place in data.locations for app in place.app_ids}
    bound.update(app for content in data.contents for app in content.app_ids)
    place_names = {channel_key(place.display_name) for place in data.locations}
    for app in data.apps:
        if app.app_id not in bound:
            raise RegistrationError("APP_UNBOUND")
        if channel_key(app.name) in place_names:
            raise RegistrationError("APP_NAME_COLLIDES_LOCATION")


def validate_map(graph: MapGraph, location_ids: set[str]) -> None:
    """@brief 校验规范图的最小规模、完整节点、边和连通性，不重生成。"""
    if len(graph.nodes) < MAP_MIN_NODES:
        raise RegistrationError("MAP_TOO_SMALL")
    pairs = [(edge.a, edge.b) for edge in graph.edges]
    if (tuple(sorted(location_ids)) != graph.nodes or pairs != sorted(set(pairs))
            or any(edge.a >= edge.b or edge.a not in location_ids or edge.b not in location_ids
                   for edge in graph.edges)):
        raise RegistrationError("MAP_EDGE_INVALID")
    adjacency = {node: set() for node in graph.nodes}
    for edge in graph.edges:
        adjacency[edge.a].add(edge.b)
        adjacency[edge.b].add(edge.a)
    reached, queue = {graph.nodes[0]}, deque([graph.nodes[0]])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency[node] - reached:
            reached.add(neighbor)
            queue.append(neighbor)
    if reached != location_ids:
        raise RegistrationError("MAP_DISCONNECTED")


def validate_scenario_against_catalog(document, catalog) -> None:
    """@brief 统一核对场景环境钉、已选对象、商家账户和人物工作主题。"""
    pin = document.environment
    manifest = catalog.manifest
    if (pin.environment_id, pin.environment_version) != (manifest.environment_id, manifest.environment_version):
        raise RegistrationError("ENVIRONMENT_PIN_MISMATCH")
    selected = {reference.location_id for reference in document.locations}
    if not selected.issubset(catalog.locations):
        raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
    roles = {account.role for account in document.accounts}
    for location_id in selected:
        if catalog.locations[location_id].kind in FOOD_STORE_KINDS and f"merchant:{location_id}" not in roles:
            raise RegistrationError("MERCHANT_ACCOUNT_MISSING")
    for agent in document.agents:
        if not set(agent.work_topic_ids).issubset(catalog.work_topics):
            raise RegistrationError("WORK_TOPIC_UNKNOWN")
        if agent.work_topic_ids and agent.work_schedule is None:
            raise RegistrationError("WORK_SCHEDULE_REQUIRED")
        if agent.home_location_id not in selected:
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
        for location_id, inventory in agent.initial_inventory.items():
            if location_id not in selected or not set(inventory).issubset(catalog.foods):
                raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
