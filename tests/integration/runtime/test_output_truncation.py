"""输出截断与思考螺旋的验收（docs/design/10-runtime.md ，裁决 D10；验收台账「截断处理」）。

真实 LifeEngine、LifeEnv 与 SQLite 上，脚本化模型经真实 LifeAgent 决策（AS2 锁定提交的 ReAct 循环，只替换远程传输）：
思考用尽输出上限的截断回合（finish_reason=length、正文为空、没有工具调用）不进入后续请求，纠错为 OUTPUT_TRUNCATED 且在
本次决策中只出现一次（相同的纠错替换、不叠加，续接链前缀不动）；截断之后的那一次重试关闭思考并 tool_choice=required，max_tokens 不变、只开放
世界请求，其余请求（含只读问答）的参数不变；正文写到上限、空的 stop 回复、参数被截断的工具调用与没有选项的回复都不是截断回合，照现行
协议纠错、不重试；重试仍没有工具调用时按现行回合预算处理；重试的回合不留下缺思考的 assistant，
重试给出的世界请求结束本次决策、同一秒下一次决策断链；导出的质量报告 truncations 与运行记录的模型响应一致。
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from worldmock.agents.life_feedback import FEEDBACK_INSTRUCTION  # noqa: E402
from worldmock.agents.life_truncation import TRUNCATION_DETAIL, TRUNCATION_CONTENT  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import (_Environment, _GroundedCorrectionDispatcher,  # noqa: E402
                                                        _ModelResponse, _context, _wire_agent)
from tests.integration.life_host_fixtures import TruncatedResponse  # noqa: E402
from tests.integration.runtime.test_same_second_continuation import (_PLAN, _decide, _environment,  # noqa: E402
                                                             _plan_reply, _reply, _single, _wait_reply)
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (advance_next, context, stage_day_plan, start_arguments)  # noqa: F401
from worldmock.footprints.export import export_run  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.models import REQUEST_MODELS  # noqa: E402

#: 截断回合的思考（每次不同，用来确认它不进入任何后续请求）。
_SPIRALS = ("先想想要不要回家，家里没有吃的，外卖要等，还是先去楼下……", "再算一遍：回家要四十分钟，楼下那家没有菜单……")
_WAIT = start_arguments(["自己"], ["interact"], ["等待"])
_MEMORY_TOOLS = {"memory_recent", "memory_search", "memory_time_range"}
#: 截断纠错回给模型的正文（第四章结构，登记的原因与改法）。
_TRUNCATION_FEEDBACK = json.loads(TRUNCATION_CONTENT)


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证请求与纠错合同。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


class _TextOnly:
    """只有正文、没有工具调用的回复（finish_reason=stop）：不是截断，AS2 报 native tool_calls are required。"""

    def __init__(self, content: str = "我先想一想再说。") -> None:
        self._content = content
        message = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
        self.choices = [SimpleNamespace(finish_reason="stop", index=0, message=message)]

    def model_dump(self, *, mode: str) -> dict:
        """提供商形状的回复。"""
        assert mode == "json"
        message = {"role": "assistant", "content": self._content, "tool_calls": None}
        return {"model": "deepseek-flash", "choices": [{"finish_reason": "stop", "index": 0, "message": message}]}


class _Finished:
    """不是思考写满的回复：给定 finish_reason 与正文，可带一个开始活动的工具调用（参数原文可以是被截断的 JSON）。"""

    _REASONING = "想一想再调用工具。"

    def __init__(self, finish_reason: str, content: str, arguments: str | None = None) -> None:
        self._finish, self._content, self._arguments = finish_reason, content, arguments
        calls = None if arguments is None else [SimpleNamespace(
            id="call-cut", type="function", function=SimpleNamespace(name="start_activity", arguments=arguments))]
        message = SimpleNamespace(content=content, tool_calls=calls, reasoning_content=self._REASONING)
        self.choices = [SimpleNamespace(finish_reason=finish_reason, index=0, message=message)]

    def model_dump(self, *, mode: str) -> dict:
        """提供商形状的回复。"""
        assert mode == "json"
        calls = None if self._arguments is None else [{"id": "call-cut", "type": "function", "function": {
            "name": "start_activity", "arguments": self._arguments}}]
        message = {"role": "assistant", "content": self._content, "tool_calls": calls,
                   "reasoning_content": self._REASONING}
        return {"model": "deepseek-flash", "choices": [{"finish_reason": self._finish, "index": 0, "message": message}]}


class _NoChoices:
    """一个选项也没有的回复（choices 为空）：不是截断回合，AS2 报 missing response choices。"""

    def __init__(self) -> None:
        self.choices: list = []

    def model_dump(self, *, mode: str) -> dict:
        """提供商形状的回复。"""
        assert mode == "json"
        return {"model": "deepseek-flash", "choices": []}


def _is_retry(request: dict) -> bool:
    """请求是截断重试：关闭思考、tool_choice=required；另核对两类请求共有的参数不变。"""
    assert request["max_tokens"] == 8000 and request["max_retries"] == 0
    assert "reasoning_effort" not in request and "temperature" not in request
    assert all(item["function"]["strict"] is True for item in request["tools"])
    retry = (request["extra_body"], request["tool_choice"]) == ({"thinking": {"type": "disabled"}}, "required")
    assert retry or (request["extra_body"], request["tool_choice"]) == ({"thinking": {"type": "enabled"}}, "auto")
    return retry


def _tools(request: dict) -> set[str]:
    """请求实际开放的原生工具名。"""
    return {item["function"]["name"] for item in request["tools"]}


def _corrections(request: dict) -> list[str]:
    """请求中作为 user 消息的纠错（按出现先后的错误码）。"""
    codes = []
    for message in request["messages"][2:]:
        if message["role"] == "user":
            codes.append(json.loads(message["content"])["error_code"])
    return codes


def _decision_responses(engine, decision_id: str) -> list:
    """本决策落库的模型响应事件（按提交先后）。"""
    return [event for event in engine.context.store.read_agent_events("model_response", 1) if event.decision_id == decision_id]


@pytest.mark.asyncio
async def test_a_truncated_turn_is_dropped_and_retried_once_without_thinking(_engine_factory) -> None:
    """截断回合不进入后续请求：重试请求的消息是首个请求的两条消息加一条截断纠错（第四章结构，原因与改法是登记的说法），
    其中没有截断回合的思考；重试关闭思考、tool_choice=required、max_tokens 仍为 8000，只开放世界请求工具；重试给出的
    世界请求照常登记与结算，回复正文成为 self_note；证据里截断回合的协议错误串以 OUTPUT_TRUNCATED 开头，决策开始只写一次。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    _, requests, _ = await _single(environment, "spiral", [
        TruncatedResponse(_SPIRALS[0]), _reply("call-wait", "start_activity", _WAIT, None, "先歇一会儿。")])
    first, retry = requests
    assert [_is_retry(item) for item in requests] == [False, True]
    assert _tools(first) == set(REQUEST_MODELS) | {"query_world"} | _MEMORY_TOOLS and _tools(retry) == set(REQUEST_MODELS) - {"recall_task_summary"}
    assert retry["messages"][:2] == first["messages"] and len(retry["messages"]) == 3
    feedback = retry["messages"][2]
    assert feedback["role"] == "user" and json.loads(feedback["content"]) == _TRUNCATION_FEEDBACK
    assert _SPIRALS[0] not in json.dumps(retry, ensure_ascii=False)
    truncated, answered = _decision_responses(engine, "decision-spiral-1")
    assert truncated.data["error"] == f"OUTPUT_TRUNCATED: {TRUNCATION_DETAIL}" and answered.data["error"] is None
    assert truncated.data["response"]["choices"][0]["message"]["reasoning_content"] == _SPIRALS[0]
    [registered] = [event for event in engine.context.store.read_agent_events("action_requested", 1)
                    if event.decision_id == "decision-spiral-1"]
    assert registered.data["tool_name"] == "start_activity" and registered.data["self_note"] == "先歇一会儿。"
    assert engine.context.store.read_agent_events("action_result", 1)[-1].data["error_code"] is None
    assert [event.decision_id for event in engine.context.store.read_agent_events("decision_started", 1)].count(
        "decision-spiral-1") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "code"), [
    pytest.param(_Finished("length", "我先想想要不要回家，家里没有吃的，外卖要等……"), "TOOL_CALL_REQUIRED",
                 id="length_with_text"),
    pytest.param(_Finished("stop", ""), "TOOL_CALL_REQUIRED", id="empty_stop"),
    pytest.param(_Finished("length", "", '{"targets": ["自己"], "actions": ["inter'), "ARGUMENTS_NOT_JSON",
                 id="length_with_cut_tool_call"),
    pytest.param(_NoChoices(), "TOOL_CALL_REQUIRED", id="no_choices"),
])
async def test_only_a_reply_that_ran_out_while_thinking_is_a_truncation(_engine_factory, reply, code: str) -> None:
    """截断回合只指 finish_reason=length、正文为空且没有工具调用的回复（规格 7.2）：写到上限的正文、空的 stop 回复、
    参数被截断的工具调用与一个选项也没有的回复都不是思考写满——按现行协议纠错（没有工具调用、参数不是 JSON），证据不记
    OUTPUT_TRUNCATED，下一次请求照常开启思考、tool_choice=auto，不发截断重试。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _, requests, _ = await _single(_environment(engine), "not-truncated", [reply, *_wait_reply()])
    assert [_is_retry(item) for item in requests] == [False, False]
    assert json.loads(requests[1]["messages"][-1]["content"])["error_code"] == code
    assert _tools(requests[1]) == set(REQUEST_MODELS) | {"query_world"} | _MEMORY_TOOLS
    first, answered = _decision_responses(engine, "decision-not-truncated-1")
    assert not first.data["error"].startswith("OUTPUT_TRUNCATED") and answered.data["error"] is None


@pytest.mark.asyncio
async def test_identical_corrections_replace_each_other_across_consecutive_truncations(_engine_factory) -> None:
    """连续截断：截断、重试只写了正文、再截断、再重试。第二次截断的纠错替换第一次的（去掉旧的，接在末尾），每个请求里
    截断纠错至多一条；重试没有工具调用的那一回合同样丢弃，只留它的纠错；两次截断的思考都不进入任何后续请求。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    responses = [TruncatedResponse(_SPIRALS[0]), _TextOnly(), TruncatedResponse(_SPIRALS[1]),
                 _reply("call-wait", "start_activity", _WAIT, None)]
    _, requests, _ = await _single(_environment(engine), "twice", responses)
    assert [_is_retry(item) for item in requests] == [False, True, False, True]
    assert [_corrections(item) for item in requests] == [
        [], ["OUTPUT_TRUNCATED"], ["OUTPUT_TRUNCATED", "TOOL_CALL_REQUIRED"],
        ["TOOL_CALL_REQUIRED", "OUTPUT_TRUNCATED"]]
    assert all(item["role"] != "assistant" for request in requests for item in request["messages"])
    wire = json.dumps(requests[1:], ensure_ascii=False)
    assert _SPIRALS[0] not in wire and _SPIRALS[1] not in wire and "我先想一想再说。" not in wire
    errors = [event.data["error"] for event in _decision_responses(engine, "decision-twice-1")]
    assert errors == [f"OUTPUT_TRUNCATED: {TRUNCATION_DETAIL}", "TOOL_CALL_REQUIRED: At least one complete native tool call is required.",
                      f"OUTPUT_TRUNCATED: {TRUNCATION_DETAIL}", None]


