"""@brief 已持久化活动的目标和路线身份恢复校验。"""

from __future__ import annotations

import logging

from worldmock.errors import IntegrityError
from worldmock.life.models import ActivityKind, ActivityState, ConsumeRequest, WorldState
from worldmock.life.travel import persisted_trip

_LOGGER = logging.getLogger(__name__)


def validate_activity_targets(state: WorldState, rules, scenario) -> None:
    """@brief 用冻结场景与内容目录核对全部持久活动目标。"""
    people = {person.agent_id: person for person in scenario.agents}
    for activity in state.activities.values():
        if not _target_is_valid(activity, state, rules, rules.graph, people):
            _LOGGER.error("Persisted activity target is invalid: %s", activity.activity_id)
            raise IntegrityError("Persisted activity target is invalid")


def _target_is_valid(activity: ActivityState, state: WorldState, rules,
                     graph, people: dict) -> bool:
    """@brief 各活动类型只接受自身权威目标和冻结来源身份。"""
    kind = activity.kind
    target = activity.target_id
    data = activity.data
    if kind == ActivityKind.WORK:
        valid = target in rules.tasks and data.get("task_id") == target
    elif kind == ActivityKind.USE_APP:
        valid = (target in rules.apps and data.get("app_id") == target
                 and data.get("content_id") in rules.contents_by_app[target])
    elif kind == ActivityKind.READ_MAIL:
        valid = target in rules.messages and data.get("message_id") == target
    elif kind == ActivityKind.LIFESTYLE:
        valid = target in rules.activity_location_kinds
    elif kind == ActivityKind.CONSUME:
        valid = _consume_target_is_valid(activity, rules)
    elif kind == ActivityKind.TRAVEL:
        valid = _travel_target_is_valid(activity, graph)
    elif kind == ActivityKind.CHAT:
        valid = (target is None and data.get("partner_id") in
                 {f"agent:{agent_id}" for agent_id in state.agents if agent_id != activity.agent_id})
    elif kind == ActivityKind.CALL:
        owner = people.get(activity.agent_id)
        valid = (target is None and owner is not None
                 and data.get("contact_id") in {contact.contact_id for contact in owner.contacts})
    else:
        valid = target is None
    return valid and (kind == ActivityKind.TRAVEL or activity.destination_id is None)


def _consume_target_is_valid(activity: ActivityState, rules) -> bool:
    """@brief 已持有商品请求仍须与持久目标和冻结商品目录一致。"""
    try:
        request = ConsumeRequest.model_validate(activity.data["request"])
    except (KeyError, ValueError):
        return False
    return (bool(request.items) and activity.target_id == request.items[0].product_id
            and all(item.product_id in {food.food_id for food in rules.scenario.foods} for item in request.items))


def _travel_target_is_valid(activity: ActivityState, graph) -> bool:
    """@brief 持久出行的原始起点、终点、方式、路径及米数必须属于同一发布图。"""
    if activity.target_id is not None:
        return False
    try:
        persisted_trip(graph, activity)
    except (IntegrityError, KeyError, ValueError):
        return False
    return True
