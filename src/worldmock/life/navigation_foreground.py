"""@brief 导航 app 在不在前台：每次世界提交之前比较提交前后的世界判定一次，关闭时写一条前台区间。

导航 app 是绑定了某种出行方式的功能 app，一趟出行选一个、整趟不换。路上只要没有进行中的槽位 2 活动，导航就在前台；
槽位 2 的活动一开始导航就关，那项活动停止执行时抽一次决定回不回来。导航此刻是否在前台保存在人物状态里，
事件只在关闭的那一秒写。本模块都是只读写世界副本的纯函数，人物看不到这些记录。
"""

from __future__ import annotations

from worldmock.footprints.models import EventDraft
from worldmock.life.models import (ActivityKind, ActivityState, NavigationForeground, WorldState, active_activities,
                                   active_side_activities)
from worldmock.life.randomness import random_stream


def foreground_event(activity: ActivityState, app_id: str, step: str, interval: tuple[int, int]) -> EventDraft:
    """@brief 一段前台区间在它关闭的那一秒写下的事件：系统可见，起因是所属活动受理的结果。

    :param activity: 所属活动：导航是出行活动，下单和支付是购买活动。
    :param step: navigation、ordering 或 payment。
    :param interval: 打开与关闭的时刻，关闭时刻就是事件的发生时刻。
    """
    opened_at, closed_at = interval
    return EventDraft(event_type="environment_event", occurred_at=closed_at, agent_id=activity.agent_id,
                      activity_id=activity.activity_id, caused_by=[activity.data["accepted_event_id"]],
                      visibility="system", source_kind="environment",
                      data={"kind": "app_foreground", "app_id": app_id, "step": step,
                            "interval_start": opened_at, "interval_end": closed_at})


def navigation_app(state: WorldState, trip: ActivityState, catalog) -> str | None:
    """@brief 这趟出行的导航 app：从绑定了它的出行方式的功能 app 里按出行活动的稳定键等概率取一个，没有绑定为空。"""
    candidates = catalog.apps_by_travel_method.get(trip.travel_method_id, ())
    if not candidates:
        return None
    return random_stream(state.random_state, f"function-app:{trip.activity_id}").choice(candidates)


def settle_navigation(before: WorldState, after: WorldState, catalog) -> tuple[WorldState, list[EventDraft]]:
    """@brief 世界提交之前的导航规则：先关闭、再打开，返回写好人物状态的世界与关闭时写下的前台事件。

    :param before: 提交前的世界。
    :param after: 待提交的世界；当前时刻取它的时钟。
    """
    agents, drafts = dict(after.agents), []
    for agent_id in sorted(after.agents):
        foreground, events = _closed(after, agent_id)
        if foreground is None:
            foreground = _opened(before, after, agent_id, catalog)
        drafts.extend(events)
        if foreground != after.agents[agent_id].navigation_foreground:
            agents[agent_id] = after.agents[agent_id].model_copy(update={"navigation_foreground": foreground})
    if agents == after.agents:
        return after, drafts
    return after.model_copy(update={"agents": agents}), drafts


def _trip(state: WorldState, agent_id: int) -> ActivityState | None:
    """@brief 人物进行中的出行；没有时为空。"""
    return next((item for item in active_activities(state, agent_id) if item.kind == ActivityKind.TRAVEL), None)


def _closed(after: WorldState, agent_id: int) -> tuple[NavigationForeground | None, list[EventDraft]]:
    """@brief 关闭：导航在前台，而之后已没有这趟进行中的出行，或者之后有进行中的槽位 2 活动。

    :return: （这一步之后仍在前台的那一段，关闭了或原本就不在前台时为空；关闭时写下的事件）。区间长度为 0 时不写事件。
    """
    foreground = after.agents[agent_id].navigation_foreground
    if foreground is None:
        return None, []
    trip = _trip(after, agent_id)
    travelling = trip is not None and trip.activity_id == foreground.activity_id
    if travelling and not active_side_activities(after, agent_id):
        return foreground, []
    now = after.current_time
    if now <= foreground.opened_at:
        return None, []
    owner = after.activities[foreground.activity_id]
    return None, [foreground_event(owner, foreground.app_id, "navigation", (foreground.opened_at, now))]


def _opened(before: WorldState, after: WorldState, agent_id: int, catalog) -> NavigationForeground | None:
    """@brief 打开：导航不在前台、之后有进行中的出行、没有进行中的槽位 2 活动、这趟出行有导航候选。

    出行是本次提交里刚开始的就打开；否则只有之前有一项进行中的槽位 2 活动（它在本次提交里停止了执行）时抽一位，
    抽到 1 回到前台。打开只写人物状态，不写事件。
    """
    trip = _trip(after, agent_id)
    if trip is None or active_side_activities(after, agent_id):
        return None
    app_id = navigation_app(after, trip, catalog)
    if app_id is None:
        return None
    if trip.activity_id in before.activities:
        stopped = active_side_activities(before, agent_id)
        if not stopped:
            return None
        key = f"navigation-return:{trip.activity_id}:{stopped[0].activity_id}"
        if random_stream(after.random_state, key).getrandbits(1) != 1:
            return None
    return NavigationForeground(activity_id=trip.activity_id, app_id=app_id, opened_at=after.current_time)
