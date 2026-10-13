from tests.integration.runtime.current_contract_fixtures import profile as current_profile
"""宿主协议纠错的错误合同（docs/design/04-simulation-engine.md 第四章「错误合同」）。

LifeAgent 覆写 AS2 的 _protocol_feedback_content（锁定提交不改）：纠错正文是共用错误结构 {error_code, reason, fix, items,
unmet} 加固定 instruction「本次没有登记到世界；按 fix 修改后重新提交」，不带英文校验转储、input_value 与网址；宿主错误码
闭集的每个码经真实宿主路径各有一例具体原因；多项请求中第二项出错时 items 写 index 1，名称类错误逐项报全部、其余条件报第一条；
写进模型响应证据的协议错误串是普通字符串「<错误码>: <英文说明>」。真实 LifeEngine、LifeEnv 与脚本化模型上，环境拒绝给出的
候选原样提交即被受理。
"""


import copy
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from worldmock.agents.life_feedback import FEEDBACK_INSTRUCTION, HostFeedback, host_feedback  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import (_WORK_ACTION, _WAIT, _WAIT_CHOICE, _WORK,  # noqa: E402
                                                        _Environment, _GroundedCorrectionDispatcher, _ModelResponse,
                                                        _add_objects, _conclude_args, _context, _memory, _observations,
                                                        _offer, _prepared, _step_observation, _view,
                                                        _visible_object, _wire_agent, _with_activities)
from tests.integration.life_host_fixtures import TruncatedResponse  # noqa: E402
from tests.integration.life_host_fixtures import environment_for, reply, decide  # noqa: E402
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (MAIN_TOOL, SIDE_TOOL, last_result, main_arguments,  # noqa: F401
                                 side_arguments)
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.activity_control import PHASE_ACTIVE  # noqa: E402
from worldmock.life.error_contract import ERROR_KEYS, HOST_ERROR_CODES, host_defaults, host_report  # noqa: E402

#: 纠错正文里不得出现的校验库原文、输入回显与网址片段。
_FOOD_LABEL = "便利店出售的牛奶"

_DUMP_MARKERS = ("input_value", "input_type", "http://", "https://", "errors.pydantic", "validation error",
                 "Value error", "For further information")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证纠错合同。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _NoToolResponse:
    """没有任何工具调用、只有回复正文的模型响应（AS2 报 native tool_calls are required）。"""

    def __init__(self) -> None:
        message = SimpleNamespace(tool_calls=[], content="我想先想一想。", reasoning_content="思考")
        self.choices = [SimpleNamespace(message=message)]

    def model_dump(self, *, mode: str) -> dict:
        """最小 JSON 响应。"""
        assert mode == "json"
        return {"choices": [{"message": {"content": "我想先想一想。", "tool_calls": [], "reasoning_content": "思考"}}]}


def _decision(tool: str, arguments: dict) -> ReactDecision:
    """一次原生工具决定。"""
    return ReactDecision("", tool, arguments, "")


def _feedback(error: str) -> dict:
    """一条协议错误回给模型的纠错正文（经 LifeAgent 覆写的 AS2 入口）。"""
    return json.loads(LifeAgent._protocol_feedback_content(error))


