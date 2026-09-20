#!/usr/bin/env python3
"""文档链接门禁工具：检查文档内的仓库内路径引用是否可解析。

@file     documentation_gate.py
@brief    WorldMock 文档链接门禁；判据见 docs/VERIFICATION.md「工程门禁」的「候选与呈现一致性」节
          「文档链接可解析」整行。
@note     机器判定一项：`docs/**/*.md` 与仓库根的 `CLAUDE.md`、`README.md` 里以反引号包裹的
          仓库内路径引用全部存在；任一不存在即非零退出并逐条列出。
          只读检查，不修改任何文件；用法与 scripts/coverage_gate.py 一致。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
#: 扫描范围：文档目录全量，加两份仓库根文档（它们也引用文档树内的路径）。
SCAN_ROOTS = ("docs",)
SCAN_FILES = ("CLAUDE.md", "README.md")
#: 反向引用形状：反引号包裹的、以 .md 结尾的仓库内相对路径。
#: 绝对路径与以点开头的路径（`.work/...` 这类非版本控制产物）不在仓库内引用之列，不判定。
REFERENCE_PATTERN = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./\-]*\.md)`")
#: 只判定形如 `docs/...` 或 `dir/...` 的仓库内路径；裸文件名（如 `CONTRACTS.md`）是
#: 同目录简写，解析目标不唯一，不在此门禁的判定范围内。
REPO_PATH_PREFIX = "docs/"

#: 已退役路径白名单：这些文件按设计已从工作树删除，仅在历史性叙述里被提及
#: （"当时的规格快照 X（已退役，存于 Git 提交 <sha>）"）。它们是**可解析的**
#: 历史记录，不是失效链接；白名单只收录条目的替代物确实存在于仓库内的路径。
RETIRED_PATHS = {
    # 退役主设计单体：内容已迁入 ARCHITECTURE.md / CONTRACTS.md / docs/dev/SPEC-*.md，
    # 原文可由 Git 提交 9b7fbc8 恢复（docs/INDEX.md「版本表」与 CLAUDE.md 均有此说明）。
    "docs/agentsociety2-design.md",
    # 追溯矩阵迁移前的旧位置：现为 docs/dev/ANALYSIS-spec-coverage.md（见其版本表「迁移」行）。
    "docs/spec-coverage.md",
}


@dataclass(frozen=True)
class Reference:
    """@brief 一条文档内的仓库内路径引用及其出现位置。"""

    source: str
    line: int
    target: str


def scan_documents(root: Path) -> list[Path]:
    """@brief 枚举待扫描的文档文件（docs 目录全量加仓库根文档）。

    @param root  仓库根目录（真实运行时为 REPO_ROOT，测试时为临时目录）。
    @return      排序后的文档路径列表；缺失的根文档静默跳过。
    """
    documents: list[Path] = []
    for name in SCAN_ROOTS:
        base = root / name
        if base.is_dir():
            documents.extend(path for path in sorted(base.rglob("*.md"))
                             if "__pycache__" not in path.parts)
    for name in SCAN_FILES:
        candidate = root / name
        if candidate.is_file():
            documents.append(candidate)
    return documents


def extract_references(root: Path, document: Path) -> list[Reference]:
    """@brief 抽取一份文档里的仓库内路径引用，记录其来源与行号。

    @param root      仓库根目录（用于形成报告里的仓库相对来源路径）。
    @param document  待扫描的文档文件。
    @return          该文档内的全部仓库内路径引用。
    """
    source = document.relative_to(root).as_posix()
    references: list[Reference] = []
    for number, text in enumerate(document.read_text(encoding="utf-8").splitlines(), start=1):
        for match in REFERENCE_PATTERN.finditer(text):
            target = match.group(1)
            if not target.startswith(REPO_PATH_PREFIX) or target in RETIRED_PATHS:
                continue
            references.append(Reference(source, number, target))
    return references


def find_broken_references(root: Path) -> list[Reference]:
    """@brief 找出全部指向不存在文件的仓库内路径引用。

    @param root  仓库根目录。
    @return      排序后的失效引用列表；空列表即门禁通过。
    """
    broken: list[Reference] = []
    for document in scan_documents(root):
        for reference in extract_references(root, document):
            if not (root / reference.target).is_file():
                broken.append(reference)
    return broken


def print_gate_summary(broken: list[Reference], scanned: int) -> None:
    """@brief 打印扫描文档数、失效引用明细与最终判定。"""
    print(f"[documentation-gate] scanned documents: {scanned}")
    print(f"[documentation-gate] broken references: {len(broken)}")
    for reference in broken:
        print(f"[documentation-gate]   {reference.source}:{reference.line}: "
              f"{reference.target} does not exist")
    verdict = "PASSED" if not broken else "FAILED"
    print(f"[documentation-gate] gate verdict: {verdict}")


def main(argv: list[str]) -> int:
    """@brief 门禁主流程：扫描全部文档、判定引用可解析、返回进程退出码。

    @param argv  命令行参数（本门禁不取参数，保留以对齐其他 scripts 的入口形态）。
    @return      0 表示全部引用可解析，1 表示存在失效引用。
    """
    del argv
    documents = scan_documents(REPO_ROOT)
    broken = find_broken_references(REPO_ROOT)
    print_gate_summary(broken, len(documents))
    return 0 if not broken else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
