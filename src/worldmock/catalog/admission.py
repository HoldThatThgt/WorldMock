"""@brief 环境包、对象容器、营业时段与声明式回调的唯一登记校验。"""

from collections import deque
from datetime import date
from pathlib import Path
import re

from worldmock.catalog.callback_models import StateReminder, VisibleHint
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import REQUIRED_CATALOG_FILES, compute_catalog_identity
from worldmock.catalog.models import CatalogManifest, MapGraph, WEEKDAYS, channel_key
from worldmock.exchange.constants import MAP_MIN_NODES, PUSH_SUMMARY_MAX_CHARS, RULES_VERSION


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
        _check_package_entries(catalog, REQUIRED_CATALOG_FILES | {"manifest.json"})


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
    """@brief 校验清单闭集、实际文件表与目录字节身份。"""
    if manifest.rules_version != RULES_VERSION:
        raise RegistrationError("RULES_VERSION_MISMATCH")
    listed = [entry.path for entry in manifest.files]
    _check_order(listed)
    extra = set(listed) - REQUIRED_CATALOG_FILES
    if extra:
        raise RegistrationError("ENVIRONMENT_STALE_FILE", str(directory / sorted(extra)[0]))
    if set(listed) != REQUIRED_CATALOG_FILES:
        raise RegistrationError("ENVIRONMENT_FILE_UNLISTED")
    _check_package_entries(directory, REQUIRED_CATALOG_FILES | {"manifest.json"})
    identity = compute_catalog_identity(directory, manifest.model_dump(mode="json"))
    if identity.files != manifest.files or identity.catalog_content_sha256 != manifest.catalog_content_sha256:
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH")


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
    """@brief 内容容器、摘要、来源和系列符合独立登记规则；同一地点类型下可点名的单元名唯一。"""
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
        if content.series is not None:
            title, episode = content.series.title, content.series.episode
            binding = (content.app_ids, content.location_kinds)
            if (title in series and series[title] != binding) or (title, episode) in episodes:
                raise RegistrationError("CONTENT_SERIES_INVALID")
            series[title] = binding
            episodes.add((title, episode))
        _check_unit_name(units, content, number)


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
