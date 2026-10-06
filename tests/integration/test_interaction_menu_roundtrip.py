"""真实人物、LifeEnv 与事件存储上的可读菜单往返。"""

from __future__ import annotations

from worldmock.life.tool_groups import compact_render, expand_tool_layer

import copy
import json
import os
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.integration.test_life_agent_protocol import _ModelResponse
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (advance_next, advance_until_ready, context, pending_names, report_request, stage_day_plan, start_arguments)  # noqa: F401
from worldmock.life.vocabulary import WAYS
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
    before = environment.engine.context.state.model_dump(mode="json")
    request = ReactLoopRequest(0, decision.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation},
    ])
    await person.run_react_loop(request)
    assert environment.engine.context.state.model_dump(mode="json") == before
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
    menus = dict(engine.context.scenario.interaction_menus_by_location_kind)
    office = menus["office"]
    menus["office"] = office.model_copy(update={"allowed_actions": tuple(
        action for action in office.allowed_actions if action != "work")})
    scenario = engine.context.scenario.model_copy(update={"interaction_menus_by_location_kind": menus})
    engine.context.semantics = replace(engine.context.semantics, scenario=scenario)
    engine.context.rules.scenario = scenario
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
        {"items", "answering", "prefer"})
    assert [event for event in home["progress_layer"]["events"] if event["type"] == "tool_call"] == []
    started = engine.commit_decision_round(first.round_id)[0]
    assert started.status.value == "in_progress"
    continuations = []
    for _ in range(16):
        if engine.context.state.agents[first.agent_id].location_id == "office":
            break
        if engine.scheduling_state().ready_agent_ids:
            continuing = context(engine, f"menu-walk-report-{_}")
            engine.begin_decision_round([continuing])
            walking = await environment.observe(continuing)
            names = [item["name"] for item in walking["tool_layer"]["targets"] if item["kind"] == "activity"]
            report = report_request(*names, summary="继续走。").model_dump(mode="json")
            await _decide(environment, continuing, walking, "report_status", report)
            continuations.append(report)
            assert engine.commit_decision_round(continuing.round_id)[0].status.value == "completed"
        else:
            advance_next(engine)
    assert engine.context.state.agents[first.agent_id].location_id == "office"
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
    assert "工作" not in _ways(arrived) and "等待" in _ways(arrived)
    assert arrived["environment_layer"]["location"]["fixed_rules"]
    conclusion = {"items": [{"activity_name": walked, "summary": "到公司了。", "memories": [], "views": []}],
                  "errands": []}
    office_requests = await _decide(environment, second, arrived, "conclude_activity", conclusion)
    office_schema = _action_schema(office_requests[0])
    assert office_schema == _action_schema(home_requests[0])
    assert "enum" not in office_schema["parameters"]["properties"]["items"]["items"]["properties"]["way"]
    layers = json.loads(office_requests[0]["messages"][1]["content"])
    assert compact_render(expand_tool_layer(layers[0])) == compact_render(arrived["tool_layer"])
    assert layers[4] == compact_render(arrived["environment_layer"])
    assert "action_id" not in office_requests[0]["messages"][1]["content"]
    assert engine.commit_decision_round(second.round_id)[0].status.value == "completed"

    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    requests = [event for event in events if event.event_type == "action_requested"]
    assert len(requests) == 2 + len(continuations)
    assert all(event.data["tool_name"] == "report_status" for event in requests[1:-1])
    assert requests[0].data["request"] == walk
    assert requests[-1].data["request"] == conclusion
    assert [event.data["request"] for event in requests] == [walk, *continuations, conclusion]
    assert [item.data["self_note"] for item in requests] == [None] * len(requests)
