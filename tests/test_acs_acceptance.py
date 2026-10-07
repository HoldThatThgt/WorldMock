"""@brief 动作与内容简化的契约登记、真实节点、集成波次与源码变异守卫。"""

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
from scripts.documentation_owners import owner_problem
from scripts.documentation_refs import explicit_anchors, section_text
from tests import acs_acceptance as registration
from tests.acceptance_guard import DEFERRAL, NODE_ID, acceptance_section, resolve_problem, stub_functions
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, mutated_source, run_behavior, source_location

SPEC = "docs/dev/SPEC-action-and-content-simplification.md"
FEATURE = "action-content-simplification"
LEDGER_HEADER = ["契约点", "阶段", "必须通过的验证"]
CASE_HEADER = ["编号", "用例验证什么"]
MUTATION_HEADER = ["变异", "行为所有者阶段", "被谁杀死", "绑定契约点"]
PRODUCTION_ROOTS = {"src", "custom", "tools", "scripts"}
ENGINE_CONTRACTS = frozenset({
    "收货恰好一次", "收货事件归收货人", "没有库存", "时长只来自内容", "娱乐接续与单条完成", "内容选择次序",
    "进度与确定性", "推送资格与打开", "按类别与方式占用", "社交、等待与邮件", "订单点名购物与支付 app",
    "请求形状与工具面", "接续成本", "足迹事实与导出身份", "完整日基线",
})


def _ledger() -> dict:
    """@brief 读取独立保存的契约证据，不由规格正文生成运行期证据。"""
    return json.loads((REPO_ROOT / "tests/fixtures/acs-acceptance.json").read_text(encoding="utf-8"))


def _spec_table(anchor: str, header: list[str]) -> list[list[str]]:
    """@brief 每张规格表独立按锚点和唯一表头解析。"""
    text = section_text((REPO_ROOT / SPEC).read_text(encoding="utf-8"), anchor)
    tables, current = [], None
    for line in text.splitlines():
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
    """@brief 只接受冻结、完整实施波次、实施波次加正式环境回执三个集合。"""
    assert done in {frozenset(), frozenset(str(stage) for stage in range(1, 7)),
                    frozenset(str(stage) for stage in range(1, 8))}, sorted(done)


def _check_ledger(ledger: dict, rows: list[list[str]], done: frozenset[str]) -> None:
    """@brief 台账身份和阶段逐行一致，完成声明必须带行为与生产定义。"""
    assert list(ledger) == [row[0] for row in rows], "contract registration differs from specification"
    assert len(rows) <= 30
    for name, stage, _ in rows:
        entry = ledger[name]
        assert set(entry) == {"stage", "spec", "tests", "implementation"}, name
        assert entry["stage"] == stage and stage in registration.STAGE_IDS, name
        for field in ("tests", "implementation"):
            assert isinstance(entry[field], list) and len(entry[field]) == len(set(entry[field])), name
        path, anchor = entry["spec"].split("#")
        assert path == SPEC and anchor.startswith("spec-acs-"), name
        assert owner_problem(REPO_ROOT, entry["spec"]) is None, name
        report = stage in registration.REPORT_ONLY_STAGES
        assert report == ("只报告" in name), name
        if report:
            assert not entry["tests"] and not entry["implementation"], name
        elif stage in done:
            assert entry["tests"] and entry["implementation"], f"finished contract without evidence: {name}"


def _case_nodes(case: dict) -> list[str]:
    """@brief 所有已登记节点立即生效，不随完成集合隐藏晚期证据。"""
    return list(case["owner_nodes"]) + [entry["node"] for entry in case["integration_nodes"]]


