"""@brief 同场景同种子的两个完整日候选相等后，原子发布真实机器身份基线。"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bootstrap_environment import require_python_identity, required_python_version
from scripts.bootstrap_sources import file_sha256
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import canonical_json


def _sha(value) -> bool:
    """@brief 检查候选中的原始 SHA-256 身份格式。"""
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _candidate(path: Path) -> dict:
    """@brief 读取完整摘要，拒绝旧格式、缺失身份及部分摘要。"""
    candidate = json.loads(path.read_text(encoding="utf-8"))
    valid = (isinstance(candidate, dict) and set(candidate) == {"scenario_sha256", "random_seed", "summary"}
             and _sha(candidate["scenario_sha256"]) and type(candidate["random_seed"]) is int)
    summary = candidate.get("summary") if isinstance(candidate, dict) else None
    valid = valid and isinstance(summary, dict) and set(summary) == {
        "artifacts", "checkpoints_sha256", "run_assets"}
    if valid:
        valid = (isinstance(summary["artifacts"], dict) and bool(summary["artifacts"])
                 and all(isinstance(key, str) and _sha(value) for key, value in summary["artifacts"].items())
                 and _sha(summary["checkpoints_sha256"]) and isinstance(summary["run_assets"], list)
                 and all(isinstance(row, list) and len(row) == 2 and _sha(row[0])
                         and type(row[1]) is int and row[1] >= 0 for row in summary["run_assets"]))
    if not valid:
        logging.error("Complete-day candidate has an invalid current identity or summary: %s", path.name)
        raise ValueError("Complete-day candidate has an invalid current identity or summary")
    return candidate


def _generation() -> dict:
    """@brief 记录实际机器与已通过引导的安装锁身份，拒绝解释器或安装证据不符。"""
    identity = {"implementation": platform.python_implementation(), "version": platform.python_version()}
    require_python_identity(identity, required_python_version(ROOT))
    version_sha = file_sha256(ROOT / ".python-version")
    requirements_sha = file_sha256(ROOT / "requirements/lock.txt")
    receipt = json.loads((ROOT / ".work/environment-receipt.json").read_text(encoding="utf-8"))
    if (receipt.get("passed") is not True or receipt.get("python_implementation") != identity["implementation"]
            or receipt.get("python_version") != identity["version"]
            or receipt.get("python_version_file_sha256") != version_sha
            or receipt.get("dependency_locks", {}).get("requirements/lock.txt") != requirements_sha):
        logging.error("Complete-day baseline requires a matching successful bootstrap receipt")
        raise ValueError("Complete-day baseline requires a matching successful bootstrap receipt")
    return {"os": platform.system(), "architecture": platform.machine(),
            "python_implementation": identity["implementation"], "python_version": identity["version"],
            "python_version_file_sha256": version_sha, "requirements_lock_sha256": requirements_sha,
            "uv_lock_sha256": file_sha256(ROOT / "uv.lock")}


def freeze_matching_baseline(first_path: Path, second_path: Path, target_path: Path) -> dict:
    """@brief 完整候选逐项一致才写入第三基线，任何失配均保留目标原字节。"""
    first, second = _candidate(first_path), _candidate(second_path)
    if first != second:
        logging.error("Complete-day candidates differ in scenario, seed or summary")
        raise ValueError("Complete-day candidates differ in scenario, seed or summary")
    result = {**first, "generation": _generation()}
    atomic_write(target_path, canonical_json(result) + b"\n")
    return result


def main() -> int:
    """@brief 发布两个实际候选的共同完整日基线，失败明确返回非零。"""
    parser = argparse.ArgumentParser(description="Freeze matching complete-day baseline candidates")
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        freeze_matching_baseline(args.first, args.second, args.target)
    except (OSError, ValueError, RuntimeError) as error:
        logging.error("Complete-day baseline publication failed: %s", error)
        return 1
    print("Complete-day baseline published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
