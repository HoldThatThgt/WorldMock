"""@brief 从本轮冻结观察查询公开能力，不修改世界、人物记忆或呈现凭据。"""

from __future__ import annotations
from worldmock.life.tool_layer import tool_layer, with_bindings
from worldmock.life.callable_index import query_category_index
from worldmock.life.discovery import DiscoveryContext, supply_row

import copy
import logging
from dataclasses import replace
from typing import Any

from pydantic import (BaseModel, ConfigDict, Field, ModelWrapValidatorHandler, ValidationError, field_validator,
                      model_validator)

from worldmock.life.error_contract import Candidate, ErrorItem, ErrorReport
from worldmock.life.guidance import default_reason
from worldmock.life.vocabulary import PURCHASE_WAYS, SYSTEM_PURCHASE_WAY, WAYS
from worldmock.life.interaction_menu import binding_action
from worldmock.life.models import purpose_only_schema
from worldmock.life.name_resolution import channel_key, near_source_names, resolve_source
from worldmock.life.object_labels import resolve_name
from worldmock.life.vocabulary import way_options
from worldmock.life.object_labels import supply_label
from worldmock.life.readable import BROWSE_LABELS
from worldmock.life.request_errors import argument_report
from worldmock.life.tool_groups import compact

_LOGGER = logging.getLogger(__name__)
#: 两种新查询参数错误的 expected（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-错误  原文）与查询明细共用的 why。
_SOURCE_EXPECTED = "null, a known place name, or a known channel name is required."
_CHANNEL_IN_NAME_EXPECTED = "A channel name belongs in from; name takes an object name."
_QUERY_WHY = "The submitted value is outside the current query contract."
#: 至少一个须非 null 的四个筛选（JSON 键，5.1）。
_FILTER_KEYS = ("name", "way", "category", "from")


