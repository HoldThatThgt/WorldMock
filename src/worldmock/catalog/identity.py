"""@brief 目录文件身份的框架化摘要与原子发布。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.models import CatalogFile
from worldmock.corpora.manifest import safe_file
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import canonical_json


#: 必须存在并登记的目录 JSONL。
REQUIRED_CATALOG_FILES = frozenset({
    "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl",
})
#: 可以不存在的目录 JSONL；存在就必须登记，不存在等于零行。
OPTIONAL_CATALOG_FILES = frozenset({"callbacks.jsonl", "map.jsonl"})


@dataclass(frozen=True)
class CatalogIdentity:
    """@brief 发布清单所需的真实文件表和完整目录摘要。"""

    #: 按相对路径排序的逐文件字节身份。
    files: tuple[CatalogFile, ...]
    #: 文件和去自引用清单的框架化摘要。
    catalog_content_sha256: str

    def manifest_fields(self) -> dict[str, Any]:
        """@brief 返回可直接写入严格清单的文件表和摘要字段。"""
        return {"files": [entry.model_dump(mode="json") for entry in self.files],
                "catalog_content_sha256": self.catalog_content_sha256}


def read_catalog_file(directory: Path, relative: str) -> bytes:
    """@brief 通过唯一安全文件边界读取真实字节，拒绝链接和缺失文件。"""
    try:
        path = safe_file(directory, relative, "Catalog file path is invalid",
                         lambda detail: RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH", detail))
        return path.read_bytes()
    except OSError as error:
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH", str(error)) from error


def present_catalog_files(directory: Path) -> tuple[str, ...]:
    """@brief 按路径升序返回必需文件和目录里实际有条目的可选文件，条目类型留给安全文件边界判断。"""
    optional = {name for name in OPTIONAL_CATALOG_FILES if os.path.lexists(directory / name)}
    return tuple(sorted(REQUIRED_CATALOG_FILES | optional))


def read_manifest_payload(directory: Path) -> dict[str, Any]:
    """@brief 读取原始清单对象用于登记与去自引用摘要。"""
    content = read_catalog_file(directory, "manifest.json")
    if b"\r" in content:
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", "Catalog JSON must use LF line endings.")
    try:
        payload = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH", str(error)) from error
    if not isinstance(payload, dict):
        raise RegistrationError("ENVIRONMENT_KIND_INVALID")
    return payload


def compute_catalog_identity(
    catalog_dir: Path | str,
    manifest_payload: dict[str, Any] | None = None,
) -> CatalogIdentity:
    """@brief 对在场的目录 JSONL 和去自引用清单按路径及字节长度分帧求摘要。"""
    directory = Path(catalog_dir)
    payload = read_manifest_payload(directory) if manifest_payload is None else dict(manifest_payload)
    digest = hashlib.sha256()
    entries = []
    for relative in present_catalog_files(directory):
        content = read_catalog_file(directory, relative)
        entries.append(CatalogFile(path=relative, sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content)))
        _update_frame(digest, relative, content)
    payload.pop("catalog_content_sha256", None)
    payload["files"] = [entry.model_dump(mode="json") for entry in entries]
    _update_frame(digest, "manifest.json", canonical_json(payload))
    return CatalogIdentity(tuple(entries), digest.hexdigest())


def refresh_manifest_identity(catalog_dir: Path | str) -> CatalogIdentity:
    """@brief 以原子写发布在场文件的文件表与摘要，登记新放入的可选文件并移出已删除的，失败时保留原清单字节。"""
    directory = Path(catalog_dir)
    payload = read_manifest_payload(directory)
    identity = compute_catalog_identity(directory, payload)
    payload.update(identity.manifest_fields())
    try:
        atomic_write(directory / "manifest.json", canonical_json(payload) + b"\n")
    except OSError as error:
        raise RegistrationError("ENVIRONMENT_IDENTITY_MISMATCH", str(error)) from error
    return identity


def _update_frame(digest: Any, relative: str, content: bytes) -> None:
    """@brief 写入相对路径、NUL、八字节内容长度和内容以消除摘要歧义。"""
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
