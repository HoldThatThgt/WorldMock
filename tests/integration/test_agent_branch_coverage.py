"""当前工具协议的观察冻结、名称守卫、受限轮不收窄工具面与记忆查询分支。"""

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
    MemoryTimeRangeRequest,
    ReadonlyFinishRequest,
)
from tests.integration.test_life_agent_protocol import (
    _Environment,
    _GroundedCorrectionDispatcher,
    _ModelResponse,
    _NEWS,
    _TASK,
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
    (lambda data: data["interaction_bindings"][0]["actions"][0].pop("canonical_action"),
     "interaction binding action is invalid"),
    (lambda data: data["interaction_bindings"][0].update(actions=["等待"]), "interaction binding action is invalid"),
    (lambda data: data.pop("activity_bindings"), "activity_bindings are invalid"),
    (lambda data: data["task_bindings"][0].pop("task_name"), "task_bindings are invalid"),
    (lambda data: data.update(goal_bindings=None), "goal_bindings are invalid"),
    (lambda data: data.update(session_bindings=[{"session_id": "s"}]), "session_bindings are invalid"),
    (lambda data: data.update(interaction_menu={"allowed_actions": ["wait"]}), "interaction menu"),
    (lambda data: data.update(wake_reasons=[""]), "wake reasons are invalid"),
])
def test_corrupt_observation_fails_before_any_model_tool(change, match: str) -> None:
    """缺层或层名不符、看法偏好或进度层元数据损坏、私有绑定损坏、菜单或唤醒原因损坏的观察不能转成模型请求：
    都是损坏的宿主观察，在任何模型调用之前失败。"""
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
    """工具层没有目标时决策照常完成：不可见名称在本轮纠正，改提交其他动作，登记即结束本轮且不终止运行。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-unseen", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
        _ModelResponse("call-recall", "recall_task_summary",
                       json.dumps({"task_name": _TASK}, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 5, memory=False)

    result = await agent.run_react_loop(ReactLoopRequest(
        0, _context().observed_at, _empty_layer_observations()))

    assert result == "recall_task_summary registered"
    assert len(environment.calls) == 1
    assert environment.calls[0].request == RecallTaskSummaryRequest(task_name=_TASK)
    action_responses = [item for item in environment.evidence if item.phase == "response"]
    assert len(action_responses) == 2
    assert "not a visible interactive object" in action_responses[0].error
    start = next(item["function"] for item in dispatcher.requests[0]["tools"]
                 if item["function"]["name"] == "start_activity")
    properties = start["parameters"]["properties"]
    for field in ("targets", "actions", "ways"):
        assert "enum" not in properties[field]["items"]


_SUPPLY = "便利店出售的饭团"
#: 受限轮与名额已满时环境给出的原因（工具层 reason 与私有绑定 note 同一文本）。
_RESTRICTED = "同一时刻只能先开始或恢复一件事"
_SLOTS_FULL = "两件事都在做，先结束一件"


def _restricted_data(stage: str) -> dict:
    """标上同刻受限轮阶段的观察：一项进行中、一项暂停的活动占满名额；门店的到店与远程下单标不可用并写原因。"""
    data = copy.deepcopy(_observations()[0]["data"])
    data["same_moment_stage"] = stage
    _add_objects(data, _visible_object(_SUPPLY, "supply", "supply-visible",
                                       _offer("obtain", "到店", "purchase", _RESTRICTED, fulfillment="pickup"),
                                       _offer("obtain", "远程下单", "purchase", _RESTRICTED, fulfillment="delivery")),
                 _visible_object("商品目录", "catalog", "catalog",
                                 _offer("interact", "浏览", "browse", _SLOTS_FULL)))
    return _with_activities(data, (_WORK, PHASE_ACTIVE), (_NEWS, PHASE_PAUSED))


def _choice(target: str, verb: str, way: str) -> dict:
    """对一个对象选择一项「动词：方式」的 start_activity 参数。"""
    return {"targets": [target], "actions": [verb], "ways": [way]}


def _conclusion(*names: str) -> dict:
    """点名活动结束的参数（逐项自述，不写记忆）。"""
    return {"task": list(names), "summary": ["先收尾。" for _ in names], "memories": [[] for _ in names]}


def test_visible_objects_freeze_verb_way_and_bound_internal_action() -> None:
    """私有绑定冻结为「名称 → （动词，方式，内部标准动作）」；标为不可用的动作照常可选（由环境以同一原因拒绝），
    没有动作的对象照常冻结，选它按协议错误纠正。"""
    data = copy.deepcopy(_observations()[0]["data"])
    _add_objects(data, _visible_object(_SUPPLY, "supply", "supply-closed"))
    person = _prepared(data)
    assert person._life_names["objects"] == {
        "自己": (("interact", "等待", "wait"), ("sleep", "睡觉", "sleep")),
        _TASK: (("interact", "工作", "work"),),
        _SUPPLY: (),
    }
    assert data["tool_layer"]["targets"][1]["actions"][0]["available"] is False
    assert person.validate_react_decisions([
        _decision("start_activity", _choice(_TASK, "interact", "工作"))], False) == ""
    assert "not offered by the chosen target" in person.validate_react_decisions([
        _decision("start_activity", _choice(_SUPPLY, "obtain", "到店"))], False)
    assert '"enum"' not in json.dumps(_functions(person)["start_activity"])


@pytest.mark.parametrize("stage", ["open", "start_or_resume", "resume_or_conclude", "conclude_only",
                                   "conclude_pending"])
def test_restricted_rounds_do_not_narrow_the_host_tool_surface(stage: str) -> None:
    """受限轮与待收尾只由工具层 available/reason 表达：宿主工具面与常规轮逐字节相同，标不可用的动作与控制照常通过
    本地校验，交给环境以同一原因业务拒绝。"""
    person = _prepared(_restricted_data(stage))
    assert json.dumps(_functions(person)) == json.dumps(_functions(_prepared()))
    for decision in (_decision("start_activity", _choice(_SUPPLY, "obtain", "远程下单")),
                     _decision("start_activity", _choice("商品目录", "interact", "浏览")),
                     _decision("pause_activity", {"task": [_WORK], "summary": ["先停。"]}),
                     _decision("report_status", {"task": [_NEWS], "summary": ["接着看。"]}),
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
        _decision("pause_activity", {"task": [_WORK], "summary": ["先停。"]})], False) == ""
    assert "not visible" in person.validate_react_decisions([
        _decision("pause_activity", {"task": [_NEWS], "summary": ["先停。"]})], False)
    assert "recipient_name is not visible" == person.validate_react_decisions([
        _decision("send_message", {"recipient_name": "陌生人", "text": "你好"})], False)
    assert "activity-private-1" not in str(functions)
    assert "session-7-0001" not in str(functions)


def test_action_validation_rejects_unseen_object_wrong_action_and_second_action() -> None:
    """对象、逐项动词与方式的闭集、三数组等长与单次动作限制在分派前执行。"""
    person = _prepared()
    _functions(person)
    assert person.validate_react_decisions([_decision("start_activity", _WAIT_CHOICE)], False) == ""
    assert "visible interactive object" in person.validate_react_decisions([
        _decision("start_activity", _choice("object-private", "interact", "等待")),
    ], False)
    for verb, way in (("interact", "工作"), ("sleep", "等待"), ("interact", "睡觉")):
        assert "not offered" in person.validate_react_decisions([
            _decision("start_activity", _choice("自己", verb, way)),
        ], False)
    assert "equal lengths" in person.validate_react_decisions([
        _decision("start_activity", {**_WAIT_CHOICE, "ways": ["等待", "睡觉"]}),
    ], False)
    person._life_action_count = 1
    assert "already submitted" in person.validate_react_decisions([
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


def test_tool_messages_follow_memory_queries_only() -> None:
    """记忆查询结果只序列化一次写进 tool 消息，失败写 {"error": 原因}；世界请求登记不追加任何消息；缺调用身份即失败。"""
    person = _prepared()
    person._react_messages = []
    person._append_react_tool_result(_decision("start_activity", _WAIT_CHOICE), ReactToolResult(True, "{}", {}))
    assert person._react_messages == []
    success = {"epistemic_status": "subjective", "episodes": []}
    person._append_react_tool_result(ReactDecision("", "memory_recent", {"limit": 1}, "", "call-1"),
                                     ReactToolResult(True, json.dumps(success), success))
    person._append_react_tool_result(ReactDecision("", "memory_search", {"query": "x", "limit": 1}, "", "call-2"),
                                     ReactToolResult(False, "memory unavailable", {"error": "memory unavailable"}))
    assert person._react_messages == [
        {"role": "tool", "tool_call_id": "call-1", "content": '{"epistemic_status":"subjective","episodes":[]}'},
        {"role": "tool", "tool_call_id": "call-2", "content": '{"error":"memory unavailable"}'}]
    with pytest.raises(RuntimeError, match="Native tool call id is unavailable"):
        person._append_react_tool_result(_decision("memory_recent", {"limit": 1}),
                                         ReactToolResult(True, json.dumps(success), success))
