#!/usr/bin/env python3
"""@brief 将当前模块设计确定性地汇编成 HTML 阅读附件，源文档是唯一维护对象。"""

from __future__ import annotations

import argparse
import logging
import html as _html
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_DIR = REPO_ROOT / "docs" / "design"
OUT_HTML = REPO_ROOT / ".work" / "docs" / "design" / "worldmock-design.html"
OUT_PDF = OUT_HTML.with_suffix(".pdf")
#: 章节顺序按系统职责组织，不引用历次特性规格。
CHAPTERS: tuple[str, ...] = (
    "00-overview", "01-architecture", "02-exchange-engine", "03-resource-engine",
    "04-simulation-engine", "05-recording-engine", "06-agent", "07-evaluation",
    "08-configuration", "09-io-formats", "10-runtime",
)
TITLE_PREFIX = "WorldMock 产品设计说明书 — "
DOC_TITLE = "WorldMock 产品设计说明书（汇编版）"
MERMAID_SRC = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

_ANCHOR_RE = re.compile(r'^<a name="([a-z][\w-]*)"></a>\s*$')
_HEADING_RE = re.compile(r"^(#{1,5}) (.+?)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|?\s*$")
_LIST_RE = re.compile(r"^(\s*)(- |\d+\. )(.*)$")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_AUTOLINK_RE = re.compile(r"&lt;(https?://[^&\s]+)&gt;")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_SIBLING_RE = re.compile(r"^(\d{2})-[a-z0-9-]+\.md(?:#(.+))?$")
#: 段落开头 → 样式盒（沿用 LabelKit 设计文档的三类盒子）。
_PARA_CLASSES = (
    (("**做：**", "**不做：**", "**边界**", "**工具级边界"), "boundary"),
    (("**背书**", "**依据**"), "backing"),
    (("**提示**", "**注意**", "**风险**", "**已知限制**", "**关键限制"), "note"),
)


@dataclass
class Chapter:
    """@brief 一个装配单元：章号、文件名、标题、显示标签与正文。"""

    number: int
    stem: str
    title: str
    label: str
    text: str


@dataclass
class Context:
    """@brief 转换上下文：当前章的章号、标签与链接基准，全局已用锚点、锚点映射、锚点队列与目录收集。"""

    chapter: int = 0
    label: str = ""
    used_ids: set[str] = field(default_factory=set)
    anchors: dict[tuple[int, str], str] = field(default_factory=dict)
    queued_ids: dict[int, list[str]] = field(default_factory=dict)
    chapter_ids: dict[int, str] = field(default_factory=dict)
    headings: list[tuple[int, str, str]] = field(default_factory=list)

    def enter(self, chapter: Chapter) -> None:
        """@brief 切换到某一章：章号、显示标签与链接基准随之变化。"""
        self.chapter, self.label = chapter.number, chapter.label


def esc(text: str) -> str:
    """@brief HTML 转义（不转义引号，正文里引号常见）。"""
    return _html.escape(text, quote=False)


def slugify(text: str) -> str:
    """@brief GitHub 口径的标题锚点：小写，去掉标点，空白换成连字符（汉字保留）。"""
    cleaned = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"\s+", "-", cleaned)


def fence_mask(lines: list[str]) -> list[bool]:
    """@brief 逐行标记是否处于代码围栏内（含围栏行本身），供标题预扫描、摘录与目录跳过。"""
    mask: list[bool] = []
    inside = False
    for line in lines:
        if line.strip().startswith("```"):
            inside = not inside
            mask.append(True)
            continue
        mask.append(inside)
    return mask


def parse_chapter(stem: str, text: str, label: str | None = None) -> Chapter:
    """@brief 从当前章节提取标题和显示信息，不再维护章节版本流水账。"""
    lines = text.replace("\r\n", "\n").split("\n")
    number = int(stem[:2])
    title = next((line[2:].strip().removeprefix(TITLE_PREFIX) for line in lines if line.startswith("# ")), stem)
    return Chapter(number, stem, title, label or f"第 {number} 章", "\n".join(lines))












def split_row(line: str) -> list[str]:
    """@brief 表格行按未转义的 | 切分（`\\|` 与代码片段内的 | 是字面量）。"""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    line = _INLINE_CODE_RE.sub(lambda m: "`" + m.group(1).replace("|", "\x01") + "`", line)
    cells = re.split(r"(?<!\\)\|", line)
    return [c.replace("\\|", "|").replace("\x01", "|").strip() for c in cells]


