"""各特性共用的源码变异执行器：唯一锚点替换、隔离子进程、基线与回执。

@file     mutation_runner.py
@brief    一个登记的源码变异（SourceMutation）只在新解释器里生效：子进程先按模块名装上变异源码的加载器，再收集并运行
          登记的行为用例，共享工作树的文件一个字节都不改。基线（不带变异）与每个变异各跑一次，各写一份回执。
          子进程入口、回执目录与超时由调用方经 RunSettings 给出；各特性使用独立回执目录，互不覆盖。
@note     变异经 sys.meta_path 按模块名注入，只对主进程里以该模块名导入的生产代码生效：Ray worker 里执行的代码、
          AS2 注册表按文件路径以另一个模块名加载的适配器类都不受影响，针对它们的变异须由直接导入该模块的用例杀死。
          文件名不以 test_ 开头，pytest 不把它当作测试模块收集。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.abc
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import dataclass

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 缺省的子进程入口模块：以 `python -m <入口> --child` 启动独立测试解释器。
CHILD_ENTRY = "tests.mutation_runner"


@dataclass(frozen=True)
class SourceMutation:
    """@brief 一个非等价源码变异与必须杀死它的独立行为用例。"""

    name: str
    symbol: str
    original: str
    replacement: str
    tests: tuple[str, ...]


@dataclass(frozen=True)
class RunSettings:
    """@brief 一次子进程运行的参数：入口模块、回执目录与超时秒数。"""

    entry: str
    receipts: Path
    timeout: int


def source_location(symbol: str) -> tuple[str, str]:
    """@brief 将当前定义符号解析为生产模块和仓库相对路径，不接受旧址映射。"""
    module, separator, qualified = symbol.partition("::")
    if (not separator or not qualified or not all(part.isidentifier() for part in qualified.split("."))
            or not all(part.isidentifier() for part in module.split("."))):
        logging.error("Invalid mutation symbol: %s", symbol)
        raise ValueError(f"Invalid mutation symbol: {symbol}")
    if module.startswith("worldmock."):
        return module, "src/" + module.replace(".", "/") + ".py"
    if module.startswith(("custom.", "scripts.")):
        return module, module.replace(".", "/") + ".py"
    logging.error("Mutation module is outside production roots: %s", module)
    raise ValueError(f"Mutation module is outside production roots: {module}")


def _import_bindings(tree: ast.AST) -> set[str]:
    """@brief 取得导入绑定，纯别名赋值不能伪装成声明处。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(item.asname or item.name.split(".")[0] for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(item.asname or item.name for item in node.names)
    return names


def _import_alias(value: ast.AST | None, imported: set[str]) -> bool:
    """@brief 只识别导入对象的纯再导出，不拒绝真实构造或计算赋值。"""
    while isinstance(value, ast.Attribute):
        value = value.value
    return isinstance(value, ast.Name) and value.id in imported


def _definitions(nodes: list[ast.stmt], name: str, imported: set[str]) -> list[ast.AST]:
    """@brief 在当前词法作用域查找真实定义，不把导入或再导出当作定义。"""
    found = []
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                found.append(node)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if (not _import_alias(node.value, imported)
                    and any(isinstance(target, ast.Name) and target.id == name for target in targets)):
                found.append(node)
        for _, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(child, ast.stmt) for child in value):
                found.extend(_definitions(value, name, imported))
    return found


def _symbol_lines(source: str, symbol: str) -> tuple[int, int]:
    """@brief 唯一定位嵌套定义的完整源码行范围，保留缩进和换行。"""
    tree = ast.parse(source)
    scope, imported = tree.body, _import_bindings(tree)
    qualified = symbol.split("::", 1)[1]
    node = None
    for name in qualified.split("."):
        matches = _definitions(scope, name, imported)
        if len(matches) != 1:
            logging.error("Mutation symbol does not resolve uniquely: %s", symbol)
            raise ValueError(f"Mutation symbol does not resolve uniquely: {symbol}")
        node = matches[0]
        scope = getattr(node, "body", [])
    return node.lineno - 1, node.end_lineno


