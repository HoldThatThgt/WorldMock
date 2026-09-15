import hashlib

import pytest

from worldmock.delivery.pipeline import _file_inventory, deliver
from worldmock.errors import IntegrityError


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
