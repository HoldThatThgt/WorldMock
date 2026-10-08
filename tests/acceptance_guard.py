"""@brief 解析真实源码变异使用的测试节点，拒绝缺失与跳过的测试。"""

from __future__ import annotations
import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


_SKIP_MARK = re.compile(r"mark\.(?:skip|xfail)")


_SKIP_CALL = re.compile(r"\b(?:pytest\.)?(?:skip|xfail|importorskip)\(")


def _skip_aliases(tree: ast.Module) -> tuple[bool, set[str]]:
    """@brief 返回（模块级 pytestmark 是否跳过或预期失败, 绑定到这类标记的模块级名字）。"""
    skipped, aliases = False, set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        if _SKIP_MARK.search(ast.unparse(node.value)):
            skipped = skipped or "pytestmark" in names
            aliases |= names
    return skipped, aliases


def _skipping(carriers: list[ast.AST], aliases: set[str]) -> list[str]:
    """@brief 列出类与函数上表示跳过或预期失败的装饰器（含经模块级别名引用的标记）。"""
    found = []
    for carrier in carriers:
        for decorator in getattr(carrier, "decorator_list", []):
            text = ast.unparse(decorator)
            if _SKIP_MARK.search(text) or text.split(".")[0].split("(")[0] in aliases:
                found.append(text)
    return found


def resolve_problem(node_id: str, root: Path = REPO_ROOT) -> str | None:
    """@brief 把测试节点解析到源码中会被收集的测试函数；不存在、不会被收集或被跳过时返回原因，否则返回 None。"""
    path_text, *parts = re.sub(r"\[.*\]$", "", node_id).split("::")
    path = root / path_text
    if not path.is_file():
        return "test file does not exist"
    if not path.name.startswith("test_"):
        return "file is not a collected test module"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_skipped, aliases = _skip_aliases(tree)
    if module_skipped:
        return "module-level pytestmark skips the whole file"
    scope, carriers = tree.body, []
    if len(parts) == 2:
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == parts[0]]
        if not classes:
            return f"class {parts[0]} does not exist"
        scope, carriers = classes[-1].body, [classes[-1]]
    functions = [node for node in scope
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[-1]]
    if not functions:
        return f"test function {parts[-1]} does not exist"
    skips = _skipping([*carriers, functions[-1]], aliases)
    if skips:
        return f"skipped by {skips}"
    if _SKIP_CALL.search(ast.unparse(functions[-1])):
        return "the test body skips or expects failure"
    return None
