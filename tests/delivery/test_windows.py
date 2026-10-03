from pathlib import Path

import pytest

from worldmock.delivery.windows import (
    _action_windows,
    _dream_windows,
    _expand_observed_results,
    _public_event_data,
    _window_identity,
    prepare_windows,
)
from tests.delivery.prompt_fixtures import layered_user, six_layers
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import StartActivityRequest


def event(identity, sequence, kind, data, visibility='agent'):
    return {'event_id': identity, 'event_seq': sequence, 'event_type': kind, 'occurred_at': 100 + sequence,
            'decision_id': 'decision', 'action_id': None, 'activity_id': None, 'agent_id': 1,
            'data': data, 'visibility': visibility, 'artifact_refs': []}


def decision_prompt(requested, rows):
    for row in rows:
        if row['event_seq'] >= requested['event_seq']:
            row['event_seq'] += 1
    tool_name = requested['data']['tool_name']
    parameters = (StartActivityRequest.model_json_schema(mode='validation')
                  if tool_name == 'start_activity' else {})
    prompt = event('prompt-' + requested['event_id'], requested['event_seq'] - 1,
                   'model_request', {'phase': 'request', 'request': {
                       'model': 'fixture',
                       'messages': [{'role': 'system', 'content': '人物身份'}, layered_user()],
                       'tools': [{'type': 'function', 'function': {
                           'name': tool_name, 'parameters': parameters}}],
                   }}, visibility='system')
    prompt['decision_id'] = requested['decision_id']
    prompt['agent_id'] = requested['agent_id']
    return prompt


def test_action_history_never_sees_future_answer_and_intent_cutoff_is_explicit(tmp_path):
    observed = event('visible', 1, 'observation', {'balance': 800})
    hidden = event('hidden', 2, 'model_response', {'secret_plan': 'future'}, 'agent')
    requested = event('target', 3, 'action_requested', {'tool_name': 'purchase'})
    requested['action_id'] = 'purchase'
    completed = event('completed', 4, 'action_result', {'status': 'completed', 'balance': 750})
    completed['action_id'] = 'purchase'
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 4,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    prompt = decision_prompt(requested, (observed, hidden, requested, completed))
    indexed = {row['event_id']: row for row in (observed, hidden, prompt, requested, completed)}
    action, intent = _action_windows(requested, {'purchase': [requested, completed]},
                                     {'decision': [observed, hidden, prompt, requested, completed]},
                                     (manifest, tmp_path, indexed))
    assert [item['event_id'] for item in action['input']['events']] == ['visible']
    assert action['input']['knowledge_cutoff_event_seq'] == 1
    assert action['target']['request'] == requested
    assert action['target']['outcomes'] == [completed]
    assert action['provenance']['source_event_ids'] == ['visible', prompt['event_id']]
    assert action['input']['decision_prompt'] == prompt['data']['request']
    assert action['provenance']['cutoff_event_seq'] == action['input']['knowledge_cutoff_event_seq'] == 1
    assert 'target_event_ids' not in action['provenance']
    assert [item['event_id'] for item in intent['input']['events']] == ['visible', 'target', 'completed']
    assert intent['input']['knowledge_cutoff_event_seq'] == completed['event_seq']
    assert all(item['event_id'] != 'hidden' for item in intent['input']['events'])


def test_latest_action_prompt_must_match_tool_instead_of_using_older_request(tmp_path):
    observed = _layered_observation('visible', 1)
    requested = event('target', 4, 'action_requested', {'tool_name': 'start_activity'})
    requested['action_id'] = 'action'
    older = decision_prompt(requested, (observed, requested))
    older['event_seq'] = 2
    latest = event('latest', 3, 'model_request', {'phase': 'request', 'request': {
        'messages': [{'role': 'system', 'content': 'Newest request'}],
        'tools': [{'function': {'name': 'send_message'}}],
    }}, visibility='system')
    rows = [observed, older, latest, requested]
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 4,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    with pytest.raises(IntegrityError, match='does not expose the requested tool'):
        _action_windows(requested, {'action': [requested]}, {'decision': rows},
                        (manifest, tmp_path, {row['event_id']: row for row in rows}))