def heading_id(ctx: Context, level: int, text: str) -> str:
    """@brief 标题锚点：章标题为 c{N}，未声明显式标识的生成标题为 c{N}-{slug}，重复时按出现顺序加 -1、-2。"""
    base = f"c{ctx.chapter}" if level == 1 else f"c{ctx.chapter}-{slugify(text)}"
    candidate, suffix = base, 0
    while candidate in ctx.used_ids:
        suffix += 1
        candidate = f"{base}-{suffix}"
    ctx.used_ids.add(candidate)
    return candidate


def prescan_anchors(chapters: list[Chapter]) -> Context:
    """@brief 预扫描显式章节标识与汇编生成标题，正文和目录共用同一标识队列。"""
    ctx = Context()
    for chapter in chapters:
        ctx.enter(chapter)
        queue = ctx.queued_ids.setdefault(chapter.number, [])
        lines = chapter.text.split("\n")
        seen_h1, pending = False, None
        for line, fenced in zip(lines, fence_mask(lines)):
            if not fenced and (declaration := _ANCHOR_RE.fullmatch(line)):
                pending = declaration.group(1)
                continue
            match = None if fenced else _HEADING_RE.match(line)
            if not match:
                continue
            level, text = len(match.group(1)), match.group(2)
            if level == 1 and seen_h1:
                level = 2
            seen_h1 = seen_h1 or level == 1
            hid = pending or heading_id(ctx, level, text)
            if pending:
                if pending in ctx.used_ids:
                    raise SystemExit(f"duplicate section identifier: {pending}")
                ctx.used_ids.add(pending)
                ctx.anchors[(chapter.number, pending)] = hid
            pending = None
            queue.append(hid)
            ctx.chapter_ids.setdefault(chapter.number, hid)
    return ctx


def lookup_anchor(ctx: Context, number: int, anchor: str) -> str | None:
    """@brief 显式标识直接定位当前章节，不通过标题推测别名。"""
    return ctx.anchors.get((number, anchor))


def map_href(href: str, ctx: Context) -> str:
    """@brief 将所属模块内及模块间链接改为阅读附件内的锚点。"""
    if href.startswith("#"):
        target = lookup_anchor(ctx, ctx.chapter, href[1:])
        if target is None:
            raise ValueError(f"Section identifier does not exist: {href}")
        return f"#{target}"
    match = _SIBLING_RE.match(href)
    if not match:
        return href
    number, anchor = int(match.group(1)), match.group(2)
    target = lookup_anchor(ctx, number, anchor) if anchor else ctx.chapter_ids.get(number)
    if target is None:
        raise ValueError(f"Section identifier does not exist: {href}")
    return f"#{target}"



def inline(text: str, ctx: Context) -> str:
    """@brief 行内 Markdown 子集：转义 → 代码片段 → 链接与自动链接 → 加粗。"""
    out: list[str] = []
    for index, segment in enumerate(_INLINE_CODE_RE.split(esc(text))):
        if index % 2 == 1:
            out.append(f"<code>{segment}</code>")
            continue
        segment = _LINK_RE.sub(
            lambda m: f'<a href="{esc(map_href(m.group(2), ctx))}">{m.group(1)}</a>', segment)
        segment = _AUTOLINK_RE.sub(r'<a href="\1">\1</a>', segment)
        out.append(_BOLD_RE.sub(r"<b>\1</b>", segment))
    return "".join(out)


def take_fence(lines: list[str], i: int) -> tuple[str, int]:
    """@brief 代码围栏：mermaid 输出为页内渲染块，其余为带语言类名的 pre/code。"""
    lang = lines[i].strip()[3:].strip()
    body: list[str] = []
    i += 1
    while i < len(lines) and lines[i].strip() != "```":
        body.append(lines[i])
        i += 1
    content = esc("\n".join(body))
    if lang == "mermaid":
        return f'<pre class="mermaid">{content}</pre>', i + 1
    cls = f' class="language-{esc(lang)}"' if lang else ""
    return f"<pre><code{cls}>{content}</code></pre>", i + 1


