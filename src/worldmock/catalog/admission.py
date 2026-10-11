"""@brief 环境包、对象容器、营业时段与声明式回调的唯一登记校验。"""

from collections import deque
from datetime import date
from pathlib import Path
import re
from typing import NoReturn

from worldmock.catalog.callback_models import StateReminder, VisibleHint
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.graph import Graph
from worldmock.catalog.identity import (
    OPTIONAL_CATALOG_FILES, REQUIRED_CATALOG_FILES, compute_catalog_identity, present_catalog_files,
)
from worldmock.catalog.models import CatalogManifest, MapGraph, WEEKDAYS, channel_key
from worldmock.exchange.constants import (
    MAP_MIN_NODES, PUSH_SUMMARY_MAX_CHARS, RULES_VERSION, TRAVEL_MAX_SECONDS, WALKING_METERS_PER_SECOND,
    WEATHER_FACTORS,
)
from worldmock.exchange.operators import travel_seconds

#: 目录 JSONL 的文件名闭集；清单自己不进文件表。
CATALOG_FILES = REQUIRED_CATALOG_FILES | OPTIONAL_CATALOG_FILES


def validate_environment_entries(environment_root: Path | str) -> None:
    """@brief 检查两层文件闭集，不进入包外目录或链接。"""
    root = Path(environment_root)
    if root.is_symlink():
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH", str(root))
    _check_package_entries(root, frozenset({"catalog", "persona.toml", "config.toml"}))
    catalog = root / "catalog"
    if catalog.is_symlink():
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH", str(catalog))
    if catalog.is_dir():
        _check_package_entries(catalog, CATALOG_FILES | {"manifest.json"})


def _check_package_entries(directory: Path, allowed: frozenset[str]) -> None:
    """@brief 包外条目失败详情使用真实路径。"""
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH", f"{directory}: {error}") from error
    for entry in entries:
        if entry.name not in allowed:
            raise RegistrationError("ENVIRONMENT_STALE_FILE", str(entry))


def validate_manifest(directory: Path, manifest: CatalogManifest) -> None:
    """@brief 按固定次序校验规则版本、文件表闭集、在场文件的登记与目录字节身份。"""
    if manifest.rules_version != RULES_VERSION:
        raise RegistrationError("RULES_VERSION_MISMATCH")
    listed = [entry.path for entry in manifest.files]
    _check_order(listed)
    extra = set(listed) - CATALOG_FILES
    if extra:
        raise RegistrationError("ENVIRONMENT_STALE_FILE", str(directory / sorted(extra)[0]))
    _require_listed(REQUIRED_CATALOG_FILES, listed)
    _check_package_entries(directory, CATALOG_FILES | {"manifest.json"})
    _require_listed(present_catalog_files(directory), listed)
    identity = compute_catalog_identity(directory, manifest.model_dump(mode="json"))
    if identity.files != manifest.files or identity.catalog_content_sha256 != manifest.catalog_content_sha256:
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH")


def _require_listed(names, listed) -> None:
    """@brief 必需文件和在场的可选文件都要登记，详情给出第一个没有登记的文件名。"""
    unlisted = sorted(set(names) - set(listed))
    if unlisted:
        raise RegistrationError("ENVIRONMENT_FILE_UNLISTED", unlisted[0])


def _check_order(values) -> None:
    """@brief 身份与绑定必须无重复且按身份字符串升序。"""
    if len(values) != len(set(values)):
        raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    if list(values) != sorted(values):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID")


def _check_bindings(values, registered, unknown="ENVIRONMENT_REF_UNKNOWN") -> None:
    """@brief 无重复排序绑定只可引用实际登记对象。"""
    _check_order(values)
    if not set(values).issubset(registered):
        raise RegistrationError(unknown)


def _check_content_apps(values, apps) -> None:
    """@brief 地点、商品、内容与提醒只可引用内容 app。"""
    _check_bindings(values, apps)
    if any(apps[key].role != "content" for key in values):
        raise RegistrationError("APP_ROLE_BINDING_INVALID")


def validate_registration(rows: dict) -> None:
    """@brief 全部文件读取后统一核对引用、名称、容器与回调。"""
    for name, identity, display in (
        ("apps", "app_id", "name"), ("locations", "location_id", "display_name"), ("goods", "goods_id", "name"),
        ("contents", "content_id", None), ("callback_states", "state", None), ("callback_rules", "rule_id", None),
    ):
        entries = rows[name]
        _check_order([getattr(row, identity) for row in entries])
        if display:
            names = [channel_key(getattr(row, display)) for row in entries]
            if len(names) != len(set(names)):
                raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    apps = {app.app_id: app for app in rows["apps"]}
    kinds = {place.kind for place in rows["locations"]}
    _check_locations(rows, apps)
    _check_goods(rows, apps, kinds)
    _check_contents(rows, apps, kinds)
    _check_apps(rows, apps)
    _check_callbacks(rows, apps)
    _check_map_edges(rows["map_edges"], [place.location_id for place in rows["locations"]])


