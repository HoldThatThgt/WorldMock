"""LifeAgent 交互菜单的实际模型请求、校验、派发与阶段门测试。"""

from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from custom.agents.life_menu import basic_activity_types, menu_action_error
from tests.integration.activity_fixtures import paused_activity
from tests.integration.test_grounded_targets import (
    _Environment,
    _active,
    _catalogs,
    _context,
    _functions,
    _observations,
    _prepare,
)
from worldmock.life.interaction_menu import (
    InteractionMenu,
    PROTECTED_ACTIVITY_KINDS,
    PROTECTED_INTERACTION_TOOLS,
)
from worldmock.life.models import REQUEST_MODELS, SetActivityStateRequest


def _menu(*, tools=(), kinds=()) -> dict:
    """构造满足保护项合同的排序有效菜单。"""
    return {
        "allowed_tools": sorted(PROTECTED_INTERACTION_TOOLS | set(tools)),
        "allowed_activity_kinds": sorted(PROTECTED_ACTIVITY_KINDS | set(kinds)),
    }


def _full_menu() -> dict:
    """构造当前请求模型与注册表活动类型的完整菜单。"""
    data = _observations(_catalogs())[0]["data"]
    return copy.deepcopy(data["interaction_menu"])


def _lifestyle_result() -> dict:
    """构造一个真实下钻返回的生活活动身份。"""
    data = {
        "tool": "browse_activities",
        "category": "sport",
        "items": [{
            "activity_id": "walk",
            "activity_type": "lifestyle:walk",
            "name": "Walk",
            "category": "sport",
        }],
    }
    return {
        "event_id": "event-lifestyle",
        "action_id": "action-lifestyle",
        "decision_id": "decision",
        "occurred_at": 0,
        "status": "completed",
        "error_code": None,
        "message": "Activity menu browsed",
        "result": {
            "status": "completed",
            "activity_id": None,
            "error_code": None,
            "message": "Activity menu browsed",
            "data": data,
        },
    }


def _work_menu_agent() -> LifeAgent:
    """准备只允许定向工作及工作品类下钻的人物。"""
    menu = _menu(
        tools={"start_targeted_activity", "browse_activities"},
        kinds={"work"},
    )
    return _prepare(_catalogs(), overrides={
        "interaction_menu": menu,
        "activity_summary": {
            "feature_film": 1,
            "foods": 1,
            "game_session": 1,
            "sport": 1,
            "work_contract": 1,
        },
    })


def test_actual_model_request_projects_tools_kinds_targets_and_categories() -> None:
    """实际发送请求只含菜单与原状态门共同允许的工具和参数枚举。"""
    agent = _work_menu_agent()
    tools = agent.build_react_tool_schemas(readonly=False)
    agent._model_name = "deepseek-v4.1-flash"
    request = agent._model_request([{"role": "user", "content": "Choose."}], tools)
    functions = {item["function"]["name"]: item["function"] for item in request["tools"]}

    assert set(functions) & set(REQUEST_MODELS) == {
        "browse_activities",
        "plan_day",
        "start_basic_activity",
        "start_targeted_activity",
    }
    basic = functions["start_basic_activity"]["parameters"]["properties"]
    targeted = functions["start_targeted_activity"]["parameters"]["properties"]
    browse = functions["browse_activities"]["parameters"]["properties"]
    assert basic["activity_type"]["enum"] == ["wait", "sleep"]
    assert targeted["activity_type"]["enum"] == ["work"]
    assert targeted["target_id"]["enum"] == ["task-a", "task-b"]
    assert browse["category"]["enum"] == ["work_contract"]


@pytest.mark.asyncio
async def test_basic_activity_menu_blocks_local_and_direct_dispatch(caplog) -> None:
    """已发现生活活动在菜单禁用 lifestyle 时不进 Schema，且两个宿主边界都拒绝。"""
    menu = _menu(tools={"browse_activities"})
    agent = _prepare(
        _catalogs(),
        overrides={"interaction_menu": menu},
        browse_results=[_lifestyle_result()],
    )
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = {"activity_type": "lifestyle:walk", "planned_duration_seconds": 60}
    decision = ReactDecision("", "start_basic_activity", payload, "")

    basic = _functions(agent)["start_basic_activity"]["parameters"]["properties"]
    assert basic["activity_type"]["enum"] == ["wait", "sleep"]
    assert "interaction menu" in agent.validate_react_decisions([decision], False)
    with pytest.raises(RuntimeError, match="interaction menu"):
        await agent.dispatch_react_tool("start_basic_activity", payload)
    assert environment.calls == []
    assert "interaction menu" in caplog.text


