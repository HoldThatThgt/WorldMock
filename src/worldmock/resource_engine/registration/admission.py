"""@brief 环境包、对象容器、营业时段与声明式回调的唯一登记校验。"""

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from datetime import date
import logging
from pathlib import Path
import re
from typing import NoReturn

from worldmock.resource_engine.catalog.callback_models import StateReminder, VisibleHint
from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.map.graph import Graph
from worldmock.resource_engine.catalog.identity import (
    OPTIONAL_CATALOG_FILES,
    REQUIRED_CATALOG_FILES,
    compute_catalog_identity,
    present_catalog_files,
)
from worldmock.resource_engine.catalog.models import (
    CatalogManifest,
    Location,
    MapGraph,
    TravelMethod,
    WEEKDAYS,
    channel_key,
)
from worldmock.exchange_engine.definitions.constants import (
    MAP_MIN_NODES,
    PUSH_SUMMARY_MAX_CHARS,
    RULES_VERSION,
    TRAVEL_MAX_SECONDS,
    WALKING_METERS_PER_SECOND,
    WALKING_TRAVEL_METHOD_ID,
    WALKING_TRAVEL_METHOD_NAME,
    WEATHER_FACTORS,
)
from worldmock.exchange_engine.commerce.operators import travel_seconds

_LOGGER = logging.getLogger(__name__)
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
                _LOGGER.error("Duplicate display name in catalog section %s", name)
                raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    apps = {app.app_id: app for app in rows["apps"]}
    kinds = {place.kind for place in rows["locations"]}
    cities = frozenset(place.city_id for place in rows["locations"])
    states = frozenset(row.state for row in rows["callback_states"])
    methods = _check_travel_methods(rows["travel_methods"])
    _check_locations(rows, apps, states)
    _check_goods(rows, apps, kinds)
    _check_store_ratings(rows)
    _check_contents(rows, apps, kinds, cities, states)
    _check_apps(rows, methods)
    _check_callbacks(rows, apps, methods)
    _check_map_edges(rows["map_edges"], rows["locations"], methods, states)


def _check_locations(rows, apps, states: frozenset[str]) -> None:
    """@brief 住宿按显式标记，商业住宿凭状态限非自有可睡地点，驿站按非即时线上商品条件强制要求。"""
    if not any(place.is_home for place in rows["locations"]):
        _LOGGER.error("Catalog declares no home location")
        raise RegistrationError("HOME_LOCATION_REQUIRED")
    for number, place in enumerate(rows["locations"], start=1):
        _check_content_apps(place.app_ids, apps)
        if place.sleep_consumes_state is not None:
            if not place.sleepable or place.is_home:
                _LOGGER.error("Location %s declares sleep_consumes_state on invalid location", place.location_id)
                raise RegistrationError(
                    "ENVIRONMENT_KIND_INVALID",
                    f"location.jsonl:{number} sleep_consumes_state requires sleepable=true and is_home=false.")
            if place.sleep_consumes_state not in states:
                _LOGGER.error("Location %s references unknown state %s", place.location_id, place.sleep_consumes_state)
                raise RegistrationError(
                    "ENVIRONMENT_REF_UNKNOWN",
                    f"location.jsonl:{number} Unknown callback state: {place.sleep_consumes_state}.")
        if place.opens is not None:
            _check_opening_hours(place.opens)
        if place.accepts_parcels and not any(place.kind in c.location_kinds for c in rows["contents"]):
            _LOGGER.error("Parcel station %s has no location content", place.location_id)
            raise RegistrationError("PARCEL_STATION_REQUIRED", f"{place.location_id} has no location content.")
    if any(goods.app_ids and not goods.instant for goods in rows["goods"]) and not any(
            p.accepts_parcels for p in rows["locations"]):
        _LOGGER.error("Non-instant app goods require at least one parcel station")
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
    """@brief 商品恰绑定一侧，店铺商品不得声明 instant，每个地点类型逐字匹配实际地点。"""
    for number, goods in enumerate(rows["goods"], start=1):
        if not goods.app_ids and not goods.store_kinds:
            _LOGGER.error("Goods %s has neither app_ids nor store_kinds", goods.goods_id)
            raise RegistrationError("GOODS_BINDING_MISSING")
        if goods.app_ids and goods.store_kinds:
            _LOGGER.error("Goods %s binds both app_ids and store_kinds", goods.goods_id)
            raise RegistrationError("GOODS_BINDING_SIDE_EXCLUSIVE")
        if goods.store_kinds and ("instant" in goods.model_fields_set or goods.instant):
            _LOGGER.error("Store goods %s declares instant", goods.goods_id)
            raise RegistrationError("ENVIRONMENT_KIND_INVALID",
                                    f"merchant-catalog.jsonl:{number} Store goods may not declare instant.")
        _check_content_apps(goods.app_ids, apps)
        _check_bindings(goods.store_kinds, kinds, "GOODS_BINDING_DANGLING")


