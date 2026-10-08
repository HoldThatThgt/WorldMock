"""@brief 引擎职责边界与唯一状态。"""

from __future__ import annotations
import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


NEW_PRODUCTION = (
    "counterfactual_runtime", "decision_coordinator", "engine", "engine_context", "model_evidence", "observe",
    "session_actions", "storefront_runtime", "time_advance", "vocabulary", "world_ops", "world_setup",
)


def _fixture(name):
    """读取相对项目位置的冻结机器证据。"""
    return json.loads((ROOT / "tests/fixtures" / name).read_text(encoding="utf-8"))


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
        "semantics", "scenario_sha256", "store", "state", "rules", "titles",
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
