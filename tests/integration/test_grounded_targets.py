"""LifeAgent 当前观察目录与原生目标工具 Schema 的一致性测试。"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from jsonschema import Draft202012Validator

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DecisionContext, DirectToolCall
from custom.agents.life_agent import LifeAgent
from worldmock.life.models import ActionReceipt, SetActivityStateRequest, StartTargetedActivityRequest


_MEMORY_TOOLS = {
    "memory_recent",
    "memory_search",
    "memory_step_range",
    "memory_tick_range",
    "memory_time_range",
    "memory_read",
}

_FAILED_OBSERVATION_CATALOGS = {
    "tasks": [],
    "content_catalog": [{"item_id": "sintel-en-subtitles", "duration_seconds": 630}],
    "game_catalog": [{"game_id": f"textworld-{seed}"} for seed in range(1201, 1209)],
    "messages": [],
    "browse_targets": ["product_catalog"],
}

_TARGET_CATALOG_FIELDS_FOR_TEST = ("work", "watch", "play_game", "read_mail", "browse")


def _context() -> DecisionContext:
    """构造一个带时区的真实宿主决策上下文。"""
    return DecisionContext(
        "run", "round", "decision", 2, datetime(2026, 1, 5, 0, 3, 45, tzinfo=timezone.utc), 50
    )


def _catalogs() -> dict:
    """构造五类均有可见目标的环境目录。"""
    return {
        "tasks": [
            {"task_id": "task-b", "remaining_contract_seconds": 3600,
             "material": {"end_char": 10, "total_chars": 10}},
            {"task_id": "task-a", "remaining_contract_seconds": 3600,
             "material": {"end_char": 10, "total_chars": 10}},
        ],
        "content_catalog": [{"item_id": "content-visible", "duration_seconds": 630}],
        "game_catalog": [{"game_id": "game-visible"}],
        "messages": [{"message_id": "message-visible"}],
        "browse_targets": ["product_catalog"],
    }


def _energy_limits(**overrides: int) -> dict:
    """形成环境权威投影的五类活动墙钟上限。"""
    values = {kind: 86_400 for kind in ("work", "watch", "play_game", "read_mail", "browse")}
    values.update(overrides)
    return {"maximum_duration_seconds_by_kind": values}


def _observations(
    catalogs: dict,
    *,
    active_activity: dict | None = None,
    paused_activities: list[dict] | None = None,
    overrides: dict | None = None,
) -> list[dict]:
    """把目录包装为 PersonAgent 实际传给 ReAct 的首观察。"""
    data = {
        "observation_event_id": "observation-current",
        "recent_action_results": [],
        "active_activity": active_activity,
        "paused_activities": paused_activities or [],
        "energy_feasibility": _energy_limits(),
        "content_progress": {},
        "products": [{"product_id": "product-visible", "price_version": "price-v1",
                      "current_stock": 2, "use_modes": ["eat"], "use_duration_seconds": 300,
                      "merchant_location_id": "home", "required_location_id": None}],
        "locations": [{"location_id": "home"}, {"location_id": "office"}],
        "location_id": "home",
        "available_inventory": {"product-visible": 1.0},
        "routes": [{"destination_id": "office", "mode": "driving", "duration_seconds": 216,
                    "minimum_energy_cost_kcal": 6.0, "energy_feasible": True}],
        "games": {},
        **catalogs,
    }
    data.update(overrides or {})
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


def _prepare(catalogs: dict, **observation_options) -> LifeAgent:
    """按生产顺序准备一次非只读人物决策。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, _observations(catalogs, **observation_options))
    agent.prepare_react_loop(request)
    return agent


def _functions(agent: LifeAgent, *, readonly: bool = False) -> dict[str, dict]:
    """按函数名索引当前实际模型工具 Schema。"""
    return {
        item["function"]["name"]: item["function"]
        for item in agent.build_react_tool_schemas(readonly=readonly)
    }