def take_table(lines: list[str], i: int, ctx: Context) -> tuple[str, int]:
    """@brief 表格：首行表头、次行分隔、其后连续的 | 行为数据行。"""
    header = split_row(lines[i])
    i += 2
    rows: list[list[str]] = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        rows.append(split_row(lines[i]))
        i += 1
    parts = ["<table>", "<tr>" + "".join(f"<th>{inline(c, ctx)}</th>" for c in header) + "</tr>"]
    for row in rows:
        parts.append("<tr>" + "".join(f"<td>{inline(c, ctx)}</td>" for c in row) + "</tr>")
    parts.append("</table>")
    return "".join(parts), i


def take_blockquote(lines: list[str], i: int, ctx: Context) -> tuple[str, int]:
    """@brief 引用块：连续的 > 行，逐行硬换行（章节文件头的状态、日期、版本就是这种写法）。"""
    rows: list[str] = []
    while i < len(lines) and lines[i].startswith(">"):
        raw = lines[i][1:].strip()
        rows.append(inline(raw[:-1].rstrip() if raw.endswith("\\") else raw, ctx))
        i += 1
    return "<blockquote>" + "<br>".join(rows) + "</blockquote>", i


def take_list(lines: list[str], i: int, ctx: Context) -> tuple[str, int]:
    """@brief 列表：无序或有序，支持缩进续接行与一级嵌套的无序子项。"""
    first = _LIST_RE.match(lines[i])
    assert first is not None
    ordered = first.group(2) != "- "
    items: list[tuple[str, list[str]]] = []
    while i < len(lines):
        match = _LIST_RE.match(lines[i])
        if match and not match.group(1) and (match.group(2) != "- ") == ordered:
            items.append((match.group(3), []))
        elif match and match.group(1) and items and match.group(2) == "- ":
            items[-1][1].append(match.group(3))
        elif lines[i].strip() and lines[i].startswith((" ", "\t")) and items:
            items[-1] = (items[-1][0] + " " + lines[i].strip(), items[-1][1])
        else:
            break
        i += 1
    tag = "ol" if ordered else "ul"
    rendered = []
    for text, children in items:
        nested = "<ul>" + "".join(f"<li>{inline(c, ctx)}</li>" for c in children) + "</ul>" if children else ""
        rendered.append(f"<li>{inline(text, ctx)}{nested}</li>")
    return f"<{tag}>" + "".join(rendered) + f"</{tag}>", i


def is_structural(line: str, lines: list[str], i: int) -> bool:
    """@brief 判断一行是否开启新的块（标题、围栏、表格、引用、列表、分隔线或空行）。"""
    stripped = line.strip()
    if _ANCHOR_RE.fullmatch(line):
        return True
    if not stripped or stripped.startswith(("#", "```", ">")) or re.match(r"^---+$", stripped):
        return True
    if stripped.startswith("|") and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
        return True
    return bool(_LIST_RE.match(line) and not _LIST_RE.match(line).group(1))


def take_paragraph(lines: list[str], i: int, ctx: Context) -> tuple[str, int]:
    """@brief 段落：聚合到下一结构行；行尾两个空格或反斜杠是硬换行；特定开头进样式盒。"""
    parts: list[str] = []
    while i < len(lines) and not is_structural(lines[i], lines, i):
        raw = lines[i].rstrip("\n")
        hard = raw.endswith("  ") or raw.endswith("\\")
        text = raw[:-1] if raw.endswith("\\") else raw
        parts.append(inline(text.strip(), ctx) + ("<br>" if hard else "\n"))
        i += 1
    body = "".join(parts).rstrip("\n")
    if body.endswith("<br>"):
        body = body[:-4]
    first = lines[i - len(parts)].strip()
    cls = next((name for heads, name in _PARA_CLASSES if first.startswith(heads)), None)
    return (f'<div class="{cls}">{body}</div>' if cls else f"<p>{body}</p>"), i


def take_heading(line: str, ctx: Context, seen_h1: bool) -> tuple[str, bool]:
    """@brief 标题：章标题加显示标签，同章内多余的 h1 降为 h2；h1、h2 收进目录。"""
    match = _HEADING_RE.match(line)
    assert match is not None
    level, text = len(match.group(1)), match.group(2)
    if level == 1 and seen_h1:
        level = 2
    if level == 1:
        text = f"{ctx.label}　{text.removeprefix(TITLE_PREFIX)}"
    queue = ctx.queued_ids.get(ctx.chapter)
    hid = queue.pop(0) if queue else heading_id(ctx, level, text)
    if level <= 2:
        ctx.headings.append((level, text, hid))
    return f'<h{level} id="{hid}">{inline(text, ctx)}</h{level}>', seen_h1 or level == 1


