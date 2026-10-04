"""严格 ReAct 原生工具协议与记忆验证测试。"""

import asyncio
from datetime import datetime, timezone
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentsociety2.agent.base import (
    AgentBase,
    ReactDecision,
    ReactLoopRequest,
    ReactToolResult,
)
from agentsociety2.agent.person_prompt import build_preamble
from agentsociety2.society import DecisionContext


class _DummyAgent(AgentBase):
    """提供协议单元测试所需的最小人物。"""

    @classmethod
    def create(cls, workspace_path: Path, profile: dict, config: dict) -> None:
        raise NotImplementedError

    async def to_workspace(self, workspace_path: Path) -> None:
        raise NotImplementedError

    async def ask(self, message: str, readonly: bool = True, *, t=None) -> str:
        raise NotImplementedError

    async def step(self, tick: int, t: datetime) -> str:
        raise NotImplementedError


def _response(*calls: tuple[str, str], content: str = "") -> Any:
    tool_calls = [
        SimpleNamespace(
            id=f"call-{index}",
            type="function",
            function=SimpleNamespace(name=name, arguments=args),
        )
        for index, (name, args) in enumerate(calls, start=1)
    ]
    message = SimpleNamespace(tool_calls=tool_calls, content=content)

    def model_dump(*, mode: str) -> dict[str, Any]:
        assert mode == "json"
        serialized = [
            {
                "id": item.id,
                "type": item.type,
                "function": {
                    "name": item.function.name,
                    "arguments": item.function.arguments,
                },
            }
            for item in tool_calls
        ]
        return {"choices": [{"message": {"content": content, "tool_calls": serialized}}]}

    return SimpleNamespace(choices=[SimpleNamespace(message=message)], model_dump=model_dump)


class _SequenceDispatcher:
    """按顺序返回模型响应或抛出明确传输错误。"""

    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)
        self.requests: list[dict[str, Any]] = []

    async def call(self, **request: Any) -> Any:
        """记录实际请求并返回下一个结果。"""
        self.requests.append(request)
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _EvidenceEnvironment:
    """保存模型调用证据供协议断言。"""

    def __init__(self) -> None:
        self.evidence: list[Any] = []

    async def record_model_exchange(self, exchange: Any) -> None:
        """保存一次请求、响应或失败证据。"""
        self.evidence.append(exchange)


def _configured_agent(
    items: list[Any],
    max_turns: int = 3,
) -> tuple[_DummyAgent, _SequenceDispatcher, _EvidenceEnvironment]:
    """构造使用真实证据钩子的最小只读人物。"""
    agent = _DummyAgent()
    dispatcher = _SequenceDispatcher(items)
    environment = _EvidenceEnvironment()
    agent._model_name = "model"
    agent._dispatcher = dispatcher
    agent._env = environment
    agent._decision_context = DecisionContext(
        "run", "round", "decision", 1, datetime(2026, 1, 5, tzinfo=timezone.utc), 0
    )
    agent._max_react_turns = max_turns
    agent.build_react_messages = lambda request: [{"role": "user", "content": "act"}]
    agent.build_react_tool_schemas = lambda **kwargs: [
        {"type": "function", "function": {"name": "finish", "parameters": {}}}
    ]
    agent.trace_span = lambda *args, **kwargs: nullcontext()  # type: ignore[method-assign]
    return agent, dispatcher, environment


def _readonly_request() -> ReactLoopRequest:
    """构造稳定只读 ReAct 请求。"""
    return ReactLoopRequest(
        1,
        datetime(2026, 1, 5, tzinfo=timezone.utc),
        [],
        readonly=True,
    )


def _feedback(message: dict[str, Any]) -> dict[str, Any]:
    """解析结构化协议反馈。"""
    return __import__("json").loads(message["content"])


