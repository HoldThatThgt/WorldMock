"""共享决策轮合同测试。"""

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from agentsociety2.society import DecisionContext, DirectToolCall, SchedulingState


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
