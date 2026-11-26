"""人物只提交本轮可见的完整名称，不能把私有身份交给模型。"""
import json
import pytest
from tests.life.conftest import (
    MAIN_TOOL,
    SEARCH_TOOL,
    SIDE_TOOL,
    main_arguments,
    side_arguments,
    stage_day_plan,
    stub_day_session,
    tool_targets,
    wakeup_for,
)
from worldmock.agents.life_agent import LifeAgent, person_inputs
from worldmock.agents.protocol.agent_loop import LoopToolCall, validate_single_tool_call
from worldmock.foundation.errors import ProtocolError
from worldmock.state.interaction.requests import (
    REQUEST_MODELS,
    SearchWorldRequest,
    StartMainActivityRequest,
    StartSideActivityRequest,
)

#: 人物在家：家可点名的一件事，与一个内容 app。
_REST = main_arguments("interact", "家", "测试休息")
_APP = side_arguments("interact", "抖音")


def _prepared(engine):
    stage_day_plan(engine)
    wakeup = wakeup_for(engine)
    sandbox = engine.open_sandbox(wakeup)
    agent = LifeAgent(person_inputs(engine.context.inputs.persona_by_id[wakeup.agent_id]))
    return engine, wakeup, sandbox, agent


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
    """对象名或 detail 找不到、已知对象在这个工具下不提供所写动作、闭集之外的 action 与已删除的字段，都按各自错误码拒绝，明细指向写错的参数。"""
    _, wakeup, sandbox, _ = _prepared(engine_factory())
    if code == "ARGUMENTS_INVALID":
        error = validate_single_tool_call(LoopToolCall(tool, payload, "call-0"), REQUEST_MODELS)
        assert error.text.startswith(f"{code}: ")
        [item] = error.report.items
        assert (item.error_code, list(item.path)) == (code, ["tool_calls", 0, "function", "arguments", *path])
        return
    res = sandbox.dispatch_world_tool(tool, payload, f"{wakeup.wakeup_id}:action:0001")
    assert not res.accepted and not res.terminal_exit
    res_payload = res.tool_message_payload["result"]
    assert res_payload["error_code"] == code
    [item] = res_payload["details"]["items"]
    assert (item["error_code"], item["path"]) == (code, ["tool_calls", 0, "function", "arguments", *path])


@pytest.mark.parametrize("tool", [MAIN_TOOL, SIDE_TOOL])
def test_retired_items_shape_is_refused_by_both_start_tools(tool):
    """旧的 items 形状交给任何一个开始工具都按参数错误拒绝：多余的 items 与缺失的每个必填参数各报一条。"""
    retired = {"items": [{"action": "interact", "target": "抖音"}]}
    error = validate_single_tool_call(LoopToolCall(tool, retired, "call-0"), REQUEST_MODELS)
    assert error.report.error_code == "ARGUMENTS_INVALID"
    required = ["action", "target", "detail"] if tool == MAIN_TOOL else ["action", "target", "prefer"]
    assert [item.path[4:] for item in error.report.items] == [*((field,) for field in required), ("items",)]


@pytest.mark.parametrize("calls", [((SIDE_TOOL, _APP),), ((MAIN_TOOL, _REST),), ((MAIN_TOOL, _REST), (SIDE_TOOL, _APP)),
                                   ((SIDE_TOOL, _APP), (MAIN_TOOL, _REST))])
def test_valid_readable_target_dispatches_exact_current_typed_request(engine_factory, calls):
    """照工具层写的完整名称经沙盒调度与屏障提交，登记的就是所写的那份有类型请求；两个开始工具同批按先后生成 action_id。"""
    engine, wakeup, sandbox, _ = _prepared(engine_factory())
    for index, (tool, payload) in enumerate(calls, start=1):
        assert validate_single_tool_call(LoopToolCall(tool, payload, f"call-{index}"), REQUEST_MODELS) is None
        result = sandbox.dispatch_world_tool(tool, payload, f"{wakeup.wakeup_id}:action:{index:04d}")
        assert result.accepted and result.terminal_exit
    outcome = sandbox.finish_wakeup(None, stub_day_session(wakeup))
    engine.commit_barrier([outcome])
    records = engine.context.store.read_agent_events("action_requested", 1)
    assert [(record.data["tool_name"], record.data["request"]) for record in records] == [
        (tool, payload) for tool, payload in calls]
    assert [record.action_id for record in records] == [
        f"{wakeup.wakeup_id}:action:{index:04d}" for index in range(1, len(calls) + 1)]
    with pytest.raises(ProtocolError, match="already finished"):
        sandbox.dispatch_world_tool(*calls[0], f"{wakeup.wakeup_id}:action:0099")


def test_tool_schema_has_current_shape_and_no_per_round_names(engine_factory):
    """开始工具与 search_world 的参数就是各自请求模型字段：action 是固定闭集，对象名与 detail 没有枚举，私有身份不进 Schema。"""
    _, wakeup, _, agent = _prepared(engine_factory())
    schemas = agent.turn_tools(16, False)
    functions = {row["function"]["name"]: row["function"] for row in schemas}
    assert ("start_activity" not in functions and list(functions)[:2] == [MAIN_TOOL, SIDE_TOOL]
            and SEARCH_TOOL in functions)
    main, side, search = (functions[tool]["parameters"]["properties"] for tool in (MAIN_TOOL, SIDE_TOOL, SEARCH_TOOL))
    assert list(main) == list(StartMainActivityRequest.model_fields) == ["action", "target", "detail"]
    assert list(side) == list(StartSideActivityRequest.model_fields) == ["action", "target", "prefer"]
    assert list(search) == list(SearchWorldRequest.model_fields) == ["query", "prefer"]
    assert functions[SIDE_TOOL]["parameters"]["required"] == ["action", "target", "prefer"]
    assert main["action"]["enum"] == ["travel", "interact", "sleep"]
    assert side["action"]["enum"] == ["purchase", "interact"]
    assert all("enum" not in json.dumps(fields[key]) for fields in (main, side, search)
               for key in fields if key != "action")
    start = json.dumps([functions[MAIN_TOOL], functions[SIDE_TOOL], functions[SEARCH_TOOL]], ensure_ascii=False)
    names = (
        {row["object"] for row in wakeup.observation["catalog_bindings"]}
        | set(tool_targets(wakeup.observation))
        | {"测试闲聊", "测试休息", "步行", "驾车", "打车", "公共交通"}
    )
    assert len(names) > 20 and not [name for name in names if name in start]
    assert "c-welcome" not in str(schemas) and "agent:1" not in str(schemas)
