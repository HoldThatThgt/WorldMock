"""同秒续接的验收（docs/design/06-agent.md 第二章「同秒续接」、第四章第 0 步、第七章「清单」）。

真实 LifeEngine、LifeEnv 与 SQLite 上，脚本化模型经真实 LifeAgent 决策：同一秒的后续决策原样续接上一决策末次请求的
全部消息，追加上一决策的 assistant（原样带思考）与一条 tool 消息 {result, changes, events}；上一决策以开始组（两个开始
工具各一次）结束时 assistant 带两个 tool_calls，按调用次序各追加一条 tool 消息，changes 与 events 只写在最后一条上；
他人同秒发来的消息在整个请求中只出现一次；时间推进后请求只有两条消息、不带此前的思考；出发后同秒、反事实分支、缺思考、
证据缺失、调用与登记不按次序同名时断链；
从中秒检查点普通恢复与不中断运行的请求相同；开关关闭时每次决策首个请求恰好两条消息；开关冻结进清单与人物配置，
恢复继承、不能覆盖；预算达到阈值时先结束续接再浓缩。
"""

from __future__ import annotations

from tests.integration.runtime.current_contract_fixtures import inputs as current_inputs, profile as current_profile

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT, ENVIRONMENT_ROOT
from worldmock.footprints.checkpoint import restore_branch

import copy
import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from worldmock.agents.life_continuation import _overlay, _view as _seen, layer_changes, send_check  # noqa: E402
from custom.envs.life_env import LifeEnv  # noqa: E402
from scripts import inspect_run  # noqa: E402
from tests.integration.runtime_fixtures import current_run_identity, write_agent_workspace  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import (_OBSERVED_AT, _ModelResponse, _conclude_args,  # noqa: E402
                                                        _memory, _stored_memories, _view)
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (MAIN_TOOL, SIDE_TOOL, advance_next, advance_until_ready, context, do_at,  # noqa: F401
                                 errand_add, errand_cancel, errand_update, layer_start, main_arguments, pending_names,
                                 progress_calls, side_arguments, stage_day_plan, tool_of, use_app)
from tests.life.work_schedule_fixtures import declare  # noqa: E402
from worldmock.life.readable import iso_time  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.continuation import _session_changed  # noqa: E402
from worldmock.life.engine import LifeEngine  # noqa: E402
from worldmock.life.models import CounterfactualIntervention, ModelIdentity, RunIdentity  # noqa: E402
from worldmock.life.prompt_capacity import ProgressBudget, fit_request  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ENVIRONMENT_ROOT
#: 上一决策修订今日计划（瞬时请求，人物同一秒再决策）；思考里提到咖啡。
_PLAN = {"goals": [{"intent_kind": "休息", "goal": "下午喝杯咖啡", "remind_time": None, "around_time": None}]}
#: 在家点名歇一刻钟：start_main_activity 的完整参数（夹具里家的内容单元「测试休息」，900 秒）。
_WAIT = main_arguments("interact", "家", "测试休息")
#: 家里没有的一件事：名称解析找不到这个 detail，宿主以 NAME_NOT_FOUND 纠错，环境以 ACTION_REQUEST_INVALID 拒绝。
_UNKNOWN = main_arguments("interact", "家", "不存在的对象")
_THINK_PLAN = "上午有点困，先把下午喝咖啡写进计划。"
_THINK_WAIT = "计划写好了，先歇一会儿。"
_NOTE = "先把计划写上。"
#: 人物档案中的场景联系人（人设层关系项与 relationship 记忆可写的对象）。
_CONTACTS = [{"salutation": "老周", "relation": "老友"}, {"salutation": "陈母", "relation": "家人"}]


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证请求合同。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _Script:
    """只替换远程模型传输：按顺序返回预定响应，记录实际发送的请求。"""

    model_name = "test-model"

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


class _GroupReply:
    """一次模型响应：思考、回复正文与同一次回复里的几个工具调用（开始组是两个开始工具各一个）。"""

    def __init__(self, calls: list[tuple[str, str, dict]], reasoning: str | None, content: str = "") -> None:
        self._calls = [(call_id, tool, json.dumps(arguments, ensure_ascii=False)) for call_id, tool, arguments in calls]
        self._reasoning, self._content = reasoning, content
        tool_calls = [SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=tool, arguments=text))
                      for call_id, tool, text in self._calls]
        message = SimpleNamespace(tool_calls=tool_calls, content=content, reasoning_content=reasoning)
        self.choices = [SimpleNamespace(message=message)]

    def model_dump(self, *, mode: str) -> dict:
        """提供商形状的回复（与单个调用的响应同形，tool_calls 按回复里的先后列出）。"""
        assert mode == "json"
        calls = [{"id": call_id, "type": "function", "function": {"name": tool, "arguments": text}}
                 for call_id, tool, text in self._calls]
        return {"choices": [{"message": {"content": self._content, "tool_calls": calls,
                                         "reasoning_content": self._reasoning}}]}


def _person(environment: LifeEnv, ctx, dispatcher: _Script) -> LifeAgent:
    """每次决策都重建人物（与 AS2 从工作区重建相同），绑定真实环境与本轮宿主身份。"""
    person = LifeAgent()
    person._id = ctx.agent_id
    person._name = f"人物{ctx.agent_id}"
    person._profile = current_profile()
    person._env = environment
    person._dispatcher = dispatcher
    person._model_name = "test-model"
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
    return [_reply(call_id, MAIN_TOOL, _WAIT, reasoning)]


