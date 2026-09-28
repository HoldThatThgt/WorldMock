"""同秒续接的验收（docs/dev/SPEC-persona-prompt.md 第二章「同秒续接」、第四章第 0 步、第七章「清单」）。

真实 LifeEngine、LifeEnv 与 SQLite 上，脚本化模型经真实 LifeAgent 决策：同一秒的后续决策原样续接上一决策末次请求的
全部消息，追加上一决策的 assistant（原样带思考）与一条 tool 消息 {result, changes, events}；他人同秒发来的消息在整个
请求中只出现一次；时间推进后请求只有两条消息、不带此前的思考；出发后同秒、反事实分支、缺思考、证据缺失时断链；
从中秒检查点普通恢复与不中断运行的请求相同；开关关闭时每次决策首个请求恰好两条消息；开关冻结进清单与人物配置，
恢复继承、不能覆盖；预算达到阈值时先结束续接再浓缩。
"""

from __future__ import annotations

import copy
import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.agents.life_continuation import layer_changes, send_check  # noqa: E402
from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.integration.test_life_agent_protocol import _ModelResponse  # noqa: E402
from tests.life.conftest import (advance_until_ready, context, engine_factory as _engine_factory,  # noqa: E402,F401
                                 pending_names, stage_day_plan, start_activity)
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.continuation import _session_changed  # noqa: E402
from worldmock.life.engine import LifeEngine  # noqa: E402
from worldmock.life.interaction_menu import WAYS  # noqa: E402
from worldmock.life.models import CounterfactualIntervention, ModelIdentity, RunIdentity  # noqa: E402
from worldmock.life.prompt_capacity import ProgressBudget, fit_request  # noqa: E402
from worldmock.life.scenario import load_scenario  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenarios" / "white-collar-12-weeks.json"
#: 上一决策修订今日计划（瞬时请求，人物同一秒再决策）；思考里提到咖啡。
_PLAN = {"goals": [{"intent_kind": "休息", "goal": "下午喝杯咖啡", "remind_time": None}]}
_WAIT = {"targets": ["自己"], "actions": ["interact"], "ways": ["等待"]}
_THINK_PLAN = "上午有点困，先把下午喝咖啡写进计划。"
_THINK_WAIT = "计划写好了，先歇一会儿。"
_NOTE = "先把计划写上。"


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证请求合同。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


class _Script:
    """只替换远程模型传输：按顺序返回预定响应，记录实际发送的请求。"""

    model_name = "deepseek-flash"

    def __init__(self, responses: list) -> None:
        self.requests: list[dict] = []
        self.responses = list(responses)

    async def call(self, **request):
        """保存真正发送的请求，返回下一个提供商形状响应。"""
        self.requests.append(copy.deepcopy(request))
        return self.responses.pop(0)


def _reply(call_id: str, tool: str, arguments: dict, reasoning: str | None, content: str = "") -> _ModelResponse:
    """一次模型响应：思考、回复正文与一个工具调用。"""
    return _ModelResponse(call_id, tool, json.dumps(arguments, ensure_ascii=False),
                          reasoning_content=reasoning, content=content)


def _person(environment: LifeEnv, ctx, dispatcher: _Script) -> LifeAgent:
    """每次决策都重建人物（与 AS2 从工作区重建相同），绑定真实环境与本轮宿主身份。"""
    person = LifeAgent()
    person._id = ctx.agent_id
    person._name = f"人物{ctx.agent_id}"
    person._profile = {"id": ctx.agent_id, "name": f"人物{ctx.agent_id}", "same_second_continuation": True}
    person._env = environment
    person._dispatcher = dispatcher
    person._model_name = "deepseek-flash"
    person._decision_context = ctx
    person._max_react_turns = 4
    person._enable_memory = False
    person.trace_span = lambda *args, **kwargs: nullcontext(SimpleNamespace(attributes={}))
    return person


async def _decide(environment: LifeEnv, ctx, responses) -> tuple[dict, list[dict], LifeAgent]:
    """一次真实观察与人物循环；responses 是响应列表，或按本次观察给出响应列表的函数。"""
    observation = await environment.observe(ctx)
    dispatcher = _Script(responses(observation) if callable(responses) else responses)
    person = _person(environment, ctx, dispatcher)
    await person.run_react_loop(ReactLoopRequest(0, ctx.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation}]))
    return observation, dispatcher.requests, person


async def _single(environment: LifeEnv, round_id: str, responses) -> tuple[dict, list[dict], LifeAgent]:
    """单人决策轮：观察、决策并结算。"""
    engine = environment.engine
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    decided = await _decide(environment, ctx, responses)
    engine.commit_decision_round(round_id)
    return decided


def _environment(engine: LifeEngine) -> LifeEnv:
    """把真实引擎挂到 LifeEnv。"""
    environment = LifeEnv()
    environment.engine = engine
    return environment


def _plan_reply(reasoning: str | None = _THINK_PLAN) -> list:
    """修订今日计划的一次响应（带回复正文）。"""
    return [_reply("call-plan", "revise_day_plan", _PLAN, reasoning, f"  {_NOTE}  ")]


def _wait_reply(call_id: str = "call-wait", reasoning: str | None = _THINK_WAIT) -> list:
    """开始等待的一次响应。"""
    return [_reply(call_id, "start_activity", _WAIT, reasoning)]


