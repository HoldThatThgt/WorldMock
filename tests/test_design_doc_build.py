"""scripts/build_design_doc.py 汇编器的单元测试与汇编文件一致性判定。

@file     test_design_doc_build.py
@brief    用合成 Markdown 验证子集转换（表格转义管道与代码片段管道、列表续接与嵌套、引用块硬换行、
          mermaid 围栏、围栏内的伪标题、章节间链接映射、标题锚点去重），并在真实章节上验证确定性与
          「已提交汇编文件与源一致」（docs/VERIFICATION.md「工程门禁」的「设计说明书汇编与源一致」
          整行的机器证据）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    """@brief 按文件路径加载 scripts/build_design_doc.py（scripts/ 非包，沿用项目先例）。"""
    module_path = REPO_ROOT / "scripts" / "build_design_doc.py"
    spec = importlib.util.spec_from_file_location("build_design_doc", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["build_design_doc"] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def _chapter(stem: str, body: str) -> object:
    """@brief 合成一章：文件头引用块加正文。"""
    text = (f"# WorldMock 产品设计说明书 — 章{stem[:2]}\n\n> **状态**：DRAFT；说明  \n> **日期**：2026-09-29  \n"
            f"> **版本**：v1.0\\\n\n{body}\n\n## 版本历史\n\n| 版本 | 日期 | 修订内容 |\n| --- | --- | --- |\n"
            f"| v1.0 | 2026-09-29 | 初稿 |\n")
    return builder.parse_chapter(stem, text)


def test_table_cells_keep_escaped_and_code_span_pipes():
    """@brief `\\|` 与代码片段内的 | 都是字面量，不切分单元格。"""
    assert builder.split_row("| a \\| b | `x | y` | c |") == ["a | b", "`x | y`", "c"]


def test_list_continuation_and_one_level_nesting():
    """@brief 缩进续接行并入上一项，缩进的无序子项成为嵌套列表；有序列表保持 ol。"""
    ctx = builder.Context()
    html = builder.convert("- 第一项\n  续接\n  - 子项\n- 第二项\n\n1. 甲\n2. 乙\n", ctx)
    assert "<ul><li>第一项 续接<ul><li>子项</li></ul></li><li>第二项</li></ul>" in html
    assert "<ol><li>甲</li><li>乙</li></ol>" in html


def test_blockquote_and_paragraph_hard_breaks():
    """@brief 引用块逐行硬换行并吃掉行尾反斜杠；段落里行尾两个空格或反斜杠是 <br>。"""
    ctx = builder.Context()
    html = builder.convert("> **日期**：2026-09-29  \n> **版本**：v1.4\\\n\n一行  \n二行\\\n三行\n", ctx)
    assert "<blockquote><b>日期</b>：2026-09-29<br><b>版本</b>：v1.4</blockquote>" in html
    assert "<p>一行<br>二行<br>三行</p>" in html


def test_mermaid_fence_and_fenced_pseudo_headings():
    """@brief mermaid 围栏输出为页内渲染块（内容转义）；围栏内的 # 行不是标题，不进目录。"""
    ctx = builder.Context()
    html = builder.convert("```mermaid\nflowchart LR\n  A[x<br/>y] --> B\n```\n\n```markdown\n# 不是标题\n```\n", ctx)
    assert '<pre class="mermaid">flowchart LR\n  A[x&lt;br/&gt;y] --&gt; B</pre>' in html
    assert '<pre><code class="language-markdown"># 不是标题</code></pre>' in html
    assert ctx.headings == []


def test_sibling_links_map_to_in_document_anchors_and_others_stay():
    """@brief 指向兄弟章节的链接改为文内锚点（有锚点时精确到标题，否则到章）；上级目录链接原样保留。"""
    chapters = [_chapter("00-overview", "见[模拟](01-simulation.md#实际区间)与[章](01-simulation.md)，"
                                        "以及[合同](../CONTRACTS.md#字段)。"),
                _chapter("01-simulation", "## 实际区间\n\n正文。")]
    html = builder.assemble(chapters)
    assert 'href="#c1-实际区间">模拟</a>' in html
    assert 'href="#c1">章</a>' in html
    assert 'href="../CONTRACTS.md#字段">合同</a>' in html


def test_same_chapter_anchor_links_get_the_chapter_prefix():
    """@brief 本章内 `#锚点` 链接（GitHub 写法，含编号标题的 `8-判断唤醒条件`）改为带章号的文内锚点；找不到的原样保留。"""
    chapters = [_chapter("04-simulation", "## 8. 判断唤醒条件\n\n见[前文](#8-判断唤醒条件)与[缺失](#没有这个标题)。")]
    html = builder.assemble(chapters)
    assert 'href="#c4-8-判断唤醒条件">前文</a>' in html
    assert 'href="#没有这个标题">缺失</a>' in html


def test_heading_ids_are_unique_and_the_build_is_deterministic():
    """@brief 同章重复标题按出现顺序加后缀；同一输入两次汇编逐字节相同。"""
    chapters = [_chapter("00-overview", "## 规则\n\n甲。\n\n## 规则\n\n乙。")]
    first = builder.assemble(chapters)
    assert 'id="c0-规则"' in first and 'id="c0-规则-1"' in first
    assert first == builder.assemble(chapters)


def test_cover_and_toc_take_chapter_metadata():
    """@brief 封面章节一览取文件头的版本、日期与状态首段；目录含每章与每个二级标题。"""
    chapters = [_chapter("00-overview", "## 甲\n\n正文。\n\n## 乙\n\n正文。")]
    html = builder.assemble(chapters)
    assert "<td>v1.0</td><td>2026-09-29</td><td>DRAFT</td>" in html
    assert "<td>v1.0</td><td>2026-09-29</td><td>初稿</td>" in html
    assert '<a href="#c0">第 0 章　章00</a>' in html
    assert '<a href="#c0-甲">甲</a>' in html and '<a href="#c0-乙">乙</a>' in html


def test_repository_chapters_assemble_deterministically_and_the_committed_html_is_current():
    """@brief 真实十章：两次汇编相同、每章与每个二级标题都进目录；已提交的汇编文件与源一致。"""
    chapters = builder.load_chapters()
    assert [c.number for c in chapters] == list(range(10))
    document = builder.assemble(chapters)
    assert document == builder.assemble(builder.load_chapters())
    for chapter in chapters:
        assert f'<a href="#c{chapter.number}">' in document
        lines = chapter.text.split("\n")
        for line, fenced in zip(lines, builder.fence_mask(lines)):
            if not fenced and line.startswith("## "):
                assert builder.esc(line[3:].strip()) in document
    assert builder.check_current(document) == []
