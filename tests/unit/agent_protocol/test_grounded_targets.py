"""人物只提交本轮可见的完整名称，不能把私有身份交给模型。"""
import json
import pytest
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from tests.integration.runtime.current_contract_fixtures import environment, person, ScriptedModel
from tests.life.conftest import (
    MAIN_TOOL,
    SIDE_TOOL,
    context,
    main_arguments,
    side_arguments,
    stage_day_plan,
    tool_targets,
)
from worldmock.state.interaction.requests import StartMainActivityRequest, StartSideActivityRequest

#: 人物在家：家可点名的一件事，与一个内容 app。
_REST = main_arguments("interact", "家", "测试休息")
_APP = side_arguments("interact", "抖音")


def prepared(engine):
    stage_day_plan(engine)
    ctx = context(engine, "names")
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    agent = person(environment(engine), ctx, ScriptedModel([]))
    agent.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    return agent, observed


@pytest.mark.parametrize("tool,payload,code,path", [
    (SIDE_TOOL, side_arguments("interact", "不存在的对象"), "NAME_NOT_FOUND", ["target", 0]),
    (MAIN_TOOL, main_arguments("interact", "不存在的对象", "测试休息"), "NAME_NOT_FOUND", ["target"]),
    (MAIN_TOOL, main_arguments("interact", "家", "不存在的事"), "NAME_NOT_FOUND", ["detail"]),
    (MAIN_TOOL, main_arguments("travel", "公司", "骑车"), "NAME_NOT_FOUND", ["detail"]),
    (MAIN_TOOL, main_arguments("travel", "家", "步行"), "ACTION_NOT_OFFERED", ["action"]),
    (MAIN_TOOL, main_arguments("interact", "公司", "季度销售数据整理"), "ACTION_NOT_OFFERED", ["action"]),
    (MAIN_TOOL, main_arguments("interact", "抖音", "测试休息"), "ACTION_NOT_OFFERED", ["action"]),
    (SIDE_TOOL, side_arguments("purchase", "抖音"), "ACTION_NOT_OFFERED", ["action"]),
    (SIDE_TOOL, side_arguments("interact", "家"), "ACTION_NOT_OFFERED", ["action"]),
    (MAIN_TOOL, {**_REST, "action": "wait"}, "ARGUMENTS_INVALID", ["action"]),
    (SIDE_TOOL, {**_APP, "action": "walk"}, "ARGUMENTS_INVALID", ["action"]),
    (SIDE_TOOL, {**_APP, "app": "抖音"}, "ARGUMENTS_INVALID", ["app"]),
    (SIDE_TOOL, {**_APP, "way": "进行"}, "ARGUMENTS_INVALID", ["way"]),
    (MAIN_TOOL, {**_REST, "way": "进行"}, "ARGUMENTS_INVALID", ["way"]),
])
def test_invalid_names_actions_and_removed_fields_fail_before_dispatch(engine_factory, tool, payload, code, path):
    """对象名或 detail 找不到、已知对象在这个工具下不提供所写动作（出行去当前地点、在别处的地点做事、把 app 交给主工具、
    把地点交给副工具）、闭集之外的 action 与已删除的字段，都在分派前按各自的错误码拒绝，明细指向写错的那个参数。"""
    agent, _ = prepared(engine_factory())
    error = agent.validate_react_decisions([ReactDecision("", tool, payload, "")], False)
    assert error.startswith(f"{code}: ")
    [item] = error.report.items
    assert (item.error_code, list(item.path)) == (code, ["tool_calls", 0, "function", "arguments", *path])


@pytest.mark.parametrize("tool", [MAIN_TOOL, SIDE_TOOL])
def test_retired_items_shape_is_refused_by_both_start_tools(engine_factory, tool):
    """旧的 items 形状交给任何一个开始工具都按参数错误拒绝：多余的 items 与缺失的每个必填参数各报一条。"""
    agent, _ = prepared(engine_factory())
    retired = {"items": [{"action": "interact", "target": "抖音"}]}
    error = agent.validate_react_decisions([ReactDecision("", tool, retired, "")], False)
    assert error.report.error_code == "ARGUMENTS_INVALID"
    required = ["action", "target", "detail"] if tool == MAIN_TOOL else ["action", "target", "prefer"]
    assert [item.path[4:] for item in error.report.items] == [*((field,) for field in required), ("items",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("calls", [((SIDE_TOOL, _APP),), ((MAIN_TOOL, _REST),), ((MAIN_TOOL, _REST), (SIDE_TOOL, _APP)),
                                   ((SIDE_TOOL, _APP), (MAIN_TOOL, _REST))])
async def test_valid_readable_target_dispatches_exact_current_typed_request(engine_factory, calls):
    """照工具层写的完整名称通过宿主校验，登记的就是所写的那份有类型请求；两个开始工具同批时按回复中的先后各登记一条，
    动作身份的序号从 1 起。"""
    engine = engine_factory()
    agent, observed = prepared(engine)
    decisions = [ReactDecision("", tool, payload, "", f"call-{index}") for index, (tool, payload) in enumerate(calls)]
    assert agent.validate_react_decisions(decisions, False) == ""
    for tool, payload in calls:
        result = await agent.dispatch_react_tool(tool, payload)
        assert result.ok
    records = engine.context.store.read_agent_events("action_requested", 1)
    assert [(record.data["tool_name"], record.data["request"]) for record in records] == [
        (tool, payload) for tool, payload in calls]
    decision_id = agent.require_decision_context().decision_id
    assert [record.action_id for record in records] == [
        f"{decision_id}:action:{index:04d}" for index in range(1, len(calls) + 1)]
    with pytest.raises(RuntimeError, match="already submitted"):
        await agent.dispatch_react_tool(*calls[0])


def test_tool_schema_has_current_shape_and_no_per_round_names(engine_factory):
    """两个开始工具的参数就是请求模型的字段：action 是固定闭集，对象名与 detail 没有枚举，本轮观察里的任何名称与私有
    身份都不进 Schema。"""
    agent, observed = prepared(engine_factory())
    schemas = agent.build_react_tool_schemas(readonly=False)
    functions = {row["function"]["name"]: row["function"] for row in schemas}
    assert "start_activity" not in functions and list(functions)[:2] == [MAIN_TOOL, SIDE_TOOL]
    main, side = (functions[tool]["parameters"]["properties"] for tool in (MAIN_TOOL, SIDE_TOOL))
    assert list(main) == list(StartMainActivityRequest.model_fields) == ["action", "target", "detail"]
    assert list(side) == list(StartSideActivityRequest.model_fields) == ["action", "target", "prefer"]
    assert functions[SIDE_TOOL]["parameters"]["required"] == ["action", "target", "prefer"]
    assert main["action"]["enum"] == ["travel", "interact", "sleep"]
    assert side["action"]["enum"] == ["purchase", "interact", "query"]
    assert all("enum" not in json.dumps(fields[key]) for fields in (main, side) for key in fields if key != "action")
    start = json.dumps([functions[MAIN_TOOL], functions[SIDE_TOOL]], ensure_ascii=False)
    names = set(tool_targets(observed)) | {"测试闲聊", "测试休息", "步行", "驾车", "打车", "公共交通"}
    assert len(names) > 20 and not [name for name in names if name in start]
    assert "c-welcome" not in str(schemas) and "agent:1" not in str(schemas)
