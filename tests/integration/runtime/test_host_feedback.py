"""宿主协议纠错与沙盒拒绝的错误合同（ProtocolFeedback 五字段结构、阶段错误与实机纠错重试）。"""

from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.integration.life_host_fixtures import TruncatedResponse  # noqa: E402
from tests.integration.runtime.current_contract_fixtures import decide, resolved_binding  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import (  # noqa: E402
    _ModelResponse,
    _MultipleModelResponse,
    _SandboxStub,
    _WAIT,
    _WAIT_CHOICE,
    _WORK,
    _conclude_args,
    _memory,
    _observations,
    _person_7,
    _step_observation,
    _wakeup,
    _with_activities,
)
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, main_arguments, side_arguments, stage_day_plan  # noqa: E402
from worldmock.agents.life_agent import LifeAgent, _life_turn_tools  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.agents.protocol.agent_loop import LoopToolCall, validate_single_tool_call  # noqa: E402
from worldmock.agents.protocol.life_feedback import (  # noqa: E402
    FEEDBACK_INSTRUCTION,
    ProtocolFeedback,
    report_content,
    stage_error,
)
from worldmock.state.interaction.errors import ERROR_KEYS, host_defaults  # noqa: E402

_DUMP_MARKERS = ("input_value", "input_type", "http://", "https://", "errors.pydantic", "validation error",
                 "Value error", "For further information")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证纠错合同。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _NoToolResponse:
    """没有任何工具调用、只有回复正文的模型响应。"""

    def __init__(self) -> None:
        message = SimpleNamespace(tool_calls=[], content="我想先想一想。", reasoning_content="思考")
        self.choices = [SimpleNamespace(message=message)]

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return {"choices": [{"message": {"content": "我想先想一想。", "tool_calls": [], "reasoning_content": "思考"}}]}


def _feedback(error: ProtocolFeedback) -> dict:
    """一条协议纠错回给模型的纠错正文。"""
    return json.loads(report_content(error.report))