def test_visible_prior_result_is_hydrated_as_direct_evidence_without_moving_cutoff(tmp_path):
    """观察 progress_event_ids 引用的先前结果并入输入；同一记录的动作请求来源不展开，也不移动知识截止。"""
    prior_request = event('prior-request', 0, 'action_requested', {'tool_name': 'submit_work'})
    prior_request.update(action_id='prior-action', decision_id='prior-decision')
    prior = event('prior-result', 1, 'action_result',
                  {'status': 'rejected', 'error_code': 'WORK_TASK_NOT_FOUND', 'message': 'Missing task'})
    prior.update(action_id='prior-action', decision_id='prior-decision')
    observed = event('visible', 2, 'observation', {'progress_event_ids': ['prior-request', 'prior-result']})
    requested = event('target', 3, 'action_requested', {
        'tool_name': 'start_activity',
        'request': {'items': [{'target': '自己', 'way': '等待', 'from': None, 'to': None}], 'answering': None, 'prefer': None}})
    requested['action_id'] = 'current-action'
    completed = event('current-result', 4, 'action_result', {'status': 'completed'})
    completed['action_id'] = 'current-action'
    prompt = decision_prompt(requested, (prior, observed, requested, completed))
    rows = (prior_request, prior, observed, prompt, requested, completed)
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 4,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    action, intent = _action_windows(requested, {'current-action': [requested, completed]},
                                     {'decision': [observed, prompt, requested, completed]},
                                     (manifest, tmp_path, {row['event_id']: row for row in rows}))

    assert [row['event_id'] for row in action['input']['events']] == ['prior-result', 'visible']
    assert action['input']['allowed_evidence_event_ids'] == ['prior-result', 'visible']
    assert action['input']['knowledge_cutoff_event_seq'] == 2
    assert action['input']['window_start'] == observed['occurred_at']
    assert action['provenance']['source_event_ids'] == [
        'prior-result', 'visible', prompt['event_id']]
    assert intent['input']['allowed_evidence_event_ids'] == [
        'prior-result', 'visible', 'target', 'current-result',
    ]


def test_action_public_identity_is_independent_of_the_future_target(tmp_path):
    observed = event('visible', 1, 'observation', {'balance': 800})
    first = event('first-request', 2, 'action_requested', {'tool_name': 'start_activity', 'secret': 'first'})
    first.update(action_id='first-action')
    first_result = event('first-result', 3, 'action_result', {'status': 'completed', 'secret': 'first'})
    first_result.update(action_id='first-action')
    first_prompt = decision_prompt(first, (observed, first, first_result))
    first_prompt['event_id'] = 'shared-prompt'
    second = event('second-request', 2, 'action_requested', {'tool_name': 'start_activity', 'secret': 'second'})
    second.update(action_id='second-action')
    second_result = event('second-result', 3, 'action_result', {'status': 'rejected', 'secret': 'second'})
    second_result.update(action_id='second-action')
    second_prompt = decision_prompt(second, (second, second_result))
    second_prompt['event_id'] = 'shared-prompt'
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 3,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}

    first_window, _ = _action_windows(
        first, {'first-action': [first, first_result]},
        {'decision': [observed, first_prompt, first, first_result]},
        (manifest, tmp_path, {row['event_id']: row for row in (observed, first_prompt, first_result)}),
    )
    second_window, _ = _action_windows(
        second, {'second-action': [second, second_result]},
        {'decision': [observed, second_prompt, second, second_result]},
        (manifest, tmp_path, {row['event_id']: row for row in (observed, second_prompt, second_result)}),
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
        EventDraft(event_type='model_request', occurred_at=100, agent_id=agent_id, decision_id=decision_id,
                   visibility='agent', data={'phase': 'request', 'request': {
                       'messages': [{'role': 'system', 'content': 'Persona and scene'}, layered_user()],
                       'tools': [{'type': 'function', 'function': {
                           'name': 'start_activity',
                           'parameters': StartActivityRequest.model_json_schema(mode='validation')}}]}}),
        EventDraft(event_type='action_requested', occurred_at=100, agent_id=agent_id,
                   decision_id=decision_id, action_id=action_id,
                   data={'tool_name': 'start_activity',
                         'request': {'items': [{'target': '自己', 'way': '等待', 'from': None, 'to': None}], 'answering': None, 'prefer': None}}),
        EventDraft(event_type='action_result', occurred_at=100, agent_id=agent_id,
                   decision_id=decision_id, action_id=action_id, data={'status': 'completed'}),
    ]


@pytest.mark.parametrize('invalid', ['missing', 'future', 'other_agent', 'private', 'wrong_type'])
def test_observed_result_must_resolve_to_exact_visible_same_agent_prior_event(invalid):
    """progress_event_ids 中无法解析、越过截止、他人、非人物可见或类型不符的结果与环境事件都拒绝。"""
    source = event('prior-result', 1, 'action_result', {'status': 'completed', 'message': 'Done'})
    source.update(action_id='prior-action', decision_id='prior-decision')
    if invalid == 'future':
        source['event_seq'] = 3
    if invalid == 'other_agent':
        source['agent_id'] = 2
    if invalid == 'private':
        source['visibility'] = 'system'
    if invalid == 'wrong_type':
        source['event_type'] = 'activity_progress'
    observed = event('visible', 2, 'observation', {'progress_event_ids': [source['event_id']]})
    indexed = {} if invalid == 'missing' else {source['event_id']: source}

    with pytest.raises(IntegrityError):
        _expand_observed_results([observed], indexed, cutoff=2)


