"""@brief 真实单人宿主在当前目录合同上的请求、纠错、活动和证据闭环。"""

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import advance_next, stage_day_plan


@pytest.mark.asyncio
async def test_real_agent_registers_current_action_and_persists_complete_wire(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    observed, requests, answer, results = await decide(engine, "interact-home", [
        ToolResponse("start_activity", {"items": [{"action": "interact", "target": "家"}]})])
    assert answer == "start_activity registered"
    assert len(requests) == 1 and results[0].status == "in_progress"
    request = requests[0]
    assert request["model"] == "test-model"
    assert request["max_retries"] == 0 and request["transport_max_retries"] == 3
    assert request["max_tokens"] <= engine.context.inputs.model_metadata.max_output_tokens
    assert len(request["messages"]) == 2
    schema = next(tool["function"]["parameters"] for tool in request["tools"]
                  if tool["function"]["name"] == "start_activity")
    assert set(schema["properties"]) == {"items"}
    assert set(schema["properties"]["items"]["items"]["properties"]) == {"action", "target"}
    assert set(observed["environment_layer"]["body"]) == {"appetite", "sleepiness", "relaxation", "social"}
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    wire = [event.data["request"] for event in events if event.event_type == "model_request"]
    assert wire == requests
    advance_next(engine)
    assert engine.context.state.activities[results[0].activity_id].elapsed_seconds > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"items": [{"action": "wait", "target": "家"}]},
    {"items": [{"action": "interact", "target": "invented-app"}]},
    {"items": [{"action": "interact", "target": "家", "app": None}]},
])
async def test_host_corrects_retired_or_unknown_action_without_partial_registration(engine_factory, invalid):
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, requests, _, results = await decide(engine, "correction", [ToolResponse("start_activity", invalid),
        ToolResponse("start_activity", {"items": [{"action": "interact", "target": "家"}]}, "call-valid")])
    assert len(requests) == 2 and results[0].status == "in_progress"
    feedback = requests[1]["messages"][-1]
    assert feedback["role"] == "tool"
    assert json.loads(feedback["content"])["error_code"]
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    registered = [event for event in events if event.event_type == "action_requested"]
    assert len(registered) == 1
    assert registered[0].data["request"] == {"items": [{"action": "interact", "target": "家"}]}


@pytest.mark.asyncio
async def test_same_second_continuation_preserves_previous_model_wire(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, first, _, _ = await decide(engine, "plan", [ToolResponse("revise_day_plan", {"goals": []}, "call-plan")])
    _, second, _, _ = await decide(engine, "interact", [ToolResponse("start_activity",
        {"items": [{"action": "interact", "target": "家"}]}, "call-interact")])
    assert second[0]["messages"][:len(first[0]["messages"])] == first[0]["messages"]
    assert len(second[0]["messages"]) == len(first[0]["messages"]) + 2
    assert second[0]["messages"][-1]["role"] == "tool"
    assert second[0]["messages"][-1]["tool_call_id"] == "call-plan"