class QueryWorldRequest(BaseModel):
    """@brief 固定六字段查询（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-请求 ）：六个键全部必填、无缺省，名称、方式、品类与购买来源
    取交集，available_only 只留已确认此刻可执行的条目（不单独构成查询条件），分页偏移严格使用 JSON 整数。来源的 JSON 键
    是 from（字段名 from_ 只在代码里用）；非 null 的名称、方式与品类不得为空白，来源的空白写法按 5.5 第一行报错。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, validate_by_alias=True,
                              serialize_by_alias=True, json_schema_extra=purpose_only_schema())

    name: str | None
    way: str | None
    category: str | None
    from_: str | None = Field(alias="from")
    available_only: bool
    offset: int = Field(ge=0)

    @field_validator("name", "way", "category")
    @classmethod
    def nonblank_filter(cls, value: str | None) -> str | None:
        """@brief 非空过滤条件必须包含实际字符，保持原写法供确定性名称解析。"""
        if value is not None and not value.strip():
            _LOGGER.error("World query filter is blank")
            raise ValueError("A non-null filter must contain a non-whitespace character")
        return value

    @model_validator(mode="wrap")
    @classmethod
    def require_filter(cls, data: Any, handler: ModelWrapValidatorHandler["QueryWorldRequest"]) -> "QueryWorldRequest":
        """@brief 名称、方式、品类、来源四键都写了 null 时在四个位置各报一条，不能把依赖约束错误放在无字段的根位置；
        其余字段同时不合法时与那些字段错误一次全报（包住字段校验而不是排在其后，字段错误不会把它略去）。available_only
        不单独构成查询条件。"""
        if not isinstance(data, dict) or any(key not in data or data[key] is not None for key in _FILTER_KEYS):
            return handler(data)
        _LOGGER.error("World query has no non-null filter")
        errors = [{"type": "value_error", "loc": (key,), "input": None,
                   "ctx": {"error": ValueError("At least one query filter must be non-null")}} for key in _FILTER_KEYS]
        try:
            handler(data)
        except ValidationError as error:
            errors.extend({key: row[key] for key in ("type", "loc", "input", "ctx") if key in row}
                          for row in error.errors())
        raise ValidationError.from_exception_data(cls.__name__, errors)


def attach_world_query(observation: dict, context: Any) -> None:
    """@brief 在观察构建时一次冻结完整权限内事实，供宿主即时只读分页使用；另冻结渠道索引（world_query_channels：渠道
    身份、称呼、成员门店，按声明顺序），供来源筛选与购买共用同一解析（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-来源解析 ）。地点表与
    住处已在观察里（locations、home_location_id），不另存副本。"""

    bindings = _complete_bindings(observation, context)
    complete = tool_layer(with_bindings(context, bindings), {})["targets"]
    private = {row["object"]: row for row in bindings}
    entries = [_frozen_entry(context, target, private.get(target["name"])) for target in complete]
    observation["world_query_bindings"] = entries
    observation["world_query_channels"] = [
        {"channel_id": channel.channel_id, "names": list(channel.names),
         "store_location_ids": list(channel.store_location_ids)} for channel in context.semantics.purchase_channels]
    observation["world_query_page_size"] = context.scenario.candidate_presentation_limit
    observation["tool_layer"]["callable_index"]["query_categories"] = query_category_index(entries)


def _complete_bindings(observation: dict, context: Any) -> list[dict]:
    """@brief 合并已知绑定、可指名目录和完整合法商品集合，不受本轮呈现窗口限制。"""
    rows = {row["object"]: copy.deepcopy(row) for row in context.bindings}
    for item in observation["catalog_bindings"]:
        rows.setdefault(item["object"], {"object": item["object"], "kind": item["kind"],
                                        "object_id": item["object_id"],
                                        "actions": [binding_action(item["way"], WAYS[item["way"]][1])]})
    for item in _supply_bindings(context):
        rows.setdefault(item["object"], item)
    return [rows[name] for name in sorted(rows)]


def _supply_bindings(context: Any) -> list[dict]:
    """@brief 指名购买已经授权的逐店商品公开事实；冻结读取不要求真实浏览准入。"""

    agent = context.state.agents[context.agent_id]
    discovery = DiscoveryContext(context.state, context.scenario, context.semantics, agent)
    rows = []
    for supply in context.scenario.supplies:
        fact = supply_row(discovery, supply)
        if fact["purchase_pairing_state"] != "defined" or not fact["access_allowed"] or not fact["service_allowed"]:
            continue
        rows.append({"object": supply_label(fact["location_name"], fact["name"]), "kind": "supply",
                     "object_id": supply.supply_id,
                     "quote": {key: fact[key] for key in ("price_version", "price_fen", "purchase_price_fen")},
                     "actions": [binding_action(PURCHASE_WAYS[mode], "purchase", fulfillment=mode)
                                 for mode in supply.fulfillment_modes]})
    return rows


def _frozen_entry(context: Any, target: dict, binding: dict | None) -> dict:
    """@brief 内部身份只用于排序，查询结果保留公开目标（完整副本，null 照存，返回时才紧凑投影）；不附逐项请求候选
    （docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-返回 ）。供给条目另记所属门店（location_id，来源筛选用，不进查询结果），其余条目
    为 None。"""
    identity = (f"{binding['kind']}:{binding['object_id']}" if binding is not None else target["name"])
    public = copy.deepcopy(target)
    aliases, location = [target["name"]], None
    if binding is not None and binding["kind"] == "supply":
        supply = context.rules.supplies[binding["object_id"]]
        aliases.append(context.rules.items[supply.product_id].name)
        location = supply.location_id
    return {"identity": identity, "category": _category(context, binding), "aliases": aliases,
            "location_id": location, "match": public}


def _category(context: Any, binding: dict | None) -> str | None:
    """@brief 使用已有公开品类树与内容类别，不创建查询专用分类词表。"""
    if binding is None:
        return None
    kind, identity = binding["kind"], binding["object_id"]
    if kind == "content" or (kind == "game" and identity in context.rules.contents):
        return BROWSE_LABELS[context.rules.contents[identity].kind]
    if kind == "game":
        return BROWSE_LABELS["game_session"]
    if kind == "supply":
        identity = context.rules.supplies[identity].product_id
    obj = context.semantics.catalog.objects.get(identity)
    return context.semantics.catalog.category_contract.require_category(obj.category_id).name if obj else None


def validate_world_query(observation: dict, request: QueryWorldRequest) -> ErrorReport | None:
    """@brief 汇总所有独立可确定的动态错误（方式、品类、来源与误写进名称的渠道称呼一次全报）；分页范围只在全部筛选
    合法后按筛选后的完整匹配数计算（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-错误 ）。宿主与环境两个入口在同一冻结观察上调用它。"""
    errors = _filter_issues(observation, request.model_dump(mode="json"))
    if not errors:
        total = len(_matching_entries(observation, request))
        if request.offset > total:
            errors.append(_query_issue("offset", request.offset, (), f"An integer in the inclusive range 0..{total}."))
    if not errors:
        return None
    _LOGGER.error("World query rejected: %s", [item.path for item in errors])
    return ErrorReport("ARGUMENTS_INVALID", "World query arguments violate the frozen public contract.",
                       "Correct all listed arguments before submitting the query again.", tuple(errors))


def query_argument_report(observation: dict, error: ValidationError, arguments: Any) -> ErrorReport:
    """@brief 宿主严格校验失败时的完整查询参数报告：先列请求模型的错误（缺键、类型、空白与四者全空），再列按冻结观察
    仍可独立判定的方式、品类、来源与误写进名称的渠道称呼错误——每条只看自身取值合法的那个键，所以来源错误不被别的键的
    类型或空白错误压下（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-错误 「来源错误与方式、品类错误一次全报」）；候选里其余键照原样。
    offset 范围只在全部筛选合法后计算，这里不算。"""
    report = argument_report("query_world", error, QueryWorldRequest, arguments)
    items = (*report.items, *_filter_issues(observation, arguments if isinstance(arguments, dict) else {}))
    return replace(report, reason="; ".join(item.why for item in items), items=items)


def _filter_issues(observation: dict, values: dict) -> list[ErrorItem]:
    """@brief 方式、品类、来源与误写进名称的渠道称呼各自独立判定，按这个顺序一次全部列出。values 是 JSON 键的查询参数
    （受理的请求按别名导出，或严格校验失败时的原始参数），每条只判定自身取值合法的那个键。"""
    errors = []
    way, category, source = (_filter_text(values, key) for key in ("way", "category", "from"))
    if way is not None and not way_options(way):
        errors.append(_query_issue("way", way, tuple(WAYS), "A declared activity way is required."))
    labels = tuple(row["category"] for row in observation["tool_layer"]["callable_index"]["query_categories"])
    if category is not None and category not in labels:
        errors.append(_query_issue("category", category, labels, "Use a category shown in callable_index."))
    if source is not None and _source_stores(observation, source) is None:
        errors.append(_source_issue(observation, values))
    misplaced = _misplaced_channel_issue(observation, values)
    if misplaced is not None:
        errors.append(misplaced)
    return errors


def _filter_text(values: dict, key: str) -> str | None:
    """@brief 可按冻结观察判定的筛选写法：名称、方式与品类须是非空白字符串，来源是任意字符串（空白写法按 5.5 第一行
    报错）；缺键、null 与类型不符时为 None（那些由请求模型报告）。"""
    value = values.get(key)
    if not isinstance(value, str) or (key != "from" and not value.strip()):
        return None
    return value


def _query_issue(field: str, written: Any, allowed: tuple, expected: str) -> ErrorItem:
    """@brief 查询明细使用相对参数位置，由宿主绑定本次真实工具调用根及下标。"""
    return ErrorItem(path=(field,), written=written, error_code="ARGUMENTS_INVALID", allowed_values=allowed,
                     expected=expected, why=_QUERY_WHY)


def _source_issue(observation: dict, values: dict) -> ErrorItem:
    """@brief 来源写法既不是地点显示名也不是渠道称呼（5.5 第一行）：候选是至多三条换了 from、其余键照原样的完整查询，
    来源只取权限内有供给条目的门店与含这些门店的渠道（与购买侧同一排名；一个也不相近时按门店身份顺序取前几个），不泄露
    无权限的门店。"""
    places = _places(observation)
    stores = _permitted_stores(observation)
    channels = tuple(row for row in _channel_rows(observation) if stores.intersection(row[2]))
    names = near_source_names(values["from"], places, stores, channels, lambda scope: True)
    candidates = tuple(Candidate("query_world", copy.deepcopy({**values, "from": name})) for name in names)
    return ErrorItem(path=("from",), written=values["from"], error_code="ARGUMENTS_INVALID", allowed_values=(),
                     expected=_SOURCE_EXPECTED, why=default_reason("SOURCE_NOT_FOUND"), candidates=candidates)


def _misplaced_channel_issue(observation: dict, values: dict) -> ErrorItem | None:
    """@brief from 为 null、name 按对象名称解析无任何匹配、而其写法恰是一个渠道称呼（5.5 第二行，与其余筛选无关）：给一条
    把该值移到 from、name 写 null 的完整查询（其余键照原样），不自动改写；其余情形（含 from 缺键）为 None。"""
    name = _filter_text(values, "name")
    if name is None or "from" not in values or values["from"] is not None:
        return None
    key = channel_key(name)
    if not any(key == channel_key(written) for row in _channel_rows(observation) for written in row[1]):
        return None
    aliases = (alias for entry in observation["world_query_bindings"] for alias in entry["aliases"])
    if resolve_name(name, aliases).matches:
        return None
    moved = copy.deepcopy({**values, "name": None, "from": name})
    return ErrorItem(path=("name",), written=name, error_code="ARGUMENTS_INVALID", allowed_values=(),
                     expected=_CHANNEL_IN_NAME_EXPECTED, why=_QUERY_WHY, candidates=(Candidate("query_world", moved),))


def _places(observation: dict) -> dict[str, str]:
    """@brief 冻结地点表：地点身份 → 显示名（observe 写入的 locations）。"""
    return {row["location_id"]: row["name"] for row in observation["locations"]}


def _channel_rows(observation: dict) -> tuple:
    """@brief 冻结渠道索引的各行，写成购买侧同一形状（name_resolution.ChannelRow：渠道身份，称呼，成员门店）。"""
    return tuple((row["channel_id"], tuple(row["names"]), tuple(row["store_location_ids"]))
                 for row in observation["world_query_channels"])


def _permitted_stores(observation: dict) -> frozenset[str]:
    """@brief 权限内有供给条目的门店身份（冻结供给条目的 location_id）。"""
    return frozenset(entry["location_id"] for entry in observation["world_query_bindings"]
                     if entry["location_id"] is not None)


def _source_stores(observation: dict, written: str) -> frozenset[str] | None:
    """@brief 所写来源解析出的门店身份集合：与购买侧同一个纯函数（name_resolution.resolve_source），入参取自冻结观察的
    地点表、住处与渠道索引；不是地点显示名也不是渠道称呼时为 None。"""
    places = _places(observation)
    match = resolve_source(written, places, observation["home_location_id"], _channel_rows(observation))
    return None if match is None else match.store_ids


def _query_ways(way: str | None) -> frozenset[str] | None:
    """@brief 所写方式指的方式集合；「购买」及其同义写法等同于同时写到店与远程下单（5.1）；未写方式时为 None。"""
    if way is None:
        return None
    options = way_options(way)
    return options | frozenset(PURCHASE_WAYS.values()) if SYSTEM_PURCHASE_WAY in options else options


def _executable(match: dict, ways: frozenset[str] | None) -> bool:
    """@brief 冻结目标此刻是否已确认可执行（5.3）：取本轮冻结观察里的 available，写了方式只看方式命中的动作，未写方式时
    看全部动作以及以该目标为参数的工具。"""
    if ways is not None:
        return any(action["available"] for action in match["actions"] if action["way"] in ways)
    return any(row["available"] for row in (*match["actions"], *match["tools"]))


def _matching_entries(observation: dict, request: QueryWorldRequest) -> list[dict]:
    """@brief 名称（确定性归一）、公开品类、方式闭集（「购买」展开）、来源（只匹配所属门店在范围内的供给条目）与可执行
    取交集，全部在排序与切页之前施加，按完整公开名和稳定身份排序；来源不按需求反查、不按距离或价格排序。"""
    entries = observation["world_query_bindings"]
    names = None
    if request.name is not None:
        names = set(resolve_name(request.name, (name for entry in entries for name in entry["aliases"])).matches)
    ways = _query_ways(request.way)
    stores = None if request.from_ is None else _source_stores(observation, request.from_) or frozenset()
    selected = [entry for entry in entries
                if (names is None or names.intersection(entry["aliases"]))
                and (request.category is None or entry["category"] == request.category)
                and (ways is None or any(action["way"] in ways for action in entry["match"]["actions"]))
                and (stores is None or entry["location_id"] in stores)
                and (not request.available_only or _executable(entry["match"], ways))]
    return sorted(selected, key=lambda entry: (entry["match"]["name"], entry["identity"]))


def query_world(observation: dict, request: QueryWorldRequest) -> dict:
    """@brief 查询已验证的冻结观察页；每项是冻结目标的无损紧凑投影（只略去 null 与空集合，复用工具层分组的 compact），
    返回新结构，调用者不能改变后续页或权限事实。"""
    entries = _matching_entries(observation, request)
    end = min(request.offset + observation["world_query_page_size"], len(entries))
    return {"matches": [compact(entry["match"]) for entry in entries[request.offset:end]],
            "total_count": len(entries), "next_offset": end if end < len(entries) else None}