def _stored_observation(engine: LifeEngine, observation: dict):
    """本次观察落库的观察事件。"""
    return next(event for event in engine.store.read_agent_events("observation", 1)
                if event.event_id == observation["observation_event_id"])


def _person_name(observation: dict, agent_id: int) -> str:
    """观察私有绑定中某位同世界人物的可读名称。"""
    return next(row["object"] for row in observation["interaction_bindings"]
                if row["kind"] == "person" and row["object_id"] == f"agent:{agent_id}")


async def _plan_then_wait(engine: LifeEngine) -> dict:
    """同一秒两次决策：先修订今日计划，再（续接）开始等待。"""
    stage_day_plan(engine)
    environment = _environment(engine)
    first_observation, first, planner = await _single(environment, "plan", _plan_reply())
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert engine.state.current_time == int(first_observation["current_time"])
    second_observation, second, _ = await _single(environment, "wait", _wait_reply())
    return {"environment": environment, "first": first, "planner": planner,
            "observation": second_observation, "second": second}


@pytest.mark.asyncio
async def test_the_continued_request_starts_with_the_previous_request_byte_for_byte(_engine_factory) -> None:
    """第二次决策的前缀等于上一决策末次请求的全部消息（逐字节）；assistant 原样带思考与回复正文；tool 消息含 result
    与 changes，不含登记确认与内部身份；链首 user 与原生工具字节不变；续接链不存进观察事件。"""
    engine = _engine_factory()
    run = await _plan_then_wait(engine)
    [previous], [request] = run["first"], run["second"]
    prefix = previous["messages"]
    assert len(request["messages"]) == len(prefix) + 2
    assert json.dumps(request["messages"][:len(prefix)], ensure_ascii=False) == json.dumps(prefix, ensure_ascii=False)
    assistant, tool = request["messages"][len(prefix):]
    assert assistant == run["planner"]._react_messages[-1]
    assert (assistant["reasoning_content"], assistant["content"]) == (_THINK_PLAN, f"  {_NOTE}  ")
    assert assistant["tool_calls"][0]["function"]["name"] == "revise_day_plan"
    assert (tool["role"], tool["tool_call_id"]) == ("tool", "call-plan")
    payload = json.loads(tool["content"])
    assert list(payload) == ["result", "changes"]
    observation = run["observation"]
    [record] = [row for row in observation["progress_layer"]["events"] if row.get("tool") == "revise_day_plan"]
    assert payload["result"] == record["result"] and payload["result"]["status"] == "completed"
    assert payload["changes"] == {"day_plan": observation["todo_layer"]["day_plan"]}
    receipt_fields = ("action_id", "decision_id", "round_id", "request_sha256", "registered", "plan:")
    assert not any(field in tool["content"] for field in receipt_fields)
    assert request["messages"][1] == previous["messages"][1]
    assert request["tools"] == previous["tools"]
    assert observation["continuation"]["tool_call_id"] == "call-plan"
    assert "continuation" not in _stored_observation(engine, observation).data
    planned = engine.store.read_agent_events("observation", 1)[-2]
    assert planned.decision_id == "decision-plan-1" and not _session_changed(engine.store, engine.state, 1, planned)


