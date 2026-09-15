import json
from pathlib import Path

import pytest

from worldmock.delivery.semantics import attach_facts, complete_annotation, validate_annotation
from worldmock.delivery.training import assign_splits, load_training


ROOT = Path(__file__).resolve().parents[2]


def sample(group='parent', event='observation'):
    return {'task': 'action',
            'input': {'events': [{'event_id': event, 'materials': []}],
                      'allowed_evidence_event_ids': [event]},
            'target': {'claims': [{'text': 'needs rest', 'kind': 'inference', 'evidence_event_ids': [event]}],
                       'intent_candidates': [{'intent': 'rest', 'basis': 'inferred'}], 'uncertainty': ''},
            'provenance': {'run_id': group, 'source_group_id': group, 'source_event_ids': [event]}}


def test_real_dataset_loading_preserves_all_facts_and_separates_answer(tmp_path):
    record = sample()
    record['target']['facts'] = {'action': 'sleep', 'duration': 3600, 'efficiency': 0.3,
                                 'nested': [{'value': 0.1}, {'missing_is_distinct': None}]}
    record['input']['efficiency'] = 0.3
    loaded, receipt = load_training([record], tmp_path / 'training')
    assert receipt['rows'] == 1
    split = next(iter(loaded))
    actual = loaded[split][0]
    assert json.loads(actual['target']) == record['target']
    assert json.loads(actual['input']) == record['input']
    assert json.loads(actual['provenance']) == record['provenance']
    assert json.loads(actual['prompt'][0]['content']) == record['input']
    assert json.loads(actual['completion'][0]['content']) == record['target']
    assert loaded[split].features['input'].decode is False
    assert receipt['json_columns']['verified_decoder'] == 'json.loads'
    assert '3600' not in actual['prompt'][0]['content']
    assert '3600' in actual['completion'][0]['content']


@pytest.mark.parametrize('shared_kind', ['event', 'asset'])
def test_shared_source_connects_different_declared_groups_into_one_split(shared_kind):
    records = [sample(f'independent-{index}', f'event-{index}') for index in range(40)]
    assert len(set(assign_splits(records))) > 1
    for record in records:
        if shared_kind == 'event':
            record['provenance']['source_event_ids'].append('shared-event')
        else:
            record['input']['events'][0]['materials'].append({'reference': {'sha256': 'a' * 64}})
    assert len(set(assign_splits(records))) == 1


def test_run_identity_keeps_disjoint_event_windows_in_one_split():
    records = [sample(f'group-{index}', f'event-{index}') for index in range(40)]
    for record in records:
        record['provenance']['run_id'] = 'same-run'
    assert len(set(assign_splits(records))) == 1


def test_postprocessor_preserves_original_action_and_rejects_future_claims():
    original = sample()
    semantics = {'target': original['target']}
    original['target'] = {'request': {'tool': 'sleep', 'time': 100}}
    result = complete_annotation(semantics, original)
    assert validate_annotation(result, original) == []
    assert 'facts' not in result['target']
    attached = attach_facts(result, original)
    assert attached['target']['facts'] == original['target']
    result['target']['claims'][0]['evidence_event_ids'] = ['future']
    assert 'outside its knowledge window' in validate_annotation(result, original)[0]
    attached['target']['facts']['request']['time'] = 999
    assert original['target']['request']['time'] == 100
    assert 'Future target facts cannot enter semantic annotation or review' in validate_annotation(attached, original)


def test_target_event_identities_are_added_only_after_semantic_review():
    original = sample()
    semantics = {'target': original['target']}
    original['target'] = {
        'request': {'event_id': 'future-request'},
        'outcomes': [{'event_id': 'future-accepted'}, {'event_id': 'future-result'}],
    }

    reviewed = complete_annotation(semantics, original)

    assert reviewed['provenance']['source_event_ids'] == ['observation']
    assert 'target_event_ids' not in reviewed['provenance']
    attached = attach_facts(reviewed, original)
    assert attached['provenance']['target_event_ids'] == [
        'future-request', 'future-accepted', 'future-result',
    ]
    assert attached['provenance']['source_event_ids'] == [
        'observation', 'future-request', 'future-accepted', 'future-result',
    ]


def test_validator_accepts_hydrated_prior_result_and_rejects_allowed_list_tampering():
    original = sample(event='observation')
    original['input']['events'].insert(0, {'event_id': 'prior-result', 'event_type': 'action_result',
                                           'materials': [], 'data': {'status': 'completed'}})
    original['input']['allowed_evidence_event_ids'] = ['prior-result', 'observation']
    original['provenance']['source_event_ids'].insert(0, 'prior-result')
    semantics = {'target': copy_target(original, 'prior-result')}
    result = complete_annotation(semantics, original)

    assert validate_annotation(result, original) == []
    tampered = json.loads(json.dumps(original))
    tampered['input']['allowed_evidence_event_ids'] = ['observation', 'forged']
    result = complete_annotation(semantics, tampered)
    errors = validate_annotation(result, tampered)
    assert 'Allowed evidence event IDs disagree with the visible input events' in errors


@pytest.mark.parametrize('task', ['action', 'intent', 'dreaming'])
def test_semantic_prompt_requires_every_citation_to_use_the_exact_allowed_set(task):
    config = (ROOT / f'labelkit-projects/{task}.toml').read_text()

    assert '每个 evidence_event_ids 值必须逐字取自 input.allowed_evidence_event_ids' in config
    assert 'input.events 与 input.prerequisite_state 中、且列于 input.allowed_evidence_event_ids' in config


@pytest.mark.parametrize('invalid', [
    'future-event', 'other-agent-event', 'private-event', 'action-id', 'episode-id', 'fabricated-event',
])
def test_validator_rejects_every_identifier_outside_direct_visible_evidence(invalid):
    original = sample()
    original['input']['events'][0]['data'] = {
        'future_event_id': 'future-event', 'other_agent_event_id': 'other-agent-event',
        'private_event_id': 'private-event', 'action_id': 'action-id', 'episode_id': 'episode-id',
    }
    result = complete_annotation({'target': copy_target(original, invalid)}, original)

    errors = validate_annotation(result, original)
    assert 'outside its knowledge window' in errors[0]
    assert f'"{invalid}"' in errors[0]
    assert 'allowed_evidence_event_ids=["observation"]' in errors[0]


def test_validator_returns_the_allowed_set_when_a_fact_has_no_citation():
    original = sample()
    target = copy_target(original, 'observation')
    target['claims'][0]['evidence_event_ids'] = []

    errors = validate_annotation(complete_annotation({'target': target}, original), original)

    assert errors == [
        'Fact or inference has no supporting event; allowed_evidence_event_ids=["observation"]',
    ]


def copy_target(record, event_id):
    target = json.loads(json.dumps(record['target']))
    target['claims'][0]['evidence_event_ids'] = [event_id]
    return target