@pytest.mark.asyncio
async def test_a_retry_that_is_truncated_again_is_not_retried_again(_engine_factory) -> None:
    """重试只发一次：重试本身又被截断时，它的纠错与前一条相同，替换而不叠加；下一次请求回到行动参数（开启思考、
    tool_choice=auto、记忆查询照常开放），不再重试。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    responses = [TruncatedResponse(_SPIRALS[0]), TruncatedResponse(_SPIRALS[1]), *_wait_reply()]
    _, requests, _ = await _single(_environment(engine), "again", responses)
    assert [_is_retry(item) for item in requests] == [False, True, False]
    assert [_corrections(item) for item in requests] == [[], ["OUTPUT_TRUNCATED"], ["OUTPUT_TRUNCATED"]]
    assert _tools(requests[2]) == set(REQUEST_MODELS) | {"query_world"} | _MEMORY_TOOLS
    assert requests[2]["messages"] == requests[1]["messages"]


@pytest.mark.asyncio
async def test_a_retry_still_without_a_tool_call_follows_the_turn_budget(_engine_factory) -> None:
    """重试仍没有工具调用时按现行回合预算处理：此后的请求照常开启思考（不再重试），每个非法回复耗一个回合，相同的纠错
    替换；四个回合用完仍没有世界请求即本次决策失败，不登记任何世界请求。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    ctx = context(engine, "exhausted")
    engine.begin_decision_round([ctx])
    responses = [TruncatedResponse(_SPIRALS[0]), _TextOnly(), _TextOnly("还是再想想。"), _TextOnly("再等等。")]
    with pytest.raises(RuntimeError, match="ReAct decision budget exhausted"):
        await _decide(_environment(engine), ctx, responses)
    requests = [event.data["request"] for event in engine.context.store.read_agent_events("model_request", 1)
                if event.decision_id == "decision-exhausted-1"]
    assert [_is_retry(item) for item in requests] == [False, True, False, False]
    assert [_corrections(item) for item in requests] == [
        [], ["OUTPUT_TRUNCATED"], ["OUTPUT_TRUNCATED", "TOOL_CALL_REQUIRED"],
        ["OUTPUT_TRUNCATED", "TOOL_CALL_REQUIRED", "TOOL_CALL_REQUIRED"]]
    feedback = [json.loads(item["content"]) for item in requests[3]["messages"]
                if item["role"] == "user" and item["content"].startswith('{"error_code"')]
    assert feedback[-2]["items"][0]["allowed_values"] != feedback[-1]["items"][0]["allowed_values"]
    assert _tools(requests[2]) == set(REQUEST_MODELS) | {"query_world"} | _MEMORY_TOOLS and _tools(requests[3]) == set(REQUEST_MODELS) - {"recall_task_summary"}
    assert [event for event in engine.context.store.read_agent_events("action_requested", 1)
            if event.decision_id == "decision-exhausted-1"] == []


