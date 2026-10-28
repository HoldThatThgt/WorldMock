"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.foundation.models import StrictModel
from typing import Any
from pydantic import Field, field_serializer, model_validator
from worldmock.state.planning.schedule import IntentKind

from worldmock.state.world.kinds import ActivityKind, ActivityStatus, CONCLUDABLE_TERMINAL_STATUSES, SessionTaskKind
from worldmock.state.logging import _LOGGER


class ActivityState(StrictModel):
    """@brief 一个活动实例的权威进度。"""

    activity_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    # 活动开始时的地点；出行活动记录出发地，目的地另由 destination_id 表达。
    location_id: str = Field(min_length=1)
    kind: ActivityKind
    status: ActivityStatus
    started_at: int = Field(ge=0)
    ended_at: int | None = Field(default=None, ge=0)
    planned_end_at: int = Field(gt=0)
    last_settled_at: int = Field(ge=0)
    elapsed_seconds: int = Field(ge=0)
    planned_duration_seconds: int = Field(gt=0)
    media_position_seconds: float = Field(default=0, ge=0)
    route_progress_meters: float = Field(default=0, ge=0)
    next_interaction_at: int | None = Field(default=None, ge=0)
    destination_id: str | None = None
    # 出行活动所用出行方式的身份（自带的步行是 walking，其余是数据集的身份）；其他活动为空。
    travel_method_id: str | None = None
    resource_claims: set[str] = Field(default_factory=set)
    origin_action_id: str = Field(min_length=1)
    origin_decision_id: str = Field(min_length=1)
    origin_request_event_id: str = Field(min_length=1)
    boundary_type: str | None = None
    interruption_reason: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    # 生命周期（docs/design/04-simulation-engine.md「动作、名称与并行」）：终态且 concluded_at 为空即待收尾；
    # report_anchor_at 为播报锚点（开始、恢复与对该活动播报时写入）；session_ids 为先后所属的地点会话。
    concluded_at: int | None = Field(default=None, ge=0)
    report_anchor_at: int | None = Field(default=None, ge=0)
    # 暂停时刻（docs/design/06-agent.md「进度、待办与记忆」）：暂停时写入、恢复时清空，待办层按它排序。
    paused_at: int | None = Field(default=None, ge=0)
    session_ids: list[str] = Field(default_factory=list)
    name: str = ""
    # 从原会话移来的任务关联与人物自述：summary 写入 progress_note，前一条推入 note_history。
    task_kind: SessionTaskKind = SessionTaskKind.NONE
    task_ref: str | None = None
    plan_goal_intent_kind: IntentKind | None = None
    progress_note: str = Field(default="", max_length=500)
    note_updated_at: int | None = Field(default=None, ge=0)
    note_history: list[dict] = Field(default_factory=list)

    @field_serializer("resource_claims", when_used="json")
    def serialize_resource_claims(self, value: set[str]) -> list[str]:
        """@brief 按稳定顺序编码活动资源集合。"""
        return sorted(value)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ActivityState":
        """@brief 要求终态具有结束时刻，暂停和活动态没有结束时刻；收尾只属于非截断终态。"""
        terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                    ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
        if (self.status in terminal) != (self.ended_at is not None):
            raise ValueError("activity ended_at disagrees with lifecycle status")
        if (not self.started_at <= self.last_settled_at <= self.planned_end_at
                or self.elapsed_seconds > self.planned_duration_seconds
                or self.planned_end_at < self.started_at
                or self.ended_at is not None and self.ended_at < self.last_settled_at):
            _LOGGER.error("Activity progress interval is invalid")
            raise ValueError("activity progress interval is invalid")
        if self.concluded_at is not None and (self.status not in CONCLUDABLE_TERMINAL_STATUSES
                                              or self.concluded_at < self.ended_at):
            _LOGGER.error("Activity conclusion disagrees with its lifecycle")
            raise ValueError("activity concluded_at disagrees with lifecycle status")
        _validate_task_reference(self.task_kind, self.task_ref, self.plan_goal_intent_kind)
        return self



def _validate_task_reference(kind: SessionTaskKind, reference: str | None, intent: str | None) -> None:
    """@brief 活动的任务类型与引用配对：none 时无引用，goal 必带引用且意图等于引用。"""
    if kind == SessionTaskKind.NONE and reference is not None:
        raise ValueError("task_ref must be null when task_kind is none")
    if kind != SessionTaskKind.NONE and reference is None:
        raise ValueError("task_ref required for goal")
    if kind == SessionTaskKind.GOAL and intent != reference:
        _LOGGER.error("Goal activity intent disagrees with its task reference")
        raise ValueError("goal activity intent must match task_ref")
