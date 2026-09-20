"""@brief 目录新契约关键拒绝分支的补充覆盖。"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests.categories.conftest import raw_object
from worldmock.categories import CategoryError, refresh_manifest_identity
from worldmock.catalog import CatalogError, ObjectDeclaration, add_object, load_catalog
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


def test_transfer_role_must_bind_declared_resource(loaded_catalog) -> None:
    """购买边角色若绑定其他资源域会触发单位准入失败。"""
    refs = valid_refs()
    bad = refs.accounts[0].model_copy(update={"resource_id": "energy_kcal", "unit": "kcal"})
    refs = refs.model_copy(update={"accounts": (bad, refs.accounts[1])})
    result = validate_scenario_against_catalog(refs, loaded_catalog)
    assert UNIT_MISMATCH in {violation.code for violation in result.violations}


def test_builder_schema_failure_keeps_original_catalog_loadable(catalog_dir) -> None:
    """同层冲突增量在 staged 预检失败后不污染原目录。"""
    payload = raw_object("conflict")
    payload["values"]["relative"]["price_fen"] = 1
    with pytest.raises(Exception):
        declaration = ObjectDeclaration.model_validate(payload)
        add_object(catalog_dir, declaration)
    assert set(load_catalog(catalog_dir).objects) == {"milk"}


def test_object_attribute_unknown_name_has_stable_error(loaded_catalog) -> None:
    """编译对象读取未知属性时不返回隐式空值。"""
    with pytest.raises(CatalogError) as excinfo:
        loaded_catalog.objects["milk"].attribute("missing")
    assert excinfo.value.code == "CATALOG_ATTRIBUTE_MISSING"
