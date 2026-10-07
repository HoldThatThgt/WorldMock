"""@brief 指派工作内容的位置前置条件与真实完成证据。"""

from __future__ import annotations

from worldmock.catalog.models import Content
from worldmock.footprints.models import EventDraft
from worldmock.life.guidance import Refusal
from worldmock.life.models import ActivityState, WorldState
from worldmock.life.scenario import AgentConfig


def work_location_refusal(action_name: str, place_name: str) -> Refusal:
    """@brief 工具展示、开始与恢复共用同一个要求地点的拒绝合同。"""
    return Refusal("ACTIVITY_PRECONDITION_UNMET", f"{action_name} requires presence at {place_name}.",
                   f"Go to {place_name}.", ("required_location_id",))


def validate_work_start(config: AgentConfig, content: Content, present_location: str | None,
                        action_name: str, place_name: str) -> None:
    """@brief 只有指派内容要求本人工作地点，要求地点与工具行使用同一条公开拒绝。"""
    if content.content_id not in config.work_content_ids:
        return
    if present_location != config.work_schedule.workplace_location_id:
        raise work_location_refusal(action_name, place_name).error()


def work_completion_draft(state: WorldState, before: ActivityState, content: Content,
                          source_event_id: str, work_content_ids: tuple[str, ...]) -> EventDraft | None:
    """@brief 首次真实推进到指派内容末尾时，生成本人的内容完成事件。"""
    if content.content_id not in work_content_ids:
        return None
    progress = state.agents[before.agent_id].content_progress.get(content.content_id)
    after = state.activities[before.activity_id]
    if (progress is None or progress.completed_rounds != 1
            or before.media_position_seconds >= content.duration_seconds
            or after.media_position_seconds < content.duration_seconds
            or after.elapsed_seconds <= before.elapsed_seconds):
        return None
    return EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=before.agent_id,
                      activity_id=before.activity_id, source_kind="environment", caused_by=[source_event_id],
                      data={"kind": "work_content_completed", "content_id": content.content_id})
