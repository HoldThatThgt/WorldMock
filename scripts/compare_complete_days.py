#!/usr/bin/env python3
"""掩码完整日比较：在基与头两个检出里各录活动日与回调日，比较只屏蔽代码身份的行为签名。

@file     compare_complete_days.py
@brief    每一侧用自己的代码与测试助手录制（子进程只从这一侧的 src、根目录与恢复的 AS2 源码导入）。签名相等即
          equal；不等时若给了 --rule，就用 compare_runs 在声明的重建规则下逐项比较两侧的运行与导出，两种完整日都
          相等即 equal_under_rules；否则 different，并附上 compare_runs 报告给出叶子路径。设计见
          docs/design/01-architecture.md「工程验证」。

用法：
  python scripts/compare_complete_days.py --base-root BASE --head-root HEAD --work WORK [--rule NAME[=B:H] ...]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.compare_runs import Chain, ComparisonError, Options, Side, compare, parse_rules  # noqa: E402
from scripts.recorded_response_run import as2_location  # noqa: E402

logger = logging.getLogger(__name__)

#: 两份完整日。
KINDS = ("activity", "callback")
#: 两侧必须逐字节相同的依赖锁。
LOCKS = ("uv.lock", "requirements/lock.txt")
#: 恢复出的 AS2 包目录与行为录制入口（相对检出根）。
AS2_SOURCE = Path("vendor/agentsociety2/packages/agentsociety2/agentsociety2")
RECORDER = Path("tests/life/behaviour_day.py")
#: 一侧录制的时限（秒）。
RECORDING_TIMEOUT = 3600
#: 签名里逐项比较的键。
SIGNATURE_KEYS = ("persona_sha256", "config_sha256", "environment_identity", "seed")


def _refuse(message: str) -> ComparisonError:
    """@brief 记录英文错误日志并返回同文的异常，由调用方抛出。"""
    logger.error("%s", message)
    return ComparisonError(message)


def side_environment(root: Path) -> dict[str, str]:
    """@brief 一侧子进程只导入这一侧的代码：PYTHONPATH 是这一侧的 src、根目录与 AS2 包目录。"""
    root = Path(root).resolve()
    paths = (root / "src", root, root / AS2_SOURCE.parent)
    return {**os.environ, "PYTHONPATH": os.pathsep.join(str(path) for path in paths), "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1"}


def require_comparable_roots(base: Path, head: Path) -> None:
    """@brief 两个检出必须同锁、各有 AS2 源码与录制入口，且各自的 AS2 从自己的检出导入。"""
    for name in LOCKS:
        if (Path(base) / name).read_bytes() != (Path(head) / name).read_bytes():
            raise _refuse("Base and head use different dependency locks; record each side in its own environment")
    for root in (Path(base), Path(head)):
        if not (root / AS2_SOURCE).is_dir():
            raise _refuse(f"Root {root} has no restored AS2 source; run scripts/bootstrap.py there")
        if not (root / RECORDER).is_file():
            raise _refuse(f"Root {root} has no behaviour recorder (tests/life/behaviour_day.py)")
        locations = as2_location(root, side_environment(root))
        if len(locations) != 1 or not locations[0].resolve().is_relative_to(root.resolve()):
            location = ", ".join(str(item) for item in locations) or "nowhere"
            raise _refuse(f"Root {root} imports AS2 from {location}")


def record_side(root: Path, case_root: Path) -> dict:
    """@brief 在一侧检出里录两份完整日并读回签名。"""
    output = case_root / "behaviour.json"
    log = case_root.parent / f"{case_root.name}.log"
    command = [sys.executable, "-P", "-m", "tests.life.behaviour_day", "--case-root", str(case_root),
               "--output", str(output)]
    case_root.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        try:
            result = subprocess.run(command, cwd=root, env=side_environment(root), stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=RECORDING_TIMEOUT)
        except subprocess.TimeoutExpired:
            logger.exception("Recording timed out in %s", root)
            raise _refuse(f"Recording failed in {root}; see {log}") from None
    if result.returncode != 0 or not output.is_file():
        raise _refuse(f"Recording failed in {root}; see {log}")
    return json.loads(output.read_text(encoding="utf-8"))


def differing_keys(base: dict, head: dict) -> list[str]:
    """@brief 两份签名里不同的项（导出工件逐个列出）。"""
    keys = [key for key in SIGNATURE_KEYS if base.get(key) != head.get(key)]
    summaries = base.get("summary", {}), head.get("summary", {})
    artifacts = summaries[0].get("artifacts", {}), summaries[1].get("artifacts", {})
    keys += [f"summary.artifacts.{name}" for name in sorted(set(artifacts[0]) | set(artifacts[1]))
             if artifacts[0].get(name) != artifacts[1].get(name)]
    keys += [f"summary.{name}" for name in ("checkpoints_sha256", "run_assets")
             if summaries[0].get(name) != summaries[1].get(name)]
    return keys


def _case_report(work: Path, kind: str, rules: list[str]) -> dict:
    """@brief 用 compare_runs 比较两侧同一种完整日的运行与导出。"""
    sides = [Side(Chain.load([work / side / kind / "run"]), work / side / kind / "export") for side in ("base", "head")]
    return compare(sides[0], sides[1], Options(None, parse_rules(rules), False))


def _verdict(work: Path, signatures: tuple[dict, dict], rules: list[str]) -> tuple[str, dict]:
    """@brief 结论与每种完整日的比较结果。"""
    kinds = {}
    for kind in KINDS:
        differing = differing_keys(signatures[0][kind], signatures[1][kind])
        kinds[kind] = {"differing": differing, "equal": not differing, "runs": None}
    if all(item["equal"] for item in kinds.values()):
        return "equal", kinds
    if rules:
        for kind in KINDS:
            kinds[kind]["runs"] = _case_report(work, kind, rules)
        if all(item["runs"]["verdict"] == "equal" for item in kinds.values()):
            return "equal_under_rules", kinds
        return "different", kinds
    for kind, item in kinds.items():
        if not item["equal"]:
            item["runs"] = _case_report(work, kind, [])
    return "different", kinds


def main(argv: list[str] | None = None) -> int:
    """@brief 命令入口；0 相等（或在规则下相等）、1 不同、2 输入错误。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Compare two checkouts on the two scripted complete days")
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--head-root", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--rule", action="append", default=[])
    arguments = parser.parse_args(argv)
    base, head, work = arguments.base_root.resolve(), arguments.head_root.resolve(), arguments.work.resolve()
    try:
        parse_rules(arguments.rule)
        require_comparable_roots(base, head)
        if work.exists():
            raise _refuse(f"Work directory {work} already exists")
        signatures = (record_side(base, work / "base"), record_side(head, work / "head"))
        verdict, kinds = _verdict(work, signatures, arguments.rule)
    except ComparisonError:
        return 2
    report = {"base_root": str(base), "head_root": str(head), "kinds": kinds, "rules": list(arguments.rule),
              "verdict": verdict}
    work.mkdir(parents=True, exist_ok=True)
    (work / "complete-day-behaviour.json").write_bytes(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    print(f"Complete-day behaviour: {verdict}")
    return 1 if verdict == "different" else 0


if __name__ == "__main__":
    raise SystemExit(main())
