"""@brief 工作主题只从真实工作活动经过秒数派生进度和完成证据。"""

from __future__ import annotations

import logging

from worldmock.catalog.models import WorkTopic
from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.footprints.models import EventDraft
from worldmock.life.guidance import Refusal
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, DERIVED_DURATION_CAP_SECONDS, WorldState
from worldmock.life.scenario import AgentConfig

#: 主题活动最短受理时长，与普通活动一分钟准入一致。
WORK_START_MINIMUM_SECONDS = 60


def validate_work_start(config: AgentConfig, topic: WorkTopic, present_location: str | None) -> None:
    """@brief 人物只能在自己工作安排的地点处理被分配的主题，途中不算在场。"""
    if topic.topic_id not in config.work_topic_ids:
        logging.error("Work topic is not assigned to agent: %s", topic.topic_id)
        raise BusinessRuleError("WORK_TOPIC_NOT_FOUND")
    required = config.work_schedule.workplace_location_id
    if present_location != required:
        logging.error("Work topic requires the agent workplace: %s", required)
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", unmet=["required_location_id"],
                                public_reason="The person must be at the assigned workplace to work on this topic.")


def work_totals(state: WorldState, agent_id: int, topic_id: str) -> int:
    """@brief 累计本人物本主题全部非失败工作活动的实际经过秒数；暂停和并行非工作活动不增加它。"""
    return sum(item.elapsed_seconds for item in state.activities.values()
               if item.agent_id == agent_id and item.kind == ActivityKind.WORK
               and item.target_id == topic_id and item.status != ActivityStatus.FAILED)


def work_start_seconds(topic: WorkTopic, worked_seconds: int) -> int:
    """@brief 主题剩余实际秒数按单次活动一天上限受理，不读截止、效率或模型描述。"""
    return min(topic.target_seconds - worked_seconds, DERIVED_DURATION_CAP_SECONDS)


def work_start_refusal(topic: WorkTopic, worked_seconds: int) -> Refusal | None:
    """@brief 开始与恢复共用主题不足一分钟的原因与下一步。"""
    if topic.target_seconds - worked_seconds >= WORK_START_MINIMUM_SECONDS:
        return None
    return Refusal("ACTIVITY_DURATION_INVALID", "Less than one minute of the topic remains; work cannot start.",
                   "Choose another activity.")


def work_resume_cap(topic: WorkTopic, worked_seconds: int) -> int:
    """@brief 恢复工作也只允许尚未做完的主题秒数，并遵守同一活动上限。"""
    return work_start_seconds(topic, worked_seconds)


def _hours(seconds: int) -> str:
    """@brief 工时用约数小时呈现，一位小数且不保留无意义的零。"""
    return f"约 {f'{seconds / 3600:.1f}'.removesuffix('.0')} 小时"


def contract_view(topic: WorkTopic, worked_seconds: int) -> dict:
    """@brief 工具层主题合同只写主题、目标、已做和剩余四项。"""
    return {"topic": topic.topic, "target": _hours(topic.target_seconds), "done": _hours(worked_seconds),
            "remaining": _hours(max(0, topic.target_seconds - worked_seconds))}


def work_progress_data(state: WorldState, activity: ActivityState, topic: WorkTopic) -> dict:
    """@brief 每次真实结算投影本主题跨活动累计工时，而不是一次活动自己的进度。"""
    worked = work_totals(state, activity.agent_id, topic.topic_id)
    return {"topic_id": topic.topic_id, "worked_seconds": worked,
            "remaining_seconds": max(0, topic.target_seconds - worked)}


def work_completion_draft(state: WorldState, before: ActivityState, topic: WorkTopic,
                          source_event_id: str) -> EventDraft | None:
    """@brief 只有本次真实结算首次越过主题目标时生成本人可见完成事件，不增设完成状态。"""
    after = state.activities[before.activity_id]
    worked = work_totals(state, before.agent_id, topic.topic_id)
    previous = worked - (after.elapsed_seconds - before.elapsed_seconds)
    if not previous < topic.target_seconds <= worked:
        return None
    return EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=before.agent_id,
                      activity_id=before.activity_id, source_kind="environment", caused_by=[source_event_id],
                      data={"kind": "work_topic_completed", "topic_id": topic.topic_id, "worked_seconds": worked})
