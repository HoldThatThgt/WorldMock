"""当前九工具协议的观察冻结、动作守卫和记忆失败分支。"""

from __future__ import annotations

import copy
import json
import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest, ReactToolResult
from custom.agents.life_agent import (
    FinishRequest,
    LifeAgent,
    LifeMemoryEpisode,
    MemoryReadRequest,
    MemoryRecentRequest,
    MemorySearchRequest,
    MemoryStepRangeRequest,
    MemoryTickRangeRequest,
    MemoryTimeRangeRequest,
    ReadonlyFinishRequest,
)
from tests.integration.test_life_agent_protocol import (
    _Environment,
    _GroundedCorrectionDispatcher,
    _ModelResponse,
    _context,
    _finish_args,
    _observations,
    _wire_agent,
)
from worldmock.life.models import REQUEST_MODELS, RecallTaskSummaryRequest


def _prepared(data: dict | None = None, *, readonly: bool = False) -> LifeAgent:
    """通过正式入口冻结单条当前观察。"""
    person = LifeAgent()
    observations = [] if readonly else copy.deepcopy(_observations())
    if data is not None:
        observations[0]["data"] = data
    person.prepare_react_loop(ReactLoopRequest(
        0, _context().observed_at, observations, None, readonly))
    return person


def _functions(person: LifeAgent, *, readonly: bool = False) -> dict[str, dict]:
    """读取准备后的真实工具 Schema。"""
    return {row["function"]["name"]: row["function"]
            for row in person.build_react_tool_schemas(readonly=readonly)}


def _decision(name: str, values: dict) -> ReactDecision:
    """构造已解析的 ReAct 工具决定。"""
    return ReactDecision("", name, values, "")


def test_memory_request_models_preserve_semantic_bounds() -> None:
    """记忆数量、时间和正文仍由领域模型约束。"""
    assert MemoryRecentRequest(limit=50).limit == 50
    with pytest.raises(ValidationError):
        MemoryRecentRequest(limit=0)
    assert MemorySearchRequest(query="keyword", limit=1).query == "keyword"
    with pytest.raises(ValidationError):
        MemorySearchRequest(query="word " * 41 + "x", limit=1)
    assert MemoryStepRangeRequest(start_step=0, end_step=9, limit=10).end_step == 9
    with pytest.raises(ValidationError):
        MemoryStepRangeRequest(start_step=5, end_step=1, limit=10)
    assert MemoryTickRangeRequest(start_tick=0, end_tick=60, limit=10).end_tick == 60
    with pytest.raises(ValidationError):
        MemoryTickRangeRequest(start_tick=60, end_tick=0, limit=10)
    with pytest.raises(ValidationError):
        MemoryTimeRangeRequest(
            start_time="2026-01-06T00:00:00+08:00",
            end_time="2026-01-05T00:00:00+08:00", limit=10)
    with pytest.raises(ValidationError):
        MemoryTimeRangeRequest(
            start_time="2026-01-05T00:00:00",
            end_time="2026-01-05T01:00:00", limit=10)


def test_memory_entries_and_finish_keep_content_limits() -> None:
    """人物主观条目与只读答复仍有各自语义长度上限。"""
    episode = {"epistemic_status": "subjective", "type": "reflection",
               "importance": 0.5, "keywords": ["wait"], "text": "I waited.",
               "source": "decision_observation", "refs": ["observation-current"]}
    assert LifeMemoryEpisode(**episode).text == "I waited."
    with pytest.raises(ValidationError):
        LifeMemoryEpisode(**dict(episode, text="长" * 1001))
    with pytest.raises(ValidationError):
        LifeMemoryEpisode(**dict(episode, refs=[]))
    assert FinishRequest(memories=[]).memories == []
    with pytest.raises(ValidationError):
        FinishRequest(memories=[LifeMemoryEpisode(**episode) for _ in range(9)])
    assert len(MemoryReadRequest(ids=[f"m-{item}" for item in range(20)]).ids) == 20
    with pytest.raises(ValidationError):
        MemoryReadRequest(ids=[])
    with pytest.raises(ValidationError):
        ReadonlyFinishRequest(answer="word " * 801)


@pytest.mark.parametrize("change,match", [
    (lambda data: data.pop("observation_event_id"), "observation event identity"),
    (lambda data: data.update(recent_action_results="broken"), "recent action results"),
    (lambda data: data["facts"].update(progress={"sessions": "broken", "results": [], "alarms": []}),
     "sessions layer"),
    (lambda data: data["facts"].update(interactive_objects="broken"), "interactive objects layer"),
    (lambda data: data["facts"]["interactive_objects"].append(
        dict(data["facts"]["interactive_objects"][0])), "interactive object identity"),
    (lambda data: data.update(interaction_menu={"allowed_actions": ["wait"]}), "interaction menu"),
])
def test_corrupt_observation_fails_before_any_model_tool(change, match: str) -> None:
    """损坏的来源、进度、对象或菜单不能转成模型工具。"""
    data = copy.deepcopy(_observations()[0]["data"])
    change(data)
    with pytest.raises(RuntimeError, match=match):
        _prepared(data)


