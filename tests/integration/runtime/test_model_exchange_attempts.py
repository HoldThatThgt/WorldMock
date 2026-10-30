"""失败发送只进入证据信封，决策、记忆、日计划与回顾的供应商响应保持原样；之后由已存响应重建的提示与没有失败发送时逐字节相同。"""

import json
from dataclasses import replace

import pytest
from litellm.exceptions import APIConnectionError, AuthenticationError

from agentsociety2.agent.base import ModelCallEvidence, ReactLoopRequest
from agentsociety2.config.llm_dispatcher import LLMDispatchError
from tests.integration.runtime.current_contract_fixtures import ToolResponse, environment, person
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse
from tests.life.conftest import MAIN_TOOL, context, main_arguments, plan_day_response, stage_day_plan
from tests.life.environment_runtime import events
from tests.unit.as2.test_llm_dispatcher_transport import _client, _error
from worldmock.foundation.errors import ProtocolError
from worldmock.runtime.lifecycle.runner import PlanDayClient, settle_pending_day_plans


ATTEMPTS = ({"attempt": 1, "error": "APIConnectionError", "failed_after_ms": 10, "wait_ms": 2000},
            {"attempt": 2, "error": "Timeout", "failed_after_ms": 2020, "wait_ms": None})


def opened_exchange(engine_factory):
    """打开真实决策轮并取得模型证据所需的观察。"""
    engine = engine_factory()
    frozen = context(engine, "attempts")
    engine.begin_decision_round([frozen])
    engine.observe(frozen)
    return engine, ModelCallEvidence(frozen, "call", "request", 1, {"messages": []})


@pytest.mark.parametrize("phase", ["response", "failure"])
def test_response_and_failure_events_carry_transport_attempts_in_the_envelope(engine_factory, phase):
    """请求没有发送记录；响应和失败在信封中完整保留，供应商响应不增加字段。"""
    engine, request = opened_exchange(engine_factory)
    engine.record_model_exchange(request)
    raw = {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}
    engine.record_model_exchange(replace(request, phase=phase, response=raw, transport_attempts=ATTEMPTS))
    [stored_request] = events(engine, "model_request")
    [stored_response] = events(engine, "model_response")
    assert "transport_attempts" not in stored_request.data
    assert stored_response.data["transport_attempts"] == list(ATTEMPTS)
    assert stored_response.data["response"] == raw
    assert "transport_attempts" not in raw


def test_request_evidence_with_attempts_is_rejected(engine_factory):
    """请求证据携带发送记录时拒绝，数据库及待配对请求均不变。"""
    engine, request = opened_exchange(engine_factory)
    before = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError, match="Model request evidence must not carry transport attempts"):
        engine.record_model_exchange(replace(request, transport_attempts=ATTEMPTS))
    assert engine.context.store.last_event_seq == before
    assert engine.evidence.request_events == {}


@pytest.mark.parametrize("attempts", [list(ATTEMPTS), ({"attempt": 1},),
    ({**ATTEMPTS[0], "extra": 0},), ({**ATTEMPTS[0], "attempt": 2},)])
def test_malformed_attempts_are_rejected(engine_factory, attempts):
    """错误容器、缺键、多键或不连续序号必须在写库前失败，之后仍可提交有效响应。"""
    engine, request = opened_exchange(engine_factory)
    engine.record_model_exchange(request)
    before = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError, match="Model exchange transport attempts are invalid"):
        engine.record_model_exchange(replace(request, phase="response", transport_attempts=attempts))
    assert engine.context.store.last_event_seq == before
    engine.record_model_exchange(replace(request, phase="response", transport_attempts=ATTEMPTS))


#: 第一次发送以 APIConnectionError 失败、等 2 秒后重发：这次调用的那一条失败发送。
SENT_AGAIN = {"attempt": 1, "error": "APIConnectionError", "failed_after_ms": 0, "wait_ms": 2000}