def test_native_finish_accepts_empty_memory_list() -> None:
    agent = _DummyAgent()

    decisions, error = agent._parse_react_responses(
        _response(("finish", '{"memories": []}')),
    )

    assert error == ""
    assert len(decisions) == 1
    assert decisions[0].action == "finish"
    assert decisions[0].args["memories"] == []


def test_native_finish_rejects_non_list_memory() -> None:
    agent = _DummyAgent()

    decisions, error = agent._parse_react_responses(
        _response(("finish", '{"memories": "none"}')),
    )

    assert decisions == []
    assert "list" in error


def test_free_text_never_becomes_a_tool_call() -> None:
    agent = _DummyAgent()

    decisions, error = agent._parse_react_responses(
        _response(content='{"answer": "left"}'),
        readonly=True,
    )

    assert decisions == []
    assert error == "native tool_calls are required"


def test_finish_mixed_with_action_is_rejected_atomically() -> None:
    agent = _DummyAgent()
    memories = '[{"type":"observation","importance":0.5,"text":"done"}]'

    decisions, error = agent._parse_react_responses(
        _response(
            ("purchase", '{"product_id":"rice","quantity":1}'),
            ("finish", f'{{"memories":{memories}}}'),
        )
    )

    assert decisions == []
    assert error == "finish must be the only tool call in a response"


def test_invalid_json_is_not_repaired() -> None:
    agent = _DummyAgent()

    decisions, error = agent._parse_react_responses(
        _response(("purchase", "{'quantity': 1}"))
    )

    assert decisions == []
    assert "invalid tool arguments" in error


@pytest.mark.asyncio
async def test_unknown_tool_gets_bounded_feedback_and_self_correction() -> None:
    """未知工具整批不执行，错误经原生工具身份反馈后由模型纠正。"""
    agent, dispatcher, environment = _configured_agent([
        _response(("invented_tool", "{}")),
        _response(("finish", '{"answer":"corrected"}')),
    ])
    executed: list[str] = []

    async def execute(decision, *, readonly=False):
        executed.append(decision.action)
        return ReactToolResult(True, "unexpected", {})

    agent._execute_react_tool = execute  # type: ignore[method-assign]

    assert await agent.run_react_loop(_readonly_request()) == "corrected"
    assert executed == []
    assert len(dispatcher.requests) == 2
    correction = dispatcher.requests[1]["messages"]
    assert [item["role"] for item in correction] == ["user", "assistant", "tool"]
    assert correction[-2]["tool_calls"][0]["function"]["name"] == "invented_tool"
    assert _feedback(correction[-1])["executed"] is False
    assert [item.phase for item in environment.evidence] == [
        "request", "response", "request", "response",
    ]
    assert environment.evidence[1].error == "unknown native tool calls: ['invented_tool']"
    assert environment.evidence[0].call_id == environment.evidence[1].call_id
    assert environment.evidence[2].call_id == environment.evidence[3].call_id
    assert environment.evidence[0].call_id != environment.evidence[2].call_id
    assert {item.attempt for item in environment.evidence} == {1}
    assert all(item.request["max_retries"] == 0 for item in environment.evidence)