def _stored_observation(engine: LifeEngine, observation: dict):
    """本次观察落库的观察事件。"""
    return next(event for event in engine.context.store.read_agent_events("observation", 1)
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
    assert engine.context.state.current_time == int(first_observation["current_time"])
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
    assert observation["continuation"]["tool_results"] == [{"tool_call_id": "call-plan", "result": record["result"]}]
    assert not {"tool_call_id", "result"} & set(observation["continuation"])
    assert "continuation" not in _stored_observation(engine, observation).data
    planned = engine.context.store.read_agent_events("observation", 1)[-2]
    assert planned.decision_id == "decision-plan-1" and not _session_changed(engine.context.store, engine.context.state, 1, planned)


@pytest.mark.asyncio
async def test_a_three_decision_chain_keeps_every_prefix_and_reports_each_change_once(_engine_factory) -> None:
    """同一秒三次决策：第三次的前缀逐字节等于第二次的请求（其中含第一次的 assistant 与 tool 消息）；第一次改动的今日
    计划已由第一条 tool 消息交还，第二条 tool 消息没有新改动时省略 changes。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", _plan_reply())

    def recall(observation: dict) -> list:
        goal = observation["goal_bindings"][0]["task_name"]
        return [_reply("call-recall", "recall_task_summary", {"task_name": goal}, "回看一下这段停留。"),
                _reply("call-revision", "revise_day_plan", _PLAN, "回看后保持计划不变。")]

    _, recalled, _ = await _single(environment, "recall", recall)
    observation, [third], _ = await _single(environment, "wait", _wait_reply())
    second = recalled[-1]
    size = len(second["messages"])
    assert json.dumps(third["messages"][:size], ensure_ascii=False) == json.dumps(second["messages"], ensure_ascii=False)
    first_tool, last_tool = second["messages"][3], third["messages"][-1]
    assert list(json.loads(first_tool["content"])) == ["result", "changes"]
    assert list(json.loads(last_tool["content"])) == ["result"]
    assert (third["messages"][size]["reasoning_content"], last_tool["tool_call_id"]) == ("回看后保持计划不变。",
                                                                                          "call-revision")
    assert observation["continuation"]["messages"][1] == third["messages"][1]


@pytest.mark.asyncio
async def test_duty_is_constant_within_a_same_second_chain_and_never_enters_changes(_engine_factory) -> None:
    """同秒续接内时钟不动，环境层 duty 与所在地点一样在链内不变、不进 changes（docs/design/06-agent.md#agent-环境层-duty ）：
    有工作安排的人物在周一零点先修订今日计划、同一秒再开始等待；链首 user 消息的环境层带非空 duty（未到上班时间），两次
    观察的 duty 逐值相同，续接的 tool 消息只交还 changes 里的今日计划，不带 duty，也不重述班次。"""
    engine = _engine_factory()
    run = await _plan_then_wait(engine)
    [previous], [request] = run["first"], run["second"]
    head = json.loads(previous["messages"][1]["content"])[4]
    assert head["layer"] == "环境层" and head["duty"]["status"] == "未到上班时间"
    assert head["duty"]["hours"] == "09:00–18:00"
    first, second = engine.context.store.read_agent_events("observation", 1)[-2:]
    assert first.data["environment_layer"]["duty"] == second.data["environment_layer"]["duty"] == head["duty"]
    assert run["observation"]["environment_layer"]["duty"] == head["duty"]
    assert request["messages"][1] == previous["messages"][1]
    tool = request["messages"][-1]
    assert tool["role"] == "tool" and list(json.loads(tool["content"])["changes"]) == ["day_plan"]
    assert "duty" not in tool["content"] and "09:00–18:00" not in tool["content"]






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
    started_at = engine.context.state.current_time
    assert advance_until_ready(engine) and engine.context.state.current_time > started_at

    def conclude(observation: dict) -> list:
        names = pending_names(observation)
        return [_reply("call-end", "conclude_activity", _ended(names, "歇好了。"), "等完了，收尾。")]

    observation, [request], _ = await _single(run["environment"], "later", conclude)
    assert observation["continuation"] is None
    assert [item["role"] for item in request["messages"]] == ["system", "user"]
    text = json.dumps(request, ensure_ascii=False)
    assert _THINK_PLAN not in text and _THINK_WAIT not in text
    progress = json.loads(request["messages"][1]["content"])[5]
    assert progress == observation["progress_layer"]
    calls = {row["tool"]: row for row in progress["events"] if row["type"] == "tool_call"}
    assert calls["revise_day_plan"]["self_note"] == _NOTE and MAIN_TOOL in calls




def _checkpoint(engine: LifeEngine):
    """以占位人物快照提交一个共同检查点（同一秒两轮之间）。"""
    snapshots = {f"agent:{agent_id}": engine.context.store.assets.put_json({"agent_id": agent_id}, {"kind": "continuation"})
                 for agent_id in engine.context.store.manifest["agent_ids"]}
    return engine.checkpoint(snapshots)


def _branch(engine: LifeEngine, checkpoint, destination: Path, metadata: dict | None = None) -> LifeEngine:
    """从检查点恢复一个分支并打开。"""
    restore_branch(engine.context.store, checkpoint.checkpoint_id, destination, metadata).close()
    return LifeEngine.open(engine.context.inputs, destination)


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
        assert resumed.context.store.manifest["branch_kind"] == "resume"
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
    engine.submit_action(DirectToolCall(ctx, "action-direct", MAIN_TOOL, do_at("家", "不存在的对象")))
    assert engine.commit_decision_round("direct")[0].status == "rejected"
    observation, [request], _ = await _single(_environment(engine), "wait", _wait_reply())
    assert observation["continuation"] is None and len(request["messages"]) == 2


_CALL = {"id": "call-direct", "type": "function", "function": {"name": MAIN_TOOL, "arguments": "{}"}}
#: 同一次回复里的另一个开始工具调用（与 _CALL 组成开始组）。
_SIDE_CALL = {"id": "call-side", "type": "function", "function": {"name": SIDE_TOOL, "arguments": "{}"}}
#: 登记的世界请求：只有一条主调用，或开始组的两条（按登记先后）。
_ONE, _GROUP, _SIDE_FIRST = (MAIN_TOOL,), (MAIN_TOOL, SIDE_TOOL), (SIDE_TOOL, MAIN_TOOL)


def _recorded_decision(engine: LifeEngine, messages: list[dict], response: dict | None,
                       registered: tuple[str, ...] = _ONE) -> None:
    """一次带手写模型证据的单人决策：行动请求与响应落库（response 为 None 时只有请求），按 registered 的先后登记世界请求
    （一条主调用，或开始组的两条）。主调用点名家里没有的一件事，整个决策被业务拒绝（人物同一秒再决策）。"""
    from agentsociety2.agent.base import ModelCallEvidence

    ctx = context(engine, "recorded")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    request = {"model": "test-model", "messages": messages, "extra_body": {"thinking": {"type": "enabled"}},
               "tools": [{"type": "function", "function": {"name": MAIN_TOOL}}]}
    call_id = f"{ctx.decision_id}:model:0001:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "request", 1, request))
    if response is not None:
        engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "response", 1, request, response=response))
    requests = {MAIN_TOOL: do_at("家", "不存在的对象"), SIDE_TOOL: use_app("哔哩哔哩")}
    for index, tool in enumerate(registered, start=1):
        engine.submit_action(DirectToolCall(ctx, f"action-recorded-{index}", tool, requests[tool]))
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


@pytest.mark.parametrize(("messages", "response", "registered", "continued"), [
    (_BASE, _answer([_CALL]), _ONE, True),
    ([*_BASE, {**_EARLIER[0], "reasoning_content": "先查记忆。"}, _EARLIER[1]], _answer([_CALL]), _ONE, True),
    ([*_BASE, *_EARLIER], _answer([_CALL]), _ONE, False),
    (_BASE, _answer([_CALL, {**_CALL, "id": "call-second"}]), _ONE, False),
    (_BASE, _answer([{**_CALL, "function": {"name": "memory_recent", "arguments": "{}"}}]), _ONE, False),
    (_BASE, _answer([{**_CALL, "function": {"name": "send_message", "arguments": "{}"}}]), _ONE, False),
    (_BASE, _answer([_CALL], reasoning=""), _ONE, False),
    (_BASE, _answer([_CALL, _SIDE_CALL]), _GROUP, True),
    (_BASE, _answer([_SIDE_CALL, _CALL]), _SIDE_FIRST, True),
    (_BASE, _answer([_SIDE_CALL, _CALL]), _GROUP, False),
    (_BASE, _answer([_CALL, _SIDE_CALL]), _ONE, False),
    (_BASE, _answer([_CALL]), _GROUP, False),
    (_BASE, _answer([_SIDE_CALL]), _ONE, False),
    (_BASE, _answer([_CALL, _SIDE_CALL], reasoning=""), _GROUP, False),
])
def test_recorded_evidence_decides_whether_the_chain_holds(_engine_factory, messages, response, registered,
                                                           continued) -> None:
    """链由模型证据判定：末次请求中此前的 assistant 缺思考、最终响应不是一个世界请求或一个开始组（同一个开始工具两次、
    只读查询）、调用的工具与登记的 action_requested 不按次序同名（先后颠倒、回复两条只登记一条、回复一条登记两条、工具
    不同）或思考为空都断链；证据齐全（含带思考的记忆查询回合、两个开始工具各一个的开始组）时续接，消息按发送键序重建，
    tool_results 按调用次序每个世界请求一条，各带自己的 tool_call_id 与公开结果。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _recorded_decision(engine, messages, response, registered)
    chain = _next_chain(engine)
    assert (chain is not None) is continued
    if continued:
        calls = response["choices"][0]["message"]["tool_calls"]
        assert chain["messages"] == messages
        assert [item["tool_call_id"] for item in chain["tool_results"]] == [call["id"] for call in calls]
        assert all(list(item) == ["tool_call_id", "result"] and item["result"]["status"] == "rejected"
                   for item in chain["tool_results"])
        assert not {"tool_call_id", "result"} & set(chain)
        assert [list(item) for item in chain["messages"]] == [list(item) for item in messages]
        assert list(chain["assistant"]) == ["role", "content", "tool_calls", "reasoning_content"]
        assert [call["function"]["name"] for call in chain["assistant"]["tool_calls"]] == list(registered)


def test_a_decision_that_registered_no_world_request_does_not_continue(_engine_factory) -> None:
    """上一决策没有以一个世界请求或一个开始组结束（有模型证据、未登记即中止）：同一秒下一次决策不续接。"""
    from agentsociety2.agent.base import ModelCallEvidence

    engine = _engine_factory()
    stage_day_plan(engine)
    ctx = context(engine, "aborted")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    request = {"model": "test-model", "messages": _BASE, "extra_body": {"thinking": {"type": "enabled"}},
               "tools": [{"type": "function", "function": {"name": MAIN_TOOL}}]}
    call_id = f"{ctx.decision_id}:model:0001:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "request", 1, request))
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "response", 1, request, response=_answer([_CALL])))
    engine.abort_decision_round("aborted", "injected failure")
    assert _next_chain(engine) is None


