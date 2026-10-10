"""@brief 仅按显式 pushable 内容发出、打开与释放会话推送。"""
from __future__ import annotations
import logging
from worldmock.catalog.models import Catalog
from worldmock.errors import IntegrityError
from worldmock.life.content_select import ContentRequest, eligible_contents, select_from_contents
from worldmock.life.models import AppPush, WorldState
from worldmock.life.randomness import random_stream
from worldmock.life.sessions import current_session
from worldmock.scheduling.events import ScheduledEvent

def validate_app_pushes(state: WorldState, catalog: Catalog) -> None:
    """@brief 恢复核验 app、内容、绑定与推送资格，不读取退役动作。"""
    for session in state.sessions.values():
        for app_id, push in session.app_pushes.items():
            content = catalog.contents.get(push.content_id)
            if (app_id not in catalog.apps or content is None or app_id not in content.app_ids
                    or not content.pushable):
                logging.error("Runtime app push refers to an invalid app or content")
                raise IntegrityError("Runtime app push refers to an invalid app or content")

def issue_app_push(state: WorldState, event: ScheduledEvent, catalog: Catalog) -> tuple[WorldState, dict]:
    """@brief 同一键控随机流先选 app，再按正常推荐选可推送内容。"""
    session = current_session(state, event.agent_id)
    if session is None:
        logging.error("App push requires an active session")
        raise IntegrityError("App push requires an active session")
    candidates = _push_candidates(state, catalog, event.agent_id)
    if not candidates:
        return state, {"kind": "app_push", "pushed": False, "session_id": session.session_id}
    stream = random_stream(state.random_state, event.scheduled_id)
    app_id = sorted(candidates)[stream.randrange(len(candidates))]
    request = ContentRequest(event.agent_id, "app", app_id)
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
    """@brief 每个会话每个 app 至多一条，仅 app 绑定可推送内容成为候选。"""
    session = current_session(state, agent_id)
    candidates = {}
    for app_id in sorted(catalog.apps):
        if app_id in session.app_pushes:
            continue
        content = tuple(item for item in eligible_contents(catalog, state, ContentRequest(agent_id, "app", app_id))
                        if item.pushable)
        if content:
            candidates[app_id] = content
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
