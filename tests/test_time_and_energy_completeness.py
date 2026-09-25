"""时间与能量规格的完整性守卫：验收台账全登记、已完成阶段有证据、无延期标记、符号增删到位。

@file     test_time_and_energy_completeness.py
@brief    以 tests/time_and_energy_acceptance.py 为数据源，对照 docs/dev/SPEC-time-and-energy.md
          「验收台账」逐条判定：登记表恰好覆盖台账各行；已完成阶段的行列出可解析、未被跳过的测试；
          生产目录与登记引用的测试文件中没有延期标记；已完成阶段的删除符号已消失、新增符号已出现。
@note     延期标记的正则由字符串片段拼成，本文件自身不含任何标记字面量。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.time_and_energy_acceptance import (
    ACCEPTANCE,
    REMOVED_SYMBOLS,
    REQUIRED_SYMBOLS,
    STAGE_IDS,
    STAGES_DONE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "dev" / "SPEC-time-and-energy.md"
LEDGER_HEADING = "## 验收台账"

#: 延期标记（片段拼接，避免本文件命中自己的扫描）。
#: 跳过标记覆盖 pytest 的跳过函数、跳过标记（含经 `from pytest import mark` 引用）与按导入跳过。
_SKIP_MARKER = "pytest" + r"\.(?:mark\.)?" + "skip" + r"|\bmark\." + "skip" + r"|importor" + "skip"
_OTHER_MARKERS = (
    r"\b" + "TO" + "DO" + r"\b",
    r"\b" + "FIX" + "ME" + r"\b",
    r"\b" + "X" + "XX" + r"\b",
    "NotImplemented" + "Error",
    "x" + "fail",
)
_DEFERRAL = re.compile("|".join((*_OTHER_MARKERS, _SKIP_MARKER)))
_DEFERRAL_EXCEPT_SKIP = re.compile("|".join(_OTHER_MARKERS))

#: 装饰器或 pytestmark 中表示跳过/预期失败的片段。
_SKIP_MARK_FRAGMENTS = ("mark." + "skip", "mark." + "x" + "fail")

#: 既有平台条件跳过所在的测试文件：只豁免跳过标记，其余延期标记照常判定。
_SKIP_ALLOWLIST = frozenset({
    "tests/corpora/test_branch_coverage.py",
    "tests/delivery/test_host_delivery_coverage.py",
    "tests/footprints/test_branch_coverage.py",
    "tests/footprints/test_validation.py",
    "tests/integration/test_host_branch_coverage.py",
    "tests/resources/test_equivalence_anchor.py",
})

PRODUCTION_ROOTS = ("src", "custom", "scripts", "tools")
_NODE_ID = re.compile(r"^tests/[\w./-]+\.py::(?:\w+::)?\w+(?:\[.*\])?$")


def _ledger_rows() -> list[str]:
    """读取规格「验收台账」表格各行的首列文本（去掉表头与分隔行）。"""
    lines = SPEC_PATH.read_text(encoding="utf-8").splitlines()
    start = lines.index(LEDGER_HEADING) + 1
    rows = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if not line.startswith("|") or re.match(r"^\|\s*-+\s*\|", line):
            continue
        first = line.split("|")[1].strip()
        if first != "契约点":
            rows.append(first)
    return rows


def _all_node_ids() -> list[str]:
    """返回登记表中所有行列出的测试节点（不论阶段是否完成）。"""
    return [node for entry in ACCEPTANCE.values() for node in entry["tests"]]


def _is_skip_mark(text: str) -> bool:
    """判定一段装饰器或标记表达式源码是否含跳过/预期失败标记。"""
    return any(fragment in text for fragment in _SKIP_MARK_FRAGMENTS)


def _module_skip_state(tree: ast.Module) -> tuple[bool, set[str]]:
    """返回（模块级 pytestmark 是否跳过, 绑定到跳过标记的模块级名字集合）。"""
    module_skipped = False
    skip_aliases: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        if not _is_skip_mark(ast.unparse(node.value)):
            continue
        module_skipped = module_skipped or "pytestmark" in names
        skip_aliases |= names
    return module_skipped, skip_aliases


def _skipping_decorators(node: ast.AST, skip_aliases: set[str]) -> list[str]:
    """列出节点上表示跳过/预期失败的装饰器源码（含经模块级别名引用的标记）。"""
    found = []
    for decorator in getattr(node, "decorator_list", []):
        text = ast.unparse(decorator)
        root = text.split(".")[0].split("(")[0]
        if _is_skip_mark(text) or root in skip_aliases:
            found.append(text)
    return found


def _find_function(scope: list[ast.stmt], name: str) -> ast.AST | None:
    """在语句列表中按名字找（异步）函数定义。"""
    for node in scope:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _resolve_problem(node_id: str) -> str | None:
    """把测试节点解析到源码中的测试函数；解析失败或被跳过时返回原因，否则返回 None。"""
    path_text, *parts = re.sub(r"\[.*\]$", "", node_id).split("::")
    path = REPO_ROOT / path_text
    if not path.is_file():
        return "test file does not exist"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_skipped, aliases = _module_skip_state(tree)
    if module_skipped:
        return "module-level pytestmark skips the whole file"
    scope, carriers = tree.body, []
    if len(parts) == 2:
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == parts[0]]
        if not classes:
            return f"class {parts[0]} does not exist"
        scope, carriers = classes[0].body, [classes[0]]
    function = _find_function(scope, parts[-1])
    if function is None or not parts[-1].startswith("test"):
        return f"test function {parts[-1]} does not exist"
    skips = [text for carrier in (*carriers, function) for text in _skipping_decorators(carrier, aliases)]
    return f"skipped by {skips}" if skips else None


def _text_files(root: Path) -> list[Path]:
    """列出目录下（或单个文件本身）可按 UTF-8 解码的文本文件，跳过缓存与打包元数据。"""
    candidates = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    files = []
    for path in candidates:
        if "__pycache__" in path.parts or any(part.endswith(".egg-info") for part in path.parts):
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(path)
    return files


def _matching_files(pattern: str, relative: str) -> list[str]:
    """返回列出路径下命中正则的文件（仓库相对路径）。"""
    regex = re.compile(pattern)
    return [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _text_files(REPO_ROOT / relative)
        if regex.search(path.read_text(encoding="utf-8"))
    ]


def _done_symbols(table: dict[str, list[tuple[str, list[str]]]]) -> list[tuple[str, str]]:
    """展开已完成阶段的符号表为（正则, 路径）对。"""
    return [
        (pattern, relative)
        for stage in sorted(STAGES_DONE)
        for pattern, paths in table.get(stage, [])
        for relative in paths
    ]


def test_registry_covers_exactly_the_acceptance_ledger_rows() -> None:
    """登记表的键与规格「验收台账」各行首列一一对应，不多不少。"""
    rows = _ledger_rows()
    assert rows, "验收台账表格为空或未找到"
    assert len(rows) == len(set(rows)), f"台账首列重复: {rows}"
    assert set(ACCEPTANCE) == set(rows)
    assert len(ACCEPTANCE) == len(rows)


def test_registry_entries_name_known_stages() -> None:
    """每一登记项只含 stage/tests，阶段编号已知，测试节点形如 path::name 或 path::Class::name。"""
    for row, entry in ACCEPTANCE.items():
        assert set(entry) == {"stage", "tests"}, row
        assert entry["stage"] in STAGE_IDS, row
        assert isinstance(entry["tests"], list), row
        bad = [node for node in entry["tests"] if not isinstance(node, str) or not _NODE_ID.match(node)]
        assert not bad, f"{row}: malformed node ids {bad}"
    assert STAGES_DONE <= STAGE_IDS
    for table in (REMOVED_SYMBOLS, REQUIRED_SYMBOLS):
        assert set(table) <= STAGE_IDS
        for pattern, paths in (item for items in table.values() for item in items):
            re.compile(pattern)
            assert paths and all(isinstance(path, str) for path in paths), pattern


def test_done_rows_list_resolvable_unskipped_tests() -> None:
    """已完成阶段的行必须列出测试；任何列出的节点都解析到存在且未被跳过的测试函数。"""
    empty = [row for row, entry in ACCEPTANCE.items() if entry["stage"] in STAGES_DONE and not entry["tests"]]
    assert not empty, f"done rows without tests: {empty}"
    problems = {node: reason for node in _all_node_ids() if (reason := _resolve_problem(node))}
    assert not problems, problems


def test_deferral_pattern_catches_every_skip_form() -> None:
    """跳过函数、跳过标记（含条件跳过、模块级 pytestmark 与 mark 别名）、按导入跳过与预期失败都算延期标记。"""
    skip = "sk" + "ip"
    skip_forms = (
        f"pytest.{skip}('x')",
        f"@pytest.mark.{skip}(reason='later stage')",
        f"@pytest.mark.{skip}if(True, reason='later')",
        f"pytestmark = pytest.mark.{skip}",
        f"@mark.{skip}",
        f"pytest.importor{skip}('x')",
    )
    samples = (*skip_forms, "@pytest.mark." + "x" + "fail")
    assert [line for line in samples if not _DEFERRAL.search(line)] == []
    assert [line for line in skip_forms if _DEFERRAL_EXCEPT_SKIP.search(line)] == []


def test_no_deferral_markers() -> None:
    """生产目录与登记引用的测试文件中没有延期标记；六个平台跳过文件只豁免跳过标记。"""
    targets = [path for root in PRODUCTION_ROOTS for path in _text_files(REPO_ROOT / root)]
    referenced = sorted({node.split("::")[0] for node in _all_node_ids()})
    targets += [REPO_ROOT / relative for relative in referenced if (REPO_ROOT / relative).is_file()]
    hits = []
    for path in targets:
        relative = path.relative_to(REPO_ROOT).as_posix()
        regex = _DEFERRAL_EXCEPT_SKIP if relative in _SKIP_ALLOWLIST else _DEFERRAL
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if regex.search(line):
                hits.append(f"{relative}:{number}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_removed_symbols_of_done_stages_are_absent() -> None:
    """已完成阶段登记的删除符号在列出路径下的任何文件中都不再出现。"""
    missing = [relative for _, relative in _done_symbols(REMOVED_SYMBOLS) if not (REPO_ROOT / relative).exists()]
    assert not missing, f"registered paths do not exist: {missing}"
    left = {
        f"{pattern} @ {relative}": hits
        for pattern, relative in _done_symbols(REMOVED_SYMBOLS)
        if (hits := _matching_files(pattern, relative))
    }
    assert not left, left


def test_required_symbols_of_done_stages_are_present() -> None:
    """已完成阶段登记的新增符号在每个列出路径中都能找到（目录下任一文件命中即算）。"""
    absent = [
        f"{pattern} @ {relative}"
        for pattern, relative in _done_symbols(REQUIRED_SYMBOLS)
        if not (REPO_ROOT / relative).exists() or not _matching_files(pattern, relative)
    ]
    assert not absent, absent
