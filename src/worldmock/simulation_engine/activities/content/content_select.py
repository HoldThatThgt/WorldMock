"""@brief 打开顺序、接续顺序与推送共享的只读内容推荐。"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from random import Random
from zoneinfo import ZoneInfo
from worldmock.resource_engine.catalog.models import Catalog, Content, Location
from worldmock.simulation_engine.observation.wording.content_prerequisite import (
    content_units,
    prerequisite_unmet,
    unit_target_episode,
)
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.agent import AgentState
from worldmock.state.world.world import WorldState
from worldmock.simulation_engine.activities.lifecycle.sessions import current_session

@dataclass(frozen=True)
class ContentRequest:
    """@brief 唯一人物点名的内容 app 与本次实际活动；地点内容由人物点名单元，不经这里挑选。"""
    #: 实际消费内容的人物。
    agent_id: int
    #: 已登记的内容 app 身份。
    app_id: str
    #: 判定内容前提所用的停留地点；人在路上时为空。
    stay_location_id: str | None
    #: 开始或接续的活动；生成推送时为空。
    activity_id: str | None = None
    #: 接续时刚完成的内容。
    previous_content_id: str | None = None

def eligible_contents(catalog: Catalog, state: WorldState, request: ContentRequest) -> tuple[Content, ...]:
    """@brief 合格内容：按完整单元定代表并检验未耗尽及此刻前提，保留所有合格单元全部分集并按内容身份升序；不抽随机数。"""
    stay = None if request.stay_location_id is None else catalog.locations[request.stay_location_id]
    moment = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    agent = state.agents[request.agent_id]
    contents = tuple(catalog.contents[key] for key in sorted(catalog.contents_by_app.get(request.app_id, ())))
    kept = []
    for unit in content_units(contents):
        item = unit_target_episode(unit, agent)
        if item is None:
            continue
        if not prerequisite_unmet(item, stay, moment):
            kept.extend(unit)
    return tuple(sorted(kept, key=lambda entry: entry.content_id))

def select_content(catalog: Catalog, state: WorldState, request: ContentRequest,
                   stream: Random) -> tuple[Content, str | None]:
    """@brief 推荐最多抽取一次，读取推送与共享历史但不修改任何状态。"""
    return select_from_contents(eligible_contents(catalog, state, request), state, request, stream)

def select_from_contents(contents: tuple[Content, ...], state: WorldState, request: ContentRequest,
                         stream: Random) -> tuple[Content, str | None]:
    """@brief 推送钉住、最近未完、已开始系列、未看单元、最低轮数依次优先；候选是非空的合格内容。"""
    pinned = (pinned_push_content(contents, state, request)
              if request.activity_id is not None and request.previous_content_id is None else None)
    if pinned is not None:
        return pinned
    agent = state.agents[request.agent_id]
    active = tuple(unit for unit in content_units(contents) if unit_target_episode(unit, agent) is not None)
    units = _exclude_reserved(active or content_units(contents), state, request)
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

def next_episode(catalog: Catalog, agent: AgentState, content: Content, rounds: int,
                 stay_moment: tuple[Location | None, datetime]) -> Content | None:
    """@brief 同一系列里接在这一集之后的下一集：集号更大且轮数低于 rounds，同时满足可重复与此刻前提时返回它。

    :param rounds: 刚做完的这一集做完之后的完成轮数。
    :param stay_moment: 本刻停留地点与本地时刻二元组，用于校验紧邻下集的地点、城市与时段前提。
    """
    if content.series is None:
        return None
    candidates = [catalog.contents[key] for key in catalog.contents_by_series[content.series.title]
                  if catalog.contents[key].series.episode > content.series.episode
                  and _rounds(agent, catalog.contents[key]) < rounds]
    if not candidates:
        return None
    candidate = candidates[0]
    if not candidate.repeatable and _rounds(agent, candidate) >= 1:
        return None
    stay, local_moment = stay_moment
    return None if prerequisite_unmet(candidate, stay, local_moment) else candidate

def autoplay_content(catalog: Catalog, state: WorldState, request: ContentRequest, stream: Random) -> Content | None:
    """@brief 接续顺序：一次 app 使用里一条放完、同一系列没有下一集时，挑同一 app 里接着放的那一条，没有时为 None。

    在此刻合格的内容里分单元，硬去掉刚放完的那一单元，取每单元将要放的那一条（代表集），只留可推送的；刚放完的是系列的
    一集时，取开始了没看完的另一部系列里最近看过的那部的代表集，不取随机数；是独立内容时，在没有进度记录的独立内容里
    按内容身份升序用 stream 抽一次。预留最后硬去掉，去掉后没有候选就是 None，不退回轮数最少的、不回放。不改状态。

    :param request: previous_content_id 是刚放完的那一条，stay_location_id 是抽取这一秒本人的停留（路上为 None）。
    :param stream: 按 {activity_id}:{draw_index} 建的随机流，draw_index 是加一之后的值。
    """
    agent = state.agents[request.agent_id]
    finished = catalog.contents[request.previous_content_id]
    units = [unit for unit in content_units(eligible_contents(catalog, state, request))
             if not _same_unit(unit, finished)]
    pairs = [(unit, representative(unit, agent)) for unit in units]
    pairs = [(unit, item) for unit, item in pairs if item.pushable]
    reserved = _reserved_ids(state, request)
    if finished.series is not None:
        return _started_series(pairs, agent, reserved)
    unseen = [item for unit, item in pairs if item.series is None and item.content_id not in agent.content_progress]
    unseen = sorted((item for item in unseen if item.content_id not in reserved), key=lambda item: item.content_id)
    return unseen[stream.randrange(len(unseen))] if unseen else None

def _started_series(pairs: list[tuple[tuple[Content, ...], Content]], agent: AgentState,
                    reserved: set[str]) -> Content | None:
    """@brief 开始了没看完、代表集可推送且没被预留的系列里，最近看过的那部的代表集；同一时刻取代表集身份小的，没有时为 None。

    :param pairs: （单元，代表集），已去掉刚放完的单元与代表集不可推送的。
    """
    started = [(unit, item) for unit, item in pairs
               if item.series is not None and item.content_id not in reserved and _started(unit, agent)]
    if not started:
        return None
    return min(started, key=lambda pair: (-max(_last_consumed(agent, item) for item in pair[0]),
                                         pair[1].content_id))[1]

def _started(unit: tuple[Content, ...], agent: AgentState) -> bool:
    """@brief 系列单元开始了没看完：有一集的轮数多于单元各集的最低轮数，或有一集保存的位置大于零。"""
    low = _unit_round(unit, agent)
    return any(_rounds(agent, item) > low or _position(agent, item) > 0 for item in unit)

def _same_unit(unit: tuple[Content, ...], finished: Content) -> bool:
    """@brief 单元是否就是刚放完的那一条所在的单元：系列按系列名，独立内容按内容身份。"""
    first = unit[0]
    if finished.series is None:
        return first.series is None and first.content_id == finished.content_id
    return first.series is not None and first.series.title == finished.series.title

@dataclass(frozen=True)
class OpeningDuration:
    """@brief 内容 app 行时长的唯一判定结果：此刻打开会不会先放一条已知内容，以及要写的秒数。"""
    #: 此刻打开会先放一条已知内容（会被钉住的推送，或排除预留之后最近没放完的那一条）。
    known_first: bool
    #: known_first 为真时是那一条的余量（整秒向上取整）；否则是此刻全部合格内容全长的最小值（秒）。
    shortest: int
    #: known_first 为真时等于 shortest；否则是此刻全部合格内容全长的最大值（秒）。
    longest: int

def opening_duration(catalog: Catalog, state: WorldState, request: ContentRequest) -> OpeningDuration | None:
    """@brief 内容 app 行的时长写哪一种只在这里判定，不抽样、不改状态；内容此刻都不合格（没有登记内容的 app 也是这样）
    时为 None。

    打开顺序排在最前的两步（推送钉住、最近未完）用的是同一对助手，所以 known_first 为真时的那一条就是此刻打开这个 app
    会开始的那一条，秒数是它的余量；否则取此刻全部合格内容（含不可推送的）全长的最小值与最大值。
    """
    contents = eligible_contents(catalog, state, request)
    if not contents:
        return None
    agent = state.agents[request.agent_id]
    pinned = pinned_push_content(contents, state, request)
    item = pinned[0] if pinned is not None else _unfinished_content(
        _exclude_reserved(content_units(contents), state, request), agent)
    if item is not None:
        remaining = ceil(item.duration_seconds - _position(agent, item))
        return OpeningDuration(True, remaining, remaining)
    durations = tuple(item.duration_seconds for item in contents)
    return OpeningDuration(False, min(durations), max(durations))

def pinned_push_content(contents: tuple[Content, ...], state: WorldState,
                        request: ContentRequest) -> tuple[Content, str] | None:
    """@brief 此刻打开会钉住的推送内容：当前会话有未释放推送且所在单元代表集可推送时返回目标内容。

    :param contents: 这个 app 此刻的合格内容。
    """
    session = current_session(state, request.agent_id)
    push = None if session is None else session.app_pushes.get(request.app_id)
    if push is None or push.status == "released":
        return None
    agent = state.agents[request.agent_id]
    for unit in content_units(contents):
        target = unit_target_episode(unit, agent)
        if target is None or not target.pushable:
            continue
        match = next((item for item in unit if item.content_id == push.content_id), None)
        if match is not None and match.pushable:
            return match, push.push_id
    return None


def _unfinished_content(units: tuple[tuple[Content, ...], ...], agent: AgentState) -> Content | None:
    """@brief 取最近消费的未完成代表内容，时间并列时取最小内容身份。"""
    items = [representative(unit, agent) for unit in units]
    unfinished = [item for item in items if _position(agent, item) > 0]
    return min(unfinished, key=lambda item: (-_last_consumed(agent, item), item.content_id)) if unfinished else None


def _exclude_reserved(units: tuple[tuple[Content, ...], ...], state: WorldState,
                      request: ContentRequest) -> tuple[tuple[Content, ...], ...]:
    """@brief 有其他候选时先排除预留、再排除刚完成内容，代表集被排除时整系列退出。"""
    agent = state.agents[request.agent_id]
    reserved = _reserved_ids(state, request)
    remaining = tuple(unit for unit in units if representative(unit, agent).content_id not in reserved)
    if remaining:
        units = remaining
    remaining = tuple(unit for unit in units if representative(unit, agent).content_id != request.previous_content_id)
    return remaining if remaining else units


def _reserved_ids(state: WorldState, request: ContentRequest) -> set[str]:
    """@brief 预留的内容身份：本人别的进行中或暂停的 interact 活动此刻的当前内容，以及当前会话（停留或路上）里
    所有未释放推送指向的内容，不分 app。"""
    reserved = {
        activity.data["content_id"] for activity in state.activities.values()
        if activity.agent_id == request.agent_id and activity.activity_id != request.activity_id
        and activity.kind == ActivityKind.INTERACT and activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}
    }
    session = current_session(state, request.agent_id)
    if session is not None:
        reserved.update(push.content_id for push in session.app_pushes.values() if push.status != "released")
    return reserved


def representative(unit: tuple[Content, ...], agent: AgentState) -> Content:
    """@brief 系列代表为最小轮数中的最低集，独立内容只有自身。"""
    return min(unit, key=lambda item: (_rounds(agent, item),
                                      0 if item.series is None else item.series.episode,
                                      item.content_id))


def _unit_round(unit: tuple[Content, ...], agent: AgentState) -> int:
    """@brief 独立内容的完成轮数或系列各集最小轮数。"""
    return min(_rounds(agent, item) for item in unit)


def _rounds(agent: AgentState, item: Content) -> int:
    """@brief 尚无消费记录的内容处于零轮。"""
    return (0 if item.content_id not in agent.content_progress
            else agent.content_progress[item.content_id].completed_rounds)


def _position(agent: AgentState, item: Content) -> float:
    """@brief 尚无消费记录的内容位置为零。"""
    return (0 if item.content_id not in agent.content_progress
            else agent.content_progress[item.content_id].position_seconds)


def _last_consumed(agent: AgentState, item: Content) -> int:
    """@brief 没有正秒数消费的内容时间以负一表示，排在所有真实消费之前。"""
    return (-1 if item.content_id not in agent.content_progress
            else agent.content_progress[item.content_id].last_consumed_at)
