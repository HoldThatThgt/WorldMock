"""@brief 测试树、脚本与工具里不留已删除的框架：不导入它与 custom，不点名它，只为它存在的测试文件已删除，测试会话只设本地
价目表这一项环境变量缺省。

被禁的模块名、子串与路径一律由字符串片段拼出：本文件自身也在扫描范围里。src 一侧的导入与点名由
tests/unit/engineering/test_framework_removal.py 守住，旧环境适配类的名字与进程环境变量名各有自己的守卫。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
#: 两道守卫扫描的三个目录。
SCANNED_ROOTS = ("tests", "scripts", "tools")
#: 不得作为任何导入顶层段的五个模块名，由片段拼出。
REMOVED_TOP_LEVEL = frozenset({"cus" + "tom", "agent" + "society2", "r" + "ay", "me" + "m0", "m" + "cp"})
#: 点名守卫读的文件后缀。
NAMED_SUFFIXES = frozenset({".py", ".js", ".cjs", ".html"})
#: 点名守卫不读的目录：tools/analysis 是生产源码，由 src 一侧的点名守卫管。
NAMED_EXCLUDED = ("tools", "analysis")
#: 不得出现的子串，由片段拼出。
REMOVED_SUBSTRINGS = ("agent" + "society2", "AGENT" + "SOCIETY_", "Agent" + "Society", "--project" + "-root",
                      "cus" + "tom/agents", "cus" + "tom.agents")
#: 独立成词的两个旧名字：前后都不紧接字母、数字或下划线（不用 \b，因为中文字符也算词字符）。
REMOVED_WORDS = re.compile(r"(?<![A-Za-z0-9_])(?:[Rr]" + r"ay|[Aa][Ss]" + r"2)(?![A-Za-z0-9_])")
#: 只为已删除的框架存在、随它删除的测试目录与文件。
FRAMEWORK_ONLY_TESTS = (
    "tests/unit/" + "a" + "s2",
    "tests/integration/" + "a" + "s2",
    "tests/integration/runtime/test_llm_dispatcher_multikey.py",
    "tests/integration/runtime/test_env_lifecycle_coverage.py",
    "tests/integration/runtime/test_llm_client_close.py",
)


def _python_files() -> list[Path]:
    """@brief 三个扫描目录下全部 .py 文件（跳过 __pycache__），按路径排序。"""
    return sorted(path for root in SCANNED_ROOTS for path in (ROOT / root).rglob("*.py")
                  if "__pycache__" not in path.parts)


def _named_files() -> list[Path]:
    """@brief 点名守卫读的文件：三个扫描目录下后缀属于 NAMED_SUFFIXES 的全部文件，tools/analysis 除外，按路径排序。"""
    return sorted(path for root in SCANNED_ROOTS for path in (ROOT / root).rglob("*")
                  if path.is_file() and path.suffix in NAMED_SUFFIXES
                  and path.relative_to(ROOT).parts[:2] != NAMED_EXCLUDED)


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    """@brief 一棵语法树里每条导入的（行号, 模块名）：Import 的每个名字与绝对 ImportFrom（level 为 0）的模块。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def _line(text: str, index: int) -> int:
    """@brief 文本里第 index 个字符所在的行号（从 1 起）。"""
    return text.count("\n", 0, index) + 1


def test_no_test_script_or_tool_module_imports_the_removed_framework():
    """@brief tests、scripts、tools 下没有一个 Python 文件导入已删除框架的顶层模块或 custom：每条导入的模块名按点切开后的
    第一段都不在五个被删的名字里。"""
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [(path.relative_to(ROOT).as_posix(), line, name) for line, name in _imported_modules(tree)
                      if name.split(".")[0] in REMOVED_TOP_LEVEL]
    assert offenders == []


def test_no_test_script_or_tool_file_names_the_removed_framework():
    """@brief tests、scripts、tools（不含 tools/analysis）下的 .py、.js、.cjs、.html 文件都不含被删框架的包名、环境变量前缀、
    类名、已删除的项目根参数与 custom 下的人物模块路径，也没有独立成词的两个旧名字；不跳过任何文件。"""
    hits = []
    for path in _named_files():
        text, name = path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix()
        hits += [(name, _line(text, text.index(needle)), needle) for needle in REMOVED_SUBSTRINGS if needle in text]
        hits += [(name, _line(text, match.start()), match.group()) for match in REMOVED_WORDS.finditer(text)]
    assert hits == []


def test_the_framework_only_test_files_are_gone():
    """@brief 只为已删除的框架存在的两个测试目录与三个测试文件都不存在。"""
    assert [name for name in FRAMEWORK_ONLY_TESTS if (ROOT / name).exists()] == []


def test_the_test_session_sets_only_the_local_cost_map():
    """@brief tests/conftest.py 里 os.environ.setdefault 的调用恰为一处，键是 LITELLM_LOCAL_MODEL_COST_MAP：测试不预设
    任何供应商凭据。"""
    tree = ast.parse((ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "setdefault" and ast.unparse(node.func.value) == "os.environ"]
    assert [ast.literal_eval(call.args[0]) for call in calls] == ["LITELLM_LOCAL_MODEL_COST_MAP"]
