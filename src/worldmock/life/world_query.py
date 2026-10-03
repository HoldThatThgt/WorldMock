"""@brief 从本轮冻结观察查询公开能力，不修改世界、人物记忆或呈现凭据。"""

from __future__ import annotations

import copy
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from worldmock.life.error_contract import ErrorItem, ErrorReport
from worldmock.life.interaction_menu import PURCHASE_WAYS, WAYS, binding_action
from worldmock.life.models import purpose_only_schema
from worldmock.life.name_resolution import resolve_name, way_options
from worldmock.life.object_labels import supply_label
from worldmock.life.readable import BROWSE_LABELS
from worldmock.life.tool_groups import compact

_LOGGER = logging.getLogger(__name__)


class QueryWorldRequest(BaseModel):
    """@brief 固定四字段查询，过滤条件取交集，分页偏移严格使用 JSON 整数。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, json_schema_extra=purpose_only_schema())

    name: str | None
    way: str | None
    category: str | None
    offset: int = Field(ge=0)

    @field_validator("name", "way", "category")
    @classmethod
    def nonblank_filter(cls, value: str | None) -> str | None:
        """@brief 非空过滤条件必须包含实际字符，保持原写法供确定性名称解析。"""
        if value is not None and not value.strip():
            _LOGGER.error("World query filter is blank")
            raise ValueError("A non-null filter must contain a non-whitespace character")
        return value

    @model_validator(mode="after")
    def require_filter(self) -> "QueryWorldRequest":
        """@brief 全空条件逐字段定位，不能把依赖约束错误放在无字段的根位置。"""
        if self.name is None and self.way is None and self.category is None:
            _LOGGER.error("World query has no non-null filter")
            errors = [{"type": "value_error", "loc": (field,), "input": None,
                       "ctx": {"error": ValueError("At least one query filter must be non-null")}}
                      for field in ("name", "way", "category")]
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self


def attach_world_query(observation: dict, context: Any) -> None:
    """@brief 在观察构建时一次冻结完整权限内事实，供宿主即时只读分页使用；另冻结渠道索引（world_query_channels：渠道
    身份、称呼、成员门店，按声明顺序），供来源筛选与购买共用同一解析（docs/dev/SPEC-errands-workday.md 5.2）。地点表与
    住处已在观察里（locations、home_location_id），不另存副本。"""
    from worldmock.life.tool_layer import tool_layer, with_bindings
    from worldmock.life.callable_index import query_category_index

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
    from worldmock.life.discovery import DiscoveryContext, supply_row

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
    （docs/dev/SPEC-errands-workday.md 5.4）。供给条目另记所属门店（location_id，来源筛选用，不进查询结果），其余条目
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
    """@brief 汇总所有独立可确定的动态错误；分页范围只在过滤条件有效后计算。"""
    errors = []
    if request.way is not None and not way_options(request.way):
        errors.append(_query_issue("way", request.way, tuple(WAYS), "A declared activity way is required."))
    labels = tuple(row["category"] for row in observation["tool_layer"]["callable_index"]["query_categories"])
    if request.category is not None and request.category not in labels:
        errors.append(_query_issue("category", request.category, labels, "Use a category shown in callable_index."))
    if not errors:
        total = len(_matching_entries(observation, request))
        if request.offset > total:
            errors.append(_query_issue("offset", request.offset, (), f"An integer in the inclusive range 0..{total}."))
    if not errors:
        return None
    _LOGGER.error("World query rejected: %s", [item.path for item in errors])
    return ErrorReport("ARGUMENTS_INVALID", "World query arguments violate the frozen public contract.",
                       "Correct all listed arguments before submitting the query again.", tuple(errors))


def _query_issue(field: str, written: Any, allowed: tuple, expected: str) -> ErrorItem:
    """@brief 查询明细使用相对参数位置，由宿主绑定本次真实工具调用根及下标。"""
    return ErrorItem(path=(field,), written=written, error_code="ARGUMENTS_INVALID", allowed_values=allowed,
                     expected=expected, why="The submitted value is outside the current query contract.")


def _matching_entries(observation: dict, request: QueryWorldRequest) -> list[dict]:
    """@brief 确定性名称归一与方式闭集、公开品类取交集，按完整公开名和稳定身份排序。"""
    entries = observation["world_query_bindings"]
    names = None
    if request.name is not None:
        names = set(resolve_name(request.name, (name for entry in entries for name in entry["aliases"])).matches)
    ways = way_options(request.way) if request.way is not None else None
    selected = [entry for entry in entries
                if (names is None or names.intersection(entry["aliases"]))
                and (request.category is None or entry["category"] == request.category)
                and (ways is None or any(action["way"] in ways for action in entry["match"]["actions"]))]
    return sorted(selected, key=lambda entry: (entry["match"]["name"], entry["identity"]))


def query_world(observation: dict, request: QueryWorldRequest) -> dict:
    """@brief 查询已验证的冻结观察页；每项是冻结目标的无损紧凑投影（只略去 null 与空集合，复用工具层分组的 compact），
    返回新结构，调用者不能改变后续页或权限事实。"""
    entries = _matching_entries(observation, request)
    end = min(request.offset + observation["world_query_page_size"], len(entries))
    return {"matches": [compact(entry["match"]) for entry in entries[request.offset:end]],
            "total_count": len(entries), "next_offset": end if end < len(entries) else None}
