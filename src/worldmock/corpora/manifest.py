"""@brief 严格验证语料来源清单和每个本地文件身份。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from worldmock.errors import IntegrityError


class CorpusSource(BaseModel):
    """@brief 一个语料文件的可追溯来源。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str = Field(min_length=1)
    url: str | None = None
    retrieved_at: datetime | None = None
    license: str = Field(min_length=1)
    selector: str = Field(min_length=1)
    fact_cutoff: str | None = None


class CorpusEntry(BaseModel):
    """@brief 规范化目录中的一个不可变普通文件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    entry_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    source: CorpusSource


class CorpusManifest(BaseModel):
    """@brief 正式运行消费的唯一内容版本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
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
    path = base / "manifest.json"
    if not path.is_file() or path.is_symlink():
        raise IntegrityError("Corpus manifest is missing or is a symbolic link")
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
    """@brief 拒绝越界路径、符号链接、大小或哈希漂移。"""
    candidate = root / entry.path
    path = candidate.resolve()
    if _has_symlink(candidate, root) or root not in path.parents or not path.is_file():
        raise IntegrityError("Corpus entry path is invalid")
    content = path.read_bytes()
    if len(content) != entry.size_bytes or hashlib.sha256(content).hexdigest() != entry.sha256:
        raise IntegrityError("Corpus entry identity mismatch")


def _has_symlink(path: Path, root: Path) -> bool:
    """@brief 在解析路径前检查文件及根目录以内的每个父组件。"""
    current = path
    while current != root and root in current.parents:
        if current.is_symlink():
            return True
        current = current.parent
    return current != root
