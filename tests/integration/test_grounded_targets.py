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
    "content_catalog": [{"item_id": "sintel-en-subtitles"}],
    "game_catalog": [{"game_id": f"textworld-{seed}"} for seed in range(1201, 1209)],
    "messages": [],
    "browse_targets": ["product_catalog"],
}


def _context() -> DecisionContext:
    """构造一个带时区的真实宿主决策上下文。"""
    return DecisionContext(
        "run", "round", "decision", 2, datetime(2026, 1, 5, 0, 3, 45, tzinfo=timezone.utc), 50
    )


def _catalogs() -> dict:
    """构造五类均有可见目标的环境目录。"""
    return {
        "tasks": [{"task_id": "task-b"}, {"task_id": "task-a"}, {"task_id": "task-a"}],
        "content_catalog": [{"item_id": "content-visible"}],
        "game_catalog": [{"game_id": "game-visible"}],
        "messages": [{"message_id": "message-visible"}],
        "browse_targets": ["product_catalog"],
    }


def _observations(catalogs: dict, *, active_activity: dict | None = None) -> list[dict]:
    """把目录包装为 PersonAgent 实际传给 ReAct 的首观察。"""
    data = {
        "observation_event_id": "observation-current",
        "recent_action_results": [],
        "active_activity": active_activity,
        **catalogs,
    }
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


def _prepare(catalogs: dict, *, active_activity: dict | None = None) -> LifeAgent:
    """按生产顺序准备一次非只读人物决策。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, _observations(catalogs, active_activity=active_activity))
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
    """五个目录都为空时撤下目标启动工具并保留活动控制。"""
    empty = {key: [] for key in _FAILED_OBSERVATION_CATALOGS}
    functions = _functions(_prepare(empty))

    assert "start_targeted_activity" not in functions
    assert "start_basic_activity" in functions
    assert "start_travel" in functions
    assert "set_activity_state" in functions


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
        ("content_catalog", [{"item_id": " "}], "content_catalog catalog identity"),
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
    second["tasks"] = [{"task_id": "task-next-round"}]
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
    active = {"activity_id": "activity-paused", "status": "paused"}
    agent = _prepare(empty, active_activity=active)
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
    agent = _prepare(empty, active_activity={"activity_id": "activity-paused", "status": "paused"})
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = {"activity_id": "activity-paused", "operation": "resume"}
    decision = ReactDecision("", "set_activity_state", payload, "")

    assert agent.validate_react_decisions([decision], False) == ""
    await agent.dispatch_react_tool("set_activity_state", payload)

    assert isinstance(environment.calls[0].request, SetActivityStateRequest)
    assert environment.calls[0].request.operation == "resume"
