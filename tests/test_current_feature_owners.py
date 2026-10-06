"""@brief 已归档特性的全量机器要求与现行正文所有权守卫。"""

from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

import pytest

from tests.test_layering_acceptance import ARCHIVED_FEATURES, FEATURES

from scripts.documentation_owners import (
    ACCEPTANCE_FIXTURE, OWNER_FIXTURE, find_owner_problems, owner_problem, substantive_body,
)

ROOT = Path(__file__).resolve().parent.parent
OWNERS = json.loads((ROOT / OWNER_FIXTURE).read_text(encoding="utf-8"))


def _write(root: Path, name: str, text: str) -> None:
    """@brief 保存合成机器台账和文档，不改正式仓库。"""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixtures(root: Path, owners: dict, acceptance: dict) -> None:
    """@brief 机器要求身份与正文所有权独立保存，删除所有权不能改掉要求身份。"""
    _write(root, OWNER_FIXTURE, json.dumps(owners))
    _write(root, ACCEPTANCE_FIXTURE, json.dumps(acceptance))


def test_all_accepted_features_have_complete_current_normative_owners():
    """@brief 已归档与当前冻结特性的规范、验收和变异要求均有实际正文所有者。"""
    assert set(OWNERS) == set(FEATURES)
    assert find_owner_problems(ROOT) == []


@pytest.mark.parametrize("body", ["", "\n## Empty child\n", '\n<a name="empty-child"></a>\n## Empty child\n',
                                  "[Contract](CONTRACTS.md#contract)\n", "见 [Contract](CONTRACTS.md#contract)。\n",
                                  "提示与模板的唯一权威文本见 `docs/CONTRACTS.md#contract`。\n"])
def test_titles_anchors_and_navigation_cannot_replace_normative_body(body):
    """@brief 空正文、子标题、标识和单纯导航都不能冒充当前规范。"""
    assert not substantive_body(body)


@pytest.mark.parametrize("feature", ARCHIVED_FEATURES)
def test_removing_each_archived_feature_current_body_fails(tmp_path, feature):
    """@brief 每个已归档特性的现行所有者即使保留文件、标题和标识，删除正文仍失败。"""
    reference = next(iter(OWNERS[feature].values()))
    path, anchor = reference.split("#")
    _write(tmp_path, path, f'<a name="{anchor}"></a>\n## Preserved title\n')
    assert "substantive normative body" in owner_problem(tmp_path, reference)


def test_removing_normative_owner_and_body_cannot_remove_the_requirement(tmp_path):
    """@brief 一并删除所有权登记和现行正文时，独立机器要求身份仍要求该规范完整存在。"""
    _fixtures(tmp_path, {"feature": {"验收：行为": "docs/current.md#current"}},
              {"feature": {"normative_requirements": ["实际行为"],
                           "验收台账": {"tables": [[["Contract", "Evidence"], ["行为", "Real test"]]]}}})
    _write(tmp_path, "docs/current.md", '<a name="current"></a>\n## Preserved title\n')
    problems = find_owner_problems(tmp_path)
    assert any("missing current owner requirement: 规范：实际行为" in problem for problem in problems)
    assert any("substantive normative body" in problem for problem in problems)


def test_new_acceptance_topic_requires_an_owner_and_missing_fixture_fails(tmp_path):
    """@brief 新增验收主题或删除任一机器台账不能静默通过。"""
    assert find_owner_problems(tmp_path)
    _fixtures(tmp_path, {"feature": {}},
              {"feature": {"验收台账": {"tables": [[["Contract", "Evidence"], ["新行为", "Real test"]]]}}})
    assert any("验收：新行为" in problem for problem in find_owner_problems(tmp_path))


def test_title_and_prose_can_change_without_changing_the_requirement_owner(tmp_path):
    """@brief 标题改名或规则改写按同一稳定所有者继续通过，不锁旧散文字面。"""
    reference = "docs/current.md#current"
    for text in ("Requests are applied atomically.", "A rejected request changes no world state."):
        _write(tmp_path, "docs/current.md", f'<a name="current"></a>\n## Changed title\n{text}\n')
        assert owner_problem(tmp_path, reference) is None


def test_archived_non_markdown_assets_preserve_exact_original_bytes():
    """@brief 归档 HTML、Schema 和机器审查证据同样保留固定原文，不因非 Markdown 而遗漏。"""
    records = json.loads((ROOT / "tests/fixtures/archive-assets.json").read_text(encoding="utf-8"))
    actual = {path.relative_to(ROOT).as_posix() for path in (ROOT / "docs/archive").rglob("*")
              if path.is_file() and path.suffix != ".md"}
    assert actual == set(records)
    for current_path, identity in records.items():
        original = subprocess.check_output(["git", "show", identity["commit"] + ":" + identity["original_path"]], cwd=ROOT)
        assert hashlib.sha256(original).hexdigest() == identity["sha256"]
        assert (ROOT / current_path).read_bytes() == original, current_path
