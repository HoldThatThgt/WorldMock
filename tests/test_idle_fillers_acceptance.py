"""让「等待」成为例外规格的完整性守卫：台账逐行登记到阶段、测试与实现，没有延后的实现，规格变异都被杀死。

@file     test_idle_fillers_acceptance.py
@brief    以 tests/fixtures/idle-fillers-acceptance.json 与 tests/idle_fillers_acceptance.py 为数据源，逐条判定：
          docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-验收台账 的契约点表（契约点、阶段、必须通过的验证三列）、行为用例表
          与变异表分别与登记一一对应且顺序相同，阶段列与登记相等；每行的规格锚点存在；已完成阶段的行列出会被收集、未被跳过、
          不是空壳的测试节点与解析到真实定义的实现符号，被引用的行为用例都有节点；只报告的阶段不登记节点；已完成阶段的变异
          表每个分句至少一个变异；特性文件存在、不重复，特性文件与登记了节点的测试文件里没有延期标记与空壳函数；每个变异名字
          唯一，落在主进程执行的生产模块上，原文在所登记的定义内恰好出现一次、变异后可编译，杀手是登记在台账某一行的行为用例；
          全部杀手用例在未变异的源码上先通过，随后每个变异被杀死（退出码 1，没有收集错误）。
@note     表格解析取规格正文的稳定章节；节点解析、延期标记正则与空壳判定取自共用模块 tests/acceptance_guard.py；符号解析
          取自文档门禁；变异执行取自共用模块 tests/mutation_runner.py（回执目录与既有两个登记分开），按变异参数化的执行
          用例由分层机制自动标为变异层。「实施阶段全部完成」的判定随阶段 5 收尾加入。
"""

from __future__ import annotations

import ast
import json
import re

import pytest

from scripts.documentation_gate import symbol_problem
from scripts.documentation_refs import section_text
from tests import errands_workday_acceptance, tool_interaction_acceptance
from tests.acceptance_guard import DEFERRAL, NODE_ID, resolve_problem, stub_functions
from tests.idle_fillers_acceptance import (
    BEHAVIOUR_CASES,
    FEATURE_PRODUCTION_FILES,
    FEATURE_TEST_FILES,
    IMPLEMENTATION_STAGES,
    MUTATION_ROWS,
    MUTATIONS,
    RECEIPTS,
    REPORT_ONLY_STAGES,
    STAGE_IDS,
    STAGES_DONE,
    run_behavior,
)
from tests.mutation_runner import REPO_ROOT, mutated_source, source_location

SPEC = "docs/dev/SPEC-idle-fillers.md"
LEDGER_ANCHOR = "spec-idle-fillers-验收台账"
LEDGER_HEADER = ["契约点", "阶段", "必须通过的验证"]
MUTATION_HEADER = ["变异", "阶段", "被谁杀死"]
CASE_HEADER = ["编号", "用例验证什么"]
ROW_KEYS = {"stage", "spec", "tests", "implementation"}
REPORT_ONLY_MARK = "只报告"
#: 生产模块的两个根：路径前缀 → 模块名前缀（src 下是已安装的包，custom 下按仓库根的命名空间导入）。
_MODULE_ROOTS = {"src/": "", "custom/": "custom."}


def _ledger() -> dict:
    """机器台账：契约点 → 阶段、规格锚点、测试节点与实现符号，保持登记顺序。"""
    return json.loads((REPO_ROOT / "tests/fixtures/idle-fillers-acceptance.json").read_text(encoding="utf-8"))


def _spec_tables() -> list[list[list[str]]]:
    """规格「验收台账」一节里的全部表格，每张表是去掉分隔行的单元格行（首行为表头）。"""
    body = section_text((REPO_ROOT / SPEC).read_text(encoding="utf-8"), LEDGER_ANCHOR)
    tables, current = [], None
    for line in body.splitlines():
        if not line.startswith("|"):
            current = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if current is None:
            current = []
            tables.append(current)
        if not set("".join(cells)) <= {"-", " "}:
            current.append(cells)
    return tables


def _spec_table(header: list[str]) -> list[list[str]]:
    """表头恰为 header 的唯一一张表的各行（去掉表头）。"""
    (table,) = [table for table in _spec_tables() if table[0] == header]
    return table[1:]


def _spec_rows() -> list[list[str]]:
    """规格「验收台账」的契约点表：契约点、阶段、必须通过的验证。"""
    return _spec_table(LEDGER_HEADER)


def _killer_nodes() -> list[str]:
    """全部登记变异的杀手节点（去重，保持登记顺序）。"""
    return list(dict.fromkeys(node for mutation in MUTATIONS for node in mutation.tests))


def _registered_nodes() -> list[str]:
    """全部登记节点：台账各行的测试与变异的杀手（去重）。"""
    ledger = [node for entry in _ledger().values() for node in entry["tests"]]
    return list(dict.fromkeys([*ledger, *_killer_nodes()]))


