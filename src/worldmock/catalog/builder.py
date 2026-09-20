"""@brief 目录追加构建原语：完整预检后提交 raw 行与摘要清单。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from worldmock.categories import refresh_manifest_identity
from worldmock.catalog.loader import MANIFEST_FILE, OBJECT_FILE, RESOURCE_FILE, TOMBSTONE_FILE, load_catalog
from worldmock.catalog.models import (
    Catalog,
    CatalogEntryKind,
    CatalogError,
    ObjectDeclaration,
    ResourceDefinition,
    Tombstone,
)


def add_resource(catalog_dir: Path | str, resource: ResourceDefinition) -> int:
    """@brief 完整预检后追加一条资源定义并提升目录 minor 版本。

    :param catalog_dir: 已可加载目录。
    :param resource: 新资源或下一追加版本。
    :returns: 实际落盘资源版本。
    """
    directory = Path(catalog_dir).resolve()
    catalog = load_catalog(directory)
    _reject_tombstoned(catalog, CatalogEntryKind.RESOURCE, resource.resource_id)
    current = catalog.resources.get(resource.resource_id)
    _require_incremented_version(resource.resource_id, resource.version, current.version if current else None)
    _stage_and_publish(directory, RESOURCE_FILE, resource.model_dump_json())
    return resource.version


def add_object(catalog_dir: Path | str, obj: ObjectDeclaration) -> int:
    """@brief 完整预检后追加一条 raw 对象增量并提升目录 minor 版本。

    :param catalog_dir: 已可加载目录。
    :param obj: 严格对象增量，禁止已解析 edges 或公式。
    :returns: 实际落盘对象版本。
    """
    directory = Path(catalog_dir).resolve()
    catalog = load_catalog(directory)
    _reject_tombstoned(catalog, CatalogEntryKind.OBJECT, obj.object_id)
    current = catalog.raw_objects.get(obj.object_id)
    _require_incremented_version(obj.object_id, obj.version, current.version if current else None)
    _stage_and_publish(directory, OBJECT_FILE, obj.model_dump_json(by_alias=True))
    return obj.version


def deprecate(
    catalog_dir: Path | str,
    kind: CatalogEntryKind,
    target_id: str,
    reason: str,
) -> Tombstone:
    """@brief 完整预检后为现有资源或对象追加墓碑。"""
    directory = Path(catalog_dir).resolve()
    catalog = load_catalog(directory)
    current_version = _live_version(catalog, kind, target_id)
    if current_version is None:
        raise CatalogError("BUILDER_TARGET_UNKNOWN", f"{kind.value} {target_id} does not exist")
    _reject_tombstoned(catalog, kind, target_id)
    tombstone = Tombstone(
        target_id=target_id, kind=kind, at_version=current_version, reason=reason,
    )
    _stage_and_publish(directory, TOMBSTONE_FILE, tombstone.model_dump_json())
    return tombstone


def _stage_and_publish(directory: Path, relative: str, line: str) -> None:
    """@brief 在同父临时目录完成追加、摘要刷新和真实加载后提交两个文件。"""
    with tempfile.TemporaryDirectory(prefix=".catalog-build-", dir=directory.parent) as temporary:
        staged = Path(temporary) / "catalog"
        shutil.copytree(directory, staged)
        _append_line(staged / relative, line)
        _bump_minor(staged)
        refresh_manifest_identity(staged)
        load_catalog(staged)
        _publish_pair(directory, staged, relative)


def _publish_pair(directory: Path, staged: Path, relative: str) -> None:
    """@brief 提交条目文件与 manifest；异常时恢复两份原始字节。"""
    targets = (directory / relative, directory / MANIFEST_FILE)
    staged_paths = (staged / relative, staged / MANIFEST_FILE)
    originals = {path: path.read_bytes() if path.exists() else None for path in targets}
    try:
        for source, target in zip(staged_paths, targets):
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, source.read_bytes())
    except Exception:
        for target, content in originals.items():
            if content is not None:
                _atomic_write(target, content)
            elif target.exists():
                target.unlink()
        raise


def _atomic_write(path: Path, content: bytes) -> None:
    """@brief 在目标目录内写临时文件并原子替换单个文件。"""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def _append_line(path: Path, line: str) -> None:
    """@brief 向 staged JSONL 文件追加一个序列化条目。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _bump_minor(directory: Path) -> None:
    """@brief 保留完整 manifest 字段，仅把 minor 提升一。"""
    path = directory / MANIFEST_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version_minor"] += 1
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _live_version(catalog: Catalog, kind: CatalogEntryKind, target_id: str) -> int | None:
    """@brief 查资源或 raw 对象当前版本。"""
    namespaces = {
        CatalogEntryKind.RESOURCE: catalog.resources,
        CatalogEntryKind.OBJECT: catalog.raw_objects,
    }
    live = namespaces[kind].get(target_id)
    return live.version if live is not None else None


def _reject_tombstoned(catalog: Catalog, kind: CatalogEntryKind, target_id: str) -> None:
    """@brief 拒绝复活已墓碑身份。"""
    tombstone = catalog.tombstones.get(target_id)
    if tombstone is not None:
        raise CatalogError(
            "BUILDER_ENTRY_TOMBSTONED",
            f"{kind.value} {target_id} tombstoned at version {tombstone.at_version}",
        )


def _require_incremented_version(target_id: str, supplied: int, current: int | None) -> None:
    """@brief 要求追加版本恰好为前一版本加一，新身份必须为一。"""
    expected = current + 1 if current is not None else 1
    if supplied != expected:
        raise CatalogError(
            "BUILDER_VERSION_NOT_INCREMENTED",
            f"{target_id} supplied version {supplied}, expected {expected}",
        )
