"""@brief 在真实人物回合请求路径验证七层请求、沙盒隔离与整次请求容量。"""

from __future__ import annotations

import copy
import json
import logging
import re

import pytest

from tests.integration.runtime.current_contract_fixtures import ScriptedModel, ToolResponse, resolved_binding
from tests.life.conftest import MAIN_TOOL, SEARCH_TOOL, SIDE_TOOL, main_arguments, stage_day_plan, wakeup_for
from worldmock.agents import life_agent
from worldmock.agents.life_agent import LifeAgent, person_inputs
from worldmock.agents.prompt import life_prompt, prompt_capacity
from worldmock.agents.prompt.tool_groups import expand_tool_layer
from worldmock.state.interaction.requests import REQUEST_MODELS

_REST = main_arguments("interact", "家", "测试休息")
_SEARCH_ARGS = {"category": "饮食", "time": "今天", "keyword": "休息"}


@pytest.fixture(autouse=True)
def _fixed_model_context(monkeypatch) -> None:
    """@brief 固定模型窗口，避免读取在线供应商元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


@pytest.mark.parametrize("layer", ["LIFE_SYSTEM_LAYER"])
def test_prompt_fingerprint_binds_every_formal_template(monkeypatch, layer: str) -> None:
    """@brief 生活系统层的任何改动都会改变运行指纹。"""
    original = life_prompt.life_prompt_sha256()
    changed = copy.deepcopy(getattr(life_prompt, layer))
    changed["rules"].append("新增一条规则。")
    monkeypatch.setattr(life_prompt, layer, changed)
    assert life_prompt.life_prompt_sha256() != original


@pytest.mark.parametrize("tool", [MAIN_TOOL, SIDE_TOOL, SEARCH_TOOL])
def test_tool_fingerprint_binds_each_start_tool_description(monkeypatch, tool: str) -> None:
    """@brief 两个开始工具与 search_world 任何一个的用途说明变更都会改变实际工具指纹。"""
    original = life_agent.life_tool_schema_sha256()
    monkeypatch.setitem(life_prompt.TOOL_DESCRIPTIONS, tool, life_prompt.TOOL_DESCRIPTIONS[tool] + "改")
    assert life_agent.life_tool_schema_sha256() != original


class _FailingScriptedModel(ScriptedModel):
    """@brief 允许在脚本项中注入传输异常的脚本模型客户端。"""

    async def call(self, request):
        """@brief 捕获请求后弹出脚本项，异常项直接抛出。"""
        self.requests.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("Scripted model responses exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _setup(engine_factory, responses: list, max_turns: int = 6) -> tuple:
    """@brief 构造真实引擎沙盒、人物唤醒适配器和脚本模型绑定。"""
    engine = engine_factory()
    stage_day_plan(engine)
    wakeup = wakeup_for(engine)
    sandbox = engine.open_sandbox(wakeup)
    agent = LifeAgent(person_inputs(engine.context.inputs.persona_by_id[wakeup.agent_id]))
    client = _FailingScriptedModel(responses)
    binding = resolved_binding(client, engine.context.inputs, agent_id=wakeup.agent_id, max_turns=max_turns)
    return engine, wakeup, sandbox, agent, client, binding


def _action_response() -> ToolResponse:
    """@brief 返回带提供商思考字段的合法生活动作（世界请求登记即结束本轮）。"""
    return ToolResponse(
        MAIN_TOOL, _REST, call_id="provider-action-call",
        reasoning="I intend to rest; the outcome is not known yet.",
    )


@pytest.mark.asyncio
async def test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user(engine_factory) -> None:
    """@brief 检索结果即时可用且原生配对、思考字段保持；沙盒登记后内部身份与摘要不进入发给模型的内容。"""
    _, wakeup, sandbox, agent, client, binding = _setup(engine_factory, [
        ToolResponse("search_memory", _SEARCH_ARGS, call_id="memory-first",
                     reasoning="I should recall what I did yesterday."),
        ToolResponse("search_memory", _SEARCH_ARGS, call_id="memory-second", reasoning=""),
        _action_response(),
    ], max_turns=5)
    queries = []
    original_search = sandbox.search_memory

    def recording_search(req):
        ans = original_search(req)
        queries.append(copy.deepcopy(ans))
        return ans

    sandbox.search_memory = recording_search
    outcome = await agent.decide_wakeup(wakeup, sandbox, binding)
    assert outcome.status == "completed"
    assert [op["tool_name"] for op in outcome.action_operations] == [MAIN_TOOL]
    assert outcome.action_operations[0]["action_id"] == f"{wakeup.wakeup_id}:action:0001"
    assert len(client.requests) == 3
    sent = json.dumps([item["messages"] for item in client.requests], ensure_ascii=False)
    for forbidden in (
        "request_sha256", "run_id", "round_id", "wakeup_id", "agent_id", "action_id", "submitted",
        wakeup.wakeup_id,
    ):
        assert re.search(rf"(?<!\w){re.escape(forbidden)}(?!\w)", sent) is None
    followup = client.requests[-1]["messages"]
    assistant = next(item for item in followup if item.get("reasoning_content"))
    assert assistant["reasoning_content"] == "I should recall what I did yesterday."
    assert assistant["tool_calls"][0]["id"] == "memory-first"
    memory_tools = [item for item in followup if item.get("tool_call_id", "").startswith("memory-")]
    assert len(memory_tools) == len(queries) == 2
    assert [json.loads(item["content"]) for item in memory_tools] == queries
    users = [item["messages"][1]["content"].encode() for item in client.requests]
    systems = [item["messages"][0]["content"].encode() for item in client.requests]
    assert all(user == users[0] for user in users) and all(system == systems[0] for system in systems)
    for item in client.requests:
        names = {tool["function"]["name"] for tool in item["tools"]}
        assert "finish" not in names
        assert set(REQUEST_MODELS) <= names


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["transport", "request", "response"])
async def test_action_transport_and_evidence_failures_are_logged_without_retry(
        engine_factory, monkeypatch, caplog, failure_phase: str) -> None:
    """@brief 行动请求的传输或证据保存错误直接终止、不重试、不登记世界请求，并产生英文错误日志。"""
    responses = [TimeoutError("transport failed")] if failure_phase == "transport" else [_action_response()]
    _, wakeup, sandbox, agent, client, binding = _setup(engine_factory, responses)
    original = sandbox.record_model_exchange

    def record(exchange):
        if exchange.phase == failure_phase:
            raise OSError("evidence storage failed")
        original(exchange)

    monkeypatch.setattr(sandbox, "record_model_exchange", record)
    expected = TimeoutError if failure_phase == "transport" else RuntimeError
    with caplog.at_level(logging.ERROR), pytest.raises(expected) as failure:
        await agent.decide_wakeup(wakeup, sandbox, binding)
    if failure_phase != "transport":
        assert isinstance(failure.value.__cause__, OSError)
        assert failure.value.feedback.report.error_code == "EVIDENCE_WRITE_FAILED"
    assert len(client.requests) == (0 if failure_phase == "request" else 1)
    assert sandbox._operations == []
    errors = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert all(not any("一" <= char <= "鿿" for char in error) for error in errors)


@pytest.mark.asyncio
async def test_each_model_entry_preflights_the_complete_token_request(engine_factory, monkeypatch) -> None:
    """@brief 行动入口达到窗口八成五时在发送前报容量故障。"""
    _, wakeup, sandbox, agent, client, binding = _setup(engine_factory, [_action_response()], max_turns=3)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: 524288)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda request: 445645)
    with pytest.raises(RuntimeError, match="model request capacity exceeded"):
        await agent.decide_wakeup(wakeup, sandbox, binding)
    assert client.requests == []
    assert sandbox._operations == []


@pytest.mark.asyncio
async def test_long_system_message_passes_when_complete_request_is_below_threshold(engine_factory, monkeypatch) -> None:
    """@brief system 字符数不设熔断：整次请求低于窗口阈值时行动决策照常发送。"""
    long_layer = {**life_prompt.LIFE_SYSTEM_LAYER, "role": life_prompt.LIFE_SYSTEM_LAYER["role"] + "很长" * 1600}
    monkeypatch.setattr(life_prompt, "LIFE_SYSTEM_LAYER", long_layer)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: 524288)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda request: 445644)
    _, wakeup, sandbox, agent, client, binding = _setup(engine_factory, [_action_response()], max_turns=1)
    await agent.decide_wakeup(wakeup, sandbox, binding)
    assert len(client.requests[0]["messages"][0]["content"]) > 3000


@pytest.mark.asyncio
async def test_start_tool_descriptions_say_purpose_only_and_conditions_stay_in_the_tool_layer(engine_factory) -> None:
    """@brief 开始工具与 search_world 的描述只写用途；动作的时长与未满足条件由工具层字段表达，系统层规则只在系统消息出现。"""
    _, wakeup, sandbox, agent, client, binding = _setup(engine_factory, [_action_response()], max_turns=3)
    outcome = await agent.decide_wakeup(wakeup, sandbox, binding)
    request = client.requests[0]
    descriptions = {tool["function"]["name"]: tool["function"]["description"] for tool in request["tools"]}
    assert "start_activity" not in descriptions
    for tool, words in (
        (MAIN_TOOL, ("槽位 1", "出行", "在当前地点做一件事", "睡觉")),
        (SIDE_TOOL, ("槽位 2", "购买商品", "内容 app")),
        (SEARCH_TOOL, ("手机应用", "同城目录", "商品", "地点")),
    ):
        assert descriptions[tool] == life_prompt.TOOL_DESCRIPTIONS[tool]
        assert all(word in descriptions[tool] for word in words)
        assert "reason" not in descriptions[tool] and "available" not in descriptions[tool]
    rules = json.loads(request["messages"][0]["content"])["rules"]
    assert any("工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。" in rule for rule in rules)
    assert any("查询记忆、回看任务与提交控制请求在本次唤醒内当场完成并回传，不推进时间。" in rule for rule in rules)
    tools = expand_tool_layer(life_prompt.user_layer(json.loads(request["messages"][1]["content"]), "工具层"))
    work = next(action for target in tools["targets"] for action in target["actions"] if not action["available"])
    assert (work["available"], "way" not in work) == (False, True) and work["reason"]
    assert outcome.action_operations[0]["tool_name"] == MAIN_TOOL
    assert outcome.action_operations[0]["request"] == _REST
