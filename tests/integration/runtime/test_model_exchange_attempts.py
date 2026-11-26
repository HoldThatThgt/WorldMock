"""失败发送只进入证据信封，决策、搜索、日计划与回顾的供应商响应保持原样；重试后的提示与无失败时逐字节相同。"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from litellm.exceptions import APIConnectionError, AuthenticationError, BadGatewayError

from tests.integration.runtime.current_contract_fixtures import resolved_binding
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse
from tests.life.conftest import MAIN_TOOL, main_arguments, plan_day_response, stage_day_plan, stub_day_session
from tests.life.environment_runtime import events
from tests.unit.runtime.model_client_fixtures import provider_error, scripted_client
from worldmock.agents.life_agent import LifeAgent, person_inputs
from worldmock.configuration.model.model_client import REQUEST_KEYS, LLMDispatchError
from worldmock.configuration.model.model_registry import ModelRegistry
from worldmock.foundation.errors import ProtocolError
from worldmock.runtime.lifecycle.midnight import settle_pending_day_plans, settle_pending_memory_digests
from worldmock.state.model.exchange import ModelCallEvidence


ATTEMPTS = (
    {"attempt": 1, "error": "APIConnectionError", "failed_after_ms": 10, "wait_ms": 2000},
    {"attempt": 2, "error": "Timeout", "failed_after_ms": 2020, "wait_ms": None},
)


def _registry(engine, client) -> ModelRegistry:
    """把脚本或重试客户端包装进真实 ModelRegistry。"""
    return ModelRegistry(
        engine.context.inputs.config,
        engine.context.inputs.model_metadata,
        client_factory=lambda _profile: client,
    )


def _opened_sandbox(engine_factory):
    """准备屏障、开启沙盒并返回首个模型调用请求证据模板。"""
    engine = engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    req = ModelCallEvidence(wakeup.wakeup_id, 1, "call", "request", 1, {"messages": []})
    return engine, wakeup, sandbox, req


@pytest.mark.parametrize("phase", ["response", "failure"])
def test_response_and_failure_events_carry_transport_attempts_in_the_envelope(engine_factory, phase):
    """沙盒证据草稿在响应或失败信封中保留 transport_attempts，供应商原始 response 不增加字段。"""
    engine, wakeup, sandbox, request = _opened_sandbox(engine_factory)
    sandbox.record_model_exchange(request)
    raw = {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}
    sandbox.record_model_exchange(replace(request, phase=phase, response=raw, transport_attempts=ATTEMPTS))
    outcome = sandbox.finish_wakeup(None, stub_day_session(wakeup))
    engine.commit_barrier([outcome])
    [stored_request] = events(engine, "model_request")
    [stored_response] = events(engine, "model_response")
    assert stored_request.data["transport_attempts"] == []
    assert stored_response.data["transport_attempts"] == list(ATTEMPTS)
    assert stored_response.data["response"] == raw
    assert "transport_attempts" not in raw


def test_sandbox_rejects_mismatched_ownership_or_missing_request_evidence(engine_factory):
    """模型证据归属不符或缺少先导请求证据时被沙盒当场以 ProtocolError 拒绝。"""
    _, wakeup, sandbox, request = _opened_sandbox(engine_factory)
    with pytest.raises(ProtocolError, match="Model exchange ownership mismatch"):
        sandbox.record_model_exchange(replace(request, wakeup_id="wrong-wakeup"))
    with pytest.raises(ProtocolError, match="Model exchange lacks recorded request"):
        sandbox.record_model_exchange(replace(request, call_id="unknown-call", phase="response", response={}))
    sandbox.record_model_exchange(request)
    with pytest.raises(ProtocolError, match="differs from its recorded request"):
        sandbox.record_model_exchange(replace(request, phase="response", request={"messages": [{"role": "user"}]},
                                              response={}))


SENT_AGAIN = {"attempt": 1, "error": "APIConnectionError", "failed_after_ms": 0, "wait_ms": 2000}


def _plan_day_registry(monkeypatch, engine, script) -> ModelRegistry:
    profile = engine.context.inputs.config.models[engine.context.inputs.config.model_routing.plan_day]
    client, _, _ = scripted_client(
        monkeypatch, script, clock=[0.0],
        max_retries=profile.max_retries, transport_max_retries=profile.transport_max_retries,
    )
    return _registry(engine, client)


@pytest.mark.parametrize("script,attempts", [
    ((APIConnectionError, "plan"), [[SENT_AGAIN]]),
    ((APIConnectionError, "invalid", "plan"), [[SENT_AGAIN], []]),
    ((APIConnectionError, AuthenticationError), [[SENT_AGAIN, {"attempt": 2, "error": "AuthenticationError",
                                                               "failed_after_ms": 2000, "wait_ms": None}]]),
], ids=["response", "validation-retry", "failure"])
async def test_plan_day_evidence_carries_attempts(engine_factory, monkeypatch, script, attempts):
    """零点计划经 ModelRegistry 与真实客户端：每次调用失败发送进入自己的响应或失败证据信封。"""
    engine = engine_factory()
    req_model = engine.context.inputs.config.models[engine.context.inputs.config.model_routing.plan_day].requested_model
    answers = {"plan": plan_day_response(model=req_model),
               "invalid": plan_day_response(arguments="not json", model=req_model)}
    registry = _plan_day_registry(
        monkeypatch, engine, [answers[item] if isinstance(item, str) else provider_error(item) for item in script],
    )
    fails = script[-1] is AuthenticationError
    if fails:
        with pytest.raises(LLMDispatchError, match="Failed to get valid response after 2 attempts"):
            await settle_pending_day_plans(engine, registry)
    else:
        assert [item["agent_id"] for item in await settle_pending_day_plans(engine, registry)] == [1]
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
    """日回顾经 ModelRegistry 与真实客户端：每次调用失败发送进入响应证据信封，供应商响应不增加字段。"""
    from tests.integration.life.test_memory_digest_runtime import _late, _night
    from tests.life.conftest import StubMemoryDigestClient, memory_digest_response
    from worldmock.simulation_engine.evidence import memory_digest

    engine = engine_factory(mutate=_late)
    _night(engine)
    [inputs] = memory_digest.pending_inputs(engine.context, engine.observations, 1, "day")
    profile = engine.context.inputs.config.models[engine.context.inputs.config.model_routing.digest]
    req_model = profile.requested_model
    answers = {"digest": memory_digest_response(StubMemoryDigestClient({"requested_model": req_model}).answer(inputs.payload),
                                                model=req_model),
               "invalid": memory_digest_response("not json", model=req_model)}
    client, _, _ = scripted_client(
        monkeypatch, [answers[item] if isinstance(item, str) else provider_error(item) for item in script],
        clock=[0.0], max_retries=profile.max_retries, transport_max_retries=profile.transport_max_retries,
    )
    assert [item["period"] for item in await settle_pending_memory_digests(engine, _registry(engine, client))] == ["2026-01-05"]
    stored = [row.data for row in events(engine, "model_response") if row.data["purpose"] == "memory_digest"]
    assert [row["phase"] for row in stored] == ["response"] * len(attempts)
    assert [row["transport_attempts"] for row in stored] == attempts
    assert all("transport_attempts" not in (row["response"] or {}) for row in stored)


@pytest.mark.parametrize("budget", [1, 0])
async def test_midnight_drafts_carry_exactly_the_six_keys_and_sends_use_the_frozen_budgets(
    engine_factory, monkeypatch, budget,
):
    """零点计划按冻结档案重试预算发送，请求底稿恰为六键且 litellm 收到同值参数。"""
    engine = engine_factory(mutate=lambda _persona, config: config["models"]["reasoner"].__setitem__("max_retries", budget))
    profile = engine.context.inputs.config.models[engine.context.inputs.config.model_routing.plan_day]
    client, _, sent = scripted_client(
        monkeypatch, [provider_error(BadGatewayError), plan_day_response(model=profile.requested_model)],
        clock=[0.0], max_retries=profile.max_retries, transport_max_retries=profile.transport_max_retries,
    )
    registry = _registry(engine, client)
    if budget:
        assert [item["agent_id"] for item in await settle_pending_day_plans(engine, registry)] == [1]
    else:
        with pytest.raises(LLMDispatchError, match="after 1 attempts"):
            await settle_pending_day_plans(engine, registry)
    drafts = [row.data["request"] for row in events(engine, "model_request") if row.data["purpose"] == "plan_day"]
    stored = [row.data for row in events(engine, "model_response") if row.data["purpose"] == "plan_day"]
    assert profile.max_retries == budget and len(drafts) == 1 and sorted(drafts[0]) == sorted(REQUEST_KEYS)
    assert len(sent) == budget + 1
    for arguments in sent:
        assert arguments["model"] == f"openai/{drafts[0]['model']}"
        assert {key: arguments[key] for key in REQUEST_KEYS[1:]} == {key: drafts[0][key] for key in REQUEST_KEYS[1:]}
    if budget:
        assert [row["phase"] for row in stored] == ["response"]
        assert stored[0]["transport_attempts"] == [
            {"attempt": 1, "error": "BadGatewayError", "failed_after_ms": 0, "wait_ms": 0}]
        assert engine.context.state.agents[1].plan_submitted_dates
    else:
        assert [row["phase"] for row in stored] == ["failure"]
        assert not engine.context.state.agents[1].plan_submitted_dates


_IN_LOOP = (
    ("call-invented", "invented_tool", {}, "先试一个别的工具。", ""),
    ("call-plan", "revise_day_plan", {"goals": [{"intent_kind": "休息", "goal": "下午喝杯咖啡",
                                                 "remind_time": None, "around_time": None}]},
     "上午有点困，先把下午喝咖啡写进计划。", "  先把计划写上。  "),
    ("call-wait", MAIN_TOOL, main_arguments("interact", "家", "测试休息"), "计划写好了，先歇一会儿。", "歇一会儿。"),
)


def _wire(value) -> str:
    return json.dumps(value, ensure_ascii=False)


async def _in_loop_run(engine_factory, monkeypatch, failing: bool) -> dict:
    """一次唤醒内历经协议纠错、revise_day_plan 与开始休息三回合，返回有/无发送重试下的发送字节与尝试记录。"""
    engine = engine_factory()
    stage_day_plan(engine)
    answers = [_ModelResponse(call, tool, json.dumps(arguments, ensure_ascii=False), reasoning, note)
               for call, tool, arguments, reasoning, note in _IN_LOOP]
    script = [item for answer in answers
              for item in ((provider_error(APIConnectionError), answer) if failing else (answer,))]
    client, _, wire = scripted_client(monkeypatch, script, clock=[0.0])
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    binding = resolved_binding(client, frozen=engine.context.inputs, stage="action", agent_id=1)
    outcome = await LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1])).decide_wakeup(
        wakeup, sandbox, binding,
    )
    engine.commit_barrier([outcome])
    sent = wire[-1]
    notes = [row.data["self_note"] for row in events(engine, "action_requested")]
    return {
        "messages": _wire(sent["messages"]),
        "tools": _wire(sent["tools"]),
        "self_notes": notes,
        "attempts": [row.data["transport_attempts"] for row in events(engine, "model_response")],
    }


async def test_later_prompts_are_byte_identical_with_and_without_attempts(engine_factory, monkeypatch):
    """每次第一次发送失败并重发后，循环内后续请求的 messages、tools 与 self_note 与无失败时逐字节相同。"""
    faulted = await _in_loop_run(engine_factory, monkeypatch, failing=True)
    clean = await _in_loop_run(engine_factory, monkeypatch, failing=False)
    assert faulted.pop("attempts") == [[SENT_AGAIN]] * 3 and clean.pop("attempts") == [[]] * 3
    assert faulted == clean
    messages = json.loads(clean["messages"])
    assert [item["role"] for item in messages] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert json.loads(messages[3]["content"])["error_code"] == "TOOL_CALL_REQUIRED"
    assert clean["self_notes"] == ["先把计划写上。", "歇一会儿。"]
