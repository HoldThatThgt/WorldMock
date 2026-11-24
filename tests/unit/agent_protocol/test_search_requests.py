"""搜索子会话四个内部工具的严格请求模型、DeepSeek strict Schema 与本地校验契约。"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from worldmock.state.interaction.request_errors import argument_report
from worldmock.state.interaction.schema import deepseek_strict_schema
from worldmock.state.interaction.search_requests import (
    INSPECT_GOODS_MAX,
    SEARCH_PAGE_LIMIT_MAX,
    SEARCH_SESSION_MODELS,
    SUBMIT_GOODS_MAX,
    SUBMIT_PLACES_MAX,
    SUBMIT_SUMMARY_MAX_CHARS,
    InspectGoodsOffersRequest,
    SearchGoodsRequest,
    SearchPlacesRequest,
    SubmitSearchResultsRequest,
)

_FORBIDDEN_SCHEMA_TOKENS = ("$ref", '"minLength"', '"maxLength"', '"minItems"', '"maxItems"', '"oneOf"', '"default"')

_VALID_PAYLOADS = {
    "search_goods": (
        {
            "keywords": ["无糖乌龙茶", "乌龙茶"],
            "category": "饮料",
            "app": "美团",
            "store": None,
            "max_price_yuan": 12.5,
            "open_only": True,
            "limit": 20,
            "offset": 0,
        },
        {
            "keywords": ["便当"],
            "category": None,
            "app": None,
            "store": "7-Eleven",
            "max_price_yuan": None,
            "open_only": False,
            "limit": SEARCH_PAGE_LIMIT_MAX,
            "offset": 20,
        },
    ),
    "inspect_goods_offers": (
        {"goods_ids": ["goods-1"]},
        {"goods_ids": [f"goods-{idx}" for idx in range(INSPECT_GOODS_MAX)]},
    ),
    "search_places": (
        {"keywords": ["咖啡"], "kind": None, "open_only": True, "limit": 10, "offset": 0},
        {"keywords": [], "kind": "餐饮店", "open_only": False, "limit": 15, "offset": 5},
    ),
    "submit_search_results": (
        {"goods_ids": [], "place_ids": [], "summary": "当前无符合预算的商品。"},
        {
            "goods_ids": [f"g-{idx}" for idx in range(SUBMIT_GOODS_MAX)],
            "place_ids": [f"p-{idx}" for idx in range(SUBMIT_PLACES_MAX)],
            "summary": "合" * SUBMIT_SUMMARY_MAX_CHARS,
        },
    ),
}


def test_search_session_models_strict_schema_and_valid_samples() -> None:
    """四个搜索内部工具均满足 DeepSeek strict JSON Schema，且合法载荷通过验证。"""
    assert list(SEARCH_SESSION_MODELS) == [
        "search_goods", "inspect_goods_offers", "search_places", "submit_search_results",
    ]
    for tool_name, model in SEARCH_SESSION_MODELS.items():
        schema = deepseek_strict_schema(model)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])
        assert "description" not in schema
        serialized = json.dumps(schema, ensure_ascii=False)
        for forbidden in _FORBIDDEN_SCHEMA_TOKENS:
            assert forbidden not in serialized, f"{tool_name} schema must not contain {forbidden}"
        validator = Draft202012Validator(schema)
        for sample in _VALID_PAYLOADS[tool_name]:
            assert list(validator.iter_errors(sample)) == []
            parsed = model.model_validate(sample)
            assert parsed.model_dump(mode="json") == sample


def _report_whys(model, tool: str, arguments: dict) -> list[tuple[tuple, str]]:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(arguments)
    report = argument_report(tool, caught.value, model, arguments)
    return [(item.path, item.why) for item in report.items]


@pytest.mark.parametrize("changes,expected_path,expected_text", [
    ({"keywords": []}, ("keywords",), "keywords must contain at least one search term"),
    ({"keywords": [" "]}, ("keywords", 0), "A non-blank string matching the declared format is required."),
    ({"category": ""}, ("category",), "A non-blank string matching the declared format is required."),
    ({"app": " "}, ("app",), "A non-blank string matching the declared format is required."),
    ({"store": "\t"}, ("store",), "A non-blank string matching the declared format is required."),
    ({"max_price_yuan": 0}, ("max_price_yuan",), "max_price_yuan must be a finite positive number"),
    ({"max_price_yuan": -5.0}, ("max_price_yuan",), "max_price_yuan must be a finite positive number"),
    ({"max_price_yuan": True}, ("max_price_yuan",), "A JSON number is required."),
    ({"max_price_yuan": float("nan")}, ("max_price_yuan",), "max_price_yuan must be a finite positive number"),
    ({"max_price_yuan": float("inf")}, ("max_price_yuan",), "max_price_yuan must be a finite positive number"),
    ({"limit": 0}, ("limit",), "limit must be between 1 and 50"),
    ({"limit": 51}, ("limit",), "limit must be between 1 and 50"),
    ({"offset": -1}, ("offset",), "offset must be non-negative"),
])
def test_search_goods_rejects_invalid_filters_and_bounds(changes, expected_path, expected_text) -> None:
    """search_goods 本地校验覆盖关键词非空、正价格有限值及分页边界。"""
    payload = {**_VALID_PAYLOADS["search_goods"][0], **changes}
    errors = _report_whys(SearchGoodsRequest, "search_goods", payload)
    assert errors == [(expected_path, expected_text)]


@pytest.mark.parametrize("changes,expected_text", [
    ({"goods_ids": []}, "goods_ids must contain between 1 and 20 items"),
    ({"goods_ids": [f"g-{idx}" for idx in range(21)]}, "goods_ids must contain between 1 and 20 items"),
    ({"goods_ids": ["g-1", "g-1"]}, "goods_ids must contain distinct identifiers"),
])
def test_inspect_goods_offers_rejects_empty_overflow_and_duplicates(changes, expected_text) -> None:
    """inspect_goods_offers 严格要求 1 至 20 个互不重复商品编号。"""
    errors = _report_whys(InspectGoodsOffersRequest, "inspect_goods_offers", changes)
    assert errors == [(("goods_ids",), expected_text)]


@pytest.mark.parametrize("changes,expected_text", [
    ({"keywords": [], "kind": None}, "search_places requires at least one keyword or kind"),
    ({"limit": 0}, "limit must be between 1 and 50"),
    ({"limit": 51}, "limit must be between 1 and 50"),
    ({"offset": -2}, "offset must be non-negative"),
])
def test_search_places_requires_filter_and_valid_pagination(changes, expected_text) -> None:
    """search_places 禁止 keywords 与 kind 同时为空，并约束分页边界。"""
    payload = {**_VALID_PAYLOADS["search_places"][0], **changes}
    with pytest.raises(ValidationError, match=expected_text):
        SearchPlacesRequest.model_validate(payload)


@pytest.mark.parametrize("changes,expected_path,expected_text", [
    ({"goods_ids": [f"g-{idx}" for idx in range(21)]}, ("goods_ids",), "goods_ids must contain between 0 and 20 items"),
    ({"goods_ids": ["g-1", "g-1"]}, ("goods_ids",), "goods_ids must contain distinct identifiers"),
    ({"place_ids": [f"p-{idx}" for idx in range(11)]}, ("place_ids",), "place_ids must contain between 0 and 10 items"),
    ({"place_ids": ["p-1", "p-1"]}, ("place_ids",), "place_ids must contain distinct identifiers"),
    ({"summary": "   "}, ("summary",), "A non-blank string matching the declared format is required."),
    ({"summary": "字" * 301}, ("summary",), "summary must be within 300 characters"),
])
def test_submit_search_results_enforces_limits_and_uniqueness(changes, expected_path, expected_text) -> None:
    """submit_search_results 限制提交数量、去重标识和 300 字总结上限。"""
    payload = {**_VALID_PAYLOADS["submit_search_results"][0], **changes}
    errors = _report_whys(SubmitSearchResultsRequest, "submit_search_results", payload)
    assert errors == [(expected_path, expected_text)]


def test_search_requests_require_all_fields_and_reject_extra_keys() -> None:
    """四个搜索内部工具无默认参数且严格禁止多余键。"""
    for tool_name, model in SEARCH_SESSION_MODELS.items():
        sample = _VALID_PAYLOADS[tool_name][0]
        for field in sample:
            missing = {key: value for key, value in sample.items() if key != field}
            assert any(path == (field,) for path, _ in _report_whys(model, tool_name, missing))
        extra = {**sample, "unexpected": 1}
        assert any(path == ("unexpected",) for path, _ in _report_whys(model, tool_name, extra))
