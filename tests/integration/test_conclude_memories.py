"""一轮决策恰好一个世界请求；记忆只经 conclude_activity 写入、来源由系统登记；发给人物模型的内容不含内部身份；
阈值巩固只计结束活动写入的条目。

对应 docs/dev/SPEC-time-and-energy.md 第七章「finish 并入 conclude_activity」「发给模型的内容不含内部身份」与验收台账
「会话、活动与记忆」；实施台账「七·记忆新增」。
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from itertools import islice
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.agent.runner import _step_agent_batch_async
import agentsociety2.society.society as society_module
from agentsociety2.society import AgentSociety, AgentSocietyConfig, DecisionContext, DirectToolCall
from custom.agents.life_agent import LifeAgent, life_action_tool_names
from custom.envs.life_env import LifeEnv
from tests.integration.test_life_agent_protocol import (
    _Environment,
    _GroundedCorrectionDispatcher,
    _MEMORY_TOOL_NAMES,
    _ModelResponse,
    _MultipleModelResponse,
    _TextResponse,
    _OBSERVED_AT,
    _StepDispatcher,
    _StepEnvironment,
    _WAIT,
    _WAIT_CHOICE,
    _WORK,
    _conclude_args,
    _create_step_workspace,
    _memory,
    _observations,
    _service,
    _stored_memories,
    _wire_agent,
    _with_activities,
)
from tests.integration.test_verified_memory import _runtime
from tests.life.conftest import (
    activity_name,
    advance_until_ready,
    context,
    engine_factory as _engine_factory,  # noqa: F401
    stage_day_plan,
    start_activity,
    wake_by_mail,
)
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_PENDING
from worldmock.life.models import ConcludeActivityRequest, PauseActivityRequest, REQUEST_MODELS, SameMomentStage


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


def _observed(*activities: tuple[str, str]) -> list[dict]:
    """当前停留里有给定活动的一条结构化观察记录。"""
    observations = _observations()
    _with_activities(observations[0]["data"], *activities)
    return observations


def _tool_names(requests: list[dict]) -> list[set[str]]:
    """逐个行动阶段请求读取实际开放的工具名。"""
    return [{item["function"]["name"] for item in request["tools"]} for request in requests if "tools" in request]


def _response_errors(environment: _Environment) -> list[str | None]:
    """按调用顺序读取每次模型响应的协议校验结果。"""
    return [item.error for item in environment.evidence if item.phase == "response"]


def _decision(tool: str, arguments: dict) -> ReactDecision:
    """构造一次已解析的原生工具选择。"""
    return ReactDecision("", tool, arguments, "")


@pytest.mark.parametrize("stage", [item.value for item in SameMomentStage])
def test_life_tool_surface_never_offers_finish(stage: str) -> None:
    """每个同刻阶段的生活决策工具面都是同样的八个人物工具与记忆查询，没有 finish；只读问答另有只答复的 finish。"""
    observations = _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_PENDING))
    observations[0]["data"]["same_moment_stage"] = stage
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, _OBSERVED_AT, observations))

    names = {item["function"]["name"] for item in agent.build_react_tool_schemas(readonly=False)}

    assert "finish" not in names
    assert names - _MEMORY_TOOL_NAMES == set(REQUEST_MODELS)
    assert life_action_tool_names() == frozenset(REQUEST_MODELS)
    readonly = LifeAgent()
    readonly.prepare_react_loop(ReactLoopRequest(0, _OBSERVED_AT, [], "今天过得怎么样？", True))
    functions = {item["function"]["name"]: item["function"]
                 for item in readonly.build_react_tool_schemas(readonly=True)}
    assert set(functions) == _MEMORY_TOOL_NAMES | {"finish"}
    assert set(functions["finish"]["parameters"]["properties"]) == {"answer"}


@pytest.mark.asyncio
async def test_registered_world_request_ends_the_decision_and_memory_queries_do_not() -> None:
    """记忆查询是观察，可在请求前多次使用、不结束本轮；生活决策里的 finish 零执行；世界请求登记即结束本轮。"""
    conclusion = _conclude_args(_WAIT, memories=[[_memory("等了一会儿，脑子清楚多了。")]])
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory-1", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-finish", "finish", json.dumps({"memories": []})),
        _ModelResponse("call-memory-2", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-conclude", "conclude_activity", json.dumps(conclusion, ensure_ascii=False)),
        _ModelResponse("call-never-requested", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 8)
    request = ReactLoopRequest(0, _OBSERVED_AT, _observed((_WAIT, PHASE_ACTIVE)))

    assert await agent.run_react_loop(request) == "conclude_activity registered"

    assert [call.request for call in environment.calls] == [ConcludeActivityRequest.model_validate(conclusion)]
    assert len(dispatcher.responses) == 1
    assert len([item for item in dispatcher.requests if "tools" in item]) == 4
    assert all("finish" not in names for names in _tool_names(dispatcher.requests))
    assert _response_errors(environment) == [None, "unknown native tool calls: ['finish']", None, None]
    assert len(agent._memory_runtime.calls) == 2
    assert [item["action"] for item in request.observations] == [
        "observe", "memory_recent", "memory_recent", "conclude_activity"]


@pytest.mark.asyncio
async def test_conclusion_memories_flatten_by_task_with_system_source() -> None:
    """宿主按 task 顺序展平 memories，来源固定为 activity_conclusion、认知状态 subjective、没有 refs；其他世界请求不写记忆。"""
    first = _memory("会议纪要整理完了，松了口气。", kind="resolution", importance=0.6)
    second = _memory("下次要早点动笔。", kind="goal", importance=0.4, keywords=("写作", "计划"))
    third = _memory("等人的时候心里有点急。", kind="conflict", importance=0.3)
    conclusion = _conclude_args(_WORK, _WAIT, memories=[[first, second], [third]])
    written = []
    for tool, arguments in (("conclude_activity", conclusion), ("start_activity", _WAIT_CHOICE)):
        dispatcher = _GroundedCorrectionDispatcher(responses=[
            _ModelResponse("call-action", tool, json.dumps(arguments, ensure_ascii=False))])
        agent = LifeAgent()
        _wire_agent(agent, _Environment(), dispatcher, 4, memory=False)
        request = ReactLoopRequest(0, _OBSERVED_AT, _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE)))
        assert await agent.run_react_loop(request) == f"{tool} registered"
        written.append(agent._last_finish_memories)

    assert written[0] == [{**item, "source": "activity_conclusion", "epistemic_status": "subjective"}
                          for item in (first, second, third)]
    assert written[1] == []


def test_only_conclude_activity_carries_memories() -> None:
    """结束工具逐项带记忆；播报与暂停不带记忆，多给 memories 按协议错误拒绝（第七章）。"""
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, _OBSERVED_AT, _observed((_WORK, PHASE_ACTIVE))))
    functions = {item["function"]["name"]: item["function"] for item in agent.build_react_tool_schemas(readonly=False)}
    assert set(functions["conclude_activity"]["parameters"]["properties"]) == {"task", "summary", "memories"}
    for tool in ("report_status", "pause_activity"):
        assert set(functions[tool]["parameters"]["properties"]) == {"task", "summary"}
        arguments = {"task": [_WORK], "summary": ["还在做。"], "memories": [[_memory()]]}
        error = agent.validate_react_decisions([_decision(tool, arguments)], False)
        assert f"invalid {tool} arguments" in error and "Extra inputs are not permitted" in error


def _nine_memories() -> list[list[dict]]:
    """两项合计九条记忆（超过八条上限）。"""
    texts = ("早上的阳光很好。", "路上有点堵。", "午饭吃得很满足。", "同事帮了我一把。", "下午有点犯困。",
             "会开得比预想顺利。", "晚上想早点睡。", "想给家里打个电话。", "周末想去爬山。")
    return [[_memory(text) for text in texts[:5]], [_memory(text) for text in texts[5:]]]


def _single(**changes: Any) -> dict:
    """结束一项活动，唯一一条记忆按 changes 改动字段。"""
    return _conclude_args(_WAIT, memories=[[{**_memory(), **changes}]])


_LIMIT_CASES = {
    "memories_shorter_than_task": (_conclude_args(_WORK, _WAIT, memories=[[_memory()]]), "equally sized"),
    "nine_memories_in_total": (_conclude_args(_WORK, _WAIT, memories=_nine_memories()), "exceed eight"),
    "empty_text": (_single(text=""), "should match pattern"),
    "blank_text": (_single(text="   "), "should match pattern"),
    "text_over_1000_characters": (_single(text="长" * 1001), "exceeds 1000 characters"),
    "hexadecimal_identity_in_text": (_single(text="我还记得 3e71a031bb3d 那一刻。"), "hexadecimal identifier"),
    "thirteen_keywords": (_single(keywords=[f"词{index}" for index in range(13)]), "more than twelve keywords"),
    "importance_above_one": (_single(importance=1.5), "less than or equal to 1"),
    "importance_below_zero": (_single(importance=-0.1), "greater than or equal to 0"),
    "fact_like_type": (_single(type="event"), "Input should be"),
    "model_supplied_source": (_single(source="decision_observation"), "Extra inputs are not permitted"),
    "model_supplied_refs": (_single(refs=["observation-current"]), "Extra inputs are not permitted"),
    "model_supplied_status": (_single(epistemic_status="subjective"), "Extra inputs are not permitted"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_LIMIT_CASES))
async def test_conclusion_memory_limits_are_protocol_errors_retried_in_the_same_decision(case: str) -> None:
    """memories 与 task 等长、合计不超过八条、text 非空不超 1000 字符且无十六进制身份、关键词不超 12 个、重要度 0–1：
    越界由本地请求模型拒绝，按协议错误在本轮重试，只有改正后的请求到达环境（不计同刻轮数）。"""
    invalid, message = _LIMIT_CASES[case]
    valid = _conclude_args(_WAIT)
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-invalid", "conclude_activity", json.dumps(invalid, ensure_ascii=False)),
        _ModelResponse("call-valid", "conclude_activity", json.dumps(valid, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 4, memory=False)

    result = await agent.run_react_loop(ReactLoopRequest(
        0, _OBSERVED_AT, _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))))

    assert result == "conclude_activity registered"
    assert [call.request for call in environment.calls] == [ConcludeActivityRequest.model_validate(valid)]
    error = _response_errors(environment)[0]
    assert error.startswith("invalid conclude_activity arguments") and message in error
    correction = [item for item in dispatcher.requests if "tools" in item][1]
    feedback = json.loads(correction["messages"][-1]["content"])
    assert feedback["executed"] is False and feedback["error"] == error
    assert agent._last_finish_memories == _stored_memories(valid)


def test_conclusion_memory_limits_accept_the_boundaries() -> None:
    """边界值都受理：合计恰好八条、text 恰好 1000 字符、12 个关键词、重要度 0 与 1、某项零条记忆。"""
    boundary = [
        _memory("长" * 1000),
        _memory("关键词正好十二个。", keywords=tuple(f"词{index}" for index in range(12))),
        _memory("一件不太要紧的小事。", importance=0.0),
        _memory("一件最要紧的大事。", importance=1.0),
        *(_memory(text) for text in ("晚风很舒服。", "楼下新开了面馆。", "想换个新杯子。", "明天要早起。")),
    ]
    arguments = _conclude_args(_WORK, _WAIT, memories=[boundary, []])
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, _OBSERVED_AT, _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))))
    agent.build_react_tool_schemas(readonly=False)

    assert agent.validate_react_decisions([_decision("conclude_activity", arguments)], False) == ""
    assert sum(len(group) for group in ConcludeActivityRequest.model_validate(arguments).memories) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"source": "decision_observation"}, {"source": "reflection"}, {"type": "event"}, {"epistemic_status": "objective"},
])
async def test_memory_runtime_accepts_only_system_sourced_conclusion_memories(tmp_path, override: dict) -> None:
    """记忆运行时只接受宿主按写入点固定来源的主观条目：旧 finish 来源、反思来源、事实型类型与客观状态都被拒绝。"""
    runtime, _ = _runtime(tmp_path)
    memory = {**_stored_memories(_conclude_args(_WAIT))[0], **override}

    with pytest.raises(ValueError):
        await runtime.after_step(tick=60, t=_OBSERVED_AT, step_count=1, finish_memories=[memory])

    assert runtime.store.iter_episodes() == []


class _ScriptedModel:
    """脚本化人物模型：行动请求按序返回脚本工具调用，留存发出的每个请求。

    可调用的脚本项按本次实际请求构造响应（例如从工具层取活动名）。
    """

    model_name = "deepseek-flash"

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def call(self, **request):
        """保存实际请求的独立副本并返回下一条响应。"""
        self.requests.append(copy.deepcopy(request))
        response = self.responses.pop(0)
        return response(request) if callable(response) else response


def _offered_activity(request: dict) -> str:
    """一次模型请求的工具层里唯一的活动目标名：宿主实际列给模型的活动名。"""
    tool_layer = json.loads(request["messages"][1]["content"])[0]
    [name] = [target["name"] for target in tool_layer["targets"] if target["kind"] == "activity"]
    return name


def _naming_offered(call_id: str, arguments: Callable[[str], dict]) -> Callable[[dict], _ModelResponse]:
    """脚本项：取本次请求工具层里唯一的活动目标名，按它构造收尾调用。

    名称不在脚本里写死：宿主列出的名称即使混进内部身份，模型照样点名、收尾照样完成，扫描一直走到身份断言。
    """
    def respond(request: dict) -> _ModelResponse:
        offered = _offered_activity(request)
        return _ModelResponse(call_id, "conclude_activity", json.dumps(arguments(offered), ensure_ascii=False))

    return respond


def _settle(engine, round_id: str, tool: str, request):
    """单个就绪人物按真实顺序观察、登记并结算一个世界请求，返回动作结果。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool, request))
    [result] = engine.commit_decision_round(decision.round_id)
    return result


