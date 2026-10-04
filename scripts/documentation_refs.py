"""@brief 文档稳定标识、符号定义和历史快照的共用只读解析。"""

from __future__ import annotations

import ast
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

ANCHOR = re.compile(r'^<a name="([a-z][\w-]*)"></a>\s*$')
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SOURCE_REFERENCE = re.compile(r"(?<![\w/])([\w./-]+\.py)::([\w.]+(?:::[\w.]+)?)")
DOCUMENT_REFERENCE = re.compile(r"(?<![\w/])((?:docs/)?[\w./-]+\.md)(?:#([\w.-]+))?")
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CODE_BASELINE = re.compile(r"^> 代码基线：`?([0-9a-f]{40})`?[。；\s]*$")
ARCHIVE_BASELINE = re.compile(r"^> 归档基线：`?([0-9a-f]{40})`?[。；\s]*$")
REFERENCE_BASELINE = re.compile(r"^> 引用出处：`([\w./-]+)` @ `([0-9a-f]{40})`$")


def archive_snapshot(text: str) -> tuple[list[str], str]:
    """@brief 归档来源元数据只在首个连续引用段；其后的原文按原始字节解码结果保留。"""
    metadata, separator, body = text.partition("\n\n")
    lines = metadata.splitlines()
    if separator and lines and all(line.startswith("> ") for line in lines) \
            and any(ARCHIVE_BASELINE.fullmatch(line) for line in lines):
        return lines, body
    return text.splitlines()[:20], text


def prose_lines(text: str) -> list[tuple[int, str]]:
    """@brief 返回代码围栏以外的行，不把范例中的引用或标题作为文档结构。"""
    lines, fence = [], None
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
        elif fence is None:
            lines.append((number, line))
    return lines


def explicit_anchors(text: str) -> list[tuple[int, str]]:
    """@brief 解析唯一支持的显式标识声明，保留声明位置以报告重复。"""
    return [(number, match.group(1)) for number, line in prose_lines(text) if (match := ANCHOR.fullmatch(line))]


def section_text(text: str, anchor: str) -> str:
    """@brief 通过稳定标识读取所属标题的正文，到下一同级或更高标题为止。"""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line == f'<a name="{anchor}"></a>')
    heading = next(index for index in range(start + 1, len(lines)) if HEADING.match(lines[index]))
    level = len(HEADING.match(lines[heading]).group(1))
    body = "\n".join(lines[heading + 1:])
    end = next((number - 1 for number, line in prose_lines(body)
                if (match := HEADING.match(line)) and len(match.group(1)) <= level), len(body.splitlines()))
    result = body.splitlines()[:end]
    while result and (not result[-1].strip() or ANCHOR.fullmatch(result[-1])):
        result.pop()
    return "\n".join(result).rstrip()


def imported_call_name(value: ast.AST, imports: dict[str, str]) -> str:
    """@brief 从词法导入身份识别标准透明调用，不按自定义函数的同名拼写猜用途。"""
    if isinstance(value, ast.Name):
        return imports.get(value.id, "")
    if isinstance(value, ast.Attribute):
        owner = imported_call_name(value.value, imports)
        return owner + "." + value.attr if owner else ""
    return ""


def call_provenance(value: ast.Call, bindings: dict, imports: dict[str, str]) -> bool | tuple | dict:
    """@brief 透明取值保留原来源；真正构造和普通工厂调用仍拥有本地定义身份。"""
    if isinstance(value.func, ast.Lambda) and not value.args and not value.keywords \
            and not (value.func.args.args or value.func.args.posonlyargs or value.func.args.kwonlyargs):
        return imported_provenance(value.func.body, bindings, imports)
    identity = imported_call_name(value.func, imports)
    if identity == "typing.cast" and len(value.args) == 2:
        return imported_provenance(value.args[1], bindings, imports)
    if identity == "builtins.vars" and len(value.args) == 1:
        return imported_provenance(value.args[0], bindings, imports)
    if identity == "builtins.getattr" and len(value.args) >= 2 and isinstance(value.args[1], ast.Constant):
        sources = [imported_provenance(value.args[0], bindings, imports)]
        sources.extend(imported_provenance(item, bindings, imports) for item in value.args[2:])
        return all(source is True for source in sources)
    if isinstance(value.func, ast.Attribute) and value.func.attr == "get" \
            and value.args and isinstance(value.args[0], ast.Constant):
        container = imported_provenance(value.func.value, bindings, imports)
        if isinstance(container, dict):
            default = imported_provenance(value.args[1], bindings, imports) if len(value.args) > 1 else False
            return container.get(value.args[0].value, default)
    return False


def boolean_provenance(value: ast.BoolOp, bindings: dict, imports: dict[str, str]) -> bool:
    """@brief 按短路表达式实际可能返回的值判定来源，常量分支不引入虚假的本地结果。"""
    results = []
    for position, item in enumerate(value.values):
        truth = bool(item.value) if isinstance(item, ast.Constant) else None
        bypassed = truth is False and isinstance(value.op, ast.Or) \
            or truth is True and isinstance(value.op, ast.And)
        if bypassed and position < len(value.values) - 1:
            continue
        results.append(imported_provenance(item, bindings, imports))
        if truth is not None and not bypassed:
            break
    return bool(results) and all(item is True for item in results)


