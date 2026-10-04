"""@brief 文档定义、历史来源、当前稳定引用及失败关闭行为的独立守卫。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import documentation_gate as gate
from scripts.documentation_refs import section_text

REPO_ROOT = Path(__file__).resolve().parent.parent


def _plant(root: Path, relative: str, text: str) -> Path:
    """@brief 在合成仓库保存文档或源码。"""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _commit(root: Path) -> str:
    """@brief 创建真实固定提交，用于验证删除后的历史来源。"""
    for command in (["git", "init", "-q"], ["git", "add", "."],
                    ["git", "-c", "user.name=Document Test", "-c", "user.email=document@example.invalid",
                     "commit", "-qm", "Fixed document snapshot"]):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def test_scan_covers_unregistered_design_manual_and_actual_root_entries(tmp_path):
    """@brief 未列入 INDEX 三列表格的现行设计、手册和根入口同样受检查。"""
    for name in ("docs/INDEX.md", "docs/design/00-overview.md", "docs/manual/config.md", "README.md", "CLAUDE.md"):
        _plant(tmp_path, name, "# Current\n")
    assert {path.relative_to(tmp_path).as_posix() for path in gate.scan_documents(tmp_path)} == {
        "docs/INDEX.md", "docs/design/00-overview.md", "docs/manual/config.md", "README.md", "CLAUDE.md"}
    assert gate.scan_documents(tmp_path / "missing") == []


def test_title_change_preserves_explicit_identity_and_body(tmp_path):
    """@brief 标题改名后链接仍按显式标识解析，围栏里的伪标题不截断正文。"""
    text = '<a name="current-rule"></a>\n## New wording\n\n正文。\n```text\n## 伪标题\n```\n\n'
    text += '<a name="next-rule"></a>\n## Next\n\n别处。\n'
    _plant(tmp_path, "docs/rule.md", text)
    _plant(tmp_path, "docs/INDEX.md", "[Rule](rule.md#current-rule)\n")
    assert gate.find_problems(tmp_path) == []
    assert section_text(text, "current-rule") == "\n正文。\n```text\n## 伪标题\n```"


@pytest.mark.parametrize("body,expected", [
    ("[Lost](missing.md)\n", "target does not exist"),
    ("[Title](#wording)\n<a name=\"current-rule\"></a>\n## Wording\n", "identifier does not exist"),
    ('<a name="same"></a>\n## One\n<a name="same"></a>\n## Two\n', "duplicate section identifier"),
    ('<a name="orphan"></a>\n正文。\n', "has no heading"),
    ('<a name="invalid identifier"></a>\n## Rule\n', "invalid explicit"),
    ("`src/worldmock/x.py:12`\n", "source line reference"),
    ("[Line](../src/worldmock/x.py#L1)\n", "source line reference"),
    ("[Range](../src/worldmock/x.py#L1-L8)\n", "source line reference"),
    ("`src/worldmock/x.py line 12`\n", "source line reference"),
    ("`src/worldmock/x.py 第 12 行`\n", "source line reference"),
    ("`src/worldmock/x.py（12）`\n", "source line reference"),
    ("`src/worldmock/x.py::Forwarded`\n", "source definition does not exist"),
])
def test_current_document_violations_fail(tmp_path, body, expected):
    """@brief 当前缺文件、标题推测、重复标识、无主标识、行号及再导出均失败。"""
    _plant(tmp_path, "docs/design/00-overview.md", "> 状态：DRAFT\n" + body)
    _plant(tmp_path, "src/worldmock/x.py", "from elsewhere import Forwarded\n")
    assert any(expected in problem.message for problem in gate.find_problems(tmp_path))


def test_definitions_resolve_classes_nested_functions_and_assignments(tmp_path):
    """@brief 按词法限定名检查实际定义，源码重排不影响引用。"""
    _plant(tmp_path, "src/worldmock/x.py", "VALUE = 1\nclass Box:\n def run(self):\n  def inner():\n   return 1\n  return inner()\n")
    _plant(tmp_path, "docs/rule.md", "`src/worldmock/x.py::VALUE` `src/worldmock/x.py::Box.run.inner`\n")
    assert gate.find_problems(tmp_path) == []


@pytest.mark.parametrize("source", [
    "from elsewhere import Foo\nAlias = Foo\n",
    "import elsewhere\nAlias = elsewhere.Foo\n",
    "from elsewhere import Foo\nForwarded = Foo\nAlias = Forwarded\n",
    "from elsewhere import Foo\nAlias, = (Foo,)\n",
    "from elsewhere import Foo\nAlias = (Foo,)[0]\n",
    "from elsewhere import Foo\nAlias = [Foo][0]\n",
    "from elsewhere import Foo\nAlias = {'x': Foo}['x']\n",
    "from elsewhere import Foo\nContainer = [Foo]\nAlias = Container[0]\n",
    "from elsewhere import Foo\nAlias = Foo if flag else Foo\n",
    "from elsewhere import Foo\nAlias = Foo or Foo\n",
    "from elsewhere import Foo\nAlias = {'x': Foo}.get('x')\n",
    "from elsewhere import Foo\nAlias = False or Foo\n",
    "from elsewhere import Foo\nAlias = True and Foo\n",
    "from elsewhere import Foo\nAlias = {**{'x': Foo}}['x']\n",
    "from elsewhere import Foo\nAlias = 1\nAlias = Foo\n",
    "import elsewhere\nAlias = getattr(elsewhere, 'Foo')\n",
    "import elsewhere\nAlias = vars(elsewhere)['Foo']\n",
    "from elsewhere import Foo\nAlias = (lambda: Foo)()\n",
    "from elsewhere import Foo\nfrom typing import cast\nAlias = cast(object, Foo)\n",
    "from elsewhere import Foo\nimport typing as t\nAlias = t.cast(object, Foo)\n",
    "from elsewhere import Foo\nfrom typing import cast as typed\nAlias = typed(object, Foo)\n",
    "import elsewhere\nimport builtins as b\nAlias = b.getattr(elsewhere, 'Foo')\n",
])
def test_assignment_reexports_are_not_actual_definitions(tmp_path, source):
    """@brief 导入对象的直接、模块属性和链式赋值再导出均不能冒充定义。"""
    _plant(tmp_path, "src/worldmock/x.py", source)
    _plant(tmp_path, "docs/rule.md", "`src/worldmock/x.py::Alias`\n")
    assert any("source definition does not exist" in problem.message for problem in gate.find_problems(tmp_path))


def test_constructed_assignment_and_local_parameter_are_actual_definitions(tmp_path):
    """@brief 构造对象、字面值与函数参数产生的本地赋值仍有真实所属定义。"""
    source = "from elsewhere import Foo\nVALUE = 1\nOBJECT = Foo()\ndef make(Foo):\n Alias = Foo\n"
    _plant(tmp_path, "src/worldmock/x.py", source)
    _plant(tmp_path, "docs/rule.md", "`src/worldmock/x.py::VALUE` `src/worldmock/x.py::OBJECT` "
           "`src/worldmock/x.py::make.Alias`\n")
    assert gate.find_problems(tmp_path) == []


def test_transparent_calls_preserve_real_constructions_and_custom_factories(tmp_path):
    """@brief 导入类的真实构造、透明函数中的构造与自定义同名工厂仍有自己的本地定义。"""
    source = ("from elsewhere import Foo\nfrom typing import cast\n"
              "OBJECT = Foo()\nCAST_OBJECT = cast(object, Foo())\nLAMBDA_OBJECT = (lambda: Foo())()\n"
              "def factory(value):\n return {'value': value}\nFACTORY_OBJECT = factory(Foo)\n"
              "def getattr(owner, name):\n return {'owner': owner, 'name': name}\n"
              "CUSTOM_OBJECT = getattr(Foo, 'field')\n"
              "def build(cast):\n LOCAL_OBJECT = cast(Foo)\n")
    _plant(tmp_path, "src/worldmock/x.py", source)
    names = ("OBJECT", "CAST_OBJECT", "LAMBDA_OBJECT", "FACTORY_OBJECT", "CUSTOM_OBJECT", "build.LOCAL_OBJECT")
    _plant(tmp_path, "docs/rule.md", " ".join(f"`src/worldmock/x.py::{name}`" for name in names))
    assert gate.find_problems(tmp_path) == []


def test_literal_container_and_mixed_destructuring_keep_actual_local_definitions(tmp_path):
    """@brief 本地构造容器与解构中的字面常量仍有效，导入成员的单独再导出失败。"""
    source = "from elsewhere import Foo\nContainer = [Foo]\nAlias, VALUE = (Foo, 1)\n"
    _plant(tmp_path, "src/worldmock/x.py", source)
    _plant(tmp_path, "docs/rule.md", "`src/worldmock/x.py::Container` `src/worldmock/x.py::VALUE`\n")
    assert gate.find_problems(tmp_path) == []
    _plant(tmp_path, "docs/rule.md", "`src/worldmock/x.py::Alias`\n")
    assert gate.find_problems(tmp_path)


def test_duplicate_identifier_across_documents_has_one_owner(tmp_path):
    """@brief 全仓标识只能属于一份文档，不能因不同文件而逃过重复检查。"""
    for relative in ("docs/one.md", "docs/two.md"):
        _plant(tmp_path, relative, '<a name="shared-rule"></a>\n## Rule\n')
    assert any("another document" in problem.message for problem in gate.find_problems(tmp_path))


@pytest.mark.parametrize("reference", ["docs/rule.md", "docs/rule.md#rule", "docs/archive/feature/old.md#old-rule"])
def test_source_and_test_references_require_current_explicit_sections(tmp_path, reference):
    """@brief 注释和文档串中的规格引用不能省略标识、用旧标题或依赖历史权威。"""
    _plant(tmp_path, "docs/rule.md", '<a name="current-rule"></a>\n## Rule\n')
    _plant(tmp_path, "docs/archive/feature/old.md", '<a name="old-rule"></a>\n## Old\n')
    _plant(tmp_path, "tests/test_x.py", f'"""{reference}"""\n')
    assert gate.code_problems(tmp_path, gate.historical_documents(tmp_path))


@pytest.mark.parametrize("body", [
    'VALUE = 1\n"""docs/rule.md#missing-rule"""\n',
    'from pydantic import Field\nVALUE = Field(description="docs/rule.md#missing-rule")\n',
])
def test_attribute_documentation_and_field_description_use_current_sections(tmp_path, body):
    """@brief 赋值后的属性说明与字段描述同样属于当前代码合同引用，不能漏过稳定标识检查。"""
    _plant(tmp_path, "docs/rule.md", '<a name="current-rule"></a>\n## Rule\n')
    _plant(tmp_path, "src/worldmock/x.py", body)
    assert any("identifier does not exist" in item.message for item in gate.code_problems(tmp_path, set()))


