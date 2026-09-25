"""可读活动选择在真实身体、观察与共同结算中的能量边界。"""

from __future__ import annotations

import pytest
from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.society import DirectToolCall

from custom.agents.life_agent import LifeAgent
from tests.life.conftest import advance_next, context, stage_day_plan, travel_quote
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.body import minimum_energy_cost
from worldmock.life.models import ActivityKind, PlanDayRequest, StartActivityRequest


def _set_energy(engine, energy_kcal: float) -> None:
    """@brief 将人物能量置于精确测试边界并持久提交。"""
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1]
    body = agent.body.model_copy(update={"energy_kcal": energy_kcal}, deep=True)
    staged.agents[1] = agent.model_copy(
        update={"body": body, "next_decision_at": staged.current_time}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_energy_position"})])
    engine.state = staged


def _observed_action(engine, round_id: str, action: str, object_id: str | None = None):
    """@brief 从冻结观察的私有绑定取可读对象和动作并执行。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    binding = next(row for row in observed["interaction_bindings"]
                   if object_id is None or row["object_id"] == object_id
                   if any(item["canonical_action"] == action for item in row["actions"]))
    label = next(item["label"] for item in binding["actions"]
                 if item["canonical_action"] == action)
    request = StartActivityRequest(targets=[binding["object"]], actions=[label])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return observed, engine.commit_decision_round(round_id)[0]


def test_zero_energy_wait_settles_after_instant_action(engine_factory) -> None:
    """零能量瞬时登记后同一秒再决策，不产生空白区间；随后的等待照常结算清醒身体（时间与能量规格第一章规则 1、2）。"""
    engine = engine_factory()
    _set_energy(engine, 0)
    decision = context(engine, "zero-energy-plan")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(
        decision, "action-zero-energy-plan", "plan_day", PlanDayRequest(goals=())))
    result = engine.commit_decision_round("zero-energy-plan")[0]
    assert result.status == "completed" and not engine.state.activities
    assert engine.scheduling_state().ready_agent_ids == (1,)
    with pytest.raises(ProtocolError, match="decision is due"):
        advance_next(engine)
    _, waited = _observed_action(engine, "zero-energy-wait", "wait")
    assert waited.status == "in_progress", waited.error_code
    before = engine.state.agents[1].body
    advance_next(engine)
    after = engine.state.agents[1].body
    assert after.energy_kcal == 0
    assert after.sleep_pressure > before.sleep_pressure
    assert after.entertainment_need > before.entertainment_need
    events = engine.store.read_agent_events("environment_event", 1)
    settlements = [item for item in events if item.data.get("kind") == "body_settlement"]
    assert settlements and all(item.data["activity_ids"] for item in settlements)
    assert waited.activity_id in settlements[-1].data["activity_ids"]


def test_zero_energy_observation_retains_recovery_choices(engine_factory) -> None:
    """零能量时工具仍是稳定入口，自身恢复动作继续可选。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    decision = context(engine, "zero-energy-observation")
    observed = engine.observe(decision)
    wrapped = [{"turn": 0, "action": "observe", "ok": True, "data": observed}]
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, decision.observed_at, wrapped))
    tool_names = {item["function"]["name"] for item in agent.build_react_tool_schemas(readonly=False)}
    assert "start_activity" in tool_names
    self_row = next(item for item in observed["facts"]["interactive_objects"]
                    if item["object"] == "自己")
    assert {"等待", "睡觉"} <= set(self_row["actions"])
    maximums = observed["energy_feasibility"]["maximum_duration_seconds_by_kind"]
    assert maximums["watch"] == 0
    assert set(maximums.values()) == {0}


def test_route_projection_uses_mode_specific_energy_cost(engine_factory) -> None:
    """同一起终点的步行与驾车按各自冻结代谢当量判断可行性。"""
    engine = engine_factory()
    routes = [item for item in engine.scenario.routes
              if item.origin_id == "home" and item.destination_id == "office"]
    costs = {item.mode.value: minimum_energy_cost(
        ActivityKind.TRAVEL, travel_quote(engine, item.mode.value, item.duration_seconds),
        engine.scenario.body, engine.scenario.body.activity_met[f"travel:{item.mode.value}"])
        for item in routes}
    assert costs["walking"] > costs["driving"]
    _set_energy(engine, (costs["walking"] + costs["driving"]) / 2)
    observed = engine.observe(context(engine, "route-energy"))
    visible = {item["mode"]: item for item in observed["routes"]
               if item["destination_id"] == "office"}
    assert visible["driving"]["energy_feasible"] is True
    assert visible["walking"]["energy_feasible"] is False


@pytest.mark.parametrize("action", ["wait", "sleep"])
def test_zero_energy_recovery_action_is_accepted(engine_factory, action: str) -> None:
    """等待和睡眠使用领域恢复豁免，可在零能量启动。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    _, result = _observed_action(engine, f"zero-{action}", action)
    assert result.status == "in_progress", result.error_code


def test_nonrecovery_action_rejects_when_derived_duration_exceeds_energy(engine_factory) -> None:
    """模型不填时长，系统派生的完整活动仍经过能量预算检查。"""
    engine = engine_factory()
    stage_day_plan(engine)
    duration = engine.scenario.activity_feedback_seconds
    budget = minimum_energy_cost(ActivityKind.BROWSE, duration, engine.scenario.body)
    _set_energy(engine, budget / 2)
    _, result = _observed_action(engine, "browse-low-energy", "browse")
    assert result.status == "rejected" and result.error_code == "INSUFFICIENT_ENERGY"
    assert not engine.state.activities
