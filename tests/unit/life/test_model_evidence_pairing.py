"""模型请求只存一次：响应与失败事件的 request 为 null、经 caused_by 指向请求事件，带来的请求须与登记时相同。"""

import copy
import logging
from dataclasses import replace

import pytest

from agentsociety2.agent.base import ModelCallEvidence
from tests.life.conftest import context
from tests.life.environment_runtime import events
from worldmock.errors import ProtocolError

#: 测试用的实际请求：一条用户消息。
REQUEST = {"messages": [{"role": "user", "content": "x"}]}


def _recorded_request(engine_factory):
    """开一轮决策、取观察并登记一次请求；返回引擎与这次请求的证据。"""
    engine = engine_factory()
    frozen = context(engine, "pairing")
    engine.begin_decision_round([frozen])
    engine.observe(frozen)
    request = ModelCallEvidence(frozen, "call", "request", 1, copy.deepcopy(REQUEST))
    engine.record_model_exchange(request)
    return engine, request


def _closing(request, phase):
    """同一调用的响应或失败证据，请求照原样带回。"""
    if phase == "response":
        return replace(request, phase="response", response={"choices": []})
    return replace(request, phase="failure", error="TimeoutError: the model call timed out")


@pytest.mark.parametrize("phase", ["response", "failure"], ids=["response", "failure"])
def test_response_and_failure_carry_no_request_and_link_to_it(engine_factory, phase):
    engine, request = _recorded_request(engine_factory)
    engine.record_model_exchange(_closing(request, phase))
    [stored_request] = events(engine, "model_request")
    [stored_response] = events(engine, "model_response")
    assert stored_response.data["phase"] == phase
    assert "request" in stored_response.data
    assert stored_response.data["request"] is None
    assert stored_response.caused_by == [stored_request.event_id]
    assert stored_request.data["request"] == REQUEST
    assert not engine.evidence.request_events


@pytest.mark.parametrize("phase", ["response", "failure"], ids=["response", "failure"])
def test_response_or_failure_with_a_changed_request_is_refused(engine_factory, phase, caplog):
    engine, request = _recorded_request(engine_factory)
    changed = replace(_closing(request, phase), request={"messages": [{"role": "user", "content": "y"}]})
    count = engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(
            ProtocolError, match="^Model exchange request differs from its recorded request$"):
        engine.record_model_exchange(changed)
    assert (f"Model {phase} for call call carries a request that differs from its recorded request"
            in caplog.messages)
    assert engine.context.store.last_event_seq == count
    assert not events(engine, "model_response")
    assert ("decision-pairing-1", "call") in engine.evidence.request_events
    engine.record_model_exchange(_closing(request, phase))
    [stored_response] = events(engine, "model_response")
    assert stored_response.data["request"] is None
    assert not engine.evidence.request_events


def test_an_equal_request_rebuilt_in_another_key_order_is_accepted(engine_factory):
    engine, request = _recorded_request(engine_factory)
    rebuilt = {key: [dict(reversed(list(message.items()))) for message in value]
               for key, value in reversed(list(copy.deepcopy(REQUEST).items()))}
    assert rebuilt == REQUEST and list(rebuilt["messages"][0]) != list(REQUEST["messages"][0])
    engine.record_model_exchange(replace(_closing(request, "response"), request=rebuilt))
    [stored_request] = events(engine, "model_request")
    [stored_response] = events(engine, "model_response")
    assert stored_response.caused_by == [stored_request.event_id]
    assert stored_response.data["request"] is None
