"""人物真的读到做完的内容：活动做完后的下一次决策，真实宿主发出的提示里有这项活动做过的内容。"""

from __future__ import annotations

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, advance_next, conclude_request, main_arguments, side_arguments, stage_day_plan
from tests.life.environment_runtime import events


@pytest.mark.asyncio
async def test_next_decision_prompt_shows_what_was_watched(engine_factory):
    """打开 Keep 放完《测试慢跑》后的下一次决策经真实宿主：落库的 model_request 边界工具差量里，进度层新增记录是
    带 contents 的结果，写着这条内容的标题与简介。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, _, _, [used] = await decide(engine, "keep", [ToolResponse(SIDE_TOOL, side_arguments("interact", "Keep"))])
    assert used.status == "in_progress", used.error_code
    while engine.context.state.activities[used.activity_id].status == "active":
        assert not engine.scheduling_state().ready_agent_ids
        advance_next(engine)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    name = engine.context.state.activities[used.activity_id].name
    _, _, _, results = await decide(engine, "after", [
        ToolResponse("conclude_activity", conclude_request(name).model_dump(mode="json")),
        ToolResponse(MAIN_TOOL, main_arguments("sleep", "60"), "call-sleep")])
    assert results[0].status == "completed", results[0].error_code
    request = [event.data["request"] for event in events(engine, "model_request")][-2]
    delta_msg = request["messages"][-1]["content"]
    assert "测试慢跑" in delta_msg and "测试慢跑的测试用合成摘要。" in delta_msg
    added = json.loads(delta_msg)["changes"]["进度层"]["events_append"]
    (record,) = [row for row in added if row.get("tool") == SIDE_TOOL]
    assert record["result"]["status"] == "completed"
    assert record["result"]["details"] == {"contents": [{"title": "测试慢跑", "summary": "测试慢跑的测试用合成摘要。",
                                                         "duration": "约 20 分钟", "finished": True}]}
