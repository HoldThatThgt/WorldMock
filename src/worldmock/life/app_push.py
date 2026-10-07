"""@brief 当前地点或路途会话的应用推送：发出、打开及按实际内容完成释放。"""

from __future__ import annotations

import logging

from worldmock.catalog.models import Catalog
from worldmock.errors import IntegrityError
from worldmock.life import randomness
from worldmock.life.models import AppPush, WorldState
from worldmock.life.sessions import current_session
from worldmock.scheduling.events import ScheduledEvent


def validate_app_pushes(state: WorldState, catalog: Catalog) -> None:
    """@brief 当前和历史会话的推送只允许引用已登记的应用与内容。"""
    for session in state.sessions.values():
        for app_id, push in session.app_pushes.items():
            if app_id not in catalog.apps or push.content_id not in catalog.contents:
                logging.error("Runtime app push refers to an unregistered app or content")
                raise IntegrityError("Runtime app push refers to an unregistered app or content")


def issue_app_push(state: WorldState, event: ScheduledEvent, catalog: Catalog) -> tuple[WorldState, dict]:
    """@brief 用一个固定随机流选择排序应用与内容，记录到到期时人物的当前会话。"""
    session = current_session(state, event.agent_id)
    if session is None:
        logging.error("App push requires an active session")
        raise IntegrityError("App push requires an active session")
    candidates = sorted(app_id for app_id, contents in catalog.contents_by_app.items()
                        if contents and app_id not in session.app_pushes)
    if not candidates:
        return state, {"kind": "app_push", "pushed": False, "session_id": session.session_id}
    stream = randomness.random_stream(state.random_state, event.scheduled_id)
    app_id = candidates[stream.randrange(len(candidates))]
    contents = sorted(catalog.contents_by_app[app_id])
    content_id = contents[stream.randrange(len(contents))]
    content = catalog.contents[content_id]
    push = AppPush(push_id=f"{event.scheduled_id}:{app_id}:{content_id}", content_id=content_id,
                   issued_at=event.due_at, status="pending")
    session = session.model_copy(update={"app_pushes": {**session.app_pushes, app_id: push}})
    changed = state.model_copy(update={"sessions": {**state.sessions, session.session_id: session}})
    data = {"kind": "app_push", "push_id": push.push_id, "app_id": app_id, "app": catalog.apps[app_id].name,
            "content_id": content_id, "title": content.title, "content_kind": content.kind.value,
            "summary": content.summary, "session_id": session.session_id, "issued_at": push.issued_at}
    return changed, data


def open_app_push(state: WorldState, agent_id: int, app_id: str) -> tuple[WorldState, AppPush | None]:
    """@brief 首次打开待消费推送；再次打开沿用同一推送，已释放或旧会话推送不命中。"""
    session = current_session(state, agent_id)
    push = None if session is None else session.app_pushes.get(app_id)
    if push is None or push.status == "released":
        return state, None
    if push.status == "opened":
        return state, push
    opened = push.model_copy(update={"status": "opened"})
    session = session.model_copy(update={"app_pushes": {**session.app_pushes, app_id: opened}})
    return state.model_copy(update={"sessions": {**state.sessions, session.session_id: session}}), opened


def release_app_pushes(state: WorldState, agent_id: int, content_id: str) -> WorldState:
    """@brief 内容在任一绑定应用真实完成时，释放当前会话指向该内容的全部推送。"""
    session = current_session(state, agent_id)
    if session is None:
        return state
    pushes = {app_id: (push.model_copy(update={"status": "released"}) if push.content_id == content_id else push)
              for app_id, push in session.app_pushes.items()}
    changed = session.model_copy(update={"app_pushes": pushes})
    return state.model_copy(update={"sessions": {**state.sessions, session.session_id: changed}})
