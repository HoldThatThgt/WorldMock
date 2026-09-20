"""真实人物、环境与足迹边界上的跨轮决策上下文验收。"""

from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tests.life.conftest import (
    advance_next,
    context,
    engine_factory as _engine_factory,
    stage_day_plan,
)
from tests.integration.test_life_agent_protocol import _DecisionStatementResponse, _ModelResponse
from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv


class _RoundDispatcher:
    """只替换远程模型传输，其余调用与结算使用生产实现。"""

    def __init__(self):
        self.requests = []
        self.responses = [
            _DecisionStatementResponse("我先歇一会儿，再接着做事。"),
            _ModelResponse(
                "provider-action", "start_basic_activity",
                '{"activity_type":"wait","planned_duration_seconds":60}',
                reasoning_content="The person chooses a short rest.",
            ),
            _ModelResponse("provider-finish", "finish", '{"memories":[]}'),
        ]

    async def call(self, **request):
        """保存真正发送的请求，返回预定的提供商形状响应。"""
        self.requests.append(copy.deepcopy(request))
        return self.responses.pop(0)


def _person(environment, ctx, dispatcher):
    """把人物绑定到真实环境和本轮宿主身份。"""
    person = LifeAgent()
    person._id = ctx.agent_id
    person._name = "测试人物"
    person._profile = {"id": ctx.agent_id, "name": "测试人物"}
    person._env = environment
    person._dispatcher = dispatcher
    person._model_name = "deepseek-flash"
    person._decision_context = ctx
    person._max_react_turns = 3
    person._enable_memory = False
    person.trace_span = lambda *args, **kwargs: nullcontext(SimpleNamespace(attributes={}))
    return person


async def _decide(environment, ctx):
    """执行一个真实观察与人物循环，返回边界前后的请求证据。"""
    dispatcher = _RoundDispatcher()
    person = _person(environment, ctx, dispatcher)
    observation = await environment.observe(ctx)
    before = environment.engine.state.model_dump(mode="json")
    request = ReactLoopRequest(0, ctx.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation},
    ])
    await person.run_react_loop(request)
    assert environment.engine.state.model_dump(mode="json") == before
    assert len(dispatcher.requests) == 3
    assert dispatcher.requests[1]["messages"][1] == dispatcher.requests[2]["messages"][1]
    tool = dispatcher.requests[-1]["messages"][-1]
    assert tool["tool_call_id"] == "provider-action"
    payload = json.loads(tool["content"])
    for key in ("action_id", "decision_id", "round_id", "request_sha256", "submitted"):
        assert key not in tool["content"]
    assert payload["data"] == {}
    assert dispatcher.requests[-1]["messages"][-2]["reasoning_content"]
    assert request.observations[-1]["data"]["action_id"] == f"{ctx.decision_id}:action:0001"
    return observation, dispatcher.requests


@pytest.mark.asyncio
async def test_real_context_roundtrip_preserves_evidence_and_delays_results(_engine_factory):
    """登记留宿主、结算后回灌和新自述事件通过真实环境与 SQLite 一起验证。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = LifeEnv()
    environment.engine = engine
    first = context(engine, "context-first")
    engine.begin_decision_round([first])
    initial, first_requests = await _decide(environment, first)
    assert initial["facts"]["recent_action_results"] == []
    results = engine.commit_decision_round(first.round_id)
    assert results[0].status.value == "in_progress"
    advance_next(engine)

    second = context(engine, "context-second")
    engine.begin_decision_round([second])
    feedback, second_requests = await _decide(environment, second)
    public = feedback["facts"]["recent_action_results"]
    assert {row["status"] for row in public} == {"in_progress", "completed"}
    assert all(row["action_id"] == f"{first.decision_id}:action:0001" for row in public)
    assert first.decision_id in second_requests[1]["messages"][1]["content"]
    assert first.decision_id not in first_requests[-1]["messages"][-1]["content"]
    engine.commit_decision_round(second.round_id)

    events = engine.store.read_events(engine.store.last_event_seq)
    starts = [event for event in events if event.event_type == "decision_started"]
    assert len(starts) == 2
    assert all(event.data["decision_statement"] == "我先歇一会儿，再接着做事。" for event in starts)
    actions = [event for event in events if event.event_type == "action_requested"]
    assert len(actions) == 2
    assert all(len(event.data["request_sha256"]) == 64 for event in actions)
    observed = [event for event in events if event.event_type == "observation"]
    assert len(observed) == 2
