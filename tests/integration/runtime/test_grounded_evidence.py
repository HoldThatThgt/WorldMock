from tests.integration.runtime.current_contract_fixtures import profile as current_profile
"""动态工具请求的持久化、身份冻结和跨目录去重回归。"""


import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ModelCallEvidence, ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (MAIN_TOOL, SIDE_TOOL, context, stage_day_plan)  # noqa: F401
from worldmock.footprints.models import canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.life.action_contract import MAIN_ACTIVITY_ACTIONS, SIDE_ACTIVITY_ACTIONS
from worldmock.run_setup import runtime_code_sha256
from tests.integration.as2_fixtures import use_fake_as2

@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch):
    """用固定供应商窗口测试请求持久化，不依赖远程元数据服务。"""
    monkeypatch.setattr("worldmock.life.prompt_capacity.model_context_length", lambda _model, metadata: metadata.context_window)


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


def _start_fields(tools: list[dict]) -> dict[str, dict]:
    """两个开始工具各自的参数属性；已删除的 start_activity 不在原生工具里。"""
    fields = {item["function"]["name"]: item["function"]["parameters"]["properties"] for item in tools}
    assert "start_activity" not in fields
    return {name: fields[name] for name in (MAIN_TOOL, SIDE_TOOL)}


def _assert_names_are_not_enumerated(fields: dict[str, dict]) -> None:
    """名称参数（target 与 detail）不带逐轮枚举；action 的枚举只是固定的动作闭集。"""
    assert fields[MAIN_TOOL]["action"]["enum"] == list(MAIN_ACTIVITY_ACTIONS)
    assert fields[SIDE_TOOL]["action"]["enum"] == list(SIDE_ACTIVITY_ACTIONS)
    names = [fields[MAIN_TOOL]["target"], fields[MAIN_TOOL]["detail"], fields[SIDE_TOOL]["target"]]
    assert "enum" not in json.dumps(names, ensure_ascii=False)


def _prepared_tools(data, observed_at):
    """用明确首观察生成实际原生工具声明。"""
    agent = LifeAgent()
    agent._profile = current_profile()
    observation = {"turn": 0, "action": "observe", "ok": True, "data": data}
    agent.prepare_react_loop(ReactLoopRequest(0, observed_at, [observation]))
    return agent, agent.build_react_tool_schemas(readonly=False)