def _targeted_parameters(agent: LifeAgent) -> dict:
    """取得当前目标活动工具的参数 Schema。"""
    return _functions(agent)["start_targeted_activity"]["parameters"]


class _Environment:
    """捕获已经通过本地目录校验的宿主绑定动作。"""

    def __init__(self) -> None:
        self.calls: list[DirectToolCall] = []

    async def submit_action(self, call: DirectToolCall) -> ActionReceipt:
        """返回与直接调用上下文一致的登记回执。"""
        self.calls.append(call)
        return ActionReceipt(
            action_id=call.action_id,
            round_id=call.context.round_id,
            decision_id=call.context.decision_id,
            request_sha256="a" * 64,
        )


def _active(kind: str, *, session_id: str | None = None) -> dict:
    """形成一个与反馈边界一致的当前活动投影。"""
    data = {"game_session_id": session_id} if session_id else {}
    return {"activity_id": f"activity-{kind}", "status": "active", "kind": kind, "data": data}


def test_prepare_binds_current_catalogs_to_native_strict_schema() -> None:
    """准备阶段把五个当前目录绑定为排序去重的原生枚举。"""
    parameters = _targeted_parameters(_prepare(_catalogs()))
    properties = parameters["properties"]

    assert properties["activity_type"]["enum"] == [
        "work", "watch", "play_game", "read_mail", "browse",
    ]
    assert properties["target_id"]["enum"] == [
        "content-visible", "game-visible", "message-visible", "product_catalog", "task-a", "task-b",
    ]
    assert "work uses observations[0].data.tasks[].task_id" in properties["activity_type"]["description"]
    assert "read_mail=messages[].message_id" in properties["target_id"]["description"]
    Draft202012Validator.check_schema(parameters)


def test_failed_run_observation_removes_unreleased_work_and_invented_target() -> None:
    """真实失败观察的空任务目录不再允许 work 或虚构 office 目标。"""
    agent = _prepare(_FAILED_OBSERVATION_CATALOGS)
    parameters = _targeted_parameters(agent)
    properties = parameters["properties"]
    payload = {
        "activity_type": "work",
        "planned_duration_seconds": 7200,
        "target_id": "office",
        "playback_speed": 1,
    }
    decision = ReactDecision("", "start_targeted_activity", payload, "")

    assert properties["activity_type"]["enum"] == ["watch", "play_game", "browse"]
    assert "office" not in properties["target_id"]["enum"]
    assert list(Draft202012Validator(parameters).iter_errors(payload))
    assert agent.validate_react_decisions([decision], False) == (
        "start_targeted_activity target_id is not visible for activity_type"
    )


def test_all_empty_catalogs_remove_only_targeted_start_tool() -> None:
    """全部公开身份为空时仅保留无需目标的基础活动。"""
    empty = {key: [] for key in _FAILED_OBSERVATION_CATALOGS}
    overrides = {"products": [], "locations": [], "available_inventory": {}, "routes": []}
    functions = _functions(_prepare(empty, overrides=overrides))

    assert "start_targeted_activity" not in functions
    assert set(functions) == _MEMORY_TOOLS | {"start_basic_activity"}


@pytest.mark.parametrize(
    ("kind", "expected", "overrides"),
    [
        ("wait", {"continue_activity", "set_activity_state"}, {}),
        ("watch", {"continue_activity", "set_activity_state", "change_playback_speed"}, {}),
        ("play_game", {"set_activity_state", "game_action"}, {
            "games": {"session-visible": {
                "session_id": "session-visible", "admissible_commands": ["look", "inventory"],
                "won": False, "lost": False,
            }},
        }),
    ],
)
def test_active_boundary_exposes_only_current_activity_controls(kind, expected, overrides) -> None:
    """当前反馈边界只提供该活动的严格控制，不提供冲突生活动作。"""
    session_id = "session-visible" if kind == "play_game" else None
    agent = _prepare(_catalogs(), active_activity=_active(kind, session_id=session_id), overrides=overrides)

    assert set(_functions(agent)) == _MEMORY_TOOLS | expected


