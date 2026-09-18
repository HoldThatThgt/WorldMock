"""真实身体状态、观察投影、工具闭集与暂停恢复的能量边界测试。"""

from __future__ import annotations

import pytest
from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.society import DirectToolCall

from custom.agents.life_agent import LifeAgent
from tests.life.conftest import advance_until_ready, advance_next, context, travel_quote, stage_day_plan
from worldmock.footprints.models import EventDraft
from worldmock.life.body import minimum_energy_cost
from worldmock.life.models import (
    ConsumeItem,
    ActivityKind,
    ConsumeRequest,
    ContinueActivityRequest,
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
)


def _commit_test_state(engine, state, kind: str) -> None:
    """提交只用于边界测试的明确状态变化。"""
    state.version = engine.state.version + 1
    event = EventDraft(
        event_type="environment_event",
        occurred_at=state.current_time,
        visibility="system",
        source_kind="scenario",
        data={"kind": kind},
    )
    engine.store.commit(state.model_dump(mode="json"), [event])
    engine.state = state


def _set_energy(engine, energy_kcal: float) -> None:
    """把人物能量放到一个明确测试边界且保持当前时刻可决策。"""
    state = engine.state.model_copy(deep=True)
    agent = state.agents[1]
    body = agent.body.model_copy(update={"energy_kcal": energy_kcal}, deep=True)
    state.agents[1] = agent.model_copy(
        update={"body": body, "next_decision_at": state.current_time}, deep=True)
    _commit_test_state(engine, state, "test_energy_position")


def _observed_action(engine, round_id: str, tool_name: str, request):
    """按真实观察、提交与共同轮结算顺序执行一个动作。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observation = engine.observe(decision)
    call = DirectToolCall(decision, f"action-{round_id}", tool_name, request)
    engine.submit_action(call)
    return observation, engine.commit_decision_round(round_id)[0]


def _prepare_agent(observation: dict, observed_at) -> LifeAgent:
    """使用真实环境观察准备一次 LifeAgent 决策。"""
    wrapped = [{"turn": 0, "action": "observe", "ok": True, "data": observation}]
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, observed_at, wrapped))
    return agent


def _tool_names(agent: LifeAgent) -> set[str]:
    """返回本轮实际模型请求能够携带的工具名。"""
    return {item["function"]["name"] for item in agent.build_react_tool_schemas(readonly=False)}


def test_zero_energy_real_observation_removes_non_recovery_actions(engine_factory) -> None:
    """真实零能量世界投影撤下全部耗能目标和路线，同时保留基础恢复动作。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    decision = context(engine, "zero-energy-observation")
    engine.begin_decision_round([decision])
    observation = engine.observe(decision)
    agent = _prepare_agent(observation, decision.observed_at)

    maximums = observation["energy_feasibility"]["maximum_duration_seconds_by_kind"]
    assert set(maximums.values()) == {0}
    assert observation["routes"] and all(not route["energy_feasible"] for route in observation["routes"])
    assert "start_targeted_activity" not in _tool_names(agent)
    assert "start_travel" not in _tool_names(agent)
    assert "start_basic_activity" in _tool_names(agent)


def test_real_route_projection_uses_mode_specific_met_cost(engine_factory) -> None:
    """同一起终点的驾车与步行路线使用各自代谢当量形成不同可行性。"""
    engine = engine_factory()
    routes = [route for route in engine.scenario.routes
              if route.origin_id == "home" and route.destination_id == "office"]
    # 路线能量预算按出发时刻受理口径时长（travel-time 分时系数折算）计算。
    costs = {
        route.mode.value: minimum_energy_cost(
            ActivityKind.TRAVEL,
            travel_quote(engine, route.mode.value, route.duration_seconds),
            engine.scenario.body,
            engine.scenario.body.activity_met[f"travel:{route.mode.value}"],
        )
        for route in routes
    }
    assert costs["walking"] > costs["driving"]
    _set_energy(engine, (costs["walking"] + costs["driving"]) / 2)
    observation, _ = _observed_action(
        engine,
        "route-energy",
        "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60),
    )
    visible = {route["mode"]: route for route in observation["routes"]
               if route["destination_id"] == "office"}

    assert visible["driving"]["minimum_energy_cost_kcal"] == costs["driving"]
    assert visible["walking"]["minimum_energy_cost_kcal"] == costs["walking"]
    assert visible["driving"]["energy_feasible"] is True
    assert visible["walking"]["energy_feasible"] is False