def _empty_layer_observations() -> list[dict]:
    """构造交互对象层与私有对象绑定都为空的当前观察。"""
    observations = copy.deepcopy(_observations())
    observations[0]["data"]["facts"]["interactive_objects"] = []
    observations[0]["data"]["interaction_bindings"] = []
    return observations


def test_empty_interactive_layer_binds_no_empty_enum() -> None:
    """空对象层合法：start_activity 照常开放，不绑定空枚举，所填名称按协议错误重试。"""
    person = _prepared(_empty_layer_observations()[0]["data"])
    functions = _functions(person)
    assert "start_activity" in functions
    properties = functions["start_activity"]["parameters"]["properties"]
    assert "enum" not in properties["targets"]["items"]
    assert "enum" not in properties["actions"]["items"]
    assert "not a visible interactive object" in person.validate_react_decisions([
        _decision("start_activity", {"targets": ["自己"], "actions": ["等待"]}),
    ], False)


@pytest.mark.asyncio
async def test_empty_interactive_layer_decision_continues_without_failure() -> None:
    """空对象层时决策照常完成：不可见名称在本轮纠正，改提交其他动作且不终止运行。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-unseen", "start_activity",
                       json.dumps({"targets": ["自己"], "actions": ["等待"]})),
        _ModelResponse("call-recall", "recall_task_summary",
                       json.dumps({"task_name": "整理会议纪要"}, ensure_ascii=False)),
        _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 5, memory=False)

    await agent.run_react_loop(ReactLoopRequest(
        0, _context().observed_at, _empty_layer_observations()))

    assert len(environment.calls) == 1
    assert environment.calls[0].request == RecallTaskSummaryRequest(task_name="整理会议纪要")
    action_responses = [item for item in environment.evidence
                        if item.phase == "response" and item.request.get("tools")]
    assert "not a visible interactive object" in action_responses[0].error
    first_tools = next(item for item in dispatcher.requests if "tools" in item)["tools"]
    start = next(item["function"] for item in first_tools
                 if item["function"]["name"] == "start_activity")
    properties = start["parameters"]["properties"]
    assert "enum" not in properties["targets"]["items"]
    assert "enum" not in properties["actions"]["items"]


def test_tool_visibility_uses_current_named_tasks_and_sessions() -> None:
    """会话控制只用当前可读名称，完整工具面仍是九个业务工具的子集。"""
    data = copy.deepcopy(_observations()[0]["data"])
    session_name = "工作 整理会议纪要（2026-01-05 09:00:00）"
    data["facts"]["progress"]["sessions"] = [{
        "session_name": session_name, "task_name": "整理会议纪要", "phase": "进行中",
    }]
    data["session_bindings"] = [{
        "session_name": session_name, "session_id": "session-private",
        "task_name": "整理会议纪要", "status": "active",
    }]
    person = _prepared(data)
    functions = _functions(person)
    assert {"start_activity", "recall_task_summary", "submit_work",
            "report_status", "pause_activity", "conclude_activity"} <= set(functions)
    assert not {"purchase", "consume", "set_activity_state", "discover_places"} & set(functions)
    assert set(functions) & set(REQUEST_MODELS) <= set(REQUEST_MODELS)
    allowed = functions["pause_activity"]["parameters"]["properties"]["task"]["items"]["enum"]
    assert allowed == ["整理会议纪要"]
    assert "session-private" not in str(functions)


def test_action_validation_rejects_unseen_object_wrong_action_and_second_action() -> None:
    """对象与逐项动作闭集、单次动作限制在分派前执行。"""
    person = _prepared()
    _functions(person)
    assert person.validate_react_decisions([
        _decision("start_activity", {"targets": ["自己"], "actions": ["等待"]}),
    ], False) == ""
    assert "visible interactive object" in person.validate_react_decisions([
        _decision("start_activity", {"targets": ["object-private"], "actions": ["等待"]}),
    ], False)
    assert "not offered" in person.validate_react_decisions([
        _decision("start_activity", {"targets": ["自己"], "actions": ["工作"]}),
    ], False)
    person._life_action_count = 1
    assert "already submitted" in person.validate_react_decisions([
        _decision("start_activity", {"targets": ["自己"], "actions": ["等待"]}),
    ], False)


@pytest.mark.asyncio
async def test_memory_dispatch_returns_retry_feedback_without_exposing_missing_ids() -> None:
    """记忆运行时失败和未知身份作为工具失败反馈，不杀人物决策。"""
    person = _prepared()
    person._memory_runtime = type("Memory", (), {
        "dispatch_tool": lambda self, action, args: (False, "memory unavailable", None),
    })()
    failure = await person.dispatch_react_tool("memory_recent", {"limit": 1})
    assert isinstance(failure, ReactToolResult)
    assert failure.ok is False
    assert "memory unavailable" in failure.observation
    person._memory_runtime = type("Memory", (), {
        "dispatch_tool": lambda self, action, args: (
            True, "{}", {"episodes": [{"id": "m-other", "text": "other"}]}),
    })()
    missing = await person.dispatch_react_tool("memory_read", {"ids": ["m-wanted"]})
    assert missing.ok is False
    assert missing.data == {"unknown_ids": ["m-wanted"]}
    assert "m-wanted" not in person._life_visible_refs
