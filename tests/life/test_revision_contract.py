"""人物决策节律、当日计划与公开观察的当前合同回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall
from tests.life.conftest import (
    StubPlanDayClient, advance_next, advance_until_ready, conclude_pending, context, plan_day_response,
    settle_day_plans, start_activity,
)
from worldmock.errors import ProtocolError
from worldmock.life import projection
from worldmock.life.models import PlanDayRequest, PlanGoalInput, ReviseDayPlanRequest, WorldState
from worldmock.life.narration import FORBIDDEN_OBSERVATION_KEYS


def _act(engine, round_id: str, tool_name: str, request):
    """单人物真实轮：先冻结观察，再登记并结算。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def _observe_current(engine, round_id: str) -> dict:
    """在当前世界做一次只读观察。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    data = engine.observe(decision)
    engine.abort_decision_round(round_id, "test observation")
    return data


def _commit_staged(engine, staged) -> None:
    """把隔离状态改动写入事件存储并换入引擎。"""
    from worldmock.footprints.models import EventDraft

    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_state_staged"},
    )])
    engine.state = staged


def _events(engine, event_type: str):
    """读取本人的已提交证据事件。"""
    return [event for event in engine.store.read_events(engine.store.last_event_seq)
            if event.event_type == event_type and event.agent_id == 1]


def _goal_plan() -> PlanDayRequest:
    """当日目标采用可读意图和自然语言正文，不设提醒。"""
    return PlanDayRequest(goals=(PlanGoalInput(intent_kind="工作", goal="推进报告", remind_time=None),))


def test_silent_sleep_boundary_settles_without_decision_events(engine_factory) -> None:
    """长睡眠经过身体结算边界时仍不调用人物模型。"""
    engine = engine_factory()
    result = _act(engine, "sleep-long", "start_activity", start_activity("睡觉", "自己"))
    assert result.status.value == "in_progress"
    started_at = engine.state.current_time
    advance_next(engine)
    assert engine.state.current_time < engine.state.activities[result.activity_id].planned_end_at
    assert engine.scheduling_state().ready_agent_ids == ()
    body = [event for event in _events(engine, "environment_event")
            if event.data.get("kind") == "body_settlement"]
    assert body[-1].data["decision_offered"] is False
    assert body[-1].data["wake_reasons"] == []
    assert any(event.occurred_at > started_at for event in _events(engine, "activity_progress"))
    assert all(event.occurred_at <= started_at
               for event_type in ("decision_started", "observation", "model_request")
               for event in _events(engine, event_type))


def test_wait_end_offers_decision_with_actual_wake_reason(engine_factory) -> None:
    """等待长度由场景间隔推导，结束边界记录计划完成唤醒。"""
    engine = engine_factory()
    result = _act(engine, "wait", "start_activity", start_activity("等待", "自己"))
    activity = engine.state.activities[result.activity_id]
    assert activity.planned_duration_seconds == engine.scenario.activity_feedback_seconds
    advance_next(engine)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    body = [event for event in _events(engine, "environment_event")
            if event.data.get("kind") == "body_settlement"]
    assert body[-1].data["decision_offered"] is True
    assert "planned_end" in body[-1].data["wake_reasons"]


def test_sleep_is_not_interrupted_by_reconsideration(engine_factory) -> None:
    """睡眠经过重估时刻仍只在实际计划结束后唤醒。"""
    engine = engine_factory()
    result = _act(engine, "sleep", "start_activity", start_activity("睡觉", "自己"))
    activity_id = result.activity_id
    planned_end = engine.state.activities[activity_id].planned_end_at
    while engine.state.current_time < planned_end:
        advance_next(engine)
        if engine.state.current_time < planned_end:
            assert engine.scheduling_state().ready_agent_ids == ()
    assert advance_until_ready(engine)
    assert engine.state.activities[activity_id].status.value == "completed"
    assert "planned_end" in engine.state.agents[1].pending_wake_reasons


def test_public_observation_excludes_private_fields_and_supply_expires(engine_factory) -> None:
    """公开路线只含语义字段，商品在浏览可见期结束后消失。"""
    engine = engine_factory()
    data = _observe_current(engine, "observe-clean")
    for key in FORBIDDEN_OBSERVATION_KEYS:
        assert key not in data["scene"] and key not in data["facts"]
    assert "end_time" not in data and "world_state_version" not in data
    for route in data["facts"]["routes"]:
        assert set(route) <= {
            "destination", "mode", "minutes", "fare_fen", "effort", "feasible", "status", "reason",
        }
        assert route["effort"] in {"轻松", "一般", "费力"}
    browse = _act(engine, "browse", "start_activity", start_activity("浏览", "商品目录"))
    assert browse.status.value == "in_progress"
    assert advance_until_ready(engine)
    agent = engine.state.agents[1]
    staged = engine.state.model_copy(deep=True)
    staged.current_time = max(agent.supply_browsed_at.values()) + 1801
    staged.agents[1] = staged.agents[1].model_copy(update={"body_settled_at": staged.current_time}, deep=True)
    _commit_staged(engine, staged)
    assert projection.visible_supplies(engine.state, engine.state.agents[1], engine.scenario) == []


def test_work_material_reveal_remains_bounded_per_decision(engine_factory) -> None:
    """工作材料仅揭示新读区间且每任务有语义上限。"""
    engine = engine_factory()
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={
        "material_reveals": [
            {"task_id": "task-a", "at": 200, "start_char": 0, "end_char": 1500, "chars": 1500},
            {"task_id": "task-a", "at": 300, "start_char": 1500, "end_char": 3400, "chars": 1900},
            {"task_id": "task-b", "at": 100, "start_char": 0, "end_char": 900, "chars": 900},
        ],
        "last_decision_time": 150,
    }, deep=True)
    ranges = projection.new_material_ranges(staged.agents[1])
    assert sum(row["chars"] for row in ranges["task-a"]) == 2000
    assert not ranges.get("task-b")


def test_plan_day_registration_and_retry_after_rejection(engine_factory) -> None:
    """零点计划输出超长目标时附回错误重试后登记，同日不再登记（系统入口重复登记是协议错误）；人物的非法修订以
    业务拒绝反馈，修正后受理（第四章「输入、调用、失败与记录」「计划与提醒」）。"""
    engine = engine_factory()
    too_long = plan_day_response([{"intent_kind": "工作", "goal": "字" * 301, "remind_time": None}])
    client = StubPlanDayClient(goals=[{"intent_kind": "工作", "goal": "推进报告", "remind_time": None}],
                               responses=[too_long])
    assert settle_day_plans(engine, client)[0]["goals"] == 1
    errors = [event.data["error"] for event in _events(engine, "model_response")
              if event.data.get("purpose") == "plan_day"]
    assert len(errors) == 2 and "exceeds 300 characters" in errors[0] and errors[1] is None
    plan = engine.state.agents[1].day_plan
    assert [goal.goal for goal in plan.goals] == ["推进报告"]
    with pytest.raises(ProtocolError, match="already generated"):
        engine.register_day_plan(1, _goal_plan(), [], [])
    invalid = ReviseDayPlanRequest.model_construct(goals=(
        PlanGoalInput.model_construct(intent_kind="工作", goal="字" * 301, remind_time=None),))
    rejected = _act(engine, "invalid-plan", "revise_day_plan", invalid)
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "ACTION_REQUEST_INVALID"
    # 业务拒绝后在同一秒再决策（时间与能量规格第一章规则 2），进度层即公开拒绝结果。
    assert engine.state.agents[1].next_decision_at == engine.state.current_time
    data = _observe_current(engine, "rejection-result")
    rows = data["facts"]["progress"]["results"]
    assert rows[-1]["error_code"] == "ACTION_REQUEST_INVALID"
    assert rows[-1]["occurred_at"] == rejected.occurred_at
    assert "action_id" not in rows[-1]
    assert engine.state.agents[1].day_plan == plan
    fixed = ReviseDayPlanRequest(goals=(PlanGoalInput(intent_kind="工作", goal="写完报告", remind_time=None),))
    assert _act(engine, "fixed-plan", "revise_day_plan", fixed).status.value == "completed"
    assert [goal.goal for goal in engine.state.agents[1].day_plan.goals] == ["写完报告"]


def test_revise_day_plan_preserves_progressed_goal(engine_factory) -> None:
    """推进过的当日目标无法改写，待办目标仍可追加。"""
    engine = engine_factory()
    client = StubPlanDayClient(goals=[{"intent_kind": "休息", "goal": "歇一歇", "remind_time": None}])
    assert settle_day_plans(engine, client)[0]["goals"] == 1
    assert advance_until_ready(engine)
    _act(engine, "rest", "start_activity", start_activity("等待", "自己"))
    assert advance_until_ready(engine)
    # 等待按计划结束即成待收尾，当秒收尾后再修订（规格第七章「活动一结束就要结束它」）。
    assert conclude_pending(engine, "rest-end").status == "completed"
    original = engine.state.agents[1].day_plan.goals[0]
    rejected = _act(engine, "rewrite", "revise_day_plan", ReviseDayPlanRequest(
        goals=(PlanGoalInput(intent_kind="休息", goal="改写休息", remind_time=None),)))
    assert rejected.error_code == "PLAN_GOAL_ALREADY_PROGRESSED"
    assert engine.state.agents[1].next_decision_at == engine.state.current_time
    result = _act(engine, "append", "revise_day_plan", ReviseDayPlanRequest(
        goals=(PlanGoalInput(intent_kind="工作", goal="写报告", remind_time=None),)))
    assert result.status.value == "completed"
    assert engine.state.agents[1].day_plan.goals[0] == original
    assert engine.state.agents[1].day_plan.goals[1].intent_kind == "工作"


def test_world_state_rejects_old_checkpoint_shapes(engine_factory) -> None:
    """结构版本字段是必需的，旧版本检查点不能加载。"""
    engine = engine_factory()
    missing = engine.state.model_dump(mode="json")
    missing.pop("schema_version")
    with pytest.raises(Exception, match="schema_version"):
        WorldState.model_validate(missing)
    previous = engine.state.model_dump(mode="json")
    previous["schema_version"] = 4
    with pytest.raises(Exception, match="schema_version"):
        WorldState.model_validate(previous)


def test_removed_sleep_debt_field_has_no_runtime_references() -> None:
    """删除的身体状态字段不得重新进入生产运行时。"""
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "src/worldmock/life/models.py", "src/worldmock/life/body.py",
        "src/worldmock/life/engine.py",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        code = [line for line in text.splitlines()
                if not line.lstrip().startswith("#")
                and "CATALOG_SLEEP_RESOURCE" not in line
                and "initial_sleep_debt_hours" not in line]
        assert not any("sleep_debt_hours" in line for line in code)