@pytest.mark.asyncio
async def test_invalid_json_uses_paired_tool_feedback() -> None:
    """参数文本解析失败仍以完整原生调用身份返回未执行反馈。"""
    agent, dispatcher, _ = _configured_agent([
        _response(("finish", "{'answer':'broken'}")),
        _response(("finish", '{"answer":"fixed"}')),
    ])

    assert await agent.run_react_loop(_readonly_request()) == "fixed"
    messages = dispatcher.requests[1]["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant", "tool"]
    assert "invalid tool arguments" in _feedback(messages[-1])["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken",
    ["missing", "blank", "duplicate", "type", "arguments", "name"],
)
async def test_unpairable_tool_calls_use_host_user_feedback(broken: str) -> None:
    """破损调用身份不进入下一次 wire，宿主用户消息要求模型重发。"""
    first = _response(("finish", '{"answer":"bad"}'), ("finish", '{"answer":"bad"}'))
    calls = first.choices[0].message.tool_calls
    if broken == "missing":
        calls.clear()
    elif broken == "blank":
        calls[0].id = ""
    elif broken == "duplicate":
        calls[1].id = calls[0].id
    elif broken == "type":
        calls[0].type = "custom"
        calls[0].function.name = "invented_tool"
    elif broken == "arguments":
        calls[0].function.name = "invented_tool"
        calls[0].function.arguments = {}
    else:
        calls[0].function.name = ""
    agent, dispatcher, _ = _configured_agent([
        first,
        _response(("finish", '{"answer":"fixed"}')),
    ])

    assert await agent.run_react_loop(_readonly_request()) == "fixed"
    messages = dispatcher.requests[1]["messages"]
    assert [item["role"] for item in messages] == ["user", "user"]
    assert all("tool_calls" not in item for item in messages)
    assert _feedback(messages[-1])["executed"] is False


@pytest.mark.asyncio
async def test_mixed_batch_is_rejected_without_partial_execution() -> None:
    """冲突批次的全部调用都返回未执行反馈且没有部分副作用。"""
    agent, dispatcher, _ = _configured_agent([
        _response(("invented_tool", "{}"), ("finish", '{"answer":"bad"}')),
        _response(("finish", '{"answer":"fixed"}')),
    ])
    executed: list[str] = []

    async def execute(decision, *, readonly=False):
        executed.append(decision.action)
        return ReactToolResult(True, "unexpected", {})

    agent._execute_react_tool = execute  # type: ignore[method-assign]

    assert await agent.run_react_loop(_readonly_request()) == "fixed"
    assert executed == []
    messages = dispatcher.requests[1]["messages"]
    feedback = [item for item in messages if item["role"] == "tool"]
    assert len(feedback) == 2
    assert all(_feedback(item)["executed"] is False for item in feedback)


@pytest.mark.asyncio
async def test_loop_exhaustion_is_fatal() -> None:
    agent = _DummyAgent()
    agent._max_react_turns = 1

    async def decisions(_request: ReactLoopRequest) -> list[ReactDecision]:
        return [ReactDecision("", "noop", {}, "", "call-1")]

    async def execute(_decision: ReactDecision, *, readonly=False) -> ReactToolResult:
        return ReactToolResult(True, "ok", {})

    agent._call_react_llm = decisions  # type: ignore[method-assign]
    agent._execute_react_tool = execute  # type: ignore[method-assign]
    agent.trace_span = lambda *args, **kwargs: nullcontext()  # type: ignore[method-assign]
    request = ReactLoopRequest(
        1,
        datetime(2026, 1, 5, tzinfo=timezone.utc),
        [],
    )

    with pytest.raises(RuntimeError, match="exhausted"):
        await agent.run_react_loop(request)


@pytest.mark.asyncio
async def test_protocol_errors_consume_exact_global_turn_budget() -> None:
    """协议错误用尽现有回合后失败且不会发出预算外请求。"""
    agent, dispatcher, environment = _configured_agent([
        _response(("invented_one", "{}")),
        _response(("invented_two", "{}")),
    ], max_turns=2)

    with pytest.raises(RuntimeError, match="decision budget exhausted"):
        await agent.run_react_loop(_readonly_request())

    assert len(dispatcher.requests) == 2
    assert [item.phase for item in environment.evidence] == [
        "request", "response", "request", "response",
    ]
    sent_feedback = dispatcher.requests[1]["messages"][-1]
    assert "invented_one" in _feedback(sent_feedback)["error"]
    assert "invented_two" not in sent_feedback["content"]


@pytest.mark.asyncio
async def test_transport_failure_is_fatal_without_protocol_feedback() -> None:
    """传输超时只记录一次失败并立即传播。"""
    agent, dispatcher, environment = _configured_agent(
        [TimeoutError("provider timed out")],
        max_turns=3,
    )

    with pytest.raises(TimeoutError, match="provider timed out"):
        await agent.run_react_loop(_readonly_request())

    assert len(dispatcher.requests) == 1
    assert [item.phase for item in environment.evidence] == ["request", "failure"]
    assert environment.evidence[-1].error == "TimeoutError('provider timed out')"