def _check_locations(rows, apps) -> None:
    """@brief 住宿按显式标记，驿站按显式资格与地点内容登记。"""
    if not any(place.is_home for place in rows["locations"]):
        raise RegistrationError("HOME_LOCATION_REQUIRED")
    for place in rows["locations"]:
        _check_content_apps(place.app_ids, apps)
        if place.opens is not None:
            _check_opening_hours(place.opens)
        if place.accepts_parcels and not any(place.kind in c.location_kinds for c in rows["contents"]):
            raise RegistrationError("PARCEL_STATION_REQUIRED", f"{place.location_id} has no location content.")
    if any(goods.app_ids for goods in rows["goods"]) and not any(p.accepts_parcels for p in rows["locations"]):
        raise RegistrationError("PARCEL_STATION_REQUIRED", "App goods require a parcel station.")


def _check_opening_hours(opens) -> None:
    """@brief 每周键为完整星期名，特殊日期使用规范 ISO 日期，时段有序不重叠。"""
    if not set(opens.weekly).issubset(WEEKDAYS):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", "Opening weekdays must use mon through sun.")
    for key in opens.exceptions:
        try:
            canonical = date.fromisoformat(key).isoformat()
        except ValueError as error:
            raise RegistrationError("ENVIRONMENT_KIND_INVALID", "Opening exceptions must use ISO dates.") from error
        if canonical != key:
            raise RegistrationError("ENVIRONMENT_KIND_INVALID", "Opening exceptions must use canonical ISO dates.")
    for intervals in (*opens.weekly.values(), *opens.exceptions.values()):
        previous_end = 0
        for starts, ends in intervals:
            start, end = _time_minutes(starts), _time_minutes(ends, allow_midnight=True)
            if start >= end or start < previous_end:
                raise RegistrationError("ENVIRONMENT_KIND_INVALID", "Opening intervals must be sorted and disjoint.")
            previous_end = end


def _time_minutes(value: str, allow_midnight: bool = False) -> int:
    """@brief 解析规范二十四小时制时刻，24:00 只允许作为时段结束。"""
    if value == "24:00" and allow_midnight:
        return 1440
    if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", "Time must use a valid HH:MM value.")
    return int(value[:2]) * 60 + int(value[3:])


def _check_goods(rows, apps, kinds) -> None:
    """@brief 商品恰绑定一侧，每个地点类型逐字匹配实际地点。"""
    for goods in rows["goods"]:
        if not goods.app_ids and not goods.store_kinds:
            raise RegistrationError("GOODS_BINDING_MISSING")
        if goods.app_ids and goods.store_kinds:
            raise RegistrationError("GOODS_BINDING_SIDE_EXCLUSIVE")
        _check_content_apps(goods.app_ids, apps)
        _check_bindings(goods.store_kinds, kinds, "GOODS_BINDING_DANGLING")


def _check_contents(rows, apps, kinds) -> None:
    """@brief 内容容器、摘要、来源、前提和系列符合独立登记规则；同一地点类型下可点名的单元名唯一。"""
    series, episodes, units = {}, set(), {}
    for number, content in enumerate(rows["contents"], start=1):
        if not content.app_ids and not content.location_kinds:
            raise RegistrationError("CONTENT_CONTAINER_MISSING")
        if content.app_ids and content.location_kinds:
            raise RegistrationError("CONTENT_CONTAINER_SIDE_EXCLUSIVE")
        _check_content_apps(content.app_ids, apps)
        _check_bindings(content.location_kinds, kinds, "CONTENT_BINDING_DANGLING")
        if len(content.summary) > PUSH_SUMMARY_MAX_CHARS:
            raise RegistrationError("CONTENT_SUMMARY_TOO_LONG")
        if content.summary_source.kind == "real" and not content.summary_source.url:
            raise RegistrationError("CONTENT_SOURCE_URL_MISSING")
        _check_prerequisite(content, kinds)
        if content.series is not None:
            _check_series(series, episodes, content)
        _check_unit_name(units, content, number)


