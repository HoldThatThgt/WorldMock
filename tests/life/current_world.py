"""@brief 现行消费者测试共用已提交世界的时刻与人物位置。"""

from worldmock.footprints.models import EventDraft


def stage_moment(engine, moment=None, location=None, agent_id=1):
    """@brief 保存真实状态副本和测试设置事件，不替代任何人物动作结算。"""
    staged = engine.context.state.model_copy(deep=True)
    now = staged.current_time if moment is None else moment
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    person = staged.agents[agent_id]
    person.location_id = location or person.location_id
    person.body_settled_at = person.next_decision_at = now
    for session in staged.sessions.values():
        if session.agent_id == agent_id and session.status == "active" and session.kind == "stay":
            session.location_id = person.location_id
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_world_staged"})])
    engine.context.state = staged
