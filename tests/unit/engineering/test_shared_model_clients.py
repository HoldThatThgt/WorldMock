"""零点计划业务入口执行同一冻结模型客户端规则。"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.foundation.errors import ProtocolError
from worldmock.simulation_engine.scheduling.plans import day_planning
from worldmock.state.interaction.requests import PlanDayRequest
from worldmock.foundation.model_feedback import schema_error_details
from worldmock.runtime.lifecycle.runner import MemoryDigestClient, PlanDayClient

CLIENTS = ((PlanDayClient, 'Plan day'), (MemoryDigestClient, 'Memory digest'))
IDENTITY = {'requested_model': 'mock-model', 'base_url': 'https://example.test', 'max_retries': 0,
            'transport_max_retries': 3, 'model_metadata': {'requested_model': 'mock-model',
             'metadata_url': 'https://example.test/models', 'context_window': 262144, 'max_output_tokens': 32000}}


class FakeClient:
    model_name = 'mock-model'
    base_url = 'https://example.test'

    def __init__(self, response):
        self.response = response
        self.requests = []

    async def call(self, **request):
        self.requests.append(copy.deepcopy(request))
        request['messages'].append({'role': 'user', 'content': 'client-local mutation'})
        return self.response


def _client(monkeypatch, cls, response):
    import agentsociety2.config
    client = FakeClient(response)
    monkeypatch.setattr(agentsociety2.config, 'build_client_for_role', lambda role: client)
    return cls(dict(IDENTITY)), client


def _call(client, request):
    return asyncio.run(client.call(request))


@pytest.mark.parametrize('cls,purpose', CLIENTS)
def test_original_clients_share_single_call_serialization_and_request_isolation(monkeypatch, cls, purpose):
    for response in ({'choices': []}, SimpleNamespace(model_dump=lambda mode: {'choices': [], 'mode': mode})):
        client, transport = _client(monkeypatch, cls, response)
        request = {'messages': [{'role': 'user', 'content': 'request'}], 'max_retries': 0}
        original = copy.deepcopy(request)
        assert _call(client, request)['choices'] == []
        assert transport.requests == [{**original, 'transport_max_retries': 3}]
        assert request == original
        assert client.purpose == purpose


@pytest.mark.parametrize('cls,purpose', CLIENTS)
@pytest.mark.parametrize('key,value', [('requested_model', 'other'), ('base_url', 'https://wrong.test')])
def test_original_clients_reject_identity_and_retry_drift(monkeypatch, cls, purpose, key, value):
    import agentsociety2.config
    monkeypatch.setattr(agentsociety2.config, 'build_client_for_role', lambda role: FakeClient({}))
    with pytest.raises(ProtocolError, match=purpose + ' .*disagree'):
        cls({**IDENTITY, key: value})


@pytest.mark.parametrize('cls,purpose', CLIENTS)
@pytest.mark.parametrize('response,reason', [(object(), 'not serializable'),
                                           (SimpleNamespace(model_dump=lambda mode: []), 'not a serialized object')])
def test_original_clients_reject_unserializable_responses(monkeypatch, cls, purpose, response, reason):
    client, _ = _client(monkeypatch, cls, response)
    with pytest.raises(ProtocolError, match=purpose + ' model response is ' + reason):
        _call(client, {'messages': [], 'max_retries': 0})


def test_plan_corrections_keep_tools_budgets_and_channel_echo():
    raw = {'choices': [{'message': {'tool_calls': [{'id': 'id', 'type': 'function',
             'function': {'name': 'wrong', 'arguments': '{}'}}], 'content': 'free text'}}]}
    request = {'messages': [{'role': 'user', 'content': 'original'}], 'tools': [{'name': 'original tool'}],
               'max_retries': 0, 'max_tokens': 123, 'tool_choice': 'required'}
    original = copy.deepcopy(request)
    error = day_planning.PlanDayOutputError('bad plan')
    plan = day_planning.prepare_plan_day_correction(request, raw, error)
    assert request == original
    for changed in (plan,):
        assert {key: changed[key] for key in request if key != 'messages'} == {
            key: request[key] for key in request if key != 'messages'}
        assert changed['messages'][:-1] == request['messages']
        feedback = json.loads(changed['messages'][-1]['content'])
        assert feedback['PREVIOUS_INVALID_RESPONSE'] == raw
        assert feedback['INSTRUCTION'].endswith('using the unchanged schema.')
        assert changed['messages'][-1]['content'] == json.dumps(feedback, ensure_ascii=False,
                                                                sort_keys=True, separators=(',', ':'))


@pytest.mark.parametrize('calls', [[], [{}, {}], [{'function': {'name': 'wrong', 'arguments': '{}'}}],
                                  [{'function': {'name': 'submit_day_plan', 'arguments': {}}}]])
def test_original_tool_argument_consumers_reject_wrong_count_name_and_json_text(calls, caplog):
    response = {'choices': [{'message': {'tool_calls': copy.deepcopy(calls)}}]}
    with pytest.raises(day_planning.PlanDayOutputError):
        day_planning._tool_arguments(response)
    assert len(caplog.records) == 1


def test_original_schema_feedback_uses_shared_sanitized_error_details():
    with pytest.raises(ValidationError) as captured:
        PlanDayRequest.model_validate({'goals': [{'intent_kind': 'unknown', 'goal': 'goal'}]})
    error = captured.value
    details = schema_error_details(error)
    assert details and all(set(item) == {'field', 'type', 'message'} for item in details)
    assert 'goals.0.intent_kind' in day_planning._schema_feedback(error)
