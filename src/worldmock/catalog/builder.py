"""@brief 目录构建原语：追加新行、打墓碑、提升 minor 版本。"""

from __future__ import annotations

import json
from pathlib import Path

from worldmock.catalog.loader import (
    MANIFEST_FILE,
    OBJECT_FILE,
    RESOURCE_FILE,
    TOMBSTONE_FILE,
    load_catalog,
)
from worldmock.catalog.models import (
    Catalog,
    CatalogEntryKind,
    CatalogError,
    CatalogManifest,
    ExchangeObject,
    ResourceDefinition,
    Tombstone,
)


def add_resource(catalog_dir: Path | str, resource: ResourceDefinition) -> int:
    """@brief 追加一条资源定义行并提升目录 minor 版本。

    :param catalog_dir: 目标目录，必须已是可加载的合法目录。
    :param resource: 新资源定义，version 必须恰好为当前版本加一（新条目为 1）。
    :returns: 实际落盘的条目 version。
    :raises CatalogError: version 未按一递增或该 id 已被墓碑时拒绝。
    """
    catalog = load_catalog(catalog_dir)
    _reject_tombstoned(catalog, CatalogEntryKind.RESOURCE, resource.resource_id)
    current = catalog.resources.get(resource.resource_id)
    _require_incremented_version(
        resource.resource_id, resource.version, current.version if current else None
    )
    _append_entry(Path(catalog_dir) / RESOURCE_FILE, resource)
    _bump_minor(Path(catalog_dir), catalog)
    return resource.version


def add_object(catalog_dir: Path | str, obj: ExchangeObject) -> int:
    """@brief 追加一条交换对象行并提升目录 minor 版本。

    :param catalog_dir: 目标目录，必须已是可加载的合法目录。
    :param obj: 新对象条目，version 必须恰好为当前版本加一（新条目为 1）。
    :returns: 实际落盘的条目 version。
    :raises CatalogError: version 未按一递增或该 id 已被墓碑时拒绝。
    """
    catalog = load_catalog(catalog_dir)
    _reject_tombstoned(catalog, CatalogEntryKind.OBJECT, obj.object_id)
    current = catalog.objects.get(obj.object_id)
    _require_incremented_version(obj.object_id, obj.version, current.version if current else None)
    _append_entry(Path(catalog_dir) / OBJECT_FILE, obj)
    _bump_minor(Path(catalog_dir), catalog)
    return obj.version


def deprecate(
    catalog_dir: Path | str,
    kind: CatalogEntryKind,
    target_id: str,
    reason: str,
) -> Tombstone:
    """@brief 为一条存在的条目追加墓碑并提升目录 minor 版本。

    :param catalog_dir: 目标目录，必须已是可加载的合法目录。
    :param kind: 目标条目的命名空间（资源/对象/公式）。
    :param target_id: 目标条目 id。
    :param reason: 弃用原因，必填。
    :returns: 实际落盘的墓碑。
    :raises CatalogError: 目标不存在或已墓碑时拒绝。
    """
    catalog = load_catalog(catalog_dir)
    current_version = _live_version(catalog, kind, target_id)
    if current_version is None:
        raise CatalogError(
            "BUILDER_TARGET_UNKNOWN",
            f"{kind.value} {target_id} does not exist in catalog",
        )
    _reject_tombstoned(catalog, kind, target_id)
    tombstone = Tombstone(
        target_id=target_id,
        kind=kind,
        at_version=current_version,
        reason=reason,
    )
    tombstone_path = Path(catalog_dir) / TOMBSTONE_FILE
    tombstone_path.parent.mkdir(parents=True, exist_ok=True)
    _append_entry(tombstone_path, tombstone)
    _bump_minor(Path(catalog_dir), catalog)
    return tombstone


def _live_version(catalog: Catalog, kind: CatalogEntryKind, target_id: str) -> int | None:
    """@brief 查目标条目的当前活版本，不存在返回 None。"""
    namespaces = {
        CatalogEntryKind.RESOURCE: catalog.resources,
        CatalogEntryKind.OBJECT: catalog.objects,
        CatalogEntryKind.FORMULA: catalog.formulas,
    }
    live = namespaces[kind].get(target_id)
    return live.version if live is not None else None


def _reject_tombstoned(catalog: Catalog, kind: CatalogEntryKind, target_id: str) -> None:
    """@brief 拒绝对已墓碑条目的追加：复活必须换新 id。"""
    tombstone = catalog.tombstones.get(target_id)
    if tombstone is not None:
        raise CatalogError(
            "BUILDER_ENTRY_TOMBSTONED",
            f"{kind.value} {target_id} tombstoned at version {tombstone.at_version};"
            " supersede with a new id instead",
        )


def _require_incremented_version(
    target_id: str,
    supplied: int,
    current: int | None,
) -> None:
    """@brief 要求落盘 version 恰好为当前版本加一（新条目为 1）。"""
    expected = current + 1 if current is not None else 1
    if supplied != expected:
        raise CatalogError(
            "BUILDER_VERSION_NOT_INCREMENTED",
            f"{target_id} supplied version {supplied} but append requires exactly"
            f" the next version {expected}",
        )


def _append_entry(path: Path, entry: ResourceDefinition | ExchangeObject | Tombstone) -> None:
    """@brief 以追加模式写入一行 JSON 序列化条目，绝不改写既有行。"""
    line = entry.model_dump_json(by_alias=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _bump_minor(catalog_dir: Path, catalog: Catalog) -> None:
    """@brief 把派生状态 manifest 的 minor 版本加一并整文件重写。

    manifest 是版本派生状态而非条目行，重写它不违反“条目只增不改”。
    """
    manifest = CatalogManifest(
        version_major=catalog.version_major,
        version_minor=catalog.version_minor + 1,
    )
    payload = json.dumps(
        {"version_major": manifest.version_major, "version_minor": manifest.version_minor},
        indent=2,
    )
    (catalog_dir / MANIFEST_FILE).write_text(payload + "\n", encoding="utf-8")
