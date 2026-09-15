from worldmock.footprints.store import FootprintStore
from worldmock.status import read_status, write_status


def test_status_reads_confirmed_state_without_modifying_any_run_file(tmp_path):
    run = tmp_path / 'run'
    store = FootprintStore.create(run, {'run_id': 'run', 'agent_ids': [1], 'window': {'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'end_time': 200, 'activities': {},
                                   'agents': {'1': {'agent_id': 1, 'location_id': 'home', 'current_activity_id': None,
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
    assert result['wall_time']['elapsed_seconds'] is None


def test_status_preserves_earlier_body_decision_time_instead_of_activity_end(tmp_path):
    run = tmp_path / 'run'
    store = FootprintStore.create(run, {'run_id': 'run', 'agent_ids': [1], 'window': {'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'end_time': 2000,
                                   'activities': {'watch': {'status': 'active', 'planned_end_at': 1900,
                                                            'next_interaction_at': 1000}},
                                   'agents': {'1': {'agent_id': 1, 'location_id': 'home',
                                                    'current_activity_id': 'watch', 'next_decision_at': 143}}})
    store.close()
    before = (run / 'footprints.sqlite').read_bytes()
    assert read_status(run)['people'][0]['next_decision_at'] == 143
    assert (run / 'footprints.sqlite').read_bytes() == before