def test_document_file_reading_argument_is_not_a_contract_reference(tmp_path):
    """@brief 文件读取参数与合成夹具字符串不是代码说明，不要求为文件路径追加章节。"""
    _plant(tmp_path, "tests/test_x.py", 'from pathlib import Path\nTEXT = Path("docs/rule.md").read_text()\n')
    assert gate.code_problems(tmp_path, set()) == []


def test_retired_paths_require_real_declared_history(tmp_path):
    """@brief 无白名单放行；历史已删文件必须在声明提交中存在。"""
    _plant(tmp_path, "docs/old.md", "## Historical section\n")
    _plant(tmp_path, "src/worldmock/x.py", "def work():\n return 1\n")
    commit = _commit(tmp_path)
    (tmp_path / "docs/old.md").unlink()
    (tmp_path / "src/worldmock/x.py").unlink()
    history = (f"> 代码基线：{commit}\n\n[Old](../../old.md#historical-section)\n`src/worldmock/x.py:2`\n"
               "[Line](../../../src/worldmock/x.py#L1-L2)\n")
    _plant(tmp_path, "docs/archive/feature/record.md", history)
    assert gate.find_problems(tmp_path) == []
    _plant(tmp_path, "docs/current.md", "`docs/old.md`\n")
    assert any("target does not exist" in problem.message for problem in gate.find_problems(tmp_path))
    _plant(tmp_path, "docs/archive/feature/record.md", history + "`src/worldmock/missing.py:1`\n")
    assert any("historical source does not exist" in problem.message for problem in gate.find_problems(tmp_path))


