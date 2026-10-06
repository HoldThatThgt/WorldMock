"""@brief 资源与规则简化的真实登记、阶段归属与源码变异完整性守卫。"""

from __future__ import annotations

import ast
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.documentation_gate import symbol_problem
from scripts.documentation_refs import explicit_anchors, section_text
from scripts.documentation_owners import owner_problem
from tests import rrs_acceptance as registration
from tests.acceptance_guard import DEFERRAL, NODE_ID, acceptance_section, resolve_problem, stub_functions
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, mutated_source, run_behavior, source_location

SPEC = "docs/dev/SPEC-resource-and-rule-simplification.md"
LEDGER_ANCHOR = "spec-rrs-验收台账"
MUTATION_ANCHOR = "spec-rrs-变异登记"
LEDGER_HEADER = ["契约点", "阶段", "必须通过的验证"]
CASE_HEADER = ["编号", "用例验证什么"]
MUTATION_HEADER = ["变异", "行为所有者阶段", "被谁杀死", "绑定契约点（与分句逐项配对）",
                   "最早可运行杀手阶段（与分句逐项配对）"]
PRODUCTION_ROOTS = {"src", "custom", "tools", "scripts"}


def _ledger() -> dict:
    """@brief 读取独立于规格的机器合同登记。"""
    return json.loads((REPO_ROOT / "tests/fixtures/rrs-acceptance.json").read_text(encoding="utf-8"))


def _spec_table(anchor: str, header: list[str]) -> list[list[str]]:
    """@brief 按独立锚点与唯一表头读取真实规格表，不复用另一个表的入口。"""
    body = section_text((REPO_ROOT / SPEC).read_text(encoding="utf-8"), anchor)
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
    (table,) = [table for table in tables if table[0] == header]
    return table[1:]


def _check_stages(done: frozenset[str]) -> None:
    """@brief 只接受完整集成波次及后续前缀，不能拆开阶段一到五的完成声明。"""
    allowed = {frozenset(), *(frozenset(str(stage) for stage in range(1, end + 1)) for end in range(5, 9))}
    assert done in allowed, f"invalid completed stage wave: {sorted(done)}"


def _check_ledger(ledger: dict, rows: list[list[str]], done: frozenset[str]) -> None:
    """@brief 台账首列、阶段和字段与规格相同，已关闭阶段有证据，只报告行不冒充实现。"""
    assert list(ledger) == [row[0] for row in rows], "contract registration differs from specification"
    text = (REPO_ROOT / SPEC).read_text(encoding="utf-8")
    for name, stage, _ in rows:
        entry = ledger[name]
        assert set(entry) == {"stage", "spec", "tests", "implementation"}, name
        assert entry["stage"] == stage and stage in registration.STAGE_IDS, name
        assert isinstance(entry["tests"], list) and isinstance(entry["implementation"], list), name
        assert len(entry["tests"]) == len(set(entry["tests"])), name
        assert len(entry["implementation"]) == len(set(entry["implementation"])), name
        path, anchor = entry["spec"].split("#")
        assert path == SPEC and f'<a name="{anchor}"></a>' in text, name
        report = stage in registration.REPORT_ONLY_STAGES
        assert report == ("只报告" in name), name
        if report:
            assert not entry["tests"] and not entry["implementation"], name
        elif stage in done:
            assert entry["tests"] and entry["implementation"], f"finished contract without evidence: {name}"


def _case_nodes(case: dict, stage: str | None = None) -> list[str]:
    """@brief 取全部已登记节点，或取指定阶段已经生效的所有者与集成节点。"""
    nodes = list(case["owner_nodes"]) if stage is None or int(case["owner_stage"]) <= int(stage) else []
    return nodes + [entry["node"] for entry in case["integration_nodes"]
                    if stage is None or int(entry["stage"]) <= int(stage)]


