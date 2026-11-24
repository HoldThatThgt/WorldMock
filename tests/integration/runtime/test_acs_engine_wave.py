"""当前活動生命周期通过原生宿主与真实冻结环境执行。"""
import pytest
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from tests.life.conftest import MAIN_TOOL, stage_day_plan, advance_until_ready, main_arguments, side_arguments, use_app
from tests.life.environment_runtime import act

@pytest.mark.asyncio
async def test_pause_and_resume_keep_the_real_content_cursor(long_content_engine):
    engine = long_content_engine
    stage_day_plan(engine)
    _, started = act(engine, "app", use_app("抖音"))
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    assert activity.status == "active"
    elapsed = activity.elapsed_seconds
    _, _, _, [paused, chat] = await decide(engine, "pause", [
        ToolResponse("pause_activity", {"items": [{"activity_name": activity.name, "summary": "先停一会儿。"}],
                                        "errands": []}),
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试闲聊"), "chat-wait")])
    assert paused.status == "completed"
    assert engine.context.state.activities[started.activity_id].status == "paused"
    assert advance_until_ready(engine)
    chat_name = engine.context.state.activities[chat.activity_id].name
    _, _, _, [_, resumed, _] = await decide(engine, "resume", [
        ToolResponse("conclude_activity", {"items": [{"activity_name": chat_name, "summary": "聊完了。",
                                                      "memories": [], "views": []}], "errands": []}),
        ToolResponse("report_status", {"items": [{"activity_name": activity.name, "summary": "继续看。"}],
                                       "errands": []}),
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试休息"), "rest-after")])
    assert resumed.status == "completed"
    assert engine.context.state.activities[started.activity_id].status == "active"
    assert engine.context.state.activities[started.activity_id].elapsed_seconds == elapsed

@pytest.mark.asyncio
async def test_host_corrects_unknown_target_before_world_registration(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, requests, _, [_, result] = await decide(engine, "correct", [
        ToolResponse("start_side_activity", side_arguments("interact", "不存在的应用"), "bad"),
        ToolResponse("start_side_activity", side_arguments("interact", "抖音"), "good")])
    assert len(requests) == 2 and result.status == "in_progress"
    assert len(engine.context.store.read_agent_events("action_requested", 1)) == 2
    assert "NAME_NOT_FOUND" in requests[1]["messages"][-1]["content"]