@pytest.mark.asyncio
async def test_a_three_decision_chain_keeps_every_prefix_and_reports_each_change_once(_engine_factory) -> None:
    """同一秒三次决策：第三次的前缀逐字节等于第二次的请求（其中含第一次的 assistant 与 tool 消息）；第一次改动的今日
    计划已由第一条 tool 消息交还，第二条 tool 消息没有新改动时省略 changes。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", _plan_reply())

    def recall(observation: dict) -> list:
        session = observation["session_bindings"][0]["name"]
        return [_reply("call-recall", "recall_task_summary", {"task_name": session}, "回看一下这段停留。")]

    _, recalled, _ = await _single(environment, "recall", recall)
    observation, [third], _ = await _single(environment, "wait", _wait_reply())
    [second] = recalled
    size = len(second["messages"])
    assert json.dumps(third["messages"][:size], ensure_ascii=False) == json.dumps(second["messages"], ensure_ascii=False)
    first_tool, last_tool = second["messages"][3], third["messages"][-1]
    assert list(json.loads(first_tool["content"])) == ["result", "changes"]
    assert list(json.loads(last_tool["content"])) == ["result"]
    assert (third["messages"][size]["reasoning_content"], last_tool["tool_call_id"]) == ("回看一下这段停留。",
                                                                                          "call-recall")
    assert observation["continuation"]["messages"][1] == third["messages"][1]


@pytest.mark.asyncio
async def test_another_persons_same_second_message_appears_once_in_the_whole_request(_engine_factory) -> None:
    """他人同秒发来的消息只在续接的 tool 消息 events 中出现一次（带全文），链首快照里没有它。"""
    engine = _engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    environment = _environment(engine)
    text = "我在楼下，给你带了杯咖啡。"
    scripts = {1: lambda observation: _wait_reply("call-1"),
               2: lambda observation: [_reply("call-2", "send_message", {
                   "recipient_name": _person_name(observation, 1), "text": text}, "告诉她咖啡到了。")]}
    await _round(environment, "meet", scripts)
    requests = await _round(environment, "reply", {1: lambda observation: _wait_reply("call-1b"),
                                                   2: lambda observation: _wait_reply("call-2b")})
    request = requests[1][0]
    assert json.dumps(request["messages"], ensure_ascii=False).count(text) == 1
    assert text not in request["messages"][1]["content"]
    events = json.loads(request["messages"][-1]["content"])["events"]
    assert [(row["type"], row["kind"], row["text"]) for row in events] == [("message_arrival", "direct", text)]


@pytest.mark.asyncio
async def test_a_message_already_returned_in_the_chain_is_not_repeated_by_a_later_tool_message(
        _engine_factory) -> None:
    """同一秒三次决策：他人的消息在第一次决策之后到达，由第二次决策的 tool 消息交还；第三次决策的 tool 消息只写第二次
    观察之后同秒发生的事，不再重复它，消息全文在整个请求中仍只出现一次（「唯一出现原则」、tool 消息 events）。"""
    engine = _engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    environment = _environment(engine)
    text = "我在楼下，给你带了杯咖啡。"
    await _round(environment, "meet", {1: lambda observation: _plan_reply(),
                                       2: lambda observation: [_reply("call-2", "send_message", {
                                           "recipient_name": _person_name(observation, 1), "text": text},
                                           "告诉她咖啡到了。")]})

    def recall(observation: dict) -> list:
        session = observation["session_bindings"][0]["name"]
        return [_reply("call-recall", "recall_task_summary", {"task_name": session}, "回看一下这段停留。")]

    await _round(environment, "recall", {1: recall, 2: lambda observation: _wait_reply("call-2b")})
    assert engine.scheduling_state().ready_agent_ids == (1,)
    requests = await _round(environment, "wait", {1: lambda observation: _wait_reply("call-1c")})
    [request] = requests[1]
    assert json.dumps(request["messages"], ensure_ascii=False).count(text) == 1
    returned = json.loads(request["messages"][3]["content"])
    assert [(row["type"], row["text"]) for row in returned["events"]] == [("message_arrival", text)]
    assert "events" not in json.loads(request["messages"][-1]["content"])


async def _round(environment: LifeEnv, round_id: str, scripts: dict) -> dict[int, list[dict]]:
    """多人决策轮：按调度顺序逐人观察与决策，结算后返回各人实际发送的请求。"""
    engine = environment.engine
    contexts = [context(engine, round_id, agent_id) for agent_id in engine.scheduling_state().ready_agent_ids]
    engine.begin_decision_round(contexts)
    requests = {}
    for ctx in contexts:
        _, requests[ctx.agent_id], _ = await _decide(environment, ctx, scripts[ctx.agent_id])
    engine.commit_decision_round(round_id)
    return requests


@pytest.mark.asyncio
async def test_after_time_advances_the_request_has_no_native_turns_or_earlier_thinking(_engine_factory) -> None:
    """时间推进后：请求只有 system 与 user，没有原生 assistant/tool 回合，上一秒的思考原文不出现；进度层带上一秒的调用
    与 self_note。"""
    engine = _engine_factory()
    run = await _plan_then_wait(engine)
    started_at = engine.state.current_time
    assert advance_until_ready(engine) and engine.state.current_time > started_at

    def conclude(observation: dict) -> list:
        names = pending_names(observation)
        return [_reply("call-end", "conclude_activity", {"task": names, "summary": ["歇好了。" for _ in names],
                                                        "memories": [[] for _ in names]}, "等完了，收尾。")]

    observation, [request], _ = await _single(run["environment"], "later", conclude)
    assert observation["continuation"] is None
    assert [item["role"] for item in request["messages"]] == ["system", "user"]
    text = json.dumps(request, ensure_ascii=False)
    assert _THINK_PLAN not in text and _THINK_WAIT not in text
    progress = json.loads(request["messages"][1]["content"])[5]
    assert progress == observation["progress_layer"]
    calls = {row["tool"]: row for row in progress["events"] if row["type"] == "tool_call"}
    assert calls["revise_day_plan"]["self_note"] == _NOTE and "start_activity" in calls


@pytest.mark.asyncio
async def test_a_same_second_departure_breaks_the_chain(_engine_factory) -> None:
    """出发后同秒（对方的消息使人物同秒再决策）：session 已切换为路程，断链，首个请求从新快照构造。"""
    engine = _engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    environment = _environment(engine)

    def leave(observation: dict) -> list:
        target, way = next((row["object"], action["label"]) for row in observation["interaction_bindings"]
                           if row["kind"] == "location" for action in row["actions"] if action["label"] == "步行")
        return [_reply("call-leave", "start_activity", {"targets": [target], "actions": [WAYS[way][0]],
                                                         "ways": [way]}, "出门去。")]

    scripts = {1: leave, 2: lambda observation: [_reply("call-2", "send_message", {
        "recipient_name": _person_name(observation, 1), "text": "路上小心。"}, "提醒她。")]}
    await _round(environment, "leave", scripts)
    assert 1 in engine.scheduling_state().ready_agent_ids
    contexts = [context(engine, "on-road", agent_id) for agent_id in engine.scheduling_state().ready_agent_ids]
    engine.begin_decision_round(contexts)
    observation = engine.observe(next(item for item in contexts if item.agent_id == 1))
    assert observation["environment_layer"]["location"]["session_kind"] == "transit"
    assert observation["continuation"] is None
    left = engine.store.read_agent_events("observation", 1)[-2]
    assert left.decision_id == "decision-leave-1" and _session_changed(engine.store, engine.state, 1, left)


def _checkpoint(engine: LifeEngine):
    """以占位人物快照提交一个共同检查点（同一秒两轮之间）。"""
    snapshots = {f"agent:{agent_id}": engine.store.assets.put_json({"agent_id": agent_id}, {"kind": "continuation"})
                 for agent_id in engine.store.manifest["agent_ids"]}
    return engine.checkpoint(snapshots)


def _branch(engine: LifeEngine, checkpoint, destination: Path, metadata: dict | None = None) -> LifeEngine:
    """从检查点恢复一个分支并打开。"""
    engine.store.restore(checkpoint.checkpoint_id, destination, metadata).close()
    return LifeEngine.open(SCENARIO, destination)


@pytest.mark.asyncio
async def test_a_plain_resume_from_a_mid_second_checkpoint_sends_the_same_request(_engine_factory, tmp_path) -> None:
    """从同一秒两次决策之间的检查点普通恢复：续接照常，恢复分支发出的请求与不中断运行逐字节相同。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    await _single(_environment(engine), "plan", _plan_reply())
    checkpoint = _checkpoint(engine)
    _, uninterrupted, _ = await _single(_environment(engine), "wait", _wait_reply())
    resumed = _branch(engine, checkpoint, tmp_path / "resumed")
    try:
        assert resumed.store.manifest["branch_kind"] == "resume"
        observation, restored, _ = await _single(_environment(resumed), "wait", _wait_reply())
        assert observation["continuation"] is not None
        assert json.dumps(restored, ensure_ascii=False) == json.dumps(uninterrupted, ensure_ascii=False)
    finally:
        resumed.close()


