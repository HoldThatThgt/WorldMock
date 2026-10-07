"""@brief 从本轮冻结观察查询公开能力，不修改世界、人物记忆或呈现凭据。"""

from __future__ import annotations

import copy
import logging
from dataclasses import replace
from typing import Any

from pydantic import (BaseModel, ConfigDict, Field, ModelWrapValidatorHandler, ValidationError, field_validator,
                      model_validator)

from worldmock.life.callable_index import query_category_index
from worldmock.life.discovery import product_bindings
from worldmock.life.tool_layer import tool_layer, with_bindings
from worldmock.life.error_contract import MAX_CANDIDATES, Candidate, ErrorItem, ErrorReport
from worldmock.life.vocabulary import WAYS
from worldmock.life.models import purpose_only_schema
from worldmock.life.name_resolution import near_names
from worldmock.exchange.types import ACTION_CATEGORY_LABELS, GOODS_CATEGORY_LABELS
from worldmock.life.object_labels import resolve_name
from worldmock.life.vocabulary import way_options
from worldmock.life.request_errors import argument_report
from worldmock.life.tool_groups import compact

_LOGGER = logging.getLogger(__name__)
#: 应用名错误与误填对象名的精确公开合同。
_APP_EXPECTED = "null or a known app name is required."
_APP_WHY = "The written app is not a known app name."
_APP_IN_NAME_EXPECTED = "An app name belongs in app; name takes an object name."
_QUERY_WHY = "The submitted value is outside the current query contract."
#: 至少一个须非 null 的四个筛选。
_FILTER_KEYS = ("name", "way", "category", "app")


