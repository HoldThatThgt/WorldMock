"""@brief 搜索子会话提示词、严格工具 Schema、隔离用户消息与进度层搜索详情 Schema 验证。"""

from __future__ import annotations

import json

import pytest

from worldmock.agents.prompt.narration import FORBIDDEN_OBSERVATION_KEYS
from worldmock.agents.prompt.prompt_schemas import LAYER_SCHEMAS, USER_LAYER_NAMES, validate_layer
from worldmock.agents.search import search_prompt
from worldmock.agents.search.search_prompt import (
    SEARCH_SYSTEM_LAYER,
    SEARCH_TOOL_DESCRIPTIONS,
    search_prompt_sha256,
    search_system_message,
    search_tool_schema_sha256,
    search_tool_schemas,
)
from worldmock.state.decisions.barrier import SearchTaskSpec
from worldmock.state.interaction.search_requests import SEARCH_SESSION_MODELS


def _task() -> SearchTaskSpec:
    """@brief 构造只含搜索任务白名单字段的 SearchTaskSpec。"""
    return SearchTaskSpec(
        action_id="run:wake:100:v1:a1:action:0001",
        agent_id=1,
        activity_id="act-search-1",
        origin_wakeup_id="run:wake:100:v1:a1",
        query="找一家附近开着的咖啡店或便利店买冰美式",
        prefer="美团外卖",
        scoped_app_id="app-meituan",
        current_location_id="loc-home-1",
        location_name="自己家",
        opened_at_clock="09:15",
        local_date="2026-01-05",
        weather_label="晴",
        open_apps=("美团外卖", "大众点评"),
        category_overview={"饮食": 12, "日用": 5},
        turn_budget=6,
    )


def test_search_user_message_enforces_strict_context_isolation():
    """@brief 搜索子会话用户消息只含 SearchTaskSpec 公开字段，绝不泄漏内部定位 ID、人设、记忆与余额。"""
    msg = search_prompt.search_user_message(_task())
    assert msg["role"] == "user"
    decoded = json.loads(msg["content"])
    assert set(decoded) == {
        "query", "prefer", "scoped_app_id", "location_name", "opened_at_clock",
        "local_date", "weather_label", "open_apps", "category_overview", "turn_budget",
    }
    for secret in ("agent_id", "action_id", "activity_id", "origin_wakeup_id", "current_location_id", "balance_fen"):
        assert secret not in decoded
    assert search_system_message()["role"] == "system"
    assert json.loads(search_system_message()["content"]) == SEARCH_SYSTEM_LAYER


def test_search_tool_schemas_match_four_strict_models():
    """@brief 搜索子会话暴露四个 strict 原生工具，全部禁止违禁 JSON Schema 关键字。"""
    schemas = search_tool_schemas()
    assert [item["function"]["name"] for item in schemas] == list(SEARCH_SESSION_MODELS)
    assert set(SEARCH_TOOL_DESCRIPTIONS) == set(SEARCH_SESSION_MODELS)
    for schema in schemas:
        fn = schema["function"]
        assert schema["type"] == "function" and fn["strict"] is True
        raw = json.dumps(fn["parameters"], ensure_ascii=False)
        for banned in ('"default"', '"minLength"', '"maxLength"', '"minItems"', '"maxItems"', '"oneOf"'):
            assert banned not in raw


def test_search_prompt_and_tool_schema_fingerprints_react_to_changes(monkeypatch):
    """@brief 搜索系统层指纹与工具 Schema 指纹均为 64 位 SHA-256 并随定义改动改变。"""
    base_prompt = search_prompt_sha256()
    base_tools = search_tool_schema_sha256()
    assert len(base_prompt) == 64 and len(base_tools) == 64
    monkeypatch.setattr(search_prompt, "SEARCH_SYSTEM_LAYER", {**SEARCH_SYSTEM_LAYER, "role": "changed"})
    assert search_prompt_sha256() != base_prompt
    monkeypatch.setattr(search_prompt, "SEARCH_TOOL_DESCRIPTIONS", {**SEARCH_TOOL_DESCRIPTIONS, "search_goods": "改写。"})
    assert search_tool_schema_sha256() != base_tools


def test_progress_layer_schema_accepts_search_world_and_search_details_and_rejects_query():
    """@brief 进度层 Schema 支持 tool='search_world' 与 details.search（含商品行与地点行），并拒绝退场的 details.query。"""
    assert "continuation" not in FORBIDDEN_OBSERVATION_KEYS
    assert USER_LAYER_NAMES[-1] == "进度层"
    assert "search_world" in str(LAYER_SCHEMAS["进度层"])
    base_layer = {
        "layer": "进度层",
        "session_started_at": "2026-01-05T08:00:00+08:00",
        "rejected_today": [],
        "events": [{
            "occurred_at": "2026-01-05T09:20:00+08:00",
            "type": "tool_call",
            "tool": "search_world",
            "arguments": {"query": "咖啡", "prefer": None},
            "result": {
                "occurred_at": "2026-01-05T09:20:00+08:00",
                "status": "completed",
                "error_code": None,
                "message": "已完成搜索。",
                "details": {
                    "search": {
                        "app": None,
                        "scope": "列出全部途径",
                        "goods_rows": [{
                            "product": "冰美式",
                            "route": "外卖",
                            "store": "街角咖啡",
                            "rating": 4.7,
                            "apps": ["美团外卖"],
                            "price": "18 元",
                            "delivery_fee": "3 元",
                            "expected_at": "2026-01-05T09:50:00+08:00",
                            "now": "可以下单",
                            "reason": None,
                        }],
                        "place_rows": [{
                            "name": "街角咖啡",
                            "kind": "cafe",
                            "open_now": True,
                            "today_hours": ["08:00-22:00"],
                            "rating": 4.7,
                            "units": ["堂食休息"],
                            "travel_options": ["步行 8 分钟"],
                        }],
                        "summary": "推荐街角咖啡冰美式。",
                    },
                },
            },
        }],
    }
    validate_layer(base_layer)
    incomplete = {**base_layer, "events": [{
        **base_layer["events"][0],
        "result": {**base_layer["events"][0]["result"], "details": {"search": {"app": None, "state": "没有查完"}}},
    }]}
    validate_layer(incomplete)
    retired_query = {**base_layer, "events": [{
        **base_layer["events"][0],
        "result": {**base_layer["events"][0]["result"], "details": {"query": {"app": None, "state": "没有查完"}}},
    }]}
    with pytest.raises(ValueError, match="violates its schema"):
        validate_layer(retired_query)
