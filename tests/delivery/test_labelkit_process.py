import copy
import hashlib
import json
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from worldmock.delivery.pipeline import deliver, run_labelkit, verify_process
from worldmock.delivery.windows import _action_windows
from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / 'fixtures/labelkit-action.json'


@pytest.mark.parametrize("review_result", [
    "pass", "repaired", "exhausted", "citation_repaired", "missing_citation_repaired",
    "max_items_repaired", "max_items_exhausted",
])
def test_real_labelkit_annotate_and_verify_requests_are_target_blind(tmp_path, monkeypatch, review_result):
    fixture = json.loads(FIXTURE.read_text())
    record = _current_record(tmp_path)
    calls = []
    fixed = copy.deepcopy(fixture['annotation'])
    fixed['target']['claims'][0]['text'] += ' Revision after independent review.'
    critique = {'critiques': [{'aspect': 'wording', 'opinion': 'Clarify evidence scope.'}], 'verdict': 'fail'}
    replies = [fixture['annotation'], {'critiques': [], 'verdict': 'pass'}]
    if review_result == 'repaired':
        replies = [fixture['annotation'], critique, fixed, {'critiques': [], 'verdict': 'pass'}]
    if review_result == 'exhausted':
        replies = [fixture['annotation'], critique, fixed, critique, fixed, critique]
    if review_result == 'citation_repaired':
        invalid = copy.deepcopy(fixture['annotation'])
        invalid['target']['claims'][0]['evidence_event_ids'] = ['forged-event']
        valid = copy.deepcopy(fixture['annotation'])
        valid['target']['claims'][0] = {'text': 'A prior action completed.', 'kind': 'fact',
                                        'evidence_event_ids': ['probe-prior-result']}
        replies = [invalid, valid, {'critiques': [], 'verdict': 'pass'}]
    if review_result == 'missing_citation_repaired':
        invalid = copy.deepcopy(fixture['annotation'])
        invalid['target']['claims'][0]['evidence_event_ids'] = []
        replies = [invalid, fixture['annotation'], {'critiques': [], 'verdict': 'pass'}]
    if review_result in {'max_items_repaired', 'max_items_exhausted'}:
        invalid = copy.deepcopy(fixture['annotation'])
        claim = copy.deepcopy(invalid['target']['claims'][0])
        invalid['target']['claims'] = [copy.deepcopy(claim) for _ in range(33)]
        replies = [invalid, fixture['annotation'], {'critiques': [], 'verdict': 'pass'}]
        if review_result == 'max_items_exhausted':
            replies = [invalid, invalid, invalid]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            content = replies[min(len(calls) - 1, len(replies) - 1)]
            response = {'id': f'fixture-{len(calls)}', 'object': 'chat.completion', 'created': 1,
                        'model': 'deepseek-flash', 'choices': [{'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content': json.dumps(content)}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr('worldmock.delivery.pipeline.dotenv_values', lambda _path: {'DEEPSEEK_API_KEY': 'fixture'})
    try:
        workspace = _workspace(tmp_path, record, server.server_port)
        if review_result in {'exhausted', 'max_items_exhausted'}:
            with pytest.raises(IntegrityError, match='exited with status 1'):
                run_labelkit('action', workspace, ROOT)
            assert (workspace / 'out/action.jsonl').read_text() == ''
            records = []
        else:
            receipt = run_labelkit('action', workspace, ROOT)
            assert receipt['exit_code'] == 0
            records = verify_process('action', workspace, [record])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    call_counts = {'pass': 2, 'repaired': 4, 'exhausted': 6,
                   'citation_repaired': 3, 'missing_citation_repaired': 3,
                   'max_items_repaired': 3, 'max_items_exhausted': 3}
    assert len(calls) == call_counts[review_result]
    target_ids = [record['target']['request']['event_id']]
    target_ids.extend(row['event_id'] for row in record['target']['outcomes'])
    future = target_ids + ['probe-action', 'future-request-canary', 'future-accepted-canary', 'future-result-canary']
    serialized_calls = [json.dumps(call, ensure_ascii=False) for call in calls]
    assert all(marker not in text for marker in future for text in serialized_calls)
    assert all('target_event_ids' not in text and '"outcomes"' not in text for text in serialized_calls)
    cutoff_values = [int(value) for text in serialized_calls
                     for value in re.findall(r'\\"knowledge_cutoff_event_seq\\"\s*:\s*(\d+)', text)]
    assert cutoff_values and set(cutoff_values) == {2}
    assert 'allowed_evidence_event_ids' in json.dumps(calls[0], ensure_ascii=False)
    if records:
        assert records[0]['target']['facts'] == record['target']
        assert records[0]['provenance']['target_event_ids'] == target_ids
        assert records[0]['provenance']['source_event_ids'] == [
            'probe-prior-result', 'probe-observation', *target_ids,
        ]
    trace = [json.loads(line) for line in (workspace / 'out/action.trace.jsonl').read_text().splitlines()]
    profiles = [row['payload']['profile'] for row in trace if row.get('ev') == 'llm.call']
    schema_repairs = {'citation_repaired', 'missing_citation_repaired', 'max_items_repaired'}
    expected_profiles = ['default', 'default', 'judge'] if review_result in schema_repairs else [
        'default', 'judge',
    ] * (len(replies) // 2)
    if review_result == 'max_items_exhausted':
        expected_profiles = ['default', 'default', 'default']
    assert profiles == expected_profiles
    sentinel = record['input']['visible_input_sentinel']
    assert serialized_calls[0].count(sentinel) == 1
    for profile, request in zip(profiles, serialized_calls, strict=True):
        if profile == 'judge':
            assert request.count(sentinel) == 1
    if review_result in {'repaired', 'exhausted', 'max_items_repaired', 'max_items_exhausted'}:
        assert all(request.count(sentinel) == 1 for profile, request in zip(profiles, serialized_calls, strict=True)
                   if profile == 'default')
    if review_result in {'repaired', 'exhausted'}:
        assert fixture['annotation']['target']['claims'][0]['text'] in json.dumps(calls[1], ensure_ascii=False)
        assert 'Clarify evidence scope.' in json.dumps(calls[2], ensure_ascii=False)
        assert fixed['target']['claims'][0]['text'] in json.dumps(calls[3], ensure_ascii=False)
        if records:
            assert records[0]['target']['claims'] == fixed['target']['claims']
    if review_result == 'citation_repaired':
        repair = json.dumps(calls[1], ensure_ascii=False)
        assert 'invalid_evidence_event_ids=[\\"forged-event\\"]' in repair
        allowed = 'allowed_evidence_event_ids=[\\"probe-prior-result\\",\\"probe-observation\\"]'
        assert allowed in repair
        assert 'A prior action completed.' in json.dumps(calls[2], ensure_ascii=False)
        repairs = [row for row in trace if row.get('ev') == 'schema.repair']
        assert repairs[-1]['payload']['resolved_at'] == 'l3_1'
    if review_result == 'missing_citation_repaired':
        repair = json.dumps(calls[1], ensure_ascii=False)
        assert 'Fact or inference has no supporting event' in repair
        allowed = 'allowed_evidence_event_ids=[\\"probe-prior-result\\",\\"probe-observation\\"]'
        assert allowed in repair
        repairs = [row for row in trace if row.get('ev') == 'schema.repair']
        assert repairs[-1]['payload']['resolved_at'] == 'l3_1'
    if review_result in {'max_items_repaired', 'max_items_exhausted'}:
        initial_messages = calls[0]['messages']
        repair_messages = calls[1]['messages']
        assert [message['role'] for message in repair_messages] == ['system', 'user', 'assistant', 'user']
        assert repair_messages[:2] == initial_messages
        assert repair_messages.count(initial_messages[0]) == 1
        assert repair_messages.count(initial_messages[1]) == 1
        assert json.loads(repair_messages[2]['content']) == invalid
        assert sum(message['content'] == repair_messages[2]['content'] for message in repair_messages) == 1
        assert '根据动作发生前的可见历史' in repair_messages[0]['content']
        assert '"maxItems": 32' in repair_messages[0]['content']
        assert '/target/claims: expected at most 32 items, received 33' in repair_messages[3]['content']
        repairs = [row for row in trace if row.get('ev') == 'schema.repair']
        expected = 'l3_1' if review_result == 'max_items_repaired' else 'rejected'
        assert repairs[-1]['payload']['resolved_at'] == expected


def _workspace(tmp_path, record, port):
    workspace = tmp_path / 'labelkit'
    shutil.copytree(ROOT / 'labelkit-projects', workspace, ignore=shutil.ignore_patterns('data', 'out', '__pycache__'))
    (workspace / 'data').mkdir()
    (workspace / 'out').mkdir()
    (workspace / 'data/action.jsonl').write_text(json.dumps(record, ensure_ascii=False) + '\n')
    config = workspace / 'config.toml'
    config.write_text(config.read_text().replace('https://api.deepseek.com', f'http://127.0.0.1:{port}/v1'))
    return workspace


def _event(identity, sequence, kind, data, action_id=None):
    return {'event_id': identity, 'event_seq': sequence, 'event_type': kind, 'occurred_at': 80 + sequence,
            'decision_id': 'probe-decision', 'action_id': action_id, 'activity_id': None, 'agent_id': 1,
            'data': data, 'visibility': 'agent', 'artifact_refs': []}


def _current_record(directory):
    prior_data = {'status': 'completed', 'message': 'Previous activity completed'}
    prior = _event('probe-prior-result', 1, 'action_result', prior_data, 'probe-prior-action')
    prior['decision_id'] = 'probe-prior-decision'
    summary = {'event_id': prior['event_id'], 'action_id': prior['action_id'],
               'decision_id': prior['decision_id'], 'occurred_at': prior['occurred_at'],
               'status': 'completed', 'error_code': None, 'message': prior_data['message'], 'result': prior_data}
    observed = _event('probe-observation', 2, 'observation', {'recent_action_results': [summary]})
    requested = _event('probe-future-request', 3, 'action_requested',
                       {'tool_name': 'start_basic_activity', 'secret': 'future-request-canary'}, 'probe-action')
    accepted = _event('probe-future-accepted', 4, 'action_accepted',
                      {'status': 'submitted', 'secret': 'future-accepted-canary'}, 'probe-action')
    result = _event('probe-future-result', 5, 'action_result',
                    {'status': 'completed', 'secret': 'future-result-canary'}, 'probe-action')
    rows = (prior, observed, requested, accepted, result)
    manifest = {'run_id': 'probe-run', 'source_group_id': 'probe-run', 'cutoff_event_seq': 5,
                'frozen_run_manifest': {'model_identity': {'requested_model': 'fixture'}}}
    record, _ = _action_windows(
        requested, {'probe-action': [requested, accepted, result]},
        {'probe-decision': [observed, requested, accepted, result]},
        (manifest, directory, {row['event_id']: row for row in rows}),
    )
    record['input']['visible_input_sentinel'] = 'visible-input-sentinel-3f57dd'
    return record


def test_failed_independent_semantic_verdict_blocks_process_output(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    source = _current_record(tmp_path)
    record = _output_row(fixture['annotation'], source, verdict='fail')
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out/action.jsonl').write_text(json.dumps(record) + '\n')
    with pytest.raises(IntegrityError, match='semantic verification did not pass'):
        verify_process('action', tmp_path, [source])


def test_failed_semantic_gate_never_publishes_delivery(tmp_path, monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    source = _current_record(tmp_path)
    invoked = []
    monkeypatch.setattr('worldmock.delivery.pipeline.prepare_windows', lambda _path: {'action': [source]})

    def rejected_process(task, workspace, _root):
        invoked.append(task)
        row = _output_row(fixture['annotation'], source, verdict='fail')
        (workspace / f'out/{task}.jsonl').write_text(json.dumps(row) + '\n')
        return {'exit_code': 0}

    monkeypatch.setattr('worldmock.delivery.pipeline.run_labelkit', rejected_process)
    destination = tmp_path / 'delivery'
    with pytest.raises(IntegrityError, match='semantic verification did not pass'):
        deliver(tmp_path / 'source', destination, ROOT)
    assert invoked == ['action']
    assert json.loads((destination / 'failure.json').read_text())['status'] == 'failed'
    assert not (destination / 'manifest.json').exists()
    assert not (destination / 'training.jsonl').exists()


def _output_row(annotation, source, verdict='pass', line_no=1, source_file='action.jsonl'):
    identity = hashlib.sha256(canonical_json(source)).hexdigest()[:16]
    return {
        'target': copy.deepcopy(annotation['target']),
        '_meta': {
            'id': identity,
            'source': {'file': source_file, 'line_no': line_no},
            'verification': {'verdict': verdict},
            'annotation': {'model': 'fixture'},
        },
    }


@pytest.mark.parametrize('mutation, message', [
    ('extra_field', 'only target and _meta'),
    ('wrong_file', 'source file'),
    ('wrong_line', 'source line is outside'),
    ('wrong_id', 'record identity'),
])
def test_process_rejects_candidate_source_mismatch(tmp_path, mutation, message):
    fixture = json.loads(FIXTURE.read_text())
    source = _current_record(tmp_path)
    row = _output_row(fixture['annotation'], source)
    if mutation == 'extra_field':
        row['input'] = source['input']
    elif mutation == 'wrong_file':
        row['_meta']['source']['file'] = 'intent.jsonl'
    elif mutation == 'wrong_line':
        row['_meta']['source']['line_no'] = 2
    else:
        row['_meta']['id'] = '0' * 16
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out/action.jsonl').write_text(json.dumps(row) + '\n')
    with pytest.raises(IntegrityError, match=message):
        verify_process('action', tmp_path, [source])


def test_process_rejects_duplicate_source_line(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    first = _current_record(tmp_path)
    second = copy.deepcopy(first)
    second['provenance']['window_id'] = 'second-window'
    rows = [_output_row(fixture['annotation'], first), _output_row(fixture['annotation'], first)]
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out/action.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises(IntegrityError, match='source line is duplicated'):
        verify_process('action', tmp_path, [first, second])
