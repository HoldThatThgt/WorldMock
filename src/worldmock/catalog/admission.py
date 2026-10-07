"""@brief 环境目录与对象绑定的唯一登记校验器。"""

from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import REQUIRED_CATALOG_FILES, compute_catalog_identity
from worldmock.catalog.models import CatalogData, CatalogManifest, MapGraph, channel_key
from worldmock.exchange.constants import MAP_MIN_NODES, PUSH_SUMMARY_MAX_CHARS, RULES_VERSION
from worldmock.exchange.types import (
    ActionCategory, AppPurpose, GOODS_CATEGORY_STORE_KINDS, STORE_KINDS, SummarySourceKind,
)
from worldmock.life.work_schedule import shift_on, shift_span


def validate_manifest(directory: Path, manifest: CatalogManifest) -> None:
    """@brief 校验清单闭集、规则版本、所有实际文件以及完整身份。"""
    if manifest.rules_version != RULES_VERSION:
        raise RegistrationError("RULES_VERSION_MISMATCH")
    listed = [entry.path for entry in manifest.files]
    if len(listed) != len(set(listed)):
        raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    if set(listed) - REQUIRED_CATALOG_FILES:
        raise RegistrationError("ENVIRONMENT_STALE_FILE")
    if not REQUIRED_CATALOG_FILES.issubset(listed):
        raise RegistrationError("ENVIRONMENT_FILE_UNLISTED")
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*")}
    if actual - set(listed) - {"manifest.json"}:
        raise RegistrationError("ENVIRONMENT_STALE_FILE")
    if listed != sorted(listed):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID")
    identity = compute_catalog_identity(directory, manifest.model_dump(mode="json"))
    if identity.files != manifest.files or identity.catalog_content_sha256 != manifest.catalog_content_sha256:
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH")


def validate_registration(data: CatalogData) -> None:
    """@brief 建立索引前校验身份、绑定、摘要、系列和发布图。"""
    groups = ((data.apps, "app_id", "name"), (data.locations, "location_id", "display_name"),
              (data.goods, "goods_id", "name"), (data.actions, "action_id", None),
              (data.contents, "content_id", None))
    for entries, identifier, name in groups:
        _check_identities(entries, identifier, name)
    app_ids = {app.app_id for app in data.apps}
    _check_locations(data, app_ids)
    _check_goods(data)
    _check_actions(data, app_ids)
    _check_contents(data, app_ids)
    _check_action_contents(data)
    _check_apps(data)
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
    """@brief 只有店铺可以绑定已登记的购物应用。"""
    for place in data.locations:
        _check_bindings(place.app_ids, app_ids)
        if place.app_ids and place.kind not in STORE_KINDS:
            raise RegistrationError("ENVIRONMENT_KIND_INVALID")


def _check_goods(data: CatalogData) -> None:
    """@brief 商品只绑定可供给该类别的店铺，同种店铺全部绑定或全部不绑定。"""
    locations = {place.location_id: place for place in data.locations}
    by_kind = {kind: {place.location_id for place in data.locations if place.kind == kind} for kind in STORE_KINDS}
    for goods in data.goods:
        if goods.store_ids == "all":
            continue
        if not goods.store_ids:
            raise RegistrationError("GOODS_BINDING_MISSING")
        _check_bindings(goods.store_ids, set(locations))
        if any(locations[store].kind not in GOODS_CATEGORY_STORE_KINDS[goods.category] for store in goods.store_ids):
            raise RegistrationError("GOODS_STORE_KIND_INVALID")
        bound = set(goods.store_ids)
        if any(bound & stores and not stores.issubset(bound) for stores in by_kind.values()):
            raise RegistrationError("GOODS_BINDING_KIND_INCONSISTENT")


def _check_actions(data: CatalogData, app_ids: set[str]) -> None:
    """@brief 动作名称全局唯一，应用绑定与使用方式必须同有同无。"""
    # 方式词是人物公开协议的一部分，直接读取其唯一声明。
    from worldmock.life.vocabulary import WAYS

    names = {channel_key(name) for name in WAYS}
    names.update(channel_key(app.name) for app in data.apps)
    names.update(channel_key(place.display_name) for place in data.locations)
    names.update(channel_key(goods.name) for goods in data.goods)
    for action in data.actions:
        name = channel_key(action.name)
        if name in names:
            raise RegistrationError("ACTION_NAME_COLLIDES")
        names.add(name)
        _check_bindings(action.app_ids, app_ids)
        if bool(action.app_ids) != (action.manner is not None):
            raise RegistrationError("ACTION_APP_MANNER_INVALID")


