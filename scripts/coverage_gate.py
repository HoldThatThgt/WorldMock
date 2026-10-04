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
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
BRANCH_THRESHOLD = 85.0
TEST_COMMAND = (sys.executable, "-m", "coverage", "run", "--parallel-mode", "-m", "pytest", "-q")
JSON_COMMAND = (sys.executable, "-m", "coverage", "json")
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


def test_shards(base: Path, count: int) -> list[list[str]]:
    """@brief 按目录和文件稳定排序分片；目录内文件连续，每个文件只分配一次。"""
    if count < 1:
        raise ValueError("shards must be a positive integer")
    files = sorted(path.relative_to(base).as_posix() for path in (base / "tests").rglob("test_*.py"))
    if not files:
        raise RuntimeError("test suite contains no test files")
    size = (len(files) + count - 1) // count
    return [files[start:start + size] for start in range(0, len(files), size)]


def junit_summary(paths: list[Path]) -> dict[str, int]:
    """@brief 汇总真实 pytest 报告，缺失报告报错；跳过和预期失败均不能放行。"""
    summary = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
    for path in paths:
        for suite in ET.parse(path).getroot().iter("testsuite"):
            for name in summary:
                summary[name] += int(suite.attrib.get(name, "0"))
    return summary


def _run_shard(files: list[str], report: Path, data: Path) -> int:
    """@brief 独立运行一片，覆盖数据和测试报告不与历史运行共享。"""
    command = [*TEST_COMMAND, *files, f"--junitxml={report}"]
    environment = {**os.environ, "COVERAGE_FILE": str(data)}
    print(f"[coverage-gate] running shard with {len(files)} files", flush=True)
    return subprocess.call(command, cwd=REPO_ROOT, env=environment)


def run_test_suite(directory: Path, shards: int = 1) -> tuple[int, dict[str, int]]:
    """@brief 使用当前 Python 并行测试，合并本次独立数据后返回退出码和真实统计。"""
    parts = test_shards(REPO_ROOT, shards)
    reports = [directory / f"tests-{index}.xml" for index in range(len(parts))]
    with ThreadPoolExecutor(max_workers=len(parts)) as pool:
        futures = [pool.submit(_run_shard, files, report, directory / ".coverage")
                   for files, report in zip(parts, reports)]
        codes = [future.result() for future in futures]
    summary = junit_summary(reports)
    environment = {**os.environ, "COVERAGE_FILE": str(directory / ".coverage")}
    combined = subprocess.call([sys.executable, "-m", "coverage", "combine", str(directory)],
                               cwd=REPO_ROOT, env=environment)
    return (0 if all(code == 0 for code in codes) and combined == 0 else 1), summary


def export_coverage_json(data_path: Path) -> dict:
    """@brief 导出 coverage JSON 到临时文件并解析返回。

    @throw RuntimeError  当 coverage json 命令以非零退出码结束时。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        json_path = Path(tmp_dir) / "coverage.json"
        command = [*JSON_COMMAND, "-o", str(json_path)]
        environment = {**os.environ, "COVERAGE_FILE": str(data_path)}
        result = subprocess.run(command, cwd=REPO_ROOT, env=environment, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"coverage json failed (rc={result.returncode}): {detail}")
        return json.loads(json_path.read_text(encoding="utf-8"))


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
    """@brief 获取实际被测提交，获取失败时不能生成虚构身份。"""
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=5, check=True)
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


def main(argv: list[str] | None = None) -> int:
    """@brief 门禁主流程：跑测试、导出覆盖、判定达标、写报告，返回进程退出码。"""
    parser = argparse.ArgumentParser(description="Run the complete coverage gate with the current Python.")
    parser.add_argument("--shards", type=int, default=1)
    args = parser.parse_args(argv)
    if args.shards < 1:
        parser.error("--shards must be a positive integer")
    try:
        with tempfile.TemporaryDirectory(prefix="worldmock-coverage-") as name:
            directory = Path(name)
            test_rc, summary = run_test_suite(directory, args.shards)
            cov_data = export_coverage_json(directory / ".coverage")
    except (RuntimeError, OSError, ET.ParseError) as error:
        print(f"[coverage-gate] ERROR: {error}", file=sys.stderr)
        return 1
    branch = compute_branch_summary(cov_data)
    gaps = find_uncovered_interfaces(REPO_ROOT, PRODUCTION_ROOTS, cov_data)
    passed = (test_rc == 0 and summary["tests"] > 0 and summary["skipped"] == 0
              and summary["failures"] == 0 and summary["errors"] == 0
              and judge_gate(branch.percent, len(gaps)))
    
    # 生成证据 manifest 绑定
    snapshot_id = str(uuid4())
    manifest_hash = compute_manifest_hash(cov_data)
    source_identity = get_git_commit_hash()
    
    report = build_report(
        branch, gaps, passed, f"python -m coverage run -m pytest -q (shards={args.shards})",
        snapshot_id, manifest_hash, source_identity
    )
    report["test_results"] = summary
    report["shards"] = args.shards
    write_report(report, REPORT_PATH)
    print_gate_summary(branch, gaps, passed)
    if test_rc != 0:
        print(
            f"[coverage-gate] test suite exited rc={test_rc}; gate cannot pass",
            file=sys.stderr,
        )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