@pytest.mark.parametrize("header", ["> 代码基线：abc1234", "> 代码基线：" + "0" * 40,
                                      "> 代码基线：" + "a" * 40 + " dirty", "> 归档基线：{commit}"])
def test_history_refuses_short_missing_dirty_or_undisclaimed_commit(tmp_path, header):
    """@brief 历史基线必须完整存在，快照不能冒充执行证据。"""
    _plant(tmp_path, "docs/INDEX.md", "# Index\n")
    commit = _commit(tmp_path)
    _plant(tmp_path, "docs/dev/research/record.md", header.replace("{commit}", commit) + "\n")
    assert gate.find_problems(tmp_path)


def test_archive_snapshot_disclaims_unreconstructible_execution(tmp_path):
    """@brief 原始未提交工作树事实可保存，归档固定提交仅负责定位原文快照。"""
    _plant(tmp_path, "docs/INDEX.md", "# Index\n")
    _plant(tmp_path, "docs/record.md", "# Original record\nOriginal execution cannot be reconstructed.\n")
    commit = _commit(tmp_path)
    _plant(tmp_path, "docs/archive/feature/record.md",
           f"> 归档基线：{commit}\n> 原始路径：`docs/record.md`\n"
           "> 原始执行依赖未提交工作树；不是审查执行证明。\n\n"
           "# Original record\nOriginal execution cannot be reconstructed.\n")
    assert gate.find_problems(tmp_path) == []


