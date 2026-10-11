"""@brief 真实单人宿主在当前目录合同上的请求、纠错、活动和证据闭环。"""

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, advance_next, main_arguments, side_arguments, stage_day_plan

#: 人在家点名「测试休息」：start_main_activity 的完整参数。
_REST = main_arguments("interact", "家", "测试休息")


@pytest.mark.asyncio
async def test_real_agent_registers_current_action_and_persists_complete_wire(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    observed, requests, answer, results = await decide(engine, "interact-home", [ToolResponse(MAIN_TOOL, _REST)])
    assert answer == "start_main_activity registered"
    assert len(requests) == 1 and results[0].status == "in_progress"
    request = requests[0]
    assert request["model"] == "test-model"
    assert request["max_retries"] == 0 and request["transport_max_retries"] == 3
    assert request["max_tokens"] <= engine.context.inputs.model_metadata.max_output_tokens
    assert len(request["messages"]) == 2
    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in request["tools"]}
    assert "start_activity" not in schemas
    assert schemas[MAIN_TOOL]["required"] == list(schemas[MAIN_TOOL]["properties"]) == ["action", "target", "detail"]
    assert schemas[MAIN_TOOL]["properties"]["action"]["enum"] == ["travel", "interact", "sleep"]
    assert schemas[SIDE_TOOL]["required"] == list(schemas[SIDE_TOOL]["properties"]) == ["action", "target"]
    assert schemas[SIDE_TOOL]["properties"]["action"]["enum"] == ["purchase", "interact"]
    assert schemas[SIDE_TOOL]["properties"]["target"]["type"] == "array"
    assert set(observed["environment_layer"]["body"]) == {"appetite", "sleepiness", "relaxation", "social"}
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    wire = [event.data["request"] for event in events if event.event_type == "model_request"]
    assert wire == requests
    advance_next(engine)
    activity = engine.context.state.activities[results[0].activity_id]
    assert activity.name.startswith("进行测试休息（") and activity.resource_claims == {"first"}
    assert activity.elapsed_seconds > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,invalid,code", [
    (MAIN_TOOL, {"action": "wait", "target": "家", "detail": None}, "ARGUMENTS_INVALID"),
    (MAIN_TOOL, main_arguments("interact", "家", "不存在的事"), "NAME_NOT_FOUND"),
    (SIDE_TOOL, side_arguments("interact", "invented-app"), "NAME_NOT_FOUND"),
    (MAIN_TOOL, {**_REST, "app": None}, "ARGUMENTS_INVALID"),
    ("start_activity", {"items": [{"action": "interact", "target": "家"}]}, "TOOL_CALL_REQUIRED"),
], ids=["retired_action", "unknown_detail", "unknown_app", "undeclared_key", "retired_tool"])
async def test_host_corrects_retired_or_unknown_action_without_partial_registration(engine_factory, tool, invalid,
                                                                                   code):
    """宿主在登记前纠正：已删除的动作、当前地点没有的事、不存在的 app、多余的键与已删除的工具 start_activity 各得
    对应的错误码；改正后只登记那一条合法请求。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, requests, _, results = await decide(engine, "correction", [
        ToolResponse(tool, invalid), ToolResponse(MAIN_TOOL, _REST, "call-valid")])
    assert len(requests) == 2 and results[0].status == "in_progress"
    feedback = requests[1]["messages"][-1]
    assert feedback["role"] == "tool"
    assert json.loads(feedback["content"])["error_code"] == code
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    registered = [event for event in events if event.event_type == "action_requested"]
    assert len(registered) == 1
    assert (registered[0].data["tool_name"], registered[0].data["request"]) == (MAIN_TOOL, _REST)


@pytest.mark.asyncio
async def test_same_second_continuation_preserves_previous_model_wire(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, first, _, _ = await decide(engine, "plan", [ToolResponse("revise_day_plan", {"goals": []}, "call-plan")])
    _, second, _, _ = await decide(engine, "interact", [ToolResponse(MAIN_TOOL, _REST, "call-interact")])
    assert second[0]["messages"][:len(first[0]["messages"])] == first[0]["messages"]
    assert len(second[0]["messages"]) == len(first[0]["messages"]) + 2
    assert second[0]["messages"][-1]["role"] == "tool"
    assert second[0]["messages"][-1]["tool_call_id"] == "call-plan"


@pytest.mark.asyncio
async def test_deleted_start_activity_reply_is_refused_as_a_tool_that_is_not_offered(engine_factory):
    """模型回复里调用已删除的 start_activity：宿主按本回合未提供的工具纠错，明细逐字，允许值是开放的十一个工具。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, requests, _, results = await decide(engine, "retired", [
        ToolResponse("start_activity", {"items": [{"action": "interact", "target": "家"}]}),
        ToolResponse(MAIN_TOOL, _REST, "call-valid")])
    offered = [tool["function"]["name"] for tool in requests[0]["tools"]]
    assert len(offered) == 11 and offered[:2] == [MAIN_TOOL, SIDE_TOOL] and "start_activity" not in offered
    feedback = json.loads(requests[1]["messages"][-1]["content"])
    assert feedback["error_code"] == "TOOL_CALL_REQUIRED"
    assert feedback["items"] == [{
        "path": ["tool_calls", 0, "function", "name"], "written": "start_activity", "error_code": "TOOL_CALL_REQUIRED",
        "allowed_values": offered, "expected": "A tool offered in this round is required.",
        "why": "The tool is not offered in this round.", "candidates": []}]
    assert results[0].status == "in_progress"