def convert(text: str, ctx: Context) -> str:
    """@brief Markdown 子集 → HTML；按块类型分派，输出只由输入决定。"""
    lines = text.split("\n")
    out: list[str] = []
    i, seen_h1 = 0, False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or _ANCHOR_RE.fullmatch(line):
            i += 1
        elif stripped.startswith("```"):
            html, i = take_fence(lines, i)
            out.append(html)
        elif _HEADING_RE.match(line):
            html, seen_h1 = take_heading(line, ctx, seen_h1)
            out.append(html)
            i += 1
        elif stripped.startswith("|") and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            html, i = take_table(lines, i, ctx)
            out.append(html)
        elif stripped.startswith(">"):
            html, i = take_blockquote(lines, i, ctx)
            out.append(html)
        elif re.match(r"^---+$", stripped):
            out.append("<hr>")
            i += 1
        elif _LIST_RE.match(line) and not _LIST_RE.match(line).group(1):
            html, i = take_list(lines, i, ctx)
            out.append(html)
        else:
            html, i = take_paragraph(lines, i, ctx)
            out.append(html)
    return "\n".join(out)


def load_chapters(design_dir: Path = DESIGN_DIR) -> list[Chapter]:
    """@brief 按架构顺序读取唯一的当前模块设计。"""
    return [parse_chapter(stem, (design_dir / f"{stem}.md").read_text(encoding="utf-8")) for stem in CHAPTERS]



def build_cover(chapters: list[Chapter], ctx: Context) -> str:
    """@brief 按模块生成阅读入口，不复制版本和实施状态。"""
    rows = "".join(f'<tr><td>{esc(chapter.label)}</td><td>'
                   f'<a href="#{ctx.chapter_ids[chapter.number]}">{esc(chapter.title)}</a></td></tr>'
                   for chapter in chapters)
    return ('<div class="cover"><h1 class="no-break">WorldMock</h1>'
            '<div class="sub">系统设计说明书</div>'
            '<p>本文由 docs/design/ 中的当前模块设计自动汇编；正文只在所属模块维护。</p>'
            '<p>开发过程记录保留在 docs/dev/，不定义当前系统行为。</p>'
            '<p>Mermaid 图在联网打开时渲染。</p></div>'
            '<section><h1>系统模块</h1><table><tr><th>章</th><th>模块</th></tr>'
            + rows + '</table></section>')



def build_toc(headings: list[tuple[int, str, str]]) -> str:
    """@brief 目录：章为一级，二级标题为二级；每项链接到文内锚点。"""
    out = ["<h1>目录</h1>", '<div class="toc"><ul>']
    open_sub = False
    for level, text, hid in headings:
        link = f'<a href="#{hid}">{esc(text)}</a>'
        if level == 1:
            if open_sub:
                out.append("</ul></li>")
            out.append(f'<li class="l1">{link}<ul>')
            open_sub = True
        else:
            out.append(f"<li>{link}</li>")
    if open_sub:
        out.append("</ul></li>")
    out.append("</ul></div>")
    return "\n".join(out)