def _scanned_files() -> list[str]:
    """延期标记与空壳函数的扫描范围：特性生产文件、特性测试文件与登记了节点的测试文件（去重）。"""
    registered = sorted({node.split("::")[0] for node in _registered_nodes()})
    return list(dict.fromkeys([*FEATURE_PRODUCTION_FILES, *FEATURE_TEST_FILES, *registered]))


def _module_name(source: str) -> str | None:
    """生产源文件路径对应的主进程模块名；不在两个生产根下或不是 Python 文件时返回 None。"""
    for prefix, package in _MODULE_ROOTS.items():
        if source.startswith(prefix) and source.endswith(".py"):
            return package + source[len(prefix):-len(".py")].replace("/", ".")
    return None


def test_ledger_rows_match_the_spec_table_in_order_and_stage() -> None:
    """规格台账表的首列与登记的契约点一一对应且顺序相同，阶段列与登记相等；每行只有四个键，阶段号在规格阶段之内，
    已完成阶段是阶段号的子集；只报告阶段的行首列带「只报告」且不登记节点与实现符号。"""
    ledger, rows = _ledger(), _spec_rows()
    assert [row[0] for row in rows] == list(ledger)
    assert {row[0]: row[1] for row in rows} == {name: entry["stage"] for name, entry in ledger.items()}
    assert all(set(entry) == ROW_KEYS for entry in ledger.values())
    assert {entry["stage"] for entry in ledger.values()} <= STAGE_IDS
    assert STAGES_DONE <= STAGE_IDS and IMPLEMENTATION_STAGES < STAGE_IDS
    assert not IMPLEMENTATION_STAGES & REPORT_ONLY_STAGES
    report_only = {name for name, entry in ledger.items() if entry["stage"] in REPORT_ONLY_STAGES}
    assert report_only == {name for name in ledger if REPORT_ONLY_MARK in name}
    assert all(not ledger[name]["tests"] and not ledger[name]["implementation"] for name in report_only)


def test_every_row_points_to_an_existing_spec_anchor() -> None:
    """每行的规格引用是本规格的稳定章节标识。"""
    text = (REPO_ROOT / SPEC).read_text(encoding="utf-8")
    for name, entry in _ledger().items():
        path, anchor = entry["spec"].split("#")
        assert path == SPEC, name
        assert f'<a name="{anchor}"></a>' in text, (name, anchor)


def test_rows_of_done_stages_list_evidence_and_every_registered_node_and_symbol_resolves() -> None:
    """已完成阶段的行必须列出节点与实现符号；任何行列出的节点、任何变异的杀手都不重复、形如 path::name，并解析到存在、
    会被收集且未被跳过、不是空壳的测试函数；实现符号解析到真实定义。"""
    ledger = _ledger()
    empty = sorted(name for name, entry in ledger.items()
                   if entry["stage"] in STAGES_DONE and not (entry["tests"] and entry["implementation"]))
    assert not empty, f"rows of finished stages without evidence: {empty}"
    duplicated = sorted(name for name, entry in ledger.items() if len(entry["tests"]) != len(set(entry["tests"])))
    assert not duplicated, f"rows listing a node twice: {duplicated}"
    nodes = _registered_nodes()
    malformed = [node for node in nodes if not NODE_ID.match(node)]
    assert not malformed, f"malformed node ids: {malformed}"
    problems = {node: reason for node in nodes if (reason := resolve_problem(node))}
    assert not problems, problems
    for node in nodes:
        path, *_, function = node.split("[")[0].split("::")
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
        body = next(item for item in ast.walk(tree)
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function)
        assert not stub_functions(body), node
    for name, entry in ledger.items():
        for reference in entry["implementation"]:
            target, symbol = reference.split("::", 1)
            assert symbol_problem(REPO_ROOT, target, symbol) is None, (name, reference)


def test_feature_file_lists_name_existing_files_once() -> None:
    """特性文件清单都存在、不重复；生产清单只含生产目录，测试清单只含 tests 目录且含本守卫与登记表；本台账的回执目录
    与既有两个变异登记的分开。"""
    files = [*FEATURE_PRODUCTION_FILES, *FEATURE_TEST_FILES]
    assert len(files) == len(set(files))
    assert [path for path in files if not (REPO_ROOT / path).is_file()] == []
    assert all(path.split("/")[0] in {"src", "custom", "tools"} for path in FEATURE_PRODUCTION_FILES)
    assert all(path.startswith("tests/") for path in FEATURE_TEST_FILES)
    assert {"tests/test_idle_fillers_acceptance.py", "tests/idle_fillers_acceptance.py"} <= set(FEATURE_TEST_FILES)
    assert RECEIPTS not in {tool_interaction_acceptance.RECEIPTS, errands_workday_acceptance.RECEIPTS}


