"""@brief 场景加载时就拒绝的世界缺陷：地点类别越出场所类型表、本轮可能同时出现的可读名称重复
（docs/dev/SPEC-dayrun-fidelity.md 8.1），以及购买渠道的准入（3.3）。

都在 semantics.load_runtime_scenario 执行，失败以 IntegrityError 拒绝加载并写明地点、类别或重复的名称，不留到
运行中途才由环境层（「Location kind has no place type label」）或观察投影（「interactive object labels must be
unique」）抛出。场所类型只认环境层的 PLACE_TYPES，不另建闭集；名称写法只认 object_labels，与观察投影同一来源。
名称按人物逐一核对：一个人物可能同时看到的全部对象名称（地点、门店商品、手边与他处持有物、商品目录、内容、游戏、
本人启用的生活活动、本人受理的工作、同世界其他人物、本人联系人、本人收件的邮件与「自己」）两两不同。

购买渠道从场景钉定的手机配置派生（裁决 S7，profile.coverage_apps.purchase_channels）；配置的身份、结构或执行证据核验
失败时不派生渠道（新建运行不因手机配置核验失败而失败，与覆盖率同一口径；App 名因此报 SOURCE_NOT_FOUND），另写一条英文
warning 日志；核验通过的渠道按准入判定：称呼在渠道之间不重复、不等于任何地点的显示名，成员门店是选入的地点并至少有一条
供给。

人物工作安排的跨引用（docs/dev/SPEC-errands-workday.md 4.3）同样在这里拒绝：工作地点是所选地点；与场景日历交叉核对，
只核对落在场景时间窗内的日历日期——补班日必须有班次，春节、病假、事假日必须不当班，加班机会日与聚餐日不作要求；时间窗
外的例外日期不拒绝。日历是场景级的，对所有声明了工作安排的人物同时生效；班次形状错误留在场景模型的校验器。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.life.content import LifestyleItem, load_content_titles, load_lifestyle_items
from worldmock.life.environment_layer import PLACE_TYPES
from worldmock.life.name_resolution import channel_key, fold
from worldmock.life.object_labels import (CATALOG_LABEL, SELF_LABEL, mail_name, object_label, remote_holding_label,
                                          supply_label)
from worldmock.life.presentation import GAME_CATEGORY
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.work_schedule import shift_on

#: 报错时列出的重复名称条数上限（其余只计数）。
_REPORTED_DUPLICATES = 3

#: 必须有班次的日历字段（补班日）与必须不当班的日历字段（春节、病假、事假日），按 CalendarConfig 的属性名；
#: 加班机会日与聚餐日不作要求。
_SHIFT_REQUIRED_DATES = ("makeup_work_dates",)
_OFF_DUTY_DATES = ("spring_festival_dates", "sick_leave_date", "personal_leave_date")


def load_purchase_channels(scenario: RuntimeScenario, corpus_root: Path) -> tuple:
    """@brief 场景的购买渠道（docs/dev/SPEC-dayrun-fidelity.md 3.3）：从钉定的手机配置派生并按准入判定；没有钉定手机配置
    时没有渠道；配置核验失败时不派生渠道并写一条英文 warning 日志（不使加载失败）。

    :param corpus_root: 场景的语料根（手机配置在其中登记）。
    :returns: profile.coverage_apps.PurchaseChannel 的元组，按渠道标识排序。
    :raises IntegrityError: 核验通过的渠道不满足准入（check_channel_admission）。
    """
    from worldmock.profile.coverage_apps import purchase_channels, verified_phone_config  # 画像包导入较重，按需导入

    try:
        config = verified_phone_config(scenario, corpus_root)
    except IntegrityError as error:
        logging.warning("Phone simulation config failed verification; no purchase channels are derived: %s", error)
        return ()
    channels = () if config is None else purchase_channels(config, scenario)
    check_channel_admission(scenario, channels)
    return channels


def check_channel_admission(scenario: RuntimeScenario, channels) -> None:
    """@brief 购买渠道的准入（3.3，两种来源相同）：称呼非空，在渠道之间不重复（去掉空白、不分大小写比较），也不等于任何
    地点的显示名（按同一比较或地点名的无损归一比较）；每个渠道至少一家成员门店，成员门店是场景选入的地点并至少有一条
    供给。任一不满足即拒绝加载，逐一写明渠道、称呼与地点。"""
    problems = [*_channel_name_problems(scenario, channels), *_channel_store_problems(scenario, channels)]
    if problems:
        message = f"Purchase channels are not admissible: {'; '.join(problems)}"
        logging.error(message)
        raise IntegrityError(message)


def _channel_name_problems(scenario: RuntimeScenario, channels) -> list[str]:
    """@brief 渠道称呼的问题：空称呼、跨渠道重复的称呼、与地点显示名相同的称呼。"""
    problems: list[str] = []
    owners: dict[str, str] = {}
    for channel in channels:
        if not channel.names or any(not name.strip() for name in channel.names):
            problems.append(f"channel {channel.channel_id} has an empty name")
        for name in channel.names:
            owner = owners.setdefault(channel_key(name), channel.channel_id)
            if owner != channel.channel_id:
                problems.append(f"name {name!r} is shared by channels {owner} and {channel.channel_id}")
            problems += [f"channel {channel.channel_id} name {name!r} equals location {item.location_id}"
                         for item in scenario.locations
                         if channel_key(name) == channel_key(item.name) or fold(name) == fold(item.name)]
    return problems


def _channel_store_problems(scenario: RuntimeScenario, channels) -> list[str]:
    """@brief 成员门店的问题：渠道没有成员门店、成员门店不是选入的地点、成员门店没有供给。"""
    selected = {item.location_id for item in scenario.locations}
    stocked = {item.location_id for item in scenario.supplies}
    problems: list[str] = []
    for channel in channels:
        if not channel.store_location_ids:
            problems.append(f"channel {channel.channel_id} has no member store")
        problems += [f"channel {channel.channel_id} member store {store} is not a selected location"
                     for store in channel.store_location_ids if store not in selected]
        problems += [f"channel {channel.channel_id} member store {store} has no supply"
                     for store in channel.store_location_ids if store in selected and store not in stocked]
    return problems


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


def check_work_schedules(scenario: RuntimeScenario) -> None:
    """@brief 人物工作安排的跨引用（docs/dev/SPEC-errands-workday.md 4.3）：工作地点是所选地点；落在场景时间窗内的日历
    日期与场景日历一致（补班日有班次，春节、病假、事假日不当班）。work_schedule 为 null 的人物不核对；任一不满足即拒绝
    加载，先列工作地点、再列日历，各按人物身份与日期升序写明人物、日期与字段。"""
    problems = [*_workplace_problems(scenario), *_calendar_problems(scenario)]
    if problems:
        message = f"Work schedules are not admissible: {'; '.join(problems)}"
        logging.error(message)
        raise IntegrityError(message)


def _scheduled_agents(scenario: RuntimeScenario) -> list:
    """@brief 声明了工作安排的人物，按人物身份升序。"""
    return sorted((agent for agent in scenario.agents if agent.work_schedule is not None),
                  key=lambda agent: agent.agent_id)


def _workplace_problems(scenario: RuntimeScenario) -> list[str]:
    """@brief 工作地点不是场景所选地点的人物，每人一条。"""
    selected = {item.location_id for item in scenario.locations}
    return [f"agent {agent.agent_id} work_schedule.workplace_location_id "
            f"{agent.work_schedule.workplace_location_id!r} is not a selected location"
            for agent in _scheduled_agents(scenario) if agent.work_schedule.workplace_location_id not in selected]


def _calendar_problems(scenario: RuntimeScenario) -> list[str]:
    """@brief 时间窗内与场景日历不一致的日期：补班日没有班次，春节、病假、事假日有班次（班次按 work_schedule.shift_on，
    日期例外优先于每周声明）。"""
    dates = _window_calendar_dates(scenario)
    problems = []
    for agent in _scheduled_agents(scenario):
        for day, field in dates:
            shift = shift_on(agent.work_schedule, day)
            prefix = f"agent {agent.agent_id} date {day.isoformat()} calendar.{field}"
            if field in _SHIFT_REQUIRED_DATES and shift is None:
                problems.append(f"{prefix} requires a shift but work_schedule declares none")
            if field in _OFF_DUTY_DATES and shift is not None:
                problems.append(f"{prefix} must be off duty but work_schedule declares a shift")
    return problems


def _window_calendar_dates(scenario: RuntimeScenario) -> list[tuple[date, str]]:
    """@brief 场景日历里落在时间窗内、有要求的（日期, 字段）对，按日期、再按字段的列出顺序排列。时间窗内的日期：不早于
    开始时刻的当地日期，且当地零点早于结束时刻；窗外的日期不核对。"""
    zone = ZoneInfo(scenario.timezone)
    first = scenario.start_at.astimezone(zone).date()
    pairs = []
    for order, field in enumerate((*_SHIFT_REQUIRED_DATES, *_OFF_DUTY_DATES)):
        value = getattr(scenario.calendar, field)
        for text in (value,) if isinstance(value, str) else value:
            day = date.fromisoformat(text)
            if first <= day and datetime.combine(day, datetime.min.time(), zone) < scenario.end_at:
                pairs.append((day, order, field))
    return [(day, field) for day, _, field in sorted(pairs)]


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
