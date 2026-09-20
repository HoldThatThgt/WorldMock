"""@brief 目录加载、追加版本与内容快照测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.categories.conftest import raw_object
from worldmock.categories import refresh_manifest_identity
from worldmock.catalog import CatalogError, load_catalog, load_object_declarations


def test_load_catalog_returns_raw_and_compiled_views(catalog_dir: Path) -> None:
    """同一目录同时暴露严格 raw 声明与只读编译视图。"""
    catalog = load_catalog(catalog_dir)
    assert set(catalog.raw_objects) == {"milk"}
    assert set(catalog.objects) == {"milk"}
    assert catalog.objects["milk"].category_id == "food"
    assert catalog.category_contract.contract_version == 1
    assert set(catalog.exchange_rules.rules) == {"buy-product", "eat-food"}


def test_load_object_declarations_retains_append_history(catalog_dir: Path) -> None:
    """公开 raw loader 保留完整追加历史并验证版本。"""
    second = raw_object()
    second["version"] = 2
    path = catalog_dir / "objects.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(second) + "\n")
    history = load_object_declarations(path)
    assert [item.version for item in history] == [1, 2]


def test_load_object_declarations_requires_existing_file(tmp_path: Path) -> None:
    """公开 raw loader 不能把缺失文件解释为空目录。"""
    with pytest.raises(CatalogError) as excinfo:
        load_object_declarations(tmp_path / "missing.jsonl")
    assert excinfo.value.code == "CATALOG_OBJECTS_MISSING"


def test_non_monotonic_object_history_rejected(catalog_dir: Path) -> None:
    """同身份追加版本未递增时拒绝。"""
    path = catalog_dir / "objects.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(raw_object()) + "\n")
    with pytest.raises(CatalogError) as excinfo:
        load_object_declarations(path)
    assert excinfo.value.code == "CATALOG_VERSION_NOT_MONOTONIC"


def test_old_edges_raw_schema_rejected_after_identity_refresh(catalog_dir: Path) -> None:
    """即使摘要合法，旧 raw edges 字段仍由对象 Schema 拒绝。"""
    path = catalog_dir / "objects.jsonl"
    payload = raw_object()
    payload["edges"] = []
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    refresh_manifest_identity(catalog_dir)
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.code == "CATALOG_SCHEMA_INVALID"


def test_manifest_missing_and_malformed_rejected(catalog_dir: Path) -> None:
    """清单缺失或不是严格模型时拒绝加载。"""
    path = catalog_dir / "manifest.json"
    path.unlink()
    with pytest.raises(CatalogError) as missing:
        load_catalog(catalog_dir)
    assert missing.value.code == "CATALOG_MANIFEST_MISSING"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CatalogError) as malformed:
        load_catalog(catalog_dir)
    assert malformed.value.code == "CATALOG_MALFORMED_MANIFEST"


def test_travel_time_bytes_are_captured_at_load(catalog_dir: Path) -> None:
    """运行依赖的出行时间映射以加载时字节快照交给运行层。"""
    path = catalog_dir / "rules/travel-time.toml"
    path.parent.mkdir()
    path.write_text("[formula.walking]\nfactor = 1\n", encoding="utf-8")
    refresh_manifest_identity(catalog_dir)
    catalog = load_catalog(catalog_dir)
    snapshot = catalog.travel_time_rules_bytes
    path.write_text("changed", encoding="utf-8")
    assert snapshot == b"[formula.walking]\nfactor = 1\n"


def test_any_rule_toml_changes_catalog_content_identity(catalog_dir: Path) -> None:
    """运行读取的 rules 下任意 TOML 字节都属于目录内容身份。"""
    path = catalog_dir / "rules/motivation.toml"
    path.parent.mkdir()
    path.write_text("[weights]\nneed = 1\n", encoding="utf-8")
    first = refresh_manifest_identity(catalog_dir)
    path.write_text("[weights]\nneed = 2\n", encoding="utf-8")
    second = refresh_manifest_identity(catalog_dir)
    assert first.catalog_content_sha256 != second.catalog_content_sha256


def test_affordances_changes_catalog_content_identity(catalog_dir: Path) -> None:
    """运行直接读取的根 affordances TOML 字节属于目录内容身份。"""
    path = catalog_dir / "affordances.toml"
    path.write_text("[affordance]\nversion = 1\n", encoding="utf-8")
    first = refresh_manifest_identity(catalog_dir)
    path.write_text("[affordance]\nversion = 2\n", encoding="utf-8")
    second = refresh_manifest_identity(catalog_dir)
    assert first.catalog_content_sha256 != second.catalog_content_sha256


def test_unknown_rule_resource_rejected(catalog_dir: Path) -> None:
    """规则引用未声明资源域时目录拒绝加载。"""
    path = catalog_dir / "exchange-rules.json"
    payload = json.loads(path.read_text())
    payload["rules"][0]["resource_id"] = "unknown"
    path.write_text(json.dumps(payload), encoding="utf-8")
    refresh_manifest_identity(catalog_dir)
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.code == "EXCHANGE_RESOURCE_UNKNOWN"
