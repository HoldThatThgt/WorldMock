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
from custom.agents.life_agent import life_tool_schema_sha256
from worldmock.foundation.serialization import canonical_json

#: 生活决策的八个原生工具，按计算工具指纹的次序：七个请求工具（两个开始工具在前）与 search_memory。
_NATIVE_TOOLS = ("start_main_activity", "start_side_activity", "recall_task_summary", "report_status",
                 "pause_activity", "conclude_activity", "revise_day_plan", "search_memory")
#: 每次模型请求的 tools 数组次序：六个世界请求、回看任务与检索记忆。
_REQUEST_ORDER = ("start_main_activity", "start_side_activity", "report_status", "pause_activity",
                  "conclude_activity", "revise_day_plan", "recall_task_summary", "search_memory")


@pytest.mark.asyncio
async def test_current_host_request_has_one_system_layer_and_six_user_layers(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    observation, [request], _, _ = await decide(engine, "layers", [
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试休息"))])
    assert request["messages"][0] == system_message(False)
    rules = json.loads(request["messages"][0]["content"])["rules"]
    assert len(rules) == 22 and rules == LIFE_SYSTEM_LAYER["rules"] == list(_RULES)
    layers = json.loads(request["messages"][1]["content"])
    assert [row["layer"] for row in layers] == ["人设层", "记忆层", "待办层", "工具层", "环境层", "进度层"]
    for layer in layers:
        validate_layer(layer)
    assert list(user_layer(layers, "环境层")["body"]) == ["appetite", "sleepiness", "relaxation", "social"]
    from worldmock.agents.prompt.tool_groups import expand_tool_layer
    assert expand_tool_layer(user_layer(layers, "工具层")) == observation["tool_layer"]
    assert request["transport_max_retries"] == 3
    assert len(request["tools"]) == 8
    assert tuple(row["function"]["name"] for row in request["tools"]) == _REQUEST_ORDER
    from custom.agents.life_agent import _TOOL_MODELS
    assert tuple(_TOOL_MODELS) == _NATIVE_TOOLS
    schemas = {row["function"]["name"]: row for row in request["tools"]}
    ordered = [schemas[name] for name in _TOOL_MODELS]
    assert life_tool_schema_sha256() == hashlib.sha256(canonical_json({"tool_schemas": ordered})).hexdigest()


@pytest.mark.asyncio
async def test_consecutive_fresh_requests_share_the_prefix_through_today(engine_factory):
    """同一天里先在家开始歇一刻钟、推进到它结束再收尾：两次请求都是新快照，第二次的 user 消息以人设层和第一次的记忆层
    （去掉末尾的 ]}）开头，整次运行不变的层在前，「今天」只在末尾追加。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observation, [first], _, [started] = await decide(engine, "start", [
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试休息"))])
    assert started.status == "in_progress"
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    assert activity.status != "active"
    _, [second], _, [concluded] = await decide(engine, "conclude", [
        ToolResponse("conclude_activity", conclude_request(activity.name).model_dump(mode="json"))])
    assert concluded.status == "completed"
    assert [[item["role"] for item in request["messages"]] for request in (first, second)] == [["system", "user"]] * 2
    persona = user_layer(json.loads(first["messages"][1]["content"]), "人设层")
    prefix = "[" + render_layer_json(persona) + "," + render_layer_json(observation["memory_layer"])[:-2]
    assert second["messages"][1]["content"].startswith(prefix)


@pytest.mark.parametrize("tool", _NATIVE_TOOLS)
def test_tool_identity_covers_each_of_the_eight_native_tool_descriptions(monkeypatch, tool):
    """工具指纹恰好覆盖八个原生工具：改动其中任何一个的用途句都改变工具指纹，提示指纹不变；复原后两个指纹都能重算
    回原值。"""
    from custom.agents import life_agent
    from worldmock.agents.prompt import life_prompt
    assert len(life_agent._TOOL_MODELS) == 8 and tool in life_agent._TOOL_MODELS
    tools, prompt = life_tool_schema_sha256(), life_prompt_sha256()
    assert life_agent.TOOL_DESCRIPTIONS is life_prompt.TOOL_DESCRIPTIONS
    with monkeypatch.context() as patch:
        patch.setitem(life_prompt.TOOL_DESCRIPTIONS, tool, life_prompt.TOOL_DESCRIPTIONS[tool] + "探针")
        assert life_tool_schema_sha256() != tools
        assert life_prompt_sha256() == prompt
    assert (life_tool_schema_sha256(), life_prompt_sha256()) == (tools, prompt)


def test_tool_identity_ignores_tools_outside_the_native_surface(monkeypatch):
    """只读问答的 finish 不在八个原生工具之内：改它的用途句不改变工具指纹。"""
    from worldmock.agents.prompt import life_prompt
    assert "finish" in life_prompt.TOOL_DESCRIPTIONS and "finish" not in _NATIVE_TOOLS
    original = life_tool_schema_sha256()
    monkeypatch.setitem(life_prompt.TOOL_DESCRIPTIONS, "finish", "探针")
    assert life_tool_schema_sha256() == original


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