@pytest.mark.asyncio
async def test_a_counterfactual_branch_breaks_the_chain(_engine_factory, tmp_path) -> None:
    """反事实分支一律断链：干预之后的首次决策从新快照构造。"""
    from worldmock.runtime import _intervention_sha256

    engine = _engine_factory()
    stage_day_plan(engine)
    first, _, _ = await _single(_environment(engine), "plan", _plan_reply())
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=123_456, hypothesis="More money changes the plan.",
        source_event_ids=(first["observation_event_id"],))
    metadata = {"branch_kind": "counterfactual", "intervention_sha256": _intervention_sha256(intervention)}
    branch = _branch(engine, _checkpoint(engine), tmp_path / "counterfactual", metadata)
    try:
        branch.apply_counterfactual(intervention)
        observation, [request], _ = await _single(_environment(branch), "wait", _wait_reply())
        assert observation["continuation"] is None
        assert [item["role"] for item in request["messages"]] == ["system", "user"]
    finally:
        branch.close()


@pytest.mark.asyncio
async def test_missing_thinking_breaks_the_chain(_engine_factory) -> None:
    """上一决策的最终 assistant 没有 reasoning_content：断链，下一决策首个请求恰好两条消息。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", _plan_reply(reasoning=None))
    observation, [request], _ = await _single(environment, "wait", _wait_reply())
    assert observation["continuation"] is None and len(request["messages"]) == 2


@pytest.mark.asyncio
async def test_missing_model_evidence_breaks_the_chain(_engine_factory) -> None:
    """上一决策没有模型证据（引擎直驱登记）：断链，下一决策首个请求恰好两条消息。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    ctx = context(engine, "direct")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, "action-direct", "start_activity", start_activity("观看", "不存在的对象")))
    assert engine.commit_decision_round("direct")[0].status == "rejected"
    observation, [request], _ = await _single(_environment(engine), "wait", _wait_reply())
    assert observation["continuation"] is None and len(request["messages"]) == 2


_CALL = {"id": "call-direct", "type": "function", "function": {"name": "start_activity", "arguments": "{}"}}


def _recorded_decision(engine: LifeEngine, messages: list[dict], response: dict) -> None:
    """一次带手写模型证据的单人决策：行动请求与响应落库，登记一个被业务拒绝的世界请求（人物同一秒再决策）。"""
    from agentsociety2.agent.base import ModelCallEvidence

    ctx = context(engine, "recorded")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    request = {"model": "deepseek-flash", "messages": messages, "extra_body": {"thinking": {"type": "enabled"}},
               "tools": [{"type": "function", "function": {"name": "start_activity"}}]}
    call_id = f"{ctx.decision_id}:model:0001:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "request", 1, request))
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "response", 1, request, response=response))
    engine.submit_action(DirectToolCall(ctx, "action-recorded", "start_activity", start_activity("观看", "不存在的对象")))
    assert engine.commit_decision_round("recorded")[0].status == "rejected"


def _answer(calls: list[dict], reasoning: str | None = "想一想。") -> dict:
    """手写的提供商响应：思考与工具调用。"""
    return {"choices": [{"message": {"content": "", "reasoning_content": reasoning, "tool_calls": calls}}]}


def _next_chain(engine: LifeEngine) -> dict | None:
    """同一秒下一次决策的观察所带的续接链。"""
    ctx = context(engine, "next")
    engine.begin_decision_round([ctx])
    return engine.observe(ctx)["continuation"]


_BASE = [{"role": "system", "content": "系统层"}, {"role": "user", "content": "六层"}]
_EARLIER = [{"role": "assistant", "content": None, "tool_calls": [{**_CALL, "id": "call-memory"}]},
            {"role": "tool", "tool_call_id": "call-memory", "content": "{\"episodes\":[]}"}]


