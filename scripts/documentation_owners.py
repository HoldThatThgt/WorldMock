"""@brief 验收主题与现行规范正文的机器所有权核对，不绑定标题措辞或散文字节。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.documentation_refs import ANCHOR, DOCUMENT_REFERENCE, HEADING, MARKDOWN_LINK, section_text

OWNER_FIXTURE = "tests/fixtures/current-feature-owners.json"
ACCEPTANCE_FIXTURE = "tests/fixtures/acceptance_requirements.json"


def requirement_names(feature: dict) -> set[str]:
    """@brief 所有验收表主题和源码变异契约必须登记；历史裁决记录不重复充当现行规范。"""
    names = {"验收：" + row[0] for name, section in feature.items() if name != "裁决记录"
             and isinstance(section, dict) for table in section.get("tables", []) for row in table[1:]}
    return names | {"变异：" + name for name in feature.get("mutation_requirements", [])} \
        | {"规范：" + name for name in feature.get("normative_requirements", [])}


def substantive_body(body: str) -> bool:
    """@brief 正文须含实际规则、字段或模板；标题、标识、导航与纯链接不算规范内容。"""
    for line in body.splitlines():
        line = line.strip()
        if not line or ANCHOR.fullmatch(line) or HEADING.fullmatch(line) or line.startswith(("```", "~~~")):
            continue
        has_reference = bool(MARKDOWN_LINK.search(line) or DOCUMENT_REFERENCE.search(line))
        remaining = MARKDOWN_LINK.sub("", line)
        remaining = DOCUMENT_REFERENCE.sub("", remaining)
        remaining = re.sub(r"https?://\S+", "", remaining)
        remaining = re.sub(r"[`*_>|：:。，；;（）()\[\]\s-]", "", remaining)
        if not remaining:
            continue
        if has_reference and re.fullmatch(
                r"(?:见|参见|引用|详见|完整文本|唯一权威|权威文本|文本|提示与模板的|模板|字段定义|以|为准)+", remaining):
            continue
        return True
    return False


def owner_problem(root: Path, reference: str) -> str | None:
    """@brief 现行所有者必须是仓库文档的明确章节，并保留非占位规范正文。"""
    path_text, separator, anchor = reference.partition("#")
    path = root / path_text
    if not separator or not anchor or not path_text.startswith("docs/") or ".." in Path(path_text).parts:
        return f"current owner requires a repository document and explicit section: {reference}"
    if path_text.startswith(("docs/archive/", "docs/dev/research/")) or not path.is_file():
        return f"current owner document is missing or historical: {reference}"
    try:
        body = section_text(path.read_text(encoding="utf-8"), anchor)
    except (StopIteration, AttributeError):
        return f"current owner section does not exist: {reference}"
    if not substantive_body(body):
        return f"current owner requires substantive normative body: {reference}"
    return None


def find_owner_problems(root: Path) -> list[str]:
    """@brief 检查五项已归档特性的全量要求与所有权；删除登记、正文或台账都失败。"""
    paths = [root / OWNER_FIXTURE, root / ACCEPTANCE_FIXTURE]
    if any(not path.is_file() for path in paths):
        return ["current feature owners and acceptance requirement fixtures are required"]
    owners, acceptance = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    problems = []
    if set(owners) != set(acceptance):
        problems.append("current feature owners must cover every accepted feature")
    for feature, requirements in owners.items():
        missing = requirement_names(acceptance.get(feature, {})) - set(requirements)
        problems.extend(f"{feature}: missing current owner requirement: {name}" for name in sorted(missing))
        extra = set(requirements) - requirement_names(acceptance.get(feature, {}))
        problems.extend(f"{feature}: unexpected current owner requirement: {name}" for name in sorted(extra))
        if not requirements:
            problems.append(f"{feature}: current owner requirements cannot be empty")
        for name, reference in requirements.items():
            if problem := owner_problem(root, reference):
                problems.append(f"{feature}/{name}: {problem}")
    return problems
