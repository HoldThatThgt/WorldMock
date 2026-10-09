"""原生人物请求把兼容名额的真实动作整组登记，环境仍原子结算。"""
import pytest
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from tests.life.conftest import stage_day_plan

@pytest.mark.asyncio
async def test_native_parallel_walk_and_app_interaction_use_both_actual_slots(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, requests, _, [result] = await decide(engine, "parallel", [ToolResponse("start_activity", {
        "items": [{"action": "walk", "target": "三杯咖啡"}, {"action": "interact", "target": "抖音"}]})])
    assert result.status == "in_progress"
    assert len(engine.context.state.activities) == 2
    assert {slot for activity in engine.context.state.activities.values()
            for slot in activity.resource_claims} == {"first", "second"}
    [record] = engine.context.store.read_agent_events("action_requested", 1)
    assert record.data["request"]["items"] == [
        {"action": "walk", "target": "三杯咖啡"}, {"action": "interact", "target": "抖音"}]
    assert len(requests) == 1

@pytest.mark.asyncio
async def test_conflicting_parallel_request_registers_once_then_changes_no_activities(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, requests, _, [result] = await decide(engine, "conflict", [ToolResponse("start_activity", {
        "items": [{"action": "interact", "target": "家"}, {"action": "interact", "target": "抖音"}]})])
    assert result.error_code == "RESOURCE_CONFLICT"
    assert not engine.context.state.activities
    assert len(requests) == len(engine.context.store.read_agent_events("action_requested", 1)) == 1