def _check_contents(data: CatalogData, app_ids: set[str]) -> None:
    """@brief 内容绑定动作及其应用子集，形式、摘要与来源符合固定要求。"""
    actions = {action.action_id: action for action in data.actions}
    for content in data.contents:
        if content.action_id not in actions:
            raise RegistrationError("CONTENT_ACTION_MISSING")
        action = actions[content.action_id]
        _check_bindings(content.app_ids, app_ids)
        if (bool(action.app_ids) != bool(content.app_ids) or not set(content.app_ids).issubset(action.app_ids)):
            raise RegistrationError("CONTENT_APP_BINDING_INVALID")
        if len(content.summary) > PUSH_SUMMARY_MAX_CHARS:
            raise RegistrationError("CONTENT_SUMMARY_TOO_LONG")
        if content.summary_source.kind == SummarySourceKind.REAL and not content.summary_source.url:
            raise RegistrationError("CONTENT_SOURCE_URL_MISSING")
    _check_series(data)


def _check_series(data: CatalogData) -> None:
    """@brief 同一标题系列的动作与应用集合一致，集数唯一。"""
    bindings, episodes = {}, set()
    for content in data.contents:
        if content.series is None:
            continue
        title = content.series.title
        binding = (content.action_id, content.app_ids)
        episode = (title, content.series.episode)
        if (title in bindings and bindings[title] != binding) or episode in episodes:
            raise RegistrationError("CONTENT_SERIES_INVALID")
        bindings[title] = binding
        episodes.add(episode)


def _check_action_contents(data: CatalogData) -> None:
    """@brief 每个动作及其每个应用组合至少有一条内容。"""
    for action in data.actions:
        contents = tuple(content for content in data.contents if content.action_id == action.action_id)
        app_empty = any(not any(app_id in content.app_ids for content in contents) for app_id in action.app_ids)
        if not contents or app_empty:
            raise RegistrationError("ACTION_CONTENT_EMPTY")


def _check_apps(data: CatalogData) -> None:
    """@brief 应用用途与店铺、支付绑定一致，没有用途时必须有动作。"""
    apps = {app.app_id: app for app in data.apps}
    shopping = {app_id for place in data.locations for app_id in place.app_ids}
    bound = {app_id for action in data.actions for app_id in action.app_ids}
    place_names = {channel_key(place.display_name) for place in data.locations}
    for app in data.apps:
        _check_bindings(app.purposes, set(AppPurpose))
        _check_bindings(app.payment_app_ids, set(apps))
        is_shopping = AppPurpose.SHOPPING in app.purposes
        if (is_shopping != (app.app_id in shopping) or is_shopping != bool(app.payment_app_ids)
                or any(AppPurpose.PAYMENT not in apps[app_id].purposes for app_id in app.payment_app_ids)):
            raise RegistrationError("APP_PURPOSE_INVALID")
        if not app.purposes and app.app_id not in bound:
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
    """@brief 统一核对场景环境钉、已选对象、商家账户和人物分配内容。"""
    pin = document.environment
    manifest = catalog.manifest
    if (pin.environment_id, pin.environment_version) != (manifest.environment_id, manifest.environment_version):
        raise RegistrationError("ENVIRONMENT_PIN_MISMATCH")
    selected = {reference.location_id for reference in document.locations}
    if not selected.issubset(catalog.locations):
        raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
    roles = {account.role for account in document.accounts}
    for location_id in selected:
        if catalog.locations[location_id].kind in STORE_KINDS and f"merchant:{location_id}" not in roles:
            raise RegistrationError("MERCHANT_ACCOUNT_MISSING")
    for agent in document.agents:
        if agent.home_location_id not in selected:
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
        _check_work_content(document, catalog, agent)


def _check_work_content(document, catalog, agent) -> None:
    """@brief 分配内容必须属于工作学习动作，且覆盖排班全长。"""
    ids = agent.work_content_ids
    if any(content_id not in catalog.contents
           or catalog.actions[catalog.contents[content_id].action_id].category != ActionCategory.WORK_STUDY
           for content_id in ids):
        raise RegistrationError("WORK_CONTENT_UNKNOWN")
    if ids and agent.work_schedule is None:
        raise RegistrationError("WORK_SCHEDULE_REQUIRED")
    if agent.work_schedule is not None and not ids:
        raise RegistrationError("WORK_CONTENT_REQUIRED")
    if agent.work_schedule is not None:
        available = sum(catalog.contents[content_id].duration_seconds for content_id in ids)
        if available < _scheduled_seconds(document, agent.work_schedule):
            raise RegistrationError("WORK_CONTENT_INSUFFICIENT")


def _scheduled_seconds(document, schedule) -> int:
    """@brief 与运行窗口相交的本地班次按完整起止时长计数，休息不扣除。"""
    zone = ZoneInfo(document.timezone)
    start, end = int(document.start_at.timestamp()), int(document.end_at.timestamp())
    day = datetime.fromtimestamp(start, zone).date()
    last_day = datetime.fromtimestamp(end - 1, zone).date()
    total = 0
    while day <= last_day:
        shift = shift_on(schedule, day)
        if shift is not None:
            begins, finishes = shift_span(shift, day, zone)
            if begins < end and finishes > start:
                total += finishes - begins
        day += timedelta(days=1)
    return total
