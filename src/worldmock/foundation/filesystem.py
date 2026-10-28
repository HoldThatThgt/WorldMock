"""@brief 严格验证语料来源清单和每个本地文件身份。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath


from worldmock.foundation.errors import IntegrityError

#: Windows 保留给设备的文件名（不分大小写，不看扩展名），任何一级都不能作为普通文件。
_WINDOWS_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                                   *(f"{port}{digit}" for port in ("COM", "LPT") for digit in "0123456789¹²³")})
#: Windows 文件名里不能出现的字符：含数据流分隔符冒号与全部控制字符（NUL 在内）。
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*') | {chr(code) for code in range(32)}


def relative_declaration(relative: str) -> bool:
    """@brief 声明是只用 "/" 分隔的非空根内相对路径，每一级都能在任何平台上原样存为普通文件名。

    两种路径语法下都不带盘符或根（Windows 上无盘符的 "/x" 不算绝对路径，同样不是根以内的相对声明），不含父引用与
    反斜杠；一级名字的规则见 _portable_name。
    """
    if not relative or "\\" in relative:
        return False
    declared, windows = PurePosixPath(relative), PureWindowsPath(relative)
    if declared.anchor or windows.anchor or ".." in declared.parts:
        return False
    return bool(declared.parts) and all(map(_portable_name, declared.parts))


def _portable_name(part: str) -> bool:
    """@brief 一级名字不以点或空格结尾（Windows 会去掉它们），不是 Windows 设备名，不含 Windows 禁用字符（含数据流
    冒号）、控制字符或孤立代理码点，UTF-8 不超过 255 字节。"""
    return (not part.endswith((".", " ")) and part.split(".")[0].rstrip(" ").upper() not in _WINDOWS_DEVICE_NAMES
            and not _WINDOWS_FORBIDDEN_CHARACTERS.intersection(part)
            and not any("\ud800" <= character <= "\udfff" for character in part)
            and len(part.encode("utf-8")) <= 255)


def safe_file(root: Path, relative: str, message: str,
              error_factory: Callable[[str], Exception] = IntegrityError) -> Path:
    """@brief 校验根以内的相对普通文件，拒绝不合声明规则的路径与所有符号链接、目录联接组件。

    :param root: 已声明的素材根目录。
    :param relative: 使用相对路径声明的文件。
    :param message: 所属消费者的英文完整性错误。
    :param error_factory: 所属领域的异常构造入口，仅拒绝时调用并记录一次故障。
    :raises IntegrityError: 声明、符号链接、边界或文件类型不合法。
    """
    base = Path(root).resolve()
    declared = Path(relative)
    if not relative_declaration(relative):
        raise error_factory(message)
    # 一次拼接，再自下而上检查根以内的每一级；按声明组件数限步，不靠走到根来停。
    candidate = base / declared
    for current in (candidate, *candidate.parents)[:len(declared.parts)]:
        if current.is_symlink() or current.is_junction():
            raise error_factory(message)
    resolved = candidate.resolve()
    if base not in resolved.parents or not resolved.is_file():
        raise error_factory(message)
    return resolved