def test_finished_activity_refresh_restores_starts_and_removes_old_controls() -> None:
    """活动完成后的下一轮观察撤下旧身份并恢复可用新动作。"""
    agent = _prepare(_catalogs(), active_activity=_active("wait"))
    assert "continue_activity" in _functions(agent)

    request = ReactLoopRequest(0, _context().observed_at, _observations(_catalogs()))
    agent.prepare_react_loop(request)
    functions = _functions(agent)

    assert "continue_activity" not in functions and "set_activity_state" not in functions
    assert "start_basic_activity" in functions and "start_targeted_activity" in functions


def test_watch_tail_and_work_limit_remove_only_impossible_starts() -> None:
    """不足最小推进量的媒体尾段和合同尾段不再成为启动目标。"""
    catalogs = _catalogs()
    catalogs["tasks"] = [{"task_id": "task-a", "remaining_contract_seconds": 59,
                          "material": {"end_char": 10, "total_chars": 10}}]
    progress = {"content-visible": {
        "item_id": "content-visible", "position_seconds": 621.0,
        "remaining_media_seconds": 9.0, "exposure_count": 1,
    }}
    agent = _prepare(catalogs, overrides={"content_progress": progress})
    functions = _functions(agent)
    kinds = functions["start_targeted_activity"]["parameters"]["properties"]["activity_type"]["enum"]

    assert "work" not in kinds and "watch" not in kinds
    assert "submit_work" in functions


def test_completed_content_remains_available_for_explicit_repeat() -> None:
    """完整看完的内容按领域重复规则从开头重新成为合法目标。"""
    progress = {"content-visible": {
        "item_id": "content-visible", "position_seconds": 630.0,
        "remaining_media_seconds": 0.0, "exposure_count": 1,
    }}
    parameters = _targeted_parameters(_prepare(
        _catalogs(), overrides={"content_progress": progress}))

    assert "watch" in parameters["properties"]["activity_type"]["enum"]


def test_business_tools_bind_visible_pairs_and_refresh_when_identities_disappear() -> None:
    """瞬时动作与出行只接受本轮公开配对，并在下一轮按状态撤下。"""
    agent = _prepare(_catalogs())
    functions = _functions(agent)
    invalid = [
        ("purchase", {"product_id": "product-visible", "quantity": 1, "fulfillment": "delivery",
                      "destination_id": "home", "price_version": "wrong"}),
        ("consume", {"product_id": "product-visible", "quantity": 1,
                     "use_mode": "use", "duration_seconds": 300}),
        ("start_travel", {"destination_id": "office", "travel_mode": "driving",
                          "planned_duration_seconds": 217}),
    ]
    assert {"purchase", "consume", "submit_work", "start_travel"} <= set(functions)
    for action, payload in invalid:
        assert agent.validate_react_decisions([ReactDecision("", action, payload, "")], False)

    catalogs = _catalogs()
    catalogs["tasks"][0]["material"] = {"end_char": 9, "total_chars": 10}
    catalogs["tasks"][1]["material"] = {"end_char": 9, "total_chars": 10}
    removed = _functions(_prepare(catalogs, overrides={
        "products": [{"product_id": "product-visible", "price_version": "price-v1",
                      "current_stock": 0, "use_modes": ["eat"], "use_duration_seconds": 300,
                      "merchant_location_id": "home", "required_location_id": None}],
        "available_inventory": {"product-visible": 0.0},
        "routes": [],
    }))
    assert {"purchase", "consume", "submit_work", "start_travel", "game_action"}.isdisjoint(removed)


def test_game_action_requires_current_session_and_admissible_command_pair() -> None:
    """游戏命令只绑定当前活动的真实会话和可接受命令。"""
    games = {"session-visible": {
        "session_id": "session-visible", "admissible_commands": ["look", "inventory"],
        "won": False, "lost": False,
    }}
    agent = _prepare(
        _catalogs(), active_activity=_active("play_game", session_id="session-visible"),
        overrides={"games": games})
    schema = _functions(agent)["game_action"]["parameters"]["properties"]
    invalid = ReactDecision(
        "", "game_action", {"game_session_id": "session-visible", "command": "jump"}, "")

    assert schema["game_session_id"]["enum"] == ["session-visible"]
    assert schema["command"]["enum"] == ["inventory", "look"]
    assert "current visible game state" in agent.validate_react_decisions([invalid], False)


