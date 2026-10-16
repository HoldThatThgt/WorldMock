"""失败发送只进入证据信封，决策、记忆与日计划的供应商响应保持原样。"""

from dataclasses import replace

import pytest
from litellm.exceptions import APIConnectionError, AuthenticationError

from agentsociety2.agent.base import ModelCallEvidence
from tests.integration.runtime.current_contract_fixtures import ToolResponse, environment, person
from tests.life.conftest import context
from tests.life.environment_runtime import events
from tests.unit.as2.test_llm_dispatcher_transport import _client, _error
from worldmock.errors import ProtocolError


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


@pytest.mark.parametrize("fails", [False, True])
async def test_memory_call_evidence_carries_attempts(engine_factory, monkeypatch, fails):
    """真实记忆分发经过一次暂时失败或最终拒绝，都把本次发送记录交给宿主落盘。"""
    engine, request = opened_exchange(engine_factory)
    raw = ToolResponse("report_status", {"items": [], "errands": []})
    kind = AuthenticationError if fails else APIConnectionError
    client, _ = _client(monkeypatch, [_error(kind), raw], clock=[0.0])
    client.model_name = engine.context.inputs.model_metadata.requested_model
    agent = person(environment(engine), request.context, client)
    arguments = {"model": agent._model_name, "messages": [{"role": "user", "content": "memory"}]}
    if fails:
        with pytest.raises(Exception, match="Failed to get valid response"):
            await agent._dispatch_memory_llm(**arguments)
    else:
        assert await agent._dispatch_memory_llm(**arguments) is raw
    [record] = events(engine, "model_response")
    assert record.data["phase"] == ("failure" if fails else "response")
    assert record.data["transport_attempts"] == [{"attempt": 1, "error": kind.__name__,
        "failed_after_ms": 0, "wait_ms": None if fails else 2000}]
