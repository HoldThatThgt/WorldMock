"""@brief 严格验证语料来源清单和每个本地文件身份。"""

from __future__ import annotations

from worldmock.base_models import FrozenModel
import hashlib
import json
from datetime import datetime
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field, model_validator

from worldmock.errors import IntegrityError

#: Windows 保留给设备的文件名（不分大小写，不看扩展名），任何一级都不能作为普通文件。
_WINDOWS_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                                   *(f"{port}{digit}" for port in ("COM", "LPT") for digit in "0123456789¹²³")})
#: Windows 文件名里不能出现的字符：含数据流分隔符冒号与全部控制字符（NUL 在内）。
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*') | {chr(code) for code in range(32)}


class CorpusSource(FrozenModel):
    """@brief 一个语料文件的可追溯来源。"""

    kind: str = Field(min_length=1)
    url: str | None = None
    retrieved_at: datetime | None = None
    license: str = Field(min_length=1)
    selector: str = Field(min_length=1)
    fact_cutoff: str | None = None


class CorpusEntry(FrozenModel):
    """@brief 规范化目录中的一个不可变普通文件。"""

    entry_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    source: CorpusSource


class CorpusManifest(FrozenModel):
    """@brief 正式运行消费的唯一内容版本。"""

    schema_version: int = Field(ge=1)
    corpus_id: str = Field(min_length=1)
    created_at: datetime
    transformer_version: str = Field(min_length=1)
    entries: tuple[CorpusEntry, ...]

    @model_validator(mode="after")
    def validate_identities(self) -> "CorpusManifest":
        """@brief 拒绝重复身份、重复路径和不带时区的创建时刻。"""
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("corpus created_at must be timezone-aware")
        ids = [entry.entry_id for entry in self.entries]
        paths = [entry.path for entry in self.entries]
        if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
            raise ValueError("corpus entry identities and paths must be unique")
        return self


def load_manifest(root: Path, expected_sha256: str | None = None) -> CorpusManifest:
    """@brief 读取 manifest 并全量复核所有语料文件。"""
    base = Path(root).resolve()
    path = safe_file(base, "manifest.json", "Corpus manifest is missing or is a symbolic link")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise IntegrityError("Corpus manifest hash mismatch")
    try:
        manifest = CorpusManifest.model_validate(json.loads(content))
    except Exception as error:
        raise IntegrityError("Corpus manifest validation failed") from error
    for entry in manifest.entries:
        _validate_entry(base, entry)
    return manifest


def _validate_entry(root: Path, entry: CorpusEntry) -> None:
    """@brief 通过唯一文件边界后拒绝大小或哈希漂移。"""
    path = safe_file(root, entry.path, "Corpus entry path is invalid")
    content = path.read_bytes()
    if len(content) != entry.size_bytes or hashlib.sha256(content).hexdigest() != entry.sha256:
        raise IntegrityError("Corpus entry identity mismatch")





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
