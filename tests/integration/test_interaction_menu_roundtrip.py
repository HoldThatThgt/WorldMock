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
from tests.integration.test_life_agent_protocol import _DecisionStatementResponse, _ModelResponse
from tests.life.conftest import (
    advance_next,
    advance_until_ready,
    context,
    engine_factory as _engine_factory,
    pending_names,
    stage_day_plan,
)
from worldmock.life.interaction_menu import WAYS
from worldmock.life import prompt_capacity


class _Dispatcher:
    """只替换提供商传输，捕获真实人物请求并返回指定的唯一世界请求（登记即结束本轮）。"""

    model_name = "deepseek-flash"

    def __init__(self, tool_name: str, arguments: dict) -> None:
        self.requests: list[dict] = []
        self.responses = [
            _DecisionStatementResponse("我照着眼前能做的事往前走。"),
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
    """让人物从已提交观察作一次真实模型动作（自述与世界请求各一次请求），保留发送的请求。"""
    dispatcher = _Dispatcher(tool_name, arguments)
    person = _person(environment, decision, dispatcher)
    before = environment.engine.state.model_dump(mode="json")
    request = ReactLoopRequest(0, decision.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation},
    ])
    await person.run_react_loop(request)
    assert environment.engine.state.model_dump(mode="json") == before
    assert len(dispatcher.requests) == 2
    return dispatcher.requests


def _action_schema(request: dict) -> dict:
    """读取实际发送给模型的 start_activity 工具 Schema。"""
    return next(item["function"] for item in request["tools"]
                if item["function"]["name"] == "start_activity")


@pytest.mark.asyncio
async def test_location_menu_changes_model_choices_and_preserves_result_evidence(
    _engine_factory, monkeypatch,
) -> None:
    """人物按可读地点行动，抵达后菜单收窄；到达的出行当秒收尾，结果经事件提交才进下一轮进度层。"""
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
    walk = {"targets": [office_binding["object"]], "actions": ["travel"], "ways": ["步行"]}
    home_requests = await _decide(environment, first, home, "start_activity", walk)
    assert travel["canonical_action"] in home["interaction_menu"]["allowed_actions"]
    assert set(_action_schema(home_requests[1])["parameters"]["properties"]) == {"targets", "actions", "ways"}
    assert home["facts"]["progress"]["results"] == []
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
    assert arrived["facts"]["progress"]["results"]
    [walked] = pending_names(arrived)
    conclusion = {"task": [walked], "summary": ["到公司了。"], "memories": [[]]}
    office_requests = await _decide(environment, second, arrived, "conclude_activity", conclusion)
    office_schema = _action_schema(office_requests[1])
    choices = office_schema["parameters"]["properties"]["ways"]["items"]["enum"]
    assert "工作" not in choices
    assert "等待" in choices
    listed = [item["action"] for row in arrived["facts"]["interactive_objects"] for item in row["actions"]]
    assert "interact：工作" not in listed and "interact：等待" in listed
    user = office_requests[1]["messages"][1]["content"]
    assert "progress" in user
    assert f"刚结束：{walked}。" in user
    assert "action_id" not in user
    assert engine.commit_decision_round(second.round_id)[0].status.value == "completed"

    events = engine.store.read_events(engine.store.last_event_seq)
    requests = [event for event in events if event.event_type == "action_requested"]
    assert len(requests) == 2
    assert requests[0].data["request"] == walk
    assert requests[1].data["request"] == conclusion
