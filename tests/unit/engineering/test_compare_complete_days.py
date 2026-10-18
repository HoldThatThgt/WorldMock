"""掩码完整日比较：签名相等才算相等；不等时只有声明的规则让 compare_runs 判两种完整日都相等才算在规则下相等。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import scripts.compare_complete_days as complete_days

SIGNATURE = {"persona_sha256": "1" * 64, "config_sha256": "2" * 64, "environment_identity": {"map_sha256": "3" * 64},
             "seed": 20260105, "summary": {"artifacts": {"events.jsonl": "4" * 64, "quality.json": "5" * 64},
                                           "checkpoints_sha256": "6" * 64, "run_assets": [["7" * 64, 10]]}}


def _root(path: Path, lock: bytes = b"lock") -> Path:
    """@brief 一个最小检出：两份依赖锁、恢复出的 AS2 包目录与录制入口。"""
    (path / "requirements").mkdir(parents=True)
    (path / "uv.lock").write_bytes(lock)
    (path / "requirements" / "lock.txt").write_bytes(b"requirements")
    (path / complete_days.AS2_SOURCE).mkdir(parents=True)
    (path / complete_days.RECORDER).parent.mkdir(parents=True)
    (path / complete_days.RECORDER).write_text("", encoding="utf-8")
    return path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """@brief 两个检出，AS2 各从自己导入；录制写出预备好的签名。"""
    base, head = _root(tmp_path / "base"), _root(tmp_path / "head")
    signatures = {"base": {"activity": SIGNATURE, "callback": SIGNATURE},
                  "head": {"activity": copy.deepcopy(SIGNATURE), "callback": copy.deepcopy(SIGNATURE)}}
    monkeypatch.setattr(complete_days, "as2_location", lambda root, environment: [root / complete_days.AS2_SOURCE])

    def record(root, case_root):
        case_root.mkdir(parents=True)
        value = signatures[case_root.name]
        (case_root / "behaviour.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    monkeypatch.setattr(complete_days, "record_side", record)
    return base, head, signatures, tmp_path / "work"


def _main(base: Path, head: Path, work: Path, *rules: str) -> tuple[int, dict | None]:
    """@brief 跑一次比较，返回退出码与报告。"""
    code = complete_days.main(["--base-root", str(base), "--head-root", str(head), "--work", str(work),
                               *(item for rule in rules for item in ("--rule", rule))])
    path = work / "complete-day-behaviour.json"
    return code, json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def test_equal_signatures_give_equal(roots, capsys):
    base, head, _, work = roots
    code, report = _main(base, head, work)
    assert code == 0 and report["verdict"] == "equal"
    assert capsys.readouterr().out.strip() == "Complete-day behaviour: equal"


def test_different_signatures_without_rules_give_different(roots, monkeypatch):
    base, head, signatures, work = roots
    signatures["head"]["activity"]["summary"]["artifacts"]["quality.json"] = "8" * 64
    monkeypatch.setattr(complete_days, "_case_report", lambda work, kind, rules: {"verdict": "different"})
    code, report = _main(base, head, work)
    assert code == 1 and report["verdict"] == "different"
    assert report["kinds"]["activity"]["differing"] == ["summary.artifacts.quality.json"]
    assert report["kinds"]["activity"]["runs"] == {"verdict": "different"}
    assert report["kinds"]["callback"] == {"differing": [], "equal": True, "runs": None}


def test_dependency_lock_mismatch_is_refused(tmp_path, caplog):
    base, head = _root(tmp_path / "base"), _root(tmp_path / "head", lock=b"other")
    code, _ = _main(base, head, tmp_path / "work")
    assert code == 2 and "different dependency locks" in caplog.text


def test_root_without_recorder_is_refused(roots, caplog):
    base, head, _, work = roots
    (head / complete_days.RECORDER).unlink()
    code, _ = _main(base, head, work)
    assert code == 2 and "no behaviour recorder (tests/life/behaviour_day.py)" in caplog.text


def test_root_whose_as2_resolves_elsewhere_is_refused(roots, monkeypatch, caplog, tmp_path):
    base, head, _, work = roots
    monkeypatch.setattr(complete_days, "as2_location", lambda root, environment: [tmp_path / "elsewhere"])
    code, _ = _main(base, head, work)
    assert code == 2 and "imports AS2 from" in caplog.text


def test_an_unexpected_failure_exits_2_not_as_different(roots, monkeypatch, caplog):
    base, head, _, work = roots

    def broken(root, case_root):
        raise OSError("disk full")

    monkeypatch.setattr(complete_days, "record_side", broken)
    code, report = _main(base, head, work)
    assert code == 2 and report is None and "Complete-day comparison failed" in caplog.text


def test_different_signatures_equal_under_rules(roots, monkeypatch):
    base, head, signatures, work = roots
    signatures["head"]["callback"]["summary"]["checkpoints_sha256"] = "9" * 64
    verdicts = {"activity": "equal", "callback": "equal"}
    calls = []

    def case_report(work, kind, rules):
        calls.append((kind, tuple(rules)))
        return {"verdict": verdicts[kind]}

    monkeypatch.setattr(complete_days, "_case_report", case_report)
    code, report = _main(base, head, work, "checkpoint-zlib")
    assert code == 0 and report["verdict"] == "equal_under_rules" and report["rules"] == ["checkpoint-zlib"]
    assert calls == [("activity", ("checkpoint-zlib",)), ("callback", ("checkpoint-zlib",))]
    verdicts["callback"] = "different"
    code, report = _main(base, head, work.with_name("again"), "checkpoint-zlib")
    assert code == 1 and report["verdict"] == "different"
    assert report["kinds"]["callback"]["runs"] == {"verdict": "different"}
