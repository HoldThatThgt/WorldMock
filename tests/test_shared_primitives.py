"""共享叶子规则通过原有生产消费者验证同源合同。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import Field, ValidationError

from worldmock.base_models import CurvePoint, EfficiencyFactor, FrozenModel
from worldmock.categories.common import CategoryError, DeepFrozenModel
from worldmock.categories.identity import _read_content_file
from worldmock.corpora.manifest import _validate_entry, safe_file
from worldmock.errors import IntegrityError, ProtocolError, reject_value
from worldmock.evaluation import behavior_metrics, goal_metrics, workload_metrics
from worldmock.footprints import assets, quality
from worldmock.footprints.timeline_labels import _load_render_day
from worldmock.footprints.intervals import body_settlements, valid_interval
from worldmock.footprints.models import Event, canonical_json, is_sha256, sha256_json
from worldmock.footprints.timeline_labels import local_day_bounds, split_local_days, timeline_label_report, window_days
from worldmock.life import content, day_planning, map_data, memory_evidence, work_review
from worldmock.model_feedback import schema_error_details
from worldmock.profile.coverage_apps import verified_phone_config


def _body(start, end, agent=1):
    return Event(run_id='run', event_id='body', event_seq=1, event_type='environment_event', occurred_at=10,
                 agent_id=agent, data={'kind': 'body_settlement', 'interval_start': start, 'interval_end': end},
                 recorded_at='2026-01-01T00:00:00Z', wall_elapsed_ms=0, state_version_before=0, state_version_after=1)


def test_structured_digest_and_raw_string_contracts_use_same_bytes():
    value = {'中文': ['é', 1], 'nested': {'b': 2, 'a': 1}}
    expected = '{"nested":{"a":1,"b":2},"中文":["é",1]}'.encode('utf-8')
    assert canonical_json(value) == expected
    assert sha256_json(value) == work_review._digest(value) == hashlib.sha256(expected).hexdigest()
    assert work_review._digest('汉字\r\n') == hashlib.sha256('汉字\r\n'.encode()).hexdigest()
    assert work_review._digest('text') != sha256_json('text')


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_digest_consumers_reject_nonfinite_values_with_log(value, caplog):
    for operation in (canonical_json, sha256_json, work_review._digest):
        with pytest.raises(ValueError):
            operation({'nested': value})
    assert sum('Canonical JSON encoding failed' in record.message for record in caplog.records) == 3


@pytest.mark.parametrize('value,valid', [('a'*64, True), ('0'*64, True), ('A'*64, False), ('g'*64, False),
                                       ('a'*63, False), ('a'*65, False), (None, False), (64, False)])
def test_sha256_format_is_strict(value, valid):
    assert is_sha256(value) is valid


@pytest.mark.parametrize('relative', ['../outside', '/outside', '\\outside', 'C:\\outside', 'C:outside',
                                      '\\\\server\\share\\outside', 'a/../outside', ''])
def test_safe_file_rejects_declared_path_before_access(tmp_path, monkeypatch, relative):
    """带盘符或根（含 Windows 上无盘符的根路径）、含父引用或为空的声明，在检查链接与文件之前就被拒绝。"""
    def accessed(path):
        raise AssertionError(f'declared path was accessed before rejection: {path}')

    monkeypatch.setattr(Path, 'is_symlink', accessed)
    monkeypatch.setattr(Path, 'is_file', accessed)
    with pytest.raises(IntegrityError, match='consumer path rejected'):
        safe_file(tmp_path, relative, 'consumer path rejected')


def test_safe_file_rejects_the_root_itself_at_the_boundary(tmp_path):
    with pytest.raises(IntegrityError, match='consumer path rejected'):
        safe_file(tmp_path, '.', 'consumer path rejected')


@pytest.mark.parametrize('relative', ['a/C:/b.txt', 'a/C:b.txt', 'zz/Q:/C:../top.txt', 'zz/Q:/C:b.txt'])
def test_safe_file_checks_drive_shaped_components_as_names_inside_the_root(tmp_path, monkeypatch, relative):
    """中间组件形似盘符时仍按根内的一个名字检查：链接检查只落在根以内，也不会借盘符相对路径改指根内别的文件。"""
    root = tmp_path / 'corpus'
    (root / 'a').mkdir(parents=True)
    (root / 'a' / 'b.txt').write_bytes(b'b')
    (root / 'top.txt').write_bytes(b'top')
    monkeypatch.chdir(root / 'a')
    checked = []
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: checked.append(path) or original(path))
    with pytest.raises(IntegrityError, match='consumer path rejected'):
        safe_file(root, relative, 'consumer path rejected')
    assert checked and all(root.resolve() in path.parents for path in checked)


def _reject_root_contained_reference(root, relative, payload, consumer):
    """验证内容身份正确时，原消费者仍按声明或链接规则拒绝根内文件。"""
    entry = SimpleNamespace(path=relative, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
    operations = {'safe_file': (lambda: safe_file(root, relative, 'consumer path rejected'), 'consumer path rejected'),
                  'corpus': (lambda: _validate_entry(root, entry), 'Corpus entry path is invalid'),
                  'map': (lambda: map_data._read_file(root, entry), 'Map file path is invalid')}
    operation, message = operations[consumer]
    with pytest.raises(IntegrityError, match=f'^{message}$'):
        operation()


@pytest.mark.parametrize('consumer', ['safe_file', 'corpus', 'map'])
def test_safe_file_consumers_reject_parent_reference_that_resolves_inside_root(tmp_path, consumer):
    root = tmp_path / 'corpus'
    root.mkdir()
    (root / 'child').mkdir()
    payload = b'{"normal":true}\n'
    (root / 'normal.json').write_bytes(payload)
    relative = 'child/../normal.json'
    assert (root / relative).resolve() == root / 'normal.json'
    normal = SimpleNamespace(path='normal.json', sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
    _validate_entry(root, normal)
    assert map_data._read_file(root, normal) == {'normal': True}
    _reject_root_contained_reference(root, relative, payload, consumer)


@pytest.mark.parametrize('link_kind', ['file', 'directory'])
@pytest.mark.parametrize('consumer', ['safe_file', 'corpus', 'map'])
def test_safe_file_consumers_reject_symlink_that_resolves_inside_root(tmp_path, monkeypatch, link_kind, consumer):
    root = tmp_path / 'corpus'
    root.mkdir()
    target = root / 'normal'
    target.mkdir()
    payload = b'{"normal":true}\n'
    (target / 'file.json').write_bytes(payload)
    link = root / 'alias'
    destination = target if link_kind == 'directory' else target / 'file.json'
    relative = 'alias/file.json' if link_kind == 'directory' else 'alias'
    try:
        link.symlink_to(destination, target_is_directory=link_kind == 'directory')
    except OSError as error:
        if getattr(error, 'winerror', None) != 1314:
            raise
        # Windows 无创建链接权限时模拟唯一链接标志，文件访问与消费者仍走真实路径。
        if link_kind == 'directory':
            link.mkdir()
            (link / 'file.json').write_bytes(payload)
        else:
            link.write_bytes(payload)
        original = Path.is_symlink
        monkeypatch.setattr(Path, 'is_symlink', lambda path: path == link or original(path))
    assert (root / relative).resolve().is_relative_to(root.resolve())
    assert (root / relative).read_bytes() == payload
    normal = SimpleNamespace(path='normal/file.json', sha256=hashlib.sha256(payload).hexdigest(),
                             size_bytes=len(payload))
    _validate_entry(root, normal)
    assert map_data._read_file(root, normal) == {'normal': True}
    _reject_root_contained_reference(root, relative, payload, consumer)


def test_original_safe_file_consumers_reject_symlink_before_hash_or_schema(tmp_path, caplog, monkeypatch):
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    root = tmp_path / 'corpus'
    root.mkdir()
    (root / 'lifestyle').mkdir()
    links = [root / 'content.json', root / 'lifestyle/items.json']
    unavailable = set()
    for link in links:
        try:
            link.symlink_to(outside)
        except OSError as error:
            if getattr(error, 'winerror', None) != 1314:
                raise
            link.write_bytes(outside.read_bytes())
            unavailable.add(link)
    if unavailable:
        # Windows 无创建链接权限时只模拟文件系统链接标志，生产消费者仍真实拒绝此路径。
        original = Path.is_symlink
        monkeypatch.setattr(Path, 'is_symlink', lambda path: path in unavailable or original(path))
    entry = SimpleNamespace(path='content.json', sha256='0'*64, size_bytes=2)
    scenario = SimpleNamespace(phone_simulation_config='content.json', phone_simulation_config_sha256='0'*64)
    for operation in (lambda: _validate_entry(root, entry), lambda: map_data._read_file(root, entry),
                      lambda: verified_phone_config(scenario, root), lambda: content.load_lifestyle_items(root, {})):
        with pytest.raises(IntegrityError, match='path is invalid'):
            operation()
    caplog.clear()
    with pytest.raises(CategoryError, match='CATALOG_IDENTITY_READ_FAILED'):
        _read_content_file(root, 'content.json')
    assert len(caplog.records) == 1
    assert 'CATALOG_IDENTITY_READ_FAILED' in caplog.records[0].message


def test_safe_file_rejects_parent_symlink_and_accepts_normal_file(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'file').write_bytes(b'actual')
    link = root / 'link'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if getattr(error, 'winerror', None) != 1314:
            raise
        # Windows 权限边界只模拟父目录链接标志，不跳过消费者的安全拒绝。
        link.mkdir()
        original = Path.is_symlink
        monkeypatch.setattr(Path, 'is_symlink', lambda path: path == link or original(path))
    with pytest.raises(IntegrityError):
        safe_file(root, 'link/file', 'bad path')
    (root / 'normal').write_bytes(b'actual')
    assert safe_file(root, 'normal', 'bad path').read_bytes() == b'actual'


def test_atomic_publication_order_and_failed_replace_cleanup(tmp_path, monkeypatch):
    destination = tmp_path / 'nested/result.json'
    calls = []
    fsync, replace = assets.os.fsync, assets.os.replace
    monkeypatch.setattr(assets.os, 'fsync', lambda descriptor: (calls.append('fsync'), fsync(descriptor))[-1])
    monkeypatch.setattr(assets.os, 'replace', lambda source, target: (calls.append('replace'), replace(source, target))[-1])
    assets.atomic_write(destination, b'{"text":"ok"}\n')
    assert destination.read_bytes() == b'{"text":"ok"}\n'
    assert calls == (['fsync', 'replace'] if assets.os.name == 'nt' else ['fsync', 'replace', 'fsync'])
    monkeypatch.setattr(assets.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('publication failed')))
    with pytest.raises(OSError, match='publication failed'):
        assets.atomic_write(destination, b'new')
    assert destination.read_bytes() == b'{"text":"ok"}\n'
    assert not list(destination.parent.glob('.pending-*'))


@pytest.mark.parametrize('invalid', [True, False, float('nan'), float('inf'), -1])
def test_curve_models_shared_by_life_and_catalog_reject_invalid_numbers(invalid):
    from worldmock.catalog.models import CurvePoint as CatalogCurve
    from worldmock.life.models import CurvePoint as LifeCurve
    assert CatalogCurve is LifeCurve is CurvePoint
    for model in (CatalogCurve, LifeCurve):
        for key in ('input_value', 'output_value'):
            with pytest.raises(ValidationError):
                model(**{'input_value': 1, 'output_value': 1, key: invalid})


def test_efficiency_factor_blank_and_empty_constraints_and_freeze_distinction():
    for fields in ({'input_resource': ' \n\t', 'curve': [{'input_value': 0, 'output_value': 1}]},
                   {'input_resource': 'energy', 'curve': []}):
        with pytest.raises(ValidationError):
            EfficiencyFactor(**fields)
    class Plain(FrozenModel):
        values: dict[str, list[int]] = Field(default_factory=dict)
    class Deep(DeepFrozenModel):
        values: dict[str, list[int]] = Field(default_factory=dict)
    plain, deep = Plain(values={'a': [1]}), Deep(values={'a': [1]})
    plain.values['a'].append(2)
    with pytest.raises(TypeError):
        deep.values['a'] = (2,)
    with pytest.raises(ValidationError):
        plain.values = {}
    with pytest.raises(ValidationError):
        Plain(unknown=True)


@pytest.mark.parametrize('start,end', [(0.0, 10), (0, 10.0), (True, 10), (0, False),
                                       (float('nan'), 10), (0, float('inf')), (10, 10), (20, 10)])
def test_interval_consumers_do_not_count_invalid_settlements(start, end):
    event = _body(start, end)
    row = event.model_dump(mode='python')
    assert not valid_interval(start, end)
    assert quality._activity_quality([], [event])['body_settlements_missing_interval'] == 1
    assert behavior_metrics._person_elapsed_minutes([row], [])['missing_interval_events'] == 1
    agent = workload_metrics.AgentWorkday(None, 'home', None, None, ())
    timeline = workload_metrics._timeline([row], agent, __import__('zoneinfo').ZoneInfo('UTC'))
    assert (timeline.start, timeline.end) == (0, 0)
    progress = {**row, 'event_type': 'activity_progress', 'activity_id': 'activity',
                'data': {**row['data'], 'activity': {'activity_id': 'activity', 'agent_id': 1,
                                                   'kind': 'chat', 'status': 'active', 'started_at': 0}}}
    assert goal_metrics._progress_evidence(progress) is None


def test_body_settlement_subject_filter_is_shared_and_strict():
    events = [_body(0, 10, 1), _body(0, 20, 2), _body(0, 30, None)]
    assert body_settlements(events, 1) == events[:1]
    assert body_settlements([event.model_dump() for event in events], 2) == [events[1].model_dump()]
    assert quality._activity_quality([], events)['person_elapsed_seconds_by_agent'] == {'1': 10, '2': 20}
    rows = [event.model_dump() for event in events]
    assert behavior_metrics._person_elapsed_minutes(rows, [])['per_agent_minutes'] == {'1': round(10/60, 6),
                                                                                     '2': round(20/60, 6)}


@pytest.mark.parametrize('zone,day,length', [('America/New_York', date(2026, 3, 8), 23*3600),
                                            ('America/New_York', date(2026, 11, 1), 25*3600),
                                            ('Europe/London', date(2026, 1, 5), 24*3600)])
def test_renderer_and_label_report_use_manifest_timezone_and_real_local_day(zone, day, length):
    start, end = local_day_bounds(day, zone)
    assert end-start == length
    window = {'start': start, 'end': end, 'timezone': zone}
    assert window_days(window) == [day]
    assert split_local_days(start, end, zone) == [(day, start, end)]
    event = _body(start, end).model_copy(update={'event_type': 'activity_progress', 'occurred_at': end,
        'activity_id': 'activity', 'data': {'interval_start': start, 'interval_end': end,
                                           'activity': {'activity_id': 'activity', 'kind': 'sleep', 'status': 'active'}}})
    renderer = _load_render_day()
    rows = renderer.day_rows([event.model_dump(mode='json')], {}, day, zone)
    assert rows[0]['interval'] == '00:00–00:00'
    assert rows[0]['duration'] == f'{length//3600} 小时'
    assert timeline_label_report([event], {'window': window})['rows_checked'] == 1
    zone_info = __import__('zoneinfo').ZoneInfo(zone)
    assert behavior_metrics._agent_days([{'agent_id': 1, 'occurred_at': start},
                                        {'agent_id': 1, 'occurred_at': end-1}], '1', zone_info) == 1
    body = _body(start, end).model_dump()
    agent = workload_metrics.AgentWorkday(None, 'home', None, None, ())
    timeline = workload_metrics._timeline([body], agent, zone_info)
    assert workload_metrics._days(timeline, zone_info) == [day]


def test_independent_reflection_snapshot_is_valid_across_memory_quality_and_behavior():
    episode = {'id': 'reflection', 'type': 'reflection', 'text': '睡前想了想',
               'epistemic_status': 'subjective', 'source': 'reflection'}
    assert memory_evidence.valid_subjective_snapshot(episode)
    assert memory_evidence.valid_subjective_snapshots([episode], ['reflection'])
    assert quality.valid_subjective_snapshots([episode], ['reflection'])
    assert behavior_metrics._episode_pairs([{'event_type': 'memory_updated', 'data': {'episodes': [episode]}}])[-1] == 0
    assert not memory_evidence.valid_subjective_snapshot({**episode, 'source': 'activity_conclusion'})


def test_value_error_rejection_logs_once_and_preserves_message(caplog):
    with pytest.raises(ValueError, match='exact reason'):
        reject_value('exact reason')
    assert [record.message for record in caplog.records] == ['exact reason']
