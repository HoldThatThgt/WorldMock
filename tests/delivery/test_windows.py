import pytest

from worldmock.delivery.windows import _action_windows, _dream_windows, _expand_observed_results, _window_identity
from worldmock.errors import IntegrityError


def event(identity, sequence, kind, data, visibility='agent'):
    return {'event_id': identity, 'event_seq': sequence, 'event_type': kind, 'occurred_at': 100 + sequence,
            'decision_id': 'decision', 'action_id': None, 'activity_id': None, 'agent_id': 1,
            'data': data, 'visibility': visibility, 'artifact_refs': []}


def result_summary(source):
    return {'event_id': source['event_id'], 'action_id': source['action_id'],
            'decision_id': source['decision_id'], 'occurred_at': source['occurred_at'],
            'status': source['data'].get('status'), 'error_code': source['data'].get('error_code'),
            'message': source['data'].get('message'), 'result': source['data']}


def test_action_history_never_sees_future_answer_and_intent_cutoff_is_explicit(tmp_path):
    observed = event('visible', 1, 'observation', {'balance': 800})
    hidden = event('hidden', 2, 'model_response', {'secret_plan': 'future'}, 'agent')
    requested = event('target', 3, 'action_requested', {'tool_name': 'purchase'})
    requested['action_id'] = 'purchase'
    completed = event('completed', 4, 'action_result', {'status': 'completed', 'balance': 750})
    completed['action_id'] = 'purchase'
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 4,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    indexed = {row['event_id']: row for row in (observed, hidden, requested, completed)}
    action, intent = _action_windows(requested, {'purchase': [requested, completed]},
                                     {'decision': [observed, hidden, requested, completed]},
                                     (manifest, tmp_path, indexed))
    assert [item['event_id'] for item in action['input']['events']] == ['visible']
    assert action['input']['knowledge_cutoff_event_seq'] == 1
    assert action['target']['request'] == requested
    assert action['target']['outcomes'] == [completed]
    assert action['provenance']['source_event_ids'] == ['visible']
    assert action['provenance']['cutoff_event_seq'] == action['input']['knowledge_cutoff_event_seq'] == 1
    assert 'target_event_ids' not in action['provenance']
    assert [item['event_id'] for item in intent['input']['events']] == ['visible', 'target', 'completed']
    assert intent['input']['knowledge_cutoff_event_seq'] == 4
    assert all(item['event_id'] != 'hidden' for item in intent['input']['events'])


def test_visible_prior_result_is_hydrated_as_direct_evidence_without_moving_cutoff(tmp_path):
    prior = event('prior-result', 1, 'action_result',
                  {'status': 'rejected', 'error_code': 'WORK_TASK_NOT_FOUND', 'message': 'Missing task'})
    prior.update(action_id='prior-action', decision_id='prior-decision')
    observed = event('visible', 2, 'observation', {'recent_action_results': [result_summary(prior)]})
    requested = event('target', 3, 'action_requested', {'tool_name': 'start_basic_activity'})
    requested['action_id'] = 'current-action'
    completed = event('current-result', 4, 'action_result', {'status': 'completed'})
    completed['action_id'] = 'current-action'
    rows = (prior, observed, requested, completed)
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 4,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    action, intent = _action_windows(requested, {'current-action': [requested, completed]},
                                     {'decision': [observed, requested, completed]},
                                     (manifest, tmp_path, {row['event_id']: row for row in rows}))

    assert [row['event_id'] for row in action['input']['events']] == ['prior-result', 'visible']
    assert action['input']['allowed_evidence_event_ids'] == ['prior-result', 'visible']
    assert action['input']['knowledge_cutoff_event_seq'] == 2
    assert action['input']['window_start'] == observed['occurred_at']
    assert action['provenance']['source_event_ids'] == ['prior-result', 'visible']
    assert intent['input']['allowed_evidence_event_ids'] == [
        'prior-result', 'visible', 'target', 'current-result',
    ]


