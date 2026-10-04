"""特性验收登记表的共用判定：机器主题、测试节点解析、延期标记与空壳函数。

@file     acceptance_guard.py
@brief    人物 Prompt 七层（tests/test_persona_prompt_acceptance.py）与一日运行保真（tests/test_dayrun_fidelity_acceptance.py）
          两个完整性守卫共用的判定：读取机器验收主题；把 path::name 或
          path::Class::name（可带参数化后缀）按 AST 解析到会被收集、未被跳过的测试函数；延期标记正则；只有 pass、省略号或
          文档字符串的空壳函数。
@note     延期标记与跳过判定的正则由字符串片段拼成，本文件自身不含任何标记字面量。文件名不以 test_ 开头，pytest 不把它
          当作测试模块收集。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def acceptance_section(feature: str, name: str) -> dict:
    """@brief 读取机器验收数据中的主题；验收登记不再依赖规格散文和章节标题。"""
    path = REPO_ROOT / "tests" / "fixtures" / "acceptance_requirements.json"
    return json.loads(path.read_text(encoding="utf-8"))[feature][name]

#: 延期标记（片段拼接，避免本文件命中自己的扫描）：英文标记、未实现异常、pytest 的跳过与预期失败，以及三个中文延期说法。
MARKERS = (
    r"\b" + "TO" + "DO" + r"\b",
    r"\b" + "FIX" + "ME" + r"\b",
    r"\b" + "X" + "XX" + r"\b",
    "NotImplemented" + "Error",
    "pytest" + r"\.(?:mark\.)?" + "skip",
    r"\bmark\." + "skip",
    "importor" + "skip",
    "x" + "fail",
    "待" + "定",
    "尚需" + "定稿",
    "待" + "实现",
)
DEFERRAL = re.compile("|".join(MARKERS))
#: 装饰器、pytestmark 或测试体中表示跳过与预期失败的片段。
_SKIP_MARK = re.compile(r"mark\.(?:" + "sk" + "ip|" + "x" + "fail)")
_SKIP_CALL = re.compile(r"\b(?:pytest\.)?(?:" + "sk" + "ip|" + "x" + "fail|importor" + "sk" + r"ip)\(")
#: 登记的测试节点形如 path::name 或 path::Class::name，可带参数化后缀。
NODE_ID = re.compile(r"^tests/[\w./-]+\.py::(?:Test\w+::)?test\w*(?:\[.*\])?$")




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


def stub_functions(tree: ast.AST) -> list[str]:
    """@brief 只有 pass、省略号或文档字符串的函数（空壳），返回「名字:行号」。"""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            body = body[1:]
        if all(isinstance(item, ast.Pass) or (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
                                              and item.value.value is Ellipsis) for item in body):
            found.append(f"{node.name}:{node.lineno}")
    return found
