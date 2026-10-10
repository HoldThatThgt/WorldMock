"""人物只提交本轮可见的完整名称，不能把私有身份交给模型。"""
import copy
import pytest
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from tests.integration.runtime.current_contract_fixtures import environment, person, ScriptedModel
from tests.life.conftest import stage_day_plan, context
from worldmock.life.models import StartActivityRequest

def prepared(engine):
    stage_day_plan(engine)
    ctx = context(engine, "names")
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    agent = person(environment(engine), ctx, ScriptedModel([]))
    agent.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    return agent, observed

@pytest.mark.parametrize("payload,code", [
    ({"items": [{"action": "interact", "target": "不存在的对象"}]}, "NAME_NOT_FOUND"),
    ({"items": [{"action": "wait", "target": "家"}]}, "ACTION_NOT_OFFERED"),
    ({"items": [{"action": "interact", "target": "抖音", "app": "抖音"}]}, "ARGUMENTS_INVALID"),
    ({"items": [{"action": "interact", "target": "抖音", "way": "进行"}]}, "ARGUMENTS_INVALID"),
])
def test_invalid_names_actions_and_removed_fields_fail_before_dispatch(engine_factory, payload, code):
    agent, _ = prepared(engine_factory())
    decision = ReactDecision("", "start_activity", payload, "")
    assert code in agent.validate_react_decisions([decision], False)

@pytest.mark.asyncio
async def test_valid_readable_target_dispatches_exact_current_typed_request(engine_factory):
    engine = engine_factory()
    agent, observed = prepared(engine)
    payload = {"items": [{"action": "interact", "target": "抖音"}]}
    assert agent.validate_react_decisions([ReactDecision("", "start_activity", payload, "")], False) == ""
    result = await agent.dispatch_react_tool("start_activity", payload)
    assert result.ok
    [record] = engine.context.store.read_agent_events("action_requested", 1)
    assert record.data["request"] == payload

def test_tool_schema_has_current_shape_and_no_per_round_names(engine_factory):
    agent, observed = prepared(engine_factory())
    schemas = agent.build_react_tool_schemas(readonly=False)
    start = next(row["function"] for row in schemas if row["function"]["name"] == "start_activity")
    assert set(start["parameters"]["properties"]) == {"items"}
    item = start["parameters"]["properties"]["items"]["items"]
    assert set(item["properties"]) == {"action", "target"}
    assert all("enum" not in item["properties"][key] for key in ["action", "target"])
    assert "c-welcome" not in str(schemas) and "agent:1" not in str(schemas)
