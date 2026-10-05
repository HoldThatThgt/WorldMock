#!/usr/bin/env python3
"""覆盖率门禁工具：运行测试套件，计算分支覆盖率与公共接口覆盖率并判定。

@file     coverage_gate.py
@brief    WorldMock 覆盖率门禁；三项覆盖率口径定义见 docs/dev/coverage-baseline.md。
@note     机器判定两项：整体分支覆盖率 >= 85%，且公共接口未覆盖数为 0；
          功能 spec 测试用例覆盖率为映射义务，不由本脚本计算。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BRANCH_THRESHOLD = 85.0
TEST_COMMAND = (sys.executable, "scripts/run_tests.py", "--tier", "full", "--workers", "6")
PRODUCTION_ROOTS = ("src/worldmock", "custom/agents", "custom/envs")
REPORT_PATH = REPO_ROOT / "docs" / "dev" / "coverage-report.json"


@dataclass
class FileBranchStat:
    """@brief 单个生产文件的分支覆盖统计。"""

    file: str
    covered: int
    missing: int

    @property
    def total(self) -> int:
        """@brief 该文件分支总数。"""
        return self.covered + self.missing

    @property
    def percent(self) -> float | None:
        """@brief 分支覆盖率百分数（两位小数）；文件无分支时为 None。"""
        if self.total == 0:
            return None
        return round(self.covered / self.total * 100.0, 2)


@dataclass
class BranchSummary:
    """@brief 汇总全部生产文件后的整体分支覆盖统计。"""

    covered: int
    missing: int
    per_file: list[FileBranchStat]

    @property
    def percent(self) -> float:
        """@brief 整体分支覆盖率百分数（两位小数）；无任何分支数据时按 0.0 失败关闭。"""
        total = self.covered + self.missing
        if total == 0:
            return 0.0
        return round(self.covered / total * 100.0, 2)


@dataclass
class InterfaceDef:
    """@brief 生产文件中一个公共接口定义（模块级函数或类体方法）。"""

    file: str
    line: int
    end_line: int
    name: str
    kind: str


@dataclass
class InterfaceGap:
    """@brief 函数体存在未执行行的公共接口（门禁缺口）。"""

    file: str
    line: int
    name: str
    kind: str


def normalize_rel_path(path: str) -> str:
    """@brief 规范化 coverage JSON 中的文件路径为仓库相对 POSIX 路径。"""
    rel = path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def compute_branch_summary(cov_data: dict) -> BranchSummary:
    """@brief 汇总 coverage JSON 中全部文件的分支覆盖数据。

    @param cov_data  coverage json 命令导出的字典。
    @return          整体与分文件分支统计（covered/missing 均为分支计数）。
    """
    per_file: list[FileBranchStat] = []
    covered_total = 0
    missing_total = 0
    for raw_path, info in sorted(cov_data.get("files", {}).items()):
        summary = info.get("summary", {})
        covered = int(summary.get("covered_branches", 0))
        missing = int(summary.get("missing_branches", 0))
        covered_total += covered
        missing_total += missing
        per_file.append(FileBranchStat(normalize_rel_path(raw_path), covered, missing))
    return BranchSummary(covered_total, missing_total, per_file)


def iter_production_files(base: Path, roots: tuple[str, ...]) -> list[tuple[Path, str]]:
    """@brief 枚举生产根目录下全部 .py 文件，返回 (绝对路径, 相对路径) 列表。

    @param base   仓库根目录（真实运行时为 REPO_ROOT，测试时为临时目录）。
    @param roots  生产代码根目录名（相对 base）。
    @return       排序后的 (文件绝对路径, 相对 POSIX 路径) 列表；跳过 __pycache__。
    """
    results: list[tuple[Path, str]] = []
    for root_name in roots:
        root = base / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            results.append((path, path.relative_to(base).as_posix()))
    return results


def _extract_class_methods(cls: ast.ClassDef, rel_path: str) -> list[InterfaceDef]:
    """@brief 收集单个类体内直接定义的公共方法（下划线开头与嵌套函数不计）。"""
    methods: list[InterfaceDef] = []
    for child in cls.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not child.name.startswith("_"):
                methods.append(
                    InterfaceDef(rel_path, child.lineno, child.end_lineno, child.name, "method")
                )
    return methods


def extract_public_interfaces(tree: ast.Module, rel_path: str) -> list[InterfaceDef]:
    """@brief 收集模块顶层与类体中的公共 def/async def 定义。

    @param tree      源文件解析后的 AST。
    @param rel_path  仓库相对路径（用于报告定位）。
    @return          公共接口定义列表；下划线开头（含 dunder）的函数不计。
    """
    defs: list[InterfaceDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                defs.append(
                    InterfaceDef(rel_path, node.lineno, node.end_lineno, node.name, "function")
                )
        elif isinstance(node, ast.ClassDef):
            defs.extend(_extract_class_methods(node, rel_path))
    return defs


def parse_production_source(path: Path, rel_path: str) -> ast.Module:
    """@brief 读取并解析生产源文件；语法错误时抛出带文件定位的 RuntimeError。"""
    source = path.read_text(encoding="utf-8")
    try:
        return ast.parse(source)
    except SyntaxError as error:
        message = f"production file failed to parse: {rel_path} (line {error.lineno}: {error.msg})"
        raise RuntimeError(message) from error


def build_missing_index(cov_data: dict) -> dict[str, set[int]]:
    """@brief 构建 文件相对路径 -> 未执行行号集合 的索引。"""
    index: dict[str, set[int]] = {}
    for raw_path, info in cov_data.get("files", {}).items():
        rel = normalize_rel_path(raw_path)
        index[rel] = {int(line) for line in info.get("missing_lines", [])}
    return index


def lookup_missing_lines(index: dict[str, set[int]], rel_path: str) -> set[int] | None:
    """@brief 查找文件的未执行行集合；文件不在 coverage 结果中时返回 None。"""
    if rel_path in index:
        return index[rel_path]
    suffix = "/" + rel_path
    for key in index:
        if key.endswith(suffix):
            return index[key]
    return None


def find_uncovered_interfaces(
    base: Path, roots: tuple[str, ...], cov_data: dict
) -> list[InterfaceGap]:
    """@brief 找出函数体存在未执行行的公共接口。

    @param base      仓库根目录。
    @param roots     生产代码根目录名（相对 base）。
    @param cov_data  coverage json 导出的字典。
    @return          未覆盖公共接口缺口列表；文件未被 coverage 测量时其全部
                     公共接口计为未覆盖（失败关闭，防静默漏检）。
    """
    index = build_missing_index(cov_data)
    gaps: list[InterfaceGap] = []
    for path, rel_path in iter_production_files(base, roots):
        tree = parse_production_source(path, rel_path)
        missing = lookup_missing_lines(index, rel_path)
        for defn in extract_public_interfaces(tree, rel_path):
            body_range = range(defn.line, defn.end_line + 1)
            if missing is None or any(line in missing for line in body_range):
                gaps.append(InterfaceGap(defn.file, defn.line, defn.name, defn.kind))
    return gaps


def judge_gate(
    branch_percent: float, uncovered_count: int, threshold: float = BRANCH_THRESHOLD
) -> bool:
    """@brief 门禁判定：分支覆盖率达到阈值且未覆盖公共接口数为 0。"""
    return branch_percent >= threshold and uncovered_count == 0


def run_test_suite(shards: int = 6) -> int:
    """@brief 使用当前解释器调用唯一全量入口；分片数就是该入口的进程数。"""
    command = (*TEST_COMMAND[:-1], str(shards))
    print(f"[coverage-gate] running: {' '.join(command)}", flush=True)
    return subprocess.call(command, cwd=REPO_ROOT)


def _runner():
    """@brief 复用唯一测试入口的集合身份与回执判定。"""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts import run_tests

    return run_tests


def _read_worker_receipts(directory: Path, shards: list[dict]) -> list[dict]:
    """@brief 读取同一运行目录内的节点选择和真实回执，缺失或重复分片均报错。"""
    indices = [shard["index"] for shard in shards]
    if not indices or sorted(indices) != list(range(len(indices))):
        raise RuntimeError("Full run worker indices are empty or duplicated")
    results = []
    for shard in shards:
        target = directory / f"shard-{shard['index']}"
        selection = json.loads((target / "selected.json").read_text("utf-8"))
        if selection["run_id"] != directory.name:
            raise RuntimeError("Full run worker selection belongs to a different run")
        expected = selection["nodes"]
        if not expected:
            raise RuntimeError("Full run contains an empty worker selection")
        receipt_path = target / "receipt.json"
        selected_path = target / "selected.json"
        if (_runner().file_sha256(receipt_path) != shard["receipt_sha256"]
                or _runner().file_sha256(selected_path) != shard["selected_sha256"]):
            raise RuntimeError("Full run worker artifacts differ from their original bytes")
        receipt = json.loads(receipt_path.read_text("utf-8"))
        results.append({**shard, "expected": expected, "receipt": receipt})
    return results


def full_run_receipts(directory: Path) -> tuple[dict, dict]:
    """@brief 将本次覆盖数据绑定到完整收集、源码输入和每个实际执行节点。"""
    runner = _runner()
    collection = json.loads((directory / "collection.json").read_text("utf-8"))
    summary = json.loads((directory / "summary.json").read_text("utf-8"))
    if collection["run_id"] != directory.name or summary["run_id"] != directory.name:
        raise RuntimeError("Full run artifacts belong to a different run")
    if runner.file_sha256(directory / "coverage.json") != summary["coverage_sha256"]:
        raise RuntimeError("Coverage report differs from the original full run bytes")
    records = collection["records"]
    nodes = [row["nodeid"] for row in records]
    if not nodes or len(nodes) != len(set(nodes)):
        raise RuntimeError("Full run collection is empty or contains duplicate nodes")
    if summary["tier"] != "full" or summary["selected_nodes"] != len(nodes):
        raise RuntimeError("Coverage requires the complete full tier collection")
    if summary["collection_sha256"] != runner.collection_digest(records):
        raise RuntimeError("Full run collection identity differs from its summary")
    if summary["environment"] != runner.environment(summary["workers"]):
        raise RuntimeError("Full run environment differs from the current gate")
    runner.require_unchanged_input(summary["source_sha256"])
    tests = runner.validate_receipts(nodes, _read_worker_receipts(directory, summary["shards"]), directory.name)
    skipped = sum(any(phase["outcome"] == "skipped" for phase in value["phases"].values())
                  for value in tests.values())
    return {"tests": len(tests), "failures": 0, "errors": 0, "skipped": skipped}, summary


def build_report(
    branch: BranchSummary,
    gaps: list[InterfaceGap],
    passed: bool,
    command: str,
    snapshot_id: str,
    manifest_hash: str,
    source_identity: str,
) -> dict:
    """@brief 组装写往 docs/dev/coverage-report.json 的门禁报告字典。
    
    @param snapshot_id      本次测试运行的唯一标识符。
    @param manifest_hash    manifest 文件或覆盖数据的 SHA-256 哈希。
    @param source_identity  被测源代码的标识（如 Git commit hash）。
    """
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": command,
        "evidence_manifest": {
            "snapshot_id": snapshot_id,
            "manifest_hash": manifest_hash,
            "source_identity": source_identity,
        },
        "thresholds": {"branch_coverage_percent_min": BRANCH_THRESHOLD},
        "branch_coverage": {
            "covered_branches": branch.covered,
            "missing_branches": branch.missing,
            "total_branches": branch.covered + branch.missing,
            "percent": branch.percent,
            "passed": branch.percent >= BRANCH_THRESHOLD,
        },
        "per_file_branch": [
            {
                "file": stat.file,
                "covered_branches": stat.covered,
                "missing_branches": stat.missing,
                "percent": stat.percent,
            }
            for stat in branch.per_file
        ],
        "uncovered_public_interfaces": [
            {"file": gap.file, "line": gap.line, "name": gap.name, "kind": gap.kind}
            for gap in gaps
        ],
        "passed": passed,
    }


def write_report(report: dict, path: Path) -> None:
    """@brief 将门禁报告以 JSON 写入目标路径（自动创建父目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[coverage-gate] report written: {path}")


