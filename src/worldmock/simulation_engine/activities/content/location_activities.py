"""@brief 在地点按名称做一件事：内容单元的列出与排序、下一条内容、动作行的时长与说明、被结束时记为做完一轮、单元被占与
未开放的拒绝。

一条不属于系列的内容自成一个单元，同一系列的各集合成一个单元；单元名是人物写进 detail 的名称。做哪个单元由人物点名，
单元内从哪一条、哪个位置开始由共享进度决定，不抽随机数。人物用 conclude_activity 结束这件事时整个单元记为做完一轮，
只有暂停才保留位置。内容写了 consumes_state 的单元凭状态，本人这个回调状态小于 1 时未开放：不列在工具层，照写名称
由受理拒绝（docs/design/04-simulation-engine.md「出行、内容与查询」）。进行中的地点系列在工具层活动目标行的 state 前面
写正在做第几集与供餐的说法，与单元行 note 同一写法（docs/design/06-agent.md「提示与工具」）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import logging
from math import ceil

from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.callback_contract import ConsumeOnStartResult
from worldmock.recording_engine.events.models import EventDraft
from worldmock.resource_engine.catalog.models import Catalog, Content
from worldmock.state.interaction.actions import binding_action
from worldmock.simulation_engine.observation.wording.content_prerequisite import (
    content_units,
    unit_target_episode,
)
from worldmock.simulation_engine.activities.content.content_select import representative
from worldmock.simulation_engine.observation.wording.guidance import Refusal
from worldmock.simulation_engine.world.world_updates import _put_agent
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.agent import AgentState, ContentProgress
from worldmock.state.world.world import WorldState
from worldmock.foundation.time.readable import duration_phrase

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Unit:
    """@brief 一个可点名的内容单元。"""

    #: 单元名：独立内容的标题，或系列的名称。
    name: str
    #: 单元键（排序用）：独立内容的身份，或系列里集号最小一集的身份。
    key: str
    #: 单元包含的内容，系列按集号升序。
    contents: tuple[Content, ...]


def location_units(catalog: Catalog, location_kind: str) -> tuple[Unit, ...]:
    """@brief 绑定到这个地点类型的全部内容单元，按单元键升序，完整列出。"""
    ids = catalog.contents_by_location_kind.get(location_kind, ())
    units = []
    for group in content_units(tuple(catalog.contents[key] for key in sorted(ids))):
        first = group[0]
        if first.series is None:
            units.append(Unit(first.title, first.content_id, group))
        else:
            episodes = tuple(sorted(group, key=lambda item: item.series.episode))
            units.append(Unit(first.series.title, episodes[0].content_id, episodes))
    return tuple(sorted(units, key=lambda unit: unit.key))


def unit_binding(unit: Unit, locked: bool) -> dict:
    """@brief 单元在私有绑定里的动作行：detail 是单元名，另带内容身份、各集标题与是否未开放三个不进提示的键。

    :param locked: 这个单元此刻对本人未开放（unit_locked）；未开放的行不列在工具层，名称解析仍认得它。
    """
    titles = [] if unit.contents[0].series is None else [item.title for item in unit.contents]
    return {**binding_action("interact", unit.name), "content_ids": [item.content_id for item in unit.contents],
            "episode_titles": titles, "locked": locked}


def unit_locked(contents: Sequence[Content], agent: AgentState) -> bool:
    """@brief 凭状态或一次性已完成的单元对本人未开放：一次性且最低轮数不小于 1，或写了 consumes_state 且该状态小于 1。"""
    if unit_target_episode(tuple(contents), agent) is None:
        return True
    state = contents[0].consumes_state
    return state is not None and agent.callback_states[state] < 1


def state_unavailable_reason(catalog: Catalog, state_name: str, subject: str) -> str:
    """@brief 按回调状态的重置与累加商品生成状态不可用提示原因，供地点内容、限定边与酒店共用。"""
    declaration = catalog.callback_states[state_name]
    goods = sorted(set(declaration.reset_on_goods) | set(declaration.gain_on_goods))
    if not goods:
        return f"{subject} is not available now."
    names = " or ".join(catalog.goods[key].name for key in goods)
    return f"{subject} is not available now; receiving {names} makes it available."


def state_unavailable_refusal(catalog: Catalog, state_name: str, subject: str) -> Refusal:
    """@brief 生成统一回调状态不足的 ACTIVITY_PRECONDITION_UNMET 拒绝，无额外下一步与改法。"""
    return Refusal("ACTIVITY_PRECONDITION_UNMET", state_unavailable_reason(catalog, state_name, subject),
                   unmet=("callback_state",))


def consume_callback_state(state: WorldState, agent_id: int, state_name: str,
                           request_content: tuple[str, str | None]) -> tuple[WorldState, EventDraft]:
    """@brief 开始动作时扣减 1 点回调状态并构造 consume_on_start 草稿（地点内容、限定边与酒店共用）。"""
    request_event_id, content_id = request_content
    agent = state.agents[agent_id]
    before = agent.callback_states[state_name]
    after = before - 1
    if after < 0:
        _LOGGER.error("Consumed state %s fell below zero", state_name)
        raise IntegrityError("Consumed state fell below zero")
    changed_agent = agent.model_copy(
        update={"callback_states": {**agent.callback_states, state_name: after}}, deep=True,
    )
    payload = ConsumeOnStartResult(state=state_name, content_id=content_id, before=before, after=after)
    draft = EventDraft(
        event_type="environment_event", occurred_at=state.current_time, agent_id=agent_id,
        source_kind="environment", visibility="system", caused_by=[request_event_id],
        data=payload.model_dump(mode="json"),
    )
    return _put_agent(state, changed_agent), draft


def locked_refusal(catalog: Catalog, agent: AgentState, action: Mapping) -> Refusal | None:
    """@brief 未开放：一次性单元已读完，或凭状态而本人这个状态小于 1 时拒绝，否则为 None；受理与工具层共用它。

    :param action: 单元在私有绑定里的动作行（detail 与 content_ids）。
    """
    contents = tuple(catalog.contents[key] for key in action["content_ids"])
    if not unit_locked(contents, agent):
        return None
    if unit_target_episode(contents, agent) is None:
        return Refusal("ACTIVITY_PRECONDITION_UNMET", f"{action['detail']} has already been finished.",
                       unmet=("content_repeatable",))
    return state_unavailable_refusal(catalog, contents[0].consumes_state, action["detail"])


def next_content(contents: Sequence[Content], agent: AgentState) -> tuple[Content, float]:
    """@brief 点名这个单元此刻会开始的那条内容及其已做位置：独立内容是它自己，系列是完成轮数最少者里集号最小的一集。"""
    content = representative(tuple(contents), agent)
    progress = agent.content_progress.get(content.content_id)
    return content, 0.0 if progress is None else progress.position_seconds


#: 完成效果 food 的两个档位在工具层 note 里的说法，次序即说法的先后。
FOOD_PHRASES = (("meal", "供一餐"), ("snack", "供一份零食"))


def unit_facts(contents: Sequence[Content], agent: AgentState) -> dict:
    """@brief 工具层单元行的时长与说明：此刻开始会做多久；说法按第几集、接着做、供一餐、供一份零食的先后以「；」相连。

    系列写第几集，上次没做完时写接着做；供餐的说法读各条内容的 completion_effects.food，与做到第几集无关
    （docs/design/06-agent.md「提示与工具」）。
    """
    content, position = next_content(contents, agent)
    notes = []
    if content.series is not None:
        notes.append(_episode_note(content, len(contents)))
    if position > 0:
        notes.append("上次没做完，接着做")
    notes.extend(_food_notes(contents))
    return {"duration": duration_phrase(ceil(content.duration_seconds - position)),
            "note": "；".join(notes) or None}


def running_series_note(catalog: Catalog, activity: ActivityState) -> str | None:
    """@brief 进行中的地点系列在工具层活动目标行的说法：「第 N 集，共 M 集」，有集供餐时再接供餐的说法，以「；」相连。

    只对进行中、不经 app（活动私有数据 app_id 为空）、当前内容是系列一集的交互活动写；其余活动（暂停或待收尾的、
    独立的一件事、一次 app 使用、出行、睡眠、购买与查询）为 None。先看种类、状态与 app_id，最后才读 content_id，
    因为别的种类的活动私有数据里没有它。N 是当前这一集的集号，M 是目录索引 contents_by_series 里这个系列的集数；
    供餐与单元行 note 同一个函数，不随做到第几集而变。

    :param activity: 人物的一项活动。
    """
    if (activity.kind != ActivityKind.INTERACT or activity.status != ActivityStatus.ACTIVE
            or activity.data["app_id"] is not None):
        return None
    content = catalog.contents[activity.data["content_id"]]
    if content.series is None:
        return None
    contents = [catalog.contents[key] for key in catalog.contents_by_series[content.series.title]]
    return "；".join([_episode_note(content, len(contents)), *_food_notes(contents)])


def _episode_note(content: Content, count: int) -> str:
    """@brief 系列一集的「第 N 集，共 M 集」：单元行 note 与进行中活动目标行的说法共用这一处写法。

    :param content: 系列的一集。
    :param count: 这个系列的集数。
    """
    return f"第 {content.series.episode} 集，共 {count} 集"


def _food_notes(contents: Sequence[Content]) -> list[str]:
    """@brief 供餐的说法：独立的一件事写「供一餐」或「供一份零食」；系列写「第 K 集供一餐」，K 是 food 为该档位的
    全部集号，升序，以「、」相连。没有 food 的单元没有这两种说法。"""
    notes = []
    for tier, phrase in FOOD_PHRASES:
        served = [item for item in contents if item.completion_effects is not None
                  and item.completion_effects.food == tier]
        if served and contents[0].series is None:
            notes.append(phrase)
        elif served:
            episodes = "、".join(str(item.series.episode) for item in sorted(served, key=lambda row: row.series.episode))
            notes.append(f"第 {episodes} 集{phrase}")
    return notes


def unit_done(agent: AgentState, catalog: Catalog, content_id: str, now: int) -> AgentState:
    """@brief 在地点做的事被人物结束：这条内容所在的单元记为做完一轮，下次点名从头做起（系列从第一集）。

    单元是人物能点名的一件事：独立内容是它自己，系列是目录索引 contents_by_series 里同名的全部各集。目标轮数取这条
    内容已完成的轮数加一（没有进度记录按 0 算）；单元里轮数低于目标的各条位置归零、轮数置为目标轮数，此前没有进度
    记录的新建一条、last_consumed_at 取 now，已有记录保留原值；轮数已不低于目标的不动。

    :param content_id: 被结束的活动此刻的当前内容（活动数据的 content_id）。
    :param now: 结束的时刻。
    :returns: 共享进度更新后的人物状态副本。
    """
    content = catalog.contents[content_id]
    ids = (content_id,) if content.series is None else catalog.contents_by_series[content.series.title]
    current = agent.content_progress.get(content_id)
    target = (0 if current is None else current.completed_rounds) + 1
    progress = dict(agent.content_progress)
    for key in ids:
        known = progress.get(key)
        if known is None or known.completed_rounds < target:
            consumed = now if known is None else known.last_consumed_at
            progress[key] = ContentProgress(content_id=key, position_seconds=0.0, completed_rounds=target,
                                            last_consumed_at=consumed)
    return agent.model_copy(update={"content_progress": progress}, deep=True)


def held_refusal(state: WorldState, agent_id: int, action: Mapping,
                 names: Mapping[str, str]) -> Refusal | None:
    """@brief 单元被占：它的某条内容是本人一项暂停中活动的当前内容时拒绝并点名那项活动，否则为 None。

    :param action: 单元在私有绑定里的动作行（detail 与 content_ids）。
    :param names: 活动身份到本轮可读名称。
    """
    held = set(action["content_ids"])
    for activity in sorted(state.activities.values(), key=lambda item: (item.started_at, item.activity_id)):
        if (activity.agent_id == agent_id and activity.kind == ActivityKind.INTERACT
                and activity.status == ActivityStatus.PAUSED and activity.data["content_id"] in held):
            name = names.get(activity.activity_id, activity.name)
            return Refusal("RESOURCE_CONFLICT", f"{action['detail']} is held by the paused activity {name}.",
                           f"Resume {name} with report_status, or conclude it.", ("content_unit",))
    return None