def test_unavailable_continue_is_rejected_before_valid_start_without_side_effect() -> None:
    """无当前活动时错误继续不执行，有限纠错后仍可提交一个合法启动。"""
    agent = _prepare(_catalogs())
    invalid = ReactDecision("", "continue_activity", {"activity_id": "piece-2026-01-05"}, "")
    valid = ReactDecision("", "start_targeted_activity", {
        "activity_type": "work", "planned_duration_seconds": 60,
        "target_id": "task-a", "playback_speed": 1,
    }, "")

    assert agent.validate_react_decisions([invalid], False) == (
        "continue_activity is not available in the current activity state")
    assert agent._life_action_count == 0
    assert agent.validate_react_decisions([valid], False) == ""


@pytest.mark.asyncio
async def test_invalid_visible_pair_cannot_bypass_validation_through_dispatch() -> None:
    """直接分派仍复用本轮公开配对校验，错误报价不会触达环境。"""
    agent = _prepare(_catalogs())
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = {
        "product_id": "product-visible", "quantity": 1, "fulfillment": "delivery",
        "destination_id": "home", "price_version": "wrong",
    }

    with pytest.raises(RuntimeError, match="not a visible pair"):
        await agent.dispatch_react_tool("purchase", payload)
    assert environment.calls == [] and agent._life_action_count == 0


@pytest.mark.parametrize("missing", ["tasks", "content_catalog", "game_catalog", "messages", "browse_targets"])
def test_prepare_rejects_every_missing_catalog(missing: str) -> None:
    """任何公开目录缺失都作为协议错误而非空目录处理。"""
    catalogs = _catalogs()
    del catalogs[missing]

    with pytest.raises(RuntimeError, match=rf"LifeAgent {missing} catalog must be a list"):
        _prepare(catalogs)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tasks", {}, "tasks catalog must be a list"),
        ("tasks", ["task"], "tasks catalog item must be an object"),
        ("tasks", [{"task_id": "task"}], "task remaining contract time"),
        ("content_catalog", [{"item_id": " "}], "content_catalog catalog identity"),
        ("content_catalog", [{"item_id": "content"}], "content duration"),
        ("game_catalog", [{"game_id": 7}], "game_catalog catalog identity"),
        ("messages", [{"subject": "missing identity"}], "messages catalog identity"),
        ("browse_targets", [{"target": "product_catalog"}], "browse_targets catalog identity"),
    ],
)
def test_prepare_rejects_malformed_catalogs(field: str, value, message: str) -> None:
    """目录类型、项目结构和身份类型必须与环境公开合同一致。"""
    catalogs = _catalogs()
    catalogs[field] = value

    with pytest.raises(RuntimeError, match=message):
        _prepare(catalogs)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"products": [{"product_id": "product", "price_version": "v1", "current_stock": -1,
                        "use_modes": ["eat"], "use_duration_seconds": 300,
                        "merchant_location_id": "home", "required_location_id": None}]},
         "current stock"),
        ({"available_inventory": {"unknown-product": 1}}, "held product contract"),
        ({"routes": [{"destination_id": "office", "mode": "driving", "duration_seconds": 0}]},
         "route contract"),
        ({"content_progress": {"unknown-content": {
            "item_id": "unknown-content", "position_seconds": 1,
            "remaining_media_seconds": 1, "exposure_count": 1,
        }}}, "content progress identity"),
    ],
)
def test_prepare_rejects_malformed_business_identity_sources(overrides, message) -> None:
    """商品、持有物、路线和观看进度损坏时不生成宽松工具 Schema。"""
    with pytest.raises(RuntimeError, match=message):
        _prepare(_catalogs(), overrides=overrides)


