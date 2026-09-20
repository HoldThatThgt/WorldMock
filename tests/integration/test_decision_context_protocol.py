"""@brief 在真实 AS2 请求路径验证决策上下文、登记隔离与各入口提示预算。"""

from __future__ import annotations

import copy
import json

import pytest

from tests.integration.test_life_agent_protocol import (
    _DecisionStatementResponse,
    _Environment,
    _MEMORY_TOOL_NAMES,
    _ModelResponse,
    _context,
    _finish_args,
    _observations,
    _wire_agent,
)
from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.agent.base import agent_runtime
from agentsociety2.society import DecisionContext
from custom.agents import life_agent
from custom.agents.life_agent import LifeAgent
from worldmock.life.models import REQUEST_MODELS
from worldmock.life.narration import (
    PromptBudgetExceededError,
    RenderBudgetExceededError,
    SYSTEM_MESSAGE_CHAR_BUDGET,
    USER_MESSAGE_CHAR_BUDGET,
)


_STATEMENT = "我有点累，想先歇一分钟。"


@pytest.mark.parametrize("template", [
    "_LIFE_INSTRUCTIONS", "_DECISION_STATEMENT_INSTRUCTIONS", "_READONLY_INSTRUCTIONS"])
def test_prompt_fingerprint_binds_every_formal_template(monkeypatch, template: str) -> None:
    """@brief 每个实际请求使用的正式模板变更都会改变运行指纹。"""
    original = life_agent.life_prompt_sha256()
    monkeypatch.setattr(life_agent, template, getattr(life_agent, template) + " changed")
    assert life_agent.life_prompt_sha256() != original


@pytest.mark.parametrize("description", ["_TARGET_KIND_DESCRIPTION", "_TARGET_ID_DESCRIPTION"])
def test_tool_fingerprint_binds_runtime_target_descriptions(monkeypatch, description: str) -> None:
    """@brief 运行期附加的目标字段指路变化也必须被工具指纹捕获。"""
    original = life_agent.life_tool_schema_sha256()
    monkeypatch.setattr(life_agent, description, getattr(life_agent, description) + " changed")
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
    """@brief 返回带提供商思考字段的合法生活动作。"""
    return _ModelResponse(
        "provider-action-call", "start_basic_activity",
        '{"activity_type":"wait","planned_duration_seconds":60}',
        reasoning_content="I intend to rest; the outcome is not known yet.")


def _finish_response(*, readonly: bool = False) -> _ModelResponse:
    """@brief 通过真实 finish Schema 结束生活或只读请求。"""
    args = {"answer": "I remember waiting."} if readonly else _finish_args()
    return _ModelResponse("provider-finish-call", "finish", json.dumps(args))


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
    """@brief 原生后续请求无宿主回执，记忆即时可用、原生配对与思考字段保持。"""
    memory = lambda identity: _ModelResponse(identity, "memory_recent", '{"limit":1}')
    agent, environment, dispatcher = _configured([
        _DecisionStatementResponse(_STATEMENT), memory("memory-before"), _action_response(),
        memory("memory-after"), _finish_response(),
    ], 5)
    context = _context()
    agent._decision_context = DecisionContext(
        "private-run-identity", "private-round-identity", "private-decision-identity",
        context.agent_id, context.observed_at, context.world_state_version)
    request = _request()

    await agent.run_react_loop(request)

    assert len(environment.calls) == 1
    host_receipt = next(item for item in request.observations if item["action"] == "start_basic_activity")
    assert host_receipt["data"] == {
        "request_sha256": "a" * 64, "status": "submitted",
        "action_id": "private-decision-identity:action:0001",
        "round_id": "private-round-identity", "decision_id": "private-decision-identity"}
    assert json.loads(host_receipt["observation"]) == host_receipt["data"]
    followup = dispatcher.requests[-1]["messages"]
    action_tool = next(item for item in followup if item.get("tool_call_id") == "provider-action-call")
    assert json.loads(action_tool["content"]) == {
        "ok": True, "observation": life_agent._ACTION_REGISTRATION_CONFIRMATION, "data": {}}
    for forbidden in (
        "request_sha256", "run_id", "round_id", "decision_id", "agent_id", "action_id", "submitted",
        "private-run-identity", "private-round-identity", "private-decision-identity", "a" * 64,
    ):
        assert forbidden not in json.dumps(followup, ensure_ascii=False)
    assistant = next(item for item in followup if item.get("reasoning_content"))
    assert assistant["reasoning_content"] == "I intend to rest; the outcome is not known yet."
    assert assistant["tool_calls"][0]["id"] == action_tool["tool_call_id"]
    memory_tools = [item for item in followup if item.get("tool_call_id", "").startswith("memory-")]
    assert len(memory_tools) == len(agent._memory_runtime.calls) == 2
    assert all(json.loads(item["content"])["data"]["episodes"][0]["id"] == "m_prior"
               for item in memory_tools)
    users = [item["messages"][1]["content"].encode() for item in dispatcher.requests[1:]]
    assert all(user == users[0] for user in users)
    assert '"tasks"' not in dispatcher.requests[0]["messages"][1]["content"]
    assert all(b'"tasks"' in user for user in users)
    for index, item in enumerate(dispatcher.requests[1:]):
        names = {tool["function"]["name"] for tool in item["tools"]}
        assert ("finish" in names) == (index >= 2)
        assert not (set(REQUEST_MODELS) & names) if index >= 2 else set(REQUEST_MODELS) & names
    finish = next(tool["function"] for tool in dispatcher.requests[-1]["tools"]
                  if tool["function"]["name"] == "finish")
    assert "Record observed facts and your intentions only" in finish["description"]
    assert "never claim that the pending action has succeeded" in finish["description"]


