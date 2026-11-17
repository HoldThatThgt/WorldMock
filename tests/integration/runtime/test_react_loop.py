"""回合循环（docs/design/06-agent.md「宿主模型协议」）：回合与调用身份、每回合的工具面与请求字典、证据次序、纠错消息、
续接前缀与交出去的对象不再改动。

用真实 LifeAgent、按五个方法记录调用的引擎替身（模型证据写入时当场存下请求的规范 JSON）与按脚本应答的 call(request)
模型客户端替身；失败发送进证据的两例换成只替换 litellm 发送的真实 ModelClient。观察取协议测试的合成观察。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Mapping
from types import SimpleNamespace

import pytest
from litellm.exceptions import APIConnectionError, BadRequestError

from tests.integration.life_host_fixtures import TruncatedResponse
from tests.integration.runtime.test_life_agent_protocol import (
    _SEARCH,
    _SEARCH_ANSWER,
    _WAIT_CHOICE,
    _Environment,
    _ModelResponse,
    _MultipleModelResponse,
    _context,
    _observations,
    _wire_agent,
)
from tests.life.conftest import MAIN_TOOL
from tests.unit.runtime.model_client_fixtures import provider_error, scripted_client
from worldmock.agents.life_agent import ACTION_THINKING
from worldmock.agents.prompt import prompt_capacity
from worldmock.agents.prompt.prompt_capacity import FittedRequest, ProgressBudget
from worldmock.agents.protocol import react_loop
from worldmock.agents.protocol.life_continuation import end_continuation, send_check
from worldmock.agents.protocol.life_feedback import FEEDBACK_INSTRUCTION, report_content, stage_feedback
from worldmock.agents.protocol.life_truncation import TRUNCATION_CONTENT
from worldmock.agents.protocol.react_loop import (
    TURNS_EXHAUSTED,
    ReactDecision,
    ReactLoop,
    accepted_assistant,
    model_call_id,
    serialized_response,
)
from worldmock.configuration.model.model_client import REQUEST_KEYS, LLMDispatchError
from worldmock.foundation.serialization import canonical_json
from worldmock.state.interaction.requests import REQUEST_MODELS
from worldmock.state.model.exchange import assistant_message, wire_tool_call

#: 只剩最后一个回合或截断重试时开放的六个世界请求（REQUEST_MODELS 的次序，去掉回看任务）。
_WORLD_TOOLS = [name for name in REQUEST_MODELS if name != "recall_task_summary"]
#: 其余回合另开放回看任务与检索记忆，共八个。
_ALL_TOOLS = [*_WORLD_TOOLS, "recall_task_summary", "search_memory"]
#: 模型服务失败阶段写进证据的文字。
_MODEL_FAILED = "MODEL_SERVICE_FAILED: The model service failed before a response was available."


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """容量预检使用合成元数据的固定窗口，不读外部模型资料。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _Engine(_Environment):
    """按五个方法记录调用的引擎替身：记下调用次序，模型证据写入时当场存下请求的规范 JSON。"""

    def __init__(self, log: list) -> None:
        super().__init__()
        self.log = log
        self.snapshots: list[bytes] = []

    def observe(self, context):
        """交出合成观察的副本。"""
        self.log.append("observe")
        return copy.deepcopy(_observations()[0]["data"])

    def read_query(self, context, tool, request):
        """记下只读查询并照协议替身回答。"""
        self.log.append("read_query")
        return super().read_query(context, tool, request)

    def submit_action(self, call):
        """记下世界请求的登记并照协议替身给出回执。"""
        self.log.append("submit_action")
        return super().submit_action(call)

    def record_model_exchange(self, exchange) -> None:
        """记下证据阶段，并当场存下请求的规范 JSON。"""
        self.log.append(f"evidence:{exchange.phase}")
        self.snapshots.append(canonical_json(exchange.request))
        super().record_model_exchange(exchange)


class _Model:
    """按脚本应答的模型客户端替身：call(request) 只收一个位置参数，记下收到的请求对象本身。"""

    def __init__(self, responses: list, log: list) -> None:
        self.responses, self.received, self.log = list(responses), [], log

    async def call(self, request):
        """记下请求后按脚本给出响应或抛出脚本里的异常。"""
        self.log.append("model")
        self.received.append(request)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _person(responses: list, max_turns: int = 4) -> tuple:
    """真实 LifeAgent 接上两个替身；返回（人物, 引擎替身, 模型替身）。"""
    log: list = []
    engine, model = _Engine(log), _Model(responses, log)
    return _wire_agent(engine, model, max_turns), engine, model


