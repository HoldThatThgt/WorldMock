"""实际宿主请求的系统消息、六个用户层和原生工具具有一致身份。"""
import hashlib
import json
import pytest
from tests.engine_fixture import engine_factory
from tests.life.conftest import stage_day_plan
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from worldmock.agents.life_prompt import system_message, life_prompt_sha256
from worldmock.life.prompt_schemas import validate_layer
from custom.agents.life_agent import life_tool_schema_sha256
from worldmock.footprints.models import canonical_json


@pytest.mark.asyncio
async def test_current_host_request_has_one_system_layer_and_six_user_layers(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    observation, [request], _, _ = await decide(engine, "layers", [ToolResponse("start_activity", {
        "items": [{"action": "interact", "target": "家"}]})])
    assert request["messages"][0] == system_message(False)
    layers = json.loads(request["messages"][1]["content"])
    assert [row["layer"] for row in layers] == ["工具层", "人设层", "记忆层", "待办层", "环境层", "进度层"]
    for layer in layers:
        validate_layer(layer)
    assert list(layers[4]["body"]) == ["appetite", "sleepiness", "relaxation", "social"]
    from worldmock.life.tool_groups import expand_tool_layer
    assert expand_tool_layer(layers[0]) == observation["tool_layer"]
    assert request["transport_max_retries"] == 3
    assert {row["function"]["name"] for row in request["tools"]} == {"start_activity", "recall_task_summary",
        "report_status", "pause_activity", "conclude_activity", "revise_day_plan", "query_world",
        "memory_recent", "memory_search", "memory_time_range"}
    from custom.agents.life_agent import _TOOL_MODELS
    schemas = {row["function"]["name"]: row for row in request["tools"]}
    ordered = [schemas[name] for name in _TOOL_MODELS]
    assert life_tool_schema_sha256() == hashlib.sha256(canonical_json({"tool_schemas": ordered})).hexdigest()


def test_prompt_identity_changes_when_actual_layer_schema_changes(monkeypatch):
    from worldmock.agents import life_prompt
    original = life_prompt_sha256()
    schemas = __import__("worldmock.life.prompt_schemas", fromlist=["LAYER_SCHEMAS"]).LAYER_SCHEMAS
    changed = {**schemas, "probe": {"type": "object"}}
    monkeypatch.setattr(life_prompt, "LAYER_SCHEMAS", changed)
    assert life_prompt_sha256() != original
