"""@brief 编译目录场景引用准入测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from worldmock.catalog.admission import (
    CATALOG_REF_INTEGRITY,
    CATALOG_VERSION_PIN_MISMATCH,
    CONSERVATION_DOMAIN_VIOLATION,
    INITIAL_VALUE_OUT_OF_BOUNDS,
    RULE_REF_UNKNOWN,
    SCHEDULE_INVALID,
    TOMBSTONED_REFERENCE,
    UNIT_MISMATCH,
    ScenarioAccountRef,
    ScenarioCatalogRefs,
    ScenarioObjectRef,
    ScenarioResourceRef,
    ScenarioScheduleRef,
    ScheduleRefKind,
    validate_scenario_against_catalog,
)
from worldmock.catalog.models import RuleEdge, Tombstone

from .conftest import valid_refs


def _codes(result) -> set[str]:
    """@brief 返回准入结果中的稳定错误码集合。"""
    return {item.code for item in result.violations}


def test_valid_compiled_catalog_refs_pass(loaded_catalog) -> None:
    """合法资源、账户与对象引用闭集通过准入。"""
    result = validate_scenario_against_catalog(valid_refs(), loaded_catalog)
    assert result.ok
    assert result.violations == ()


def test_catalog_pin_pair_and_account_role_uniqueness_are_strict() -> None:
    """目录版本必须成对声明，一个语义角色不能绑定多个账户。"""
    with pytest.raises(ValueError):
        ScenarioCatalogRefs(catalog_major=3)
    account = valid_refs().accounts[0]
    with pytest.raises(ValueError):
        ScenarioCatalogRefs(accounts=(account, account.model_copy(update={"account_id": "second"})))


def test_version_pin_and_reference_integrity_fail_closed(loaded_catalog) -> None:
    """版本不符与未知对象均被收集为准入违例。"""
    refs = valid_refs().model_copy(update={
        "catalog_minor": 2,
        "objects": (ScenarioObjectRef(object_id="unknown"),),
    })
    codes = _codes(validate_scenario_against_catalog(refs, loaded_catalog))
    assert {CATALOG_VERSION_PIN_MISMATCH, CATALOG_REF_INTEGRITY} <= codes


def test_resource_and_account_unit_mismatch_rejected(loaded_catalog) -> None:
    """资源初值与账户实例单位必须等于注册表单位。"""
    refs = valid_refs().model_copy(update={
        "resources": (ScenarioResourceRef(resource_id="energy_kcal", initial_value=1, unit="joule"),),
        "accounts": (
            ScenarioAccountRef(
                account_id="agent", resource_id="money_fen", role="buyer",
                initial_value=10, unit="yuan",
            ),
            valid_refs().accounts[1],
        ),
    })
    assert UNIT_MISMATCH in _codes(validate_scenario_against_catalog(refs, loaded_catalog))


def test_open_resource_cannot_back_account_instance(loaded_catalog) -> None:
    """场景账户不能绑定开放 level 资源。"""
    refs = valid_refs().model_copy(update={
        "accounts": (
            ScenarioAccountRef(
                account_id="agent", resource_id="energy_kcal", role="buyer",
                initial_value=10, unit="kcal",
            ),
            valid_refs().accounts[1],
        ),
    })
    assert CONSERVATION_DOMAIN_VIOLATION in _codes(
        validate_scenario_against_catalog(refs, loaded_catalog)
    )


def test_initial_level_outside_bounds_rejected(loaded_catalog) -> None:
    """身体资源初值超过硬上界时拒绝。"""
    refs = valid_refs().model_copy(update={
        "resources": (ScenarioResourceRef(
            resource_id="energy_kcal", initial_value=3000, unit="kcal",
        ),),
    })
    assert INITIAL_VALUE_OUT_OF_BOUNDS in _codes(
        validate_scenario_against_catalog(refs, loaded_catalog)
    )


def test_schedule_order_object_version_and_account_checked(loaded_catalog) -> None:
    """预排表顺序、对象版本和账单账户引用全部 fail-closed。"""
    schedule = (
        ScenarioScheduleRef(
            kind=ScheduleRefKind.PRICE_CHANGE, due_at=2, sequence=0,
            object_id="milk", object_version=2,
        ),
        ScenarioScheduleRef(
            kind=ScheduleRefKind.BILL_DUE, due_at=1, sequence=0, account_id="missing",
        ),
    )
    refs = valid_refs().model_copy(update={"schedule": schedule})
    assert SCHEDULE_INVALID in _codes(validate_scenario_against_catalog(refs, loaded_catalog))


def test_tombstoned_object_reference_rejected(loaded_catalog) -> None:
    """新场景不能引用墓碑对象。"""
    tombstone = Tombstone(
        target_id="milk", kind="object", at_version=1, reason="retired",
    )
    catalog = replace(loaded_catalog, tombstones={"milk": tombstone})
    assert TOMBSTONED_REFERENCE in _codes(validate_scenario_against_catalog(valid_refs(), catalog))


def test_unknown_compiled_rule_reference_rejected(loaded_catalog) -> None:
    """动态编译边不能绕过冻结 typed rule 表。"""
    obj = loaded_catalog.objects["milk"].model_copy(update={
        "edges": (*loaded_catalog.objects["milk"].edges,
                  RuleEdge(on="consume:use", type="rule", rule_id="unknown", coefficient=1)),
    })
    catalog = replace(loaded_catalog, objects={"milk": obj})
    assert RULE_REF_UNKNOWN in _codes(validate_scenario_against_catalog(valid_refs(), catalog))