def _observation() -> dict:
    """本次决策的合成观察：人物在家，可以照工具层写的名字开始休息。"""
    return copy.deepcopy(_observations()[0]["data"])


async def _run(agent, observation: dict | None = None) -> tuple[list[ReactDecision], list[dict]]:
    """在一份观察上跑回合循环，返回登记的世界请求与循环结束时的消息历史。"""
    agent._context = _context()
    budget, history = agent._prepare(observation or _observation())
    loop = ReactLoop(agent, _context(), budget, history)
    return await loop.run(), loop._history


def _world(call_id: str = "call-world") -> _ModelResponse:
    """开始休息的世界请求（带思考）。"""
    return _ModelResponse(call_id, MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False),
                          reasoning_content="先歇一会儿。")


def _search(call_id: str = "call-search") -> _ModelResponse:
    """检索今天记忆的只读查询（带思考）。"""
    return _ModelResponse(call_id, "search_memory", _SEARCH, reasoning_content="想一想今天做过什么。")


def _invented(call_id: str = "call-bad", name: str = "invented_tool", reasoning: str | None = "试一试。"):
    """调用本回合没有开放的工具：调用结构完整，按协议纠错。"""
    return _ModelResponse(call_id, name, "{}", reasoning_content=reasoning)


def _text_only(content: str) -> SimpleNamespace:
    """只有回复正文、没有工具调用的响应。"""
    message = SimpleNamespace(content=content, tool_calls=[], reasoning_content=None)
    document = {"choices": [{"message": {"content": content, "tool_calls": []}}]}
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           model_dump=lambda *, mode: copy.deepcopy(document))


def _feedback(message: dict) -> dict:
    """解析一条纠错消息的正文。"""
    return json.loads(message["content"])


def _broken_pair(kind: str) -> _MultipleModelResponse:
    """两条本回合没有开放的工具调用，按 kind 把调用结构弄得不完整（或不改）。"""
    reply = _MultipleModelResponse([("call-1", "invented_one", "{}"), ("call-2", "invented_two", "{}")])
    calls = reply.choices[0].message.tool_calls
    if kind == "missing":
        calls.clear()
    elif kind == "type":
        calls[0].type = "custom"
    elif kind == "blank":
        calls[0].id = "  "
    elif kind == "name":
        calls[0].function.name = " "
    elif kind == "arguments":
        calls[0].function.arguments = {}
    elif kind == "duplicate":
        calls[1].id = " call-1 "
    return reply


_BROKEN = ["missing", "type", "blank", "name", "arguments", "duplicate"]