def test_watch_energy_limit_uses_wall_clock_for_fast_playback(engine_factory) -> None:
    """观看能量边界按墙钟秒计算，二倍速不会把受理成本翻倍。"""
    engine = engine_factory()
    energy = minimum_energy_cost(ActivityKind.WATCH, 60, engine.scenario.body)
    _set_energy(engine, energy)
    decision = context(engine, "watch-wall-clock")
    engine.begin_decision_round([decision])
    observation = engine.observe(decision)
    content_id = engine.scenario.contents[0].item_id
    request = StartTargetedActivityRequest(
        activity_type="watch",
        planned_duration_seconds=60,
        target_id=content_id,
        playback_speed=2,
    )
    engine.submit_action(DirectToolCall(
        decision, "action-watch-wall-clock", "start_targeted_activity", request))
    result = engine.commit_decision_round("watch-wall-clock")[0]

    maximums = observation["energy_feasibility"]["maximum_duration_seconds_by_kind"]
    assert maximums["watch"] == 60
    assert result.status == "in_progress"


def test_start_one_second_beyond_energy_limit_is_rejected(engine_factory) -> None:
    """领域受理继续以完整成本拒绝比公开上限多一秒的活动。"""
    engine = engine_factory()
    energy = minimum_energy_cost(ActivityKind.BROWSE, 60, engine.scenario.body)
    _set_energy(engine, energy)

    observation, result = _observed_action(
        engine,
        "browse-one-second-over",
        "start_targeted_activity",
        StartTargetedActivityRequest(
            activity_type="browse",
            planned_duration_seconds=61,
            target_id="product_catalog",
            playback_speed=1,
        ),
    )

    maximums = observation["energy_feasibility"]["maximum_duration_seconds_by_kind"]
    assert maximums["browse"] == 60
    assert result.status == "rejected" and result.error_code == "INSUFFICIENT_ENERGY"
    assert engine.state.agents[1].current_activity_id is None


@pytest.mark.parametrize("duration", [901, 902, 1801])
def test_exact_energy_activity_completes_across_real_feedback(engine_factory, duration: int) -> None:
    """真实引擎按九百秒反馈多段结算时，精确总能量可完成活动。"""
    engine = engine_factory()
    energy = minimum_energy_cost(ActivityKind.BROWSE, duration, engine.scenario.body)
    _set_energy(engine, energy)
    _, result = _observed_action(
        engine,
        f"browse-exact-{duration}",
        "start_targeted_activity",
        StartTargetedActivityRequest(
            activity_type="browse",
            planned_duration_seconds=duration,
            target_id="product_catalog",
            playback_speed=1,
        ),
    )
    activity_id = result.activity_id

    while engine.state.activities[activity_id].status == "active":
        advance_until_ready(engine)  # 重估唤醒点继续；静默边界照常结算
        if engine.state.activities[activity_id].status == "active":
            _observed_action(
                engine,
                f"continue-exact-{duration}-{engine.state.current_time}",
                "continue_activity",
                ContinueActivityRequest(activity_id=activity_id),
            )

    assert engine.state.activities[activity_id].elapsed_seconds == duration
    assert engine.state.activities[activity_id].status == "completed"
    assert 0 <= engine.state.agents[1].body.energy_kcal <= 1e-9


@pytest.mark.parametrize("kind", ["wait", "sleep"])
def test_zero_energy_basic_recovery_activity_is_accepted(engine_factory, kind: str) -> None:
    """领域启动边界在零能量时仍接受等待与睡眠。"""
    engine = engine_factory()
    _set_energy(engine, 0)
    _, result = _observed_action(
        engine,
        f"zero-{kind}",
        "start_basic_activity",
        StartBasicActivityRequest(activity_type=kind, planned_duration_seconds=60),
    )
    assert result.status == "in_progress"


