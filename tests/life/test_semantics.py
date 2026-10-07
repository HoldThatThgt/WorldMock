"""@brief 身体常数、环境对象和人物实例的真实组装合同。"""

from __future__ import annotations

import json
import shutil

import pytest
from pydantic import ValidationError

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from worldmock.catalog import load_catalog
from worldmock.errors import IntegrityError
from worldmock.exchange.body import BODY_CONSTANTS
from worldmock.life.scenario import LifeScenario, parse_scenario_document
from worldmock.life.semantics import assemble_semantics
from worldmock.life.world_setup import load_runtime_scenario

_SCENARIO = ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json"


@pytest.fixture(scope="module")
def semantics():
    """@brief 只加载当前人工环境，不创建引擎。"""
    return load_runtime_scenario(_SCENARIO)[0]


def test_energy_thresholds_come_from_worldmock_body_constants(semantics):
    """@brief 身体能量上限、基础代谢和告警阈值只取系统常数。"""
    body = semantics.body
    assert body.hunger_threshold_kcal == BODY_CONSTANTS.hunger_threshold_kcal == 800
    assert body.emergency_threshold_kcal == BODY_CONSTANTS.emergency_threshold_kcal == 400
    assert body.maximum_energy_kcal == BODY_CONSTANTS.maximum_energy_kcal == 2400
    assert body.basal_kcal_per_hour == BODY_CONSTANTS.basal_kcal_per_hour == 65
    assert body.source_kind == "worldmock:1"
    assert dict(body.activity_met) == {**BODY_CONSTANTS.activity_met, "lifestyle:quiet-rest": 1.0}
    assert dict(body.entertainment_relief_per_hour) == {
        "article": 0.2, "image_text": 0.2, "short_video": 0.3, "long_video": 0.4, "live": 0.3}


def test_registered_objects_and_body_instances_are_preserved(semantics):
    """@brief 食品、应用、内容和主题来自同一冻结登记，实例初值由场景保留。"""
    assert semantics.scenario.foods == tuple(semantics.catalog.foods.values())
    assert semantics.scenario.apps == tuple(semantics.catalog.apps.values())
    assert semantics.scenario.contents == tuple(semantics.catalog.contents.values())
    assert semantics.scenario.work_topics == tuple(semantics.catalog.work_topics.values())
    assert semantics.body.initial_entertainment_need == semantics.document.body.initial_entertainment_need == 0.8
    assert semantics.scenario.agents == semantics.document.agents
    assert semantics.scenario.work_topics[0].target_seconds == 28800


def test_ledger_accounts_exclude_the_external_anchor(semantics):
    """@brief 外部锚点不变成人物或服务账户余额。"""
    expected = {row.account_id: row.money_fen for row in semantics.document.accounts if row.role != "external:scenario"}
    expected.update({f"agent:{row.agent_id}": row.initial_balance_fen for row in semantics.document.agents})
    assert dict(semantics.accounts) == expected
    assert "external:scenario" not in semantics.accounts


def test_assembly_rejects_initial_energy_above_fixed_maximum():
    """@brief 人物初态超过固定身体上限时真实组装拒绝。"""
    document = json.loads(_SCENARIO.read_text(encoding="utf-8"))
    document["body"]["initial_energy_kcal"] = 2401
    with pytest.raises(ValidationError, match="initial energy exceeds maximum"):
        assemble_semantics(LifeScenario.model_validate(document), load_catalog(ARTIFICIAL_ENVIRONMENT_ROOT / "catalog"),
                           ARTIFICIAL_ENVIRONMENT_ROOT / "corpora")


@pytest.mark.parametrize("met", [0, -1, float("nan"), float("inf"), "expression"])
def test_assembly_rejects_invalid_lifestyle_met(tmp_path, met):
    """@brief 生活活动自身 MET 必须是有限正数，不接受表达式。"""
    root = tmp_path / "corpora"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT / "corpora", root)
    path = root / "lifestyle/items.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["items"][0]["met_value"] = met
    path.write_text(json.dumps(payload), encoding="utf-8")
    document, _ = parse_scenario_document(_SCENARIO)
    with pytest.raises(IntegrityError, match="Lifestyle declarations are invalid"):
        assemble_semantics(document, load_catalog(ARTIFICIAL_ENVIRONMENT_ROOT / "catalog"), root)


def test_candidate_constants_match_the_scene_declaration(semantics):
    """@brief 候选节律原样传入运行语义，不从环境增加配置。"""
    constants = semantics.candidate_constants
    assert constants.presentation_limit == semantics.document.candidate_presentation_limit
    assert constants.pool_limit == semantics.document.candidate_pool_limit
    assert constants.rotation_quota_ratio == semantics.document.rotation_quota_ratio
    assert constants.rotation_stale_seconds == semantics.document.rotation_stale_seconds


def test_initial_agent_preserves_declared_body_needs(semantics):
    """@brief 实际人物初态 helper 使用场景声明的三种需要，不重置娱乐需要。"""
    from worldmock.life.world_setup import _initial_agent

    scenario = semantics.scenario
    config = scenario.agents[0]
    agent = _initial_agent(config, scenario, f"agent:{config.agent_id}", int(scenario.start_at.timestamp()))
    assert agent.body.energy_kcal == scenario.body.initial_energy_kcal
    assert agent.body.sleep_deficit_hours == scenario.body.initial_sleep_debt_hours
    assert agent.body.entertainment_need == scenario.body.initial_entertainment_need
    assert agent.inventory == config.initial_inventory
