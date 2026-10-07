"""@brief 一次性原生闹钟的严格时刻、身份和纯排期。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from worldmock.errors import PreconditionError, ProtocolError
from worldmock.life.schedule import alarm_time
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

if TYPE_CHECKING:
    from worldmock.life.models import PendingAction, SetAlarmRequest, WorldState


def set_alarm(state: WorldState, pending: PendingAction, request: SetAlarmRequest) -> tuple[WorldState, dict]:
    """@brief 创建原请求身份唯一的一次性排期，真实结果因果由同次提交绑定。"""
    moment = alarm_time(request.at)
    if not state.current_time < moment < state.end_time:
        raise PreconditionError("ALARM_TIME_INVALID", ["alarm_time"],
            public_reason="Alarm time must be a future whole second before the run ends.")
    identity = f"alarm-{pending.action_id}"
    result = {"alarm_id": identity, "at": request.at}
    existing = next((item for item in state.pending_events if item.scheduled_id == identity), None)
    if existing is not None:
        if existing.due_at != moment or existing.data["request_event_id"] != pending.requested_event_id:
            raise ProtocolError("An alarm identity cannot be reused for another request")
        return state, result
    sequence = max((item.sequence for item in state.pending_events), default=0) + 1
    event = ScheduledEvent(scheduled_id=identity, kind=ScheduledKind.ALARM_RING, due_at=moment,
                           sequence=sequence, agent_id=pending.agent_id,
                           data={"request_event_id": pending.requested_event_id})
    events = sorted([*state.pending_events, event], key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    return state.model_copy(update={"pending_events": events}, deep=True), result
