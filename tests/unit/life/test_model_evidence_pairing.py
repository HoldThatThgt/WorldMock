"""模型请求只存一次：响应与失败事件的 request 为 null、经 caused_by 指向请求事件，带来的请求须与登记时相同。"""

import copy
import logging
from dataclasses import replace

import pytest

from tests.life.environment_runtime import events
from worldmock.foundation.errors import ProtocolError
from worldmock.state.model.exchange import ModelCallEvidence

#: 测试用的实际请求：一条用户消息。
REQUEST = {"messages": [{"role": "user", "content": "x"}]}


def _recorded_request(engine_factory):
    """开启屏障沙盒并登记一次请求；返回引擎、沙盒与这次请求的证据。"""
    engine = engine_factory()
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    request = ModelCallEvidence(wakeup_id=wakeup.wakeup_id, agent_id=wakeup.agent_id,
                                call_id="call", phase="request", attempt=1, request=copy.deepcopy(REQUEST))
    sandbox.record_model_exchange(request)
    return engine, sandbox, request


def _closing(request, phase):
    """同一调用的响应或失败证据，请求照原样带回。"""
    if phase == "response":
        return replace(request, phase="response", response={"choices": []})
    return replace(request, phase="failure", error="TimeoutError: the model call timed out")


@pytest.mark.parametrize("phase", ["response", "failure"], ids=["response", "failure"])
def test_response_and_failure_carry_no_request_and_link_to_it(engine_factory, phase):
    engine, sandbox, request = _recorded_request(engine_factory)
    sandbox.record_model_exchange(_closing(request, phase))
    engine.commit_barrier([sandbox.finish_wakeup(None)])
    [stored_request] = events(engine, "model_request")
    [stored_response] = events(engine, "model_response")
    assert stored_response.data["phase"] == phase
    assert "request" in stored_response.data
    assert stored_response.data["request"] is None
    assert stored_response.caused_by == [stored_request.event_id]
    assert stored_request.data["request"] == REQUEST
    assert not sandbox._request_records


@pytest.mark.parametrize("phase", ["response", "failure"], ids=["response", "failure"])
def test_response_or_failure_with_a_changed_request_is_refused(engine_factory, phase, caplog):
    engine, sandbox, request = _recorded_request(engine_factory)
    changed = replace(_closing(request, phase), request={"messages": [{"role": "user", "content": "y"}]})
    count = engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(
            ProtocolError, match="^Model exchange request differs from its recorded request$"):
        sandbox.record_model_exchange(changed)
    assert (f"Model {phase} for call call carries a request that differs from its recorded request"
            in caplog.messages)
    assert engine.context.store.last_event_seq == count
    assert (request.wakeup_id, "call") in sandbox._request_records
    sandbox.record_model_exchange(_closing(request, phase))
    engine.commit_barrier([sandbox.finish_wakeup(None)])
    [stored_response] = events(engine, "model_response")
    assert stored_response.data["request"] is None
    assert not sandbox._request_records


def test_an_equal_request_rebuilt_in_another_key_order_is_accepted(engine_factory):
    engine, sandbox, request = _recorded_request(engine_factory)
    rebuilt = {key: [dict(reversed(list(message.items()))) for message in value]
               for key, value in reversed(list(copy.deepcopy(REQUEST).items()))}
    assert rebuilt == REQUEST and list(rebuilt["messages"][0]) != list(REQUEST["messages"][0])
    sandbox.record_model_exchange(replace(_closing(request, "response"), request=rebuilt))
    engine.commit_barrier([sandbox.finish_wakeup(None)])
    [stored_request] = events(engine, "model_request")
    [stored_response] = events(engine, "model_response")
    assert stored_response.caused_by == [stored_request.event_id]
    assert stored_response.data["request"] is None
