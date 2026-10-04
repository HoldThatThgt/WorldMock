"""@brief 新检出与双平台身份检查的实际命令边界。"""

import json
import subprocess
from pathlib import Path

import pytest

from scripts import fresh_check as fresh


def test_fresh_checkout_disables_submodules_ssh_and_pip_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fresh.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    monkeypatch.setattr(fresh.subprocess, "check_output", lambda *args, **kwargs: "locked-commit\n")
    monkeypatch.setattr(fresh, "quick_check", lambda *args: {"passed": True, "code_sha256": "a" * 64})
    monkeypatch.setattr(fresh, "python312", lambda: ["python3.12"])
    result = fresh.fresh_check("https://example.invalid/repo", "locked-commit", wheelhouse=tmp_path)
    assert "--no-recurse-submodules" in calls[0][0]
    assert calls[1][0] == ["git", "checkout", "--detach", "locked-commit"]
    assert calls[2][0][:2] == ["python3.12", "scripts/bootstrap.py"]
    assert "--use-uv" not in calls[2][0]
    for command, arguments in calls:
        assert arguments["env"]["GIT_SSH_COMMAND"] == "false"
        assert arguments["env"]["PIP_NO_CACHE_DIR"] == "1"
    assert result["source_commit"] == "locked-commit"


def test_failed_clone_stops_before_bootstrap(monkeypatch):
    calls = []

    def reject(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(fresh.subprocess, "run", reject)
    with pytest.raises(subprocess.CalledProcessError):
        fresh.fresh_check("https://example.invalid/repo", "locked-commit")
    assert len(calls) == 1


def test_quick_check_runs_the_frozen_scope_and_actual_identity(tmp_path, monkeypatch):
    calls = []
    (tmp_path / ".work").mkdir()
    (tmp_path / ".work/environment-receipt.json").write_text(json.dumps({"passed": True}))
    monkeypatch.setattr(fresh.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(fresh.subprocess, "check_output", lambda command, **kwargs: "a" * 64 + "\n")
    result = fresh.quick_check(tmp_path, "release", fresh.check_environment())
    assert result["scope"] == "quick" and result["code_sha256"] == "a" * 64
    assert result["checks"] == list(fresh.QUICK_TESTS)
    assert {"tests/life/test_shared_rules.py", "tests/test_single_source.py", "tests/test_coverage_gate.py"} <= {
        item for item in calls[0]}
    if fresh.sys.platform.startswith("linux"):
        assert calls[1][-1] == "tests/corpora/test_textworld_runtime.py"


@pytest.mark.parametrize("mutation", ["code_sha256", "source_commit", "passed"])
def test_platform_comparison_rejects_mismatch_and_incomplete_evidence(tmp_path, mutation):
    good = {"code_sha256": "a" * 64, "source_commit": "commit", "passed": True}
    paths = [tmp_path / "linux.json", tmp_path / "windows.json"]
    for path in paths:
        path.write_text(json.dumps(good))
    assert fresh.compare_identities(paths)["passed"] is True
    changed = dict(good)
    changed[mutation] = False if mutation == "passed" else "different"
    paths[1].write_text(json.dumps(changed))
    with pytest.raises(RuntimeError, match="differs|must pass"):
        fresh.compare_identities(paths)
