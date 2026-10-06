#!/usr/bin/env python3
"""@brief 当前定义、稳定章节、历史快照及代码文档引用的失败关闭门禁。"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.documentation_refs import (
    ARCHIVE_BASELINE, CODE_BASELINE, DOCUMENT_REFERENCE, MARKDOWN_LINK, REFERENCE_BASELINE, SOURCE_REFERENCE,
    archive_snapshot, definition_names, explicit_anchors, full_commit_exists, historical_content,
    prose_lines, resolve_target,
)
from scripts.documentation_owners import OWNER_FIXTURE, find_owner_problems

ROOT_DOCUMENTS = ("README.md", "CLAUDE.md", "AGENTS.md")
HISTORICAL_STATES = frozenset({"归档", "已裁决", "调研证据", "验收记录", "审查记录", "实施台账", "未冻结提案"})
SOURCE_LINE = re.compile(
    r"\.py(?:[:：]\s*\d|#L\d(?:-L?\d+)?|[`*_]*\s+(?:lines?\s+\d|第\s*\d+\s*行)"
    r"|[`*_]*\s*[（(]\s*\d+(?:\s*[-–]\s*\d+)?\s*[）)])|`[:：]\d+(?:[-–]\d+)?`", re.IGNORECASE)


@dataclass(frozen=True)
class Problem:
    """@brief 带仓库相对位置的问题，输出始终使用英文。"""

    source: str
    line: int
    message: str


def scan_documents(root: Path) -> list[Path]:
    """@brief 扫描文档全树与实际存在的根入口，设计草稿和手册属于当前文档。"""
    documents = sorted((root / "docs").rglob("*.md")) if (root / "docs").is_dir() else []
    return documents + [root / name for name in ROOT_DOCUMENTS if (root / name).is_file()]


def historical_documents(root: Path) -> set[Path]:
    """@brief INDEX 的历史状态与明确研究、归档目录决定历史归属，不按 DRAFT 判断。"""
    history = {path.resolve() for path in scan_documents(root)
               if path.relative_to(root).as_posix().startswith(("docs/archive/", "docs/dev/research/"))}
    index = root / "docs" / "INDEX.md"
    if not index.is_file():
        return history
    for _, line in prose_lines(index.read_text(encoding="utf-8")):
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) != 3 or cells[-1] not in HISTORICAL_STATES:
            continue
        references = MARKDOWN_LINK.findall(cells[0]) or re.findall(r"docs/[\w./-]+\.md", cells[0])
        for target in references:
            path, _ = resolve_target(root, index, target)
            history.add(path)
    return history


def baseline_problems(root: Path, path: Path, text: str) -> tuple[str | None, list[Problem]]:
    """@brief 历史代码基线或明确仅定位快照的归档基线必须是完整现存提交。"""
    source = path.relative_to(root).as_posix()
    header, original_body = archive_snapshot(text)
    code = [match.group(1) for line in header if (match := CODE_BASELINE.fullmatch(line))]
    archive = [match.group(1) for line in header if (match := ARCHIVE_BASELINE.fullmatch(line))]
    commits = code or archive
    if len(commits) != 1 or len(archive) > 1:
        return None, [Problem(source, 1, "historical document requires one full commit baseline")]
    commit = commits[0]
    if not full_commit_exists(root, commit):
        return None, [Problem(source, 1, f"historical commit does not exist: {commit}")]
    if archive and not full_commit_exists(root, archive[0]):
        return None, [Problem(source, 1, f"archive commit does not exist: {archive[0]}")]
    if archive and not any("不是审查执行证明" in line for line in header):
        return None, [Problem(source, 1, "archive baseline must disclaim execution evidence")]
    reference_sources = [REFERENCE_BASELINE.fullmatch(line) for line in header if line.startswith("> 引用出处：")]
    seen_sources = set()
    for match in reference_sources:
        if match is None or match.group(1) in seen_sources:
            return None, [Problem(source, 1, "historical reference requires one full commit for its exact path")]
        target, target_commit = match.groups()
        seen_sources.add(target)
        if not full_commit_exists(root, target_commit) or historical_content(root, target_commit,
                                                                           target.split("::", 1)[0]) is None:
            return None, [Problem(source, 1, f"historical reference source does not exist: {target}")]
    if archive:
        origin = next((match.group(1) for line in header
                       if (match := re.fullmatch(r"> 原始路径：`(docs/[\w./-]+\.md)`", line))), source)
        original = historical_content(root, archive[0], origin)
        if original is None:
            return None, [Problem(source, 1, f"archive original document does not exist: {origin}")]
        if not any(line.strip() and not line.startswith((">", "#", '<a name="'))
                   for _, line in prose_lines(original_body)):
            return None, [Problem(source, 1, "archive snapshot requires substantive original document body")]
        if original_body != original:
            message = "archive snapshot body differs from the declared original commit and path"
            return None, [Problem(source, 1, message)]
    return commit, []


def anchor_problems(path: Path, root: Path, text: str) -> list[Problem]:
    """@brief 显式标识必须唯一且附着标题，格式错误也不能被静默忽略。"""
    source, seen, problems = path.relative_to(root).as_posix(), set(), []
    lines = prose_lines(text)
    declarations = dict(explicit_anchors(text))
    for index, (number, line) in enumerate(lines):
        if not line.startswith('<a name="'):
            continue
        anchor = declarations.get(number)
        if anchor is None:
            problems.append(Problem(source, number, "invalid explicit section identifier"))
            continue
        if anchor in seen:
            problems.append(Problem(source, number, f"duplicate section identifier: {anchor}"))
        seen.add(anchor)
        following = next((body for _, body in lines[index + 1:] if body.strip()), "")
        if not re.match(r"^#{1,6} ", following):
            problems.append(Problem(source, number, f"section identifier has no heading: {anchor}"))
    return problems


def target_problem(root: Path, path: Path, target: str, commit: str | None) -> str | None:
    """@brief 当前路径和稳定标识直接解析；历史失效路径只在真实基线内检查。"""
    parsed = urlsplit(target)
    if parsed.scheme or target.startswith("//"):
        return None
    destination, anchor = resolve_target(root, path, target)
    if not destination.is_relative_to(root.resolve()):
        return f"reference escapes repository: {target}"
    if commit:
        text = historical_content(root, commit, destination.relative_to(root.resolve()).as_posix())
        if text is None:
            return f"historical target does not exist: {target}"
    elif destination.exists():
        text = destination.read_text(encoding="utf-8") if destination.suffix == ".md" else ""
    else:
        return f"target does not exist: {target}"
    if anchor and destination.suffix == ".md":
        anchors = {name for _, name in explicit_anchors(text)}
        if commit:
            # 历史快照中的标题标识按当时正文解析，不作为当前章节别名保留。
            anchors |= {re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", line.lstrip("# ").lower()))
                        for _, line in prose_lines(text) if re.match(r"^#{1,6} ", line)}
        if anchor not in anchors:
            return f"explicit section identifier does not exist: {target}"
    return None


def historical_source_problem(root: Path, commit: str | None, target: str) -> str | None:
    """@brief 历史源码路径按声明提交查存在性，不拿当前工作树证明旧位置。"""
    if commit is None or not target.startswith(("src/", "custom/", "tests/", "scripts/", "tools/")):
        return None
    if historical_content(root, commit, target) is None:
        return f"historical source does not exist at baseline: {target}"
    return None


def symbol_problem(root: Path, target: str, symbol: str, commit: str | None = None) -> str | None:
    """@brief 符号引用只接受声明处的词法限定名，不接受旧址再导出。"""
    path = root / target
    if commit is not None:
        content = historical_content(root, commit, target)
        if content is None:
            return f"historical source does not exist: {target}"
    else:
        if not path.is_file():
            return f"source file does not exist: {target}"
        content = path.read_text(encoding="utf-8")
    qualified = symbol.replace("::", ".")
    if qualified not in definition_names(content):
        return f"source definition does not exist: {target}::{symbol}"
    return None


def declared_symbol_problems(root: Path, source: str, header: list[str]) -> list[Problem]:
    """@brief 当前文档保留的旧裁决定义必须有唯一完整提交出处，不能放行其他现行引用。"""
    problems, seen = [], set()
    for line in header:
        if not line.startswith("> 引用出处：") or "::" not in line:
            continue
        match = REFERENCE_BASELINE.fullmatch(line)
        if match is None:
            problems.append(Problem(source, 1, "historical symbol requires one full commit source"))
            continue
        reference, commit = match.groups()
        target, symbol = reference.split("::", 1)
        if reference in seen or not full_commit_exists(root, commit):
            problems.append(Problem(source, 1, "historical symbol source is duplicate or commit does not exist"))
        elif problem := symbol_problem(root, target, symbol, commit):
            problems.append(Problem(source, 1, problem))
        seen.add(reference)
    return problems


def document_problems(root: Path, path: Path, history: set[Path]) -> list[Problem]:
    """@brief 检查一份文档的当前源码引用、稳定链接或历史快照。"""
    text = path.read_bytes().decode("utf-8")
    source, commit, problems = path.relative_to(root).as_posix(), None, []
    historical = path.resolve() in history
    if historical:
        commit, problems = baseline_problems(root, path, text)
    problems.extend(anchor_problems(path, root, text))
    reference_path, document_commit = path, commit
    header, _ = archive_snapshot(text)
    problems.extend(declared_symbol_problems(root, source, header))
    reference_sources = {match.group(1): match.group(2) for line in header
                         if (match := REFERENCE_BASELINE.fullmatch(line))}
    if historical:
        origin = next((match.group(1) for line in header
                       if (match := re.fullmatch(r"> 原始路径：`(docs/[\w./-]+\.md)`", line))), None)
        if origin:
            reference_path = root / origin
        document_commit = next((match.group(1) for line in header
                                if (match := ARCHIVE_BASELINE.fullmatch(line))), commit)
    for number, line in prose_lines(text):
        if not historical and SOURCE_LINE.search(line):
            problems.append(Problem(source, number, "current document contains a source line reference"))
        if not historical:
            for target, symbol in SOURCE_REFERENCE.findall(line):
                if problem := symbol_problem(root, target, symbol, reference_sources.get(target + "::" + symbol)):
                    problems.append(Problem(source, number, problem))
        elif any(CODE_BASELINE.fullmatch(line) for line in header):
            local_line = re.sub(r"\[[^\]]*\]\(https?://[^)]+\)", "", line)
            for target in re.findall(r"(?:src|custom|tests|scripts|tools)/[\w./-]+\.py", local_line):
                source_commit = reference_sources.get(target, commit)
                if problem := historical_source_problem(root, source_commit, target):
                    problems.append(Problem(source, number, problem))
        targets = MARKDOWN_LINK.findall(line)
        targets.extend(match.group(1) for match in re.finditer(r"`(docs/[\w./-]+\.md(?:#[\w.-]+)?)`", line))
        for target in targets:
            destination, _ = resolve_target(root, reference_path, target)
            relative = destination.relative_to(root.resolve()).as_posix() \
                if destination.is_relative_to(root.resolve()) else ""
            target_commit = reference_sources.get(relative, document_commit)
            if problem := target_problem(root, reference_path, target, target_commit):
                problems.append(Problem(source, number, problem))
    return problems


def code_reference_lines(text: str) -> list[tuple[int, str]]:
    """@brief 检查注释、独立文档字符串与字段说明，文件读取参数不当作条款引用。"""
    lines = [(token.start[0], token.string) for token in tokenize.generate_tokens(io.StringIO(text).readline)
             if token.type == tokenize.COMMENT]
    for node in ast.walk(ast.parse(text)):
        value = node.value if isinstance(node, ast.Expr) else \
            node.value if isinstance(node, ast.keyword) and node.arg == "description" else None
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            lines.extend((value.lineno + offset, line) for offset, line in enumerate(value.value.splitlines()))
    return sorted(lines)


def code_problems(root: Path, history: set[Path]) -> list[Problem]:
    """@brief 生产与测试条款引用均指向当前文档及显式标识，不使用归档作为现行权威。"""
    problems = []
    for directory in ("src", "custom", "tests"):
        for path in sorted((root / directory).rglob("*.py")):
            for number, line in code_reference_lines(path.read_text(encoding="utf-8")):
                for target, anchor in DOCUMENT_REFERENCE.findall(line):
                    if not target.startswith("docs/"):
                        continue
                    destination, _ = resolve_target(root, path, target)
                    if not anchor:
                        message = f"code reference requires an explicit section identifier: {target}"
                    elif destination in history:
                        message = f"code reference uses historical document as current authority: {target}"
                    else:
                        message = target_problem(root, path, f"{target}#{anchor}", None)
                    if message:
                        problems.append(Problem(path.relative_to(root).as_posix(), number, message))
    return problems


def find_problems(root: Path) -> list[Problem]:
    """@brief 全文档、全代码引用的正式门禁，不提供报告放行或退役白名单。"""
    history = historical_documents(root)
    problems = [problem for path in scan_documents(root) for problem in document_problems(root, path, history)]
    owners: dict[str, str] = {}
    for path in scan_documents(root):
        for line, identifier in explicit_anchors(path.read_text(encoding="utf-8")):
            source = path.relative_to(root).as_posix()
            if identifier in owners and owners[identifier] != source:
                problems.append(Problem(source, line, f"section identifier belongs to another document: {identifier}"))
            owners[identifier] = source
    problems.extend(code_problems(root, history))
    if (root / "docs/dev/SPEC-layering-and-test-tiers.md").is_file():
        problems.extend(Problem(OWNER_FIXTURE, 1, message) for message in find_owner_problems(root))
    return sorted(set(problems), key=lambda item: (item.source, item.line, item.message))


def main(argv: list[str]) -> int:
    """@brief 输出英文错误与最终判定；任何问题都非零退出。"""
    del argv
    problems = find_problems(REPO_ROOT)
    for problem in problems:
        print(f"[documentation-gate] ERROR {problem.source}:{problem.line}: {problem.message}", file=sys.stderr)
    print(f"[documentation-gate] scanned documents: {len(scan_documents(REPO_ROOT))}")
    print(f"[documentation-gate] problems: {len(problems)}")
    print(f"[documentation-gate] gate verdict: {'FAILED' if problems else 'PASSED'}")
    return int(bool(problems))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