class QueryWorldRequest(BaseModel):
    """@brief 六键全部必填，名称、方式、品类与登记应用取交集，分页偏移为严格非负整数。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, json_schema_extra=purpose_only_schema())

    name: str | None
    way: str | None
    category: str | None
    app: str | None
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
        """@brief 四种筛选全为空时在各位置报错，并保留其他独立严格校验错误。"""
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
    """@brief 冻结完整权限内商品与动作，不改变世界、呈现窗口或人物记忆。"""
    bindings = _complete_bindings(context)
    complete = tool_layer(with_bindings(context, bindings), {})["targets"]
    private = {row["object"]: row for row in bindings}
    entries = [_frozen_entry(context, target, private.get(target["name"])) for target in complete]
    observation["world_query_bindings"] = entries
    observation["world_query_apps"] = {identity: {"name": app.name}
                                       for identity, app in context.semantics.catalog.apps.items()}
    observation["world_query_page_size"] = context.scenario.candidate_presentation_limit
    observation["tool_layer"]["callable_index"]["query_categories"] = query_category_index(entries)


def _complete_bindings(context: Any) -> list[dict]:
    """@brief 完整商品来自呈现同一归属入口，其他绑定保持本轮权限与固定动作集合。"""
    rows = {row["object"]: copy.deepcopy(row) for row in context.bindings if row["kind"] != "supply"}
    for item in product_bindings(context):
        rows[item["object"]] = item
    return [rows[name] for name in sorted(rows)]


def _frozen_entry(context: Any, target: dict, binding: dict | None) -> dict:
    """@brief 冻结完整公开目标和真实应用名称，内部身份只用于稳定排序。"""
    identity = f"{binding['kind']}:{binding['object_id']}" if binding is not None else target["name"]
    return {"identity": identity, "category": _category(context, binding), "aliases": [target["name"]],
            "match": copy.deepcopy(target)}


def _category(context: Any, binding: dict | None) -> str | None:
    """@brief 商品与动作标签从唯一固定分类表和真实目录对象派生。"""
    if binding is None:
        return None
    catalog = context.semantics.catalog
    if binding["kind"] == "supply":
        return GOODS_CATEGORY_LABELS[catalog.goods[binding["goods_id"]].category]
    if binding["kind"] == "action":
        return ACTION_CATEGORY_LABELS[catalog.actions[binding["object_id"]].category]
    return None


def validate_world_query(observation: dict, request: QueryWorldRequest) -> ErrorReport | None:
    """@brief 同一冻结观察一次汇总方式、品类、应用和对象名错误，合法筛选后才检查分页范围。"""
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
    """@brief 严格模型错误和仍可独立确定的动态筛选错误一次全报，候选保留其他原始字段。"""
    report = argument_report("query_world", error, QueryWorldRequest, arguments)
    items = (*report.items, *_filter_issues(observation, arguments if isinstance(arguments, dict) else {}))
    return replace(report, reason="; ".join(item.why for item in items), items=items)


def _filter_issues(observation: dict, values: dict) -> list[ErrorItem]:
    """@brief 方式、品类、应用与应用误填名称各自独立判定，不用一个错误掩盖其他错误。"""
    errors = []
    way, category, app = (_filter_text(values, key) for key in ("way", "category", "app"))
    if way is not None and not way_options(way):
        errors.append(_query_issue("way", way, tuple(WAYS), "A declared activity way is required."))
    labels = tuple(row["category"] for row in observation["tool_layer"]["callable_index"]["query_categories"])
    if category is not None and category not in labels:
        errors.append(_query_issue("category", category, labels, "Use a category shown in callable_index."))
    if app is not None and app not in _app_names(observation):
        errors.append(_app_issue(observation, values))
    misplaced = _misplaced_app_issue(observation, values)
    if misplaced is not None:
        errors.append(misplaced)
    return errors


def _filter_text(values: dict, key: str) -> str | None:
    """@brief 类型正确的文本才进入动态检查，应用空白文本仍需报未知名称。"""
    value = values.get(key)
    if not isinstance(value, str) or (key != "app" and not value.strip()):
        return None
    return value


def _query_issue(field: str, written: Any, allowed: tuple, expected: str) -> ErrorItem:
    """@brief 查询明细使用相对参数位置，由宿主绑定本次真实工具调用根及下标。"""
    return ErrorItem(path=(field,), written=written, error_code="ARGUMENTS_INVALID", allowed_values=allowed,
                     expected=expected, why=_QUERY_WHY)


def _app_issue(observation: dict, values: dict) -> ErrorItem:
    """@brief 未知应用名返回至多三条只换应用字段的完整查询，不能把店铺当应用。"""
    names = _app_names(observation)
    close = near_names(values["app"], names)
    chosen = close if close else names[:MAX_CANDIDATES]
    candidates = tuple(Candidate("query_world", copy.deepcopy({**values, "app": name})) for name in chosen)
    return ErrorItem(path=("app",), written=values["app"], error_code="ARGUMENTS_INVALID", allowed_values=(),
                     expected=_APP_EXPECTED, why=_APP_WHY, candidates=candidates)


def _misplaced_app_issue(observation: dict, values: dict) -> ErrorItem | None:
    """@brief 应用误写为对象名称时只提供显式移动字段的完整查询，不替人物执行。"""
    name = _filter_text(values, "name")
    if name is None or "app" not in values or values["app"] is not None or name not in _app_names(observation):
        return None
    aliases = (alias for entry in observation["world_query_bindings"] for alias in entry["aliases"])
    if resolve_name(name, aliases).matches:
        return None
    moved = copy.deepcopy({**values, "name": None, "app": name})
    return ErrorItem(path=("name",), written=name, error_code="ARGUMENTS_INVALID", allowed_values=(),
                     expected=_APP_IN_NAME_EXPECTED, why=_QUERY_WHY, candidates=(Candidate("query_world", moved),))


def _app_names(observation: dict) -> tuple[str, ...]:
    """@brief 全部登记应用名按身份排序，包括仅导航或支付的应用。"""
    return tuple(row["name"] for _, row in sorted(observation["world_query_apps"].items()))


def _executable(match: dict, ways: frozenset[str] | None, app: str | None) -> bool:
    """@brief 筛了方式或应用时只读对应动作的冻结资格，不能用另一个应用的可用性代替。"""
    if ways is not None or app is not None:
        return any(action["available"] for action in match["actions"]
                   if (ways is None or action["way"] in ways) and (app is None or action["app"] == app))
    return any(row["available"] for row in (*match["actions"], *match["tools"]))


def _matching_entries(observation: dict, request: QueryWorldRequest) -> list[dict]:
    """@brief 所有筛选先取交集再按完整公开名及稳定身份排序，最后才分页。"""
    entries = observation["world_query_bindings"]
    names = None
    if request.name is not None:
        names = set(resolve_name(request.name, (name for entry in entries for name in entry["aliases"])).matches)
    ways = None if request.way is None else way_options(request.way)
    selected = [entry for entry in entries
                if (names is None or names.intersection(entry["aliases"]))
                and (request.category is None or entry["category"] == request.category)
                and (ways is None or any(action["way"] in ways for action in entry["match"]["actions"]))
                and (request.app is None or any(action["app"] == request.app for action in entry["match"]["actions"]))
                and (not request.available_only or _executable(entry["match"], ways, request.app))]
    return sorted(selected, key=lambda entry: (entry["match"]["name"], entry["identity"]))


def query_world(observation: dict, request: QueryWorldRequest) -> dict:
    """@brief 查询已验证的冻结观察页；每项是冻结目标的无损紧凑投影（只略去 null 与空集合，复用工具层分组的 compact），
    返回新结构，调用者不能改变后续页或权限事实。"""
    entries = _matching_entries(observation, request)
    end = min(request.offset + observation["world_query_page_size"], len(entries))
    return {"matches": [compact(entry["match"]) for entry in entries[request.offset:end]],
            "total_count": len(entries), "next_offset": end if end < len(entries) else None}
