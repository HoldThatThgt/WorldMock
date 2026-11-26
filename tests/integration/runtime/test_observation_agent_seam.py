"""人物模型请求使用六个公开层，宿主绑定身份留在环境内部。"""
import json
import pytest
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, advance_until_ready, main_arguments, side_arguments, stage_day_plan
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide


@pytest.mark.asyncio
async def test_current_activity_controls_have_readable_names_and_no_private_identity(long_content_engine):
    engine = long_content_engine
    stage_day_plan(engine)
    _, _, _, [started] = await decide(engine, "app", [ToolResponse(SIDE_TOOL, side_arguments("interact", "抖音"))])
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    observation, [request, _], _, [result, _] = await decide(engine, "status", [
        ToolResponse("report_status", {"items": [{"activity_name": activity.name, "summary": "继续看。"}],
                                      "errands": []}),
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试休息"), "call-rest")])
    assert result.status == "completed"
    deltas = json.loads(request["messages"][-1]["content"])["changes"]["工具层"]["group_deltas"]
    target = next(row for row in deltas["added"] if row["name"] == activity.name)
    assert target["kind"] == "activity"
    assert activity.activity_id not in json.dumps(request["messages"], ensure_ascii=False)
    assert observation["activity_bindings"]
