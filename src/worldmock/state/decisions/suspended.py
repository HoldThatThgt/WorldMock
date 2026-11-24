"""@brief 跨模块屏障只读模型基类与无栈挂起恢复载荷。"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from worldmock.foundation.models import FrozenModel


class StateModel(FrozenModel):
    """@brief 跨模块屏障传递的不可变状态载荷基类。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SuspendedLoopState(StateModel):
    """@brief 保存于 AgentState.suspended_loop 的无栈挂起恢复凭证与沙盒延续状态。"""

    wakeup_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    activity_id: str = Field(min_length=1)
    pending_tool_call_id: str = Field(min_length=1)
    messages: tuple[dict[str, Any], ...]
    turn_used: int = Field(ge=0)
    memory_search_count: int = Field(default=0, ge=0)
    accepted_action_ids: tuple[str, ...] = ()
    pending_memories: tuple[dict[str, Any], ...] = ()
    verified_experiences: tuple[dict[str, Any], ...] = ()
    local_rejection_counts: dict[str, int] = Field(default_factory=dict)
    observation_event_id: str = Field(min_length=1)
    observation_parts: dict[str, str] = Field(default_factory=dict)
    suspended_at: int = Field(ge=0)
    base_layers: tuple[dict[str, Any], ...] = ()
