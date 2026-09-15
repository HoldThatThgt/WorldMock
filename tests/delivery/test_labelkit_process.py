import copy
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from worldmock.delivery.pipeline import deliver, run_labelkit, verify_process
from worldmock.delivery.semantics import complete_annotation
from worldmock.errors import IntegrityError


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / 'fixtures/labelkit-action.json'


@pytest.mark.parametrize("review_result", ["pass", "repaired", "exhausted"])
def test_real_labelkit_annotate_and_verify_requests_are_target_blind(tmp_path, monkeypatch, review_result):
    needs_repair = review_result != "pass"
    fixture = json.loads(FIXTURE.read_text())
    calls = []
    fixed = copy.deepcopy(fixture['annotation'])
    fixed['target']['claims'][0]['text'] += ' Revision after independent review.'
    critique = {'critiques': [{'aspect': 'wording', 'opinion': 'Clarify evidence scope.'}], 'verdict': 'fail'}
    replies = ([fixture['annotation'], critique, fixed, {'critiques': [], 'verdict': 'pass'}]
               if needs_repair else [fixture['annotation'], {'critiques': [], 'verdict': 'pass'}])
    if review_result == 'exhausted':
        replies = [fixture['annotation'], critique, fixed, critique, fixed, critique]

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
        workspace = _workspace(tmp_path, fixture['input'], server.server_port)
        if review_result == 'exhausted':
            with pytest.raises(IntegrityError, match='exited with status 1'):
                run_labelkit('action', workspace, ROOT)
            assert (workspace / 'out/action.jsonl').read_text() == ''
            records = []
        else:
            receipt = run_labelkit('action', workspace, ROOT)
            assert receipt['exit_code'] == 0
            records = verify_process('action', workspace, [fixture['input']])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    assert len(calls) == {'pass': 2, 'repaired': 4, 'exhausted': 6}[review_result]
    canary = fixture['input']['target']['future_canary']
    assert all(canary not in json.dumps(call, ensure_ascii=False) for call in calls)
    assert fixture['annotation']['target']['claims'][0]['text'] in json.dumps(calls[1], ensure_ascii=False)
    if records:
        assert records[0]['target']['facts'] == fixture['input']['target']
    trace = [json.loads(line) for line in (workspace / 'out/action.trace.jsonl').read_text().splitlines()]
    profiles = [row['payload']['profile'] for row in trace if row.get('ev') == 'llm.call']
    assert profiles == ['default', 'judge'] * (len(replies) // 2)
    if needs_repair:
        assert 'Clarify evidence scope.' in json.dumps(calls[2], ensure_ascii=False)
        assert fixed['target']['claims'][0]['text'] in json.dumps(calls[3], ensure_ascii=False)
        if records:
            assert records[0]['target']['claims'] == fixed['target']['claims']


def _workspace(tmp_path, record, port):
    workspace = tmp_path / 'labelkit'
    shutil.copytree(ROOT / 'labelkit-projects', workspace, ignore=shutil.ignore_patterns('data', 'out', '__pycache__'))
    (workspace / 'data').mkdir()
    (workspace / 'out').mkdir()
    (workspace / 'data/action.jsonl').write_text(json.dumps(record, ensure_ascii=False) + '\n')
    config = workspace / 'config.toml'
    config.write_text(config.read_text().replace('https://api.deepseek.com', f'http://127.0.0.1:{port}/v1'))
    return workspace


def test_failed_independent_semantic_verdict_blocks_process_output(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    record = complete_annotation(copy.deepcopy(fixture['annotation']), fixture['input'])
    record['_meta'] = {'id': 'failed-review', 'verification': {'verdict': 'fail'},
                       'annotation': {'model': 'fixture'}}
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out/action.jsonl').write_text(json.dumps(record) + '\n')
    with pytest.raises(IntegrityError, match='semantic verification did not pass'):
        verify_process('action', tmp_path, [fixture['input']])


def test_failed_semantic_gate_never_publishes_delivery(tmp_path, monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    invoked = []
    monkeypatch.setattr('worldmock.delivery.pipeline.prepare_windows', lambda _path: {'action': [fixture['input']]})

    def rejected_process(task, workspace, _root):
        invoked.append(task)
        row = complete_annotation(copy.deepcopy(fixture['annotation']), fixture['input'])
        row['_meta'] = {'id': 'failed-review', 'verification': {'verdict': 'fail'},
                        'annotation': {'model': 'fixture'}}
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
