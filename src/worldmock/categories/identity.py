"""@brief 品类契约、规则与完整目录清单的内容身份计算。"""

from __future__ import annotations

from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import canonical_json
from worldmock.corpora.manifest import safe_file
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from worldmock.categories.common import CategoryError
from worldmock.catalog.identity import _update_frame

_CONTENT_FILES = (
    "resources.jsonl",
    "objects.jsonl",
    "overrides/tombstones.jsonl",
    "category-contract.json",
    "exchange-rules.json",
    "affordances.toml",
)
_REQUIRED_FILES = frozenset({
    "resources.jsonl", "objects.jsonl", "category-contract.json", "exchange-rules.json",
})


@dataclass(frozen=True)
class CatalogIdentity:
    """@brief 目录清单必须保存的契约版本与三个 SHA-256 身份。"""

    category_contract_version: int
    category_contract_sha256: str
    exchange_rules_sha256: str
    catalog_content_sha256: str

    def manifest_fields(self) -> dict[str, int | str]:
        """@brief 返回可直接合并进 manifest 的四个身份字段。"""
        return asdict(self)


def compute_catalog_identity(
    catalog_dir: Path | str,
    manifest_payload: dict[str, Any] | None = None,
) -> CatalogIdentity:
    """@brief 按冻结顺序和无歧义分帧算法计算目录身份。

    :param catalog_dir: 待计算目录。
    :param manifest_payload: 可选清单载荷；省略时读取目录清单。
    :returns: 契约版本、两个原始文件摘要与完整内容摘要。
    :raises CategoryError: 必需文件或清单无效。
    """
    directory = Path(catalog_dir)
    payload = _manifest_payload(directory) if manifest_payload is None else dict(manifest_payload)
    contract_bytes = _read_content_file(directory, "category-contract.json")
    rules_bytes = _read_content_file(directory, "exchange-rules.json")
    contract_version = _contract_version(contract_bytes)
    content_digest = _content_digest(directory, payload)
    return CatalogIdentity(
        category_contract_version=contract_version,
        category_contract_sha256=hashlib.sha256(contract_bytes).hexdigest(),
        exchange_rules_sha256=hashlib.sha256(rules_bytes).hexdigest(),
        catalog_content_sha256=content_digest,
    )


def refresh_manifest_identity(catalog_dir: Path | str) -> CatalogIdentity:
    """@brief 原子刷新清单身份字段并保留所有其他声明。

    :param catalog_dir: 含完整候选目录与 manifest 的路径。
    :returns: 实际写入的目录身份。
    :raises CategoryError: 目录不完整、清单无效或原子写入失败。
    """
    directory = Path(catalog_dir)
    manifest_path = directory / "manifest.json"
    payload = _manifest_payload(directory)
    preliminary = compute_catalog_identity(directory, payload)
    payload.update(preliminary.manifest_fields())
    identity = compute_catalog_identity(directory, payload)
    payload.update(identity.manifest_fields())
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        atomic_write(manifest_path, serialized.encode("utf-8"))
    except OSError as error:
        raise CategoryError("CATALOG_IDENTITY_WRITE_FAILED", str(error)) from error
    return identity


def _content_digest(directory: Path, manifest_payload: dict[str, Any]) -> str:
    """@brief 对固定依赖文件及去自引用清单做带路径和长度的分帧摘要。"""
    digest = hashlib.sha256()
    for relative in _CONTENT_FILES:
        content = _read_content_file(directory, relative)
        _update_frame(digest, relative, content)
    for path in sorted((directory / "rules").glob("*.toml")):
        relative = path.relative_to(directory).as_posix()
        _update_frame(digest, relative, _read_content_file(directory, relative))
    payload = dict(manifest_payload)
    payload.pop("catalog_content_sha256", None)
    _update_frame(digest, "manifest.json", canonical_json(payload))
    return digest.hexdigest()


def _read_content_file(directory: Path, relative: str) -> bytes:
    """@brief 读取目录身份文件；两个可选文件缺失时以空字节入摘要。"""
    path = directory / relative
    if not path.exists() and not path.is_symlink():
        if relative in _REQUIRED_FILES:
            raise CategoryError("CATALOG_IDENTITY_FILE_MISSING", f"{path} does not exist")
        return b""
    try:
        validated = safe_file(directory, relative, "Catalog identity file path is invalid",
                              lambda message: CategoryError("CATALOG_IDENTITY_READ_FAILED", f"{path}: {message}"))
        return validated.read_bytes()
    except OSError as error:
        raise CategoryError("CATALOG_IDENTITY_READ_FAILED", f"{path}: {error}") from error




def _manifest_payload(directory: Path) -> dict[str, Any]:
    """@brief 读取 manifest JSON 对象。"""
    path = directory / "manifest.json"
    try:
        file = safe_file(directory, "manifest.json", "Catalog manifest path is invalid",
                         lambda message: CategoryError("CATALOG_MANIFEST_INVALID", f"{path}: {message}"))
        payload = json.loads(file.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CategoryError("CATALOG_MANIFEST_INVALID", f"{path}: {error}") from error
    if not isinstance(payload, dict):
        raise CategoryError("CATALOG_MANIFEST_INVALID", f"{path} root must be an object")
    return payload


def _contract_version(content: bytes) -> int:
    """@brief 从契约原始字节读取正整数版本。"""
    try:
        version = json.loads(content)["contract_version"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise CategoryError("CATEGORY_CONTRACT_VERSION_INVALID", str(error)) from error
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CategoryError("CATEGORY_CONTRACT_VERSION_INVALID", "contract_version must be positive integer")
    return version
