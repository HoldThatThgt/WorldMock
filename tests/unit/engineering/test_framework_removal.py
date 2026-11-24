"""@brief 去掉外部框架之后的守卫：目录、导入、进程变量、依赖锁、门禁扫描根、工作流、许可原文与来源注明都不回来。

被禁的名字一律由字符串片段在运行时拼出，本文件扫描 tests/ 时不会命中自己；11 个退役进程变量名只在这里拼一次，
别的用例需要时从本模块导入 REMOVED_PROCESS_VARIABLES。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
import tomllib
from pathlib import Path

import tests.unit.engineering.test_import_graph as import_graph
import tests.unit.engineering.test_style_gate as style_gate
from scripts import coverage_gate

ROOT = Path(__file__).resolve().parents[3]
_FRAMEWORK = "Agent" + "Society"
_VARIABLE_PREFIX = _FRAMEWORK.upper() + "_"
#: 运行装配过去写进进程环境的 11 个变量名，次序同删除前的写入次序。
REMOVED_PROCESS_VARIABLES = (
    "WORKSPACE" + "_PATH",
    _VARIABLE_PREFIX + "LLM_API_KEY",
    _VARIABLE_PREFIX + "LLM_API_BASE",
    _VARIABLE_PREFIX + "LLM_MODEL",
    _VARIABLE_PREFIX + "CODER_LLM_API_KEY",
    _VARIABLE_PREFIX + "CODER_LLM_API_BASE",
    _VARIABLE_PREFIX + "CODER_LLM_MODEL",
    _VARIABLE_PREFIX + "EMBEDDING_MODEL",
    _VARIABLE_PREFIX + "LLM_REQUEST_TIMEOUT",
    "MEM0" + "_TELEMETRY",
    "ANONYMIZED" + "_TELEMETRY",
)
_PACKAGE = _FRAMEWORK.lower() + "2"
_RAY = "r" + "ay"
#: Python 源码不得导入的顶层模块。
_REMOVED_MODULES = frozenset(("custom", _PACKAGE, _RAY, "mem0", "mcp"))
#: 依赖锁里不得再出现的包：框架本身与只为它存在的 74 个第三方包（tzdata 是 zoneinfo 在 Windows 上的时区库，作为直接依赖保留）。
REMOVED_PACKAGES = frozenset((
    _PACKAGE, "aiosqlite", "annotated-doc", "backoff", "black", "cffi", "cloudpickle", "contourpy", "cryptography",
    "cycler", "duckdb", "editables", "elemental-xenon", "faiss-cpu", "fastapi", "fonttools", "geojson", "greenlet",
    "grpcio", "h2", "hatchling", "hpack", "httpx-sse", "hyperframe", "joblib", "json-repair", "kiwisolver",
    "krippendorff", "markdown-it-py", "matplotlib", "mcp", "mdurl", "mem0ai", "msgpack", "mypy-extensions",
    "narwhals", "numpy", "pandas", "pathspec", "pillow", "platformdirs", "plotly", "portalocker", "posthog",
    "protobuf", "pycityproto", "pycparser", "pydantic-settings", "pyjwt", "pyparsing", "pyproj", "python-dateutil",
    "python-multipart", "pytokens", "pytz", "pywin32", "qdrant-client", _RAY, "rich", "scikit-learn", "scipy",
    "shapely", "shellingham", "six", "sqlalchemy", "sqlmodel", "sse-starlette", "starlette", "stringcase",
    "threadpoolctl", "trove-classifiers", "typer", "types-protobuf", "uvicorn", "wsproto",
))
LICENSE_PATH = "LICENSES/" + _FRAMEWORK + "2-Apache-2.0.txt"
#: 含上游衍生代码的模块在文档串最后一行写的唯一来源与许可句。
PROVENANCE_NOTE = ("来源：本模块部分代码改写自 " + _FRAMEWORK + "2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 "
                   + LICENSE_PATH + "。")
#: 写来源句的模块（仓库相对路径）：各章指定的五个模块加来源核对复核新增的模块。
UPSTREAM_DERIVED_MODULES = frozenset((
    "src/worldmock/agents/protocol/agent_loop.py",
    "src/worldmock/state/model/exchange.py",
    "src/worldmock/configuration/model/model_client.py",
    "src/worldmock/agents/memory/workspace.py",
    "src/worldmock/agents/memory/life_memory_store.py",
))
_FRAMEWORK_NAME = re.compile(
    _FRAMEWORK + "|" + _FRAMEWORK.lower() + "|(?<![A-Za-z0-9])" + "A" + "S2" + "(?![A-Za-z0-9])")
_PRODUCTION_ROOTS = ["src/worldmock", "tools/analysis"]


def _python_files(*folders: str) -> list[Path]:
    """@brief 列出给定目录下全部 Python 源码，跳过 __pycache__，按路径排序。"""
    return [path for folder in folders for path in sorted((ROOT / folder).rglob("*.py"))
            if "__pycache__" not in path.parts]


def _imported_top_modules(path: Path) -> list[str]:
    """@brief 一个源码文件里 import 的每个名字与绝对 from import 的模块，按点切开后的第一段。"""
    tops = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            tops.extend(item.name.split(".")[0] for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.append(node.module.split(".")[0])
    return tops


def _requirement_names(path: Path) -> set[str]:
    """@brief 一份 requirements 锁里固定了版本的包名。"""
    return {line.split("==")[0].strip() for line in path.read_text(encoding="utf-8").splitlines() if "==" in line}


def test_removed_framework_paths_are_gone():
    """@brief 仓库根下没有 custom、patches 与源码恢复脚本；许可目录按原字节检出，忽略表不再列框架生成的目录。"""
    for relative in ("custom", "patches", "scripts/bootstrap_sources.py"):
        assert not (ROOT / relative).exists(), relative
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "LICENSES/** -text" in attributes
    assert [line for line in attributes if line.startswith("patches/")] == []
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert [line for line in ignored if line.startswith("vendor/")] == []
    assert "." + _RAY + "/" not in ignored


def test_source_never_names_the_removed_process_variables():
    """@brief src、scripts、tools、tests 的 Python 源码、工作流与 pyproject.toml 都不再出现 11 个退役进程变量名。"""
    files = [*_python_files("src", "scripts", "tools", "tests"),
             *sorted((ROOT / ".github" / "workflows").glob("*.yml")), ROOT / "pyproject.toml"]
    found = [(path.relative_to(ROOT).as_posix(), name) for path in files
             for name in REMOVED_PROCESS_VARIABLES if name in path.read_text(encoding="utf-8")]
    assert found == []


def test_production_source_never_imports_the_removed_framework_or_custom():
    """@brief src 下没有一个 Python 文件导入 custom、外部框架包或只为它存在的三个顶层模块。"""
    found = [(path.relative_to(ROOT).as_posix(), top) for path in _python_files("src")
             for top in _imported_top_modules(path) if top in _REMOVED_MODULES]
    assert found == []


def test_gates_scan_the_same_production_roots():
    """@brief 覆盖率配置、覆盖率门禁与风格门禁都只扫 src/worldmock 与 tools/analysis，导入图里没有 custom 模块。"""
    coverage = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]["run"]
    assert coverage["source"] == _PRODUCTION_ROOTS
    assert list(coverage_gate.PRODUCTION_ROOTS) == _PRODUCTION_ROOTS
    assert list(style_gate.SCAN_ROOTS) == _PRODUCTION_ROOTS
    assert [pattern for pattern in coverage["omit"] if "vendor" in pattern] == []
    assert [name for name in import_graph.modules(ROOT) if name.startswith("custom.")] == []


def test_dependency_declarations_and_locks_exclude_the_removed_framework():
    """@brief 依赖声明恰为六个运行依赖、四个开发依赖与 setuptools；三份锁都与删除的 75 个包不相交。"""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == [
        "pydantic==2.12.5", "litellm==1.89.2", "networkx==3.6.1", "jsonschema==4.26.0", "requests==2.32.5",
        "tzdata==2026.4"]
    groups = project["dependency-groups"]
    assert groups["dev"] == ["coverage==7.15.4", "pytest==9.1.1", "pytest-asyncio==1.4.0", "tomlkit==0.15.1"]
    assert groups["build"] == ["setuptools==84.0.0"]
    assert "sources" not in project["tool"].get("uv", {})
    locked = {package["name"] for package in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]}
    for names in (locked, _requirement_names(ROOT / "requirements/lock.txt"),
                  _requirement_names(ROOT / "requirements/build-lock.txt")):
        assert names
        assert names & REMOVED_PACKAGES == set()


def test_locked_environment_cannot_import_the_removed_framework():
    """@brief 当前解释器的环境里找不到外部框架包与只为它存在的三个顶层模块。"""
    names = (_PACKAGE, _RAY, "mem0", "mcp")
    assert {name: importlib.util.find_spec(name) for name in names} == dict.fromkeys(names)


def test_workflows_install_no_chart_fonts():
    """@brief 两个工作流不再安装中文图表字体，查看器所需的 Node 原生库照装。"""
    for name in ("rebuildable-environment.yml", "tests.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "fonts-noto-cjk" not in text
        assert "libatomic1" in text


def test_upstream_license_is_kept_verbatim():
    """@brief 上游许可原文逐字节保存：11,362 字节，摘要与上游归档里的 LICENSE 相同。"""
    content = (ROOT / LICENSE_PATH).read_bytes()
    assert len(content) == 11362
    assert hashlib.sha256(content).hexdigest() == "b65b784ed7e4318dc1c3e8944046d867edcc272c7f237ea99dcf0c0ce3a1e75d"


def test_upstream_derived_modules_name_their_source_and_license():
    """@brief 含来源句的模块恰为登记的集合，每个模块文档串的最后一行恰为那一句。"""
    noted = {path.relative_to(ROOT).as_posix() for path in _python_files("src/worldmock")
             if PROVENANCE_NOTE in path.read_text(encoding="utf-8")}
    assert noted == UPSTREAM_DERIVED_MODULES
    for relative in sorted(UPSTREAM_DERIVED_MODULES):
        docstring = ast.get_docstring(ast.parse((ROOT / relative).read_text(encoding="utf-8")))
        assert docstring is not None, relative
        assert docstring.splitlines()[-1] == PROVENANCE_NOTE, relative


def test_production_code_names_the_framework_only_in_provenance_notes():
    """@brief 生产源码里点名外部框架的行去掉首尾空白后都恰为来源句；注释、文档串、日志与报错不再点名它。"""
    found = [(path.relative_to(ROOT).as_posix(), number) for path in _python_files(*_PRODUCTION_ROOTS)
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
             if _FRAMEWORK_NAME.search(line) and line.strip() != PROVENANCE_NOTE]
    assert found == []
