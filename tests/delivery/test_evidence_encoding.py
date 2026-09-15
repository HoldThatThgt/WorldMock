import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from worldmock.delivery.evidence_encoding import (
    DICTIONARY_FIELD,
    ENCODING_FIELD,
    POINTER_RULE,
    decode_model_input,
    encode_model_input,
)
from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json


def observation(identity, data, agent_id=1):
    return {
        'event_id': identity,
        'event_type': 'observation',
        'occurred_at': len(identity),
        'agent_id': agent_id,
        'data': data,
        'materials': [],
    }


def logical_input():
    catalog = {'items': [{'name': '午餐', 'price_fen': 1800}], 'description': '可见目录' * 300}
    result = {'event_id': 'prior-result', 'action_id': 'prior-action', 'decision_id': 'prior-decision',
              'occurred_at': 1, 'status': 'completed', 'error_code': None, 'message': None,
              'result': {'status': 'completed', 'value': 0.3}}
    events = [
        observation('event-1', {'catalog': catalog, 'same_value_other_field': catalog,
                                'recent_action_results': [result], 'nullable': None}),
        observation('event-2', {'catalog': catalog, 'missing_is_preserved': ['一', '二'], 'nullable': None}),
        observation('event-3', {'catalog': catalog, 'missing_is_preserved': ['二', '一'], 'nullable': None}),
    ]
    return {'events': events, 'allowed_evidence_event_ids': [event['event_id'] for event in events],
            'knowledge_cutoff_event_seq': 3, 'prerequisite_state': {'event_id': None}}


def test_profitable_dictionary_round_trip_preserves_every_logical_value():
    source = logical_input()
    encoded = encode_model_input(source)
    entries = encoded[DICTIONARY_FIELD]

    assert encoded[ENCODING_FIELD]['schema_version'] == 1
    assert encoded[ENCODING_FIELD]['rule'] == POINTER_RULE
    assert [entry['field'] for entry in entries] == ['catalog']
    assert all(set(event['data']['catalog']) == {'value_pointer'} for event in encoded['events'])
    assert isinstance(encoded['events'][0]['data']['same_value_other_field'], dict)
    assert decode_model_input(encoded) == source
    assert canonical_json(decode_model_input(encoded)) == canonical_json(source)
    assert encoded['events'][0]['materials'] == source['events'][0]['materials']
    assert encoded['events'][0]['data']['recent_action_results'] == source['events'][0]['data']['recent_action_results']


def test_empty_dictionary_uses_the_same_versioned_envelope_for_short_values():
    source = {'events': [observation('a', {'ready': False}), observation('b', {'ready': False})]}
    encoded = encode_model_input(source)

    assert encoded[DICTIONARY_FIELD] == []
    assert encoded[ENCODING_FIELD]['kind'] == 'rfc6901_field_dictionary'
    assert encoded['events'][0]['data']['ready'] is False
    assert decode_model_input(encoded) == source


@pytest.mark.parametrize('mutation, message', [
    ('marker', 'marker'),
    ('value', 'digest'),
    ('field', 'digest'),
    ('pointer', 'outside its local dictionary'),
    ('event', 'canonical input digest'),
    ('nested', 'nested or misplaced'),
])
def test_decoder_rejects_tampered_or_misplaced_dictionary_evidence(mutation, message):
    encoded = encode_model_input(logical_input())
    if mutation == 'marker':
        encoded[ENCODING_FIELD]['kind'] = 'other'
    elif mutation == 'value':
        encoded[DICTIONARY_FIELD][0]['value']['description'] = 'changed'
    elif mutation == 'field':
        encoded[DICTIONARY_FIELD][0]['field'] = 'other'
    elif mutation == 'pointer':
        encoded['events'][0]['data']['catalog']['value_pointer'] = '/events/0/data/nullable'
    elif mutation == 'event':
        encoded['events'][0]['event_id'] = 'changed'
    else:
        encoded['events'][0]['materials'] = [{'value_pointer': '/observation_value_dictionary/0/value'}]

    with pytest.raises(IntegrityError, match=message):
        decode_model_input(encoded)


def test_decoder_rejects_valid_but_unused_dictionary_entry():
    encoded = encode_model_input(logical_input())
    candidate = _unused_entry_after(encoded[DICTIONARY_FIELD][-1]['sha256'])
    encoded[DICTIONARY_FIELD].append(candidate)

    with pytest.raises(IntegrityError, match='unused entry'):
        decode_model_input(encoded)


def _unused_entry_after(minimum):
    for index in range(10000):
        value = {'unused': index}
        digest = hashlib.sha256(canonical_json(['unused_field', value])).hexdigest()
        if digest > minimum:
            return {'field': 'unused_field', 'sha256': digest, 'value': value}
    raise AssertionError('Cannot construct an ordered unused dictionary entry')