@pytest.mark.asyncio
async def test_used_up_turns_stop_with_the_budget_fault_and_no_world_request(caplog) -> None:
    """回合上限个回合都没有登记世界请求：先写一条错误日志，再以回合用完的文字停止；模型恰好调用回合上限次。"""
    agent, engine, model = _person([_invented("call-1"), _invented("call-2")], max_turns=2)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await _run(agent)
    assert str(failure.value) == TURNS_EXHAUSTED == "ReAct decision budget exhausted without a world request"
    assert type(failure.value) is RuntimeError
    assert "LifeAgent 7 used all 2 model turns without a world request" in [
        record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    assert len(model.received) == 2 and engine.calls == []


@pytest.mark.asyncio
async def test_every_model_call_is_a_turn_with_the_next_call_identity() -> None:
    """检索、纠错、世界请求三个回合：第 t 回合的调用身份是「决策身份:model:t:attempt:1」，请求与响应证据成对同号。"""
    agent, engine, _ = _person([_search(), _invented(), _world()])
    await _run(agent)
    identities = [f"decision:model:{turn:04d}:attempt:1" for turn in (1, 2, 3)]
    assert [(item.phase, item.call_id) for item in engine.evidence] == [
        (phase, identity) for identity in identities for phase in ("request", "response")]
    assert {item.attempt for item in engine.evidence} == {1}
    assert model_call_id("decision", 12) == "decision:model:0012:attempt:1"


@pytest.mark.asyncio
async def test_tools_drop_the_read_queries_on_the_last_turn_and_on_the_truncation_retry() -> None:
    """四个回合：检索、截断、截断重试里写错、最后一个回合登记；截断重试与最后一个回合只有六个世界请求，其余八个。"""
    agent, _, model = _person([_search(), TruncatedResponse("想了很多。"), _invented("call-retry"), _world()])
    await _run(agent)
    names = [[tool["function"]["name"] for tool in request["tools"]] for request in model.received]
    assert names == [_ALL_TOOLS, _ALL_TOOLS, _WORLD_TOOLS, _WORLD_TOOLS]
    assert [request["tool_choice"] for request in model.received] == ["auto", "auto", "required", "auto"]


@pytest.mark.parametrize("retry", [False, True], ids=["action", "retry"])
def test_turn_request_has_the_six_client_keys_in_order(retry: bool) -> None:
    """请求字典恰好六个键，次序是 REQUEST_KEYS；平时思考开启、工具自选，截断重试关闭思考、必须调用工具。"""
    agent, _, _ = _person([])
    identity = agent.inputs.model_identity
    messages, tools = [{"role": "user", "content": "x"}], agent.turn_tools(4, retry)
    request = agent.turn_request(messages, tools, retry)
    assert tuple(request) == REQUEST_KEYS
    assert request["model"] == identity["requested_model"]
    assert request["messages"] is messages and request["tools"] is tools
    assert request["tool_choice"] == ("required" if retry else "auto")
    assert request["max_tokens"] == identity["stages"]["action"]["max_tokens"]
    assert request["extra_body"] == {"thinking": {"type": "disabled" if retry else "enabled"}}
    assert request["extra_body"] is not ACTION_THINKING


@pytest.mark.asyncio
async def test_a_condensed_request_is_sent_but_the_history_keeps_the_original_progress_layer(monkeypatch) -> None:
    """容量预检改写过的请求就是发出并留证的请求；回合之后的历史仍是原来的消息，浓缩只出现在发出的请求里。"""
    real = react_loop.fit_request

    def condensing(request, metadata, budget):
        """真实预检之后把 user 消息换成浓缩过的样子，消息条数不变。"""
        fitted = real(request, metadata, budget)
        messages = [dict(item) for item in fitted.request["messages"]]
        messages[1]["content"] = "condensed"
        return FittedRequest({**fitted.request, "messages": messages}, fitted.prompt_tokens)

    monkeypatch.setattr(react_loop, "fit_request", condensing)
    agent, engine, model = _person([_search(), _world()])
    _, history = await _run(agent)
    original = agent._prepare(_observation())[1][1]
    assert [request["messages"][1]["content"] for request in model.received] == ["condensed", "condensed"]
    assert engine.evidence[0].request is model.received[0]
    assert history[1] == original and history[1]["content"] != "condensed"


def _continued_observation() -> dict:
    """带续接链的合成观察：上一决策的末次请求是同一份新快照，最终 assistant 登记了 prev-call。"""
    observation = _observation()
    agent, _, _ = _person([])
    _, fresh = agent._prepare(observation)
    observation["continuation"] = {
        "messages": fresh,
        "assistant": assistant_message("", [wire_tool_call("prev-call", MAIN_TOOL, json.dumps(_WAIT_CHOICE))],
                                       "上一决策的思考。"),
        "tool_results": [{"tool_call_id": "prev-call", "result": {"status": "in_progress"}}],
        "events": []}
    return observation


@pytest.mark.asyncio
async def test_capacity_ended_continuation_keeps_later_turns_on_the_fresh_snapshot(monkeypatch) -> None:
    """容量第 0 步结束续接：第一回合发出的请求换成新快照，之后的回合都从新快照续写，历史里不再有上一决策的链。"""
    monkeypatch.setattr(prompt_capacity, "request_token_count",
                        lambda request: 10 ** 9 if "prev-call" in json.dumps(request["messages"]) else 1000)
    agent, _, model = _person([_search(), _world()])
    _, history = await _run(agent, _continued_observation())
    assert all("prev-call" not in json.dumps(request["messages"]) for request in model.received)
    assert [item["role"] for item in model.received[1]["messages"]] == ["system", "user", "assistant", "tool"]
    assert "prev-call" not in json.dumps(history)


@pytest.mark.asyncio
async def test_request_evidence_comes_before_the_send_and_the_response_after_validation() -> None:
    """一回合的次序：请求证据、发送、解析校验后的响应证据、登记；客户端收到的唯一参数就是请求证据里的那个字典。"""
    agent, engine, model = _person([_invented(), _world()])
    await _run(agent)
    assert engine.log == ["evidence:request", "model", "evidence:response",
                          "evidence:request", "model", "evidence:response", "submit_action"]
    assert [model.received[0], model.received[1]] == [engine.evidence[0].request, engine.evidence[2].request]
    assert model.received[0] is engine.evidence[0].request and model.received[1] is engine.evidence[2].request
    assert engine.evidence[1].request is engine.evidence[0].request
    assert engine.evidence[1].response["protocol_feedback"]["error_code"] == "TOOL_CALL_REQUIRED"


@pytest.mark.asyncio
async def test_a_failed_send_records_the_model_service_stage_and_reraises_the_original() -> None:
    """发送抛出异常：写一份模型服务阶段的失败证据（同一请求对象，response 只带纠错报告，原异常的 repr 不进证据），再把
    原异常原样上抛。"""
    injected = OSError("connection reset")
    agent, engine, model = _person([injected])
    with pytest.raises(OSError) as failure:
        await _run(agent)
    assert failure.value is injected
    request, closing = engine.evidence
    assert (request.phase, closing.phase) == ("request", "failure")
    assert closing.request is request.request is model.received[0]
    assert closing.error == _MODEL_FAILED
    assert closing.response == {"protocol_feedback": stage_feedback("MODEL_SERVICE_FAILED", ("tool_calls",))
                                .report.payload()}
    assert closing.transport_attempts == () and "connection reset" not in json.dumps(closing.response)


@pytest.mark.asyncio
async def test_a_cancelled_send_records_no_failure_evidence() -> None:
    """发送被取消：取消原样传出，只留下发送前的请求证据。"""
    agent, engine, _ = _person([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await _run(agent)
    assert [item.phase for item in engine.evidence] == ["request"]


@pytest.mark.asyncio
async def test_a_failed_send_carries_the_failed_sends_the_client_collected(monkeypatch) -> None:
    """真实模型客户端先传输失败、等两秒重发后以不可重试的错误结束：失败证据带上这次调用收集到的两次失败发送，
    再把客户端的最终失败原样上抛。"""
    agent, engine, _ = _person([])
    client, _, _ = scripted_client(monkeypatch, [provider_error(APIConnectionError), provider_error(BadRequestError)],
                                   clock=[0.0], transport_max_retries=1)
    agent.model_client = client
    with pytest.raises(LLMDispatchError):
        await _run(agent)
    closing = engine.evidence[-1]
    assert closing.phase == "failure"
    assert [item["error"] for item in closing.transport_attempts] == ["APIConnectionError", "BadRequestError"]


@pytest.mark.asyncio
async def test_a_response_after_a_failed_send_carries_that_send_in_its_evidence(monkeypatch) -> None:
    """真实模型客户端第一次传输失败、等两秒重发成功：响应证据带上那一次失败发送。"""
    agent, engine, _ = _person([])
    client, _, _ = scripted_client(monkeypatch, [provider_error(APIConnectionError), _world()],
                                   clock=[0.0], transport_max_retries=1)
    agent.model_client = client
    await _run(agent)
    assert engine.evidence[-1].phase == "response"
    assert engine.evidence[-1].transport_attempts == (
        {"attempt": 1, "error": "APIConnectionError", "failed_after_ms": 0, "wait_ms": 2000},)


def test_serialized_response_accepts_a_model_dump_or_a_mapping_and_rejects_the_rest(caplog) -> None:
    """有 model_dump 取它的 JSON 形式，映射取浅拷贝；其余对象、或 model_dump 不是对象时以英文 TypeError 停止并写日志。"""
    dumped = SimpleNamespace(model_dump=lambda *, mode: {"mode": mode})
    mapping = {"choices": []}
    assert serialized_response(dumped) == {"mode": "json"}
    copied = serialized_response(mapping)
    assert copied == mapping and copied is not mapping
    with caplog.at_level(logging.ERROR), pytest.raises(TypeError, match="^Model response is not serializable$"):
        serialized_response([])
    with caplog.at_level(logging.ERROR), pytest.raises(TypeError,
                                                       match="^Model response payload must be an object$"):
        serialized_response(SimpleNamespace(model_dump=lambda *, mode: ["not", "an", "object"]))
    assert ["Model response is not serializable", "Model response payload must be an object"] == [
        record.getMessage() for record in caplog.records if record.levelno == logging.ERROR][-2:]
    assert isinstance(serialized_response(dumped), Mapping)


@pytest.mark.asyncio
async def test_an_unserializable_response_stops_with_its_type_error_and_no_response_evidence() -> None:
    """响应不能序列化：TypeError 原样上抛，不改写成证据写入失败，不写响应证据，也不再请求模型。"""
    agent, engine, model = _person([[], _world()])
    with pytest.raises(TypeError, match="^Model response is not serializable$"):
        await _run(agent)
    assert [item.phase for item in engine.evidence] == ["request"] and len(model.received) == 1


@pytest.mark.asyncio
async def test_the_usage_check_reuses_the_count_taken_before_sending(monkeypatch) -> None:
    """响应证据写完之后的用量校核用的是发送前预检计下的输入 token 数，每回合只计数一次。"""
    counted, checked = [], []
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda request: counted.append(request) or 30000)
    monkeypatch.setattr(react_loop, "compare_usage", lambda estimate, response: checked.append((estimate, response)))
    agent, engine, _ = _person([_world()])
    await _run(agent)
    reserve = engine.evidence[0].request["max_tokens"]
    assert len(counted) == 1 and checked == [(30000 - reserve, engine.evidence[1].response)]


@pytest.mark.asyncio
async def test_a_failed_evidence_write_stops_with_the_evidence_stage(monkeypatch, caplog) -> None:
    """响应证据写入失败：写错误日志，以证据写入阶段的 RuntimeError 停止，原因是原异常；不再请求模型。"""
    agent, engine, model = _person([_invented(), _world()])
    injected = OSError("evidence store unavailable")
    record = engine.record_model_exchange

    def failing(exchange):
        """响应证据的写入失败，其余照常。"""
        if exchange.phase == "response":
            raise injected
        record(exchange)

    monkeypatch.setattr(engine, "record_model_exchange", failing)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await _run(agent)
    assert str(failure.value) == "EVIDENCE_WRITE_FAILED: Model evidence could not be persisted."
    assert failure.value.__cause__ is injected and len(model.received) == 1
    assert "Model evidence persistence failed: evidence store unavailable" in caplog.messages


@pytest.mark.asyncio
async def test_an_accepted_reply_is_the_assistant_message_and_world_requests_add_no_tool_message() -> None:
    """通过校验的回复接成一条 assistant（键依次 role、content、tool_calls、reasoning_content）；世界请求登记后不追加消息。"""
    agent, engine, _ = _person([_world()])
    registered, history = await _run(agent)
    assert [decision.action for decision in registered] == [MAIN_TOOL]
    assert [item["role"] for item in history] == ["system", "user", "assistant"]
    assert list(history[-1]) == ["role", "content", "tool_calls", "reasoning_content"]
    assert history[-1]["tool_calls"] == [{"id": "call-world", "type": "function", "function": {
        "name": MAIN_TOOL, "arguments": json.dumps(_WAIT_CHOICE, ensure_ascii=False)}}]
    assert [call.action_id for call in engine.calls] == ["decision:action:0001"]


def test_assistant_message_keeps_the_wire_key_order_and_default_arguments() -> None:
    """工具调用键依次 id、type、function（name、arguments），缺省取空串与 "{}"；思考为真值才追加在最后，正文原样。"""
    call = wire_tool_call(None, None, None)
    assert call == {"id": "", "type": "function", "function": {"name": "", "arguments": "{}"}}
    assert list(call) == ["id", "type", "function"] and list(call["function"]) == ["name", "arguments"]
    assert list(assistant_message(None, [call], "")) == ["role", "content", "tool_calls"]
    assert assistant_message(None, [call], "")["content"] is None
    assert list(assistant_message("", [], "想过")) == ["role", "content", "tool_calls", "reasoning_content"]
    assert accepted_assistant(_world()) == assistant_message("", [wire_tool_call(
        "call-world", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False))], "先歇一会儿。")


def test_end_continuation_returns_a_new_list_and_leaves_the_input_untouched(caplog) -> None:
    """结束续接返回新快照副本接上链后部分的新列表与不带续接的预算，入参不变；链中缺思考的发送前自检同样不改入参。"""
    fresh = ({"role": "system", "content": "s"}, {"role": "user", "content": "u"})
    budget = ProgressBudget((), (), fresh_messages=fresh, chain_length=3)
    messages = [{"role": "system", "content": "old"}, {"role": "user", "content": "old"},
                {"role": "assistant", "content": "", "tool_calls": []}, {"role": "tool", "content": "{}"}]
    before = copy.deepcopy(messages)
    replaced, ended = end_continuation(messages, budget)
    assert messages == before and replaced is not messages
    assert replaced == [*fresh, messages[3]] and replaced[0] is not fresh[0]
    assert (ended.fresh_messages, ended.chain_length) == (None, 0)
    with caplog.at_level(logging.WARNING):
        checked, unchained = send_check(messages, budget)
    assert messages == before and checked == replaced and unchained == ended


@pytest.mark.asyncio
async def test_a_correction_after_a_thinking_request_with_complete_calls_is_paired() -> None:
    """思考开启的请求里调用结构完整的错误回复：历史接上原样的 assistant 与每个调用一条 tool 纠错。"""
    reply = _MultipleModelResponse([("call-1", "invented_one", "{}"), ("call-2", "invented_two", "{}")])
    agent, _, model = _person([reply, _world()])
    await _run(agent)
    messages = model.received[1]["messages"]
    assert [item["role"] for item in messages] == ["system", "user", "assistant", "tool", "tool"]
    assert [item["tool_call_id"] for item in messages[3:]] == ["call-1", "call-2"]
    assert messages[3]["content"] == messages[4]["content"]
    assert messages[2] == accepted_assistant(reply)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _BROKEN)
