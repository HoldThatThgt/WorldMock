"""观察合同（affordance_contract）冻结块：形状与注册表一一对应、场景运行期不变、agent 侧派生。"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.society import DirectToolCall

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from custom.agents.life_agent import LifeAgent
from tests.life.conftest import advance_next, context
from worldmock.catalog.affordances import default_affordances
from worldmock.life.engine import affordance_contract
from worldmock.life.models import REQUEST_MODELS, StartBasicActivityRequest

#: 合同条目的七键闭集（与引擎 AFFORDANCE_CONTRACT_KEYS 同形）。
_CONTRACT_KEYS = {
    "surface", "target_discovery", "target_field", "energy_projection",
    "feedback_eligible", "continuable", "game_boundary"}


def test_contract_shape_matches_registry_one_to_one() -> None:
    """合同条目与注册表 kinds 一一对应（声明序），七键逐字段与视图声明相等。"""
    view = default_affordances()
    contract = affordance_contract(view)

    assert list(contract) == list(view.kinds)
    assert len(contract) == 10
    for name, spec in view.kinds.items():
        entry = contract[name]
        assert set(entry) == _CONTRACT_KEYS
        assert entry["surface"] == spec.surface
        assert entry["target_discovery"] == spec.target_discovery
        if spec.target_discovery == "catalog_field":
            assert entry["target_field"] == [spec.target_field, spec.target_id_key]
        else:
            assert entry["target_field"] is None
        assert entry["energy_projection"] == spec.energy_projection
        assert entry["feedback_eligible"] == (name in view.feedback_kinds)
        assert entry["continuable"] == (name in view.continuable_kinds)
        assert entry["game_boundary"] == (name in view.game_boundary_kinds)


def test_contract_pins_current_registry_behavior() -> None:
    """M0/M1 行为期望在合同里逐 kind 复现（continuable 七键口径不含 game）。"""
    contract = affordance_contract(default_affordances())

    assert contract["play_game"]["surface"] == "game"
    assert contract["play_game"]["continuable"] is False
    assert contract["play_game"]["game_boundary"] is True
    assert contract["work"]["target_field"] == ["tasks", "task_id"]
    assert contract["work"]["energy_projection"] is True
    assert contract["read_mail"]["target_field"] == ["messages", "message_id"]
    assert contract["browse"]["target_field"] == ["browse_targets", None]
    assert contract["watch"]["target_discovery"] == "browse_result"
    assert contract["watch"]["target_field"] is None
    assert contract["travel"]["surface"] == "travel"
    assert contract["travel"]["energy_projection"] is False
    assert contract["lifestyle"]["surface"] == "basic"
    assert contract["lifestyle"]["target_discovery"] == "browse_result"


def test_engine_observation_carries_frozen_contract_across_run(engine_factory) -> None:
    """合同随场景组装一次性生成：动作结算并推进时间后再次观察，值与对象都不变。"""
    engine = engine_factory()
    ctx = context(engine, "contract-first")
    engine.begin_decision_round([ctx])
    first = engine.observe(ctx)
    contract = first["affordance_contract"]

    engine.submit_action(DirectToolCall(
        ctx, "action-contract", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)))
    engine.commit_decision_round("contract-first")
    advance_next(engine)

    ctx_next = context(engine, "contract-second")
    engine.begin_decision_round([ctx_next])
    second = engine.observe(ctx_next)

    assert second["affordance_contract"] == contract
    assert second["affordance_contract"] is contract


def _agent_with_data(data: dict) -> None:
    """用一份手写观察数据按正式入口准备 LifeAgent 决策。"""
    agent = LifeAgent()
    data = {"agent_id": 1, "verified_experiences": [], **data, "interaction_menu": {
        "allowed_tools": sorted(REQUEST_MODELS),
        "allowed_activity_kinds": sorted(default_affordances().kinds),
    }}
    observed = datetime(2026, 1, 5, tzinfo=timezone.utc)
    data["current_time"] = int(observed.timestamp())
    wrapped = [{"turn": 0, "action": "observe", "ok": True, "data": data}]
    agent.prepare_react_loop(ReactLoopRequest(0, observed, wrapped))


def test_agent_fails_closed_without_contract() -> None:
    """观察缺合同即拒绝（fail-closed）。"""
    with pytest.raises(RuntimeError, match="affordance contract is unavailable"):
        _agent_with_data({"observation_event_id": "observation-current"})


def test_agent_fails_closed_on_malformed_contract_entry() -> None:
    """合同条目缺键即拒绝（七键闭集形状校验）。"""
    contract = affordance_contract(default_affordances())
    broken = {key: dict(value) for key, value in contract.items()}
    broken["work"].pop("energy_projection")
    with pytest.raises(RuntimeError, match="contract keys are invalid"):
        _agent_with_data({"observation_event_id": "observation-current",
                          "affordance_contract": broken})


def test_agent_energy_projection_keys_follow_contract() -> None:
    """能量投影键集必须与合同的 energy_projection 闭集一致（生产者=校验者同源）。"""
    contract = affordance_contract(default_affordances())
    tampered = {key: dict(value) for key, value in contract.items()}
    tampered["browse"]["energy_projection"] = False
    data = {
        "observation_event_id": "observation-current",
        "affordance_contract": tampered,
        "energy_feasibility": {"maximum_duration_seconds_by_kind": {
            "work": 86_400, "watch": 86_400, "play_game": 86_400,
            "read_mail": 86_400, "browse": 86_400}},
    }
    with pytest.raises(RuntimeError, match="energy feasibility kinds are invalid"):
        _agent_with_data(data)
