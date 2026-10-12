"""@brief 记下进程内不写编码的文本读取：与系统语言和 PYTHONUTF8 无关。"""

import io


def default_encoding_reads(monkeypatch) -> list[None]:
    """@brief 把 io.text_encoding 换成记下「编码为 None」调用的包装，返回记录表。

    pathlib.Path.read_text 与 open 不写编码时必经这个函数；记录表为空即这段期间没有一次依赖系统默认编码的文本读取。
    """
    calls, original = [], io.text_encoding

    def recorded(encoding, stacklevel=2):
        """@brief 记下编码为 None 的调用，其余照原函数。"""
        if encoding is None:
            calls.append(encoding)
        return original(encoding, stacklevel + 1)

    monkeypatch.setattr(io, "text_encoding", recorded)
    return calls
