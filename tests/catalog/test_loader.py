"""@brief 目录加载、追加版本与内容快照测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.categories.conftest import raw_object
from worldmock.categories import compute_catalog_identity, refresh_manifest_identity
from worldmock.catalog import CategoryError, load_catalog, load_object_declarations


def test_load_catalog_returns_raw_and_compiled_views(catalog_dir: Path) -> None:
    """同一目录同时暴露严格 raw 声明与只读编译视图。"""
    catalog = load_catalog(catalog_dir)
    assert set(catalog.raw_objects) == {"milk"}
    assert set(catalog.objects) == {"milk"}
    assert catalog.objects["milk"].category_id == "food"
    assert catalog.category_contract.contract_version == 1
    assert set(catalog.exchange_rules.rules) == {"buy-product", "eat-food"}


def test_manifest_identity_publication_failure_keeps_previous_bytes(catalog_dir, monkeypatch, caplog):
    """身份刷新发布失败保留旧清单与原异常，临时文件不残留。"""
    from worldmock.footprints import assets

    previous = (catalog_dir / "manifest.json").read_bytes()
    failure = OSError("catalog publication unavailable")

    def fail_replace(source, destination):
        raise failure

    monkeypatch.setattr(assets.os, "replace", fail_replace)
    with pytest.raises(CategoryError) as rejected:
        refresh_manifest_identity(catalog_dir)
    assert rejected.value.code == "CATALOG_IDENTITY_WRITE_FAILED"
    assert rejected.value.__cause__ is failure
    assert (catalog_dir / "manifest.json").read_bytes() == previous
    assert not list(catalog_dir.glob(".pending-*"))
    errors = [record.getMessage() for record in caplog.records
              if record.name == "worldmock.categories.common"]
    assert errors == ["CATALOG_IDENTITY_WRITE_FAILED: catalog publication unavailable"]


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
    with pytest.raises(CategoryError) as excinfo:
        load_object_declarations(tmp_path / "missing.jsonl")
    assert excinfo.value.code == "CATALOG_OBJECTS_MISSING"


def test_non_monotonic_object_history_rejected(catalog_dir: Path) -> None:
    """同身份追加版本未递增时拒绝。"""
    path = catalog_dir / "objects.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(raw_object()) + "\n")
    with pytest.raises(CategoryError) as excinfo:
        load_object_declarations(path)
    assert excinfo.value.code == "CATALOG_VERSION_NOT_MONOTONIC"


def test_old_edges_raw_schema_rejected_after_identity_refresh(catalog_dir: Path) -> None:
    """即使摘要合法，旧 raw edges 字段仍由对象 Schema 拒绝。"""
    path = catalog_dir / "objects.jsonl"
    payload = raw_object()
    payload["edges"] = []
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    refresh_manifest_identity(catalog_dir)
    with pytest.raises(CategoryError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.code == "CATALOG_SCHEMA_INVALID"


def test_manifest_missing_and_malformed_rejected(catalog_dir: Path) -> None:
    """清单缺失或不是严格模型时拒绝加载。"""
    path = catalog_dir / "manifest.json"
    path.unlink()
    with pytest.raises(CategoryError) as missing:
        load_catalog(catalog_dir)
    assert missing.value.code == "CATALOG_MANIFEST_MISSING"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CategoryError) as malformed:
        load_catalog(catalog_dir)
    assert malformed.value.code == "CATALOG_MALFORMED_MANIFEST"


def test_travel_time_bytes_are_captured_at_load(catalog_dir: Path) -> None:
    """运行依赖的出行时间映射以加载时字节快照交给运行层。"""
    path = catalog_dir / "rules/travel-time.toml"
    path.parent.mkdir()
    path.write_bytes(b"[formula.walking]\nfactor = 1\n")
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


def _prepare_identity_dependencies(catalog_dir: Path) -> None:
    """@brief 用真实时长规则和完整素材要求建立当前算法可加载的身份基线。"""
    travel_path = catalog_dir / "rules/travel-time.toml"
    travel_path.parent.mkdir(exist_ok=True)
    travel_path.write_bytes(Path("catalog/rules/travel-time.toml").read_bytes())
    manifest_path = catalog_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["category_requirements"] = json.loads(
        Path("catalog/manifest.json").read_text(encoding="utf-8"))["category_requirements"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    refresh_manifest_identity(catalog_dir)


def _tamper_identity_dependency(catalog_dir: Path, dependency: str) -> None:
    """@brief 只改一种合法依赖内容，保留所有版本与旧清单摘要。"""
    path = catalog_dir / dependency
    if dependency == "objects.jsonl":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["values"]["absolute"]["price_fen"] = 700
    elif dependency == "resources.jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        next(row for row in rows if row["resource_id"] == "energy_kcal")["bounds"]["max"] = 3000
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return
    elif dependency == "overrides/tombstones.jsonl":
        path.parent.mkdir(exist_ok=True)
        payload = {"target_id": "milk", "kind": "object", "at_version": 1, "reason": "retired"}
    elif dependency == "rules/travel-time.toml":
        content = path.read_text(encoding="utf-8")
        assert "walking = 1.0" in content
        path.write_text(content.replace("walking = 1.0", "walking = 1.1", 1), encoding="utf-8")
        return
    else:
        assert dependency == "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["category_requirements"]["declared_at"] = "2099-01-01"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


@pytest.mark.parametrize("dependency", [
    "objects.jsonl", "resources.jsonl", "overrides/tombstones.jsonl", "rules/travel-time.toml", "manifest.json",
])
def test_same_version_dependency_tamper_changes_digest_and_is_rejected_by_loader(
    catalog_dir: Path, dependency: str,
) -> None:
    """每种结算依赖都进入完整摘要，同版本篡改只因内容身份不匹配而拒绝。"""
    _prepare_identity_dependencies(catalog_dir)
    original = load_catalog(catalog_dir)
    before = compute_catalog_identity(catalog_dir)
    manifest_before = json.loads((catalog_dir / "manifest.json").read_text(encoding="utf-8"))

    _tamper_identity_dependency(catalog_dir, dependency)

    after = compute_catalog_identity(catalog_dir)
    manifest_after = json.loads((catalog_dir / "manifest.json").read_text(encoding="utf-8"))
    assert after.catalog_content_sha256 != before.catalog_content_sha256
    assert after.category_contract_version == before.category_contract_version
    assert after.category_contract_sha256 == before.category_contract_sha256
    assert after.exchange_rules_sha256 == before.exchange_rules_sha256
    for field in ("version_major", "version_minor", "category_contract_version", "catalog_content_sha256"):
        assert manifest_after[field] == manifest_before[field]
    assert original.catalog_content_sha256 == before.catalog_content_sha256
    with pytest.raises(CategoryError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.code == "CATALOG_CONTENT_IDENTITY_MISMATCH"
    assert excinfo.value.message == "catalog identity fields mismatch: ['catalog_content_sha256']"


def test_unknown_rule_resource_rejected(catalog_dir: Path) -> None:
    """规则引用未声明资源域时目录拒绝加载。"""
    path = catalog_dir / "exchange-rules.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rules"][0]["resource_id"] = "unknown"
    path.write_text(json.dumps(payload), encoding="utf-8")
    refresh_manifest_identity(catalog_dir)
    with pytest.raises(CategoryError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.code == "EXCHANGE_RESOURCE_UNKNOWN"
