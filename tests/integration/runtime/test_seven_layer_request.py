"""实际宿主请求的系统消息、六个用户层和原生工具具有一致身份。"""
import hashlib
import json
import pytest
from tests.engine_fixture import engine_factory
from tests.life.conftest import MAIN_TOOL, main_arguments, stage_day_plan
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from worldmock.agents.life_prompt import system_message, life_prompt_sha256
from worldmock.life.prompt_schemas import validate_layer
from custom.agents.life_agent import life_tool_schema_sha256
from worldmock.footprints.models import canonical_json

#: 生活决策的十个原生工具，按计算工具指纹的次序：七个请求工具（两个开始工具在前）与三个记忆查询。
_NATIVE_TOOLS = ("start_main_activity", "start_side_activity", "recall_task_summary", "report_status",
                 "pause_activity", "conclude_activity", "revise_day_plan", "memory_recent", "memory_search",
                 "memory_time_range")
#: 每次模型请求的 tools 数组次序：六个世界请求、回看任务与三个记忆查询。
_REQUEST_ORDER = ("start_main_activity", "start_side_activity", "report_status", "pause_activity",
                  "conclude_activity", "revise_day_plan", "recall_task_summary", "memory_recent", "memory_search",
                  "memory_time_range")


@pytest.mark.asyncio
async def test_current_host_request_has_one_system_layer_and_six_user_layers(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    observation, [request], _, _ = await decide(engine, "layers", [
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试休息"))])
    assert request["messages"][0] == system_message(False)
    layers = json.loads(request["messages"][1]["content"])
    assert [row["layer"] for row in layers] == ["工具层", "人设层", "记忆层", "待办层", "环境层", "进度层"]
    for layer in layers:
        validate_layer(layer)
    assert list(layers[4]["body"]) == ["appetite", "sleepiness", "relaxation", "social"]
    from worldmock.life.tool_groups import expand_tool_layer
    assert expand_tool_layer(layers[0]) == observation["tool_layer"]
    assert request["transport_max_retries"] == 3
    assert len(request["tools"]) == 10
    assert tuple(row["function"]["name"] for row in request["tools"]) == _REQUEST_ORDER
    from custom.agents.life_agent import _TOOL_MODELS
    assert tuple(_TOOL_MODELS) == _NATIVE_TOOLS
    schemas = {row["function"]["name"]: row for row in request["tools"]}
    ordered = [schemas[name] for name in _TOOL_MODELS]
    assert life_tool_schema_sha256() == hashlib.sha256(canonical_json({"tool_schemas": ordered})).hexdigest()


@pytest.mark.parametrize("tool", _NATIVE_TOOLS)
def test_tool_identity_covers_each_of_the_ten_native_tool_descriptions(monkeypatch, tool):
    """工具指纹恰好覆盖十个原生工具：改动其中任何一个的用途句都改变工具指纹，提示指纹不变；复原后两个指纹都能重算
    回原值。"""
    from custom.agents import life_agent
    from worldmock.agents import life_prompt
    assert len(life_agent._TOOL_MODELS) == 10 and tool in life_agent._TOOL_MODELS
    tools, prompt = life_tool_schema_sha256(), life_prompt_sha256()
    assert life_agent.TOOL_DESCRIPTIONS is life_prompt.TOOL_DESCRIPTIONS
    with monkeypatch.context() as patch:
        patch.setitem(life_prompt.TOOL_DESCRIPTIONS, tool, life_prompt.TOOL_DESCRIPTIONS[tool] + "探针")
        assert life_tool_schema_sha256() != tools
        assert life_prompt_sha256() == prompt
    assert (life_tool_schema_sha256(), life_prompt_sha256()) == (tools, prompt)


def test_tool_identity_ignores_tools_outside_the_native_surface(monkeypatch):
    """只读问答的 finish 不在十个原生工具之内：改它的用途句不改变工具指纹。"""
    from worldmock.agents import life_prompt
    assert "finish" in life_prompt.TOOL_DESCRIPTIONS and "finish" not in _NATIVE_TOOLS
    original = life_tool_schema_sha256()
    monkeypatch.setitem(life_prompt.TOOL_DESCRIPTIONS, "finish", "探针")
    assert life_tool_schema_sha256() == original


def test_prompt_identity_changes_when_system_layer_text_changes(monkeypatch):
    """系统层任何一条 rules 的文字改变都改变提示指纹，工具指纹不变。"""
    from worldmock.agents import life_prompt
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
    from worldmock.agents import life_prompt
    original = life_prompt_sha256()
    schemas = __import__("worldmock.life.prompt_schemas", fromlist=["LAYER_SCHEMAS"]).LAYER_SCHEMAS
    changed = {**schemas, "probe": {"type": "object"}}
    monkeypatch.setattr(life_prompt, "LAYER_SCHEMAS", changed)
    assert life_prompt_sha256() != original
