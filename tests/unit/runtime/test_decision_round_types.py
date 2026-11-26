"""调度状态与屏障并发契约模型：字段、冻结合同、挂起恢复要求与废弃同秒续接类型清除。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pydantic import ValidationError

from worldmock.foundation.serialization import canonical_json
from worldmock.state.decisions import barrier
from worldmock.state.decisions import results as decision_results
from worldmock.state.decisions import round as decision_round
from worldmock.state.decisions import state as decision_state
from worldmock.state.decisions.barrier import (
    AgentWakeupInput,
    BarrierCommitReceipt,
    DaySessionState,
    SandboxOutcome,
    SearchCommitReceipt,
    SearchSessionOutcome,
    SearchTaskSpec,
    StateModel,
    SuspendedLoopState,
    ToolDispatchResult,
)
from worldmock.state.decisions.day_session import FIVE_LAYER_KEYS
from worldmock.state.decisions.round import SchedulingState
from worldmock.state.world import agent as world_agent
from worldmock.state.world import kinds as world_kinds
from worldmock.state.world.agent import AgentState, BodyState
from worldmock.state.world.world import WORLD_SCHEMA_VERSION, WorldState

_NOW = datetime(2026, 1, 5, tzinfo=timezone.utc)
_SHA = "a" * 64


def _state(**updates) -> SchedulingState:
    """按给定字段覆盖构造一份调度状态。"""
    values = {"current_time": _NOW, "ready_agent_ids": (1,), "next_event_at": None, "world_state_version": 0}
    values.update(updates)
    return SchedulingState(**values)


def _suspended() -> SuspendedLoopState:
    return SuspendedLoopState(
        wakeup_id="wake-1",
        action_id="act-1",
        activity_id="activity-1",
        pending_tool_call_id="call-search-1",
        turn_used=2,
        memory_search_count=1,
        accepted_action_ids=("act-1",),
        pending_memories=(),
        verified_experiences=(),
        local_rejection_counts={"start_main_activity": 0},
        observation_event_id="evt-obs-1",
        observation_parts={"locations": "sha-1"},
        suspended_at=100,
    )


def _day_session(**updates) -> DaySessionState:
    values = {
        "local_date": "2026-01-05",
        "system_prompt": '{"layer":"系统层"}',
        "tools_digest": _SHA,
        "messages": (
            {"role": "system", "content": '{"layer":"系统层"}'},
            {"role": "user", "content": '{"layer":"环境层"}'},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "continue_activity", "arguments": "{}"}}],
            },
        ),
        "pending_boundary_calls": ("call-1",),
        "last_observed_layers": {"environment_layer": {"observed_at": "2026-01-05T08:00:00+08:00"}},
        "loaded_target_keys": (("location", "home"),),
        "compaction_count": 0,
        "compacted_through_seq": 0,
        "last_prompt_tokens": 1200,
    }
    values.update(updates)
    return DaySessionState(**values)


def _world_state(day_session: DaySessionState | None = None) -> WorldState:
    agent = AgentState(
        agent_id=1,
        name="林舟",
        location_id="home",
        balance_account_id="agent:1",
        body=BodyState(
            energy_kcal=1800.0,
            sleep_pressure=0.2,
            sleep_deficit_hours=0.0,
            sleep_need_hours=8.0,
            chronotype_midsleep=3.5,
            social_need=0.3,
            relaxation_need=0.2,
            social_need_growth_per_hour=0.05,
        ),
        body_settled_at=0,
        next_decision_at=0,
        day_session=day_session,
    )
    return WorldState(
        schema_version=WORLD_SCHEMA_VERSION,
        version=0,
        current_time=0,
        end_time=3600,
        run_id="run-1",
        timezone="Asia/Shanghai",
        persona_sha256s={1: _SHA},
        config_sha256=_SHA,
        agents={1: agent},
        accounts={"agent:1": 5000, "external:scenario": -5000},
        random_seed=7,
        random_state='{"v":1}',
    )


def test_round_module_and_retired_continuation_types_are_clean() -> None:
    """round 模块只导出 SchedulingState；同秒续接与旧决策轮类型已彻底删除。"""
    assert decision_round.__all__ == ["SchedulingState"]
    for name in ("DecisionContext", "DirectToolCall", "SocietyCheckpointRequest",
                 "AgentStepBatchRequest", "AgentCreateBatchRequest"):
        assert not hasattr(decision_round, name), name
    assert not hasattr(decision_results, "ActionReceipt")
    for name in ("PendingAction", "DecisionRoundState", "SameMomentPresentation"):
        assert not hasattr(decision_state, name), name
    assert not hasattr(world_kinds, "SameMomentStage")
    assert not hasattr(world_agent, "SameMomentStage")
    assert world_kinds.ActivityKind.SEARCH == "search"


def test_scheduling_state_requires_future_boundary_and_unique_agents() -> None:
    state = SchedulingState(_NOW, (1, 2), _NOW.replace(hour=1), 4)
    assert state.ready_agent_ids == (1, 2)
    with pytest.raises((AttributeError, TypeError)):
        state.world_state_version = 5  # type: ignore[misc]


@pytest.mark.parametrize("build,text", [
    pytest.param(lambda: _state(current_time="2026-01-05T00:00:00+00:00"), "current_time must be a datetime",
                 id="state-text-time"),
    pytest.param(lambda: _state(current_time=datetime(2026, 1, 5)), "current_time must be timezone-aware",
                 id="state-naive-time"),
    pytest.param(lambda: _state(next_event_at=1767571200), "next_event_at must be a datetime", id="state-number-next"),
    pytest.param(lambda: _state(next_event_at=datetime(2026, 1, 6)), "next_event_at must be timezone-aware",
                 id="state-naive-next"),
    pytest.param(lambda: _state(next_event_at=_NOW), "next_event_at must be later than current_time",
                 id="state-same-next"),
    pytest.param(lambda: _state(ready_agent_ids=(2, 2)), "ready_agent_ids must be unique", id="state-duplicate"),
    pytest.param(lambda: _state(ready_agent_ids=(1, "2")), "ready_agent_ids must contain integers",
                 id="state-text-agent"),
    pytest.param(lambda: _state(ready_agent_ids=(False,)), "ready_agent_ids must contain integers",
                 id="state-boolean-agent"),
    pytest.param(lambda: _state(world_state_version=-2), "world_state_version must be a non-negative integer",
                 id="state-negative-version"),
])
def test_scheduling_state_rejects_each_invalid_field_with_its_text(build, text) -> None:
    """调度状态对每一条不合法字段以原文拒绝，不修正、不补缺省。"""
    with pytest.raises(ValueError) as refused:
        build()
    assert str(refused.value) == text


def test_barrier_dtos_and_resume_validation() -> None:
    """屏障、沙盒与搜索会话边界载荷全部冻结且校验严格，退役字段彻底删除。"""
    assert set(barrier.__all__) == {
        "AgentWakeupInput", "BarrierCommitReceipt", "DaySessionState", "SandboxOutcome", "SearchCommitReceipt",
        "SearchSessionOutcome", "SearchTaskSpec", "StateModel", "SuspendedLoopState", "ToolDispatchResult",
    }
    assert WORLD_SCHEMA_VERSION == 18
    assert "presentation_state" not in WorldState.model_fields
    assert "pending_presentation" not in AgentWakeupInput.model_fields
    assert "pending_presentation" not in SandboxOutcome.model_fields
    assert not {"base_layers", "messages"} & set(SuspendedLoopState.model_fields)

    suspended = _suspended()
    session = _day_session()
    assert suspended.pending_tool_call_id == "call-search-1"
    regular = AgentWakeupInput(
        wakeup_id="w-1", run_id="run-1", agent_id=1, observed_at=0, local_date="2026-01-05",
        world_state_version=0, mode="regular", observation={"ok": True}, action_seq_start=0,
        day_session=session, boundary_delta_payload={"ok": True},
        settled_day_events=({"event_seq": 1, "kind": "action_result"},),
    )
    assert issubclass(AgentWakeupInput, StateModel)
    assert regular.suspended_loop is None
    assert regular.day_session == session

    with pytest.raises(ValueError, match="resume_search wakeup requires suspended_loop"):
        AgentWakeupInput(
            wakeup_id="w-2", run_id="run-1", agent_id=1, observed_at=100, local_date="2026-01-05",
            world_state_version=1, mode="resume_search", observation={}, action_seq_start=1,
        )

    resumed = AgentWakeupInput(
        wakeup_id="w-2", run_id="run-1", agent_id=1, observed_at=300, local_date="2026-01-05",
        world_state_version=1, mode="resume_search", observation={"ok": True}, action_seq_start=2,
        suspended_loop=suspended, search_result_payload={"goods_ids": ["g-1"]},
    )
    assert resumed.suspended_loop == suspended

    dispatch = ToolDispatchResult(accepted=True, terminal_exit=False, suspended=True,
                                  tool_message_payload={"status": "suspended"}, activity_id="activity-1")
    outcome = SandboxOutcome(
        wakeup_id="w-1", agent_id=1, status="suspended", state=_world_state(session),
        day_session=session, compaction_records=({"compaction_count": 1},),
        observation_event_id="evt-obs-1",
    )
    spec = SearchTaskSpec(
        action_id="act-1", agent_id=1, activity_id="activity-1", origin_wakeup_id="w-1",
        query="无糖乌龙茶", current_location_id="home", location_name="家",
        opened_at_clock="08:05", local_date="2026-01-05", weather_label="晴",
        open_apps=("meituan",), category_overview={"饮料": 12}, turn_budget=6,
    )
    session_out = SearchSessionOutcome(
        action_id="act-1", agent_id=1, activity_id="activity-1",
        goods_ids=("g-1",), place_ids=(), summary="找到无糖乌龙茶两款。",
    )
    search_receipt = SearchCommitReceipt(completed_count=1, activity_ids=("activity-1",), event_seq_last=4)
    barrier_receipt = BarrierCommitReceipt(
        committed_agent_ids=(1,), conflict_requeued_agent_ids=(), world_state_version=1, event_seq_last=6,
    )
    assert (dispatch.suspended, dispatch.activity_id, outcome.state.schema_version, spec.turn_budget,
            session_out.goods_ids, search_receipt.completed_count, barrier_receipt.world_state_version) == (
        True, "activity-1", WORLD_SCHEMA_VERSION, 6, ("g-1",), 1, 1)


def test_day_session_state_invariants_and_world_json_roundtrip() -> None:
    """DaySessionState 强制消息角色前缀、无第二条 user、挂起 ID 配对与五层基准键集，并支持 WorldState JSON 无损往返。"""
    assert FIVE_LAYER_KEYS == frozenset({
        "tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer",
    })
    session = _day_session(
        messages=(
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "init"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c-main", "type": "function", "function": {"name": "start_main_activity", "arguments": "{}"}},
                    {"id": "c-side", "type": "function", "function": {"name": "start_side_activity", "arguments": "{}"}},
                ],
            },
        ),
        pending_boundary_calls=("c-main", "c-side"),
        loaded_target_keys=(("location", "home"), ("app", "bilibili"), ("product", "tea")),
    )
    world = _world_state(session)
    dumped = world.model_dump(mode="json")
    assert canonical_json(dumped)
    restored = WorldState.model_validate(dumped)
    assert restored.agents[1].day_session == session

    for kwargs in (
        {"local_date": "2026-02-30"},
        {"tools_digest": "short"},
        {"messages": ({"role": "system", "content": "sys"},)},
        {"messages": ({"role": "user", "content": "u"}, {"role": "user", "content": "u2"})},
        {"messages": ({"role": "system", "content": "s"}, {"role": "assistant", "content": "a"})},
        {
            "messages": (
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
                {"role": "user", "content": "second user"},
            ),
            "pending_boundary_calls": (),
        },
        {"pending_boundary_calls": (" ",)},
        {"pending_boundary_calls": ("call-1", "call-1")},
        {"pending_boundary_calls": ("missing-id",)},
        {
            "messages": ({"role": "system", "content": "s"}, {"role": "user", "content": "u"}),
            "pending_boundary_calls": ("call-1",),
        },
        {"last_observed_layers": {"unknown_layer": {}}},
        {"loaded_target_keys": (("activity", "act-1"),)},
        {"loaded_target_keys": (("location", " "),)},
        {"compaction_count": -1},
        {"compacted_through_seq": -1},
        {"last_prompt_tokens": -1},
    ):
        with pytest.raises((ValueError, ValidationError)):
            _day_session(**kwargs)
