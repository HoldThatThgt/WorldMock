"""@brief 全球时钟屏障、人物沙盒、搜索子会话与有序提交器之间的冻结边界模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, model_validator

from worldmock.state.decisions.suspended import StateModel, SuspendedLoopState
from worldmock.state.logging import _LOGGER
from worldmock.state.world.world import WorldState

if TYPE_CHECKING:
    from worldmock.recording_engine.events.memory_evidence import MemoryUpdateEvidence
    from worldmock.recording_engine.events.models import EventDraft
else:
    EventDraft = Any
    MemoryUpdateEvidence = Any

__all__ = [
    "AgentWakeupInput",
    "BarrierCommitReceipt",
    "SandboxOutcome",
    "SearchCommitReceipt",
    "SearchSessionOutcome",
    "SearchTaskSpec",
    "StateModel",
    "SuspendedLoopState",
    "ToolDispatchResult",
]


class AgentWakeupInput(StateModel):
    """@brief prepare_barrier 生成的只读唤醒快照，传入并发人物唤醒协程。"""

    wakeup_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    observed_at: int = Field(ge=0)
    world_state_version: int = Field(ge=0)
    mode: Literal["regular", "resume_search"]
    observation: dict[str, Any]
    observation_parts: dict[str, str] = Field(default_factory=dict)
    pending_presentation: dict[str, int] = Field(default_factory=dict)
    action_seq_start: int = Field(ge=0)
    suspended_loop: SuspendedLoopState | None = None
    search_result_payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_resume_state(self) -> AgentWakeupInput:
        """@brief 恢复搜索唤醒必须携带对应的挂起上下文。"""
        if self.mode == "resume_search" and self.suspended_loop is None:
            _LOGGER.error("resume_search wakeup %s lacks suspended_loop", self.wakeup_id)
            raise ValueError("resume_search wakeup requires suspended_loop")
        return self


class ToolDispatchResult(StateModel):
    """@brief AgentSandbox.dispatch_world_tool 返回给 AgentLoop 的工具即时结果。"""

    accepted: bool
    terminal_exit: bool
    suspended: bool
    tool_message_payload: dict[str, Any]
    pending_memories: tuple[dict[str, Any], ...] = ()


class SandboxOutcome(StateModel):
    """@brief 人物唤醒完成或挂起后交由 BarrierMerger 核验与合并的完整输出。"""

    wakeup_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    status: Literal["completed", "suspended"]
    state: WorldState
    evidence_drafts: tuple[EventDraft, ...] = ()
    observation_parts: dict[str, str] = Field(default_factory=dict)
    pending_presentation: dict[str, int] = Field(default_factory=dict)
    accepted_action_ids: tuple[str, ...] = ()
    pending_memories: tuple[dict[str, Any], ...] = ()
    verified_experiences: tuple[dict[str, Any], ...] = ()
    observation_event_id: str = Field(min_length=1)
    observation_payload: dict[str, Any] = Field(default_factory=dict)
    memory_evidence: MemoryUpdateEvidence | None = None
    action_operations: tuple[dict[str, Any], ...] = ()


class SearchTaskSpec(StateModel):
    """@brief due_searches 生成的只读搜索子任务上下文。"""

    action_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    activity_id: str = Field(min_length=1)
    origin_wakeup_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    prefer: str | None = None
    scoped_app_id: str | None = None
    current_location_id: str = Field(min_length=1)
    location_name: str = Field(min_length=1)
    opened_at_clock: str = Field(min_length=1)
    local_date: str = Field(min_length=1)
    weather_label: str = Field(min_length=1)
    open_apps: tuple[str, ...] = ()
    category_overview: dict[str, int] = Field(default_factory=dict)
    turn_budget: int = Field(ge=1)


class SearchSessionOutcome(StateModel):
    """@brief SearchSessionRunner 返回的纯 ID 列表、说明与内部模型交互草稿。"""

    action_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    activity_id: str = Field(min_length=1)
    goods_ids: tuple[str, ...] = ()
    place_ids: tuple[str, ...] = ()
    summary: str = Field(min_length=1)
    evidence_drafts: tuple[EventDraft, ...] = ()


class SearchCommitReceipt(StateModel):
    """@brief complete_searches 的原子提交回执。"""

    completed_count: int = Field(ge=0)
    activity_ids: tuple[str, ...] = ()
    event_seq_last: int = Field(ge=0)


class BarrierCommitReceipt(StateModel):
    """@brief commit_barrier 的原子提交回执。"""

    committed_agent_ids: tuple[int, ...] = ()
    conflict_requeued_agent_ids: tuple[int, ...] = ()
    world_state_version: int = Field(ge=0)
    event_seq_last: int = Field(ge=0)
