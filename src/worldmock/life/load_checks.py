"""@brief 场景加载时就拒绝的世界缺陷：地点类别越出场所类型表、本轮可能同时出现的可读名称重复
（docs/dev/SPEC-dayrun-fidelity.md 8.1）。

两项都在 semantics.load_runtime_scenario 执行，失败以 IntegrityError 拒绝加载并写明地点、类别或重复的名称，不留到
运行中途才由环境层（「Location kind has no place type label」）或观察投影（「interactive object labels must be
unique」）抛出。场所类型只认环境层的 PLACE_TYPES，不另建闭集；名称写法只认 object_labels，与观察投影同一来源。
名称按人物逐一核对：一个人物可能同时看到的全部对象名称（地点、门店商品、手边与他处持有物、商品目录、内容、游戏、
本人启用的生活活动、本人受理的工作、同世界其他人物、本人联系人、本人收件的邮件与「自己」）两两不同。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from worldmock.errors import IntegrityError
from worldmock.life.content import LifestyleItem, load_content_titles, load_lifestyle_items
from worldmock.life.environment_layer import PLACE_TYPES
from worldmock.life.object_labels import (CATALOG_LABEL, SELF_LABEL, mail_name, object_label, remote_holding_label,
                                          supply_label)
from worldmock.life.presentation import GAME_CATEGORY
from worldmock.life.scenario import RuntimeScenario

#: 报错时列出的重复名称条数上限（其余只计数）。
_REPORTED_DUPLICATES = 3


def check_location_kinds(scenario: RuntimeScenario) -> None:
    """@brief 所选地点的 kind 必须属于环境层的场所类型表 PLACE_TYPES，越界即拒绝并逐一写明地点与类别。

    kind 是交互菜单与环境层的场所分类，与活动环境 location_kinds 不是同一个概念。
    """
    outside = [f"{item.location_id} kind {item.kind!r}" for item in scenario.locations if item.kind not in PLACE_TYPES]
    if outside:
        message = (f"Selected locations use kinds that are not place types: {'; '.join(outside)};"
                   f" allowed kinds: {', '.join(sorted(PLACE_TYPES))}")
        logging.error(message)
        raise IntegrityError(message)


def check_visible_labels(scenario: RuntimeScenario, corpus_root: Path) -> None:
    """@brief 所选地点的显示名互不相同，且每个人物可能同时看到的对象名称两两不同；否则拒绝加载。

    同名地点须以地图字段 branch_label 消歧（显示名「名称（branch_label）」）。

    :param corpus_root: 场景的语料根（与引擎同一解析方式）。
    """
    _check_place_names(scenario)
    for agent_id, labels in visible_object_labels(scenario, corpus_root).items():
        duplicates = sorted((label, sources) for label, sources in labels.items() if len(sources) > 1)
        if duplicates:
            listed = "; ".join(f"{label!r} from {', '.join(sources)}"
                               for label, sources in duplicates[:_REPORTED_DUPLICATES])
            more = len(duplicates) - _REPORTED_DUPLICATES
            message = (f"Agent {agent_id} would see non-unique object labels: {listed}"
                       + (f"; and {more} more" if more > 0 else ""))
            logging.error(message)
            raise IntegrityError(message)


def visible_object_labels(scenario: RuntimeScenario, corpus_root: Path) -> dict[int, dict[str, list[str]]]:
    """@brief 每个人物可能同时看到的全部对象名称及其来源（人物 → 名称 → 来源列表，来源形如 location:<身份>）。

    名称写法与观察投影同一来源（object_labels）；内容与游戏标题、生活活动名称取自语料，与引擎运行时装载的是同一份。
    """
    titles = load_content_titles(scenario.contents, corpus_root)
    lifestyle = load_lifestyle_items(corpus_root, scenario.body.activity_met)
    shared = _world_labels(scenario, titles)
    return {agent.agent_id: _group(shared + _agent_labels(scenario, agent, lifestyle)) for agent in scenario.agents}


def _check_place_names(scenario: RuntimeScenario) -> None:
    """@brief 所选地点的显示名（运行时地点的 name）唯一；重名且无法以分店标签区分即拒绝，逐一写明重名与地点。"""
    places: dict[str, list[str]] = defaultdict(list)
    for location in scenario.locations:
        places[location.name].append(location.location_id)
    shared = [f"{name!r} ({', '.join(sorted(identities))})"
              for name, identities in sorted(places.items()) if len(identities) > 1]
    if shared:
        message = (f"Selected locations share display names: {'; '.join(shared)};"
                   " declare distinct branch_label values in the map")
        logging.error(message)
        raise IntegrityError(message)


def _world_labels(scenario: RuntimeScenario, titles: dict[str, str]) -> list[tuple[str, str]]:
    """@brief 与人物无关的对象名称及其来源：地点、门店商品、手边与他处持有物、商品目录、内容与游戏。"""
    places = {item.location_id: item.name for item in scenario.locations}
    products = {item.product_id: item.name for item in (*scenario.products, *scenario.inventory_items)}
    labels = [(object_label("location", name), f"location:{identity}") for identity, name in places.items()]
    labels += [(supply_label(places[item.location_id], products[item.product_id]), f"supply:{item.supply_id}")
               for item in scenario.supplies]
    labels += [(object_label("holding", name), f"product:{identity}") for identity, name in products.items()]
    labels += [(remote_holding_label(name, place), f"product:{identity}@{location}")
               for identity, name in products.items() for location, place in places.items()]
    labels.append((CATALOG_LABEL, "catalog:product_catalog"))
    labels += [(object_label("game" if item.kind == GAME_CATEGORY else "content", titles[item.item_id]),
                f"content:{item.item_id}") for item in scenario.contents]
    labels += [(object_label("game", item.title), f"game:{item.game_id}") for item in scenario.games]
    return labels


def _agent_labels(scenario: RuntimeScenario, agent, lifestyle: tuple[LifestyleItem, ...]) -> list[tuple[str, str]]:
    """@brief 一个人物才会看到的对象名称及其来源：本人受理的工作、启用的生活活动、同世界其他人物、本人联系人、
    本人收件的场景邮件与「自己」。"""
    enabled = None if agent.lifestyle_activities is None else set(agent.lifestyle_activities)
    labels = [(object_label("work_task", item.title), f"task:{item.task_id}") for item in scenario.tasks
              if item.assignee_agent_id == f"agent:{agent.agent_id}"]
    labels += [(object_label("lifestyle", item.name), f"lifestyle:{item.activity_id}") for item in lifestyle
               if enabled is None or item.activity_id in enabled]
    labels += [(object_label("person", other.name), f"agent:{other.agent_id}") for other in scenario.agents
               if other.agent_id != agent.agent_id]
    labels += [(object_label("contact", item.salutation), f"contact:{item.contact_id}") for item in agent.contacts]
    labels += [(object_label("mail", mail_name(item.subject, int(item.sent_at.timestamp()), scenario.timezone)),
                f"message:{item.message_id}") for item in scenario.messages
               if item.recipient_agent_id == agent.agent_id]
    labels.append((SELF_LABEL, f"agent:{agent.agent_id}"))
    return labels


def _group(labels: list[tuple[str, str]]) -> dict[str, list[str]]:
    """@brief 按名称归并来源（保留出现顺序）。"""
    grouped: dict[str, list[str]] = defaultdict(list)
    for label, source in labels:
        grouped[label].append(source)
    return grouped