@pytest.mark.parametrize(("messages", "response", "continued"), [
    (_BASE, _answer([_CALL]), True),
    ([*_BASE, {**_EARLIER[0], "reasoning_content": "先查记忆。"}, _EARLIER[1]], _answer([_CALL]), True),
    ([*_BASE, *_EARLIER], _answer([_CALL]), False),
    (_BASE, _answer([_CALL, {**_CALL, "id": "call-second"}]), False),
    (_BASE, _answer([{**_CALL, "function": {"name": "memory_recent", "arguments": "{}"}}]), False),
    (_BASE, _answer([{**_CALL, "function": {"name": "send_message", "arguments": "{}"}}]), False),
    (_BASE, _answer([_CALL], reasoning=""), False),
])
def test_recorded_evidence_decides_whether_the_chain_holds(_engine_factory, messages, response, continued) -> None:
    """链由模型证据判定：末次请求中此前的 assistant 缺思考、最终响应不是恰好一个世界请求、调用的工具与登记不符或
    思考为空都断链；证据齐全（含带思考的记忆查询回合）时续接，且消息按发送键序重建。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _recorded_decision(engine, messages, response)
    chain = _next_chain(engine)
    assert (chain is not None) is continued
    if continued:
        assert chain["messages"] == messages and chain["tool_call_id"] == "call-direct"
        assert [list(item) for item in chain["messages"]] == [list(item) for item in messages]
        assert list(chain["assistant"]) == ["role", "content", "tool_calls", "reasoning_content"]


def test_a_decision_that_registered_no_world_request_does_not_continue(_engine_factory) -> None:
    """上一决策没有以恰好一个世界请求结束（有模型证据、未登记即中止）：同一秒下一次决策不续接。"""
    from agentsociety2.agent.base import ModelCallEvidence

    engine = _engine_factory()
    stage_day_plan(engine)
    ctx = context(engine, "aborted")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    request = {"model": "deepseek-flash", "messages": _BASE, "extra_body": {"thinking": {"type": "enabled"}},
               "tools": [{"type": "function", "function": {"name": "start_activity"}}]}
    call_id = f"{ctx.decision_id}:model:0001:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "request", 1, request))
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "response", 1, request, response=_answer([_CALL])))
    engine.abort_decision_round("aborted", "injected failure")
    assert _next_chain(engine) is None


def test_a_repeated_observation_of_the_same_decision_does_not_continue(_engine_factory) -> None:
    """同一决策同一秒的第二次观察（重放）不是相邻的两次决策，不续接。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    ctx = context(engine, "twice")
    engine.begin_decision_round([ctx])
    assert engine.observe(ctx)["continuation"] is None
    assert engine.observe(ctx)["continuation"] is None


def test_a_missing_result_of_the_previous_request_breaks_the_chain(_engine_factory, monkeypatch) -> None:
    """上一世界请求的结果不在本 session 的进度记录中（证据缺失）时断链。"""
    from worldmock.life import continuation

    engine = _engine_factory()
    stage_day_plan(engine)
    _recorded_decision(engine, _BASE, _answer([_CALL]))
    monkeypatch.setattr(continuation, "records_after", lambda context, store, after_seq: [])
    assert _next_chain(engine) is None


@pytest.mark.asyncio
async def test_a_rejected_request_continues_with_the_rejection_reason(_engine_factory) -> None:
    """上一决策的世界请求被业务拒绝时同样续接：tool 消息的 result 带错误码与原因（与进度层同一投影）。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)

    def unavailable(observation: dict) -> list:
        row = next(row for row in observation["tool_layer"]["targets"] for action in row["actions"]
                   if not action["available"])
        action = next(action for action in row["actions"] if not action["available"])
        return [_reply("call-refused", "start_activity", {"targets": [row["name"]], "actions": [action["action"]],
                                                           "ways": [action["way"]]}, "试试看。")]

    await _single(environment, "refused", unavailable)
    observation, [request], _ = await _single(environment, "wait", _wait_reply())
    result = json.loads(request["messages"][-1]["content"])["result"]
    assert result["status"] == "rejected" and result["error_code"] and result["details"]["reason"]
    [record] = [row for row in observation["progress_layer"]["events"] if row["type"] == "tool_call"]
    assert result == record["result"]


@pytest.mark.asyncio
async def test_the_pre_send_check_fails_when_the_current_decision_lacks_thinking(_engine_factory) -> None:
    """带续接链的请求发送前自检：本次决策内的 assistant（协议纠错回合）没有 reasoning_content 时以英文错误失败。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", _plan_reply())
    ctx = context(engine, "wait")
    engine.begin_decision_round([ctx])
    bad = _reply("call-bad", "start_activity", {"targets": ["不存在的对象"], "actions": ["interact"],
                                                 "ways": ["等待"]}, None)
    with pytest.raises(RuntimeError, match="current decision is missing reasoning_content"):
        await _decide(environment, ctx, [bad, *_wait_reply()])


def test_the_pre_send_check_breaks_a_chain_whose_assistant_lacks_thinking() -> None:
    """链中的 assistant 缺思考时断链：原地换成新快照消息，本决策此后的回合从新快照续写；没有链时原样返回。"""
    system, head, fresh = ({"role": "system", "content": "s"}, {"role": "user", "content": "head"},
                           {"role": "user", "content": "fresh"})
    call = [{"id": "call", "type": "function", "function": {"name": "report_status", "arguments": "{}"}}]
    chain = [system, head, {"role": "assistant", "content": None, "tool_calls": call},
             {"role": "tool", "tool_call_id": "call", "content": "{}"}]
    budget = ProgressBudget((), (), fresh_messages=(system, fresh), chain_length=len(chain))
    current = {"role": "assistant", "content": None, "tool_calls": call, "reasoning_content": "想想。"}
    messages = [*chain, current]
    ended = send_check(messages, budget)
    assert messages == [system, fresh, current]
    assert (ended.fresh_messages, ended.chain_length) == (None, 0)
    plain = ProgressBudget((), ())
    assert send_check([system, head], plain) is plain


