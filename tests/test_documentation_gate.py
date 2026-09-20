"""scripts/documentation_gate.py 链接可解析判定的单元测试。

@file     test_documentation_gate.py
@brief    用合成文档树（tmp_path）验证门禁核心判定逻辑本身，并在真实仓库树上
          执行一次端到端判定（这是 docs/VERIFICATION.md「工程门禁」的「文档链接
          可解析」整行的机器证据）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_gate_module():
    """@brief 按文件路径加载 scripts/documentation_gate.py（scripts/ 非包，沿用项目先例）。"""
    module_path = REPO_ROOT / "scripts" / "documentation_gate.py"
    spec = importlib.util.spec_from_file_location("documentation_gate", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclass 解析字符串注解依赖 sys.modules 中同名模块，须先注册再执行。
    sys.modules["documentation_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate_module()


def _plant(root: Path, relative: str, text: str) -> Path:
    """@brief 在合成仓库里落一份文档，返回其路径。"""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_documents_covers_docs_tree_and_root_documents(tmp_path):
    """@brief 扫描范围是 docs 目录全量加仓库根文档，缺失的根文档静默跳过。"""
    _plant(tmp_path, "docs/INDEX.md", "# 入口\n")
    _plant(tmp_path, "docs/dev/SPEC-x.md", "# 规格\n")
    _plant(tmp_path, "CLAUDE.md", "# 说明\n")
    found = {path.relative_to(tmp_path).as_posix()
             for path in gate.scan_documents(tmp_path)}
    assert found == {"docs/INDEX.md", "docs/dev/SPEC-x.md", "CLAUDE.md"}

    _plant(tmp_path, "README.md", "# 自述\n")
    found = {path.relative_to(tmp_path).as_posix()
             for path in gate.scan_documents(tmp_path)}
    assert found == {"docs/INDEX.md", "docs/dev/SPEC-x.md", "CLAUDE.md", "README.md"}

    empty = tmp_path / "empty"
    empty.mkdir()
    assert gate.scan_documents(empty) == []


def test_extract_references_takes_repo_paths_with_line_numbers(tmp_path):
    """@brief 只抽取 `docs/` 前缀的反引号路径引用，记录来源与行号。"""
    document = _plant(tmp_path, "docs/a.md", "\n".join([
        "第一行无引用。",
        "见 `docs/CONTRACTS.md` 与裸文件名 `CONTRACTS.md`。",
        "见绝对路径 `/tmp/x.md` 与非文档引用 `src/worldmock/life/engine.py`。",
        "见非版本控制产物 `.work/REPORT.md`。",
        "见第二条 `docs/dev/SPEC-x.md`。",
    ]))
    references = gate.extract_references(tmp_path, document)
    assert [(item.line, item.target) for item in references] == [
        (2, "docs/CONTRACTS.md"), (5, "docs/dev/SPEC-x.md")]


def test_find_broken_references_reports_only_missing_targets(tmp_path):
    """@brief 存在的目标放行，缺失的目标逐条报出（来源、行号、目标）。"""
    _plant(tmp_path, "docs/INDEX.md", "见 `docs/CONTRACTS.md`。\n")
    _plant(tmp_path, "docs/CONTRACTS.md", "# 接口\n")
    _plant(tmp_path, "docs/dev/SPEC-x.md", "见 `docs/ARCHITECTURE.md`。\n")
    broken = gate.find_broken_references(tmp_path)
    assert [(item.source, item.line, item.target) for item in broken] == [
        ("docs/dev/SPEC-x.md", 1, "docs/ARCHITECTURE.md")]


def test_retired_paths_are_not_reported_as_broken(tmp_path):
    """@brief 已退役路径（历史性叙述里被提及）不判为失效引用。"""
    retired = sorted(gate.RETIRED_PATHS)
    assert retired, "the retired-path allowlist must not be empty"
    body = "".join(f"当时的规格快照 `{path}`（已退役）。\n" for path in retired)
    _plant(tmp_path, "docs/INDEX.md", body)
    assert gate.find_broken_references(tmp_path) == []
    # 白名单之外的同形引用仍判失效，说明白名单是精确匹配而非前缀放行。
    _plant(tmp_path, "docs/INDEX.md", body + "见 `docs/agentsociety2-design-v2.md`。\n")
    assert [item.target for item in gate.find_broken_references(tmp_path)] == [
        "docs/agentsociety2-design-v2.md"]


def test_main_exit_code_follows_broken_count(tmp_path, monkeypatch, capsys):
    """@brief 退出码：无失效引用为 0，有失效引用为 1，且明细被打印。"""
    _plant(tmp_path, "docs/INDEX.md", "见 `docs/MISSING.md`。\n")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    assert gate.main([]) == 1
    printed = capsys.readouterr().out
    assert "docs/INDEX.md:1" in printed and "docs/MISSING.md" in printed
    assert "FAILED" in printed

    _plant(tmp_path, "docs/MISSING.md", "# 补上\n")
    assert gate.main([]) == 0
    assert "PASSED" in capsys.readouterr().out


def test_real_repository_documentation_links_resolve():
    """@brief 真实仓库树的文档链接全部可解析（本门禁的端到端判据）。"""
    assert gate.scan_documents(REPO_ROOT), "the repository must contain documentation"
    broken = gate.find_broken_references(REPO_ROOT)
    assert broken == [], "\n".join(
        f"{item.source}:{item.line}: {item.target}" for item in broken)
