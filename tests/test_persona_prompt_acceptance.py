"""人物 Prompt 七层规格的完整性守卫：台账逐行有测试、裁决全部落地、没有延后的实现。

@file     test_persona_prompt_acceptance.py
@brief    以 tests/persona_prompt_acceptance.py 为数据源，测试时解析 docs/dev/SPEC-persona-prompt.md 的「验收台账」、
          第六章两张表（程序错误与缺口）与「裁决记录」第二张表（#21–#38），逐行判定：登记表与规格各行一一对应（规格增删行
          而登记表不跟随即失败）；每行至少登记一个存在、会被收集且未被跳过的测试（按 AST 解析，含类中的测试与参数化节点）；
          本特性新增或改动的生产文件与新增测试文件中没有延期标记、没有只有 pass 或省略号的空壳函数。
@note     延期标记的正则由字符串片段拼成，本文件自身不含任何标记字面量。文档不扫描：实现后文档作业完成前，被修订文档中
          「修订部分」的说明是既定写法。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.persona_prompt_acceptance import (
    FEATURE_PRODUCTION_FILES,
    FEATURE_TEST_FILES,
    GAPS,
    LEDGER,
    PROGRAM_ERRORS,
    REPORT_ONLY,
    RULINGS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "dev" / "SPEC-persona-prompt.md"
LEDGER_HEADING = "## 验收台账"
CHAPTER_SIX_HEADING = "## 六、统一修复的程序错误"
RULINGS_HEADING = "## 裁决记录"
#: 按推荐定为已定的待确认裁决编号。
ADOPTED_RULINGS = list(range(21, 39))
#: 只报告行在规格中的固定说法。
REPORT_ONLY_PHRASE = "只报告，不作门禁"

#: 延期标记（片段拼接，避免本文件命中自己的扫描）：英文标记、未实现异常、pytest 的跳过与预期失败，以及三个中文延期说法。
_MARKERS = (
    r"\b" + "TO" + "DO" + r"\b",
    r"\b" + "FIX" + "ME" + r"\b",
    r"\b" + "X" + "XX" + r"\b",
    "NotImplemented" + "Error",
    "pytest" + r"\.(?:mark\.)?" + "skip",
    r"\bmark\." + "skip",
    "importor" + "skip",
    "x" + "fail",
    "待" + "定",
    "尚需" + "定稿",
    "待" + "实现",
)
_DEFERRAL = re.compile("|".join(_MARKERS))
#: 装饰器、pytestmark 或测试体中表示跳过与预期失败的片段。
_SKIP_MARK = re.compile(r"mark\.(?:" + "sk" + "ip|" + "x" + "fail)")
_SKIP_CALL = re.compile(r"\b(?:pytest\.)?(?:" + "sk" + "ip|" + "x" + "fail|importor" + "sk" + r"ip)\(")
_NODE_ID = re.compile(r"^tests/[\w./-]+\.py::(?:Test\w+::)?test\w*(?:\[.*\])?$")
_CELL = re.compile(r"(?<!\\)\|")


def _section(heading: str) -> list[str]:
    """规格中某个二级章节的正文行（到下一个二级标题为止）。"""
    lines = SPEC_PATH.read_text(encoding="utf-8").splitlines()
    start = lines.index(heading) + 1
    end = next((index for index in range(start, len(lines)) if lines[index].startswith("## ")), len(lines))
    return lines[start:end]


def _tables(heading: str) -> list[list[list[str]]]:
    """章节内的各张表格：每张表为逐行的单元格列表（首行是表头，分隔行去掉；转义的竖线不断开单元格）。"""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in _section(heading):
        if not line.startswith("|"):
            if current:
                tables.append(current)
                current = []
            continue
        if not re.match(r"^\|\s*-+\s*\|", line):
            current.append([cell.strip() for cell in _CELL.split(line)[1:-1]])
    if current:
        tables.append(current)
    return tables


def _ledger_rows() -> list[tuple[str, str]]:
    """「验收台账」逐行的（契约点，必须通过的验证）。"""
    (table,) = _tables(LEDGER_HEADING)
    header, *rows = table
    assert header == ["契约点", "必须通过的验证"]
    return [(row[0], row[1]) for row in rows]


def _chapter_six_rows() -> tuple[list[str], list[str]]:
    """第六章两张表的首列：程序错误各行与缺口各行。"""
    errors, gaps = _tables(CHAPTER_SIX_HEADING)
    assert errors[0][0] == "程序错误" and gaps[0][0] == "缺口"
    return [row[0] for row in errors[1:]], [row[0] for row in gaps[1:]]


def _pending_rulings() -> list[tuple[int, str]]:
    """「裁决记录」第二张表逐行的（编号，状态）。"""
    _, second = _tables(RULINGS_HEADING)
    assert second[0] == ["#", "事项", "推荐", "另一选项及后果", "状态"]
    return [(int(row[0]), row[-1]) for row in second[1:]]


def _registered() -> dict[str, list[str]]:
    """全部登记项：台账行、程序错误行、缺口行与裁决编号各自的测试节点。"""
    entries = {f"验收台账：{row}": nodes for row, nodes in LEDGER.items()}
    entries.update({f"程序错误：{row}": nodes for row, nodes in PROGRAM_ERRORS.items()})
    entries.update({f"缺口：{row}": nodes for row, nodes in GAPS.items()})
    entries.update({f"裁决 #{number}": nodes for number, nodes in RULINGS.items()})
    return entries


def _skip_aliases(tree: ast.Module) -> tuple[bool, set[str]]:
    """返回（模块级 pytestmark 是否跳过或预期失败, 绑定到这类标记的模块级名字）。"""
    skipped, aliases = False, set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        if _SKIP_MARK.search(ast.unparse(node.value)):
            skipped = skipped or "pytestmark" in names
            aliases |= names
    return skipped, aliases


def _skipping(carriers: list[ast.AST], aliases: set[str]) -> list[str]:
    """列出类与函数上表示跳过或预期失败的装饰器（含经模块级别名引用的标记）。"""
    found = []
    for carrier in carriers:
        for decorator in getattr(carrier, "decorator_list", []):
            text = ast.unparse(decorator)
            if _SKIP_MARK.search(text) or text.split(".")[0].split("(")[0] in aliases:
                found.append(text)
    return found


def _resolve_problem(node_id: str, root: Path = REPO_ROOT) -> str | None:
    """把测试节点解析到源码中会被收集的测试函数；不存在、不会被收集或被跳过时返回原因，否则返回 None。"""
    path_text, *parts = re.sub(r"\[.*\]$", "", node_id).split("::")
    path = root / path_text
    if not path.is_file():
        return "test file does not exist"
    if not path.name.startswith("test_"):
        return "file is not a collected test module"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_skipped, aliases = _skip_aliases(tree)
    if module_skipped:
        return "module-level pytestmark skips the whole file"
    scope, carriers = tree.body, []
    if len(parts) == 2:
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == parts[0]]
        if not classes:
            return f"class {parts[0]} does not exist"
        scope, carriers = classes[-1].body, [classes[-1]]
    functions = [node for node in scope
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[-1]]
    if not functions:
        return f"test function {parts[-1]} does not exist"
    skips = _skipping([*carriers, functions[-1]], aliases)
    if skips:
        return f"skipped by {skips}"
    if _SKIP_CALL.search(ast.unparse(functions[-1])):
        return "the test body skips or expects failure"
    return None


def _stub_functions(tree: ast.AST) -> list[str]:
    """只有 pass、省略号或文档字符串的函数（空壳），返回「名字:行号」。"""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            body = body[1:]
        if all(isinstance(item, ast.Pass) or (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
                                              and item.value.value is Ellipsis) for item in body):
            found.append(f"{node.name}:{node.lineno}")
    return found


def _feature_files() -> list[str]:
    """需要扫描的文件：本特性新增或改动的生产文件与新增测试文件。"""
    return [*FEATURE_PRODUCTION_FILES, *FEATURE_TEST_FILES]


def test_every_ledger_row_is_registered_and_every_registered_row_exists() -> None:
    """登记表的键与规格「验收台账」各行首列一一对应：规格增加的行未登记、登记的行已不在规格中都失败。"""
    rows = [row for row, _ in _ledger_rows()]
    assert rows, "验收台账表格为空或未找到"
    assert len(rows) == len(set(rows)), f"台账首列重复: {rows}"
    assert sorted(set(rows) - set(LEDGER)) == [], "台账行未登记测试"
    assert sorted(set(LEDGER) - set(rows)) == [], "登记的行已不在台账中"


def test_every_chapter_six_row_is_registered_and_every_registered_row_exists() -> None:
    """第六章「程序错误」与「缺口」两张表的各行与登记表一一对应。"""
    errors, gaps = _chapter_six_rows()
    assert errors and gaps
    assert len(errors) == len(set(errors)) and len(gaps) == len(set(gaps))
    assert set(errors) == set(PROGRAM_ERRORS)
    assert set(gaps) == set(GAPS)


def test_rulings_21_to_38_are_adopted_and_each_is_registered() -> None:
    """「裁决记录」第二张表恰为 #21–#38，全部按推荐定为已定，每一条都登记了验证推荐做法的测试。"""
    rulings = _pending_rulings()
    assert [number for number, _ in rulings] == ADOPTED_RULINGS
    assert {number: status for number, status in rulings if status != "已定"} == {}
    assert sorted(RULINGS) == ADOPTED_RULINGS


