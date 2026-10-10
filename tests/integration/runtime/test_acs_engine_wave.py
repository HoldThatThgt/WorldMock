"""当前活動生命周期通过原生宿主与真实冻结环境执行。"""
import pytest
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from tests.life.conftest import stage_day_plan, advance_until_ready, start_activity
from tests.life.environment_runtime import act

@pytest.mark.asyncio
async def test_pause_and_resume_keep_the_real_content_cursor(long_content_engine):
    engine = long_content_engine
    stage_day_plan(engine)
    _, started = act(engine, "app", start_activity("interact", "抖音"))
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    assert activity.status == "active"
    elapsed = activity.elapsed_seconds
    _, _, _, [paused] = await decide(engine, "pause", [ToolResponse("pause_activity", {
        "items": [{"activity_name": activity.name, "summary": "先停一会儿。"}], "errands": []})])
    assert paused.status == "completed"
    assert engine.context.state.activities[started.activity_id].status == "paused"
    _, _, _, [resumed] = await decide(engine, "resume", [ToolResponse("report_status", {
        "items": [{"activity_name": activity.name, "summary": "继续看。"}], "errands": []})])
    assert resumed.status == "completed"
    assert engine.context.state.activities[started.activity_id].status == "active"
    assert engine.context.state.activities[started.activity_id].elapsed_seconds == elapsed

@pytest.mark.asyncio
async def test_host_corrects_unknown_target_before_world_registration(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, requests, _, [result] = await decide(engine, "correct", [
        ToolResponse("start_activity", {"items": [{"action": "interact", "target": "不存在的应用"}]}, "bad"),
        ToolResponse("start_activity", {"items": [{"action": "interact", "target": "抖音"}]}, "good")])
    assert len(requests) == 2 and result.status == "in_progress"
    assert len(engine.context.store.read_agent_events("action_requested", 1)) == 1
    assert "NAME_NOT_FOUND" in requests[1]["messages"][-1]["content"]
