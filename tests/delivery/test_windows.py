import pytest

from worldmock.delivery.windows import (
    _action_windows,
    _dream_windows,
    _expand_observed_results,
    _window_identity,
    prepare_windows,
)
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore


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


@pytest.mark.parametrize('collision', ['decision', 'action', 'visible_unowned'])
def test_authenticated_export_rejects_cross_agent_action_group(tmp_path, collision):
    export = _completed_multi_agent_export(tmp_path, collision)

    with pytest.raises(IntegrityError, match='crosses agent boundaries'):
        prepare_windows(export)


def test_authenticated_export_accepts_distinct_agent_groups_and_system_facts(tmp_path):
    export = _completed_multi_agent_export(tmp_path, 'none', include_system=True)

    windows = prepare_windows(export)

    assert len(windows['action']) == len(windows['intent']) == 2
    assert len(windows['dreaming']) == 6
    assert all(len({row['agent_id'] for row in record['input']['events']}) == 1
               for record in windows['action'])
    first = next(record for record in windows['action'] if record['target']['request']['agent_id'] == 1)
    assert any(row['agent_id'] is None for row in first['target']['outcomes'])
    assert all(row['agent_id'] == 1 for row in first['input']['events'])


def _completed_multi_agent_export(tmp_path, collision, include_system=False):
    """创建可通过正式导出认证的两人物动作证据。"""
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'agent_ids': [1, 2],
                'model_identity': {'name': 'fixture'},
                'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    state = {'version': 0, 'current_time': 100}
    store = FootprintStore.create(tmp_path / 'run', manifest, state)
    drafts = _multi_agent_drafts(collision, include_system)
    store.commit(state, drafts)
    store.commit({'version': 1, 'current_time': 200},
                 [EventDraft(event_type='run_ended', occurred_at=200, visibility='system')])
    snapshot = store.assets.put_json({'saved': True}, {'kind': 'fixture'})
    checkpoint = store.checkpoint(store.state, {'agent:1': snapshot, 'agent:2': snapshot, 'life': snapshot})
    destination = tmp_path / 'export'
    export_run(store, checkpoint.last_event_seq, destination)
    store.close()
    return destination


def _multi_agent_drafts(collision, include_system):
    """构造正常分组或单一明确跨人物碰撞。"""
    decision_two = 'decision-1' if collision == 'decision' else 'decision-2'
    action_two = 'action-1' if collision == 'action' else 'action-2'
    drafts = []
    for agent_id, decision_id, action_id in ((1, 'decision-1', 'action-1'),
                                              (2, decision_two, action_two)):
        drafts.extend(_agent_action_drafts(agent_id, decision_id, action_id))
    if include_system:
        drafts.append(EventDraft(event_type='environment_event', occurred_at=100, visibility='system',
                                 decision_id='decision-1', action_id='action-1', data={'kind': 'system_fact'}))
    if collision == 'visible_unowned':
        drafts.append(EventDraft(event_type='environment_event', occurred_at=100, visibility='agent',
                                 decision_id='decision-1', action_id='action-1', data={'kind': 'unowned'}))
    return drafts


def _agent_action_drafts(agent_id, decision_id, action_id):
    """构造一个人物同决策内的观察、请求和结果。"""
    return [
        EventDraft(event_type='observation', occurred_at=100, agent_id=agent_id, decision_id=decision_id,
                   data={'location_id': f'location-{agent_id}'}),
        EventDraft(event_type='action_requested', occurred_at=100, agent_id=agent_id,
                   decision_id=decision_id, action_id=action_id,
                   data={'tool_name': 'start_basic_activity', 'request': {'kind': 'wait'}}),
        EventDraft(event_type='action_result', occurred_at=100, agent_id=agent_id,
                   decision_id=decision_id, action_id=action_id, data={'status': 'completed'}),
    ]


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


