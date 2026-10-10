from tests.integration.runtime.current_contract_fixtures import profile as current_profile
"""当前工具协议的观察冻结、名称守卫、受限轮不收窄工具面与记忆查询分支。"""


import copy
import json
import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest, ReactToolResult
from custom.agents.life_agent import (
    LifeAgent,
    MemoryRecentRequest,
    MemorySearchRequest,
    MemoryTimeRangeRequest,
    ReadonlyFinishRequest,
)
from tests.integration.runtime.test_life_agent_protocol import (
    _Environment,
    _GroundedCorrectionDispatcher,
    _ModelResponse,
    _NEWS,
    _WORK_ACTION,
    _WAIT,
    _WAIT_CHOICE,
    _WORK,
    _add_objects,
    _context,
    _memory,
    _observations,
    _offer,
    _visible_object,
    _wire_agent,
    _with_activities,
)
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_PAUSED
from worldmock.life.models import ConclusionMemory, REQUEST_MODELS, RecallTaskSummaryRequest
from tests.life.conftest import start_arguments, conclude_request


def _prepared(data: dict | None = None, *, readonly: bool = False) -> LifeAgent:
    """通过正式入口冻结单条当前观察。"""
    person = LifeAgent()
    person._profile = current_profile()
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
    request = conclude_request(_WAIT, summary="收尾。", memories=[[]])
    assert [(item.memories, item.views) for item in request.items] == [([], [])]
    with pytest.raises(ValidationError):
        conclude_request(_WAIT, summary="收尾。", memories=[[ConclusionMemory(**memory) for _ in range(9)]])
    with pytest.raises(ValidationError):
        ReadonlyFinishRequest(answer="word " * 801)


def _misaligned_meta(data: dict) -> None:
    """进度层有一条记录，私有元数据却为空。"""
    data["progress_layer"]["events"].append({"occurred_at": "2026-01-05T00:00:00+00:00", "type": "plan_reminder",
                                             "goal": "工作"})


@pytest.mark.parametrize("change,match", [
    (lambda data: data.pop("observation_event_id"), "observation event identity"),
    (lambda data: data.update(observation_event_id=""), "observation event identity"),
    (lambda data: data.pop("tool_layer"), "observation layer tool_layer is unavailable"),
    (lambda data: data["todo_layer"].update(layer="工具层"), "observation layer todo_layer is unavailable"),
    (lambda data: data.pop("memory_layer"), "observation layer memory_layer is unavailable"),
    (lambda data: data.update(environment_layer=[]), "observation layer environment_layer is unavailable"),
    (lambda data: data.pop("progress_layer"), "observation layer progress_layer is unavailable"),
    (lambda data: data.pop("persona_views"), "persona views are invalid"),
    (lambda data: data["persona_views"].update(preferences=None), "persona views are invalid"),
    (_misaligned_meta, "progress metadata is invalid"),
    (lambda data: data.update(progress_meta=[{"protected": "yes", "lines": []}]), "progress metadata is invalid"),
    (lambda data: data.pop("interaction_bindings"), "interaction bindings must be a list"),
    (lambda data: data["interaction_bindings"].append(dict(data["interaction_bindings"][0])),
     "interaction binding identity is invalid"),
    (lambda data: data["interaction_bindings"][0].pop("kind"), "interaction binding identity is invalid"),
    (lambda data: data["interaction_bindings"][0]["actions"][0].pop("action"),
     "interaction binding action is invalid"),
    (lambda data: data["interaction_bindings"][0].update(actions=["等待"]), "interaction binding action is invalid"),
    (lambda data: data.pop("activity_bindings"), "activity_bindings are invalid"),
    (lambda data: data["activity_bindings"].append({"name": "等待（1月5日 09:00 起）", "activity_id": "activity-x",
                                                    "phase": "进行中", "resumable": False, "note_labels": None}),
     "activity_bindings are invalid"),
    (lambda data: data.update(goal_bindings=None), "goal_bindings are invalid"),
    (lambda data: data.update(wake_reasons=[""]), "wake reasons are invalid"),
    (lambda data: data.pop("errand_bindings"), "errand_bindings are invalid"),
    (lambda data: data["errand_bindings"].append({"name": "补一份食品", "errand_id": "errand-x", "outcome": "obtain"}),
     "errand_bindings are invalid"),
    (lambda data: data["errand_item_bindings"].append("现有食品"), "errand_item_bindings are invalid"),
])
def test_corrupt_observation_fails_before_any_model_tool(change, match: str) -> None:
    """缺层或层名不符、看法偏好或进度层元数据损坏、私有绑定损坏（含两类私有事务绑定缺失、缺键或不是对象）、菜单或唤醒
    原因损坏的观察不能转成模型请求：都是损坏的宿主观察，在任何模型调用之前失败。"""
    data = copy.deepcopy(_observations()[0]["data"])
    change(data)
    with pytest.raises(RuntimeError, match=match):
        _prepared(data)


