import copy
import hashlib
import json
import re
import shutil
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from worldmock.delivery.evidence_encoding import POINTER_RULE, encode_model_record
from worldmock.delivery.pipeline import (
    _bind_source_trace,
    _model_source,
    deliver,
    run_labelkit,
    verify_process,
    write_model_inputs,
)
from worldmock.delivery.windows import _action_windows, _window_identity
from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / 'fixtures/labelkit-action.json'


@pytest.mark.parametrize("review_result", [
    "pass", "repaired", "exhausted", "citation_repaired", "missing_citation_repaired",
    "max_items_repaired", "max_items_exhausted", "history_repaired", "history_exhausted",
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
    history_first = copy.deepcopy(fixture['annotation'])
    history_second = copy.deepcopy(fixture['annotation'])
    first_history = {'critiques': [
        {'aspect': 'aggregation', 'opinion': 'first-history-critique-sentinel'},
    ], 'verdict': 'fail'}
    second_history = {'critiques': [
        {'aspect': 'citation', 'opinion': 'second-history-critique-sentinel'},
    ], 'verdict': 'fail'}
    if review_result in {'history_repaired', 'history_exhausted'}:
        history_first['target']['claims'][0]['text'] = 'first-repair-candidate-sentinel'
        history_second['target']['claims'][0]['text'] = 'second-repair-candidate-sentinel'
        final_verdict = {'critiques': [], 'verdict': 'pass'}
        if review_result == 'history_exhausted':
            final_verdict = {'critiques': [
                {'aspect': 'kind', 'opinion': 'final-history-critique-sentinel'},
            ], 'verdict': 'fail'}
        replies = [
            fixture['annotation'], first_history, history_first,
            second_history, history_second, final_verdict,
        ]

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
        if review_result in {'exhausted', 'max_items_exhausted', 'history_exhausted'}:
            with pytest.raises(IntegrityError, match='exited with status 1'):
                run_labelkit('action', workspace, ROOT, [record])
            assert (workspace / 'out/action.jsonl').read_text() == ''
            records = []
        else:
            receipt = run_labelkit('action', workspace, ROOT, [record])
            assert receipt['exit_code'] == 0
            records = verify_process('action', workspace, [record])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    call_counts = {'pass': 2, 'repaired': 4, 'exhausted': 6,
                   'citation_repaired': 3, 'missing_citation_repaired': 3,
                   'max_items_repaired': 3, 'max_items_exhausted': 3,
                   'history_repaired': 6, 'history_exhausted': 6}
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
    assert all(text.count(POINTER_RULE) == 1 for text in serialized_calls)
    assert all('observation_value_encoding' in text for text in serialized_calls)
    assert all('observation_value_dictionary' in text for text in serialized_calls)
    if records:
        assert records[0]['input'] == record['input']
        assert 'observation_value_encoding' not in records[0]['input']
        assert records[0]['target']['facts'] == record['target']
        assert records[0]['provenance']['target_event_ids'] == target_ids
        assert records[0]['provenance']['source_event_ids'] == [
            'probe-prior-result', 'probe-observation', *target_ids,
        ]
        model_input = records[0]['provenance']['model_input']
        assert model_input['labelkit_record_id'] == records[0]['provenance']['labelkit_record_id']
        assert model_input['source_line'] == 1
        assert model_input['trace_path'] == 'labelkit/out/action.trace.jsonl'
    trace = [json.loads(line) for line in (workspace / 'out/action.trace.jsonl').read_text().splitlines()]
    profiles = [row['payload']['profile'] for row in trace if row.get('ev') == 'llm.call']
    schema_repairs = {'citation_repaired', 'missing_citation_repaired', 'max_items_repaired'}
    expected_profiles = ['default', 'default', 'judge'] if review_result in schema_repairs else [
        'default', 'judge',
    ] * (len(replies) // 2)
    if review_result == 'max_items_exhausted':
        expected_profiles = ['default', 'default', 'default']
    assert profiles == expected_profiles
    _assert_model_wire_contract(profiles, calls)
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
    if review_result in {'history_repaired', 'history_exhausted'}:
        _assert_cumulative_history_requests(calls, fixture['annotation'])
        if records:
            assert records[0]['target']['claims'] == history_second['target']['claims']
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


def _assert_model_wire_contract(profiles, calls):
    """确认生成与独立评审配置实际进入 LabelKit HTTP 请求。"""
    for profile, request in zip(profiles, calls, strict=True):
        messages = json.dumps(request['messages'], ensure_ascii=False)
        common = '事实中的每个独立断言都须由该 claim 所列来源直接支持'
        assert messages.count(common) == 1
        assert messages.count('不能借用未列出的 input.events 补证') == 1
        assert messages.count('命名布尔字段的 false 只否定该字段表达的命题') == 1
        assert messages.count('没有历史进度不能由当前目录推出“从未开始”') == 1
        assert messages.count('数值接近零本身不等同于高低或需求等级分类') == 1
        assert messages.count('按 RFC 6901 从当前 input 单跳取得完整字段值') == 1
        assert messages.count('证据仅限 input.events 与 input.prerequisite_state 中') == 1
        assert messages.count('聚合用于消除同一主题的重复，不要求合并不同因果主题') == 1
        assert messages.count('三十二条是上限而非目标数量') == 1
        assert messages.count('必须指出共享主体、关系与尚未概括的重复内容') == 1
        if profile == 'judge':
            assert request['thinking'] == {'type': 'enabled'}
            assert request['max_tokens'] == 32768
            assert '每轮独立完整审查当前候选' in messages
            assert '一次列出发现的全部实质问题' in messages
            assert '身份字段是不透明标识' in messages
            continue
        assert request['thinking'] == {'type': 'disabled'}
        assert request['max_tokens'] == 131072
        assert '摘要用共同阶段或同一活动的概括保留重复发生及有意义的差异' in messages
        assert '不为完整保存原始材料穷举 ID' in messages


def _assert_cumulative_history_requests(calls, initial):
    """确认返工累计历史要求，同时保持当前候选与独立复评边界。"""
    serialized = [json.dumps(call, ensure_ascii=False) for call in calls]
    first_critique = 'first-history-critique-sentinel'
    second_critique = 'second-history-critique-sentinel'
    initial_text = initial['target']['claims'][0]['text']

    assert serialized[2].count(first_critique) == 1
    assert second_critique not in serialized[2]
    assert serialized[4].count(first_critique) == 1
    assert serialized[4].count(second_critique) == 1
    assert serialized[2].count(initial_text) == 1
    assert serialized[4].count('first-repair-candidate-sentinel') == 1
    assert initial_text not in serialized[4]
    assert 'second-repair-candidate-sentinel' not in serialized[4]
    assert serialized[1].count(initial_text) == 1
    assert serialized[3].count('first-repair-candidate-sentinel') == 1
    assert serialized[5].count('second-repair-candidate-sentinel') == 1
    assert all(first_critique not in serialized[index] for index in (1, 3, 5))
    assert all(second_critique not in serialized[index] for index in (1, 3, 5))
    assert '逐项重新核验全部审核意见' in serialized[4]
    assert '不得使旧缺陷复现' in serialized[4]


def _workspace(tmp_path, record, port):
    workspace = tmp_path / 'labelkit'
    shutil.copytree(ROOT / 'labelkit-projects', workspace, ignore=shutil.ignore_patterns('data', 'out', '__pycache__'))
    (workspace / 'data').mkdir()
    (workspace / 'out').mkdir()
    write_model_inputs('action', [record], workspace)
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
    record['provenance']['window_id'] = _window_identity(record['task'], record['input'])
    return record


def test_failed_independent_semantic_verdict_blocks_process_output(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    source = _current_record(tmp_path)
    record = _output_row(fixture['annotation'], source, verdict='fail')
    _prepare_verified_sources(tmp_path, [source])
    (tmp_path / 'out/action.jsonl').write_text(json.dumps(record) + '\n')
    with pytest.raises(IntegrityError, match='semantic verification did not pass'):
        verify_process('action', tmp_path, [source])


def test_failed_semantic_gate_never_publishes_delivery(tmp_path, monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    source = _current_record(tmp_path)
    invoked = []
    monkeypatch.setattr('worldmock.delivery.pipeline.prepare_windows', lambda _path: {'action': [source]})

    def rejected_process(task, workspace, _root, inputs):
        invoked.append(task)
        assert inputs == [source]
        row = _output_row(fixture['annotation'], source, verdict='fail')
        (workspace / f'out/{task}.jsonl').write_text(json.dumps(row) + '\n')
        _write_successful_trace(task, workspace, inputs)
        _bind_source_trace(task, workspace)
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
    encoded = encode_model_record(source)
    identity = hashlib.sha256(canonical_json(encoded)).hexdigest()[:16]
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
    _prepare_verified_sources(tmp_path, [source])
    (tmp_path / 'out/action.jsonl').write_text(json.dumps(row) + '\n')
    with pytest.raises(IntegrityError, match=message):
        verify_process('action', tmp_path, [source])


def test_process_rejects_duplicate_source_line(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    first = _current_record(tmp_path)
    second = copy.deepcopy(first)
    second['input']['boundary_type'] = 'second-decision'
    second['provenance']['window_id'] = _window_identity(second['task'], second['input'])
    rows = [_output_row(fixture['annotation'], first), _output_row(fixture['annotation'], first)]
    _prepare_verified_sources(tmp_path, [first, second])
    (tmp_path / 'out/action.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises(IntegrityError, match='source line is duplicated'):
        verify_process('action', tmp_path, [first, second])


@pytest.mark.parametrize('target', ['model_input', 'source_index'])
def test_process_rejects_model_source_tampering_before_cli(tmp_path, monkeypatch, target):
    source = _current_record(tmp_path)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'out').mkdir()
    write_model_inputs('action', [source], tmp_path)
    path = tmp_path / f'data/action.{"source-index." if target == "source_index" else ""}jsonl'
    row = json.loads(path.read_text())
    if target == 'model_input':
        row['input']['boundary_type'] = 'tampered'
    else:
        row['model_input_sha256'] = '0' * 64
    path.write_bytes(canonical_json(row) + b'\n')
    monkeypatch.setattr('worldmock.delivery.pipeline.subprocess.run',
                        lambda *_args, **_kwargs: pytest.fail('Tampered source invoked LabelKit'))

    with pytest.raises(IntegrityError):
        run_labelkit('action', tmp_path, ROOT, [source])


def test_process_rejects_trace_changed_after_source_binding(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    source = _current_record(tmp_path)
    _prepare_verified_sources(tmp_path, [source])
    row = _output_row(fixture['annotation'], source)
    (tmp_path / 'out/action.jsonl').write_text(json.dumps(row) + '\n')
    (tmp_path / 'out/action.trace.jsonl').write_text('changed-after-binding\n')

    with pytest.raises(IntegrityError, match='Cannot parse LabelKit trace'):
        verify_process('action', tmp_path, [source])


@pytest.mark.parametrize('mutation, message', [
    ('not_json', 'Cannot parse LabelKit trace'),
    ('mixed_run', 'one valid run_id'),
    ('missing_end', 'one run.end'),
    ('failed_end', 'successful run end'),
    ('missing_verdict', 'final passing verdict'),
    ('failed_verdict', 'final passing verdict'),
    ('unknown_verdict', 'invalid source record'),
    ('missing_judge', 'lacks generation or review'),
    ('unmapped_call', 'model call is not bound'),
    ('missing_repair_cycle', 'lacks generation or review before a verdict'),
    ('missing_output', 'unsuccessful model call'),
    ('wrong_model', 'unsuccessful model call'),
    ('nonzero_retry', 'unsuccessful model call'),
    ('wrong_config', 'run identity'),
    ('wrong_counts', 'counts do not preserve'),
])
def test_trace_binding_requires_successful_lifecycle_and_final_verdicts(tmp_path, mutation, message):
    source = _current_record(tmp_path)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'out').mkdir()
    write_model_inputs('action', [source], tmp_path)
    _ensure_trace_files('action', tmp_path)
    rows = _successful_trace_rows('action', tmp_path, [source])
    if mutation == 'not_json':
        (tmp_path / 'out/action.trace.jsonl').write_text('not-json\n')
    else:
        if mutation == 'mixed_run':
            rows[1]['run_id'] = 'b' * 12
        elif mutation == 'missing_end':
            rows.pop()
        elif mutation == 'failed_end':
            rows[-1]['payload']['exit_code'] = 1
        elif mutation == 'missing_verdict':
            rows = [row for row in rows if row['ev'] != 'verify.verdict']
        elif mutation == 'failed_verdict':
            next(row for row in rows if row['ev'] == 'verify.verdict')['payload']['verdict'] = 'fail'
        elif mutation == 'unknown_verdict':
            next(row for row in rows if row['ev'] == 'verify.verdict')['record_ids'] = ['0' * 16]
        elif mutation == 'missing_judge':
            rows = [row for row in rows if row['payload'].get('profile') != 'judge']
        elif mutation == 'unmapped_call':
            call = next(row for row in rows if row['ev'] == 'llm.call')
            call['payload']['gen_ai.input.messages'][1]['content'][0]['text'] = '[待标注数据] {}'
        elif mutation == 'missing_repair_cycle':
            verdict = next(row for row in rows if row['ev'] == 'verify.verdict')
            verdict['payload']['verdict'] = 'fail'
            final = copy.deepcopy(verdict)
            final['payload'] = {'round': 2, 'verdict': 'pass'}
            rows.insert(-1, final)
        elif mutation == 'missing_output':
            next(row for row in rows if row['ev'] == 'llm.call')['payload'].pop('gen_ai.output.messages')
        elif mutation == 'wrong_model':
            next(row for row in rows if row['ev'] == 'llm.call')['payload']['gen_ai.request.model'] = 'other'
        elif mutation == 'nonzero_retry':
            next(row for row in rows if row['ev'] == 'llm.call')['payload']['retries'] = 1
        elif mutation == 'wrong_config':
            rows[0]['payload']['config_digest'] = 'sha256:' + '0' * 64
        else:
            rows[-1]['payload']['counts']['emitted'] = 0
        _write_trace(tmp_path / 'out/action.trace.jsonl', rows)
    with pytest.raises(IntegrityError, match=message):
        _bind_source_trace('action', tmp_path)
    source_index = json.loads((tmp_path / 'data/action.source-index.jsonl').read_text())
    assert 'trace_sha256' not in source_index


def test_trace_rejects_model_calls_after_the_final_passing_verdict(tmp_path):
    source = _current_record(tmp_path)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'out').mkdir()
    write_model_inputs('action', [source], tmp_path)
    _ensure_trace_files('action', tmp_path)
    rows = _successful_trace_rows('action', tmp_path, [source])
    verdict = next(row for row in rows if row['ev'] == 'verify.verdict')
    replayed_calls = [copy.deepcopy(row) for row in rows if row['ev'] == 'llm.call']
    second_round = _trace_event(verdict['run_id'], 'verify', 'verify.verdict', verdict['record_ids'],
                                {'round': 2, 'verdict': 'pass'})
    rows[-1:-1] = [*replayed_calls, second_round]
    _write_trace(tmp_path / 'out/action.trace.jsonl', rows)

    with pytest.raises(IntegrityError, match='after a passing verdict'):
        _bind_source_trace('action', tmp_path)
    source_index = json.loads((tmp_path / 'data/action.source-index.jsonl').read_text())
    assert 'trace_sha256' not in source_index


def test_trace_accepts_judge_schema_repair_between_generation_and_verdict(tmp_path):
    source = _current_record(tmp_path)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'out').mkdir()
    write_model_inputs('action', [source], tmp_path)
    _ensure_trace_files('action', tmp_path)
    rows = _successful_trace_rows('action', tmp_path, [source])
    config = tomllib.loads((tmp_path / 'config.toml').read_text())
    verdict_index = next(index for index, row in enumerate(rows) if row['ev'] == 'verify.verdict')
    identity = rows[verdict_index]['record_ids'][0]
    repair = _schema_repair_call('a' * 12, 'judge', config['llm']['judge']['model'],
                                 '{"critiques": [], "verdict": "fail"}')
    schema_repair = _trace_event('a' * 12, 'schema', 'schema.repair', [identity],
                                 {'resolved_at': 'l3_1', 'violations': [': required']})
    rows[verdict_index:verdict_index] = [repair, schema_repair]
    _write_trace(tmp_path / 'out/action.trace.jsonl', rows)

    digest = _bind_source_trace('action', tmp_path)
    source_index = json.loads((tmp_path / 'data/action.source-index.jsonl').read_text())
    assert source_index['trace_sha256'] == digest


@pytest.mark.parametrize('mutation, message', [
    ('wrong_model', 'unsuccessful model call'),
    ('nonzero_retry', 'unsuccessful model call'),
    ('after_final_verdict', 'schema repair call after its final verdict'),
    ('invalid_payload', 'source input is invalid JSON'),
    ('non_object_payload', 'source input must be a JSON object'),
])
def test_trace_rejects_broken_judge_schema_repair_calls(tmp_path, mutation, message):
    source = _current_record(tmp_path)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'out').mkdir()
    write_model_inputs('action', [source], tmp_path)
    _ensure_trace_files('action', tmp_path)
    rows = _successful_trace_rows('action', tmp_path, [source])
    config = tomllib.loads((tmp_path / 'config.toml').read_text())
    payload = '{"critiques": [], "verdict": "fail"}'
    if mutation == 'invalid_payload':
        payload = 'not-json'
    elif mutation == 'non_object_payload':
        payload = '[1, 2]'
    repair = _schema_repair_call('a' * 12, 'judge', config['llm']['judge']['model'], payload)
    if mutation == 'wrong_model':
        repair['payload']['gen_ai.request.model'] = 'other-model'
    elif mutation == 'nonzero_retry':
        repair['payload']['retries'] = 1
    verdict_index = next(index for index, row in enumerate(rows) if row['ev'] == 'verify.verdict')
    if mutation == 'after_final_verdict':
        rows[verdict_index + 1:verdict_index + 1] = [repair]
    else:
        rows[verdict_index:verdict_index] = [repair]
    _write_trace(tmp_path / 'out/action.trace.jsonl', rows)

    with pytest.raises(IntegrityError, match=message):
        _bind_source_trace('action', tmp_path)
    source_index = json.loads((tmp_path / 'data/action.source-index.jsonl').read_text())
    assert 'trace_sha256' not in source_index


def test_process_rejects_consistently_rewritten_model_source_before_cli(tmp_path, monkeypatch):
    source = _current_record(tmp_path)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'out').mkdir()
    write_model_inputs('action', [source], tmp_path)
    rewritten = copy.deepcopy(source)
    rewritten['input']['boundary_type'] = 'tampered-decision-boundary'
    rewritten['provenance']['window_id'] = _window_identity(rewritten['task'], rewritten['input'])
    encoded = encode_model_record(rewritten)
    index = _model_source('action', 1, encoded)
    (tmp_path / 'data/action.jsonl').write_bytes(canonical_json(encoded) + b'\n')
    (tmp_path / 'data/action.source-index.jsonl').write_bytes(canonical_json(index) + b'\n')
    monkeypatch.setattr('worldmock.delivery.pipeline.subprocess.run',
                        lambda *_args, **_kwargs: pytest.fail('Rewritten model source invoked LabelKit'))

    with pytest.raises(IntegrityError, match='Decoded model input differs'):
        run_labelkit('action', tmp_path, ROOT, [source])


def test_process_rejects_another_valid_trace_after_source_binding(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    source = _current_record(tmp_path)
    _prepare_verified_sources(tmp_path, [source])
    row = _output_row(fixture['annotation'], source)
    (tmp_path / 'out/action.jsonl').write_text(json.dumps(row) + '\n')
    rows = _successful_trace_rows('action', tmp_path, [source])
    for item in rows:
        item['run_id'] = 'b' * 12
    _write_trace(tmp_path / 'out/action.trace.jsonl', rows)

    with pytest.raises(IntegrityError, match='source index disagrees with its encoded line or trace'):
        verify_process('action', tmp_path, [source])


def _prepare_verified_sources(workspace, inputs):
    """为纯验收测试写入真实编码行、来源索引和 trace 绑定。"""
    (workspace / 'data').mkdir(exist_ok=True)
    (workspace / 'out').mkdir(exist_ok=True)
    write_model_inputs('action', inputs, workspace)
    _write_successful_trace('action', workspace, inputs)
    _bind_source_trace('action', workspace)


def _write_successful_trace(task, workspace, inputs):
    """写入符合真实 LabelKit 生命周期结构的最小成功 trace。"""
    _ensure_trace_files(task, workspace)
    _write_trace(workspace / f'out/{task}.trace.jsonl', _successful_trace_rows(task, workspace, inputs))


def _successful_trace_rows(task, workspace, inputs):
    """按实际编码行身份构造成功运行、逐记录裁决与终局计数。"""
    run_id = 'a' * 12
    config_raw = (workspace / 'config.toml').read_bytes()
    project_raw = (workspace / f'{task}.toml').read_bytes()
    config = tomllib.loads(config_raw.decode())
    start = {'tool_version': 'labelkit/1.0.0', 'trace_schema_version': 1,
             'config_digest': 'sha256:' + hashlib.sha256(config_raw).hexdigest(),
             'project_digest': 'sha256:' + hashlib.sha256(project_raw).hexdigest()}
    rows = [_trace_event(run_id, 'run', 'run.start', [], start)]
    for source in inputs:
        encoded = encode_model_record(source)
        identity = hashlib.sha256(canonical_json(encoded)).hexdigest()[:16]
        model_input = canonical_json(encoded['input']).decode()
        rows.append(_model_call(run_id, 'default', config['llm']['default']['model'],
                                f'[待标注数据] {model_input}'))
        rows.append(_model_call(run_id, 'judge', config['llm']['judge']['model'],
                                f'[原始数据] {model_input}\n[标注结果] {{}}'))
        rows.append(_trace_event(run_id, 'verify', 'verify.verdict', [identity],
                                 {'round': 1, 'verdict': 'pass'}))
    count = len(inputs)
    counts = {'scanned': count, 'ingested': count, 'bad_input': 0, 'dropped_dup': 0,
              'dropped_lowq': 0, 'dropped_verify': 0, 'failed': 0, 'generated': 0, 'emitted': count}
    rows.append(_trace_event(run_id, 'run', 'run.end', [], {'counts': counts, 'exit_code': 0}))
    return rows


def _trace_event(run_id, stage, event, record_ids, payload):
    """构造与 LabelKit EventLog 相同字段的测试事件。"""
    return {'ts': '2026-09-16T00:00:00.000+08:00', 'run_id': run_id, 'batch_no': 0,
            'stage': stage, 'ev': event, 'record_ids': record_ids, 'payload': payload}


def _model_call(run_id, profile, model, text):
    """构造携带完整实际模型输入的成功 LLM trace 事件。"""
    messages = [
        {'role': 'system', 'content': [{'type': 'text', 'text': 'fixture schema and instruction'}]},
        {'role': 'user', 'content': [{'type': 'text', 'text': text}]},
    ]
    output = [{'role': 'assistant', 'content': '{}'}]
    payload = {'profile': profile, 'gen_ai.request.model': model, 'latency_ms': 1,
               'gen_ai.usage.input_tokens': 1, 'gen_ai.usage.output_tokens': 1,
               'retries': 0, 'status': 'ok', 'gen_ai.input.messages': messages,
               'gen_ai.output.messages': output}
    return _trace_event(run_id, 'llm', 'llm.call', [], payload)


def _schema_repair_call(run_id, profile, model, payload):
    """构造评审模型修复自身输出的非绑定 schema 修复 LLM trace 事件。"""
    messages = [
        {'role': 'user', 'content': [{'type': 'text', 'text': f'[原始输出]\n{payload}'}]},
    ]
    output = [{'role': 'assistant', 'content': '{"critiques": [], "verdict": "fail"}'}]
    body = {'profile': profile, 'gen_ai.request.model': model, 'latency_ms': 1,
            'gen_ai.usage.input_tokens': 1, 'gen_ai.usage.output_tokens': 1,
            'retries': 0, 'status': 'ok', 'gen_ai.input.messages': messages,
            'gen_ai.output.messages': output}
    return _trace_event(run_id, 'llm', 'llm.call', [], body)


def _ensure_trace_files(task, workspace):
    """为纯边界测试补齐 trace 身份所绑定的最小配置文件。"""
    config = workspace / 'config.toml'
    project = workspace / f'{task}.toml'
    if not config.exists():
        config.write_text('[llm.default]\nmodel="deepseek-flash"\n[llm.judge]\nmodel="deepseek-flash"\n')
    if not project.exists():
        project.write_text('schema_version=1\n')


def _write_trace(path, rows):
    """以 JSONL 写入测试 trace。"""
    path.write_bytes(b''.join(canonical_json(row) + b'\n' for row in rows))