async def _corrected_loop(bad_responses: list, observations: list[dict] | None = None) -> tuple[list, list, list]:
    """真实 ReAct 循环：先给出几次不合法的响应，最后提交等待；返回（纠错正文列表，响应证据的错误串，发出的请求）。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        *bad_responses, _ModelResponse("call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    environment = _Environment()
    agent = LifeAgent()
    agent._profile = current_profile()
    _wire_agent(agent, environment, dispatcher, len(bad_responses) + 2, memory=False)
    result = await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, observations or [
        {"turn": 0, "action": "observe", "ok": True, "data": _step_observation()}]))
    assert result == f"{MAIN_TOOL} registered" and len(environment.calls) == 1
    feedback = [json.loads(request["messages"][-1]["content"]) for request in dispatcher.requests[1:]]
    errors = [item.error for item in environment.evidence if item.phase == "response" and item.error is not None]
    return feedback, errors, dispatcher.requests


@pytest.mark.asyncio
async def test_protocol_feedback_is_the_shared_structure_with_the_fixed_instruction() -> None:
    """损坏 JSON、缺字段与无调用保留五键反馈、完整路径和纯英文证据；改法是各错误码自己的改法，不是一句固定的话。"""
    missing = copy.deepcopy(_WAIT_CHOICE)
    missing.pop("action")
    broken = '{"target": "自己家" "action":"interact", "detail": "歇一会儿"}'
    feedback, errors, requests = await _corrected_loop([
        _ModelResponse("call-json", MAIN_TOOL, broken),
        _ModelResponse("call-shape", MAIN_TOOL, json.dumps(missing, ensure_ascii=False)), _NoToolResponse()])
    assert [item["error_code"] for item in feedback] == ["ARGUMENTS_NOT_JSON", "ARGUMENTS_INVALID", "TOOL_CALL_REQUIRED"]
    for item in feedback:
        assert list(item) == [*ERROR_KEYS, "instruction"] and item["instruction"] == FEEDBACK_INSTRUCTION
        assert item["reason"] and item["unmet"] == []
        assert item["fix"] == host_defaults(item["error_code"])[1]
        assert not any(marker in json.dumps(item) for marker in _DUMP_MARKERS)
    assert [item["fix"] for item in feedback] == [
        "Submit a complete JSON object.", "Correct every reported parameter.",
        "Submit one world request, the two start tools together, or only read queries."]
    assert feedback[0]["items"][0]["path"] == ["tool_calls", 0, "function", "arguments"]
    assert "line 1, column" in feedback[0]["items"][0]["expected"]
    row = feedback[1]["items"][0]
    assert row["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert row["written"] is None and "required" in row["expected"]
    assert row["allowed_values"] == ["travel", "interact", "sleep"]
    assert feedback[2]["items"][0]["path"] == ["tool_calls"]
    assert [type(error) for error in errors] == [str, str, str]
    assert [error.split(":", 1)[0] for error in errors] == [item["error_code"] for item in feedback]
    assert len(requests) == 4


def test_a_view_about_an_unknown_person_in_the_second_item_is_reported_at_index_one() -> None:
    """结束两项活动，第一项只写偏好、第二项的关系看法写了不认识的人：SUBJECT_NOT_KNOWN 的明细指向第二项（index 1），参数名
    是 about、原文是所写的名字（docs/design/04-simulation-engine.md 第四章 items、第六章 views）。"""
    data = _with_activities(_observations()[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))
    agent = _prepared([{"turn": 0, "action": "observe", "ok": True, "data": data}])
    arguments = _conclude_args(_WORK, _WAIT, memories=[[], []],
                               views=[[_view("拿铁", "下午想喝拿铁。", kind="preference")], [_view("陌生人")]])
    payload = _feedback(agent.validate_react_decisions([_decision("conclude_activity", arguments)], False))
    assert payload["error_code"] == "SUBJECT_NOT_KNOWN"
    assert [(item["path"][-5:], item["written"], item["error_code"]) for item in payload["items"]] == [
        (["items", 1, "views", 0, "about"], "陌生人", "SUBJECT_NOT_KNOWN")]


#: relationship 看法的对象不是已知人物时写进证据的英文说明（证据串为「SUBJECT_NOT_KNOWN: <说明>: <名字>」）。
_SUBJECT_DETAIL = "conclude_activity relationship subject is not a known person"


def _two_activities() -> LifeAgent:
    """构造两项真实绑定形状的活动，检验逐项纠错位置。"""
    data = _with_activities(_observations()[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))
    return _prepared([{"turn": 0, "action": "observe", "ok": True, "data": data}])


def _concluded(agent: LifeAgent, arguments: dict) -> tuple[str, dict]:
    """一次 conclude_activity 经宿主本地校验：（写进证据的协议错误串，回给模型的纠错正文）。"""
    error = agent.validate_react_decisions([_decision("conclude_activity", arguments)], False)
    return error, _feedback(error)




def test_an_unknown_activity_name_does_not_hide_an_unknown_view_in_another_item() -> None:
    """活动名和关系对象错误独立汇总，包含同一项中的不同字段。"""
    agent = _two_activities()
    _, payload = _concluded(agent, _conclude_args("散步", _WAIT, memories=[[], []], views=[[], [_view("陌生人")]]))
    assert payload["error_code"] == "NAME_NOT_FOUND"
    assert [(item["path"][4:], item["written"], item["error_code"]) for item in payload["items"]] == [
        (["items", 0, "activity_name"], "散步", "NAME_NOT_FOUND"),
        (["items", 1, "views", 0, "about"], "陌生人", "SUBJECT_NOT_KNOWN")]
    _, both = _concluded(agent, _conclude_args("散步", _WAIT, memories=[[], []], views=[[_view("王五")], []]))
    assert [item["path"][4:] for item in both["items"]] == [
        ["items", 0, "activity_name"], ["items", 0, "views", 0, "about"]]
    assert "散步" in payload["reason"] and "陌生人" in payload["reason"] and payload["fix"]




def test_argument_errors_are_written_per_field_without_any_validation_dump() -> None:
    """类型、范围、文字身份和多余字段全部定位，JSON 原值不字符串化。"""
    concluding = _prepared([{"turn": 0, "action": "observe", "ok": True, "data": _step_observation()}])
    memory = _memory("我记得 3e71a031bb3d4103 那会儿很累。", importance=1.5)
    arguments = {**_conclude_args(_WAIT, memories=[[memory]]), "extra": 1}
    error = concluding.validate_react_decisions([_decision("conclude_activity", arguments)], False)
    payload = _feedback(error)
    assert payload["error_code"] == "ARGUMENTS_INVALID"
    assert [item["path"][4:] for item in payload["items"]] == [
        ["items", 0, "memories", 0, "text"], ["items", 0, "memories", 0, "importance"], ["extra"]]
    assert [item["written"] for item in payload["items"]] == [memory["text"], 1.5, 1]
    assert not any(marker in LifeAgent._protocol_feedback_content(error) for marker in _DUMP_MARKERS)
    limited = _conclude_args(_WAIT, memories=[[_memory("长" * 1001)]])
    row = _feedback(concluding.validate_react_decisions([_decision("conclude_activity", limited)], False))["items"][0]
    assert row["path"][-5:] == ["items", 0, "memories", 0, "text"] and "1000" in row["why"]






def test_feedback_rejects_bare_strings_instead_of_reconstructing_positions() -> None:
    """不从旧日志猜测错误类别或参数位置。"""
    for text in ("OUTPUT_TRUNCATED: model output ended", "NAME_NOT_FOUND: missing object", "memory unavailable"):
        with pytest.raises(TypeError, match="structured error report"):
            _feedback(text)


def test_feedback_structure_survives_copying_and_evidence_stays_a_plain_string() -> None:
    """HostFeedback 复制后仍带结构（AS2 或证据链路可能复制协议错误串）；落进证据时是普通字符串。"""
    error = _prepared().validate_react_decisions([_decision(SIDE_TOOL, side_arguments(
        "interact", "不存在的对象"))], False)
    copied = copy.deepcopy(error)
    assert isinstance(copied, HostFeedback) and copied.report == error.report and copied == error
    assert type(str(error)) is str

#: 用内容 app 的完整调用：纠错给出的候选就是它。
_USE_APP = side_arguments("interact", "抖音")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,wrong,code,allowed", [
    (SIDE_TOOL, {**_USE_APP, "action": "wait"}, "ARGUMENTS_INVALID", ["purchase", "interact", "query"]),
    (SIDE_TOOL, side_arguments("purchase", "抖音"), "ACTION_NOT_OFFERED", ["interact"]),
    (MAIN_TOOL, main_arguments("interact", "抖音", "测试休息"), "ACTION_NOT_OFFERED", []),
])
async def test_reported_allowed_action_can_be_resubmitted_to_real_engine(engine_factory, tool, wrong, code, allowed):
    """真实引擎、LifeEnv 与脚本化模型：闭集之外的 action 带回这个工具允许的全部动作；已知对象不提供所写动作时带回它
    在这个工具下提供的动作与可以照抄的完整调用（把内容 app 交给主工具时候选属于副工具）。照纠错改写后原样提交即被受理，
    整次决策只登记一个世界请求。"""
    from tests.integration.runtime.current_contract_fixtures import decide, ToolResponse
    from tests.life.conftest import stage_day_plan
    engine = engine_factory()
    stage_day_plan(engine)
    _, requests, _, [result] = await decide(engine, "correct-action", [
        ToolResponse(tool, wrong, "wrong"), ToolResponse(SIDE_TOOL, _USE_APP, "correct")])
    assert result.status == "in_progress" and len(requests) == 2
    report = json.loads(requests[1]["messages"][-1]["content"])
    assert report["error_code"] == code and report["fix"] == host_defaults(code)[1]
    [item] = report["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert (item["written"], item["allowed_values"]) == (wrong["action"], allowed)
    if code == "ACTION_NOT_OFFERED":
        assert [(row["tool"], row["arguments"]) for row in item["candidates"]] == [(SIDE_TOOL, _USE_APP)]
    else:
        assert item["candidates"] == []
    [requested] = engine.context.store.read_agent_events("action_requested", 1)
    assert (requested.data["tool_name"], requested.data["request"]) == (SIDE_TOOL, _USE_APP)


def test_each_unknown_name_has_its_actual_item_position():
    """开始组的两条调用各写了一个不存在的对象：两条名称错误都报出，路径以各自真实的调用位置开头，副工具另带数组下标。"""
    agent = _prepared()
    main = _decision(MAIN_TOOL, main_arguments("interact", "不存在的对象甲", "歇一会儿"))
    side = _decision(SIDE_TOOL, side_arguments("interact", "不存在的对象乙"))
    for batch, paths in (([main, side], [[0, "function", "arguments", "target"],
                                         [1, "function", "arguments", "target", 0]]),
                         ([side, main], [[0, "function", "arguments", "target", 0],
                                         [1, "function", "arguments", "target"]])):
        report = _feedback(agent.validate_react_decisions(batch, False))
        assert [row["path"] for row in report["items"]] == [["tool_calls", *path] for path in paths]
        assert [row["written"] for row in report["items"]] == [item.args["target"] if item.action == MAIN_TOOL
                                                               else item.args["target"][0] for item in batch]
        assert all(row["error_code"] == "NAME_NOT_FOUND" for row in report["items"])
        assert report["fix"] == host_defaults("NAME_NOT_FOUND")[1]


def test_each_unknown_subject_keeps_the_nested_view_position():
    agent = _two_activities()
    _, report = _concluded(agent, _conclude_args(_WORK, _WAIT, memories=[[], []], views=[
        [_view("陌生人甲"), _view("陌生人乙")], [_view("陌生人丙")]]))
    assert [row["path"][-5:] for row in report["items"]] == [
        ["items", 0, "views", 0, "about"], ["items", 0, "views", 1, "about"],
        ["items", 1, "views", 0, "about"]]