async def _corrected_loop(bad_responses: list, observation: dict | None = None) -> tuple[list, list, list]:
    """真实通用循环：先给非法响应，最后提交等待；返回（纠错正文列表，响应证据错误串，请求列表）。"""
    responses = [*bad_responses, _ModelResponse("call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False))]
    requests: list[dict] = []

    class _Client:
        async def call(self, request):
            requests.append(copy.deepcopy(request))
            return responses.pop(0)

    sandbox = _SandboxStub(observation or _step_observation())
    await LifeAgent(_person_7()).decide_wakeup(
        _wakeup(observation or _step_observation()), sandbox, _binding_for(_Client(), len(responses) + 1),
    )
    assert len(sandbox.calls) == 1
    feedback = [json.loads(req["messages"][-1]["content"]) for req in requests[1:]]
    errors = [item.error for item in sandbox.evidence if item.phase == "response" and item.error is not None]
    ev_feedbacks = [item.response["protocol_feedback"] for item in sandbox.evidence
                    if item.phase == "response" and item.error is not None]
    return feedback, errors, requests, ev_feedbacks


def _binding_for(client, max_turns: int):
    return resolved_binding(client, max_turns=max_turns)


@pytest.mark.asyncio
async def test_protocol_feedback_is_the_shared_structure_with_the_fixed_instruction() -> None:
    """损坏 JSON 与缺字段以配对 tool 消息纠错，无调用保留五键证据反馈且绝不插入第二条 user 消息。"""
    missing = copy.deepcopy(_WAIT_CHOICE)
    missing.pop("action")
    broken = '{"target": "自己家" "action":"interact", "detail": "歇一会儿"}'
    _, errors, requests, ev_feedbacks = await _corrected_loop([
        _ModelResponse("call-json", MAIN_TOOL, broken),
        _ModelResponse("call-shape", MAIN_TOOL, json.dumps(missing, ensure_ascii=False)),
        _NoToolResponse(),
    ])
    paired = [json.loads(req["messages"][-1]["content"]) for req in requests[1:3]]
    assert [item["error_code"] for item in paired] == ["ARGUMENTS_NOT_JSON", "ARGUMENTS_INVALID"]
    assert requests[3]["messages"] == requests[2]["messages"]
    assert all([m["role"] for m in req["messages"] if m["role"] == "user"] == ["user"] for req in requests)
    assert [item["error_code"] for item in ev_feedbacks] == ["ARGUMENTS_NOT_JSON", "ARGUMENTS_INVALID", "TOOL_CALL_REQUIRED"]
    for item in paired:
        assert list(item) == [*ERROR_KEYS, "instruction"] and item["instruction"] == FEEDBACK_INSTRUCTION
        assert item["reason"] and item["unmet"] == []
        assert item["fix"] == host_defaults(item["error_code"])[1]
        assert not any(marker in json.dumps(item) for marker in _DUMP_MARKERS)
    assert [item["fix"] for item in ev_feedbacks] == [
        "Submit a complete JSON object.", "Correct every reported parameter.",
        "Submit one world request, the two start tools together, or only read queries."]
    assert paired[0]["items"][0]["path"] == ["tool_calls", 0, "function", "arguments"]
    assert "line 1, column" in paired[0]["items"][0]["expected"]
    row = paired[1]["items"][0]
    assert row["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert row["written"] is None and "required" in row["expected"]
    assert row["allowed_values"] == ["travel", "interact", "sleep"]
    assert ev_feedbacks[2]["items"][0]["path"] == ["tool_calls"]
    assert [type(error) for error in errors] == [str, str, str]
    assert [error.split(":", 1)[0] for error in errors] == [item["error_code"] for item in ev_feedbacks]
    assert len(requests) == 4


def test_argument_errors_are_written_per_field_without_any_validation_dump() -> None:
    """类型、范围、十六进制身份与多余字段全部精确定位，JSON 原值不字符串化。"""
    _, models = _life_turn_tools(2, False)
    memory = _memory("我记得 3e71a031bb3d4103 那会儿很累。", importance=1.5)
    arguments = {**_conclude_args(_WAIT, memories=[[memory]]), "extra": 1}
    error = validate_single_tool_call(LoopToolCall("conclude_activity", arguments, "c1"), models)
    payload = _feedback(error)
    assert payload["error_code"] == "ARGUMENTS_INVALID"
    assert [item["path"][4:] for item in payload["items"]] == [
        ["items", 0, "memories", 0, "text"], ["items", 0, "memories", 0, "importance"], ["extra"]]
    assert [item["written"] for item in payload["items"]] == [memory["text"], 1.5, 1]
    assert not any(marker in report_content(error.report) for marker in _DUMP_MARKERS)
    limited = _conclude_args(_WAIT, memories=[[_memory("长" * 1001)]])
    row = _feedback(validate_single_tool_call(LoopToolCall("conclude_activity", limited, "c2"), models))["items"][0]
    assert row["path"][-5:] == ["items", 0, "memories", 0, "text"] and "1000" in row["why"]


@pytest.mark.asyncio
async def test_protocol_feedback_is_a_structure_with_its_evidence_text() -> None:
    """ProtocolFeedback 是不可变结构；模型证据的 error 与它的 text 一致。"""
    _, models = _life_turn_tools(2, False)
    wrong = {**_WAIT_CHOICE, "action": "bad"}
    error = validate_single_tool_call(LoopToolCall(MAIN_TOOL, wrong, "c1"), models)
    copied = copy.deepcopy(error)
    assert isinstance(error, ProtocolFeedback) and not isinstance(error, str)
    assert copied == error and copied.report == error.report
    _, errors, _, _ = await _corrected_loop([
        _ModelResponse("call-bad", MAIN_TOOL, json.dumps(wrong, ensure_ascii=False)),
    ])
    assert errors == [error.text] and type(errors[0]) is str


@pytest.mark.parametrize("code,path,text", [
    ("MODEL_SERVICE_FAILED", ("tool_calls",),
     "MODEL_SERVICE_FAILED: The model service failed before a response was available."),
    ("EVIDENCE_WRITE_FAILED", ("tool_calls",), "EVIDENCE_WRITE_FAILED: Model evidence could not be persisted."),
    ("TOOL_SERVICE_FAILED", ("tool_calls", 1),
     "TOOL_SERVICE_FAILED: The tool service failed before a result was available."),
])
def test_stage_errors_keep_their_text_and_carry_the_feedback(code: str, path: tuple, text: str) -> None:
    """三个阶段码的异常仍是 RuntimeError，挂着带位置信息的 ProtocolFeedback。"""
    error = stage_error(code, path)
    assert type(error) is RuntimeError
    assert error.args == (text,) and str(error) == text
    assert repr(error) == f"RuntimeError({text!r})"
    assert error.feedback.text == text and error.feedback.report.error_code == code
    assert [tuple(item.path) for item in error.feedback.report.items] == [path]
    assert [item.error_code for item in error.feedback.report.items] == [code]


_USE_APP = side_arguments("interact", "抖音")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,wrong,code,allowed", [
    (SIDE_TOOL, {**_USE_APP, "action": "wait"}, "ARGUMENTS_INVALID", ["purchase", "interact"]),
    (SIDE_TOOL, side_arguments("purchase", "抖音"), "ACTION_NOT_OFFERED", ["interact"]),
    (MAIN_TOOL, main_arguments("interact", "抖音", "测试休息"), "ACTION_NOT_OFFERED", []),
])
async def test_reported_allowed_action_can_be_resubmitted_to_real_engine(_engine_factory, tool, wrong, code, allowed):
    """真实引擎与脚本化模型：action 非枚举在本地纠错，对象不支持所写动作在沙盒拒绝并带回候选，按纠错重提即受理。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _, requests, answer, results = await decide(engine, "correct-action", [
        _ModelResponse("wrong", tool, json.dumps(wrong, ensure_ascii=False)),
        _ModelResponse("correct", SIDE_TOOL, json.dumps(_USE_APP, ensure_ascii=False)),
    ])
    assert len(requests) == 2 and results[-1].status == "in_progress"
    tool_msg = json.loads(requests[1]["messages"][-1]["content"])
    if code == "ARGUMENTS_INVALID":
        assert answer == [SIDE_TOOL]
        assert tool_msg["error_code"] == code and tool_msg["fix"] == host_defaults(code)[1]
        [item] = tool_msg["items"]
        assert (item["written"], item["allowed_values"], item["candidates"]) == (wrong["action"], allowed, [])
    else:
        assert answer == [tool, SIDE_TOOL] and results[0].status == "rejected"
        res_block = tool_msg["result"]
        assert res_block["error_code"] == code and res_block["details"]["fix"] == host_defaults(code)[1]
        [item] = res_block["details"]["items"]
        assert (item["written"], item["allowed_values"]) == (wrong["action"], allowed)
        assert [(row["tool"], row["arguments"]) for row in item["candidates"]] == [(SIDE_TOOL, _USE_APP)]


def test_each_unknown_name_in_sandbox_keeps_its_item_position(_engine_factory) -> None:
    """沙盒分别核对两个开始工具调用里的未知对象名，均以 NAME_NOT_FOUND 返回。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    r_main = sandbox.dispatch_world_tool(
        MAIN_TOOL, main_arguments("interact", "不存在的对象甲", "歇一会儿"), f"{wakeup.wakeup_id}:action:0001",
    )
    r_side = sandbox.dispatch_world_tool(
        SIDE_TOOL, side_arguments("interact", "不存在的对象乙"), f"{wakeup.wakeup_id}:action:0002",
    )
    for dispatched, written in ((r_main, "不存在的对象甲"), (r_side, "不存在的对象乙")):
        assert not dispatched.accepted
        payload = dispatched.tool_message_payload["result"]
        assert payload["error_code"] == "NAME_NOT_FOUND"
        assert any(item["written"] == written for item in payload["details"]["items"])
