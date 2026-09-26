"""交互菜单按私有绑定映射后的内部动作剪裁可读对象动作，工具仍由各自业务条件开放。"""

from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from custom.agents.life_menu import menu_action_error, parse_interaction_menu
from tests.integration.test_life_agent_protocol import (
    _Environment, _GroundedCorrectionDispatcher, _ModelResponse, _SELF_OBJECT, _WAIT_CHOICE, _add_objects,
    _context, _observations, _offer, _visible_object, _wire_agent,
)
from worldmock.life.interaction_menu import (
    WAYS,
    InteractionMenu,
    menu_allows_action,
    resolve_interaction_menu,
    resolve_selection,
)
from worldmock.life.models import REQUEST_MODELS

#: 条件已满足（没有说明）的工作对象：菜单剪裁与条件说明无关。
_TASK = _visible_object("整理会议纪要", "work_task", "task-visible", _offer("interact", "工作", "work"))
_WORK_CHOICE = {"targets": ["整理会议纪要"], "actions": ["interact"], "ways": ["工作"]}


def _observed_menu(allowed_actions: tuple[str, ...]) -> list[dict]:
    """以真实人物观察结构只替换菜单和可读交互对象。"""
    observations = copy.deepcopy(_observations())
    data = observations[0]["data"]
    data["interaction_menu"] = {"allowed_actions": sorted(allowed_actions)}
    data["facts"]["interactive_objects"] = []
    data["interaction_bindings"] = []
    _add_objects(data, _SELF_OBJECT, _TASK)
    return observations


def _prepared_agent(allowed_actions: tuple[str, ...]) -> LifeAgent:
    """准备真实 LifeAgent 的首轮工具和冻结对象。"""
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(
        0, _context().observed_at, _observed_menu(allowed_actions)))
    return agent


def _functions(agent: LifeAgent, readonly: bool = False) -> dict[str, dict]:
    """从实际人物工具 Schema 读取最终开放工具。"""
    return {row["function"]["name"]: row["function"]
            for row in agent.build_react_tool_schemas(readonly=readonly)}


def test_menu_declaration_has_only_allowed_actions_and_protected_self_actions() -> None:
    """菜单已无工具与活动种类字段，等待和睡觉始终保留。"""
    menu = InteractionMenu(allowed_actions=("sleep", "wait"))
    assert set(menu.model_dump()) == {"allowed_actions"}
    for raw in (
        {"allowed_actions": ["wait"]},
        {"allowed_actions": ["sleep", "wait", "unknown"]},
        {"allowed_actions": ["sleep", "wait", "wait"]},
        {"allowed_actions": ["sleep", "wait"], "allowed_tools": []},
        {"allowed_actions": ["sleep", "wait"], "allowed_activity_kinds": []},
    ):
        with pytest.raises(ValidationError):
            InteractionMenu.model_validate(raw)


def test_effective_menu_intersects_location_and_person_actions() -> None:
    """人物与地点声明求交集并确定性排序，仍保留自身动作。"""
    scenario = SimpleNamespace(
        agents=(SimpleNamespace(agent_id=1, interaction_menu=InteractionMenu(
            allowed_actions=("work", "sleep", "wait"))),),
        locations=(SimpleNamespace(location_id="home", kind="home"),),
        interaction_menus_by_location_kind={"home": InteractionMenu(
            allowed_actions=("travel_to", "wait", "sleep"))},
    )
    effective = resolve_interaction_menu(scenario, 1, "home")
    assert effective.allowed_actions == ("sleep", "wait")


def test_actual_tool_schema_limits_action_phrases_without_removing_other_tools() -> None:
    """地点菜单只剪裁 start_activity 的对象、动词与方式枚举（没有菜单内动作的对象不进枚举），工作提交等工具保持条件可见。"""
    agent = _prepared_agent(("sleep", "wait"))
    functions = _functions(agent)
    assert {"start_activity", "submit_work", "recall_task_summary", "plan_day"} <= set(functions)
    properties = functions["start_activity"]["parameters"]["properties"]
    assert set(properties) == {"targets", "actions", "ways"}
    assert properties["targets"]["items"]["enum"] == ["自己"]
    assert properties["actions"]["items"]["enum"] == ["interact", "sleep"]
    ways = properties["ways"]["items"]["enum"]
    assert set(ways) == {"等待", "睡觉"}
    assert all(WAYS[way][1] in {"sleep", "wait"} for way in ways)
    opened = _functions(_prepared_agent(("sleep", "wait", "work")))["start_activity"]["parameters"]["properties"]
    assert opened["targets"]["items"]["enum"] == ["整理会议纪要", "自己"]
    assert set(opened["ways"]["items"]["enum"]) == {"等待", "睡觉", "工作"}