@pytest.mark.asyncio
async def test_a_world_request_from_the_retry_ends_the_decision_and_breaks_the_same_second_chain(
        _engine_factory) -> None:
    """重试请求没有思考，续接链按无思考回合处理：重试给出的瞬时世界请求（修订今日计划）结束本次决策，同一秒的下一次
    决策不续接，首个请求只有系统层与六层两条消息；对照组（没有截断、回复带思考）同一秒照常续接。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    _, planned, _ = await _single(environment, "plan", [TruncatedResponse(_SPIRALS[0]),
                                                        _reply("call-plan", "revise_day_plan", _PLAN, None)])
    assert [_is_retry(item) for item in planned] == [False, True]
    assert engine.context.store.read_agent_events("action_result", 1)[-1].data["error_code"] is None
    observation, [request], _ = await _single(environment, "wait", _wait_reply())
    assert observation["continuation"] is None and len(request["messages"]) == 2
    control = _engine_factory()
    stage_day_plan(control)
    control_environment = _environment(control)
    await _single(control_environment, "plan", _plan_reply())
    observation, [request], _ = await _single(control_environment, "wait", _wait_reply())
    assert observation["continuation"] is not None and len(request["messages"]) > 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("retried", "code", "named"), [
    pytest.param(("call-bad", "start_activity", start_arguments(["不存在的对象"], ["interact"], ["等待"])),
                 "NAME_NOT_FOUND", "不存在的对象", id="misspelled_name"),
    pytest.param(("call-memory", "memory_recent", {"limit": 3}), "TOOL_CALL_REQUIRED", "memory_recent",
                 id="memory_query"),
])
async def test_a_rejected_retry_turn_is_dropped_so_a_continued_decision_keeps_every_thought(
        _engine_factory, retried: tuple, code: str, named: str) -> None:
    """同一秒续接的决策里截断后，重试写错名称或去查记忆（重试不开放记忆查询，不执行）都被纠错：重试的回合同截断回合
    一样不留在历史中，纠错作为 user 消息接在后面；下一次请求续接链前缀逐字节不变，其中每个 assistant 都带思考（发送前
    自检不失败），最后带思考的世界请求照常登记。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", _plan_reply())
    call_id, tool, arguments = retried
    observation, requests, _ = await _single(environment, "wait", [
        TruncatedResponse(_SPIRALS[0]), _reply(call_id, tool, arguments, None), *_wait_reply()])
    assert observation["continuation"] is not None
    first, retry, last = requests
    assert [_is_retry(item) for item in requests] == [False, True, False]
    chain = first["messages"]
    assert retry["messages"][:-1] == chain and json.loads(retry["messages"][-1]["content"]) == _TRUNCATION_FEEDBACK
    assert last["messages"][:len(chain)] == chain
    assert [json.loads(item["content"])["error_code"] for item in last["messages"][len(chain):]] == [
        "OUTPUT_TRUNCATED", code]
    detail = json.loads(last["messages"][-1]["content"])["items"]
    assert any(item["written"] == named for item in detail)
    assert all(item["role"] == "user" for item in last["messages"][len(chain):])
    assert all(item.get("reasoning_content") for item in last["messages"] if item["role"] == "assistant")
    assert call_id not in json.dumps(last, ensure_ascii=False)
    [registered] = [event for event in engine.context.store.read_agent_events("action_requested", 1)
                    if event.decision_id == "decision-wait-1"]
    assert registered.data["tool_name"] == "start_activity"