def _check_prerequisite(content, kinds) -> None:
    """@brief 内容前提只能写在 app 内容上并至少声明地点类型或时段之一；地点类型有序、无重复并指向登记地点。"""
    prerequisite = content.prerequisite
    if prerequisite is None:
        return
    if content.location_kinds:
        raise RegistrationError("CONTENT_PREREQUISITE_INVALID", f"{content.content_id} is a location content.")
    if not prerequisite.location_kinds and prerequisite.opens is None:
        raise RegistrationError("CONTENT_PREREQUISITE_INVALID",
                                f"{content.content_id} declares neither location kinds nor opening hours.")
    _check_order(prerequisite.location_kinds)
    for kind in prerequisite.location_kinds:
        if kind not in kinds:
            raise RegistrationError("CONTENT_BINDING_DANGLING", f"{content.content_id} prerequisite names {kind}.")
    if prerequisite.opens is not None:
        _check_opening_hours(prerequisite.opens)


def _check_series(series: dict, episodes: set, content) -> None:
    """@brief 同一系列各集的容器绑定与内容前提必须完全相同，集号不得重复。

    :param series: 系列名到先登记那一集的（容器绑定，内容前提）。
    :param episodes: 已登记的（系列名，集号）。
    """
    title, episode = content.series.title, content.series.episode
    binding = (content.app_ids, content.location_kinds)
    first = series.setdefault(title, (binding, content.prerequisite))
    if first[0] != binding or (title, episode) in episodes:
        raise RegistrationError("CONTENT_SERIES_INVALID")
    if first[1] != content.prerequisite:
        raise RegistrationError("CONTENT_SERIES_INVALID", f"{title} episodes declare different prerequisites.")
    episodes.add((title, episode))


def _check_unit_name(units: dict, content, number: int) -> None:
    """@brief 一行内容在它的每个地点类型下登记单元名；同名而所属单元不同时拒绝并指出两行行号。

    单元名是独立内容的标题或系列名，两者合在一起按去掉全部空白、不分大小写比较；同一系列的各集属于同一单元。
    """
    name = content.title if content.series is None else content.series.title
    unit = ("content", content.content_id) if content.series is None else ("series", name)
    for kind in sorted(content.location_kinds):
        owner, first_number, first_name = units.setdefault((kind, channel_key(name)), (unit, number, name))
        if owner != unit:
            raise RegistrationError(
                "ENVIRONMENT_ID_DUPLICATE",
                f"content-catalog.jsonl:{first_number} and content-catalog.jsonl:{number} "
                f"give location kind {kind} the same activity name {first_name}.")


def _check_apps(rows, apps) -> None:
    """@brief 角色与动作互斥，纯目录 app 使用数据集浏览时长。"""
    names = {channel_key(p.display_name) for p in rows["locations"]}
    function_actions = {"walk", "drive", "take_taxi", "take_bus", "purchase"}
    for app in apps.values():
        if channel_key(app.name) in names:
            raise RegistrationError("APP_NAME_COLLIDES_LOCATION")
        if app.role == "function":
            if not app.actions:
                raise RegistrationError("APP_UNBOUND")
            _check_bindings(app.actions, function_actions, "ENVIRONMENT_KIND_INVALID")
            if "browse_duration_seconds" in app.model_fields_set:
                raise RegistrationError("ENVIRONMENT_KIND_INVALID")
            continue
        if "actions" in app.model_fields_set:
            raise RegistrationError("APP_ROLE_BINDING_INVALID")
        explicit = any(app.app_id in c.app_ids for c in rows["contents"])
        directory = any(app.app_id in g.app_ids for g in rows["goods"]) or any(
            app.app_id in p.app_ids and any(p.kind in g.store_kinds for g in rows["goods"]) for p in rows["locations"])
        if not explicit and not directory:
            raise RegistrationError("APP_UNBOUND")
        if not explicit and app.browse_duration_seconds is None:
            raise RegistrationError("ENVIRONMENT_NUMBER_INVALID")
        if explicit and "browse_duration_seconds" in app.model_fields_set:
            raise RegistrationError("ENVIRONMENT_KIND_INVALID")


def _check_callbacks(rows, apps) -> None:
    """@brief 状态、商品、触发阈值、足迹池与提醒效果严格对应。"""
    states = {state.state: state for state in rows["callback_states"]}
    goods = {item.goods_id for item in rows["goods"]}
    for state in states.values():
        if not 0 <= state.initial <= state.cap:
            raise RegistrationError("ENVIRONMENT_NUMBER_INVALID")
        _check_bindings(state.reset_on_goods, goods)
    for rule in rows["callback_rules"]:
        trigger, effect = rule.trigger, rule.effect
        if trigger.trigger_class == "state":
            _check_state_threshold(trigger.key, trigger.below, states)
        if trigger.trigger_class == "time" and trigger.key == "monthly":
            _time_minutes(trigger.time)
        if effect.type == "state_delta":
            if effect.state not in states:
                raise RegistrationError("CALLBACK_STATE_UNREGISTERED")
            if effect.delta == 0:
                raise RegistrationError("ENVIRONMENT_NUMBER_INVALID")
        if effect.type == "footprint_chain":
            if not effect.steps or any(not step.app_pool for step in effect.steps):
                raise RegistrationError("CALLBACK_FOOTPRINT_EMPTY")
            for step in effect.steps:
                _check_bindings(step.app_pool, apps)
        _check_notify(rule, states, apps)