def test_progress_sources_expand_visible_environment_events_and_skip_requests_and_private_evidence():
    """进度层来源按事件引用展开：消息送达等人物可见环境事件并入；动作请求与记忆查询的模型证据只跳过、不拒绝。"""
    arrival = event('arrival', 1, 'environment_event', {'kind': 'message_arrival', 'message_id': 'm1'})
    requested = event('prior-request', 2, 'action_requested', {'tool_name': 'send_message'})
    result = event('prior-result', 3, 'action_result', {'status': 'completed', 'message': 'Message sent'})
    response = event('memory-response', 4, 'model_response', {'phase': 'response'}, visibility='system')
    follow_up = event('memory-request', 5, 'model_request', {'phase': 'request'}, visibility='system')
    observed = event('visible', 6, 'observation', {'progress_event_ids': [
        'arrival', 'prior-request', 'prior-result', 'memory-response', 'memory-request']})
    indexed = {row['event_id']: row for row in (arrival, requested, result, response, follow_up, observed)}

    expanded = _expand_observed_results([observed], indexed, cutoff=6)

    assert [row['event_id'] for row in expanded] == ['arrival', 'prior-result', 'visible']


@pytest.mark.parametrize('ordering', ['after', 'equal'])
def test_observed_result_reference_may_not_reach_at_or_past_the_observation_itself(ordering):
    """引用排序守卫（docs/dev/SPEC-labelkit-delivery.md「信息范围与标签」「来源关联」）：未来事件不得进入引用集合。

    观察的 progress_event_ids 引用的结果事件序号 >= 观察自身序号时，
    导出管道必须经 `source["event_seq"] >= observation["event_seq"]` 拒绝。
    本用例把 knowledge cutoff 放宽到引用之后（cutoff=5 > 引用序号），
    确保拒绝只来自排序半句——同行的 `> cutoff` 半句是窗口成员资格已蕴含
    的冗余防御，'future' 既有用例（序号 3、cutoff=2）恰好只测到它。
    """
    sequence = 3 if ordering == 'after' else 2
    source = event('forward-result', sequence, 'action_result', {'status': 'completed', 'message': 'Done'})
    source.update(action_id='forward-action', decision_id='forward-decision')
    observed = event('visible', 2, 'observation', {'progress_event_ids': [source['event_id']]})
    indexed = {row['event_id']: row for row in (source, observed)}

    with pytest.raises(IntegrityError, match='knowledge cutoff'):
        _expand_observed_results([observed], indexed, cutoff=5)

    prior = event('prior-result', 1, 'action_result', {'status': 'completed', 'message': 'Done'})
    prior.update(action_id='prior-action', decision_id='prior-decision')
    earlier_observed = event('visible', 2, 'observation', {'progress_event_ids': [prior['event_id']]})
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


def _layered_observation(identity, sequence, **extra):
    """构造按第八版世界结构保存的观察：四个环境构建层原文，加上私有绑定、看法偏好与证据键。

    另带一个旧结构的 scene 字典：旧交付代码会把它重新渲染进窗口，新代码必须既不渲染也不暴露它。
    """
    data = {
        'tool_layer': {'layer': '工具层', 'targets': [{'name': '自己', 'kind': 'self', 'phase': None, 'state': None,
                                                       'actions': [], 'tools': []}]},
        'todo_layer': {'layer': '待办层', 'paused_activities': [], 'errands': [], 'day_plan': None},
        'environment_layer': {'layer': '环境层', 'observed_at': '2026-01-05T07:30:00+08:00',
                              'body': {'appetite': '不饿', 'sleepiness': '清醒', 'entertainment': '平常',
                                       'social': '平常', 'mood': '平静'},
                              'location': {'session_kind': 'stay', 'name': '家', 'place_type': '住宅',
                                           'fixed_rules': []}},
        'memory_layer': {'layer': '记忆层', 'earlier_days': [], 'earlier_today': []},
        'persona_views': {'relationships': [], 'preferences': []},
        'interaction_bindings': [{'object': '自己', 'kind': 'self', 'object_id': 'agent:1', 'actions': []}],
        'progress_event_ids': [], 'presented_message_ids': [], 'balance_fen': 800,
        'scene': {'location': '家'},
    }
    data.update(extra)
    return event(identity, sequence, 'observation', data)


_LAYER_KEYS = ('tool_layer', 'todo_layer', 'environment_layer', 'memory_layer')


def _action_prompt(identity, sequence, layers, tool_name='start_activity', purpose=None):
    """同一决策中的一次行动请求（或带 purpose 的其他调用）：system 加六层 user。"""
    data = {'phase': 'request', 'request': {
        'model': 'fixture', 'messages': [{'role': 'system', 'content': '系统层'}, layered_user(layers)],
        'tools': [{'type': 'function', 'function': {'name': tool_name, 'parameters': {}}}]}}
    if purpose:
        data['purpose'] = purpose
    return event(identity, sequence, 'model_request', data, visibility='system')


def _dreaming_day(*rows):
    """一个自然日的 dreaming 输入：按事件构造三种模式的窗口。"""
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': rows[-1]['event_seq'],
                'window': {'start': '2026-01-05T00:00:00+08:00', 'end': '2026-01-06T00:00:00+08:00',
                           'timezone': 'Asia/Shanghai'},
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    return _dream_windows(list(rows), manifest, Path('.'), {row['event_id']: row for row in rows})


