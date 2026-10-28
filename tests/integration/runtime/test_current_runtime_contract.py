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
    assert schemas[SIDE_TOOL]["required"] == list(schemas[SIDE_TOOL]["properties"]) == ["action", "target", "prefer"]
    assert schemas[SIDE_TOOL]["properties"]["action"]["enum"] == ["purchase", "interact", "query"]
    assert schemas[SIDE_TOOL]["properties"]["target"]["type"] == "array"
    assert set(observed["environment_layer"]["body"]) == {"appetite", "sleepiness", "relaxation", "social"}
    events = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
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
    events = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
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
    """模型回复里调用已删除的 start_activity：宿主按本回合未提供的工具纠错，明细逐字，允许值是开放的十个工具。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, requests, _, results = await decide(engine, "retired", [
        ToolResponse("start_activity", {"items": [{"action": "interact", "target": "家"}]}),
        ToolResponse(MAIN_TOOL, _REST, "call-valid")])
    offered = [tool["function"]["name"] for tool in requests[0]["tools"]]
    assert len(offered) == 10 and offered[:2] == [MAIN_TOOL, SIDE_TOOL] and "start_activity" not in offered
    feedback = json.loads(requests[1]["messages"][-1]["content"])
    assert feedback["error_code"] == "TOOL_CALL_REQUIRED"
    assert feedback["items"] == [{
        "path": ["tool_calls", 0, "function", "name"], "written": "start_activity", "error_code": "TOOL_CALL_REQUIRED",
        "allowed_values": offered, "expected": "A tool offered in this round is required.",
        "why": "The tool is not offered in this round.", "candidates": []}]
    assert results[0].status == "in_progress"


@pytest.mark.asyncio
async def test_removed_query_world_reply_is_refused_as_a_tool_that_is_not_offered(engine_factory):
    """模型凭旧习惯调用已删除的 query_world：宿主沿用未提供工具的纠错，回执逐字；这条调用不登记世界请求，下一回合的
    合法开始请求照常受理。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, requests, _, results = await decide(engine, "removed-query", [
        ToolResponse("query_world", {"name": "抖音", "action": None, "category": None, "available_only": False,
                                     "offset": 0}),
        ToolResponse(MAIN_TOOL, _REST, "call-valid")])
    offered = [tool["function"]["name"] for tool in requests[0]["tools"]]
    assert offered == ["start_main_activity", "start_side_activity", "report_status", "pause_activity",
                       "conclude_activity", "revise_day_plan", "recall_task_summary", "memory_recent",
                       "memory_search", "memory_time_range"]
    assert json.loads(requests[1]["messages"][-1]["content"]) == {
        "error_code": "TOOL_CALL_REQUIRED", "reason": "The tool call batch is invalid.",
        "fix": "Submit one world request, the two start tools together, or only read queries.",
        "items": [{"path": ["tool_calls", 0, "function", "name"], "written": "query_world",
                   "error_code": "TOOL_CALL_REQUIRED", "allowed_values": offered,
                   "expected": "A tool offered in this round is required.",
                   "why": "The tool is not offered in this round.", "candidates": []}],
        "unmet": [], "instruction": "No world action was registered. Correct the reported parameters and submit again."}
    requested = [event for event in list(engine.context.store.iter_events(engine.context.store.last_event_seq))
                 if event.event_type == "action_requested"]
    assert [event.data["tool_name"] for event in requested] == [MAIN_TOOL]
    assert results[0].status == "in_progress"


def _dataset_methods(tmp_path):
    """夹具副本换成两种自定的出行方式：电动车（light）与划船（heavy），导航 app 只绑定步行。"""
    from tests.life.environment_inputs import copy_environment, replace_travel_methods, travel_method_row

    return replace_travel_methods(copy_environment(tmp_path / "methods"), [
        travel_method_row("ebike", "电动车", 6.5, 150, 80, "light"), travel_method_row("row", "划船", 2, 0, 0, "heavy"),
    ], ("walking",))