@pytest.mark.parametrize(
    ("projection", "message"),
    [
        (None, "energy feasibility kinds"),
        ({"maximum_duration_seconds_by_kind": {"work": 60}}, "energy feasibility kinds"),
        ({"maximum_duration_seconds_by_kind": {
            "work": True, "watch": 60, "play_game": 60, "read_mail": 60, "browse": 60,
        }}, "work energy duration limit"),
    ],
)
def test_prepare_rejects_missing_or_malformed_energy_projection(projection, message) -> None:
    """能量可行性缺失或类型损坏时不得重新暴露宽松动作。"""
    with pytest.raises(RuntimeError, match=message):
        _prepare(_catalogs(), overrides={"energy_feasibility": projection})


def test_prepare_rejects_terminal_game_as_active_control() -> None:
    """已经终结的游戏进度不能作为当前命令会话重新开放。"""
    games = {"session-visible": {
        "session_id": "session-visible", "admissible_commands": ["look"],
        "won": True, "lost": False,
    }}
    with pytest.raises(RuntimeError, match="game progress is terminal"):
        _prepare(
            _catalogs(), active_activity=_active("play_game", session_id="session-visible"),
            overrides={"games": games})


def test_union_enum_still_requires_exact_kind_and_target_pair() -> None:
    """联合目标枚举不能让内容身份冒充工作任务身份。"""
    agent = _prepare(_catalogs())
    payload = {
        "activity_type": "work",
        "planned_duration_seconds": 60,
        "target_id": "content-visible",
        "playback_speed": 1,
    }

    assert list(Draft202012Validator(_targeted_parameters(agent)).iter_errors(payload)) == []
    assert "not visible for activity_type" in agent.validate_react_decisions(
        [ReactDecision("", "start_targeted_activity", payload, "")], False
    )


def test_memory_observation_does_not_change_frozen_catalog_and_next_round_refreshes() -> None:
    """记忆工具结果不改变本轮目录，新决策重新读取下一轮首观察。"""
    first = _catalogs()
    request = ReactLoopRequest(0, _context().observed_at, _observations(first))
    agent = LifeAgent()
    agent.prepare_react_loop(request)
    first_enum = _targeted_parameters(agent)["properties"]["target_id"]["enum"]
    request.observations.append({"turn": 1, "action": "memory_recent", "ok": True, "data": {"episodes": []}})

    assert _targeted_parameters(agent)["properties"]["target_id"]["enum"] == first_enum

    second = _catalogs()
    second["tasks"] = [{"task_id": "task-next-round", "remaining_contract_seconds": 3600,
                        "material": {"end_char": 10, "total_chars": 10}}]
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, _observations(second)))
    refreshed = _targeted_parameters(agent)["properties"]["target_id"]["enum"]
    assert "task-next-round" in refreshed
    assert "task-a" not in refreshed and "task-b" not in refreshed