def test_report_only_rows_stay_report_only_in_the_spec() -> None:
    """只报告的行（真实模型运行）在规格中仍写明只报告、不作门禁；它登记的是所用工具的测试，说明写在登记表里。"""
    verification = dict(_ledger_rows())
    for row, note in REPORT_ONLY.items():
        assert row in LEDGER and note
        assert REPORT_ONLY_PHRASE in verification[row], row


def test_program_error_ledger_row_carries_every_chapter_six_regression() -> None:
    """台账「程序错误」行登记第六章每一行的回归用例。"""
    regressions = {node for nodes in PROGRAM_ERRORS.values() for node in nodes}
    assert regressions <= set(LEDGER["程序错误"])


def test_every_registered_node_resolves_to_a_collected_unskipped_test() -> None:
    """每个登记项至少一个节点、节点不重复且形如 path::name 或 path::Class::name；每个节点都解析到存在、会被收集且
    未被跳过的测试函数。"""
    entries = _registered()
    empty = sorted(name for name, nodes in entries.items() if not nodes)
    assert not empty, f"entries without tests: {empty}"
    duplicated = sorted(name for name, nodes in entries.items() if len(nodes) != len(set(nodes)))
    assert not duplicated, f"entries listing a node twice: {duplicated}"
    nodes = sorted({node for items in entries.values() for node in items})
    malformed = [node for node in nodes if not _NODE_ID.match(node)]
    assert not malformed, f"malformed node ids: {malformed}"
    problems = {node: reason for node in nodes if (reason := _resolve_problem(node))}
    assert not problems, problems