def test_dreaming_window_reads_self_note_and_the_actual_request_layer_json(monkeypatch):
    """dreaming 读 self_note 与实际请求中的层 JSON（docs/dev/SPEC-persona-prompt.md 第七章「交付」）：每个动作请求取
    同决策动作前最后一次实际行动请求的首个 user 六层，请求事件身份与层 JSON 摘要写入来源；零点计划与动作之后的请求不取。
    decision_started 与 model_request 是私有事件，不作窗口事件；交付从不调用场景渲染器。"""
    import hashlib

    import worldmock.delivery.windows as windows
    import worldmock.life.narration as narration
    from worldmock.footprints.models import canonical_json

    def never(*_args, **_kwargs):
        raise AssertionError('delivery must not re-render the observation')

    monkeypatch.setattr(narration, 'render_scene', never, raising=False)
    assert windows.DREAMING_PRIVATE_EVENTS == windows.PRIVATE_EVENTS
    assert not hasattr(windows, 'render_scene')
    head, recalled = six_layers(), six_layers([{'type': 'message_arrival', 'sender': '王磊', 'text': '帮我带杯咖啡'}])
    observed = _layered_observation('observed', 1)
    started = event('decision-start', 2, 'decision_started',
                    {'round_id': 'r1', 'round_order': 0, 'world_state_version': 3, 'wake_reasons': []})
    first, latest = _action_prompt('prompt-first', 3, head), _action_prompt('prompt-latest', 4, recalled)
    planned = _action_prompt('plan-day', 5, six_layers(), purpose='plan_day')
    requested = event('request-3', 6, 'action_requested', {
        'tool_name': 'start_activity', 'request': {'items': [{'target': '自己', 'way': '等待', 'from': None, 'to': None}], 'answering': None, 'prefer': None},
        'request_sha256': 'f' * 64, 'reused': False, 'self_note': '先歇一会儿，再去买菜。'})
    requested.update(action_id='action-1')
    after = _action_prompt('prompt-after', 7, six_layers())

    records = _dreaming_day(observed, started, first, latest, planned, requested, after)

    assert len(records) == 3
    digest = hashlib.sha256(canonical_json(recalled)).hexdigest()
    for record in records:
        rows = {row['event_id']: row for row in record['input']['events']}
        assert set(rows) == {'observed', 'request-3'}
        assert rows['observed']['data'] == {key: observed['data'][key] for key in _LAYER_KEYS}
        assert rows['request-3']['data'] == {'tool_name': 'start_activity',
                                             'request': requested['data']['request'],
                                             'self_note': '先歇一会儿，再去买菜。'}
        assert record['input']['decision_layers'] == [{'action_event_id': 'request-3', 'layers': recalled}]
        assert record['provenance']['decision_layer_sources'] == [{'event_id': 'prompt-latest', 'sha256': digest}]
        assert 'prompt-latest' in record['provenance']['source_event_ids']
        assert 'prompt-latest' not in record['input']['allowed_evidence_event_ids']
        assert record['provenance']['window_id'] == _window_identity('dreaming', record['input'])


@pytest.mark.parametrize('broken', ['missing', 'no_user', 'plain_text', 'wrong_order', 'extra_item'])
def test_dreaming_rejects_an_action_without_the_recorded_layer_json(broken):
    """动作请求没有实际行动请求、请求没有 user、user 不是六层 JSON 或层序不符时拒绝，不从观察猜测层 JSON。"""
    layers = six_layers()
    if broken == 'wrong_order':
        layers[0], layers[1] = layers[1], layers[0]
    if broken == 'extra_item':
        layers.append('多余')
    prompt = _action_prompt('prompt', 2, layers)
    messages = prompt['data']['request']['messages']
    if broken == 'no_user':
        del messages[1]
    if broken == 'plain_text':
        messages[1]['content'] = '人物卡：安。场景：办公室。'
    requested = event('request', 3, 'action_requested', {'tool_name': 'start_activity', 'self_note': None})
    rows = [_layered_observation('observed', 1)] + ([] if broken == 'missing' else [prompt]) + [requested]

    with pytest.raises(IntegrityError, match='no recorded decision prompt' if broken == 'missing' else 'six-layer'):
        _dreaming_day(*rows)


_SECRET = '其实我一直在想辞职的事。'
_OPINION = '王磊做事总拖，下次不找他了。'
_MINUTES = '工作写会议纪要（1月5日 09:00 起）'


def _conclusion_windows(tmp_path, request: dict) -> tuple[dict, dict, dict, list[dict]]:
    """带记忆的收尾请求所在决策的动作、意图与 dreaming 窗口：返回请求事件、动作窗口、意图窗口与三个 dreaming 窗口。"""
    observed = _layered_observation('visible', 1)
    requested = event('target', 2, 'action_requested', {'tool_name': 'conclude_activity', 'request': request})
    requested['action_id'] = 'conclude'
    written = event('memory', 3, 'memory_updated', {
        'operation': 'append_episodes', 'trigger': ['activity_conclusion'], 'decision_id': 'decision',
        'episodes': [{'id': 'm1', 'type': 'reflection', 'text': _SECRET, 'epistemic_status': 'subjective'}]},
        visibility='system')
    completed = event('completed', 4, 'action_result', {'status': 'completed', 'message': 'Activities concluded'})
    completed['action_id'] = 'conclude'
    prompt = decision_prompt(requested, (observed, requested, written, completed))
    rows = (observed, prompt, requested, written, completed)
    indexed = {row['event_id']: row for row in rows}
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': completed['event_seq'],
                'window': {'start': '1970-01-01T08:00:00+08:00', 'end': '1970-01-02T08:00:00+08:00',
                           'timezone': 'Asia/Shanghai'},
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    action, intent = _action_windows(requested, {'conclude': [requested, completed]}, {'decision': list(rows)},
                                     (manifest, tmp_path, indexed))
    return requested, action, intent, _dream_windows(list(rows), manifest, tmp_path, indexed)


