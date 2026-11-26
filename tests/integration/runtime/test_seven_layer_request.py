"""实际宿主请求的系统消息、六个用户层和原生工具具有一致身份。"""
import hashlib
import json
import pytest
from tests.engine_fixture import engine_factory
from tests.life.conftest import MAIN_TOOL, advance_until_ready, conclude_request, main_arguments, stage_day_plan
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from tests.unit.start_interfaces.test_system_layer_text import _RULES
from worldmock.agents.prompt.life_prompt import LIFE_SYSTEM_LAYER, system_message, life_prompt_sha256, user_layer
from worldmock.agents.prompt.narration import render_layer_json
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.agents.life_agent import life_tool_schema_sha256
from worldmock.configuration.model.model_client import REQUEST_KEYS
from worldmock.foundation.serialization import canonical_json

#: 生活决策的 11 个原生工具。
_NATIVE_TOOLS = (
    "start_main_activity", "start_side_activity", "continue_activity", "report_status", "pause_activity",
    "conclude_activity", "revise_day_plan", "search_world", "recall_task_summary", "search_memory", "lookup_targets",
)


@pytest.mark.asyncio
async def test_current_host_request_has_one_system_layer_and_six_user_layers(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    observation, [request], _, _ = await decide(engine, "layers", [
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试休息"))])
    assert request["messages"][0] == system_message()
    rules = json.loads(request["messages"][0]["content"])["rules"]
    assert rules and rules == LIFE_SYSTEM_LAYER["rules"] == list(_RULES)
    layers = json.loads(request["messages"][1]["content"])
    assert [row["layer"] for row in layers] == ["人设层", "记忆层", "待办层", "工具层", "环境层", "进度层"]
    for layer in layers:
        validate_layer(layer)
    assert list(user_layer(layers, "环境层")["body"]) == ["appetite", "sleepiness", "relaxation", "social"]
    from worldmock.agents.prompt.tool_groups import expand_tool_layer
    assert expand_tool_layer(user_layer(layers, "工具层")) == observation["tool_layer"]
    assert sorted(request) == sorted(REQUEST_KEYS)
    assert len(request["tools"]) == 11
    from worldmock.agents.life_agent import _FULL_MODELS
    assert set(_FULL_MODELS) == set(_NATIVE_TOOLS)
    assert tuple(row["function"]["name"] for row in request["tools"]) == tuple(_FULL_MODELS)
    schemas = {row["function"]["name"]: row for row in request["tools"]}
    ordered = [schemas[name] for name in _FULL_MODELS]
    assert life_tool_schema_sha256() == hashlib.sha256(canonical_json({"tool_schemas": ordered})).hexdigest()


@pytest.mark.asyncio
async def test_consecutive_fresh_requests_share_the_prefix_through_today(engine_factory):
    """同一天里先在家开始歇一刻钟、推进到它结束再收尾：两次唤醒复用同一日会话，第二次请求与第一次在 [system, user]
    完全逐字节相同，仅在末尾追加上一次的 assistant 边界调用与配对 role: "tool" 环境差量（含 记忆层.今天_append）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, [first], _, [started] = await decide(engine, "start", [
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试休息"), call_id="c-start-1")])
    assert started.status == "in_progress"
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    assert activity.status != "active"
    _, [second, _], _, [concluded, _] = await decide(engine, "conclude", [
        ToolResponse("conclude_activity", conclude_request(activity.name).model_dump(mode="json")),
        ToolResponse(MAIN_TOOL, main_arguments("sleep", "60"), "call-sleep")])
    assert concluded.status == "completed"
    assert [item["role"] for item in first["messages"]] == ["system", "user"]
    assert [item["role"] for item in second["messages"]] == ["system", "user", "assistant", "tool"]
    assert second["messages"][:2] == first["messages"]
    assert second["messages"][2]["tool_calls"][0]["id"] == "c-start-1"
    delta = json.loads(second["messages"][3]["content"])
    assert delta["ok"] is True and "boundary" in delta and "记忆层" in delta["changes"]
    assert "今天_append" in delta["changes"]["记忆层"]


@pytest.mark.parametrize("tool", _NATIVE_TOOLS)
def test_tool_identity_covers_each_of_the_eleven_native_tool_descriptions(monkeypatch, tool):
    """工具指纹恰好覆盖 11 个原生工具：改动其中任何一个的用途句都改变工具指纹，提示指纹不变；复原后两个指纹都能重算
    回原值。"""
    from worldmock.agents import life_agent
    from worldmock.agents.prompt import life_prompt
    assert len(life_agent._FULL_MODELS) == 11 and tool in life_agent._FULL_MODELS
    tools, prompt = life_tool_schema_sha256(), life_prompt_sha256()
    assert life_agent.TOOL_DESCRIPTIONS is life_prompt.TOOL_DESCRIPTIONS
    with monkeypatch.context() as patch:
        patch.setitem(life_prompt.TOOL_DESCRIPTIONS, tool, life_prompt.TOOL_DESCRIPTIONS[tool] + "探针")
        assert life_tool_schema_sha256() != tools
        assert life_prompt_sha256() == prompt
    assert (life_tool_schema_sha256(), life_prompt_sha256()) == (tools, prompt)


def test_prompt_identity_changes_when_system_layer_text_changes(monkeypatch):
    """系统层任何一条 rules 的文字改变都改变提示指纹，工具指纹不变。"""
    from worldmock.agents.prompt import life_prompt
    prompt, tools = life_prompt_sha256(), life_tool_schema_sha256()
    rules = life_prompt.LIFE_SYSTEM_LAYER["rules"]
    for index, rule in enumerate(rules):
        changed = {**life_prompt.LIFE_SYSTEM_LAYER, "rules": [*rules[:index], rule + "探针", *rules[index + 1:]]}
        with monkeypatch.context() as patch:
            patch.setattr(life_prompt, "LIFE_SYSTEM_LAYER", changed)
            assert life_prompt_sha256() != prompt
            assert life_tool_schema_sha256() == tools
    assert (life_prompt_sha256(), life_tool_schema_sha256()) == (prompt, tools)


def test_prompt_identity_changes_when_actual_layer_schema_changes(monkeypatch):
    from worldmock.agents.prompt import life_prompt
    original = life_prompt_sha256()
    schemas = __import__('worldmock.agents.prompt.prompt_schemas', fromlist=["LAYER_SCHEMAS"]).LAYER_SCHEMAS
    changed = {**schemas, "probe": {"type": "object"}}
    monkeypatch.setattr(life_prompt, "LAYER_SCHEMAS", changed)
    assert life_prompt_sha256() != original
