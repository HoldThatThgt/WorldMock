"""生产代码风格常驻门禁（docs/VERIFICATION.md#verification-worldmock-验收标准「工程门禁」/GAP-5）。

检查范围：src/worldmock 与 custom 全部生产 .py（测试文件不扫）。规则：
行宽 ≤120 字符、生产函数 ≤50 行且 ≤5 参（下划线私有同受约束，方法不计
self/cls）、文件 ≤2000 行、生产函数 docstring 含中文（Doxygen 口径）。

棘轮口径：本门禁对新增违规零容忍——任何新违规立即失败。既有存量违规
以 _OUTSTANDING 按文件×种类冻结记账（均为本门禁建立时点非本单元所有权
的文件，见 C7 交付报告）：只允许各所有权单元修复后同步把对应计数清零，
实际计数与清单不一致（新增或清单腐化）同样失败；清单清空后本门禁即成为
纯全量门禁。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("src/worldmock", "custom")
MAX_LINE_WIDTH = 120
MAX_FUNCTION_LINES = 50
MAX_PARAMETERS = 5
MAX_FILE_LINES = 2000
_CJK = re.compile(r"[\u4e00-\u9fff]")

#: 既有存量违规（文件 -> 种类 -> 计数）；修复后必须同步缩减直至清空。
_OUTSTANDING: dict[str, dict[str, int]] = {
    "src/worldmock/evaluation/behavior_metrics.py": {"DOCSTRING": 1},
    "src/worldmock/life/social_actions.py": {"PARAMS": 1},
    "src/worldmock/opslog.py": {"PARAMS": 1},
    "src/worldmock/runtime.py": {"PARAMS": 1},
}


def _production_files() -> list[Path]:
    """返回两个扫描根下的全部生产 Python 文件（稳定排序）。"""
    files = []
    for root in SCAN_ROOTS:
        base = PROJECT_ROOT / root
        files.extend(path for path in sorted(base.rglob("*.py")) if "__pycache__" not in path.parts)
    return files


def _parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """统计函数入参个数（可调用者视角；方法首位 self/cls 不计）。"""
    args = node.args
    count = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
    count += 1 if args.vararg else 0
    count += 1 if args.kwarg else 0
    if args.args and args.args[0].arg in ("self", "cls"):
        count -= 1
    return count


def _style_violations() -> dict[str, dict[str, int]]:
    """按文件×种类统计全部风格违规。"""
    violations: dict[str, dict[str, int]] = {}

    def record(relative: str, kind: str) -> None:
        bucket = violations.setdefault(relative, {})
        bucket[kind] = bucket.get(kind, 0) + 1

    for path in _production_files():
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if len(lines) > MAX_FILE_LINES:
            record(relative, "FILELEN")
        for number, line in enumerate(lines, start=1):
            if len(line.rstrip()) > MAX_LINE_WIDTH:
                record(relative, "WIDTH")
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.end_lineno - node.lineno + 1 > MAX_FUNCTION_LINES:
                record(relative, "FUNCLEN")
            if _parameter_count(node) > MAX_PARAMETERS:
                record(relative, "PARAMS")
            docstring = ast.get_docstring(node)
            if docstring is None or not _CJK.search(docstring):
                record(relative, "DOCSTRING")
    return violations


def test_style_gate_admits_no_new_violations():
    """全量风格门禁：实际违规恰等于冻结清单（新增即红，修复后清单须缩减）。"""
    actual = _style_violations()
    expected = {path: dict(kinds) for path, kinds in _OUTSTANDING.items() if kinds}
    new = {path: {kind: count for kind, count in kinds.items()
                  if count > expected.get(path, {}).get(kind, 0)}
           for path, kinds in actual.items()}
    new = {path: kinds for path, kinds in new.items() if kinds}
    stale = {path: {kind: expected[path][kind] - actual.get(path, {}).get(kind, 0)
                    for kind in kinds if expected[path][kind] > actual.get(path, {}).get(kind, 0)}
             for path, kinds in expected.items()}
    stale = {path: kinds for path, kinds in stale.items() if kinds}
    assert not new, f"new style violations must be fixed, not waived: {new}"
    assert not stale, (f"style debt was fixed; shrink _OUTSTANDING accordingly: {stale}")
    assert actual == expected, f"unexpected violation shape: actual={actual} expected={expected}"