def test_allowed_discovered_lifestyle_identity_is_bound_to_basic_schema() -> None:
    """允许 lifestyle 时仍只能选择本轮下钻结果中的精确活动身份。"""
    agent = _prepare(
        _catalogs(),
        overrides={"interaction_menu": _menu(kinds={"lifestyle"})},
        browse_results=[_lifestyle_result()],
    )
    properties = _functions(agent)["start_basic_activity"]["parameters"]["properties"]
    assert properties["activity_type"]["enum"] == ["wait", "sleep", "lifestyle:walk"]
    invented = ReactDecision("", "start_basic_activity", {
        "activity_type": "lifestyle:invented",
        "planned_duration_seconds": 60,
    }, "")
    assert "not visible" in agent.validate_react_decisions([invented], False)


@pytest.mark.asyncio
async def test_targeted_kind_cannot_bypass_menu_through_shared_tool() -> None:
    """共享定向工具开放工作时，游戏类型仍在 Schema、本地与直接派发处被拒绝。"""
    agent = _work_menu_agent()
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = {
        "activity_type": "play_game",
        "planned_duration_seconds": 60,
        "target_id": "game-visible",
        "playback_speed": 1,
    }
    decision = ReactDecision("", "start_targeted_activity", payload, "")

    assert agent.validate_react_decisions([decision], False)
    with pytest.raises(RuntimeError):
        await agent.dispatch_react_tool("start_targeted_activity", payload)
    assert environment.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,kind,payload", [
    ("start_travel", "travel", {
        "destination_id": "office", "travel_mode": "driving", "planned_duration_seconds": 216}),
    ("consume", "consume", {
        "items": [{"product_id": "product-visible", "quantity": 1}],
        "use_mode": "eat", "duration_seconds": 300}),
])
async def test_kind_denial_removes_permitted_travel_or_consume_tool(tool, kind, payload) -> None:
    """工具名和领域条件均允许时，活动类型单独禁止也必须撤下实际工具并阻断宿主派发。"""
    decision = ReactDecision("", tool, payload, "")
    unrestricted = _prepare(_catalogs())
    assert tool in _functions(unrestricted)
    assert unrestricted.validate_react_decisions([decision], False) == ""
    menu = _full_menu()
    menu["allowed_activity_kinds"].remove(kind)
    assert tool in menu["allowed_tools"]
    agent = _prepare(_catalogs(), overrides={"interaction_menu": menu})
    agent._model_name = "deepseek-v4.1-flash"
    request = agent._model_request(
        [{"role": "user", "content": "Choose."}], agent.build_react_tool_schemas(readonly=False))
    assert tool not in {item["function"]["name"] for item in request["tools"]}
    assert agent.validate_react_decisions([decision], False)
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    with pytest.raises(RuntimeError, match="unavailable"):
        await agent.dispatch_react_tool(tool, payload)
    assert environment.calls == []


@pytest.mark.parametrize("broken", [
    None,
    {"allowed_tools": sorted(PROTECTED_INTERACTION_TOOLS),
     "allowed_activity_kinds": ["sleep", "wait"], "extra": []},
    {"allowed_tools": sorted((PROTECTED_INTERACTION_TOOLS - {"plan_day"})),
     "allowed_activity_kinds": ["sleep", "wait"]},
    {"allowed_tools": sorted(PROTECTED_INTERACTION_TOOLS) + ["unknown_tool"],
     "allowed_activity_kinds": ["sleep", "wait"]},
    {"allowed_tools": sorted(PROTECTED_INTERACTION_TOOLS) + ["plan_day"],
     "allowed_activity_kinds": ["sleep", "wait"]},
])
def test_broken_observation_menu_fails_closed_and_logs(broken, caplog) -> None:
    """缺失、未知、重复、额外字段或缺保护项的观察菜单不回退到全量工具。"""
    with pytest.raises(RuntimeError, match="interaction menu"):
        _prepare(_catalogs(), overrides={"interaction_menu": broken})
    assert "interaction menu" in caplog.text