def _check_cases(cases: dict, ledger: dict, done: frozenset[str]) -> None:
    """@brief 用例属于最早引用它的契约阶段，集成证据不能替代纯所有者。"""
    rows = _spec_table("spec-acs-验收台账", LEDGER_HEADER)
    assert list(cases) == [row[0] for row in _spec_table("spec-acs-行为用例", CASE_HEADER)]
    for name, case in cases.items():
        assert set(case) == {"owner_stage", "owner_nodes", "integration_nodes"}, name
        stages = [int(stage) for _, stage, text in rows if name in re.findall(r"T\d+", text)]
        assert case["owner_stage"] == str(min(stages)), f"incorrect owner stage: {name}"
        assert isinstance(case["owner_nodes"], list) and isinstance(case["integration_nodes"], list), name
        for entry in case["integration_nodes"]:
            assert set(entry) == {"stage", "node"}, name
            assert entry["stage"] in registration.IMPLEMENTATION_STAGES, name
            assert int(entry["stage"]) > int(case["owner_stage"]), name
        nodes = _case_nodes(case)
        assert len(nodes) == len(set(nodes)), name
    for name, stage, verification in rows:
        referenced = re.findall(r"T\d+", verification)
        assert set(referenced) <= set(cases), name
        if stage in done:
            for case_name in referenced:
                assert cases[case_name]["owner_nodes"], f"finished case without owner evidence: {case_name}"
                assert set(_case_nodes(cases[case_name])) <= set(ledger[name]["tests"]), (name, case_name)


def _test_body(node: str, root: Path = REPO_ROOT) -> ast.AST:
    """@brief 返回真实测试函数，保留类作用域节点的校验结果。"""
    path, *_, function = re.sub(r"\[.*\]$", "", node).split("::")
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    (body,) = [item for item in ast.walk(tree)
               if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function]
    return body


def _check_nodes(nodes: list[str], root: Path = REPO_ROOT) -> None:
    """@brief 未来节点、不能收集的节点、空壳与跳过的节点均不能进入登记。"""
    for node in set(nodes):
        assert NODE_ID.fullmatch(node), f"malformed test node: {node}"
        assert resolve_problem(node, root) is None, (node, resolve_problem(node, root))
        assert not stub_functions(_test_body(node, root)), f"empty test node: {node}"


def _check_implementation(reference: str, root: Path = REPO_ROOT) -> None:
    """@brief 实现只能引用四个生产根里的真实定义，不接受导入再导出或测试 helper。"""
    target, symbol = reference.split("::", 1)
    path = Path(target)
    assert not path.is_absolute() and ".." not in path.parts, reference
    assert path.parts[0] in PRODUCTION_ROOTS and path.suffix == ".py", reference
    assert symbol_problem(root, target, symbol) is None, (reference, symbol_problem(root, target, symbol))


def _check_mutation_rows(rows: dict, ledger: dict, done: frozenset[str]) -> dict:
    """@brief 变异表每个分句恰好登记一次，归属阶段与契约一致。"""
    spec_rows = _spec_table("spec-acs-变异登记", MUTATION_HEADER)
    assert list(rows) == [row[0] for row in spec_rows], "mutation registration differs from specification"
    assert sum(len(text.split("；")) for text, _, _, _ in spec_rows) <= 68
    names = {}
    for text, stage, killers, contract in spec_rows:
        entry = rows[text]
        assert set(entry) == {"stage", "clauses"} and entry["stage"] == stage, text
        assert ledger[contract]["stage"] == stage, text
        assert [clause["text"] for clause in entry["clauses"]] == text.split("；"), text
        for clause in entry["clauses"]:
            assert set(clause) == {"text", "contract", "mutations"}, text
            assert clause["contract"] == contract and isinstance(clause["mutations"], tuple), text
            if stage in done:
                assert clause["mutations"], f"finished mutation clause without evidence: {clause['text']}"
            for name in clause["mutations"]:
                assert name not in names, f"duplicate mutation registration: {name}"
                names[name] = (stage, contract, set(re.findall(r"T\d+", killers)))
    return names


def _check_mutations(names: dict, ledger: dict, cases: dict, mutations: tuple) -> None:
    """@brief 杀手取所属用例的真实行为节点，阶段一至三只用同阶段纯所有者。"""
    actual = [mutation.name for mutation in mutations]
    assert len(actual) == len(set(actual)) and set(actual) == set(names), "source mutation definitions differ"
    ledger_nodes = {node for row in ledger.values() for node in row["tests"]}
    for mutation in mutations:
        stage, _, killers = names[mutation.name]
        assert killers and "T50" not in killers and killers <= set(cases), mutation.name
        eligible = {node for case in killers for node in (
            cases[case]["owner_nodes"] if int(stage) <= 3 else _case_nodes(cases[case]))}
        assert mutation.tests and set(mutation.tests) <= eligible & ledger_nodes, mutation.name
        _check_nodes(list(mutation.tests))
        module, relative = source_location(mutation.symbol)
        assert module.startswith(("worldmock.", "custom.", "scripts.")), mutation.name
        _check_implementation(relative + "::" + mutation.symbol.split("::", 1)[1])
        original, changed = mutated_source(mutation)
        assert original != changed, mutation.name


