import hashlib
import json
from pathlib import Path

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore


def test_export_uses_cutoff_and_excludes_pause_from_duration(tmp_path):
    store = FootprintStore.create(tmp_path / 'run', {
        'run_id': 'run', 'agent_ids': [1], 'window': {'start': 100, 'end': 300, 'timezone': 'UTC'}
    }, {'version': 0, 'current_time': 100})
    activity = {'activity_id': 'work', 'agent_id': 1, 'kind': 'work', 'started_at': 100,
                'elapsed_seconds': 10, 'ended_at': None, 'status': 'active'}
    store.commit({'version': 1, 'current_time': 110}, [EventDraft(
        event_type='activity_progress', occurred_at=110, activity_id='work',
        data={'activity': activity, 'interval_start': 100, 'interval_end': 110})])
    activity = {**activity, 'elapsed_seconds': 20, 'ended_at': 200, 'status': 'completed'}
    store.commit({'version': 2, 'current_time': 200}, [EventDraft(
        event_type='activity_progress', occurred_at=200, activity_id='work',
        data={'activity': activity, 'interval_start': 190, 'interval_end': 200})])
    store.commit({'version': 3, 'current_time': 300}, [EventDraft(
        event_type='run_ended', occurred_at=300)])
    manifest = export_run(store, 2, tmp_path / 'export')
    rows = [json.loads(line) for line in (tmp_path / 'export' / 'activities.jsonl').read_text().splitlines()]
    assert rows[0]['segments'] == [{'started_at': 100, 'ended_at': 110}, {'started_at': 190, 'ended_at': 200}]
    assert rows[0]['elapsed_seconds'] == 20
    assert manifest['experiment_completed'] is False
    assert manifest['cutoff_event_seq'] == 2
    assert manifest['artifacts']['events']['rows'] == 2
    events = [json.loads(line) for line in (tmp_path / 'export' / 'events.jsonl').read_text().splitlines()]
    assert [event['occurred_at'] for event in events] == [110, 200]
    store.close()


def test_clock_end_without_successful_final_checkpoint_is_not_complete(tmp_path):
    store = FootprintStore.create(tmp_path / 'run', {
        'run_id': 'run', 'agent_ids': [1], 'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}
    }, {'version': 0, 'current_time': 100})
    store.commit({'version': 1, 'current_time': 200}, [EventDraft(event_type='run_ended', occurred_at=200)])
    assert export_run(store, 1, tmp_path / 'unsaved')['experiment_completed'] is False
    ref = store.assets.put_json({'snapshot': 'saved'}, {'kind': 'fixture'})
    point = store.checkpoint(store.state, {'agent:1': ref, 'life': ref})
    assert export_run(store, point.last_event_seq, tmp_path / 'saved')['experiment_completed'] is True
    (store.assets.root / ref.sha256).unlink()
    with pytest.raises(IntegrityError):
        export_run(store, point.last_event_seq, tmp_path / 'damaged')
    assert not (tmp_path / 'damaged').exists()
    store.close()


def test_export_corrupt_asset_never_publishes_manifest(tmp_path):
    store = FootprintStore.create(tmp_path / 'run', {
        'run_id': 'run', 'agent_ids': [1], 'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}
    }, {'version': 0, 'current_time': 100})
    ref = store.assets.put_json({'actual': 'content'}, {'kind': 'fixture'})
    store.commit({'version': 1, 'current_time': 200}, [EventDraft(
        event_type='observation', occurred_at=200, artifact_refs=[ref])])
    (store.assets.root / ref.sha256).write_bytes(b'broken')
    with pytest.raises(IntegrityError):
        export_run(store, 1, tmp_path / 'export')
    assert not (tmp_path / 'export').exists()
    store.close()