async def test_a_correction_without_complete_calls_is_one_host_user_message(kind: str) -> None:
    """没有调用、type 不是 function、id 空白、名字空白、参数不是字符串、去空白后 id 重复：纠错是一条 user 消息，没有
    assistant 与 tool 消息。"""
    agent, _, model = _person([_broken_pair(kind), _world()])
    await _run(agent)
    messages = model.received[1]["messages"]
    assert [item["role"] for item in messages] == ["system", "user", "user"]
    assert _feedback(messages[-1])["instruction"] == FEEDBACK_INSTRUCTION


@pytest.mark.asyncio
async def test_a_correction_in_a_thinking_disabled_retry_is_a_host_user_message() -> None:
    """截断重试关闭了思考：重试回合里调用结构完整的错误回复也只接一条 user 纠错，历史里没有缺思考的 assistant。"""
    agent, _, model = _person([TruncatedResponse("想了很多。"), _invented("call-retry"), _world()])
    await _run(agent)
    assert model.received[1]["extra_body"] == {"thinking": {"type": "disabled"}}
    messages = model.received[2]["messages"]
    assert [item["role"] for item in messages] == ["system", "user", "user", "user"]
    assert messages[2]["content"] == TRUNCATION_CONTENT
    assert _feedback(messages[3])["error_code"] == "TOOL_CALL_REQUIRED"


