"""场景语义组装器的公式求值、告警映射与目录准入测试。"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from worldmock.catalog import Catalog, CatalogManifest, ResourceDefinition, load_catalog
from worldmock.errors import IntegrityError
from worldmock.life.models import ContractKind, TravelMode
from worldmock.life.scenario import parse_scenario_document
from worldmock.life.semantics import (
    ScenarioSemantics,
    assemble_semantics,
    evaluate_int_expression,
)

_CATALOG = Path("catalog")
_SCENARIOS = (Path("scenarios/multi-person.json"), Path("scenarios/white-collar-12-weeks.json"))


@pytest.fixture(scope="module")
def semantics() -> ScenarioSemantics:
    """组装正式十二周场景作为共享夹具。"""
    document, _ = parse_scenario_document(_SCENARIOS[1])
    return assemble_semantics(document, load_catalog(_CATALOG))


def test_travel_fare_formula_reproduces_every_frozen_route(semantics) -> None:
    """walking=0、driving=max(500,ceil(m*0.2)) 逐条复现全部冻结报价。"""
    for path in _SCENARIOS:
        document, _ = parse_scenario_document(path)
        for route in document.routes:
            if route.mode == TravelMode.WALKING:
                expected = 0
            else:
                expected = max(500, math.ceil(route.distance_meters * 0.2))
            assert semantics.travel_fare(route.mode.value, route.distance_meters) == expected
            assert route.fare_fen == expected


def test_contract_math_matches_the_engine_settlement(semantics) -> None:
    """三模板 convert 公式求值与 work 结算的数学完全一致。"""
    task = semantics.scenario.tasks[0]
    shell = type(task).model_construct
    hourly = shell(contract_kind=ContractKind.HOURLY, pay_fen=60000,
                   maximum_duration_seconds=28800)
    piece = shell(contract_kind=ContractKind.PIECE, pay_fen=60000,
                  maximum_duration_seconds=28800)
    assert semantics.contract_payment(hourly, 7200, None) == min(
        60000 * 7200 // 3600, 60000 * 28800 // 3600)
    assert semantics.contract_payment(hourly, 3600 * 24, None) == 60000 * 28800 // 3600
    assert semantics.contract_payment(piece, 0, 7) == 60000 * 7
    with pytest.raises(IntegrityError, match="oracle piece count"):
        semantics.contract_payment(piece, 0, None)
    assert semantics.contract_payment(task, 0, None) == task.pay_fen


def test_alert_codes_map_catalog_uppercase_to_engine_lowercase(semantics) -> None:
    """目录 HUNGRY/EMERGENCY_ENERGY 映射为引擎现行小写告警码。"""
    assert dict(semantics.alert_codes) == {
        "HUNGRY": "hungry", "EMERGENCY_ENERGY": "emergency_energy"}
    assert semantics.body.hunger_threshold_kcal == 800
    assert semantics.body.emergency_threshold_kcal == 400
    assert semantics.body.maximum_energy_kcal == 2400


def test_products_resolve_edges_to_concrete_accounts(semantics) -> None:
    """商品价格/热量来自对象边，商家解析到场景声明的具体账户。"""
    milk = next(item for item in semantics.scenario.products
                if item.product_id == "food-6923644266066")
    umbrella = next(item for item in semantics.scenario.products
                    if item.product_id == "item-umbrella")
    assert milk.price_fen == 680 and milk.energy_kcal_per_unit == 185
    assert milk.merchant_account_id == "merchant:grocery"
    assert umbrella.price_fen == 3990 and umbrella.energy_kcal_per_unit == 0
    assert umbrella.merchant_account_id == "merchant:general"
    assert umbrella.effect == "weather_protection"
    assert milk.price_version == "catalog:food-6923644266066:v1"


def test_ledger_accounts_exclude_the_external_anchor(semantics) -> None:
    """运行账本包含服务与人物账户，外部锚点只做守恒吸收。"""
    assert "external:scenario" not in semantics.accounts
    assert semantics.accounts["agent:1"] == 800000
    assert semantics.accounts["employer:zhangjiang"] == 4000000
    assert set(semantics.accounts) == {
        "agent:1", "merchant:grocery", "merchant:general",
        "employer:zhangjiang", "transport:service"}


def _drifted_catalog(semantics: ScenarioSemantics, mutate) -> Catalog:
    """复制目录资源表、应用一次变异并重建只读视图。"""
    resources = {key: value.model_dump() for key, value in semantics.catalog.resources.items()}
    mutate(resources)
    rebuilt = {key: ResourceDefinition.model_validate(value) for key, value in resources.items()}
    manifest = CatalogManifest(version_major=semantics.catalog.version_major,
                               version_minor=semantics.catalog.version_minor)
    return Catalog.build(manifest, rebuilt, dict(semantics.catalog.objects),
                         dict(semantics.catalog.formulas), dict(semantics.catalog.tombstones))


def test_assembly_rejects_catalog_met_drift(semantics) -> None:
    """目录 MET 表缺少引擎活动键时组装 fail-closed。"""

    def drop_work_met(resources):
        resources["energy_kcal"]["drain_rules"]["activity_met"].pop("work")

    with pytest.raises(IntegrityError, match="activity MET keys"):
        assemble_semantics(semantics.document, _drifted_catalog(semantics, drop_work_met))


def test_assembly_rejects_unbounded_initial_below_minimum(semantics) -> None:
    """无上界资源的初值低于目录下界时由完整准入第四条拒绝。"""

    def raise_sleep_floor(resources):
        resources["sleep_debt_hours"]["bounds"]["min"] = 5

    with pytest.raises(IntegrityError, match="INITIAL_VALUE_OUT_OF_BOUNDS"):
        assemble_semantics(semantics.document, _drifted_catalog(semantics, raise_sleep_floor))


def test_assembly_rejects_missing_alert_rules(semantics) -> None:
    """能量资源缺少告警规则时阈值参数无法组装。"""

    def drop_alerts(resources):
        resources["energy_kcal"]["alert_rules"] = []

    with pytest.raises(IntegrityError, match="HUNGRY"):
        assemble_semantics(semantics.document, _drifted_catalog(semantics, drop_alerts))


def test_expression_evaluator_is_closed_and_safe() -> None:
    """求值器只接受封闭算子集并拒绝未知记号。"""
    assert evaluate_int_expression("max(500, ceil(m * 0.2))", {"m": 1000}) == 500
    assert evaluate_int_expression("min(a // 3600, b // 3600)", {"a": 7200, "b": 28800}) == 2
    assert evaluate_int_expression("1 + 2 * 3", {}) == 7
    with pytest.raises(IntegrityError, match="unsupported tokens"):
        evaluate_int_expression("m ^ 2", {"m": 3})
    with pytest.raises(IntegrityError, match="unknown name"):
        evaluate_int_expression("pay_fen * 2", {})
    with pytest.raises(IntegrityError, match="integer"):
        evaluate_int_expression("m / 3", {"m": 10})


def test_admission_refs_cover_accounts_and_objects(semantics) -> None:
    """准入引用包含对象闭集与角色账户绑定。"""
    refs = semantics.refs
    assert (refs.catalog_major, refs.catalog_minor) == (1, 0)
    roles = {account.role for account in refs.accounts}
    assert {"buyer", "traveler", "worker", "merchant:grocery", "merchant:general",
            "employer:zhangjiang", "transport:service", "external:scenario"} <= roles
    objects = {item.object_id for item in refs.objects}
    assert "travel-service" in objects
    expected_contracts = {f"contract-{task.contract_kind.value}"
                          for task in semantics.scenario.tasks}
    assert expected_contracts <= objects
    assert objects - expected_contracts == {
        "travel-service",
        *{item.product_id for item in semantics.document.products}}
