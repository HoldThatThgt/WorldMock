"""完整自然日真实运行消费已经双候选核对的当前行为基线。"""

import json
from pathlib import Path

import pytest

from tests.life.day_baseline_runtime import record_complete_day

PROJECT = Path(__file__).resolve().parents[3]

#: 每个脚本日的种子，以及夹具没有地图声明时由这个种子生成的地图摘要；地图摘要变了，就是「没有声明时地图不变」没有守住。
FROZEN_IDENTITY = {
    "activity": (20260105, "04645c19cfa4576bfe988cca8c41182d5fd67c67ceea1f671e247e130882a8c8"),
    "callback": (20260106, "a54b9d8d1ad1f70b2dbc0500d74392c6c8b120698d2b24c5bbc78fff1e9daa7e"),
}


@pytest.mark.parametrize("kind", ["activity", "callback"])
def test_complete_day_matches_frozen_current_inputs_and_complete_evidence(monkeypatch, tmp_path, kind):
    actual = record_complete_day(monkeypatch, tmp_path / kind, PROJECT, kind)
    path = PROJECT / f"tests/fixtures/dayrun-{kind}-baseline.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == {key: value for key, value in expected.items() if key != "generation"}


@pytest.mark.parametrize("kind", ["activity", "callback"])
def test_frozen_baseline_keeps_its_seed_generated_map_and_current_environment_identity(kind):
    """@brief 冻结的基线保持各自的种子与生成的地图，环境身份是夹具环境的现行版本和现行规则版本。"""
    path = PROJECT / f"tests/fixtures/dayrun-{kind}-baseline.json"
    frozen = json.loads(path.read_text(encoding="utf-8"))
    seed, map_sha256 = FROZEN_IDENTITY[kind]
    identity = frozen["environment_identity"]
    assert frozen["seed"] == seed
    assert identity["map_sha256"] == map_sha256
    assert (identity["environment_id"], identity["environment_version"], identity["rules_version"]) == (
        "artificial-test", "4", 5)