def mutated_source(mutation: SourceMutation) -> tuple[str, str]:
    """@brief 只替换符号作用域内唯一原文，并验证变异源码可编译。"""
    _, relative = source_location(mutation.symbol)
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    start, end = _symbol_lines(source, mutation.symbol)
    lines = source.splitlines(keepends=True)
    selected = "".join(lines[start:end])
    if selected.count(mutation.original) != 1:
        logging.error("Mutation source is not unique in symbol: %s", mutation.name)
        raise ValueError(f"Mutation source must occur exactly once in symbol: {mutation.name}")
    lines[start:end] = [selected.replace(mutation.original, mutation.replacement, 1)]
    changed = "".join(lines)
    if source == changed:
        logging.error("Mutation does not change production source: %s", mutation.name)
        raise ValueError(f"Mutation must change production source: {mutation.name}")
    try:
        compile(changed, relative, "exec")
    except SyntaxError:
        logging.exception("Mutation source cannot compile: %s", mutation.name)
        raise
    return source, changed


def run_behavior(nodes: tuple[str, ...], directory: Path, mutation: SourceMutation | None,
                 settings: RunSettings) -> dict:
    """@brief 在新解释器执行行为用例；收集失败、超时或基础失败不能冒充杀死变异。"""
    command = [sys.executable, "-m", settings.entry, "--child"]
    original = changed = ""
    if mutation is not None:
        original, changed = mutated_source(mutation)
        path = directory / "mutation.json"
        module, relative = source_location(mutation.symbol)
        path.write_text(json.dumps({"module": module, "path": relative, "source": changed}),
                        encoding="utf-8")
        command.extend(["--source", str(path)])
    command.extend(nodes)
    command.extend(["--basetemp", str(directory / "child")])
    from tests.data_paths import ENVIRONMENT_ROOT

    command.extend(["--environment-root", str(ENVIRONMENT_ROOT)])
    environment = {**os.environ, "AGENTSOCIETY_LLM_API_KEY": "test-only",
                   "MEM0_TELEMETRY": "False", "ANONYMIZED_TELEMETRY": "False",
                   "LITELLM_LOCAL_MODEL_COST_MAP": "True", "PYTHONUTF8": "1"}
    result = subprocess.run(command, cwd=REPO_ROOT, env=environment, capture_output=True, text=True,
                            encoding="utf-8", timeout=settings.timeout)
    receipt = {"name": mutation.name if mutation else "baseline", "exit_code": result.returncode,
               "tests": list(nodes), "stdout": result.stdout, "stderr": result.stderr,
               "original_sha256": hashlib.sha256(original.encode()).hexdigest() if mutation else None,
               "mutated_sha256": hashlib.sha256(changed.encode()).hexdigest() if mutation else None}
    settings.receipts.mkdir(parents=True, exist_ok=True)
    (settings.receipts / f"{receipt['name']}.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                                                               encoding="utf-8")
    return receipt


class _MutatedLoader(importlib.abc.Loader):
    """@brief 在模块首次导入时执行变异源码，避免旧函数引用掩盖变异。"""

    def __init__(self, case: dict):
        self.case = case

    def create_module(self, spec):
        """@brief 由 Python 创建正常模块对象。"""
        return None

    def exec_module(self, module):
        """@brief 真实源码经编译后在正常模块命名空间执行。"""
        path = str(REPO_ROOT / self.case["path"])
        module.__file__ = path
        exec(compile(self.case["source"], path, "exec"), module.__dict__)


class _MutationFinder(importlib.abc.MetaPathFinder):
    """@brief 仅替换指定生产模块，其余导入保持正常依赖解析。"""

    def __init__(self, case: dict):
        self.case = case

    def find_spec(self, fullname, path=None, target=None):
        """@brief 精确定位目标模块，不替换行为测试或其他生产模块。"""
        if fullname == self.case["module"]:
            return importlib.util.spec_from_loader(fullname, _MutatedLoader(self.case))
        return None


def _child() -> int:
    """@brief 独立测试解释器入口，先安装源码加载器再收集行为测试。"""
    arguments = sys.argv[2:]
    if arguments[:1] == ["--source"]:
        case = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
        sys.meta_path.insert(0, _MutationFinder(case))
        arguments = arguments[2:]
    import pytest

    return pytest.main([*arguments, "-q", "--tb=short", "--disable-warnings"])


if __name__ == "__main__":
    raise SystemExit(_child())