def _environment(engine) -> LifeEnv:
    """把真实引擎接到 LifeEnv（人物经它观察、登记、留证与写记忆证据）。"""
    environment = LifeEnv()
    environment.engine = engine
    return environment


def _engine_with_pending_wait(engine_factory) -> tuple[Any, LifeEnv, str]:
    """真实引擎：人物开始一段等待并推进到等待到点，等待成为刚结束、待收尾，人物在同一秒获得决策。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _settle(engine, "wait", "start_activity", start_activity("等待", "自己"))
    assert advance_until_ready(engine)
    assert engine.state.activities[started.activity_id].status.value == "completed"
    return engine, _environment(engine), started.activity_id


def _engine_with_paused_watch_and_pending_wait(engine_factory) -> tuple[Any, LifeEnv, str, str]:
    """真实引擎：人物看了五分钟内容后暂停，接着等待到点；此刻暂停的观看与刚结束的等待各占一个名额。"""
    engine = engine_factory()
    stage_day_plan(engine)
    shown = engine._observation_data(engine.state.agents[1])
    content, way = next((row["object"], item["label"]) for row in shown["interaction_bindings"]
                        if row["kind"] == "content" for item in row["actions"] if item["canonical_action"] == "watch")
    watch = _settle(engine, "watch", "start_activity", start_activity(way, content))
    wake_by_mail(engine, offset=300)
    assert advance_until_ready(engine)
    watch_name = activity_name(engine, watch.activity_id)
    paused = _settle(engine, "pause", "pause_activity", PauseActivityRequest(task=[watch_name], summary=["先停一下。"]))
    assert paused.status.value == "completed"
    wait = _settle(engine, "wait", "start_activity", start_activity("等待", "自己"))
    assert advance_until_ready(engine)
    assert engine.state.activities[wait.activity_id].status.value == "completed"
    return engine, _environment(engine), watch.activity_id, wait.activity_id


async def _person(environment: LifeEnv, workspace: Path, model: _ScriptedModel) -> LifeAgent:
    """从全新工作区装配启用记忆的真实 LifeAgent（巩固阈值取默认值），绑定真实环境。"""
    LifeAgent.create(workspace, {"id": 1, "name": "陈曦", "occupation": "engineer"},
                     {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 8})
    service = SimpleNamespace(env=environment, llm=SimpleNamespace(default=model), trace=None)
    return await LifeAgent.from_workspace(workspace, service)


async def _decide(engine, environment: LifeEnv, person: LifeAgent, round_id: str):
    """真实人物步骤：开轮、观察、登记唯一世界请求与写记忆，然后结算；返回本轮决策身份与动作结果。"""
    decision = context(engine, round_id)
    await environment.begin_decision_round((decision,))
    person.bind_decision_context(decision)
    assert await person.step(0, decision.observed_at) == "conclude_activity registered"
    [result] = await environment.commit_decision_round(decision.round_id)
    return decision, result


def _events(engine, event_type: str, decision_id: str) -> list:
    """本轮决策写下的某类事件。"""
    return [event for event in engine.store.read_events(engine.store.last_event_seq)
            if event.event_type == event_type and event.decision_id == decision_id]


@pytest.mark.asyncio
async def test_conclusion_sources_are_registered_by_the_system(_engine_factory, tmp_path) -> None:
    """带记忆收尾：环境只校验形状并随 action_requested 留证，来源（活动、会话、已核验经历）与写入点由系统登记。"""
    engine, environment, wait_id = _engine_with_pending_wait(_engine_factory)
    name = activity_name(engine, wait_id)
    memories = [_memory("等了一刻钟，心里平静了。", importance=0.3),
                _memory("想起还没回妈妈的消息。", kind="relationship", importance=0.4)]
    conclusion = {"task": [name], "summary": ["等完了。"], "memories": [memories]}
    model = _ScriptedModel([_ModelResponse("call-conclude", "conclude_activity",
                                           json.dumps(conclusion, ensure_ascii=False))])
    person = await _person(environment, tmp_path / "agent_0001", model)

    decision, result = await _decide(engine, environment, person, "conclude")

    activity = engine.state.activities[wait_id]
    assert result.status.value == "completed"
    assert activity.status.value == "completed" and activity.concluded_at == engine.state.current_time
    [observed] = _events(engine, "observation", decision.decision_id)
    waited = [row["source_event_id"] for row in observed.data["verified_experiences"] if row["activity_id"] == wait_id]
    assert waited
    assert result.data["memory_sources"] == [{"task": name, "activity_id": wait_id,
                                              "session_id": activity.session_ids[-1], "verified_event_ids": waited}]
    [requested] = _events(engine, "action_requested", decision.decision_id)
    assert requested.data["request"]["memories"] == [memories]
    [update] = _events(engine, "memory_updated", decision.decision_id)
    assert update.data["trigger"] == ["activity_conclusion"]
    assert update.data["decision_id"] == decision.decision_id
    stored = person._memory_runtime.store.iter_episodes()
    assert set(update.data["created_episode_ids"]) == {item["id"] for item in stored}
    assert [(item["type"], item["text"], item["source"], item["epistemic_status"], item["refs"])
            for item in stored] == [(memory["type"], memory["text"], "activity_conclusion", "subjective", [])
                                    for memory in memories]


@pytest.mark.asyncio
async def test_conclusion_rejected_at_settlement_keeps_its_memories(_engine_factory, tmp_path) -> None:
    """登记后结算被拒的 conclude_activity（有刚结束的等待却只结束暂停的观看），其记忆仍作为人物自述保留，写入点记为该动作。"""
    engine, environment, watch_id, wait_id = _engine_with_paused_watch_and_pending_wait(_engine_factory)
    memories = [_memory("那段短剧还没看完，有点惦记。", importance=0.3)]
    conclusion = {"task": [activity_name(engine, watch_id)], "summary": ["不看了。"], "memories": [memories]}
    model = _ScriptedModel([_ModelResponse("call-conclude", "conclude_activity",
                                           json.dumps(conclusion, ensure_ascii=False))])
    person = await _person(environment, tmp_path / "agent_0001", model)

    decision, result = await _decide(engine, environment, person, "rejected")

    assert (result.status.value, result.error_code) == ("rejected", "ACTIVITY_CONCLUSION_REQUIRED")
    assert activity_name(engine, wait_id) in result.message
    assert engine.state.activities[watch_id].status.value == "paused"
    [requested] = _events(engine, "action_requested", decision.decision_id)
    assert requested.data["request"]["memories"] == [memories]
    [update] = _events(engine, "memory_updated", decision.decision_id)
    assert update.data["trigger"] == ["activity_conclusion"]
    assert update.data["decision_id"] == decision.decision_id
    stored = person._memory_runtime.store.iter_episodes()
    assert [(item["text"], item["source"], item["epistemic_status"]) for item in stored] == [
        (memories[0]["text"], "activity_conclusion", "subjective")]
    assert update.data["created_episode_ids"] == [stored[0]["id"]]


#: 八位以上的十六进制串（事件身份、摘要、条目编号中的长数字段）。
_HEX_RUN = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8,}(?![0-9A-Za-z])")
#: 场景与事实清单按时间合同披露的 epoch 秒是时刻，不是身份：只放行本轮时刻前后一年内的纯数字串。
_TIME_WINDOW_SECONDS = 366 * 86400
#: 以 _id 或 _ids 结尾的 JSON 键。
_ID_KEY = re.compile(r'"([A-Za-z0-9_]*_ids?)"\s*:')


def _private_identities(value: Any, key: str = "") -> set[str]:
    """递归收集以 _id、_ids 结尾或名为 id 的键所带的身份值（只取含数字或分隔符、不是普通单词的串）。"""
    if isinstance(value, dict):
        return set().union(*(_private_identities(child, name) for name, child in value.items()))
    if isinstance(value, list):
        return set().union(*(_private_identities(child, key) for child in value))
    identity_key = key == "id" or key.endswith("_id") or key.endswith("_ids")
    return {value} if identity_key and isinstance(value, str) and re.search(r"[\d:_-]", value) else set()


def _time_value(run: str, now: int) -> bool:
    """命中的纯数字串是否为本轮前后一年内的 epoch 秒（例如场景的「Unix epoch：当前 …」与任务截止时刻）。"""
    return run.isdigit() and abs(int(run) - now) <= _TIME_WINDOW_SECONDS


def _model_visible(messages: list[dict], tools: list[dict]) -> list[str]:
    """模型能读到的全部文本：消息正文、思考字段、回传的工具调用名与参数、工具 Schema。

    tool_call_id 与工具调用的 id 是协议句柄，不属于内部身份（第七章「发给模型的内容不含内部身份」）。
    """
    texts = [json.dumps(tools, ensure_ascii=False)]
    for message in messages:
        texts.extend((str(message.get("content") or ""), str(message.get("reasoning_content") or "")))
        texts.extend(part for call in message.get("tool_calls") or []
                     for part in (call["function"]["name"], call["function"]["arguments"]))
    return texts


def _identity_leaks(text: str, private: set[str], now: int) -> list[str]:
    """一段模型可见文本中的内部身份：十六进制串（时刻秒数除外）、*_id 键与本轮私有身份值。"""
    leaks = [match.group() for match in _HEX_RUN.finditer(text) if not _time_value(match.group(), now)]
    leaks.extend(_ID_KEY.findall(text))
    leaks.extend(identity for identity in private if identity in text)
    return leaks


@pytest.mark.asyncio
async def test_nothing_sent_to_the_person_model_carries_an_internal_identity(_engine_factory, tmp_path) -> None:
    """系统层、六层 user、工具 Schema 与工具返回（记忆查询、协议纠错）中都没有内部身份；登记后没有工具返回。

    模型按工具层实际列出的活动名点名收尾，名称里混进的内部身份由身份断言检出，而不是让脚本对话提前中断。"""
    engine, environment, wait_id = _engine_with_pending_wait(_engine_factory)
    name = activity_name(engine, wait_id)
    model = _ScriptedModel([
        _ModelResponse("call-recent", "memory_recent", '{"limit":5}'),
        _ModelResponse("call-search", "memory_search", json.dumps({"query": "散步", "limit": 5}, ensure_ascii=False)),
        _naming_offered("call-mismatch", lambda offered: {"task": [offered], "summary": ["等完了。"], "memories": []}),
        _naming_offered("call-conclude", lambda offered: _conclude_args(
            offered, memories=[[_memory("等完这一刻钟，决定出门散步。", importance=0.2)]])),
    ])
    person = await _person(environment, tmp_path / "agent_0001", model)
    store = person._memory_runtime.store
    earlier = [_memory("晚饭后在小区里散步很舒服。", kind="preference", importance=0.2),
               _memory("答应周末陪朋友去散步。", kind="commitment", importance=0.2)]
    store.append_episodes(_stored_memories(_conclude_args("散步（1月4日 19:00 起）", memories=[earlier])),
                          tick=0, t=_OBSERVED_AT - timedelta(hours=10), step_count=0)

    decision, result = await _decide(engine, environment, person, "scan")

    assert result.status.value == "completed"
    [observed] = _events(engine, "observation", decision.decision_id)
    private = _private_identities(observed.data) | {item["id"] for item in store.iter_episodes()} | {
        observed.event_id, decision.run_id, decision.round_id, decision.decision_id,
        f"{decision.decision_id}:action:0001"}
    now = observed.data["current_time"]
    raw = _identity_leaks(json.dumps(observed.data, ensure_ascii=False), private, now)
    assert {wait_id, "activity_id", "source_event_id"} <= set(raw) and name not in private
    assert len(model.requests) == 4
    visible = [text for request in model.requests
               for text in _model_visible(request["messages"], request.get("tools", []))]
    visible.extend(_model_visible(person._react_messages, []))
    assert [leak for text in visible for leak in _identity_leaks(text, private, now)] == []
    assert any(observed.data["environment_layer"]["observed_at"] in text for text in visible)
    returned = [json.loads(message["content"]) for message in person._react_messages if message.get("role") == "tool"]
    episodes = [episode for payload in returned for episode in payload.get("episodes", [])]
    assert len(episodes) >= 3
    assert all(set(episode) == {"type", "text", "time", "epistemic_status"} for episode in episodes)
    assert any(payload.get("executed") is False for payload in returned)
    assert len(returned) == 3 and person._react_messages[-1]["role"] == "assistant"
    assert f'"name":"{name}","kind":"activity","phase":"刚结束"' in model.requests[0]["messages"][1]["content"]
    assert [_offered_activity(request) for request in model.requests[2:]] == [name, name]


@pytest.mark.asyncio
async def test_memory_queries_and_the_reply_body_reach_the_next_progress_layer(_engine_factory, tmp_path) -> None:
    """真实宿主证据进入同一段停留的下一次进度层：记忆查询只写条数，收尾调用带回复正文 self_note，
    参数略去 relationship、preference 记忆；记忆条目正文不出现在进度层。"""
    engine, environment, wait_id = _engine_with_pending_wait(_engine_factory)
    name = activity_name(engine, wait_id)
    memories = [_memory("等完这一刻钟，心里踏实了。", importance=0.2),
                _memory("想给老朋友打个电话。", kind="relationship", importance=0.3)]
    conclusion = {"task": [name], "summary": ["等完了。"], "memories": [memories]}
    model = _ScriptedModel([
        _ModelResponse("call-recent", "memory_recent", '{"limit":5}'),
        _ModelResponse("call-conclude", "conclude_activity", json.dumps(conclusion, ensure_ascii=False),
                       content="  等够了，收个尾。 "),
    ])
    person = await _person(environment, tmp_path / "agent_0001", model)
    person._memory_runtime.store.append_episodes(
        _stored_memories(_conclude_args("散步（1月4日 19:00 起）", memories=[[_memory("散步很舒服。")]])),
        tick=0, t=_OBSERVED_AT - timedelta(hours=10), step_count=0)
    decision, result = await _decide(engine, environment, person, "queried")
    assert result.status.value == "completed"
    [requested] = _events(engine, "action_requested", decision.decision_id)
    assert requested.data["self_note"] == "等够了，收个尾。"

    after = context(engine, "after-query")
    engine.begin_decision_round([after])
    records = [event for event in engine.observe(after)["progress_layer"]["events"] if event["type"] == "tool_call"]
    query = next(record for record in records if record["tool"] == "memory_recent")
    assert query["arguments"] == {"limit": 5}
    assert (query["result"]["status"], query["result"]["details"]) == ("completed", {"episodes": 1})
    assert "散步很舒服" not in json.dumps(records, ensure_ascii=False)
    concluded = next(record for record in records if record["tool"] == "conclude_activity")
    assert concluded["self_note"] == "等够了，收个尾。"
    assert concluded["arguments"]["memories"] == [[memories[0]]]


def _society(monkeypatch, tmp_path: Path, environment: _StepEnvironment, dispatcher) -> AgentSociety:
    """用真实 AS2 runner 在进程内执行人物批次的 Society（只替换模型传输与环境）。"""
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("WORKSPACE_PATH", str(project_root))

    def run_actual_batch(request, proxy):
        async def done():
            return {"results": await _step_agent_batch_async(request, proxy), "token_stats": {}}

        return done()

    monkeypatch.setattr(society_module, "step_agent_batch",
                        type("Task", (), {"remote": staticmethod(run_actual_batch)})())
    config = AgentSocietyConfig(
        run_id="run", agent_specs=({"id": 7, "profile": {}, "config": {}},), agent_class_name="LifeAgent",
        env_router=environment, service_proxy=_service(environment, dispatcher),
        start_time=environment.current_time, end_time=environment.current_time + timedelta(hours=1),
        run_dir=tmp_path, custom_root=project_root, workspace_mode="restored")
    society = AgentSociety(config)
    society._initialized = True
    society._environment_started = True
    return society


@pytest.mark.asyncio
async def test_budget_exhausted_without_a_registered_world_request_fails_the_run(monkeypatch, tmp_path) -> None:
    """回合预算耗尽仍无登记的世界请求即运行故障：Society 中止本轮、登记运行失败，不结算、不写记忆、不建检查点。"""
    _create_step_workspace(tmp_path / "as2" / "agents" / "agent_0007")
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    dispatcher.responses = [_ModelResponse(f"call-memory-{index}", "memory_recent", '{"limit":1}')
                            for index in range(4)]
    society = _society(monkeypatch, tmp_path, environment, dispatcher)

    with pytest.raises(RuntimeError, match="Agent decision failed"):
        await society.run()

    assert environment.calls == []
    assert environment.memory_updates == []
    assert environment.commits == []
    assert environment.checkpoints == []
    assert _response_errors(environment) == [None, None, None, "unknown native tool calls: ['memory_recent']"]
    assert len(environment.aborts) == 1
    assert "ReAct decision budget exhausted" in environment.aborts[0][1]
    assert len(environment.failures) == 1
    assert "ReAct decision budget exhausted" in environment.failures[0]


@pytest.mark.asyncio
async def test_readonly_finish_batched_with_another_tool_is_rejected() -> None:
    """只读问答的 finish 必须单独返回：与记忆查询同批时整批零执行、按协议错误重试，单独的 finish 才给出答案。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _MultipleModelResponse([("call-memory", "memory_recent", '{"limit":1}'),
                                ("call-finish", "finish", '{"answer":"同批的答复"}')]),
        _ModelResponse("call-finish-alone", "finish", json.dumps({"answer": "我记得昨天等过人。"}, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 3)

    answer = await agent.run_react_loop(ReactLoopRequest(0, _OBSERVED_AT, [], "昨天做了什么？", True))

    assert answer == "我记得昨天等过人。"
    assert agent._memory_runtime.calls == []
    assert environment.calls == []
    assert _response_errors(environment) == ["finish must be the only tool call in a response", None]
    feedback = [json.loads(message["content"]) for message in dispatcher.requests[1]["messages"]
                if message.get("role") == "tool"]
    assert [item["executed"] for item in feedback] == [False, False]


#: 结束活动写下的主观记忆（类型各异、正文互不相似，避免按相似度合并）。
_CONCLUSION_MEMORIES = (
    ("routine", "晚上把碗洗了，厨房总算清爽。"),
    ("goal", "想在月底前读完那本小说。"),
    ("relationship", "给妈妈打电话时她声音很精神。"),
    ("preference", "比起外卖，我还是喜欢自己煮面。"),
    ("commitment", "答应了同事周五前交方案初稿。"),
    ("conflict", "和房东为了热水器又争了几句。"),
    ("resolution", "决定以后每周三晚上去游泳。"),
    ("identity", "我越来越像个能自己拿主意的人。"),
    ("routine", "早起先喝一杯温水已经成了习惯。"),
    ("goal", "这个季度想把英语口语练起来。"),
    ("relationship", "楼下的邻居送了我一袋橘子。"),
    ("preference", "下雨天最适合窝在沙发上听播客。"),
)
#: 就寝反思的五条洞见（AS2 固定重要度 0.8，合计 4.0，已超过重要度阈值）。
_INSIGHTS = ("我其实很在意家里整不整洁。", "和家人的联系让我踏实。", "工作上的承诺我都记在心里。",
             "独处的时候我最放松。", "我想让日子过得更有规律。")


class _DayModel:
    """脚本化人物模型：按请求形状回答收尾请求、阈值巩固与就寝反思，并留存巩固载荷。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.conclusions: list[dict] = []
        self.consolidations: list[dict] = []
        self.reflections = 0

    async def call(self, **request):
        """行动请求取下一条收尾；其余是记忆运行时的反思或巩固。"""
        if "tools" in request:
            arguments = self.conclusions.pop(0)
            return _ModelResponse(f"call-{len(self.conclusions)}", "conclude_activity",
                                  json.dumps(arguments, ensure_ascii=False))
        user = request["messages"][-1]["content"]
        if "memory_reflection_input" in user:
            self.reflections += 1
            body = user.split("<memory_reflection_input>", 1)[1].split("</memory_reflection_input>", 1)[0]
            cited = json.loads(body)["episodes"][0]["id"]
            return _TextResponse(json.dumps(
                [{"insight": text, "refs": [cited]} for text in _INSIGHTS], ensure_ascii=False))
        self.consolidations.append(json.loads(user))
        return _TextResponse("我在意把日子过得有条理。")


class _DayEnvironment(_StepEnvironment):
    """一日里的决策观察：两项进行中的活动可以带记忆结束；已有当日计划；醒来那一轮的唤醒原因含 wake_from_sleep。"""

    def __init__(self) -> None:
        super().__init__()
        self.wake_reasons: list[str] = []

    async def observe(self, context: DecisionContext) -> dict:
        """在一项进行中等待的观察上再加一项进行中的工作，并写入本轮唤醒原因。"""
        data = _with_activities(await super().observe(context), (_WORK, PHASE_ACTIVE))
        data["wake_reasons"] = list(self.wake_reasons)
        data["todo_layer"]["day_plan"] = {"date": "2026-01-05", "goals": []}
        return data


@pytest.mark.asyncio
async def test_threshold_consolidation_counts_conclusion_entries_but_not_bedtime_reflection(tmp_path) -> None:
    """就寝反思之后，经结束活动写满 10 条、重要度合计 2.0（低于 3.0）时恰好触发一次巩固；此前的反思条目不计入。

    巩固载荷每条只有 {type, text, time}，不带条目身份（第七章「finish 并入 conclude_activity」「发给模型的内容不含内部身份」）。
    """
    workspace = tmp_path / "agent_0007"
    LifeAgent.create(workspace, {"id": 7, "name": "Alice", "occupation": "engineer"},
                     {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 4})
    environment, model = _DayEnvironment(), _DayModel()
    agent = await LifeAgent.from_workspace(workspace, _service(environment, model))
    texts = iter(_CONCLUSION_MEMORIES)
    days = ((["wake_from_sleep"], ((_WAIT, 1),)), ([], ((_WORK, 4), (_WAIT, 4))), ([], ((_WORK, 2),)),
            ([], ((_WAIT, 1),)))
    unconsolidated_after_step = []
    for index, (wake, groups) in enumerate(days, start=1):
        environment.wake_reasons = wake
        memories = [[_memory(text, kind=kind, importance=0.2) for kind, text in islice(texts, count)]
                    for _, count in groups]
        model.conclusions.append(_conclude_args(*(name for name, _ in groups), memories=memories))
        agent.bind_decision_context(DecisionContext("run", f"round-{index}", f"decision-{index}", 7, _OBSERVED_AT, 3))
        assert await agent.step(0, _OBSERVED_AT) == "conclude_activity registered"
        unconsolidated_after_step.append(agent._memory_runtime.store.unconsolidated())

    assert [update.consolidated for update in environment.memory_updates] == [True, False, True, False]
    assert model.reflections == 1
    assert [payload["reason"] for payload in model.consolidations] == ["sleep boundary", "pending episode threshold"]
    after_eight = unconsolidated_after_step[1]
    reflections = [item for item in after_eight if item["source"] == "reflection"]
    assert len(after_eight) == 13 and len(reflections) == 5
    assert sum(item["importance"] for item in reflections) >= 3.0
    threshold = model.consolidations[1]["new_subjective_episodes"]
    assert len(threshold) == 15
    assert all(set(item) == {"type", "text", "time"} for item in threshold)
    stored = agent._memory_runtime.store.iter_episodes()
    importance = {item["text"]: item["importance"] for item in stored}
    counted = [item for item in threshold if item["type"] != "reflection"]
    assert len(counted) == 10
    assert sum(importance[item["text"]] for item in counted) == pytest.approx(2.0)
    assert Counter(item["source"] for item in stored) == {"activity_conclusion": 12, "reflection": 5}
    assert [len(update.created_episode_ids) for update in environment.memory_updates] == [6, 8, 2, 1]
