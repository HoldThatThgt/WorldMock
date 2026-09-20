"""@brief docs/design/03-resource-engine.md「准入边界」八条准入规则的正反例测试（类型级资源 + 账户声明 + 占位端点）。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.catalog.admission import (
    CATALOG_REF_INTEGRITY,
    CATALOG_VERSION_PIN_MISMATCH,
    CONSERVATION_DOMAIN_VIOLATION,
    FORMULA_REF_UNKNOWN,
    INITIAL_VALUE_OUT_OF_BOUNDS,
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
from worldmock.catalog.loader import load_catalog

from .conftest import (
    default_objects,
    default_resources,
    default_rules,
    default_tombstones,
    valid_refs,
    write_catalog,
)


def _catalog(
    tmp_path: Path,
    resources: list[dict] | None = None,
    objects: list[dict] | None = None,
    rules: dict[str, str] | None = None,
):
    """@brief 用可变载荷构建默认目录并加载。"""
    root = write_catalog(
        tmp_path / "catalog",
        resources if resources is not None else default_resources(),
        objects if objects is not None else default_objects(),
        default_tombstones(),
        rules=rules if rules is not None else default_rules(),
    )
    return load_catalog(root)


def _codes(result) -> set[str]:
    """@brief 提取全部违例错误码。"""
    return {violation.code for violation in result.violations}


# 第一条：引用完整性。


def test_reference_integrity_positive(tmp_path: Path) -> None:
    """@brief 全部引用已知条目（含占位端点角色绑定）时准入通过。"""
    result = validate_scenario_against_catalog(valid_refs(), _catalog(tmp_path))
    assert result.ok
    assert result.violations == ()


def test_reference_integrity_unknown_resource(tmp_path: Path) -> None:
    """@brief 场景引用未注册资源被拒。"""
    refs = valid_refs()
    refs = refs.model_copy(update={"resources": refs.resources + (
        ScenarioResourceRef(resource_id="calories", initial_value=1, unit="kcal"),
    )})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {CATALOG_REF_INTEGRITY}


def test_reference_integrity_unknown_account_type(tmp_path: Path) -> None:
    """@brief 场景账户绑定未注册的类型资源被拒。"""
    refs = valid_refs()
    refs = refs.model_copy(update={"accounts": refs.accounts + (
        ScenarioAccountRef(
            account_id="account:loyalty", resource_id="loyalty_points",
            role="loyalty", initial_value=0, unit="point",
        ),
    )})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {CATALOG_REF_INTEGRITY}


def test_reference_integrity_unknown_object(tmp_path: Path) -> None:
    """@brief 场景引用未注册对象被拒。"""
    refs = valid_refs()
    refs = refs.model_copy(update={"objects": refs.objects + (ScenarioObjectRef(object_id="ghost"),)})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {CATALOG_REF_INTEGRITY}


def test_reference_integrity_unknown_edge_endpoint(tmp_path: Path) -> None:
    """@brief 引用对象的边指向不存在的字面资源被拒。"""
    objects = default_objects()
    objects[0]["edges"][1]["resource"] = "energy_missing"
    refs = valid_refs()
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path, objects=objects))
    assert _codes(result) == {CATALOG_REF_INTEGRITY}


# 第二条：单位匹配。


def test_unit_mismatch_on_scenario_initial_value(tmp_path: Path) -> None:
    """@brief 场景初值单位与注册表不一致被拒。"""
    refs = valid_refs()
    mismatched = (ScenarioResourceRef(resource_id="energy_kcal", initial_value=1500, unit="cal"),)
    refs = refs.model_copy(update={"resources": mismatched})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {UNIT_MISMATCH}


def test_unit_mismatch_on_account_unit(tmp_path: Path) -> None:
    """@brief 场景账户声明单位与类型资源单位不一致被拒。"""
    refs = valid_refs()
    accounts = list(refs.accounts)
    accounts[0] = accounts[0].model_copy(update={"unit": "cents"})
    refs = refs.model_copy(update={"accounts": tuple(accounts)})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {UNIT_MISMATCH}


def test_unit_mismatch_on_explicit_edge_unit(tmp_path: Path) -> None:
    """@brief 边上显式声明的单位与资源单位不一致被拒。"""
    objects = default_objects()
    objects[0]["edges"][1]["unit"] = "cal"
    result = validate_scenario_against_catalog(valid_refs(), _catalog(tmp_path, objects=objects))
    assert _codes(result) == {UNIT_MISMATCH}


def test_unit_mismatch_on_formula_output_unit(tmp_path: Path) -> None:
    """@brief 公式声明的输出单位与转账资源单位不一致被拒。

    把 traveler 与 transport 角色改绑到 point 单位的封闭账户：fare 公式
    输出 fen，转账域却是 point。
    """
    resources = default_resources()
    resources.append(
        {
            "resource_id": "loyalty_points",
            "kind": "account",
            "unit": "point",
            "owner": "global",
            "bounds": None,
            "conservation": "closed",
            "alert_rules": [],
            "provenance": [
                {"part": "type", "paths": [], "type": "scenario", "source": "test fixture"}
            ],
            "version": 1,
        }
    )
    refs = valid_refs()
    accounts = tuple(
        account.model_copy(update={"resource_id": "loyalty_points", "unit": "point"})
        if account.role in ("traveler", "transport:service")
        else account
        for account in refs.accounts
    )
    refs = refs.model_copy(update={"accounts": accounts})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path, resources=resources))
    assert _codes(result) == {UNIT_MISMATCH}


# 第三条：守恒域一致。


def test_conservation_scenario_account_must_bind_closed_account_type(tmp_path: Path) -> None:
    """@brief 场景账户绑定开放域 level 资源被拒。"""
    refs = valid_refs()
    refs = refs.model_copy(update={"accounts": refs.accounts + (
        ScenarioAccountRef(
            account_id="account:energy", resource_id="energy_kcal",
            role="energy_holder", initial_value=100, unit="kcal",
        ),
    )})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {CONSERVATION_DOMAIN_VIOLATION}


def test_conservation_unbound_role_rejected(tmp_path: Path) -> None:
    """@brief 占位端点的角色未被场景账户声明被拒。"""
    refs = valid_refs()
    accounts = tuple(a for a in refs.accounts if a.role != "merchant:grocery")
    refs = refs.model_copy(update={"accounts": accounts})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {CONSERVATION_DOMAIN_VIOLATION}


def test_conservation_transfer_literal_endpoint_must_be_closed_account(tmp_path: Path) -> None:
    """@brief 字面端点的 transfer 指向开放域账户被拒（同单位以隔离守恒域规则）。"""
    resources = default_resources()
    resources.append(
        {
            "resource_id": "voucher_fen",
            "kind": "account",
            "unit": "fen",
            "owner": "global",
            "bounds": None,
            "conservation": "open",
            "alert_rules": [],
            "provenance": [
                {"part": "type", "paths": [], "type": "scenario", "source": "test fixture"}
            ],
            "version": 1,
        }
    )
    objects = default_objects()
    objects[0]["edges"][0] = {
        "on": "purchase",
        "type": "transfer",
        "from": "money_fen",
        "to": "voucher_fen",
        "amount_fen": 680,
    }
    result = validate_scenario_against_catalog(valid_refs(), _catalog(tmp_path, resources=resources, objects=objects))
    assert _codes(result) == {CONSERVATION_DOMAIN_VIOLATION}


def test_conservation_transfer_endpoints_must_share_domain(tmp_path: Path) -> None:
    """@brief 占位两端绑定到不同 closed 域被拒（同单位以隔离守恒域规则）。"""
    resources = default_resources()
    resources.append(
        {
            "resource_id": "coupon_fen",
            "kind": "account",
            "unit": "fen",
            "owner": "global",
            "bounds": None,
            "conservation": "closed",
            "alert_rules": [],
            "provenance": [
                {"part": "type", "paths": [], "type": "scenario", "source": "test fixture"}
            ],
            "version": 1,
        }
    )
    objects = default_objects()
    objects[0]["edges"][0] = {
        "on": "purchase",
        "type": "transfer",
        "from": "{buyer}",
        "to": "{coupon_bank}",
        "amount_fen": 680,
        "account_roles": {"from": "buyer", "to": "coupon_bank"},
    }
    refs = valid_refs()
    refs = refs.model_copy(update={"accounts": refs.accounts + (
        ScenarioAccountRef(
            account_id="coupon:bank", resource_id="coupon_fen",
            role="coupon_bank", initial_value=0, unit="fen",
        ),
    )})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path, resources=resources, objects=objects))
    assert _codes(result) == {CONSERVATION_DOMAIN_VIOLATION}


def test_conservation_source_must_target_open_domain(tmp_path: Path) -> None:
    """@brief source 边注入封闭守恒账户被拒。"""
    objects = default_objects()
    objects[0]["edges"][1] = {
        "on": "consume",
        "type": "source",
        "resource": "money_fen",
        "amount": 185,
    }
    result = validate_scenario_against_catalog(valid_refs(), _catalog(tmp_path, objects=objects))
    assert _codes(result) == {CONSERVATION_DOMAIN_VIOLATION}


# 第四条：静态初值界内。


def test_initial_value_out_of_bounds(tmp_path: Path) -> None:
    """@brief 初值越过资源硬上界被拒。"""
    refs = valid_refs()
    over = (ScenarioResourceRef(resource_id="energy_kcal", initial_value=3000, unit="kcal"),)
    refs = refs.model_copy(update={"resources": over})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {INITIAL_VALUE_OUT_OF_BOUNDS}


def test_initial_value_at_boundary_accepted(tmp_path: Path) -> None:
    """@brief 恰好落在界上的初值放行（闭区间）。"""
    refs = valid_refs()
    at_max = (ScenarioResourceRef(resource_id="energy_kcal", initial_value=2400, unit="kcal"),)
    refs = refs.model_copy(update={"resources": at_max})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert result.ok


def test_null_upper_bound_skips_max_comparison(tmp_path: Path) -> None:
    """@brief bounds.max 为 null 的资源任意大初值放行（只受下界约束）。"""
    refs = valid_refs()
    unbounded = (ScenarioResourceRef(resource_id="sleep_debt_hours",
                                     initial_value=1e9, unit="hours"),)
    refs = refs.model_copy(update={"resources": unbounded})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert result.ok


def test_null_upper_bound_still_enforces_minimum(tmp_path: Path) -> None:
    """@brief bounds.max 为 null 的资源初值低于下界仍被拒。"""
    refs = valid_refs()
    below = (ScenarioResourceRef(resource_id="sleep_debt_hours",
                                 initial_value=-1, unit="hours"),)
    refs = refs.model_copy(update={"resources": below})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {INITIAL_VALUE_OUT_OF_BOUNDS}


# 第五条：无墓碑引用。


def test_tombstoned_object_reference_rejected(tmp_path: Path) -> None:
    """@brief 新场景引用墓碑对象被拒。"""
    refs = valid_refs()
    refs = refs.model_copy(update={"objects": refs.objects + (ScenarioObjectRef(object_id="food-soda"),)})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {TOMBSTONED_REFERENCE}


# 第六条：预排表合法性。


def test_schedule_not_strictly_increasing(tmp_path: Path) -> None:
    """@brief 预排表 (due_at, sequence) 出现回退或重复被拒。"""
    refs = valid_refs()
    duplicated = refs.schedule + (
        ScenarioScheduleRef(kind=ScheduleRefKind.CALENDAR_FLAG, due_at=500, sequence=1),
    )
    refs = refs.model_copy(update={"schedule": duplicated})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {SCHEDULE_INVALID}


def test_schedule_object_version_mismatch(tmp_path: Path) -> None:
    """@brief 预排引用钉错对象版本被拒。"""
    refs = valid_refs()
    wrong_version = (
        ScenarioScheduleRef(
            kind=ScheduleRefKind.PRICE_CHANGE,
            due_at=100,
            sequence=1,
            object_id="food-milk",
            object_version=7,
        ),
        refs.schedule[1],
    )
    refs = refs.model_copy(update={"schedule": wrong_version})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {SCHEDULE_INVALID}


def test_schedule_bill_due_undeclared_account(tmp_path: Path) -> None:
    """@brief BILL_DUE 引用场景未声明的账户实例被拒。"""
    refs = valid_refs()
    orphan = refs.schedule[:1] + (
        ScenarioScheduleRef(kind=ScheduleRefKind.BILL_DUE, due_at=500, sequence=1, account_id="ghost"),
    )
    refs = refs.model_copy(update={"schedule": orphan})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {SCHEDULE_INVALID}


# 第八条：计费公式引用。


def test_unknown_formula_reference_rejected(tmp_path: Path) -> None:
    """@brief 对象顶层 formula_ref 引用未声明的费率公式被拒。"""
    objects = default_objects()
    objects[2]["formula_ref"] = "fare_unknown"
    objects[2]["edges"][0]["amount_fen"] = {"formula_ref": "fare_unknown", "params": {}}
    result = validate_scenario_against_catalog(valid_refs(), _catalog(tmp_path, objects=objects))
    assert _codes(result) == {FORMULA_REF_UNKNOWN}


def test_unknown_edge_formula_binding_rejected(tmp_path: Path) -> None:
    """@brief 边金额的公式绑定引用未声明公式被拒。"""
    objects = default_objects()
    objects[2]["edges"][0]["amount_fen"] = {"formula_ref": "fare_missing", "params": {}}
    result = validate_scenario_against_catalog(valid_refs(), _catalog(tmp_path, objects=objects))
    assert _codes(result) == {FORMULA_REF_UNKNOWN}


# 补充：版本钉扎与违例聚合。


def test_version_pin_mismatch_rejected(tmp_path: Path) -> None:
    """@brief 场景钉扎的目录版本与加载视图不一致被拒。"""
    refs = valid_refs().model_copy(update={"catalog_major": 2, "catalog_minor": 4})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert _codes(result) == {CATALOG_VERSION_PIN_MISMATCH}


def test_multiple_violations_accumulate(tmp_path: Path) -> None:
    """@brief 多条违例同时出现时逐条报告，不许短路。"""
    refs = valid_refs().model_copy(update={"objects": (ScenarioObjectRef(object_id="ghost"),)})
    result = validate_scenario_against_catalog(refs, _catalog(tmp_path))
    assert not result.ok
    assert len(result.violations) == 1
    both = valid_refs().model_copy(
        update={
            "resources": (ScenarioResourceRef(resource_id="ghost", initial_value=1, unit="x"),),
            "objects": (ScenarioObjectRef(object_id="ghost2"),),
        }
    )
    combined = validate_scenario_against_catalog(both, _catalog(tmp_path))
    assert _codes(combined) == {CATALOG_REF_INTEGRITY}
    assert len(combined.violations) == 2


def test_duplicate_account_roles_rejected_at_construction() -> None:
    """@brief 同一角色绑定多个账户实例在构造期即被拒（绑定不许有歧义）。"""
    refs = valid_refs()
    payload = refs.model_dump()
    payload["accounts"] = list(payload["accounts"]) + [
        {
            "account_id": "account:agent:2",
            "resource_id": "money_fen",
            "role": "buyer",
            "initial_value": 1,
            "unit": "fen",
        }
    ]
    with pytest.raises(ValidationError, match="roles must be unique"):
        ScenarioCatalogRefs.model_validate(payload)


_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_RULE_COUNT_PATTERN = re.compile(r"的([一二三四五六七八九十]+)条规则")


def _chinese_number(text: str) -> int:
    """@brief 解析 docstring 里的中文条数（支持"十"以内与"十几"两种形态）。"""
    if "十" not in text:
        return _CN_DIGITS[text]
    head, _, tail = text.partition("十")
    tens = _CN_DIGITS[head] if head else 1
    ones = _CN_DIGITS[tail] if tail else 0
    return tens * 10 + ones


def _admission_module() -> ast.Module:
    """@brief 解析 admission 生产源文件。"""
    from worldmock.catalog import admission

    source = Path(admission.__file__).read_text(encoding="utf-8")
    return ast.parse(source)


def _rule_check_call_count(tree: ast.Module) -> int:
    """@brief 统计 validate_scenario_against_catalog 函数体内实际调用的 _check_* 条数。"""
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "validate_scenario_against_catalog")
    return sum(1 for node in ast.walk(function)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id.startswith("_check_"))


def test_admission_rule_count_matches_docstring() -> None:
    """@brief docstring 自陈的规则条数等于函数体内实际调用的 _check_* 条数（防再次漂移）。"""
    tree = _admission_module()
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "validate_scenario_against_catalog")
    declared = _RULE_COUNT_PATTERN.search(ast.get_docstring(function) or "")
    assert declared is not None, "admission docstring must declare its rule count in Chinese"
    assert _chinese_number(declared.group(1)) == _rule_check_call_count(tree)
    assert _chinese_number(declared.group(1)) == 8
