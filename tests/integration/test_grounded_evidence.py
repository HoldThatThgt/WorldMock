"""动态工具请求的持久化、身份冻结和跨目录去重回归。"""

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ModelCallEvidence, ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from tests.life.conftest import context, engine_factory as _engine_factory  # noqa: F401
from worldmock.footprints.models import canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.runtime import runtime_code_sha256

_BUDGET_MARKER = "\n\nAuthoritative ReAct model-turn budget:\n"


def _request_budget(request: dict[str, Any]) -> dict[str, Any]:
    """读取已持久化模型请求的权威回合预算。"""
    content = request["messages"][0]["content"]
    return json.loads(content.split(_BUDGET_MARKER, 1)[1])


class _PersistentResponse:
    """提供可由真实模型证据路径持久化的原生工具响应。"""

    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        function = SimpleNamespace(name=name, arguments=arguments)
        tool_call = SimpleNamespace(id=call_id, type="function", function=function)
        self.choices = [SimpleNamespace(message=SimpleNamespace(
            content="",
            tool_calls=[tool_call],
        ))]

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        """返回真实事件存储接受的 JSON 响应。"""
        assert mode == "json"
        call = self.choices[0].message.tool_calls[0]
        return {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": call.id,
            "type": call.type,
            "function": {
                "name": call.function.name,
                "arguments": call.function.arguments,
            },
        }]}}]}


class _CorrectionDispatcher:
    """先返回未知工具，再返回合法只读结束。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses = [
            _PersistentResponse("call-invalid", "invented_tool", "{}"),
            _PersistentResponse("call-finish", "finish", '{"answer":"fixed"}'),
        ]

    async def call(self, **request: Any) -> Any:
        """记录实际模型请求并返回下一个响应。"""
        self.requests.append(request)
        return self.responses.pop(0)


class _EngineEvidenceEnvironment:
    """把人物模型证据写入真实 LifeEngine。"""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    async def record_model_exchange(self, exchange: Any) -> None:
        """同步调用真实环境模型证据事务。"""
        self.engine.record_model_exchange(exchange)


def _prepared_tools(data, observed_at):
    """用明确首观察生成实际原生工具声明。"""
    agent = LifeAgent()
    observation = {"turn": 0, "action": "observe", "ok": True, "data": data}
    agent.prepare_react_loop(ReactLoopRequest(0, observed_at, [observation]))
    return agent, agent.build_react_tool_schemas(readonly=False)


def test_model_request_persists_exact_grounded_tools(_engine_factory):  # noqa: F811
    """真实环境观察生成的完整工具请求经关闭重开数据库后保持原值。"""
    engine = _engine_factory()
    ctx = context(engine, "grounded-wire")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    agent, tools = _prepared_tools(observation, ctx.observed_at)
    agent._model_name = "deepseek-flash"
    wire = agent._model_request([{"role": "user", "content": "Choose a visible activity."}], tools)
    expected = canonical_json(wire)
    targeted = next(item for item in tools if item["function"]["name"] == "start_targeted_activity")
    assert "enum" in targeted["function"]["parameters"]["properties"]["target_id"]
    engine.record_model_exchange(ModelCallEvidence(ctx, "grounded-call", "request", 1, wire))
    cutoff = engine.store.last_event_seq
    directory = engine.store.run_dir
    engine.close()

    reopened = FootprintStore(directory)
    try:
        requests = [event for event in reopened.read_events(cutoff) if event.event_type == "model_request"]
        assert len(requests) == 1
        assert canonical_json(requests[0].data["request"]) == expected
        assert requests[0].data["request"]["tools"] == tools
        assert _request_budget(requests[0].data["request"])["phase"] == "before_action"
    finally:
        reopened.close()


async def _run_persisted_protocol_correction(engine, ctx) -> Path:
    """通过真实人物与环境证据接口执行一次协议纠正。"""
    agent = LifeAgent()
    agent._id = ctx.agent_id
    agent._name = "Alice"
    agent._profile = {"id": ctx.agent_id, "name": "Alice"}
    agent._model_name = "deepseek-flash"
    agent._dispatcher = _CorrectionDispatcher()
    agent._env = _EngineEvidenceEnvironment(engine)
    agent._decision_context = ctx
    agent._max_react_turns = 2
    agent.trace_span = lambda *args, **kwargs: nullcontext()  # type: ignore[method-assign]
    request = ReactLoopRequest(0, ctx.observed_at, [], "What happened?", True)
    assert await agent.run_react_loop(request) == "fixed"
    return engine.store.run_dir


def test_protocol_feedback_is_persisted_in_next_model_request(_engine_factory):  # noqa: F811
    """非法原始响应与实际反馈请求均通过真实 SQLite 边界留证。"""
    import asyncio

    engine = _engine_factory()
    ctx = context(engine, "protocol-feedback")
    engine.begin_decision_round([ctx])
    directory = asyncio.run(_run_persisted_protocol_correction(engine, ctx))
    engine.close()

    reopened = FootprintStore(directory)
    try:
        events = [
            event for event in reopened.read_events(reopened.last_event_seq)
            if event.event_type in {"model_request", "model_response"}
        ]
        assert [event.event_type for event in events] == [
            "model_request", "model_response", "model_request", "model_response",
        ]
        assert events[1].data["error"] == "unknown native tool calls: ['invented_tool']"
        raw_call = events[1].data["response"]["choices"][0]["message"]["tool_calls"][0]
        assert raw_call["function"]["name"] == "invented_tool"
        messages = events[2].data["request"]["messages"]
        assert [message["role"] for message in messages][-2:] == ["assistant", "tool"]
        feedback = json.loads(messages[-1]["content"])
        assert feedback["executed"] is False
        assert "unknown native tool calls" in feedback["error"]
        first_names = {item["function"]["name"] for item in events[0].data["request"]["tools"]}
        second_names = {item["function"]["name"] for item in events[2].data["request"]["tools"]}
        assert "memory_recent" in first_names
        assert second_names == {"finish"}
        assert [_request_budget(event.data["request"])["remaining_model_turns_including_current"]
                for event in (events[0], events[2])] == [2, 1]
    finally:
        reopened.close()


def test_runtime_code_identity_includes_life_agent_content(tmp_path: Path):
    """只改变人物实现字节就必须改变整个运行身份。"""
    for name in ("pyproject.toml", "uv.lock"):
        (tmp_path / name).write_text("frozen dependency fixture\n")
    source = tmp_path / "custom" / "agents" / "life_agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("TARGET_SOURCE = 'current_observation'\n")
    before = runtime_code_sha256(tmp_path)

    source.write_text("TARGET_SOURCE = 'unbound'\n")

    assert runtime_code_sha256(tmp_path) != before


def test_grounded_tool_deduplicates_target_ids_across_catalogs(_engine_factory):  # noqa: F811
    """不同目录中的相同身份在最终目标枚举中只出现一次。"""
    engine = _engine_factory()
    ctx = context(engine, "cross-catalog-id")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    observation["tasks"] = [{"task_id": "shared-id", "remaining_contract_seconds": 3600,
                             "material": {"end_char": 10, "total_chars": 10}}]
    observation["content_catalog"] = [{"item_id": "shared-id", "duration_seconds": 630}]
    _, tools = _prepared_tools(observation, ctx.observed_at)
    targeted = next(item for item in tools if item["function"]["name"] == "start_targeted_activity")
    ids = targeted["function"]["parameters"]["properties"]["target_id"]["enum"]

    assert ids.count("shared-id") == 1
    assert ids == sorted(set(ids))