def _registered_nodes(ledger: dict) -> list[str]:
    """@brief 台账、用例与变异的每条引用都立即参加完整性核对。"""
    return ([node for row in ledger.values() for node in row["tests"]]
            + [node for case in registration.BEHAVIOUR_CASES.values() for node in _case_nodes(case)]
            + [node for mutation in registration.MUTATIONS for node in mutation.tests])


def _collect(nodes: set[str], directory: Path, pure: bool) -> None:
    """@brief 在新解释器收集全部已登记节点，纯所有者解释器不得加载 LifeEngine。"""
    if not nodes:
        return
    arguments = ["--collect-only", *sorted(nodes), "-q", "--environment-profile", "dev", "-p", "no:cacheprovider",
                 "--basetemp", str(directory)]
    script = "import sys,pytest; result=pytest.main(" + repr(arguments) + "); assert result==0; "
    if pure:
        script += "assert 'worldmock.life.engine' not in sys.modules; "
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def test_stage_sets_accept_only_frozen_full_wave_or_full_wave_with_receipts() -> None:
    """@brief 冻结没有完成声明，报告阶段不属于实施阶段。"""
    assert registration.STAGE_IDS == frozenset(str(stage) for stage in range(8))
    assert registration.IMPLEMENTATION_STAGES == frozenset(str(stage) for stage in range(1, 7))
    assert registration.REPORT_ONLY_STAGES == frozenset({"7"})
    _check_stages(registration.STAGES_DONE)


def test_every_spec_anchor_has_one_current_normative_owner() -> None:
    """@brief 全部稳定锚点和机器要求逐项一致，每个所有者都有实质正文。"""
    anchors = [anchor for _, anchor in explicit_anchors((REPO_ROOT / SPEC).read_text(encoding="utf-8"))]
    assert acceptance_section(FEATURE, "normative_requirements") == [
        anchor.removeprefix("spec-acs-") for anchor in anchors]
    owners = json.loads((REPO_ROOT / "tests/fixtures/current-feature-owners.json").read_text(encoding="utf-8"))
    for anchor in anchors:
        reference = owners[FEATURE]["规范：" + anchor.removeprefix("spec-acs-")]
        assert reference == SPEC + "#" + anchor
        assert owner_problem(REPO_ROOT, reference) is None, anchor


def test_contract_case_and_mutation_tables_match_independent_spec_tables() -> None:
    """@brief 三张表独立比对，阶段未关闭也不能漏登身份、错归属或隐藏现有证据。"""
    ledger = _ledger()
    rows = _spec_table("spec-acs-验收台账", LEDGER_HEADER)
    assert len(rows) == 30
    assert acceptance_section(FEATURE, "验收台账")["tables"] == [[LEDGER_HEADER, *rows]]
    assert acceptance_section(FEATURE, "mutation_requirements") == [
        row[0] for row in _spec_table("spec-acs-变异登记", MUTATION_HEADER)]
    _check_ledger(ledger, rows, registration.STAGES_DONE)
    _check_cases(registration.BEHAVIOUR_CASES, ledger, registration.STAGES_DONE)
    _check_mutation_rows(registration.MUTATION_ROWS, ledger, registration.STAGES_DONE)


def test_every_registered_node_and_production_symbol_resolves_immediately() -> None:
    """@brief 不存在的未来名字不能因完成集合为空而进入台账。"""
    ledger = _ledger()
    _check_nodes(_registered_nodes(ledger))
    for row in ledger.values():
        for reference in row["implementation"]:
            _check_implementation(reference)


def test_registered_nodes_are_collectable_and_early_owners_do_not_import_life_engine(tmp_path) -> None:
    """@brief 全部登记真实收集，阶段一至三的纯所有者另在独立解释器收集。"""
    _collect(set(_registered_nodes(_ledger())), tmp_path / "all", False)
    for stage in ("1", "2", "3"):
        nodes = {node for case in registration.BEHAVIOUR_CASES.values() if case["owner_stage"] == stage
                 for node in case["owner_nodes"]}
        _collect(nodes, tmp_path / stage, True)