def _check_cases(cases: dict, rows: list[list[str]], ledger: dict, done: frozenset[str]) -> None:
    """@brief 行为最早归属从具名合同读取，晚期节点不抬高所有权，关闭时必须具备引用证据。"""
    assert list(cases) == [row[0] for row in _spec_table(LEDGER_ANCHOR, CASE_HEADER)]
    for name, case in cases.items():
        assert set(case) == {"owner_stage", "owner_nodes", "integration_nodes"}, name
        stages = [int(row[1]) for row in rows if name in re.findall(r"T\d+", row[2])]
        assert case["owner_stage"] == str(min(stages)), f"incorrect behavior owner stage: {name}"
        assert len(case["owner_nodes"]) == len(set(case["owner_nodes"])), name
        for entry in case["integration_nodes"]:
            assert set(entry) == {"stage", "node"}, name
            assert entry["stage"] in registration.IMPLEMENTATION_STAGES, name
            assert int(entry["stage"]) > int(case["owner_stage"]), name
        nodes = _case_nodes(case)
        assert len(nodes) == len(set(nodes)), name
    current = max(done, key=int) if done else "0"
    for name, stage, verification in rows:
        referenced = re.findall(r"T\d+", verification)
        assert set(referenced) <= set(cases), name
        if stage not in done:
            continue
        for case_name in referenced:
            case = cases[case_name]
            assert case["owner_nodes"], f"finished behavior without owner evidence: {case_name}"
            assert set(_case_nodes(case, current)) <= set(ledger[name]["tests"]), (name, case_name)


def _check_nodes(nodes: list[str], root: Path = REPO_ROOT) -> None:
    """@brief 登记的当前或晚期节点均立即解析，未来名字、跳过节点和空壳不能作为证据。"""
    for node in set(nodes):
        assert NODE_ID.fullmatch(node), f"malformed test node: {node}"
        assert resolve_problem(node, root) is None, (node, resolve_problem(node, root))
        path, *_, function = re.sub(r"\[.*\]$", "", node).split("::")
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        bodies = [item for item in ast.walk(tree)
                  if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function]
        assert bodies and not any(stub_functions(body) for body in bodies), f"empty test node: {node}"


def _check_implementation(reference: str, root: Path = REPO_ROOT) -> None:
    """@brief 实现符号只能位于真实生产文件，测试 helper 或再导出不能冒充实现。"""
    target, symbol = reference.split("::", 1)
    path = Path(target)
    assert not path.is_absolute() and ".." not in path.parts, reference
    assert path.parts[0] in PRODUCTION_ROOTS and path.suffix == ".py", reference
    assert symbol_problem(root, target, symbol) is None, (reference, symbol_problem(root, target, symbol))


def _check_mutation_rows(rows: dict, ledger: dict, done: frozenset[str]) -> dict:
    """@brief 变异行、分句、合同与最早杀手阶段逐项对应，关闭波次不得留下未覆盖分句。"""
    spec_rows = _spec_table(MUTATION_ANCHOR, MUTATION_HEADER)
    assert list(rows) == [row[0] for row in spec_rows], "mutation registration differs from specification"
    names = {}
    for text, stage, killers, contracts, earliest in spec_rows:
        entry = rows[text]
        assert set(entry) == {"stage", "clauses"} and entry["stage"] == stage, text
        expected = list(zip(text.split("；"), contracts.split("；"), earliest.split("；"), strict=True))
        assert len(entry["clauses"]) == len(expected), text
        for clause, (sentence, contract, killer_stage) in zip(entry["clauses"], expected, strict=True):
            assert set(clause) == {"text", "contract", "earliest_runnable_killer_stage", "mutations"}, text
            assert (clause["text"], clause["contract"], clause["earliest_runnable_killer_stage"]) == (
                sentence, contract, killer_stage), text
            assert contract in ledger and ledger[contract]["stage"] == stage, text
            allowed = {stage, "5"} if int(stage) <= 4 else {stage}
            assert killer_stage in allowed, f"late mutation killer stage: {sentence}"
            assert isinstance(clause["mutations"], tuple), sentence
            if stage in done:
                assert clause["mutations"], f"finished mutation clause without evidence: {sentence}"
            for name in clause["mutations"]:
                assert name not in names, f"mutation registered more than once: {name}"
                names[name] = (clause, set(re.findall(r"T\d+", killers)))
    return names