def test_a_recorded_request_without_its_response_breaks_the_chain(_engine_factory) -> None:
    """上一决策的末次行动请求已落库、响应缺失（证据缺失）而世界请求照常登记：同一秒下一次决策不续接；补上同一份响应
    就续接（对照组），可见断链只因为响应缺失。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _recorded_decision(engine, _BASE, None)
    requests = [event for event in engine.context.store.read_agent_events("model_request", 1)
                if event.decision_id == "decision-recorded-1"]
    assert len(requests) == 1 and not engine.context.store.read_agent_events("model_response", 1)
    assert [event.decision_id for event in engine.context.store.read_agent_events("action_requested", 1)] == [
        "decision-recorded-1"]
    assert _next_chain(engine) is None
    answered = _engine_factory()
    stage_day_plan(answered)
    _recorded_decision(answered, _BASE, _answer([_CALL]))
    assert _next_chain(answered)["messages"] == _BASE


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
    await _single(environment, "refused", _unavailable)
    observation, [request], _ = await _single(environment, "wait", _wait_reply())
    result = json.loads(request["messages"][-1]["content"])["result"]
    assert result["status"] == "rejected" and result["error_code"] and result["details"]["reason"]
    [record] = [row for row in observation["progress_layer"]["events"] if row["type"] == "tool_call"]
    assert result == record["result"]


def _unavailable(observation: dict) -> list:
    """一次必被业务拒绝的响应：开始工具层第一个标为不可用的动作（照它所在的行写对应开始工具的调用）。"""
    row = next(row for row in observation["tool_layer"]["targets"] for action in row["actions"]
               if not action["available"])
    action = next(action for action in row["actions"] if not action["available"])
    request = layer_start(observation, row["name"], action["action"], action["detail"])
    return [_reply("call-refused", tool_of(request), request.model_dump(mode="json"), "试试看。")]


#: 贴着「有点想放松」档界（0.3）的起始放松需要：一次服务拒绝的情绪代价（+0.05）就让放松分级从「不闷」变一档。
_NEAR_RELAXATION_BAND = 0.28


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(MAIN_TOOL, SIDE_TOOL), (SIDE_TOOL, MAIN_TOOL)], ids=["main_first", "side_first"])
async def test_a_start_group_is_continued_with_one_tool_message_per_call(_engine_factory, order) -> None:
    """同一次回复里两个开始工具各调用一次：宿主放行这一批，按回复里的先后登记两条动作（:0001、:0002，self_note 只记在
    第一条上）。环境整组拒绝（在家歇一会儿成立，纸巾要到店才能买，两项都不开始），人物同一秒再决策。下一次请求在上一请求
    的全部消息之后是带两个 tool_calls 的 assistant 与两条 tool 消息：按调用次序各对应一个 tool_call_id，result 是各自动作的
    公开结果（与进度层该动作的记录同一投影），changes 只写在最后一条上（拒绝的情绪代价让放松分级变了一档）。对照组：整组
    受理时两条动作各开始一项活动，人物有进行中活动、同一秒不再决策，所以同一秒的续接只出现在整组被拒之后。"""
    engine = _engine_factory(mutate=lambda persona, config: persona.update(
        initial_relaxation_need=_NEAR_RELAXATION_BAND))
    stage_day_plan(engine)
    environment = _environment(engine)
    arguments = {MAIN_TOOL: _WAIT, SIDE_TOOL: side_arguments("purchase", "纸巾")}
    calls = [(f"call-{tool}", tool, arguments[tool]) for tool in order]
    head, [previous], starter = await _single(environment, "group", [
        _GroupReply(calls, "歇着顺手买包纸巾。", " 两件事一起。 ")])
    registered = engine.context.store.read_agent_events("action_requested", 1)
    assert [(event.action_id, event.data["tool_name"], event.data["request"], event.data["self_note"])
            for event in registered] == [
        ("decision-group-1:action:0001", order[0], arguments[order[0]], "两件事一起。"),
        ("decision-group-1:action:0002", order[1], arguments[order[1]], None)]
    assert engine.context.state.activities == {} and engine.scheduling_state().ready_agent_ids == (1,)
    observation, [request], _ = await _single(environment, "wait", _wait_reply())
    prefix = previous["messages"]
    assert json.dumps(request["messages"][:len(prefix)], ensure_ascii=False) == json.dumps(prefix, ensure_ascii=False)
    assistant, *tools = request["messages"][len(prefix):]
    assert assistant == starter._react_messages[-1] and assistant["reasoning_content"] == "歇着顺手买包纸巾。"
    assert [(call["id"], call["function"]["name"]) for call in assistant["tool_calls"]] == [
        (call_id, tool) for call_id, tool, _ in calls]
    assert [(item["role"], item["tool_call_id"]) for item in tools] == [("tool", call_id) for call_id, _, _ in calls]
    records = progress_calls(observation)
    assert [(row["tool"], row["arguments"]) for row in records] == [(tool, arguments[tool]) for tool in order]
    payloads = [json.loads(item["content"]) for item in tools]
    assert [payload["result"] for payload in payloads] == [row["result"] for row in records]
    assert all(payload["result"]["status"] == "rejected"
               and payload["result"]["message"].endswith(" Neither start request was started.") for payload in payloads)
    assert [list(payload) for payload in payloads] == [["result"], ["result", "changes"]]
    assert head["environment_layer"]["body"]["relaxation"] == "不闷"
    assert observation["environment_layer"]["body"]["relaxation"] == "有点想放松"
    assert payloads[1]["changes"] == {"body": observation["environment_layer"]["body"]}
    assert observation["continuation"]["tool_results"] == [
        {"tool_call_id": call_id, "result": row["result"]} for (call_id, _, _), row in zip(calls, records, strict=True)]
    control = _engine_factory()
    stage_day_plan(control)
    accepted = {MAIN_TOOL: _WAIT, SIDE_TOOL: side_arguments("interact", "哔哩哔哩")}
    await _single(_environment(control), "group", [_GroupReply(
        [(f"call-{tool}", tool, accepted[tool]) for tool in order], "歇着刷会儿视频。")])
    assert sorted(item.origin_action_id for item in control.context.state.activities.values()) == [
        "decision-group-1:action:0001", "decision-group-1:action:0002"]
    assert control.scheduling_state().ready_agent_ids == ()


def _chain_report(engine: LifeEngine) -> tuple[list[dict], dict]:
    """运行检查工具只读打开这次运行的 SQLite，返回续接对比里各决策的链字段和全运行合计。"""
    store = engine.context.store
    point = store.last_event_seq
    with inspect_run._connect(store.run_dir) as conn:
        report = inspect_run.continuation_report(conn)
    assert store.last_event_seq == point
    rows = [{key: row[key] for key in ("decision_id", "same_second_follow_up", "chain_position", "chain_intact")}
            for row in report["decisions"]]
    return rows, report["summary"]


@pytest.mark.asyncio
async def test_the_continuation_report_reads_a_chain_through_a_start_group_as_intact(_engine_factory) -> None:
    """同一秒三次决策：修订今日计划（链首），整组被拒的开始组（续接修订，带一条 tool 消息），开始等待（续接开始组，
    带两条 tool 消息）。运行检查工具从这次运行的 SQLite 读回：链位置依次是 0、1、2，后两次链完整，没有断链，最长链三次。
    对照：把落库的续接请求里开始组的两条 tool 消息对调，或只留一条，同一份读数就判为断链。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", _plan_reply())
    calls = [("call-main", MAIN_TOOL, _WAIT), ("call-side", SIDE_TOOL, side_arguments("purchase", "纸巾"))]
    _, [grouped], _ = await _single(environment, "group", [_GroupReply(calls, "歇着顺手买包纸巾。")])
    assert engine.context.state.activities == {} and [item["role"] for item in grouped["messages"][2:]] == [
        "assistant", "tool"]
    _, [request], _ = await _single(environment, "wait", _wait_reply())
    assert [item["role"] for item in request["messages"][len(grouped["messages"]):]] == ["assistant", "tool", "tool"]
    rows, summary = _chain_report(engine)
    assert rows == [
        {"decision_id": "decision-plan-1", "same_second_follow_up": False, "chain_position": 0, "chain_intact": None},
        {"decision_id": "decision-group-1", "same_second_follow_up": True, "chain_position": 1, "chain_intact": True},
        {"decision_id": "decision-wait-1", "same_second_follow_up": True, "chain_position": 2, "chain_intact": True}]
    assert (summary["decisions"], summary["continued"], summary["broken_chains"], summary["longest_chain"]) == (
        3, 2, 0, 3)
    with inspect_run._connect(engine.context.store.run_dir) as conn:
        decisions = inspect_run._decisions(conn)
    for tamper in (lambda tools: tools[::-1], lambda tools: tools[:1], lambda tools: [*tools, tools[-1]]):
        broken = copy.deepcopy(decisions)
        messages = broken[2]["calls"][0][0]["data"]["request"]["messages"]
        messages[-2:] = tamper(messages[-2:])
        assert [row["chain_intact"] for row in inspect_run._chain_rows(broken)] == [None, True, False]


