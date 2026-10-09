"""符号作用域变异执行器的边界与反例。"""

from pathlib import Path
import subprocess
import sys

import pytest

from tests import mutation_runner
from tests.mutation_runner import SourceMutation, mutated_source, source_location


def _source(tmp_path: Path, monkeypatch, text: str) -> None:
    """@brief 建立隔离生产模块，不改共享仓库的实际源码。"""
    path = tmp_path / "src/worldmock/sample.py"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(mutation_runner, "REPO_ROOT", tmp_path)


def _case(symbol="worldmock.sample::Example.answer", original="return 1", replacement="return 2"):
    """@brief 构造最小变异登记。"""
    return SourceMutation("sample", symbol, original, replacement, ("tests/sample.py::test_answer",))


def test_mutation_changes_only_the_named_definition_with_duplicate_text_elsewhere(tmp_path, monkeypatch):
    """@brief 同文出现在其它函数时，仅指定方法发生语义变异。"""
    text = "class Example:\n    def answer(self):\n        return 1\n\ndef other():\n    return 1\n"
    _source(tmp_path, monkeypatch, text)
    original, changed = mutated_source(_case())
    assert original == text
    assert changed == text.replace("        return 1", "        return 2", 1)
    namespace = {}
    exec(compile(changed, "sample.py", "exec"), namespace)
    assert namespace["Example"]().answer() == 2
    assert namespace["other"]() == 1


def test_mutation_resolves_nested_function_and_module_assignment(tmp_path, monkeypatch):
    """@brief 嵌套定义及具名赋值均能按当前符号定位。"""
    text = "LIMIT = 1\ndef outer():\n    def inner():\n        return 1\n    return inner()\n"
    _source(tmp_path, monkeypatch, text)
    _, nested = mutated_source(_case("worldmock.sample::outer.inner"))
    _, assigned = mutated_source(_case("worldmock.sample::LIMIT", "LIMIT = 1", "LIMIT = 2"))
    assert nested == text.replace("return 1", "return 2")
    assert assigned == text.replace("LIMIT = 1", "LIMIT = 2")


@pytest.mark.parametrize("text,symbol", [
    ("def other():\n    return 1\n", "worldmock.sample::missing"),
    ("from other import answer\n", "worldmock.sample::answer"),
    ("from other import original\nanswer = original\n", "worldmock.sample::answer"),
    ("import other as imported\nanswer = imported.original\n", "worldmock.sample::answer"),
    ("def answer():\n    return 1\ndef answer():\n    return 1\n", "worldmock.sample::answer"),
    ("def outer():\n    value = 1\n    value = 2\n", "worldmock.sample::outer.value"),
])
def test_missing_imported_or_ambiguous_symbol_is_rejected(tmp_path, monkeypatch, text, symbol, caplog):
    """@brief 不存在、再导出与不唯一的定义不能假装有效锚点。"""
    _source(tmp_path, monkeypatch, text)
    with pytest.raises(ValueError, match="does not resolve uniquely"):
        mutated_source(_case(symbol))
    assert "does not resolve uniquely" in caplog.text


@pytest.mark.parametrize("original,replacement,message", [
    ("absent", "return 2", "not unique"),
    ("return 1", "return 1", "does not change"),
    ("return 1", "return (", "compile"),
])
def test_invalid_original_equal_replacement_and_invalid_program_are_rejected(
        tmp_path, monkeypatch, original, replacement, message, caplog):
    """@brief 拒绝失效原文、等价替换和无法执行的变异。"""
    _source(tmp_path, monkeypatch, "class Example:\n    def answer(self):\n        return 1\n")
    with pytest.raises((ValueError, SyntaxError)):
        mutated_source(_case(original=original, replacement=replacement))
    assert message in caplog.text


def test_original_must_be_unique_inside_the_definition(tmp_path, monkeypatch):
    """@brief 同一方法内重复原文仍然失败，不能随意选择首次出现。"""
    text = "class Example:\n    def answer(self):\n        if False:\n            return 1\n        return 1\n"
    _source(tmp_path, monkeypatch, text)
    with pytest.raises(ValueError, match="exactly once"):
        mutated_source(_case())


@pytest.mark.parametrize("symbol", ["worldmock.sample", "worldmock.sample::", "os::path", "tools.sample::answer", "tests.sample::answer", "custom.x::a..b"])
def test_symbol_rejects_nonproduction_or_malformed_location(symbol):
    """@brief 固定生产包根不接受任意模块与畸形限定名。"""
    with pytest.raises(ValueError):
        source_location(symbol)


def test_source_mutation_has_only_current_symbol_fields():
    """@brief 登记只有当前定义符号，不保留旧模块或路径兼容字段。"""
    assert set(SourceMutation.__dataclass_fields__) == {"name", "symbol", "original", "replacement", "tests"}
    assert source_location("custom.agents.life_agent::LifeAgent.step") == (
        "custom.agents.life_agent", "custom/agents/life_agent.py")


def test_source_mutation_resolves_actual_python_production_roots():
    """@brief 三个实际 Python 生产根只解析当前物理模块。"""
    for symbol, relative in (
        ("worldmock.life.interact::InteractivityRuntime.start", "src/worldmock/life/interact.py"),
        ("custom.agents.life_agent::LifeAgent.step", "custom/agents/life_agent.py"),
        ("scripts.freeze_rrs_baseline::freeze_matching_baseline", "scripts/freeze_rrs_baseline.py"),
    ):
        module, path = source_location(symbol)
        assert module == symbol.split("::")[0] and path == relative
        assert (mutation_runner.REPO_ROOT / path).is_file()


def test_retired_test_environment_selector_is_rejected(tmp_path):
    """@brief 真实测试入口拒绝退役环境选择参数，不接受旧发布目录路径。"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--environment-root", str(tmp_path), "--collect-only"],
        cwd=mutation_runner.REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 4
    assert "unrecognized arguments: --environment-root" in result.stderr
