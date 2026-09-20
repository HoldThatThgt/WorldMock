"""@brief 目录构建器完整预检、字段保留与失败回滚测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.categories.conftest import raw_object
from worldmock.catalog import (
    CatalogEntryKind,
    CatalogError,
    ObjectDeclaration,
    ResourceDefinition,
    add_object,
    add_resource,
    deprecate,
    load_catalog,
)


def test_add_object_preserves_manifest_identity_and_contract_fields(catalog_dir: Path) -> None:
    """新增同品类对象只追加 raw 行并完整刷新清单身份。"""
    before = json.loads((catalog_dir / "manifest.json").read_text())
    payload = raw_object("milk-second")
    version = add_object(catalog_dir, ObjectDeclaration.model_validate(payload))
    after = json.loads((catalog_dir / "manifest.json").read_text())
    catalog = load_catalog(catalog_dir)
    assert version == 1
    assert "milk-second" in catalog.objects
    assert after["version_minor"] == before["version_minor"] + 1
    assert after["category_contract_version"] == before["category_contract_version"]
    assert after["category_contract_sha256"] == before["category_contract_sha256"]
    assert after["catalog_content_sha256"] != before["catalog_content_sha256"]


def test_invalid_object_does_not_append_or_bump_manifest(catalog_dir: Path) -> None:
    """候选对象编译失败时对象文件与清单字节保持原值。"""
    object_path = catalog_dir / "objects.jsonl"
    manifest_path = catalog_dir / "manifest.json"
    before = (object_path.read_bytes(), manifest_path.read_bytes())
    payload = raw_object("broken")
    payload["values"]["absolute"].pop("price_fen")
    declaration = ObjectDeclaration.model_validate(payload)
    with pytest.raises(CatalogError):
        add_object(catalog_dir, declaration)
    assert (object_path.read_bytes(), manifest_path.read_bytes()) == before


def test_object_version_must_be_exact_next_version(catalog_dir: Path) -> None:
    """已有身份只能追加恰好下一版本。"""
    payload = raw_object()
    payload["version"] = 3
    with pytest.raises(CatalogError) as excinfo:
        add_object(catalog_dir, ObjectDeclaration.model_validate(payload))
    assert excinfo.value.code == "BUILDER_VERSION_NOT_INCREMENTED"


def test_deprecate_object_is_visible_and_blocks_revival(catalog_dir: Path) -> None:
    """对象墓碑进入摘要目录并阻止同身份复活。"""
    tombstone = deprecate(catalog_dir, CatalogEntryKind.OBJECT, "milk", "retired")
    assert tombstone.at_version == 1
    catalog = load_catalog(catalog_dir)
    assert "milk" in catalog.tombstoned_ids()
    payload = raw_object()
    payload["version"] = 2
    with pytest.raises(CatalogError) as excinfo:
        add_object(catalog_dir, ObjectDeclaration.model_validate(payload))
    assert excinfo.value.code == "BUILDER_ENTRY_TOMBSTONED"


def test_deprecate_unknown_target_rejected(catalog_dir: Path) -> None:
    """不存在的资源或对象不能写墓碑。"""
    with pytest.raises(CatalogError) as excinfo:
        deprecate(catalog_dir, CatalogEntryKind.OBJECT, "missing", "unknown")
    assert excinfo.value.code == "BUILDER_TARGET_UNKNOWN"


def test_add_resource_runs_full_catalog_preflight(catalog_dir: Path) -> None:
    """新增资源通过完整 staged 目录加载后才公开。"""
    resource = ResourceDefinition.model_validate({
        "resource_id": "inventory_closed",
        "kind": "level",
        "unit": "item",
        "owner": "agent_location",
        "bounds": {"min": 0, "max": None},
        "conservation": "closed",
        "provenance": [{"part": "definition", "type": "scenario", "source": "fixture"}],
        "version": 1,
    })
    assert add_resource(catalog_dir, resource) == 1
    assert load_catalog(catalog_dir).resources["inventory_closed"].conservation == "closed"
