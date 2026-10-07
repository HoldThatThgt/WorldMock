#!/usr/bin/env python3
"""@brief 三层测试唯一运行入口：真实收集、文件分片、独立回执和完整性判定。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.bootstrap_sources import file_sha256
from scripts.bootstrap_environment import required_python_version, require_python_identity
DURATIONS = ROOT / "docs/dev/test-durations.json"
INPUT_ROOTS = ("src/worldmock", "custom/agents", "custom/envs", "tests", "scripts", "docs",
               "requirements", "patches")
GENERATED_REPORTS = {"docs/dev/test-durations.json", "docs/dev/coverage-report.json"}


def _run(command, **options):
    """@brief 全部子进程在仓库根目录运行，调用者负责检查退出码。"""
    return subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", **options)


def changed_paths(base):
    """@brief 比较基线覆盖暂存、未暂存和新增路径；Git 失败不能当作没有修改。"""
    paths = set()
    for arguments in (["diff", "--name-only", base], ["ls-files", "--others", "--exclude-standard"]):
        result = _run(["git", *arguments], capture_output=True)
        if result.returncode:
            raise RuntimeError(f"Git change selection failed: {result.stderr.strip()}")
        paths.update(result.stdout.splitlines())
    return sorted(paths)


def _add_digest_file(digest, relative, path):
    """@brief 把相对文件身份和真实字节一并加入摘要，避免文件边界混淆。"""
    name, content = relative.encode("utf-8"), path.read_bytes()
    digest.update(len(name).to_bytes(8, "big"))
    digest.update(name)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)


def environment_source_digest(environment_root):
    """@brief 绑定显式环境目录的真实文件字节，缺少输入立即失败。"""
    root = ROOT / Path(environment_root)
    paths = []
    for name in ("catalog", "corpora", "scenarios"):
        directory = root / name
        if not directory.is_dir():
            logging.error("Test environment input directory is missing: %s", name)
            raise FileNotFoundError(f"Test environment input directory is missing: {name}")
        files = [path for path in directory.rglob("*") if path.is_file()]
        if not files:
            logging.error("Test environment input directory is empty: %s", name)
            raise ValueError(f"Test environment input directory is empty: {name}")
        paths.extend(files)
    digest = hashlib.sha256()
    for path in sorted(paths):
        _add_digest_file(digest, path.relative_to(root).as_posix(), path)
    return digest.hexdigest()


def source_digest(environment_root=None):
    """@brief 绑定本次源码、测试、设计和冻结输入的真实字节，生成的计时与覆盖报告不进入输入。"""
    paths = {ROOT / name for name in ("tests/conftest.py", "pyproject.toml", "uv.lock", ".gitattributes", ".python-version")}
    for name in INPUT_ROOTS:
        paths.update(path for path in (ROOT / name).rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(ROOT).as_posix()
        if "__pycache__" in path.parts or relative in GENERATED_REPORTS:
            continue
        _add_digest_file(digest, relative, path)
    if environment_root is not None:
        digest.update(b"test-environment")
        digest.update(bytes.fromhex(environment_source_digest(environment_root)))
    return digest.hexdigest()


def require_unchanged_input(expected, environment_root=None):
    """@brief 任意被测输入在收集和执行之间变化都拒绝本次结果。"""
    if source_digest(environment_root) != expected:
        raise RuntimeError("Test source input changed during this run")


def collection_digest(records):
    """@brief 绑定完整节点、标记和依赖；数量一致不能代替身份一致。"""
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode("utf-8")).hexdigest()


def environment(workers):
    """@brief 记录可比较的当前解释器、机器和进程数。"""
    identity = {"implementation": platform.python_implementation(), "version": platform.python_version()}
    require_python_identity(identity, required_python_version(ROOT))
    return {"python": platform.python_version(), "platform": platform.platform(),
            "python_implementation": identity["implementation"],
            "python_version_file_sha256": file_sha256(ROOT / ".python-version"),
            "cpu_count": os.cpu_count(), "workers": workers}


def standard_reference(args, records, source):
    """@brief 复用同输入、同集合和同环境的实测标准墙钟；不匹配时直接实跑标准层。"""
    from tests.tier_support import selected

    path = ROOT / ".work/test-runs/standard-summary.json"
    digest = collection_digest([row for row in records if selected(row, "standard")])
    candidate = json.loads(path.read_text("utf-8")) if path.is_file() else None
    matches = candidate is not None and candidate["source_sha256"] == source
    matches = matches and candidate["collection_sha256"] == digest
    matches = matches and candidate["environment"] == environment(args.workers)
    if not matches:
        run(argparse.Namespace(tier="standard", workers=args.workers, base=args.base,
                               environment_root=args.environment_root))
        candidate = json.loads(path.read_text("utf-8"))
    if (candidate["source_sha256"] != source or candidate["collection_sha256"] != digest
            or candidate["environment"] != environment(args.workers)):
        raise RuntimeError("Measured standard tier identity differs from the full run")
    return candidate


def collect(directory, tier, changed, environment_root=None):
    """@brief 从真实 pytest 收集全部节点，由同一选择规则产生该层集合。"""
    path = directory / "collection.json"
    command = [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "--worldmock-manifest", str(path)]
    if environment_root is not None:
        command.extend(["--environment-root", str(environment_root)])
    result = _run(command, capture_output=True)
    if result.returncode or not path.is_file():
        raise RuntimeError(f"Test collection failed (rc={result.returncode}): {result.stdout}{result.stderr}")
    sys.path.insert(0, str(ROOT))
    from tests.tier_support import selected

    collection = json.loads(path.read_text(encoding="utf-8"))
    if collection["run_id"] != directory.name:
        raise RuntimeError("Test collection belongs to a different run")
    records = collection["records"]
    nodes = [row["nodeid"] for row in records]
    if not nodes or len(nodes) != len(set(nodes)):
        raise RuntimeError("Test collection is empty or contains duplicate nodes")
    return records, [row for row in records if selected(row, tier, changed)]


def duration_weights(path=DURATIONS):
    """@brief 已有计时只作为调度权重；新文件明确从一秒初始权重开始。"""
    if not path.is_file():
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    return {name: float(value) for name, value in report["files"].items()}


def shards(records, workers, weights):
    """@brief 以文件为原子分配到当前最轻的分片，保持每个文件节点连续。"""
    if workers < 1:
        raise ValueError("Worker count must be positive")
    grouped = defaultdict(list)
    for row in records:
        grouped[row["file"]].append(row["nodeid"])
    buckets = [{"files": [], "nodes": [], "weight": 0.0} for _ in range(min(workers, len(grouped)))]
    for file in sorted(grouped, key=lambda name: (-weights.get(name, 1.0), name)):
        bucket = min(buckets, key=lambda item: (item["weight"], len(item["files"])))
        bucket["files"].append(file)
        bucket["nodes"].extend(grouped[file])
        bucket["weight"] += weights.get(file, 1.0)
    return buckets


def run_shard(index, shard, directory, coverage, environment_root=None):
    """@brief 单个 pytest 子进程只执行指定节点，输出、临时目录和回执彼此隔离。"""
    target = directory / f"shard-{index}"
    target.mkdir()
    selected = target / "selected.json"
    selected.write_text(json.dumps({"run_id": directory.name, "nodes": shard["nodes"]},
                                   ensure_ascii=False), encoding="utf-8")
    receipt = target / "receipt.json"
    command = [sys.executable, "-m"]
    if coverage:
        command.extend(["coverage", "run", "-p", "--data-file", str(directory / ".coverage"), "-m"])
    start = perf_counter()
    # 测试临时目录不能继承被测仓库身份，各进程使用独立的系统临时目录。
    with TemporaryDirectory(prefix="worldmock-test-shard-") as temporary:
        # 父目录文件先收集，子目录文件连续排列，保持局部夹具的目录作用域。
        files = sorted(shard["files"], key=lambda name: (Path(name).parts[:-1], Path(name).name))
        command.extend(["pytest", "-q", *files, "--worldmock-selected", str(selected),
                        "--worldmock-receipt", str(receipt), "--basetemp", str(Path(temporary) / "pytest")])
        if environment_root is not None:
            command.extend(["--environment-root", str(environment_root)])
        with (target / "output.log").open("w", encoding="utf-8") as output:
            result = _run(command, stdout=output, stderr=subprocess.STDOUT)
    return {"index": index, "run_id": directory.name, "returncode": result.returncode,
            "wall_seconds": perf_counter() - start,
            "receipt": json.loads(receipt.read_text("utf-8")) if receipt.is_file() else None,
            "receipt_sha256": file_sha256(receipt) if receipt.is_file() else None,
            "selected_sha256": file_sha256(selected),
            "expected": shard["nodes"], "log": target.relative_to(ROOT).as_posix() + "/output.log"}


def _completed_test(node, value):
    """@brief 只接受 pytest 的真实完成阶段和结果，空值或自造结果不是通过。"""
    phases = value["phases"]
    if not {"setup", "teardown"} <= set(phases) or set(phases) - {"setup", "call", "teardown"}:
        raise RuntimeError(f"Test has no completion receipt: {node}")
    if "call" not in phases and phases["setup"]["outcome"] != "skipped":
        raise RuntimeError(f"Test has no call receipt: {node}")
    for phase in phases.values():
        outcome = phase["outcome"]
        if not isinstance(outcome, str) or outcome not in {"passed", "skipped", "failed"}:
            raise RuntimeError(f"Test receipt contains an invalid outcome: {node}")
        if outcome == "failed":
            raise RuntimeError(f"Test receipt contains a failure: {node}")


def validate_receipts(expected, results, run_id):
    """@brief 本次回执、收集与执行多重集必须恰好覆盖所选节点，任何重复、遗漏或失败均拒绝。"""
    if not expected or len(expected) != len(set(expected)):
        raise RuntimeError("Complete selected test collection is empty or duplicated")
    actual, selections, collections, tests = [], [], [], {}
    for result in results:
        receipt = result["receipt"]
        if result["returncode"] or receipt is None or receipt["exit_code"]:
            raise RuntimeError(f"Test worker failed or receipt is missing: shard {result['index']} ({result['log']})")
        if result["run_id"] != run_id or receipt["run_id"] != run_id:
            raise RuntimeError(f"Test worker receipt belongs to a different run: shard {result['index']}")
        selected, collected = result["expected"], receipt["collected"]
        if not selected or len(selected) != len(set(selected)):
            raise RuntimeError(f"Worker selection is empty or duplicated: shard {result['index']}")
        if Counter(collected) != Counter(selected):
            raise RuntimeError(f"Worker collection differs from its selected nodes: shard {result['index']}")
        for node, value in receipt["tests"].items():
            _completed_test(node, value)
            tests[node] = value
        selections.extend(selected)
        collections.extend(collected)
        actual.extend(receipt["tests"])
    if any(Counter(nodes) != Counter(expected) for nodes in (selections, collections, actual)):
        raise RuntimeError("Executed test nodes differ from the complete selected collection")
    return tests


def duration_report(records, tests, wall_seconds, environment, standard=None):
    """@brief 记录完整节点、阶段耗时、文件总量及默认快速层慢用例，性能不以少选代替。"""
    from tests.tier_support import selected

    files = defaultdict(float)
    fast_slow, standard_total, rows = [], 0.0, []
    for record in records:
        value = tests[record["nodeid"]]
        duration = value["duration"]
        files[record["file"]] += duration
        rows.append({**record, **value})
        if selected(record, "fast") and duration > 1.0:
            fast_slow.append({"nodeid": record["nodeid"], "seconds": duration})
        if selected(record, "standard"):
            standard_total += duration
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "environment": environment, "wall_seconds": wall_seconds, "nodes": rows,
            "files": dict(sorted(files.items())), "fast_over_one_second": fast_slow,
            "standard_total_seconds": standard_total,
            "standard_wall_seconds": None if standard is None else standard["wall_seconds"],
            "standard_reference": standard}


def coverage_gate(directory, environment_root=None):
    """@brief 合并本次正常源码覆盖数据，再由原覆盖率门禁判定，变异子进程不计入。"""
    data_file = directory / ".coverage"
    command = [sys.executable, "-m", "coverage", "combine", "--data-file", str(data_file), str(directory)]
    result = _run(command, capture_output=True)
    if result.returncode or not data_file.is_file():
        raise RuntimeError(f"Coverage combination failed: {result.stdout}{result.stderr}")
    path = directory / "coverage.json"
    result = _run([sys.executable, "-m", "coverage", "json", "--data-file", str(data_file), "-o", str(path)],
                  capture_output=True)
    if result.returncode or not path.is_file():
        raise RuntimeError(f"Coverage JSON is missing: {result.stdout}{result.stderr}")
    coverage = json.loads(path.read_text("utf-8"))
    coverage["run_id"] = directory.name
    path.write_text(json.dumps(coverage, indent=2) + "\n", encoding="utf-8")
    summary_path = directory / "summary.json"
    summary = json.loads(summary_path.read_text("utf-8"))
    if summary["run_id"] != directory.name:
        raise RuntimeError("Coverage summary belongs to a different run")
    summary["coverage_sha256"] = file_sha256(path)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    command = [sys.executable, "scripts/coverage_gate.py", "--from-json", str(path)]
    if environment_root is not None:
        command.extend(["--environment-root", str(environment_root)])
    result = _run(command)
    if result.returncode:
        raise RuntimeError("Coverage gate failed")


def run(args):
    """@brief 先冻结所选节点，再分片执行和核对，最后保留环境与耗时证据。"""
    start = perf_counter()
    directory = ROOT / ".work/test-runs" / str(uuid4())
    directory.mkdir(parents=True)
    source = source_digest(args.environment_root)
    changed = changed_paths(args.base)
    all_records, records = collect(directory, args.tier, changed, args.environment_root)
    if not records:
        raise RuntimeError("Selected test tier contains no tests")
    standard = standard_reference(args, all_records, source) if args.tier == "full" else None
    scheduled = shards(records, args.workers, duration_weights())
    print(f"[test-runner] {args.tier}: {len(records)}/{len(all_records)} nodes in {len(scheduled)} workers", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_shard, index, shard, directory, args.tier == "full", args.environment_root)
                   for index, shard in enumerate(scheduled)]
        results = [future.result() for future in futures]
    tests = validate_receipts([row["nodeid"] for row in records], results, directory.name)
    require_unchanged_input(source, args.environment_root)
    report = _summary(args, records, tests, results, changed, perf_counter() - start)
    report.update(source_sha256=source, run_id=directory.name)
    (directory / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.tier == "standard":
        reference = ROOT / ".work/test-runs/standard-summary.json"
        reference.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.tier == "full":
        duration = duration_report(records, tests, report["wall_seconds"], report["environment"], standard)
        DURATIONS.write_text(json.dumps(duration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        coverage_gate(directory, args.environment_root)
        require_unchanged_input(source, args.environment_root)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


def _summary(args, records, tests, results, changed, wall):
    """@brief 基本快速集合与因修改额外选中的引擎用例分别计时，并明确参考目标。"""
    from tests.tier_support import selected

    extra = [row["nodeid"] for row in records if args.tier == "fast" and not selected(row, "fast")]
    skipped = [node for node, value in tests.items()
               if any(phase["outcome"] == "skipped" for phase in value["phases"].values())]
    default_time = sum(value["duration"] for node, value in tests.items() if node not in extra)
    target = {"fast": 180, "standard": 2100}.get(args.tier)
    return {"tier": args.tier, "workers": args.workers, "selected_nodes": len(records), "changed": changed,
            "collection_sha256": collection_digest(records),
            "wall_seconds": wall, "test_total_seconds": sum(value["duration"] for value in tests.values()),
            "default_test_total_seconds": default_time, "changed_engine_nodes": extra,
            "changed_engine_total_seconds": sum(tests[node]["duration"] for node in extra),
            "skipped_nodes": skipped,
            "reference_target_seconds": target,
            "reference_target_exceeded": None if extra or skipped or args.workers != 6
            else target is not None and wall > target,
            "environment": environment(args.workers),
            "shards": [{key: value for key, value in result.items() if key not in {"receipt", "expected"}}
                       for result in results]}


def main():
    """@brief 解析跨平台运行参数，所有失败明确输出英文并以非零状态退出。"""
    parser = argparse.ArgumentParser(description="Run WorldMock test tiers with complete node receipts")
    parser.add_argument("--tier", choices=("fast", "standard", "full"), default="fast")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--environment-root", help="use this explicit environment directory for pytest inputs")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    try:
        return run(args)
    except (OSError, ValueError, TypeError, RuntimeError, KeyError) as error:
        print(f"[test-runner] ERROR: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
