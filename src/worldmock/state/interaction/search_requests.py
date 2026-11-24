"""@brief 搜索子会话四个内部工具的严格请求模型与本地校验。"""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator

from worldmock.state.interaction.schema import ToolRequestModel
from worldmock.state.logging import _LOGGER

SEARCH_PAGE_LIMIT_MAX = 50
INSPECT_GOODS_MAX = 20
SUBMIT_GOODS_MAX = 20
SUBMIT_PLACES_MAX = 10
SUBMIT_SUMMARY_MAX_CHARS = 300


def _validate_limit(value: int) -> int:
    """@brief 分页条数上限统一限制在 1 至 50。"""
    if not 1 <= value <= SEARCH_PAGE_LIMIT_MAX:
        _LOGGER.error("Search page limit %d is outside [1, %d]", value, SEARCH_PAGE_LIMIT_MAX)
        raise ValueError(f"limit must be between 1 and {SEARCH_PAGE_LIMIT_MAX}")
    return value


def _validate_offset(value: int) -> int:
    """@brief 分页偏移量必须为非负整数。"""
    if value < 0:
        _LOGGER.error("Search offset %d cannot be negative", value)
        raise ValueError("offset must be non-negative")
    return value


def _validate_distinct_ids(values: list[str], minimum: int, maximum: int, label: str) -> list[str]:
    """@brief 校验标识列表的数量上下界与唯一性。"""
    if not minimum <= len(values) <= maximum:
        _LOGGER.error("Search %s count %d is outside [%d, %d]", label, len(values), minimum, maximum)
        raise ValueError(f"{label} must contain between {minimum} and {maximum} items")
    if len(set(values)) != len(values):
        _LOGGER.error("Search %s contains duplicate identifiers", label)
        raise ValueError(f"{label} must contain distinct identifiers")
    return values


class SearchGoodsRequest(ToolRequestModel):
    """@brief 按多近义关键词与可选条件分页检索当前视野内的可售商品。"""

    keywords: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="搜索商品名称或品类的关键词列表。")
    category: Annotated[str, Field(pattern=r"\S")] | None = Field(description="限定商品品类，不限写 null。")
    app: Annotated[str, Field(pattern=r"\S")] | None = Field(description="限定售卖应用名称或标识，不限写 null。")
    store: Annotated[str, Field(pattern=r"\S")] | None = Field(description="限定店铺名称或标识，不限写 null。")
    max_price_yuan: float | None = Field(description="单价上限（元），不限写 null。")
    open_only: bool = Field(description="是否只看此刻营业或可下单的商品。")
    limit: int = Field(description="本页返回条数。")
    offset: int = Field(description="分页起始偏移量。")

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, value: list[str]) -> list[str]:
        """@brief 至少提供一个非空白检索关键词。"""
        if not value:
            _LOGGER.error("SearchGoodsRequest keywords cannot be empty")
            raise ValueError("keywords must contain at least one search term")
        return value

    @field_validator("max_price_yuan")
    @classmethod
    def validate_max_price_yuan(cls, value: float | None) -> float | None:
        """@brief 价格上限非空时必须是有限正数。"""
        if value is not None and (not math.isfinite(value) or value <= 0):
            _LOGGER.error("SearchGoodsRequest max_price_yuan is invalid: %r", value)
            raise ValueError("max_price_yuan must be a finite positive number")
        return value

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, value: int) -> int:
        """@brief 核验商品检索分页条数范围。"""
        return _validate_limit(value)

    @field_validator("offset")
    @classmethod
    def validate_offset(cls, value: int) -> int:
        """@brief 核验商品检索分页偏移量非负。"""
        return _validate_offset(value)


class InspectGoodsOffersRequest(ToolRequestModel):
    """@brief 批量试算候选商品的实时价格、运费、营业与送达报价。"""

    goods_ids: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="要试算实时报价的商品编号列表。")

    @field_validator("goods_ids")
    @classmethod
    def validate_goods_ids(cls, value: list[str]) -> list[str]:
        """@brief 校验 1 至 20 个互不重复的非空白商品编号。"""
        return _validate_distinct_ids(value, 1, INSPECT_GOODS_MAX, "goods_ids")


class SearchPlacesRequest(ToolRequestModel):
    """@brief 按关键词或地点类别检索同城地点与营业、出行摘要。"""

    keywords: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="搜索地点名称或内容的关键词列表。")
    kind: Annotated[str, Field(pattern=r"\S")] | None = Field(description="限定地点类别，不限写 null。")
    open_only: bool = Field(description="是否只看此刻营业中的地点。")
    limit: int = Field(description="本页返回条数。")
    offset: int = Field(description="分页起始偏移量。")

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, value: int) -> int:
        """@brief 核验地点检索分页条数范围。"""
        return _validate_limit(value)

    @field_validator("offset")
    @classmethod
    def validate_offset(cls, value: int) -> int:
        """@brief 核验地点检索分页偏移量非负。"""
        return _validate_offset(value)

    @model_validator(mode="after")
    def validate_filter_present(self) -> SearchPlacesRequest:
        """@brief 要求 keywords 与 kind 至少提供一个有效过滤条件。"""
        if not self.keywords and self.kind is None:
            _LOGGER.error("SearchPlacesRequest requires keywords or kind")
            raise ValueError("search_places requires at least one keyword or kind")
        return self


class SubmitSearchResultsRequest(ToolRequestModel):
    """@brief 提交经过白名单验证的最终候选商品与地点标识并结束搜索子会话。"""

    goods_ids: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="最终入选的商品编号列表。")
    place_ids: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="最终入选的地点编号列表。")
    summary: str = Field(pattern=r"\S", description="简短说明检索结果或未找到原因。")

    @field_validator("goods_ids")
    @classmethod
    def validate_goods_ids(cls, value: list[str]) -> list[str]:
        """@brief 校验至多 20 个互不重复的商品编号（可为空）。"""
        return _validate_distinct_ids(value, 0, SUBMIT_GOODS_MAX, "goods_ids")

    @field_validator("place_ids")
    @classmethod
    def validate_place_ids(cls, value: list[str]) -> list[str]:
        """@brief 校验至多 10 个互不重复的地点编号（可为空）。"""
        return _validate_distinct_ids(value, 0, SUBMIT_PLACES_MAX, "place_ids")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        """@brief 检索说明去掉首尾空白后非空且不超过 300 字。"""
        if len(value.strip()) > SUBMIT_SUMMARY_MAX_CHARS:
            _LOGGER.error("SubmitSearchResultsRequest summary exceeds %d characters", SUBMIT_SUMMARY_MAX_CHARS)
            raise ValueError(f"summary must be within {SUBMIT_SUMMARY_MAX_CHARS} characters")
        return value


SEARCH_SESSION_MODELS: dict[str, type[ToolRequestModel]] = {
    "search_goods": SearchGoodsRequest,
    "inspect_goods_offers": InspectGoodsOffersRequest,
    "search_places": SearchPlacesRequest,
    "submit_search_results": SubmitSearchResultsRequest,
}
