"""@brief 在真实 AS2 请求路径验证决策上下文、登记隔离与整次请求容量。"""

from __future__ import annotations

import copy
import json
import re

import pytest

from tests.integration.test_life_agent_protocol import (
    _DecisionStatementResponse,
    _Environment,
    _MEMORY_TOOL_NAMES,
    _ModelResponse,
    _PRIOR_EPISODE,
    _PRIOR_PROJECTION,
    _WAIT_CHOICE,
    _context,
    _observations,
    _wire_agent,
)
from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.agent.base import agent_runtime
from agentsociety2.society import DecisionContext
from custom.agents import life_agent
from custom.agents.life_agent import LifeAgent
from worldmock.life.models import REQUEST_MODELS
from worldmock.life import prompt_capacity
from worldmock.life.narration import RenderBudgetExceededError


_STATEMENT = "我有点累，想先歇一分钟。"


@pytest.fixture(autouse=True)
def _fixed_model_context(monkeypatch) -> None:
    """@brief 集成测试固定模型窗口，避免读取在线供应商元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_000_000)


@pytest.mark.parametrize("template", [
    "_LIFE_INSTRUCTIONS", "_DECISION_STATEMENT_INSTRUCTIONS", "_READONLY_INSTRUCTIONS"])
def test_prompt_fingerprint_binds_every_formal_template(monkeypatch, template: str) -> None:
    """@brief 每个实际请求使用的正式模板变更都会改变运行指纹。"""
    original = life_agent.life_prompt_sha256()
    monkeypatch.setattr(life_agent, template, getattr(life_agent, template) + " changed")
    assert life_agent.life_prompt_sha256() != original


def test_tool_fingerprint_binds_start_activity_description(monkeypatch) -> None:
    """@brief 对象与动作选择规则变更会改变实际工具指纹。"""
    original = life_agent.life_tool_schema_sha256()
    monkeypatch.setitem(life_agent._TOOL_DESCRIPTIONS, "start_activity",
                        life_agent._TOOL_DESCRIPTIONS["start_activity"] + " changed")
    assert life_agent.life_tool_schema_sha256() != original


class _ScriptedDispatcher:
    """@brief 留存真实请求的独立副本，按脚本返回模型响应或传输错误。"""

    model_name = "deepseek-flash"

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def call(self, **request):
        """@brief 捕获提供商入口的完整参数，任何多余请求都会明确失败。"""
        self.requests.append(copy.deepcopy(request))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _action_response() -> _ModelResponse:
    """@brief 返回带提供商思考字段的合法生活动作（世界请求登记即结束本轮）。"""
    return _ModelResponse(
        "provider-action-call", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False),
        reasoning_content="I intend to rest; the outcome is not known yet.")


def _finish_response() -> _ModelResponse:
    """@brief 通过只读 finish Schema 结束只读请求（生活决策没有 finish）。"""
    return _ModelResponse("provider-finish-call", "finish", json.dumps({"answer": "I remember waiting."}))


def _configured(responses: list, max_turns: int = 6) -> tuple:
    """@brief 使用现有真实人物装配，只替换外部模型提供商。"""
    agent, environment = LifeAgent(), _Environment()
    dispatcher = _ScriptedDispatcher(responses)
    _wire_agent(agent, environment, dispatcher, max_turns)
    return agent, environment, dispatcher


def _request(*, readonly: bool = False) -> ReactLoopRequest:
    """@brief 只读请求携带不可渲染的观察，证明其不会进入生活上下文路径。"""
    observations = [{"data": {"scene": object(), "facts": object()}}] if readonly else _observations()
    return ReactLoopRequest(0, _context().observed_at, observations, "What happened?", readonly)


@pytest.mark.asyncio
async def test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user() -> None:
    """@brief 记忆查询结果即时可用且原生配对、思考字段保持；登记回执与条目身份都不进入发给模型的内容。"""
    agent, environment, dispatcher = _configured([
        _DecisionStatementResponse(_STATEMENT),
        _ModelResponse("memory-first", "memory_recent", '{"limit":1}',
                       reasoning_content="I should recall what I did yesterday."),
        _ModelResponse("memory-second", "memory_recent", '{"limit":1}'),
        _action_response(),
    ], 5)
    context = _context()
    agent._decision_context = DecisionContext(
        "private-run-identity", "private-round-identity", "private-decision-identity",
        context.agent_id, context.observed_at, context.world_state_version)
    request = _request()

    assert await agent.run_react_loop(request) == "start_activity registered"

    assert len(environment.calls) == 1
    assert len(dispatcher.requests) == 4
    host_receipt = next(item for item in request.observations if item["action"] == "start_activity")
    assert host_receipt["data"] == {
        "request_sha256": "a" * 64, "status": "submitted",
        "action_id": "private-decision-identity:action:0001",
        "round_id": "private-round-identity", "decision_id": "private-decision-identity"}
    assert json.loads(host_receipt["observation"]) == host_receipt["data"]
    history = agent._react_messages
    action_tool = next(item for item in history if item.get("tool_call_id") == "provider-action-call")
    assert json.loads(action_tool["content"]) == {
        "ok": True, "observation": life_agent._ACTION_REGISTRATION_CONFIRMATION, "data": {}}
    sent = json.dumps([item["messages"] for item in dispatcher.requests] + [history], ensure_ascii=False)
    for forbidden in (
        "request_sha256", "run_id", "round_id", "decision_id", "agent_id", "action_id", "submitted",
        "private-run-identity", "private-round-identity", "private-decision-identity", "a" * 64,
        _PRIOR_EPISODE["id"],
    ):
        # 检查完整协议身份与记忆条目身份；身份前言里的供给差异说明（based_on_action_id）不是宿主回执泄露。
        assert re.search(rf"(?<!\w){re.escape(forbidden)}(?!\w)", sent) is None
    followup = dispatcher.requests[-1]["messages"]
    assistant = next(item for item in followup if item.get("reasoning_content"))
    assert assistant["reasoning_content"] == "I should recall what I did yesterday."
    assert assistant["tool_calls"][0]["id"] == "memory-first"
    memory_tools = [item for item in followup if item.get("tool_call_id", "").startswith("memory-")]
    assert len(memory_tools) == len(agent._memory_runtime.calls) == 2
    assert all(json.loads(item["content"])["data"]["episodes"] == [_PRIOR_PROJECTION] for item in memory_tools)
    users = [item["messages"][1]["content"].encode() for item in dispatcher.requests[1:]]
    assert all(user == users[0] for user in users)
    assert '"tasks"' not in dispatcher.requests[0]["messages"][1]["content"]
    assert all(b'"tasks"' in user for user in users)
    for item in dispatcher.requests[1:]:
        names = {tool["function"]["name"] for tool in item["tools"]}
        assert "finish" not in names
        assert set(REQUEST_MODELS) & names


@pytest.mark.asyncio
async def test_decision_statement_request_explicitly_describes_generation_contract() -> None:
    """@brief 显式自述要求随真实无工具请求发送，边界二百汉字可继续行动。"""
    agent, _, dispatcher = _configured([
        _DecisionStatementResponse("我" * 200), _action_response()], 3)
    await agent.run_react_loop(_request())
    request = dispatcher.requests[0]
    assert "tools" not in request and "tool_choice" not in request
    assert request["temperature"] == 1.0
    assert request["max_tokens"] == 8000
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    system = request["messages"][0]["content"]
    assert all(phrase in system for phrase in ("一至三句", "第一人称中文", "感受和打算", "二百汉字"))
    assert "我" * 200 in dispatcher.requests[1]["messages"][1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["", "   ", "我" * 201])
async def test_invalid_decision_statement_retries_once_with_same_context(invalid: str) -> None:
    """@brief 空或超长自述消耗真实回合，仅重试一次并保留同一输入。"""
    agent, environment, dispatcher = _configured([
        _DecisionStatementResponse(invalid), _DecisionStatementResponse(_STATEMENT),
        _action_response()], 4)
    await agent.run_react_loop(_request())
    assert len(dispatcher.requests) == agent._model_call_sequence == 3
    assert dispatcher.requests[0] == dispatcher.requests[1]
    assert environment.evidence[1].error is not None
    assert environment.evidence[3].error is None
    assert len(environment.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["", "我" * 201])
async def test_invalid_decision_statement_twice_stops_before_world_action(invalid: str) -> None:
    """@brief 两次协议错误后无第三次尝试，也无生活动作。"""
    agent, environment, dispatcher = _configured([_DecisionStatementResponse(invalid)] * 2, 4)
    with pytest.raises(RuntimeError, match="failed the protocol twice"):
        await agent.run_react_loop(_request())
    assert len(dispatcher.requests) == agent._model_call_sequence == 2
    assert environment.calls == []


@pytest.mark.asyncio
async def test_decision_statement_retry_requires_remaining_statement_and_action_turns() -> None:
    """@brief 协议失败之后，剩余回合不够再自述一次并登记世界请求（自述前须剩 2 个回合）时不再发注定失败的请求。"""
    agent, environment, dispatcher = _configured([_DecisionStatementResponse("")], 2)
    with pytest.raises(RuntimeError, match="decision_statement requires 2 model turns but only 1 remain"):
        await agent.run_react_loop(_request())
    assert len(dispatcher.requests) == 1
    assert environment.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["transport", "request", "response"])
async def test_decision_statement_transport_and_evidence_failures_are_logged_without_retry(
        monkeypatch, failure_phase: str) -> None:
    """@brief 传输或证据保存错误直接终止，并产生英文错误日志。"""
    responses = [TimeoutError("transport failed")] if failure_phase == "transport" else [
        _DecisionStatementResponse(_STATEMENT)]
    agent, environment, dispatcher = _configured(responses)
    errors = []
    monkeypatch.setattr(agent_runtime.logger, "error", lambda *args: errors.append(args))
    monkeypatch.setattr(agent._logger, "error", lambda *args: errors.append(args))
    original = environment.record_model_exchange

    async def record(exchange):
        if exchange.phase == failure_phase:
            raise OSError("evidence storage failed")
        await original(exchange)

    monkeypatch.setattr(environment, "record_model_exchange", record)
    expected = TimeoutError if failure_phase == "transport" else OSError
    with pytest.raises(expected):
        await agent.run_react_loop(_request())
    assert len(dispatcher.requests) == (0 if failure_phase == "request" else 1)
    assert environment.calls == []
    assert errors
    assert all(not any("一" <= char <= "鿿" for char in str(error)) for error in errors)


@pytest.mark.asyncio
async def test_readonly_loop_uses_only_card_memory_and_question() -> None:
    """@brief 只读请求跳过自述和观察，查询结果即时返回（不带条目身份）且不写世界或记忆。"""
    agent, environment, dispatcher = _configured([
        _ModelResponse("readonly-memory", "memory_recent", '{"limit":1}'),
        _finish_response()], 2)
    agent._profile["persona_card"] = "我是一个爱散步的人。"
    result = await agent.run_react_loop(_request(readonly=True))
    assert result == "I remember waiting."
    assert len(dispatcher.requests) == 2
    assert agent._life_decision_statement is None
    first_user = dispatcher.requests[0]["messages"][1]["content"]
    assert first_user == "我是一个爱散步的人。\n\n主观归纳：\n# Subjective\n\n外部只读问题：What happened?"
    assert {tool["function"]["name"] for tool in dispatcher.requests[0]["tools"]} == (
        _MEMORY_TOOL_NAMES | {"finish"})
    assert {tool["function"]["name"] for tool in dispatcher.requests[1]["tools"]} == {"finish"}
    assert "I waited yesterday." in dispatcher.requests[1]["messages"][-1]["content"]
    assert _PRIOR_EPISODE["id"] not in dispatcher.requests[1]["messages"][-1]["content"]
    assert environment.calls == environment.memory_updates == []
    assert agent._last_finish_memories is None


@pytest.mark.asyncio
@pytest.mark.parametrize("readonly", [False, True])
@pytest.mark.parametrize("hanzi", [600, 601])
async def test_persona_card_budget_applies_to_life_and_readonly_requests(readonly: bool, hanzi: int) -> None:
    """@brief 人物卡上限两种入口相同，等于上限通过、超限时模型零请求。"""
    responses = [_finish_response()] if readonly else [
        _DecisionStatementResponse(_STATEMENT), _action_response()]
    agent, environment, dispatcher = _configured(responses, 3)
    agent._profile["persona_card"] = "人" * hanzi
    if hanzi > 600:
        with pytest.raises(RenderBudgetExceededError, match="persona card exceeds"):
            await agent.run_react_loop(_request(readonly=readonly))
        assert dispatcher.requests == environment.calls == []
    else:
        await agent.run_react_loop(_request(readonly=readonly))
        assert all("人" * 600 in item["messages"][1]["content"] for item in dispatcher.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["decision_statement", "action", "readonly"])
async def test_each_model_entry_preflights_the_complete_token_request(monkeypatch, stage: str) -> None:
    """@brief 自述、行动与只读入口达到窗口八成五时都在发送前报容量故障。"""
    readonly = stage == "readonly"
    responses = [_finish_response()] if readonly else [
        _DecisionStatementResponse(_STATEMENT), _action_response()]
    agent, environment, dispatcher = _configured(responses, 3)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 100_000)

    def measured(request: dict) -> int:
        """@brief 只让指定入口达到阈值，其他入口维持可发送。"""
        tools = request.get("tools")
        if stage == "decision_statement" and not tools:
            return 85_000
        if stage == "action" and tools:
            return 85_000
        if stage == "readonly" and tools:
            return 85_000
        return 1000

    monkeypatch.setattr(prompt_capacity, "request_token_count", measured)
    with pytest.raises(RuntimeError, match="model request capacity exceeded"):
        await agent.run_react_loop(_request(readonly=readonly))
    assert len(dispatcher.requests) == (1 if stage == "action" else 0)
    assert environment.calls == []


@pytest.mark.asyncio
async def test_long_system_message_passes_when_complete_request_is_below_threshold(monkeypatch) -> None:
    """@brief 旧 system 字符熔断不影响整次请求低于窗口阈值的只读决策。"""
    monkeypatch.setattr(life_agent, "_READONLY_INSTRUCTIONS",
                        life_agent._READONLY_INSTRUCTIONS + " context" * 700)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 100_000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda request: 84_999)
    agent, _, dispatcher = _configured([_finish_response()], 1)
    await agent.run_react_loop(_request(readonly=True))
    assert len(dispatcher.requests[0]["messages"][0]["content"]) > 3000


@pytest.mark.asyncio
async def test_start_activity_schema_uses_readable_target_and_action() -> None:
    """@brief 真实工具 Schema 只暴露可读目标、动词和方式，执行参数由引擎派生；描述说明「动词：方式」与说明文字，
    不再要求只选前置条件已满足的对象（docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，做错了再说明」）。"""
    agent, environment, dispatcher = _configured([
        _DecisionStatementResponse(_STATEMENT), _action_response()], 3)
    await agent.run_react_loop(_request())
    action_request = dispatcher.requests[1]
    start = next(tool["function"] for tool in action_request["tools"]
                 if tool["function"]["name"] == "start_activity")
    assert set(start["parameters"]["properties"]) == {"targets", "actions", "ways"}
    description = start["description"]
    assert "facts.interactive_objects" in description
    assert "preconditions_met" not in description and "Choose only objects" not in description
    assert "verb：way" in description and "note" in description
    assert all(field in description for field in ("targets", "actions", "ways"))
    assert "自己" in action_request["messages"][1]["content"]
    assert environment.calls[0].request.model_dump() == _WAIT_CHOICE


@pytest.mark.asyncio
async def test_start_activity_description_says_notes_give_length_or_unmet_condition() -> None:
    """@brief 动作的说明可以是内容的时长（全长或还剩多少），也可以是尚未满足的条件：实际发出的工具描述两种都讲明，
    并且只把「选了条件未满足的动作」说成由世界给出原因与下一步，不把时长说明当成未满足的条件
    （docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，做错了再说明」、第八章「呈现」）。"""
    agent, _, dispatcher = _configured([_DecisionStatementResponse(_STATEMENT), _action_response()], 3)
    await agent.run_react_loop(_request())
    description = next(tool["function"]["description"] for tool in dispatcher.requests[1]["tools"]
                       if tool["function"]["name"] == "start_activity")
    assert description == life_agent._TOOL_DESCRIPTIONS["start_activity"]
    note = next(sentence for sentence in re.split(r"(?<=\.)\s+", description) if "note" in sentence)
    gives, choosing = note.split(";", 1)
    assert "length" in gives and "condition that is not met" in gives
    assert "condition is not met" in choosing
    assert "reason" in choosing and "next step" in choosing
