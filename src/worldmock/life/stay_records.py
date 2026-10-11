"""@brief 停留进出事件：人物每进入、每离开一个地点各写一条，由提交前后世界里会话的变化得出。

停留就是地点停留会话。运行开始时人物所在的那一段随世界创建的首次提交写一条；之后每次世界提交之前比较一次：
多出一段路上会话是出发，为之前进行中的那段停留写离开；多出一段停留会话是到达或途中提前结束出行，写进入。
运行截止不写离开，路上不是停留。事件都是系统可见的记录，人物看不到。
"""

from __future__ import annotations

import logging

from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import ActivityKind, SessionKind, SessionState, SessionStatus, WorldState

_LOGGER = logging.getLogger(__name__)
#: 一段停留怎样开始：运行开始时已在这里、出行按计划到达、途中提前结束出行回到出发地。
RUN_START, ARRIVAL, TRAVEL_ENDED_EARLY = "run_start", "arrival", "travel_ended_early"


def run_start_event(state: WorldState) -> EventDraft:
    """@brief 运行开始时人物已在的那段停留：不是一次真实的进入，没有起因，整次运行恰一条。"""
    (session,) = state.sessions.values()
    return _entered(session, RUN_START, [])


def stay_events(before: WorldState, after: WorldState, drafts: list[EventDraft]) -> list[EventDraft]:
    """@brief 世界提交之前按会话的变化写停留进出事件。

    :param before: 提交前的世界。
    :param after: 待提交的世界。
    :param drafts: 本次提交已经排好的事件草稿；进出事件的起因取其中受理出发的结果、出行走完的进度或结束出行的结果。
    """
    events = []
    for session in after.sessions.values():
        if session.session_id in before.sessions:
            continue
        if session.kind == SessionKind.TRANSIT:
            events.append(_left(before, session, drafts))
        elif session.opened_by_action_id is None:
            events.append(_entered(session, ARRIVAL, [_arrival_cause(after, session, drafts)]))
        else:
            cause = _draft_id(drafts, "action_result", "action_id", session.opened_by_action_id)
            events.append(_entered(session, TRAVEL_ENDED_EARLY, [cause]))
    return events


def _entered(session: SessionState, entry_kind: str, causes: list[str]) -> EventDraft:
    """@brief 一段停留开始的事件，发生时刻是停留的开始时刻。"""
    return EventDraft(event_type="environment_event", occurred_at=session.started_at, agent_id=session.agent_id,
                      caused_by=causes, visibility="system", source_kind="environment",
                      data={"kind": "location_entered", "session_id": session.session_id,
                            "location_id": session.location_id, "entry_kind": entry_kind})


def _left(before: WorldState, transit: SessionState, drafts: list[EventDraft]) -> EventDraft:
    """@brief 出发：之前进行中的那段停留在出发时刻结束，起因是受理这次出发的动作结果。"""
    stay = next((item for item in before.sessions.values() if item.agent_id == transit.agent_id
                 and item.kind == SessionKind.STAY and item.status == SessionStatus.ACTIVE), None)
    if stay is None:
        _LOGGER.error("Departure has no stay in progress to leave")
        raise IntegrityError("Departure has no stay in progress to leave")
    cause = _draft_id(drafts, "action_result", "action_id", transit.opened_by_action_id)
    return EventDraft(event_type="environment_event", occurred_at=transit.started_at, agent_id=transit.agent_id,
                      caused_by=[cause], visibility="system", source_kind="environment",
                      data={"kind": "location_left", "session_id": stay.session_id,
                            "location_id": stay.location_id})


def _arrival_cause(after: WorldState, stay: SessionState, drafts: list[EventDraft]) -> str:
    """@brief 到达的起因：同一秒结束的那段路上会话里，那趟出行走完这一秒的进度事件。"""
    for transit in after.sessions.values():
        if (transit.agent_id, transit.kind, transit.ended_at) != (stay.agent_id, SessionKind.TRANSIT, stay.started_at):
            continue
        for activity_id in transit.activity_ids:
            trip = after.activities[activity_id]
            if trip.kind == ActivityKind.TRAVEL and trip.ended_at == stay.started_at:
                return _draft_id(drafts, "activity_progress", "activity_id", activity_id)
    _LOGGER.error("Arrival has no finished trip in this commit")
    raise IntegrityError("Arrival has no finished trip in this commit")


def _draft_id(drafts: list[EventDraft], event_type: str, field: str, value: str | None) -> str:
    """@brief 本次提交里某种事件类型、某个身份字段取给定值的那条草稿的事件身份；找不到是运行故障。"""
    for draft in drafts:
        if draft.event_type == event_type and getattr(draft, field) == value:
            return draft.event_id
    _LOGGER.error("Stay record has no cause event in this commit")
    raise IntegrityError("Stay record has no cause event in this commit")
