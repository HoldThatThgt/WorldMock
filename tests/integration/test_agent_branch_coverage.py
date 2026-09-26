"""当前工具协议的观察冻结、动作守卫、同刻受限轮与记忆查询分支。"""

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
    LifeAgent,
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
    _NEWS,
    _WAIT,
    _WORK,
    _context,
    _memory,
    _observations,
    _wire_agent,
    _with_activities,
)
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING
from worldmock.life.models import (
    ConcludeActivityRequest,
    ConclusionMemory,
    REQUEST_MODELS,
    RecallTaskSummaryRequest,
)


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
    """收尾记忆条目与只读答复仍有各自语义上限；记忆不带 refs，来源不由模型填写（第七章）。"""
    memory = _memory("我等了一会儿。")
    assert ConclusionMemory(**memory).text == "我等了一会儿。"
    with pytest.raises(ValidationError):
        ConclusionMemory(**dict(memory, text="长" * 1001))
    for extra in ({"refs": ["observation-current"]}, {"source": "decision_observation"}):
        with pytest.raises(ValidationError):
            ConclusionMemory(**memory, **extra)
    request = ConcludeActivityRequest(task=[_WAIT], summary=["收尾。"], memories=[[]])
    assert request.memories == [[]]
    with pytest.raises(ValidationError):
        ConcludeActivityRequest(task=[_WAIT], summary=["收尾。"],
                                memories=[[ConclusionMemory(**memory) for _ in range(9)]])
    with pytest.raises(ValidationError):
        ReadonlyFinishRequest(answer="word " * 801)


