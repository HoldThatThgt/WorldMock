"""@brief 在地点按名称做一件事：内容单元的列出与排序、下一条内容、动作行的时长与说明、单元被占的拒绝。

一条不属于系列的内容自成一个单元，同一系列的各集合成一个单元；单元名是人物写进 detail 的名称。做哪个单元由人物点名，
单元内从哪一条、哪个位置开始由共享进度决定，不抽随机数。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil

from worldmock.catalog.models import Catalog, Content
from worldmock.life.action_contract import binding_action
from worldmock.life.content_select import content_units, representative
from worldmock.life.guidance import Refusal
from worldmock.life.models import ActivityKind, ActivityStatus, AgentState, WorldState
from worldmock.life.readable import duration_phrase


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


def unit_binding(unit: Unit) -> dict:
    """@brief 单元在私有绑定里的动作行：detail 是单元名，另带内容身份与各集标题两个不进提示的键。"""
    titles = [] if unit.contents[0].series is None else [item.title for item in unit.contents]
    return {**binding_action("interact", unit.name), "content_ids": [item.content_id for item in unit.contents],
            "episode_titles": titles}


def next_content(contents: Sequence[Content], agent: AgentState) -> tuple[Content, float]:
    """@brief 点名这个单元此刻会开始的那条内容及其已做位置：独立内容是它自己，系列是完成轮数最少者里集号最小的一集。"""
    content = representative(tuple(contents), agent)
    progress = agent.content_progress.get(content.content_id)
    return content, 0.0 if progress is None else progress.position_seconds


def unit_facts(contents: Sequence[Content], agent: AgentState) -> dict:
    """@brief 工具层单元行的时长与说明：此刻开始会做多久；系列写第几集，上次没做完时写接着做。"""
    content, position = next_content(contents, agent)
    notes = []
    if content.series is not None:
        notes.append(f"第 {content.series.episode} 集，共 {len(contents)} 集")
    if position > 0:
        notes.append("上次没做完，接着做")
    return {"duration": duration_phrase(ceil(content.duration_seconds - position)),
            "note": "；".join(notes) or None}


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
