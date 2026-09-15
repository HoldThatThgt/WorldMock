import json

import pytest

from worldmock.delivery.semantics import attach_facts, complete_annotation, validate_annotation
from worldmock.delivery.training import assign_splits, load_training


def sample(group='parent', event='observation'):
    return {'task': 'action', 'input': {'events': [{'event_id': event, 'materials': []}]},
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