def test_feature_and_registered_files_carry_no_deferral_markers() -> None:
    """特性生产文件、特性测试文件与登记了节点的测试文件里没有任何延期标记（跳过与预期失败的标记在内）。"""
    hits = []
    for relative in _scanned_files():
        for number, line in enumerate((REPO_ROOT / relative).read_text(encoding="utf-8").splitlines(), 1):
            if DEFERRAL.search(line):
                hits.append(f"{relative}:{number}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_feature_and_registered_python_files_have_no_stub_functions() -> None:
    """特性生产文件、特性测试文件与登记了节点的测试文件里没有只有 pass、省略号或文档字符串的空壳函数。"""
    stubs = {relative: found for relative in _scanned_files() if relative.endswith(".py")
             if (found := stub_functions(ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))))}
    assert not stubs, stubs


def test_mutations_are_named_once_and_anchored_in_main_process_production_code() -> None:
    """每个登记变异：名字唯一，落在 src 或 custom 下的 Python 生产模块上，模块名就是该文件在主进程里的导入名；原文在所登记的
    定义内恰好出现一次、替换后可编译且与原文不同；杀手非空且都是登记在台账某一行的行为用例。"""
    names = [mutation.name for mutation in MUTATIONS]
    assert len(names) == len(set(names)), names
    ledger_nodes = {node for entry in _ledger().values() for node in entry["tests"]}
    for mutation in MUTATIONS:
        module, source = source_location(mutation.symbol)
        assert _module_name(source) == module, mutation.name
        original, changed = mutated_source(mutation)
        assert original != changed, mutation.name
        assert mutation.tests and set(mutation.tests) <= ledger_nodes, mutation.name


def test_behaviour_cases_match_the_spec_and_done_rows_list_their_nodes() -> None:
    """登记的行为用例编号与规格行为用例表首列一致且顺序相同；台账某行引用的 T 编号都已登记，该行阶段已完成时每个被引用的
    T 都有节点，且这些节点列在该行的测试里；任何登记节点都形如 path::name 并解析到会被收集的测试函数。"""
    assert list(BEHAVIOUR_CASES) == [row[0] for row in _spec_table(CASE_HEADER)]
    ledger = _ledger()
    for row in _spec_rows():
        referenced = re.findall(r"T\d+", row[2])
        assert set(referenced) <= set(BEHAVIOUR_CASES), (row[0], referenced)
        if ledger[row[0]]["stage"] in STAGES_DONE:
            for case in referenced:
                assert BEHAVIOUR_CASES[case], (row[0], case)
                assert set(BEHAVIOUR_CASES[case]) <= set(ledger[row[0]]["tests"]), (row[0], case)
    nodes = [node for cases in BEHAVIOUR_CASES.values() for node in cases]
    assert [node for node in nodes if not NODE_ID.match(node)] == []
    assert {node: reason for node in nodes if (reason := resolve_problem(node))} == {}


def test_mutation_rows_match_the_spec_table_and_cover_done_stages() -> None:
    """变异登记与规格变异表首列一一对应且顺序相同、阶段相等；每行的分句数等于首列以「；」分隔的分句数；已完成阶段的每个
    分句至少登记一个变异；每个登记变异恰好归属一个分句，且只有已完成阶段的行登记变异。"""
    rows = _spec_table(MUTATION_HEADER)
    assert list(MUTATION_ROWS) == [row[0] for row in rows]
    assert {row[0]: row[1] for row in rows} == {text: entry["stage"] for text, entry in MUTATION_ROWS.items()}
    assert all(set(entry) == {"stage", "clauses"} for entry in MUTATION_ROWS.values())
    names = []
    for text, entry in MUTATION_ROWS.items():
        assert len(entry["clauses"]) == len(text.split("；")), text
        if entry["stage"] in STAGES_DONE:
            assert all(entry["clauses"]), f"finished-stage mutation row without a mutation: {text}"
        else:
            assert not any(entry["clauses"]), f"mutation registered before its stage is done: {text}"
        names.extend(name for clause in entry["clauses"] for name in clause)
    assert sorted(names) == sorted(mutation.name for mutation in MUTATIONS)
    assert len(names) == len(set(names))


@pytest.fixture(scope="session")
def idle_fillers_mutation_baseline(tmp_path_factory):
    """同一源码上全部杀手用例必须先通过，随后才运行各个独立变异。"""
    directory = tmp_path_factory.mktemp("idle-fillers-baseline")
    result = run_behavior(tuple(_killer_nodes()), directory)
    (directory / "baseline-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
    return result


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_idle_fillers_source_mutation_is_killed(mutation, idle_fillers_mutation_baseline, tmp_path):
    """每个非等价源码变异必须被行为断言杀死；未执行、存活、收集错误均不放行。"""
    result = run_behavior(mutation.tests, tmp_path, mutation)
    (tmp_path / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["original_sha256"] != result["mutated_sha256"]
    assert result["exit_code"] == 1, result["stdout"] + result["stderr"]
    assert "failed" in result["stdout"] and "ERROR collecting" not in result["stdout"]