def _plan_day_caller(monkeypatch, identity, script):
    """按冻结身份建立真实 PlanDayClient；它取得的是只替换 Router 的真实 LLMClient，按脚本失败或作答。"""
    import agentsociety2.config

    inner, _ = _client(monkeypatch, script, clock=[0.0])
    inner.model_name, inner.base_url = identity["requested_model"], identity["base_url"]
    monkeypatch.setattr(agentsociety2.config, "build_client_for_role", lambda role: inner)
    return PlanDayClient(identity)


@pytest.mark.parametrize("script,attempts", [
    ((APIConnectionError, "plan"), [[SENT_AGAIN]]),
    ((APIConnectionError, "invalid", "plan"), [[SENT_AGAIN], []]),
    ((APIConnectionError, AuthenticationError), [[SENT_AGAIN, {"attempt": 2, "error": "AuthenticationError",
                                                               "failed_after_ms": 2000, "wait_ms": None}]]),
], ids=["response", "validation-retry", "failure"])
async def test_plan_day_evidence_carries_attempts(engine_factory, monkeypatch, script, attempts):
    """零点计划经真实 PlanDayClient 与真实分发器：每次调用的失败发送进入这次调用自己的响应证据（含校验纠错的那次），
    最终失败时进入失败证据并抛出；供应商响应不增加字段。"""
    engine = engine_factory()
    identity = engine.context.store.manifest["stage_model_identity"]
    answers = {"plan": plan_day_response(model=identity["requested_model"]),
               "invalid": plan_day_response(arguments="not json", model=identity["requested_model"])}
    caller = _plan_day_caller(monkeypatch, identity,
                              [answers[item] if isinstance(item, str) else _error(item) for item in script])
    fails = script[-1] is AuthenticationError
    if fails:
        with pytest.raises(LLMDispatchError, match="Failed to get valid response after 2 attempts"):
            await settle_pending_day_plans(engine, caller)
    else:
        assert [item["agent_id"] for item in await settle_pending_day_plans(engine, caller)] == [1]
    stored = [row.data for row in events(engine, "model_response") if row.data["purpose"] == "plan_day"]
    assert [row["phase"] for row in stored] == (["failure"] if fails else ["response"] * len(attempts))
    assert [row["transport_attempts"] for row in stored] == attempts
    assert all("transport_attempts" not in (row["response"] or {}) for row in stored)
    assert bool(engine.context.state.agents[1].plan_submitted_dates) is not fails


@pytest.mark.parametrize("script,attempts", [
    ((APIConnectionError, "digest"), [[SENT_AGAIN]]),
    ((APIConnectionError, "invalid", "digest"), [[SENT_AGAIN], []]),
], ids=["response", "validation-retry"])
async def test_memory_digest_evidence_carries_attempts(engine_factory, monkeypatch, script, attempts):
    """日回顾经真实 MemoryDigestClient 与真实分发器：每次调用的失败发送进入这次调用自己的响应证据（含校验纠错的那次）；
    供应商响应不增加字段。"""
    from tests.integration.life.test_memory_digest_runtime import _digest_caller, _late, _night
    from tests.life.conftest import StubMemoryDigestClient, memory_digest_response
    from worldmock.runtime.lifecycle.runner import settle_pending_memory_digests
    from worldmock.simulation_engine.evidence import memory_digest

    engine = engine_factory(mutate=_late)
    _night(engine)
    [inputs] = memory_digest.pending_inputs(engine.context, engine.observations, 1, "day")
    identity = engine.context.store.manifest["stage_model_identity"]
    answers = {"digest": memory_digest_response(StubMemoryDigestClient().answer(inputs.payload),
                                                model=identity["requested_model"]),
               "invalid": memory_digest_response("not json", model=identity["requested_model"])}
    caller = _digest_caller(monkeypatch, identity,
                            [answers[item] if isinstance(item, str) else _error(item) for item in script])
    assert [item["period"] for item in await settle_pending_memory_digests(engine, caller)] == ["2026-01-05"]
    stored = [row.data for row in events(engine, "model_response") if row.data["purpose"] == "memory_digest"]
    assert [row["phase"] for row in stored] == ["response"] * len(attempts)
    assert [row["transport_attempts"] for row in stored] == attempts
    assert all("transport_attempts" not in (row["response"] or {}) for row in stored)


