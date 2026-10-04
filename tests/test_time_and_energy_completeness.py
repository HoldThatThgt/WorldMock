"""时间与能量规格的完整性守卫：验收台账全登记、已完成阶段有证据、无延期标记、符号增删到位。

@file     test_time_and_energy_completeness.py
@brief    以 tests/time_and_energy_acceptance.py 为数据源，对照 docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动
          「验收台账」逐条判定：登记表恰好覆盖台账各行；实施阶段 0–7 全部完成；已完成阶段的行列出可解析、未被跳过的测试；
          生产目录与登记引用的测试文件中没有延期标记；已完成阶段的删除符号已消失、新增符号已出现。
@note     延期标记的正则由字符串片段拼成，本文件自身不含任何标记字面量。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.acceptance_guard import acceptance_section

from tests.time_and_energy_acceptance import (
    ACCEPTANCE,
    REMOVED_SYMBOLS,
    REMOVED_DEFINITIONS,
    STAGE_IDS,
    STAGES_DONE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

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

#: 改代码与测试的实施阶段 0–7；阶段 8 是只报告的真实复验，阶段 9 是实现后文档。
IMPLEMENTATION_STAGES = frozenset(str(n) for n in range(8))
_NODE_ID = re.compile(r"^tests/[\w./-]+\.py::(?:\w+::)?\w+(?:\[.*\])?$")


def _ledger_rows() -> list[str]:
    """@brief 读取机器验收数据中的要求名称，不解析规格散文。"""
    (table,) = acceptance_section("time-and-energy", "验收台账")["tables"]
    return [row[0] for row in table[1:]]


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


def _production_symbols(root: Path) -> dict[str, set[str]]:
    """@brief 扫描整个生产包的实际定义、名称、字段与字面量，忽略注释及文档字符串。"""
    found = {}
    for directory in ("src", "custom"):
        for path in sorted((root / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                          and ast.get_docstring(node) is not None}
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    names.update(re.findall(r"[A-Za-z_]\w*", node.value))
            found[path.relative_to(root).as_posix()] = names
    return found


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
    assert set(REMOVED_SYMBOLS) <= STAGE_IDS
    assert all(re.fullmatch(r"[A-Za-z_]\w*", symbol)
               for symbols in REMOVED_SYMBOLS.values() for symbol in symbols)


def test_every_implementation_stage_is_done() -> None:
    """实施阶段 0–7 全部在 STAGES_DONE 中，其台账行、删除与新增符号都由本守卫判定。

    阶段 8 是真实复验（练秋湖与 r14 复跑），只报告、不作门禁；阶段 9 是实现后文档（实现状态文档最后提交）。
    二者不改代码与测试，不要求进入 STAGES_DONE。
    """
    assert IMPLEMENTATION_STAGES < STAGE_IDS
    assert STAGE_IDS - IMPLEMENTATION_STAGES == {"8", "9"}
    missing = sorted(IMPLEMENTATION_STAGES - STAGES_DONE)
    assert not missing, f"implementation stages not done: {missing}"


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
    """@brief 删除符号覆盖整个生产包，搬到另一个文件仍会失败。"""
    removed = {symbol for stage in STAGES_DONE for symbol in REMOVED_SYMBOLS.get(stage, [])}
    hits = {path: sorted(symbols & removed) for path, symbols in _production_symbols(REPO_ROOT).items()
            if symbols & removed}
    for directory in ("src", "custom"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            definitions = {node.name for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if left := definitions & REMOVED_DEFINITIONS:
                hits[path.relative_to(REPO_ROOT).as_posix()] = sorted(left)
    assert not hits, hits


def test_removed_symbol_scan_detects_a_definition_moved_to_another_file(tmp_path) -> None:
    """@brief 旧位置已清空但新位置仍有删除定义时，整个包的结构扫描能找到它。"""
    path = tmp_path / "src" / "worldmock" / "elsewhere.py"
    path.parent.mkdir(parents=True)
    path.write_text('def _after_unknown():\n    return 1\n', encoding="utf-8")
    assert _production_symbols(tmp_path)["src/worldmock/elsewhere.py"] == {"_after_unknown"}
