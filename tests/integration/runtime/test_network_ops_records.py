"""模型运维日志区分写入耗时、传输等待和协议拒绝，日计划与回顾失败也保留证据。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from litellm.exceptions import APIConnectionError, AuthenticationError

from tests.integration.life.test_opslog import _records
from tests.integration.runtime.test_model_exchange_attempts import ATTEMPTS, opened_exchange
from tests.unit.as2.test_llm_dispatcher_transport import _client, _error
from worldmock.simulation_engine.evidence import memory_digest
from worldmock.simulation_engine.scheduling.plans.day_planning import request_draft, response_draft
from worldmock.runtime.lifecycle.runner import _call_memory_digest, _call_plan_day


@pytest.mark.parametrize("phase,code", [("response", None), ("response", "TOOL_CALL_REQUIRED"),
                                       ("failure", "MODEL_SERVICE_FAILED")])
@pytest.mark.parametrize("attempts", [(), ATTEMPTS])
def test_model_exchange_logs_separate_commit_wait_and_protocol_failure(engine_factory, phase, code, attempts):
    """每条证据报告写入耗时，仅失败发送产生等待日志，仅纠错响应产生协议拒绝日志。"""
    engine, request = opened_exchange(engine_factory)
    engine.record_model_exchange(request)
    raw = {"protocol_feedback": {"error_code": code}} if code else {"choices": []}
    engine.record_model_exchange(replace(request, phase=phase, response=raw, transport_attempts=attempts))
    records = _records(engine)
    exchange = [row for row in records if row["code"] == "MODEL_EXCHANGE"]
    assert len(exchange) == 2
    assert all(isinstance(row["commit_ms"], float) and row["commit_ms"] >= 0 for row in exchange)
    waits = [row for row in records if row["code"] == "MODEL_TRANSPORT_WAIT"]
    assert len(waits) == bool(attempts)
    if waits:
        assert waits[0]["failed_sends"] == 2 and waits[0]["waited_ms"] == 2000
        assert waits[0]["last_error"] == "Timeout"
    rejected = [row for row in records if row["code"] == "MODEL_PROTOCOL_REJECTED"]
    assert [row["error_code"] for row in rejected] == ([code] if phase == "response" and code else [])


@pytest.mark.parametrize("fails", [False, True])
async def test_plan_day_call_records_attempts_and_outcome(engine_factory, monkeypatch, fails):
    """真实分发器的日计划请求成功与失败均记录发送、调用耗时和传输等待。"""
    engine = engine_factory()
    kind = AuthenticationError if fails else APIConnectionError
    raw = {"choices": [], "usage": {"prompt_tokens": 10}}
    client, _ = _client(monkeypatch, [_error(kind), raw], clock=[0.0])

    async def call(request):
        return await client.call(**request)

    now = engine.context.state.current_time
    request = {"messages": [{"role": "user", "content": "plan"}]}
    evidence = [request_draft(now, 1, ("plan", 1, None), request)]
    link = ("plan", 1, evidence[0].event_id)
    if fails:
        with pytest.raises(Exception, match="Failed to get valid response"):
            await _call_plan_day(engine, SimpleNamespace(call=call), request, (evidence, link, 1))
    else:
        response, attempts = await _call_plan_day(engine, SimpleNamespace(call=call), request, (evidence, link, 1))
        evidence.append(response_draft(now, 1, (*link, attempts), response, None))
        engine.context.store.append_evidence(evidence)
        assert response == raw
    rows = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
    response = next(row for row in rows if row.event_type == "model_response")
    assert response.data["transport_attempts"] == [{"attempt": 1, "error": kind.__name__,
        "failed_after_ms": 0, "wait_ms": None if fails else 2000}]
    records = _records(engine)
    [record] = [row for row in records if row["code"] == "PLAN_DAY_CALL"]
    assert record["outcome"] == ("failure" if fails else "response")
    assert record["level"] == ("error" if fails else "info")
    assert record["call_ms"] >= 0
    assert len([row for row in records if row["code"] == "MODEL_TRANSPORT_WAIT"]) == 1


@pytest.mark.parametrize("fails", [False, True])
async def test_memory_digest_call_records_attempts_and_outcome(engine_factory, monkeypatch, fails):
    """真实分发器的回顾请求成功与失败均记录发送、调用耗时（MEMORY_DIGEST_CALL）和传输等待，零点计划的记录码不出现。"""
    engine = engine_factory()
    kind = AuthenticationError if fails else APIConnectionError
    raw = {"choices": [], "usage": {"prompt_tokens": 10}}
    client, _ = _client(monkeypatch, [_error(kind), raw], clock=[0.0])

    async def call(request):
        return await client.call(**request)

    now = engine.context.state.current_time
    request = {"messages": [{"role": "user", "content": "digest"}]}
    call_id = "memory-digest:1:day:2026-01-04:attempt:1"
    evidence = [memory_digest.request_draft(now, 1, (call_id, 1, None), request)]
    link = (call_id, 1, evidence[0].event_id)
    if fails:
        with pytest.raises(Exception, match="Failed to get valid response"):
            await _call_memory_digest(engine, SimpleNamespace(call=call), request, (evidence, link, 1))
    else:
        response, attempts = await _call_memory_digest(engine, SimpleNamespace(call=call), request,
                                                       (evidence, link, 1))
        evidence.append(memory_digest.response_draft(now, 1, (*link, attempts), response, None))
        engine.context.store.append_evidence(evidence)
        assert response == raw
    rows = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
    response = next(row for row in rows if row.event_type == "model_response")
    assert response.data["purpose"] == "memory_digest"
    assert response.data["phase"] == ("failure" if fails else "response")
    assert response.data["transport_attempts"] == [{"attempt": 1, "error": kind.__name__,
        "failed_after_ms": 0, "wait_ms": None if fails else 2000}]
    records = _records(engine)
    [record] = [row for row in records if row["code"] == "MEMORY_DIGEST_CALL"]
    outcome = "failure" if fails else "response"
    assert record["outcome"] == outcome and record["level"] == ("error" if fails else "info")
    assert record["detail"] == f"Memory digest call {call_id} ended with {outcome}"
    assert record["agent_id"] == 1 and record["call_id"] == call_id and record["call_ms"] >= 0
    assert not [row for row in records if row["code"] == "PLAN_DAY_CALL"]
    [wait] = [row for row in records if row["code"] == "MODEL_TRANSPORT_WAIT"]
    assert wait["level"] == "warn" and wait["agent_id"] == 1