def test_feature_file_lists_are_real_relative_and_have_no_deferral_or_stubs() -> None:
    """@brief 特性清单只登记当前真实文件，每个已登记文件的函数均非空壳。"""
    retired_wave = "tests/integration/test_rrs_engine_wave.py"
    assert not (REPO_ROOT / retired_wave).exists(), retired_wave
    for relative, retired in (
        ("src/worldmock/life/object_labels.py", {"supply_label", "names_store", "written_place", "_SOLD_BY"}),
        ("src/worldmock/life/session_actions.py", {"_task_link"}),
    ):
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        definitions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        definitions |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
                        and isinstance(node.ctx, ast.Store)}
        assert not definitions & retired, relative
    production, tests = registration.FEATURE_PRODUCTION_FILES, registration.FEATURE_TEST_FILES
    listed = [*production, *tests]
    assert len(listed) == len(set(listed)), "duplicate feature files"
    assert {"tests/acs_acceptance.py", "tests/test_acs_acceptance.py"} <= set(tests)
    assert all(Path(path).parts[0] in PRODUCTION_ROOTS for path in production)
    assert all(path.startswith("tests/") for path in tests)
    registered = [node.split("::", 1)[0] for node in _registered_nodes(_ledger())]
    for relative in set([*listed, *registered]):
        path = Path(relative)
        assert not path.is_absolute() and ".." not in path.parts and (REPO_ROOT / path).is_file(), relative
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        if path.suffix in {".py", ".js", ".cjs"}:
            assert not DEFERRAL.search(text), relative
        if path.suffix == ".py":
            assert not stub_functions(ast.parse(text)), relative
    assert registration.RECEIPTS == REPO_ROOT / ".work/acs/mutation-receipts"


def test_registered_mutations_have_exact_source_anchors_and_behavior_killers() -> None:
    """@brief 全部已登记变异立即验证可编译源码、唯一原文与同阶段真实行为杀手。"""
    ledger = _ledger()
    names = _check_mutation_rows(registration.MUTATION_ROWS, ledger, registration.STAGES_DONE)
    _check_mutations(names, ledger, registration.BEHAVIOUR_CASES, registration.MUTATIONS)


def test_action_content_source_mutations_are_killed(tmp_path) -> None:
    """@brief 先跑未变异行为基线，再逐个证明源码变异被断言杀死而非导入或收集错误。"""
    if not registration.MUTATIONS:
        assert not _check_mutation_rows(registration.MUTATION_ROWS, _ledger(), registration.STAGES_DONE)
        return
    nodes = tuple(dict.fromkeys(node for mutation in registration.MUTATIONS for node in mutation.tests))
    options = ("-q", "--environment-profile", "dev", "-p", "no:cacheprovider")
    directory = tmp_path / "baseline"
    directory.mkdir()
    baseline = run_behavior((*nodes, *options), directory, None,
                            RunSettings(CHILD_ENTRY, registration.RECEIPTS, 1200))
    assert baseline["exit_code"] == 0, baseline["stdout"] + baseline["stderr"]
    for mutation in registration.MUTATIONS:
        directory = tmp_path / mutation.name
        directory.mkdir()
        result = run_behavior((*mutation.tests, *options), directory, mutation,
                              RunSettings(CHILD_ENTRY, registration.RECEIPTS, 300))
        assert result["original_sha256"] != result["mutated_sha256"]
        assert result["exit_code"] == 1, result["stdout"] + result["stderr"]
        assert "FAILED " in result["stdout"] and "ERROR collecting" not in result["stdout"]
        assert ("AssertionError" in result["stdout"]
                or re.search(r"^E\s+assert\b", result["stdout"], re.MULTILINE)
                or re.search(r"^E\s+Failed: DID NOT RAISE\b", result["stdout"], re.MULTILINE))
        assert "ImportError" not in result["stdout"] + result["stderr"]


def test_closed_wave_has_real_engine_nodes_for_every_engine_contract() -> None:
    """@brief 实施关闭时引擎契约各有真实引擎夹具节点，早期契约必须另外登记集成节点。"""
    if not registration.IMPLEMENTATION_STAGES <= registration.STAGES_DONE:
        return
    rows = _spec_table("spec-acs-验收台账", LEDGER_HEADER)
    for name, stage, verification in rows:
        if name not in ENGINE_CONTRACTS:
            continue
        cases = [registration.BEHAVIOUR_CASES[case] for case in re.findall(r"T\d+", verification)]
        nodes = {entry["node"] for case in cases for entry in case["integration_nodes"]} if int(stage) <= 3 else {
            node for case in cases for node in _case_nodes(case)}
        nodes &= set(_ledger()[name]["tests"])
        assert nodes, f"engine contract without integration evidence: {name}"
        assert any(_uses_real_engine(node) for node in nodes), name


