"""@brief 目录新契约关键拒绝分支的补充覆盖。"""

from __future__ import annotations

import json

import pytest

from tests.categories.conftest import raw_object
from worldmock.categories import CategoryError, refresh_manifest_identity
from worldmock.catalog import CatalogError, ObjectDeclaration, add_object, load_catalog
from worldmock.catalog import builder
from worldmock.catalog.admission import UNIT_MISMATCH, validate_scenario_against_catalog

from .conftest import valid_refs


def test_rule_for_undefined_pairing_fails(loaded_catalog) -> None:
    """目录运行规则查询不能把缺项当零作用。"""
    with pytest.raises(CategoryError) as excinfo:
        loaded_catalog.rule_for("person", "food", "consume:use")
    assert excinfo.value.code == "PAIRING_UNDEFINED"


def test_exchange_resource_unit_mismatch_rejected(catalog_dir) -> None:
    """规则资源单位与资源定义不一致时目录加载失败。"""
    path = catalog_dir / "exchange-rules.json"
    payload = json.loads(path.read_text())
    payload["rules"][0]["unit"] = "yuan"
    path.write_text(json.dumps(payload), encoding="utf-8")
    refresh_manifest_identity(catalog_dir)
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.code == "EXCHANGE_RESOURCE_DOMAIN_MISMATCH"


def test_buyer_account_must_bind_closed_money_resource(loaded_catalog) -> None:
    """购买账户若绑定身体资源域会触发守恒准入失败。"""
    refs = valid_refs()
    bad = refs.accounts[0].model_copy(update={"resource_id": "energy_kcal", "unit": "kcal"})
    refs = refs.model_copy(update={"accounts": (bad, refs.accounts[1])})
    result = validate_scenario_against_catalog(refs, loaded_catalog)
    assert "CONSERVATION_DOMAIN_VIOLATION" in {violation.code for violation in result.violations}


def test_builder_schema_failure_keeps_original_catalog_loadable(catalog_dir) -> None:
    """同层冲突增量在 staged 预检失败后不污染原目录。"""
    payload = raw_object("conflict")
    payload["values"]["relative"]["price_fen"] = 1
    with pytest.raises(Exception):
        declaration = ObjectDeclaration.model_validate(payload)
        add_object(catalog_dir, declaration)
    assert set(load_catalog(catalog_dir).objects) == {"milk"}


@pytest.mark.parametrize("manifest_replaced", [False, True], ids=["before-replace", "after-replace"])
def test_builder_manifest_commit_failure_restores_object_and_manifest_bytes(
    catalog_dir, monkeypatch, manifest_replaced,
) -> None:
    """对象已经提交后清单写入失败，两个文件都恢复到原始字节。"""
    object_path, manifest_path = catalog_dir / "objects.jsonl", catalog_dir / "manifest.json"
    before = {path: path.read_bytes() for path in (object_path, manifest_path)}
    original_write = builder._atomic_write
    failed = False

    def fail_manifest_once(path, content):
        nonlocal failed
        if path == manifest_path and not failed:
            assert object_path.read_bytes() != before[object_path]
            assert json.loads(object_path.read_text().splitlines()[-1])["object_id"] == "milk-second"
            if manifest_replaced:
                original_write(path, content)
                assert manifest_path.read_bytes() != before[manifest_path]
            failed = True
            raise OSError("Injected manifest commit failure")
        original_write(path, content)

    monkeypatch.setattr(builder, "_atomic_write", fail_manifest_once)
    with pytest.raises(OSError, match="Injected manifest commit failure"):
        add_object(catalog_dir, ObjectDeclaration.model_validate(raw_object("milk-second")))

    assert failed
    assert {path: path.read_bytes() for path in before} == before
    assert set(load_catalog(catalog_dir).objects) == {"milk"}
    assert not list(catalog_dir.parent.glob(".catalog-build-*"))


def test_object_attribute_unknown_name_has_stable_error(loaded_catalog) -> None:
    """编译对象读取未知属性时不返回隐式空值。"""
    with pytest.raises(CatalogError) as excinfo:
        loaded_catalog.objects["milk"].attribute("missing")
    assert excinfo.value.code == "CATALOG_ATTRIBUTE_MISSING"