def _chained_request(reasoning: str) -> tuple[dict, ProgressBudget]:
    """一个续接请求（链中 assistant 带长思考）与带新快照的预算；进度层有一条未受保护、带 self_note 的记录。"""
    record = {"occurred_at": "2026-01-05T08:00:00+08:00", "type": "tool_call", "tool": "report_status",
              "arguments": {}, "result": {}, "self_note": "写下的进展。"}
    layers = ({"layer": "进度层", "session_started_at": "2026-01-05T08:00:00+08:00", "events": [record]},)
    system = {"role": "system", "content": "system"}
    fresh = {"role": "user", "content": json.dumps(list(layers), ensure_ascii=False, separators=(",", ":"))}
    call = [{"id": "call", "type": "function", "function": {"name": "report_status", "arguments": "{}"}}]
    chain = [system, {"role": "user", "content": "head"},
             {"role": "assistant", "content": None, "tool_calls": call, "reasoning_content": reasoning},
             {"role": "tool", "tool_call_id": "call", "content": "{}"}]
    request = {"model": prompt_capacity.MODEL_ALIAS, "messages": chain, "tools": [], "max_tokens": 10}
    return request, ProgressBudget(layers, ({"protected": False, "lines": []},), fresh_messages=(system, fresh),
                                   chain_length=len(chain))


def _window_above(tokens: int) -> int:
    """使阈值恰好高于给定 token 数的上下文长度。"""
    return int((tokens + 1) / prompt_capacity.CONTEXT_THRESHOLD) + 1


def test_budget_step_zero_ends_the_continuation_before_any_condensation(monkeypatch) -> None:
    """达到阈值时先结束本秒续接（新快照、思考不再出现），低于阈值即停，self_note 不动；新快照仍超限才浓缩进度层。"""
    request, budget = _chained_request("长长的思考" * 50)
    fresh = {**request, "messages": list(budget.fresh_messages)}
    layer = json.loads(budget.fresh_messages[1]["content"])
    del layer[0]["events"][0]["self_note"]
    noteless = {**request, "messages": [budget.fresh_messages[0], {"role": "user", "content": json.dumps(
        layer, ensure_ascii=False, separators=(",", ":"))}]}
    fresh_size, noteless_size = (prompt_capacity.request_token_count(item) for item in (fresh, noteless))
    assert noteless_size + 3 < fresh_size < prompt_capacity.request_token_count(request)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: _window_above(fresh_size))
    fitted = fit_request(request, budget)
    assert fitted["messages"] == list(budget.fresh_messages)
    assert len(request["messages"]) == 4
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: _window_above(noteless_size))
    condensed = fit_request(request, budget)
    assert condensed["messages"] == noteless["messages"]


