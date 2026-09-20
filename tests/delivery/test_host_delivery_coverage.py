"""C4e 交付簇分支覆盖：pipeline 正反路径、编码往返、trace 校验、窗口边界、规则与训练门禁。

deliver() 成功发布路径 E2E（docs/CONTRACTS.md「CLI 命令接口」deliver 合同）：用打桩 LabelKit CLI
产物（fake subprocess.run 写出与真实 CLI 相同结构的 out/*.jsonl 与 trace）
构造完整成功清单走通发布；此前 test_publication.py 只测失败路径。
模型客户端全部打桩，零真实调用。
"""

import copy
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from worldmock.delivery.evidence_encoding import (
    DICTIONARY_FIELD,
    ENCODING_FIELD,
    decode_model_input,
    decode_model_record,
    encode_model_input,
    encode_model_record,
)
from worldmock.delivery.generation import _delivery_digest
from worldmock.delivery.pipeline import (
    _completed_trace_digest,
    _file_inventory,
    _load_source_bundle,
    _model_source,
    _read_jsonl,
    _trace_model_inputs,
    _trace_models,
    _verify_decoded_sources,
    deliver,
    run_labelkit,
    verify_process,
    write_model_inputs,
)
from worldmock.delivery.replay import replay_windows
from worldmock.delivery.rules import evaluate_causal_events, execute_fixture
from worldmock.delivery.semantics import attach_facts, validate_annotation
from worldmock.delivery.trace_validation import completed_trace_sha256
from worldmock.delivery.training import load_training
from worldmock.delivery.windows import (
    _action_windows,
    _activity_boundaries,
    _expand_observed_results,
    _prerequisite_state,
    _visible_event,
    prepare_windows,
)
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore

ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_TASKS = ('action', 'intent', 'dreaming')
FIXTURE_ANNOTATION = Path(__file__).parent / 'fixtures/labelkit-action.json'


def event(identity, sequence, kind, data, visibility='agent', agent_id=1):
    """构造窗口逻辑使用的最小事件行。"""
    return {'event_id': identity, 'event_seq': sequence, 'event_type': kind,
            'occurred_at': 100 + sequence, 'decision_id': 'decision', 'action_id': None,
            'activity_id': None, 'agent_id': agent_id, 'data': data,
            'visibility': visibility, 'artifact_refs': []}


# ---------------------------------------------------------------------------
# 导出与 LabelKit CLI 桩构造
# ---------------------------------------------------------------------------