@pytest.mark.asyncio
async def test_cancellation_is_fatal_without_protocol_feedback() -> None:
    """任务取消只留下已发请求证据且不转成模型纠错。"""
    agent, dispatcher, environment = _configured_agent(
        [asyncio.CancelledError()],
        max_turns=3,
    )

    with pytest.raises(asyncio.CancelledError):
        await agent.run_react_loop(_readonly_request())

    assert len(dispatcher.requests) == 1
    assert [item.phase for item in environment.evidence] == ["request"]


@pytest.mark.asyncio
async def test_response_evidence_failure_is_fatal_without_new_model_call() -> None:
    """响应证据写入失败时不生成纠错请求。"""
    agent, dispatcher, environment = _configured_agent([
        _response(("invented_tool", "{}")),
        _response(("finish", '{"answer":"must-not-run"}')),
    ])

    async def fail_response(exchange: Any) -> None:
        if exchange.phase == "response":
            raise OSError("evidence unavailable")
        environment.evidence.append(exchange)

    environment.record_model_exchange = fail_response  # type: ignore[method-assign]

    with pytest.raises(OSError, match="evidence unavailable"):
        await agent.run_react_loop(_readonly_request())

    assert len(dispatcher.requests) == 1
    assert [item.phase for item in environment.evidence] == ["request"]


@pytest.mark.asyncio
async def test_unserializable_response_is_fatal_without_protocol_feedback() -> None:
    """无法可靠留证的响应立即失败且不进入纠错历史。"""
    agent, dispatcher, environment = _configured_agent([
        [],
        _response(("finish", '{"answer":"must-not-run"}')),
    ])

    with pytest.raises(TypeError, match="not serializable"):
        await agent.run_react_loop(_readonly_request())

    assert len(dispatcher.requests) == 1
    assert [item.phase for item in environment.evidence] == ["request"]


@pytest.mark.asyncio
async def test_tool_execution_failure_is_fatal_without_new_model_call() -> None:
    """已验证工具的环境执行异常不得进入协议纠错路径。"""
    agent, dispatcher, environment = _configured_agent([_response(("noop", "{}"))])
    agent.build_react_tool_schemas = lambda **kwargs: [
        {"type": "function", "function": {"name": "noop", "parameters": {}}}
    ]

    async def fail_execution(decision, *, readonly=False):
        raise OSError("environment unavailable")

    agent._execute_react_tool = fail_execution  # type: ignore[method-assign]

    with pytest.raises(OSError, match="environment unavailable"):
        await agent.run_react_loop(_readonly_request())

    assert len(dispatcher.requests) == 1
    assert [item.phase for item in environment.evidence] == ["request", "response"]


def test_ask_mode_prompt_requires_finish() -> None:
    prompt = build_preamble(name="Alice", ask_mode=True)

    assert "finish(answer=...)" in prompt
    assert "OVERRIDE REJECTION" in prompt


def _memory_runtime():
    from agentsociety2.agent.memory_runtime import (
        MemoryRuntimeConfig,
        PersonMemoryRuntime,
    )

    return PersonMemoryRuntime(
        agent_id=1,
        agent_name="Alice",
        config=MemoryRuntimeConfig(),
        get_model_name=lambda: "m",
        dispatch_llm=lambda **kwargs: None,
        get_profile=lambda: {},
        logger=__import__("logging").getLogger("test"),
    )


def test_invalid_finish_memory_is_fatal() -> None:
    runtime = _memory_runtime()

    with pytest.raises(Exception):
        runtime._validate_finish_memories(
            [{"type": "not-a-real-type", "importance": 0.5, "text": "bad"}]
        )