def _check_store_ratings(rows: dict) -> None:
    """@brief 登记完商品绑定后，逐个地点检查有售商品的分店均有自己的评分。"""
    selling_kinds = {kind for goods in rows["goods"] for kind in goods.store_kinds}
    for number, place in enumerate(rows["locations"], start=1):
        if place.kind in selling_kinds and place.rating is None:
            raise RegistrationError("ENVIRONMENT_NUMBER_INVALID",
                                    f"location.jsonl:{number} A selling location requires a rating.")
        if place.kind not in selling_kinds and place.rating is not None:
            raise RegistrationError("ENVIRONMENT_KIND_INVALID",
                                    f"location.jsonl:{number} Only selling locations can declare a rating.")


def _check_contents(rows, apps, kinds, cities: frozenset[str], states: frozenset[str]) -> None:
    """@brief 内容容器、摘要、来源、前提、凭的状态与完成效果、系列符合独立登记规则；同一地点类型下可点名的单元名唯一。

    :param cities: 登记的全部城市身份集合。
    :param states: 登记的全部状态名；没有 callbacks.jsonl 时为空集。
    """
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
        _check_prerequisite(content, kinds, cities)
        _check_location_declarations(content, states)
        if content.series is not None:
            _check_series(series, episodes, content)
        _check_unit_name(units, content, number)


def _check_prerequisite(content, kinds, cities: frozenset[str]) -> None:
    """@brief 内容前提只能写在 app 内容上并至少声明城市、地点类型或时段之一；城市与地点类型有序去重并指向登记地点。"""
    prerequisite = content.prerequisite
    if prerequisite is None:
        return
    if content.location_kinds:
        _LOGGER.error("Location content %s declares prerequisite", content.content_id)
        raise RegistrationError("CONTENT_PREREQUISITE_INVALID", f"{content.content_id} is a location content.")
    if not prerequisite.city_ids and not prerequisite.location_kinds and prerequisite.opens is None:
        _LOGGER.error("Content %s declares empty prerequisite", content.content_id)
        raise RegistrationError(
            "CONTENT_PREREQUISITE_INVALID",
            f"{content.content_id} declares neither city_ids, location_kinds, nor opening hours.")
    _check_order(prerequisite.city_ids)
    for city_id in prerequisite.city_ids:
        if city_id not in cities:
            _LOGGER.error("Content %s prerequisite names unknown city_id %s", content.content_id, city_id)
            raise RegistrationError("CONTENT_BINDING_DANGLING", f"{content.content_id} prerequisite names {city_id}.")
    _check_order(prerequisite.location_kinds)
    for kind in prerequisite.location_kinds:
        if kind not in kinds:
            _LOGGER.error("Content %s prerequisite names unknown kind %s", content.content_id, kind)
            raise RegistrationError("CONTENT_BINDING_DANGLING", f"{content.content_id} prerequisite names {kind}.")
    if prerequisite.opens is not None:
        _check_opening_hours(prerequisite.opens)


