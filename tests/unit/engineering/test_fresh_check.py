"""@brief 新检出与双平台身份检查的实际命令边界。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import fresh_check as fresh




def test_fresh_checkout_disables_submodules_ssh_and_pip_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fresh.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    monkeypatch.setattr(fresh.subprocess, "check_output", lambda *args, **kwargs: "locked-commit\n")
    monkeypatch.setattr(fresh, "quick_check", lambda *args: {"passed": True, "code_sha256": "a" * 64})
    monkeypatch.setattr(fresh, "python_command", lambda: ["python3.12"])
    result = fresh.fresh_check("https://example.invalid/repo", "locked-commit", wheelhouse=tmp_path)
    assert "--no-recurse-submodules" in calls[0][0]
    assert calls[1][0] == ["git", "checkout", "--detach", "locked-commit"]
    assert calls[2][0][:2] == ["python3.12", "scripts/bootstrap.py"]
    assert "--use-uv" not in calls[2][0]
    for command, arguments in calls:
        assert arguments["env"]["GIT_SSH_COMMAND"] == "false"
        assert arguments["env"]["PIP_NO_CACHE_DIR"] == "1"
    assert result["source_commit"] == "locked-commit"
    expected = {"GIT_SSH_COMMAND": "false", "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_CONFIG_FILE": os.devnull, "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
    for key in expected:
        monkeypatch.delenv(key, raising=False)
    environment = fresh.check_environment()
    changed = {key: value for key, value in environment.items() if os.environ.get(key) != value}
    assert changed == expected
    assert {key: value for key, value in environment.items() if key not in changed} == dict(os.environ)


def test_failed_clone_stops_before_bootstrap(monkeypatch):
    calls = []

    def reject(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(fresh.subprocess, "run", reject)
    with pytest.raises(subprocess.CalledProcessError):
        fresh.fresh_check("https://example.invalid/repo", "locked-commit")
    assert len(calls) == 1


def test_quick_check_runs_static_directories_and_actual_identity(tmp_path, monkeypatch):
    calls, identities = [], []
    (tmp_path / ".work").mkdir()
    (tmp_path / ".work/environment-receipt.json").write_text(json.dumps({"passed": True}))
    monkeypatch.setattr(fresh.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(fresh.subprocess, "check_output",
                        lambda command, **kwargs: identities.append((command, kwargs)) or "a" * 64 + "\n")
    result = fresh.quick_check(tmp_path, "release", fresh.check_environment())
    assert result["scope"] == "quick" and result["code_sha256"] == "a" * 64
    assert result["checks"] == list(fresh.QUICK_TESTS)
    assert calls[0][7:] == ["tests/unit/engineering", "tests/integration/environment"]
    assert len(calls) == 1
    [(command, arguments)] = identities
    source = "from worldmock.runtime.framework.setup import runtime_code_sha256; print(runtime_code_sha256())"
    assert command == [str(fresh.environment_python(tmp_path / ".venv")), "-c", source]
    assert arguments["cwd"] == tmp_path


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