def _tool_payload(request: dict) -> dict:
    """续接请求末尾那条 tool 消息的 JSON 正文。"""
    return json.loads(request["messages"][-1]["content"])


async def _workspace_person(environment: LifeEnv, workspace: Path, responses: list) -> tuple[LifeAgent, _Script]:
    """启用记忆的真实 LifeAgent：首次在工作区新建，此后每次决策都从工作区重建（与 AS2 每次决策重建人物相同）。"""
    if not (workspace / "AGENT.json").is_file():
        LifeAgent.create(workspace, current_profile(),
                         {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 8})
    dispatcher = _Script(responses)
    service = SimpleNamespace(env=environment, llm=SimpleNamespace(default=dispatcher), trace=None)
    return await LifeAgent.from_workspace(workspace, service), dispatcher


async def _step(environment: LifeEnv, person: LifeAgent, round_id: str) -> str:
    """真实人物步骤：开轮、观察、决策（含记忆查询与协议纠错回合）并登记唯一世界请求，然后结算。"""
    ctx = context(environment.engine, round_id)
    await environment.begin_decision_round((ctx,))
    person.bind_decision_context(ctx)
    registered = await person.step(0, ctx.observed_at)
    await environment.commit_decision_round(round_id)
    return registered


@pytest.mark.asyncio
async def test_a_decision_with_memory_and_correction_turns_is_continued_byte_for_byte(_engine_factory,
                                                                                    tmp_path) -> None:
    """真实决策 k 先做一次记忆查询、再有一次协议纠错回合，最后修订今日计划；同一秒决策 k+1 的前缀逐字节等于 k 的末次
    请求（system、链首 user、记忆查询与纠错的原生回合，原样带各自的思考），其后是 k 的最终 assistant 与一条只写改动的
    tool 消息；记忆条目只在那条原生 tool 消息里出现一次，不进任何一层（第二章「消息布局」、第一章「唯一出现原则」）。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    workspace = tmp_path / "agent_0001"
    person, first = await _workspace_person(environment, workspace, [
        _reply("call-memory", "memory_recent", {"limit": 5}, "先翻翻记忆。"),
        _reply("call-bad", MAIN_TOOL, _UNKNOWN, "那就先等一会儿。"), *_plan_reply()])
    person._memory_runtime.store.append_episodes(
        _stored_memories(_conclude_args("散步（1月4日 19:00 起）", memories=[[_memory("散步时想好了喝咖啡。")]])),
        tick=0, t=_OBSERVED_AT, step_count=1)
    assert await _step(environment, person, "plan") == "revise_day_plan registered"
    last = first.requests[-1]
    assert len(first.requests) == 3
    assert [item["role"] for item in last["messages"]] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert [item.get("reasoning_content") for item in last["messages"][2::2]] == ["先翻翻记忆。", "那就先等一会儿。"]
    assert "散步时想好了喝咖啡。" in last["messages"][3]["content"]
    assert json.loads(last["messages"][5]["content"])["error_code"] == "NAME_NOT_FOUND"

    person, second = await _workspace_person(environment, workspace, _wait_reply())
    assert await _step(environment, person, "wait") == f"{MAIN_TOOL} registered"
    [request] = second.requests
    size = len(last["messages"])
    assert json.dumps(request["messages"][:size], ensure_ascii=False) == json.dumps(last["messages"],
                                                                                    ensure_ascii=False)
    assistant, tool = request["messages"][size:]
    assert (assistant["reasoning_content"], assistant["tool_calls"][0]["id"]) == (_THINK_PLAN, "call-plan")
    assert tool["tool_call_id"] == "call-plan" and list(json.loads(tool["content"])) == ["result", "changes"]
    assert request["tools"] == last["tools"]
    assert json.dumps(request, ensure_ascii=False).count("散步时想好了喝咖啡。") == 1


def _ended(names: list[str], summary: str) -> dict:
    """给点名的活动收尾的参数：一项一个对象，写同一句自述，不写记忆与看法；没有个人事务变更。"""
    return {"items": [{"activity_name": name, "summary": summary, "memories": [], "views": []} for name in names],
            "errands": []}


def _views() -> list[dict]:
    """一次收尾写下的看法与偏好（写在 views，about 写对象，只在人设层出现）。"""
    return [_view("老周", "老周一向靠谱。"), _view("咖啡", "下午想喝拿铁。", kind="preference")]


@pytest.mark.asyncio
async def test_changes_overlay_relationships_and_preferences_so_later_tool_messages_do_not_repeat_them(
        _engine_factory) -> None:
    """同一秒三次决策：先给刚结束的等待收尾并写下对老周的看法与对咖啡的偏好，第二次决策的 tool 消息以 changes 交还人设层
    关系项的更新（完整条目）与新偏好；第三次决策的 tool 消息相对「链首快照叠加此前 changes」计算，看法与偏好已经交还过，
    不再重复，只写今日计划的改动（第二章 tool 消息 changes「以最新为准」）。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "wait", _wait_reply(reasoning="先等一刻钟。"))
    assert advance_until_ready(engine)

    def conclude(observation: dict) -> list:
        [name] = pending_names(observation)
        arguments = {"items": [{"activity_name": name, "summary": "等完了。", "memories": [], "views": _views()}],
                     "errands": []}
        return [_reply("call-end", "conclude_activity", arguments, "等完了，记下看法。")]

    head, _, _ = await _single(environment, "end", conclude)
    _, [second], _ = await _single(environment, "plan", _plan_reply())
    observation, [third], _ = await _single(environment, "again", _wait_reply("call-wait-again"))
    persona = json.loads(second["messages"][1]["content"])[1]
    [friend] = [item for item in persona["relationships"] if item["person"] == "老周"]
    assert friend["view"] is None and persona["preferences"] == []
    changes = _tool_payload(second)["changes"]
    assert changes["relationships"] == [{**friend, "view": "老周一向靠谱。"}]
    assert changes["preferences"] == [{"subject": "咖啡", "preference": "下午想喝拿铁。",
                                       "noted_on": observation["environment_layer"]["observed_at"][:10]}]
    assert pending_names(head)[0] in changes["targets"]["removed"]
    assert _tool_payload(third)["changes"] == {"day_plan": observation["todo_layer"]["day_plan"]}
    assert third["messages"][:len(second["messages"])] == second["messages"]






