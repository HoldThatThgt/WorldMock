"""实际地点决定动作可用性，原生工具声明跨地点保持一致。"""
import pytest
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, side_arguments, stage_day_plan, context
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from worldmock.life.tool_groups import expand_tool_layer
import json

@pytest.mark.asyncio
@pytest.mark.parametrize("initial,units,sleepable", [("家", ["测试闲聊", "测试休息"], True), ("三杯咖啡", [], False)])
async def test_native_request_uses_current_location_availability_and_stable_tools(engine_factory, initial, units,
                                                                                 sleepable):
    """当前地点的行只有可点名的事与睡觉（没有出行），别的地点只有各出行方式的行（步行在前，其余按出行方式身份升序）；
    十个原生工具的声明不随地点改变。"""
    engine = engine_factory(mutate=lambda persona, config: persona.update(initial_location=initial))
    stage_day_plan(engine)
    observed, [request], _, [result] = await decide(engine, "location", [
        ToolResponse(SIDE_TOOL, side_arguments("interact", "抖音"))])
    assert result.status == "in_progress"
    tools = expand_tool_layer(json.loads(request["messages"][1]["content"])[0])
    location = next(row for row in tools["targets"] if row["name"] == initial)
    assert not any(row["action"] == "travel" for row in location["actions"])
    assert [row["detail"] for row in location["actions"] if row["action"] == "interact"] == units
    assert [row["action"] for row in location["actions"]] == ["interact"] * len(units) + ["sleep"] * sleepable
    elsewhere = [row for row in tools["targets"] if row["kind"] == "location" and row["name"] != initial]
    assert elsewhere and all([(row["action"], row["detail"]) for row in target["actions"]] == [
        ("travel", method) for method in ("步行", "公共交通", "驾车", "打车")] for target in elsewhere)
    assert all(list(row) == ["action", "detail", "available", "reason", "price", "duration", "quantity", "note"]
               for target in tools["targets"] for row in target["actions"])
    assert len(request["tools"]) == 10
    assert [tool["function"]["name"] for tool in request["tools"][:2]] == [MAIN_TOOL, SIDE_TOOL]
    assert set(request["tools"][0]["function"]["parameters"]["properties"]) == {"action", "target", "detail"}
    assert set(request["tools"][1]["function"]["parameters"]["properties"]) == {"action", "target"}
