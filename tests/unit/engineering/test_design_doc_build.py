"""@brief 验证 Markdown 转换、章节链接、锚点与设计阅读附件的确定性构建。"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import re

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


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
    """@brief 合成带显式锚点的模块正文。"""
    text = f"# WorldMock 产品设计说明书 — 章{stem[:2]}\n\n{body}\n"
    lines, seen, fenced = [], {}, False
    for line in text.splitlines(keepends=True):
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and (match := re.match(r"^(#{1,5}) (.+)", line)):
            base = f"c{int(stem[:2])}" if len(match[1]) == 1 else f"c{int(stem[:2])}-{builder.slugify(match[2])}"
            suffix = seen.get(base, 0)
            seen[base] = suffix + 1
            lines.append(f'<a name="{base + (f"-{suffix}" if suffix else "")}"></a>\n')
        lines.append(line)
    return builder.parse_chapter(stem, "".join(lines))


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
    chapters = [_chapter("00-overview", "见[模拟](01-simulation.md#c1-实际区间)与[章](01-simulation.md)，"
                                        "以及[手册](../manual/runtime.md#运行)。"),
                _chapter("01-simulation", "## 实际区间\n\n正文。")]
    html = builder.assemble(chapters)
    assert 'href="#c1-实际区间">模拟</a>' in html
    assert 'href="#c1">章</a>' in html
    assert 'href="../manual/runtime.md#运行">手册</a>' in html


def test_same_chapter_anchor_links_get_the_chapter_prefix():
    """@brief 本章内显式锚点链接映射到已登记的章节标题。"""
    chapters = [_chapter("04-simulation", "## 8. 判断唤醒条件\n\n见[前文](#c4-8-判断唤醒条件)与[再次](#c4-8-判断唤醒条件)。")]
    html = builder.assemble(chapters)
    assert 'href="#c4-8-判断唤醒条件">前文</a>' in html
    assert 'href="#c4-8-判断唤醒条件">再次</a>' in html


def test_missing_sibling_identifier_aborts_instead_of_opening_the_chapter():
    """@brief 章节路径存在但稳定标识缺失时终止汇编，不能改链到整章掩盖断链。"""
    chapters = [_chapter("00-overview", "见[缺失](01-simulation.md#missing-section)。"),
                _chapter("01-simulation", "## 实际区间\n\n正文。")]
    with pytest.raises(ValueError, match="Section identifier does not exist"):
        builder.assemble(chapters)


def test_heading_ids_are_unique_and_the_build_is_deterministic():
    """@brief 同章重复标题按出现顺序加后缀；同一输入两次汇编逐字节相同。"""
    chapters = [_chapter("00-overview", "## 规则\n\n甲。\n\n## 规则\n\n乙。")]
    first = builder.assemble(chapters)
    assert 'id="c0-规则"' in first and 'id="c0-规则-1"' in first
    assert first == builder.assemble(chapters)












def test_explicit_section_survives_heading_rename_without_visible_markup():
    """@brief 相同显式标识随正文标题改名保留，生成物不展示标记标签。"""
    first = builder.parse_chapter("00-overview", '<a name="durable-rule"></a>\n# Original\n')
    renamed = builder.parse_chapter("00-overview", '<a name="durable-rule"></a>\n# Renamed\n')
    for chapter in (first, renamed):
        html = builder.assemble([chapter])
        assert 'id="durable-rule"' in html and '&lt;a name=' not in html



def test_all_current_modules_are_assembled_deterministically():
    chapters = builder.load_chapters()
    document = builder.assemble(chapters)
    assert document == builder.assemble(chapters)
    assert len(chapters) == len(builder.CHAPTERS)
    for chapter in chapters:
        assert f'id="chapter-{chapter.number}"' in document
        assert builder.esc(chapter.title) in document


def test_build_writes_only_the_requested_reading_artifact(tmp_path):
    output = tmp_path / "attachments" / "worldmock-design.html"
    assert builder.main(["--output", str(output)]) == 0
    assert output.read_text("utf-8") == builder.assemble(builder.load_chapters())
    assert builder.OUT_HTML.is_relative_to(REPO_ROOT / ".work")
