#!/usr/bin/env python3
"""@brief 在无子模块、无 SSH 和无 pip 缓存的新检出上验证固定环境。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bootstrap_environment import environment_python, python_command

QUICK_TESTS = ("tests/unit/engineering", "tests/integration/environment", "tests/integration/as2")


def check_environment() -> dict[str, str]:
    """@brief 新机器检查明确禁用 SSH、pip 缓存、uv 和真实模型调用。"""
    environment = dict(os.environ)
    environment.update(GIT_SSH_COMMAND="false", PIP_NO_CACHE_DIR="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
                       PIP_CONFIG_FILE=os.devnull, AGENTSOCIETY_LLM_API_KEY="offline-test-key",
                       AGENTSOCIETY_LLM_API_BASE="https://example.invalid", LITELLM_LOCAL_MODEL_COST_MAP="True")
    return environment


def quick_check(root: Path, profile: str, environment: dict) -> dict:
    """@brief 执行环境引导与 AS2 接入检查，并读取实际导入源码的运行身份。"""
    python = environment_python(root / ".venv")
    command = [str(python), "-m", "pytest", "-q", f"--environment-profile={profile}",
               "-p", "no:cacheprovider", *QUICK_TESTS]
    subprocess.run(command, cwd=root, env=environment, check=True)
    source = "from pathlib import Path; from worldmock.runtime.framework.setup import runtime_code_sha256; "
    source += "print(runtime_code_sha256(Path('.')))"
    identity = subprocess.check_output([str(python), "-c", source], cwd=root, env=environment, text=True).strip()
    receipt = json.loads((root / ".work/environment-receipt.json").read_text(encoding="utf-8"))
    return {"scope": "quick", "checks": list(QUICK_TESTS), "code_sha256": identity,
            "environment": receipt, "passed": bool(receipt["passed"])}


def fresh_check(repository: str, commit: str, profile: str = "release", wheelhouse: Path | None = None) -> dict:
    """@brief 指定提交经独立 clone 和同一引导入口检查，任何命令失败立即终止。"""
    environment = check_environment()
    with tempfile.TemporaryDirectory(prefix="worldmock-fresh-") as temporary:
        checkout = Path(temporary) / "repository"
        subprocess.run(["git", "clone", "--no-recurse-submodules", "--no-local", "--no-checkout",
                        repository, str(checkout)], env=environment, check=True)
        subprocess.run(["git", "checkout", "--detach", commit], cwd=checkout, env=environment, check=True)
        command = [*python_command(), "scripts/bootstrap.py", "--profile", profile]
        if wheelhouse is not None:
            command.extend(["--wheelhouse", str(wheelhouse.resolve())])
        subprocess.run(command, cwd=checkout, env=environment, check=True)
        result = quick_check(checkout, profile, environment)
        result["source_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=checkout, env=environment, text=True).strip()
        return result


def compare_identities(paths: list[Path]) -> dict:
    """@brief Ubuntu 与 Windows 必须属于同一提交、源码摘要和成功检查，不能只检查格式。"""
    results = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if len(results) != 2 or not all(result["passed"] for result in results):
        raise RuntimeError("Both platform checks must pass")
    for key in ("source_commit", "code_sha256"):
        if len({result[key] for result in results}) != 1:
            raise RuntimeError(f"Platform {key} differs")
    return {"passed": True, "source_commit": results[0]["source_commit"], "code_sha256": results[0]["code_sha256"]}


def main() -> int:
    """@brief 提供新检出检查和 CI 身份合并，写相对路径的检查收据。"""
    parser = argparse.ArgumentParser(description="Verify a new checkout without SSH or pip cache")
    parser.add_argument("--repository", default=str(ROOT))
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--profile", choices=("release", "dev"), default="release")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".work/fresh-check.json"))
    parser.add_argument("--compare", type=Path, nargs=2)
    arguments = parser.parse_args()
    try:
        if arguments.compare is not None:
            result = compare_identities(arguments.compare)
        else:
            result = fresh_check(arguments.repository, arguments.commit, arguments.profile, arguments.wheelhouse)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        logging.error("Fresh environment check failed: %s", error)
        return 1
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes((json.dumps(result, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
