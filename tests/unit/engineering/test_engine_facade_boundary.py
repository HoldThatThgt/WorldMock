"""@brief 引擎门面的边界：旧环境类与只读转发已删除且名字不再出现、模拟引擎不导入已删除的框架、人物使用的五个引擎方法都是同步方法（docs/design/10-runtime.md）。"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from worldmock.simulation_engine.engine import LifeEngine

ROOT = Path(__file__).resolve().parents[3]
#: 守卫扫描的目录与单独列出的文件；docs/dev 下冻结的开发记录不扫。
_SCANNED_DIRECTORIES = ("src", "tests", "scripts", "tools", "docs/design", "docs/manual")
_SCANNED_FILES = ("README.md", "docs/INDEX.md", "pyproject.toml")
#: 只读这些后缀的文本文件，不解码二进制夹具。
_TEXT_SUFFIXES = frozenset({".py", ".md", ".toml", ".json", ".jsonl", ".html", ".js", ".txt", ".yml", ".yaml"})
#: 旧环境类名（含以它开头的标识符）的正则与五个固定串；都由片段拼成，本文件自身不含它们。
_RETIRED_NAME = re.compile(r"\b" + "Life" + "Env")
_RETIRED_STRINGS = (
    "life_env" + "_actor_class",
    "custom" + ".envs",
    "custom" + "/envs",
    "runtime.framework" + ".read_query",
    "runtime/framework" + "/read_query",
)
#: 删除的框架核对文字：src/ 里不得再出现。
_RETIRED_CHECKS = (
    "Society current time disagrees with the environment",
    "Checkpoint request must use SocietyCheckpointRequest",
    "Checkpoint request identity or time is stale",
    "Checkpoint request agent set is incomplete",
    "tool surface disagrees",
    "query surface disagrees",
    "Agent" + "Society2 DecisionContext",
)
#: 已删除框架与它的进程框架的顶层模块名，由片段拼成。
_REMOVED_MODULES = frozenset({"agent" + "society2", "r" + "ay"})
#: 人物使用的五个引擎方法与各自的参数名（含 self）。
_PERSON_METHODS = {
    "observe": ["self", "context"],
    "submit_action": ["self", "call"],
    "read_query": ["self", "context", "tool", "request"],
    "record_model_exchange": ["self", "exchange"],
    "record_memory_update": ["self", "evidence"],
}


def _scanned_texts() -> list[tuple[str, str]]:
    """守卫扫描的文本：规定目录下规定后缀的文件（跳过 __pycache__）与三个单独列出的文件，按 UTF-8 读出，带仓库相对路径。"""
    paths = [path for directory in _SCANNED_DIRECTORIES for path in sorted((ROOT / directory).rglob("*"))
             if path.is_file() and path.suffix in _TEXT_SUFFIXES and "__pycache__" not in path.parts]
    paths.extend(ROOT / name for name in _SCANNED_FILES)
    return [(path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")) for path in paths]


def test_life_env_and_its_forwarders_are_gone():
    """旧环境类与跨进程只读转发两个文件不存在；现行代码与现行文档里没有以旧环境类名开头的标识符，也没有五个旧串；
    src/ 里没有删除的框架核对文字。"""
    assert not (ROOT / "custom" / "envs" / "life_env.py").exists()
    assert not (ROOT / "src" / "worldmock" / "runtime" / "framework" / "read_query.py").exists()
    texts = _scanned_texts()
    named = [name for name, text in texts
             if _RETIRED_NAME.search(text) or any(needle in text for needle in _RETIRED_STRINGS)]
    assert named == []
    checks = [f"{name}: {check}" for name, text in texts if name.startswith("src/")
              for check in _RETIRED_CHECKS if check in text]
    assert checks == []


def test_simulation_engine_imports_no_framework():
    """模拟引擎的每个模块都不导入已删除的框架与它的进程框架，含 TYPE_CHECKING 块与函数体内的导入。"""
    found = []
    for path in sorted((ROOT / "src" / "worldmock" / "simulation_engine").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            found.extend(f"{path.relative_to(ROOT).as_posix()}: {name}" for name in names
                         if name.split(".")[0] in _REMOVED_MODULES)
    assert found == []


def test_person_facing_engine_methods_are_synchronous():
    """人物使用的五个引擎方法都是普通同步方法（去掉自动启用的已提交世界守卫之后看），参数名依次固定。"""
    for name, parameters in _PERSON_METHODS.items():
        method = inspect.unwrap(getattr(LifeEngine, name))
        assert not inspect.iscoroutinefunction(method), name
        assert list(inspect.signature(method).parameters) == parameters, name