def test_model_request_persists_exact_grounded_tools(_engine_factory):  # noqa: F811
    """真实环境观察生成的完整工具请求经关闭重开数据库后保持原值；工具不带逐轮枚举，消息不附加预算文字。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    ctx = context(engine, "grounded-wire")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    agent, tools = _prepared_tools(observation, ctx.observed_at)
    agent._model_name = "test-model"
    wire = agent._model_request([{"role": "user", "content": "Choose a visible activity."}], tools)
    expected = canonical_json(wire)
    fields = _start_fields(tools)
    assert set(fields[MAIN_TOOL]) == {"action", "target", "detail"}
    assert set(fields[SIDE_TOOL]) == {"action", "target"}
    _assert_names_are_not_enumerated(fields)
    engine.record_model_exchange(ModelCallEvidence(ctx, "grounded-call", "request", 1, wire))
    cutoff = engine.context.store.last_event_seq
    directory = engine.context.store.run_dir
    engine.close()

    reopened = FootprintStore(directory)
    try:
        requests = [event for event in reopened.read_events(cutoff) if event.event_type == "model_request"]
        assert len(requests) == 1
        assert canonical_json(requests[0].data["request"]) == expected
        assert requests[0].data["request"]["tools"] == tools
        assert requests[0].data["request"]["messages"] == [{"role": "user", "content": "Choose a visible activity."}]
    finally:
        reopened.close()


async def _run_persisted_protocol_correction(engine, ctx) -> Path:
    """通过真实人物与环境证据接口执行一次协议纠正。"""
    agent = LifeAgent()
    agent._profile = current_profile()
    agent._id = ctx.agent_id
    agent._name = "Alice"
    agent._profile = {**current_profile(), **{'id': ctx.agent_id, 'name': 'Alice'}}
    agent._model_name = "test-model"
    agent._dispatcher = _CorrectionDispatcher()
    agent._env = _EngineEvidenceEnvironment(engine)
    agent._decision_context = ctx
    agent._max_react_turns = 2
    agent.trace_span = lambda *args, **kwargs: nullcontext()  # type: ignore[method-assign]
    request = ReactLoopRequest(0, ctx.observed_at, [], "What happened?", True)
    assert await agent.run_react_loop(request) == "fixed"
    return engine.context.store.run_dir


def test_protocol_feedback_is_persisted_in_next_model_request(_engine_factory):  # noqa: F811
    """非法原始响应与实际反馈请求均通过真实 SQLite 边界留证。"""
    import asyncio

    engine = _engine_factory()
    stage_day_plan(engine)
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
        assert events[1].data["error"] == "TOOL_CALL_REQUIRED: The tool call batch is invalid."
        raw_call = events[1].data["response"]["choices"][0]["message"]["tool_calls"][0]
        assert raw_call["function"]["name"] == "invented_tool"
        messages = events[2].data["request"]["messages"]
        assert [message["role"] for message in messages][-2:] == ["assistant", "tool"]
        feedback = json.loads(messages[-1]["content"])
        assert (feedback["error_code"], feedback["reason"]) == ("TOOL_CALL_REQUIRED", "The tool call batch is invalid.")
        assert feedback["items"][0]["path"] == ["tool_calls", 0, "function", "name"]
        assert feedback["items"][0]["written"] == "invented_tool"
        assert feedback["instruction"] == (
            "No world action was registered. Correct the reported parameters and submit again.")
        assert "error" not in feedback
        first_names = {item["function"]["name"] for item in events[0].data["request"]["tools"]}
        second_names = {item["function"]["name"] for item in events[2].data["request"]["tools"]}
        assert "memory_recent" in first_names
        assert second_names == {"finish"}
        assert all("Authoritative" not in event.data["request"]["messages"][0]["content"]
                   for event in (events[0], events[2]))
        assert [event for event in reopened.read_events(reopened.last_event_seq)
                if event.event_type == "decision_started"] == []
    finally:
        reopened.close()


def test_runtime_code_identity_includes_life_agent_content(tmp_path: Path, monkeypatch, tmp_path_factory):
    """只改变人物实现字节就必须改变整个运行身份（AS2 取锁定的临时包，不依赖本机安装）。"""
    use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))
    for name in ("pyproject.toml", "uv.lock"):
        (tmp_path / name).write_text("frozen dependency fixture\n")
    source = tmp_path / "custom" / "agents" / "life_agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("TARGET_SOURCE = 'current_observation'\n")
    before = runtime_code_sha256(tmp_path)

    source.write_text("TARGET_SOURCE = 'unbound'\n")

    assert runtime_code_sha256(tmp_path) != before


def test_grounded_tool_uses_unique_readable_names_without_internal_ids(_engine_factory):  # noqa: F811
    """本轮工具层的目标名称唯一且与私有绑定一一对应，原生工具不带名称枚举，也不泄露内部对象身份。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    ctx = context(engine, "cross-catalog-id")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    _, tools = _prepared_tools(observation, ctx.observed_at)
    _assert_names_are_not_enumerated(_start_fields(tools))
    public = [row["name"] for row in observation["tool_layer"]["targets"]]
    private = [row["object_id"] for row in observation["interaction_bindings"]]

    assert len(public) == len(set(public))
    assert {row["object"] for row in observation["interaction_bindings"]} <= set(public)
    assert not set(private) & set(public)
    assert not any(identity in str(tools) for identity in private if len(identity) > 3)