@pytest.mark.asyncio
async def test_the_correction_body_uses_the_default_json_separators() -> None:
    """纠错正文是五字段报告加固定指示的 JSON，用缺省分隔符「, 」与「: 」。"""
    agent, engine, model = _person([_invented(), _world()])
    await _run(agent)
    content = model.received[1]["messages"][-1]["content"]
    report = engine.evidence[1].response["protocol_feedback"]
    assert content == json.dumps({**report, "instruction": FEEDBACK_INSTRUCTION}, ensure_ascii=False)
    assert '", "' in content and '": "' in content


@pytest.mark.asyncio
async def test_a_tool_service_failure_points_to_its_call_and_reraises_with_the_stage(monkeypatch, caplog) -> None:
    """同一回复里第二个检索的只读查询服务失败：先写带异常的日志，再以指向第二个调用的工具服务阶段上抛；第一个检索已经
    生效，不再请求模型。"""
    reply = _MultipleModelResponse([("call-1", "search_memory", _SEARCH), ("call-2", "search_memory", _SEARCH)])
    agent, engine, model = _person([reply])
    injected = OSError("query store unavailable")
    answered = []

    def read_query(context, tool, request):
        """第一次照常回答，第二次服务失败。"""
        if answered:
            raise injected
        answered.append(tool)
        return dict(_SEARCH_ANSWER)

    monkeypatch.setattr(engine, "read_query", read_query)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await _run(agent)
    assert str(failure.value) == "TOOL_SERVICE_FAILED: The tool service failed before a result was available."
    assert failure.value.__cause__ is injected
    assert [tuple(item.path) for item in failure.value.feedback.report.items] == [("tool_calls", 1)]
    assert answered == ["search_memory"] and len(model.received) == 1
    [record] = [item for item in caplog.records if item.getMessage().startswith("LifeAgent tool dispatch failed")]
    assert record.getMessage() == "LifeAgent tool dispatch failed: search_memory" and record.exc_info[1] is injected