def test_reserved_pointer_shape_is_rejected_before_encoding():
    source = logical_input()
    source['events'][0]['data']['domain_value'] = {'value_pointer': 'literal-domain-value'}

    with pytest.raises(IntegrityError, match='reserved value pointer'):
        encode_model_input(source)


def test_per_record_dictionary_cannot_reveal_another_agent_or_future_canary():
    first = logical_input()
    second = copy.deepcopy(first)
    second['events'] = [observation('other', {'catalog': {'secret': 'future-other-agent-canary'}}, 2)]
    encoded = encode_model_input(first)

    assert 'future-other-agent-canary' not in canonical_json(encoded).decode()
    assert decode_model_input(encode_model_input(second)) == second


def test_python_object_alias_does_not_create_a_pointer_outside_observation_data():
    shared = {'catalog': {'description': 'same visible catalog' * 300}}
    source = {'events': [observation(str(index), shared) for index in range(3)],
              'prerequisite_state': {'event_id': '0', 'state': shared}}
    encoded = encode_model_input(source)

    assert encoded[DICTIONARY_FIELD]
    assert 'value_pointer' not in canonical_json(encoded['prerequisite_state']).decode()
    assert decode_model_input(encoded) == source


def test_wire_bytes_are_identical_across_four_python_hash_seeds(tmp_path):
    source = tmp_path / 'input.json'
    source.write_bytes(canonical_json(logical_input()))
    script = (
        'import hashlib,json,sys; '
        'from worldmock.delivery.evidence_encoding import encode_model_input; '
        'from worldmock.footprints.models import canonical_json; '
        'value=json.load(open(sys.argv[1])); '
        'print(hashlib.sha256(canonical_json(encode_model_input(value))).hexdigest())'
    )
    digests = []
    for seed in ('1', '7', '31', '101'):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run([sys.executable, '-c', script, str(source)], env=environment,
                                capture_output=True, text=True, check=True)
        digests.append(result.stdout.strip())
    assert len(set(digests)) == 1


def test_decoder_rejects_dictionary_entry_relabelled_with_a_consistent_digest():
    encoded = encode_model_input(logical_input())
    entry = encoded[DICTIONARY_FIELD][0]
    entry['field'] = 'same_value_other_field'
    entry['sha256'] = hashlib.sha256(canonical_json([entry['field'], entry['value']])).hexdigest()

    with pytest.raises(IntegrityError, match='disagrees with its field or digest'):
        decode_model_input(encoded)


def _multi_entry_input():
    shared = {
        'catalog': {'items': [{'name': '午餐', 'price_fen': 1800}], 'description': '可见目录' * 300},
        'price_board': {'items': [{'name': '晚餐', 'price_fen': 2400}], 'note': '今日价格' * 300},
        'menu_specials': {'items': [{'name': '汤品', 'price_fen': 900}], 'note': '限量供应' * 300},
        'seating_note': {'zones': ['大厅', '露台'], 'description': '座位说明' * 300},
    }
    events = [
        observation(f'event-{index}', dict(shared, nullable=None))
        for index in range(1, 4)
    ]
    return {'events': events, 'allowed_evidence_event_ids': [event['event_id'] for event in events],
            'knowledge_cutoff_event_seq': 3, 'prerequisite_state': {'event_id': None}}


def test_multi_entry_dictionary_wire_bytes_are_identical_across_four_python_hash_seeds(tmp_path):
    encoded = encode_model_input(_multi_entry_input())
    assert len(encoded[DICTIONARY_FIELD]) == 4
    source = tmp_path / 'multi-entry-input.json'
    source.write_bytes(canonical_json(_multi_entry_input()))
    script = (
        'import hashlib,json,sys; '
        'from worldmock.delivery.evidence_encoding import encode_model_input; '
        'from worldmock.footprints.models import canonical_json; '
        'value=json.load(open(sys.argv[1])); '
        'print(hashlib.sha256(canonical_json(encode_model_input(value))).hexdigest())'
    )
    digests = []
    for seed in ('1', '7', '31', '101'):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run([sys.executable, '-c', script, str(source)], env=environment,
                                capture_output=True, text=True, check=True)
        digests.append(result.stdout.strip())
    assert len(set(digests)) == 1


def test_complete_encoding_is_strictly_smaller_when_dictionary_is_used():
    source = logical_input()
    encoded = encode_model_input(source)
    literal = copy.deepcopy(encoded)
    values = {event['event_id']: source['events'][index]['data']['catalog']
              for index, event in enumerate(encoded['events'])}
    for event in literal['events']:
        event['data']['catalog'] = values[event['event_id']]
    literal[DICTIONARY_FIELD] = []

    assert encoded[DICTIONARY_FIELD]
    assert len(canonical_json(encoded)) < len(canonical_json(literal))
