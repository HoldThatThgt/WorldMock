"""@brief 外部环境效果沿候选、冻结活动与恢复结算流动的行为验收。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from worldmock.catalog.affordances import engine_affordances
from worldmock.errors import IntegrityError
from worldmock.life.activities import (
    ActivityOrigin,
    advance_activity,
    change_activity,
    lifestyle_relief_rates,
    start_activity,
)
from worldmock.life.body import last_flows
from worldmock.life.content import load_lifestyle_items
from worldmock.life.models import (
    ActivityKind,
    ActivityStartParams,
    ActivityState,
    AgentState,
    BodyState,
    ChangeActivityRequest,
)
from worldmock.life.motivation import MotivationContext, _lifestyle_candidate
from worldmock.life.scenario import BodyConfig


_EFFECTS = {"entertainment": 0.2, "social": 0.1, "health": 0.6}


def synthetic_body() -> BodyConfig:
    """@brief 身体运行视图采用当前字段，生活活动效果独立来自人工目录。"""
    return BodyConfig.model_validate({
        'mass_kg': 65, 'initial_energy_kcal': 1800, 'maximum_energy_kcal': 2400,
        'initial_sleep_debt_hours': 0, 'initial_entertainment_need': 0.8,
        'basal_kcal_per_hour': 65, 'entertainment_need_per_awake_hour': 0.08,
        'hunger_threshold_kcal': 800, 'emergency_threshold_kcal': 400,
        'activity_met': {'wait': 1.0, 'sleep': 1.0, 'lifestyle:jogging': 7.0},
        'entertainment_relief_per_hour': {}, 'source_kind': 'test'})


def _external_item(root: Path, effects: dict):
    """@brief 向独立外部环境写一条人工生活活动并通过实际加载器读取。"""
    (root / "lifestyle").mkdir(parents=True)
    payload = {"items": [{"activity_id": "jogging", "name_zh": "慢跑", "category": "sport",
                          "typical_duration_minutes": 30, "description_zh": "人工活动条目",
                          "met_value": 7.0, "effects": effects}]}
    (root / "lifestyle/items.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return load_lifestyle_items(root, {"lifestyle:jogging": 7.0})[0]


def _agent() -> AgentState:
    """@brief 同一人物用于比较独立环境的实际效果。"""
    return AgentState(agent_id=1, name="Lin", location_id="home", body_settled_at=0,
                      balance_account_id="agent:1", next_decision_at=0,
                      body=BodyState(energy_kcal=1800, sleep_pressure=0.3, sleep_deficit_hours=1,
                                     entertainment_need=0.8, social_need=0.6))


def _running(effects: dict):
    """@brief 经实际活动领域接口创建带已冻结效果的生活活动。"""
    agent, activity = start_activity(
        _agent(), ActivityStartParams(activity_type=ActivityKind.LIFESTYLE,
                                     planned_duration_seconds=1800, target_id="jogging"),
        0, ActivityOrigin("action", "decision", "event"), synthetic_body())
    activity.data["effects"] = dict(effects)
    return agent, activity


def _candidate(item):
    """@brief 构造真实候选上下文并预演当前环境的人工活动。"""
    from tests.life.test_motivation import _context
    context = _context(agent=_agent(), body=synthetic_body(), lifestyle_items=(item,),
                       activity_location_kinds={}, current_time=0, end_time=3600)
    family = engine_affordances().families["lifestyle"]
    return _lifestyle_candidate(context, item, family, family.generator_params)


def test_same_activity_identity_uses_each_external_environments_effects(tmp_path: Path) -> None:
    """@brief 同名活动的候选预演和实际需要缓解分别由两个外部环境决定。"""
    mild = _external_item(tmp_path / "mild", _EFFECTS)
    strong = _external_item(tmp_path / "strong", {"entertainment": 0.8, "social": 0.6, "health": 0.9})
    assert mild.activity_id == strong.activity_id
    mild_candidate, strong_candidate = _candidate(mild), _candidate(strong)
    assert mild_candidate is not None and strong_candidate is not None
    assert mild_candidate.entertainment_delta == pytest.approx(-0.05)
    assert strong_candidate.entertainment_delta == pytest.approx(-0.2)
    outcomes = []
    for item in (mild, strong):
        agent, activity = _running(item.effects)
        advance = advance_activity(activity, agent, 1800, synthetic_body())
        outcomes.append(advance.agent.body)
    assert outcomes[0].entertainment_need == pytest.approx(0.75)
    assert outcomes[1].entertainment_need == pytest.approx(0.6)
    assert outcomes[0].social_need == pytest.approx(0.575)
    assert outcomes[1].social_need == pytest.approx(0.45)
    assert outcomes[1].mood_valence > outcomes[0].mood_valence > 0
    last_flows()


def test_lifestyle_effects_are_immutable_and_detached_from_input(tmp_path: Path) -> None:
    """@brief 目录中的效果不可修改，来源容器变化也不影响已加载条目。"""
    effects = dict(_EFFECTS)
    item = replace(_external_item(tmp_path, effects), effects=effects)
    with pytest.raises(TypeError):
        item.effects["entertainment"] = 1.0
    effects["entertainment"] = 1.0
    assert item.effects["entertainment"] == 0.2


@pytest.mark.parametrize("effects", [None, {}, {"entertainment": 0.2},
                                      {**_EFFECTS, "health": float("nan")},
                                      {**_EFFECTS, "social": True}, {**_EFFECTS, "social": -0.1}])
def test_invalid_environment_effects_fail_with_error_log(tmp_path: Path, caplog, effects) -> None:
    """@brief 环境效果缺键或取值无效时明确失败并记录英文错误。"""
    with pytest.raises(IntegrityError, match="effects are missing or invalid"):
        _external_item(tmp_path, effects)
    assert any(record.levelname == "ERROR" and "Lifestyle effects" in record.message for record in caplog.records)


def test_missing_environment_effects_fail_with_error_log(tmp_path: Path, caplog) -> None:
    """@brief 目录缺失 effects 字段不能被转换为全零效果。"""
    _external_item(tmp_path, _EFFECTS)
    path = tmp_path / "lifestyle/items.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["items"][0]["effects"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IntegrityError, match="row is invalid"):
        load_lifestyle_items(tmp_path, {"lifestyle:jogging": 7.0})
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_serialized_paused_activity_resumes_with_its_frozen_effects(tmp_path: Path) -> None:
    """@brief 暂停活动序列化后恢复，结算仍读启动效果并保留缓解分录。"""
    item = _external_item(tmp_path, _EFFECTS)
    agent, activity = _running(item.effects)
    first = advance_activity(activity, agent, 600, synthetic_body())
    first.agent.next_decision_at = 600
    agent, paused = change_activity(first.agent, first.activity, ChangeActivityRequest(
        activity_id=activity.activity_id, operation="pause"), 600, 300)
    path = tmp_path / "lifestyle/items.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["items"][0]["effects"] = {"entertainment": 1.0, "social": 1.0, "health": 1.0}
    path.write_text(json.dumps(changed), encoding="utf-8")
    restored = ActivityState.model_validate_json(paused.model_dump_json())
    agent, resumed = change_activity(agent, restored, ChangeActivityRequest(
        activity_id=activity.activity_id, operation="resume"), 900, 300)
    last_flows()
    completed = advance_activity(resumed, agent, resumed.planned_end_at, synthetic_body())
    assert completed.completed and completed.activity.data["effects"] == _EFFECTS
    assert completed.agent.body.entertainment_need == pytest.approx(0.75)
    assert completed.agent.body.social_need == pytest.approx(0.575)
    flows = [entry for entry in last_flows() if "entertainment_relief" in entry.cause]
    assert sum(entry.amount for entry in flows) == pytest.approx(1 / 30)


@pytest.mark.parametrize("data", [{}, {"effects": {}}, {"effects": {"entertainment": 0.2}}])
def test_missing_frozen_activity_effects_fail_with_error_log(caplog, data: dict) -> None:
    """@brief 现行活动记录缺冻结效果时失败，不能查询另一环境或默认为零。"""
    agent, activity = _running(_EFFECTS)
    activity.data = data
    with pytest.raises(IntegrityError, match="effects"):
        advance_activity(activity, agent, 600, synthetic_body())
    assert any(record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.parametrize("effects", [
    {**_EFFECTS, "entertainment": True}, {**_EFFECTS, "social": False},
    {**_EFFECTS, "social": -0.1}, {**_EFFECTS, "health": 1.1},
    {**_EFFECTS, "health": float("nan")}, {**_EFFECTS, "entertainment": float("inf")},
    {**_EFFECTS, "social": float("-inf")}, {**_EFFECTS, "health": "0.6"},
    {**_EFFECTS, "extra": 0.1},
], ids=["true", "false", "negative", "above-one", "nan", "infinity", "negative-infinity", "text", "extra-key"])
def test_restored_invalid_activity_effects_fail_with_error_log(caplog, effects: dict) -> None:
    """@brief 实际活动 JSON 恢复后拒绝非法冻结效果，不修改状态修正输入。"""
    agent, activity = _running(_EFFECTS)
    payload = json.loads(activity.model_dump_json())
    payload["data"]["effects"] = effects
    restored = ActivityState.model_validate_json(json.dumps(payload))
    with pytest.raises(IntegrityError, match="effects are missing or invalid"):
        advance_activity(restored, agent, 600, synthetic_body())
    assert any(record.levelname == "ERROR" and "Lifestyle effects" in record.message for record in caplog.records)


def test_relief_conversion_uses_the_supplied_effects() -> None:
    """@brief 显式环境效果转换成三个既有身体通道的结算量。"""
    assert lifestyle_relief_rates(_EFFECTS) == pytest.approx((0.1, 0.05, 0.06))