@pytest.mark.asyncio
async def test_recorded_requests_are_never_changed_after_they_are_recorded() -> None:
    """检索、纠错、世界请求三个回合：结束时每份证据里的请求都与写入当时的规范 JSON 逐字节相同。"""
    agent, engine, _ = _person([_search(), _invented(), _world()])
    await _run(agent)
    assert [canonical_json(item.request) for item in engine.evidence] == engine.snapshots
    assert len({id(item.request["messages"]) for item in engine.evidence[::2]}) == 3


def test_free_text_never_becomes_a_tool_call() -> None:
    """只有正文没有工具调用的回复不会变成工具调用：没有决策，按缺少工具调用纠错。"""
    agent, _, _ = _person([])
    agent.turn_tools(4, False)
    decisions, feedback = agent._parse(_text_only('{"answer": "left"}'))
    assert decisions == []
    assert feedback.text == "TOOL_CALL_REQUIRED: At least one complete native tool call is required."


def test_invalid_json_is_not_repaired() -> None:
    """单引号写成的参数不会被修补成 JSON：没有决策，纠错指向参数根。"""
    agent, _, _ = _person([])
    agent.turn_tools(4, False)
    decisions, feedback = agent._parse(_ModelResponse("call-1", MAIN_TOOL, "{'detail': 1}"))
    assert decisions == []
    assert [(item.error_code, tuple(item.path)) for item in feedback.report.items] == [
        ("ARGUMENTS_NOT_JSON", ("tool_calls", 0, "function", "arguments"))]


