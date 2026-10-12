"""人物真的读到做完的内容：活动做完后的下一次决策，真实宿主发出的提示里有这项活动做过的内容。"""

from __future__ import annotations

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import SIDE_TOOL, advance_next, conclude_request, stage_day_plan, use_app
from tests.life.environment_runtime import act, events


@pytest.mark.asyncio
async def test_next_decision_prompt_shows_what_was_watched(engine_factory):
    """打开 Keep 放完《测试慢跑》后的下一次决策经真实宿主：落库的 model_request 用户消息里，进度层这条请求的记录是
    带 contents 的第二条结果，写着这条内容的标题与简介。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, used = act(engine, "keep", use_app("Keep"))
    assert used.status == "in_progress", used.error_code
    while engine.context.state.activities[used.activity_id].status == "active":
        assert not engine.scheduling_state().ready_agent_ids
        advance_next(engine)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    name = engine.context.state.activities[used.activity_id].name
    _, _, _, results = await decide(engine, "after", [
        ToolResponse("conclude_activity", conclude_request(name).model_dump(mode="json"))])
    assert results[0].status == "completed", results[0].error_code
    (request,) = [event.data["request"] for event in events(engine, "model_request")
                  if event.decision_id == "decision-after-1"]
    user = request["messages"][1]["content"]
    assert "测试慢跑" in user and "测试慢跑的测试用合成摘要。" in user
    (progress,) = [layer for layer in json.loads(user) if layer["layer"] == "进度层"]
    (record,) = [row for row in progress["events"] if row.get("tool") == SIDE_TOOL]
    assert record["result"]["status"] == "completed"
    assert record["result"]["details"] == {"contents": [{"title": "测试慢跑", "summary": "测试慢跑的测试用合成摘要。",
                                                         "duration": "约 20 分钟", "finished": True}]}
