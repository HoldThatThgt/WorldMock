import copy
import hashlib
import json

import pytest

from tests.delivery.prompt_fixtures import layered_user, six_layers
from worldmock.delivery.evidence_encoding import decode_model_record
from worldmock.delivery.pipeline import write_model_inputs
from worldmock.delivery.replay import replay_windows
from worldmock.delivery.windows import _window_identity
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore


#: 分支中实际行动请求的六层 JSON（进度层带余额被改后的一条结果，便于核对来源）。
BRANCH_LAYERS = six_layers([{'type': 'tool_call', 'tool': 'start_activity', 'result': {'status': 'completed'}}])


def finish_export(store, directory):
    store.commit({'version': store.state['version'] + 1, 'current_time': 200},
                 [EventDraft(event_type='run_ended', occurred_at=200)])
    reference = store.assets.put_json({'complete': True}, {'kind': 'fixture'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    export_run(store, point.last_event_seq, directory)


@pytest.fixture
def pair(tmp_path):
    manifest = {'run_id': 'parent', 'source_group_id': 'parent', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'}, 'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    parent = FootprintStore.create(tmp_path / 'parent', manifest, {'version': 0, 'current_time': 100})
    observed = parent.commit(parent.state, [EventDraft(event_type='observation', occurred_at=100,
                                                       agent_id=1, data={'balance_fen': 800})])[0]
    ref = parent.assets.put_json({'state': 'parent'}, {'kind': 'fixture'})
    checkpoint = parent.checkpoint(parent.state, {'agent:1': ref, 'life': ref})
    request = {'kind': 'set_balance_fen', 'agent_id': 1, 'balance_fen': 50,
               'hypothesis': 'Less liquidity may reduce purchases.', 'source_event_ids': [observed.event_id]}
    digest = hashlib.sha256(canonical_json({'tool_name': 'counterfactual_intervention', 'request': request})).hexdigest()
    child = parent.restore(checkpoint.checkpoint_id, tmp_path / 'child',
                            {'branch_kind': 'counterfactual', 'intervention_sha256': digest})
    child.commit({'version': child.state['version'] + 1, 'current_time': 100}, [EventDraft(
        event_type='environment_event', occurred_at=100, agent_id=1, visibility='evaluation',
        caused_by=[observed.event_id], data={'kind': 'counterfactual_intervention',
                                           'intervention_kind': 'set_balance_fen', 'hypothesis': request['hypothesis'],
                                           'source_event_ids': request['source_event_ids'],
                                           'intervention_sha256': digest, 'before_balance_fen': 800,
                                           'after_balance_fen': 50})])
    child.commit(child.state, [EventDraft(event_type='observation', occurred_at=100, agent_id=1,
                                          decision_id='branch-decision', data={'balance_fen': 50}),
                               EventDraft(event_type='model_request', occurred_at=100, agent_id=1,
                                          decision_id='branch-decision', visibility='system', data={
                                              'phase': 'request', 'request': {
                                                  'messages': [{'role': 'system', 'content': '系统层'},
                                                               layered_user(BRANCH_LAYERS)],
                                                  'tools': [{'type': 'function', 'function': {
                                                      'name': 'start_activity', 'parameters': {}}}]}}),
                               EventDraft(event_type='action_requested', occurred_at=100, agent_id=1,
                                          decision_id='branch-decision', action_id='wait', data={
                                              'tool_name': 'start_activity',
                                              'request': {'targets': ['自己'], 'actions': ['interact'],
                                                          'ways': ['等待']}})])
    source_dir, replay_dir = tmp_path / 'source-export', tmp_path / 'replay-export'
    finish_export(parent, source_dir)
    finish_export(child, replay_dir)
    parent.close()
    child.close()
    return source_dir, replay_dir, observed.event_id, digest


def test_completed_actual_branch_binds_hypothesis_checkpoint_and_results(pair):
    source, replay, origin_id, digest = pair
    records = replay_windows(source, replay)
    assert len(records) == 1
    record = records[0]
    assert record['input']['counterfactual']['world_kind'] == 'resimulated'
    assert record['input']['counterfactual']['balance_fen'] == 50
    assert record['input']['prerequisite_state']['state'] is None
    assert record['input']['prerequisite_state']['event_id'] == record['input']['events'][0]['event_id']
    binding = record['target']['environment_replay']
    assert binding['parent_run_id'] == 'parent'
    assert binding['intervention_sha256'] == digest
    assert binding['source_event_ids'] == [origin_id]
    assert binding['result_event_ids'] == [event['event_id'] for event in record['input']['events']]
    assert origin_id in record['provenance']['source_event_ids']
    expected = list(dict.fromkeys([event['event_id'] for event in record['input']['events']] +
                                  [record['input']['prerequisite_state']['event_id']]))
    assert record['input']['allowed_evidence_event_ids'] == expected
    assert set(expected) <= set(record['provenance']['source_event_ids'])
    assert binding['run_id'] != binding['parent_run_id']
    requested = next(row for row in record['input']['events'] if row['event_type'] == 'action_requested')
    assert record['input']['decision_layers'] == [{'action_event_id': requested['event_id'], 'layers': BRANCH_LAYERS}]
    prompt_id = record['provenance']['decision_layer_sources'][0]['event_id']
    assert record['provenance']['decision_layer_sources'] == [
        {'event_id': prompt_id, 'sha256': hashlib.sha256(canonical_json(BRANCH_LAYERS)).hexdigest()}]
    assert prompt_id in record['provenance']['source_event_ids']
    assert prompt_id not in record['input']['allowed_evidence_event_ids']
    assert record['provenance']['window_id'] == _window_identity(record['task'], record['input'])
    changed = copy.deepcopy(record['input'])
    changed['counterfactual']['hypothesis'] = 'A different visible counterfactual condition.'
    assert record['provenance']['window_id'] != _window_identity(record['task'], changed)


def test_actual_replay_uses_the_same_versioned_model_input_entry(pair, tmp_path):
    source, replay, _origin_id, _digest = pair
    record = replay_windows(source, replay)[0]
    workspace = tmp_path / 'labelkit'
    (workspace / 'data').mkdir(parents=True)
    write_model_inputs('dreaming', [record], workspace)
    wire = json.loads((workspace / 'data/dreaming.jsonl').read_text(encoding="utf-8"))

    assert wire['input']['observation_value_encoding']['schema_version'] == 1
    assert 'observation_value_dictionary' in wire['input']
    assert decode_model_record(wire) == record


def test_original_world_cannot_be_mislabeled_as_an_actual_replay(pair):
    source, _replay, _origin, _digest = pair
    with pytest.raises(IntegrityError, match='does not belong'):
        replay_windows(source, source)


def test_actual_branch_rejects_unrelated_parent(pair, tmp_path):
    source, replay, _origin, _digest = pair
    parent = FootprintStore.create(tmp_path / 'unrelated', {
        'run_id': 'unrelated', 'source_group_id': 'unrelated', 'agent_ids': [1],
        'model_identity': {'name': 'fixture'}, 'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}
    }, {'version': 0, 'current_time': 100})
    other = tmp_path / 'other-export'
    finish_export(parent, other)
    parent.close()
    with pytest.raises(IntegrityError, match='does not belong'):
        replay_windows(other, replay)
