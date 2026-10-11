"""@brief 用真实宿主流程录制查看器请求，验证当前工具与同秒续接。"""

from pathlib import Path
import json

import pytest

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import SIDE_TOOL, side_arguments, stage_day_plan


async def _recorded_viewer_requests(factory):
    """@brief 真实人物先修订计划，再通过同秒续接开始平台浏览。"""
    engine = factory()
    stage_day_plan(engine, submitted=True)
    first = await decide(engine, "viewer-plan", [ToolResponse("revise_day_plan", {
        "goals": [{"intent_kind": "休息", "goal": "浏览一下美团", "remind_time": None,
                   "around_time": None}]}, call_id="viewer-plan")])
    second = await decide(engine, "viewer-browse", [ToolResponse(SIDE_TOOL,
        side_arguments("interact", "美团"), call_id="viewer-browse")])
    return {"action": first[1][0], "continuation": second[1][0]}, engine


@pytest.mark.asyncio
async def test_viewer_fixture_is_recorded_from_the_current_host(_engine_factory):
    """@brief 冻结请求逐字保留当前真实宿主的系统层、工具及续接消息。"""
    requests, engine = await _recorded_viewer_requests(_engine_factory)
    fixture = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "viewer-context.json"
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    assert recorded["action"]["messages"][0] == requests["action"]["messages"][0]
    assert recorded["action"]["tools"] == requests["action"]["tools"]
    assert recorded["continuation"]["messages"][:2] == recorded["action"]["messages"]
    assert requests["continuation"]["messages"][:2] == requests["action"]["messages"]
    names = {tool["function"]["name"] for tool in requests["action"]["tools"]}
    assert {"start_main_activity", "start_side_activity", "report_status", "pause_activity", "conclude_activity",
            "revise_day_plan", "query_world", "recall_task_summary"} <= names
    assert names.isdisjoint({"set_alarm", "send_message", "submit_work"})
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    assert not any(event.event_type == "action_rejected" for event in events)
    activity = next(iter(engine.context.state.activities.values()))
    assert activity.kind.value == "interact" and activity.data["content_id"] is None