@pytest.mark.parametrize("change,match", [
    (lambda data: data.pop("observation_event_id"), "observation event identity"),
    (lambda data: data.update(observation_event_id=""), "observation event identity"),
    (lambda data: data["facts"].update(progress={"sessions": "broken", "results": [], "alarms": []}),
     "sessions layer"),
    (lambda data: data["facts"]["progress"]["sessions"][0].pop("activities"), "session identity"),
    (lambda data: _with_activities(data, (_WAIT, "未知阶段")), "activity phase"),
    (lambda data: _with_activities(data, (_WAIT, PHASE_ACTIVE), (_WAIT, PHASE_PAUSED)), "activity identity"),
    (lambda data: data.pop("activity_bindings"), "activity bindings"),
    (lambda data: _with_activities(data, (_NEWS, PHASE_PAUSED))["activity_bindings"][0].pop("resumable"),
     "activity bindings"),
    (lambda data: data["facts"].update(interactive_objects="broken"), "interactive objects layer"),
    (lambda data: data["facts"]["interactive_objects"].append(
        dict(data["facts"]["interactive_objects"][0])), "interactive object identity"),
    (lambda data: data.update(interaction_menu={"allowed_actions": ["wait"]}), "interaction menu"),
    (lambda data: data.pop("same_moment_stage"), "same-moment stage"),
    (lambda data: data.update(same_moment_stage="bogus"), "same-moment stage"),
])
def test_corrupt_observation_fails_before_any_model_tool(change, match: str) -> None:
    """损坏的进度层、活动绑定、对象、菜单或同刻阶段不能转成模型工具；闭集之外的同刻阶段是损坏的宿主观察。"""
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
    """空对象层时决策照常完成：不可见名称在本轮纠正，改提交其他动作，登记即结束本轮且不终止运行。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-unseen", "start_activity",
                       json.dumps({"targets": ["自己"], "actions": ["等待"]})),
        _ModelResponse("call-recall", "recall_task_summary",
                       json.dumps({"task_name": "整理会议纪要"}, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 5, memory=False)

    result = await agent.run_react_loop(ReactLoopRequest(
        0, _context().observed_at, _empty_layer_observations()))

    assert result == "recall_task_summary registered"
    assert len(environment.calls) == 1
    assert environment.calls[0].request == RecallTaskSummaryRequest(task_name="整理会议纪要")
    action_responses = [item for item in environment.evidence
                        if item.phase == "response" and item.request.get("tools")]
    assert len(action_responses) == 2
    assert "not a visible interactive object" in action_responses[0].error
    first_tools = next(item for item in dispatcher.requests if "tools" in item)["tools"]
    start = next(item["function"] for item in first_tools
                 if item["function"]["name"] == "start_activity")
    properties = start["parameters"]["properties"]
    assert "enum" not in properties["targets"]["items"]
    assert "enum" not in properties["actions"]["items"]


_MEMORY_TOOLS = {"memory_recent", "memory_search", "memory_step_range", "memory_tick_range",
                 "memory_time_range"}
_SUPPLY = "便利店出售的饭团"
#: 两项同时刚结束、待收尾的活动（到达时出行与路上开始的播客一并结束，第七章「会话切换的边界情形」）。
_WALK = "步行前往公园（1月4日 23:30 起）"
_PODCAST = "收听晚间播客（1月4日 23:30 起）"


def _restricted_data(stage: str, *activities: tuple[str, str], elsewhere: tuple[str, ...] = ()) -> dict:
    """构造标上同刻受限轮阶段的观察：缺省一项进行中、一项暂停的活动占满两个名额，另有一家可购买门店。"""
    data = copy.deepcopy(_observations()[0]["data"])
    data["same_moment_stage"] = stage
    data["facts"]["interactive_objects"] += [
        {"object": _SUPPLY, "actions": ["到店购买", "配送到当前位置"], "preconditions_met": True},
        {"object": "商品目录", "actions": ["浏览"], "preconditions_met": True}]
    data["interaction_bindings"] += [
        {"object": _SUPPLY, "kind": "supply", "object_id": "supply-visible", "actions": [
            {"label": "到店购买", "canonical_action": "purchase"},
            {"label": "配送到当前位置", "canonical_action": "purchase"}]},
        {"object": "商品目录", "kind": "catalog", "object_id": "catalog",
         "actions": [{"label": "浏览", "canonical_action": "browse"}]}]
    return _with_activities(data, *(activities or ((_WORK, PHASE_ACTIVE), (_NEWS, PHASE_PAUSED))),
                            elsewhere=elsewhere)


def _enum(functions: dict[str, dict], tool: str, field: str) -> list[str]:
    """读取工具 Schema 中数组参数的名称枚举。"""
    return functions[tool]["parameters"]["properties"][field]["items"]["enum"]


def _conclusion(*names: str) -> dict:
    """点名活动结束的参数（逐项自述，不写记忆）。"""
    return {"task": list(names), "summary": ["先收尾。" for _ in names], "memories": [[] for _ in names]}


def test_start_or_resume_stage_narrows_tools_and_enums() -> None:
    """start_or_resume 只开放开始或恢复：不含到店购买与远程下单，播报只列此处可恢复的暂停活动，记忆查询照常（第一章规则 3）。"""
    opened = _functions(_prepared(_restricted_data("open")))
    assert {"pause_activity", "conclude_activity", "recall_task_summary"} <= set(opened)
    assert _SUPPLY in _enum(opened, "start_activity", "targets")
    assert {"到店购买", "配送到当前位置"} <= set(_enum(opened, "start_activity", "actions"))
    person = _prepared(_restricted_data("start_or_resume"))
    functions = _functions(person)
    assert set(functions) - _MEMORY_TOOLS == {"start_activity", "report_status"}
    assert _MEMORY_TOOLS & set(functions)
    assert _enum(functions, "start_activity", "targets") == sorted(["自己", "整理会议纪要", "商品目录"])
    assert sorted(_enum(functions, "start_activity", "actions")) == sorted(["等待", "睡觉", "工作", "浏览"])
    assert _enum(functions, "report_status", "task") == [_NEWS]
    assert "not a visible interactive object" in person.validate_react_decisions([
        _decision("start_activity", {"targets": [_SUPPLY], "actions": ["到店购买"]})], False)
    assert "task contains an unavailable activity" in person.validate_react_decisions([
        _decision("report_status", {"task": [_WORK], "summary": ["继续。"]})], False)
    assert "not available in the current activity state" in person.validate_react_decisions([
        _decision("pause_activity", {"task": [_WORK], "summary": ["先停。"]})], False)
    assert person.validate_react_decisions([
        _decision("report_status", {"task": [_NEWS], "summary": ["接着看。"]})], False) == ""
    assert person.validate_react_decisions([
        _decision("start_activity", {"targets": ["商品目录"], "actions": ["浏览"]})], False) == ""


def test_paused_activity_elsewhere_is_not_resumable_in_restricted_rounds() -> None:
    """开始地点在别处的暂停活动：常规轮仍可点名播报，受限轮的恢复枚举不列它，但仍可结束（第一章规则 3、第七章）。"""
    opened = _functions(_prepared(_restricted_data("open", (_NEWS, PHASE_PAUSED), elsewhere=(_NEWS,))))
    assert _enum(opened, "report_status", "task") == [_NEWS]
    person = _prepared(_restricted_data("start_or_resume", (_NEWS, PHASE_PAUSED), elsewhere=(_NEWS,)))
    functions = _functions(person)
    assert set(functions) - _MEMORY_TOOLS == {"start_activity"}
    assert "not available in the current activity state" in person.validate_react_decisions([
        _decision("report_status", {"task": [_NEWS], "summary": ["接着看。"]})], False)
    both_paused = _restricted_data("resume_or_conclude", (_NEWS, PHASE_PAUSED), (_WORK, PHASE_PAUSED),
                                   elsewhere=(_NEWS,))
    person = _prepared(both_paused)
    functions = _functions(person)
    assert _enum(functions, "report_status", "task") == [_WORK]
    assert _enum(functions, "conclude_activity", "task") == sorted([_NEWS, _WORK])
    assert "task contains an unavailable activity" in person.validate_react_decisions([
        _decision("report_status", {"task": [_NEWS], "summary": ["接着看。"]})], False)
    assert person.validate_react_decisions([_decision("conclude_activity", _conclusion(_NEWS))], False) == ""


def test_conclude_pending_stage_requires_concluding_every_just_ended_activity() -> None:
    """conclude_pending 只开放结束，名称只列刚结束的活动，且须一次点名全部待收尾活动（第一章规则 3、第七章）。"""
    person = _prepared(_restricted_data("conclude_pending", (_WALK, PHASE_PENDING), (_PODCAST, PHASE_PENDING)))
    functions = _functions(person)
    assert set(functions) - _MEMORY_TOOLS == {"conclude_activity"}
    assert _MEMORY_TOOLS & set(functions)
    assert _enum(functions, "conclude_activity", "task") == sorted([_WALK, _PODCAST])
    assert set(functions["conclude_activity"]["parameters"]["properties"]) == {"task", "summary", "memories"}
    assert person.validate_react_decisions([_decision("conclude_activity", _conclusion(_WALK))], False) == (
        "conclude_activity must name every activity that just ended")
    assert person.validate_react_decisions([_decision("conclude_activity", _conclusion(_WALK, _PODCAST))],
                                           False) == ""
    assert "not available in the current activity state" in person.validate_react_decisions([
        _decision("start_activity", {"targets": ["自己"], "actions": ["等待"]})], False)
    person = _prepared(_restricted_data("conclude_pending", (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_PENDING)))
    assert _enum(_functions(person), "conclude_activity", "task") == [_WAIT]
    assert "task contains an unavailable activity" in person.validate_react_decisions([
        _decision("conclude_activity", _conclusion(_WORK, _WAIT))], False)
    assert person.validate_react_decisions([_decision("conclude_activity", _conclusion(_WAIT))], False) == ""


def test_resume_or_conclude_stage_offers_resume_or_conclusion_only() -> None:
    """resume_or_conclude 只开放恢复与结束：播报只列可恢复的暂停活动，结束列全部占名额的活动（第一章规则 3）。"""
    person = _prepared(_restricted_data("resume_or_conclude"))
    functions = _functions(person)
    assert set(functions) - _MEMORY_TOOLS == {"report_status", "conclude_activity"}
    assert _enum(functions, "report_status", "task") == [_NEWS]
    assert _enum(functions, "conclude_activity", "task") == sorted([_WORK, _NEWS])
    assert "task contains an unavailable activity" in person.validate_react_decisions([
        _decision("report_status", {"task": [_WORK], "summary": ["继续。"]})], False)
    assert person.validate_react_decisions([
        _decision("report_status", {"task": [_NEWS], "summary": ["接着看。"]})], False) == ""
    assert person.validate_react_decisions([_decision("conclude_activity", _conclusion(_WORK))], False) == ""
    for tool, arguments in (("start_activity", {"targets": ["自己"], "actions": ["等待"]}),
                            ("pause_activity", {"task": [_WORK], "summary": ["先停。"]})):
        assert "not available in the current activity state" in person.validate_react_decisions([
            _decision(tool, arguments)], False)


def test_conclude_only_stage_offers_only_conclusion_of_slot_activities() -> None:
    """conclude_only 只开放结束，名称枚举列占名额的活动，结束不会被本地校验挡住（第一章规则 3）。"""
    person = _prepared(_restricted_data("conclude_only"))
    functions = _functions(person)
    assert set(functions) - _MEMORY_TOOLS == {"conclude_activity"}
    assert _enum(functions, "conclude_activity", "task") == sorted([_WORK, _NEWS])
    assert person.validate_react_decisions([_decision("conclude_activity", _conclusion(_NEWS))], False) == ""
    assert person.validate_react_decisions([_decision("conclude_activity", _conclusion(_WORK, _NEWS))],
                                           False) == ""
    for tool, arguments in (("start_activity", {"targets": ["自己"], "actions": ["等待"]}),
                            ("report_status", {"task": [_NEWS], "summary": ["接着看。"]}),
                            ("recall_task_summary", {"task_name": "整理会议纪要"})):
        assert "not available in the current activity state" in person.validate_react_decisions([
            _decision(tool, arguments)], False)


def test_wait_only_stage_offers_only_wait() -> None:
    """wait_only 只开放等待：start_activity 的对象与动作枚举只剩「自己」与「等待」，活动控制与回看都撤下。"""
    person = _prepared(_restricted_data("wait_only"))
    functions = _functions(person)
    assert set(functions) - _MEMORY_TOOLS == {"start_activity"}
    assert _enum(functions, "start_activity", "targets") == ["自己"]
    assert _enum(functions, "start_activity", "actions") == ["等待"]
    assert "not offered by the chosen target" in person.validate_react_decisions([
        _decision("start_activity", {"targets": ["自己"], "actions": ["睡觉"]})], False)
    assert "not available in the current activity state" in person.validate_react_decisions([
        _decision("report_status", {"task": [_NEWS], "summary": ["接着看。"]})], False)
    assert person.validate_react_decisions([
        _decision("start_activity", {"targets": ["自己"], "actions": ["等待"]})], False) == ""


def test_tool_visibility_uses_current_named_tasks_and_activities() -> None:
    """活动控制只用当前可读活动名，完整工具面仍是业务工具的子集，私有身份不进 Schema。"""
    data = copy.deepcopy(_observations()[0]["data"])
    _with_activities(data, (_WORK, PHASE_ACTIVE))
    person = _prepared(data)
    functions = _functions(person)
    assert {"start_activity", "recall_task_summary", "submit_work",
            "report_status", "pause_activity", "conclude_activity"} <= set(functions)
    assert not {"purchase", "consume", "set_activity_state", "discover_places", "finish"} & set(functions)
    assert set(functions) & set(REQUEST_MODELS) <= set(REQUEST_MODELS)
    assert _enum(functions, "pause_activity", "task") == [_WORK]
    assert "activity-private-1" not in str(functions)
    assert "session-7-0001" not in str(functions)


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
async def test_memory_dispatch_returns_retry_feedback_and_hides_episode_identities() -> None:
    """记忆运行时失败作为工具失败反馈、不杀人物决策；成功结果只投影 {type, text, time, epistemic_status}。"""
    person = _prepared()
    person._memory_runtime = type("Memory", (), {
        "dispatch_tool": lambda self, action, args: (False, "memory unavailable", None),
    })()
    failure = await person.dispatch_react_tool("memory_recent", {"limit": 1})
    assert isinstance(failure, ReactToolResult)
    assert failure.ok is False
    assert "memory unavailable" in failure.observation
    stored = {"id": "m_00000060_000001", "agent_id": 7, "type": "goal", "text": "想早点睡",
              "time": "2026-01-04T22:00:00+08:00", "importance": 0.4, "keywords": ["睡觉"],
              "source": "activity_conclusion", "refs": [], "epistemic_status": "subjective"}
    person._memory_runtime = type("Memory", (), {
        "dispatch_tool": lambda self, action, args: (True, "{}", {"episodes": [dict(stored)]}),
    })()
    success = await person.dispatch_react_tool("memory_search", {"query": "睡觉", "limit": 1})
    assert success.ok is True
    assert success.data == {"epistemic_status": "subjective", "episodes": [
        {"type": "goal", "text": "想早点睡", "time": "2026-01-04T22:00:00+08:00", "epistemic_status": "subjective"}]}
    assert json.loads(success.observation) == success.data
    assert stored["id"] not in success.observation


@pytest.mark.asyncio
async def test_life_agent_host_guards_fail_closed_on_unprepared_or_wrong_state() -> None:
    """宿主状态不完整或调用越界时明确失败：未冻结对象层不生成 Schema，未准备工具面不校验，只读模式不提交世界请求，
    未知工具不分派，模型未初始化不发决策自述请求（这些都是宿主内部错误，不是人物可触发的业务情形）。"""
    person = _prepared()
    del person._life_visible_objects
    with pytest.raises(RuntimeError, match="visible interactive objects are not prepared"):
        person.build_react_tool_schemas(readonly=False)
    person = _prepared()
    person._life_request_models = None
    with pytest.raises(RuntimeError, match="request tool schemas are not prepared"):
        person.validate_react_decisions([_decision("start_activity", {"targets": ["自己"], "actions": ["等待"]})],
                                        False)
    with pytest.raises(RuntimeError, match="disabled in readonly mode"):
        await person.dispatch_react_tool("start_activity", {"targets": ["自己"], "actions": ["等待"]},
                                         readonly=True)
    with pytest.raises(RuntimeError, match="Unsupported LifeAgent tool dispatch"):
        await person.dispatch_react_tool("finish", {"answer": "不存在的生活决策结束工具"})
    person._model_name = None
    with pytest.raises(RuntimeError, match="LLM is not initialized"):
        person.prelude_decision_statement_request(ReactLoopRequest(0, _context().observed_at, _observations()))