def test_event_after_final_checkpoint_invalidates_completion(tmp_path):
    store = FootprintStore.create(tmp_path / 'run', {
        'run_id': 'run', 'agent_ids': [1], 'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}
    }, {'version': 0, 'current_time': 100})
    store.commit({'version': 1, 'current_time': 200}, [EventDraft(event_type='run_ended', occurred_at=200)])
    ref = store.assets.put_json({'snapshot': 'saved'}, {'kind': 'fixture'})
    store.checkpoint(store.state, {'agent:1': ref, 'life': ref})
    store.commit(store.state, [EventDraft(event_type='observation', occurred_at=200)])
    assert export_run(store, store.last_event_seq, tmp_path / 'export')['experiment_completed'] is False
    store.close()


def test_checkpoint_requires_every_participant(tmp_path):
    store = FootprintStore.create(tmp_path / 'run', {'run_id': 'run', 'agent_ids': [1, 2]},
                                  {'version': 0, 'current_time': 100})
    ref = store.assets.put_json({'actual': 'snapshot'}, {'kind': 'fixture'})
    with pytest.raises(IntegrityError):
        store.checkpoint(store.state, {'agent:1': ref, 'life': ref})
    assert store.last_event_seq == 0
    assert store.connection.execute('SELECT count(*) FROM checkpoints').fetchone()[0] == 0
    store.close()


def test_export_writes_hash_bound_timeline_projections(tmp_path):
    """导出生成 timeline/<agent>-<date>.md 投影并列入清单（哈希绑定）。"""
    store = FootprintStore.create(tmp_path / 'run', {
        'run_id': 'run', 'agent_ids': [1], 'window': {'start': 100, 'end': 300, 'timezone': 'UTC'}
    }, {'version': 0, 'current_time': 100})
    activity = {'activity_id': 'work', 'agent_id': 1, 'kind': 'work', 'started_at': 100,
                'elapsed_seconds': 10, 'ended_at': None, 'status': 'active'}
    store.commit({'version': 1, 'current_time': 110}, [EventDraft(
        event_type='activity_progress', occurred_at=110, agent_id=1, activity_id='work',
        data={'activity': activity, 'interval_start': 100, 'interval_end': 110})])
    manifest = export_run(store, 1, tmp_path / 'export')

    records = manifest['timeline']
    assert [item['path'] for item in records] == ['timeline/1-1970-01-01.md']
    record = records[0]
    assert record['agent_id'] == 1 and record['date'] == '1970-01-01'
    payload = (tmp_path / 'export' / record['path']).read_bytes()
    assert record['size_bytes'] == len(payload)
    assert record['sha256'] == hashlib.sha256(payload).hexdigest()
    assert '工作' in payload.decode('utf-8')

    tampered = b'# forged timeline\n'
    (tmp_path / 'export' / record['path']).write_bytes(tampered)
    assert hashlib.sha256(tampered).hexdigest() != record['sha256']
    store.close()


def test_export_timeline_projection_matches_render_day_output(tmp_path):
    """timeline 投影与 scripts/render_day.py 的渲染函数逐字节一致。"""
    import importlib.util

    store = FootprintStore.create(tmp_path / 'run', {
        'run_id': 'run', 'agent_ids': [1], 'window': {'start': 100, 'end': 300, 'timezone': 'UTC'}
    }, {'version': 0, 'current_time': 100})
    activity = {'activity_id': 'wait', 'agent_id': 1, 'kind': 'wait', 'started_at': 100,
                'elapsed_seconds': 10, 'ended_at': None, 'status': 'active'}
    store.commit({'version': 1, 'current_time': 110}, [EventDraft(
        event_type='activity_progress', occurred_at=110, agent_id=1, activity_id='wait',
        data={'activity': activity, 'interval_start': 100, 'interval_end': 110})])
    export_run(store, 1, tmp_path / 'export')

    spec = importlib.util.spec_from_file_location(
        'render_day_export_check', Path(__file__).resolve().parents[2] / 'scripts' / 'render_day.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from datetime import date
    expected = module.render_day_markdown(store.connection, 1, date(1970, 1, 1))
    actual = (tmp_path / 'export' / 'timeline' / '1-1970-01-01.md').read_text(encoding='utf-8')
    assert actual == expected
    assert '空档' in actual
    store.close()
