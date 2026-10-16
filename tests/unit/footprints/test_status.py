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


def test_status_lists_the_five_latest_checkpoint_ids_without_reading_their_bodies(tmp_path):
    """recent_checkpoints 是最近五行检查点的身份，按写入先后倒排，直接取主键列：六行正文都不是 zlib 流、摘要全零，
    解析或解压正文的读取方会在这些行上失败。"""
    run = tmp_path / 'run'
    store = FootprintStore.create(run, {'run_id': 'run', 'agent_ids': [1], 'window': {'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'end_time': 200, 'activities': [],
                                   'agents': {'1': {'agent_id': 1, 'location_id': 'home',
                                                    'next_decision_at': 100}}})
    with store.connection:
        for index in range(1, 7):
            store.connection.execute('INSERT INTO checkpoints VALUES(?,?,?)', (f'cp-{index}', b'not zlib', '0' * 64))
    store.close()
    assert read_status(run)['recent_checkpoints'] == ['cp-6', 'cp-5', 'cp-4', 'cp-3', 'cp-2']


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


# ---- 运行中可查：短读、按类型索引计数（长时运行成本第四章） ----


def _status_run(run):
    """一个带五类事件的运行库：两种模型、两个阶段的模型响应，以及观察、行动结果和检查点前的证据。"""
    from worldmock.footprints.models import EventDraft

    store = FootprintStore.create(run, {'run_id': 'run', 'agent_ids': [1], 'window': {'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'end_time': 200, 'activities': [],
                                   'agents': {}})
    usage = {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}

    def response(model, phase):
        return EventDraft(event_type='model_response', occurred_at=100, source_kind='model', visibility='system',
                          data={'phase': phase, 'response': {'model': model, 'usage': usage}})

    drafts = [EventDraft(event_type='observation', occurred_at=100, agent_id=1),
              EventDraft(event_type='model_request', occurred_at=100, source_kind='model', visibility='system',
                         data={'phase': 'request', 'request': {}}),
              response('flash', 'response'), response('pro', 'error'), response('pro', 'response'),
              EventDraft(event_type='action_result', occurred_at=100, agent_id=1),
              EventDraft(event_type='observation', occurred_at=100, agent_id=1),
              response('flash', 'response')]
    for draft in drafts:
        store.append_evidence([draft])
    store.close()


def test_status_counts_and_wall_time_equal_the_whole_history_queries(tmp_path):
    import sqlite3
    from datetime import datetime

    run = tmp_path / 'run'
    _status_run(run)
    result = read_status(run)
    connection = sqlite3.connect(run / 'footprints.sqlite')
    try:
        grouped = connection.execute("SELECT json_extract(body,'$.event_type'),count(*) FROM events "
                                     "GROUP BY json_extract(body,'$.event_type')").fetchall()
        lowest, highest = connection.execute("SELECT min(json_extract(body,'$.recorded_at')),"
                                             "max(json_extract(body,'$.recorded_at')) FROM events").fetchone()
    finally:
        connection.close()
    assert len(grouped) >= 4
    assert list(result['event_counts'].items()) == grouped
    assert result['wall_time']['started_at'] == datetime.fromisoformat(lowest).isoformat()
    assert result['wall_time']['last_event_at'] == datetime.fromisoformat(highest).isoformat()


def test_status_issues_no_explicit_transaction(tmp_path, monkeypatch):
    import sqlite3

    run = tmp_path / 'run'
    _status_run(run)
    statements, connect = [], sqlite3.connect

    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr('worldmock.status.sqlite3.connect', traced)
    read_status(run)
    assert statements
    assert not [sql for sql in statements if sql.lstrip().upper().startswith('BEGIN')]


#: 第四章之前的用量合计语句（逐条读正文判断类型），用来核对经类型索引的同一行集。
_OBSERVED_USAGE_BY_BODY = """
    SELECT json_extract(body,'$.data.response.model'), count(*),
    count(json_extract(body,'$.data.response.usage.prompt_tokens')),
    sum(json_extract(body,'$.data.response.usage.prompt_tokens')),
    sum(json_extract(body,'$.data.response.usage.completion_tokens')),
    sum(json_extract(body,'$.data.response.usage.total_tokens'))
    FROM events WHERE json_extract(body,'$.event_type')='model_response'
    AND json_extract(body,'$.data.phase')='response'
    GROUP BY json_extract(body,'$.data.response.model')
    ORDER BY json_extract(body,'$.data.response.model')
"""


def test_observed_usage_reads_responses_through_the_type_index(tmp_path):
    import sqlite3
    from worldmock.status import _OBSERVED_USAGE_SQL

    run = tmp_path / 'run'
    _status_run(run)
    connection = sqlite3.connect(run / 'footprints.sqlite')
    try:
        plan = "\n".join(row[-1] for row in connection.execute(f"EXPLAIN QUERY PLAN {_OBSERVED_USAGE_SQL}"))
        assert 'USING COVERING INDEX events_agent_type' in plan
        expected = connection.execute(_OBSERVED_USAGE_BY_BODY).fetchall()
        assert connection.execute(_OBSERVED_USAGE_SQL).fetchall() == expected
    finally:
        connection.close()
    assert [row[0] for row in expected] == ['flash', 'pro'] and expected[1][1] == 1
    assert [(row['model'], row['responses']) for row in observed_model_usage(run)] == [('flash', 2), ('pro', 1)]