def _trimmed_shown(action: dict, intent: dict, dreams: list[dict]) -> list[dict]:
    """意图与 dreaming 窗口输入里显示的收尾请求；三类窗口的输入都不含记忆与看法正文。"""
    for record in (action, intent, *dreams):
        assert _SECRET not in str(record['input']) and _OPINION not in str(record['input'])
    return [next(row for row in record['input']['events'] if row['event_id'] == 'target')['data']
            for record in (intent, *dreams)]


def test_conclude_activity_with_memories_does_not_leak_memory_text(tmp_path):
    """带记忆与看法的 conclude_activity 不泄露记忆正文（docs/dev/SPEC-time-and-energy.md 第九章交付链；交付设计「证据窗口与
    输入结构」）。

    意图与 dreaming 窗口输入里的动作请求经 control_items 读出，每项只保留 activity_name 与 summary（items[].memories 与
    items[].views 裁去，docs/dev/SPEC-dayrun-fidelity.md 第六章）；动作窗口的目标保留完整请求，写入记忆的
    memory_updated 仍是私有事件，既不进输入也不进 target.outcomes。
    """
    request = {'items': [{'activity_name': _MINUTES, 'summary': '纪要写完了。',
                          'memories': [{'type': 'reflection', 'text': _SECRET, 'importance': 0.6, 'keywords': ['辞职']}],
                          'views': [{'type': 'relationship', 'about': '王磊', 'text': _OPINION, 'importance': 0.5,
                                     'keywords': ['同事']}]}], 'errands': []}
    requested, action, intent, dreams = _conclusion_windows(tmp_path, request)

    assert action['target']['request'] == requested
    assert action['target']['request']['data']['request']['items'][0]['memories'][0]['text'] == _SECRET
    assert [row['event_id'] for row in action['target']['outcomes']] == ['completed']
    assert len(dreams) == 3
    trimmed = {'tool_name': 'conclude_activity', 'request': {'items': [{'activity_name': _MINUTES,
                                                                        'summary': '纪要写完了。'}]}}
    assert _trimmed_shown(action, intent, dreams) == [trimmed] * 4


def test_retired_control_request_shapes_are_rejected_by_delivery(tmp_path):
    """交付拒绝退役控制请求，不把旧数组或旧记忆字段转换成现行请求。"""
    import pytest

    from worldmock.errors import IntegrityError

    legacy = {'task': [_MINUTES], 'summary': ['纪要写完了。'], 'memories': [[
        {'type': 'reflection', 'text': _SECRET, 'importance': 0.6, 'keywords': ['辞职'], 'subject': None},
        {'type': 'relationship', 'text': _OPINION, 'importance': 0.5, 'keywords': ['同事'], 'subject': '王磊'}]]}
    with pytest.raises(IntegrityError, match="request shape"):
        _conclusion_windows(tmp_path, legacy)
    items = {'items': [{'activity_name': _MINUTES, 'summary': '纪要写完了。'}]}
    earlier = ({**legacy, 'memories': [[{key: value for key, value in memory.items() if key != 'subject'}
                                         for memory in legacy['memories'][0]]]},
               {'task': legacy['task'], 'summary': legacy['summary']})
    for tool, request in [*(('conclude_activity', body) for body in earlier),
                          *((tool, {'task': legacy['task'], 'summary': legacy['summary']})
                            for tool in ('report_status', 'pause_activity'))]:
        with pytest.raises(IntegrityError, match="request shape"):
            _public_event_data({'event_type': 'action_requested', 'data': {'tool_name': tool, 'request': request}})
    for tool in ('report_status', 'pause_activity'):
        shown = _public_event_data({'event_type': 'action_requested', 'data': {
            'tool_name': tool, 'request': {**items, 'errands': []}, 'self_note': '收个尾。'}})
        assert shown == {'tool_name': tool, 'request': items, 'self_note': '收个尾。'}