def _single_agent_export(tmp_path):
    """建立可通过正式导出认证的单人物动作证据（观察+请求+终态+检查点）。

    event_id 固定：training.assign_splits 按来源身份连通组哈希分桶
    （bucket 8→validation、9→test、其余→train），默认 uuid4 事件身份会让
    单组数据集每次运行随机落入 validation/test（约 20% 概率缺 train 拆分，
    manifest 文件断言随之闪红）；固定身份使拆分永久落 train。
    """
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'},
                'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    store = FootprintStore.create(tmp_path / 'run', manifest, {'version': 0, 'current_time': 100})
    drafts = [
        EventDraft(event_id='fixture-observation-1', event_type='observation', occurred_at=100,
                   agent_id=1, decision_id='decision',
                   data={'balance_fen': 800, 'energy_kcal': 100}),
        EventDraft(event_id='fixture-request-1', event_type='action_requested', occurred_at=100,
                   agent_id=1, decision_id='decision', action_id='request',
                   data={'tool_name': 'start_basic_activity', 'request': {'kind': 'wait'}}),
    ]
    store.commit(store.state, drafts)
    store.commit({'version': 1, 'current_time': 200},
                 [EventDraft(event_id='fixture-run-end-1', event_type='run_ended', occurred_at=200)])
    reference = store.assets.put_json({'saved': 'complete'}, {'kind': 'fixture'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    directory = tmp_path / 'export'
    export_run(store, point.last_event_seq, directory)
    store.close()
    return directory


def _replay_export_pair(tmp_path):
    """建立含完整动作的父导出与真实反事实分支导出。"""
    manifest = {'run_id': 'parent', 'source_group_id': 'parent', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'},
                'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    parent = FootprintStore.create(tmp_path / 'parent', manifest, {'version': 0, 'current_time': 100})
    drafts = [
        EventDraft(event_type='observation', occurred_at=100, agent_id=1, decision_id='decision',
                   data={'balance_fen': 800}),
        EventDraft(event_type='action_requested', occurred_at=100, agent_id=1,
                   decision_id='decision', action_id='request',
                   data={'tool_name': 'start_basic_activity', 'request': {'kind': 'wait'}}),
        EventDraft(event_type='action_result', occurred_at=100, agent_id=1,
                   decision_id='decision', action_id='request', data={'status': 'completed'}),
    ]
    observed = parent.commit(parent.state, drafts)[0]
    reference = parent.assets.put_json({'state': 'parent'}, {'kind': 'fixture'})
    checkpoint = parent.checkpoint(parent.state, {'agent:1': reference, 'life': reference})
    request = {'kind': 'set_balance_fen', 'agent_id': 1, 'balance_fen': 50,
               'hypothesis': 'Less liquidity may reduce purchases.',
               'source_event_ids': [observed.event_id]}
    digest = hashlib.sha256(canonical_json(
        {'tool_name': 'counterfactual_intervention', 'request': request})).hexdigest()
    child = parent.restore(checkpoint.checkpoint_id, tmp_path / 'child',
                           {'branch_kind': 'counterfactual', 'intervention_sha256': digest})
    child.commit({'version': child.state['version'] + 1, 'current_time': 100}, [EventDraft(
        event_type='environment_event', occurred_at=100, agent_id=1, visibility='evaluation',
        caused_by=[observed.event_id], data={'kind': 'counterfactual_intervention',
                                             'intervention_kind': 'set_balance_fen',
                                             'hypothesis': request['hypothesis'],
                                             'source_event_ids': request['source_event_ids'],
                                             'intervention_sha256': digest,
                                             'before_balance_fen': 800, 'after_balance_fen': 50})])
    child.commit(child.state, [EventDraft(event_type='observation', occurred_at=100, agent_id=1,
                                          data={'balance_fen': 50}),
                               EventDraft(event_type='action_requested', occurred_at=100, agent_id=1,
                                          action_id='wait', data={'tool_name': 'start_basic_activity',
                                                                  'kind': 'wait'})])
    source_dir, replay_dir = tmp_path / 'source-export', tmp_path / 'replay-export'
    _finish_export(parent, source_dir)
    _finish_export(child, replay_dir)
    parent.close()
    child.close()
    return source_dir, replay_dir


def _finish_export(store, directory):
    """补终态事件与最终检查点后形成正式导出。"""
    store.commit({'version': store.state['version'] + 1, 'current_time': 200},
                 [EventDraft(event_type='run_ended', occurred_at=200)])
    reference = store.assets.put_json({'complete': True}, {'kind': 'fixture'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    export_run(store, point.last_event_seq, directory)


def _trace_event(run_id, stage, name, record_ids, payload):
    """构造与 LabelKit EventLog 相同字段的 trace 事件。"""
    return {'ts': '2026-09-18T00:00:00.000+08:00', 'run_id': run_id, 'batch_no': 0,
            'stage': stage, 'ev': name, 'record_ids': record_ids, 'payload': payload}


def _model_call(run_id, profile, model, text, *, string_content=False):
    """构造携带完整实际模型输入的成功 LLM trace 事件。"""
    user_content = text if string_content else [{'type': 'text', 'text': text}]
    messages = [{'role': 'system', 'content': [{'type': 'text', 'text': 'fixture schema'}]},
                {'role': 'user', 'content': user_content}]
    payload = {'profile': profile, 'gen_ai.request.model': model, 'latency_ms': 1,
               'gen_ai.usage.input_tokens': 1, 'gen_ai.usage.output_tokens': 1,
               'retries': 0, 'status': 'ok', 'gen_ai.input.messages': messages,
               'gen_ai.output.messages': [{'role': 'assistant', 'content': '{}'}]}
    return _trace_event(run_id, 'llm', 'llm.call', [], payload)


def _annotation(decoded):
    """按模型可见输入构造能通过语义校验的最小标注候选。"""
    allowed = decoded['input']['allowed_evidence_event_ids']
    if decoded['input'].get('mode') == 'counterfactual':
        claims = [{'text': '假设：条件变化会改变下一步选择。', 'kind': 'hypothesis',
                   'evidence_event_ids': []}]
    else:
        claims = [{'text': '人物做出了可见的行动。', 'kind': 'fact',
                   'evidence_event_ids': [allowed[0]]}]
    return {'claims': claims}


def _write_semantic_outputs(task, workspace):
    """fake CLI：按真实结构写语义输出行与完整成功 trace。"""
    encoded_rows = _read_jsonl(workspace / 'data' / f'{task}.jsonl')
    config_raw = (workspace / 'config.toml').read_bytes()
    project_raw = (workspace / f'{task}.toml').read_bytes()
    config = tomllib.loads(config_raw.decode())
    run_id = 'a' * 12
    rows = [_trace_event(run_id, 'run', 'run.start', [], {
        'tool_version': 'labelkit/1.0.0', 'trace_schema_version': 1,
        'config_digest': 'sha256:' + hashlib.sha256(config_raw).hexdigest(),
        'project_digest': 'sha256:' + hashlib.sha256(project_raw).hexdigest()})]
    outputs = []
    for line_no, encoded in enumerate(encoded_rows, 1):
        decoded = decode_model_record(encoded)
        identity = hashlib.sha256(canonical_json(encoded)).hexdigest()[:16]
        model_input = canonical_json(encoded['input']).decode()
        rows.append(_model_call(run_id, 'default', config['llm']['default']['model'],
                                f'[待标注数据] {model_input}'))
        rows.append(_model_call(run_id, 'judge', config['llm']['judge']['model'],
                                f'[原始数据] {model_input}\n[标注结果] {{}}'))
        rows.append(_trace_event(run_id, 'verify', 'verify.verdict', [identity],
                                 {'round': 1, 'verdict': 'pass'}))
        outputs.append({'target': _annotation(decoded), '_meta': {
            'id': identity, 'source': {'file': f'{task}.jsonl', 'line_no': line_no},
            'verification': {'verdict': 'pass'}, 'annotation': {'model': 'fixture'}}})
    count = len(encoded_rows)
    counts = {'scanned': count, 'ingested': count, 'bad_input': 0, 'dropped_dup': 0,
              'dropped_lowq': 0, 'dropped_verify': 0, 'failed': 0, 'generated': 0,
              'emitted': count}
    rows.append(_trace_event(run_id, 'run', 'run.end', [], {'counts': counts, 'exit_code': 0}))
    (workspace / 'out' / f'{task}.jsonl').write_bytes(
        b''.join(canonical_json(row) + b'\n' for row in outputs))
    (workspace / 'out' / f'{task}.trace.jsonl').write_bytes(
        b''.join(canonical_json(row) + b'\n' for row in rows))


def _write_rules_outputs(workspace):
    """fake CLI：写四个冻结变体的生成框架产物与提交清单。"""
    out = workspace / 'out'
    variants = {
        'positive': [('request', 0), ('accept', 1), ('complete', 2)],
        'missing_accept': [('request', 0), ('complete', 2)],
        'complete_before_accept': [('request', 0), ('complete', 1), ('accept', 2)],
        'completion_timeout': [('request', 0), ('accept', 1), ('complete', 100)],
    }
    stream, main = [], []
    for variant, roles in variants.items():
        owner = f'seq-{variant}'
        member_ids = []
        for index, (role, timestamp) in enumerate(roles):
            identity = f'event-{variant}-{index}'
            member_ids.append(identity)
            stream.append({'_meta': {'id': f'row-{identity}', 'event': {
                'event_id': identity, 'owner_sequence_id': owner, 'role': role,
                'logical_time_us': int(timestamp * 1_000_000)}}, 'payload': {'role': role}})
        main.append({'_meta': {'id': owner, 'generation': {
            'variant': variant, 'world_branch_id': 'branch-fixture'},
            'stream': {'member_ids': member_ids}}})
    run_id = 'b' * 12
    digest = _delivery_digest(main + stream)
    report = {'generate': {'sequence': {'run_id': run_id, 'delivery_digest': digest}}}
    manifest = {'schema_version': 1, 'run_id': run_id, 'delivery_digest': digest,
                'artifacts_committed': True}
    contents = {'main': main, 'stream': stream, 'report': report}
    for name, value in contents.items():
        raw = (json.dumps(value, ensure_ascii=False, indent=1) + '\n').encode() if name == 'report' \
            else b''.join(canonical_json(row) + b'\n' for row in value)
        path = out / f'{name}.json'
        path.write_bytes(raw)
        manifest[name] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
        if name != 'report':
            manifest[name]['rows'] = len(value)
    (out / 'rules.manifest.json').write_text(json.dumps(manifest))


def _fake_labelkit_cli(monkeypatch, calls):
    """在 pipeline 命名空间打桩 LabelKit CLI 子进程；产物走真实校验代码。"""
    import worldmock.delivery.pipeline as pipeline

    def fake_run(command, **_kwargs):
        project = Path(command[command.index('--project') + 1])
        task = project.stem
        calls.append(task)
        if task in SEMANTIC_TASKS:
            _write_semantic_outputs(task, project.parent)
        else:
            _write_rules_outputs(project.parent)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(pipeline.subprocess, 'run', fake_run)
    monkeypatch.setattr(pipeline, 'dotenv_values', lambda _path: {'DEEPSEEK_API_KEY': 'fixture'})


# ---------------------------------------------------------------------------
# deliver() 成功发布路径 E2E（docs/CONTRACTS.md「CLI 命令接口」）
# ---------------------------------------------------------------------------


def test_deliver_success_path_publishes_full_manifest(tmp_path, monkeypatch):
    """成功发布：真实管线 + 打桩 CLI 产物，发布完整 manifest 且不写 failure。"""
    calls: list[str] = []
    _fake_labelkit_cli(monkeypatch, calls)
    destination = tmp_path / 'delivery'

    manifest = deliver(_single_agent_export(tmp_path), destination, ROOT)

    assert calls == ['action', 'intent', 'dreaming', 'rules']
    assert manifest['schema_version'] == 1 and manifest['committed'] is True
    assert manifest['run_id'] == 'run' and manifest['source_group_id'] == 'run'
    assert set(manifest['labelkit']) == {'action', 'intent', 'dreaming', 'rules'}
    for task in SEMANTIC_TASKS:
        receipt = manifest['labelkit'][task]
        assert receipt['exit_code'] == 0 and len(receipt['trace_sha256']) == 64
    assert 'trace_sha256' not in manifest['labelkit']['rules']
    assert manifest['training_receipt']['rows'] == 5  # 1 action + 1 intent + 3 dreaming
    assert manifest['training_receipt']['saved_dataset_reloaded'] is True
    assert len(manifest['rule_results']) == 4
    assert all(item['passed'] is True for item in manifest['rule_results'])
    assert {item['actual']['allowed'] for item in manifest['rule_results']} == {True, False}
    assert (destination / 'manifest.json').is_file()
    assert not (destination / 'failure.json').exists()
    for name in ('training.jsonl', 'rules.jsonl', 'rule-results.json', 'rule-expressions.json',
                 'training-receipt.json', 'labelkit/out/action.jsonl',
                 'labelkit/out/action.trace.jsonl', 'labelkit/out/rules.manifest.json',
                 'training/dataset/train/state.json', 'training/train.jsonl'):
        assert name in manifest['files'], name
    assert 'manifest.json' not in manifest['files']
    assert len((destination / 'training.jsonl').read_text().splitlines()) == 5
    published = json.loads((destination / 'manifest.json').read_text())
    assert published['committed'] is True
    training = json.loads((destination / 'training-receipt.json').read_text())
    assert training['splits'] and sum(training['splits'].values()) == 5


def test_deliver_success_path_binds_counterfactual_replay_export(tmp_path, monkeypatch):
    """成功发布携带反事实重演：真实 replay_windows 认证 + 来源绑定进清单。"""
    calls: list[str] = []
    _fake_labelkit_cli(monkeypatch, calls)
    source_dir, replay_dir = _replay_export_pair(tmp_path)
    destination = tmp_path / 'delivery'

    manifest = deliver(source_dir, destination, ROOT, (replay_dir,))

    assert calls == ['action', 'intent', 'dreaming', 'rules']
    assert manifest['training_receipt']['rows'] == 6  # 3 dreaming + 1 重演 + 1 action + 1 intent
    reference = manifest['replay_exports'][0]
    assert reference['path'] == str(replay_dir.resolve())
    assert reference['manifest_sha256'] == hashlib.sha256(
        (replay_dir / 'manifest.json').read_bytes()).hexdigest()
    assert manifest['source_export']['manifest_sha256'] == hashlib.sha256(
        (source_dir / 'manifest.json').read_bytes()).hexdigest()
    records = [json.loads(line) for line in (destination / 'training.jsonl').read_text().splitlines()]
    replay_rows = [row for row in records
                   if row['input'].get('boundary_type') == 'counterfactual_replay']
    assert len(replay_rows) == 1
    assert replay_rows[0]['input']['counterfactual']['world_kind'] == 'resimulated'
    assert not (destination / 'failure.json').exists()


def test_deliver_rejects_existing_destination_and_duplicate_replay(tmp_path, monkeypatch):
    """已有目的目录与重复重演导出都在发布前拒绝（不产生任何产物）。"""
    export = _single_agent_export(tmp_path)
    destination = tmp_path / 'delivery'
    destination.mkdir()
    with pytest.raises(IntegrityError, match='already exists'):
        deliver(export, destination, ROOT)
    replay_dir = tmp_path / 'replay'
    replay_dir.mkdir()
    (replay_dir / 'manifest.json').write_text('{}')
    with pytest.raises(IntegrityError, match='repeats a counterfactual replay export'):
        deliver(export, tmp_path / 'delivery2', ROOT, (replay_dir, replay_dir))
    assert not (tmp_path / 'delivery2').exists()


def test_file_inventory_rejects_symbolic_artifacts(tmp_path):
    """清单盘点拒绝符号链接文件。"""
    (tmp_path / 'real.json').write_text('data')
    (tmp_path / 'link.json').symlink_to(tmp_path / 'real.json')
    with pytest.raises(IntegrityError, match='symbolic file'):
        _file_inventory(tmp_path)


# ---------------------------------------------------------------------------
# pipeline 边界分支
# ---------------------------------------------------------------------------


def _workspace(tmp_path, records, task='action'):
    """建立写入模型输入与成功 trace 的最小 LabelKit 工作区。"""
    from tests.delivery.test_labelkit_process import (
        _ensure_trace_files,
        _write_successful_trace,
    )

    workspace = tmp_path / 'labelkit'
    (workspace / 'data').mkdir(parents=True)
    (workspace / 'out').mkdir()
    write_model_inputs(task, records, workspace)
    _ensure_trace_files(task, workspace)
    _write_successful_trace(task, workspace, records)
    return workspace


def _source_record(tmp_path):
    """构造单条已验证的 action 训练记录。"""
    from tests.delivery.test_labelkit_process import _current_record

    return _current_record(tmp_path)


def test_run_labelkit_rejects_wrong_input_combinations(tmp_path, monkeypatch):
    """语义任务缺输入、非语义任务带输入、缺凭据三种入口拒绝。"""
    import worldmock.delivery.pipeline as pipeline

    record = _source_record(tmp_path)
    workspace = _workspace(tmp_path, [record])
    monkeypatch.setattr(pipeline, 'dotenv_values', lambda _path: {'DEEPSEEK_API_KEY': 'fixture'})
    monkeypatch.setattr(pipeline.subprocess, 'run',
                        lambda *_a, **_k: pytest.fail('rejected input must not invoke LabelKit'))
    with pytest.raises(IntegrityError, match='complete logical inputs'):
        run_labelkit('action', workspace, ROOT)
    with pytest.raises(IntegrityError, match='cannot receive semantic model inputs'):
        run_labelkit('rules', workspace, ROOT, [record])
    monkeypatch.setattr(pipeline, 'dotenv_values', lambda _path: {})
    with pytest.raises(IntegrityError, match='DEEPSEEK_API_KEY is required'):
        run_labelkit('rules', workspace, ROOT)


def test_write_model_inputs_rejects_unsupported_or_empty_inputs(tmp_path):
    """模型输入写入只接受支持任务与非空记录。"""
    workspace = tmp_path / 'ws'
    (workspace / 'data').mkdir(parents=True)
    with pytest.raises(IntegrityError, match='supported task and non-empty'):
        write_model_inputs('rules', [{'task': 'rules'}], workspace)
    with pytest.raises(IntegrityError, match='supported task and non-empty'):
        write_model_inputs('action', [], workspace)


def test_verify_process_rejects_row_count_mismatch(tmp_path):
    """输出行数与输入守恒不符时拒绝。"""
    from tests.delivery.test_labelkit_process import _output_row, _prepare_verified_sources

    fixture = json.loads(FIXTURE_ANNOTATION.read_text())
    first = _source_record(tmp_path)
    second = copy.deepcopy(first)
    second['input']['boundary_type'] = 'second-decision'
    second['provenance']['window_id'] = _window_identity(second['task'], second['input'])
    _prepare_verified_sources(tmp_path, [first, second])
    (tmp_path / 'out/action.jsonl').write_text(
        json.dumps(_output_row(fixture['annotation'], first)) + '\n')
    with pytest.raises(IntegrityError, match='did not preserve every input window'):
        verify_process('action', tmp_path, [first, second])


def test_verify_process_rejects_semantic_violation_after_pass_verdict(tmp_path):
    """评审通过但标注引用越界证据时仍拒绝发布。"""
    from tests.delivery.test_labelkit_process import _output_row, _prepare_verified_sources

    fixture = json.loads(FIXTURE_ANNOTATION.read_text())
    record = _source_record(tmp_path)
    annotation = copy.deepcopy(fixture['annotation'])
    annotation['target']['claims'][0]['evidence_event_ids'] = ['forged-event']
    _prepare_verified_sources(tmp_path, [record])
    row = _output_row(annotation, record)
    (tmp_path / 'out/action.jsonl').write_text(json.dumps(row) + '\n')
    with pytest.raises(IntegrityError, match='violated semantic or factual constraints'):
        verify_process('action', tmp_path, [record])


def _window_identity(task, input_data):
    """与生产同口径的公开窗口身份。"""
    from worldmock.delivery.windows import _window_identity as compute

    return compute(task, input_data)


def test_model_source_rejects_task_and_window_mismatch(tmp_path):
    """编码行来源索引拒绝任务不符与窗口身份不符。"""
    record = _source_record(tmp_path)
    other = copy.deepcopy(record)
    other['task'] = 'intent'
    with pytest.raises(IntegrityError, match='task does not match'):
        _model_source('action', 1, encode_model_record(other))
    tampered = copy.deepcopy(record)
    tampered['provenance']['window_id'] = '0' * 64
    with pytest.raises(IntegrityError, match='invalid logical window identity'):
        _model_source('action', 1, encode_model_record(tampered))
    assert _model_source('action', 1, encode_model_record(record))['task'] == 'action'


def test_load_source_bundle_rejects_empty_or_mismatched_index(tmp_path):
    """来源捆绑校验拒绝空编码文件与空来源索引。"""
    record = _source_record(tmp_path)
    workspace = _workspace(tmp_path, [record])
    (workspace / 'data/action.jsonl').write_text('')
    with pytest.raises(IntegrityError, match='does not preserve every encoded line'):
        _load_source_bundle('action', workspace, trace_required=False)
    (workspace / 'data/action.jsonl').write_text('{}\n')
    (workspace / 'data/action.source-index.jsonl').write_text('')
    with pytest.raises(IntegrityError, match='does not preserve every encoded line'):
        _load_source_bundle('action', workspace, trace_required=False)


def test_completed_trace_digest_requires_identity_files(tmp_path):
    """trace 身份文件缺失时明确失败。"""
    record = _source_record(tmp_path)
    workspace = _workspace(tmp_path, [record])
    (workspace / 'config.toml').unlink()
    with pytest.raises(IntegrityError, match='Cannot read LabelKit trace identity files'):
        _completed_trace_digest('action', workspace, [])


def test_trace_models_rejects_invalid_toml_and_empty_models():
    """模型身份读取拒绝坏 TOML 与空模型名。"""
    with pytest.raises(IntegrityError, match='Cannot read LabelKit model identities'):
        _trace_models(b'not = [toml')
    with pytest.raises(IntegrityError, match='model identities are invalid'):
        _trace_models(b'[llm.default]\nmodel=""\n[llm.judge]\nmodel="deepseek-flash"\n')


def test_trace_model_inputs_rejects_duplicate_records(tmp_path):
    """重复编码行身份在 trace 绑定前被拒绝。"""
    record = _source_record(tmp_path)
    encoded = encode_model_record(record)
    with pytest.raises(IntegrityError, match='identities are duplicated'):
        _trace_model_inputs([encoded, copy.deepcopy(encoded)])


def test_verify_decoded_sources_rejects_count_mismatch(tmp_path):
    """解码守恒校验拒绝行数不一致。"""
    record = _source_record(tmp_path)
    encoded = encode_model_record(record)
    with pytest.raises(IntegrityError, match='do not preserve every logical window'):
        _verify_decoded_sources([encoded], [record, copy.deepcopy(record)])


def test_read_jsonl_rejects_missing_file(tmp_path):
    """来源文件读取缺失即失败关闭。"""
    with pytest.raises(IntegrityError, match='Cannot read model source file'):
        _read_jsonl(tmp_path / 'absent.jsonl')


# ---------------------------------------------------------------------------
# evidence_encoding 解码拒绝矩阵与编码守卫
# ---------------------------------------------------------------------------


def observation(identity, data, agent_id=1):
    """构造编码测试的观察事件。"""
    return {'event_id': identity, 'event_type': 'observation', 'occurred_at': 1,
            'agent_id': agent_id, 'data': data, 'materials': []}


def logical_input():
    """可盈利字典编码的标准逻辑窗口。"""
    catalog = {'items': [{'name': '午餐', 'price_fen': 1800}], 'description': '可见目录' * 300}
    events = [observation(f'event-{index}', {'catalog': catalog, 'nullable': None})
              for index in range(1, 4)]
    return {'events': events, 'allowed_evidence_event_ids': [e['event_id'] for e in events],
            'knowledge_cutoff_event_seq': 3, 'prerequisite_state': {'event_id': None}}


def test_encode_round_trip_guard_rejects_diverging_decode(monkeypatch):
    """编码自检守卫：解码协作者返回不一致结果时拒绝发布。"""
    import worldmock.delivery.evidence_encoding as encoding

    source = logical_input()
    encoded = encode_model_input(source)
    original = encoding.decode_model_input
    monkeypatch.setattr(encoding, 'decode_model_input', lambda _value: {'tampered': True})
    try:
        with pytest.raises(IntegrityError, match='does not round-trip'):
            encode_model_input(source)
    finally:
        monkeypatch.setattr(encoding, 'decode_model_input', original)
    assert decode_model_input(encoded) == source


def test_encode_rejects_invalid_logical_input_shapes():
    """逻辑输入拒绝非对象、事件列表缺失与保留字段。"""
    with pytest.raises(IntegrityError, match='requires an events list'):
        encode_model_input('not-a-dict')
    with pytest.raises(IntegrityError, match='reserved encoding field'):
        encode_model_input({'events': [], ENCODING_FIELD: {}})
    with pytest.raises(IntegrityError, match='requires an ordered event object list'):
        encode_model_input({'events': ['not-an-object']})


def test_encode_rejects_non_object_observation_data():
    """观察数据必须为对象。"""
    with pytest.raises(IntegrityError, match='Observation data must be an object'):
        encode_model_input({'events': [{'event_type': 'observation', 'data': 'oops'}]})


def test_encode_rejects_dictionary_digest_collision(monkeypatch):
    """字典身份冲突守卫：摘要协作者失真时拒绝。"""
    import worldmock.delivery.evidence_encoding as encoding

    monkeypatch.setattr(encoding, '_digest', lambda _value: 'constant-digest')
    with pytest.raises(IntegrityError, match='digest collision'):
        encode_model_input(logical_input())


def _multi_entry_input():
    """产生多条字典条目的逻辑窗口（验证条目顺序校验）。"""
    shared = {
        'catalog': {'items': [{'name': '午餐', 'price_fen': 1800}], 'description': '可见目录' * 300},
        'price_board': {'items': [{'name': '晚餐', 'price_fen': 2400}], 'note': '今日价格' * 300},
        'menu_specials': {'items': [{'name': '汤品', 'price_fen': 900}], 'note': '限量供应' * 300},
        'seating_note': {'zones': ['大厅', '露台'], 'description': '座位说明' * 300},
    }
    events = [observation(f'event-{index}', dict(shared, nullable=None))
              for index in range(1, 4)]
    return {'events': events, 'allowed_evidence_event_ids': [e['event_id'] for e in events],
            'knowledge_cutoff_event_seq': 3, 'prerequisite_state': {'event_id': None}}


@pytest.mark.parametrize('mutation, message', [
    ('not_dict', 'must be an object'),
    ('marker_shape', 'invalid shape'),
    ('marker_contract', 'does not match the supported contract'),
    ('digest_type', 'no canonical input digest'),
    ('digest_pattern', 'invalid canonical input digest'),
    ('dictionary_type', 'must be an array'),
    ('entry_shape', 'entry has an invalid shape'),
    ('entry_digest', 'invalid digest'),
    ('entry_pointer', 'nested value pointer'),
    ('entry_types', 'field and digest must be strings'),
    ('entry_order', 'duplicated or out of order'),
])
def test_decode_rejects_tampered_envelope(mutation, message):
    """解码拒绝矩阵：信封与字典被篡改的每种方式。"""
    encoded = encode_model_input(_multi_entry_input())
    if mutation == 'not_dict':
        with pytest.raises(IntegrityError, match=message):
            decode_model_input('not-a-dict')
        return
    if mutation == 'marker_shape':
        encoded[ENCODING_FIELD].pop('standard')
    elif mutation == 'marker_contract':
        encoded[ENCODING_FIELD]['standard'] = 'JSON-pointer'
    elif mutation == 'digest_type':
        encoded[ENCODING_FIELD]['decoded_input_sha256'] = 64
    elif mutation == 'digest_pattern':
        encoded[ENCODING_FIELD]['decoded_input_sha256'] = 'z' * 64
    elif mutation == 'dictionary_type':
        encoded[DICTIONARY_FIELD] = {'entry': True}
    elif mutation == 'entry_shape':
        encoded[DICTIONARY_FIELD][0]['extra'] = 1
    elif mutation == 'entry_digest':
        encoded[DICTIONARY_FIELD][0]['sha256'] = 'g' * 64
    elif mutation == 'entry_pointer':
        encoded[DICTIONARY_FIELD][0]['value'] = {'value_pointer': '/x'}
    elif mutation == 'entry_types':
        encoded[DICTIONARY_FIELD][0]['field'] = 5
    else:
        encoded[DICTIONARY_FIELD][0], encoded[DICTIONARY_FIELD][1] = (
            encoded[DICTIONARY_FIELD][1], encoded[DICTIONARY_FIELD][0])
    assert len(encoded[DICTIONARY_FIELD]) >= 2 or mutation != 'entry_order'
    with pytest.raises(IntegrityError, match=message):
        decode_model_input(encoded)


def test_decode_rejects_pointer_misuse_and_out_of_range():
    """指针必须在观察数据字段内且索引在字典范围内。"""
    encoded = encode_model_input(logical_input())
    system = copy.deepcopy(encoded['events'][0])
    system['event_type'] = 'action_result'
    encoded['events'].append(system)
    with pytest.raises(IntegrityError, match='outside an observation data field'):
        decode_model_input(encoded)

    encoded = encode_model_input(logical_input())
    encoded['events'][0]['data']['catalog'] = {
        'value_pointer': '/observation_value_dictionary/9/value'}
    with pytest.raises(IntegrityError, match='outside its local dictionary'):
        decode_model_input(encoded)


def test_decode_rejects_unresolvable_pointer(monkeypatch):
    """指针解析协作者失败时守卫拒绝（guard 测试）。"""
    from jsonpointer import JsonPointerException

    import worldmock.delivery.evidence_encoding as encoding

    def broken(_source, _pointer):
        raise JsonPointerException('reference error')

    monkeypatch.setattr(encoding, 'resolve_pointer', broken)
    with pytest.raises(IntegrityError, match='cannot be resolved'):
        decode_model_input(encode_model_input(logical_input()))


def test_decode_rejects_non_object_event_data():
    """解码要求事件 data 为对象。"""
    encoded = encode_model_input(logical_input())
    encoded['events'][0]['data'] = 'oops'
    with pytest.raises(IntegrityError, match='data must be an object'):
        decode_model_input(encoded)


def test_record_codecs_reject_non_object_inputs():
    """记录级编解码入口拒绝非对象记录。"""
    with pytest.raises(IntegrityError, match='requires an object input'):
        encode_model_record({'input': 'not-a-dict'})
    with pytest.raises(IntegrityError, match='requires an object input'):
        encode_model_record(['not-a-record'])
    with pytest.raises(IntegrityError, match='requires an object input'):
        decode_model_record({'input': 'not-a-dict'})
    record = {'task': 'action', 'input': logical_input(), 'target': {'facts': {}},
              'provenance': {'run_id': 'run'}}
    assert decode_model_record(encode_model_record(record)) == record


# ---------------------------------------------------------------------------
# trace_validation 拒绝矩阵
# ---------------------------------------------------------------------------


def _write_rows(path, rows):
    """以 canonical JSONL 写 trace 行。"""
    path.write_bytes(b''.join(canonical_json(row) + b'\n' for row in rows))


def _trace_arguments(workspace, records):
    """按管线同口径组装 completed_trace_sha256 的全部参数。"""
    path = workspace / 'out/action.trace.jsonl'
    digests = {}
    for record in records:
        encoded = encode_model_record(record)
        identity = hashlib.sha256(canonical_json(encoded)).hexdigest()[:16]
        digests[identity] = hashlib.sha256(canonical_json(encoded['input'])).hexdigest()
    config = tomllib.loads((workspace / 'config.toml').read_text())
    models = {profile: config['llm'][profile]['model'] for profile in ('default', 'judge')}
    return [path, digests,
            'sha256:' + hashlib.sha256((workspace / 'config.toml').read_bytes()).hexdigest(),
            'sha256:' + hashlib.sha256((workspace / 'action.toml').read_bytes()).hexdigest(),
            models]


def _as_strings(rows):
    """把 trace 中 LLM 消息 content 从块列表改为字符串（字符串 content 分支）。"""
    mutated = copy.deepcopy(rows)
    for row in mutated:
        if row['ev'] == 'llm.call':
            for message in row['payload']['gen_ai.input.messages']:
                content = message['content']
                if isinstance(content, list):
                    message['content'] = content[0]['text']
    return mutated


def test_trace_validation_rejects_missing_and_empty_trace(tmp_path):
    """trace 缺失与空白均失败关闭。"""
    record = _source_record(tmp_path)
    workspace = _workspace(tmp_path, [record])
    arguments = _trace_arguments(workspace, [record])
    (workspace / 'out/action.trace.jsonl').unlink()
    with pytest.raises(IntegrityError, match='Cannot read LabelKit trace'):
        completed_trace_sha256(*arguments)
    (workspace / 'out/action.trace.jsonl').write_bytes(b'   \n')
    with pytest.raises(IntegrityError, match='trace is empty'):
        completed_trace_sha256(*arguments)


def test_trace_validation_rejects_non_object_rows_and_bad_envelopes(tmp_path):
    """trace 行必须为对象且信封字段齐全、类型正确。"""
    record = _source_record(tmp_path)
    workspace = _workspace(tmp_path, [record])
    rows = [json.loads(line)
            for line in (workspace / 'out/action.trace.jsonl').read_text().splitlines()]
    arguments = _trace_arguments(workspace, [record])
    path = workspace / 'out/action.trace.jsonl'

    path.write_text('[1,2]\n')
    with pytest.raises(IntegrityError, match='must be a JSON object'):
        completed_trace_sha256(*arguments)
    extra = dict(rows[1])
    extra['surprise'] = 1
    path.write_text(json.dumps(extra) + '\n')
    with pytest.raises(IntegrityError, match='invalid envelope'):
        completed_trace_sha256(*arguments)
    negative = dict(rows[1])
    negative['batch_no'] = -1
    path.write_text(json.dumps(negative) + '\n')
    with pytest.raises(IntegrityError, match='invalid field types'):
        completed_trace_sha256(*arguments)


def test_trace_validation_rejects_broken_lifecycle(tmp_path):
    """run.start 缺失、工具版本与零计数违例各自拒绝。"""
    record = _source_record(tmp_path)
    workspace = _workspace(tmp_path, [record])
    rows = [json.loads(line)
            for line in (workspace / 'out/action.trace.jsonl').read_text().splitlines()]
    arguments = _trace_arguments(workspace, [record])
    path = workspace / 'out/action.trace.jsonl'

    no_start = [row for row in rows if row['ev'] != 'run.start']
    _write_rows(path, no_start)
    with pytest.raises(IntegrityError, match='must start with one run.start'):
        completed_trace_sha256(*arguments)
    no_version = copy.deepcopy(rows)
    no_version[0]['payload'].pop('tool_version')
    _write_rows(path, no_version)
    with pytest.raises(IntegrityError, match='schema version is unsupported'):
        completed_trace_sha256(*arguments)
    rejected = copy.deepcopy(rows)
    rejected[-1]['payload']['counts']['bad_input'] = 1
    _write_rows(path, rejected)
    with pytest.raises(IntegrityError, match='rejected or failed source records'):
        completed_trace_sha256(*arguments)


def test_trace_validation_accepts_string_content_and_rejects_bad_shapes(tmp_path):
    """字符串 content 的消息可整体通过；坏形状逐项拒绝。"""
    record = _source_record(tmp_path)
    workspace = _workspace(tmp_path, [record])
    rows = _as_strings([json.loads(line) for line in
                        (workspace / 'out/action.trace.jsonl').read_text().splitlines()])
    arguments = _trace_arguments(workspace, [record])
    path = workspace / 'out/action.trace.jsonl'
    _write_rows(path, rows)
    assert len(completed_trace_sha256(*arguments)) == 64

    def first_call():
        return next(index for index, row in enumerate(rows) if row['ev'] == 'llm.call')

    def rewrite(index, mutate):
        mutated = copy.deepcopy(rows)
        mutate(mutated[index])
        _write_rows(path, mutated)

    call = first_call()
    rewrite(call, lambda row: row['payload'].__setitem__('gen_ai.input.messages', 'oops'))
    with pytest.raises(IntegrityError, match='no complete request messages'):
        completed_trace_sha256(*arguments)
    rewrite(call, lambda row: row['payload']['gen_ai.input.messages'].__setitem__(0, 'oops'))
    with pytest.raises(IntegrityError, match='request message is invalid'):
        completed_trace_sha256(*arguments)
    rewrite(call, lambda row: row['payload']['gen_ai.input.messages'][1].__setitem__('content', 5))
    with pytest.raises(IntegrityError, match='request content is invalid'):
        completed_trace_sha256(*arguments)
    mixed = copy.deepcopy(rows)
    mixed[call]['payload']['gen_ai.input.messages'][1]['content'] = [
        {'type': 'image'}, {'type': 'text', 'text': mixed[call]['payload'][
            'gen_ai.input.messages'][1]['content']}]
    _write_rows(path, mixed)
    assert len(completed_trace_sha256(*arguments)) == 64
    repair = _model_call('a' * 12, 'judge', arguments[4]['judge'], '[原始输出]\n{}',
                         string_content=True)
    repair['record_ids'] = ['record-id']
    with_repair = [*rows[:-1], repair, rows[-1]]
    _write_rows(path, with_repair)
    with pytest.raises(IntegrityError, match='unsuccessful model call'):
        completed_trace_sha256(*arguments)


def test_trace_validation_rejects_duplicate_expected_inputs_and_bad_verdicts(tmp_path):
    """期望输入重复与裁决序号非法各自拒绝。"""
    first = _source_record(tmp_path)
    second = copy.deepcopy(first)
    second['input']['boundary_type'] = 'second-decision'
    second['provenance']['window_id'] = _window_identity(second['task'], second['input'])
    workspace = _workspace(tmp_path, [first, second])
    arguments = _trace_arguments(workspace, [first, second])
    shared_digest = next(iter(arguments[1].values()))
    duplicated = {identity: shared_digest for identity in arguments[1]}
    with pytest.raises(IntegrityError, match='not uniquely traceable'):
        completed_trace_sha256(arguments[0], duplicated, *arguments[2:])
    rows = [json.loads(line) for line in
            (workspace / 'out/action.trace.jsonl').read_text().splitlines()]
    skipped = copy.deepcopy(rows)
    verdict = next(row for row in skipped if row['ev'] == 'verify.verdict')
    verdict['payload']['round'] = 5
    _write_rows(workspace / 'out/action.trace.jsonl', skipped)
    with pytest.raises(IntegrityError, match='verdict sequence is invalid'):
        completed_trace_sha256(*arguments)


def test_trace_validation_rejects_invalid_source_input_markers(tmp_path):
    """模型输入标记缺失、非 JSON 与非对象逐项拒绝。"""
    record = _source_record(tmp_path)
    workspace = _workspace(tmp_path, [record])
    rows = [json.loads(line) for line in
            (workspace / 'out/action.trace.jsonl').read_text().splitlines()]
    arguments = _trace_arguments(workspace, [record])
    path = workspace / 'out/action.trace.jsonl'

    def rewrite_text(text):
        mutated = copy.deepcopy(rows)
        call = next(row for row in mutated if row['ev'] == 'llm.call'
                    and row['payload']['profile'] == 'default')
        call['payload']['gen_ai.input.messages'][1]['content'] = [{'type': 'text', 'text': text}]
        _write_rows(path, mutated)

    rewrite_text('no markers at all')
    with pytest.raises(IntegrityError, match='one complete source input'):
        completed_trace_sha256(*arguments)
    rewrite_text('[待标注数据] not-json')
    with pytest.raises(IntegrityError, match='source input is invalid JSON'):
        completed_trace_sha256(*arguments)
    rewrite_text('[待标注数据] [1,2]')
    with pytest.raises(IntegrityError, match='source input must be a JSON object'):
        completed_trace_sha256(*arguments)


# ---------------------------------------------------------------------------
# windows 边界分支
# ---------------------------------------------------------------------------


def test_prepare_windows_rejects_export_without_any_task_window(tmp_path):
    """三类任务任一为空即拒绝（无人物可见事件的导出）。"""
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'},
                'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    store = FootprintStore.create(tmp_path / 'run', manifest, {'version': 0, 'current_time': 100})
    store.commit(store.state, [EventDraft(event_type='environment_event', occurred_at=100,
                                          visibility='system', data={'kind': 'silent'})])
    store.commit({'version': 1, 'current_time': 200},
                 [EventDraft(event_type='run_ended', occurred_at=200)])
    reference = store.assets.put_json({'saved': True}, {'kind': 'fixture'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    directory = tmp_path / 'export'
    export_run(store, point.last_event_seq, directory)
    store.close()
    with pytest.raises(IntegrityError, match='no complete evidence windows'):
        prepare_windows(directory)


def test_action_windows_require_prior_visible_observation(tmp_path):
    """动作训练要求此前存在可见观察。"""
    requested = event('target', 3, 'action_requested', {'tool_name': 'purchase'})
    requested['action_id'] = 'purchase'
    manifest = {'run_id': 'run', 'source_group_id': 'run',
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    with pytest.raises(IntegrityError, match='preceding visible observation'):
        _action_windows(requested, {'purchase': [requested]}, {'decision': [requested]},
                        (manifest, tmp_path, {'target': requested}))


def test_action_groups_reject_requests_without_agent(tmp_path):
    """动作请求必须声明人物。"""
    requested = event('target', 3, 'action_requested', {'tool_name': 'purchase'})
    requested['agent_id'] = None
    requested['action_id'] = 'purchase'
    manifest = {'run_id': 'run', 'source_group_id': 'run',
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    with pytest.raises(IntegrityError, match='identify its agent'):
        _action_windows(requested, {'purchase': [requested]}, {'decision': [requested]},
                        (manifest, tmp_path, {'target': requested}))


def test_visible_event_expands_text_and_binary_materials(tmp_path):
    """文本素材展开内容、二进制素材保留模态引用、摘要不符拒绝。"""
    payload = {'scene': '清晨在家'}
    raw = json.dumps(payload).encode()
    digest = hashlib.sha256(raw).hexdigest()
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / digest).write_bytes(raw)
    reference = {'sha256': digest, 'size_bytes': len(raw), 'media_type': 'application/json'}
    visible = _visible_event({'event_id': 'e1', 'event_type': 'observation', 'occurred_at': 1,
                              'agent_id': 1, 'data': {}, 'artifact_refs': [reference]}, tmp_path)
    assert visible['materials'][0]['content'] == json.dumps(payload)

    binary = b'\x89PNG\r\n'
    binary_digest = hashlib.sha256(binary).hexdigest()
    (assets / binary_digest).write_bytes(binary)
    binary_reference = {'sha256': binary_digest, 'size_bytes': len(binary),
                        'media_type': 'image/png'}
    visible = _visible_event({'event_id': 'e2', 'event_type': 'observation', 'occurred_at': 1,
                              'agent_id': 1, 'data': {}, 'artifact_refs': [binary_reference]},
                             tmp_path)
    assert 'content' not in visible['materials'][0]

    forged = {'sha256': digest, 'size_bytes': len(raw) + 1, 'media_type': 'application/json'}
    with pytest.raises(IntegrityError, match='does not match its event reference'):
        _visible_event({'event_id': 'e3', 'event_type': 'observation', 'occurred_at': 1,
                        'agent_id': 1, 'data': {}, 'artifact_refs': [forged]}, tmp_path)


def test_expand_observed_results_rejects_non_list_and_invisible_references():
    """观察摘要引用必须为列表且来自人物可见观察。"""
    observed = event('visible', 2, 'observation', {'recent_action_results': 'not-a-list'})
    with pytest.raises(IntegrityError, match='must be a list'):
        _expand_observed_results([observed], {}, cutoff=2)
    hidden = event('hidden', 2, 'observation',
                   {'recent_action_results': [{'event_id': 'x'}]}, visibility='system')
    with pytest.raises(IntegrityError, match='agent-visible observation'):
        _expand_observed_results([hidden], {'x': {}}, cutoff=2)
    string_reference = event('visible', 2, 'observation', {'recent_action_results': ['str']})
    with pytest.raises(IntegrityError, match='no event identity'):
        _expand_observed_results([string_reference], {}, cutoff=2)


def test_attach_evidence_skips_absent_prerequisite_identity(tmp_path):
    """前置状态未观察时不追加来源身份（无前置观察的自然日窗口）。"""
    from worldmock.delivery.windows import _attach_evidence, _record

    observed = event('observed', 1, 'action_requested', {'tool_name': 'wait'})
    manifest = {'run_id': 'run', 'source_group_id': 'run',
                'frozen_run_manifest': {'model_identity': {'name': 'fixture'}}}
    record = _record('dreaming', [observed], {'mode': 'induction', 'environment_replay': None},
                     manifest, {'availability': 'unobserved', 'event_id': None,
                                'observed_at': None, 'state': None})
    _attach_evidence(record, [observed], {'observed': observed}, tmp_path)
    assert record['input']['allowed_evidence_event_ids'] == ['observed']


def test_prerequisite_state_reports_unobserved_without_observations():
    """无可见观察时前置状态明确未观察。"""
    requested = event('target', 3, 'action_requested', {})
    prerequisite = _prerequisite_state([requested], [requested])
    assert prerequisite == {'availability': 'unobserved', 'event_id': None,
                            'observed_at': None, 'state': None}
    observed = event('observed', 1, 'observation', {'balance_fen': 800})
    known = _prerequisite_state([requested], [observed, requested])
    assert known['availability'] == 'observed' and known['event_id'] == 'observed'


def test_activity_boundaries_capture_terminal_activity_statuses():
    """活动边界保留完成与取消的观察记录。"""
    completed = event('done', 1, 'action_result', {'activity': {
        'activity_id': 'a1', 'status': 'completed', 'boundary_type': 'natural',
        'ended_at': 120}})
    cancelled = event('stopped', 2, 'action_result', {'activity': {
        'activity_id': 'a2', 'status': 'cancelled', 'boundary_type': 'truncated',
        'ended_at': 130}})
    running = event('running', 3, 'action_result', {'activity': {
        'activity_id': 'a3', 'status': 'in_progress', 'boundary_type': 'none',
        'ended_at': None}})
    boundaries = _activity_boundaries([completed, cancelled, running])
    assert {row['activity_id'] for row in boundaries} == {'a1', 'a2'}
    assert all(row['source_event_id'] in {'done', 'stopped'} for row in boundaries)


# ---------------------------------------------------------------------------
# rules / semantics / training / generation 门禁分支
# ---------------------------------------------------------------------------


def test_causal_rules_detect_duplicate_and_future_cause():
    """重复事件身份与未来原因各自生成硬错误；缺失支持字段时安静跳过。"""
    first = {'source_event_id': 'e1', 'occurred_at': '2026-01-05T10:00:00+08:00',
             'ended_at': None, 'caused_by': [], 'source_pointer': 'events:1'}
    second = {'source_event_id': 'e1', 'occurred_at': '2026-01-05T09:00:00+08:00',
              'ended_at': None, 'caused_by': ['e1'], 'source_pointer': 'events:2'}
    findings = evaluate_causal_events([first, second], {'fields': ['occurred_at', 'caused_by']})
    assert {item['code'] for item in findings} == {'duplicate_event', 'future_cause'}
    sound = {'source_event_id': 'e2', 'occurred_at': '2026-01-05T11:00:00+08:00',
             'ended_at': None, 'caused_by': ['e1'], 'source_pointer': 'events:2'}
    assert evaluate_causal_events([first, sound],
                                  {'fields': ['occurred_at', 'caused_by']}) == []
    quiet = {'source_event_id': 'e1', 'occurred_at': '2026-01-05T10:00:00+08:00',
             'ended_at': None, 'state_version_before': None,
             'state_version_after': None, 'resource_changes': {'wallet': -5},
             'caused_by': [], 'source_pointer': 'events:1'}
    assert evaluate_causal_events([quiet], {'fields': list(quiet)}) == []


def test_execute_fixture_propagates_rule_engine_errors():
    """规则引擎执行异常按原样传播（记录后上抛）。"""
    fixture = {'initial_state': {'deadline_seconds': 60},
               'events': [], 'expected': {}, 'provenance': {'kind': 'fixture'}}
    with pytest.raises(Exception) as caught:
        execute_fixture(fixture, {'allowed': 'complete and (broken'})
    assert not isinstance(caught.value, IntegrityError)


def test_semantic_validation_requires_original_record_and_hypothesis():
    """缺原始记录拒绝；反事实缺假设拒绝；无效标注不可回填。"""
    errors = validate_annotation({'target': {'claims': []}}, None)
    assert errors == ['Original record is required for semantic validation']

    original = {'task': 'dreaming',
                'input': {'events': [{'event_id': 'event-1', 'materials': []}],
                          'allowed_evidence_event_ids': ['event-1'],
                          'mode': 'counterfactual'},
                'target': {'mode': 'counterfactual'},
                'provenance': {'run_id': 'run', 'source_group_id': 'run',
                               'source_event_ids': ['event-1']}}
    candidate = {'target': {'claims': [{'text': '可见事实。', 'kind': 'fact',
                                        'evidence_event_ids': ['event-1']}]}}
    assert validate_annotation(candidate, original) == [
        'Counterfactual output does not declare a hypothesis']
    with pytest.raises(IntegrityError, match='Cannot attach facts'):
        attach_facts(candidate, original)


def _training_record(event_id='event-1'):
    """训练加载测试使用的最小合格记录。"""
    return {'task': 'action', 'input': {'events': [{'event_id': event_id, 'materials': []}]},
            'target': {'claims': []},
            'provenance': {'run_id': 'run', 'source_group_id': 'run',
                           'source_event_ids': [event_id]}}


def test_training_rejects_empty_records_and_foreign_fields(tmp_path):
    """训练加载拒绝空记录与合同外字段。"""
    with pytest.raises(IntegrityError, match='no records'):
        load_training([], tmp_path / 'training')
    record = dict(_training_record(), extra=1)
    with pytest.raises(IntegrityError, match='outside the canonical contract'):
        load_training([record], tmp_path / 'training')


def test_training_rejects_loader_and_disk_tampering(tmp_path, monkeypatch):
    """加载器与磁盘回读被篡改时守恒校验失败关闭。"""
    from datasets import Dataset, DatasetDict, Features, Json, Value

    import worldmock.delivery.training as training

    features = Features({'task': Value('string'), 'input': Json(decode=False),
                         'target': Json(decode=False), 'provenance': Json(decode=False)})
    tampered_row = {'task': 'action', 'input': '{"tampered": true}', 'target': '{}',
                    'provenance': '{}'}
    monkeypatch.setattr(training, 'load_dataset', lambda *_a, **_k: DatasetDict(
        {'train': Dataset.from_list([tampered_row], features=features)}))
    with pytest.raises(IntegrityError, match='changed canonical training records'):
        load_training([_training_record()], tmp_path / 'loader-tampered')

    monkeypatch.setattr(training, 'load_dataset',
                        __import__('datasets').load_dataset)
    different = DatasetDict({'train': Dataset.from_list([{
        'prompt': [{'role': 'user', 'content': 'different'}],
        'completion': [{'role': 'assistant', 'content': 'different'}]}])})
    monkeypatch.setattr(training, 'load_from_disk', lambda *_a, **_k: different)
    with pytest.raises(IntegrityError, match='changed the verified conversation records'):
        load_training([_training_record()], tmp_path / 'disk-tampered')


def test_generation_fixture_rejects_invalid_owner_or_role():
    """生成框架事件的身份与角色必须属于其主序列。"""
    from worldmock.delivery.generation import _fixture_event

    row = {'_meta': {'event': {'event_id': 'e1', 'owner_sequence_id': 'other', 'role': 'request',
                               'logical_time_us': 0}}, 'payload': {}}
    with pytest.raises(IntegrityError, match='invalid owner or role'):
        _fixture_event(row, 'owner')
    row['_meta']['event']['owner_sequence_id'] = 'owner'
    row['_meta']['event']['role'] = 'mystery'
    with pytest.raises(IntegrityError, match='invalid owner or role'):
        _fixture_event(row, 'owner')
    row['_meta']['event']['role'] = 'accept'
    assert _fixture_event(row, 'owner')['role'] == 'accept'


# ---------------------------------------------------------------------------
# replay 认证拒绝矩阵
# ---------------------------------------------------------------------------


def test_replay_requires_visible_post_intervention_results(tmp_path):
    """干预后无人物可见结果的重演分支拒绝（无实际可见结果）。"""
    manifest = {'run_id': 'parent', 'source_group_id': 'parent', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'},
                'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    parent = FootprintStore.create(tmp_path / 'parent', manifest, {'version': 0, 'current_time': 100})
    observed = parent.commit(parent.state, [EventDraft(event_type='observation', occurred_at=100,
                                                       agent_id=1, data={'balance_fen': 800})])[0]
    reference = parent.assets.put_json({'state': 'parent'}, {'kind': 'fixture'})
    checkpoint = parent.checkpoint(parent.state, {'agent:1': reference, 'life': reference})
    request = {'kind': 'set_balance_fen', 'agent_id': 1, 'balance_fen': 50,
               'hypothesis': 'Less liquidity may reduce purchases.',
               'source_event_ids': [observed.event_id]}
    digest = hashlib.sha256(canonical_json(
        {'tool_name': 'counterfactual_intervention', 'request': request})).hexdigest()
    child = parent.restore(checkpoint.checkpoint_id, tmp_path / 'child',
                           {'branch_kind': 'counterfactual', 'intervention_sha256': digest})
    child.commit({'version': child.state['version'] + 1, 'current_time': 100}, [EventDraft(
        event_type='environment_event', occurred_at=100, agent_id=1, visibility='evaluation',
        caused_by=[observed.event_id], data={'kind': 'counterfactual_intervention',
                                             'intervention_kind': 'set_balance_fen',
                                             'hypothesis': request['hypothesis'],
                                             'source_event_ids': request['source_event_ids'],
                                             'intervention_sha256': digest,
                                             'before_balance_fen': 800, 'after_balance_fen': 50})])
    source_dir, replay_dir = tmp_path / 'source-export', tmp_path / 'replay-export'
    _finish_export(parent, source_dir)
    _finish_export(child, replay_dir)
    parent.close()
    child.close()
    with pytest.raises(IntegrityError, match='no actual visible result'):
        replay_windows(source_dir, replay_dir)


def _replay_parts(tmp_path, *, request_override=None, second_intervention=False,
                  intervention_visibility='evaluation', occurred_at=100):
    """建立父子导出与可定制的干预事件，返回认证入口与原始素材。

    request_override 同时改写冻结身份与事件数据（保持两者一致，用于单独触发
    可见性/边界校验而非身份校验）。
    """
    manifest = {'run_id': 'parent', 'source_group_id': 'parent', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'},
                'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    parent = FootprintStore.create(tmp_path / 'parent', manifest, {'version': 0, 'current_time': 100})
    observed = parent.commit(parent.state, [EventDraft(event_type='observation', occurred_at=100,
                                                       agent_id=1, data={'balance_fen': 800})])[0]
    reference = parent.assets.put_json({'state': 'parent'}, {'kind': 'fixture'})
    checkpoint = parent.checkpoint(parent.state, {'agent:1': reference, 'life': reference})
    request = {'kind': 'set_balance_fen', 'agent_id': 1, 'balance_fen': 50,
               'hypothesis': 'Less liquidity may reduce purchases.',
               'source_event_ids': [observed.event_id]}
    if request_override is not None:
        request = dict(request, **request_override)
    digest = hashlib.sha256(canonical_json(
        {'tool_name': 'counterfactual_intervention', 'request': request})).hexdigest()
    child = parent.restore(checkpoint.checkpoint_id, tmp_path / 'child',
                           {'branch_kind': 'counterfactual', 'intervention_sha256': digest})
    data = {'kind': 'counterfactual_intervention', 'intervention_kind': 'set_balance_fen',
            'hypothesis': request['hypothesis'],
            'source_event_ids': request['source_event_ids'],
            'intervention_sha256': digest,
            'before_balance_fen': 800, 'after_balance_fen': 50}
    drafts = [EventDraft(event_type='environment_event', occurred_at=occurred_at, agent_id=1,
                         visibility=intervention_visibility, caused_by=[observed.event_id],
                         data=data)]
    if second_intervention:
        drafts.append(EventDraft(event_type='environment_event', occurred_at=occurred_at,
                                 agent_id=1, visibility=intervention_visibility,
                                 data=dict(data)))
    child.commit({'version': child.state['version'] + 1, 'current_time': occurred_at}, drafts)
    child.commit({'version': child.state['version'] + 1, 'current_time': occurred_at},
                 [EventDraft(event_type='observation', occurred_at=occurred_at, agent_id=1,
                             data={'balance_fen': 50})])
    source_dir, replay_dir = tmp_path / 'source-export', tmp_path / 'replay-export'
    _finish_export(parent, source_dir)
    _finish_export(child, replay_dir)
    parent.close()
    child.close()
    return source_dir, replay_dir, observed.event_id


def test_replay_requires_exactly_one_intervention(tmp_path):
    """零个或多个干预事件都拒绝。"""
    none_dir = tmp_path / 'no-intervention'
    manifest = {'run_id': 'parent', 'source_group_id': 'parent', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'},
                'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    parent = FootprintStore.create(tmp_path / 'parent2', manifest, {'version': 0, 'current_time': 100})
    observed = parent.commit(parent.state, [EventDraft(event_type='observation', occurred_at=100,
                                                       agent_id=1, data={'balance_fen': 800})])[0]
    reference = parent.assets.put_json({'state': 'parent'}, {'kind': 'fixture'})
    checkpoint = parent.checkpoint(parent.state, {'agent:1': reference, 'life': reference})
    child = parent.restore(checkpoint.checkpoint_id, none_dir,
                           {'branch_kind': 'counterfactual', 'intervention_sha256': '0' * 64})
    child.commit(child.state, [EventDraft(event_type='observation', occurred_at=100, agent_id=1,
                                          data={'balance_fen': 800})])
    source_dir, replay_dir = tmp_path / 'source-export2', tmp_path / 'replay-export2'
    _finish_export(parent, source_dir)
    _finish_export(child, replay_dir)
    parent.close()
    child.close()
    with pytest.raises(IntegrityError, match='exactly one applied intervention'):
        replay_windows(source_dir, replay_dir)

    doubled_source, doubled, _ = _replay_parts(tmp_path / 'doubled', second_intervention=True)
    with pytest.raises(IntegrityError, match='exactly one applied intervention'):
        replay_windows(doubled_source, doubled)


def test_replay_intervention_must_match_frozen_identity():
    """干预事件的身份摘要与冻结分支不符时拒绝（unit 校验器直击）。"""
    from worldmock.delivery.replay import _validate_intervention

    branch = {'run_id': 'child', 'frozen_run_manifest': {'intervention_sha256': 'a' * 64}}
    event = {'run_id': 'child', 'agent_id': 1, 'occurred_at': 100, 'event_seq': 3,
             'data': {'kind': 'counterfactual_intervention',
                      'intervention_kind': 'set_balance_fen',
                      'hypothesis': 'Less liquidity may reduce purchases.',
                      'source_event_ids': ['event-1'],
                      'intervention_sha256': 'b' * 64, 'after_balance_fen': 50}}
    checkpoint = {'event_seq': 2, 'occurred_at': 100}
    with pytest.raises(IntegrityError, match='does not match the frozen branch'):
        _validate_intervention(branch, [event], checkpoint)


def test_replay_sources_must_stay_inside_parent_knowledge(tmp_path):
    """条件来源事件必须在父检查点可见范围内。"""
    source, replay, _ = _replay_parts(
        tmp_path, request_override={'source_event_ids': ['forged-event']})
    with pytest.raises(IntegrityError, match='outside the parent knowledge boundary'):
        replay_windows(source, replay)


def test_replay_intervention_must_apply_at_its_checkpoint(tmp_path):
    """干预必须发生在共同检查点边界（晚于检查点）。"""
    source, replay, _ = _replay_parts(tmp_path, occurred_at=101)
    with pytest.raises(IntegrityError, match='applied at its parent checkpoint'):
        replay_windows(source, replay)


def test_replay_parent_prefix_must_match_source_export(tmp_path):
    """分支事件前缀与父导出不一致时拒绝（unit 校验器直击）。"""
    from worldmock.delivery.replay import _validate_parent

    parent_manifest = {'run_id': 'parent', 'source_group_id': 'group'}
    branch = {'run_id': 'child', 'source_group_id': 'group',
              'frozen_run_manifest': {'branch_kind': 'counterfactual',
                                      'parent_run_id': 'parent',
                                      'parent_checkpoint_id': 'cp-1'}}
    original = [_plain_event(1), _checkpoint_event(2)]
    diverged = [_plain_event(1, payload={'balance_fen': 50}), _checkpoint_event(2)]
    with pytest.raises(IntegrityError, match='prefix differs'):
        _validate_parent(parent_manifest, original, branch, diverged)
    absent = [_plain_event(1)]
    with pytest.raises(IntegrityError, match='missing or duplicated'):
        _validate_parent(parent_manifest, absent, branch, [_plain_event(1)])


def _checkpoint_event(sequence):
    """构造父导出中的检查点事件行。"""
    return {'event_id': f'checkpoint-{sequence}', 'event_seq': sequence,
            'event_type': 'checkpoint_created', 'occurred_at': 100, 'agent_id': None,
            'data': {'checkpoint_id': 'cp-1'}}


def _plain_event(sequence, payload=None):
    """构造父导出中的普通事件行。"""
    return {'event_id': f'event-{sequence}', 'event_seq': sequence,
            'event_type': 'observation', 'occurred_at': 100, 'agent_id': 1,
            'data': payload if payload is not None else {'balance_fen': 800}}


# ---------------------------------------------------------------------------
# generation 拒绝分支
# ---------------------------------------------------------------------------


def _generation_product(tmp_path, *, variant='positive', duplicate=False, undeclared=False,
                        incomplete=False):
    """构造最小生成产物并返回 (manifest, main, stream)。"""
    from worldmock.delivery.generation import EXPECTED_VARIANTS

    roles = [('request', 0), ('accept', 1), ('complete', 2)]
    stream, main = [], []
    variants = [variant]
    if not incomplete:
        variants = list(EXPECTED_VARIANTS)
    if undeclared:
        variants = ['mystery-variant']
    for name in variants:
        owner = f'seq-{name}'
        member_ids = []
        for index, (role, timestamp) in enumerate(roles):
            identity = f'event-{name}-{index}' if not duplicate or index else 'event-dup'
            member_ids.append(identity)
            stream.append({'_meta': {'event': {'event_id': identity,
                                               'owner_sequence_id': owner, 'role': role,
                                               'logical_time_us': int(timestamp * 1e6)}},
                           'payload': {}})
        main.append({'_meta': {'id': owner, 'generation': {'variant': name,
                                                           'world_branch_id': 'b1'},
                               'stream': {'member_ids': member_ids}}})
    if incomplete:
        main[0]['_meta']['stream']['member_ids'].pop()
    return {'schema_version': 1, 'run_id': 'run', 'delivery_digest': '0' * 64,
            'artifacts_committed': True}, main, stream


def _fixture_catalog():
    """generation_fixtures 使用的最小目录与来源。"""
    catalog = {'initial_state': {'sla': {'max_wait_us': 60_000_000}}}
    source = {'run_id': 'run', 'source_event_ids': ['event-1']}
    return catalog, source


def test_generation_fixtures_reject_duplicate_event_identities(tmp_path):
    """生成事件身份重复即拒绝。"""
    from worldmock.delivery.generation import generation_fixtures

    manifest, main, stream = _generation_product(tmp_path, duplicate=True)
    catalog, source = _fixture_catalog()
    with pytest.raises(IntegrityError, match='duplicated'):
        generation_fixtures((manifest, main, stream), catalog, source)


def test_generation_fixtures_reject_undeclared_variants(tmp_path):
    """未在冻结规范声明的变体即拒绝。"""
    from worldmock.delivery.generation import generation_fixtures

    manifest, main, stream = _generation_product(tmp_path, undeclared=True)
    catalog, source = _fixture_catalog()
    with pytest.raises(IntegrityError, match='undeclared variant'):
        generation_fixtures((manifest, main, stream), catalog, source)


def test_generation_fixtures_reject_incomplete_membership(tmp_path):
    """变体或事件成员不完整即拒绝。"""
    from worldmock.delivery.generation import generation_fixtures

    manifest, main, stream = _generation_product(tmp_path, incomplete=True)
    catalog, source = _fixture_catalog()
    with pytest.raises(IntegrityError, match='incomplete'):
        generation_fixtures((manifest, main, stream), catalog, source)

