"""分层与测试层规格的闭合登记、生产边界和改动前证据。"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from tests.acceptance_guard import DEFERRAL, resolve_problem, stub_functions
from scripts.documentation_gate import symbol_problem

ROOT = Path(__file__).resolve().parents[1]
CLAUSES = frozenset({
    "完整导入图", "延迟与基础层", "显式引擎协作", "完整日不变", "当前引用门禁", "历史基线", "正文与归档",
    "文本与验收解耦", "符号作用域突变", "缓存隔离", "真实装配", "自动分层", "分片完整性", "覆盖与耗时",
    "文档设计一致",
})
NEW_PRODUCTION = (
    "counterfactual_runtime", "decision_coordinator", "engine", "engine_context", "model_evidence", "observe",
    "session_actions", "storefront_runtime", "time_advance", "vocabulary", "world_ops", "world_setup",
)
ARCHIVED_FEATURES = ("time-and-energy", "persona-prompt", "dayrun-fidelity", "tool-interaction", "errands-workday")
FEATURES = (*ARCHIVED_FEATURES, "resource-rule-simplification")


def _fixture(name):
    """读取相对项目位置的冻结机器证据。"""
    return json.loads((ROOT / "tests/fixtures" / name).read_text(encoding="utf-8"))


def test_every_layering_clause_has_executable_nonempty_evidence():
    """新增、漏登、跳过或空壳验收都不能视作规格完成。"""
    ledger = _fixture("layering-acceptance.json")
    assert set(ledger) == CLAUSES
    spec = (ROOT / "docs/dev/SPEC-layering-and-test-tiers.md").read_text(encoding="utf-8")
    for name, row in ledger.items():
        assert f"| {name} |" in spec
        assert row["tests"], name
        assert row["implementation"], name
        for reference in row["implementation"]:
            target, symbol = reference.split("::")
            assert symbol_problem(ROOT, target, symbol) is None, (name, reference)
        path, anchor = row["spec"].split("#")
        assert f'<a name="{anchor}"></a>' in (ROOT / path).read_text(encoding="utf-8")
        for node in row["tests"]:
            assert resolve_problem(node) is None, (name, node, resolve_problem(node))
            path, function = node.split("::")
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            body = next(item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == function)
            assert not stub_functions(body), (name, node)


def test_new_production_responsibilities_have_no_deferred_or_empty_implementation():
    """每个新增生产职责必须是完整实现。"""
    paths = [ROOT / f"src/worldmock/life/{name}.py" for name in NEW_PRODUCTION]
    paths += [ROOT / "src/worldmock/run_setup.py", ROOT / "src/worldmock/profile/coverage_targets.py"]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not DEFERRAL.search(text), path.relative_to(ROOT)
        assert not stub_functions(ast.parse(text)), path.relative_to(ROOT)


def test_facade_keeps_public_signatures_and_has_no_business_mixin():
    """公开生活协议保留，旧 Mixin 和业务私有代理不能留在门面。"""
    path = ROOT / "src/worldmock/life/engine.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    facade = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LifeEngine")
    assert not facade.bases
    constructor = next(node for node in facade.body
                       if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    assignments = {node.attr for node in ast.walk(constructor)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name) and node.value.id == "self"}
    assert assignments == {"context", "observations", "actions", "decisions", "clock", "evidence",
                           "counterfactual"}
    methods = {node.name: node for node in facade.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    expected = _fixture("layering-public-api.json")["methods"]
    assert {name for name in methods if not name.startswith("_")} == set(expected)
    for name, signature in expected.items():
        method = methods[name]
        actual = {
            "arguments": ast.dump(method.args, include_attributes=False),
            "returns": ast.dump(method.returns, include_attributes=False) if method.returns else None,
            "decorators": [ast.dump(item, include_attributes=False) for item in method.decorator_list],
        }
        assert actual == signature, name
    assert {name for name in methods if name.startswith("_")} == {"__init__", "_life_snapshot"}
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 600
    retired = {"ObservationMixin", "SessionActionMixin", "StorefrontRuntimeMixin"}
    for source in (ROOT / "src/worldmock/life").glob("*.py"):
        assert not retired.intersection(node.name for node in ast.walk(ast.parse(source.read_text("utf-8")))
                                        if isinstance(node, ast.ClassDef))


def test_context_has_one_state_owner_and_no_facade_back_reference():
    """上下文保存唯一世界，协作者的构造接口不接受整个门面。"""
    from worldmock.life.engine_context import EngineContext

    assert set(EngineContext.__dataclass_fields__) == {
        "semantics", "scenario_sha256", "corpus_root", "store", "state", "rules", "lifestyle_items", "titles",
        "affordance_contract", "ops",
    }
    assert isinstance(EngineContext.scenario, property)
    for name in NEW_PRODUCTION:
        tree = ast.parse((ROOT / f"src/worldmock/life/{name}.py").read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name != "__getattr__"
                if node.name == "__init__":
                    assert not {"engine", "facade"}.intersection(arg.arg for arg in node.args.args)


def test_accepted_increment_specs_have_one_current_owner():
    """原位置删除且历史原文归档，现行行为登记覆盖所有已验收特性。"""
    from scripts.documentation_owners import find_owner_problems

    requirements = _fixture("acceptance_requirements.json")
    assert set(FEATURES) == set(requirements)
    assert find_owner_problems(ROOT) == []
    for feature in ARCHIVED_FEATURES:
        assert not (ROOT / f"docs/dev/SPEC-{feature}.md").exists()
        archived = list((ROOT / "docs/archive" / feature).glob(f"SPEC-{feature}.md"))
        assert len(archived) == 1, feature
        text = archived[0].read_text(encoding="utf-8")
        assert "2cf230115f4084aef2376e9d77bc976efbed43ea" in "\n".join(text.splitlines()[:20])


def test_original_mutation_generation_has_complete_byte_equivalence_evidence():
    """仍登记的历史变异保留两个生成器逐字节一致的证据，退役符号不再要求当前存在。"""
    from tests.errands_workday_acceptance import MUTATIONS as errands
    from tests.tool_interaction_acceptance import MUTATIONS as tools

    proof = _fixture("mutation-symbol-baseline.json")
    expected = {f"tool:{item.name}" for item in tools} | {f"errands:{item.name}" for item in errands}
    retained = expected & set(proof["mutations"])
    assert retained, "current mutation registration has no historical evidence intersection"
    assert proof["source_commit"] == _fixture("dayrun-layering-baseline.json")["source_commit"]
    for name in retained:
        row = proof["mutations"][name]
        assert row["legacy_generated_sha256"] == row["symbol_generated_sha256"]
        assert row["original_sha256"] != row["symbol_generated_sha256"]


def test_layering_implementation_ledger_is_bidirectional():
    """所有新增职责文件必须被规格认领，真实实现符号只能有一个条款归属。"""
    ledger = _fixture("layering-acceptance.json")
    references = [reference for row in ledger.values() for reference in row["implementation"]]
    assert len(references) == len(set(references))
    expected = {f"src/worldmock/life/{name}.py" for name in NEW_PRODUCTION}
    expected |= {
        "src/worldmock/run_setup.py", "src/worldmock/profile/coverage_targets.py",
        "scripts/documentation_refs.py", "scripts/documentation_gate.py", "scripts/documentation_owners.py",
        "scripts/run_tests.py",
        "tests/frozen_inputs.py", "tests/engine_fixture.py", "tests/dayrun_baseline.py",
        "tests/mutation_runner.py", "tests/tier_support.py", "tests/test_import_graph.py", "tests/conftest.py",
    }
    actual = {reference.split("::")[0] for reference in references}
    assert expected <= actual, sorted(expected - actual)


@pytest.mark.parametrize("fixture_name", ["dayrun-layering-baseline.json", "dayrun-shared-rules-baseline.json"])
def test_real_day_baseline_was_frozen_only_after_two_matching_runs(fixture_name):
    """冻结工件必须携带真实完整日双跑回执，两个种子的完整摘要逐项相等。"""
    proof = _fixture(fixture_name)
    generation = proof["generation"]
    assert generation["source_commit"] == proof["source_commit"]
    assert generation["harness_sha256"] == hashlib.sha256(
        (ROOT / "tests/dayrun_baseline.py").read_bytes()).hexdigest()
    encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert generation["cases_sha256"] == hashlib.sha256(encode(proof["cases"])).hexdigest()
    expected = {seed: hashlib.sha256(encode(value)).hexdigest() for seed, value in proof["cases"].items()}
    assert set(expected) == {"20260105", "20260106"}
    runs = generation["runs"]
    assert len(runs) == 2 and len({row["run"] for row in runs}) == 2
    for row in runs:
        assert row["tests"] == 2
        assert row["failures"] == row["errors"] == row["skipped"] == 0
        assert row["wall_seconds"] > 0
        assert row["case_signatures"] == expected
        assert len(row["execution_receipt_sha256"]) == 64
    assert runs[0]["case_signatures"] == runs[1]["case_signatures"]