def test_readonly_needs_no_environment_catalog_and_opens_no_life_action() -> None:
    """显式只读循环不读取生活目录且只开放记忆与结束工具。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, [], "What happened?", True)
    agent.prepare_react_loop(request)

    assert set(_functions(agent, readonly=True)) == _MEMORY_TOOLS | {"finish"}


def test_paused_activity_keeps_resume_control_when_target_catalogs_are_empty() -> None:
    """目标启动工具撤下时暂停活动仍保留显式恢复控制。"""
    empty = {key: [] for key in _FAILED_OBSERVATION_CATALOGS}
    paused = [{"activity_id": "activity-paused", "status": "paused",
               "remaining_duration_seconds": 60, "minimum_energy_cost_kcal": 2.0,
               "resume_energy_feasible": True}]
    agent = _prepare(empty, paused_activities=paused)
    functions = _functions(agent)
    decision = ReactDecision(
        "", "set_activity_state", {"activity_id": "activity-paused", "operation": "resume"}, ""
    )

    assert "start_targeted_activity" not in functions
    assert "resume" in functions["set_activity_state"]["parameters"]["properties"]["operation"]["enum"]
    assert agent.validate_react_decisions([decision], False) == ""


@pytest.mark.asyncio
async def test_valid_grounded_target_dispatches_same_typed_request() -> None:
    """通过目录配对校验的目标以同一 Pydantic 请求交给环境。"""
    agent = _prepare(_catalogs())
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = {
        "activity_type": "work",
        "planned_duration_seconds": 60,
        "target_id": "task-a",
        "playback_speed": 1,
    }
    decision = ReactDecision("", "start_targeted_activity", payload, "")

    assert agent.validate_react_decisions([decision], False) == ""
    result = await agent.dispatch_react_tool("start_targeted_activity", payload)

    assert result.ok is True
    assert len(environment.calls) == 1
    assert isinstance(environment.calls[0].request, StartTargetedActivityRequest)
    assert environment.calls[0].request.target_id == "task-a"


@pytest.mark.asyncio
async def test_paused_resume_dispatch_remains_typed_and_independent_of_catalogs() -> None:
    """暂停活动恢复动作不依赖目标启动目录并保持原请求类型。"""
    empty = {key: [] for key in _FAILED_OBSERVATION_CATALOGS}
    paused = [{"activity_id": "activity-paused", "status": "paused",
               "remaining_duration_seconds": 60, "minimum_energy_cost_kcal": 2.0,
               "resume_energy_feasible": True}]
    agent = _prepare(empty, paused_activities=paused)
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = {"activity_id": "activity-paused", "operation": "resume"}
    decision = ReactDecision("", "set_activity_state", payload, "")

    assert agent.validate_react_decisions([decision], False) == ""
    await agent.dispatch_react_tool("set_activity_state", payload)

    assert isinstance(environment.calls[0].request, SetActivityStateRequest)
    assert environment.calls[0].request.operation == "resume"


@pytest.mark.asyncio
async def test_energy_limit_binds_schema_and_rejects_sixty_one_seconds_before_dispatch() -> None:
    """本轮每类能量上限同时约束实际 Schema 与分派前的精确配对。"""
    maximums = {kind: 0 for kind in _TARGET_CATALOG_FIELDS_FOR_TEST}
    maximums["work"] = 60
    maximums["watch"] = 120
    agent = _prepare(_catalogs(), overrides={
        "energy_feasibility": _energy_limits(**maximums),
        "routes": [],
    })
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    parameters = _targeted_parameters(agent)
    allowed = {"activity_type": "work", "planned_duration_seconds": 60,
               "target_id": "task-a", "playback_speed": 1}
    excessive = {**allowed, "planned_duration_seconds": 61}
    above_global = {"activity_type": "watch", "planned_duration_seconds": 121,
                    "target_id": "content-visible", "playback_speed": 1}

    assert parameters["properties"]["planned_duration_seconds"]["maximum"] == 120
    assert list(Draft202012Validator(parameters).iter_errors(allowed)) == []
    assert list(Draft202012Validator(parameters).iter_errors(excessive)) == []
    assert list(Draft202012Validator(parameters).iter_errors(above_global))
    error = agent.validate_react_decisions(
        [ReactDecision("", "start_targeted_activity", excessive, "")], False)
    assert "current energy limit" in error
    with pytest.raises(RuntimeError, match="current energy limit"):
        await agent.dispatch_react_tool("start_targeted_activity", excessive)
    assert environment.calls == [] and agent._life_action_count == 0


def test_route_schema_keeps_feasible_driving_and_removes_infeasible_walking() -> None:
    """相同起点的路线按各自方式代谢成本独立形成可见闭集。"""
    routes = [
        {"destination_id": "office", "mode": "driving", "duration_seconds": 216,
         "minimum_energy_cost_kcal": 6.0, "energy_feasible": True},
        {"destination_id": "office", "mode": "walking", "duration_seconds": 900,
         "minimum_energy_cost_kcal": 50.0, "energy_feasible": False},
    ]
    agent = _prepare(_catalogs(), overrides={"routes": routes})
    parameters = _functions(agent)["start_travel"]["parameters"]
    walking = {"destination_id": "office", "travel_mode": "walking",
               "planned_duration_seconds": 900}

    assert parameters["properties"]["travel_mode"]["enum"] == ["driving"]
    assert list(Draft202012Validator(parameters).iter_errors(walking))
    assert "not a visible route" in agent.validate_react_decisions(
        [ReactDecision("", "start_travel", walking, "")], False)


def test_zero_energy_keeps_recovery_and_purchase_then_refreshes_targets() -> None:
    """零能量撤下耗能目标，但保留恢复与购买；新观察恢复能量后目标重新出现。"""
    zero = {kind: 0 for kind in _TARGET_CATALOG_FIELDS_FOR_TEST}
    agent = _prepare(_catalogs(), overrides={
        "energy_feasibility": _energy_limits(**zero),
        "routes": [{"destination_id": "office", "mode": "driving", "duration_seconds": 216,
                    "minimum_energy_cost_kcal": 6.0, "energy_feasible": False}],
    })
    names = set(_functions(agent))

    assert "start_targeted_activity" not in names and "start_travel" not in names
    assert {"start_basic_activity", "consume", "purchase"} <= names

    agent.prepare_react_loop(ReactLoopRequest(
        0,
        _context().observed_at,
        _observations(_catalogs(), overrides={"energy_feasibility": _energy_limits(work=60)}),
    ))
    refreshed = _functions(agent)
    assert "start_targeted_activity" in refreshed and "work" in (
        refreshed["start_targeted_activity"]["parameters"]["properties"]["activity_type"]["enum"])


def test_infeasible_paused_activity_exposes_cancel_without_resume() -> None:
    """当前能量不足时暂停活动仍可取消，但 Schema 和本地校验都拒绝恢复。"""
    paused = [{"activity_id": "activity-paused", "status": "paused",
               "remaining_duration_seconds": 600, "minimum_energy_cost_kcal": 20.0,
               "resume_energy_feasible": False}]
    agent = _prepare(_catalogs(), paused_activities=paused)
    function = _functions(agent)["set_activity_state"]
    resume = ReactDecision(
        "", "set_activity_state", {"activity_id": "activity-paused", "operation": "resume"}, "")
    cancel = ReactDecision(
        "", "set_activity_state", {"activity_id": "activity-paused", "operation": "cancel"}, "")

    assert function["parameters"]["properties"]["operation"]["enum"] == ["cancel"]
    assert "exceeds current energy" in agent.validate_react_decisions([resume], False)
    assert agent.validate_react_decisions([cancel], False) == ""


def test_paused_resume_pairs_each_identity_with_its_own_feasibility() -> None:
    """联合活动身份与操作枚举仍由本地校验保持每个暂停活动的能量配对。"""
    paused = [
        {"activity_id": "activity-feasible", "status": "paused",
         "remaining_duration_seconds": 60, "minimum_energy_cost_kcal": 2.0,
         "resume_energy_feasible": True},
        {"activity_id": "activity-infeasible", "status": "paused",
         "remaining_duration_seconds": 600, "minimum_energy_cost_kcal": 20.0,
         "resume_energy_feasible": False},
    ]
    agent = _prepare(_catalogs(), paused_activities=paused)
    operations = _functions(agent)["set_activity_state"]["parameters"]["properties"]["operation"]["enum"]
    invalid = ReactDecision(
        "", "set_activity_state", {"activity_id": "activity-infeasible", "operation": "resume"}, "")
    valid = ReactDecision(
        "", "set_activity_state", {"activity_id": "activity-feasible", "operation": "resume"}, "")

    assert operations == ["resume", "cancel"]
    assert "exceeds current energy" in agent.validate_react_decisions([invalid], False)
    assert agent.validate_react_decisions([valid], False) == ""
