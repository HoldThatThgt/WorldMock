"""场景语义组装器的闭集算子、告警映射与目录准入测试。"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from worldmock.catalog import Catalog, ResourceDefinition, load_catalog
from worldmock.errors import IntegrityError
from worldmock.life.models import ContractKind, TravelMode
from worldmock.life.map_data import load_map_dataset
from worldmock.life.scenario import parse_scenario_document
from worldmock.life import semantics as semantics_module
from worldmock.life.semantics import ScenarioSemantics, assemble_semantics
from worldmock.life.travel_time import parse_travel_time_profile

_CATALOG = Path("catalog")
_SCENARIOS = (Path("scenarios/multi-person.json"), Path("scenarios/white-collar-12-weeks.json"))


@pytest.fixture(scope="module")
def semantics() -> ScenarioSemantics:
    """组装正式十二周场景作为共享夹具。"""
    document, _ = parse_scenario_document(_SCENARIOS[1])
    return assemble_semantics(document, load_catalog(_CATALOG), load_map_dataset(Path("corpora"), document.map_dataset))


def test_travel_fare_rule_reproduces_every_frozen_route(semantics) -> None:
    """闭集 walking/driving 费率逐条复现全部冻结报价。"""
    for path in _SCENARIOS:
        document, _ = parse_scenario_document(path)
        dataset = load_map_dataset(Path("corpora"), document.map_dataset)
        for route in dataset.routes:
            if route.mode == TravelMode.WALKING:
                expected = 0
            else:
                expected = max(500, math.ceil(route.distance_meters * 0.2))
            assert semantics.travel_fare(route.mode.value, route.distance_meters) == expected
            assert route.fare_fen == expected


def test_contract_math_matches_the_engine_settlement(semantics) -> None:
    """三种 typed 合同算子与 work 结算的数学完全一致。"""
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
    with pytest.raises(IntegrityError, match="EXCHANGE_PIECE_COUNT_MISSING"):
        semantics.contract_payment(piece, 0, None)
    assert semantics.contract_payment(task, 0, None) == task.pay_fen


def test_energy_thresholds_come_from_catalog_alert_rules(semantics) -> None:
    """目录 alert_rules 的 HUNGRY/EMERGENCY_ENERGY 仍是 800、400 kcal 阈值的来源；语义不另存告警码。"""
    assert not hasattr(semantics, "alert_codes")
    assert semantics.body.hunger_threshold_kcal == 800
    assert semantics.body.emergency_threshold_kcal == 400
    assert semantics.body.maximum_energy_kcal == 2400


def test_products_and_supplies_resolve_their_separate_responsibilities(semantics) -> None:
    """商品保留热量与使用效果；独立供给声明价格、库存与具体卖方。"""
    milk = next(item for item in semantics.scenario.products
                if item.product_id == "food-6923644266066")
    umbrella = next(item for item in semantics.scenario.products
                    if item.product_id == "item-umbrella")
    supplies = {item.product_id: item for item in semantics.scenario.supplies if item.location_id == "market"}
    assert supplies[milk.product_id].quotes[0].price_fen == 680 and milk.energy_kcal_per_unit == 185
    assert supplies[milk.product_id].seller_account_id == "merchant:grocery"
    assert supplies[umbrella.product_id].quotes[0].price_fen == 3990 and umbrella.energy_kcal_per_unit == 0
    assert supplies[umbrella.product_id].seller_account_id == "merchant:general"
    assert umbrella.effect == "weather_protection"
    assert supplies[milk.product_id].quotes[0].price_version == "supply-market-food-6923644266066:v1"
    assert not {"price_fen", "merchant_account_id", "initial_stock"} & type(milk).model_fields.keys()


def test_ledger_accounts_exclude_the_external_anchor(semantics) -> None:
    """运行账本包含服务与人物账户，外部锚点只做守恒吸收。"""
    assert "external:scenario" not in semantics.accounts
    assert semantics.accounts["agent:1"] == 800000
    assert semantics.accounts["employer:zhangjiang"] == 4000000
    # 正餐供给明确绑定餐饮账户，独立门店可以复用同一个业务角色。
    assert semantics.accounts["merchant:restaurant"] == 10000000
    assert set(semantics.accounts) == {
        "agent:1", "merchant:grocery", "merchant:general", "merchant:restaurant",
        "employer:zhangjiang", "transport:service", "merchant:haode", "merchant:nonggongshang"}


def _drifted_catalog(semantics: ScenarioSemantics, mutate) -> Catalog:
    """复制目录资源表、应用一次变异并重建只读视图。"""
    resources = {key: value.model_dump() for key, value in semantics.catalog.resources.items()}
    mutate(resources)
    rebuilt = {key: ResourceDefinition.model_validate(value) for key, value in resources.items()}
    return replace(semantics.catalog, resources=MappingProxyType(rebuilt))


def test_assembly_rejects_catalog_met_drift(semantics) -> None:
    """目录 MET 表缺少引擎活动键时组装 fail-closed。"""

    def drop_work_met(resources):
        resources["energy_kcal"]["drain_rules"]["activity_met"].pop("work")

    with pytest.raises(IntegrityError, match="activity MET"):
        assemble_semantics(semantics.document, _drifted_catalog(semantics, drop_work_met), semantics.scenario.map_dataset)


def test_assembly_rejects_malformed_lifestyle_met_key(semantics) -> None:
    """lifestyle MET 键形状非法（空 id/非 slug 字符）时组装 fail-closed。"""

    def inject_bad_lifestyle_key(resources):
        resources["energy_kcal"]["drain_rules"]["activity_met"]["lifestyle:Bad_Id"] = 2.0

    with pytest.raises(IntegrityError, match="malformed"):
        assemble_semantics(semantics.document, _drifted_catalog(semantics, inject_bad_lifestyle_key),
                           semantics.scenario.map_dataset)


def test_assembly_rejects_unbounded_initial_below_minimum(semantics) -> None:
    """无上界资源的初值低于目录下界时由完整准入第四条拒绝。"""

    def raise_sleep_floor(resources):
        resources["sleep_debt_hours"]["bounds"]["min"] = 5

    with pytest.raises(IntegrityError, match="INITIAL_VALUE_OUT_OF_BOUNDS"):
        assemble_semantics(semantics.document, _drifted_catalog(semantics, raise_sleep_floor),
                           semantics.scenario.map_dataset)


def test_assembly_rejects_missing_alert_rules(semantics) -> None:
    """能量资源缺少告警规则时阈值参数无法组装。"""

    def drop_alerts(resources):
        resources["energy_kcal"]["alert_rules"] = []

    with pytest.raises(IntegrityError, match="HUNGRY"):
        assemble_semantics(semantics.document, _drifted_catalog(semantics, drop_alerts), semantics.scenario.map_dataset)


def test_arbitrary_expression_evaluator_is_removed(semantics) -> None:
    """生产语义不再导出表达式解释器，数值表也拒绝字符串表达式。"""
    assert not hasattr(semantics_module, "evaluate_int_expression")
    malformed = semantics.catalog.travel_time_rules_bytes.replace(
        b"walking = 1.0", b'walking = "1 + 0"')
    with pytest.raises(IntegrityError, match="not numeric"):
        parse_travel_time_profile(malformed)


def test_admission_refs_cover_accounts_and_objects(semantics) -> None:
    """准入引用包含对象闭集与角色账户绑定。"""
    refs = semantics.refs
    assert (refs.catalog_major, refs.catalog_minor) == (4, 1)
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