def _uses_real_engine(node: str, root: Path = REPO_ROOT) -> bool:
    """@brief 测试可直接构造引擎，也可通过真实 pytest fixture 链构造引擎。"""
    path = root / node.split("::", 1)[0]
    candidates = [path, *(parent / "conftest.py" for parent in path.parents if parent != root.parent)]
    fixtures = {}
    for candidate in candidates:
        if not candidate.is_file() or not candidate.is_relative_to(root):
            continue
        tree = ast.parse(candidate.read_text(encoding="utf-8"))
        for name, body in _fixture_functions(tree).items():
            fixtures.setdefault(name, body)
        for item in tree.body:
            if isinstance(item, ast.ImportFrom) and item.module == "tests.engine_fixture":
                source = root / "tests/engine_fixture.py"
                if not source.is_file():
                    continue
                exported = _fixture_functions(ast.parse(source.read_text(encoding="utf-8")))
                for alias in item.names:
                    if alias.name in exported:
                        fixtures.setdefault(alias.asname or alias.name, exported[alias.name])
    pending, visited = [_test_body(node, root)], set()
    while pending:
        body = pending.pop()
        if body.name in visited:
            continue
        visited.add(body.name)
        calls = [item.func for item in ast.walk(body) if isinstance(item, ast.Call)]
        if any(isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name)
               and call.value.id == "LifeEngine" and call.attr in {"create", "open"} for call in calls):
            return True
        names = {arg.arg for arg in body.args.args} | {call.id for call in calls if isinstance(call, ast.Name)}
        pending.extend(fixtures[name] for name in names & fixtures.keys())
    return False


def _fixture_functions(tree: ast.Module) -> dict:
    """@brief 只识别源码中真实 pytest fixture 定义，普通 helper 不能冒充夹具。"""
    return {item.name: item for item in tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                "fixture" in ast.unparse(decorator) for decorator in item.decorator_list)}


@pytest.mark.parametrize("node, expected", [
    ("tests/test_start_activity_contract.py::test_purchase_uses_each_observed_quote_and_single_unit", True),
    ("tests/life/test_named_purchase.py::test_named_purchase_rejects_unknown_goods_and_unbound_apps_atomically", False),
])
def test_imported_engine_factory_is_real_evidence_but_registered_world_is_not(node, expected):
    """@brief 实际共享夹具内的引擎构造成立；纯登记目录与世界对象不能代替引擎。"""
    assert _uses_real_engine(node) is expected


def test_factory_name_without_life_engine_constructor_is_not_real_engine_evidence(tmp_path):
    """@brief 同名 pytest 工厂没有真实构造调用时仍然拒绝，不能按夹具名字放行。"""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text("from tests.engine_fixture import engine_factory\n", encoding="utf-8")
    (tests / "engine_fixture.py").write_text(
        "import pytest\n@pytest.fixture\ndef engine_factory():\n    return {'registered_world': True}\n",
        encoding="utf-8")
    (tests / "test_example.py").write_text(
        "def test_registered(engine_factory):\n    assert engine_factory['registered_world']\n", encoding="utf-8")
    assert not _uses_real_engine("tests/test_example.py::test_registered", tmp_path)


def test_unimported_real_factory_cannot_count_as_the_test_fixture(tmp_path):
    """@brief 未实际导入的真实工厂文件不能给无夹具测试制造引擎证据。"""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "engine_fixture.py").write_text(
        "import pytest\n@pytest.fixture\ndef engine_factory():\n    return LifeEngine.create()\n",
        encoding="utf-8")
    (tests / "test_example.py").write_text(
        "def test_registered(engine_factory):\n    assert engine_factory is not None\n", encoding="utf-8")
    assert not _uses_real_engine("tests/test_example.py::test_registered", tmp_path)


@pytest.mark.parametrize("end", [0, 6, 7])
def test_only_three_legal_completion_sets_are_accepted(end) -> None:
    """@brief 空集、完整实施与完整实施加正式环境回执合法。"""
    _check_stages(frozenset(str(stage) for stage in range(1, end + 1)))