@pytest.mark.asyncio
async def test_decision_statement_request_explicitly_describes_generation_contract() -> None:
    """@brief 显式自述要求随真实无工具请求发送，边界二百汉字可继续行动。"""
    agent, _, dispatcher = _configured([
        _DecisionStatementResponse("我" * 200), _action_response(), _finish_response()], 3)
    await agent.run_react_loop(_request())
    request = dispatcher.requests[0]
    assert "tools" not in request and "tool_choice" not in request
    assert request["temperature"] == 1.3
    assert request["max_tokens"] == 400
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
        _action_response(), _finish_response()], 4)
    await agent.run_react_loop(_request())
    assert len(dispatcher.requests) == agent._model_call_sequence == 4
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
async def test_decision_statement_retry_requires_remaining_action_and_finish_turns() -> None:
    """@brief 协议失败之后，回合不足以完成行动时不再发注定失败的自述请求。"""
    agent, environment, dispatcher = _configured([_DecisionStatementResponse("")], 3)
    with pytest.raises(RuntimeError, match="requires 3 model turns but only 2 remain"):
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
    assert all(not any("\u4e00" <= char <= "\u9fff" for char in str(error)) for error in errors)


@pytest.mark.asyncio
async def test_readonly_loop_uses_only_card_memory_and_question() -> None:
    """@brief 只读请求跳过自述和观察，查询结果即时返回且不写世界或记忆。"""
    agent, environment, dispatcher = _configured([
        _ModelResponse("readonly-memory", "memory_recent", '{"limit":1}'),
        _finish_response(readonly=True)], 2)
    agent._profile["persona_card"] = "我是一个爱散步的人。"
    result = await agent.run_react_loop(_request(readonly=True))
    assert result == "I remember waiting."
    assert len(dispatcher.requests) == 2
    assert agent._life_decision_statement is None
    first_user = dispatcher.requests[0]["messages"][1]["content"]
    assert first_user == "我是一个爱散步的人。\n\n# Memory\n\n外部只读问题：What happened?"
    assert {tool["function"]["name"] for tool in dispatcher.requests[0]["tools"]} == (
        _MEMORY_TOOL_NAMES | {"finish"})
    assert {tool["function"]["name"] for tool in dispatcher.requests[1]["tools"]} == {"finish"}
    assert "I waited yesterday." in dispatcher.requests[1]["messages"][-1]["content"]
    assert environment.calls == environment.memory_updates == []
    assert agent._last_finish_memories is None