def _empty_layer_observations() -> list[dict]:
    """构造工具层目标与私有对象绑定都为空的当前观察。"""
    observations = copy.deepcopy(_observations())
    observations[0]["data"]["tool_layer"]["targets"] = []
    observations[0]["data"]["interaction_bindings"] = []
    return observations


@pytest.mark.asyncio
async def test_empty_interactive_layer_decision_continues_without_failure() -> None:
    """工具层没有目标时决策照常完成：不可见名称在本轮纠正，改为修订空计划后结束本轮。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-unseen", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
        _ModelResponse("call-revise", "revise_day_plan", '{"goals":[]}'),
    ])
    environment = _Environment()
    agent = LifeAgent()
    agent._profile = current_profile()
    _wire_agent(agent, environment, dispatcher, 5, memory=False)

    result = await agent.run_react_loop(ReactLoopRequest(
        0, _context().observed_at, _empty_layer_observations()))

    assert result == "revise_day_plan registered"
    assert len(environment.calls) == 1
    assert environment.calls[0].request.goals == []
    action_responses = [item for item in environment.evidence if item.phase == "response"]
    assert len(action_responses) == 2
    assert "No known object" in action_responses[0].error
    start = next(item["function"] for item in dispatcher.requests[0]["tools"]
                 if item["function"]["name"] == "start_activity")
    properties = start["parameters"]["properties"]
    for field in ("target", "action"):
        assert "enum" not in properties["items"]["items"]["properties"][field]


_APP = "学课程"
#: 受限轮与名额已满时环境给出的原因（工具层 reason 与私有绑定 note 同一文本）。
_RESTRICTED = "同一时刻只能先开始或恢复一件事"
_SLOTS_FULL = "两件事都在做，先结束一件"


def _restricted_data(stage: str) -> dict:
    """标上同刻受限轮阶段：一项进行中、一项暂停，应用启动标不可用并写原因。"""
    data = copy.deepcopy(_observations()[0]["data"])
    data["same_moment_stage"] = stage
    _add_objects(data, _visible_object(
        _APP,
        "app",
        "learn-course",
        _offer("interact", _RESTRICTED),
    ))
    return _with_activities(data, (_WORK, PHASE_ACTIVE), (_NEWS, PHASE_PAUSED))


def _choice(target: str, action: str) -> dict:
    """对一个对象选择一项「动词：方式」的 start_activity 参数。"""
    return start_arguments([target], [action])


def _conclusion(*names: str) -> dict:
    """点名活动结束的参数（一项一个对象，写自述，不写记忆与看法；没有个人事务变更）。"""
    return {"items": [{"activity_name": name, "summary": "先收尾。", "memories": [], "views": []} for name in names],
            "errands": []}




@pytest.mark.parametrize("stage", ["open", "start_or_resume", "resume_or_conclude", "conclude_only",
                                   "conclude_pending"])
def test_restricted_rounds_do_not_narrow_the_host_tool_surface(stage: str) -> None:
    """受限轮与待收尾只由工具层 available/reason 表达：宿主工具面与常规轮逐字节相同，标不可用的动作与控制照常通过
    本地校验，交给环境以同一原因业务拒绝。"""
    person = _prepared(_restricted_data(stage))
    assert json.dumps(_functions(person)) == json.dumps(_functions(_prepared()))
    for decision in (_decision("start_activity", _choice(_APP, "interact")),
                     _decision("pause_activity", {"items": [{"activity_name": _WORK, "summary": "先停。"}],
                                                  "errands": []}),
                     _decision("report_status", {"items": [{"activity_name": _NEWS, "summary": "接着看。"}],
                                                 "errands": []}),
                     _decision("conclude_activity", _conclusion(_WORK, _NEWS)),
                     _decision("recall_task_summary", {"task_name": _NEWS})):
        assert person.validate_react_decisions([decision], False) == ""


def test_tool_visibility_uses_current_named_tasks_and_activities() -> None:
    """八个人物工具整次运行都在，名称只按当前私有绑定核对；私有身份不进 Schema。"""
    data = copy.deepcopy(_observations()[0]["data"])
    _with_activities(data, (_WORK, PHASE_ACTIVE))
    person = _prepared(data)
    functions = _functions(person)
    assert set(REQUEST_MODELS) <= set(functions)
    assert not {"purchase", "consume", "set_activity_state", "discover_places", "finish"} & set(functions)
    assert person.validate_react_decisions([
        _decision("pause_activity", {"items": [{"activity_name": _WORK, "summary": "先停。"}], "errands": []})],
        False) == ""
    assert "not known" in person.validate_react_decisions([
        _decision("pause_activity", {"items": [{"activity_name": _NEWS, "summary": "先停。"}], "errands": []})],
        False)
    assert "TOOL_CALL_REQUIRED: The tool call batch is invalid." == person.validate_react_decisions([
        _decision("send_message", {"recipient_name": "陌生人", "text": "你好"})], False)
    assert "activity-private-1" not in str(functions)
    assert "session-7-0001" not in str(functions)


def test_action_validation_rejects_unseen_object_wrong_action_and_second_action() -> None:
    """对象、逐项动词与方式的闭集、三数组等长与单次动作限制在分派前执行。"""
    person = _prepared()
    _functions(person)
    assert person.validate_react_decisions([_decision("start_activity", _WAIT_CHOICE)], False) == ""
    assert "No known object" in person.validate_react_decisions([
        _decision("start_activity", _choice("object-private", "interact")),
    ], False)
    for verb in ("wait", "travel", "obtain"):
        assert "ACTION_NOT_OFFERED" in person.validate_react_decisions([
            _decision("start_activity", _choice("自己家", verb)),
        ], False)
    assert "not declared" in person.validate_react_decisions([
        _decision("start_activity", {**_WAIT_CHOICE, "ways": ["等待", "睡觉"]}),
    ], False)
    person._life_action_count = 1
    assert "world request" in person.validate_react_decisions([
        _decision("start_activity", _WAIT_CHOICE),
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
    """宿主调用越界时明确失败：只读模式不提交世界请求，未知工具不分派（宿主内部错误，不是人物可触发的业务情形）。"""
    person = _prepared()
    with pytest.raises(RuntimeError, match="disabled in readonly mode"):
        await person.dispatch_react_tool("start_activity", _WAIT_CHOICE, readonly=True)
    with pytest.raises(RuntimeError, match="Unsupported LifeAgent tool dispatch"):
        await person.dispatch_react_tool("finish", {"answer": "不存在的生活决策结束工具"})
    with pytest.raises(RuntimeError, match="Unsupported LifeAgent tool dispatch"):
        await person.dispatch_react_tool("memory_step_range", {"start_step": 0, "end_step": 1, "limit": 1})


@pytest.mark.asyncio
async def test_recall_dispatch_without_an_environment_fails_closed() -> None:
    """回看由环境按冻结观察读出：人物还没有接上环境时，宿主本地校验通过的 recall_task_summary 在分派处明确失败（宿主内部
    错误，不是人物可触发的业务情形），不虚构回看结果；回看不是世界请求，失败后本轮的世界请求名额仍在。"""
    data = copy.deepcopy(_observations()[0]["data"])
    _with_activities(data, (_WORK, PHASE_ACTIVE))
    person = _prepared(data)
    recall = {"task_name": _WORK}
    assert person._env is None
    assert person.validate_react_decisions([_decision("recall_task_summary", recall)], False) == ""
    with pytest.raises(RuntimeError, match="Environment is not initialized"):
        await person.dispatch_react_tool("recall_task_summary", recall)
    assert person._life_action_count == 0


def test_tool_messages_follow_memory_queries_only() -> None:
    """记忆查询结果只序列化一次写进 tool 消息，失败写 {"error": 原因}；世界请求登记不追加任何消息；缺调用身份即失败。"""
    person = _prepared()
    person._react_messages = []
    person._append_react_tool_result(_decision("start_activity", _WAIT_CHOICE), ReactToolResult(True, "{}", {}))
    assert person._react_messages == []
    success = {"epistemic_status": "subjective", "episodes": []}
    person._append_react_tool_result(ReactDecision("", "memory_recent", {"limit": 1}, "", "call-1"),
                                     ReactToolResult(True, json.dumps(success), success))
    failure = person._dispatch_strict_memory
    person._memory_runtime = type("Memory", (), {
        "dispatch_tool": lambda self, action, args: (False, "memory unavailable", None),
    })()
    decision = ReactDecision("", "memory_search", {"query": "x", "limit": 1}, "", "call-2")
    assert person.validate_react_decisions([
        ReactDecision("", "memory_recent", {"limit": 1}, "", "call-1"), decision], False) == ""
    result = failure(decision.action, decision.args)
    person._append_react_tool_result(decision, result)
    assert json.loads(person._react_messages[0]["content"]) == success
    feedback = json.loads(person._react_messages[1]["content"])
    assert set(feedback) == {"error_code", "reason", "fix", "items", "unmet"}
    assert feedback["error_code"] == "MEMORY_QUERY_FAILED"
    assert feedback["items"][0]["path"] == ["tool_calls", 1]
    assert feedback["items"][0]["allowed_values"] == []
    with pytest.raises(RuntimeError, match="Native tool call id is unavailable"):
        person._append_react_tool_result(_decision("memory_recent", {"limit": 1}),
                                         ReactToolResult(True, json.dumps(success), success))
