"""@brief 动作开始、同系列接续与推送共享的只读内容推荐。"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from math import ceil
from random import Random
from worldmock.catalog.models import Catalog, Content
from worldmock.errors import BusinessRuleError
from worldmock.life.models import ActivityKind, ActivityStatus, AgentState, WorldState
from worldmock.life.sessions import current_session

@dataclass(frozen=True)
class ContentRequest:
    """@brief 唯一人物点名的内容 app 与本次实际活动；地点内容由人物点名单元，不经这里挑选。"""
    #: 实际消费内容的人物。
    agent_id: int
    #: 已登记的内容 app 身份。
    app_id: str
    #: 开始或接续的活动；生成推送时为空。
    activity_id: str | None = None
    #: 接续时刚完成的内容。
    previous_content_id: str | None = None

def eligible_contents(catalog: Catalog, state: WorldState, request: ContentRequest) -> tuple[Content, ...]:
    """@brief 内容 app 的候选只来自它自己绑定的内容，不再过滤工作指派或类别。"""
    return tuple(catalog.contents[key] for key in sorted(catalog.contents_by_app.get(request.app_id, ())))

def select_content(catalog: Catalog, state: WorldState, request: ContentRequest,
                   stream: Random) -> tuple[Content, str | None]:
    """@brief 推荐最多抽取一次，读取推送与共享历史但不修改任何状态。"""
    return select_from_contents(eligible_contents(catalog, state, request), state, request, stream)

def select_from_contents(contents: tuple[Content, ...], state: WorldState, request: ContentRequest,
                         stream: Random) -> tuple[Content, str | None]:
    """@brief 推送钉住、最近未完、已开始系列、未看单元、最低轮数依次优先。"""
    if not contents:
        logging.error("Content pool is exhausted")
        raise BusinessRuleError("CONTENT_EXHAUSTED")
    pinned = (_pinned_content(contents, state, request)
              if request.activity_id is not None and request.previous_content_id is None else None)
    if pinned is not None:
        return pinned
    agent = state.agents[request.agent_id]
    units = _exclude_reserved(content_units(contents), state, request)
    unfinished = _unfinished_content(units, agent)
    if unfinished is not None:
        return unfinished, None
    started = [unit for unit in units if unit[0].series is not None
               and any(_rounds(agent, item) > _unit_round(unit, agent) for item in unit)]
    if started:
        unit = min(started, key=lambda unit: (-max(_last_consumed(agent, item) for item in unit),
                                             representative(unit, agent).content_id))
        return representative(unit, agent), None
    unseen = [unit for unit in units if all(_last_consumed(agent, item) < 0 for item in unit)]
    minimum = min(_unit_round(unit, agent) for unit in units)
    candidates = unseen or [unit for unit in units if _unit_round(unit, agent) == minimum]
    representatives = sorted((representative(unit, agent) for unit in candidates), key=lambda item: item.content_id)
    return representatives[stream.randrange(len(representatives))], None

def content_duration_range(inputs, state: WorldState, agent_id: int, app_id: str) -> tuple[int, int] | None:
    """@brief 内容 app 的查询不抽样，优先显示推送或未完成余量，否则显示全长范围。"""
    catalog = inputs.catalog
    request = ContentRequest(agent_id, app_id)
    contents = eligible_contents(catalog, state, request)
    if not contents:
        duration = catalog.apps[app_id].browse_duration_seconds
        return None if duration is None else (duration, duration)
    agent = state.agents[agent_id]
    pinned = _pinned_content(contents, state, request)
    item = pinned[0] if pinned is not None else _unfinished_content(
        _exclude_reserved(content_units(contents), state, request), agent)
    if item is not None:
        remaining = ceil(item.duration_seconds - _position(agent, item))
        return remaining, remaining
    durations = tuple(item.duration_seconds for item in contents)
    return min(durations), max(durations)

def _pinned_content(contents: tuple[Content, ...], state: WorldState,
                    request: ContentRequest) -> tuple[Content, str] | None:
    """@brief 读取当前会话中同应用同动作且未释放的推送，不改变其状态。"""
    session = current_session(state, request.agent_id)
    push = None if session is None else session.app_pushes.get(request.app_id)
    if push is None or push.status == "released":
        return None
    item = next((content for content in contents if content.content_id == push.content_id), None)
    return None if item is None else (item, push.push_id)


def _unfinished_content(units: tuple[tuple[Content, ...], ...], agent: AgentState) -> Content | None:
    """@brief 取最近消费的未完成代表内容，时间并列时取最小内容身份。"""
    items = [representative(unit, agent) for unit in units]
    unfinished = [item for item in items if _position(agent, item) > 0]
    return min(unfinished, key=lambda item: (-_last_consumed(agent, item), item.content_id)) if unfinished else None


def content_units(contents: tuple[Content, ...]) -> tuple[tuple[Content, ...], ...]:
    """@brief 独立内容各自成单元，系列内各集按共同身份归为一个单元。"""
    series, singles = {}, []
    for item in contents:
        if item.series is None:
            singles.append((item,))
        else:
            key = item.series.title
            series.setdefault(key, []).append(item)
    return tuple([*singles, *(tuple(series[key]) for key in sorted(series))])


def _exclude_reserved(units: tuple[tuple[Content, ...], ...], state: WorldState,
                      request: ContentRequest) -> tuple[tuple[Content, ...], ...]:
    """@brief 有其他候选时先排除预留、再排除刚完成内容，代表集被排除时整系列退出。"""
    agent = state.agents[request.agent_id]
    reserved = {
        activity.data["content_id"] for activity in state.activities.values()
        if activity.agent_id == request.agent_id and activity.activity_id != request.activity_id
        and activity.kind == ActivityKind.INTERACT and activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}
        and activity.data["content_id"] is not None
    }
    session = current_session(state, request.agent_id)
    if session is not None:
        reserved.update(push.content_id for push in session.app_pushes.values() if push.status != "released")
    remaining = tuple(unit for unit in units if representative(unit, agent).content_id not in reserved)
    if remaining:
        units = remaining
    remaining = tuple(unit for unit in units if representative(unit, agent).content_id != request.previous_content_id)
    return remaining if remaining else units


def representative(unit: tuple[Content, ...], agent: AgentState) -> Content:
    """@brief 系列代表为最小轮数中的最低集，独立内容只有自身。"""
    return min(unit, key=lambda item: (_rounds(agent, item), item.series.episode if item.series is not None else 0,
                                      item.content_id))


def _unit_round(unit: tuple[Content, ...], agent: AgentState) -> int:
    """@brief 独立内容的完成轮数或系列各集最小轮数。"""
    return min(_rounds(agent, item) for item in unit)


def _rounds(agent: AgentState, item: Content) -> int:
    """@brief 尚无消费记录的内容处于零轮。"""
    progress = agent.content_progress.get(item.content_id)
    return 0 if progress is None else progress.completed_rounds


def _position(agent: AgentState, item: Content) -> float:
    """@brief 尚无消费记录的内容位置为零。"""
    progress = agent.content_progress.get(item.content_id)
    return 0 if progress is None else progress.position_seconds


def _last_consumed(agent: AgentState, item: Content) -> int:
    """@brief 没有正秒数消费的内容时间以负一表示，排在所有真实消费之前。"""
    progress = agent.content_progress.get(item.content_id)
    return -1 if progress is None else progress.last_consumed_at