@pytest.mark.asyncio
async def test_unknown_tool_gets_bounded_feedback_and_self_correction() -> None:
    """未知工具整批不执行，错误经原生工具身份反馈后由模型纠正。"""
    agent, engine, model = _person([_invented(), _world()])
    await _run(agent)
    assert [call.tool_name for call in engine.calls] == [MAIN_TOOL]
    assert len(model.received) == 2
    correction = model.received[1]["messages"]
    assert [item["role"] for item in correction] == ["system", "user", "assistant", "tool"]
    assert correction[-2]["tool_calls"][0]["function"]["name"] == "invented_tool"
    assert _feedback(correction[-1])["error_code"] == "TOOL_CALL_REQUIRED"
    assert [item.phase for item in engine.evidence] == ["request", "response", "request", "response"]
    assert engine.evidence[1].error.startswith("TOOL_CALL_REQUIRED: ")
    assert engine.evidence[0].call_id == engine.evidence[1].call_id
    assert engine.evidence[2].call_id == engine.evidence[3].call_id
    assert engine.evidence[0].call_id != engine.evidence[2].call_id
    assert {item.attempt for item in engine.evidence} == {1}
    assert all(sorted(item.request) == sorted(REQUEST_KEYS) for item in engine.evidence)


@pytest.mark.asyncio
async def test_invalid_json_uses_paired_tool_feedback() -> None:
    """参数文本解析失败仍以完整原生调用身份返回未执行反馈。"""
    broken = _ModelResponse("call-json", MAIN_TOOL, "{'detail': 'broken'}", reasoning_content="写错了。")
    agent, _, model = _person([broken, _world()])
    await _run(agent)
    messages = model.received[1]["messages"]
    assert [item["role"] for item in messages] == ["system", "user", "assistant", "tool"]
    assert _feedback(messages[-1])["error_code"] == "ARGUMENTS_NOT_JSON"


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", _BROKEN)
async def test_unpairable_tool_calls_use_host_user_feedback(broken: str) -> None:
    """破损调用身份不进入下一次请求，宿主用户消息要求模型重发。"""
    agent, _, model = _person([_broken_pair(broken), _world()])
    await _run(agent)
    messages = model.received[1]["messages"]
    assert [item["role"] for item in messages] == ["system", "user", "user"]
    assert all("tool_calls" not in item for item in messages)
    assert _feedback(messages[-1])["error_code"]