#: 同一秒续接链里新增的两条个人事务（正式目录里的真实商品）：一条带备注与提醒钟点，一条要取得三份。
_SNACK_ERRAND = errand_add("周末补薯片", "薯片", memo="下班顺路去超市", remind_time="18:00")
_TEA_ERRAND = errand_add("囤三杯柠檬茶", "柠檬茶", quantity=3)


@pytest.mark.asyncio
async def test_errand_changes_reach_the_next_tool_message_as_added_and_removed(_engine_factory) -> None:
    """真实收尾新增两条事务，同秒后续收尾更改或取消，仅回报未出现的变化。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "interact", _wait_reply())
    advance_until_ready(engine)

    def note(observation):
        [name] = pending_names(observation)
        return [_reply("call-note", "conclude_activity", {
            **_ended([name], "聊完了。"), "errands": [_SNACK_ERRAND, _TEA_ERRAND]}, "把要买的记下。")]

    head, _, _ = await _single(environment, "note", note)
    rows, requests, _ = await _single(environment, "plan", _plan_reply())
    [second] = requests
    earlier = _tool_payload(second)
    assert earlier["changes"]["errands"] == {"added": rows["todo_layer"]["errands"]}
    assert head["todo_layer"]["errands"] == []
    assert [row["name"] for row in rows["todo_layer"]["errands"]] == ["周末补薯片", "囤三杯柠檬茶"]
    assert earlier["result"]["details"]["errands"] == [
        {"errand": "周末补薯片", "change": "新增"}, {"errand": "囤三杯柠檬茶", "change": "新增"}]
    current, [third], _ = await _single(environment, "again", _wait_reply("call-again"))
    assert _tool_payload(third)["changes"] == {"day_plan": current["todo_layer"]["day_plan"]}
    assert third["messages"][:len(second["messages"])] == second["messages"]


@pytest.mark.asyncio
async def test_the_pre_send_check_fails_when_the_current_decision_lacks_thinking(_engine_factory) -> None:
    """带续接链的请求发送前自检：本次决策内的 assistant（协议纠错回合）没有 reasoning_content 时以英文错误失败。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", _plan_reply())
    ctx = context(engine, "wait")
    engine.begin_decision_round([ctx])
    bad = _reply("call-bad", MAIN_TOOL, _UNKNOWN, None)
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
    request = {"model": "test-model", "messages": chain, "tools": [], "max_tokens": 10}
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
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: _window_above(fresh_size))
    fitted = fit_request(request, current_inputs().model_metadata, budget)
    assert fitted["messages"] == list(budget.fresh_messages)
    assert len(request["messages"]) == 4
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: _window_above(noteless_size))
    condensed = fit_request(request, current_inputs().model_metadata, budget)
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
    bad = _reply("call-bad", MAIN_TOOL, _UNKNOWN, "先试一个。")
    observation, requests, _ = await _single(environment, "wait", [bad, *_wait_reply()])
    assert observation["continuation"] is not None and len(requests) == 2
    for request in requests:
        assert _THINK_PLAN not in json.dumps(request, ensure_ascii=False)
        assert json.loads(request["messages"][1]["content"])[5] == observation["progress_layer"]
    assert [item["role"] for item in requests[1]["messages"]] == ["system", "user", "assistant", "tool"]