def _check_mutation_definitions(names: dict, ledger: dict, cases: dict, mutations: tuple) -> None:
    """@brief 真实源码变异恰好归一个分句，杀手来自该行的已生效行为节点，结构守卫不能替代行为。"""
    mutation_names = [mutation.name for mutation in mutations]
    assert len(mutation_names) == len(set(mutation_names)), "duplicate mutation definitions"
    assert set(mutation_names) == set(names), "mutation clauses and source definitions differ"
    ledger_nodes = {node for entry in ledger.values() for node in entry["tests"]}
    structural = set(_case_nodes(cases["T56"]))
    for mutation in mutations:
        clause, killers = names[mutation.name]
        assert killers and "T56" not in killers and killers <= set(cases), mutation.name
        eligible = {node for name in killers for node in _case_nodes(
            cases[name], clause["earliest_runnable_killer_stage"])} - structural
        assert mutation.tests and set(mutation.tests) <= eligible & ledger_nodes, mutation.name
        _check_nodes(list(mutation.tests))
        module, relative = source_location(mutation.symbol)
        assert module.startswith(("worldmock.", "custom.", "scripts.")), mutation.name
        _check_implementation(relative + "::" + mutation.symbol.split("::", 1)[1])
        original, changed = mutated_source(mutation)
        assert original != changed, mutation.name


def _registered_nodes(ledger: dict) -> list[str]:
    """@brief 汇总台账、行为与源码变异引用的全部真实节点。"""
    return ([node for row in ledger.values() for node in row["tests"]]
            + [node for case in registration.BEHAVIOUR_CASES.values() for node in _case_nodes(case)]
            + [node for mutation in registration.MUTATIONS for node in mutation.tests])


def test_stage_sets_allow_only_the_integrated_wave_and_later_prefixes() -> None:
    """@brief 冻结不声明生产行为完成，实施阶段与只报告阶段保持互斥。"""
    assert registration.STAGE_IDS == frozenset(str(stage) for stage in range(10))
    assert registration.IMPLEMENTATION_STAGES == frozenset(str(stage) for stage in range(1, 9))
    assert registration.REPORT_ONLY_STAGES == {"9"}
    _check_stages(registration.STAGES_DONE)



def test_every_spec_anchor_has_a_registered_current_normative_body_owner() -> None:
    """@brief 所有稳定锚点都登记实际正文所有者，审查记录和版本表同样不可漏登。"""
    anchors = [anchor for _, anchor in explicit_anchors((REPO_ROOT / SPEC).read_text(encoding="utf-8"))]
    requirements = acceptance_section("resource-rule-simplification", "normative_requirements")
    assert requirements == [anchor.removeprefix("spec-rrs-") for anchor in anchors]
    owners = json.loads((REPO_ROOT / "tests/fixtures/current-feature-owners.json").read_text(encoding="utf-8"))
    for anchor in anchors:
        reference = owners["resource-rule-simplification"]["规范：" + anchor.removeprefix("spec-rrs-")]
        assert reference == SPEC + "#" + anchor
        assert owner_problem(REPO_ROOT, reference) is None, (anchor, owner_problem(REPO_ROOT, reference))


def test_ledger_rows_match_the_spec_table_in_order_and_stage() -> None:
    """@brief 台账全量身份及完成阶段证据由独立规格表约束。"""
    _check_ledger(_ledger(), _spec_table(LEDGER_ANCHOR, LEDGER_HEADER), registration.STAGES_DONE)


def test_behaviour_cases_match_spec_and_earliest_contract_ownership() -> None:
    """@brief 所有行为用例有真实归属；关闭时所有者和到期集成节点都在合同登记中。"""
    _check_cases(registration.BEHAVIOUR_CASES, _spec_table(LEDGER_ANCHOR, LEDGER_HEADER),
                 _ledger(), registration.STAGES_DONE)


def test_every_registered_node_and_implementation_resolves_immediately() -> None:
    """@brief 未关闭波次可以登记真实纯节点，但不能登记未来名字或非生产定义。"""
    ledger = _ledger()
    _check_nodes(_registered_nodes(ledger))
    for entry in ledger.values():
        for reference in entry["implementation"]:
            _check_implementation(reference)


