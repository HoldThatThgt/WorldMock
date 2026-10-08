"""@brief 场景加载时就拒绝的世界缺陷：地点类别越出场所类型表、本轮可能同时出现的可读名称重复
（docs/design/04-simulation-engine.md ），以及购买渠道的准入（3.3）。

都在 semantics.load_runtime_scenario 执行，失败以 IntegrityError 拒绝加载并写明地点、类别或重复的名称，不留到
运行中途才由环境层（「Location kind has no place type label」）或观察投影（「interactive object labels must be
unique」）抛出。场所类型只认环境层的 PLACE_TYPES，不另建闭集；名称写法只认 object_labels，与观察投影同一来源。
名称按人物逐一核对：一个人物可能同时看到的全部对象名称（地点、门店商品、手边与他处持有物、商品目录、内容、游戏、
本人启用的生活活动、本人受理的工作、同世界其他人物、本人联系人、本人收件的邮件与「自己」）两两不同。

人物工作安排的跨引用（docs/design/06-agent.md#agent-工作安排声明 ）同样在这里拒绝：工作地点是所选地点；与场景日历交叉核对，
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
from worldmock.exchange.types import LocationKind
from worldmock.exchange.operators import delivery_seconds
from worldmock.life.parcel import nearest_parcel_station
from worldmock.life.object_labels import SELF_LABEL, object_label

from worldmock.life.scenario import RuntimeScenario
from worldmock.life.work_schedule import shift_on

#: 报错时列出的重复名称条数上限（其余只计数）。
_REPORTED_DUPLICATES = 3

#: 必须有班次的日历字段（补班日）与必须不当班的日历字段（春节、病假、事假日），按 CalendarConfig 的属性名；
#: 加班机会日与聚餐日不作要求。
_SHIFT_REQUIRED_DATES = ("makeup_work_dates",)
_OFF_DUTY_DATES = ("spring_festival_dates", "sick_leave_date", "personal_leave_date")


def check_location_kinds(scenario: RuntimeScenario) -> None:
    """@brief 所选地点的 kind 必须属于环境层的场所类型表 PLACE_TYPES，越界即拒绝并逐一写明地点与类别。

    kind 是交互菜单与环境层的场所分类，与活动环境 location_kinds 不是同一个概念。
    """
    outside = [f"{item.location_id} kind {item.kind!r}" for item in scenario.locations if item.kind not in LocationKind]
    if outside:
        message = (f"Selected locations use kinds that are not place types: {'; '.join(outside)};"
                   f" allowed kinds: {', '.join(sorted(LocationKind))}")
        logging.error(message)
        raise IntegrityError(message)


def check_parcel_stations(semantics) -> None:
    """@brief 声明或图距可产生长途运输时，所有人物家庭最近驿站须实例化。"""
    places = semantics.scenario.locations
    long_shipping = any(place.shipping_seconds > 86400 if place.shipping_seconds is not None else
                        bool(place.app_ids) and any(
                            delivery_seconds(semantics.catalog.shortest_m(place.location_id, destination.location_id))
                            > 86400 for destination in places) for place in places)
    if long_shipping:
        for agent in semantics.scenario.agents:
            nearest_parcel_station(semantics, agent.home_location_id)


def check_work_schedules(scenario: RuntimeScenario) -> None:
    """@brief 人物工作安排的跨引用（docs/design/06-agent.md#agent-工作安排声明 ）：工作地点是所选地点；落在场景时间窗内的日历
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


def check_visible_labels(scenario: RuntimeScenario) -> None:
    """@brief 所选地点的显示名互不相同，且每个人物可能同时看到的对象名称两两不同；否则拒绝加载。

    同名地点须以地图字段 branch_label 消歧（显示名「名称（branch_label）」）。

    @param scenario 已验证的冻结运行场景。
    """
    _check_place_names(scenario)
    for agent_id, labels in visible_object_labels(scenario).items():
        duplicates = sorted((label, sources) for label, sources in labels.items() if len(sources) > 1)
        if duplicates:
            listed = "; ".join(f"{label!r} from {', '.join(sources)}"
                               for label, sources in duplicates[:_REPORTED_DUPLICATES])
            more = len(duplicates) - _REPORTED_DUPLICATES
            message = (f"Agent {agent_id} would see non-unique object labels: {listed}"
                       + (f"; and {more} more" if more > 0 else ""))
            logging.error(message)
            raise IntegrityError(message)


def visible_object_labels(scenario: RuntimeScenario) -> dict[int, dict[str, list[str]]]:
    """@brief 每个人物可能同时看到的全部对象名称及其来源（人物 → 名称 → 来源列表，来源形如 location:<身份>）。

    地点、商品与动作使用登记的显示名，人物与联系人来自场景。
    """
    shared = _world_labels(scenario)
    return {agent.agent_id: _group(shared + _agent_labels(scenario, agent)) for agent in scenario.agents}


def _check_place_names(scenario: RuntimeScenario) -> None:
    """@brief 所选地点的 display_name 唯一；重名且无法以分店标签区分即拒绝，逐一写明重名与地点。"""
    places: dict[str, list[str]] = defaultdict(list)
    for location in scenario.locations:
        places[location.display_name].append(location.location_id)
    shared = [f"{name!r} ({', '.join(sorted(identities))})"
              for name, identities in sorted(places.items()) if len(identities) > 1]
    if shared:
        message = (f"Selected locations share display names: {'; '.join(shared)};"
                   " declare distinct branch_label values in the registered locations")
        logging.error(message)
        raise IntegrityError(message)


def _world_labels(scenario: RuntimeScenario) -> list[tuple[str, str]]:
    """@brief 地点、商品与动作提供可见目标名称。"""
    labels = [(item.display_name, f"location:{item.location_id}") for item in scenario.locations]
    labels += [(item.name, f"goods:{item.goods_id}") for item in scenario.goods]
    labels += [(item.name, f"action:{item.action_id}") for item in scenario.actions]
    return labels


def _agent_labels(scenario: RuntimeScenario, agent) -> list[tuple[str, str]]:
    """@brief 人物、联系人与自己提供人物侧可见目标名称。"""
    labels = [(other.name, f"agent:{other.agent_id}") for other in scenario.agents
              if other.agent_id != agent.agent_id]
    labels += [(item.salutation, f"contact:{item.contact_id}") for item in agent.contacts]
    labels.append((SELF_LABEL, f"agent:{agent.agent_id}"))
    return labels


def _group(labels: list[tuple[str, str]]) -> dict[str, list[str]]:
    """@brief 按名称归并来源（保留出现顺序）。"""
    grouped: dict[str, list[str]] = defaultdict(list)
    for label, source in labels:
        grouped[label].append(source)
    return grouped
