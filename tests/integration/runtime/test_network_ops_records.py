"""日计划与回顾失败及传输等待的模型运维日志证据。"""

from types import SimpleNamespace

import pytest
from litellm.exceptions import APIConnectionError, AuthenticationError

from tests.integration.life.test_opslog import _records
from tests.unit.runtime.model_client_fixtures import draft_request, provider_error, scripted_client
from worldmock.runtime.lifecycle.midnight import _CallSpec, _call_background_model
from worldmock.simulation_engine.evidence import memory_digest
from worldmock.simulation_engine.scheduling.plans import day_planning
from worldmock.simulation_engine.scheduling.plans.day_planning import request_draft, response_draft


@pytest.mark.parametrize("fails", [False, True])
async def test_plan_day_call_records_attempts_and_outcome(engine_factory, monkeypatch, fails):
    """真实模型客户端的日计划请求成功与失败均记录发送、调用耗时和传输等待。"""
    engine = engine_factory()
    kind = AuthenticationError if fails else APIConnectionError
    raw = {"choices": [], "usage": {"prompt_tokens": 10}}
    client, _, _ = scripted_client(monkeypatch, [provider_error(kind), raw], clock=[0.0])

    async def call(request):
        return await client.call(request)

    now = engine.context.state.current_time
    request = draft_request([{"role": "user", "content": "plan"}])
    evidence = [request_draft(now, 1, ("plan", 1, None), request)]
    spec = _CallSpec(1, "plan", 1, evidence[0].event_id, "PLAN_DAY_CALL", "Plan day", day_planning.failure_draft)
    if fails:
        with pytest.raises(Exception, match="Failed to get valid response"):
            await _call_background_model(engine, SimpleNamespace(call=call), request, evidence, spec)
    else:
        response, attempts = await _call_background_model(engine, SimpleNamespace(call=call), request, evidence, spec)
        evidence.append(response_draft(now, 1, ("plan", 1, evidence[0].event_id, attempts), response, None))
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
    """真实模型客户端的回顾请求成功与失败均记录发送、调用耗时（MEMORY_DIGEST_CALL）和传输等待，零点计划的记录码不出现。"""
    engine = engine_factory()
    kind = AuthenticationError if fails else APIConnectionError
    raw = {"choices": [], "usage": {"prompt_tokens": 10}}
    client, _, _ = scripted_client(monkeypatch, [provider_error(kind), raw], clock=[0.0])

    async def call(request):
        return await client.call(request)

    now = engine.context.state.current_time
    request = draft_request([{"role": "user", "content": "digest"}])
    call_id = "memory-digest:1:day:2026-01-04:attempt:1"
    evidence = [memory_digest.request_draft(now, 1, (call_id, 1, None), request)]
    spec = _CallSpec(1, call_id, 1, evidence[0].event_id, "MEMORY_DIGEST_CALL", "Memory digest",
                     memory_digest.failure_draft)
    if fails:
        with pytest.raises(Exception, match="Failed to get valid response"):
            await _call_background_model(engine, SimpleNamespace(call=call), request, evidence, spec)
    else:
        response, attempts = await _call_background_model(
            engine, SimpleNamespace(call=call), request, evidence, spec,
        )
        evidence.append(memory_digest.response_draft(now, 1, (call_id, 1, evidence[0].event_id, attempts),
                                                     response, None))
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
