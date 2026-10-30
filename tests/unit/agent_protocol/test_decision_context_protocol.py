"""@brief 在真实 AS2 请求路径验证七层请求、登记隔离与整次请求容量。"""

from __future__ import annotations

from worldmock.agents.prompt.tool_groups import expand_tool_layer

import copy
import json
import re

import pytest

from tests.integration.runtime.test_life_agent_protocol import (
    _Environment,
    _ModelResponse,
    _SEARCH,
    _SEARCH_ANSWER,
    _WAIT_CHOICE,
    _context,
    _observations,
    _wire_agent,
)
from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.agent.base import agent_runtime
from agentsociety2.society import DecisionContext
from custom.agents import life_agent
from worldmock.agents.prompt import life_prompt
from custom.agents.life_agent import LifeAgent
from worldmock.agents.prompt.life_persona import persona_layer
from worldmock.state.interaction.requests import REQUEST_MODELS
from worldmock.agents.prompt import prompt_capacity
from worldmock.agents.prompt.narration import render_layer_json
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL


@pytest.fixture(autouse=True)
def _fixed_model_context(monkeypatch) -> None:
    """@brief 集成测试固定模型窗口，避免读取在线供应商元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


@pytest.mark.parametrize("layer", ["LIFE_SYSTEM_LAYER", "READONLY_SYSTEM_LAYER"])
def test_prompt_fingerprint_binds_every_formal_template(monkeypatch, layer: str) -> None:
    """@brief 生活与只读两份系统层的任何改动都会改变运行指纹。"""
    original = life_prompt.life_prompt_sha256()
    changed = copy.deepcopy(getattr(life_prompt, layer))
    changed["rules"].append("新增一条规则。")
    monkeypatch.setattr(life_prompt, layer, changed)
    assert life_prompt.life_prompt_sha256() != original


@pytest.mark.parametrize("tool", [MAIN_TOOL, SIDE_TOOL])
def test_tool_fingerprint_binds_each_start_tool_description(monkeypatch, tool: str) -> None:
    """@brief 两个开始工具任何一个的用途说明变更都会改变实际工具指纹。"""
    original = life_agent.life_tool_schema_sha256()
    monkeypatch.setitem(life_prompt.TOOL_DESCRIPTIONS, tool, life_prompt.TOOL_DESCRIPTIONS[tool] + "改")
    assert life_agent.life_tool_schema_sha256() != original


class _ScriptedDispatcher:
    """@brief 留存真实请求的独立副本，按脚本返回模型响应或传输错误。"""

    model_name = "test-model"

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
        "provider-action-call", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False),
        reasoning_content="I intend to rest; the outcome is not known yet.")


def _finish_response() -> _ModelResponse:
    """@brief 通过只读 finish Schema 结束只读请求（生活决策没有 finish）。"""
    return _ModelResponse("provider-finish-call", "finish", json.dumps({"answer": "I remember waiting."}))


def _configured(responses: list, max_turns: int = 6) -> tuple:
    """@brief 使用现有真实人物装配，只替换外部模型提供商。"""
    agent, environment = LifeAgent(), _Environment()
    dispatcher = _ScriptedDispatcher(responses)
    _wire_agent(agent, environment, dispatcher, max_turns)
    agent._model_name = agent._profile["model_identity"]["requested_model"]
    return agent, environment, dispatcher


def _request(*, readonly: bool = False) -> ReactLoopRequest:
    """@brief 只读请求携带不可渲染的观察，证明其不会进入生活上下文路径。"""
    observations = [{"data": {"tool_layer": object(), "progress_layer": object()}}] if readonly else _observations()
    return ReactLoopRequest(0, _context().observed_at, observations, "What happened?", readonly)


@pytest.mark.asyncio
async def test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user() -> None:
    """@brief 检索结果即时可用且原生配对、思考字段保持；登记后不追加 tool 消息，回执与内部身份都不进入发给模型的内容。"""
    agent, environment, dispatcher = _configured([
        _ModelResponse("memory-first", "search_memory", _SEARCH,
                       reasoning_content="I should recall what I did yesterday."),
        _ModelResponse("memory-second", "search_memory", _SEARCH),
        _action_response(),
    ], 5)
    context = _context()
    agent._decision_context = DecisionContext(
        "private-run-identity", "private-round-identity", "private-decision-identity",
        context.agent_id, context.observed_at, context.world_state_version)
    request = _request()

    assert await agent.run_react_loop(request) == f"{MAIN_TOOL} registered"

    assert len(environment.calls) == 1
    assert len(dispatcher.requests) == 3
    host_receipt = next(item for item in request.observations if item["action"] == MAIN_TOOL)
    assert host_receipt["data"] == {
        "request_sha256": "a" * 64, "status": "submitted",
        "action_id": "private-decision-identity:action:0001",
        "round_id": "private-round-identity", "decision_id": "private-decision-identity"}
    assert json.loads(host_receipt["observation"]) == host_receipt["data"]
    history = agent._react_messages
    assert not any(item.get("tool_call_id") == "provider-action-call" for item in history)
    assert history[-1]["role"] == "assistant" and history[-1]["tool_calls"][0]["id"] == "provider-action-call"
    sent = json.dumps([item["messages"] for item in dispatcher.requests] + [history], ensure_ascii=False)
    for forbidden in (
        "request_sha256", "run_id", "round_id", "decision_id", "agent_id", "action_id", "submitted",
        "private-run-identity", "private-round-identity", "private-decision-identity", "a" * 64,
    ):
        assert re.search(rf"(?<!\w){re.escape(forbidden)}(?!\w)", sent) is None
    followup = dispatcher.requests[-1]["messages"]
    assistant = next(item for item in followup if item.get("reasoning_content"))
    assert assistant["reasoning_content"] == "I should recall what I did yesterday."
    assert assistant["tool_calls"][0]["id"] == "memory-first"
    memory_tools = [item for item in followup if item.get("tool_call_id", "").startswith("memory-")]
    assert len(memory_tools) == len(environment.queries) == 2
    assert all(json.loads(item["content"]) == _SEARCH_ANSWER for item in memory_tools)
    users = [item["messages"][1]["content"].encode() for item in dispatcher.requests]
    systems = [item["messages"][0]["content"].encode() for item in dispatcher.requests]
    assert all(user == users[0] for user in users) and all(system == systems[0] for system in systems)
    for item in dispatcher.requests:
        names = {tool["function"]["name"] for tool in item["tools"]}
        assert "finish" not in names
        assert set(REQUEST_MODELS) <= names


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["transport", "request", "response"])
async def test_action_transport_and_evidence_failures_are_logged_without_retry(
        monkeypatch, failure_phase: str) -> None:
    """@brief 行动请求的传输或证据保存错误直接终止、不重试、不登记世界请求，并产生英文错误日志。"""
    responses = [TimeoutError("transport failed")] if failure_phase == "transport" else [_action_response()]
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
    expected = TimeoutError if failure_phase == "transport" else RuntimeError
    with pytest.raises(expected) as failure:
        await agent.run_react_loop(_request())
    if failure_phase != "transport":
        assert isinstance(failure.value.__cause__, OSError)
        assert failure.value.args[0].report.error_code == "EVIDENCE_WRITE_FAILED"
    assert len(dispatcher.requests) == (0 if failure_phase == "request" else 1)
    assert environment.calls == []
    assert all(not any("一" <= char <= "鿿" for char in str(error)) for error in errors)


@pytest.mark.asyncio
async def test_readonly_loop_uses_only_persona_memory_and_question() -> None:
    """@brief 只读请求跳过观察：只读系统层、人设层与记忆层组成的 user、原样的外部问题；每个回合只开放 finish，
    写了检索的回复按未开放的工具纠错、不送环境，不写世界或记忆。"""
    agent, environment, dispatcher = _configured([
        _ModelResponse("readonly-memory", "search_memory", _SEARCH),
        _finish_response()], 2)
    agent._profile["persona_card"] = "我是一个爱散步的人。"
    result = await agent.run_react_loop(_request(readonly=True))
    assert result == "I remember waiting."
    assert len(dispatcher.requests) == 2
    persona = persona_layer(agent._profile, "Alice")
    memory = life_prompt.empty_memory_layer()
    assert dispatcher.requests[0]["messages"] == [
        {"role": "system", "content": render_layer_json(life_prompt.READONLY_SYSTEM_LAYER)},
        {"role": "user", "content": render_layer_json([persona, memory])},
        {"role": "user", "content": "What happened?"}]
    assert "爱散步" not in json.dumps(dispatcher.requests[0], ensure_ascii=False)
    assert [{tool["function"]["name"] for tool in item["tools"]} for item in dispatcher.requests] == [{"finish"}] * 2
    assert "TOOL_CALL_REQUIRED" in dispatcher.requests[1]["messages"][-1]["content"]
    assert environment.calls == environment.memory_updates == environment.queries == []
    assert agent._last_finish_memories is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["action", "readonly"])
async def test_each_model_entry_preflights_the_complete_token_request(monkeypatch, stage: str) -> None:
    """@brief 行动与只读入口达到窗口八成五时都在发送前报容量故障。"""
    readonly = stage == "readonly"
    responses = [_finish_response()] if readonly else [_action_response()]
    agent, environment, dispatcher = _configured(responses, 3)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: 524288)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda request: 445645)
    with pytest.raises(RuntimeError, match="model request capacity exceeded"):
        await agent.run_react_loop(_request(readonly=readonly))
    assert dispatcher.requests == []
    assert environment.calls == []


@pytest.mark.asyncio
async def test_long_system_message_passes_when_complete_request_is_below_threshold(monkeypatch) -> None:
    """@brief system 字符数不设熔断：整次请求低于窗口阈值时只读决策照常发送。"""
    long_layer = {**life_prompt.READONLY_SYSTEM_LAYER, "role": life_prompt.READONLY_SYSTEM_LAYER["role"] + "很长" * 1600}
    monkeypatch.setattr(life_prompt, "READONLY_SYSTEM_LAYER", long_layer)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: 524288)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda request: 445644)
    agent, _, dispatcher = _configured([_finish_response()], 1)
    await agent.run_react_loop(_request(readonly=True))
    assert len(dispatcher.requests[0]["messages"][0]["content"]) > 3000


@pytest.mark.asyncio
async def test_start_tool_descriptions_say_purpose_only_and_conditions_stay_in_the_tool_layer() -> None:
    """@brief 两个开始工具的描述只写用途（各占哪个槽位、能开始哪几种活动）；动作的时长与未满足的条件改由工具层字段表达
    （duration、reason），「标为不可用会被拒绝、原因就是 reason」只在系统层写一次（docs/design/06-agent.md 第一章
    「工具层」「系统层」）。"""
    agent, environment, dispatcher = _configured([_action_response()], 3)
    await agent.run_react_loop(_request())
    request = dispatcher.requests[0]
    descriptions = {tool["function"]["name"]: tool["function"]["description"] for tool in request["tools"]}
    assert "start_activity" not in descriptions
    for tool, words in ((MAIN_TOOL, ("槽位 1", "出行", "在当前地点做一件事", "睡觉")),
                        (SIDE_TOOL, ("槽位 2", "购买商品", "查询商品", "内容 app"))):
        assert descriptions[tool] == life_prompt.TOOL_DESCRIPTIONS[tool]
        assert all(word in descriptions[tool] for word in words)
        assert "reason" not in descriptions[tool] and "available" not in descriptions[tool]
    rules = json.loads(request["messages"][0]["content"])["rules"]
    assert [rule for rule in rules if "reason" in rule] == [
        "工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。查询记忆和回看任务当场完成，不推进时间。"
        "available 为 true 的动作单独提交都会受理；同一件事做几次、先做哪件，环境都不另作处理。"]
    tools = expand_tool_layer(life_prompt.user_layer(json.loads(request["messages"][1]["content"]), "工具层"))
    task = next(target for target in tools["targets"]
                if target["name"] == "整理会议纪要")
    [work] = task["actions"]
    assert (work["available"], work["reason"]) == (False, "整理会议纪要需要在办公室")
    assert "way" not in work and work["detail"] is None
    assert environment.calls[0].tool_name == MAIN_TOOL
    assert environment.calls[0].request.model_dump(by_alias=True) == _WAIT_CHOICE
