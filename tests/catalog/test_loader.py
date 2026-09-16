"""@brief 目录加载器的结构校验正反例测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import CatalogError

from .conftest import (
    default_objects,
    default_resources,
    default_rules,
    default_tombstones,
    write_catalog,
)


def test_loads_default_fixture(tmp_path: Path) -> None:
    """@brief 默认夹具加载出完整的冻结视图（公式来自 rules TOML）。"""
    root = write_catalog(
        tmp_path / "catalog",
        default_resources(),
        default_objects(),
        default_tombstones(),
    )
    catalog = load_catalog(root)
    assert len(catalog.resources) == 3
    assert len(catalog.objects) == 4
    assert set(catalog.formulas) == {"fare"}
    formula = catalog.formulas["fare"]
    assert formula.unit == "fen"
    assert formula.branches["walking"].amount == 0
    assert formula.branches["driving"].amount == "max(500, ceil(m * 0.2))"
    assert formula.params == {"m": "route.distance_meters", "mode": "route.mode"}
    assert catalog.tombstoned_ids() == frozenset({"food-soda"})
    assert (catalog.version_major, catalog.version_minor) == (1, 0)


def test_missing_entry_files_load_empty(tmp_path: Path) -> None:
    """@brief 只有清单的目录加载为空视图（构建器从零起步的形态）。"""
    root = write_catalog(tmp_path / "catalog", [], [], [], rules={})
    catalog = load_catalog(root)
    assert len(catalog.resources) == 0
    assert len(catalog.objects) == 0
    assert len(catalog.formulas) == 0
    assert catalog.version_minor == 0


def test_malformed_json_line_rejected(tmp_path: Path) -> None:
    """@brief JSONL 必须逐行可解析。"""
    root = write_catalog(tmp_path / "catalog", default_resources(), default_objects(), [])
    resource_file = root / "resources.jsonl"
    resource_file.write_text(resource_file.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "CATALOG_MALFORMED_JSON"


def test_schema_invalid_line_rejected(tmp_path: Path) -> None:
    """@brief 缺溯源的资源行过不了模型 Schema。"""
    resources = default_resources()
    del resources[1]["provenance"]
    root = write_catalog(tmp_path / "catalog", resources, default_objects(), [])
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "CATALOG_SCHEMA_INVALID"


def test_version_must_be_strictly_monotonic(tmp_path: Path) -> None:
    """@brief 同一 id 重复落盘同一 version 被拒绝。"""
    resources = default_resources()
    resources.append(dict(resources[1]))
    root = write_catalog(tmp_path / "catalog", resources, default_objects(), [])
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "CATALOG_VERSION_NOT_MONOTONIC"


def test_last_line_wins(tmp_path: Path) -> None:
    """@brief 追加式目录里同 id 的末行是当前版本。"""
    resources = default_resources()
    bumped = json.loads(json.dumps(resources[1]))
    bumped["bounds"]["max"] = 2500
    bumped["version"] = 2
    resources.append(bumped)
    root = write_catalog(tmp_path / "catalog", resources, default_objects(), [])
    catalog = load_catalog(root)
    assert catalog.resources["energy_kcal"].version == 2
    assert catalog.resources["energy_kcal"].bounds is not None
    assert catalog.resources["energy_kcal"].bounds.max == 2500


def test_tombstone_conflict_rejected(tmp_path: Path) -> None:
    """@brief 墓碑之后不允许出现更高 version 的活条目。"""
    objects = default_objects()
    bumped = json.loads(json.dumps(objects[1]))
    bumped["version"] = 2
    objects.append(bumped)
    root = write_catalog(tmp_path / "catalog", default_resources(), objects, default_tombstones())
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "CATALOG_TOMBSTONE_CONFLICT"


def test_manifest_missing_rejected(tmp_path: Path) -> None:
    """@brief 目录版本清单不可缺。"""
    root = write_catalog(tmp_path / "catalog", default_resources(), default_objects(), [])
    (root / "manifest.json").unlink()
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "CATALOG_MANIFEST_MISSING"


def test_malformed_manifest_rejected(tmp_path: Path) -> None:
    """@brief 清单本身必须是合法版本对象。"""
    root = write_catalog(tmp_path / "catalog", default_resources(), default_objects(), [])
    (root / "manifest.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "CATALOG_MALFORMED_MANIFEST"


def test_malformed_formula_toml_rejected(tmp_path: Path) -> None:
    """@brief rules/ 下公式 TOML 语法坏掉时以稳定错误码失败。"""
    rules = default_rules()
    rules["fare.toml"] = "[formula.walking\namount_fen = "
    root = write_catalog(tmp_path / "catalog", default_resources(), default_objects(), [], rules=rules)
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "CATALOG_MALFORMED_TOML"


def test_formula_branch_with_foreign_key_rejected(tmp_path: Path) -> None:
    """@brief 公式分支表内只许 amount_fen 一个键。"""
    rules = default_rules()
    rules["fare.toml"] = '[formula.walking]\namount_fen = 0\nextra = 1\n'
    root = write_catalog(tmp_path / "catalog", default_resources(), default_objects(), [], rules=rules)
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "CATALOG_MALFORMED_TOML"


def test_mapping_rule_toml_skipped_not_parsed(tmp_path: Path) -> None:
    """@brief 无 [formula] 表的映射规则文件不进公式表也不报错。"""
    rules = default_rules()
    rules["off-foods.mapping.toml"] = (
        '[dataset]\nfamily = "open-food-facts"\n\n[energy_edge]\n'
        'on = "consume"\ntype = "source"\nresource_id = "energy_kcal"\n'
    )
    root = write_catalog(tmp_path / "catalog", default_resources(), default_objects(), [], rules=rules)
    catalog = load_catalog(root)
    assert set(catalog.formulas) == {"fare"}


def test_loaded_view_is_frozen(tmp_path: Path) -> None:
    """@brief 加载视图的映射不可变更。"""
    root = write_catalog(tmp_path / "catalog", default_resources(), default_objects(), [])
    catalog = load_catalog(root)
    with pytest.raises(TypeError):
        catalog.resources["energy_kcal"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        catalog.objects["new"] = None  # type: ignore[index]
