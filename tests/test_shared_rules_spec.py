"""最终特性规格的行为登记、冻结来源与设计同步完整性守卫。"""

from __future__ import annotations

import json
from pathlib import Path

from tests.shared_rules_acceptance import ENVIRONMENT, RULES
from tests.test_time_and_energy_completeness import _resolve_problem

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/dev/SPEC-shared-rules-and-rebuildable-env.md"


def test_all_frozen_shared_rule_rows_have_real_behavior_evidence():
    text = SPEC.read_text(encoding="utf-8")
    rules = text.split("## 共享规则", 1)[1].split("## 可重建环境", 1)[0]
    names = {line.split("|")[1].strip() for line in rules.splitlines()
             if line.startswith("| ") and not line.startswith("| ---")}
    names.discard("规则名称")
    assert names == set(RULES)
    for name, nodes in RULES.items():
        assert nodes, name
        for node in nodes:
            assert _resolve_problem(node) is None, (name, node, _resolve_problem(node))


def test_every_environment_clause_has_real_behavior_evidence():
    text = SPEC.read_text(encoding="utf-8").split("## 可重建环境", 1)[1].split("## 文档原子同步清单", 1)[0]
    names = {line.removeprefix("### ") for line in text.splitlines() if line.startswith("### ")}
    assert names == set(ENVIRONMENT)
    for name, nodes in ENVIRONMENT.items():
        for node in nodes:
            assert _resolve_problem(node) is None, (name, node, _resolve_problem(node))


def test_frozen_current_as2_identity_agrees_with_installation_and_runtime():
    from worldmock.runtime import AS2_LOCKED_SOURCE_SHA256
    series = json.loads((ROOT / "patches/agentsociety2/SERIES").read_text(encoding="utf-8"))
    assert series["source_sha256"] == AS2_LOCKED_SOURCE_SHA256
    for name in ("docs/dev/SPEC-shared-rules-and-rebuildable-env.md", "docs/dev/SPEC-as2-integration.md",
                 "docs/EXPERIMENT.md"):
        assert AS2_LOCKED_SOURCE_SHA256 in (ROOT / name).read_text(encoding="utf-8"), name


def test_design_chapters_and_contracts_reference_the_frozen_feature():
    paths = [ROOT / "docs/CONTRACTS.md", ROOT / "docs/ARCHITECTURE.md"]
    paths += sorted((ROOT / "docs/design").glob("0[1-9]-*.md"))
    assert len(paths) == 11
    for path in paths:
        assert "SPEC-shared-rules-and-rebuildable-env.md" in path.read_text(encoding="utf-8"), path