def test_control_requests_without_errands_are_rejected_by_delivery(tmp_path):
    """带 items 而缺请求级 errands 的控制请求是旧形状记录（docs/dev/SPEC-errands-workday.md 2.3、2.9）：交付构造窗口时经
    control_items 以完整性故障拒绝，不当作没有事务变更的现行请求放行；三个控制工具的请求事件投影同样拒绝。带空 errands
    的同一请求照常构造动作、意图与三个 dreaming 窗口，投影不带 errands。"""
    lacking = {'items': [{'activity_name': _MINUTES, 'summary': '纪要写完了。', 'memories': [], 'views': []}]}
    with pytest.raises(IntegrityError, match='conclude_activity request lacks errands'):
        _conclusion_windows(tmp_path, lacking)
    for tool in ('report_status', 'pause_activity', 'conclude_activity'):
        items = {'items': [{'activity_name': _MINUTES, 'summary': '纪要写完了。',
                            **({'memories': [], 'views': []} if tool == 'conclude_activity' else {})}]}
        with pytest.raises(IntegrityError, match=f'{tool} request lacks errands'):
            _public_event_data({'event_type': 'action_requested', 'data': {'tool_name': tool, 'request': items}})
    current = {**lacking, 'errands': []}
    requested, action, intent, dreams = _conclusion_windows(tmp_path, current)
    assert action['target']['request'] == requested and requested['data']['request'] == current
    trimmed = {'tool_name': 'conclude_activity', 'request': {'items': [{'activity_name': _MINUTES,
                                                                        'summary': '纪要写完了。'}]}}
    assert _trimmed_shown(action, intent, dreams) == [trimmed] * 4


def test_action_and_intent_windows_show_layers_and_self_note_but_never_decision_started(tmp_path):
    """动作与意图窗口：观察只给四个环境构建层原文，意图输入中的动作请求带 self_note，私有的 decision_started 不进入。"""
    from worldmock.delivery.windows import PRIVATE_EVENTS

    assert 'decision_started' in PRIVATE_EVENTS
    observed = _layered_observation('visible', 1)
    started = event('decision-start', 2, 'decision_started',
                    {'round_id': 'r1', 'round_order': 0, 'world_state_version': 3, 'wake_reasons': []})
    requested = event('target', 3, 'action_requested', {
        'tool_name': 'start_activity', 'request': {'items': [{'target': '自己', 'way': '等待', 'from': None, 'to': None}], 'answering': None, 'prefer': None},
        'request_sha256': 'f' * 64, 'reused': False, 'self_note': '有点累，先歇一会儿。'})
    requested['action_id'] = 'wait'
    completed = event('completed', 4, 'action_result', {'status': 'completed', 'message': 'Done'})
    completed['action_id'] = 'wait'
    prompt = decision_prompt(requested, (observed, started, requested, completed))
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': 4,
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    indexed = {row['event_id']: row for row in (observed, started, prompt, requested, completed)}

    action, intent = _action_windows(requested, {'wait': [requested, completed]},
                                     {'decision': [observed, started, prompt, requested, completed]},
                                     (manifest, tmp_path, indexed))

    for record in (action, intent):
        rows = {row['event_id']: row for row in record['input']['events']}
        assert 'decision-start' not in rows
        assert rows['visible']['data'] == {key: observed['data'][key] for key in _LAYER_KEYS}
        serialized = str(record['input']['events'])
        for private in ('interaction_bindings', 'persona_views', 'progress_event_ids', 'scene', 'balance_fen'):
            assert private not in serialized
    assert rows['target']['data']['self_note'] == '有点累，先歇一会儿。'
    assert action['target']['request']['data']['self_note'] == '有点累，先歇一会儿。'