def _check_location_declarations(content, states: frozenset[str]) -> None:
    """@brief 凭的状态与完成效果只属于地点内容；完成效果至少声明一个成员；凭的状态必须是登记的状态名。"""
    if content.app_ids and (content.consumes_state is not None or content.completion_effects is not None):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", f"{content.content_id} is an app content; "
                                "consumes_state and completion_effects belong to location contents.")
    # 成员写成 null 已被行模型拒绝，所以没有显式写出的成员就是三个成员都省略。
    if content.completion_effects is not None and not content.completion_effects.model_fields_set:
        raise RegistrationError("ENVIRONMENT_KIND_INVALID",
                                f"{content.content_id} declares an empty completion_effects.")
    if content.consumes_state is not None and content.consumes_state not in states:
        raise RegistrationError("ENVIRONMENT_REF_UNKNOWN",
                                f"{content.content_id} consumes_state names {content.consumes_state}.")


def _check_series(series: dict, episodes: set, content) -> None:
    """@brief 同系列各集容器绑定、凭的状态、记忆类别与 repeatable 须一致且集号唯一；app 系列允许分集独立前提。

    :param series: 系列名到首集（app_ids，location_kinds，凭的状态，记忆类别，repeatable）。
    :param episodes: 已登记的（系列名，集号）。
    """
    title, episode = content.series.title, content.series.episode
    first = series.setdefault(
        title, (content.app_ids, content.location_kinds, content.consumes_state,
                content.memory_category, content.repeatable))
    if (first[0], first[1]) != (content.app_ids, content.location_kinds) or (title, episode) in episodes:
        _LOGGER.error("Series %s has conflicting binding or duplicate episode %s", title, episode)
        raise RegistrationError("CONTENT_SERIES_INVALID")
    if first[2] != content.consumes_state:
        _LOGGER.error("Series %s episodes declare different consumed states", title)
        raise RegistrationError("CONTENT_SERIES_INVALID", f"{title} episodes declare different consumed states.")
    if first[3] != content.memory_category:
        _LOGGER.error("Series %s episodes declare different memory categories", title)
        raise RegistrationError("CONTENT_SERIES_INVALID", f"{title} episodes declare different memory categories.")
    if first[4] != content.repeatable:
        _LOGGER.error("Series %s episodes declare different repeatable flags", title)
        raise RegistrationError("CONTENT_SERIES_INVALID", f"{title} episodes declare different repeatable flags.")
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


def _check_travel_methods(methods) -> frozenset[str]:
    """@brief 出行方式依次核对保留身份、身份重复、行次序、保留名称和名称重复，返回可引用的全部身份（含步行）。"""
    identities, names = {}, {}
    for number, method in enumerate(methods, start=1):
        if method.travel_method_id == WALKING_TRAVEL_METHOD_ID:
            _reject_travel_method("ENVIRONMENT_ID_DUPLICATE", number,
                                  f"uses the reserved identity {WALKING_TRAVEL_METHOD_ID}.")
    for number, method in enumerate(methods, start=1):
        first = identities.setdefault(method.travel_method_id, number)
        if first != number:
            _reject_travel_method("ENVIRONMENT_ID_DUPLICATE", first, f"and travel-method.jsonl:{number} "
                                  f"share the identity {method.travel_method_id}.")
    for number in range(2, len(methods) + 1):
        if methods[number - 2].travel_method_id > methods[number - 1].travel_method_id:
            _reject_travel_method("ENVIRONMENT_KIND_INVALID", number,
                                  "Travel method rows must be sorted by travel_method_id.")
    for number, method in enumerate(methods, start=1):
        if channel_key(method.name) == channel_key(WALKING_TRAVEL_METHOD_NAME):
            _reject_travel_method("ENVIRONMENT_ID_DUPLICATE", number,
                                  f"uses the built-in name {WALKING_TRAVEL_METHOD_NAME}.")
    for number, method in enumerate(methods, start=1):
        first, name = names.setdefault(channel_key(method.name), (number, method.name))
        if first != number:
            _reject_travel_method("ENVIRONMENT_ID_DUPLICATE", first,
                                  f"and travel-method.jsonl:{number} share the name {name}.")
    return frozenset({WALKING_TRAVEL_METHOD_ID, *identities})