def test_zero_energy_consume_restores_targeted_actions_next_observation(engine_factory) -> None:
    """零能量进食按既有例外完成后，下一真实观察重新开放耗能目标。"""
    engine = engine_factory()
    product = engine.scenario.products[0]
    state = engine.state.model_copy(deep=True)
    agent = state.agents[1]
    body = agent.body.model_copy(update={"energy_kcal": 0}, deep=True)
    inventory = {agent.location_id: {product.product_id: 1.0}}
    state.agents[1] = agent.model_copy(
        update={"body": body, "inventory": inventory, "next_decision_at": state.current_time},
        deep=True,
    )
    _commit_test_state(engine, state, "test_zero_energy_food")
    request = ConsumeRequest(
        items=(ConsumeItem(product_id=product.product_id, quantity=1),),
        use_mode="eat",
        duration_seconds=product.use_duration_seconds,
    )
    before, started = _observed_action(engine, "consume-zero", "consume", request)
    assert started.status == "in_progress"
    assert before["body"]["values"]["energy_kcal"] == 0  # 数值组进事件（分级进提示）
    advance_next(engine)
    assert engine.state.agents[1].body.energy_kcal > 0

    decision = context(engine, "after-food")
    engine.begin_decision_round([decision])
    after = engine.observe(decision)
    prepared = _prepare_agent(after, decision.observed_at)
    assert max(after["energy_feasibility"]["maximum_duration_seconds_by_kind"].values()) >= 60
    assert "start_targeted_activity" in _tool_names(prepared)


def test_paused_non_recovery_resume_rejects_but_cancel_succeeds(engine_factory) -> None:
    """暂停活动剩余能耗不足时领域拒绝恢复且保留取消路径。"""
    engine = engine_factory()
    _, started = _observed_action(
        engine,
        "start-browse",
        "start_targeted_activity",
        StartTargetedActivityRequest(
            activity_type="browse",
            planned_duration_seconds=7200,
            target_id="product_catalog",
            playback_speed=1,
        ),
    )
    activity_id = started.activity_id
    advance_until_ready(engine)  # 重估唤醒边界暂停（静默边界不再提供决策）
    _observed_action(
        engine,
        "pause-browse",
        "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="pause"),
    )
    advance_until_ready(engine)
    _set_energy(engine, 0)

    observation, rejected = _observed_action(
        engine,
        "resume-browse",
        "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="resume"),
    )
    paused = observation["paused_activities"][0]
    assert paused["remaining_duration_seconds"] == 7200 - 5400
    assert paused["resume_energy_feasible"] is False
    assert rejected.status == "rejected" and rejected.error_code == "INSUFFICIENT_ENERGY"
    assert engine.state.activities[activity_id].status == "paused"

    advance_until_ready(engine)
    _, cancelled = _observed_action(
        engine,
        "cancel-browse",
        "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="cancel"),
    )
    assert cancelled.status == "completed"
    assert engine.state.activities[activity_id].status == "cancelled"


def test_resume_checks_terminal_status_before_energy(engine_factory) -> None:
    """恢复仍有剩余时长的终态活动保留状态错误，不被能量检查覆盖。"""
    engine = engine_factory()
    _, started = _observed_action(
        engine,
        "start-cancelled-browse",
        "start_targeted_activity",
        StartTargetedActivityRequest(
            activity_type="browse",
            planned_duration_seconds=7200,
            target_id="product_catalog",
            playback_speed=1,
        ),
    )
    activity_id = started.activity_id
    advance_until_ready(engine)
    _observed_action(
        engine,
        "cancel-active-browse",
        "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="cancel"),
    )
    advance_until_ready(engine)
    _set_energy(engine, 0)

    _, rejected = _observed_action(
        engine,
        "resume-cancelled-browse",
        "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="resume"),
    )

    assert rejected.status == "rejected"
    assert rejected.error_code == "ACTIVITY_NOT_PAUSED"
    assert engine.state.activities[activity_id].status == "cancelled"
    assert engine.state.activities[activity_id].elapsed_seconds == 5400


def test_paused_recovery_activity_can_resume_at_zero_energy(engine_factory) -> None:
    """等待活动恢复复用同一恢复种类来源，不被普通能量门错误拒绝。"""
    engine = engine_factory()
    _, started = _observed_action(
        engine,
        "start-wait",
        "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=7200),
    )
    activity_id = started.activity_id
    advance_until_ready(engine)
    _observed_action(
        engine,
        "pause-wait",
        "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="pause"),
    )
    advance_until_ready(engine)
    _set_energy(engine, 0)
    observation, resumed = _observed_action(
        engine,
        "resume-wait",
        "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="resume"),
    )

    assert observation["paused_activities"][0]["resume_energy_feasible"] is True
    assert resumed.status == "completed"
    assert engine.state.activities[activity_id].status == "active"
