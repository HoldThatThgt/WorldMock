from worldmock.delivery.windows import _action_windows, _dream_windows


def event(identity, sequence, kind, data, visibility='agent'):
    return {'event_id': identity, 'event_seq': sequence, 'event_type': kind, 'occurred_at': 100 + sequence,
            'decision_id': 'decision', 'action_id': None, 'activity_id': None, 'agent_id': 1,
            'data': data, 'visibility': visibility, 'artifact_refs': []}


def test_action_history_never_sees_future_answer_and_intent_cutoff_is_explicit(tmp_path):
    observed = event('visible', 1, 'observation', {'balance': 800})
    hidden = event('hidden', 2, 'model_response', {'secret_plan': 'future'}, 'agent')
    requested = event('target', 3, 'action_requested', {'tool_name': 'purchase'})
    requested['action_id'] = 'purchase'
    completed = event('completed', 4, 'action_result', {'status': 'completed', 'balance': 750})
    completed['action_id'] = 'purchase'
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 4,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    action, intent = _action_windows(requested, {'purchase': [requested, completed]},
                                     {'decision': [observed, hidden, requested, completed]}, (manifest, tmp_path))
    assert [item['event_id'] for item in action['input']['events']] == ['visible']
    assert action['input']['knowledge_cutoff_event_seq'] == 1
    assert action['target']['request'] == requested
    assert action['target']['outcomes'] == [completed]
    assert action['provenance']['target_event_ids'] == ['target', 'completed']
    assert [item['event_id'] for item in intent['input']['events']] == ['visible', 'target', 'completed']
    assert intent['input']['knowledge_cutoff_event_seq'] == 4
    assert all(item['event_id'] != 'hidden' for item in intent['input']['events'])


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
    records = _dream_windows([observed, ended], manifest, tmp_path)
    assert len(records) == 3
    assert {record['input']['date'] for record in records} == {'2026-01-05'}
    assert all(record['input']['window_end'] == end for record in records)
    assert all(record['provenance']['source_event_ids'] == ['observed', 'final-result'] for record in records)

    counterfactual = next(row for row in records if row["input"]["mode"] == "counterfactual")
    assert counterfactual["input"]["counterfactual"] == {"world_kind": "observed", "environment_replay": None}
    assert counterfactual["target"]["environment_replay"] is None