@pytest.mark.parametrize('ordering', ['after', 'equal'])
def test_observed_result_reference_may_not_reach_at_or_past_the_observation_itself(ordering):
    """引用排序守卫（docs/dev/SPEC-labelkit-delivery.md「信息范围与标签」「来源关联」）：未来事件不得进入引用集合。

    观察的 recent_action_results 引用的结果事件序号 >= 观察自身序号时，
    导出管道必须经 `source["event_seq"] >= observation["event_seq"]` 拒绝。
    本用例把 knowledge cutoff 放宽到引用之后（cutoff=5 > 引用序号），
    确保拒绝只来自排序半句——同行的 `> cutoff` 半句是窗口成员资格已蕴含
    的冗余防御，'future' 既有用例（序号 3、cutoff=2）恰好只测到它。
    """
    sequence = 3 if ordering == 'after' else 2
    source = event('forward-result', sequence, 'action_result', {'status': 'completed', 'message': 'Done'})
    source.update(action_id='forward-action', decision_id='forward-decision')
    observed = event('visible', 2, 'observation', {'recent_action_results': [result_summary(source)]})
    indexed = {row['event_id']: row for row in (source, observed)}

    with pytest.raises(IntegrityError, match='knowledge cutoff'):
        _expand_observed_results([observed], indexed, cutoff=5)

    prior = event('prior-result', 1, 'action_result', {'status': 'completed', 'message': 'Done'})
    prior.update(action_id='prior-action', decision_id='prior-decision')
    earlier_observed = event('visible', 2, 'observation',
                             {'recent_action_results': [result_summary(prior)]})
    expanded = _expand_observed_results(
        [earlier_observed], {row['event_id']: row for row in (prior, earlier_observed)}, cutoff=2)
    assert [row['event_id'] for row in expanded] == ['prior-result', 'visible']


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


def test_dreaming_window_reads_decision_statement_and_scene(tmp_path):
    """dreaming 窗口可含当日 decision_started 决策自述与观察 scene（docs/dev/SPEC-life-realism.md「两阶段决策」「呈现与导出」）。"""
    observed = event('observed', 1, 'observation', {'scene': '清晨在家，阳光很好。', 'balance_fen': 800})
    decision_statement = event('decision_statement-start', 2, 'decision_started',
                      {'decision_statement': '今天想先把报告写完，再去买菜。', 'phase': 'decision_statement'})
    requested = event('request-3', 3, 'action_requested', {'tool_name': 'plan_day'})
    requested.update(action_id='action-1')
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 3,
                'window': {'start': '2026-01-05T00:00:00+08:00', 'end': '2026-01-06T00:00:00+08:00',
                           'timezone': 'Asia/Shanghai'},
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    indexed = {row['event_id']: row for row in (observed, decision_statement, requested)}

    records = _dream_windows([observed, decision_statement, requested], manifest, tmp_path, indexed)

    assert len(records) == 3
    for record in records:
        rows = record['input']['events']
        decision_statement_rows = [row for row in rows if row['event_type'] == 'decision_started']
        assert [row['data'].get('decision_statement') for row in decision_statement_rows] == \
            ['今天想先把报告写完，再去买菜。']
        assert any(row['event_type'] == 'observation'
                   and '清晨在家' in str(row['data'].get('scene')) for row in rows)


def test_action_and_intent_windows_still_exclude_the_decision_statement_carrier(tmp_path):
    """信息范围合同：决策自述仅 dreaming 可读，动作/意图可见输入仍排除（docs/dev/SPEC-life-realism.md「两阶段决策」）。"""
    from worldmock.delivery.windows import PRIVATE_EVENTS

    assert 'decision_started' in PRIVATE_EVENTS
    observed = event('visible', 1, 'observation', {'balance': 800})
    decision_statement = event('decision_statement-start', 2, 'decision_started',
                      {'decision_statement': '今天想先把报告写完，再去买菜。', 'phase': 'decision_statement'})
    requested = event('target', 3, 'action_requested', {'tool_name': 'purchase'})
    requested['action_id'] = 'purchase'
    completed = event('completed', 4, 'action_result', {'status': 'completed', 'balance': 750})
    completed['action_id'] = 'purchase'
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 4,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    indexed = {row['event_id']: row for row in (observed, decision_statement, requested, completed)}

    action, intent = _action_windows(requested, {'purchase': [requested, completed]},
                                     {'decision': [observed, decision_statement, requested, completed]},
                                     (manifest, tmp_path, indexed))

    for record in (action, intent):
        rows = record['input']['events']
        assert all(row['event_type'] != 'decision_started' for row in rows)
        assert all('decision_statement' not in str(row['data']) for row in rows)
