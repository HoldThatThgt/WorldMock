"""SearchSessionRunner 提示词隔离契约、禁思考参数、四工具严格 Schema 与 search_details 投影测试。"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, decide, resolved_binding  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse  # noqa: E402
from tests.life.conftest import MAIN_TOOL, advance_next, main_arguments, stage_day_plan  # noqa: E402
from worldmock.agents.search.search_prompt import (  # noqa: E402
    SEARCH_SYSTEM_LAYER,
    search_prompt_sha256,
    search_system_message,
    search_tool_schema_sha256,
    search_tool_schemas,
    search_user_message,
)
from worldmock.agents.search.search_session import SearchSessionRunner  # noqa: E402
from worldmock.simulation_engine.observation.layers.search_projection import (  # noqa: E402
    ALL_ROUTES,
    SCOPED,
    UNFINISHED,
    search_details,
)
from worldmock.state.interaction.actions import SEARCH_TOOL  # noqa: E402
from worldmock.state.model.exchange import SEARCH_SESSION_PURPOSE  # noqa: E402


def test_search_tool_schemas_are_strict_and_hide_internal_identities() -> None:
    """搜索子会话开放四个严格原生工具，additionalProperties=False 且不泄露私有环境身份枚举。"""
    schemas = search_tool_schemas()
    assert [item["function"]["name"] for item in schemas] == [
        "search_goods", "inspect_goods_offers", "search_places", "submit_search_results",
    ]
    for item in schemas:
        fn = item["function"]
        assert fn["strict"] is True
        params = fn["parameters"]
        assert params["additionalProperties"] is False
        assert {"agent_id", "wakeup_id", "activity_id"} .isdisjoint(params["properties"])
    assert len(search_prompt_sha256()) == 64
    assert search_tool_schema_sha256() == search_tool_schema_sha256()


@pytest.mark.asyncio
async def test_search_session_prompt_is_strictly_isolated_with_thinking_disabled(engine_factory) -> None:
    """SearchSessionRunner 请求彻底隔离人设、记忆、余额、余额账户与同城其他人物，且 thinking=disabled。"""
    engine = engine_factory()
    stage_day_plan(engine)
    secret_memory = "昨晚在日记里写下绝不外传的人设习惯秘密"
    # 发起真实 search_world 活动并推至到期
    await decide(engine, "ask-search", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "牛肉面", "prefer": "美团"}, ensure_ascii=False)),
    ])
    while not engine.due_searches():
        advance_next(engine)
    [(task, catalog)] = engine.due_searches()

    model = ScriptedModel([
        _ModelResponse("s-1", "search_goods", json.dumps({
            "keywords": ["牛肉面"], "category": None, "app": None, "store": None,
            "max_price_yuan": None, "open_only": False, "limit": 20, "offset": 0,
        }, ensure_ascii=False)),
        _ModelResponse("s-2", "submit_search_results", json.dumps({
            "goods_ids": ["beef-noodles"], "place_ids": ["noodle-1"], "summary": "推荐一心面馆牛肉面。",
        }, ensure_ascii=False)),
    ])
    outcome = await SearchSessionRunner().run(task, catalog, resolved_binding(model, frozen=engine.context.inputs,
                                                                              stage="search"))
    assert len(model.requests) == 2 and len(model.requests) <= task.turn_budget
    first, second = model.requests
    assert first["extra_body"] == {"thinking": {"type": "disabled"}}
    assert second["extra_body"] == {"thinking": {"type": "disabled"}}
    assert [t["function"]["name"] for t in first["tools"]] == [
        "search_goods", "inspect_goods_offers", "search_places", "submit_search_results",
    ]
    assert first["messages"][0] == search_system_message()
    assert first["messages"][1] == search_user_message(task)
    user_payload = json.loads(first["messages"][1]["content"])
    assert set(user_payload) == {
        "query", "prefer", "scoped_app_id", "location_name", "opened_at_clock",
        "local_date", "weather_label", "open_apps", "category_overview", "turn_budget",
    }
    wire_text = json.dumps(model.requests, ensure_ascii=False)
    for forbidden in (
        secret_memory, "balance_fen", "agent:1", task.origin_wakeup_id, task.activity_id, task.action_id,
        engine.context.inputs.persona_by_id[1].name, "人设层", "记忆层", "待办层", "进度层",
    ):
        assert forbidden not in wire_text
    # 模型证据信封必须标明 purpose="search_session"、确定性 search call_id 及所属 activity_id
    req_drafts = [d for d in outcome.evidence_drafts if d.event_type == "model_request"]
    resp_drafts = [d for d in outcome.evidence_drafts if d.event_type == "model_response"]
    assert [(d.data["call_id"], d.data["purpose"], d.activity_id, d.wakeup_id) for d in req_drafts] == [
        (f"{task.action_id}:search:0001:attempt:1", SEARCH_SESSION_PURPOSE, task.activity_id, task.origin_wakeup_id),
        (f"{task.action_id}:search:0002:attempt:1", SEARCH_SESSION_PURPOSE, task.activity_id, task.origin_wakeup_id),
    ]
    assert all(d.data["purpose"] == SEARCH_SESSION_PURPOSE and d.activity_id == task.activity_id for d in resp_drafts)


@pytest.mark.asyncio
async def test_last_turn_narrows_search_tools_to_submit_only(engine_factory) -> None:
    """只剩最后 1 个搜索回合时自动收窄工具列表，仅保留 submit_search_results。"""
    engine = engine_factory()
    stage_day_plan(engine)
    await decide(engine, "s", [_ModelResponse("c-sw", SEARCH_TOOL, '{"query":"芯片","prefer":null}')])
    while not engine.due_searches():
        advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    task_two = task.model_copy(update={"turn_budget": 2})
    model = ScriptedModel([
        _ModelResponse("s-1", "search_goods", json.dumps({
            "keywords": ["薯片"], "category": None, "app": None, "store": None,
            "max_price_yuan": None, "open_only": False, "limit": 10, "offset": 0,
        }, ensure_ascii=False)),
        _ModelResponse("s-2", "submit_search_results", json.dumps({
            "goods_ids": ["chips"], "place_ids": [], "summary": "找到薯片。",
        }, ensure_ascii=False)),
    ])
    await SearchSessionRunner().run(task_two, catalog, resolved_binding(model, frozen=engine.context.inputs, stage="search"))
    assert [t["function"]["name"] for t in model.requests[1]["tools"]] == [
        "search_goods", "inspect_goods_offers", "search_places", "submit_search_results",
    ]


def SearchTaskSpec_with_budget(task, budget: int):
    from dataclasses import replace

    return replace(task, turn_budget=budget)


@pytest.mark.asyncio
async def test_search_details_projection_unfinished_and_completed_shapes(engine_factory) -> None:
    """未完成搜索投影出状态「没有查完」，完成搜索投影出范围、应用、商品报价与地点交通选项。"""
    engine = engine_factory()
    stage_day_plan(engine)
    await decide(engine, "proj", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "面条", "prefer": "美团"}, ensure_ascii=False)),
    ])
    act = next(iter(engine.context.state.activities.values()))
    unfinished = search_details(act.model_dump(mode="json"), engine.context.inputs)
    assert unfinished == {"app": "美团", "state": UNFINISHED}

    while not engine.due_searches():
        advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    model = ScriptedModel([
        _ModelResponse("s-1", "submit_search_results", json.dumps({
            "goods_ids": ["beef-noodles"], "place_ids": ["noodle-1"], "summary": "美团可下单牛肉面。",
        }, ensure_ascii=False)),
    ])
    out = await SearchSessionRunner().run(task, catalog, resolved_binding(model, frozen=engine.context.inputs, stage="search"))
    engine.complete_searches([out])
    completed_act = engine.context.state.activities[act.activity_id]
    details = search_details(completed_act.model_dump(mode="json"), engine.context.inputs)
    assert details["scope"] == SCOPED and details["app"] == "美团"
    assert details["summary"] == "美团可下单牛肉面。"
    assert details["goods_rows"][0]["product"] == "牛肉面" and details["goods_rows"][0]["price"] == "28 元"
    assert details["place_rows"][0]["name"] == "一心面馆" and details["place_rows"][0]["travel_options"]
    assert isinstance(details["place_rows"][0]["today_hours"], list)
    assert ALL_ROUTES != SCOPED