@pytest.mark.parametrize("done", [{"1"}, {"1", "2", "3"}, {"6"}, {"7"}, {"1", "2", "3", "4", "5"},
                                  {"1", "2", "3", "4", "5", "6", "8"}])
def test_partial_waves_and_report_only_completion_are_rejected(done) -> None:
    """@brief 中间阶段、只有回执或未知阶段不能冒充完整实施。"""
    with pytest.raises(AssertionError):
        _check_stages(frozenset(done))


@pytest.mark.parametrize("change", ["missing", "stage", "closed_without_evidence"])
def test_missing_contract_wrong_stage_or_empty_closed_evidence_is_rejected(change) -> None:
    """@brief 台账身份、阶段与完成证据分别有真实反例。"""
    ledger, done = _ledger(), frozenset()
    name = next(iter(ledger))
    if change == "missing":
        del ledger[name]
    elif change == "stage":
        ledger[name]["stage"] = "7"
    else:
        ledger[name]["tests"] = []
        ledger[name]["implementation"] = []
        done = registration.IMPLEMENTATION_STAGES
    with pytest.raises(AssertionError):
        _check_ledger(ledger, _spec_table("spec-acs-验收台账", LEDGER_HEADER), done)


def test_behavior_owner_stage_cannot_be_moved_to_integration_stage() -> None:
    """@brief 收货行为归阶段二，晚期引擎证据不能抬高所有权。"""
    cases = copy.deepcopy(registration.BEHAVIOUR_CASES)
    cases["T12"]["owner_stage"] = "4"
    with pytest.raises(AssertionError):
        _check_cases(cases, _ledger(), frozenset())


@pytest.mark.parametrize("change", ["missing", "contract", "closed_without_mutation"])
def test_missing_mutation_wrong_contract_or_empty_closed_clause_is_rejected(change) -> None:
    """@brief 变异漏项、错绑契约与无源码变异的完成声明均被拒绝。"""
    rows, done = copy.deepcopy(registration.MUTATION_ROWS), frozenset()
    text = next(iter(rows))
    if change == "missing":
        del rows[text]
    elif change == "contract":
        rows[text]["clauses"][0]["contract"] = "完整日基线"
    else:
        rows[text]["clauses"][0]["mutations"] = ()
        done = registration.IMPLEMENTATION_STAGES
    with pytest.raises(AssertionError):
        _check_mutation_rows(rows, _ledger(), done)


def test_future_nodes_empty_bodies_and_nonproduction_symbols_are_rejected(tmp_path) -> None:
    """@brief 创建真实反例文件，未来测试、空壳和测试定义不能作为验收。"""
    path = tmp_path / "tests/test_real.py"
    path.parent.mkdir()
    path.write_text("def test_real():\n    pass\n", encoding="utf-8")
    for node in ("tests/test_real.py::test_future", "tests/test_real.py::test_real"):
        with pytest.raises(AssertionError):
            _check_nodes([node], tmp_path)
    with pytest.raises(AssertionError):
        _check_implementation("tests/test_real.py::test_real", tmp_path)


def test_skipped_nodes_are_rejected_while_unfinished_wave_is_empty(tmp_path) -> None:
    """@brief 即使尚未关闭波次，跳过的真实节点也不能被登记。"""
    path = tmp_path / "tests/test_real.py"
    path.parent.mkdir()
    path.write_text("import pytest\n@pytest.mark." + "sk" + "ip\ndef test_real():\n    assert 1 == 1\n",
                    encoding="utf-8")
    with pytest.raises(AssertionError):
        _check_nodes(["tests/test_real.py::test_real"], tmp_path)


def test_structural_guard_cannot_kill_a_behavior_source_mutation() -> None:
    """@brief 工程门禁用例不能替代源码行为变异的杀手。"""
    node = "tests/test_acs_acceptance.py::test_stage_sets_accept_only_frozen_full_wave_or_full_wave_with_receipts"
    mutation = registration.SourceMutation("structural_killer", "worldmock.life.models::WorldState", "old", "new",
                                           (node,))
    with pytest.raises(AssertionError):
        _check_mutations({mutation.name: ("6", "工程门禁与文档一致性", {"T50"})}, _ledger(),
                         registration.BEHAVIOUR_CASES, (mutation,))
