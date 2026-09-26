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
    pending_names,
    stage_day_plan,
)
from tests.integration.test_life_agent_protocol import _DecisionStatementResponse, _ModelResponse
from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv


class _RoundDispatcher:
    """只替换远程模型传输，其余调用与结算使用生产实现；世界请求登记即结束本轮，没有 finish。"""

    def __init__(self, action: tuple[str, dict]):
        self.requests = []
        tool_name, arguments = action
        self.responses = [
            _DecisionStatementResponse("我先歇一会儿，再接着做事。"),
            _ModelResponse(
                "provider-action", tool_name, json.dumps(arguments, ensure_ascii=False),
                reasoning_content="The person chooses what to do next.",
            ),
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


async def _decide(environment, ctx, choose):
    """执行一个真实观察与人物循环（choose 按本轮观察给出唯一世界请求），返回观察与发给模型的请求。"""
    observation = await environment.observe(ctx)
    dispatcher = _RoundDispatcher(choose(observation))
    person = _person(environment, ctx, dispatcher)
    before = environment.engine.state.model_dump(mode="json")
    request = ReactLoopRequest(0, ctx.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation},
    ])
    await person.run_react_loop(request)
    assert environment.engine.state.model_dump(mode="json") == before
    assert len(dispatcher.requests) == 2
    tool = person._react_messages[-1]
    assert tool["tool_call_id"] == "provider-action"
    payload = json.loads(tool["content"])
    for key in ("action_id", "decision_id", "round_id", "request_sha256", "submitted", "in_progress"):
        assert key not in tool["content"]
    assert payload["data"] == {}
    assert person._react_messages[-2]["reasoning_content"]
    assert request.observations[-1]["data"]["action_id"] == f"{ctx.decision_id}:action:0001"
    return observation, dispatcher.requests


def _wait(observation: dict) -> tuple[str, dict]:
    """开始一段等待。"""
    return "start_activity", {"targets": ["自己"], "actions": ["等待"]}


def _conclude_just_ended(observation: dict) -> tuple[str, dict]:
    """给本轮刚结束、待收尾的活动收尾，并写一条记忆。"""
    names = pending_names(observation)
    memory = {"type": "routine", "text": "歇了一会儿，精神好些了。", "importance": 0.3, "keywords": ["休息"]}
    return "conclude_activity", {"task": names, "summary": ["歇好了。" for _ in names],
                                 "memories": [[memory] for _ in names]}


@pytest.mark.asyncio
async def test_real_context_roundtrip_preserves_evidence_and_delays_results(_engine_factory, monkeypatch):
    """登记留宿主、结算后回灌（刚结束的活动当秒收尾）和新自述事件通过真实环境与 SQLite 一起验证。"""
    monkeypatch.setattr("worldmock.life.prompt_capacity.model_context_length", lambda _model: 131072)
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = LifeEnv()
    environment.engine = engine
    first = context(engine, "context-first")
    engine.begin_decision_round([first])
    initial, first_requests = await _decide(environment, first, _wait)
    assert initial["facts"]["progress"]["results"] == []
    results = engine.commit_decision_round(first.round_id)
    assert results[0].status.value == "in_progress"
    advance_next(engine)

    second = context(engine, "context-second")
    engine.begin_decision_round([second])
    feedback, second_requests = await _decide(environment, second, _conclude_just_ended)
    public = feedback["facts"]["progress"]["results"]
    assert {row["status"] for row in public} == {"in_progress", "completed"}
    assert all("action_id" not in row for row in public)
    [waited] = pending_names(feedback)
    assert "in_progress" in second_requests[1]["messages"][1]["content"]
    assert f"刚结束：{waited}。" in second_requests[1]["messages"][1]["content"]
    assert "in_progress" not in first_requests[-1]["messages"][1]["content"]
    concluded = engine.commit_decision_round(second.round_id)[0]
    assert concluded.status.value == "completed"
    assert [row["task"] for row in concluded.data["memory_sources"]] == [waited]

    events = engine.store.read_events(engine.store.last_event_seq)
    starts = [event for event in events if event.event_type == "decision_started"]
    assert len(starts) == 2
    assert all(event.data["decision_statement"] == "我先歇一会儿，再接着做事。" for event in starts)
    actions = [event for event in events if event.event_type == "action_requested"]
    assert len(actions) == 2
    assert all(len(event.data["request_sha256"]) == 64 for event in actions)
    assert actions[1].data["request"]["memories"] == [[{
        "type": "routine", "text": "歇了一会儿，精神好些了。", "importance": 0.3, "keywords": ["休息"]}]]
    observed = [event for event in events if event.event_type == "observation"]
    assert len(observed) == 2
