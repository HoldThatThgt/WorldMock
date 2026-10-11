"""原生人物在同一次回复里调用两个开始工具，整组登记，环境仍原子结算。"""
import json
import pytest
from tests.integration.runtime.current_contract_fixtures import decide
from tests.integration.runtime.test_life_agent_protocol import _MultipleModelResponse
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, main_arguments, side_arguments, stage_day_plan
from tests.life.environment_runtime import action_result


def _group(*calls):
    """同一次回复里的开始组：（工具名，参数）逐条成为一个原生工具调用，调用身份按先后编号。"""
    return _MultipleModelResponse([(f"call-{index}", tool, json.dumps(arguments, ensure_ascii=False))
                                   for index, (tool, arguments) in enumerate(calls, start=1)])


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(MAIN_TOOL, SIDE_TOOL), (SIDE_TOOL, MAIN_TOOL)])
async def test_native_parallel_walk_and_app_interaction_use_both_actual_slots(engine_factory, order):
    """出行与用内容 app 同组：两条调用各登记一条动作、同一时刻开始两项活动并各占一个槽位；两条调用的先后不改变结果，
    环境只向框架返回第一条登记动作的结果。"""
    engine = engine_factory()
    stage_day_plan(engine)
    arguments = {MAIN_TOOL: main_arguments("travel", "三杯咖啡", "步行"), SIDE_TOOL: side_arguments("interact", "抖音")}
    _, requests, answer, [result] = await decide(engine, "parallel", [_group(*((tool, arguments[tool])
                                                                                for tool in order))])
    assert answer == f"{order[0]} and {order[1]} registered"
    assert result.status == "in_progress" and len(requests) == 1
    records = engine.context.store.read_agent_events("action_requested", 1)
    assert [(record.data["tool_name"], record.data["request"]) for record in records] == [
        (tool, arguments[tool]) for tool in order]
    assert len({record.decision_id for record in records}) == 1
    assert [record.action_id[-4:] for record in records] == ["0001", "0002"] and result.action_id == records[0].action_id
    activities = engine.context.state.activities
    assert len(activities) == 2
    started = {}
    for record in records:
        accepted = action_result(engine, record.action_id)
        assert (accepted.status, accepted.message) == ("in_progress", "Activities started")
        assert [row["activity_id"] for row in accepted.data["activities"]] == [accepted.activity_id]
        assert activities[accepted.activity_id].origin_action_id == record.action_id
        started[record.data["tool_name"]] = activities[accepted.activity_id]
    assert started[MAIN_TOOL].resource_claims == {"first"} and started[SIDE_TOOL].resource_claims == {"second"}
    assert started[MAIN_TOOL].started_at == started[SIDE_TOOL].started_at == engine.context.state.current_time


@pytest.mark.asyncio
async def test_conflicting_parallel_request_registers_once_then_changes_no_activities(engine_factory):
    """睡眠要求槽位 2 空着：与用内容 app 同组时整组被拒，两条动作得到同一份拒绝，一项活动都没有开始；宿主不为
    业务拒绝再请求模型。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, requests, _, [result] = await decide(engine, "conflict", [_group(
        (MAIN_TOOL, main_arguments("sleep", "450")), (SIDE_TOOL, side_arguments("interact", "抖音")))])
    assert result.error_code == "RESOURCE_CONFLICT"
    assert result.message == "Sleep requires the second activity slot to be empty. Neither start request was started."
    assert not engine.context.state.activities
    records = engine.context.store.read_agent_events("action_requested", 1)
    assert len(requests) == 1 and [record.data["tool_name"] for record in records] == [MAIN_TOOL, SIDE_TOOL]
    first, second = (action_result(engine, record.action_id) for record in records)
    assert first == result and second == result.model_copy(update={"action_id": records[1].action_id})
    assert result.data["fix"] == "Submit start_main_activity alone to sleep."
    assert [item["path"] for item in result.data["items"]] == [
        ["tool_calls", index, "function", "arguments", "target"] for index in (0, 1)]
    assert [item["written"] for item in result.data["items"]] == ["450", ["抖音"]]
