import json

import pytest

from tests.text_encoding import default_encoding_reads
from worldmock.errors import IntegrityError
from worldmock.footprints.store import FootprintStore
from worldmock.status import observed_model_usage, read_status, write_status


def test_status_reads_confirmed_state_without_modifying_any_run_file(tmp_path):
    run = tmp_path / 'run'
    store = FootprintStore.create(run, {'run_id': 'run', 'agent_ids': [1], 'window': {'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'end_time': 200, 'activities': [],
                                   'agents': {'1': {'agent_id': 1, 'location_id': 'home',
                                                    'next_decision_at': 100}}})
    store.close()
    write_status(run, 'running', 'deciding')
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    result = read_status(run)
    after = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    assert before == after
    assert result['state_version'] == 0
    assert result['event_counts'] == {}
    assert result['runtime'] == {'status': 'running', 'phase': 'deciding', 'error_type': None}
    assert result['token_stats'] is None
    assert result['people'][0]['next_decision_at'] == 100
    assert result['people'][0]['active_activities'] == []
    assert result['wall_time']['elapsed_seconds'] is None


def test_status_preserves_earlier_body_decision_time_instead_of_activity_end(tmp_path):
    run = tmp_path / 'run'
    store = FootprintStore.create(run, {'run_id': 'run', 'agent_ids': [1], 'window': {'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'end_time': 2000,
                                   'activities': [{'status': 'active', 'planned_end_at': 1900,
                                                   'activity_id': 'watch', 'agent_id': 1, 'started_at': 100,
                                                   'next_interaction_at': 1000}],
                                   'agents': {'1': {'agent_id': 1, 'location_id': 'home',
                                                    'next_decision_at': 143}}})
    store.close()
    before = (run / 'footprints.sqlite').read_bytes()
    assert read_status(run)['people'][0]['next_decision_at'] == 143
    assert (run / 'footprints.sqlite').read_bytes() == before


def test_status_refuses_a_run_directory_whose_world_activities_are_not_an_array(tmp_path, caplog):
    """更早的引擎写下的运行目录（活动是以身份为键的映射）：状态查询写英文错误日志，以可读的 ValueError 拒绝，不读它。"""
    import logging

    run = tmp_path / 'run'
    store = FootprintStore.create(run, {'run_id': 'run', 'agent_ids': [1], 'window': {'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'end_time': 200, 'activities': {},
                                   'agents': {'1': {'agent_id': 1, 'location_id': 'home',
                                                    'next_decision_at': 100}}})
    store.close()
    message = 'Run directory was written by an older engine: world activities are not an array'
    caplog.set_level(logging.ERROR)
    with pytest.raises(ValueError, match=f'^{message}$'):
        read_status(run)
    assert message in caplog.text


def _store_with_responses(run, models):
    """建一个最小运行库，按给定模型名各落一条带用量的模型响应（None 表示响应不带模型名）。"""
    from worldmock.footprints.models import EventDraft

    store = FootprintStore.create(run, {'run_id': '运行一', 'agent_ids': [1], 'window': {'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'end_time': 200, 'activities': [],
                                   'agents': {}})
    usage = {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}
    drafts = [EventDraft(event_type='model_response', occurred_at=100, source_kind='model', visibility='system',
                         data={'phase': 'response', 'response': {**({} if model is None else {'model': model}),
                                                                 'usage': usage}})
              for model in models]
    if drafts:
        store.append_evidence(drafts)
    store.close()


def test_status_reads_the_three_run_files_as_utf8_without_the_default_encoding(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    _store_with_responses(run, [])
    write_status(run, 'completed', 'stopped')
    receipt = [{'model': '模型甲', 'responses': 1, 'responses_with_usage': 0,
                'input_tokens': None, 'output_tokens': None, 'total_tokens': None}]
    (run / 'result.json').write_text(json.dumps({'token_stats': receipt}, ensure_ascii=False), encoding='utf-8')
    calls = default_encoding_reads(monkeypatch)
    result = read_status(run)
    assert calls == []
    assert result['run_id'] == '运行一' and result['token_stats'] == receipt
    assert result['runtime'] == {'status': 'completed', 'phase': 'stopped', 'error_type': None}


def test_observed_usage_rows_are_ordered_by_model_and_both_keys_are_reported(tmp_path):
    run = tmp_path / 'run'
    _store_with_responses(run, ['b', 'a', None, 'b'])
    rows = observed_model_usage(run)
    assert [row['model'] for row in rows] == [None, 'a', 'b']
    assert rows[2] == {'model': 'b', 'responses': 2, 'responses_with_usage': 2,
                       'input_tokens': 6, 'output_tokens': 4, 'total_tokens': 10}
    result = read_status(run)
    assert result['observed_model_usage'] == rows and result['token_stats'] is None


def test_observed_usage_is_empty_without_responses_and_refuses_a_missing_database(tmp_path, caplog):
    run = tmp_path / 'run'
    _store_with_responses(run, [])
    assert observed_model_usage(run) == []
    with pytest.raises(IntegrityError, match='^Run database does not exist$'):
        observed_model_usage(tmp_path / 'absent')
    assert 'Run database does not exist' in caplog.text
