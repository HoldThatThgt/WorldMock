"""@brief 目录构建原语的只增不改与版本递增测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from worldmock.catalog.builder import add_object, add_resource, deprecate
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import (
    CatalogEntryKind,
    CatalogError,
    ExchangeObject,
    ResourceDefinition,
)

from .conftest import catalog_dir  # noqa: F401


def _extra_resource(version: int) -> ResourceDefinition:
    """@brief 构造一条可落盘的新资源定义。"""
    return ResourceDefinition.model_validate(
        {
            "resource_id": "mood_score",
            "kind": "level",
            "unit": "ratio",
            "owner": "agent",
            "bounds": {"min": 0, "max": 1},
            "conservation": "open",
            "provenance": [
                {"part": "bounds", "paths": ["bounds.min", "bounds.max"],
                 "type": "assumption", "source": "experiment design note"}
            ],
            "version": version,
        }
    )


def _file_bytes(path: Path) -> bytes:
    """@brief 读整个条目文件的字节，供只增不改前缀断言。"""
    return path.read_bytes()


def test_add_resource_appends_and_bumps_minor(catalog_dir: Path) -> None:
    """@brief 新增资源追加一行并把 minor 版本加一。"""
    before = _file_bytes(catalog_dir / "resources.jsonl")
    version = add_resource(catalog_dir, _extra_resource(1))
    after = _file_bytes(catalog_dir / "resources.jsonl")
    assert version == 1
    assert after.startswith(before)
    assert after.count(b"\n") == before.count(b"\n") + 1
    catalog = load_catalog(catalog_dir)
    assert "mood_score" in catalog.resources
    assert (catalog.version_major, catalog.version_minor) == (1, 1)


def test_add_resource_requires_exact_next_version(catalog_dir: Path) -> None:
    """@brief 新条目必须从 1 起，已有条目重复落盘同版本被拒。"""
    before = _file_bytes(catalog_dir / "resources.jsonl")
    with pytest.raises(CatalogError) as skipped:
        add_resource(catalog_dir, _extra_resource(2))
    assert skipped.value.code == "BUILDER_VERSION_NOT_INCREMENTED"
    existing = load_catalog(catalog_dir).resources["energy_kcal"]
    with pytest.raises(CatalogError) as duplicated:
        add_resource(catalog_dir, existing)
    assert duplicated.value.code == "BUILDER_VERSION_NOT_INCREMENTED"
    assert _file_bytes(catalog_dir / "resources.jsonl") == before
    assert load_catalog(catalog_dir).version_minor == 0


def test_add_resource_bumped_entry_keeps_old_lines(catalog_dir: Path) -> None:
    """@brief 对既有资源的升版同样只追加不改旧行。"""
    before = _file_bytes(catalog_dir / "resources.jsonl")
    bumped = _extra_resource(1)
    assert add_resource(catalog_dir, bumped) == 1
    second = ResourceDefinition.model_validate(
        {
            "resource_id": "mood_score",
            "kind": "level",
            "unit": "ratio",
            "owner": "agent",
            "bounds": {"min": 0, "max": 2},
            "conservation": "open",
            "provenance": [
                {"part": "bounds", "paths": ["bounds.min", "bounds.max"],
                 "type": "assumption", "source": "experiment design note"}
            ],
            "version": 2,
        }
    )
    add_resource(catalog_dir, second)
    after = _file_bytes(catalog_dir / "resources.jsonl")
    assert after.startswith(before)
    catalog = load_catalog(catalog_dir)
    assert catalog.resources["mood_score"].version == 2


def test_add_object_appends_and_bumps_minor(catalog_dir: Path) -> None:
    """@brief 新增对象追加一行并把 minor 版本加一。"""
    before = _file_bytes(catalog_dir / "objects.jsonl")
    obj = ExchangeObject.model_validate(
        {
            "object_id": "food-oat",
            "name": "燕麦片",
            "object_kind": "product",
            "edges": [
                {
                    "on": "consume", "type": "source", "resource": "energy_kcal", "amount": 1500,
                }
            ],
            "provenance": [
                {"part": "energy", "paths": ["edges[0].amount"],
                 "type": "anchored", "source": "numbeo:shanghai/oats"}
            ],
            "version": 1,
        }
    )
    assert add_object(catalog_dir, obj) == 1
    after = _file_bytes(catalog_dir / "objects.jsonl")
    assert after.startswith(before)
    assert after.count(b"\n") == before.count(b"\n") + 1
    assert "food-oat" in load_catalog(catalog_dir).objects


def test_add_object_duplicate_version_rejected(catalog_dir: Path) -> None:
    """@brief 重复落盘同一对象版本被拒且文件不动。"""
    before = _file_bytes(catalog_dir / "objects.jsonl")
    existing = load_catalog(catalog_dir).objects["food-milk"]
    with pytest.raises(CatalogError) as excinfo:
        add_object(catalog_dir, existing)
    assert excinfo.value.code == "BUILDER_VERSION_NOT_INCREMENTED"
    assert _file_bytes(catalog_dir / "objects.jsonl") == before


def test_deprecate_writes_tombstone_and_keeps_entry_readable(catalog_dir: Path) -> None:
    """@brief 墓碑落盘后条目保留可读且 minor 版本加一。"""
    objects_before = _file_bytes(catalog_dir / "objects.jsonl")
    tombstone = deprecate(catalog_dir, CatalogEntryKind.OBJECT, "food-milk", "quality recall")
    assert tombstone.at_version == 1
    assert _file_bytes(catalog_dir / "objects.jsonl") == objects_before
    assert (catalog_dir / "overrides/tombstones.jsonl").read_text(encoding="utf-8") != ""
    catalog = load_catalog(catalog_dir)
    assert catalog.tombstoned_ids() == frozenset({"food-soda", "food-milk"})
    assert "food-milk" in catalog.objects
    assert catalog.version_minor == 1


def test_deprecate_unknown_target_rejected(catalog_dir: Path) -> None:
    """@brief 给不存在的条目打墓碑被拒。"""
    with pytest.raises(CatalogError) as excinfo:
        deprecate(catalog_dir, CatalogEntryKind.RESOURCE, "ghost", "typo")
    assert excinfo.value.code == "BUILDER_TARGET_UNKNOWN"


def test_append_to_tombstoned_entry_rejected(catalog_dir: Path) -> None:
    """@brief 墓碑条目禁止复活追加：换新 id 才合法。"""
    deprecate(catalog_dir, CatalogEntryKind.OBJECT, "food-milk", "quality recall")
    resurrected = ExchangeObject.model_validate(
        {
            "object_id": "food-milk",
            "name": "纯牛奶",
            "object_kind": "product",
            "edges": [
                {
                    "on": "consume", "type": "source", "resource": "energy_kcal", "amount": 700,
                }
            ],
            "provenance": [
                {"part": "energy", "paths": ["edges[0].amount"],
                 "type": "anchored", "source": "numbeo:shanghai/milk"}
            ],
            "version": 2,
        }
    )
    with pytest.raises(CatalogError) as excinfo:
        add_object(catalog_dir, resurrected)
    assert excinfo.value.code == "BUILDER_ENTRY_TOMBSTONED"