def _check_state_threshold(name, below, states) -> None:
    """@brief 阈值必须引用登记状态并落在一至上限。"""
    if name not in states:
        raise RegistrationError("CALLBACK_STATE_UNREGISTERED")
    if not 1 <= below <= states[name].cap:
        raise RegistrationError("ENVIRONMENT_NUMBER_INVALID")


def _check_notify(rule, states, apps) -> None:
    """@brief 可见提示只配足迹链，状态提醒只配状态增量。"""
    if isinstance(rule.notify, VisibleHint) and rule.effect.type != "footprint_chain":
        raise RegistrationError("CALLBACK_NOTIFY_MISMATCH")
    if isinstance(rule.notify, StateReminder):
        if rule.effect.type != "state_delta":
            raise RegistrationError("CALLBACK_NOTIFY_MISMATCH")
        _check_state_threshold(rule.notify.when.state, rule.notify.when.below, states)
        _check_content_apps((rule.notify.app_id,), apps)


def _check_map_edges(edges, location_ids) -> None:
    """@brief 声明边依次核对端点次序、端点登记、地点对唯一、行次序和互不矛盾，详情带行号。"""
    registered, lines = set(location_ids), {}
    pairs = [(edge.a, edge.b) for edge in edges]
    for number, (a, b) in enumerate(pairs, start=1):
        if a >= b:
            _reject_map_row("ENVIRONMENT_KIND_INVALID", number, "Map endpoints must satisfy a < b.")
    for number, pair in enumerate(pairs, start=1):
        for endpoint in pair:
            if endpoint not in registered:
                _reject_map_row("ENVIRONMENT_REF_UNKNOWN", number, f"Unknown location_id: {endpoint}.")
    for number, (a, b) in enumerate(pairs, start=1):
        first = lines.setdefault((a, b), number)
        if first != number:
            _reject_map_row("ENVIRONMENT_ID_DUPLICATE", first, f"and map.jsonl:{number} declare the same pair {a}-{b}.")
    for number in range(2, len(pairs) + 1):
        if pairs[number - 2] > pairs[number - 1]:
            _reject_map_row("ENVIRONMENT_KIND_INVALID", number, "Map rows must be sorted by (a, b).")
    _check_declared_distances(edges, location_ids)


def _reject_map_row(code: str, number: int, detail: str) -> NoReturn:
    """@brief 声明边的登记失败详情一律以文件名和行号开头。"""
    raise RegistrationError(code, f"map.jsonl:{number} {detail}")


def _check_declared_distances(edges, location_ids) -> None:
    """@brief 只含声明边的图上，任何一条声明边都不得被经其他声明边的更短路线绕过。"""
    declared = Graph(MapGraph(nodes=tuple(sorted(location_ids)), edges=tuple(edges)))
    for number, edge in enumerate(edges, start=1):
        meters = declared.distance_meters(edge.a, edge.b)
        if meters < edge.length_m:
            route = " -> ".join(declared.path(edge.a, edge.b))
            _reject_map_row("MAP_DISTANCE_CONTRADICTED", number,
                            f"{edge.a}-{edge.b} declares {edge.length_m} m but {route} is {meters} m.")


def validate_route_limit(nodes: tuple[str, ...], graph: Graph) -> None:
    """@brief 生成后的地图上最远的一对地点，步行在最差天气下必须一天之内走到。"""
    farthest = None
    for origin in nodes:
        for destination, (meters, _) in graph.shortest_paths(origin).items():
            if origin < destination and (farthest is None or meters > farthest[0]):
                farthest = (meters, origin, destination)
    meters, origin, destination = farthest
    seconds = travel_seconds("walking", meters, min(WEATHER_FACTORS.values()))
    if seconds > TRAVEL_MAX_SECONDS:
        raise RegistrationError(
            "MAP_ROUTE_TOO_LONG",
            f"{origin} -> {destination} is {meters} m, which takes {seconds} s at {WALKING_METERS_PER_SECOND} m/s "
            f"in the worst weather; the limit is {TRAVEL_MAX_SECONDS} s.")


def validate_map(graph: MapGraph, location_ids: set[str]) -> None:
    """@brief 内存图节点完整、规范正边有序且连通。"""
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