def test_resolver_rejects_missing_uncollected_and_skipped_tests(tmp_path: Path) -> None:
    """解析器有牙齿：文件、类或函数不存在，模块不会被收集，装饰器、别名、pytestmark 或测试体跳过都判为问题；
    模块级测试、类中的测试与参数化节点都能解析。"""
    skip, fail = "sk" + "ip", "x" + "fail"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text(
        "import pytest\n"
        f"_LATER = pytest.mark.{skip}if(True, reason='later')\n"
        "def test_plain():\n    assert True\n"
        f"@pytest.mark.{fail}\ndef test_marked():\n    assert True\n"
        f"@_LATER\ndef test_aliased():\n    assert True\n"
        f"def test_inline():\n    pytest.{skip}('later')\n"
        "class TestGroup:\n    def test_member(self):\n        assert True\n"
        "@pytest.mark.parametrize('value', [1, 2])\ndef test_param(value):\n    assert value\n",
        encoding="utf-8")
    (tmp_path / "tests" / "test_skipped_module.py").write_text(
        f"import pytest\npytestmark = pytest.mark.{skip}\ndef test_any():\n    assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "helpers.py").write_text("def test_helper():\n    assert True\n", encoding="utf-8")
    sample = "tests/test_sample.py::"
    assert _resolve_problem(sample + "test_plain", tmp_path) is None
    assert _resolve_problem(sample + "TestGroup::test_member", tmp_path) is None
    assert _resolve_problem(sample + "test_param[1]", tmp_path) is None
    for node in (sample + "test_marked", sample + "test_aliased", sample + "test_inline", sample + "test_missing",
                 sample + "TestMissing::test_member", "tests/test_absent.py::test_plain",
                 "tests/test_skipped_module.py::test_any", "tests/helpers.py::test_helper"):
        assert _resolve_problem(node, tmp_path) is not None, node


def test_frozen_feature_file_lists_name_existing_files_once() -> None:
    """冻结的本特性文件清单都存在、不重复；生产清单只含生产目录，测试清单只含 tests 目录。"""
    files = _feature_files()
    assert len(files) == len(set(files))
    assert [path for path in files if not (REPO_ROOT / path).is_file()] == []
    assert all(path.split("/")[0] in {"src", "custom", "scripts", "tools"} for path in FEATURE_PRODUCTION_FILES)
    assert all(path.startswith("tests/") for path in FEATURE_TEST_FILES)


@pytest.mark.parametrize("line", [
    "# " + "TO" + "DO: later", "# " + "FIX" + "ME", "# " + "X" + "XX", "raise NotImplemented" + "Error",
    "pytest." + "sk" + "ip('x')", "@pytest.mark." + "sk" + "ip", "@pytest.mark." + "sk" + "ipif(True, reason='x')",
    "@mark." + "sk" + "ip", "pytest.importor" + "sk" + "ip('x')", "@pytest.mark." + "x" + "fail",
    "\"\"\"@brief 规则" + "待" + "定。\"\"\"", "# 措辞" + "尚需" + "定稿", "return None  # " + "待" + "实现",
])
def test_deferral_pattern_catches_every_marker(line: str) -> None:
    """每一种延期标记都被扫描正则抓住。"""
    assert _DEFERRAL.search(line)


def test_deferral_pattern_leaves_ordinary_code_alone() -> None:
    """普通代码与中文说明不误报：跳过与失败的普通词、同字不同义的说法都放行。"""
    for line in ("def skip_memory_messages(items):", "status = 'failed'", "决定了。", "待办层的暂停活动", "实现状态"):
        assert not _DEFERRAL.search(line), line


def test_stub_detection_catches_pass_ellipsis_and_docstring_only_bodies() -> None:
    """空壳判定：只有 pass、只有省略号、只有文档字符串的函数都算，有实际语句的函数不算。"""
    tree = ast.parse("def a():\n    pass\n\ndef b():\n    '''说明。'''\n\nasync def c():\n    ...\n\n"
                     "def d():\n    '''说明。'''\n    return 1\n\nclass E:\n    def f(self):\n        pass\n")
    assert _stub_functions(tree) == ["a:1", "b:4", "c:7", "f:15"]


def test_feature_files_carry_no_deferral_markers() -> None:
    """本特性新增或改动的生产文件（含查看器脚本）与新增测试文件中没有任何延期标记。"""
    hits = []
    for relative in _feature_files():
        for number, line in enumerate((REPO_ROOT / relative).read_text(encoding="utf-8").splitlines(), 1):
            if _DEFERRAL.search(line):
                hits.append(f"{relative}:{number}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_feature_python_files_have_no_stub_functions() -> None:
    """本特性新增或改动的 Python 生产文件与新增测试文件中没有只有 pass、省略号或文档字符串的空壳函数。"""
    stubs = {relative: found for relative in _feature_files() if relative.endswith(".py")
             if (found := _stub_functions(ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))))}
    assert not stubs, stubs
