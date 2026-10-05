"""个人事务与工作安排规格的完整性守卫：台账逐行登记到阶段与测试、没有延后的实现、规格变异逐句有登记并被杀死。

@file     test_errands_workday_acceptance.py
@brief    以 tests/errands_workday_acceptance.py 为数据源，测试时解析 docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库「验收台账」一节，
          逐条判定：这一节只有一张表，表头为「契约点、阶段、必须通过的验证」三列；首列与登记表的键一一对应且顺序相同，
          阶段列与登记的阶段相等；已完成阶段的行必须列出节点，全部登记节点（含变异的杀手）解析到存在、会被收集且未被
          跳过的测试；只报告的行首列仍带「只报告」，表前仍写明复跑指标只报告、不作门禁；特性文件存在、不重复，特性文件
          与登记了节点的测试文件里没有延期标记、没有空壳函数；规格「源码变异必须覆盖」一段逐句登记，已完成阶段的每一句
          至少对应一个登记变异；每个变异落在主进程执行的生产模块上、原文恰好出现一次、可编译，杀手是已登记的行为用例；
          每个变异在基线通过之后被杀死（退出码 1，没有收集错误）。
@note     表格解析、节点解析、延期标记正则与空壳判定取自共用模块 tests/acceptance_guard.py；变异执行取自共用模块
          tests/mutation_runner.py（与工具交互台账共用，回执目录分开）。「实施阶段全部完成」的判定随阶段 5
          收尾加入：阶段 1–5 必须都在已完成集合里。本文件自身不含任何延期标记字面量。
"""

from __future__ import annotations

import ast
import json

import pytest

from tests import tool_interaction_acceptance
from tests.acceptance_guard import DEFERRAL, NODE_ID, acceptance_section, resolve_problem, stub_functions
from tests.errands_workday_acceptance import (
    EXTRA_MUTATIONS,
    FEATURE_PRODUCTION_FILES,
    FEATURE_TEST_FILES,
    IMPLEMENTATION_STAGES,
    LEDGER,
    MUTATIONS,
    RECEIPTS,
    REPORT_ONLY,
    SPEC_MUTATIONS,
    STAGE_IDS,
    STAGES_DONE,
    run_behavior,
)
from tests.mutation_runner import REPO_ROOT, mutated_source, source_location

LEDGER_HEADING = "## 验收台账"
LEDGER_HEADER = ["契约点", "阶段", "必须通过的验证"]
#: 只报告行在规格中的固定说法（台账表前的说明句）与台账行首列必带的字样。
REPORT_ONLY_PHRASE = "复跑指标只报告，不作门禁"
REPORT_ONLY_MARK = "只报告"
#: 规格变异段的起句与收句：两者之间（可跨多行）是逐句的变异清单，句与句以分号或顿号相接，段末是句号。
#: 生产模块的两个根：路径前缀 → 模块名前缀（src 下是已安装的包，custom 下按仓库根的命名空间导入）。
_MODULE_ROOTS = {"src/": "", "custom/": "custom."}


def _ledger_rows() -> list[list[str]]:
    """「验收台账」唯一一张表的各行（去掉表头）：契约点、阶段、必须通过的验证。"""
    (table,) = acceptance_section("errands-workday", "验收台账")["tables"]
    header, *rows = table
    assert header == LEDGER_HEADER
    return rows



def _killer_nodes() -> list[str]:
    """全部登记变异的杀手节点（去重，保持登记顺序）。"""
    return list(dict.fromkeys(node for mutation in MUTATIONS for node in mutation.tests))


def _registered_nodes() -> list[str]:
    """全部登记节点：台账各行的测试与变异的杀手（去重）。"""
    ledger = [node for entry in LEDGER.values() for node in entry["tests"]]
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


def test_ledger_section_has_one_three_column_table_matching_the_registration() -> None:
    """「验收台账」一节只有一张三列表；首列与登记表的键一一对应且顺序相同（规格增删行而登记表不跟随即失败），阶段列与
    登记的阶段相等，阶段号都在规格「实施计划」的阶段之内。"""
    rows = _ledger_rows()
    names = [row[0] for row in rows]
    assert names and len(names) == len(set(names)), names
    assert sorted(set(names) - set(LEDGER)) == [], "台账行未登记"
    assert sorted(set(LEDGER) - set(names)) == [], "登记的行已不在台账中"
    assert list(LEDGER) == names
    assert {row[0]: row[1] for row in rows} == {name: entry["stage"] for name, entry in LEDGER.items()}
    assert {entry["stage"] for entry in LEDGER.values()} <= STAGE_IDS
    assert STAGES_DONE <= STAGE_IDS
    assert all(set(entry) == {"stage", "tests"} for entry in LEDGER.values())


def test_rows_of_done_stages_list_tests_and_every_registered_node_resolves() -> None:
    """已完成阶段的行必须列出节点；任何行列出的节点、任何变异的杀手都不重复、形如 path::name 或 path::Class::name，
    并解析到存在、会被收集且未被跳过的测试函数。"""
    empty = sorted(name for name, entry in LEDGER.items() if entry["stage"] in STAGES_DONE and not entry["tests"])
    assert not empty, f"rows of finished stages without tests: {empty}"
    duplicated = sorted(name for name, entry in LEDGER.items() if len(entry["tests"]) != len(set(entry["tests"])))
    assert not duplicated, f"rows listing a node twice: {duplicated}"
    nodes = _registered_nodes()
    assert nodes
    malformed = [node for node in nodes if not NODE_ID.match(node)]
    assert not malformed, f"malformed node ids: {malformed}"
    problems = {node: reason for node in nodes if (reason := resolve_problem(node))}
    assert not problems, problems


