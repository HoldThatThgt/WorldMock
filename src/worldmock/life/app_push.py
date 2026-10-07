"""@brief 当前会话的娱乐推送：按动作资格发出、真实打开与完成释放。"""

from __future__ import annotations

import logging

from worldmock.catalog.models import Catalog
from worldmock.errors import IntegrityError
from worldmock.exchange.types import ActionCategory, AppUseManner
from worldmock.life.content_select import ContentRequest, eligible_contents, select_content
from worldmock.life.models import AppPush, WorldState
from worldmock.life.randomness import random_stream
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.sessions import current_session
from worldmock.scheduling.events import ScheduledEvent


def validate_app_pushes(state: WorldState, catalog: Catalog) -> None:
    """@brief 推送只允许引用已登记、实际绑定该 app 的娱乐手机动作内容。"""
    for session in state.sessions.values():
        for app_id, push in session.app_pushes.items():
            content = catalog.contents.get(push.content_id)
            action = None if content is None else catalog.actions[content.action_id]
            if (app_id not in catalog.apps or content is None or app_id not in content.app_ids
                    or action.category != ActionCategory.ENTERTAINMENT
                    or action.manner != AppUseManner.PHONE_CONTINUOUS):
                logging.error("Runtime app push refers to an invalid app, action, or content")
                raise IntegrityError("Runtime app push refers to an invalid app, action, or content")


def issue_app_push(state: WorldState, event: ScheduledEvent, catalog: Catalog,
                   scenario: RuntimeScenario) -> tuple[WorldState, dict]:
    """@brief 同一预排流依次选排序 app、动作与历史合格内容，写入到期时当前会话。"""
    session = current_session(state, event.agent_id)
    if session is None:
        raise IntegrityError("App push requires an active session")
    candidates = _push_candidates(state, catalog, scenario, event.agent_id)
    if not candidates:
        return state, {"kind": "app_push", "pushed": False, "session_id": session.session_id}
    stream = random_stream(state.random_state, event.scheduled_id)
    app_id = sorted(candidates)[stream.randrange(len(candidates))]
    actions = candidates[app_id]
    action_id = actions[stream.randrange(len(actions))]
    content, _ = select_content(catalog, scenario, state, ContentRequest(event.agent_id, action_id, app_id), stream)
    push = AppPush(push_id=f"{event.scheduled_id}:{app_id}:{content.content_id}", content_id=content.content_id,
                   issued_at=event.due_at, status="pending")
    changed_session = session.model_copy(update={"app_pushes": {**session.app_pushes, app_id: push}})
    changed = state.model_copy(update={"sessions": {**state.sessions, session.session_id: changed_session}}, deep=True)
    data = {"kind": "app_push", "push_id": push.push_id, "app_id": app_id, "app": catalog.apps[app_id].name,
        "action_id": action_id, "action": catalog.actions[action_id].name, "content_id": content.content_id,
        "title": content.title, "summary": content.summary, "session_id": session.session_id,
        "issued_at": push.issued_at}
    return changed, data


def _push_candidates(state: WorldState, catalog: Catalog, scenario: RuntimeScenario,
                     agent_id: int) -> dict[str, tuple[str, ...]]:
    """@brief 推送窗口保留每 session 每 app 一条，只从娱乐手机动作取得候选。"""
    session = current_session(state, agent_id)
    candidates: dict[str, list[str]] = {}
    for action_id, action in sorted(catalog.actions.items()):
        if action.category != ActionCategory.ENTERTAINMENT or action.manner != AppUseManner.PHONE_CONTINUOUS:
            continue
        for app_id in action.app_ids:
            if app_id not in session.app_pushes and eligible_contents(
                    catalog, scenario, state, ContentRequest(agent_id, action_id, app_id)):
                candidates.setdefault(app_id, []).append(action_id)
    return {app_id: tuple(sorted(actions)) for app_id, actions in sorted(candidates.items())}


def open_app_push(state: WorldState, agent_id: int, app_id: str, action_id: str,
                  catalog: Catalog) -> tuple[WorldState, AppPush | None]:
    """@brief 只受理同 app、同动作的真实开始；再次开始沿用未释放推送。"""
    session = current_session(state, agent_id)
    push = None if session is None else session.app_pushes.get(app_id)
    if (push is None or push.status == "released"
            or catalog.contents[push.content_id].action_id != action_id):
        return state, None
    if push.status == "opened":
        return state, push
    opened = push.model_copy(update={"status": "opened"})
    changed_session = session.model_copy(update={"app_pushes": {**session.app_pushes, app_id: opened}})
    changed = state.model_copy(update={"sessions": {**state.sessions, session.session_id: changed_session}}, deep=True)
    return changed, opened


def release_app_pushes(state: WorldState, agent_id: int, content_id: str) -> WorldState:
    """@brief 任一活动真实完成内容，释放当前会话指向该内容的全部推送。"""
    session = current_session(state, agent_id)
    if session is None:
        return state
    pushes = {app_id: (push.model_copy(update={"status": "released"}) if push.content_id == content_id else push)
              for app_id, push in session.app_pushes.items()}
    changed = session.model_copy(update={"app_pushes": pushes})
    return state.model_copy(update={"sessions": {**state.sessions, session.session_id: changed}}, deep=True)
