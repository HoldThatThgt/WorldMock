import hashlib
import json

import pytest

from worldmock.delivery.windows import prepare_windows
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.validation import validate_export


@pytest.fixture
def complete_export(tmp_path):
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'}, 'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    store = FootprintStore.create(tmp_path / 'run', manifest, {'version': 0, 'current_time': 100})
    observation = EventDraft(event_type='observation', occurred_at=100, agent_id=1, decision_id='decision',
                             data={'energy_kcal': 100})
    request = EventDraft(event_type='action_requested', occurred_at=100, agent_id=1, decision_id='decision',
                         action_id='request', data={'tool_name': 'start_basic_activity', 'request': {'kind': 'wait'}})
    store.commit(store.state, [observation, request])
    store.commit({'version': 1, 'current_time': 200}, [EventDraft(event_type='run_ended', occurred_at=200)])
    reference = store.assets.put_json({'saved': 'complete'}, {'kind': 'fixture'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    directory = tmp_path / 'export'
    export_run(store, point.last_event_seq, directory)
    store.close()
    return directory


def test_production_windows_require_and_accept_complete_authenticated_export(complete_export):
    manifest, events, activities = validate_export(complete_export)
    assert manifest['experiment_completed'] is True
    assert len(events) == 4 and activities == []
    windows = prepare_windows(complete_export)
    assert {task: len(rows) for task, rows in windows.items()} == {'action': 1, 'intent': 1, 'dreaming': 3}


@pytest.mark.parametrize('field', ['version', 'committed', 'completed', 'rows', 'path', 'cutoff', 'window',
                                  'coverage', 'source_runs', 'checkpoint_hash', 'missing_asset', 'event_hash'])
def test_incomplete_or_tampered_export_never_reaches_labelkit(complete_export, field):
    path = complete_export / 'manifest.json'
    manifest = json.loads(path.read_text())
    if field == 'version': manifest['schema_version'] = 999
    if field == 'committed': manifest['committed'] = False
    if field == 'completed': manifest['experiment_completed'] = False
    if field == 'rows': manifest['artifacts']['events']['rows'] = 999
    if field == 'path': manifest['artifacts']['events']['path'] = 'wrong.jsonl'
    if field == 'cutoff': manifest['cutoff_event_seq'] = 999
    if field == 'window': manifest['window']['end'] = '2026-03-30T00:00:00+00:00'
    if field == 'coverage': manifest['daily_coverage'] = []
    if field == 'source_runs': manifest['source_run_ids'] = ['invented']
    if field == 'checkpoint_hash': manifest['checkpoint']['record_sha256'] = '0' * 64
    if field == 'missing_asset': next((complete_export / 'assets').iterdir()).unlink()
    if field == 'event_hash': (complete_export / 'events.jsonl').write_text('{}\n')
    path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError): prepare_windows(complete_export)


@pytest.mark.parametrize('rehash', [False, True])
def test_quality_report_requires_bytes_and_actual_event_recomputation(complete_export, rehash):
    path = complete_export / 'quality.json'
    quality = json.loads(path.read_text())
    quality['actions']['unique_action_ids'] = 999
    path.write_text(json.dumps(quality))
    if rehash:
        manifest_path = complete_export / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['quality_report'].update(size_bytes=path.stat().st_size,
                                           sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError): validate_export(complete_export)