def test_report_only_rows_stay_report_only_in_the_spec() -> None:
    """只报告的行（配对全天复跑）在规格中仍写明只报告、不作门禁：台账表前的说明句照旧，行首列仍带「只报告」；凡首列带
    「只报告」的台账行都登记了说明。"""
    names = [row[0] for row in _ledger_rows()]
    assert dict(REPORT_ONLY) == acceptance_section("errands-workday", "report_only")
    assert REPORT_ONLY and all(REPORT_ONLY.values())
    assert set(REPORT_ONLY) == {name for name in names if REPORT_ONLY_MARK in name}
    assert set(REPORT_ONLY) <= set(LEDGER)


def test_feature_file_lists_name_existing_files_once() -> None:
    """特性文件清单都存在、不重复；生产清单只含生产目录，测试清单只含 tests 目录，且含本守卫、登记表与共用变异执行器；
    本台账的回执目录与工具交互台账的分开。"""
    files = [*FEATURE_PRODUCTION_FILES, *FEATURE_TEST_FILES]
    assert len(files) == len(set(files))
    assert [path for path in files if not (REPO_ROOT / path).is_file()] == []
    assert all(path.split("/")[0] in {"src", "custom", "scripts", "tools"} for path in FEATURE_PRODUCTION_FILES)
    assert all(path.startswith("tests/") for path in FEATURE_TEST_FILES)
    assert {"tests/test_errands_workday_acceptance.py", "tests/errands_workday_acceptance.py",
            "tests/mutation_runner.py"} <= set(FEATURE_TEST_FILES)
    assert RECEIPTS != tool_interaction_acceptance.RECEIPTS


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


def test_every_implementation_stage_is_done() -> None:
    """实施阶段全部完成：改代码与测试的阶段 1–4 与全量门禁阶段 5 都在 STAGES_DONE 中，其台账行与变异句由本守卫判定。

    阶段 0 是冻结提交上的基线全量，没有台账行；阶段 6 是配对全天复跑，只报告、不作门禁，只在用户要求时启动；
    阶段 7 是实现后文档。三者不改代码，不属于实施阶段。
    """
    assert IMPLEMENTATION_STAGES < STAGE_IDS
    assert STAGE_IDS - IMPLEMENTATION_STAGES == {"0", "6", "7"}
    missing = sorted(IMPLEMENTATION_STAGES - STAGES_DONE)
    assert not missing, f"implementation stages not done: {missing}"


def test_spec_mutation_sentences_are_registered_in_order_and_done_stages_are_covered() -> None:
    """规格「源码变异必须覆盖」一段逐句登记：登记的各句按顺序首尾相接（以分号或顿号相隔）恰好拼回规格原文，规格增删
    改一句而登记表不跟随即失败；每句的阶段在规格阶段之内，点名的变异都已登记；已完成阶段的每一句至少一个登记变异。"""
    assert list(SPEC_MUTATIONS) == acceptance_section("errands-workday", "mutation_requirements")
    assert all(set(entry) == {"stage", "mutations"} for entry in SPEC_MUTATIONS.values())
    assert {entry["stage"] for entry in SPEC_MUTATIONS.values()} <= STAGE_IDS
    names = {mutation.name for mutation in MUTATIONS}
    unknown = {sentence: entry["mutations"] for sentence, entry in SPEC_MUTATIONS.items()
               if not set(entry["mutations"]) <= names}
    assert not unknown, unknown
    uncovered = [sentence for sentence, entry in SPEC_MUTATIONS.items()
                 if entry["stage"] in STAGES_DONE and not entry["mutations"]]
    assert not uncovered, f"spec mutation sentences of finished stages without a registered mutation: {uncovered}"


def test_mutations_are_named_once_and_anchored_in_main_process_production_code() -> None:
    """每个登记变异：名字唯一，且恰好归到规格的某一句或登记了出处的额外变异之一；落在 src 或 custom 下的 Python 生产
    模块上，模块名就是该文件在主进程里的导入名；原文在源文件恰好出现一次、替换后可编译；杀手非空且都是登记在台账某一行
    的行为用例。"""
    names = [mutation.name for mutation in MUTATIONS]
    assert len(names) == len(set(names)), names
    by_sentence = [name for entry in SPEC_MUTATIONS.values() for name in entry["mutations"]]
    assert len(by_sentence) == len(set(by_sentence)), by_sentence
    assert not set(by_sentence) & set(EXTRA_MUTATIONS)
    assert set(by_sentence) | set(EXTRA_MUTATIONS) == set(names)
    assert all(EXTRA_MUTATIONS.values())
    ledger_nodes = {node for entry in LEDGER.values() for node in entry["tests"]}
    for mutation in MUTATIONS:
        module, source = source_location(mutation.symbol)
        assert _module_name(source) == module, mutation.name
        original, changed = mutated_source(mutation)
        assert original != changed, mutation.name
        assert mutation.tests and set(mutation.tests) <= ledger_nodes, mutation.name


@pytest.fixture(scope="session")
def errands_mutation_baseline(tmp_path_factory):
    """同一源码上全部杀手用例必须先通过，随后才运行各个独立变异。"""
    directory = tmp_path_factory.mktemp("errands-workday-baseline")
    result = run_behavior(tuple(_killer_nodes()), directory)
    (directory / "baseline-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
    return result


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_errands_workday_source_mutation_is_killed(mutation, errands_mutation_baseline, tmp_path):
    """每个非等价源码变异必须被行为断言杀死；未执行、存活、收集错误均不放行。"""
    result = run_behavior(mutation.tests, tmp_path, mutation)
    (tmp_path / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["original_sha256"] != result["mutated_sha256"]
    assert result["exit_code"] == 1, result["stdout"] + result["stderr"]
    assert "failed" in result["stdout"] and "ERROR collecting" not in result["stdout"]
