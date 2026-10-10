"""@brief 冻结世界的只读交集查询，不产生动作或随机抽取。"""

from __future__ import annotations
import copy
import logging
from dataclasses import replace
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from worldmock.exchange.types import GOODS_CATEGORY_LABELS
from worldmock.life.action_contract import ACTIVITY_ACTIONS
from worldmock.life.callable_index import query_category_index
from worldmock.life.discovery import product_bindings
from worldmock.life.error_contract import ErrorItem, ErrorReport
from worldmock.life.models import purpose_only_schema
from worldmock.life.object_labels import resolve_name
from worldmock.life.request_errors import argument_report
from worldmock.life.tool_groups import compact
from worldmock.life.tool_layer import tool_layer, with_bindings

_FILTER_KEYS = ("name", "action", "category")


class QueryWorldRequest(BaseModel):
    """@brief 三种筛选取交集，五个字段必填且拒绝旧 way 与 app。"""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, json_schema_extra=purpose_only_schema())
    name: str | None
    action: str | None
    category: str | None
    available_only: bool
    offset: int = Field(ge=0)

    @field_validator("name", "action", "category")
    @classmethod
    def nonblank_filter(cls, value: str | None) -> str | None:
        """@brief 非空筛选必须含实际字符。"""
        if value is not None and not value.strip():
            logging.error("World query filter is blank")
            raise ValueError("A non-null filter must contain a non-whitespace character")
        return value

    @model_validator(mode="after")
    def require_filter(self):
        """@brief 至少一个字符串筛选必须非空。"""
        if all(getattr(self, key) is None for key in _FILTER_KEYS):
            logging.error("World query has no filter")
            raise ValueError("At least one string filter is required")
        return self


def attach_world_query(observation: dict, context: Any) -> None:
    """@brief 地点、内容 app、完整地点侧商品与活动组成唯一冻结查询集合。"""
    rows = {row["object"]: copy.deepcopy(row) for row in observation["catalog_bindings"]}
    rows.update({row["object"]: row for row in product_bindings(context)})
    complete = tool_layer(with_bindings(context, list(rows.values())), {})["targets"]
    entries = []
    for target in complete:
        binding = rows.get(target["name"])
        goods = None if binding is None or binding["kind"] != "supply" else context.catalog.goods[binding["goods_id"]]
        entries.append({"identity": target["name"], "aliases": [target["name"]],
                        "category": None if goods is None else GOODS_CATEGORY_LABELS[goods.category],
                        "match": target})
    observation["world_query_bindings"] = entries
    observation["world_query_page_size"] = context.config.candidate_presentation_limit
    observation["tool_layer"]["callable_index"]["query_categories"] = query_category_index(entries)


def _filter_issues(values: dict) -> list[ErrorItem]:
    """@brief 动作与商品类别独立校验，不为容器推断内容类别。"""
    issues = []
    for key, allowed in (("action", ACTIVITY_ACTIONS), ("category", tuple(GOODS_CATEGORY_LABELS.values()))):
        value = values.get(key)
        if isinstance(value, str) and value.strip() and value not in allowed:
            issues.append(ErrorItem((key,), value, "ARGUMENTS_INVALID", allowed,
                                    "A declared query filter value is required.",
                                    "The submitted value is outside the current query contract."))
    return issues


def validate_world_query(observation: dict, request: QueryWorldRequest) -> ErrorReport | None:
    """@brief 有效交集可以为空，偏移量允许结果末尾。"""
    errors = _filter_issues(request.model_dump(mode="json"))
    if not errors:
        total = len(_matching_entries(observation, request))
        if request.offset > total:
            errors.append(ErrorItem(("offset",), request.offset, "ARGUMENTS_INVALID",
                                    expected=f"An integer in the inclusive range 0..{total}.",
                                    why="The offset is outside the result set."))
    if not errors:
        return None
    logging.error("World query arguments violate the frozen contract")
    return ErrorReport("ARGUMENTS_INVALID", "World query arguments violate the frozen public contract.",
                       "Correct all listed arguments before submitting the query again.", tuple(errors))


def query_argument_report(observation: dict, error: ValidationError, arguments: Any) -> ErrorReport:
    """@brief 结构错误与仍可确定的筛选错误同批返回。"""
    report = argument_report("query_world", error, QueryWorldRequest, arguments)
    issues = (*report.items, *_filter_issues(arguments if isinstance(arguments, dict) else {}))
    return replace(report, reason="; ".join(item.why for item in issues), items=issues)


def _matching_entries(observation: dict, request: QueryWorldRequest) -> list[dict]:
    """@brief 筛选先取交集，按完整名称稳定排序，最后分页。"""
    entries = observation["world_query_bindings"]
    names = None if request.name is None else set(resolve_name(
        request.name, (name for entry in entries for name in entry["aliases"])).matches)
    selected = []
    for entry in entries:
        actions = [row for row in entry["match"]["actions"]
                   if request.action is None or row["action"] == request.action]
        if names is not None and not names.intersection(entry["aliases"]):
            continue
        if request.category is not None and request.category != entry["category"]:
            continue
        if request.action is not None and not actions:
            continue
        tools = entry["match"]["tools"] if request.action is None else []
        if request.available_only and not any(row["available"] for row in (*actions, *tools)):
            continue
        selected.append(entry)
    return sorted(selected, key=lambda entry: (entry["match"]["name"], entry["identity"]))


def query_world(observation: dict, request: QueryWorldRequest) -> dict:
    """@brief 新返回结构不能更改冻结事实或后续页。"""
    entries = _matching_entries(observation, request)
    end = min(request.offset + observation["world_query_page_size"], len(entries))
    return {"matches": [compact(entry["match"]) for entry in entries[request.offset:end]],
            "total_count": len(entries), "next_offset": end if end < len(entries) else None}
