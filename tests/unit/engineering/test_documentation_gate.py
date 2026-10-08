"""@brief 当前文档链接检查；历史记录不要求随现行路径和代码变化。"""

from pathlib import Path

import pytest

from scripts.documentation_gate import find_problems, scan_documents, section_identifiers

ROOT = Path(__file__).resolve().parents[3]


def write(root, name, content):
    """@brief 写入一个最小文档。"""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_current_design_manual_and_root_entries_are_checked(tmp_path):
    for name in ("README.md", "AGENTS.md", "docs/INDEX.md", "docs/design/module.md", "docs/manual/usage.md"):
        write(tmp_path, name, "[missing](missing.md)\n")
    write(tmp_path, "docs/dev/feature/spec.md", "[historical](deleted.md)\n")
    assert len(scan_documents(tmp_path)) == 5
    assert len(find_problems(tmp_path)) == 5


def test_current_links_resolve_explicit_and_heading_anchors(tmp_path):
    write(tmp_path, "docs/INDEX.md", "[module](design/module.md#模块规则)\n[explicit](design/module.md#stable)\n")
    write(tmp_path, "docs/design/module.md", '<a name="stable"></a>\n# 模块规则\n')
    assert find_problems(tmp_path) == []


@pytest.mark.parametrize("target", ["module.md#missing", "missing.md", "../../../outside.md"])
def test_missing_targets_and_escaping_paths_fail(tmp_path, target):
    write(tmp_path, "docs/design/module.md", f"# Module\n[broken]({target})\n")
    assert len(find_problems(tmp_path)) == 1


def test_fenced_examples_and_external_links_are_not_local_references(tmp_path):
    write(tmp_path, "docs/INDEX.md",
          "[external](https://example.com/anything)\n~~~markdown\n[example](missing.md)\n~~~\n")
    assert find_problems(tmp_path) == []


def test_repeated_explicit_identifiers_fail_and_heading_suffixes_work(tmp_path):
    write(tmp_path, "docs/INDEX.md", '<a name="same"></a>\n# Topic\n<a name="same"></a>\n# Topic\n')
    assert len(find_problems(tmp_path)) == 1
    assert {"same", "topic", "topic-1"} <= section_identifiers((tmp_path / "docs/INDEX.md").read_text("utf-8"))


def test_real_repository_current_links_resolve():
    assert find_problems(ROOT) == []