#: 同一秒的三次回答：本回合没有的工具（TOOL_CALL_REQUIRED 纠错）、改交修订今日计划、续接后开始在家歇一刻钟。
_SAME_SECOND = (("call-invented", "invented_tool", {}, "先试一个别的工具。", ""),
                ("call-plan", "revise_day_plan", {"goals": [{"intent_kind": "休息", "goal": "下午喝杯咖啡",
                                                             "remind_time": None, "around_time": None}]},
                 "上午有点困，先把下午喝咖啡写进计划。", "  先把计划写上。  "),
                ("call-wait", MAIN_TOOL, main_arguments("interact", "家", "测试休息"), "计划写好了，先歇一会儿。",
                 "歇一会儿。"))


def _wire(value) -> str:
    """按发送键序的 JSON 文本，逐字节比较用。"""
    return json.dumps(value, ensure_ascii=False)


async def _decided(engine, round_id, client):
    """一次单人决策轮：真实观察，真实 LifeAgent 经给定客户端决策并结算；返回观察与宿主上下文。"""
    env, frozen = environment(engine), context(engine, round_id)
    engine.begin_decision_round([frozen])
    observed = await env.observe(frozen)
    await person(env, frozen, client).run_react_loop(ReactLoopRequest(0, frozen.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    engine.commit_decision_round(round_id)
    return observed, frozen


async def _same_second_run(engine_factory, monkeypatch, failing):
    """新引擎上同一秒两次决策；failing 时每次调用的第一次发送都以 APIConnectionError 失败。返回下一决策实际发出的
    messages 与 tools、它观察的进度层、各决策的 self_note 与全部模型响应信封里的失败发送。"""
    engine = engine_factory()
    stage_day_plan(engine)
    answers = [_ModelResponse(call, tool, json.dumps(arguments, ensure_ascii=False), reasoning, note)
               for call, tool, arguments, reasoning, note in _SAME_SECOND]
    script = [item for answer in answers
              for item in ((_error(APIConnectionError), answer) if failing else (answer,))]
    client, _ = _client(monkeypatch, script, clock=[0.0])
    client.model_name = engine.context.inputs.model_metadata.requested_model
    await _decided(engine, "plan", client)
    observed, frozen = await _decided(engine, "wait", client)
    sent = client._router.requests[-1]
    notes = [row.data["self_note"] for row in events(engine, "action_requested")]
    return {"messages": _wire(sent["messages"]), "tools": _wire(sent["tools"]),
            "progress": _wire(observed["progress_layer"]), "self_notes": notes,
            "note_reader": engine.decisions._decision_self_note(frozen),
            "attempts": [row.data["transport_attempts"] for row in events(engine, "model_response")]}


async def test_later_prompts_are_byte_identical_with_and_without_attempts(engine_factory, monkeypatch):
    """同一秒先被 TOOL_CALL_REQUIRED 纠错、再交合法请求，下一决策由已存证据重建的续接消息与工具、进度层（含
    rejected_today）和 self_note，在每次第一次发送都失败、信封带失败发送时与没有失败时逐字节相同。"""
    faulted = await _same_second_run(engine_factory, monkeypatch, failing=True)
    clean = await _same_second_run(engine_factory, monkeypatch, failing=False)
    assert faulted.pop("attempts") == [[SENT_AGAIN]] * 3 and clean.pop("attempts") == [[]] * 3
    assert faulted == clean
    messages = json.loads(clean["messages"])
    assert [item["role"] for item in messages] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert json.loads(messages[3]["content"])["error_code"] == "TOOL_CALL_REQUIRED"
    assert [row["written"] for row in json.loads(clean["progress"])["rejected_today"]] == ["invented_tool"]
    assert clean["self_notes"] == ["先把计划写上。", "歇一会儿。"] and clean["note_reader"] == "歇一会儿。"