@pytest.mark.asyncio
async def test_dataset_travel_method_name_is_the_one_wording_from_the_prompt_to_the_arrival(engine_factory, tmp_path):
    """真实宿主：提示里的紧凑工具层还原后与完整工具层逐值相同，全部目的地的出行行落在同一组；人物照工具层写的出行方式
    名称就是活动名、环境层路上的位置和到达句里的那个名称。"""
    from tests.life.conftest import context
    from tests.life.environment_runtime import finish_activity
    from worldmock.simulation_engine.activities.lifecycle.activity_control import open_activities
    from worldmock.recording_engine.projections.activities.experiences import recent_verified_experiences
    from worldmock.agents.prompt.prompt_schemas import validate_layer
    from worldmock.simulation_engine.observation.wording.settled_text import experience_sentence
    from worldmock.agents.prompt.tool_groups import expand_tool_layer
    from worldmock.simulation_engine.observation.targets.tool_layer import LayerContext

    engine = engine_factory(_dataset_methods(tmp_path))
    stage_day_plan(engine, submitted=True)
    observed, requests, answer, results = await decide(engine, "ride", [
        ToolResponse(MAIN_TOOL, main_arguments("travel", "公司", "电动车"))])
    assert answer == "start_main_activity registered" and results[0].status == "in_progress"
    prompt = json.loads(requests[0]["messages"][1]["content"])[0]
    assert expand_tool_layer(prompt) == observed["tool_layer"]
    [group] = [row for row in prompt["groups"] if row["action"] == "travel"]
    assert group["kind"] == "location" and len(group["targets"]) == 7 * 3
    assert [row["detail"] for row in group["targets"][:3]] == ["步行", "电动车", "划船"]
    assert [row["note"] for row in group["targets"][:3]] == ["费力", "轻松", "费力"]
    assert "travel_method_id" not in requests[0]["messages"][1]["content"]
    assert "ebike" not in requests[0]["messages"][1]["content"]
    trip = engine.context.state.activities[results[0].activity_id]
    assert trip.name.startswith("电动车前往公司（") and trip.travel_method_id == "ebike"
    on_the_way = engine.observe(context(engine, "on-the-way"))
    validate_layer(on_the_way["environment_layer"])
    assert on_the_way["environment_layer"]["location"] == {
        "session_kind": "transit", "name": "去公司的路上", "travel_method": "电动车"}
    finish_activity(engine, results[0], "ride")
    names = {"activities": {key: row.name for key, row in engine.context.state.activities.items()}, "sessions": {}}
    arrivals = [fact for fact in recent_verified_experiences(engine.context.store, 1, engine.context.store.last_event_seq)
                if fact["kind"] == "arrived"]
    assert [experience_sentence(LayerContext(engine.context, 1, names, [], open_activities(engine.context.state, 1)), fact)[6:] for fact in arrivals] == [
        "电动车到了公司"]


@pytest.mark.asyncio
@pytest.mark.parametrize("detail,code", [("骑车", "NAME_NOT_FOUND"), ("ebike", "NAME_NOT_FOUND"), (None, "ARGUMENTS_INVALID")])
async def test_host_corrects_a_travel_detail_that_names_no_method_and_never_defaults_to_walking(
        engine_factory, tmp_path, detail, code):
    """宿主与环境共用同一个解析函数：出行方式写错、写成身份或不写，宿主当场纠错、不登记世界请求；
    模型改写成列出的名称后才出发，走的就是它写的那种方式。"""
    engine = engine_factory(_dataset_methods(tmp_path))
    stage_day_plan(engine, submitted=True)
    wrong = {"action": "travel", "target": "公司", "detail": detail}
    _, requests, _, results = await decide(engine, "corrected", [
        ToolResponse(MAIN_TOOL, wrong), ToolResponse(MAIN_TOOL, main_arguments("travel", "公司", "划船"), "call-valid")])
    feedback = json.loads(requests[1]["messages"][-1]["content"])
    assert feedback["error_code"] == code
    [item] = feedback["items"]
    assert item["path"][-1] == "detail" and item["written"] == detail
    assert item["allowed_values"] == ([] if detail is None else ["步行", "电动车", "划船"])
    events = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
    registered = [event.data["request"] for event in events if event.event_type == "action_requested"]
    assert registered == [main_arguments("travel", "公司", "划船")]
    assert results[0].status == "in_progress"
    [trip] = engine.context.state.activities.values()
    assert (trip.travel_method_id, trip.data["exertion"]) == ("row", "heavy") and trip.name.startswith("划船前往公司（")
