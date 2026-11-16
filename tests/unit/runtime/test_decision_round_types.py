"""决策轮三个数据类型的合同：名称、字段次序、校验与英文报错原文。"""

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from worldmock.state.decisions import round as decision_round
from worldmock.state.decisions.round import DecisionContext, DirectToolCall, SchedulingState


def _context(**updates) -> DecisionContext:
    values = {
        "run_id": "run-a",
        "round_id": "round-a",
        "decision_id": "decision-a",
        "agent_id": 7,
        "observed_at": datetime(2026, 1, 5, tzinfo=timezone.utc),
        "world_state_version": 3,
    }
    values.update(updates)
    return DecisionContext(**values)


def test_round_module_exports_exactly_the_three_types() -> None:
    """模块只导出三个类型；只为进程间打包存在的三个请求类型不存在。"""
    assert decision_round.__all__ == ["DecisionContext", "DirectToolCall", "SchedulingState"]
    for name in ("SocietyCheckpointRequest", "AgentStepBatchRequest", "AgentCreateBatchRequest"):
        assert not hasattr(decision_round, name), name


def test_decision_context_is_immutable_and_complete() -> None:
    context = _context()
    assert context.agent_id == 7
    with pytest.raises((AttributeError, TypeError)):
        context.agent_id = 8  # type: ignore[misc]


@pytest.mark.parametrize("field", ["run_id", "round_id", "decision_id"])
def test_decision_context_rejects_empty_identity(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _context(**{field: " "})


def test_decision_context_requires_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _context(observed_at=datetime(2026, 1, 5))


def test_decision_context_rejects_negative_world_version() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _context(world_state_version=-1)


class _Request(BaseModel):
    """测试用已验证请求。"""

    value: str


def test_direct_tool_call_requires_validated_request() -> None:
    context = _context()
    call = DirectToolCall(context, "action-1", "purchase", _Request(value="x"))

    assert call.context is context
    with pytest.raises(ValueError, match="validated Pydantic model"):
        DirectToolCall(context, "action-1", "purchase", {"value": "x"})  # type: ignore[arg-type]


def test_scheduling_state_requires_future_boundary() -> None:
    current = datetime(2026, 1, 5, tzinfo=timezone.utc)
    state = SchedulingState(current, (1, 2), current.replace(hour=1), 4)

    assert state.ready_agent_ids == (1, 2)
    with pytest.raises(ValueError, match="later than"):
        SchedulingState(current, (1,), current, 4)


def test_scheduling_state_rejects_duplicate_agents() -> None:
    current = datetime(2026, 1, 5, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="unique"):
        SchedulingState(current, (1, 1), None, 4)


_NOW = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _call(**updates) -> DirectToolCall:
    """按给定字段覆盖构造一条直接工具调用。"""
    values = {"context": _context(), "action_id": "action-1", "tool_name": "purchase", "request": _Request(value="x")}
    values.update(updates)
    return DirectToolCall(**values)


def _state(**updates) -> SchedulingState:
    """按给定字段覆盖构造一份调度状态。"""
    values = {"current_time": _NOW, "ready_agent_ids": (1,), "next_event_at": None, "world_state_version": 0}
    values.update(updates)
    return SchedulingState(**values)


@pytest.mark.parametrize("build,text", [
    pytest.param(lambda: _context(run_id=""), "run_id must be a non-empty string", id="context-run-id"),
    pytest.param(lambda: _context(round_id=3), "round_id must be a non-empty string", id="context-round-id"),
    pytest.param(lambda: _context(decision_id="\t"), "decision_id must be a non-empty string", id="context-decision-id"),
    pytest.param(lambda: _context(agent_id=True), "agent_id must be an integer", id="context-boolean-agent"),
    pytest.param(lambda: _context(agent_id="7"), "agent_id must be an integer", id="context-text-agent"),
    pytest.param(lambda: _context(observed_at=datetime(2026, 1, 5)), "observed_at must be timezone-aware",
                 id="context-naive-time"),
    pytest.param(lambda: _context(world_state_version=-1), "world_state_version must be a non-negative integer",
                 id="context-negative-version"),
    pytest.param(lambda: _context(world_state_version="3"), "world_state_version must be a non-negative integer",
                 id="context-text-version"),
    pytest.param(lambda: _call(context="decision-a"), "context must be a DecisionContext", id="call-context"),
    pytest.param(lambda: _call(action_id=" "), "action_id must be a non-empty string", id="call-action-id"),
    pytest.param(lambda: _call(tool_name=""), "tool_name must be a non-empty string", id="call-empty-tool"),
    pytest.param(lambda: _call(request={"value": "x"}), "request must be a validated Pydantic model",
                 id="call-raw-request"),
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
def test_round_types_reject_each_invalid_field_with_its_text(build, text) -> None:
    """三个类型对每一条不合法的字段都以原文拒绝，不修正、不补缺省。"""
    with pytest.raises(ValueError) as refused:
        build()
    assert str(refused.value) == text
