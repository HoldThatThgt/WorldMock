"""@brief 活动的展示标题：活动关联对象的名称（docs/CONTRACTS.md 活动控制行的 title）。

生活活动取目录名称，观看与游戏取内容标题，工作取任务标题，通话取联系人在本人通讯录里的称谓，交谈取对方姓名；
等待、睡眠、出行、阅读邮件、浏览与使用没有标题（None）。标题写进活动快照与控制行，时间线据此显示中文名称
（docs/dev/SPEC-dayrun-fidelity.md 7.4）。名称都是场景与目录里的静态事实，索引在引擎建立时算一次。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from worldmock.life.models import ActivityKind, ActivityState
from worldmock.life.scenario import RuntimeScenario


@dataclass(frozen=True, slots=True)
class TitleIndex:
    """@brief 各类活动对象的名称索引。

    :param lifestyles: 生活活动条目身份 → 名称。
    :param contents: 内容与游戏身份 → 标题。
    :param tasks: 工作任务身份 → 任务标题。
    :param contacts: （人物身份，联系人身份）→ 该人物通讯录里的称谓。
    :param people: 同世界人物的不透明身份 agent:<id> → 姓名。
    """

    lifestyles: Mapping[str, str]
    contents: Mapping[str, str]
    tasks: Mapping[str, str]
    contacts: Mapping[tuple[int, str], str]
    people: Mapping[str, str]


def title_index(scenario: RuntimeScenario, lifestyle_names: Mapping[str, str],
                content_titles: Mapping[str, str]) -> TitleIndex:
    """@brief 由场景（任务、通讯录、人物）与已加载的生活活动名称、内容标题建立名称索引。"""
    return TitleIndex(
        lifestyles=lifestyle_names, contents=content_titles,
        tasks={item.task_id: item.title for item in scenario.tasks},
        contacts={(agent.agent_id, contact.contact_id): contact.salutation
                  for agent in scenario.agents for contact in agent.contacts},
        people={f"agent:{agent.agent_id}": agent.name for agent in scenario.agents})


def activity_title(activity: ActivityState, index: TitleIndex) -> str | None:
    """@brief 活动关联对象的展示标题；没有关联对象或名称不在索引里时返回 None。

    通话与交谈的对象记在活动数据里（contact_id、partner_id），其余活动的对象是 target_id。
    """
    if activity.kind == ActivityKind.CALL:
        return index.contacts.get((activity.agent_id, str(activity.data.get("contact_id"))))
    if activity.kind == ActivityKind.CHAT:
        return index.people.get(str(activity.data.get("partner_id")))
    if activity.target_id is None:
        return None
    if activity.kind == ActivityKind.LIFESTYLE:
        return index.lifestyles.get(activity.target_id)
    if activity.kind in (ActivityKind.WATCH, ActivityKind.PLAY_GAME):
        return index.contents.get(activity.target_id)
    if activity.kind == ActivityKind.WORK:
        return index.tasks.get(activity.target_id)
    return None