def _reject_travel_method(code: str, number: int, detail: str) -> NoReturn:
    """@brief 出行方式的登记失败详情一律以文件名和行号开头。"""
    raise RegistrationError(code, f"travel-method.jsonl:{number} {detail}")


def _check_method_reference(source: str, value: str, methods: frozenset[str]) -> None:
    """@brief app 行与回调规则只可引用登记的出行方式或自带的步行，详情带引用所在的文件与行号。"""
    if value not in methods:
        raise RegistrationError("ENVIRONMENT_REF_UNKNOWN", f"{source} Unknown travel_method_id: {value}.")


def _check_apps(rows, methods: frozenset[str]) -> None:
    """@brief 功能 app 至少有购买或出行方式一种绑定，内容 app 不得携带绑定且至少有内容或接单。"""
    names = {channel_key(p.display_name) for p in rows["locations"]}
    for number, app in enumerate(rows["apps"], start=1):
        if channel_key(app.name) in names:
            raise RegistrationError("APP_NAME_COLLIDES_LOCATION")
        if app.role == "function":
            if not app.actions and not app.travel_method_ids:
                raise RegistrationError("APP_UNBOUND")
            _check_order(app.actions)
            _check_order(app.travel_method_ids)
            for value in app.travel_method_ids:
                _check_method_reference(f"app-catalog.jsonl:{number}", value, methods)
            continue
        if {"actions", "travel_method_ids"} & app.model_fields_set:
            raise RegistrationError("APP_ROLE_BINDING_INVALID")
        explicit = any(app.app_id in c.app_ids for c in rows["contents"])
        directory = any(app.app_id in g.app_ids for g in rows["goods"]) or any(
            app.app_id in p.app_ids and any(p.kind in g.store_kinds for g in rows["goods"]) for p in rows["locations"])
        if not explicit and not directory:
            raise RegistrationError("APP_UNBOUND")


def _check_callbacks(rows, apps, methods: frozenset[str]) -> None:
    """@brief 状态、商品、触发阈值、出行方式引用、足迹池与提醒效果严格对应。"""
    states = {state.state: state for state in rows["callback_states"]}
    goods = {item.goods_id for item in rows["goods"]}
    for state in states.values():
        if not 0 <= state.initial <= state.cap:
            _LOGGER.error("Invalid initial/cap range on callback state %s", state.state)
            raise RegistrationError("ENVIRONMENT_NUMBER_INVALID")
        _check_bindings(state.reset_on_goods, goods)
        _check_bindings(state.gain_on_goods, goods)
        if set(state.reset_on_goods) & set(state.gain_on_goods):
            _LOGGER.error("Callback state %s overlaps reset_on_goods and gain_on_goods", state.state)
            raise RegistrationError(
                "ENVIRONMENT_KIND_INVALID",
                f"Callback state {state.state} overlaps reset_on_goods and gain_on_goods.")
    for number, rule in enumerate(rows["callback_rules"], start=len(states) + 1):
        trigger, effect = rule.trigger, rule.effect
        if trigger.trigger_class == "action" and trigger.key == "travel":
            _check_method_reference(f"callbacks.jsonl:{number}", trigger.travel_method_id, methods)
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


def _check_map_edges(edges, locations: Sequence[Location], methods: frozenset[str],
                     states: frozenset[str]) -> None:
    """@brief 声明边依次核对端点次序、端点登记、地点对唯一、行次序、限制字段与自由边互不矛盾。"""
    city_by_node = {place.location_id: place.city_id for place in locations}
    lines = {}
    pairs = [(edge.a, edge.b) for edge in edges]
    for number, (a, b) in enumerate(pairs, start=1):
        if a >= b:
            _reject_map_row("ENVIRONMENT_KIND_INVALID", number, "Map endpoints must satisfy a < b.")
    for number, pair in enumerate(pairs, start=1):
        for endpoint in pair:
            if endpoint not in city_by_node:
                _reject_map_row("ENVIRONMENT_REF_UNKNOWN", number, f"Unknown location_id: {endpoint}.")
    for number, (a, b) in enumerate(pairs, start=1):
        first = lines.setdefault((a, b), number)
        if first != number:
            _reject_map_row("ENVIRONMENT_ID_DUPLICATE", first, f"and map.jsonl:{number} declare the same pair {a}-{b}.")
    for number in range(2, len(pairs) + 1):
        if pairs[number - 2] > pairs[number - 1]:
            _reject_map_row("ENVIRONMENT_KIND_INVALID", number, "Map rows must be sorted by (a, b).")
    for number, edge in enumerate(edges, start=1):
        _check_one_edge(number, edge, city_by_node, methods, states)
    _check_declared_distances(edges, locations)