@pytest.mark.asyncio
async def test_denied_readable_action_fails_local_and_direct_dispatch() -> None:
    """可见工作对象仍不能绕过地点菜单调用被剪裁的工作动作（映射后的内部动作不在菜单内）。"""
    agent = _prepared_agent(("sleep", "wait"))
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    decision = ReactDecision("", "start_activity", _WORK_CHOICE, "")
    assert "interaction menu" in agent.validate_react_decisions([decision], False)
    two = {"targets": ["自己", "整理会议纪要"], "actions": ["interact", "interact"], "ways": ["等待", "工作"]}
    assert "interaction menu" in agent.validate_react_decisions([ReactDecision("", "start_activity", two, "")],
                                                                False)
    with pytest.raises(RuntimeError, match="interaction menu"):
        await agent.dispatch_react_tool("start_activity", _WORK_CHOICE)
    assert environment.calls == []


def test_menu_checks_only_start_activity_and_requires_human_action() -> None:
    """菜单按私有绑定映射后的内部动作逐项判定启动（整组都要在菜单内），不按短语反解；正文工具由自身业务条件校验。"""
    menu = InteractionMenu(allowed_actions=("sleep", "wait"))
    bindings = _observed_menu(("sleep", "wait"))[0]["data"]["interaction_bindings"]
    [waiting] = resolve_selection(bindings, ["自己"], ["interact"], ["等待"])
    [working] = resolve_selection(bindings, ["整理会议纪要"], ["interact"], ["工作"])
    assert waiting["selected_action"]["canonical_action"] == "wait"
    assert working["selected_action"]["canonical_action"] == "work"
    assert resolve_selection(bindings, ["整理会议纪要"], ["interact"], ["等待"]) is None
    assert menu_allows_action(menu, "start_activity", ["wait"])
    assert menu_allows_action(menu, "start_activity", ["wait", "sleep"])
    assert not menu_allows_action(menu, "start_activity", ["work"])
    assert not menu_allows_action(menu, "start_activity", ["wait", "work"])
    assert not menu_allows_action(menu, "start_activity", [])
    assert menu_action_error(menu, "start_activity", ["work"]) == "interaction menu denies start_activity"
    assert menu_action_error(menu, "start_activity", ["sleep"]) == ""
    assert menu_allows_action(menu, "send_message", [])
    assert menu_action_error(menu, "send_message", []) == ""


@pytest.mark.parametrize("broken", [
    None,
    {"allowed_actions": ["wait"]},
    {"allowed_actions": ["sleep", "wait", "unknown"]},
    {"allowed_actions": ["sleep", "wait"], "allowed_tools": []},
    {"allowed_actions": ["wait", "sleep"]},
])
def test_broken_observation_menu_fails_closed_and_logs(broken, caplog) -> None:
    """缺失、旧字段、未知动作或乱序菜单均在真实人物准备阶段拒绝。"""
    observations = _observed_menu(("sleep", "wait"))
    observations[0]["data"]["interaction_menu"] = broken
    with pytest.raises(RuntimeError, match="interaction menu"):
        LifeAgent().prepare_react_loop(ReactLoopRequest(
            0, _context().observed_at, observations))
    assert "interaction menu" in caplog.text


@pytest.mark.asyncio
async def test_readonly_and_post_action_phases_do_not_reopen_business_tools() -> None:
    """只读问答不开放业务工具；生活决策登记一个世界请求即结束，没有重开业务工具的动作后阶段，与地点菜单无关。"""
    readonly = LifeAgent()
    readonly.prepare_react_loop(ReactLoopRequest(
        0, _context().observed_at, [], "What happened?", True))
    assert set(_functions(readonly, readonly=True)).isdisjoint(REQUEST_MODELS)
    wait = _WAIT_CHOICE
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-action", "start_activity", json.dumps(wait, ensure_ascii=False))])
    agent, environment = LifeAgent(), _Environment()
    _wire_agent(agent, environment, dispatcher, 4, memory=False)
    result = await agent.run_react_loop(ReactLoopRequest(
        0, _context().observed_at, _observed_menu(("sleep", "wait"))))
    assert result == "start_activity registered"
    assert len(dispatcher.requests) == 2
    assert len(environment.calls) == 1
    assert "already submitted" in agent.validate_react_decisions([
        ReactDecision("", "start_activity", wait, "")], False)


def test_menu_parser_rejects_unsorted_observation() -> None:
    """有效动作菜单必须按确定性顺序冻结。"""
    with pytest.raises(RuntimeError, match="actions must be sorted"):
        parse_interaction_menu({"interaction_menu": {"allowed_actions": ["wait", "sleep"]}})
