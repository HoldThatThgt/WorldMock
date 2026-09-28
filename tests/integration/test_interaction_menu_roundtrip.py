"""真实人物、LifeEnv 与事件存储上的可读菜单往返。"""

from __future__ import annotations

import copy
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.integration.test_life_agent_protocol import _ModelResponse
from tests.life.conftest import (
    advance_next,
    advance_until_ready,
    context,
    engine_factory as _engine_factory,
    pending_names,
    stage_day_plan,
    start_arguments,
)
from worldmock.life.interaction_menu import WAYS
from worldmock.life import prompt_capacity


class _Dispatcher:
    """只替换提供商传输，捕获真实人物请求并返回指定的唯一世界请求（登记即结束本轮）。"""

    model_name = "deepseek-flash"

    def __init__(self, tool_name: str, arguments: dict) -> None:
        self.requests: list[dict] = []
        self.responses = [
            _ModelResponse("provider-action", tool_name, json.dumps(arguments, ensure_ascii=False)),
        ]

    async def call(self, **request):
        """保存最终请求，并返回下一条真实协议形状的响应。"""
        self.requests.append(copy.deepcopy(request))
        return self.responses.pop(0)


def _person(environment: LifeEnv, decision, dispatcher: _Dispatcher) -> LifeAgent:
    """把人物绑定到真实宿主决策身份和环境。"""
    person = LifeAgent()
    person._id = decision.agent_id
    person._name = "测试人物"
    person._profile = {"id": decision.agent_id, "name": "测试人物"}
    person._env = environment
    person._dispatcher = dispatcher
    person._model_name = dispatcher.model_name
    person._decision_context = decision
    person._max_react_turns = 3
    person._enable_memory = False
    person.trace_span = lambda *args, **kwargs: nullcontext(SimpleNamespace(attributes={}))
    return person


async def _decide(environment: LifeEnv, decision, observation: dict,
                  tool_name: str, arguments: dict) -> list[dict]:
    """让人物从已提交观察作一次真实模型动作（没有记忆查询时恰好一次请求），保留发送的请求。"""
    dispatcher = _Dispatcher(tool_name, arguments)
    person = _person(environment, decision, dispatcher)
    before = environment.engine.state.model_dump(mode="json")
    request = ReactLoopRequest(0, decision.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation},
    ])
    await person.run_react_loop(request)
    assert environment.engine.state.model_dump(mode="json") == before
    assert len(dispatcher.requests) == 1
    return dispatcher.requests


def _action_schema(request: dict) -> dict:
    """读取实际发送给模型的 start_activity 工具 Schema。"""
    return next(item["function"] for item in request["tools"]
                if item["function"]["name"] == "start_activity")


def _ways(observation: dict) -> list[str]:
    """工具层各目标列出的全部方式。"""
    return [item["way"] for target in observation["tool_layer"]["targets"] for item in target["actions"]]


@pytest.mark.asyncio
async def test_location_menu_changes_model_choices_and_preserves_result_evidence(
    _engine_factory, monkeypatch,
) -> None:
    """人物按可读地点行动，抵达后工具层按菜单收窄；到达的出行当秒成为刚结束的活动目标，请求与结果经事件提交。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_000_000)
    engine = _engine_factory()
    stage_day_plan(engine)
    menus = dict(engine.scenario.interaction_menus_by_location_kind)
    office = menus["office"]
    menus["office"] = office.model_copy(update={"allowed_actions": tuple(
        action for action in office.allowed_actions if action != "work")})
    engine.scenario = engine.scenario.model_copy(update={"interaction_menus_by_location_kind": menus})
    environment = LifeEnv()
    environment.engine = engine

    first = context(engine, "menu-home")
    engine.begin_decision_round([first])
    home = await environment.observe(first)
    office_binding = next(row for row in home["interaction_bindings"]
                          if row["kind"] == "location" and row["object_id"] == "office")
    travel = next(item for item in office_binding["actions"] if item.get("travel_mode") == "walking")
    assert (travel["verb"], travel["label"], travel["canonical_action"]) == ("travel", "步行", "travel_to")
    assert WAYS[travel["label"]] == ("travel", travel["canonical_action"])
    walk = start_arguments([office_binding["object"]], ["travel"], ["步行"])
    home_requests = await _decide(environment, first, home, "start_activity", walk)
    assert travel["canonical_action"] in home["interaction_menu"]["allowed_actions"]
    assert set(_action_schema(home_requests[0])["parameters"]["properties"]) == (
        {"targets", "actions", "ways", "from", "to", "answering"})
    assert [event for event in home["progress_layer"]["events"] if event["type"] == "tool_call"] == []
    started = engine.commit_decision_round(first.round_id)[0]
    assert started.status.value == "in_progress"
    for _ in range(16):
        if engine.state.agents[first.agent_id].location_id == "office":
            break
        advance_next(engine)
    assert engine.state.agents[first.agent_id].location_id == "office"
    assert advance_until_ready(engine)

    second = context(engine, "menu-office")
    engine.begin_decision_round([second])
    arrived = await environment.observe(second)
    assert "work" in home["interaction_menu"]["allowed_actions"]
    assert "work" not in arrived["interaction_menu"]["allowed_actions"]
    [walked] = pending_names(arrived)
    ended = next(target for target in arrived["tool_layer"]["targets"] if target["name"] == walked)
    assert (ended["kind"], ended["phase"]) == ("activity", "刚结束")
    assert "conclude_activity" in [item["tool"] for item in ended["tools"]]
    assert "工作" in _ways(home) and "工作" not in _ways(arrived) and "等待" in _ways(arrived)
    assert arrived["environment_layer"]["fixed_rules"]
    conclusion = {"task": [walked], "summary": ["到公司了。"], "memories": [[]]}
    office_requests = await _decide(environment, second, arrived, "conclude_activity", conclusion)
    office_schema = _action_schema(office_requests[0])
    assert office_schema == _action_schema(home_requests[0])
    assert "enum" not in office_schema["parameters"]["properties"]["ways"]["items"]
    layers = json.loads(office_requests[0]["messages"][1]["content"])
    assert layers[0] == arrived["tool_layer"] and layers[4] == arrived["environment_layer"]
    assert "action_id" not in office_requests[0]["messages"][1]["content"]
    assert engine.commit_decision_round(second.round_id)[0].status.value == "completed"

    events = engine.store.read_events(engine.store.last_event_seq)
    requests = [event for event in events if event.event_type == "action_requested"]
    assert len(requests) == 2
    assert requests[0].data["request"] == walk
    assert requests[1].data["request"] == conclusion
    assert [item.data["self_note"] for item in requests] == [None, None]
