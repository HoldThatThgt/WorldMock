"""@brief 地点会话：一段地点停留或两地之间的一段路，由系统随出发与到达开始和结束。

依据 docs/design/04-simulation-engine.md：
- 运行开始时按初始地点开始首个停留会话；
- 出发（出行被受理）时结束当前停留会话，开始去目的地的路上会话；同一请求开始的活动都归入路上会话；
- 到达或提前结束出行时结束路上会话，在人物此刻地点开始停留会话；槽位 2 的活动保持各自生命周期；
- 暂停的活动属于它的开始地点，回到该地点时归入那里的新停留会话；
- 实验截止时所有会话结束。

会话的开始与结束只写入世界状态（随共同检查点保存），不写事件，也不新增事件类型。本模块的函数都返回新的世界状态，
不就地改动传入状态；引擎只接线。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.life.models import (ActivityKind, ActivityState, ActivityStatus, SessionKind, SessionState,
                                   SessionStatus, WorldState)

def moment_label(epoch: int, timezone_name: str) -> str:
    """@brief 名称中的绝对时刻「<M月D日> HH:MM」（场景时区）；名称用绝对日期，结束后不改名。"""
    moment = datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))
    return f"{moment.month}月{moment.day}日 {moment:%H:%M}"


def current_session(state: WorldState, agent_id: int) -> SessionState | None:
    """@brief 人物唯一进行中的会话；运行截止后没有进行中会话。"""
    return next((item for item in state.sessions.values()
                 if item.agent_id == agent_id and item.status == SessionStatus.ACTIVE), None)


def on_road(state: WorldState, activity: ActivityState) -> bool:
    """@brief 活动是否属于正在进行的一段路：路上的活动不能暂停，随出行结束。"""
    if not activity.session_ids:
        return False
    session = state.sessions.get(activity.session_ids[-1])
    return (session is not None and session.kind == SessionKind.TRANSIT
            and session.status == SessionStatus.ACTIVE)


def at_stay(state: WorldState, agent_id: int, location_id: str) -> bool:
    """@brief 人物此刻是否停留在指定地点（路上不算在任何地点）；暂停的活动只在开始地点恢复。"""
    session = current_session(state, agent_id)
    return (session is not None and session.kind == SessionKind.STAY
            and session.location_id == location_id)


def open_stay(state: WorldState, agent_id: int, place_names: dict[str, str],
              opened_by_action_id: str | None = None) -> WorldState:
    """@brief 在人物当前地点开始一段停留；该地点仍暂停的本人活动归入这段新会话。

    :param opened_by_action_id: 途中结束出行引起的切换记下该动作身份；运行开始与到达为 None。
    """
    agent = state.agents[agent_id]
    now = state.current_time
    session = SessionState(
        session_id=_next_session_id(state, agent_id), agent_id=agent_id, kind=SessionKind.STAY,
        location_id=agent.location_id, status=SessionStatus.ACTIVE, started_at=now,
        name=f"{place_names[agent.location_id]}（{moment_label(now, state.timezone)} 起）",
        opened_by_action_id=opened_by_action_id)
    paused = [item.activity_id for item in sorted(state.activities.values(), key=_start_order)
              if item.agent_id == agent_id and item.status == ActivityStatus.PAUSED
              and item.location_id == agent.location_id]
    changed = state.model_copy(update={"sessions": {**state.sessions, session.session_id: session}})
    return assign(changed, session.session_id, paused)


def begin_transit(state: WorldState, agent_id: int, destination_id: str,
                  place_names: dict[str, str], *, opened_by_action_id: str) -> WorldState:
    """@brief 出发：结束当前停留会话，开始去目的地的路上会话（出发地取人物当前地点）。

    :param opened_by_action_id: 出发请求的动作身份（进度层以其结果的事件序号为下界）。
    """
    changed = _end_current(state, agent_id)
    now = changed.current_time
    session = SessionState(
        session_id=_next_session_id(changed, agent_id), agent_id=agent_id, kind=SessionKind.TRANSIT,
        origin_id=changed.agents[agent_id].location_id, destination_id=destination_id,
        status=SessionStatus.ACTIVE, started_at=now,
        name=f"去{place_names[destination_id]}的路上（{moment_label(now, changed.timezone)} 起）",
        opened_by_action_id=opened_by_action_id)
    return changed.model_copy(update={"sessions": {**changed.sessions, session.session_id: session}})


def end_transit(state: WorldState, agent_id: int, place_names: dict[str, str],
                opened_by_action_id: str | None = None) -> WorldState:
    """@brief 到达或提前结束出行后开始停留会话，槽位 2 的活动保持各自生命周期。"""
    session = current_session(state, agent_id)
    if session is None or session.kind != SessionKind.TRANSIT:
        return state
    return open_stay(_end_current(state, agent_id), agent_id, place_names, opened_by_action_id)


def arriving_transit(state: WorldState, agent_id: int, target: int) -> str | None:
    """@brief 本边界到达的路上会话身份：其出行在 target 走完计划时长（推进前仍进行、推进后已完成或到达被拒）。"""
    session = current_session(state, agent_id)
    if session is None or session.kind != SessionKind.TRANSIT:
        return None
    for activity_id in session.activity_ids:
        travel = state.activities[activity_id]
        if travel.kind != ActivityKind.TRAVEL or travel.planned_end_at > target:
            continue
        if travel.status == ActivityStatus.ACTIVE or travel.status in {
                ActivityStatus.COMPLETED, ActivityStatus.FAILED} and travel.ended_at == target:
            return session.session_id
    return None


def assign(state: WorldState, session_id: str, activity_ids: list[str]) -> WorldState:
    """@brief 把活动归入会话：会话记下活动，活动在 session_ids 末尾记下会话（已归入的不重复）。"""
    if not activity_ids:
        return state
    session = state.sessions[session_id]
    members = list(session.activity_ids) + [item for item in activity_ids if item not in session.activity_ids]
    activities = dict(state.activities)
    for activity_id in activity_ids:
        activity = activities[activity_id]
        if session_id not in activity.session_ids:
            activities[activity_id] = activity.model_copy(
                update={"session_ids": [*activity.session_ids, session_id]}, deep=True)
    sessions = {**state.sessions, session_id: session.model_copy(update={"activity_ids": members}, deep=True)}
    return state.model_copy(update={"sessions": sessions, "activities": activities})


def end_all(state: WorldState) -> WorldState:
    """@brief 实验截止：所有进行中会话结束；活动按既有 truncated 处理，不要求收尾。"""
    sessions = {key: (item.model_copy(update={"status": SessionStatus.ENDED, "ended_at": state.current_time},
                                      deep=True) if item.status == SessionStatus.ACTIVE else item)
                for key, item in state.sessions.items()}
    return state.model_copy(update={"sessions": sessions})


def place_label(session: SessionState, place_names: dict[str, str]) -> str:
    """@brief 会话的地点说法：停留为地点名，路上为「去<目的地>的路上」。"""
    if session.kind == SessionKind.STAY:
        return place_names[session.location_id]
    return f"去{place_names[session.destination_id]}的路上"


def _end_current(state: WorldState, agent_id: int) -> WorldState:
    """@brief 结束人物当前进行中的会话（没有则不变）。"""
    session = current_session(state, agent_id)
    if session is None:
        return state
    ended = session.model_copy(update={"status": SessionStatus.ENDED, "ended_at": state.current_time}, deep=True)
    return state.model_copy(update={"sessions": {**state.sessions, session.session_id: ended}})


def _next_session_id(state: WorldState, agent_id: int) -> str:
    """@brief 按人物已有会话数派生稳定身份（同一秒先后开始的会话互不冲突）。"""
    count = sum(1 for item in state.sessions.values() if item.agent_id == agent_id)
    return f"session-{agent_id}-{count + 1:04d}"


def _start_order(activity: ActivityState) -> tuple[int, str]:
    """@brief 活动的稳定开始顺序键。"""
    return activity.started_at, activity.activity_id