def test_actual_decision_prompt_is_authoritative_and_private_review_never_becomes_target(tmp_path):
    observed = _layered_observation(
        'observed', 1, affordance_contract={'secret': 'catalog'},
        energy_feasibility={'secret': 'energy'}, game_catalog=['hidden-game'],
        interaction_menu={'secret': 'menu'}, body={'values': {'energy_kcal': 900}},
    )
    requested = event('requested', 3, 'action_requested', {'tool_name': 'start_activity'})
    requested['action_id'] = 'selected-action'
    prompt = decision_prompt(requested, (observed, requested))
    prompt['data']['request']['messages'][1]['content'] = '人物卡：安。场景：办公室。记忆：昨天写了报告。'
    prompt['data']['request']['messages'].extend([
        {'role': 'assistant', 'content': None, 'reasoning_content': '先查看刚才的工具反馈。',
         'tool_calls': [{'id': 'call-1', 'type': 'function',
                         'function': {'name': 'memory_recent', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': '没有找到新记忆。'},
    ])
    private_review = event('private-review', 5, 'model_request', {
        'phase': 'request', 'purpose': 'work_review', 'request': {'hidden_answer': 'private'}}, 'system')
    private_review['action_id'] = requested['action_id']
    completed = event('completed', 6, 'action_result', {'status': 'completed', 'message': 'Done'})
    completed['action_id'] = requested['action_id']
    rows = (observed, prompt, requested, private_review, completed)
    manifest = {'run_id': 'run', 'source_group_id': 'run',
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}

    action, intent = _action_windows(requested, {requested['action_id']: [requested, private_review, completed]},
                                     {'decision': list(rows)},
                                     (manifest, tmp_path, {row['event_id']: row for row in rows}))

    assert action['input']['decision_prompt'] == prompt['data']['request']
    assert action['input']['decision_prompt']['messages'][2]['reasoning_content'] == '先查看刚才的工具反馈。'
    assert '人物卡：安' in action['input']['decision_prompt']['messages'][1]['content']
    assert action['input']['prerequisite_state']['state'] is None
    assert action['target']['outcomes'] == [completed]
    assert private_review not in intent['target']['outcomes']
    for record in (action, intent):
        serialized = str(record['input'])
        assert 'affordance_contract' not in serialized
        assert 'energy_feasibility' not in serialized
        assert 'game_catalog' not in serialized
        assert 'interaction_menu' not in serialized
        assert 'hidden_answer' not in serialized
        assert record['provenance']['decision_prompt_event_id'] == prompt['event_id']

    from worldmock.delivery.evidence_encoding import encode_model_record
    from worldmock.delivery.pipeline import _model_source
    source = _model_source('action', 1, encode_model_record(action))
    assert source['decision_prompt_event_id'] == prompt['event_id']
    assert source['decision_prompt_sha256'] == action['provenance']['decision_prompt_sha256']

    tampered = action.copy()
    tampered['input'] = dict(action['input'], decision_prompt={'messages': [], 'tools': []})
    tampered['provenance'] = dict(action['provenance'],
                                  window_id=_window_identity('action', tampered['input']))
    with pytest.raises(IntegrityError, match='source digest'):
        _model_source('action', 1, encode_model_record(tampered))


def test_continued_same_second_request_is_kept_verbatim_in_action_and_intent_windows(tmp_path):
    """同秒续接的实际请求：链首 system 与 user 之后是上一决策的 assistant 消息（带 reasoning_content 与世界请求调用）和
    对应的 tool 消息；动作与意图窗口原样保留这份实际请求，不重建、不删减（docs/dev/SPEC-persona-prompt.md 验收台账「下游」）。"""
    import copy
    import hashlib
    import json

    from worldmock.footprints.models import canonical_json

    observed = _layered_observation('observed', 1)
    requested = event('requested', 3, 'action_requested', {
        'tool_name': 'conclude_activity', 'request': {'items': [
            {'activity_name': '等待（1月5日 07:30 起）', 'summary': '歇好了。', 'memories': [], 'views': []}],
            'errands': []},
        'request_sha256': 'e' * 64, 'reused': False, 'self_note': None})
    requested['action_id'] = 'conclude'
    prompt = decision_prompt(requested, (observed, requested))
    wait = {'items': [{'target': '自己', 'way': '等待', 'from': None, 'to': None}], 'answering': None, 'prefer': None}
    earlier = {'role': 'assistant', 'content': '先歇一会儿。', 'reasoning_content': '同一秒里先开始等待。',
               'tool_calls': [{'id': 'call-wait', 'type': 'function', 'function': {
                   'name': 'start_activity', 'arguments': json.dumps(wait, ensure_ascii=False)}}]}
    result = {'occurred_at': '2026-01-05T07:30:00+08:00', 'status': 'in_progress', 'error_code': None,
              'message': 'Activity started', 'details': {'planned': [{'name': '等待（1月5日 07:30 起）',
                                                                      'duration': '约 12 分钟'}]}}
    feedback = {'role': 'tool', 'tool_call_id': 'call-wait', 'content': json.dumps(
        {'result': result, 'changes': {'targets': {'updated': [], 'removed': ['自己']}}},
        ensure_ascii=False, separators=(',', ':'))}
    prompt['data']['request']['messages'].extend([earlier, feedback])
    sent = copy.deepcopy(prompt['data']['request'])
    completed = event('completed', 5, 'action_result', {'status': 'completed', 'message': 'Done'})
    completed['action_id'] = 'conclude'
    rows = (observed, prompt, requested, completed)
    manifest = {'run_id': 'run', 'source_group_id': 'run',
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}

    action, intent = _action_windows(requested, {'conclude': [requested, completed]}, {'decision': list(rows)},
                                     (manifest, tmp_path, {row['event_id']: row for row in rows}))

    digest = hashlib.sha256(canonical_json(sent)).hexdigest()
    for record in (action, intent):
        assert record['input']['decision_prompt'] == sent
        assert record['input']['decision_prompt']['messages'][2:] == [earlier, feedback]
        assert record['provenance']['decision_prompt_sha256'] == digest


def test_plan_day_request_never_becomes_a_decision_prompt_or_window_input(tmp_path):
    """零点计划 plan_day 的请求带 purpose，即便晚于人物行动请求也不作窗口 prompt；其证据与计划登记是系统事件，不进窗口。"""
    observed = _layered_observation('observed', 1)
    requested = event('requested', 5, 'action_requested', {'tool_name': 'start_activity'})
    requested['action_id'] = 'selected-action'
    prompt = decision_prompt(requested, ())
    prompt['event_seq'] = 2
    plan_request = event('plan-request', 3, 'model_request', {
        'phase': 'request', 'purpose': 'plan_day', 'call_id': 'plan-day:1:2026-01-05:attempt:1',
        'request': {'model': 'fixture', 'messages': [{'role': 'user', 'content': '零点计划输入'}],
                    'tools': [{'type': 'function', 'function': {'name': 'start_activity', 'parameters': {}}}]}},
        'system')
    plan_request['decision_id'] = None
    registered = event('plan-registered', 4, 'environment_event', {
        'kind': 'day_plan_generated', 'day_plan': {'day_end': 1000, 'goals': []}, 'source_event_ids': []}, 'system')
    completed = event('completed', 6, 'action_result', {'status': 'completed', 'message': 'Done'})
    completed['action_id'] = requested['action_id']
    rows = (observed, prompt, plan_request, registered, requested, completed)
    manifest = {'run_id': 'run', 'source_group_id': 'run',
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}

    action, intent = _action_windows(requested, {requested['action_id']: [requested, completed]},
                                     {'decision': list(rows)},
                                     (manifest, tmp_path, {row['event_id']: row for row in rows}))

    assert action['input']['decision_prompt'] == prompt['data']['request']
    assert action['provenance']['decision_prompt_event_id'] == prompt['event_id']
    for record in (action, intent):
        assert '零点计划输入' not in str(record['input']) and 'day_plan_generated' not in str(record['input'])


def _reflection_request(identity, sequence, retry_of=None):
    """就寝反思的一次模型请求：不带 purpose，思考关闭，工具唯一为 submit_reflection；校验重试的请求另带附回的 user 消息。"""
    messages = [{'role': 'system', 'content': 'Reflect on a simulated person\'s recent memories.'},
                {'role': 'user', 'content': '<memory_reflection_input>{"episodes":[]}</memory_reflection_input>'}]
    if retry_of:
        messages.append({'role': 'user', 'content': '{"VALIDATION_ERROR":"' + retry_of + '"}'})
    return event(identity, sequence, 'model_request', {'phase': 'request', 'call_id': identity, 'request': {
        'model': 'fixture', 'messages': messages, 'tool_choice': 'required',
        'extra_body': {'thinking': {'type': 'disabled'}},
        'tools': [{'type': 'function', 'function': {'name': 'submit_reflection', 'parameters': {}}}]}},
        visibility='system')


def test_reflection_requests_after_the_action_never_become_the_decision_prompt(tmp_path):
    """含就寝反思的决策，其交付窗口的决策提示仍是动作之前的行动请求（docs/dev/SPEC-errands-workday.md 1.1「身份与判定」）。

    醒来那一轮先登记收尾请求，记忆运行时随后才发出就寝反思（一次不合合同、一次重试）：两条反思请求同样不带 purpose、
    带工具，但事件序号都在 action_requested 之后。动作、意图与 dreaming 窗口取的都是动作之前最后一份不带 purpose 的
    请求（工具含 conclude_activity 的行动请求）；反思的请求、响应与提示文字不进任何窗口输入。"""
    observed = _layered_observation('visible', 1)
    request = {'items': [{'activity_name': _MINUTES, 'summary': '睡醒了。', 'memories': [], 'views': []}],
               'errands': []}
    requested = event('target', 2, 'action_requested', {'tool_name': 'conclude_activity', 'request': request})
    requested['action_id'] = 'conclude'
    first = _reflection_request('reflection-1', 3)
    refused = event('reflection-1-response', 4, 'model_response', {'phase': 'response', 'call_id': 'reflection-1'},
                    visibility='system')
    retry = _reflection_request('reflection-2', 5, retry_of='response must contain exactly one tool call')
    written = event('memory', 6, 'memory_updated', {
        'operation': 'append_episodes', 'trigger': ['reflection'], 'decision_id': 'decision',
        'episodes': [{'id': 'm1', 'type': 'reflection', 'text': _SECRET, 'epistemic_status': 'subjective',
                      'source': 'reflection'}]}, visibility='system')
    completed = event('completed', 7, 'action_result', {'status': 'completed', 'message': 'Activities concluded'})
    completed['action_id'] = 'conclude'
    prompt = decision_prompt(requested, (observed, requested, first, refused, retry, written, completed))
    rows = (observed, prompt, requested, first, refused, retry, written, completed)
    assert [row['event_seq'] for row in rows] == list(range(1, 9))
    assert prompt['event_seq'] < requested['event_seq'] < first['event_seq'] < retry['event_seq']
    indexed = {row['event_id']: row for row in rows}
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'cutoff_event_seq': completed['event_seq'],
                'window': {'start': '1970-01-01T08:00:00+08:00', 'end': '1970-01-02T08:00:00+08:00',
                           'timezone': 'Asia/Shanghai'},
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}

    action, intent = _action_windows(requested, {'conclude': [requested, completed]}, {'decision': list(rows)},
                                     (manifest, tmp_path, indexed))
    dreams = _dream_windows(list(rows), manifest, tmp_path, indexed)

    for record in (action, intent):
        assert record['provenance']['decision_prompt_event_id'] == prompt['event_id']
        assert record['input']['decision_prompt'] == prompt['data']['request']
        assert [tool['function']['name'] for tool in record['input']['decision_prompt']['tools']] == [
            'conclude_activity']
    assert len(dreams) == 3
    for record in dreams:
        assert [source['event_id'] for source in record['provenance']['decision_layer_sources']] == [prompt['event_id']]
    for record in (action, intent, *dreams):
        assert 'submit_reflection' not in str(record['input']) and 'memory_reflection_input' not in str(record['input'])
        assert not {'reflection-1', 'reflection-2'} & set(record['provenance']['source_event_ids'])
