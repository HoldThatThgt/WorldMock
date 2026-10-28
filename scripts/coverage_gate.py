#!/usr/bin/env python3
"""覆盖率门禁工具：运行测试套件，计算分支覆盖率与公共接口覆盖率并判定。

@file     coverage_gate.py
@brief    读取 coverage 与 pytest JUnit 报告，判定当前源码的分支和公共接口覆盖。
@note     机器判定两项：整体分支覆盖率 >= 85%，且公共接口未覆盖数为 0；
          CI 对固定测试层执行、附件和被测提交负责，本脚本不调度测试。
"""

from __future__ import annotations

import argparse
import logging
import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BRANCH_THRESHOLD = 85.0
PRODUCTION_ROOTS = ("src/worldmock", "custom/agents", "custom/envs", "tools/analysis")
REPORT_PATH = REPO_ROOT / ".work" / "test-results" / "coverage-report.json"


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



def read_test_results(paths: list[Path]) -> dict[str, int]:
    """@brief 汇总 pytest 原生 JUnit 报告，空报告和无效计数不能表示通过。"""
    totals = dict(tests=0, failures=0, errors=0, skipped=0)
    for path in paths:
        suites = ElementTree.parse(path).getroot().findall("testsuite")
        if not suites:
            raise ValueError(f"JUnit report has no test suite: {path}")
        for suite in suites:
            counts = {key: int(suite.attrib[key]) for key in totals}
            if any(value < 0 for value in counts.values()) or counts["tests"] == 0:
                raise ValueError(f"JUnit report has invalid test counts: {path}")
            for key, value in counts.items():
                totals[key] += value
    return totals


def build_report(branch: BranchSummary, gaps: list[InterfaceGap], results: dict, commit: str) -> dict:
    """@brief 生成适合 PR 摘要和附件的覆盖报告，不保存历史调度或规格台账。"""
    passed = (results["tests"] > 0 and not any(results[key] for key in ("failures", "errors", "skipped"))
              and judge_gate(branch.percent, len(gaps)))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": commit,
        "test_results": results,
        "branch_coverage": {"covered_branches": branch.covered, "missing_branches": branch.missing,
                            "percent": branch.percent, "threshold": BRANCH_THRESHOLD},
        "uncovered_public_interfaces": [
            {"file": gap.file, "line": gap.line, "name": gap.name, "kind": gap.kind} for gap in gaps
        ],
        "passed": passed,
    }


def write_report(report: dict, path: Path) -> None:
    """@brief 将当次覆盖结论写到指定运行附件目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_git_commit_hash() -> str:
    """@brief 获取当前检出的提交；CI 在该提交的干净工作树执行全部层。"""
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=5, check=True)
    return result.stdout.strip()


def summary_markdown(report: dict) -> str:
    """@brief 生成 GitHub Actions 可直接显示的检查摘要。"""
    results, branch = report["test_results"], report["branch_coverage"]
    verdict = "PASSED" if report["passed"] else "FAILED"
    return (f"### Test and coverage checks: {verdict}\n\n"
            f"Tested commit: {report['source_commit']}\n\n"
            "| Check | Result |\n| --- | --- |\n"
            f"| Tests | {results['tests']} |\n"
            f"| Failures / errors / skipped | {results['failures']} / {results['errors']} / {results['skipped']} |\n"
            f"| Branch coverage | {branch['percent']:.2f}% (required: {branch['threshold']:.0f}%) |\n"
            f"| Uncovered public interfaces | {len(report['uncovered_public_interfaces'])} |\n\n"
            "Detailed reports and logs are available in this workflow run's artifacts.\n")


def main(argv: list[str] | None = None) -> int:
    """@brief 评估原生覆盖和测试报告；失败输出英文诊断并返回非零码。"""
    parser = argparse.ArgumentParser(description="Evaluate coverage and pytest JUnit reports")
    parser.add_argument("--from-json", required=True, type=Path)
    parser.add_argument("--junit", required=True, nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.from_json.read_text(encoding="utf-8"))
        branch = compute_branch_summary(data)
        gaps = find_uncovered_interfaces(REPO_ROOT, PRODUCTION_ROOTS, data)
        report = build_report(branch, gaps, read_test_results(args.junit), get_git_commit_hash())
        write_report(report, args.output)
        summary = summary_markdown(report)
        if args.summary is not None:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            with args.summary.open("a", encoding="utf-8") as output:
                output.write(summary)
        print(summary)
        for gap in gaps:
            logging.error("Uncovered public interface: %s:%s %s", gap.file, gap.line, gap.name)
        return 0 if report["passed"] else 1
    except (OSError, ValueError, KeyError, ElementTree.ParseError, subprocess.SubprocessError) as error:
        logging.error("Coverage check failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
