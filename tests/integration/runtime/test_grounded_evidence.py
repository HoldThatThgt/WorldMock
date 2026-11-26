"""动态工具请求的持久化、身份冻结和跨目录去重回归。"""


import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import resolved_binding
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, main_arguments, stage_day_plan, stub_day_session  # noqa: F401
from worldmock.agents.life_agent import LifeAgent, life_action_tool_names, life_query_tool_names, person_inputs
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.exchange_engine.conditions.activity_rules import MAIN_ACTIVITY_ACTIONS, SIDE_ACTIVITY_ACTIONS
from worldmock.runtime.framework import setup as run_setup
from worldmock.state.model.exchange import ModelCallEvidence

@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch):
    """用固定供应商窗口测试请求持久化，不依赖远程元数据服务。"""
    monkeypatch.setattr('worldmock.agents.prompt.prompt_capacity.model_context_length', lambda _model, metadata: metadata.context_window)


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
    """先返回未知工具，再返回在家里合法的开始请求。"""

    model_name = "test-model"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        valid = json.dumps(main_arguments("interact", "家", "测试休息"), ensure_ascii=False)
        self.responses = [
            _PersistentResponse("call-invalid", "invented_tool", "{}"),
            _PersistentResponse("call-valid", MAIN_TOOL, valid),
        ]

    async def call(self, request: dict[str, Any]) -> Any:
        """记录实际模型请求并返回下一个响应。"""
        self.requests.append(request)
        return self.responses.pop(0)

    async def close(self) -> None:
        return None


def _person(engine: Any) -> LifeAgent:
    """按人设表构造无状态 LifeAgent。"""
    return LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1]))


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


def test_model_request_persists_exact_grounded_tools(_engine_factory):  # noqa: F811
    """真实环境观察生成的完整工具请求经关闭重开数据库后保持原值；工具不带逐轮枚举，消息不附加预算文字。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    agent = _person(engine)
    tools = agent.turn_tools(2, False)
    wire = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Choose a visible activity."}],
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 4096,
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    expected = canonical_json(wire)
    fields = _start_fields(tools)
    assert set(fields[MAIN_TOOL]) == {"action", "target", "detail"}
    assert set(fields[SIDE_TOOL]) == {"action", "target", "prefer"}
    _assert_names_are_not_enumerated(fields)
    sandbox.record_model_exchange(ModelCallEvidence(
        wakeup_id=wakeup.wakeup_id, agent_id=1, call_id="grounded-call", phase="request", attempt=1, request=wire,
    ))
    engine.commit_barrier([sandbox.finish_wakeup(None, stub_day_session(wakeup))])
    cutoff = engine.context.store.last_event_seq
    directory = engine.context.store.run_dir
    engine.close()

    reopened = FootprintStore(directory)
    try:
        requests = [event for event in list(reopened.iter_events(cutoff)) if event.event_type == "model_request"]
        assert len(requests) == 1
        assert canonical_json(requests[0].data["request"]) == expected
        assert requests[0].data["request"]["tools"] == tools
        assert requests[0].data["request"]["messages"] == [{"role": "user", "content": "Choose a visible activity."}]
    finally:
        reopened.close()


async def _run_persisted_protocol_correction(engine) -> Path:
    """通过真实人物、沙盒、AgentLoop 与屏障提交执行一次协议纠正，纠正后的开始请求登记成功。"""
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    binding = resolved_binding(_CorrectionDispatcher(), frozen=engine.context.inputs, stage="action", max_turns=3)
    outcome = await _person(engine).decide_wakeup(wakeup, sandbox, binding)
    engine.commit_barrier([outcome])
    return engine.context.store.run_dir


def test_protocol_feedback_is_persisted_in_next_model_request(_engine_factory):  # noqa: F811
    """非法原始响应与实际反馈请求均通过真实 SQLite 边界留证。"""
    import asyncio

    engine = _engine_factory()
    stage_day_plan(engine)
    directory = asyncio.run(_run_persisted_protocol_correction(engine))
    engine.close()

    reopened = FootprintStore(directory)
    try:
        events = [
            event for event in list(reopened.iter_events(reopened.last_event_seq))
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
        assert first_names == second_names == life_action_tool_names() | life_query_tool_names()
        assert all("Authoritative" not in event.data["request"]["messages"][0]["content"]
                   for event in (events[0], events[2]))
        assert len([event for event in list(reopened.iter_events(reopened.last_event_seq))
                    if event.event_type == "decision_started"]) == 1
    finally:
        reopened.close()


def test_runtime_code_identity_includes_life_agent_content(tmp_path: Path, monkeypatch):
    """只改变人物实现字节就必须改变整个运行身份；项目根指到临时工程根。"""
    for name in ("pyproject.toml", "uv.lock"):
        (tmp_path / name).write_text("frozen dependency fixture\n")
    source = tmp_path / "src" / "worldmock" / "agents" / "life_agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("TARGET_SOURCE = 'current_observation'\n")
    monkeypatch.setattr(run_setup, "project_root", lambda: tmp_path)
    before = run_setup.runtime_code_sha256()

    source.write_text("TARGET_SOURCE = 'unbound'\n")

    assert run_setup.runtime_code_sha256() != before


def test_grounded_tool_uses_unique_readable_names_without_internal_ids(_engine_factory):  # noqa: F811
    """本轮工具层的目标名称唯一且与私有绑定一一对应，原生工具不带名称枚举，也不泄露内部对象身份。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    observation = wakeup.observation
    tools = _person(engine).turn_tools(2, False)
    _assert_names_are_not_enumerated(_start_fields(tools))
    public = [row["name"] for row in observation["tool_layer"]["targets"]]
    private = [row["object_id"] for row in observation["interaction_bindings"]]

    assert len(public) == len(set(public))
    assert {row["object"] for row in observation["interaction_bindings"]} <= set(public)
    assert not set(private) & set(public)
    assert not any(identity in str(tools) for identity in private if len(identity) > 3)
