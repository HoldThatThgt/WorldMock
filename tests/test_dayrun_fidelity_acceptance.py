"""一日运行保真规格的完整性守卫：台账逐行有测试、裁决全部落地、没有延后的实现、文档不留待验收标记。

@file     test_dayrun_fidelity_acceptance.py
@brief    以 tests/dayrun_fidelity_acceptance.py 为数据源，测试时解析 docs/dev/SPEC-dayrun-fidelity.md 的「验收台账」与「裁决记录」
          两张表，逐行判定：登记表与台账各行一一对应（规格增删行而登记表不跟随即失败）；裁决除 D13（登记）与 S2（撤回）外全部
          已定，每条有要建之物的已定裁决都登记了测试；只报告的行在规格中仍写明只报告；输入警告的闭集逐码各有一例；每个登记的
          节点都解析到存在、会被收集且未被跳过的测试（按 AST 解析，含类中的测试与参数化节点）；本特性新增或改动的生产文件与
          新增测试文件中没有延期标记、没有空壳函数；docs/ 里不再留有本特性冻结时写下的临时待验收标记。
@note     表格解析、节点解析、延期标记正则与空壳判定取自共用模块 tests/acceptance_guard.py（与人物 Prompt 七层的守卫同用）；
          延期标记与待验收标记的字符串都由片段拼成（见 _PENDING_MARKERS），本文件自身不含任何标记字面量。
          文档标记指本特性冻结时写下的三个待验收状态词与文档地图的「修订条款」未实现说法；一行含标记且满足三者之一即判为
          仍在：表格里某个单元格以标记开头（状态格），行是引用块（文件头的 `> ` 行），或行点名本规格（「一日运行」「保真」
          连写、或文件名片段 dayrun-fidelity）。版本表与版本历史中的表格行是历史，docs/dev/research/ 下是研究与验收记录，
          都不扫描；定义状态词的图例句与其他特性的行不点名本规格，不在判定之内。之后的特性冻结时若在状态格或文件头用同样
          的状态词，须把这项判定限定到本特性的行。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.acceptance_guard import DEFERRAL, NODE_ID, resolve_problem, section, stub_functions, tables
from tests.dayrun_fidelity_acceptance import (
    FEATURE_PRODUCTION_FILES,
    FEATURE_TEST_FILES,
    LEDGER,
    REPORT_ONLY,
    RULINGS,
    STATUS_ONLY,
    WARNING_CODE_TESTS,
)
from worldmock.profile.injection import INPUT_WARNING_CODES

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "dev" / "SPEC-dayrun-fidelity.md"
LEDGER_HEADING = "## 验收台账"
RULINGS_HEADING = "## 裁决记录"
#: 「裁决记录」第一张表（会话中已定的方向）与第二张表（本文的推荐与另一选项）的编号，按表中顺序。
DECIDED_IN_SESSION = ["D1", "U1", "U2", "U3", "U4"]
RECOMMENDED = [f"D{number}" for number in range(2, 20)] + [f"S{number}" for number in range(1, 12)]
#: 已定裁决状态列的开头（其后可带用户裁定的说明）。
DECIDED = "已定"
#: 只报告行在规格中的固定说法（台账表前的说明句）与台账行首列必带的字样。
REPORT_ONLY_PHRASE = "复跑指标只报告，不作门禁"
REPORT_ONLY_MARK = "只报告"

#: 本特性冻结时写进文档的临时标记（片段拼接，避免本文件命中自己的扫描）：三个待验收状态词与文档地图的未实现说法。
_PENDING_MARKERS = ("本次" + "离线验收" + "待执行", "离线验收" + "待执行", "待" + "重验", "修订条款" + "尚未" + "实现")
_PENDING = re.compile("|".join(_PENDING_MARKERS))
_STATUS_CELL = re.compile(r"^[「“`*\s]*(?:" + "|".join(_PENDING_MARKERS) + ")")
_THIS_SPEC = re.compile("一日运行" + "保真|dayrun-" + "fidelity")
_VERSION_HEADING = re.compile(r"^## 版本(?:表|历史)?\s*$")
#: 研究与验收记录的目录：其中的表格叙述各特性标记的来龙去脉，不扫描。
_RESEARCH = "docs/dev/research/"
_CELL = re.compile(r"(?<!\\)\|")


def _ledger_rows() -> list[tuple[str, str]]:
    """「验收台账」逐行的（契约点，必须通过的验证）。"""
    (table,) = tables(SPEC_PATH, LEDGER_HEADING)
    header, *rows = table
    assert header == ["契约点", "必须通过的验证"]
    return [(row[0], row[1]) for row in rows]


def _rulings() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """「裁决记录」两张表逐行的（编号，状态）：第一张是会话中已定的方向，第二张是推荐与另一选项。"""
    first, second = tables(SPEC_PATH, RULINGS_HEADING)
    assert first[0] == ["#", "事项", "结论", "状态"]
    assert second[0] == ["#", "事项", "推荐", "另一选项及后果", "状态"]
    return [(row[0], row[-1]) for row in first[1:]], [(row[0], row[-1]) for row in second[1:]]


def _registered() -> dict[str, list[str]]:
    """全部登记项：台账行、裁决编号与警告码各自的测试节点。"""
    entries = {f"验收台账：{row}": nodes for row, nodes in LEDGER.items()}
    entries.update({f"裁决 {number}": nodes for number, nodes in RULINGS.items()})
    entries.update({f"输入警告码 {code}": [node] for code, node in WARNING_CODE_TESTS.items()})
    return entries


def _test_body_constants(node_id: str) -> set[str]:
    """测试函数体（去掉文档字符串）里的全部字符串常量。"""
    path_text, *parts = node_id.split("::")
    tree = ast.parse((REPO_ROOT / path_text).read_text(encoding="utf-8"))
    scope = tree.body
    if len(parts) == 2:
        scope = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == parts[0]).body
    function = next(node for node in scope
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[-1])
    body = function.body[1:] if ast.get_docstring(function) is not None else function.body
    return {node.value for statement in body for node in ast.walk(statement)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def _feature_files() -> list[str]:
    """需要扫描的文件：本特性新增或改动的生产文件与新增测试文件。"""
    return [*FEATURE_PRODUCTION_FILES, *FEATURE_TEST_FILES]


def _pending_line(line: str) -> bool:
    """一行含本特性的临时标记，且是状态格以标记开头的表格行、引用块行或点名本规格的行。"""
    if not _PENDING.search(line):
        return False
    cells = [cell.strip() for cell in _CELL.split(line)[1:-1]] if line.startswith("|") else []
    return any(_STATUS_CELL.match(cell) for cell in cells) or line.startswith(">") or bool(_THIS_SPEC.search(line))


def _document_markers(path: Path, relative: str) -> list[str]:
    """一份文档里仍在的临时标记，逐条写成「路径:行号: 原文」；版本表与版本历史中的表格行不扫描。"""
    found, history = [], False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("## "):
            history = bool(_VERSION_HEADING.match(line))
        if history and line.startswith("|"):
            continue
        if _pending_line(line):
            found.append(f"{relative}:{number}: {line.strip()[:80]}")
    return found


def _pending_markers(root: Path) -> list[str]:
    """root/docs 下（研究与验收记录目录除外）全部 Markdown 文档里仍在的临时标记。"""
    documents = {path.relative_to(root).as_posix(): path for path in (root / "docs").rglob("*.md")}
    return [marker for relative in sorted(documents) if not relative.startswith(_RESEARCH)
            for marker in _document_markers(documents[relative], relative)]


def test_every_ledger_row_is_registered_and_every_registered_row_exists() -> None:
    """登记表的键与规格「验收台账」各行首列一一对应：规格增加的行未登记、登记的行已不在规格中都失败。"""
    rows = [row for row, _ in _ledger_rows()]
    assert rows, "验收台账表格为空或未找到"
    assert len(rows) == len(set(rows)), f"台账首列重复: {rows}"
    assert sorted(set(rows) - set(LEDGER)) == [], "台账行未登记测试"
    assert sorted(set(LEDGER) - set(rows)) == [], "登记的行已不在台账中"


def test_rulings_are_decided_except_the_registered_and_the_withdrawn_and_each_decided_one_is_registered() -> None:
    """「裁决记录」第一张表恰为 D1、U1–U4，第二张表恰为 D2–D19、S1–S11；除 D13（登记，范围外）与 S2（撤回）外状态都以
    已定开头；每条已定裁决都登记了验证其定下做法的测试，只有没有要建之物的 U4 只断言状态。"""
    session, recommended = _rulings()
    assert [number for number, _ in session] == DECIDED_IN_SESSION
    assert [number for number, _ in recommended] == RECOMMENDED
    statuses = dict(session + recommended)
    undecided = {number: status for number, status in statuses.items() if not status.startswith(DECIDED)}
    assert sorted(undecided) == ["D13", "S2"], undecided
    assert {number: status for number, status in statuses.items()
            if number in STATUS_ONLY and not status.startswith(STATUS_ONLY[number])} == {}
    decided = {number for number, status in statuses.items() if status.startswith(DECIDED)}
    assert sorted(RULINGS) == sorted(decided - set(STATUS_ONLY))
    assert set(STATUS_ONLY) <= set(statuses) and not set(STATUS_ONLY) & set(RULINGS)


def test_report_only_rows_stay_report_only_in_the_spec() -> None:
    """只报告的行（WC 与 BIZ 全天复跑）在规格中仍写明只报告、不作门禁：台账表前的说明句照旧，行首列仍带「只报告」；它登记的
    是所读质量报告各节的测试，说明写在登记表里。"""
    verification = dict(_ledger_rows())
    notes = [line for line in section(SPEC_PATH, LEDGER_HEADING) if line.strip() and not line.startswith("|")]
    assert any(REPORT_ONLY_PHRASE in line for line in notes), notes
    assert REPORT_ONLY
    for row, note in REPORT_ONLY.items():
        assert row in LEDGER and row in verification and note
        assert REPORT_ONLY_MARK in row


def test_every_input_warning_code_has_one_registered_example() -> None:
    """「输入警告」行逐码各一例：登记的码恰是警告码闭集（十个），每例都在该行登记的测试里，且测试体本身断言这个码。"""
    assert tuple(WARNING_CODE_TESTS) == INPUT_WARNING_CODES and len(INPUT_WARNING_CODES) == 10
    assert set(WARNING_CODE_TESTS.values()) <= set(LEDGER["输入警告"])
    missing = {code: node for code, node in WARNING_CODE_TESTS.items() if code not in _test_body_constants(node)}
    assert not missing, missing


def test_every_registered_node_resolves_to_a_collected_unskipped_test() -> None:
    """每个登记项至少一个节点、节点不重复且形如 path::name 或 path::Class::name；每个节点都解析到存在、会被收集且
    未被跳过的测试函数。"""
    entries = _registered()
    empty = sorted(name for name, nodes in entries.items() if not nodes)
    assert not empty, f"entries without tests: {empty}"
    duplicated = sorted(name for name, nodes in entries.items() if len(nodes) != len(set(nodes)))
    assert not duplicated, f"entries listing a node twice: {duplicated}"
    nodes = sorted({node for items in entries.values() for node in items})
    malformed = [node for node in nodes if not NODE_ID.match(node)]
    assert not malformed, f"malformed node ids: {malformed}"
    problems = {node: reason for node in nodes if (reason := resolve_problem(node))}
    assert not problems, problems


def test_frozen_feature_file_lists_name_existing_files_once() -> None:
    """冻结的本特性文件清单都存在、不重复；生产清单只含生产目录，测试清单只含 tests 目录，且含本守卫与登记表自身。"""
    files = _feature_files()
    assert len(files) == len(set(files))
    assert [path for path in files if not (REPO_ROOT / path).is_file()] == []
    assert all(path.split("/")[0] in {"src", "custom", "scripts", "tools"} for path in FEATURE_PRODUCTION_FILES)
    assert all(path.startswith("tests/") for path in FEATURE_TEST_FILES)
    assert {"tests/test_dayrun_fidelity_acceptance.py", "tests/dayrun_fidelity_acceptance.py",
            "tests/acceptance_guard.py"} <= set(FEATURE_TEST_FILES)


def test_feature_files_carry_no_deferral_markers() -> None:
    """本特性新增或改动的生产文件（含查看器页面）与新增测试文件中没有任何延期标记。"""
    hits = []
    for relative in _feature_files():
        for number, line in enumerate((REPO_ROOT / relative).read_text(encoding="utf-8").splitlines(), 1):
            if DEFERRAL.search(line):
                hits.append(f"{relative}:{number}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_feature_python_files_have_no_stub_functions() -> None:
    """本特性新增或改动的 Python 生产文件与新增测试文件中没有只有 pass、省略号或文档字符串的空壳函数。"""
    stubs = {relative: found for relative in _feature_files() if relative.endswith(".py")
             if (found := stub_functions(ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))))}
    assert not stubs, stubs


def test_docs_carry_no_pending_markers_of_this_feature() -> None:
    """docs/ 不再留有本特性冻结时写下的临时标记：没有以标记开头的状态格，没有含标记的引用块行，点名本规格的行也不含标记
    （判定见文件头说明；版本表与版本历史中的表格行、docs/dev/research/ 不扫描）。"""
    markers = _pending_markers(REPO_ROOT)
    assert not markers, "\n".join(markers)


def test_resolver_rejects_missing_uncollected_and_skipped_tests(tmp_path: Path) -> None:
    """解析器有牙齿：文件、类或函数不存在，模块不会被收集，函数或类上的跳过装饰器、参数化里的跳过、列表形式的 pytestmark、
    测试体里的预期失败都判为问题；模块级测试、异步测试、类中的测试与参数化节点都能解析。"""
    skip, fail = "sk" + "ip", "x" + "fail"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text(
        "import pytest\n"
        "def test_plain():\n    assert True\n"
        "async def test_async():\n    assert True\n"
        f"@pytest.mark.parametrize('value', [pytest.param(1, marks=pytest.mark.{skip})])\n"
        "def test_param_skipped(value):\n    assert value\n"
        f"def test_inline():\n    pytest.{fail}('later')\n"
        f"@pytest.mark.{skip}\nclass TestSkipped:\n    def test_member(self):\n        assert True\n"
        "class TestGroup:\n    def test_member(self):\n        assert True\n"
        "@pytest.mark.parametrize('value', [1, 2])\ndef test_param(value):\n    assert value\n",
        encoding="utf-8")
    (tmp_path / "tests" / "test_listed_module.py").write_text(
        f"import pytest\npytestmark = [pytest.mark.{skip}]\ndef test_any():\n    assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "support.py").write_text("def test_helper():\n    assert True\n", encoding="utf-8")
    sample = "tests/test_sample.py::"
    for node in (sample + "test_plain", sample + "test_async", sample + "TestGroup::test_member",
                 sample + "test_param[2]"):
        assert resolve_problem(node, tmp_path) is None, node
    for node in (sample + "test_param_skipped[1]", sample + "test_inline", sample + "TestSkipped::test_member",
                 sample + "TestGroup::test_other", sample + "test_absent", "tests/test_listed_module.py::test_any",
                 "tests/support.py::test_helper", "tests/test_gone.py::test_plain"):
        assert resolve_problem(node, tmp_path) is not None, node


@pytest.mark.parametrize("line", [
    "# " + "TO" + "DO: later", "# " + "FIX" + "ME", "# " + "X" + "XX", "raise NotImplemented" + "Error",
    "pytest." + "sk" + "ip('x')", "@pytest.mark." + "sk" + "ip", "@pytest.mark." + "sk" + "ipif(True, reason='x')",
    "@mark." + "sk" + "ip", "pytest.importor" + "sk" + "ip('x')", "@pytest.mark." + "x" + "fail",
    "\"\"\"@brief 阈值" + "待" + "定。\"\"\"", "# 文案" + "尚需" + "定稿", "return None  # " + "待" + "实现",
])
def test_deferral_pattern_catches_every_marker(line: str) -> None:
    """每一种延期标记都被扫描正则抓住。"""
    assert DEFERRAL.search(line)


def test_deferral_pattern_leaves_ordinary_code_alone() -> None:
    """普通代码与中文说明不误报：跳过与失败的普通词、同字不同义的说法都放行。"""
    for line in ("def skip_until(items):", "status = 'failed'", "决定了。", "待办层的目标", "已实现并通过离线验收"):
        assert not DEFERRAL.search(line), line


def test_stub_detection_catches_pass_ellipsis_and_docstring_only_bodies() -> None:
    """空壳判定：只有 pass、只有省略号、只有文档字符串的函数都算，有实际语句的函数不算。"""
    tree = ast.parse("def a():\n    pass\n\nasync def b():\n    '''说明。'''\n    ...\n\n"
                     "def c():\n    '''说明。'''\n    return 1\n\nclass D:\n    def e(self):\n        '''说明。'''\n")
    assert stub_functions(tree) == ["a:1", "b:4", "e:13"]


def _write_docs(root: Path, files: dict[str, str]) -> None:
    """在 root/docs 下写出给定的 Markdown 文档（相对路径 → 正文）。"""
    for relative, text in files.items():
        path = root / "docs" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_pending_marker_scan_catches_every_form_and_leaves_history_alone(tmp_path: Path) -> None:
    """文档标记判定有牙齿：以四个标记开头的状态格、含标记的引用块行、点名本规格（中文名或文件名片段）且含标记的正文与
    表格行都报出；不点名本规格的图例句与其他规格的行、版本表与版本历史中的表格行、docs/dev/research/ 下的记录都放行——
    同样的记录放到研究目录之外就报出。"""
    feature, pending = "一日运行" + "保真", "离线验收" + "待执行"
    verified, recheck, unimplemented = "本次" + pending, "待" + "重验", "修订条款" + "尚未" + "实现"
    record = (f"| VF1 | 按{feature}规格修订的行记「{pending}」 | 通过 |\n"
              f"> {feature}规格：两行仍记“{pending}”。\n")
    _write_docs(tmp_path, {
        "dev/SPEC-a.md": (
            f"# 甲规格\n\n> 状态：FROZEN；按其他规格修订，两行仍记“{pending}”。\n> 日期：2026-09-29\n\n"
            f"## 验收台账\n\n`{pending}` 表示本次新增实现仍须在最终源码身份运行并登记结果。\n\n"
            "| 行 | 观察 | 状态 |\n| --- | --- | --- |\n"
            f"| 甲 | 观察 | {pending} |\n| 乙 | 观察 | 通过 |\n| 丙 | 观察 | {verified} |\n| 丁 | 观察 | {recheck} |\n"
            f"| 戊 | 见[{feature}规格](SPEC-dayrun-fidelity.md) | 离线覆盖 |\n\n"
            f"按{feature}规格修订的两行曾记“{pending}”，已改回。\n\n"
            "## 版本表\n\n| 版本 | 日期 | 修订内容 |\n| --- | --- | --- |\n"
            f"| 0.2 | 2026-09-29 | 按{feature}规格修订，两行记「{pending}」 |\n"),
        "dev/SPEC-b.md": (
            "# 乙规格\n\n> 状态：FROZEN。\n\n## 版本历史\n\n| 版本 | 日期 | 修订内容 |\n| --- | --- | --- |\n"
            f"| v1 | 2026-09-27 | 改动行状态记「{verified}」 |\n"),
        "INDEX.md": (
            "# 索引\n\n## 文档地图\n\n| 文档 | 内容 | 状态 |\n| --- | --- | --- |\n"
            f"| 甲 | 摘要；由{feature}规格修订，{unimplemented} | FROZEN |\n"
            f"| 乙 | 摘要；由其他规格修订，{unimplemented} | FROZEN |\n"
            f"| 丙 | 摘要；见 PROPOSAL-dayrun-fidelity.md，{recheck} | FROZEN |\n"),
        "dev/notes.md": record,
        "dev/research/record.md": record,
    })
    found = [marker.split(": ", 1)[0] for marker in _pending_markers(tmp_path)]
    assert found == ["docs/INDEX.md:7", "docs/INDEX.md:9", "docs/dev/SPEC-a.md:3", "docs/dev/SPEC-a.md:12",
                     "docs/dev/SPEC-a.md:14", "docs/dev/SPEC-a.md:15", "docs/dev/SPEC-a.md:18",
                     "docs/dev/notes.md:1", "docs/dev/notes.md:2"]
