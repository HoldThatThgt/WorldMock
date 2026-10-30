"""六个公开层与回看任务的回执不泄露私有身份，每项事实按所属层出现。"""
import json
import pytest
from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
from tests.life.conftest import SIDE_TOOL, side_arguments, stage_day_plan, advance_until_ready, context
from worldmock.state.planning.schedule import PlanGoal
from worldmock.agents.prompt.life_prompt import user_layer
from worldmock.agents.prompt.tool_groups import expand_tool_layer

PRIVATE_KEYS = {"activity_id", "content_id", "app_id", "location_id", "goods_id", "session_id",
                "order_id", "object_id", "interaction_bindings",
                "travel_method_id", "content_ids", "episode_titles", "locked"}

def assert_public(value):
    if isinstance(value, dict):
        assert not PRIVATE_KEYS.intersection(value)
        for item in value.values():
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)

def public_messages(request):
    values = []
    for message in request["messages"]:
        text = message.get("content")
        if message["role"] in {"user", "tool"} and text:
            values.append(json.loads(text))
    return values

@pytest.mark.asyncio
async def test_actual_query_and_start_requests_keep_facts_public_and_names_unique(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="休息", goal="歇一歇",
                                           registered_at=engine.context.state.current_time),))
    observed, requests, _, [result] = await decide(engine, "public", [
        ToolResponse("recall_task_summary", {"task_name": "歇一歇"}, "recall"),
        ToolResponse(SIDE_TOOL, side_arguments("interact", "抖音"), "start")])
    assert result.status == "in_progress" and len(requests) == 2
    for request in requests:
        for value in public_messages(request):
            assert_public(value)
        layers = public_messages(request)[0]
        targets = expand_tool_layer(user_layer(layers, "工具层"))["targets"]
        assert len({row["name"] for row in targets}) == len(targets)
        assert len([row for row in layers if row["layer"] == "环境层"]) == 1
    assert "c-welcome" not in json.dumps(requests, ensure_ascii=False)
    assert observed["interaction_bindings"]
    private = [action for row in observed["interaction_bindings"] for action in row.get("actions", [])]
    assert any("travel_method_id" in action for action in private)
    assert any("content_ids" in action and "episode_titles" in action and "locked" in action for action in private)

@pytest.mark.asyncio
async def test_progress_and_conclusion_requests_preserve_public_activity_name(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, _, _, [started] = await decide(engine, "start", [ToolResponse(SIDE_TOOL, side_arguments("interact", "抖音"))])
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    _, requests, _, [result] = await decide(engine, "conclude", [ToolResponse("conclude_activity", {
        "items": [{"activity_name": activity.name, "summary": "看完了。", "memories": [], "views": []}],
        "errands": []})])
    assert result.status == "completed"
    for value in public_messages(requests[0]):
        assert_public(value)
    assert activity.activity_id not in json.dumps(requests[0], ensure_ascii=False)

def test_public_identity_check_detects_nested_leaks():
    with pytest.raises(AssertionError):
        assert_public({"matches": [{"state": {"content_id": "c-welcome"}}]})