CSS = """
  @page { size: A4; margin: 18mm 16mm; }
  * { box-sizing: border-box; }
  html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
         font-size: 10.5pt; line-height: 1.7; color: #1a1a1a; margin: 0 auto; max-width: 1080px; padding: 0 24px; }
  h1 { font-size: 17pt; border-bottom: 3px solid #2456a6; padding-bottom: 6px; margin-top: 48px; page-break-before: always; }
  h1.no-break { page-break-before: avoid; }
  h2 { font-size: 13.5pt; color: #2456a6; margin-top: 28px; border-bottom: 1px solid #c9d6ea; padding-bottom: 3px; }
  h3 { font-size: 11.5pt; margin-top: 20px; color: #163a75; }
  h4, h5 { font-size: 10.5pt; margin-top: 16px; color: #333; }
  p { margin: 8px 0; text-align: justify; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9pt; page-break-inside: auto; }
  th, td { border: 1px solid #b9c6d8; padding: 4px 7px; vertical-align: top; text-align: left; }
  th { background: #e8eef7; font-weight: 600; }
  tr { page-break-inside: avoid; }
  code { font-family: "Menlo", "SF Mono", "Consolas", monospace; font-size: 8.5pt; background: #f0f3f8; padding: 1px 4px; border-radius: 3px; }
  pre { font-family: "Menlo", "SF Mono", "Consolas", monospace; font-size: 8.2pt; background: #f6f8fa; border: 1px solid #dbe2ea;
        border-radius: 4px; padding: 10px 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-all;
        line-height: 1.5; page-break-inside: avoid; }
  pre code { background: none; padding: 0; }
  pre.mermaid { background: #fff; border: 1px dashed #c9d6ea; text-align: center; white-space: pre; }
  pre.mermaid svg { max-width: 100%; height: auto; }
  blockquote { border-left: 4px solid #8ea6cf; background: #f4f7fb; margin: 10px 0; padding: 8px 12px; font-size: 9.5pt; color: #333; }
  a { color: #2456a6; }
  .backing { border-left: 4px solid #2e8b57; background: #f0f8f2; padding: 8px 12px; margin: 10px 0; font-size: 9.5pt; }
  .note { border-left: 4px solid #c9861a; background: #fdf6e9; padding: 8px 12px; margin: 10px 0; font-size: 9.5pt; }
  .boundary { border-left: 4px solid #7a4fb5; background: #f6f1fb; padding: 8px 12px; margin: 10px 0; font-size: 9.5pt; }
  .cover { min-height: 250mm; display: flex; flex-direction: column; justify-content: center; text-align: center;
           page-break-after: always; }
  .cover h1 { border: none; font-size: 26pt; page-break-before: avoid; margin-top: 0; }
  .cover .sub { font-size: 14pt; color: #444; margin-top: 8px; }
  .toc { font-size: 10pt; }
  .toc a { text-decoration: none; color: #1a1a1a; }
  .toc ul { list-style: none; padding-left: 18px; line-height: 2; }
  .toc > ul { padding-left: 0; }
  .toc .l1 { font-weight: 600; }
  .kv td:first-child { width: 22%; font-weight: 600; background: #f4f7fb; }
"""

MERMAID_SCRIPT = f"""<script type="module">
  try {{
    const mermaid = (await import("{MERMAID_SRC}")).default;
    mermaid.initialize({{ startOnLoad: false, theme: "neutral", securityLevel: "loose",
                          suppressErrorRendering: true }});
    await mermaid.run({{ querySelector: "pre.mermaid", suppressErrors: true }});
  }} catch (error) {{
    console.warn("Mermaid could not be loaded.", error);
  }}
</script>"""


def assemble(chapters: list[Chapter]) -> str:
    """@brief 汇编完整 HTML：封面、章节一览、目录与当前模块正文。"""
    ctx = prescan_anchors(chapters)
    sections: list[str] = []
    for chapter in chapters:
        ctx.enter(chapter)
        sections.append(f'<section class="chapter" id="chapter-{chapter.number}">'
                        + convert(chapter.text, ctx) + "</section>")
    ctx.enter(chapters[0])
    body = build_cover(chapters, ctx) + build_toc(ctx.headings) + "\n" + "\n".join(sections)
    return (f"<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"UTF-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{esc(DOC_TITLE)}</title>\n<style>{CSS}</style>\n</head>\n"
            f"<!-- 本文件由 scripts/build_design_doc.py 从 docs/design/ 的模块正文汇编生成；"
            f"请修改源后重新构建，勿直接编辑。 -->\n"
            f"<body>\n{body}\n{MERMAID_SCRIPT}\n</body>\n</html>\n")





def export_pdf(out_html: Path = OUT_HTML, out_pdf: Path = OUT_PDF) -> None:
    """@brief 用 Chrome headless 把汇编 HTML 打印为 PDF（可选，需要本机 Chrome）。"""
    if not CHROME.is_file():
        raise SystemExit(f"Chrome not found: {CHROME}")
    command = [str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
               f"--print-to-pdf={out_pdf}", out_html.resolve().as_uri()]
    subprocess.run(command, check=True, timeout=120)


def main(argv: list[str]) -> int:
    """@brief 构建阅读附件；不读取或比较任何已提交的生成文件。"""
    parser = argparse.ArgumentParser(description="Build the current system design reading copy")
    parser.add_argument("--output", type=Path, default=OUT_HTML)
    parser.add_argument("--pdf", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = assemble(load_chapters())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding="utf-8", newline="\n")
        print(f"[design-doc] wrote {args.output} ({len(document):,} chars)")
        if args.pdf:
            export_pdf(args.output, args.output.with_suffix(".pdf"))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        logging.error("Design document build failed: %s", error)
        return 1



if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