@pytest.mark.asyncio
@pytest.mark.parametrize("readonly", [False, True])
@pytest.mark.parametrize("hanzi", [600, 601])
async def test_persona_card_budget_applies_to_life_and_readonly_requests(readonly: bool, hanzi: int) -> None:
    """@brief 人物卡上限两种入口相同，等于上限通过、超限时模型零请求。"""
    responses = [_finish_response(readonly=True)] if readonly else [
        _DecisionStatementResponse(_STATEMENT), _action_response(), _finish_response()]
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
@pytest.mark.parametrize("extra", [0, 1])
async def test_user_budget_is_enforced_at_every_model_entry(monkeypatch, stage: str, extra: int) -> None:
    """@brief 通过真实请求逐一验证自述、行动与只读 user 的四万字符边界。"""
    readonly = stage == "readonly"
    responses = [_finish_response(readonly=True)] if readonly else [
        _DecisionStatementResponse(_STATEMENT), _action_response(), _finish_response()]
    agent, _, dispatcher = _configured(responses, 3)
    request = _request(readonly=readonly)
    monkeypatch.setattr(agent, "_build_memory_context", lambda: {"memory_md": "", "recent_episodes": []})
    agent.prepare_react_loop(request)
    agent._life_decision_statement = _STATEMENT
    baseline = (agent._life_user_sections(request, with_facts=False) if stage == "decision_statement"
                else agent.build_react_messages(request)[1]["content"])
    memory_text = "x" * (USER_MESSAGE_CHAR_BUDGET - len(baseline) - 2 + extra)
    monkeypatch.setattr(agent, "_build_memory_context", lambda: {
        "memory_md": memory_text, "recent_episodes": []})
    if extra or stage == "decision_statement":
        with pytest.raises(PromptBudgetExceededError, match="user message exceeds"):
            await agent.run_react_loop(request)
    else:
        await agent.run_react_loop(request)
    index = 1 if stage == "action" else 0
    if extra:
        assert len(dispatcher.requests) == index
    else:
        assert len(dispatcher.requests[index]["messages"][1]["content"]) == USER_MESSAGE_CHAR_BUDGET


def _set_system_budget(monkeypatch, agent: LifeAgent, stage: str, extra: int) -> None:
    """@brief 依据真实阶段预算块算出固定前言长度，保留生产预算判定。"""
    if stage == "decision_statement":
        suffix = life_agent._DECISION_STATEMENT_INSTRUCTIONS
    else:
        agent._life_readonly = stage == "readonly"
        agent._model_call_sequence = 0 if stage == "readonly" else 1
        budget = agent._react_budget(stage == "readonly")
        suffix = life_agent._REACT_BUDGET_MARKER + json.dumps(budget, ensure_ascii=False, sort_keys=True)
        if budget["remaining_model_turns_including_current"] <= 2:
            suffix = life_agent._LOW_BUDGET_SENTENCE.format(phase=budget["phase"]) + suffix
    template = "_READONLY_INSTRUCTIONS" if stage == "readonly" else "_LIFE_INSTRUCTIONS"
    monkeypatch.setattr(life_agent, template, "x" * (SYSTEM_MESSAGE_CHAR_BUDGET - len(suffix) + extra))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["decision_statement", "action", "readonly"])
@pytest.mark.parametrize("extra", [0, 1])
async def test_system_budget_includes_appended_generation_and_turn_requirements(
        monkeypatch, stage: str, extra: int) -> None:
    """@brief 最终 system 包括生成要求或回合预算；边界通过、超限绝不发出。"""
    readonly = stage == "readonly"
    responses = [_finish_response(readonly=True)] if readonly else [
        _DecisionStatementResponse(_STATEMENT), _action_response(), _finish_response()]
    agent, _, dispatcher = _configured(responses, 1 if readonly else 3)
    _set_system_budget(monkeypatch, agent, stage, extra)
    if extra or stage == "decision_statement":
        with pytest.raises(PromptBudgetExceededError, match="system message exceeds"):
            await agent.run_react_loop(_request(readonly=readonly))
    else:
        await agent.run_react_loop(_request(readonly=readonly))
    index = 1 if stage == "action" else 0
    if extra:
        assert len(dispatcher.requests) == index
    else:
        assert len(dispatcher.requests[index]["messages"][0]["content"]) == SYSTEM_MESSAGE_CHAR_BUDGET


@pytest.mark.asyncio
async def test_read_mail_description_resolves_a_visible_inbox_identity() -> None:
    """@brief 阅读工具描述与真实提示中的 facts.inbox 邮件身份一致。"""
    action = _ModelResponse("read-mail", "start_targeted_activity", json.dumps({
        "activity_type": "read_mail", "target_id": "message-visible",
        "planned_duration_seconds": 60, "playback_speed": 1}))
    agent, environment, dispatcher = _configured([
        _DecisionStatementResponse("我想先看看这封邮件。"), action, _finish_response()], 3)
    request = _request()
    request.observations[0]["data"]["facts"]["inbox"] = [{
        "kind": "mail", "id": "message-visible", "title": "工作进度", "read": False}]
    await agent.run_react_loop(request)
    actual = dispatcher.requests[1]
    parameters = next(tool["function"]["parameters"]["properties"] for tool in actual["tools"]
                      if tool["function"]["name"] == "start_targeted_activity")
    for field in ("activity_type", "target_id"):
        description = parameters[field]["description"]
        assert "facts.inbox" in description and "kind is mail" in description
        assert "messages[].message_id" not in description
    assert '"id":"message-visible"' in actual["messages"][1]["content"]
    assert environment.calls[0].request.target_id == "message-visible"
