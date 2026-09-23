"""引擎内部可供性合同与注册表一致。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.catalog.affordances import default_affordances
from worldmock.life.engine import affordance_contract
from worldmock.life.models import StartActivityRequest


def test_contract_shape_matches_registry_one_to_one() -> None:
    """@brief 内部冻结合同只保留动作、发现、能量、反馈和游戏边界。"""
    view = default_affordances()
    contract = affordance_contract(view)
    assert list(contract) == list(view.kinds)
    for name, spec in view.kinds.items():
        entry = contract[name]
        assert set(entry) == {"action", "target_discovery", "target_field",
                              "energy_projection", "feedback_eligible", "game_boundary"}
        assert entry["action"] == spec.action
        assert entry["target_discovery"] == spec.target_discovery
        assert entry["energy_projection"] == spec.energy_projection
        assert entry["feedback_eligible"] == (name in view.feedback_kinds)
        assert entry["game_boundary"] == (name in view.game_boundary_kinds)


def test_contract_pins_current_registry_behavior() -> None:
    """@brief 关键活动从注册表派生的动作与目标字段保持确定。"""
    contract = affordance_contract(default_affordances())
    assert contract["work"]["target_field"] == ["tasks", "task_id"]
    assert contract["read_mail"]["target_field"] == ["messages", "message_id"]
    assert contract["play_game"]["game_boundary"] is True
    assert contract["watch"]["target_discovery"] == "browse_result"


def test_engine_contract_remains_frozen_across_decisions(engine_factory) -> None:
    """@brief 一次动作结算和时钟推进不改写场景可供性声明。"""
    engine = engine_factory()
    first_context = context(engine, "contract-first")
    engine.begin_decision_round([first_context])
    first = engine.observe(first_context)
    contract = first["affordance_contract"]

    request = StartActivityRequest(targets=["自己"], actions=["等待"])
    engine.submit_action(DirectToolCall(first_context, "contract-wait", "start_activity", request))
    engine.commit_decision_round("contract-first")
    advance_next(engine)

    next_context = context(engine, "contract-second")
    engine.begin_decision_round([next_context])
    second = engine.observe(next_context)
    assert second["affordance_contract"] is contract