def get_git_commit_hash() -> str:
    """@brief 获取实际提交身份；不能以 unknown 代替失败的证据来源。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"[coverage-gate] ERROR: Source identity lookup failed: {error}", file=sys.stderr)
        raise RuntimeError("Source identity lookup failed") from error
    if result.returncode != 0:
        print(f"[coverage-gate] ERROR: Source identity lookup failed: {result.stderr.strip()}", file=sys.stderr)
        raise RuntimeError("Source identity lookup failed")
    return result.stdout.strip()


def compute_manifest_hash(cov_data: dict) -> str:
    """@brief 计算覆盖数据 manifest 的 SHA-256 哈希（用于绑定证据）。"""
    canonical = json.dumps(cov_data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def print_gate_summary(branch: BranchSummary, gaps: list[InterfaceGap], passed: bool) -> None:
    """@brief 打印整体与分文件分支覆盖率、未覆盖接口缺口清单与最终判定。"""
    print(
        f"[coverage-gate] branch coverage: {branch.percent:.2f}% "
        f"(covered={branch.covered}, missing={branch.missing}, "
        f"threshold={BRANCH_THRESHOLD:.2f})"
    )
    for stat in branch.per_file:
        percent = "n/a" if stat.percent is None else f"{stat.percent:.2f}%"
        print(
            f"[coverage-gate]   {stat.file}: {percent} "
            f"(covered={stat.covered}, missing={stat.missing})"
        )
    print(f"[coverage-gate] uncovered public interfaces: {len(gaps)}")
    for gap in gaps:
        print(f"[coverage-gate]   {gap.file}:{gap.line}: {gap.name} ({gap.kind})")
    verdict = "PASSED" if passed else "FAILED"
    print(f"[coverage-gate] gate verdict: {verdict}")


def _evaluate_coverage(path: Path) -> int:
    """@brief 只评估同一完整运行的覆盖文件与回执，报告绑定实际输入和提交。"""
    cov_data = json.loads(path.read_text(encoding="utf-8"))
    if path.name != "coverage.json" or cov_data["run_id"] != path.parent.name:
        raise RuntimeError("Coverage report belongs to a different run")
    results, summary = full_run_receipts(path.parent)
    branch = compute_branch_summary(cov_data)
    gaps = find_uncovered_interfaces(REPO_ROOT, PRODUCTION_ROOTS, cov_data)
    passed = results["tests"] > 0 and results["skipped"] == 0 and judge_gate(branch.percent, len(gaps))
    command = (*TEST_COMMAND[:-1], str(summary["workers"]))
    report = build_report(branch, gaps, passed, "python " + " ".join(command[1:]),
                          path.parent.name, compute_manifest_hash(cov_data), get_git_commit_hash())
    report["test_results"] = results
    report["shards"] = summary["workers"]
    report["evidence_manifest"].update(source_sha256=summary["source_sha256"],
                                       collection_sha256=summary["collection_sha256"])
    write_report(report, REPORT_PATH)
    print_gate_summary(branch, gaps, passed)
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    """@brief 全量入口委托唯一 runner；已合并覆盖只接受本次完整节点回执。"""
    parser = argparse.ArgumentParser(description="WorldMock complete coverage gate")
    parser.add_argument("--from-json", type=Path)
    parser.add_argument("--shards", type=int, default=6)
    args = parser.parse_args(argv)
    if args.shards < 1:
        parser.error("--shards must be a positive integer")
    try:
        return run_test_suite(args.shards) if args.from_json is None else _evaluate_coverage(args.from_json)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        print(f"[coverage-gate] ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
