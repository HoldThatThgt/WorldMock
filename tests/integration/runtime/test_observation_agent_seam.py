"""人物模型请求使用六个公开层，宿主绑定身份留在环境内部。"""
import json
import pytest
from tests.life.conftest import stage_day_plan, side_arguments, use_app, advance_until_ready
from tests.life.environment_runtime import act
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from worldmock.life.tool_groups import expand_tool_layer

@pytest.mark.asyncio
async def test_current_activity_controls_have_readable_names_and_no_private_identity(long_content_engine):
    engine = long_content_engine
    stage_day_plan(engine)
    _, started = act(engine, "app", use_app("抖音"))
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    observation, [request], _, [result] = await decide(engine, "status", [
        ToolResponse("report_status", {"items": [{"activity_name": activity.name, "summary": "继续看。"}],
                                      "errands": []})])
    assert result.status == "completed"
    layers = json.loads(request["messages"][1]["content"])
    tools = expand_tool_layer(layers[0])
    target = next(row for row in tools["targets"] if row["name"] == activity.name)
    assert target["kind"] == "activity"
    assert activity.activity_id not in request["messages"][1]["content"]
    assert observation["activity_bindings"]

@pytest.mark.asyncio
async def test_query_world_uses_frozen_observation_before_one_world_request(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, requests, _, [result] = await decide(engine, "query", [
        ToolResponse("query_world", {"name": "抖音", "action": None, "category": None,
                                     "available_only": False, "offset": 0}, "query"),
        ToolResponse("start_side_activity", side_arguments("interact", "抖音"), "start")])
    assert len(requests) == 2 and result.status == "in_progress"
    assert len(engine.context.store.read_agent_events("action_requested", 1)) == 1
    answer = json.loads(requests[1]["messages"][-1]["content"])
    assert answer["matches"] and answer["matches"][0]["name"] == "抖音"