def _switch_off_engine(tmp_path: Path) -> LifeEngine:
    """以关闭同秒续接的阶段身份新建当前环境运行。"""
    from tests.integration.runtime_fixtures import current_run_identity
    frozen = current_inputs()
    return LifeEngine.create(frozen, tmp_path / "switch-off", current_run_identity(frozen, "switch-off"),
        __import__("worldmock.run_setup", fromlist=["stage_model_identity"]).stage_model_identity(
            frozen.config.model, frozen.model_metadata, False),
        {"same_second_continuation": False})


@pytest.mark.asyncio
async def test_switch_off_every_decisions_first_request_has_exactly_two_messages(tmp_path) -> None:
    """开关关闭（冻结进清单）：同一秒的后续决策也从新快照构造，每次决策首个请求恰好两条消息。"""
    engine = _switch_off_engine(tmp_path)
    try:
        assert engine.context.store.manifest["stage_model_identity"]["stages"]["action"]["same_second_continuation"] is False
        run = await _plan_then_wait(engine)
        assert run["observation"]["continuation"] is None
        assert [len(requests[0]["messages"]) for requests in (run["first"], run["second"])] == [2, 2]
        assert _THINK_PLAN not in json.dumps(run["second"], ensure_ascii=False)
    finally:
        engine.close()