def _check_one_edge(number: int, edge, city_by_node: Mapping[str, str],
                    methods: frozenset[str], states: frozenset[str]) -> None:
    """@brief 核对单条声明边的跨城约束、自由边禁携字段、出行方式引用、状态及发车时段。"""
    cross_city = city_by_node[edge.a] != city_by_node[edge.b]
    if cross_city:
        if not edge.travel_method_ids:
            _reject_map_row("ENVIRONMENT_KIND_INVALID", number, "Intercity edges must declare travel_method_ids.")
        if WALKING_TRAVEL_METHOD_ID in edge.travel_method_ids:
            _reject_map_row("ENVIRONMENT_KIND_INVALID", number, "Intercity edges may not allow walking.")
    if edge.travel_method_ids is None:
        if edge.consumes_state is not None or edge.opens is not None:
            _reject_map_row("ENVIRONMENT_KIND_INVALID", number,
                            "Free map edges may not declare consumes_state or opens.")
        return
    try:
        _check_order(edge.travel_method_ids)
    except RegistrationError as error:
        detail = ("Duplicate travel_method_id." if error.code == "ENVIRONMENT_ID_DUPLICATE"
                  else "travel_method_ids must be sorted.")
        _reject_map_row(error.code, number, detail)
    for method_id in edge.travel_method_ids:
        if method_id not in methods:
            _reject_map_row("ENVIRONMENT_REF_UNKNOWN", number, f"Unknown travel_method_id: {method_id}.")
    if edge.consumes_state is not None and edge.consumes_state not in states:
        _reject_map_row("ENVIRONMENT_REF_UNKNOWN", number, f"Unknown callback state: {edge.consumes_state}.")
    if edge.opens is not None:
        try:
            _check_opening_hours(edge.opens)
        except RegistrationError as error:
            _reject_map_row(error.code, number, error.detail or error.message)


def _reject_map_row(code: str, number: int, detail: str) -> NoReturn:
    """@brief 声明边的登记失败详情一律以文件名和行号开头并记录错误日志。"""
    _LOGGER.error("Map edge validation failed (%s) at map.jsonl:%d: %s", code, number, detail)
    raise RegistrationError(code, f"map.jsonl:{number} {detail}")


def _check_declared_distances(edges, locations: Sequence[Location]) -> None:
    """@brief 只含自由声明边的图上，任何一条自由声明边都不得被经其他自由声明边的更短路线绕过。"""
    city_by_node = {place.location_id: place.city_id for place in locations}
    free_edges = tuple(edge for edge in edges if edge.travel_method_ids is None)
    declared = Graph(MapGraph(nodes=tuple(sorted(city_by_node)), edges=free_edges), city_by_node)
    for number, edge in enumerate(edges, start=1):
        if edge.travel_method_ids is not None:
            continue
        meters = declared.distance_meters(edge.a, edge.b)
        if meters < edge.length_m:
            route = " -> ".join(declared.path(edge.a, edge.b))
            _reject_map_row("MAP_DISTANCE_CONTRADICTED", number,
                            f"{edge.a}-{edge.b} declares {edge.length_m} m but {route} is {meters} m.")


def validate_route_limit(locations: Sequence[Location], graph: Graph,
                         methods: Mapping[str, TravelMethod]) -> None:
    """@brief 双轨核验一日出行上限：多节点城市自由街区子图连通且可达上限，各限定声明边单边可达上限。"""
    _check_free_city_limits(locations, graph, methods)
    _check_gated_edge_limits(graph, methods)


