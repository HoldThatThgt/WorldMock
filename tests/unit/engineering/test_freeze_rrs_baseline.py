"""@brief 完整候选失配、安装身份失配和原子冻结的生产入口行为门禁。"""

from __future__ import annotations

import copy
import hashlib
import json
import platform
from pathlib import Path

import pytest

from scripts import freeze_rrs_baseline as freezer

ROOT = Path(__file__).resolve().parents[3]


def _candidate():
    """@brief 构造当前候选形状，完整日的实际生成由独立整日入口验证。"""
    return {"persona_sha256": "a" * 64, "config_sha256": "f" * 64, "seed": 20260106,
            "environment_identity": {"environment_id": "artificial-test", "environment_version": "4",
                                     "rules_version": 6, "catalog_content_sha256": "1" * 64,
                                     "map_sha256": "2" * 64},
            "summary": {"artifacts": {"events.jsonl": "b" * 64},
                        "checkpoints_sha256": "c" * 64, "run_assets": [["d" * 64, 12]]}}


def _paths(tmp_path, first, second):
    """@brief 写两个输入和已有目标，便于检验拒绝前后字节。"""
    paths = (tmp_path / "first.json", tmp_path / "second.json", tmp_path / "baseline.json")
    paths[0].write_text(json.dumps(first), encoding="utf-8")
    paths[1].write_text(json.dumps(second), encoding="utf-8")
    paths[2].write_bytes(b"previous baseline\n")
    return paths


def test_matching_full_candidates_publish_complete_summary_and_actual_generation(tmp_path):
    """@brief 真正入口保留全部候选并记录实际平台和当前安装身份。"""
    candidate = _candidate()
    paths = _paths(tmp_path, candidate, candidate)
    result = freezer.freeze_matching_baseline(*paths)
    assert set(result) == {"persona_sha256", "config_sha256", "environment_identity", "seed", "summary", "generation"}
    assert {key: result[key] for key in candidate} == candidate
    assert json.loads(paths[2].read_text()) == result
    assert paths[2].read_bytes().endswith(b"\n")
    assert not list(tmp_path.glob(".pending-*"))
    generation = result["generation"]
    assert generation["os"] == platform.system()
    assert generation["architecture"] == platform.machine()
    assert generation["python_implementation"] == platform.python_implementation() == "CPython"
    assert generation["python_version"] == platform.python_version() == (ROOT / ".python-version").read_text().strip()
    for key, relative in (("python_version_file_sha256", ".python-version"),
                          ("requirements_lock_sha256", "requirements/lock.txt"), ("uv_lock_sha256", "uv.lock")):
        assert generation[key] == hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


@pytest.mark.parametrize("field", ["persona_sha256", "config_sha256", "environment_identity", "seed", "artifacts",
                                   "checkpoints_sha256", "run_assets"])
def test_mismatched_complete_day_candidates_preserve_existing_target(tmp_path, caplog, monkeypatch, field):
    """@brief 三个身份和摘要内每类证据任一失配都拒绝，旧目标不可被第一份覆盖。"""
    generation = {"os": "test", "architecture": "test", "python_implementation": "CPython",
                  "python_version": "3.12.13", "python_version_file_sha256": "a" * 64,
                  "requirements_lock_sha256": "b" * 64, "uv_lock_sha256": "c" * 64}
    monkeypatch.setattr(freezer, "_generation", lambda: generation)
    first, second = _candidate(), _candidate()
    if field in {"persona_sha256", "config_sha256"}:
        second[field] = "e" * 64
    elif field == "environment_identity":
        second[field]["map_sha256"] = "e" * 64
    elif field == "seed":
        second[field] += 1
    elif field == "artifacts":
        second["summary"][field]["events.jsonl"] = "e" * 64
    elif field == "checkpoints_sha256":
        second["summary"][field] = "e" * 64
    else:
        second["summary"][field][0][1] += 1
    paths = _paths(tmp_path, first, second)
    before = paths[2].read_bytes()
    with pytest.raises(ValueError, match="Complete-day candidates differ"):
        freezer.freeze_matching_baseline(*paths)
    assert paths[2].read_bytes() == before
    assert "Complete-day candidates differ" in caplog.text


@pytest.mark.parametrize("change", ["extra", "bool_seed", "bad_hash", "partial_summary", "partial_asset",
                                    "scenario_format", "partial_environment", "bool_rules", "old_rules"])
def test_invalid_current_candidate_is_rejected_before_publication(tmp_path, caplog, change):
    """@brief 输入身份和完整摘要形状有误时拒绝，不制造部分基线。"""
    first, second = _candidate(), _candidate()
    if change == "extra":
        second["generation"] = {}
    elif change == "bool_seed":
        second["seed"] = True
    elif change == "bad_hash":
        second["persona_sha256"] = "not a hash"
    elif change == "partial_summary":
        second["summary"].pop("checkpoints_sha256")
    elif change == "partial_asset":
        second["summary"]["run_assets"] = [["d" * 64]]
    elif change == "scenario_format":
        second = {"scenario_sha256": "a" * 64, "random_seed": 20260106, "summary": second["summary"]}
    elif change == "partial_environment":
        second["environment_identity"].pop("map_sha256")
    elif change == "old_rules":
        first["environment_identity"]["rules_version"] = second["environment_identity"]["rules_version"] = 5
    else:
        second["environment_identity"]["rules_version"] = True
    paths = _paths(tmp_path, first, second)
    with pytest.raises(ValueError, match="invalid current identity or summary"):
        freezer.freeze_matching_baseline(*paths)
    assert paths[2].read_bytes() == b"previous baseline\n"
    assert "invalid current identity or summary" in caplog.text


@pytest.mark.parametrize("change", ["passed", "python_implementation", "python_version",
                                     "python_version_file_sha256", "requirements_lock"])
def test_generation_rejects_nonmatching_bootstrap_identity(tmp_path, monkeypatch, caplog, change):
    """@brief 实际安装锁、解释器或引导结果不符时保留旧目标。"""
    install = tmp_path / "installation"
    for relative in (".python-version", "requirements/lock.txt", "uv.lock"):
        path = install / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / relative).read_bytes())
    receipt = copy.deepcopy(json.loads((ROOT / ".work/environment-receipt.json").read_text()))
    if change == "requirements_lock":
        receipt["dependency_locks"]["requirements/lock.txt"] = "0" * 64
    else:
        receipt[change] = False if change == "passed" else "different"
    receipt_path = install / ".work/environment-receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(freezer, "ROOT", install)
    candidate = _candidate()
    paths = _paths(tmp_path, candidate, candidate)
    with pytest.raises(ValueError, match="matching successful bootstrap receipt"):
        freezer.freeze_matching_baseline(*paths)
    assert paths[2].read_bytes() == b"previous baseline\n"
    assert "matching successful bootstrap receipt" in caplog.text