def test_registered_owner_and_due_integration_nodes_are_collectable(tmp_path) -> None:
    """@brief 已登记纯所有者与已到期集成节点在真实 pytest 入口中能收集。"""
    current = max(registration.STAGES_DONE, key=int) if registration.STAGES_DONE else "0"
    nodes = {node for case in registration.BEHAVIOUR_CASES.values() for node in case["owner_nodes"]}
    nodes.update(node for case in registration.BEHAVIOUR_CASES.values() for node in _case_nodes(case, current))
    if not nodes:
        return
    command = [sys.executable, "-m", "pytest", "--collect-only", *sorted(nodes), "-q",
               "--environment-profile", "dev", "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "collect")]
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def test_stage_one_pure_collection_does_not_import_the_complete_engine(tmp_path) -> None:
    """@brief 场景、状态和身份节点的真实收集检查已加载模块，不以静态导入位置冒充隔离。"""
    nodes = {node for case in registration.BEHAVIOUR_CASES.values() if case["owner_stage"] == "1"
             for node in case["owner_nodes"]}
    arguments = ["--collect-only", *sorted(nodes), "-q", "--environment-profile", "dev", "-p", "no:cacheprovider",
                 "--basetemp", str(tmp_path / "collect")]
    script = (
        "import sys,pytest; result=pytest.main(" + repr(arguments) + "); assert result==0; "
        "forbidden={'worldmock.life.engine','worldmock.life.rules','worldmock.life.games',"
        "'worldmock.life.read_query_runtime','custom.envs.life_env'}; "
        "assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules)); "
        "assert not any(name.startswith('agentsociety2.society') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_map_owners_execute_without_the_complete_engine(tmp_path) -> None:
    """@brief 实际图、行程、会话与证据节点执行后仍不加载完整引擎和 AS2 执行链。"""
    nodes = {node for case in registration.BEHAVIOUR_CASES.values() if case["owner_stage"] == "2"
             for node in case["owner_nodes"]}
    assert nodes
    arguments = [*sorted(nodes), "-q", "--environment-profile", "dev", "-p", "no:cacheprovider",
                 "--basetemp", str(tmp_path / "owners")]
    script = (
        "import sys,pytest; result=pytest.main(" + repr(arguments) + "); assert result==0; "
        "forbidden={'worldmock.life.engine','worldmock.life.game','worldmock.life.games',"
        "'worldmock.life.read_query_runtime','custom.envs.life_env'}; "
        "assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules)); "
        "assert not any(name.startswith('agentsociety2.society') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_app_owners_execute_without_the_complete_engine(tmp_path) -> None:
    """@brief 应用与推送的全部实际所有者执行后仍未加载完整引擎和 AS2 执行链。"""
    nodes = {node for case in registration.BEHAVIOUR_CASES.values() if case["owner_stage"] == "4"
             for node in case["owner_nodes"]}
    assert nodes
    arguments = [*sorted(nodes), "-q", "--environment-profile", "dev", "-p", "no:cacheprovider",
                 "--basetemp", str(tmp_path / "owners")]
    script = (
        "import sys,pytest; result=pytest.main(" + repr(arguments) + "); assert result==0; "
        "forbidden={'worldmock.life.engine','worldmock.life.game','worldmock.life.games',"
        "'worldmock.life.read_query_runtime','custom.envs.life_env'}; "
        "assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules)); "
        "assert not any(name.startswith('agentsociety2.society') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_feature_file_lists_are_real_relative_and_have_no_deferral_or_stubs() -> None:
    """@brief 四个生产根与测试清单只保存现有文件，不允许延期标记和空壳函数。"""
    production, tests = registration.FEATURE_PRODUCTION_FILES, registration.FEATURE_TEST_FILES
    listed = [*production, *tests]
    assert len(listed) == len(set(listed)), "duplicate feature files"
    assert {"tests/rrs_acceptance.py", "tests/test_rrs_acceptance.py"} <= set(tests)
    assert all(Path(path).parts[0] in PRODUCTION_ROOTS for path in production)
    assert all(path.startswith("tests/") for path in tests)
    registered = [node.split("::", 1)[0] for node in _registered_nodes(_ledger())]
    for relative in set([*listed, *registered]):
        path = Path(relative)
        assert not path.is_absolute() and ".." not in path.parts, relative
        assert (REPO_ROOT / path).is_file(), relative
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        if path.suffix in {".py", ".js", ".cjs"}:
            assert not DEFERRAL.search(text), relative
        if path.suffix == ".py":
            assert not stub_functions(ast.parse(text)), relative
    assert registration.RECEIPTS == REPO_ROOT / ".work/rrs/mutation-receipts"


def test_mutation_rows_have_exact_contracts_stages_and_due_coverage() -> None:
    """@brief 变异分句的具名合同与最早杀手阶段不能被晚期集成证据抬高。"""
    _check_mutation_rows(registration.MUTATION_ROWS, _ledger(), registration.STAGES_DONE)


def test_registered_mutations_have_real_production_anchors_and_behavior_killers() -> None:
    """@brief 即使波次尚未关闭，已登记变异也必须可编译并有真实行为杀手。"""
    ledger = _ledger()
    names = _check_mutation_rows(registration.MUTATION_ROWS, ledger, registration.STAGES_DONE)
    _check_mutation_definitions(names, ledger, registration.BEHAVIOUR_CASES, registration.MUTATIONS)


@pytest.fixture(scope="session")
def rrs_mutation_baseline(tmp_path_factory):
    """@brief 当前所有真实行为杀手先在独立解释器中通过，再执行源码变异。"""
    nodes = tuple(dict.fromkeys(node for mutation in registration.MUTATIONS for node in mutation.tests))
    options = ("-q", "--environment-profile", "dev", "-p", "no:cacheprovider")
    directory = tmp_path_factory.mktemp("rrs-mutation-baseline")
    result = run_behavior((*nodes, *options), directory, None,
                          RunSettings(CHILD_ENTRY, registration.RECEIPTS, 600))
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
    return result


@pytest.mark.parametrize("mutation", registration.MUTATIONS, ids=lambda mutation: mutation.name)
def test_resource_rule_source_mutation_is_killed(mutation, rrs_mutation_baseline, tmp_path):
    """@brief 有效变异须由实际行为断言杀死，语法、导入、收集错误不能计为证据。"""
    options = ("-q", "--environment-profile", "dev", "-p", "no:cacheprovider")
    result = run_behavior((*mutation.tests, *options), tmp_path, mutation,
                          RunSettings(CHILD_ENTRY, registration.RECEIPTS, 180))
    assert result["original_sha256"] != result["mutated_sha256"]
    assert result["exit_code"] == 1, result["stdout"] + result["stderr"]
    assert "FAILED " in result["stdout"] and "ERROR collecting" not in result["stdout"]
    assert ("AssertionError" in result["stdout"]
            or re.search(r"^E\s+assert\b", result["stdout"], re.MULTILINE)
            or re.search(r"^E\s+Failed: DID NOT RAISE\b", result["stdout"], re.MULTILINE))
    assert "ImportError" not in result["stdout"] + result["stderr"]


@pytest.mark.parametrize("done", [{"1"}, {"1", "2", "3", "4"}, {"5"}, {"1", "2", "3", "4", "5", "7"},
                                  {"1", "2", "3", "4", "5", "6", "7", "8", "9"}])
def test_partial_waves_and_report_only_completion_are_rejected(done) -> None:
    """@brief 不完整波次、越级关闭与只报告阶段均被守卫拒绝。"""
    with pytest.raises(AssertionError):
        _check_stages(frozenset(done))


@pytest.mark.parametrize("change", ["missing", "stage", "closed_without_evidence"])
def test_missing_contract_wrong_stage_and_empty_finished_evidence_are_rejected(change) -> None:
    """@brief 台账缺项、错阶段与虚假关闭不会因冻结时为空而失去检查。"""
    ledger, done = _ledger(), frozenset()
    name = next(iter(ledger))
    if change == "missing":
        del ledger[name]
    elif change == "stage":
        ledger[name]["stage"] = "8"
    else:
        ledger[name]["tests"] = []
        ledger[name]["implementation"] = []
        done = frozenset(str(stage) for stage in range(1, 6))
    with pytest.raises(AssertionError):
        _check_ledger(ledger, _spec_table(LEDGER_ANCHOR, LEDGER_HEADER), done)


@pytest.mark.parametrize("case,stage", [("T11", "1"), ("T12", "4")])
def test_resource_claim_and_package_identity_owner_stages_cannot_be_swapped(case, stage) -> None:
    """@brief 最终占用与早期包资源身份分别归阶段四和阶段一。"""
    cases = copy.deepcopy(registration.BEHAVIOUR_CASES)
    cases[case]["owner_stage"] = stage
    with pytest.raises(AssertionError):
        _check_cases(cases, _spec_table(LEDGER_ANCHOR, LEDGER_HEADER), _ledger(), frozenset())


@pytest.mark.parametrize("change", ["missing", "contract", "killer_stage", "closed_without_mutation"])
def test_missing_mutation_wrong_contract_and_delayed_killer_are_rejected(change) -> None:
    """@brief 变异缺项、错误绑定、延期杀手和关闭后的空分句都不能作为完整登记。"""
    rows, done = copy.deepcopy(registration.MUTATION_ROWS), frozenset()
    text = next(iter(rows))
    if change == "missing":
        del rows[text]
    elif change == "contract":
        rows[text]["clauses"][0]["contract"] = "完整日基线"
    elif change == "killer_stage":
        rows[text]["clauses"][0]["earliest_runnable_killer_stage"] = "8"
    else:
        rows[text]["clauses"][0]["mutations"] = ()
        done = frozenset(str(stage) for stage in range(1, 6))
    with pytest.raises(AssertionError):
        _check_mutation_rows(rows, _ledger(), done)


@pytest.mark.parametrize("body", ["def test_real():\n    pass\n", "def test_real():\n    assert True\n"])
def test_future_nodes_and_empty_test_bodies_are_rejected(tmp_path, body) -> None:
    """@brief 未来节点名和空壳测试不能因未完成波次而被接受，真实节点可以提前登记。"""
    path = tmp_path / "tests/test_real.py"
    path.parent.mkdir()
    path.write_text(body, encoding="utf-8")
    with pytest.raises(AssertionError):
        _check_nodes(["tests/test_real.py::test_future"], tmp_path)
    if "pass" in body:
        with pytest.raises(AssertionError):
            _check_nodes(["tests/test_real.py::test_real"], tmp_path)
    else:
        _check_nodes(["tests/test_real.py::test_real"], tmp_path)


def test_skipped_nodes_and_nonproduction_implementation_are_rejected(tmp_path) -> None:
    """@brief 跳过测试与测试文件里的定义均不能成为生产验收证据。"""
    path = tmp_path / "tests/test_real.py"
    path.parent.mkdir()
    path.write_text("import pytest\n@pytest.mark." + "sk" + "ip\ndef test_real():\n    assert True\n")
    with pytest.raises(AssertionError):
        _check_nodes(["tests/test_real.py::test_real"], tmp_path)
    with pytest.raises(AssertionError):
        _check_implementation("tests/test_real.py::test_real", tmp_path)


def test_source_mutation_without_a_behavior_killer_is_rejected() -> None:
    """@brief 有登记名但没有行为杀手的变异定义立即失败，不等待波次关闭。"""
    mutation = registration.SourceMutation("empty_killer", "worldmock.life.models::WorldState", "old", "new", ())
    clause = {"earliest_runnable_killer_stage": "1"}
    with pytest.raises(AssertionError):
        _check_mutation_definitions({mutation.name: (clause, {"T2"})}, _ledger(),
                                    registration.BEHAVIOUR_CASES, (mutation,))


@pytest.mark.parametrize("end", [0, 5, 6, 7, 8])
def test_complete_wave_prefixes_are_allowed(end) -> None:
    """@brief 冻结、完整集成波次及后续顺序关闭集合都合法。"""
    _check_stages(frozenset(str(stage) for stage in range(1, end + 1)))


def test_source_mutation_cannot_use_a_structural_guard_as_its_behavior_killer() -> None:
    """@brief 结构门禁不能充当行为变异杀手。"""
    node = "tests/test_rrs_acceptance.py::test_stage_sets_allow_only_the_integrated_wave_and_later_prefixes"
    mutation = registration.SourceMutation("structural_killer", "worldmock.life.models::WorldState", "old", "new", (node,))
    with pytest.raises(AssertionError):
        _check_mutation_definitions({mutation.name: ({"earliest_runnable_killer_stage": "8"}, {"T56"})},
                                    _ledger(), registration.BEHAVIOUR_CASES, (mutation,))


def test_all_implementation_stages_are_complete_and_dataset_is_report_only():
    """@brief 最终收口不允许保留任何实现阶段，数据仓交接保持真实只报告边界。"""
    assert registration.STAGES_DONE == registration.IMPLEMENTATION_STAGES == frozenset(str(i) for i in range(1, 9))
    assert registration.REPORT_ONLY_STAGES == frozenset({"9"})
    _check_ledger(_ledger(), _spec_table(LEDGER_ANCHOR, LEDGER_HEADER), registration.STAGES_DONE)
    _check_cases(registration.BEHAVIOUR_CASES, _spec_table(LEDGER_ANCHOR, LEDGER_HEADER),
                 _ledger(), registration.STAGES_DONE)