def test_archive_header_without_original_body_cannot_claim_complete_snapshot(tmp_path):
    """@brief 原始路径和完整提交只负责定位，两行声明不构成完整归档正文。"""
    _plant(tmp_path, "docs/record.md", "# Original record\nThe original evidence remains explicit.\n")
    commit = _commit(tmp_path)
    _plant(tmp_path, "docs/archive/feature/record.md",
           f"> 归档基线：{commit}\n> 原始路径：`docs/record.md`\n> 不是审查执行证明。\n")
    assert any("requires substantive original" in item.message for item in gate.find_problems(tmp_path))


def test_archive_cannot_replace_original_snapshot_with_unrelated_substantive_body(tmp_path):
    """@brief 有提交、原始路径与免责声明仍不足以证明快照；正文必须等于该提交原文。"""
    _plant(tmp_path, "docs/original.md", "# Original\nThe original requirement remains explicit.\n")
    commit = _commit(tmp_path)
    (tmp_path / "docs/original.md").unlink()
    _plant(tmp_path, "docs/archive/feature/record.md",
           f"> 归档基线：{commit}\n> 原始路径：`docs/original.md`\n> 不是审查执行证明。\n\n"
           "# Original\nUnrelated replacement.\n")
    assert any("differs from the declared original" in item.message for item in gate.find_problems(tmp_path))


def test_archive_relative_links_resolve_from_original_path_without_rewriting_body(tmp_path):
    """@brief 搬入归档后的相对链接按原始路径解析，正文与原提交保持逐字相同。"""
    _plant(tmp_path, "docs/dev/rule.md", "# Rule\nThe requirement is explicit.\n")
    original = "# Original\nSee [Rule](../rule.md).\n"
    _plant(tmp_path, "docs/dev/research/record.md", original)
    commit = _commit(tmp_path)
    (tmp_path / "docs/dev/research/record.md").unlink()
    _plant(tmp_path, "docs/archive/feature/record.md",
           f"> 归档基线：{commit}\n> 原始路径：`docs/dev/research/record.md`\n> 不是审查执行证明。\n\n" + original)
    assert gate.find_problems(tmp_path) == []


def test_exact_reference_source_is_explicit_and_does_not_search_other_baselines(tmp_path):
    """@brief 后补引用逐路径声明真实出处；没有声明时只查执行基线，不能寻找其他提交放行。"""
    original = "# Original\n`tests/test_added.py`\n"
    _plant(tmp_path, "docs/record.md", original)
    code_commit = _commit(tmp_path)
    _plant(tmp_path, "tests/test_added.py", "def test_added():\n assert True\n")
    archive_commit = _commit(tmp_path)
    header = (f"> 代码基线：{code_commit}\n> 归档基线：{archive_commit}\n"
              "> 原始路径：`docs/record.md`\n> 不是审查执行证明。\n")
    _plant(tmp_path, "docs/archive/feature/record.md", header + "\n" + original)
    assert any("historical source does not exist" in item.message for item in gate.find_problems(tmp_path))
    exact_source = f"> 引用出处：`tests/test_added.py` @ `{archive_commit}`\n"
    _plant(tmp_path, "docs/archive/feature/record.md", header + exact_source + "\n" + original)
    assert gate.find_problems(tmp_path) == []


def test_main_fails_closed_and_reports_english_problem(tmp_path, monkeypatch, capsys):
    """@brief 门禁失败返回非零并输出定位；完整引用恢复后返回零。"""
    _plant(tmp_path, "docs/INDEX.md", "[Missing](missing.md)\n")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    assert gate.main([]) == 1
    result = capsys.readouterr()
    assert "target does not exist" in result.err and "FAILED" in result.out
    _plant(tmp_path, "docs/missing.md", "# Current\n")
    assert gate.main([]) == 0
    assert "PASSED" in capsys.readouterr().out


def test_real_repository_documents_and_source_references_resolve():
    """@brief 正式仓库全文档和生产、测试引用共同满足完整门禁。"""
    problems = gate.find_problems(REPO_ROOT)
    assert not problems, "\n".join(f"{item.source}:{item.line}: {item.message}" for item in problems)
