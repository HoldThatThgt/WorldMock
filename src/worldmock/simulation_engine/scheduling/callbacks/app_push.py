"""@brief 按此刻合格且显式 pushable 的内容发出会话推送，并打开与释放它。"""
from __future__ import annotations
import logging
from worldmock.resource_engine.catalog.models import Catalog
from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.actions.admission import present_location
from worldmock.simulation_engine.activities.content.content_select import (
    ContentRequest,
    eligible_contents,
    select_from_contents,
)
from worldmock.simulation_engine.observation.wording.content_prerequisite import (
    content_units,
    unit_target_episode,
)
from worldmock.state.world.agent import AppPush
from worldmock.state.world.world import WorldState
from worldmock.foundation.randomness import random_stream
from worldmock.simulation_engine.activities.lifecycle.sessions import current_session
from worldmock.state.planning.events import ScheduledEvent

def validate_app_pushes(state: WorldState, catalog: Catalog) -> None:
    """@brief 恢复核验 app、内容、绑定与推送资格，不读取退役动作；已送达的推送此刻不满足内容前提是合法状态。"""
    for session in state.sessions.values():
        for app_id, push in session.app_pushes.items():
            content = catalog.contents.get(push.content_id)
            if (app_id not in catalog.apps or content is None or app_id not in content.app_ids
                    or not content.pushable):
                logging.error("Runtime app push refers to an invalid app or content")
                raise IntegrityError("Runtime app push refers to an invalid app or content")

def issue_app_push(state: WorldState, event: ScheduledEvent, catalog: Catalog) -> tuple[WorldState, dict]:
    """@brief 同一键控随机流先在有候选的 app 里选一个，再按正常推荐在它的候选内容里取一条。"""
    session = current_session(state, event.agent_id)
    if session is None:
        logging.error("App push requires an active session")
        raise IntegrityError("App push requires an active session")
    candidates = _push_candidates(state, catalog, event.agent_id)
    if not candidates:
        return state, {"kind": "app_push", "pushed": False, "session_id": session.session_id}
    stream = random_stream(state.random_state, event.scheduled_id)
    app_id = sorted(candidates)[stream.randrange(len(candidates))]
    request = ContentRequest(event.agent_id, app_id, present_location(state, event.agent_id))
    content, _ = select_from_contents(candidates[app_id], state, request, stream)
    push = AppPush(push_id=f"{event.scheduled_id}:{app_id}:{content.content_id}", content_id=content.content_id,
                   issued_at=event.due_at, status="pending")
    changed_session = session.model_copy(update={"app_pushes": {**session.app_pushes, app_id: push}})
    changed = state.model_copy(update={"sessions": {**state.sessions, session.session_id: changed_session}}, deep=True)
    data = {"kind": "app_push", "push_id": push.push_id, "app_id": app_id, "app": catalog.apps[app_id].name,
            "content_id": content.content_id, "title": content.title, "summary": content.summary,
            "session_id": session.session_id, "issued_at": push.issued_at}
    return changed, data

def _push_candidates(state: WorldState, catalog: Catalog, agent_id: int) -> dict[str, tuple]:
    """@brief 每个会话每个 app 至多一条；按完整单元代表集验证 pushable 与前提，人在路上不推带地点前提内容。"""
    session = current_session(state, agent_id)
    stay, candidates = present_location(state, agent_id), {}
    agent = state.agents[agent_id]
    for app_id in sorted(catalog.apps):
        if app_id in session.app_pushes:
            continue
        eligible = eligible_contents(catalog, state, ContentRequest(agent_id, app_id, stay))
        pushed = []
        for unit in content_units(eligible):
            target = unit_target_episode(unit, agent)
            if target is not None and target.pushable:
                pushed.extend(unit)
        if pushed:
            candidates[app_id] = tuple(sorted(pushed, key=lambda item: item.content_id))
    return candidates

def open_app_push(state: WorldState, agent_id: int, app_id: str,
                  content_id: str) -> tuple[WorldState, AppPush | None]:
    """@brief 只有首次受理该 app 且真实取到钉住内容才打开推送。"""
    session = current_session(state, agent_id)
    push = None if session is None else session.app_pushes.get(app_id)
    if push is None or push.status == "released" or push.content_id != content_id:
        return state, None
    if push.status == "opened":
        return state, push
    opened = push.model_copy(update={"status": "opened"})
    changed_session = session.model_copy(update={"app_pushes": {**session.app_pushes, app_id: opened}})
    changed = state.model_copy(update={"sessions": {**state.sessions, session.session_id: changed_session}}, deep=True)
    return changed, opened

def release_app_pushes(state: WorldState, agent_id: int, content_id: str) -> WorldState:
    """@brief 真实内容完成释放当前会话指向它的全部推送。"""
    session = current_session(state, agent_id)
    if session is None:
        return state
    pushes = {app_id: (push.model_copy(update={"status": "released"}) if push.content_id == content_id else push)
              for app_id, push in session.app_pushes.items()}
    changed = session.model_copy(update={"app_pushes": pushes})
    return state.model_copy(update={"sessions": {**state.sessions, session.session_id: changed}}, deep=True)
