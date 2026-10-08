#!/usr/bin/env python3
"""@brief 检查当前系统设计和手册的本地链接；历史开发记录不参加现行文档门禁。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
ANCHOR = re.compile(r'<a (?:name|id)="([^"]+)"></a>')


@dataclass(frozen=True)
class Problem:
    """@brief 当前文档中的断链或重复标识。"""

    source: str
    line: int
    message: str


def prose_lines(text: str) -> list[tuple[int, str]]:
    """@brief 只检查代码围栏之外的正文，避免把示例当作实际文档链接。"""
    lines, fence = [], None
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(\x60{3,}|~{3,})", line)
        if match:
            marker = match.group(1)[0]
            fence = marker if fence is None else None if marker == fence else fence
        elif fence is None:
            lines.append((number, line))
    return lines


def scan_documents(root: Path) -> list[Path]:
    """@brief 现行入口、模块设计、用户手册是唯一的链接检查范围。"""
    documents = [root / name for name in ("README.md", "AGENTS.md", "docs/INDEX.md")]
    for directory in ("docs/design", "docs/manual"):
        documents.extend(sorted((root / directory).rglob("*.md")))
    return [path for path in documents if path.is_file()]


def section_identifiers(text: str) -> set[str]:
    """@brief 收集显式标识和 Markdown 标题锚点，重复标题按出现次序编号。"""
    identifiers, counts = set(), {}
    for _, line in prose_lines(text):
        identifiers.update(ANCHOR.findall(line))
        if not (match := re.match(r"^#{1,6}\s+(.+)", line)):
            continue
        slug = re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", match.group(1).lower()).strip())
        count = counts.get(slug, 0)
        identifiers.add(f"{slug}-{count}" if count else slug)
        counts[slug] = count + 1
    return identifiers


def link_problem(root: Path, source: Path, target: str) -> str | None:
    """@brief 当前本地链接必须指向存在的文件或章节，外部网址不发起网络请求。"""
    parsed = urlsplit(target)
    if parsed.scheme or target.startswith("//"):
        return None
    destination = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source.resolve()
    if not destination.is_relative_to(root.resolve()):
        return f"Reference escapes repository: {target}"
    if not destination.exists():
        return f"Target does not exist: {target}"
    if parsed.fragment and destination.suffix == ".md":
        identifiers = section_identifiers(destination.read_text(encoding="utf-8"))
        if unquote(parsed.fragment) not in identifiers:
            return f"Section does not exist: {target}"
    return None


def find_problems(root: Path = REPO_ROOT) -> list[Problem]:
    """@brief 检查当前文档的明确标识和链接，不建立历史条款归属或源码符号台账。"""
    problems = []
    for path in scan_documents(root):
        seen = set()
        relative = path.relative_to(root).as_posix()
        for number, line in prose_lines(path.read_text(encoding="utf-8")):
            for anchor in ANCHOR.findall(line):
                if anchor in seen:
                    problems.append(Problem(relative, number, f"Duplicate section identifier: {anchor}"))
                seen.add(anchor)
            for target in MARKDOWN_LINK.findall(re.sub(r"\x60[^\x60]*\x60", "", line)):
                if message := link_problem(root, path, target):
                    problems.append(Problem(relative, number, message))
    return problems


def main() -> int:
    """@brief 输出英文诊断，任一当前文档断链使检查失败。"""
    try:
        problems = find_problems()
    except (OSError, ValueError) as error:
        logging.error("Documentation check failed: %s", error)
        return 1
    for problem in problems:
        logging.error("%s:%s: %s", problem.source, problem.line, problem.message)
    print(f"[documentation] {'FAILED' if problems else 'PASSED'}: {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
