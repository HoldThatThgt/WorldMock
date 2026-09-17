"""生活活动目录（corpora/lifestyle）与 lifestyle:<id> 子类型接入的域内测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.life.models import (
    ActivityKind,
    StartActivityRequest,
    StartBasicActivityRequest,
    deepseek_strict_schema,
)
from worldmock.life.scenario import AgentConfig, load_scenario

from tests.life.conftest import advance_next, submit_round

ROOT = Path(__file__).resolve().parents[2]
ITEMS_PATH = ROOT / "corpora/lifestyle/items.json"
CATEGORIES = {"sport", "hobby", "learning", "household", "social",
              "wellness", "pet", "volunteer", "outdoor"}


def _items() -> list[dict]:
    return json.loads(ITEMS_PATH.read_text(encoding="utf-8"))["items"]


def test_lifestyle_catalog_integrity() -> None:
    """目录条数、身份、品类、中文文案与 MET 溯源全部封闭合规。"""
    items = _items()
    assert 180 <= len(items) <= 200
    ids = [item["activity_id"] for item in items]
    names = [item["name_zh"] for item in items]
    assert len(set(ids)) == len(ids)
    assert len(set(names)) == len(names)
    for item in items:
        # 目录 v2 富化：time_window/location_kinds/needs_partner/effects 四字段
        # （admission._check_lifestyle 的 v2 口径）。
        assert set(item) == {"activity_id", "name_zh", "category",
                             "typical_duration_minutes", "description_zh",
                             "met_value", "source", "time_window",
                             "location_kinds", "needs_partner", "effects"}
        assert item["category"] in CATEGORIES
        assert item["name_zh"].strip() and item["description_zh"].strip()
        assert 5 <= item["typical_duration_minutes"] <= 1440
        assert 1.0 <= item["met_value"] <= 16.0
        assert "Compendium 2011" in item["source"]


def test_lifestyle_catalog_covers_all_categories() -> None:
    """九个品类都有条目，目录不是单品类退化集。"""
    present = {item["category"] for item in _items()}
    assert present == CATEGORIES


def test_catalog_activity_met_carries_lifestyle_keys() -> None:
    """目录 energy_kcal 的 activity_met 精确携带全部 lifestyle:<id> 键。"""
    by_id = {}
    for line in (ROOT / "catalog/resources.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            by_id[entry["resource_id"]] = entry
    met = by_id["energy_kcal"]["drain_rules"]["activity_met"]
    expected = {f"lifestyle:{item['activity_id']}": item["met_value"] for item in _items()}
    lifestyle = {key: value for key, value in met.items() if key.startswith("lifestyle:")}
    assert lifestyle == expected
    base = {key for key in met if not key.startswith("lifestyle:")}
    assert len(base) == 10


def test_basic_request_pattern_contract() -> None:
    """start_basic_activity 的 activity_type 只接受 wait/sleep/lifestyle:<slug>。"""
    for valid in ("wait", "sleep", "lifestyle:yoga", "lifestyle:3d-printing"):
        StartBasicActivityRequest(activity_type=valid, planned_duration_seconds=120)
    for invalid in ("work", "lifestyle:", "lifestyle:Yoga", "lifestyle:yoga x", "lifestyle:-bad"):
        with pytest.raises(ValidationError):
            StartBasicActivityRequest(activity_type=invalid, planned_duration_seconds=120)
    schema = deepseek_strict_schema(StartBasicActivityRequest)
    assert schema["properties"]["activity_type"]["type"] == "string"


def test_start_activity_requires_target_for_lifestyle() -> None:
    """领域内启动 lifestyle 必须携带目录条目身份。"""
    with pytest.raises(ValidationError):
        StartActivityRequest(activity_type=ActivityKind.LIFESTYLE, planned_duration_seconds=120)
    request = StartActivityRequest(activity_type=ActivityKind.LIFESTYLE,
                                   planned_duration_seconds=120, target_id="yoga")
    assert request.target_id == "yoga"


def test_engine_accepts_and_settles_lifestyle_met(engine_factory) -> None:
    """未声明子集的世界全局启用：lifestyle:yoga 受理并按目录 MET 结算能量。"""
    engine = engine_factory()
    body = engine.scenario.body
    met = body.activity_met["lifestyle:yoga"]
    request = StartBasicActivityRequest(activity_type="lifestyle:yoga",
                                        planned_duration_seconds=900)
    _, results = submit_round(engine, {1: ("start_basic_activity", request)}, "lifestyle-yoga")
    assert results[0].activity_id
    activity = engine.state.activities[results[0].activity_id]
    assert activity.kind == ActivityKind.LIFESTYLE
    assert activity.target_id == "yoga"
    before = engine.state.agents[1].body.energy_kcal
    for _ in range(12):
        if engine.state.activities[results[0].activity_id].status.value != "active":
            break
        advance_next(engine)
    activity = engine.state.activities[results[0].activity_id]
    hours = 900 / 3600
    expected_cost = (body.basal_kcal_per_hour + max(met - 1, 0) * body.mass_kg) * hours
    assert activity.status.value == "completed"
    assert engine.state.agents[1].body.energy_kcal == pytest.approx(before - expected_cost)


def test_engine_rejects_unknown_lifestyle_id(engine_factory) -> None:
    """目录外条目 id 被业务规则拒绝，而非查表崩溃。"""
    engine = engine_factory()
    request = StartBasicActivityRequest(activity_type="lifestyle:not-in-catalog",
                                        planned_duration_seconds=120)
    _, results = submit_round(engine, {1: ("start_basic_activity", request)}, "lifestyle-bad")
    assert results[0].status.value == "rejected"
    assert results[0].error_code == "UNKNOWN_LIFESTYLE_ACTIVITY"


def test_engine_honors_agent_lifestyle_declaration(engine_factory) -> None:
    """multi-person 声明了子集：集合外的目录内 id 与未知 id 同口径拒绝。"""
    engine = engine_factory("multi-person.json")
    declarations = {item.agent_id: item.lifestyle_activities
                    for item in engine.scenario.agents}
    assert declarations[1] is not None and "yoga" in declarations[1]
    assert "sport-badminton" not in declarations[1]
    badminton = StartBasicActivityRequest(activity_type="lifestyle:sport-badminton",
                                          planned_duration_seconds=120)
    other = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120)
    _, results = submit_round(engine, {1: ("start_basic_activity", badminton),
                                       2: ("start_basic_activity", other)},
                              "lifestyle-undeclared")
    assert results[0].status.value == "rejected"
    assert results[0].error_code == "UNKNOWN_LIFESTYLE_ACTIVITY"


def test_scenario_admission_rejects_unknown_lifestyle_declaration() -> None:
    """语义层准入：声明了目录外的 activity_id 直接 fail-closed。"""
    from worldmock.errors import IntegrityError
    from worldmock.life.scenario import AgentConfig
    from worldmock.life.semantics import _check_lifestyle_declarations

    scenario, _ = load_scenario(ROOT / "scenarios/multi-person.json")
    body = scenario.body.model_copy(update={"activity_met": {"lifestyle:yoga": 2.5}})
    agent = scenario.agents[0].model_copy(
        update={"lifestyle_activities": ("yoga", "not-in-catalog")})
    document = scenario.model_copy(update={"agents": (agent,)})
    with pytest.raises(IntegrityError, match="unknown lifestyle activities"):
        _check_lifestyle_declarations(document, body)
    good = scenario.agents[0].model_copy(update={"lifestyle_activities": ("yoga",)})
    document = scenario.model_copy(update={"agents": (good,)})
    _check_lifestyle_declarations(document, body)


def test_agent_config_lifestyle_validation() -> None:
    """场景文档校验：空列表与重复 id 直接拒绝。"""
    scenario, _ = load_scenario(ROOT / "scenarios/multi-person.json")
    payload = scenario.agents[0].model_dump(mode="python")

    def revived(value):
        return AgentConfig.model_validate({**payload, "lifestyle_activities": value})

    assert revived(("yoga",)).lifestyle_activities == ("yoga",)
    with pytest.raises(ValidationError):
        revived(())
    with pytest.raises(ValidationError):
        revived(("yoga", "yoga"))


def test_scenario_files_load_with_lifestyle_wiring() -> None:
    """三份正式场景在新目录与 MET 表下仍完整加载。"""
    for name in ("multi-person.json", "multi-person-half-day.json", "white-collar-12-weeks.json"):
        scenario, _ = load_scenario(ROOT / "scenarios" / name)
        lifestyle_keys = {key for key in scenario.body.activity_met if key.startswith("lifestyle:")}
        assert len(lifestyle_keys) >= 180
