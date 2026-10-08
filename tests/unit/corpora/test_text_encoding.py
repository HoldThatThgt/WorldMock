"""@brief 引擎语料消费者的文本读写明确声明编码，不依赖机器区域设置。"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_CORPORA_SOURCE = _REPO / "src/worldmock/corpora"
_TEXT_CALLS = {"read_text", "write_text", "open"}


def _is_binary_open(call: ast.Call) -> bool:
    """@brief open 调用是否以二进制模式打开（位置参数或 mode 关键字中含 b）。"""
    modes = [arg for arg in call.args[1:2] if isinstance(arg, ast.Constant)]
    modes += [keyword.value for keyword in call.keywords
              if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant)]
    return any(isinstance(mode.value, str) and "b" in mode.value for mode in modes)


def _implicit_encoding_calls(path: Path, base: Path = _REPO) -> list[str]:
    """@brief 列出文件中未显式给出 encoding 的文本读写调用（路径相对 base 显示）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in _TEXT_CALLS or any(keyword.arg == "encoding" for keyword in node.keywords):
            continue
        if name == "open" and (_is_binary_open(node) or isinstance(func, ast.Attribute)):
            continue
        found.append(f"{path.relative_to(base).as_posix()}:{node.lineno} {name}")
    return found


def test_corpora_text_io_names_its_encoding() -> None:
    """语料包内每一次文本读写都显式给出编码，不依赖运行机器的区域设置。"""
    offenders = [call for path in sorted(_CORPORA_SOURCE.rglob("*.py")) for call in _implicit_encoding_calls(path)]
    assert offenders == []


def test_implicit_encoding_scan_flags_text_reads_and_skips_binary(tmp_path) -> None:
    """扫描本身能识别缺省编码的文本读写，并放过二进制读写与已给编码的调用。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from pathlib import Path\n"
        "Path('a').read_text()\n"
        "Path('a').write_text('x')\n"
        "open('a')\n"
        "Path('a').read_text(encoding='utf-8')\n"
        "open('a', 'rb')\n"
        "open('a', mode='wb')\n", encoding="utf-8")
    assert _implicit_encoding_calls(sample, tmp_path) == [
        "sample.py:2 read_text",
        "sample.py:3 write_text",
        "sample.py:4 open",
    ]