def _check_free_city_limits(locations: Sequence[Location], graph: Graph,
                            methods: Mapping[str, TravelMethod]) -> None:
    """@brief 检验多节点城市自由街区子图内部连通及 walking/city_wide 最慢方式雪天不超过一日上限。"""
    by_city: dict[str, list[str]] = defaultdict(list)
    for place in locations:
        by_city[place.city_id].append(place.location_id)
    multi_cities = [(city_id, tuple(sorted(nodes))) for city_id, nodes in sorted(by_city.items())
                    if len(nodes) >= MAP_MIN_NODES]
    for city_id, city_nodes in multi_cities:
        if set(graph.free_shortest_paths(city_nodes[0])) != set(city_nodes):
            _LOGGER.error("Free-street subgraph of city %s is disconnected", city_id)
            raise RegistrationError("MAP_DISCONNECTED", f"City {city_id} free street graph is disconnected.")
    farthest = _farthest_free_pair(multi_cities, graph)
    if farthest is None:
        return
    meters, origin, destination = farthest
    slowest = min((WALKING_METERS_PER_SECOND,
                   *(method.speed_meters_per_second for method in methods.values() if method.city_wide)))
    _require_within_day_limit(origin, destination, meters, slowest)


def _farthest_free_pair(multi_cities: list[tuple[str, tuple[str, ...]]],
                        graph: Graph) -> tuple[int, str, str] | None:
    """@brief 在多节点城市的同城自由子图中找出距离最远的一对地点。"""
    farthest: tuple[int, str, str] | None = None
    for _, city_nodes in multi_cities:
        for origin in city_nodes:
            for destination, (meters, _) in graph.free_shortest_paths(origin).items():
                if origin < destination and (
                        farthest is None or meters > farthest[0]
                        or (meters == farthest[0] and (origin, destination) < farthest[1:])):
                    farthest = (meters, origin, destination)
    return farthest


def _check_gated_edge_limits(graph: Graph, methods: Mapping[str, TravelMethod]) -> None:
    """@brief 检验每条声明了 travel_method_ids 的边在最慢允许方式雪天 0.6 下单边不超过一日上限。"""
    for edge in graph.gated_edges():
        slowest = min(
            WALKING_METERS_PER_SECOND if method_id == WALKING_TRAVEL_METHOD_ID
            else methods[method_id].speed_meters_per_second
            for method_id in edge.travel_method_ids)
        _require_within_day_limit(edge.a, edge.b, edge.length_m, slowest)


def _require_within_day_limit(origin: str, destination: str, meters: int, slowest: int | float) -> None:
    """@brief 校验给定路程在最慢速度与最差天气下不超过 TRAVEL_MAX_SECONDS。"""
    seconds = travel_seconds(slowest, meters, min(WEATHER_FACTORS.values()))
    if seconds > TRAVEL_MAX_SECONDS:
        _LOGGER.error("Route %s -> %s (%d m at %s m/s) takes %d s, exceeding %d s",
                      origin, destination, meters, slowest, seconds, TRAVEL_MAX_SECONDS)
        raise RegistrationError(
            "MAP_ROUTE_TOO_LONG",
            f"{origin} -> {destination} is {meters} m, which takes {seconds} s at {slowest} m/s "
            f"in the worst weather; the limit is {TRAVEL_MAX_SECONDS} s.")


def validate_map(graph: MapGraph, location_ids: set[str]) -> None:
    """@brief 内存图节点完整、规范正边有序且全图整体连通。"""
    if len(graph.nodes) < MAP_MIN_NODES:
        _LOGGER.error("Map has fewer than %d nodes", MAP_MIN_NODES)
        raise RegistrationError("MAP_TOO_SMALL")
    pairs = [(edge.a, edge.b) for edge in graph.edges]
    if (tuple(sorted(location_ids)) != graph.nodes or pairs != sorted(set(pairs))
            or any(edge.a >= edge.b or edge.a not in location_ids or edge.b not in location_ids
                   for edge in graph.edges)):
        _LOGGER.error("Map contains invalid or unsorted edge endpoints")
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
        _LOGGER.error("Map is not globally connected")
        raise RegistrationError("MAP_DISCONNECTED")
