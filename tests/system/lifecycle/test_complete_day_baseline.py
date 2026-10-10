"""完整自然日真实运行消费已经双候选核对的当前行为基线。"""

import json
from pathlib import Path

import pytest

from tests.life.day_baseline_runtime import record_complete_day

PROJECT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("kind", ["activity", "callback"])
def test_complete_day_matches_frozen_current_inputs_and_complete_evidence(monkeypatch, tmp_path, kind):
    actual = record_complete_day(monkeypatch, tmp_path / kind, PROJECT, kind)
    path = PROJECT / f"tests/fixtures/dayrun-{kind}-baseline.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == {key: value for key, value in expected.items() if key != "generation"}
