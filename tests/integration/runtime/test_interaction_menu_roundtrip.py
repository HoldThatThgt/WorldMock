"""实际地点决定动作可用性，原生工具声明跨地点保持一致。"""
import pytest
from tests.life.conftest import stage_day_plan, context
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from worldmock.life.tool_groups import expand_tool_layer
import json

@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["家", "三杯咖啡"])
async def test_native_request_uses_current_location_availability_and_stable_tools(engine_factory, initial):
    engine = engine_factory(mutate=lambda persona, config: persona.update(initial_location=initial))
    stage_day_plan(engine)
    observed, [request], _, [result] = await decide(engine, "location", [ToolResponse("start_activity", {
        "items": [{"action": "interact", "target": "抖音"}]})])
    assert result.status == "in_progress"
    tools = expand_tool_layer(json.loads(request["messages"][1]["content"])[0])
    location = next(row for row in tools["targets"] if row["name"] == initial)
    assert not any(row["action"] == "walk" for row in location["actions"])
    assert len(request["tools"]) == 10
    assert set(request["tools"][0]["function"]["parameters"]["properties"]) == {"items"}
