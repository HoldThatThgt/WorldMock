"""交互菜单只剪裁可读对象动作，工具仍由各自业务条件开放。"""

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
    _Environment, _GroundedCorrectionDispatcher, _ModelResponse, _context, _observations, _wire_agent,
)
from worldmock.life.interaction_menu import (
    InteractionMenu,
    canonical_action,
    menu_allows_action,
    resolve_interaction_menu,
)
from worldmock.life.models import REQUEST_MODELS, SendMessageActionRequest, StartActivityRequest


def _observed_menu(allowed_actions: tuple[str, ...]) -> list[dict]:
    """以真实人物观察结构只替换菜单和可读交互对象。"""
    observations = copy.deepcopy(_observations())
    data = observations[0]["data"]
    data["interaction_menu"] = {"allowed_actions": sorted(allowed_actions)}
    data["facts"]["interactive_objects"] = [
        {"object": "自己", "actions": ["等待", "睡觉"], "preconditions_met": True},
        {"object": "整理会议纪要", "actions": ["工作"], "preconditions_met": True},
    ]
    data["interaction_bindings"] = [
        {"object": "自己", "kind": "self", "object_id": "self", "actions": [
            {"label": "等待", "canonical_action": "wait"},
            {"label": "睡觉", "canonical_action": "sleep"}]},
        {"object": "整理会议纪要", "kind": "work_task", "object_id": "task-visible",
         "actions": [{"label": "工作", "canonical_action": "work"}]},
    ]
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
    """地点菜单只剪裁 start_activity 动作枚举，工作提交与修订当日目标等工具保持条件可见；
    plan_day 是零点后台任务，不在人物工具面（时间与能量规格第四章）。"""
    observations = _observed_menu(("sleep", "wait"))
    observations[0]["data"]["facts"]["day_plan"] = {"goals": []}
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observations))
    functions = _functions(agent)
    assert {"start_activity", "submit_work", "recall_task_summary", "revise_day_plan"} <= set(functions)
    assert "plan_day" not in functions
    assert set(functions["start_activity"]["parameters"]["properties"]) == {"targets", "actions"}
    choices = functions["start_activity"]["parameters"]["properties"]["actions"]["items"]["enum"]
    assert set(choices) == {"等待", "睡觉"}
    assert all(canonical_action(label) in {"sleep", "wait"} for label in choices)


@pytest.mark.asyncio
async def test_denied_readable_action_fails_local_and_direct_dispatch() -> None:
    """可见工作对象仍不能绕过地点菜单调用被剪裁的工作动作。"""
    agent = _prepared_agent(("sleep", "wait"))
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = {"targets": ["整理会议纪要"], "actions": ["工作"]}
    decision = ReactDecision("", "start_activity", payload, "")
    assert "interaction menu" in agent.validate_react_decisions([decision], False)
    with pytest.raises(RuntimeError, match="interaction menu"):
        await agent.dispatch_react_tool("start_activity", payload)
    assert environment.calls == []


def test_menu_checks_only_start_activity_and_requires_human_action() -> None:
    """菜单按动作短语判断启动；正文工具由自身业务条件校验。"""
    menu = InteractionMenu(allowed_actions=("sleep", "wait"))
    assert menu_allows_action(menu, "start_activity",
                              StartActivityRequest(targets=["自己"], actions=["等待"]))
    assert not menu_allows_action(menu, "start_activity",
                                  StartActivityRequest(targets=["整理会议纪要"], actions=["工作"]))
    message = SendMessageActionRequest(recipient_name="小王", text="今晚见")
    assert menu_action_error(menu, "send_message", message) == ""


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
    wait = {"targets": ["自己"], "actions": ["等待"]}
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