@pytest.mark.asyncio
async def test_a_truncation_in_a_continued_decision_leaves_the_chain_prefix_untouched(_engine_factory) -> None:
    """替换只在本次决策自有的消息里进行：同一秒的上一决策有过截断（重试只写了正文），最后以带思考的世界请求结束，链照常
    成立、链里带着那条截断纠错；本决策再截断时，新的截断纠错接在链后，链前缀逐字节不变，链里那条相同的纠错不去掉。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    _, planned, _ = await _single(environment, "plan", [TruncatedResponse(_SPIRALS[0]), _TextOnly(), *_plan_reply()])
    assert [_is_retry(item) for item in planned] == [False, True, False]
    observation, requests, _ = await _single(environment, "wait", [TruncatedResponse(_SPIRALS[1]), *_wait_reply()])
    assert observation["continuation"] is not None
    first, retry = requests
    assert first["messages"][:len(planned[-1]["messages"])] == planned[-1]["messages"]
    assert retry["messages"][:-1] == first["messages"] and _is_retry(retry)
    assert _corrections(retry) == ["OUTPUT_TRUNCATED", "TOOL_CALL_REQUIRED", "OUTPUT_TRUNCATED"]


@pytest.mark.asyncio
async def test_readonly_questions_keep_their_parameters_and_a_truncated_answer_is_retried_with_finish_only() -> None:
    """只读问答与行动经同一请求入口：没有截断时只读请求的参数不变（开启思考、tool_choice=auto、8000）；只读回答被截断时
    按状态同样重试一次，关闭思考、tool_choice=required，只开放 finish。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-finish-1", "finish", '{"answer":"first"}'),
        TruncatedResponse(_SPIRALS[0]),
        _ModelResponse("call-finish-2", "finish", '{"answer":"second"}'),
    ])
    agent = LifeAgent()
    _wire_agent(agent, _Environment(), dispatcher, 4)
    question = ReactLoopRequest(0, _context().observed_at, [], "What happened?", True)
    assert await agent.run_react_loop(question) == "first"
    assert await agent.run_react_loop(question) == "second"
    assert [_is_retry(item) for item in dispatcher.requests] == [False, False, False, True]
    assert [_tools(item) for item in dispatcher.requests] == [
        _MEMORY_TOOLS | {"finish"}, _MEMORY_TOOLS | {"finish"}, _MEMORY_TOOLS | {"finish"}, {"finish"}]
    retry = dispatcher.requests[3]
    assert retry["messages"][:-1] == dispatcher.requests[2]["messages"]
    assert json.loads(retry["messages"][-1]["content"]) == _TRUNCATION_FEEDBACK
    assert _SPIRALS[0] not in json.dumps(retry, ensure_ascii=False)