def test_unsorted_observation_menu_fails_closed() -> None:
    """环境有效菜单必须保持确定性排序，宿主拒绝乱序证据。"""
    menu = _full_menu()
    menu["allowed_tools"].reverse()
    with pytest.raises(RuntimeError, match="tools must be sorted"):
        _prepare(_catalogs(), overrides={"interaction_menu": menu})


def test_unsorted_observation_activity_kinds_fail_closed() -> None:
    """活动类型交集也必须保持确定性排序。"""
    menu = _full_menu()
    menu["allowed_activity_kinds"].reverse()
    with pytest.raises(RuntimeError, match="activity kinds must be sorted"):
        _prepare(_catalogs(), overrides={"interaction_menu": menu})


def test_unknown_resume_identity_is_left_to_existing_domain_validation() -> None:
    """菜单没有活动归属证据时不取代既有 NOT_FOUND 或 NOT_OWNED 判定。"""
    menu = InteractionMenu.model_validate(_full_menu())
    request = SetActivityStateRequest(activity_id="missing", operation="resume")
    controls = {"paused_activity_kinds": {}}
    assert menu_action_error(menu, "set_activity_state", request, controls, ()) == ""


@pytest.mark.parametrize("kind", [None, "", 42])
def test_broken_paused_kind_fails_closed_and_logs(kind, caplog) -> None:
    """恢复权限依赖的活动类型缺失或损坏时，人物记录英文错误并终止准备。"""
    paused = [{
        "activity_id": "activity-watch", "status": "paused", "kind": kind,
        "remaining_duration_seconds": 600, "minimum_energy_cost_kcal": 2.0,
        "resume_energy_feasible": True,
    }]
    with pytest.raises(RuntimeError, match="paused activity kind is invalid"):
        _prepare(_catalogs(), paused_activities=paused)
    assert "LifeAgent paused activity kind is invalid" in caplog.text


def test_basic_activity_projection_rejects_broken_results_and_identity(caplog) -> None:
    """生活活动下钻结果的容器与活动身份损坏时记录英文日志并失败关闭。"""
    menu = InteractionMenu.model_validate(_menu(kinds={"lifestyle"}))
    with pytest.raises(RuntimeError, match="recent action results are invalid"):
        basic_activity_types(menu, {"recent_action_results": "broken"})

    result = _lifestyle_result()
    result["result"]["data"]["items"][0]["activity_type"] = "work"
    with pytest.raises(RuntimeError, match="lifestyle activity type is invalid"):
        _prepare(
            _catalogs(),
            overrides={"interaction_menu": _menu(kinds={"lifestyle"})},
            browse_results=[result],
        )
    assert "LifeAgent browsed lifestyle activity type is invalid" in caplog.text


def test_readonly_and_action_phases_do_not_reopen_menu_actions() -> None:
    """只读不读取世界菜单，日首和动作后阶段继续优先于菜单。"""
    readonly = LifeAgent()
    readonly.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at, [], "What happened?", True))
    assert set(_functions(readonly, readonly=True)).isdisjoint(REQUEST_MODELS)

    day_start = _prepare(_catalogs(), overrides={
        "interaction_menu": _full_menu(),
        "wake_reasons": ["run_start"],
    })
    assert set(_functions(day_start)) & set(REQUEST_MODELS) == {"plan_day"}
    day_start._life_action_count = 1
    assert set(_functions(day_start)) & set(REQUEST_MODELS) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,restriction,start_tool", [
    ("watch", "activity_kind", "start_targeted_activity"),
    ("watch", "start_tool", "start_targeted_activity"),
])
async def test_paused_resume_obeys_kind_menu_while_cancel_stays_available(
        kind, restriction, start_tool) -> None:
    """活动类型或对应启动工具单独禁止时撤下恢复，取消仍可用。"""
    menu = _full_menu()
    if restriction == "activity_kind":
        menu["allowed_activity_kinds"].remove(kind)
    else:
        menu["allowed_tools"].remove(start_tool)
    paused = [paused_activity("activity-watch", kind=kind, remaining_duration_seconds=600)]
    unrestricted = _prepare(_catalogs(), paused_activities=paused)
    full_operation = _functions(unrestricted)["set_activity_state"]["parameters"]["properties"]["operation"]
    assert full_operation["enum"] == ["cancel", "resume"]
    agent = _prepare(_catalogs(), overrides={"interaction_menu": menu},
                     paused_activities=paused)
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    resume = {"activity_id": "activity-watch", "operation": "resume"}
    cancel = {"activity_id": "activity-watch", "operation": "cancel"}
    operation = _functions(agent)["set_activity_state"]["parameters"]["properties"]["operation"]

    assert operation["enum"] == ["cancel"]
    assert "not valid for the visible activity state" in agent.validate_react_decisions(
        [ReactDecision("", "set_activity_state", resume, "")], False)
    assert agent.validate_react_decisions(
        [ReactDecision("", "set_activity_state", cancel, "")], False) == ""
    with pytest.raises(RuntimeError, match="not valid for the visible activity state"):
        await agent.dispatch_react_tool("set_activity_state", resume)
    assert environment.calls == []
    assert agent._life_action_count == 0
    await agent.dispatch_react_tool("set_activity_state", cancel)
    assert len(environment.calls) == 1
    assert agent._life_action_count == 1