@pytest.mark.asyncio
async def test_mixed_batch_is_rejected_without_partial_execution() -> None:
    """冲突批次的全部调用都返回未执行反馈且没有部分副作用。"""
    mixed = _MultipleModelResponse([("call-1", "invented_tool", "{}"),
                                    ("call-2", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, engine, model = _person([mixed, _world()])
    await _run(agent)
    assert [call.tool_name for call in engine.calls] == [MAIN_TOOL] and len(engine.calls) == 1
    feedback = [item for item in model.received[1]["messages"] if item["role"] == "tool"]
    assert len(feedback) == 2
    assert all(_feedback(item)["instruction"] == FEEDBACK_INSTRUCTION for item in feedback)


@pytest.mark.asyncio
async def test_loop_exhaustion_is_fatal() -> None:
    """一个回合也没有登记世界请求即运行故障。"""
    agent, _, _ = _person([_invented()], max_turns=1)
    with pytest.raises(RuntimeError, match="exhausted"):
        await _run(agent)


@pytest.mark.asyncio
async def test_protocol_errors_consume_exact_global_turn_budget() -> None:
    """协议错误用尽现有回合后失败且不会发出预算外请求。"""
    agent, engine, model = _person([_invented(name="invented_one"), _invented(name="invented_two")], max_turns=2)
    with pytest.raises(RuntimeError, match="decision budget exhausted"):
        await _run(agent)
    assert len(model.received) == 2
    assert [item.phase for item in engine.evidence] == ["request", "response", "request", "response"]
    sent_feedback = model.received[1]["messages"][-1]
    assert "invented_one" in json.dumps(_feedback(sent_feedback), ensure_ascii=False)
    assert "invented_two" not in sent_feedback["content"]


@pytest.mark.asyncio
async def test_transport_failure_is_fatal_without_protocol_feedback() -> None:
    """传输超时只记录一次失败并立即传播。"""
    agent, engine, model = _person([TimeoutError("provider timed out")], max_turns=3)
    with pytest.raises(TimeoutError, match="provider timed out"):
        await _run(agent)
    assert len(model.received) == 1
    assert [item.phase for item in engine.evidence] == ["request", "failure"]
    assert engine.evidence[-1].error == _MODEL_FAILED


@pytest.mark.asyncio
async def test_cancellation_is_fatal_without_protocol_feedback() -> None:
    """任务取消只留下已发请求证据且不转成模型纠错。"""
    agent, engine, model = _person([asyncio.CancelledError()], max_turns=3)
    with pytest.raises(asyncio.CancelledError):
        await _run(agent)
    assert len(model.received) == 1
    assert [item.phase for item in engine.evidence] == ["request"]


@pytest.mark.asyncio
async def test_response_evidence_failure_is_fatal_without_new_model_call(monkeypatch) -> None:
    """响应证据写入失败时不生成纠错请求。"""
    agent, engine, model = _person([_invented(), _world()])
    record = engine.record_model_exchange

    def fail_response(exchange) -> None:
        """响应证据写入失败，其余照常。"""
        if exchange.phase == "response":
            raise OSError("evidence unavailable")
        record(exchange)

    monkeypatch.setattr(engine, "record_model_exchange", fail_response)
    with pytest.raises(RuntimeError) as failure:
        await _run(agent)
    assert isinstance(failure.value.__cause__, OSError) and str(failure.value.__cause__) == "evidence unavailable"
    assert len(model.received) == 1
    assert [item.phase for item in engine.evidence] == ["request"]


@pytest.mark.asyncio
async def test_unserializable_response_is_fatal_without_protocol_feedback() -> None:
    """无法可靠留证的响应立即失败且不进入纠错历史。"""
    agent, engine, model = _person([[], _world()])
    with pytest.raises(TypeError, match="not serializable"):
        await _run(agent)
    assert len(model.received) == 1
    assert [item.phase for item in engine.evidence] == ["request"]


@pytest.mark.asyncio
async def test_tool_execution_failure_is_fatal_without_new_model_call(monkeypatch) -> None:
    """已验证工具的引擎执行异常不得进入协议纠错路径。"""
    agent, engine, model = _person([_world()])

    def fail_execution(call):
        """登记世界请求时引擎服务失败。"""
        raise OSError("environment unavailable")

    monkeypatch.setattr(engine, "submit_action", fail_execution)
    with pytest.raises(RuntimeError) as failure:
        await _run(agent)
    assert isinstance(failure.value.__cause__, OSError) and str(failure.value.__cause__) == "environment unavailable"
    assert len(model.received) == 1
    assert [item.phase for item in engine.evidence] == ["request", "response"]


@pytest.mark.asyncio
async def test_structured_observation_appears_once_in_user_message() -> None:
    """结构化观察只在 user 消息里出现一次。"""
    observation = _observation()
    observation["environment_layer"]["weather"] = "kitchen-sink-a1"
    agent, _, model = _person([_world()])
    await _run(agent, observation)
    user_content = model.received[0]["messages"][1]["content"]
    assert user_content.count("kitchen-sink-a1") == 1


@pytest.mark.asyncio
async def test_reasoning_content_survives_into_followup_request() -> None:
    """只读查询回合的思考原样留在下一次请求的 assistant 里。"""
    agent, _, model = _person([_ModelResponse("call-1", "search_memory", _SEARCH,
                                              reasoning_content="chain-of-thought-1"), _world()])
    await _run(agent)
    assistant = next(item for item in model.received[1]["messages"] if item["role"] == "assistant")
    assert assistant["reasoning_content"] == "chain-of-thought-1"
    assert assistant["tool_calls"][0]["function"]["name"] == "search_memory"


@pytest.mark.asyncio
async def test_reasoning_content_survives_protocol_feedback_request() -> None:
    """纠错回合的思考原样留在下一次请求的 assistant 里。"""
    agent, _, model = _person([_invented(reasoning="chain-of-thought-2"), _world()])
    await _run(agent)
    assistant = next(item for item in model.received[1]["messages"] if item["role"] == "assistant")
    assert assistant["reasoning_content"] == "chain-of-thought-2"


def test_assistant_message_without_reasoning_omits_key() -> None:
    """响应没有思考时 assistant 不带 reasoning_content 键。"""
    message = SimpleNamespace(content="text", tool_calls=[])
    assistant = accepted_assistant(SimpleNamespace(choices=[SimpleNamespace(message=message)]))
    assert "reasoning_content" not in assistant