def test_continuation_messages_read_earlier_changes_and_skip_memory_and_feedback_tool_messages() -> None:
    """宿主组装：链中此前的续接 tool 消息的 changes 叠加到链首快照上，记忆查询与协议纠错的 tool 消息不算改动；
    新 tool 消息只写相对叠加结果的改动。tool_results 有两条（开始组）时每个 tool_call_id 各一条 tool 消息、按调用次序，
    changes 与 events 只写在最后一条上，前一条只有 result。"""
    from worldmock.agents.life_continuation import continuation_messages

    def layers(state: str, body: str) -> list[dict]:
        return [{"layer": "工具层", "targets": [{"name": "店", "kind": "location", "state": state,
                                                   "actions": [], "tools": []}], "callable_index": {}},
                {"layer": "人设层", "relationships": [], "preferences": []}, {"layer": "记忆层"},
                {"layer": "待办层", "paused_activities": [], "errands": [], "day_plan": None},
                {"layer": "环境层", "body": {"appetite": body}}, {"layer": "进度层"}]

    call = {"id": "call-earlier", "type": "function", "function": {"name": "report_status", "arguments": "{}"}}
    earlier = {"result": {"status": "completed"}, "changes": {"body": {"appetite": "有点饿"}}}
    feedback = {"error_code": "NAME_NOT_FOUND", "reason": "bad", "fix": "fix", "items": [], "unmet": [],
                "instruction": "本次没有登记到世界；按 fix 修改后重新提交"}
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
        "tool_results": [{"tool_call_id": "call-last", "result": {"status": "completed"}}], "events": []}
    changes = {"targets": {"updated": [{"name": "店", "state": "打烊", "kind": "location", "actions": [],
                                        "tools": [], "phase": None}]}}
    messages = continuation_messages(chain, layers("打烊", "有点饿"))
    assert messages[:9] == [*chain["messages"], chain["assistant"]] and len(messages) == 10
    assert (messages[-1]["role"], messages[-1]["tool_call_id"]) == ("tool", "call-last")
    assert json.loads(messages[-1]["content"]) == {"result": {"status": "completed"}, "changes": changes}
    main, side = {"status": "in_progress", "message": "main"}, {"status": "in_progress", "message": "side"}
    arrived = {"occurred_at": "2026-01-05T08:00:00+08:00", "type": "delivery", "text": "外卖到了"}
    pair = {**chain["assistant"], "tool_calls": [{**_CALL, "id": "call-main"}, _SIDE_CALL]}
    group = {**chain, "assistant": pair, "events": [arrived], "tool_results": [
        {"tool_call_id": "call-main", "result": main}, {"tool_call_id": "call-side", "result": side}]}
    started = continuation_messages(group, layers("打烊", "有点饿"))
    assert started[:9] == [*chain["messages"], pair] and len(started) == 11
    assert [(item["role"], item["tool_call_id"]) for item in started[9:]] == [("tool", "call-main"),
                                                                              ("tool", "call-side")]
    assert json.loads(started[9]["content"]) == {"result": main}
    assert json.loads(started[10]["content"]) == {"result": side, "changes": changes, "events": [arrived]}
    assert continuation_messages(None, layers("打烊", "有点饿")) is None