@pytest.mark.asyncio
async def test_mixed_paused_kinds_validate_the_requested_identity_before_dispatch() -> None:
    """共享恢复枚举存在时逐个核验暂停身份，不能借另一个允许活动的类型绕过。"""
    menu = _full_menu()
    menu["allowed_activity_kinds"].remove("play_game")
    paused = [paused_activity(identity, kind=kind, remaining_duration_seconds=600)
              for identity, kind in (("allowed-watch", "watch"), ("denied-game", "play_game"))]
    agent = _prepare(_catalogs(), overrides={"interaction_menu": menu}, paused_activities=paused)
    operations = _functions(agent)["set_activity_state"]["parameters"]["properties"]["operation"]["enum"]
    assert operations == ["cancel", "resume"]
    denied = {"activity_id": "denied-game", "operation": "resume"}
    allowed = {"activity_id": "allowed-watch", "operation": "resume"}
    assert "not valid for the visible activity state" in agent.validate_react_decisions(
        [ReactDecision("", "set_activity_state", denied, "")], False)
    assert agent.validate_react_decisions([ReactDecision("", "set_activity_state", allowed, "")], False) == ""
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    with pytest.raises(RuntimeError, match="not valid for the visible activity state"):
        await agent.dispatch_react_tool("set_activity_state", denied)
    assert environment.calls == []
    await agent.dispatch_react_tool("set_activity_state", allowed)
    assert len(environment.calls) == 1
    assert environment.calls[0].request.activity_id == "allowed-watch"


def test_existing_active_controls_survive_removed_start_kind() -> None:
    """地点变化后禁止 watch 仍保留既有观看活动的继续、暂停、取消和调速控制。"""
    menu = _full_menu()
    menu["allowed_activity_kinds"].remove("watch")
    agent = _prepare(
        _catalogs(),
        active_activity=_active("watch"),
        overrides={"interaction_menu": menu},
    )
    names = set(_functions(agent))
    assert {"continue_activity", "set_activity_state", "change_playback_speed"} <= names


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["activity_kind", "start_tool"])
async def test_current_game_command_survives_removal_of_new_game_permission(restriction) -> None:
    """撤掉新开游戏资格后，当前游戏仍保留合法命令、暂停和取消控制。"""
    menu = _full_menu()
    menu["allowed_activity_kinds" if restriction == "activity_kind" else "allowed_tools"].remove(
        "play_game" if restriction == "activity_kind" else "start_targeted_activity")
    games = {"session-visible": {
        "session_id": "session-visible", "admissible_commands": ["look", "inventory"],
        "won": False, "lost": False,
    }}
    agent = _prepare(_catalogs(), active_activity=_active("play_game", session_id="session-visible"),
                     overrides={"interaction_menu": menu, "games": games})
    functions = _functions(agent)
    assert {"game_action", "set_activity_state"} <= set(functions)
    assert functions["game_action"]["parameters"]["properties"]["command"]["enum"] == ["inventory", "look"]
    payload = {"game_session_id": "session-visible", "command": "look"}
    assert agent.validate_react_decisions([ReactDecision("", "game_action", payload, "")], False) == ""
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    await agent.dispatch_react_tool("game_action", payload)
    assert len(environment.calls) == 1
    assert environment.calls[0].request.command == "look"