@pytest.mark.asyncio
async def test_budget_step_zero_starts_the_decision_from_a_fresh_snapshot_for_every_turn(
        _engine_factory, monkeypatch) -> None:
    """真实决策：带链的首个请求超过阈值时第 0 步改用新快照；本决策后续回合（协议纠错之后）也从新快照续写。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", _plan_reply())
    original, over = prompt_capacity.request_token_count, []

    def count(request: dict) -> int:
        """只把本决策第一次计量的续接请求记为超限。"""
        chained = _THINK_PLAN in json.dumps(request, ensure_ascii=False)
        if chained and not over:
            over.append(True)
            return 10 ** 9
        return original(request)

    monkeypatch.setattr(prompt_capacity, "request_token_count", count)
    bad = _reply("call-bad", "start_activity", {"targets": ["不存在的对象"], "actions": ["interact"],
                                                 "ways": ["等待"]}, "先试一个。")
    observation, requests, _ = await _single(environment, "wait", [bad, *_wait_reply()])
    assert observation["continuation"] is not None and len(requests) == 2
    for request in requests:
        assert _THINK_PLAN not in json.dumps(request, ensure_ascii=False)
        assert json.loads(request["messages"][1]["content"])[5] == observation["progress_layer"]
    assert [item["role"] for item in requests[1]["messages"]] == ["system", "user", "assistant", "tool"]


def _switch_off_engine(tmp_path: Path) -> LifeEngine:
    """以关闭同秒续接的阶段身份新建运行。"""
    from worldmock.runtime import stage_model_identity

    scenario, _ = load_scenario(SCENARIO)
    identity = RunIdentity(
        run_id="switch-off", source_group_id="switch-off", agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(), timezone=scenario.timezone,
        code_sha256="2" * 64, model_identity=ModelIdentity(
            provider="openai_compatible", base_url="https://api.deepseek.com", requested_model="deepseek-flash",
            thinking="enabled", tool_choice="auto", max_retries=0, prompt_sha256="0" * 64,
            tool_schema_sha256="1" * 64))
    return LifeEngine.create(SCENARIO, tmp_path / "switch-off", identity, stage_model_identity(False))


@pytest.mark.asyncio
async def test_switch_off_every_decisions_first_request_has_exactly_two_messages(tmp_path) -> None:
    """开关关闭（冻结进清单）：同一秒的后续决策也从新快照构造，每次决策首个请求恰好两条消息。"""
    engine = _switch_off_engine(tmp_path)
    try:
        assert engine.store.manifest["stage_model_identity"]["stages"]["action"]["same_second_continuation"] is False
        run = await _plan_then_wait(engine)
        assert run["observation"]["continuation"] is None
        assert [len(requests[0]["messages"]) for requests in (run["first"], run["second"])] == [2, 2]
        assert _THINK_PLAN not in json.dumps(run["second"], ensure_ascii=False)
    finally:
        engine.close()


def test_continuation_messages_read_earlier_changes_and_skip_memory_and_feedback_tool_messages() -> None:
    """宿主组装：链中此前的续接 tool 消息的 changes 叠加到链首快照上，记忆查询与协议纠错的 tool 消息不算改动；
    新 tool 消息只写相对叠加结果的改动。"""
    from custom.agents.life_continuation import continuation_messages

    def layers(state: str, body: str) -> list[dict]:
        return [{"layer": "工具层", "targets": [{"name": "店", "state": state}]},
                {"layer": "人设层", "relationships": [], "preferences": []}, {"layer": "记忆层"},
                {"layer": "待办层", "paused_activities": [], "day_plan": None},
                {"layer": "环境层", "body": {"appetite": body}}, {"layer": "进度层"}]

    call = {"id": "call-earlier", "type": "function", "function": {"name": "report_status", "arguments": "{}"}}
    earlier = {"result": {"status": "completed"}, "changes": {"body": {"appetite": "有点饿"}}}
    feedback = {"ok": False, "executed": False, "error_type": "protocol_validation_error", "error": "bad"}
    chain = {"messages": [
        {"role": "system", "content": "系统层"},
        {"role": "user", "content": json.dumps(layers("营业", "不饿"), ensure_ascii=False)},
        {"role": "assistant", "content": None, "tool_calls": [{**call, "id": "call-memory"}], "reasoning_content": "查"},
        {"role": "tool", "tool_call_id": "call-memory", "content": json.dumps({"episodes": []})},
        {"role": "assistant", "content": None, "tool_calls": [{**call, "id": "call-bad"}], "reasoning_content": "错"},
        {"role": "tool", "tool_call_id": "call-bad", "content": json.dumps(feedback)},
        {"role": "assistant", "content": None, "tool_calls": [call], "reasoning_content": "报"},
        {"role": "tool", "tool_call_id": "call-earlier", "content": json.dumps(earlier, ensure_ascii=False)}],
        "assistant": {"role": "assistant", "content": "", "tool_calls": [{**call, "id": "call-last"}],
                      "reasoning_content": "再报"},
        "tool_call_id": "call-last", "result": {"status": "completed"}, "events": []}
    messages = continuation_messages(chain, layers("打烊", "有点饿"))
    assert messages[:9] == [*chain["messages"], chain["assistant"]]
    assert json.loads(messages[-1]["content"]) == {"result": {"status": "completed"},
                                                   "changes": {"targets": {"updated": [{"name": "店", "state": "打烊"}]}}}
    assert continuation_messages(None, layers("打烊", "有点饿")) is None


def test_layer_changes_overlay_earlier_changes_and_keep_the_latest() -> None:
    """changes 相对于模型所见：链首快照依次叠加此前各 changes，以最新为准；没有改动的键不写。"""
    def layers(targets, paused, plan, body, views):
        return [{"layer": "工具层", "targets": targets}, {"layer": "人设层", "relationships": views, "preferences": []},
                {"layer": "记忆层"}, {"layer": "待办层", "paused_activities": paused, "day_plan": plan},
                {"layer": "环境层", "body": body}, {"layer": "进度层"}]

    shop, desk = {"name": "店", "state": "营业"}, {"name": "工位", "state": None}
    paused = {"name": "读书（9月28日 09:00 起）", "place": "家"}
    head = layers([shop, desk], [paused], None, {"appetite": "不饿"}, [{"person": "王磊", "view": None}])
    earlier = {"targets": {"removed": ["工位"]}, "body": {"appetite": "有点饿"}}
    current = layers([{**shop, "state": "打烊"}], [], None, {"appetite": "有点饿"},
                     [{"person": "王磊", "view": "靠谱"}])
    assert layer_changes(head, [earlier], current) == {
        "targets": {"updated": [{"name": "店", "state": "打烊"}]},
        "paused_activities": {"removed": ["读书（9月28日 09:00 起）"]},
        "relationships": [{"person": "王磊", "view": "靠谱"}]}
    assert layer_changes(head, [], head) == {}


@pytest.mark.parametrize("switch", [None, 1, "on"])
def test_the_stage_identity_requires_a_boolean_switch(switch) -> None:
    """五个入口共用的阶段身份校验：开关缺失或不是布尔值即拒绝；开与关都合法。"""
    from worldmock.runtime import stage_model_identity
    from worldmock.validation import validate_stage_model_identity

    for value in (True, False):
        validate_stage_model_identity(stage_model_identity(value))
    identity = stage_model_identity()
    if switch is None:
        del identity["stages"]["action"]["same_second_continuation"]
    else:
        identity["stages"]["action"]["same_second_continuation"] = switch
    with pytest.raises(IntegrityError, match="same_second_continuation is .*expected a boolean"):
        validate_stage_model_identity(identity)


def test_the_switch_is_frozen_into_the_run_manifest_and_the_agent_profile(tmp_path) -> None:
    """运行参数 same_second_continuation 冻结进清单 stages.action 与人物配置；命令行 run 可以关闭。"""
    from worldmock.cli import build_parser
    from worldmock.runtime import RunRequest, _agent_specs, _prepare_new_run

    prepared = _prepare_new_run(RunRequest(ROOT, SCENARIO, tmp_path / "off", "off", "off", False))
    assert prepared.env_kwargs["create_options"] == {"same_second_continuation": False}
    assert prepared.same_second_continuation is False
    scenario, _ = load_scenario(SCENARIO)
    profiles = [spec["profile"] for spec in _agent_specs(scenario, persona_compiler=lambda _: None,
                                                         same_second_continuation=False)]
    assert profiles and all(profile["same_second_continuation"] is False for profile in profiles)
    arguments = ["run", "--scenario", str(SCENARIO), "--output", str(tmp_path / "cli")]
    assert build_parser().parse_args(arguments).same_second_continuation == "on"
    switched = build_parser().parse_args([*arguments, "--same-second-continuation", "off"])
    assert switched.same_second_continuation == "off"


@pytest.mark.asyncio
async def test_life_env_freezes_the_switch_into_the_manifest(tmp_path) -> None:
    """LifeEnv 新建运行时把 create_options 的开关冻结进清单；打开已有运行读取清单中的值。"""
    from tests.integration.test_env_lifecycle_coverage import _identity, _start

    scenario, _ = load_scenario(SCENARIO)
    environment = LifeEnv(scenario_path=str(SCENARIO), run_dir=str(tmp_path / "env"),
                          run_identity=_identity(scenario, "env").model_dump(mode="json"),
                          create_options={"same_second_continuation": False})
    await environment.init(_start(scenario))
    try:
        action = environment.engine.store.manifest["stage_model_identity"]["stages"]["action"]
        assert action["same_second_continuation"] is False
    finally:
        await environment.close()


@pytest.mark.parametrize("command", ["resume", "replay"])
def test_resume_and_replay_cannot_override_the_switch(command, tmp_path) -> None:
    """恢复与重放继承父清单的值：命令行不接受续接开关。"""
    from worldmock.cli import build_parser

    arguments = [command, "--scenario", str(SCENARIO), "--output", str(tmp_path / "child"), "--parent",
                 str(tmp_path / "parent"), "--checkpoint", "checkpoint"]
    if command == "replay":
        arguments += ["--intervention", str(tmp_path / "intervention.json")]
    assert build_parser().parse_args(arguments).command == command
    with pytest.raises(SystemExit):
        build_parser().parse_args([*arguments, "--same-second-continuation", "off"])


def _parent_run(tmp_path: Path, switch: bool, profile_switch: bool):
    """父运行：清单冻结开关 switch，检查点中人物工作区的配置冻结 profile_switch。"""
    from tests.integration.test_host_branch_coverage import _agent_workspace
    from worldmock.footprints.checkpoint import snapshot_directory
    from worldmock.footprints.store import FootprintStore
    from worldmock.life.run_identity import category_identity
    from worldmock.life.semantics import load_runtime_scenario
    from worldmock.runtime import _model_identity, runtime_code_sha256, stage_model_identity

    semantics, digest = load_runtime_scenario(SCENARIO)
    manifest = {"schema_version": 1, "run_id": "parent", "agent_ids": [1],
                "model_identity": _model_identity().model_dump(mode="json"),
                "stage_model_identity": stage_model_identity(switch), "code_sha256": runtime_code_sha256(ROOT),
                "scenario_sha256": digest, **category_identity(semantics)}
    state = {"version": 0, "current_time": 1767225600}
    tmp_path.mkdir(parents=True)
    parent = FootprintStore.create(tmp_path / "parent", manifest, state)
    agent = {"schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None,
             "profile": {"id": 1, "same_second_continuation": profile_switch}}
    workspace = _agent_workspace(tmp_path, agent_document=agent)
    snapshots = {"agent:1": snapshot_directory(workspace, parent.assets),
                 "life": parent.assets.put_json({"state_version": 0}, {"kind": "life_checkpoint"})}
    return parent, parent.checkpoint(state, snapshots)


def test_resume_inherits_the_parent_switch_and_rejects_a_diverged_profile(tmp_path) -> None:
    """恢复继承父清单的开关（子清单与准备结果都取父值）；人物配置冻结的值与父清单不同时拒绝发布分支。"""
    from worldmock.runtime import ResumeRequest, _prepare_resume

    parent, checkpoint = _parent_run(tmp_path / "same", False, False)
    try:
        prepared = _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "same" / "parent",
                                                 checkpoint.checkpoint_id, tmp_path / "same" / "child"))
    finally:
        parent.close()
    assert prepared.same_second_continuation is False
    manifest = json.loads((tmp_path / "same" / "child" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage_model_identity"]["stages"]["action"]["same_second_continuation"] is False
    parent, checkpoint = _parent_run(tmp_path / "diverged", False, True)
    try:
        with pytest.raises(IntegrityError, match="same_second_continuation must match the parent manifest"):
            _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "diverged" / "parent",
                                          checkpoint.checkpoint_id, tmp_path / "diverged" / "child"))
        assert not (tmp_path / "diverged" / "child").exists()
    finally:
        parent.close()