def test_action_public_identity_is_independent_of_the_future_target(tmp_path):
    observed = event('visible', 1, 'observation', {'balance': 800})
    first = event('first-request', 2, 'action_requested', {'tool_name': 'sleep', 'secret': 'first'})
    first.update(action_id='first-action')
    first_result = event('first-result', 3, 'action_result', {'status': 'completed', 'secret': 'first'})
    first_result.update(action_id='first-action')
    second = event('second-request', 2, 'action_requested', {'tool_name': 'wait', 'secret': 'second'})
    second.update(action_id='second-action')
    second_result = event('second-result', 3, 'action_result', {'status': 'rejected', 'secret': 'second'})
    second_result.update(action_id='second-action')
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 3,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}

    first_window, _ = _action_windows(
        first, {'first-action': [first, first_result]},
        {'decision': [observed, first, first_result]},
        (manifest, tmp_path, {row['event_id']: row for row in (observed, first, first_result)}),
    )
    second_window, _ = _action_windows(
        second, {'second-action': [second, second_result]},
        {'decision': [observed, second, second_result]},
        (manifest, tmp_path, {row['event_id']: row for row in (observed, second, second_result)}),
    )

    assert first_window['input'] == second_window['input']
    assert first_window['provenance'] == second_window['provenance']
    assert first_window['target'] != second_window['target']


@pytest.mark.parametrize('invalid', ['missing', 'future', 'other_agent', 'private', 'wrong_type', 'mismatch'])
def test_observed_result_must_resolve_to_exact_visible_same_agent_prior_event(invalid):
    source = event('prior-result', 1, 'action_result', {'status': 'completed', 'message': 'Done'})
    source.update(action_id='prior-action', decision_id='prior-decision')
    if invalid == 'future':
        source['event_seq'] = 3
    if invalid == 'other_agent':
        source['agent_id'] = 2
    if invalid == 'private':
        source['visibility'] = 'system'
    if invalid == 'wrong_type':
        source['event_type'] = 'model_response'
    reference = result_summary(source)
    if invalid == 'mismatch':
        reference['message'] = 'Changed'
    observed = event('visible', 2, 'observation', {'recent_action_results': [reference]})
    indexed = {} if invalid == 'missing' else {source['event_id']: source}

    with pytest.raises(IntegrityError):
        _expand_observed_results([observed], indexed, cutoff=2)


def test_midnight_experiment_end_does_not_create_an_extra_training_day(tmp_path):
    start, end = 1767542400, 1767628800
    observed = event('observed', 1, 'observation', {'balance_fen': 800})
    observed['occurred_at'] = start
    ended = event('final-result', 2, 'action_result', {'status': 'cancelled'})
    ended['occurred_at'] = end
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 2,
                'window': {'start': '2026-01-05T00:00:00+08:00', 'end': '2026-01-06T00:00:00+08:00',
                           'timezone': 'Asia/Shanghai'},
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    indexed = {row['event_id']: row for row in (observed, ended)}
    records = _dream_windows([observed, ended], manifest, tmp_path, indexed)
    assert len(records) == 3
    assert {record['input']['date'] for record in records} == {'2026-01-05'}
    assert all(record['input']['window_end'] == end for record in records)
    assert all(record['provenance']['source_event_ids'] == ['observed', 'final-result'] for record in records)
    assert all(record['input']['allowed_evidence_event_ids'] == ['observed', 'final-result']
               for record in records)
    assert len({record['provenance']['window_id'] for record in records}) == 3
    assert all(record['provenance']['window_id'] == _window_identity(record['task'], record['input'])
               for record in records)

    counterfactual = next(row for row in records if row["input"]["mode"] == "counterfactual")
    assert counterfactual["input"]["counterfactual"] == {"world_kind": "observed", "environment_replay": None}
    assert counterfactual["target"]["environment_replay"] is None
