import hashlib
import json

import pytest

from worldmock.delivery.pipeline import _file_inventory, deliver
from worldmock.errors import IntegrityError
from worldmock.life.models import WORLD_SCHEMA_VERSION


def test_manifest_inventory_binds_every_gate_and_consumer_file(tmp_path):
    names = ['labelkit/out/action.jsonl', 'labelkit/out/action.trace.jsonl', 'labelkit/out/rules.manifest.json',
             'labelkit/out/rules.stream.jsonl', 'labelkit/out/rules.report.json', 'labelkit/action.log',
             'training/dataset/train/data-00000.arrow', 'training-receipt.json', 'rule-expressions.json',
             'rule-results.json', 'training.jsonl', 'rules.jsonl']
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    inventory = _file_inventory(tmp_path)
    assert set(inventory) == set(names)
    for name in names:
        assert inventory[name] == {'size_bytes': len(name.encode()), 'sha256': hashlib.sha256(name.encode()).hexdigest()}


def test_invalid_source_never_creates_delivery_or_runs_model(tmp_path, monkeypatch):
    def reject(_directory): raise IntegrityError('Incomplete source export')
    def unexpected(*args): pytest.fail('Invalid source invoked a production model')
    monkeypatch.setattr('worldmock.delivery.pipeline.prepare_windows', reject)
    monkeypatch.setattr('worldmock.delivery.pipeline.run_labelkit', unexpected)
    with pytest.raises(IntegrityError): deliver(tmp_path / 'source', tmp_path / 'out', tmp_path)
    assert not (tmp_path / 'out').exists()


def _write_final_checkpoint(source, schema_version):
    source.mkdir(parents=True)
    manifest = {'checkpoint': {'state': {'schema_version': schema_version}}}
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


def test_delivery_rejects_export_whose_final_checkpoint_has_another_world_schema_version(tmp_path, monkeypatch):
    def unexpected(*args): pytest.fail('An export of another world schema version reached window construction')
    source = tmp_path / 'source'
    _write_final_checkpoint(source, WORLD_SCHEMA_VERSION - 1)
    monkeypatch.setattr('worldmock.delivery.pipeline.prepare_windows', unexpected)
    monkeypatch.setattr('worldmock.delivery.pipeline.run_labelkit', unexpected)
    with pytest.raises(IntegrityError, match='world schema version'):
        deliver(source, tmp_path / 'out', tmp_path)
    assert not (tmp_path / 'out').exists()


def test_delivery_gate_is_world_schema_eight_and_rejects_version_seven_exports(tmp_path, monkeypatch):
    """交付入口按世界结构版本 8 设门禁：版本 7 的导出在构造窗口前被拒绝（docs/dev/SPEC-persona-prompt.md 第七章）。"""
    def unexpected(*args): pytest.fail('A version 7 export reached window construction')
    assert WORLD_SCHEMA_VERSION == 8
    source = tmp_path / 'source'
    _write_final_checkpoint(source, 7)
    monkeypatch.setattr('worldmock.delivery.pipeline.prepare_windows', unexpected)
    with pytest.raises(IntegrityError, match='world schema version'):
        deliver(source, tmp_path / 'out', tmp_path)
    assert not (tmp_path / 'out').exists()


def test_delivery_checks_world_schema_before_preparing_windows(tmp_path, monkeypatch):
    def reached(_directory): raise IntegrityError('windows reached')
    source = tmp_path / 'source'
    _write_final_checkpoint(source, WORLD_SCHEMA_VERSION)
    monkeypatch.setattr('worldmock.delivery.pipeline.prepare_windows', reached)
    with pytest.raises(IntegrityError, match='windows reached'):
        deliver(source, tmp_path / 'out', tmp_path)
    assert not (tmp_path / 'out').exists()
