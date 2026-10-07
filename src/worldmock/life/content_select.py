"""@brief 动作开始、娱乐接续与推送共享的只读内容选择规则。"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from random import Random

from worldmock.catalog.models import Catalog, Content
from worldmock.errors import BusinessRuleError
from worldmock.life.models import ActivityKind, ActivityStatus, AgentState, WorldState
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.sessions import at_stay, current_session


@dataclass(frozen=True)
class ContentRequest:
    """@brief 人物点名的动作应用与本次选择的活动上下文。"""

    #: 实际消费内容的人物。
    agent_id: int
    #: 已登记且已完成名称解析的动作。
    action_id: str
    #: 人物明确点名的应用，无应用动作写空。
    app_id: str | None
    #: 开始或接续的活动；生成推送时为空。
    activity_id: str | None = None
    #: 接续时刚完成的内容；开始或生成推送时为空。
    previous_content_id: str | None = None


def eligible_contents(catalog: Catalog, scenario: RuntimeScenario, state: WorldState,
                      request: ContentRequest) -> tuple[Content, ...]:
    """@brief 返回符合动作应用绑定、人物指派、完成轮数和工作地点的排序内容。"""
    return _eligible_contents(catalog, scenario, state, request, True)


def required_work_location(catalog: Catalog, scenario: RuntimeScenario, state: WorldState,
                           request: ContentRequest) -> str | None:
    """@brief 仅因不在工作地点导致合格内容为空时返回该地点，供准入与工具层共用。"""
    if eligible_contents(catalog, scenario, state, request):
        return None
    if not _eligible_contents(catalog, scenario, state, request, False):
        return None
    config = next(agent for agent in scenario.agents if agent.agent_id == request.agent_id)
    return config.work_schedule.workplace_location_id


def select_content(catalog: Catalog, scenario: RuntimeScenario, state: WorldState,
                   request: ContentRequest, stream: Random) -> tuple[Content, str | None]:
    """@brief 按推送、未完成、系列、未看与轮数选择一次，不修改状态或打开推送。"""
    contents = eligible_contents(catalog, scenario, state, request)
    if not contents:
        raise BusinessRuleError("CONTENT_EXHAUSTED")
    pinned = (_pinned_content(contents, state, request)
              if request.activity_id is not None and request.previous_content_id is None else None)
    if pinned is not None:
        return pinned
    agent = state.agents[request.agent_id]
    units = _exclude_reserved(_content_units(contents), state, request)
    unfinished = _unfinished_content(units, agent)
    if unfinished is not None:
        return unfinished, None
    started = [unit for unit in units if unit[0].series is not None
               and any(_rounds(agent, item) > _unit_round(unit, agent) for item in unit)]
    if started:
        unit = min(started, key=lambda unit: (-max(_last_consumed(agent, item) for item in unit),
                                             _representative(unit, agent).content_id))
        return _representative(unit, agent), None
    unseen = [unit for unit in units if all(_last_consumed(agent, item) < 0 for item in unit)]
    if unseen:
        candidates = unseen
    else:
        minimum = min(_unit_round(unit, agent) for unit in units)
        candidates = [unit for unit in units if _unit_round(unit, agent) == minimum]
    representatives = sorted((_representative(unit, agent) for unit in candidates), key=lambda item: item.content_id)
    return representatives[stream.randrange(len(representatives))], None


def content_duration_range(catalog: Catalog, scenario: RuntimeScenario, state: WorldState,
                           request: ContentRequest) -> tuple[int, int] | None:
    """@brief 推送或最近未完成内容显示剩余秒数，否则显示合格内容全长范围，不抽取。"""
    contents = eligible_contents(catalog, scenario, state, request)
    if not contents:
        return None
    agent = state.agents[request.agent_id]
    pinned = _pinned_content(contents, state, request) if request.previous_content_id is None else None
    item = pinned[0] if pinned is not None else _unfinished_content(
        _exclude_reserved(_content_units(contents), state, request), agent)
    if item is not None:
        remaining = ceil(item.duration_seconds - _position(agent, item))
        return remaining, remaining
    durations = tuple(item.duration_seconds for item in contents)
    return min(durations), max(durations)


def _eligible_contents(catalog: Catalog, scenario: RuntimeScenario, state: WorldState,
                       request: ContentRequest, check_location: bool) -> tuple[Content, ...]:
    """@brief 派生场景指派集合，未指派的工作内容与其他内容均可按轮重复。"""
    action = catalog.actions[request.action_id]
    if bool(action.app_ids) != (request.app_id is not None):
        return ()
    ids = (catalog.contents_by_action[request.action_id] if request.app_id is None
           else catalog.contents_by_action_app.get((request.action_id, request.app_id), ()))
    config = next(agent for agent in scenario.agents if agent.agent_id == request.agent_id)
    assigned = {content_id for person in scenario.agents for content_id in person.work_content_ids}
    own = set(config.work_content_ids)
    agent = state.agents[request.agent_id]
    result = []
    for content_id in sorted(ids):
        item = catalog.contents[content_id]
        if content_id in assigned:
            if content_id not in own or _rounds(agent, item) >= 1:
                continue
            if check_location and not at_stay(state, request.agent_id, config.work_schedule.workplace_location_id):
                continue
        result.append(item)
    return tuple(result)


def _pinned_content(contents: tuple[Content, ...], state: WorldState,
                    request: ContentRequest) -> tuple[Content, str] | None:
    """@brief 读取当前会话中同应用同动作且未释放的推送，不改变其状态。"""
    if request.app_id is None:
        return None
    session = current_session(state, request.agent_id)
    push = None if session is None else session.app_pushes.get(request.app_id)
    if push is None or push.status == "released":
        return None
    item = next((content for content in contents if content.content_id == push.content_id), None)
    return None if item is None else (item, push.push_id)


def _unfinished_content(units: tuple[tuple[Content, ...], ...], agent: AgentState) -> Content | None:
    """@brief 取最近消费的未完成代表内容，时间并列时取最小内容身份。"""
    items = [_representative(unit, agent) for unit in units]
    unfinished = [item for item in items if _position(agent, item) > 0]
    return min(unfinished, key=lambda item: (-_last_consumed(agent, item), item.content_id)) if unfinished else None


def _content_units(contents: tuple[Content, ...]) -> tuple[tuple[Content, ...], ...]:
    """@brief 独立内容各自成单元，系列内各集按共同身份归为一个单元。"""
    series, singles = {}, []
    for item in contents:
        if item.series is None:
            singles.append((item,))
        else:
            key = (item.action_id, item.series.title)
            series.setdefault(key, []).append(item)
    return tuple([*singles, *(tuple(series[key]) for key in sorted(series))])


def _exclude_reserved(units: tuple[tuple[Content, ...], ...], state: WorldState,
                      request: ContentRequest) -> tuple[tuple[Content, ...], ...]:
    """@brief 有其他候选时先排除预留、再排除刚完成内容，代表集被排除时整系列退出。"""
    agent = state.agents[request.agent_id]
    reserved = {
        activity.data["content_id"] for activity in state.activities.values()
        if activity.agent_id == request.agent_id and activity.activity_id != request.activity_id
        and activity.kind == ActivityKind.ACTION and activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}
    }
    session = current_session(state, request.agent_id)
    if session is not None:
        reserved.update(push.content_id for push in session.app_pushes.values() if push.status != "released")
    remaining = tuple(unit for unit in units if _representative(unit, agent).content_id not in reserved)
    if remaining:
        units = remaining
    remaining = tuple(unit for unit in units if _representative(unit, agent).content_id != request.previous_content_id)
    return remaining if remaining else units


def _representative(unit: tuple[Content, ...], agent: AgentState) -> Content:
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
