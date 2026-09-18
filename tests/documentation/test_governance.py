"""文档治理门禁测试（GOV-MUST-01 至 GOV-MUST-12）。

验证文档 marker 解析、目录状态、唯一当前规范、迁移映射、相对链接、
owner 不相交、registry pre-existing、real-acceptance NOT-COMPLETE 等规则。

复用 test_style_gate.py 的 AST 扫描模式和 test_coverage_gate.py 的纯函数模式。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "docs"


def _load_gate_module():
    """加载 scripts/documentation_gate.py（沿用 test_coverage_gate.py 先例）。"""
    module_path = REPO_ROOT / "scripts" / "documentation_gate.py"
    spec = importlib.util.spec_from_file_location("documentation_gate", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["documentation_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate_module()


def test_metadata_markers():
    """GOV-MUST-01: 文档 marker 可解析且路径自洽。
    
    检查所有应有 marker 的文档（根文档、dev/、history/）是否：
    1. 首行包含合法 worldmock-doc marker
    2. canonical_path 与实际路径一致
    3. status 和 implementation_state 在允许的枚举值内
    """
    findings = gate.check_metadata_markers()
    errors = [f for f in findings if f.severity == "error"]
    
    # 应该没有 marker 解析错误
    assert not errors, f"Metadata marker violations found:\n" + "\n".join(
        f"  {f.file}:{f.line} - {f.message}" for f in errors
    )


def test_directory_and_status_rules():
    """GOV-MUST-02: 目录集合和状态目录一致。
    
    检查：
    1. 不存在禁止的目录（docs/specs/、docs/spec/、docs/dev/SPEC/ 等）
    2. PROPOSAL 状态文件在 docs/dev/ 且以 PROPOSAL- 前缀命名
    3. SUPERSEDED 状态文件在 docs/history/
    4. DRAFT 状态文件在 docs/dev/
    """
    findings = gate.check_directory_and_status_rules()
    errors = [f for f in findings if f.severity == "error"]
    
    assert not errors, f"Directory and status rule violations found:\n" + "\n".join(
        f"  {f.file}:{f.line} - {f.message}" for f in errors
    )


def test_contract_definition_ownership():
    """GOV-MUST-03: 接口定义唯一归属 CONTRACTS。
    
    检查 docs/CONTRACTS.md 是接口定义的唯一来源，其他文档不重复定义接口。
    （简化实现：检查唯一当前规范，避免重复的 FROZEN authority）
    """
    findings = gate.check_unique_current_spec()
    errors = [f for f in findings if f.severity == "error"]
    
    assert not errors, f"Contract definition ownership violations found:\n" + "\n".join(
        f"  {f.file} - {f.message}" for f in errors
    )


def test_migration_map_and_no_redirects():
    """GOV-MUST-04: 主单体迁移映射完整且无旧路径转发。
    
    检查：
    1. 旧设计文件已迁移到 docs/history/
    2. 不存在旧路径的重定向或转发内容
    """
    findings = gate.check_migration_mapping()
    errors = [f for f in findings if f.severity == "error"]
    
    assert not errors, f"Migration mapping violations found:\n" + "\n".join(
        f"  {f.file} - {f.message}" for f in errors
    )


def test_relative_links():
    """检查文档中的链接使用相对路径。
    
    查找并警告使用绝对路径的链接（如 [text](/absolute/path)）。
    """
    findings = gate.check_relative_links()
    # 相对链接检查是 warning 级别，不阻止通过
    warnings = [f for f in findings if f.severity == "warning"]
    
    # 如果有警告，输出但不失败
    if warnings:
        print(f"\nRelative link warnings ({len(warnings)}):")
        for f in warnings[:5]:  # 只显示前5个
            print(f"  {f.file}:{f.line} - {f.message}")
        if len(warnings) > 5:
            print(f"  ... and {len(warnings) - 5} more")


def test_ownership_is_disjoint():
    """GOV-MUST-12 部分：owner 文件集合不相交。
    
    检查不同 owner 的文件集合两两不相交，避免所有权冲突。
    """
    findings = gate.check_owner_disjoint()
    errors = [f for f in findings if f.severity == "error"]
    
    assert not errors, f"Owner disjoint violations found:\n" + "\n".join(
        f"  {f.file} - {f.message}" for f in errors
    )


def test_registry_proposal_scope():
    """GOV-MUST-05: registry 提案保持未冻结且本 SPEC 不新增 registry 依赖。
    
    检查：
    1. affordance-registry 和 resource-registry 提案文件存在
    2. 提案状态为 PROPOSAL
    3. 治理 SPEC 不新增 registry runtime 依赖
    """
    findings = gate.check_registry_pre_existing()
    errors = [f for f in findings if f.severity == "error"]
    
    assert not errors, f"Registry proposal scope violations found:\n" + "\n".join(
        f"  {f.file}:{f.line} - {f.message}" for f in errors
    )


def test_real_acceptance_state():
    """GOV-MUST-11: 真实验收状态不得被确定性绿证覆盖。
    
    检查 evidence-manifest.json 中的 real_acceptance_claim：
    1. 状态为 NOT-COMPLETE 或 UNVERIFIABLE
    2. 如果声称 COMPLETE，必须有对应的 evidence_ids
    3. 三项真实验收（DeepSeek 十二周、三次隔离盲评、完整自然日 LabelKit）
       当前必须保持 NOT-COMPLETE 或 UNVERIFIABLE
    """
    findings = gate.check_real_acceptance_not_complete()
    errors = [f for f in findings if f.severity == "error"]
    
    assert not errors, f"Real acceptance state violations found:\n" + "\n".join(
        f"  {f.file}:{f.line} - {f.message}" for f in errors
    )


def test_all_governance_checks_integrated():
    """集成测试：运行所有治理检查。
    
    这是总的门禁测试，确保所有 11 项检查都通过。
    """
    findings = gate.run_all_checks()
    errors = [f for f in findings if f.severity == "error"]
    
    if errors:
        error_summary = {}
        for f in errors:
            rule = f.rule
            error_summary[rule] = error_summary.get(rule, 0) + 1
        
        summary = ", ".join(f"{rule}: {count}" for rule, count in sorted(error_summary.items()))
        assert False, (
            f"Documentation governance gate failed with {len(errors)} error(s)\n"
            f"Error breakdown: {summary}\n"
            f"First 3 errors:\n" + "\n".join(
                f"  [{f.rule}] {f.file}:{f.line} - {f.message}" for f in errors[:3]
            )
        )