def test_layer_changes_overlay_earlier_changes_and_keep_the_latest() -> None:
    """changes 相对于模型所见：链首快照依次叠加此前各 changes，以最新为准；没有改动的键不写。"""
    def layers(targets, paused, plan, body, views):
        return [{"layer": "工具层", "targets": [{**row, "kind": "location", "actions": [], "tools": []}
                                                   for row in targets], "callable_index": {}}, {"layer": "人设层", "relationships": views, "preferences": []},
                {"layer": "记忆层"}, {"layer": "待办层", "paused_activities": paused, "errands": [], "day_plan": plan},
                {"layer": "环境层", "body": body}, {"layer": "进度层"}]

    shop, desk = {"name": "店", "state": "营业"}, {"name": "工位", "state": None}
    paused = {"name": "读书（9月28日 09:00 起）", "place": "家"}
    head = layers([shop, desk], [paused], None, {"appetite": "不饿"}, [{"person": "王磊", "view": None}])
    earlier = {"targets": {"removed": ["工位"]}, "body": {"appetite": "有点饿"}}
    current = layers([{**shop, "state": "打烊"}], [], None, {"appetite": "有点饿"},
                     [{"person": "王磊", "view": "靠谱"}])
    assert layer_changes(head, [earlier], current) == {
        "targets": {"updated": [{"name": "店", "state": "打烊", "kind": "location", "actions": [],
                                                                                   "tools": [], "phase": None}]},
        "paused_activities": {"removed": ["读书（9月28日 09:00 起）"]},
        "relationships": [{"person": "王磊", "view": "靠谱"}]}
    assert layer_changes(head, [], head) == {}


def test_layer_changes_report_errand_rows_added_changed_and_removed() -> None:
    """待办层个人事务按名称进 changes（docs/design/04-simulation-engine.md 「同秒续接」）：added 是此刻新增或内容有变的
    完整行（此刻顺序），removed 是已不在待办层的名称（办成或取消）；相对模型所见，此前 changes 已移除或已写入的不再重报；
    errands 写在 paused_activities 之后、day_plan 之前；没有改动不写。"""
    def layers(paused, errands, plan):
        return [{"layer": "工具层", "targets": [], "callable_index": {}},
                {"layer": "人设层", "relationships": [], "preferences": []}, {"layer": "记忆层"},
                {"layer": "待办层", "paused_activities": paused, "errands": errands, "day_plan": plan},
                {"layer": "环境层", "body": {"appetite": "不饿"}}, {"layer": "进度层"}]

    def row(name: str, progress: str = "已取得 0 份", memo: str | None = None) -> dict:
        return {"name": name, "done_when": f"取得{name} 1 份", "progress": progress, "memo": memo,
                "remind_time": None, "noted_on": "2026-01-05"}

    kit, tea, meal = row("食材包"), row("冰红茶"), row("素炒什锦")
    paused = {"name": "读书（9月28日 09:00 起）", "place": "家"}
    head = layers([paused], [kit, tea], None)
    earlier = {"errands": {"removed": ["冰红茶"], "added": [meal]}}
    noted = {**kit, "memo": "下班前下单"}
    plan = {"date": "2026-01-05", "goals": []}
    current = layers([], [noted, meal, row("牛奶")], plan)
    changes = layer_changes(head, [earlier], current)
    assert changes == {"paused_activities": {"removed": ["读书（9月28日 09:00 起）"]},
                       "errands": {"added": [noted, row("牛奶")]}, "day_plan": plan}
    assert list(changes) == ["paused_activities", "errands", "day_plan"]
    assert layer_changes(head, [earlier], layers([paused], [meal], None)) == {"errands": {"removed": ["食材包"]}}
    assert layer_changes(head, [], layers([paused], [kit, tea], None)) == {}
    assert layer_changes(head, [earlier, {"errands": {"added": [noted]}}], layers([paused], [noted, meal], None)) == {}








@pytest.mark.parametrize("command", ["resume", "replay"])
def test_resume_and_replay_cannot_override_the_switch(command, tmp_path) -> None:
    """恢复与重放继承父清单的值：命令行不接受续接开关。"""
    from worldmock.cli import build_parser

    arguments = [command, "--environment", str(SCENARIO), "--output", str(tmp_path / "child"), "--parent",
                 str(tmp_path / "parent"), "--checkpoint", "checkpoint"]
    if command == "replay":
        arguments += ["--intervention", str(tmp_path / "intervention.json")]
    assert build_parser().parse_args(arguments).command == command
    with pytest.raises(SystemExit):
        build_parser().parse_args([*arguments, "--same-second-continuation", "off"])