@pytest.mark.asyncio
async def test_the_exported_quality_report_counts_the_truncations_the_run_recorded(_engine_factory, tmp_path) -> None:
    """真实导出路径：两次决策共三次截断（其中一次输出 8001 个 token），导出的 quality.json 的 truncations 与导出事件中
    finish_reason=length 的模型响应逐项一致：次数、按用途分列、浪费的输出 token 与其中的思考 token。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", [TruncatedResponse(_SPIRALS[0], 8001),
                                        _reply("call-plan", "revise_day_plan", _PLAN, None)])
    await _single(environment, "wait", [TruncatedResponse(_SPIRALS[1]), _TextOnly(), TruncatedResponse(_SPIRALS[0]),
                                        _reply("call-wait", "start_activity", _WAIT, None)])
    advance_next(engine)
    export = tmp_path / "export"
    export_run(engine.context.store, engine.context.store.last_event_seq, export)
    quality = json.loads((export / "quality.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (export / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    recorded = [row["data"]["response"] for row in rows if row["event_type"] == "model_response"
                and row["data"]["response"]["choices"][0].get("finish_reason") == "length"]
    assert len(recorded) == 3
    assert quality["truncations"] == {
        "truncated_responses": 3, "purpose_counts": {"decision": 3},
        "wasted_output_tokens": sum(item["usage"]["completion_tokens"] for item in recorded),
        "reasoning_tokens": sum(item["usage"]["completion_tokens_details"]["reasoning_tokens"] for item in recorded),
        "responses_missing_output_tokens": 0, "responses_missing_reasoning_tokens": 0,
        "method": quality["truncations"]["method"]}
    assert quality["truncations"]["wasted_output_tokens"] == 24001 == quality["truncations"]["reasoning_tokens"]
    assert quality["schema_version"] == 6
