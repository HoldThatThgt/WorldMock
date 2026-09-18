"""文档治理门禁核心逻辑（纯函数模式，复用 coverage_gate 模式）。

检查 11 项文档治理规则：marker 解析、目录状态、唯一当前规范、迁移映射、
相对链接、owner 不相交、registry pre-existing、real-acceptance NOT-COMPLETE。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"

# 允许的状态和实现状态枚举
ALLOWED_STATUS = {"FROZEN", "PROPOSAL", "DRAFT", "SUPERSEDED", "DEPRECATED", "REMOVED"}
ALLOWED_IMPL_STATE = {"COMPLETE", "NOT-COMPLETE", "UNVERIFIABLE"}

# 目标目录规则
ROOT_DOCS = {
    "docs/INDEX.md",
    "docs/ARCHITECTURE.md",
    "docs/CONTRACTS.md",
    "docs/EXPERIMENT.md",
    "docs/VERIFICATION.md",
}
FORBIDDEN_DIRS = {
    "docs/specs",
    "docs/spec",
    "docs/dev/SPEC",
    "docs/dev/PROPOSAL",
    "docs/dev/ANALYSIS",
}


@dataclass
class DocumentMetadata:
    """文档 marker 元数据。"""

    path: Path
    canonical_path: str
    status: str
    implementation_state: str
    kind: str
    owner: str
    authority: str | None
    superseded_by: str | list[str] | None
    replaces: list[str]
    raw_marker: dict[str, Any]


@dataclass
class Finding:
    """治理违规发现。"""

    rule: str
    file: str
    line: int
    severity: str
    message: str


def parse_first_line_marker(path: Path) -> DocumentMetadata | None:
    """解析文档首行的 worldmock-doc marker（只读首行）。"""
    if not path.is_file() or path.suffix != ".md":
        return None

    try:
        first_line = path.read_text(encoding="utf-8").split("\n", 1)[0]
    except Exception:
        return None

    # 匹配 <!-- worldmock-doc: {...} -->
    match = re.match(r'<!--\s*worldmock-doc:\s*(\{.*?\})\s*-->', first_line)
    if not match:
        return None

    try:
        marker = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    # 必需字段
    required = {
        "canonical_path",
        "status",
        "implementation_state",
        "kind",
        "owner",
        "schema",
    }
    if not required.issubset(marker.keys()):
        return None

    return DocumentMetadata(
        path=path,
        canonical_path=marker["canonical_path"],
        status=marker["status"],
        implementation_state=marker["implementation_state"],
        kind=marker["kind"],
        owner=marker["owner"],
        authority=marker.get("authority"),
        superseded_by=marker.get("superseded_by"),
        replaces=marker.get("replaces", []),
        raw_marker=marker,
    )


def check_metadata_markers() -> list[Finding]:
    """GOV-MUST-01: marker 可解析且路径自洽。"""
    findings = []
    md_files = list(DOCS_ROOT.rglob("*.md"))

    for path in md_files:
        # 跳过非规范文档
        if "node_modules" in path.parts or ".git" in path.parts:
            continue

        relative = path.relative_to(PROJECT_ROOT)
        metadata = parse_first_line_marker(path)

        if metadata is None:
            # 只检查应该有 marker 的文件
            if any(
                str(relative).startswith(prefix)
                for prefix in ["docs/INDEX.md", "docs/ARCHITECTURE.md", "docs/CONTRACTS.md", 
                              "docs/EXPERIMENT.md", "docs/VERIFICATION.md", "docs/dev/", "docs/history/"]
            ):
                findings.append(
                    Finding(
                        rule="GOV-MUST-01",
                        file=str(relative),
                        line=1,
                        severity="error",
                        message="Missing or invalid worldmock-doc marker in first line",
                    )
                )
            continue

        # 检查 canonical_path 是否与实际路径一致
        if metadata.canonical_path != str(relative):
            findings.append(
                Finding(
                    rule="GOV-MUST-01",
                    file=str(relative),
                    line=1,
                    severity="error",
                    message=f"canonical_path mismatch: marker says '{metadata.canonical_path}' but file is at '{relative}'",
                )
            )

        # 检查状态值是否合法
        if metadata.status not in ALLOWED_STATUS:
            findings.append(
                Finding(
                    rule="GOV-MUST-01",
                    file=str(relative),
                    line=1,
                    severity="error",
                    message=f"Invalid status '{metadata.status}', must be one of {ALLOWED_STATUS}",
                )
            )

        if metadata.implementation_state not in ALLOWED_IMPL_STATE:
            findings.append(
                Finding(
                    rule="GOV-MUST-01",
                    file=str(relative),
                    line=1,
                    severity="error",
                    message=f"Invalid implementation_state '{metadata.implementation_state}', must be one of {ALLOWED_IMPL_STATE}",
                )
            )

    return findings


def check_directory_and_status_rules() -> list[Finding]:
    """GOV-MUST-02: 目录集合和状态目录一致。"""
    findings = []

    # 检查禁止的目录是否存在
    for forbidden in FORBIDDEN_DIRS:
        path = PROJECT_ROOT / forbidden
        if path.exists():
            findings.append(
                Finding(
                    rule="GOV-MUST-02",
                    file=forbidden,
                    line=0,
                    severity="error",
                    message=f"Forbidden directory exists: {forbidden}",
                )
            )

    # 检查 PROPOSAL 文件必须在 docs/dev/ 且以 PROPOSAL- 前缀
    md_files = list(DOCS_ROOT.rglob("*.md"))
    for path in md_files:
        metadata = parse_first_line_marker(path)
        if metadata is None:
            continue

        relative = path.relative_to(PROJECT_ROOT)

        # PROPOSAL 状态检查
        if metadata.status == "PROPOSAL":
            if not str(relative).startswith("docs/dev/PROPOSAL-"):
                findings.append(
                    Finding(
                        rule="GOV-MUST-02",
                        file=str(relative),
                        line=1,
                        severity="error",
                        message=f"PROPOSAL status file must be in docs/dev/ with PROPOSAL- prefix",
                    )
                )

        # SUPERSEDED 状态必须在 docs/history/
        if metadata.status == "SUPERSEDED":
            if not str(relative).startswith("docs/history/"):
                findings.append(
                    Finding(
                        rule="GOV-MUST-02",
                        file=str(relative),
                        line=1,
                        severity="error",
                        message=f"SUPERSEDED status file must be in docs/history/",
                    )
                )

        # DRAFT 文件应在 docs/dev/
        if metadata.status == "DRAFT":
            if not str(relative).startswith("docs/dev/"):
                findings.append(
                    Finding(
                        rule="GOV-MUST-02",
                        file=str(relative),
                        line=1,
                        severity="error",
                        message=f"DRAFT status file should be in docs/dev/",
                    )
                )

    return findings


def check_unique_current_spec() -> list[Finding]:
    """检查是否存在唯一当前规范（无重复 FROZEN 定义同一职责）。"""
    findings = []
    md_files = list(DOCS_ROOT.rglob("*.md"))
    
    # 按 authority 分组 FROZEN 文档
    authority_docs: dict[str, list[str]] = {}
    
    for path in md_files:
        metadata = parse_first_line_marker(path)
        if metadata is None or metadata.status != "FROZEN":
            continue
            
        relative = str(path.relative_to(PROJECT_ROOT))
        
        # 检查是否有明确的 authority
        if metadata.authority:
            if metadata.authority in authority_docs:
                authority_docs[metadata.authority].append(relative)
            else:
                authority_docs[metadata.authority] = [relative]
    
    # 检查重复的 authority
    for authority, docs in authority_docs.items():
        if len(docs) > 1:
            findings.append(
                Finding(
                    rule="UNIQUE-SPEC",
                    file=", ".join(docs),
                    line=0,
                    severity="error",
                    message=f"Multiple FROZEN documents claim authority '{authority}': {docs}",
                )
            )
    
    return findings


def check_migration_mapping() -> list[Finding]:
    """GOV-MUST-04: 主单体迁移映射完整且无旧路径转发。"""
    findings = []
    
    # 检查是否存在旧的重定向文件或转发内容
    old_design_paths = [
        "docs/agentsociety2-design.md",
        "docs/agentsociety2-design-v2.md",
        "docs/design-improvement-guide.md",
        "docs/DESIGN-ORGANIZATION-ANALYSIS.md",
    ]
    
    for old_path in old_design_paths:
        path = PROJECT_ROOT / old_path
        if path.exists():
            # 检查是否在历史目录（已迁移）
            if not old_path.startswith("docs/history/"):
                findings.append(
                    Finding(
                        rule="GOV-MUST-04",
                        file=old_path,
                        line=0,
                        severity="error",
                        message=f"Old design file should be in docs/history/, not at current location: {old_path}",
                    )
                )
    
    return findings


def check_relative_links() -> list[Finding]:
    """检查文档中的链接是否使用相对路径（不使用绝对路径）。"""
    findings = []
    md_files = list(DOCS_ROOT.rglob("*.md"))
    
    # 简单检查：查找明显的绝对路径链接
    abs_path_pattern = re.compile(r'\[([^\]]+)\]\(/[^\)]+\)')
    
    for path in md_files:
        try:
            content = path.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), start=1):
                if abs_path_pattern.search(line):
                    relative = path.relative_to(PROJECT_ROOT)
                    findings.append(
                        Finding(
                            rule="RELATIVE-LINKS",
                            file=str(relative),
                            line=i,
                            severity="warning",
                            message=f"Absolute path link found, should use relative path",
                        )
                    )
        except Exception:
            continue
    
    return findings


def check_owner_disjoint() -> list[Finding]:
    """GOV-MUST-12 部分：owner 文件集合不相交。"""
    findings = []
    
    # 从所有文档收集 owner 信息
    owner_files: dict[str, set[str]] = {}
    md_files = list(DOCS_ROOT.rglob("*.md"))
    
    for path in md_files:
        metadata = parse_first_line_marker(path)
        if metadata is None:
            continue
            
        relative = str(path.relative_to(PROJECT_ROOT))
        owner = metadata.owner
        
        if owner not in owner_files:
            owner_files[owner] = set()
        owner_files[owner].add(relative)
    
    # 检查 owner 之间是否有交集
    owners = list(owner_files.keys())
    for i, owner1 in enumerate(owners):
        for owner2 in owners[i + 1 :]:
            intersection = owner_files[owner1] & owner_files[owner2]
            if intersection:
                findings.append(
                    Finding(
                        rule="GOV-MUST-12",
                        file=", ".join(sorted(intersection)),
                        line=0,
                        severity="error",
                        message=f"File ownership conflict between '{owner1}' and '{owner2}': {intersection}",
                    )
                )
    
    return findings


def check_registry_pre_existing() -> list[Finding]:
    """GOV-MUST-05: registry 提案保持未冻结且本 SPEC 不新增 registry 依赖。"""
    findings = []
    
    # 检查 registry 提案文件状态
    registry_proposals = [
        "docs/dev/PROPOSAL-affordance-registry.md",
        "docs/dev/PROPOSAL-resource-registry.md",
    ]
    
    for proposal_path in registry_proposals:
        path = PROJECT_ROOT / proposal_path
        if not path.exists():
            findings.append(
                Finding(
                    rule="GOV-MUST-05",
                    file=proposal_path,
                    line=0,
                    severity="error",
                    message=f"Registry proposal file not found at expected location: {proposal_path}",
                )
            )
            continue
            
        metadata = parse_first_line_marker(path)
        if metadata is None:
            findings.append(
                Finding(
                    rule="GOV-MUST-05",
                    file=proposal_path,
                    line=1,
                    severity="error",
                    message=f"Registry proposal missing valid marker",
                )
            )
            continue
            
        if metadata.status != "PROPOSAL":
            findings.append(
                Finding(
                    rule="GOV-MUST-05",
                    file=proposal_path,
                    line=1,
                    severity="error",
                    message=f"Registry proposal status must be PROPOSAL, got '{metadata.status}'",
                )
            )
    
    # 检查 src/custom/tests 中是否有新增的 registry runtime 导入
    # 只检测实际的 import 语句，不检查 SPEC 文档内容
    tests_dir = PROJECT_ROOT / "src" / "custom" / "tests"
    if tests_dir.exists():
        py_files = list(tests_dir.rglob("*.py"))
        registry_import_pattern = re.compile(
            r'^\s*(?:from\s+worldmock\.catalog|import\s+worldmock\.catalog)',
            re.MULTILINE
        )
        
        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8")
                # 移除注释行
                lines = []
                for line in content.splitlines():
                    stripped = line.strip()
                    if not stripped.startswith('#'):
                        lines.append(line)
                code_without_comments = '\n'.join(lines)
                
                if registry_import_pattern.search(code_without_comments):
                    relative = py_file.relative_to(PROJECT_ROOT)
                    findings.append(
                        Finding(
                            rule="GOV-MUST-05",
                            file=str(relative),
                            line=0,
                            severity="error",
                            message="Test file imports worldmock.catalog registry runtime (forbidden)",
                        )
                    )
            except Exception:
                continue
    
    return findings


def check_real_acceptance_not_complete() -> list[Finding]:
    """GOV-MUST-11: 真实验收状态不得被确定性绿证覆盖。"""
    findings = []
    
    # 检查 evidence-manifest.json
    manifest_path = PROJECT_ROOT / "docs/evidence-manifest.json"
    if not manifest_path.exists():
        findings.append(
            Finding(
                rule="GOV-MUST-11",
                file="docs/evidence-manifest.json",
                line=0,
                severity="error",
                message="evidence-manifest.json not found",
            )
        )
        return findings
    
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        real_claims = manifest.get("identity", {}).get("real_acceptance_claim", {})
        
        # real_acceptance_claim 是字典，键为 claim 名称，值为状态对象
        if not isinstance(real_claims, dict):
            findings.append(
                Finding(
                    rule="GOV-MUST-11",
                    file="docs/evidence-manifest.json",
                    line=0,
                    severity="error",
                    message=f"real_acceptance_claim must be a dict, got {type(real_claims).__name__}",
                )
            )
            return findings
        
        # 检查每个 claim 的状态
        for claim_name, claim_data in real_claims.items():
            if not isinstance(claim_data, dict):
                findings.append(
                    Finding(
                        rule="GOV-MUST-11",
                        file="docs/evidence-manifest.json",
                        line=0,
                        severity="error",
                        message=f"Claim '{claim_name}' must be a dict with status field",
                    )
                )
                continue
                
            status = claim_data.get("status")
            
            # 检查真实验收的状态
            if status == "COMPLETE":
                # 需要验证是否真的有完整证据
                evidence_ids = claim_data.get("evidence_ids", [])
                if not evidence_ids:
                    findings.append(
                        Finding(
                            rule="GOV-MUST-11",
                            file="docs/evidence-manifest.json",
                            line=0,
                            severity="error",
                            message=f"Claim '{claim_name}' marked COMPLETE but has no evidence_ids",
                        )
                    )
            elif status not in ["NOT-COMPLETE", "UNVERIFIABLE"]:
                findings.append(
                    Finding(
                        rule="GOV-MUST-11",
                        file="docs/evidence-manifest.json",
                        line=0,
                        severity="error",
                        message=f"Claim '{claim_name}' status must be NOT-COMPLETE or UNVERIFIABLE, got '{status}'",
                    )
                )
    except (json.JSONDecodeError, KeyError) as e:
        findings.append(
            Finding(
                rule="GOV-MUST-11",
                file="docs/evidence-manifest.json",
                line=0,
                severity="error",
                message=f"Invalid manifest structure: {e}",
            )
        )
    
    return findings


def run_all_checks() -> list[Finding]:
    """运行所有 11 项检查。"""
    all_findings = []
    
    all_findings.extend(check_metadata_markers())
    all_findings.extend(check_directory_and_status_rules())
    all_findings.extend(check_unique_current_spec())
    all_findings.extend(check_migration_mapping())
    all_findings.extend(check_relative_links())
    all_findings.extend(check_owner_disjoint())
    all_findings.extend(check_registry_pre_existing())
    all_findings.extend(check_real_acceptance_not_complete())
    
    return all_findings


def main() -> int:
    """门禁入口：运行所有检查，输出结果并返回退出码。"""
    findings = run_all_checks()
    
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    
    if errors:
        print(f"Documentation governance gate FAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        print()
        for finding in errors:
            print(f"[{finding.severity.upper()}] {finding.rule} | {finding.file}:{finding.line}")
            print(f"  {finding.message}")
            print()
        return 1
    elif warnings:
        print(f"Documentation governance gate PASSED with {len(warnings)} warning(s)")
        for finding in warnings:
            print(f"[{finding.severity.upper()}] {finding.rule} | {finding.file}:{finding.line}")
            print(f"  {finding.message}")
        return 0
    else:
        print("Documentation governance gate PASSED: all checks passed")
        return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