def imported_provenance(value: ast.AST | None, bindings: dict, imports: dict[str, str]) -> bool | tuple | dict:
    """@brief 静态跟踪导入绑定及字面容器；实际构造与常量保留自己的定义身份。"""
    if isinstance(value, ast.Name):
        return bindings.get(value.id, False)
    if isinstance(value, ast.Attribute):
        return imported_provenance(value.value, bindings, imports) is True
    if isinstance(value, (ast.Tuple, ast.List)):
        return tuple(imported_provenance(item, bindings, imports) for item in value.elts)
    if isinstance(value, ast.Dict):
        entries = {}
        for key, item in zip(value.keys, value.values):
            source = imported_provenance(item, bindings, imports)
            if isinstance(key, ast.Constant):
                entries[key.value] = source
            elif key is None and isinstance(source, dict):
                entries.update(source)
        return entries
    if isinstance(value, ast.IfExp):
        if isinstance(value.test, ast.Constant):
            return imported_provenance(value.body if value.test.value else value.orelse, bindings, imports)
        return all(imported_provenance(item, bindings, imports) is True for item in (value.body, value.orelse))
    if isinstance(value, ast.BoolOp):
        return boolean_provenance(value, bindings, imports)
    if isinstance(value, ast.Call):
        return call_provenance(value, bindings, imports)
    if isinstance(value, ast.Subscript):
        container = imported_provenance(value.value, bindings, imports)
        if isinstance(value.slice, ast.Constant):
            key = value.slice.value
            if isinstance(container, tuple) and isinstance(key, int) and -len(container) <= key < len(container):
                return container[key]
            if isinstance(container, dict) and key in container:
                return container[key]
        members = container.values() if isinstance(container, dict) else \
            container if isinstance(container, tuple) else ()
        return container is True or any(item is True for item in members)
    return False


def assignment_bindings(target: ast.AST, provenance: bool | tuple | dict) -> list[tuple[str, object]]:
    """@brief 解构绑定逐项保留来源，导入别名不能连带隐藏同组的本地常量。"""
    if isinstance(target, ast.Name):
        return [(target.id, provenance)]
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(provenance, tuple) \
            and len(target.elts) == len(provenance):
        return [binding for item, source in zip(target.elts, provenance)
                for binding in assignment_bindings(item, source)]
    return [(node.id, provenance) for node in ast.walk(target)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)]


def record_imports(node: ast.Import | ast.ImportFrom, bindings: dict, imports: dict[str, str]) -> None:
    """@brief 同时保存导入结果来源与其真实限定名，支持标准库模块和函数的显式别名。"""
    for alias in node.names:
        name = alias.asname or alias.name.split(".")[0]
        module = "." * node.level + (node.module or "") if isinstance(node, ast.ImportFrom) else ""
        identity = module + "." + alias.name if isinstance(node, ast.ImportFrom) else \
            alias.name if alias.asname else alias.name.split(".")[0]
        bindings[name], imports[name] = True, identity


def definition_names(text: str) -> set[str]:
    """@brief 按词法作用域枚举真实类、函数与赋值定义；导入和再导出不构成定义。"""
    names = set()
    pending = [(ast.parse(text).body, "", {}, {"getattr": "builtins.getattr", "vars": "builtins.vars"})]
    while pending:
        body, prefix, bindings, imports = pending.pop()
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                record_imports(node, bindings, imports)
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                names.add(name)
                bindings[node.name] = False
                imports.pop(node.name, None)
                arguments = {arg.arg for arg in ast.walk(node.args) if isinstance(arg, ast.arg)} \
                    if hasattr(node, "args") else set()
                pending.append((node.body, name + ".", {key: value for key, value in bindings.items()
                                                      if key not in arguments},
                                {key: value for key, value in imports.items() if key not in arguments}))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                provenance = imported_provenance(node.value, bindings, imports)
                identity = imported_call_name(node.value, imports)
                for target in targets:
                    for name, source in assignment_bindings(target, provenance):
                        bindings[name] = source
                        if identity:
                            imports[name] = identity
                        else:
                            imports.pop(name, None)
                        if source is not True:
                            names.add(prefix + name)
                        else:
                            names.discard(prefix + name)
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.AsyncWith, ast.AsyncFor)):
                pending.append((node.body, prefix, bindings, imports))
                pending.append((getattr(node, "orelse", []), prefix, bindings, imports))
                pending.append((getattr(node, "finalbody", []), prefix, bindings, imports))
                pending.extend((handler.body, prefix, bindings, imports) for handler in getattr(node, "handlers", []))
    return names


def resolve_target(root: Path, source: Path, target: str) -> tuple[Path, str]:
    """@brief 解析仓库根路径或文档相对链接，外部链接由调用方排除。"""
    parsed = urlsplit(unquote(target))
    relative = parsed.path
    path = root / relative if relative.startswith(("docs/", "src/", "custom/", "tests/", "scripts/", "vendor/")) \
        else source.parent / relative if relative else source
    return path.resolve(), parsed.fragment


@lru_cache(maxsize=None)
def full_commit_exists(root: Path, commit: str) -> bool:
    """@brief 核对完整提交存在，不把 HEAD 或短提交冒充历史基线。"""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    result = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=root,
                            capture_output=True, check=False)
    return result.returncode == 0


@lru_cache(maxsize=None)
def historical_content(root: Path, commit: str, relative: str) -> str | None:
    """@brief 从固定提交读取历史源文件；不存在时显式返回缺失，由门禁失败关闭。"""
    result = subprocess.run(["git", "show", f"{commit}:{relative}"], cwd=root,
                            capture_output=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8") if Path(relative).suffix in {".md", ".py"} else ""
