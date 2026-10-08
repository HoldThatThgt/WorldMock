"""@brief 只读工具误入真实动作派发时立即拒绝，不建立任何世界转换。"""

from __future__ import annotations

import logging

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.models import PendingAction


@pytest.mark.parametrize("tool_name, arguments", [
    pytest.param("query_world", {"name": "地点：家", "way": None, "category": None, "from": None,
                                "available_only": True, "offset": 0}, id="query_world"),
    pytest.param("recall_task_summary", {"task_name": "工作：测试任务"}, id="recall_task_summary"),
])
def test_read_only_tools_cannot_enter_action_dispatch(engine_factory, caplog, tool_name, arguments) -> None:
    """@brief 真实动作派发拒绝只读工具，记录英文错误，内存世界、持久世界与完整事件流均保持原样。"""
    engine = engine_factory()
    state = engine.context.state
    store = engine.context.store
    before = state.model_dump(mode="json")
    persisted = store.state
    sequence = store.last_event_seq
    events = store.read_events(sequence)
    pending = PendingAction(
        run_id=state.run_id, round_id="misrouted-read-only", decision_id="read-only-decision",
        action_id="read-only-action", agent_id=min(state.agents), observed_at=state.current_time,
        world_state_version=state.version, order=0, tool_name=tool_name, request=arguments,
        request_sha256="0" * 64, requested_event_id="read-only-request",
    )
    caplog.clear()
    with caplog.at_level(logging.ERROR), pytest.raises(BusinessRuleError, match="^ACTION_REQUEST_INVALID$"):
        engine.actions.dispatch(state, pending)

    assert any(record.levelno == logging.ERROR
               and record.getMessage() == f"Read-only query reached world dispatch: {tool_name}"
               for record in caplog.records)
    assert all(record.getMessage().isascii() for record in caplog.records)
    assert engine.context.state is state
    assert state.model_dump(mode="json") == before
    assert store.state == persisted
    assert store.last_event_seq == sequence
    assert store.read_events(sequence) == events
